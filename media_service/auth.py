from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from shadow_sdk.service_auth import ServiceAuthError, authenticate_service_token


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    app_id: str


def require_service(request: Request) -> ServiceIdentity:
    try:
        app_id = authenticate_service_token(
            request.headers.get("authorization", ""),
            request.app.state.settings.service_token_hashes,
        )
    except ServiceAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return ServiceIdentity(app_id=app_id)
