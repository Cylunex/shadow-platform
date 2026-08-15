import json

import pytest

from scripts.generate_service_token import rotate_service_token
from shadow_sdk.service_auth import (
    ServiceAuthError,
    authenticate_service_token,
    hash_service_token,
    load_service_token_hashes,
)


def test_service_token_registry_stores_only_hash_and_rotates(tmp_path):
    registry = tmp_path / "service-token-hashes.json"
    first = rotate_service_token(registry, "travel")
    second = rotate_service_token(registry, "travel")
    raw = registry.read_text(encoding="utf-8")
    hashes = load_service_token_hashes(registry)

    assert first not in raw
    assert second not in raw
    assert hashes["travel"] == (hash_service_token(second), hash_service_token(first))
    assert authenticate_service_token(f"Bearer {first}", hashes) == "travel"
    assert authenticate_service_token(f"Bearer {second}", hashes) == "travel"


def test_service_token_registry_rejects_plaintext(tmp_path):
    registry = tmp_path / "service-token-hashes.json"
    registry.write_text(
        json.dumps({"version": 1, "apps": {"travel": {"token_sha256": ["plaintext"]}}}),
        encoding="utf-8",
    )

    with pytest.raises(ServiceAuthError, match="SHA-256"):
        load_service_token_hashes(registry)


def test_service_token_requires_high_entropy_length():
    registry = {"travel": (hash_service_token("short"),)}

    with pytest.raises(ServiceAuthError, match="valid service"):
        authenticate_service_token("Bearer short", registry)
