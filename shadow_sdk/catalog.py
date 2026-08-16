from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .llm import ID_PATTERN


class CatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AppAuth:
    mode: str
    groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AppDescriptor:
    app_id: str
    title: str
    owner: str
    lifecycle: str
    kind: str
    canonical_url: str | None
    aliases: tuple[str, ...]
    auth: AppAuth
    health_path: str | None
    media: bool
    llm_models: tuple[str, ...]
    agent_audience: bool


def load_app_catalog(path: str | Path) -> dict[str, AppDescriptor]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise CatalogError("unsupported app catalog version")
    apps = raw.get("apps")
    if not isinstance(apps, dict) or not apps:
        raise CatalogError("app catalog must define apps")
    result: dict[str, AppDescriptor] = {}
    urls: set[str] = set()
    for app_id, config in apps.items():
        _id("app_id", app_id)
        if not isinstance(config, dict):
            raise CatalogError(f"app descriptor must be an object: {app_id}")
        expected = {
            "title",
            "owner",
            "lifecycle",
            "kind",
            "canonical_url",
            "aliases",
            "auth",
            "health_path",
            "media",
            "llm_models",
            "agent_audience",
        }
        required = expected - {"aliases"}
        missing = required - set(config)
        if missing:
            raise CatalogError(f"missing fields for {app_id}: {sorted(missing)}")
        unexpected = set(config) - expected
        if unexpected:
            raise CatalogError(f"unexpected fields for {app_id}: {sorted(unexpected)}")
        auth = config.get("auth")
        if not isinstance(auth, dict):
            raise CatalogError(f"auth must be an object: {app_id}")
        groups = _ids(auth.get("groups"), f"auth groups for {app_id}")
        aliases = _strings(config.get("aliases", []), f"aliases for {app_id}")
        llm_models = _ids(config.get("llm_models"), f"LLM aliases for {app_id}")
        canonical_url = config.get("canonical_url")
        if canonical_url is not None and not isinstance(canonical_url, str):
            raise CatalogError(f"canonical_url must be a string or null: {app_id}")
        for url in ([canonical_url] if canonical_url else []) + aliases:
            if not url.startswith("https://"):
                raise CatalogError(f"catalog URLs must use HTTPS: {url}")
            normalized = url.rstrip("/") or url
            if normalized in urls:
                raise CatalogError(f"duplicate catalog URL: {url}")
            urls.add(normalized)
        health_path = config.get("health_path")
        if health_path is not None and (
            not isinstance(health_path, str) or not health_path.startswith("/")
        ):
            raise CatalogError(f"health_path must start with '/': {app_id}")
        result[app_id] = AppDescriptor(
            app_id=app_id,
            title=_text(config.get("title"), f"title for {app_id}"),
            owner=_id("owner", config.get("owner")),
            lifecycle=_choice(
                config.get("lifecycle"),
                {"experimental", "production", "deprecated"},
                f"lifecycle for {app_id}",
            ),
            kind=_choice(
                config.get("kind"),
                {"web", "service", "mobile", "library"},
                f"kind for {app_id}",
            ),
            canonical_url=canonical_url,
            aliases=tuple(aliases),
            auth=AppAuth(
                mode=_choice(
                    auth.get("mode"),
                    {
                        "public",
                        "public-with-protected-paths",
                        "forward-auth",
                        "oidc",
                        "service-bearer",
                    },
                    f"auth mode for {app_id}",
                ),
                groups=tuple(groups),
            ),
            health_path=health_path,
            media=_boolean(config.get("media"), f"media for {app_id}"),
            llm_models=tuple(llm_models),
            agent_audience=_boolean(config.get("agent_audience"), f"agent_audience for {app_id}"),
        )
    return result


def _id(label: str, value: Any) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise CatalogError(f"invalid {label}: {value!r}")
    return value


def _ids(value: Any, label: str) -> list[str]:
    values = _strings(value, label)
    for item in values:
        _id(label, item)
    return values


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CatalogError(f"{label} must be an array of strings")
    if len(set(value)) != len(value):
        raise CatalogError(f"{label} must not contain duplicates")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{label} must be a non-empty string")
    return value.strip()


def _choice(value: Any, choices: set[str], label: str) -> str:
    if value not in choices:
        raise CatalogError(f"invalid {label}: {value!r}")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise CatalogError(f"{label} must be a boolean")
    return value
