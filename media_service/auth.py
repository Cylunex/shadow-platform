from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    app_id: str


def require_service(request: Request) -> ServiceIdentity:
    authorization = request.headers.get("authorization", "")
    scheme, _, supplied = authorization.partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        raise HTTPException(status_code=401, detail="service bearer token required")

    for app_id, expected in request.app.state.settings.service_tokens.items():
        if secrets.compare_digest(supplied, expected):
            return ServiceIdentity(app_id=app_id)
    raise HTTPException(status_code=401, detail="invalid service bearer token")
