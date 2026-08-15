from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .auth import ServiceIdentity, require_service
from .config import Settings
from .database import Base, create_database
from .models import MediaObject, UploadIntent
from .schemas import AccessGrant, DeleteResult, MediaView, UploadCreate, UploadCreated, UploadTarget
from .storage import MIME_EXTENSION, LocalStorage, StorageError


def utcnow() -> datetime:
    return datetime.now(UTC)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def media_access_token(media_id: str, expires_at: datetime, key: str) -> str:
    payload = f"{media_id}.{int(expires_at.timestamp())}"
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{payload}.{encoded}"


def verify_media_access_token(media_id: str, supplied: str, key: str) -> bool:
    if not key:
        return False
    parts = supplied.split(".")
    if len(parts) != 3 or parts[0] != media_id:
        return False
    try:
        expires = int(parts[1])
    except ValueError:
        return False
    if expires < int(time.time()):
        return False
    expected = media_access_token(media_id, datetime.fromtimestamp(expires, UTC), key)
    return hmac.compare_digest(expected, supplied)


def get_db(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


ServiceDep = Annotated[ServiceIdentity, Depends(require_service)]
DbDep = Annotated[Session, Depends(get_db)]


def _same_mime(declared: str, actual: str) -> bool:
    declared = declared.split(";", 1)[0].strip().lower()
    return declared == actual or {declared, actual} <= {"image/heic", "image/heif"}


def safe_filename(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(character if character.isprintable() else "_" for character in name)
    return name if name not in {"", ".", ".."} else "image"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    engine, session_factory = create_database(resolved.database_url)
    storage = LocalStorage(resolved.storage_root, strip_metadata=resolved.strip_metadata)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    expose_docs = resolved.environment != "production"
    app = FastAPI(
        title="Shadow Media",
        version="0.3.0",
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.state.settings = resolved
    app.state.session_factory = session_factory
    app.state.storage = storage

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz(db: DbDep):
        try:
            db.execute(text("SELECT 1"))
            storage.root.mkdir(parents=True, exist_ok=True)
            if not storage.root.is_dir():
                raise OSError("storage root is unavailable")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="media dependencies unavailable") from exc
        return {"status": "ready"}

    @app.post("/v1/uploads", response_model=UploadCreated, status_code=201)
    def create_upload(
        body: UploadCreate,
        request: Request,
        identity: ServiceDep,
        db: DbDep,
    ):
        content_type = body.content_type.split(";", 1)[0].strip().lower()
        if content_type not in resolved.allowed_mime_types:
            raise HTTPException(status_code=400, detail="unsupported image content type")
        if body.size_bytes > resolved.max_upload_bytes:
            raise HTTPException(status_code=413, detail="image exceeds configured size limit")

        upload_id = str(uuid.uuid4())
        upload_token = secrets.token_urlsafe(32)
        now = utcnow()
        expires_at = now + timedelta(seconds=resolved.upload_ttl_seconds)
        extension = MIME_EXTENSION[content_type]
        storage_key = f"{identity.app_id}/{now:%Y/%m}/{upload_id}{extension}"
        intent = UploadIntent(
            id=upload_id,
            app_id=identity.app_id,
            owner_sub=body.owner_sub,
            resource_type=body.resource_type,
            resource_id=body.resource_id,
            visibility=body.visibility,
            original_filename=safe_filename(body.original_filename),
            declared_mime=content_type,
            declared_size=body.size_bytes,
            storage_backend="local",
            storage_key=storage_key,
            upload_token_hash=token_hash(upload_token),
            status="pending",
            created_at=now,
            expires_at=expires_at,
        )
        db.add(intent)
        db.commit()
        return UploadCreated(
            upload_id=upload_id,
            expires_at=expires_at,
            target=UploadTarget(
                url=f"{resolved.public_base_url}/v1/uploads/{upload_id}/content",
                headers={
                    "Authorization": f"Upload {upload_token}",
                    "Content-Type": content_type,
                },
            ),
        )

    @app.put("/v1/uploads/{upload_id}/content", status_code=204)
    async def upload_content(upload_id: str, request: Request, db: DbDep):
        intent = db.get(UploadIntent, upload_id)
        if not intent or intent.status != "pending":
            raise HTTPException(status_code=404, detail="active upload not found")
        if intent.expires_at.replace(tzinfo=UTC) < utcnow():
            intent.status = "expired"
            db.commit()
            raise HTTPException(status_code=410, detail="upload intent expired")

        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "upload" or not secrets.compare_digest(
            token_hash(supplied), intent.upload_token_hash
        ):
            raise HTTPException(status_code=401, detail="invalid upload token")
        if not _same_mime(request.headers.get("content-type", ""), intent.declared_mime):
            raise HTTPException(status_code=400, detail="content type does not match declaration")

        try:
            stored = await storage.put(
                intent.storage_key,
                request.stream(),
                declared_size=intent.declared_size,
                max_size=resolved.max_upload_bytes,
            )
            if not _same_mime(intent.declared_mime, stored.content_type):
                storage.delete(intent.storage_key)
                raise StorageError("decoded image type does not match declaration")
        except StorageError as exc:
            intent.status = "failed"
            intent.failure_reason = str(exc)
            db.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        intent.actual_mime = stored.content_type
        intent.actual_size = stored.size_bytes
        intent.sha256 = stored.sha256
        intent.width = stored.width
        intent.height = stored.height
        intent.status = "uploaded"
        intent.uploaded_at = utcnow()
        db.commit()

    @app.post("/v1/uploads/{upload_id}/complete", response_model=MediaView)
    def complete_upload(
        upload_id: str,
        identity: ServiceDep,
        db: DbDep,
    ):
        intent = db.get(UploadIntent, upload_id)
        if not intent or intent.app_id != identity.app_id:
            raise HTTPException(status_code=404, detail="upload not found")
        if intent.status == "completed" and intent.media_id:
            existing = db.get(MediaObject, intent.media_id)
            if existing:
                return existing
        if intent.status != "uploaded":
            raise HTTPException(status_code=409, detail=f"upload is {intent.status}")

        now = utcnow()
        media = MediaObject(
            id=str(uuid.uuid4()),
            app_id=intent.app_id,
            owner_sub=intent.owner_sub,
            resource_type=intent.resource_type,
            resource_id=intent.resource_id,
            visibility=intent.visibility,
            original_filename=intent.original_filename,
            content_type=intent.actual_mime or intent.declared_mime,
            size_bytes=intent.actual_size or intent.declared_size,
            sha256=intent.sha256 or "",
            width=intent.width or 0,
            height=intent.height or 0,
            storage_backend=intent.storage_backend,
            storage_key=intent.storage_key,
            status="ready",
            created_at=now,
        )
        db.add(media)
        db.flush()
        intent.status = "completed"
        intent.media_id = media.id
        intent.completed_at = now
        db.commit()
        return media

    @app.get("/v1/media/{media_id}", response_model=MediaView)
    def get_media(
        media_id: str,
        identity: ServiceDep,
        db: DbDep,
    ):
        media = db.get(MediaObject, media_id)
        if not media or media.app_id != identity.app_id or media.status != "ready":
            raise HTTPException(status_code=404, detail="media not found")
        return media

    @app.post("/v1/media/{media_id}/access", response_model=AccessGrant)
    def grant_access(
        media_id: str,
        identity: ServiceDep,
        db: DbDep,
    ):
        media = db.get(MediaObject, media_id)
        if not media or media.app_id != identity.app_id or media.status != "ready":
            raise HTTPException(status_code=404, detail="media not found")
        base = f"{resolved.public_base_url}/v1/content/{media.id}"
        if media.visibility == "public":
            return AccessGrant(url=base, expires_at=None)
        if not resolved.access_signing_key:
            raise HTTPException(status_code=503, detail="private media signing is not configured")
        expires_at = utcnow() + timedelta(seconds=resolved.access_ttl_seconds)
        token = media_access_token(media.id, expires_at, resolved.access_signing_key)
        return AccessGrant(url=f"{base}?token={token}", expires_at=expires_at)

    @app.get("/v1/content/{media_id}", response_class=FileResponse)
    def media_content(
        media_id: str,
        db: DbDep,
        token: Annotated[str, Query()] = "",
    ):
        media = db.get(MediaObject, media_id)
        if not media or media.status != "ready":
            raise HTTPException(status_code=404, detail="media not found")
        if media.visibility != "public" and not verify_media_access_token(
            media_id, token, resolved.access_signing_key
        ):
            raise HTTPException(status_code=403, detail="valid media access grant required")
        path = storage.path_for(media.storage_key)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="media object missing")
        return FileResponse(
            path,
            media_type=media.content_type,
            filename=media.original_filename,
            content_disposition_type="inline",
        )

    @app.delete("/v1/media/{media_id}", response_model=DeleteResult)
    def delete_media(
        media_id: str,
        identity: ServiceDep,
        db: DbDep,
    ):
        media = db.scalar(
            select(MediaObject).where(
                MediaObject.id == media_id,
                MediaObject.app_id == identity.app_id,
                MediaObject.status == "ready",
            )
        )
        if not media:
            raise HTTPException(status_code=404, detail="media not found")
        now = utcnow()
        media.status = "deleted"
        media.deleted_at = now
        media.delete_after = now + timedelta(days=resolved.soft_delete_retention_days)
        db.commit()
        return DeleteResult(id=media.id, status="deleted", delete_after=media.delete_after)

    return app


app = create_app()
