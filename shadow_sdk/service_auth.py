from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Any

SERVICE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class ServiceAuthError(ValueError):
    pass


def hash_service_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_service_token_hashes(path: str | Path) -> dict[str, tuple[str, ...]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ServiceAuthError("unsupported service token registry version")
    apps = raw.get("apps")
    if not isinstance(apps, dict) or not apps:
        raise ServiceAuthError("service token registry must define apps")
    result: dict[str, tuple[str, ...]] = {}
    for app_id, config in apps.items():
        if not isinstance(app_id, str) or not SERVICE_ID_PATTERN.fullmatch(app_id):
            raise ServiceAuthError(f"invalid service app_id: {app_id!r}")
        if not isinstance(config, dict) or set(config) != {"token_sha256"}:
            raise ServiceAuthError(f"invalid service token entry: {app_id}")
        values = config.get("token_sha256")
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 2
            or len(set(values)) != len(values)
            or not all(
                isinstance(value, str) and SHA256_PATTERN.fullmatch(value) for value in values
            )
        ):
            raise ServiceAuthError(f"{app_id} must have one or two unique SHA-256 digests")
        result[app_id] = tuple(values)
    return result


def authenticate_service_token(authorization: str, registry: dict[str, tuple[str, ...]]) -> str:
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or len(token) < 32:
        raise ServiceAuthError("valid service Bearer token required")
    supplied = hash_service_token(token)
    matched: str | None = None
    for app_id, accepted in registry.items():
        if any(secrets.compare_digest(supplied, expected) for expected in accepted):
            matched = app_id
    if matched is None:
        raise ServiceAuthError("invalid service Bearer token")
    return matched


def build_service_token_registry(apps: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "version": 1,
        "apps": {app_id: {"token_sha256": digests} for app_id, digests in sorted(apps.items())},
    }
