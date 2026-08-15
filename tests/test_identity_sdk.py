import pytest

from shadow_sdk.identity import VerifiedIdentity


def test_verified_identity_uses_issuer_and_subject_as_external_key():
    identity = VerifiedIdentity.from_verified_claims(
        {
            "iss": "https://auth.example.com",
            "sub": "abc-123",
            "preferred_username": "alice",
            "name": "Alice",
            "email": "alice@example.com",
            "groups": ["travel-users"],
        }
    )

    assert identity.external_key == ("https://auth.example.com", "abc-123")
    assert identity.groups == ("travel-users",)


def test_verified_identity_requires_stable_claims():
    with pytest.raises(ValueError):
        VerifiedIdentity.from_verified_claims({"preferred_username": "alice"})
