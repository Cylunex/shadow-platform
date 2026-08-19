from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from media_service.asset_lifecycle import apply_asset_lifecycle
from media_service.asset_models import AssetUploadSession
from media_service.asset_storage import AssetLocalStorage
from media_service.config import Settings
from media_service.database import create_database
from media_service.models import MediaObject, UploadIntent
from media_service.storage import LocalStorage


@dataclass(frozen=True, slots=True)
class CleanupResult:
    expired_uploads: int
    deleted_objects: int
    expired_asset_uploads: int = 0
    orphaned_trashed: int = 0
    purged_assets: int = 0
    gc_candidates: int = 0


def cleanup_media(settings: Settings, *, now: datetime | None = None) -> CleanupResult:
    now = now or datetime.now(UTC)
    engine, session_factory = create_database(settings.database_url)
    storage = LocalStorage(settings.storage_root)
    asset_storage = AssetLocalStorage(settings.storage_root)
    expired_uploads = 0
    expired_asset_uploads = 0
    deleted_objects = 0
    try:
        with session_factory() as db:
            intents = db.scalars(
                select(UploadIntent).where(
                    UploadIntent.expires_at < now,
                    UploadIntent.status.in_(["pending", "uploaded", "failed"]),
                )
            )
            for intent in intents:
                storage.delete(intent.storage_key)
                intent.status = "expired"
                expired_uploads += 1
            asset_uploads = db.scalars(
                select(AssetUploadSession).where(
                    AssetUploadSession.expires_at < now,
                    AssetUploadSession.status.in_(["pending", "uploaded", "failed"]),
                )
            )
            for upload in asset_uploads:
                asset_storage.delete_staging(upload.staging_key)
                upload.status = "expired"
                expired_asset_uploads += 1
            media_objects = db.scalars(
                select(MediaObject).where(
                    MediaObject.status == "deleted",
                    MediaObject.delete_after.is_not(None),
                    MediaObject.delete_after <= now,
                )
            )
            for media in media_objects:
                storage.delete(media.storage_key)
                db.delete(media)
                deleted_objects += 1
            db.commit()
        with session_factory() as db:
            asset_result = apply_asset_lifecycle(db, settings, now=now)
    finally:
        engine.dispose()
    return CleanupResult(
        expired_uploads=expired_uploads,
        deleted_objects=deleted_objects,
        expired_asset_uploads=expired_asset_uploads,
        orphaned_trashed=asset_result.orphaned_trashed,
        purged_assets=asset_result.purged_assets,
        gc_candidates=len(asset_result.gc_candidates),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean expired Shadow Media objects")
    parser.parse_args()
    result = cleanup_media(Settings.from_env())
    print(
        f"expired_uploads={result.expired_uploads} "
        f"deleted_objects={result.deleted_objects} "
        f"expired_asset_uploads={result.expired_asset_uploads} "
        f"orphaned_trashed={result.orphaned_trashed} "
        f"purged_assets={result.purged_assets} "
        f"gc_candidates={result.gc_candidates}"
    )


if __name__ == "__main__":
    main()
