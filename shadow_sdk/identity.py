from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """Normalized claims after an OIDC library has verified the token.

    This class deliberately does not decode or validate JWTs. Applications must use a
    standards-compliant OIDC client to verify signature, issuer, audience, state, nonce,
    expiry and PKCE before constructing this value.
    """

    issuer: str
    subject: str
    username: str
    display_name: str
    email: str
    groups: tuple[str, ...]

    @property
    def external_key(self) -> tuple[str, str]:
        return self.issuer, self.subject

    @classmethod
    def from_verified_claims(cls, claims: Mapping[str, Any]) -> VerifiedIdentity:
        issuer = str(claims.get("iss") or "").strip()
        subject = str(claims.get("sub") or "").strip()
        if not issuer or not subject:
            raise ValueError("verified OIDC claims must include non-empty iss and sub")
        raw_groups = claims.get("groups") or []
        if isinstance(raw_groups, str):
            raw_groups = [raw_groups]
        if not isinstance(raw_groups, (list, tuple)):
            raise ValueError("groups claim must be a string or array")
        return cls(
            issuer=issuer,
            subject=subject,
            username=str(claims.get("preferred_username") or subject),
            display_name=str(claims.get("name") or claims.get("preferred_username") or subject),
            email=str(claims.get("email") or ""),
            groups=tuple(str(group) for group in raw_groups),
        )
