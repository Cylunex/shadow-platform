import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from shadow_sdk.confirmation import (
    ConfirmationBinding,
    ConfirmationError,
    ConfirmationReplayStore,
    ConfirmationSigner,
    ConfirmationVerifier,
    arguments_sha256,
    canonical_json,
    encode_confirmation_receipt,
    enforce_destructive_limits,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _key_files(tmp_path, kind="ed25519"):
    private = (
        ed25519.Ed25519PrivateKey.generate()
        if kind == "ed25519"
        else ec.generate_private_key(ec.SECP256R1())
    )
    private_path = tmp_path / f"{kind}-private.pem"
    public_path = tmp_path / f"{kind}-public.pem"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


@pytest.mark.parametrize("kind", ["ed25519", "es256"])
def test_confirmation_round_trip_and_idempotent_replay(tmp_path, kind):
    private_path, public_path = _key_files(tmp_path, kind)
    binding = ConfirmationBinding(
        audience="conformance",
        plugin_id="shadow-conformance",
        capability_id="conformance.records.publish",
        tool_name="conformance.records.publish",
        effect="publish",
        arguments={"record_id": "example", "body": {"title": "测试"}},
        resource_uri="shadow://conformance/records/example",
    )
    signer = ConfirmationSigner.from_pem_file(
        private_path,
        issuer="shadow-profile",
        key_id="test-key",
        ttl=timedelta(minutes=5),
    )
    receipt = signer.issue(
        binding,
        actor="agent-session",
        now=NOW,
        receipt_id="receipt-example",
        nonce="nonce-example-1234567890",
    )
    verifier = ConfirmationVerifier(
        {"test-key": public_path},
        allowed_issuers={"shadow-profile"},
        replay_store=ConfirmationReplayStore(tmp_path / "replay.sqlite3"),
    )
    encoded = encode_confirmation_receipt(receipt)

    first = verifier.verify_and_consume(
        encoded, binding, idempotency_key="request-1", now=NOW
    )
    replay = verifier.verify_and_consume(
        encoded, binding, idempotency_key="request-1", now=NOW
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert receipt["arguments_sha256"] == arguments_sha256(binding.arguments)
    assert receipt["signature"]["algorithm"] == ("EdDSA" if kind == "ed25519" else "ES256")


def test_confirmation_rejects_argument_changes_expiry_and_cross_request_replay(tmp_path):
    private_path, public_path = _key_files(tmp_path)
    binding = ConfirmationBinding(
        audience="conformance",
        plugin_id="shadow-conformance",
        capability_id="conformance.records.delete",
        tool_name="conformance.records.delete",
        effect="delete",
        arguments={"record_ids": ["a"]},
    )
    signer = ConfirmationSigner.from_pem_file(
        private_path, issuer="shadow-profile", key_id="test-key", ttl=timedelta(seconds=30)
    )
    encoded = encode_confirmation_receipt(
        signer.issue(binding, actor="agent", now=NOW, nonce="nonce-example-1234567890")
    )
    verifier = ConfirmationVerifier(
        {"test-key": public_path},
        allowed_issuers={"shadow-profile"},
        replay_store=ConfirmationReplayStore(tmp_path / "replay.sqlite3"),
    )

    changed = ConfirmationBinding(
        audience=binding.audience,
        plugin_id=binding.plugin_id,
        capability_id=binding.capability_id,
        tool_name=binding.tool_name,
        effect=binding.effect,
        arguments={"record_ids": ["a", "b"]},
    )
    with pytest.raises(ConfirmationError, match="arguments_sha256"):
        verifier.verify_and_consume(encoded, changed, idempotency_key="request-1", now=NOW)

    verifier.verify_and_consume(encoded, binding, idempotency_key="request-1", now=NOW)
    with pytest.raises(ConfirmationError, match="already been consumed"):
        verifier.verify_and_consume(encoded, binding, idempotency_key="request-2", now=NOW)
    with pytest.raises(ConfirmationError, match="expired"):
        verifier.verify_and_consume(
            encoded, binding, idempotency_key="request-1", now=NOW + timedelta(minutes=2)
        )


def test_destructive_limits_always_preserve_at_least_one_item():
    enforce_destructive_limits(requested_items=9, total_before=10)

    with pytest.raises(ConfirmationError, match="leave at least 1"):
        enforce_destructive_limits(requested_items=10, total_before=10)


def test_confirmation_rejects_unsupported_fields_and_excessive_lifespan(tmp_path):
    private_path, public_path = _key_files(tmp_path)
    binding = ConfirmationBinding(
        audience="conformance",
        plugin_id="shadow-conformance",
        capability_id="conformance.records.publish",
        tool_name="conformance.records.publish",
        effect="publish",
        arguments={"record_id": "example"},
    )
    signer = ConfirmationSigner.from_pem_file(
        private_path, issuer="shadow-profile", key_id="test-key"
    )
    verifier = ConfirmationVerifier(
        {"test-key": public_path},
        allowed_issuers={"shadow-profile"},
        replay_store=ConfirmationReplayStore(tmp_path / "replay.sqlite3"),
    )

    receipt = signer.issue(binding, actor="agent", now=NOW)
    receipt["unexpected"] = True
    with pytest.raises(ConfirmationError, match="unsupported fields"):
        verifier.verify_and_consume(
            encode_confirmation_receipt(receipt),
            binding,
            idempotency_key="request-1",
            now=NOW,
        )

    long_lived = signer.issue(binding, actor="agent", now=NOW)
    long_lived["expires_at"] = "2026-08-23T12:30:00Z"
    unsigned = {name: value for name, value in long_lived.items() if name != "signature"}
    private_key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    long_lived["signature"]["value"] = (
        base64.urlsafe_b64encode(private_key.sign(canonical_json(unsigned)))
        .rstrip(b"=")
        .decode("ascii")
    )
    with pytest.raises(ConfirmationError, match="lifespan"):
        verifier.verify_and_consume(
            encode_confirmation_receipt(long_lived),
            binding,
            idempotency_key="request-2",
            now=NOW,
        )
