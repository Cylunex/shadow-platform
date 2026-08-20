from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from shadow_sdk.service_auth import ServiceAuthError, authenticate_service_token

from .models import BrowserSession, LocalIdentity

SESSION_COOKIE = "__Host-shadow-notify-session"
CSRF_COOKIE = "__Host-shadow-notify-csrf"


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def get_db(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


DbDep = Annotated[Session, Depends(get_db)]


def require_service(request: Request) -> str:
    try:
        return authenticate_service_token(
            request.headers.get("authorization", ""),
            request.app.state.settings.service_token_hashes,
        )
    except ServiceAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


ServiceDep = Annotated[str, Depends(require_service)]


@dataclass(frozen=True, slots=True)
class UserIdentity:
    issuer: str
    subject: str
    display_name: str
    groups: frozenset[str]

    @property
    def owner_key(self) -> tuple[str, str]:
        return self.issuer, self.subject


def current_user(request: Request, db: DbDep) -> UserIdentity:
    settings = request.app.state.settings
    if settings.dev_auth and settings.environment != "production":
        subject = request.headers.get("X-Dev-User", "dev-user")
        groups = frozenset(
            item.strip()
            for item in request.headers.get("X-Dev-Groups", settings.admin_group).split(",")
            if item.strip()
        )
        return UserIdentity("dev://shadow", subject, subject, groups)
    handle = request.cookies.get(SESSION_COOKIE)
    if not handle:
        raise HTTPException(status_code=401, detail="login required")
    row = db.scalar(
        select(BrowserSession).where(
            BrowserSession.session_hash == digest(handle),
            BrowserSession.revoked_at.is_(None),
            BrowserSession.expires_at > datetime.now(UTC),
        )
    )
    if row is None:
        raise HTTPException(status_code=401, detail="session expired")
    identity = db.get(LocalIdentity, row.identity_id)
    if identity is None or not identity.enabled:
        raise HTTPException(status_code=403, detail="identity disabled")
    row.last_seen_at = datetime.now(UTC)
    db.commit()
    return UserIdentity(
        identity.issuer,
        identity.subject,
        identity.display_name or identity.username or identity.subject,
        frozenset(row.groups_snapshot),
    )


UserDep = Annotated[UserIdentity, Depends(current_user)]


def require_admin(request: Request, user: UserDep) -> UserIdentity:
    if request.app.state.settings.admin_group not in user.groups:
        raise HTTPException(status_code=403, detail="platform administrator group required")
    return user


AdminDep = Annotated[UserIdentity, Depends(require_admin)]


def require_csrf(request: Request) -> None:
    settings = request.app.state.settings
    if settings.dev_auth and settings.environment != "production":
        return
    if request.headers.get("Origin") not in settings.allowed_origins:
        raise HTTPException(status_code=403, detail="untrusted origin")
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get("X-CSRF-Token")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
