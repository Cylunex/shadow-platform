from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from .auth import CSRF_COOKIE, SESSION_COOKIE, DbDep, digest, new_csrf_token, require_csrf
from .models import BrowserSession, LocalIdentity, OidcTransaction

router = APIRouter()
BINDING_COOKIE = "__Host-shadow-notify-login"
ALLOWED_JWT_ALGORITHMS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def pkce_challenge(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode()).digest())


def safe_return_to(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/inbox"
    return value[:1000]


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def _redirect_uri(request: Request) -> str:
    settings = request.app.state.settings
    candidate = f"{request.url.scheme}://{request.url.netloc}/auth/callback"
    if candidate not in settings.oidc_callbacks:
        raise HTTPException(status_code=400, detail="untrusted callback origin")
    return candidate


def _discovery(issuer: str) -> dict:
    try:
        response = httpx.get(issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=5.0)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="identity provider unavailable") from exc
    if payload.get("issuer") != issuer:
        raise HTTPException(status_code=502, detail="OIDC issuer mismatch")
    return payload


@router.get("/login")
def login(request: Request, db: DbDep, return_to: str | None = None):
    settings = request.app.state.settings
    if not settings.oidc_issuer or not settings.session_secret:
        raise HTTPException(status_code=503, detail="browser authentication is not configured")
    metadata = _discovery(settings.oidc_issuer)
    redirect_uri = _redirect_uri(request)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    binding = secrets.token_urlsafe(32)
    db.add(
        OidcTransaction(
            state_hash=digest(state),
            browser_binding_hash=digest(binding),
            nonce_hash=digest(nonce),
            pkce_verifier_ciphertext=_fernet(settings.session_secret).encrypt(verifier.encode()),
            redirect_uri=redirect_uri,
            return_to=safe_return_to(return_to),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    )
    db.commit()
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email groups",
        "state": state,
        "nonce": nonce,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    response = RedirectResponse(
        f"{metadata['authorization_endpoint']}?{urllib.parse.urlencode(params)}", status_code=302
    )
    response.set_cookie(
        BINDING_COOKIE,
        binding,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=600,
    )
    return response


def _claims(id_token: str, metadata: dict, request: Request) -> dict:
    settings = request.app.state.settings
    try:
        header = jwt.get_unverified_header(id_token)
        algorithm = str(header.get("alg", ""))
        if algorithm not in ALLOWED_JWT_ALGORITHMS:
            raise ValueError("unsupported ID Token algorithm")
        response = httpx.get(metadata["jwks_uri"], timeout=5.0)
        response.raise_for_status()
        key_set = jwt.PyJWKSet.from_dict(response.json())
        keys = [key.key for key in key_set.keys if key.key_id == header.get("kid")]
        if len(keys) != 1:
            raise ValueError("unknown signing key")
        return jwt.decode(
            id_token,
            keys[0],
            algorithms=[algorithm],
            audience=settings.oidc_client_id,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        )
    except (httpx.HTTPError, ValueError, KeyError, jwt.PyJWTError) as exc:
        raise HTTPException(status_code=401, detail="invalid ID Token") from exc


def _profile(tokens: dict, claims: dict, metadata: dict) -> dict:
    groups = claims.get("groups")
    if isinstance(groups, list) and all(isinstance(item, str) for item in groups):
        return claims
    access_token = tokens.get("access_token")
    endpoint = metadata.get("userinfo_endpoint")
    if not isinstance(access_token, str) or not endpoint:
        raise HTTPException(status_code=401, detail="OIDC groups unavailable")
    try:
        response = httpx.get(
            endpoint, headers={"Authorization": f"Bearer {access_token}"}, timeout=5.0
        )
        response.raise_for_status()
        profile = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="OIDC userinfo failed") from exc
    if not secrets.compare_digest(str(profile.get("sub", "")), str(claims.get("sub", ""))):
        raise HTTPException(status_code=401, detail="OIDC userinfo subject mismatch")
    return {**claims, **profile, "sub": claims["sub"]}


@router.get("/auth/callback")
def callback(request: Request, db: DbDep, code: str, state: str):
    settings = request.app.state.settings
    binding = request.cookies.get(BINDING_COOKIE)
    if not binding:
        raise HTTPException(status_code=401, detail="login browser binding missing")
    transaction = db.scalar(
        select(OidcTransaction).where(OidcTransaction.state_hash == digest(state)).with_for_update()
    )
    now = datetime.now(UTC)
    if (
        transaction is None
        or transaction.consumed_at is not None
        or transaction.expires_at.replace(tzinfo=transaction.expires_at.tzinfo or UTC) <= now
    ):
        raise HTTPException(status_code=401, detail="OIDC state invalid or expired")
    if not secrets.compare_digest(transaction.browser_binding_hash, digest(binding)):
        raise HTTPException(status_code=401, detail="login browser binding mismatch")
    if _redirect_uri(request) != transaction.redirect_uri:
        raise HTTPException(status_code=401, detail="OIDC callback mismatch")
    transaction.consumed_at = now
    db.commit()
    try:
        verifier = (
            _fernet(settings.session_secret).decrypt(transaction.pkce_verifier_ciphertext).decode()
        )
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="OIDC transaction cannot be decrypted") from exc
    metadata = _discovery(settings.oidc_issuer)
    try:
        response = httpx.post(
            metadata["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": transaction.redirect_uri,
                "code_verifier": verifier,
            },
            auth=(settings.oidc_client_id, settings.oidc_client_secret),
            timeout=5.0,
        )
        response.raise_for_status()
        tokens = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="OIDC code exchange failed") from exc
    claims = _claims(str(tokens.get("id_token", "")), metadata, request)
    if not secrets.compare_digest(digest(str(claims.get("nonce", ""))), transaction.nonce_hash):
        raise HTTPException(status_code=401, detail="OIDC nonce mismatch")
    profile = _profile(tokens, claims, metadata)
    groups = profile.get("groups")
    if not isinstance(groups, list) or not all(isinstance(item, str) for item in groups):
        raise HTTPException(status_code=401, detail="OIDC groups invalid")
    identity = db.scalar(
        select(LocalIdentity).where(
            LocalIdentity.issuer == settings.oidc_issuer,
            LocalIdentity.subject == claims["sub"],
        )
    )
    if identity is None:
        identity = LocalIdentity(issuer=settings.oidc_issuer, subject=str(claims["sub"]))
        db.add(identity)
        db.flush()
    identity.username = str(profile.get("preferred_username") or claims["sub"])
    identity.display_name = str(profile.get("name") or identity.username)
    identity.email = str(profile.get("email") or "")
    identity.last_login_at = now
    handle = secrets.token_urlsafe(48)
    db.add(
        BrowserSession(
            identity_id=identity.id,
            session_hash=digest(handle),
            groups_snapshot=groups,
            expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
        )
    )
    db.commit()
    response = RedirectResponse(transaction.return_to, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        handle,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=settings.session_ttl_seconds,
    )
    response.set_cookie(
        CSRF_COOKIE,
        new_csrf_token(),
        secure=True,
        httponly=False,
        samesite="lax",
        path="/",
        max_age=settings.session_ttl_seconds,
    )
    response.delete_cookie(BINDING_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    return response


@router.post("/logout")
def logout(request: Request, db: DbDep, _: None = Depends(require_csrf)):
    handle = request.cookies.get(SESSION_COOKIE)
    if handle:
        session = db.scalar(
            select(BrowserSession).where(BrowserSession.session_hash == digest(handle))
        )
        if session:
            session.revoked_at = datetime.now(UTC)
            db.commit()
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    response.delete_cookie(CSRF_COOKIE, secure=True, httponly=False, samesite="lax", path="/")
    return response
