from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
ALLOWED_PROTOCOLS = frozenset({"openai-compatible", "anthropic"})
FORBIDDEN_SECRET_KEYS = frozenset(
    {"api_key", "api-key", "token", "secret", "password", "authorization"}
)


class LLMConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedLLMConfig:
    registry_version: int
    app_id: str
    alias: str
    protocol: str
    base_url: str
    model: str
    api_key_file: Path
    timeout_seconds: int
    fallbacks: tuple[str, ...]

    def read_api_key(self) -> str:
        value = self.api_key_file.read_text(encoding="utf-8").strip()
        if not value:
            raise LLMConfigError(f"LLM credential file is empty: {self.api_key_file}")
        return value


def load_registry(path: str | Path) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise LLMConfigError("LLM registry must be a YAML object")
    _reject_inline_secrets(raw)
    if raw.get("version") != 1:
        raise LLMConfigError("unsupported LLM registry version")
    providers = raw.get("providers")
    models = raw.get("models")
    if not isinstance(providers, dict) or not providers:
        raise LLMConfigError("LLM registry must define providers")
    if not isinstance(models, dict) or not models:
        raise LLMConfigError("LLM registry must define models")
    _validate_registry_shape(raw)
    return raw


def resolve_llm_config(
    registry_path: str | Path,
    *,
    secrets_dir: str | Path,
    app_id: str,
    alias: str,
    require_secret: bool = True,
) -> ResolvedLLMConfig:
    _require_id("app_id", app_id)
    _require_id("model alias", alias)
    registry = load_registry(registry_path)

    models = registry["models"]
    base_model = models.get(alias)
    if not isinstance(base_model, dict):
        raise LLMConfigError(f"unknown model alias: {alias}")
    model_config = dict(base_model)

    apps = registry.get("apps") or {}
    if apps and not isinstance(apps, dict):
        raise LLMConfigError("apps must be an object")
    app_config = apps.get(app_id, {}) if isinstance(apps, dict) else {}
    if app_config and not isinstance(app_config, dict):
        raise LLMConfigError(f"app override must be an object: {app_id}")
    overrides = app_config.get("models", {}) if app_config else {}
    if overrides and not isinstance(overrides, dict):
        raise LLMConfigError(f"app model overrides must be an object: {app_id}")
    override = overrides.get(alias, {}) if isinstance(overrides, dict) else {}
    if override and not isinstance(override, dict):
        raise LLMConfigError(f"model override must be an object: {app_id}/{alias}")
    model_config.update(override)

    provider_id = model_config.get("provider")
    _require_id("provider", provider_id)
    provider = registry["providers"].get(provider_id)
    if not isinstance(provider, dict):
        raise LLMConfigError(f"unknown provider: {provider_id}")

    protocol = provider.get("protocol")
    if protocol not in ALLOWED_PROTOCOLS:
        raise LLMConfigError(f"unsupported provider protocol: {protocol}")
    base_url = _validate_base_url(provider.get("base_url"))
    model = model_config.get("model")
    if not isinstance(model, str) or not model.strip():
        raise LLMConfigError(f"model name is required for alias: {alias}")

    timeout = model_config.get("timeout_seconds", provider.get("timeout_seconds", 90))
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 600:
        raise LLMConfigError("timeout_seconds must be an integer between 1 and 600")

    fallbacks = model_config.get("fallbacks", [])
    if not isinstance(fallbacks, list) or not all(isinstance(item, str) for item in fallbacks):
        raise LLMConfigError("fallbacks must be an array of model aliases")
    for fallback in fallbacks:
        _require_id("fallback alias", fallback)
        if fallback not in models:
            raise LLMConfigError(f"unknown fallback model alias: {fallback}")
        if fallback == alias:
            raise LLMConfigError("model alias cannot fall back to itself")

    credential_template = provider.get("credential_file")
    if not isinstance(credential_template, str) or not credential_template.strip():
        raise LLMConfigError(f"credential_file is required for provider: {provider_id}")
    if "{" in credential_template.replace("{app_id}", "") or "}" in credential_template.replace(
        "{app_id}", ""
    ):
        raise LLMConfigError("credential_file only supports the {app_id} placeholder")
    try:
        credential_relative = credential_template.format(app_id=app_id)
    except (KeyError, ValueError) as exc:
        raise LLMConfigError("credential_file only supports the {app_id} placeholder") from exc
    root = Path(secrets_dir).expanduser().resolve()
    credential = (root / credential_relative).resolve()
    if root != credential and root not in credential.parents:
        raise LLMConfigError("credential_file escapes the configured secrets directory")
    if require_secret and not credential.is_file():
        raise LLMConfigError(f"LLM credential file does not exist: {credential}")

    return ResolvedLLMConfig(
        registry_version=1,
        app_id=app_id,
        alias=alias,
        protocol=protocol,
        base_url=base_url,
        model=model.strip(),
        api_key_file=credential,
        timeout_seconds=timeout,
        fallbacks=tuple(fallbacks),
    )


def _require_id(label: str, value: Any) -> None:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise LLMConfigError(f"invalid {label}: {value!r}")


def _validate_base_url(value: Any) -> str:
    if not isinstance(value, str):
        raise LLMConfigError("provider base_url must be a string")
    normalized = value.rstrip("/")
    parsed = urlsplit(normalized)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.hostname or (
        parsed.scheme != "https" and not (local and parsed.scheme == "http")
    ):
        raise LLMConfigError("provider base_url must use HTTPS, except for localhost development")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LLMConfigError("provider base_url must not contain credentials, query or fragment")
    return normalized


def _reject_inline_secrets(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower().replace("_", "-")
            if normalized in FORBIDDEN_SECRET_KEYS:
                location = ".".join((*path, str(key)))
                raise LLMConfigError(f"inline secret field is forbidden: {location}")
            _reject_inline_secrets(nested, (*path, str(key)))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_inline_secrets(nested, (*path, str(index)))


def _validate_registry_shape(registry: dict[str, Any]) -> None:
    _reject_unexpected(registry, {"version", "providers", "models", "apps"}, "registry")
    providers = registry["providers"]
    for provider_id, provider in providers.items():
        _require_id("provider", provider_id)
        if not isinstance(provider, dict):
            raise LLMConfigError(f"provider must be an object: {provider_id}")
        _reject_unexpected(
            provider,
            {"protocol", "base_url", "credential_file", "timeout_seconds"},
            f"provider {provider_id}",
        )
    models = registry["models"]
    for alias, model in models.items():
        _require_id("model alias", alias)
        if not isinstance(model, dict):
            raise LLMConfigError(f"model must be an object: {alias}")
        _reject_unexpected(
            model,
            {"provider", "model", "timeout_seconds", "fallbacks"},
            f"model {alias}",
        )
    apps = registry.get("apps") or {}
    if not isinstance(apps, dict):
        raise LLMConfigError("apps must be an object")
    for app_id, app in apps.items():
        _require_id("app_id", app_id)
        if not isinstance(app, dict):
            raise LLMConfigError(f"app override must be an object: {app_id}")
        _reject_unexpected(app, {"models"}, f"app {app_id}")
        overrides = app.get("models")
        if not isinstance(overrides, dict):
            raise LLMConfigError(f"app model overrides must be an object: {app_id}")
        for alias, override in overrides.items():
            _require_id("model alias", alias)
            if alias not in models:
                raise LLMConfigError(f"app override references unknown model alias: {alias}")
            if not isinstance(override, dict):
                raise LLMConfigError(f"model override must be an object: {app_id}/{alias}")
            _reject_unexpected(
                override,
                {"provider", "model", "timeout_seconds", "fallbacks"},
                f"model override {app_id}/{alias}",
            )


def _reject_unexpected(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unexpected = set(value) - allowed
    if unexpected:
        raise LLMConfigError(f"unexpected fields in {label}: {sorted(unexpected)}")
