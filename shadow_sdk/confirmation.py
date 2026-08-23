from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519


class ConfirmationError(ValueError):
    """Raised when a confirmation receipt is malformed, invalid, expired, or replayed."""


@dataclass(frozen=True, slots=True)
class ConfirmationBinding:
    audience: str
    plugin_id: str
    capability_id: str
    tool_name: str
    effect: str
    arguments: Any
    resource_uri: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedConfirmation:
    receipt: Mapping[str, Any]
    replayed: bool


def canonical_json(value: Any) -> bytes:
    """Return the stable UTF-8 JSON representation used for signatures and bindings."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ConfirmationError("confirmation value must be lossless JSON") from exc
    return encoded.encode("utf-8")


def arguments_sha256(arguments: Any) -> str:
    return hashlib.sha256(canonical_json(arguments)).hexdigest()


def encode_confirmation_receipt(receipt: Mapping[str, Any]) -> str:
    return _b64url(canonical_json(dict(receipt)))


def decode_confirmation_receipt(value: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value or len(value) > 16384:
        raise ConfirmationError("confirmation receipt header is invalid")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfirmationError("confirmation receipt header is invalid") from exc
    if not isinstance(decoded, dict):
        raise ConfirmationError("confirmation receipt must contain an object")
    return decoded


class ConfirmationSigner:
    def __init__(
        self,
        private_key: ed25519.Ed25519PrivateKey | ec.EllipticCurvePrivateKey,
        *,
        issuer: str,
        key_id: str,
        ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        if not issuer or not key_id:
            raise ConfirmationError("confirmation issuer and key_id are required")
        if ttl <= timedelta(0) or ttl > timedelta(minutes=15):
            raise ConfirmationError("confirmation ttl must be between 1 second and 15 minutes")
        if not isinstance(private_key, (ed25519.Ed25519PrivateKey, ec.EllipticCurvePrivateKey)):
            raise ConfirmationError("confirmation key must be Ed25519 or EC")
        if isinstance(private_key, ec.EllipticCurvePrivateKey) and not isinstance(
            private_key.curve, ec.SECP256R1
        ):
            raise ConfirmationError("ES256 requires a P-256 private key")
        self._private_key = private_key
        self.issuer = issuer
        self.key_id = key_id
        self.ttl = ttl

    @classmethod
    def from_pem_file(
        cls,
        path: str | Path,
        *,
        issuer: str,
        key_id: str,
        ttl: timedelta = timedelta(minutes=5),
    ) -> ConfirmationSigner:
        try:
            key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
        except (OSError, ValueError, TypeError) as exc:
            raise ConfirmationError("cannot load confirmation private key") from exc
        return cls(key, issuer=issuer, key_id=key_id, ttl=ttl)

    def issue(
        self,
        binding: ConfirmationBinding,
        *,
        actor: str,
        now: datetime | None = None,
        receipt_id: str | None = None,
        nonce: str | None = None,
    ) -> dict[str, Any]:
        if not actor:
            raise ConfirmationError("confirmation actor is required")
        issued_at = _as_utc(now or datetime.now(UTC))
        unsigned: dict[str, Any] = {
            "version": 1,
            "receipt_id": receipt_id or f"receipt-{secrets.token_urlsafe(18)}",
            "issuer": self.issuer,
            "actor": actor,
            "audience": binding.audience,
            "plugin_id": binding.plugin_id,
            "capability_id": binding.capability_id,
            "tool_name": binding.tool_name,
            "effect": binding.effect,
            "arguments_sha256": arguments_sha256(binding.arguments),
            "issued_at": _format_time(issued_at),
            "expires_at": _format_time(issued_at + self.ttl),
            "nonce": nonce or secrets.token_urlsafe(24),
            "single_use": True,
        }
        if binding.resource_uri is not None:
            unsigned["resource_uri"] = binding.resource_uri
        algorithm, signature = _sign(self._private_key, canonical_json(unsigned))
        return {
            **unsigned,
            "signature": {
                "algorithm": algorithm,
                "key_id": self.key_id,
                "value": _b64url(signature),
            },
        }


class ConfirmationReplayStore:
    """Durable single-use receipt store with idempotent retry recognition."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS confirmation_receipts (
                    nonce TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL,
                    idempotency_sha256 TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT NOT NULL
                )
                """
            )

    def consume(
        self,
        *,
        nonce: str,
        receipt_id: str,
        idempotency_key: str,
        expires_at: datetime,
        now: datetime,
    ) -> bool:
        if not idempotency_key:
            raise ConfirmationError("idempotency key is required for confirmed operations")
        idempotency_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        now_text = _format_time(_as_utc(now))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM confirmation_receipts WHERE expires_at < ?", (now_text,)
            )
            try:
                connection.execute(
                    """
                    INSERT INTO confirmation_receipts
                        (nonce, receipt_id, idempotency_sha256, expires_at, consumed_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        nonce,
                        receipt_id,
                        idempotency_digest,
                        _format_time(expires_at),
                        now_text,
                    ),
                )
                return False
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """
                    SELECT receipt_id, idempotency_sha256
                    FROM confirmation_receipts WHERE nonce = ?
                    """,
                    (nonce,),
                ).fetchone()
                if row == (receipt_id, idempotency_digest):
                    return True
                raise ConfirmationError("confirmation receipt has already been consumed") from None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection


class ConfirmationVerifier:
    def __init__(
        self,
        public_keys: Mapping[str, str | Path | bytes],
        *,
        allowed_issuers: set[str] | frozenset[str],
        replay_store: ConfirmationReplayStore,
        clock_skew: timedelta = timedelta(seconds=30),
    ) -> None:
        if not public_keys or not allowed_issuers:
            raise ConfirmationError("confirmation keys and issuers are required")
        if clock_skew < timedelta(0) or clock_skew > timedelta(minutes=5):
            raise ConfirmationError("confirmation clock skew must be between 0 and 5 minutes")
        self._keys = {key_id: _load_public_key(value) for key_id, value in public_keys.items()}
        self.allowed_issuers = frozenset(allowed_issuers)
        self.replay_store = replay_store
        self.clock_skew = clock_skew

    def verify_and_consume(
        self,
        encoded_receipt: str,
        binding: ConfirmationBinding,
        *,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> VerifiedConfirmation:
        receipt = decode_confirmation_receipt(encoded_receipt)
        current = _as_utc(now or datetime.now(UTC))
        _validate_receipt_shape(receipt)
        signature = receipt["signature"]
        key = self._keys.get(signature["key_id"])
        if key is None:
            raise ConfirmationError("confirmation signing key is not trusted")
        unsigned = {name: value for name, value in receipt.items() if name != "signature"}
        _verify_signature(key, signature["algorithm"], canonical_json(unsigned), signature["value"])
        issued_at = _parse_time(receipt["issued_at"])
        expires_at = _parse_time(receipt["expires_at"])
        if receipt["issuer"] not in self.allowed_issuers:
            raise ConfirmationError("confirmation issuer is not trusted")
        if issued_at > current + self.clock_skew:
            raise ConfirmationError("confirmation receipt was issued in the future")
        if expires_at <= current - self.clock_skew or expires_at <= issued_at:
            raise ConfirmationError("confirmation receipt is expired")
        if expires_at - issued_at > timedelta(minutes=15):
            raise ConfirmationError("confirmation receipt lifespan exceeds 15 minutes")
        expected = {
            "audience": binding.audience,
            "plugin_id": binding.plugin_id,
            "capability_id": binding.capability_id,
            "tool_name": binding.tool_name,
            "effect": binding.effect,
            "arguments_sha256": arguments_sha256(binding.arguments),
        }
        if binding.resource_uri is not None:
            expected["resource_uri"] = binding.resource_uri
        for field, value in expected.items():
            if receipt.get(field) != value:
                raise ConfirmationError(f"confirmation receipt does not match {field}")
        replayed = self.replay_store.consume(
            nonce=receipt["nonce"],
            receipt_id=receipt["receipt_id"],
            idempotency_key=idempotency_key,
            expires_at=expires_at,
            now=current,
        )
        return VerifiedConfirmation(receipt=receipt, replayed=replayed)


def enforce_destructive_limits(
    *, requested_items: int, total_before: int, min_remaining: int = 1
) -> None:
    """Reject a delete that would remove all records from its protected resource set."""

    if requested_items < 1 or total_before < 1 or min_remaining < 1:
        raise ConfirmationError("destructive limit values must be positive")
    if requested_items > total_before - min_remaining:
        raise ConfirmationError(
            f"delete must leave at least {min_remaining} protected item(s) remaining"
        )


def _validate_receipt_shape(receipt: Mapping[str, Any]) -> None:
    required = {
        "version",
        "receipt_id",
        "issuer",
        "actor",
        "audience",
        "plugin_id",
        "capability_id",
        "tool_name",
        "effect",
        "arguments_sha256",
        "issued_at",
        "expires_at",
        "nonce",
        "single_use",
        "signature",
    }
    allowed = required | {"resource_uri"}
    if set(receipt) - allowed:
        raise ConfirmationError("confirmation receipt contains unsupported fields")
    if receipt.get("version") != 1 or receipt.get("single_use") is not True:
        raise ConfirmationError("unsupported confirmation receipt")
    if not required.issubset(receipt) or not isinstance(receipt.get("signature"), dict):
        raise ConfirmationError("confirmation receipt is incomplete")
    text_fields = required - {"version", "single_use", "signature"}
    if any(not isinstance(receipt.get(field), str) or not receipt[field] for field in text_fields):
        raise ConfirmationError("confirmation receipt text fields are invalid")
    if "resource_uri" in receipt and (
        not isinstance(receipt["resource_uri"], str) or not receipt["resource_uri"]
    ):
        raise ConfirmationError("confirmation resource_uri is invalid")
    if len(receipt["arguments_sha256"]) != 64 or any(
        character not in "0123456789abcdef" for character in receipt["arguments_sha256"]
    ):
        raise ConfirmationError("confirmation arguments_sha256 is invalid")
    signature = receipt["signature"]
    if set(signature) != {"algorithm", "key_id", "value"} or any(
        not isinstance(signature.get(field), str) or not signature[field]
        for field in ("algorithm", "key_id", "value")
    ):
        raise ConfirmationError("confirmation signature is invalid")
    if signature["algorithm"] not in {"EdDSA", "ES256"}:
        raise ConfirmationError("confirmation algorithm is unsupported")


def _load_public_key(value: str | Path | bytes):
    try:
        data = value if isinstance(value, bytes) else Path(value).read_bytes()
        key = serialization.load_pem_public_key(data)
    except (OSError, ValueError, TypeError) as exc:
        raise ConfirmationError("cannot load confirmation public key") from exc
    if isinstance(key, ec.EllipticCurvePublicKey) and not isinstance(key.curve, ec.SECP256R1):
        raise ConfirmationError("ES256 requires a P-256 public key")
    if not isinstance(key, (ed25519.Ed25519PublicKey, ec.EllipticCurvePublicKey)):
        raise ConfirmationError("confirmation key must be Ed25519 or EC")
    return key


def _sign(key, payload: bytes) -> tuple[str, bytes]:
    if isinstance(key, ed25519.Ed25519PrivateKey):
        return "EdDSA", key.sign(payload)
    return "ES256", key.sign(payload, ec.ECDSA(hashes.SHA256()))


def _verify_signature(key, algorithm: str, payload: bytes, encoded_signature: str) -> None:
    try:
        signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
        if algorithm == "EdDSA" and isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(signature, payload)
        elif algorithm == "ES256" and isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, payload, ec.ECDSA(hashes.SHA256()))
        else:
            raise ConfirmationError("confirmation algorithm does not match its key")
    except (ValueError, InvalidSignature) as exc:
        raise ConfirmationError("confirmation signature is invalid") from exc


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ConfirmationError("confirmation timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ConfirmationError("confirmation timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfirmationError("confirmation timestamp is invalid") from exc
    return _as_utc(parsed)
