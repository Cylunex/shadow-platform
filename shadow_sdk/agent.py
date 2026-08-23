from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class AgentAuthError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    agent_id: str
    owner_app: str
    audience: str
    scopes: frozenset[str]
    capabilities: frozenset[str] = frozenset()

    def require_scope(self, scope: str) -> None:
        if scope not in self.scopes:
            raise AgentAuthError(f"agent lacks required scope: {scope}")

    def require_capability(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise AgentAuthError(f"agent lacks required capability: {capability}")


class AgentAuthenticator:
    """Local verifier for Shadow Agent opaque Bearer tokens.

    The registry stores only paths to SHA-256 token digests. Authentication does not
    call a central gateway, and the raw token is never retained by this object.
    """

    def __init__(
        self,
        registry_path: str | Path,
        *,
        secrets_dir: str | Path,
        audience: str,
    ) -> None:
        _require_id("audience", audience)
        self.audience = audience
        self._entries = _load_entries(registry_path, Path(secrets_dir), audience)

    def authenticate(self, authorization: str) -> AgentIdentity:
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or len(token) < 32:
            raise AgentAuthError("valid agent Bearer token required")
        supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        matched: AgentIdentity | None = None
        for identity, accepted_hashes in self._entries:
            if any(secrets.compare_digest(supplied_hash, value) for value in accepted_hashes):
                matched = identity
        if matched is None:
            raise AgentAuthError("invalid agent Bearer token")
        return matched


def _load_entries(
    registry_path: str | Path,
    secrets_dir: Path,
    audience: str,
) -> tuple[tuple[AgentIdentity, tuple[str, ...]], ...]:
    raw = yaml.safe_load(Path(registry_path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise AgentAuthError("unsupported agent registry")
    agents = raw.get("agents")
    if not isinstance(agents, dict) or not agents:
        raise AgentAuthError("agent registry must define agents")

    root = secrets_dir.expanduser().resolve()
    entries = []
    for agent_id, config in agents.items():
        _require_id("agent_id", agent_id)
        if not isinstance(config, dict):
            raise AgentAuthError(f"agent config must be an object: {agent_id}")
        unexpected = set(config) - {
            "owner_app",
            "audiences",
            "scopes",
            "capabilities",
            "credential_hash_files",
            "disabled",
        }
        if unexpected:
            raise AgentAuthError(f"unexpected agent config fields: {sorted(unexpected)}")
        if config.get("disabled", False):
            continue
        owner_app = config.get("owner_app")
        _require_id("owner_app", owner_app)
        audiences = _string_list(config.get("audiences"), "audiences", allow_empty=False)
        for item in audiences:
            _require_id("audience", item)
        if audience not in audiences:
            continue
        scopes = _string_list(config.get("scopes", []), "scopes", allow_empty=True)
        if not all(SCOPE_PATTERN.fullmatch(scope) for scope in scopes):
            raise AgentAuthError(f"invalid scope configured for agent: {agent_id}")
        capabilities = _string_list(
            config.get("capabilities", []), "capabilities", allow_empty=True
        )
        if not all(SCOPE_PATTERN.fullmatch(item) for item in capabilities):
            raise AgentAuthError(f"invalid capability configured for agent: {agent_id}")
        paths = _string_list(
            config.get("credential_hash_files"),
            "credential_hash_files",
            allow_empty=False,
        )
        if len(paths) > 2:
            raise AgentAuthError("at most two credential hashes are allowed during rotation")
        accepted_hashes = tuple(_read_hash_file(root, item) for item in paths)
        entries.append(
            (
                AgentIdentity(
                    agent_id=agent_id,
                    owner_app=owner_app,
                    audience=audience,
                    scopes=frozenset(scopes),
                    capabilities=frozenset(capabilities),
                ),
                accepted_hashes,
            )
        )
    return tuple(entries)


def _read_hash_file(root: Path, relative: str) -> str:
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        raise AgentAuthError("credential hash file escapes the configured secrets directory")
    if not path.is_file():
        raise AgentAuthError(f"agent credential hash file does not exist: {path}")
    value = path.read_text(encoding="utf-8").strip().lower()
    if not SHA256_PATTERN.fullmatch(value):
        raise AgentAuthError(f"agent credential hash file is invalid: {path}")
    return value


def _require_id(label: str, value: Any) -> None:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise AgentAuthError(f"invalid {label}: {value!r}")


def _string_list(value: Any, label: str, *, allow_empty: bool) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AgentAuthError(f"{label} must be an array of strings")
    if not allow_empty and not value:
        raise AgentAuthError(f"{label} must not be empty")
    if len(set(value)) != len(value):
        raise AgentAuthError(f"{label} must not contain duplicates")
    return value
