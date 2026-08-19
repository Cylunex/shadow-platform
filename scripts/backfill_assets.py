from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from media_service.asset_models import (
    Asset,
    AssetBlob,
    AssetBlobLocation,
    AssetLegacyMediaMap,
    AssetReference,
    AssetVersion,
)
from media_service.config import Settings
from media_service.database import Base, create_database
from media_service.models import MediaObject


@dataclass(frozen=True, slots=True)
class BackfillResult:
    migrated: int
    skipped: int


def _load_owner_map(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("owner map must be a JSON object")
    result: dict[str, str] = {}
    for subject, owner_id in payload.items():
        if not isinstance(subject, str) or not isinstance(owner_id, str):
            raise ValueError("owner map keys and values must be strings")
        result[subject] = str(uuid.UUID(owner_id))
    return result


def _reference_uri(media: MediaObject) -> str:
    resource_type = media.resource_type.strip("/") or "resources"
    resource_id = media.resource_id.strip("/") or media.id
    return f"shadow://{media.app_id}/{resource_type}/{resource_id}"


def backfill_assets(
    settings: Settings,
    owner_map: dict[str, str],
    *,
    now: datetime | None = None,
) -> BackfillResult:
    now = now or datetime.now(UTC)
    engine, session_factory = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    migrated = 0
    skipped = 0
    try:
        with session_factory() as db:
            media_objects = db.scalars(select(MediaObject).order_by(MediaObject.created_at))
            for media in media_objects:
                if db.get(AssetLegacyMediaMap, media.id):
                    skipped += 1
                    continue
                owner_id = owner_map.get(media.owner_sub)
                if not owner_id:
                    raise ValueError(
                        f"owner_sub {media.owner_sub!r} has no stable shadow_user_id mapping"
                    )

                blob = db.scalar(
                    select(AssetBlob).where(
                        AssetBlob.digest_algorithm == "sha256",
                        AssetBlob.digest == media.sha256,
                        AssetBlob.size_bytes == media.size_bytes,
                    )
                )
                if not blob:
                    blob = AssetBlob(
                        id=str(uuid.uuid4()),
                        digest_algorithm="sha256",
                        digest=media.sha256,
                        size_bytes=media.size_bytes,
                        integrity_state="healthy",
                        created_at=media.created_at,
                    )
                    db.add(blob)
                    db.flush()

                location = db.scalar(
                    select(AssetBlobLocation).where(
                        AssetBlobLocation.backend_id == media.storage_backend,
                        AssetBlobLocation.object_key == media.storage_key,
                    )
                )
                if not location:
                    location = AssetBlobLocation(
                        id=str(uuid.uuid4()),
                        blob_id=blob.id,
                        backend_id=media.storage_backend,
                        object_key=media.storage_key,
                        backend_checksum=f"sha256:{media.sha256}",
                        state="available",
                        created_at=media.created_at,
                    )
                    db.add(location)

                access_mode = "delegated" if media.visibility == "scoped" else media.visibility
                lifecycle_state = "trashed" if media.status == "deleted" else "active"
                asset = Asset(
                    id=str(uuid.uuid4()),
                    owner_id=owner_id,
                    created_by_app_id=media.app_id,
                    ownership_mode="app_managed",
                    display_name=media.original_filename,
                    access_mode=access_mode,
                    sensitivity="normal",
                    retention_policy_key="legacy-media",
                    lifecycle_state=lifecycle_state,
                    created_at=media.created_at,
                    trashed_at=media.deleted_at,
                    purge_after=media.delete_after,
                )
                db.add(asset)
                db.flush()
                version = AssetVersion(
                    id=str(uuid.uuid4()),
                    asset_id=asset.id,
                    version_number=1,
                    blob_id=blob.id,
                    original_filename=media.original_filename,
                    declared_mime=media.content_type,
                    detected_mime=media.content_type,
                    media_family="image",
                    technical_metadata={"width": media.width, "height": media.height},
                    source_fidelity="migrated_sanitized",
                    change_reason="legacy MediaObject backfill",
                    created_by=f"service:{media.app_id}",
                    state="ready",
                    created_at=media.created_at,
                )
                db.add(version)
                db.flush()
                asset.current_version_id = version.id
                reference = AssetReference(
                    id=str(uuid.uuid4()),
                    asset_id=asset.id,
                    app_id=media.app_id,
                    resource_uri=_reference_uri(media),
                    usage_role="legacy-media",
                    reference_key=f"legacy-media:{media.id}",
                    binding_mode="pinned",
                    pinned_version_id=version.id,
                    state="released" if media.status == "deleted" else "active",
                    created_at=media.created_at,
                    released_at=media.deleted_at,
                )
                db.add(reference)
                db.add(
                    AssetLegacyMediaMap(
                        media_id=media.id,
                        asset_id=asset.id,
                        created_at=now,
                    )
                )
                migrated += 1
            db.commit()
    finally:
        engine.dispose()
    return BackfillResult(migrated=migrated, skipped=skipped)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idempotently backfill legacy Shadow Media rows into Asset v1 tables"
    )
    parser.add_argument(
        "--owner-map",
        required=True,
        type=Path,
        help="JSON object mapping legacy owner_sub values to stable shadow_user_id UUIDs",
    )
    args = parser.parse_args()
    result = backfill_assets(Settings.from_env(), _load_owner_map(args.owner_map))
    print(f"migrated={result.migrated} skipped={result.skipped}")


if __name__ == "__main__":
    main()
