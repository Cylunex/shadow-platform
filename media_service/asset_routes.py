from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .asset_models import (
    Asset,
    AssetAuditEvent,
    AssetBlob,
    AssetBlobLocation,
    AssetDerivative,
    AssetOutboxEvent,
    AssetReference,
    AssetUploadSession,
    AssetVersion,
)
from .asset_schemas import (
    AssetAccessGrant,
    AssetAccessRequest,
    AssetDerivativeCreate,
    AssetDerivativeView,
    AssetReferenceCreate,
    AssetReferenceDelegationCreate,
    AssetReferenceRelease,
    AssetReferenceResolution,
    AssetReferenceView,
    AssetUploadCreate,
    AssetUploadCreated,
    AssetUploadTarget,
    AssetVersionUploadCreate,
    AssetVersionView,
    AssetView,
)
from .auth import ServiceIdentity, require_service
from .storage import StorageError

router = APIRouter(tags=["assets"])


def utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _db(request: Request):
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


DbDep = Annotated[Session, Depends(_db)]
ServiceDep = Annotated[ServiceIdentity, Depends(require_service)]


def _safe_filename(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(character if character.isprintable() else "_" for character in name)
    return name if name not in {"", ".", ".."} else "asset"


def _signing_key(request: Request) -> str:
    configured = request.app.state.settings.access_signing_key
    return configured or "shadow-development-asset-signing-key"


def _upload_token(session_id: str, expires_at: datetime, key: str) -> str:
    expires_at = _as_utc(expires_at)
    payload = f"asset-upload.{session_id}.{int(expires_at.timestamp())}"
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{payload}.{encoded}"


def _access_token(version_id: str, operation: str, expires_at: datetime, key: str) -> str:
    payload = f"asset-content.{version_id}.{operation}.{int(expires_at.timestamp())}"
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{payload}.{encoded}"


def _valid_access_token(version_id: str, operation: str, supplied: str, key: str) -> bool:
    parts = supplied.split(".")
    if len(parts) != 5 or parts[:3] != ["asset-content", version_id, operation]:
        return False
    try:
        expires_at = datetime.fromtimestamp(int(parts[3]), UTC)
    except ValueError:
        return False
    if expires_at.timestamp() < time.time():
        return False
    return hmac.compare_digest(_access_token(version_id, operation, expires_at, key), supplied)


def _request_fingerprint(body: AssetUploadCreate) -> str:
    encoded = json.dumps(
        body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _version_request_fingerprint(asset_id: str, body: AssetVersionUploadCreate) -> str:
    payload = {"asset_id": asset_id, **body.model_dump(mode="json")}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _asset_view(db: Session, asset: Asset) -> AssetView:
    if not asset.current_version_id:
        raise HTTPException(status_code=409, detail="asset has no current version")
    version = db.get(AssetVersion, asset.current_version_id)
    if not version or version.asset_id != asset.id:
        raise HTTPException(status_code=409, detail="asset current version is invalid")
    return AssetView(
        id=asset.id,
        owner_id=asset.owner_id,
        created_by_app_id=asset.created_by_app_id,
        ownership_mode=asset.ownership_mode,
        display_name=asset.display_name,
        access_mode=asset.access_mode,
        sensitivity=asset.sensitivity,
        retention_policy_key=asset.retention_policy_key,
        lifecycle_state=asset.lifecycle_state,
        current_version_id=version.id,
        zero_referenced_at=asset.zero_referenced_at,
        created_at=asset.created_at,
        trashed_at=asset.trashed_at,
        purge_after=asset.purge_after,
        current_version=AssetVersionView.model_validate(version),
    )


def _service_reference(
    db: Session, asset_id: str, app_id: str
) -> AssetReference | None:
    return db.scalar(
        select(AssetReference).where(
            AssetReference.asset_id == asset_id,
            AssetReference.app_id == app_id,
            AssetReference.state == "active",
        ).limit(1)
    )


def _service_can_read(db: Session, asset: Asset, app_id: str) -> bool:
    if asset.created_by_app_id == app_id or asset.access_mode == "public":
        return True
    reference = _service_reference(db, asset.id, app_id)
    if reference is None:
        return False
    return asset.access_mode == "delegated" or (
        reference.delegated_by_app_id == asset.created_by_app_id
    )


def _resource_app_id(resource_uri: str) -> str:
    return resource_uri.removeprefix("shadow://").split("/", 1)[0]


def _resolved_reference(db: Session, reference: AssetReference) -> AssetReferenceResolution:
    asset = db.get(Asset, reference.asset_id)
    if (
        not asset
        or asset.lifecycle_state != "active"
        or not _service_can_read(db, asset, reference.app_id)
    ):
        raise HTTPException(status_code=404, detail="asset reference not found")
    version_id = (
        reference.pinned_version_id
        if reference.binding_mode == "pinned"
        else asset.current_version_id
    )
    version = db.get(AssetVersion, version_id) if version_id else None
    if not version or version.asset_id != asset.id or version.state != "ready":
        raise HTTPException(status_code=409, detail="asset reference version is unavailable")
    return AssetReferenceResolution(
        reference=AssetReferenceView.model_validate(reference),
        asset=_asset_view(db, asset),
        resolved_version_id=version.id,
    )


def _audit(
    db: Session,
    *,
    identity: ServiceIdentity,
    action: str,
    asset_id: str | None,
    details: dict[str, object] | None = None,
) -> None:
    db.add(
        AssetAuditEvent(
            app_id=identity.app_id,
            actor=f"service:{identity.app_id}",
            action=action,
            asset_id=asset_id,
            details=details or {},
            created_at=utcnow(),
        )
    )


def _outbox(
    db: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, object],
) -> None:
    db.add(
        AssetOutboxEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            created_at=utcnow(),
        )
    )


def _asset_upload_response(
    settings,
    *,
    session_id: str,
    expires_at: datetime,
    token: str,
    declared_mime: str,
) -> AssetUploadCreated:
    headers = {
        "Authorization": f"Upload {token}",
        "Content-Type": declared_mime,
    }
    path = f"/v1/upload-sessions/{session_id}/content"
    target = AssetUploadTarget(
        url=f"{settings.public_base_url}{path}",
        headers=headers,
        route="canonical",
    )
    alternates: list[AssetUploadTarget] = []
    seen = {settings.public_base_url.rstrip("/")}
    for index, base_url in enumerate(settings.asset_upload_base_urls, start=1):
        normalized = base_url.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        alternates.append(
            AssetUploadTarget(
                url=f"{normalized}{path}",
                headers=headers,
                route=f"alternate-{index}",
            )
        )
    return AssetUploadCreated(
        upload_session_id=session_id,
        expires_at=expires_at,
        target=target,
        alternate_targets=alternates,
    )


@router.post("/v1/upload-sessions", response_model=AssetUploadCreated, status_code=201)
def create_asset_upload(
    body: AssetUploadCreate,
    request: Request,
    identity: ServiceDep,
    db: DbDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    settings = request.app.state.settings
    declared_mime = body.content_type.split(";", 1)[0].strip().lower()
    if declared_mime not in settings.asset_allowed_mime_types:
        raise HTTPException(status_code=400, detail="unsupported asset content type")
    if body.size_bytes > settings.asset_max_upload_bytes:
        raise HTTPException(status_code=413, detail="asset exceeds configured size limit")
    if idempotency_key is not None and not 1 <= len(idempotency_key) <= 255:
        raise HTTPException(status_code=400, detail="invalid Idempotency-Key")

    fingerprint = _request_fingerprint(body)
    if body.initial_reference and (
        _resource_app_id(body.initial_reference.resource_uri) != identity.app_id
    ):
        raise HTTPException(
            status_code=400, detail="initial resource URI must belong to calling app"
        )
    existing = None
    if idempotency_key:
        existing = db.scalar(
            select(AssetUploadSession).where(
                AssetUploadSession.app_id == identity.app_id,
                AssetUploadSession.idempotency_key == idempotency_key,
            )
        )
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="Idempotency-Key payload mismatch")
        expires_at = _as_utc(existing.expires_at)
        token = _upload_token(existing.id, expires_at, _signing_key(request))
        return _asset_upload_response(
            settings,
            session_id=existing.id,
            expires_at=expires_at,
            token=token,
            declared_mime=existing.declared_mime,
        )

    session_id = str(uuid.uuid4())
    now = utcnow()
    expires_at = now + timedelta(seconds=settings.asset_upload_ttl_seconds)
    token = _upload_token(session_id, expires_at, _signing_key(request))
    initial = body.initial_reference
    session = AssetUploadSession(
        id=session_id,
        app_id=identity.app_id,
        owner_id=str(body.owner_id),
        ownership_mode=body.ownership_mode,
        access_mode=body.access_mode,
        sensitivity=body.sensitivity,
        retention_policy_key=body.retention_policy_key,
        display_name=body.display_name or _safe_filename(body.original_filename),
        original_filename=_safe_filename(body.original_filename),
        declared_mime=declared_mime,
        declared_size=body.size_bytes,
        staging_key=session_id,
        upload_token_hash=_hash_token(token),
        status="pending",
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        initial_resource_uri=initial.resource_uri if initial else None,
        initial_usage_role=initial.usage_role if initial else None,
        initial_reference_key=initial.reference_key if initial else None,
        initial_binding_mode=initial.binding_mode if initial else None,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    return _asset_upload_response(
        settings,
        session_id=session.id,
        expires_at=expires_at,
        token=token,
        declared_mime=declared_mime,
    )


@router.post(
    "/v1/assets/{asset_id}/version-upload-sessions",
    response_model=AssetUploadCreated,
    status_code=201,
)
def create_asset_version_upload(
    asset_id: str,
    body: AssetVersionUploadCreate,
    request: Request,
    identity: ServiceDep,
    db: DbDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    asset = db.get(Asset, asset_id)
    if not asset or asset.created_by_app_id != identity.app_id or asset.lifecycle_state != "active":
        raise HTTPException(status_code=404, detail="asset not found")
    declared_mime = body.content_type.split(";", 1)[0].strip().lower()
    settings = request.app.state.settings
    if declared_mime not in settings.asset_allowed_mime_types:
        raise HTTPException(status_code=400, detail="unsupported asset content type")
    if body.size_bytes > settings.asset_max_upload_bytes:
        raise HTTPException(status_code=413, detail="asset exceeds configured size limit")
    if idempotency_key is not None and not 1 <= len(idempotency_key) <= 255:
        raise HTTPException(status_code=400, detail="invalid Idempotency-Key")

    fingerprint = _version_request_fingerprint(asset.id, body)
    existing = None
    if idempotency_key:
        existing = db.scalar(
            select(AssetUploadSession).where(
                AssetUploadSession.app_id == identity.app_id,
                AssetUploadSession.idempotency_key == idempotency_key,
            )
        )
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="Idempotency-Key payload mismatch")
        expires_at = _as_utc(existing.expires_at)
        token = _upload_token(existing.id, expires_at, _signing_key(request))
        return _asset_upload_response(
            settings,
            session_id=existing.id,
            expires_at=expires_at,
            token=token,
            declared_mime=existing.declared_mime,
        )

    session_id = str(uuid.uuid4())
    now = utcnow()
    expires_at = now + timedelta(seconds=settings.asset_upload_ttl_seconds)
    token = _upload_token(session_id, expires_at, _signing_key(request))
    session = AssetUploadSession(
        id=session_id,
        app_id=identity.app_id,
        owner_id=asset.owner_id,
        ownership_mode=asset.ownership_mode,
        access_mode=asset.access_mode,
        sensitivity=asset.sensitivity,
        retention_policy_key=asset.retention_policy_key,
        display_name=asset.display_name,
        original_filename=_safe_filename(body.original_filename),
        declared_mime=declared_mime,
        declared_size=body.size_bytes,
        staging_key=session_id,
        upload_token_hash=_hash_token(token),
        status="pending",
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        target_asset_id=asset.id,
        change_reason=body.change_reason,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    return _asset_upload_response(
        settings,
        session_id=session.id,
        expires_at=expires_at,
        token=token,
        declared_mime=declared_mime,
    )


@router.put("/v1/upload-sessions/{session_id}/content", status_code=204)
async def upload_asset_content(session_id: str, request: Request, db: DbDep):
    session = db.get(AssetUploadSession, session_id)
    if not session or session.status != "pending":
        raise HTTPException(status_code=404, detail="active asset upload not found")
    if session.expires_at.replace(tzinfo=UTC) < utcnow():
        session.status = "expired"
        db.commit()
        raise HTTPException(status_code=410, detail="asset upload expired")
    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "upload" or not secrets.compare_digest(
        _hash_token(supplied), session.upload_token_hash
    ):
        raise HTTPException(status_code=401, detail="invalid asset upload token")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != session.declared_mime:
        raise HTTPException(status_code=400, detail="content type does not match declaration")

    try:
        staged = await request.app.state.asset_storage.write_staging(
            session.staging_key,
            request.stream(),
            declared_mime=session.declared_mime,
            declared_size=session.declared_size,
            max_size=request.app.state.settings.asset_max_upload_bytes,
        )
    except StorageError as exc:
        session.status = "failed"
        session.failure_reason = str(exc)
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.actual_mime = staged.inspected.detected_mime
    session.media_family = staged.inspected.media_family
    session.actual_size = staged.size_bytes
    session.sha256 = staged.sha256
    session.technical_metadata = staged.inspected.technical_metadata
    session.status = "uploaded"
    session.uploaded_at = utcnow()
    db.commit()


@router.post("/v1/upload-sessions/{session_id}/complete", response_model=AssetView)
def complete_asset_upload(
    session_id: str,
    request: Request,
    identity: ServiceDep,
    db: DbDep,
):
    session = db.get(AssetUploadSession, session_id)
    if not session or session.app_id != identity.app_id:
        raise HTTPException(status_code=404, detail="asset upload not found")
    if session.status == "completed" and session.asset_id:
        existing_asset = db.get(Asset, session.asset_id)
        if existing_asset:
            return _asset_view(db, existing_asset)
    if session.status not in {"uploaded", "finalizing"}:
        raise HTTPException(status_code=409, detail=f"asset upload is {session.status}")
    if not session.sha256 or not session.actual_size or not session.actual_mime:
        raise HTTPException(status_code=409, detail="asset upload verification is incomplete")

    session.status = "finalizing"
    db.commit()
    try:
        object_key = request.app.state.asset_storage.finalize_staging(
            session.staging_key,
            sha256=session.sha256,
            size_bytes=session.actual_size,
        )
    except StorageError as exc:
        session.status = "failed"
        session.failure_reason = str(exc)
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    now = utcnow()
    blob = db.scalar(
        select(AssetBlob).where(
            AssetBlob.digest_algorithm == "sha256",
            AssetBlob.digest == session.sha256,
            AssetBlob.size_bytes == session.actual_size,
        )
    )
    if not blob:
        blob = AssetBlob(
            id=str(uuid.uuid4()),
            digest_algorithm="sha256",
            digest=session.sha256,
            size_bytes=session.actual_size,
            integrity_state="healthy",
            created_at=now,
        )
        db.add(blob)
        db.flush()
    location = db.scalar(
        select(AssetBlobLocation).where(
            AssetBlobLocation.backend_id == request.app.state.asset_storage.backend_id,
            AssetBlobLocation.object_key == object_key,
        )
    )
    if not location:
        location = AssetBlobLocation(
            id=str(uuid.uuid4()),
            blob_id=blob.id,
            backend_id=request.app.state.asset_storage.backend_id,
            object_key=object_key,
            backend_checksum=f"sha256:{session.sha256}",
            state="available",
            last_verified_at=now,
            created_at=now,
        )
        db.add(location)

    if session.target_asset_id:
        asset = db.get(Asset, session.target_asset_id)
        if (
            not asset
            or asset.created_by_app_id != identity.app_id
            or asset.lifecycle_state != "active"
        ):
            session.status = "failed"
            session.failure_reason = "target asset is no longer writable"
            db.commit()
            raise HTTPException(status_code=409, detail=session.failure_reason)
        version_number = (
            db.scalar(
                select(func.max(AssetVersion.version_number)).where(
                    AssetVersion.asset_id == asset.id
                )
            )
            or 0
        ) + 1
    else:
        asset = Asset(
            id=str(uuid.uuid4()),
            owner_id=session.owner_id,
            created_by_app_id=identity.app_id,
            ownership_mode=session.ownership_mode,
            display_name=session.display_name,
            access_mode=session.access_mode,
            sensitivity=session.sensitivity,
            retention_policy_key=session.retention_policy_key,
            lifecycle_state="active",
            zero_referenced_at=now if session.initial_reference_key is None else None,
            created_at=now,
        )
        db.add(asset)
        db.flush()
        version_number = 1
    version = AssetVersion(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        version_number=version_number,
        blob_id=blob.id,
        original_filename=session.original_filename,
        declared_mime=session.declared_mime,
        detected_mime=session.actual_mime,
        media_family=session.media_family or "binary",
        technical_metadata=session.technical_metadata or {},
        source_fidelity="original",
        change_reason=session.change_reason,
        created_by=f"service:{identity.app_id}",
        state="ready",
        created_at=now,
    )
    db.add(version)
    db.flush()
    asset.current_version_id = version.id

    if not session.target_asset_id and session.initial_reference_key:
        binding_mode = session.initial_binding_mode or "pinned"
        reference = AssetReference(
            id=str(uuid.uuid4()),
            asset_id=asset.id,
            app_id=identity.app_id,
            resource_uri=session.initial_resource_uri or "",
            usage_role=session.initial_usage_role or "file",
            reference_key=session.initial_reference_key,
            binding_mode=binding_mode,
            pinned_version_id=version.id if binding_mode == "pinned" else None,
            state="active",
            created_at=now,
        )
        db.add(reference)

    session.status = "completed"
    session.asset_id = asset.id
    session.result_version_id = version.id
    session.completed_at = now
    _outbox(
        db,
        event_type="asset.version.created" if session.target_asset_id else "asset.created",
        aggregate_type="asset",
        aggregate_id=asset.id,
        payload={"asset_id": asset.id, "version_id": version.id, "app_id": identity.app_id},
    )
    _audit(
        db,
        identity=identity,
        action="asset.version.created" if session.target_asset_id else "asset.created",
        asset_id=asset.id,
        details={"version_id": version.id, "version_number": version.version_number},
    )
    db.commit()
    return _asset_view(db, asset)


@router.get("/v1/assets/{asset_id}", response_model=AssetView)
def get_asset(asset_id: str, identity: ServiceDep, db: DbDep):
    asset = db.get(Asset, asset_id)
    if (
        not asset
        or asset.lifecycle_state == "purged"
        or not _service_can_read(db, asset, identity.app_id)
    ):
        raise HTTPException(status_code=404, detail="asset not found")
    return _asset_view(db, asset)


@router.post("/v1/assets/{asset_id}/trash", response_model=AssetView)
def trash_asset(asset_id: str, request: Request, identity: ServiceDep, db: DbDep):
    asset = db.get(Asset, asset_id)
    if not asset or asset.created_by_app_id != identity.app_id:
        raise HTTPException(status_code=404, detail="asset not found")
    if asset.lifecycle_state == "purged":
        raise HTTPException(status_code=409, detail="purged asset cannot be trashed")
    if asset.lifecycle_state == "active":
        now = utcnow()
        asset.lifecycle_state = "trashed"
        asset.trashed_at = now
        asset.purge_after = now + timedelta(
            days=request.app.state.settings.asset_trash_retention_days
        )
        _outbox(
            db,
            event_type="asset.trashed",
            aggregate_type="asset",
            aggregate_id=asset.id,
            payload={"asset_id": asset.id, "reason": "explicit"},
        )
        _audit(db, identity=identity, action="asset.trashed", asset_id=asset.id)
        db.commit()
    return _asset_view(db, asset)


@router.post("/v1/assets/{asset_id}/restore", response_model=AssetView)
def restore_asset(asset_id: str, identity: ServiceDep, db: DbDep):
    asset = db.get(Asset, asset_id)
    if not asset or asset.created_by_app_id != identity.app_id:
        raise HTTPException(status_code=404, detail="asset not found")
    if asset.lifecycle_state != "trashed":
        raise HTTPException(status_code=409, detail="only trashed assets can be restored")
    now = utcnow()
    if asset.purge_after and _as_utc(asset.purge_after) <= now:
        raise HTTPException(status_code=409, detail="asset retention period has expired")
    asset.lifecycle_state = "active"
    asset.trashed_at = None
    asset.purge_after = None
    if asset.ownership_mode == "app_managed" and not db.scalar(
        select(AssetReference.id).where(
            AssetReference.asset_id == asset.id,
            AssetReference.state == "active",
        )
    ):
        asset.zero_referenced_at = now
    _outbox(
        db,
        event_type="asset.restored",
        aggregate_type="asset",
        aggregate_id=asset.id,
        payload={"asset_id": asset.id},
    )
    _audit(db, identity=identity, action="asset.restored", asset_id=asset.id)
    db.commit()
    return _asset_view(db, asset)


@router.post("/v1/asset-references", response_model=AssetReferenceView, status_code=201)
def create_asset_reference(body: AssetReferenceCreate, identity: ServiceDep, db: DbDep):
    if _resource_app_id(body.resource_uri) != identity.app_id:
        raise HTTPException(status_code=400, detail="resource URI must belong to calling app")
    existing = db.scalar(
        select(AssetReference).where(
            AssetReference.app_id == identity.app_id,
            AssetReference.reference_key == body.reference_key,
        )
    )
    expected = (
        body.asset_id,
        body.resource_uri,
        body.usage_role,
        body.binding_mode,
        body.pinned_version_id,
    )
    if existing:
        actual = (
            existing.asset_id,
            existing.resource_uri,
            existing.usage_role,
            existing.binding_mode,
            existing.pinned_version_id,
        )
        if actual != expected:
            raise HTTPException(status_code=409, detail="reference_key payload mismatch")
        if existing.state == "released":
            asset = db.get(Asset, existing.asset_id)
            if (
                not asset
                or asset.lifecycle_state != "active"
                or (
                    asset.created_by_app_id != identity.app_id
                    and asset.access_mode not in {"delegated", "public"}
                )
            ):
                raise HTTPException(status_code=404, detail="asset not found")
            existing.state = "active"
            existing.released_at = None
            if asset:
                asset.zero_referenced_at = None
            db.commit()
        return existing

    asset = db.get(Asset, body.asset_id)
    if not asset or asset.lifecycle_state != "active":
        raise HTTPException(status_code=404, detail="asset not found")
    if asset.created_by_app_id != identity.app_id and asset.access_mode not in {
        "delegated",
        "public",
    }:
        raise HTTPException(status_code=404, detail="asset not found")
    if body.pinned_version_id:
        version = db.get(AssetVersion, body.pinned_version_id)
        if not version or version.asset_id != asset.id or version.state != "ready":
            raise HTTPException(status_code=400, detail="pinned version does not belong to asset")

    reference = AssetReference(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        app_id=identity.app_id,
        resource_uri=body.resource_uri,
        usage_role=body.usage_role,
        reference_key=body.reference_key,
        delegated_by_app_id=None,
        binding_mode=body.binding_mode,
        pinned_version_id=body.pinned_version_id,
        state="active",
        created_at=utcnow(),
    )
    db.add(reference)
    asset.zero_referenced_at = None
    _outbox(
        db,
        event_type="asset.reference.added",
        aggregate_type="asset",
        aggregate_id=asset.id,
        payload={"asset_id": asset.id, "reference_id": reference.id, "app_id": identity.app_id},
    )
    _audit(
        db,
        identity=identity,
        action="asset.reference.added",
        asset_id=asset.id,
        details={"reference_id": reference.id},
    )
    db.commit()
    return reference


@router.post(
    "/v1/asset-reference-delegations",
    response_model=AssetReferenceView,
    status_code=201,
)
def delegate_asset_reference(
    body: AssetReferenceDelegationCreate,
    request: Request,
    identity: ServiceDep,
    db: DbDep,
):
    """资产创建者把一个具体私有资产引用显式委派给目标应用。"""
    if body.target_app_id == identity.app_id:
        raise HTTPException(status_code=400, detail="use the regular reference endpoint")
    if body.target_app_id not in request.app.state.settings.service_token_hashes:
        raise HTTPException(status_code=400, detail="target app is not registered")
    if _resource_app_id(body.resource_uri) != body.target_app_id:
        raise HTTPException(status_code=400, detail="resource URI must belong to target app")
    asset = db.get(Asset, body.asset_id)
    if (
        not asset
        or asset.lifecycle_state != "active"
        or asset.created_by_app_id != identity.app_id
    ):
        raise HTTPException(status_code=404, detail="asset not found")
    if body.pinned_version_id:
        version = db.get(AssetVersion, body.pinned_version_id)
        if not version or version.asset_id != asset.id or version.state != "ready":
            raise HTTPException(status_code=400, detail="pinned version does not belong to asset")
    existing = db.scalar(
        select(AssetReference).where(
            AssetReference.app_id == body.target_app_id,
            AssetReference.reference_key == body.reference_key,
        )
    )
    expected = (
        body.asset_id,
        body.resource_uri,
        body.usage_role,
        body.binding_mode,
        body.pinned_version_id,
        identity.app_id,
    )
    if existing:
        actual = (
            existing.asset_id,
            existing.resource_uri,
            existing.usage_role,
            existing.binding_mode,
            existing.pinned_version_id,
            existing.delegated_by_app_id,
        )
        if actual != expected:
            raise HTTPException(status_code=409, detail="reference_key payload mismatch")
        if existing.state == "released":
            existing.state = "active"
            existing.released_at = None
            asset.zero_referenced_at = None
            db.commit()
        return existing
    reference = AssetReference(
        id=str(uuid.uuid4()),
        asset_id=asset.id,
        app_id=body.target_app_id,
        resource_uri=body.resource_uri,
        usage_role=body.usage_role,
        reference_key=body.reference_key,
        delegated_by_app_id=identity.app_id,
        binding_mode=body.binding_mode,
        pinned_version_id=body.pinned_version_id,
        state="active",
        created_at=utcnow(),
    )
    db.add(reference)
    asset.zero_referenced_at = None
    _outbox(
        db,
        event_type="asset.reference.delegated",
        aggregate_type="asset",
        aggregate_id=asset.id,
        payload={
            "asset_id": asset.id,
            "reference_id": reference.id,
            "app_id": body.target_app_id,
            "delegated_by_app_id": identity.app_id,
        },
    )
    _audit(
        db,
        identity=identity,
        action="asset.reference.delegated",
        asset_id=asset.id,
        details={"reference_id": reference.id, "target_app_id": body.target_app_id},
    )
    db.commit()
    return reference


@router.get(
    "/v1/asset-references/resolve",
    response_model=list[AssetReferenceResolution],
)
def resolve_asset_references(
    identity: ServiceDep,
    db: DbDep,
    resource_uri: Annotated[str, Query(min_length=10, max_length=1024)],
    usage_role: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
):
    if _resource_app_id(resource_uri) != identity.app_id:
        raise HTTPException(status_code=400, detail="resource URI must belong to calling app")
    statement = select(AssetReference).where(
        AssetReference.app_id == identity.app_id,
        AssetReference.resource_uri == resource_uri,
        AssetReference.state == "active",
    )
    if usage_role is not None:
        statement = statement.where(AssetReference.usage_role == usage_role)
    references = list(db.scalars(statement.order_by(AssetReference.created_at, AssetReference.id)))
    return [_resolved_reference(db, reference) for reference in references]


@router.delete("/v1/asset-references/{reference_id}", response_model=AssetReferenceRelease)
def release_asset_reference(reference_id: str, identity: ServiceDep, db: DbDep):
    reference = db.scalar(
        select(AssetReference).where(
            AssetReference.id == reference_id,
            (
                (AssetReference.app_id == identity.app_id)
                | (AssetReference.delegated_by_app_id == identity.app_id)
            ),
        )
    )
    if not reference:
        raise HTTPException(status_code=404, detail="asset reference not found")
    if reference.state == "released" and reference.released_at:
        return AssetReferenceRelease(
            id=reference.id, state="released", released_at=reference.released_at
        )
    now = utcnow()
    reference.state = "released"
    reference.released_at = now
    remaining = db.scalar(
        select(func.count())
        .select_from(AssetReference)
        .where(
            AssetReference.asset_id == reference.asset_id,
            AssetReference.state == "active",
            AssetReference.id != reference.id,
        )
    )
    asset = db.get(Asset, reference.asset_id)
    if asset and not remaining:
        asset.zero_referenced_at = now
    _outbox(
        db,
        event_type="asset.reference.released",
        aggregate_type="asset",
        aggregate_id=reference.asset_id,
        payload={"asset_id": reference.asset_id, "reference_id": reference.id},
    )
    _audit(
        db,
        identity=identity,
        action="asset.reference.released",
        asset_id=reference.asset_id,
        details={"reference_id": reference.id},
    )
    db.commit()
    return AssetReferenceRelease(id=reference.id, state="released", released_at=now)


def _would_create_derivative_cycle(
    db: Session, source_version_id: str, derived_version_id: str
) -> bool:
    frontier = {derived_version_id}
    visited: set[str] = set()
    while frontier:
        if source_version_id in frontier:
            return True
        visited.update(frontier)
        next_versions = set(
            db.scalars(
                select(AssetDerivative.derived_version_id).where(
                    AssetDerivative.source_version_id.in_(frontier),
                    AssetDerivative.state != "failed",
                )
            )
        )
        frontier = next_versions - visited
    return False


@router.post("/v1/asset-derivatives", response_model=AssetDerivativeView, status_code=201)
def create_asset_derivative(body: AssetDerivativeCreate, identity: ServiceDep, db: DbDep):
    source = db.get(AssetVersion, body.source_version_id)
    derived = db.get(AssetVersion, body.derived_version_id)
    if not source or not derived:
        raise HTTPException(status_code=404, detail="asset version not found")
    source_asset = db.get(Asset, source.asset_id)
    derived_asset = db.get(Asset, derived.asset_id)
    if (
        not source_asset
        or not derived_asset
        or not _service_can_read(db, source_asset, identity.app_id)
        or derived_asset.created_by_app_id != identity.app_id
        or derived_asset.ownership_mode != "derived"
    ):
        raise HTTPException(status_code=404, detail="asset version not found")
    if _would_create_derivative_cycle(db, source.id, derived.id):
        raise HTTPException(status_code=409, detail="asset derivative would create a cycle")
    parameters_hash = hashlib.sha256(
        json.dumps(body.parameters, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    existing = db.scalar(
        select(AssetDerivative).where(
            AssetDerivative.source_version_id == source.id,
            AssetDerivative.recipe_key == body.recipe_key,
            AssetDerivative.recipe_version == body.recipe_version,
            AssetDerivative.parameters_hash == parameters_hash,
        )
    )
    if existing:
        if existing.derived_version_id != derived.id:
            raise HTTPException(status_code=409, detail="derivative recipe already has an output")
        return existing
    derivative = AssetDerivative(
        id=str(uuid.uuid4()),
        source_version_id=source.id,
        derived_version_id=derived.id,
        recipe_key=body.recipe_key,
        recipe_version=body.recipe_version,
        parameters_hash=parameters_hash,
        generator=body.generator,
        generator_version=body.generator_version,
        state="ready",
        created_at=utcnow(),
    )
    db.add(derivative)
    _outbox(
        db,
        event_type="asset.derivative.ready",
        aggregate_type="asset",
        aggregate_id=derived_asset.id,
        payload={
            "source_version_id": source.id,
            "derived_version_id": derived.id,
            "recipe_key": body.recipe_key,
        },
    )
    _audit(
        db,
        identity=identity,
        action="asset.derivative.created",
        asset_id=derived_asset.id,
        details={"source_version_id": source.id, "recipe_key": body.recipe_key},
    )
    db.commit()
    return derivative


@router.post("/v1/asset-versions/{version_id}/access-grants", response_model=AssetAccessGrant)
def grant_asset_access(
    version_id: str,
    body: AssetAccessRequest,
    request: Request,
    identity: ServiceDep,
    db: DbDep,
):
    version = db.get(AssetVersion, version_id)
    asset = db.get(Asset, version.asset_id) if version else None
    if (
        not version
        or version.state != "ready"
        or not asset
        or asset.lifecycle_state != "active"
        or not _service_can_read(db, asset, identity.app_id)
    ):
        raise HTTPException(status_code=404, detail="asset version not found")
    base = f"{request.app.state.settings.public_base_url}/v1/asset-content/{version.id}"
    if asset.access_mode == "public":
        url = f"{base}?operation={body.operation}"
        expires_at = None
    else:
        expires_at = utcnow() + timedelta(seconds=request.app.state.settings.access_ttl_seconds)
        token = _access_token(version.id, body.operation, expires_at, _signing_key(request))
        url = f"{base}?operation={body.operation}&token={token}"
    _audit(
        db,
        identity=identity,
        action="asset.access-granted",
        asset_id=asset.id,
        details={"version_id": version.id, "operation": body.operation},
    )
    db.commit()
    return AssetAccessGrant(
        asset_id=asset.id,
        version_id=version.id,
        url=url,
        operation=body.operation,
        expires_at=expires_at,
    )


@router.get("/v1/asset-content/{version_id}", response_class=FileResponse)
def read_asset_content(
    version_id: str,
    request: Request,
    db: DbDep,
    operation: Annotated[str, Query(pattern="^(inline|download)$")] = "inline",
    token: Annotated[str, Query()] = "",
):
    version = db.get(AssetVersion, version_id)
    asset = db.get(Asset, version.asset_id) if version else None
    if not version or not asset or version.state != "ready" or asset.lifecycle_state != "active":
        raise HTTPException(status_code=404, detail="asset content not found")
    if asset.access_mode != "public" and not _valid_access_token(
        version.id, operation, token, _signing_key(request)
    ):
        raise HTTPException(status_code=403, detail="valid asset access grant required")
    location = db.scalar(
        select(AssetBlobLocation).where(
            AssetBlobLocation.blob_id == version.blob_id,
            AssetBlobLocation.state == "available",
        )
    )
    if not location:
        raise HTTPException(status_code=404, detail="asset Blob is unavailable")
    path = request.app.state.asset_storage.blob_path(location.object_key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="asset Blob is missing")
    return FileResponse(
        path,
        media_type=version.detected_mime,
        filename=version.original_filename,
        content_disposition_type="attachment" if operation == "download" else "inline",
    )
