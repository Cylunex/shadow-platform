from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from shadow_sdk.service_auth import load_service_token_hashes


def _read_secret(path: str | Path | None) -> str:
    if not path:
        return ""
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"secret file is empty: {path}")
    return value


def _json_list(name: str, default: str = "[]") -> tuple[str, ...]:
    raw = os.getenv(name, default)
    value = json.loads(raw)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a JSON string array")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class ChannelAccount:
    channel: str
    account_id: str
    enabled: bool = True
    token: str = ""
    app_id: str = ""
    app_secret: str = ""
    api_base: str = ""
    domain: str = "feishu"


@dataclass(frozen=True, slots=True)
class TargetConfig:
    recipient_issuer: str
    recipient_subject: str
    channel: str
    account_id: str
    target_kind: str
    target_id: str
    thread_id: str = ""
    label: str = ""
    enabled: bool = True
    is_home: bool = False
    min_severity: str = "info"
    categories: tuple[str, ...] = ()
    require_mention: bool = True
    command_level: str = "safe"


@dataclass(frozen=True, slots=True)
class PrincipalConfig:
    recipient_issuer: str
    recipient_subject: str
    channel: str
    account_id: str
    sender_id: str
    role: str = "user"
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ProbeConfig:
    service_id: str
    url: str
    timeout_seconds: float = 3.0
    expected_status: int = 200


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    accounts: dict[tuple[str, str], ChannelAccount] = field(default_factory=dict)
    targets: tuple[TargetConfig, ...] = ()
    principals: tuple[PrincipalConfig, ...] = ()
    probes: tuple[ProbeConfig, ...] = ()


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _is_loopback_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        return False
    return parsed.scheme == "http" and address.is_loopback and parsed.username is None


def _is_https_origin(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _is_https_callback(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and parsed.path == "/auth/callback"
        and not parsed.query
        and not parsed.fragment
    )


def load_channel_config(path: str | Path | None) -> ChannelConfig:
    if not path:
        return ChannelConfig()
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    root = _mapping(raw, "channel config")
    if root.get("version") != 1:
        raise ValueError("unsupported notification channel config version")

    accounts: dict[tuple[str, str], ChannelAccount] = {}
    for channel, account_items in _mapping(root.get("accounts", {}), "accounts").items():
        if channel not in {"telegram", "feishu", "qqbot"}:
            raise ValueError(f"unsupported notification channel: {channel}")
        for account_id, item_raw in _mapping(account_items, f"accounts.{channel}").items():
            item = _mapping(item_raw, f"accounts.{channel}.{account_id}")
            secret_file = item.get("token_file") or item.get("app_secret_file")
            enabled = bool(item.get("enabled", True))
            secret = _read_secret(secret_file) if enabled else ""
            account = ChannelAccount(
                channel=channel,
                account_id=str(account_id),
                enabled=enabled,
                token=secret if channel == "telegram" else "",
                app_id=str(item.get("app_id", "")),
                app_secret=secret if channel in {"feishu", "qqbot"} else "",
                api_base=str(item.get("api_base", "")),
                domain=str(item.get("domain", "feishu")),
            )
            if account.enabled:
                if channel == "telegram" and not account.token:
                    raise ValueError(f"telegram account {account_id} requires token_file")
                if channel in {"feishu", "qqbot"} and (
                    not account.app_id or not account.app_secret
                ):
                    raise ValueError(f"{channel} account {account_id} requires app credentials")
            accounts[(channel, str(account_id))] = account

    targets: list[TargetConfig] = []
    for index, item_raw in enumerate(root.get("targets", [])):
        item = _mapping(item_raw, f"targets[{index}]")
        recipient = _mapping(item.get("recipient"), f"targets[{index}].recipient")
        target = TargetConfig(
            recipient_issuer=str(recipient.get("issuer", "")),
            recipient_subject=str(recipient.get("subject", "")),
            channel=str(item.get("channel", "")),
            account_id=str(item.get("account_id", "default")),
            target_kind=str(item.get("target_kind", "direct")),
            target_id=str(item.get("target_id", "")),
            thread_id=str(item.get("thread_id", "")),
            label=str(item.get("label", "")),
            enabled=bool(item.get("enabled", True)),
            is_home=bool(item.get("is_home", False)),
            min_severity=str(item.get("min_severity", "info")),
            categories=tuple(str(value) for value in item.get("categories", [])),
            require_mention=bool(item.get("require_mention", True)),
            command_level=str(item.get("command_level", "safe")),
        )
        if not target.recipient_issuer or not target.recipient_subject or not target.target_id:
            raise ValueError(f"targets[{index}] requires recipient and target_id")
        if target.channel not in {"telegram", "feishu", "qqbot"}:
            raise ValueError(f"targets[{index}] has unsupported channel")
        if target.target_kind not in {"direct", "group", "channel"}:
            raise ValueError(f"targets[{index}] has invalid target_kind")
        if target.min_severity not in {"info", "success", "warning", "critical"}:
            raise ValueError(f"targets[{index}] has invalid min_severity")
        if target.command_level not in {"safe", "operator"}:
            raise ValueError(f"targets[{index}] has invalid command_level")
        if (target.channel, target.account_id) not in accounts:
            raise ValueError(f"targets[{index}] references unknown account")
        if target.enabled and target.target_kind != "direct" and not target.categories:
            raise ValueError(f"targets[{index}] group/channel requires explicit categories")
        targets.append(target)

    principals: list[PrincipalConfig] = []
    for index, item_raw in enumerate(root.get("principals", [])):
        item = _mapping(item_raw, f"principals[{index}]")
        recipient = _mapping(item.get("recipient"), f"principals[{index}].recipient")
        principal = PrincipalConfig(
            recipient_issuer=str(recipient.get("issuer", "")),
            recipient_subject=str(recipient.get("subject", "")),
            channel=str(item.get("channel", "")),
            account_id=str(item.get("account_id", "default")),
            sender_id=str(item.get("sender_id", "")),
            role=str(item.get("role", "user")),
            enabled=bool(item.get("enabled", True)),
        )
        if principal.role not in {"user", "operator"}:
            raise ValueError(f"principals[{index}] has invalid role")
        if (
            not principal.recipient_issuer
            or not principal.recipient_subject
            or not principal.sender_id
        ):
            raise ValueError(f"principals[{index}] requires recipient and sender_id")
        if (principal.channel, principal.account_id) not in accounts:
            raise ValueError(f"principals[{index}] references unknown account")
        principals.append(principal)

    probes: list[ProbeConfig] = []
    for index, item_raw in enumerate(root.get("probes", [])):
        item = _mapping(item_raw, f"probes[{index}]")
        probe = ProbeConfig(
            service_id=str(item.get("service_id", "")),
            url=str(item.get("url", "")),
            timeout_seconds=float(item.get("timeout_seconds", 3.0)),
            expected_status=int(item.get("expected_status", 200)),
        )
        if not probe.service_id or not _is_loopback_http_url(probe.url):
            raise ValueError(f"probes[{index}] must use an explicit loopback URL")
        if not 0.1 <= probe.timeout_seconds <= 30:
            raise ValueError(f"probes[{index}] timeout out of range")
        probes.append(probe)

    return ChannelConfig(accounts, tuple(targets), tuple(principals), tuple(probes))


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "development"
    database_url: str = "sqlite:///./data/notifications.db"
    service_token_hashes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    channel_config: ChannelConfig = field(default_factory=ChannelConfig)
    oidc_issuer: str = ""
    oidc_client_id: str = "shadow-notifications"
    oidc_client_secret: str = ""
    oidc_callbacks: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    session_secret: str = ""
    session_ttl_seconds: int = 30 * 86400
    admin_group: str = "shadow-admins"
    chat_gateway_apps: tuple[str, ...] = ("hermes", "openclaw")
    dev_auth: bool = False
    max_attempts: int = 8
    retention_days: int = 180
    probe_interval_seconds: int = 60

    def __post_init__(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("SHADOW_ENV must be development, test or production")
        if not 1 <= self.max_attempts <= 50:
            raise ValueError("max_attempts must be between 1 and 50")
        if not 1 <= self.retention_days <= 3650:
            raise ValueError("retention_days must be between 1 and 3650")
        if not 5 <= self.probe_interval_seconds <= 3600:
            raise ValueError("probe interval must be between 5 and 3600 seconds")
        if not 60 <= self.session_ttl_seconds <= 366 * 86400:
            raise ValueError("session TTL out of range")
        if self.environment == "production":
            if self.dev_auth:
                raise ValueError("dev auth is forbidden in production")
            if self.database_url.startswith("sqlite"):
                raise ValueError("production notification service requires PostgreSQL")
            if not self.service_token_hashes:
                raise ValueError("production notification service tokens are required")
            if not self.oidc_issuer.startswith("https://"):
                raise ValueError("production OIDC issuer must use HTTPS")
            if not self.oidc_client_secret or not self.session_secret:
                raise ValueError("production OIDC and session secrets are required")
            if len(self.session_secret) < 32:
                raise ValueError("production session secret must contain at least 32 characters")
            if not self.oidc_callbacks or not self.allowed_origins:
                raise ValueError("production callback and origin allowlists are required")
            for callback in self.oidc_callbacks:
                if not _is_https_callback(callback):
                    raise ValueError("OIDC callbacks must be exact HTTPS URLs")
            if any(not _is_https_origin(origin) for origin in self.allowed_origins):
                raise ValueError("allowed origins must be exact HTTPS origins")

    @classmethod
    def from_env(cls) -> Settings:
        database_url = _read_secret(os.getenv("SHADOW_NOTIFY_DATABASE_URL_FILE")) or os.getenv(
            "SHADOW_NOTIFY_DATABASE_URL", "sqlite:///./data/notifications.db"
        )
        token_file = os.getenv("SHADOW_NOTIFY_SERVICE_TOKEN_HASHES_FILE")
        return cls(
            environment=os.getenv("SHADOW_ENV", "development"),
            database_url=database_url,
            service_token_hashes=load_service_token_hashes(token_file) if token_file else {},
            channel_config=load_channel_config(os.getenv("SHADOW_NOTIFY_CHANNEL_CONFIG_FILE")),
            oidc_issuer=os.getenv("SHADOW_NOTIFY_OIDC_ISSUER", ""),
            oidc_client_id=os.getenv("SHADOW_NOTIFY_OIDC_CLIENT_ID", "shadow-notifications"),
            oidc_client_secret=_read_secret(os.getenv("SHADOW_NOTIFY_OIDC_CLIENT_SECRET_FILE")),
            oidc_callbacks=_json_list("SHADOW_NOTIFY_OIDC_CALLBACKS"),
            allowed_origins=_json_list("SHADOW_NOTIFY_ALLOWED_ORIGINS"),
            session_secret=_read_secret(os.getenv("SHADOW_NOTIFY_SESSION_SECRET_FILE")),
            session_ttl_seconds=int(os.getenv("SHADOW_NOTIFY_SESSION_TTL_SECONDS", "2592000")),
            admin_group=os.getenv("SHADOW_NOTIFY_ADMIN_GROUP", "shadow-admins"),
            chat_gateway_apps=_json_list(
                "SHADOW_NOTIFY_CHAT_GATEWAY_APPS", '["hermes", "openclaw"]'
            ),
            dev_auth=os.getenv("SHADOW_NOTIFY_DEV_AUTH", "false").lower() == "true",
            max_attempts=int(os.getenv("SHADOW_NOTIFY_MAX_ATTEMPTS", "8")),
            retention_days=int(os.getenv("SHADOW_NOTIFY_RETENTION_DAYS", "180")),
            probe_interval_seconds=int(os.getenv("SHADOW_NOTIFY_PROBE_INTERVAL_SECONDS", "60")),
        )
