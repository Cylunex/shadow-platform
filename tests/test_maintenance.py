from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from media_service.asset_models import Asset, AssetBlob, AssetLegacyMediaMap, AssetVersion
from media_service.config import Settings as MediaSettings
from media_service.database import Base as MediaBase
from media_service.database import create_database as create_media_database
from media_service.models import MediaObject, UploadIntent
from scripts.backfill_assets import backfill_assets
from scripts.cleanup_media import cleanup_media
from scripts.cleanup_telemetry import cleanup_telemetry
from telemetry_service.config import Settings as TelemetrySettings
from telemetry_service.database import Base as TelemetryBase
from telemetry_service.database import create_database as create_telemetry_database
from telemetry_service.models import LLMUsage


def test_media_cleanup_expires_uploads_and_physically_deletes_soft_deleted_objects(tmp_path):
    now = datetime.now(UTC)
    settings = MediaSettings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'media.db'}",
        storage_root=tmp_path / "objects",
        public_base_url="http://testserver",
    )
    engine, factory = create_media_database(settings.database_url)
    MediaBase.metadata.create_all(engine)
    pending_key = "travel/pending.png"
    deleted_key = "travel/deleted.png"
    for key in (pending_key, deleted_key):
        path = settings.storage_root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
    with factory() as db:
        db.add(
            UploadIntent(
                id="upload-1",
                app_id="travel",
                owner_sub="user-1",
                resource_type="place",
                resource_id="place-1",
                visibility="private",
                original_filename="photo.png",
                declared_mime="image/png",
                declared_size=4,
                storage_backend="local",
                storage_key=pending_key,
                upload_token_hash="0" * 64,
                status="pending",
                created_at=now - timedelta(days=2),
                expires_at=now - timedelta(days=1),
            )
        )
        db.add(
            MediaObject(
                id="media-1",
                app_id="travel",
                owner_sub="user-1",
                resource_type="place",
                resource_id="place-1",
                visibility="private",
                original_filename="photo.png",
                content_type="image/png",
                size_bytes=4,
                sha256="0" * 64,
                width=1,
                height=1,
                storage_backend="local",
                storage_key=deleted_key,
                status="deleted",
                created_at=now - timedelta(days=10),
                deleted_at=now - timedelta(days=8),
                delete_after=now - timedelta(days=1),
            )
        )
        db.commit()
    engine.dispose()

    result = cleanup_media(settings, now=now)

    assert result.expired_uploads == 1
    assert result.deleted_objects == 1
    assert not (settings.storage_root / pending_key).exists()
    assert not (settings.storage_root / deleted_key).exists()


def test_telemetry_cleanup_applies_retention(tmp_path):
    now = datetime.now(UTC)
    settings = TelemetrySettings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'telemetry.db'}",
        retention_days=30,
    )
    engine, factory = create_telemetry_database(settings.database_url)
    TelemetryBase.metadata.create_all(engine)
    with factory() as db:
        for request_id, started_at in (
            ("old", now - timedelta(days=31)),
            ("current", now - timedelta(days=1)),
        ):
            db.add(
                LLMUsage(
                    request_id=request_id,
                    app_id="travel",
                    agent_id=None,
                    model_alias="chat-default",
                    provider="primary",
                    actual_model="snapshot",
                    protocol="openai-compatible",
                    api="responses",
                    status="success",
                    latency_ms=1,
                    input_tokens=1,
                    output_tokens=1,
                    cached_tokens=0,
                    retry_count=0,
                    streamed=False,
                    started_at=started_at,
                    received_at=now,
                )
            )
        db.commit()
    engine.dispose()

    assert cleanup_telemetry(settings, now=now) == 1


def test_legacy_media_backfill_requires_explicit_owner_mapping_and_is_idempotent(tmp_path):
    now = datetime.now(UTC)
    settings = MediaSettings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'backfill.db'}",
        storage_root=tmp_path / "objects",
        public_base_url="http://testserver",
    )
    engine, factory = create_media_database(settings.database_url)
    MediaBase.metadata.create_all(engine)
    with factory() as db:
        db.add(
            MediaObject(
                id="media-backfill",
                app_id="garden",
                owner_sub="legacy-subject",
                resource_type="post",
                resource_id="42",
                visibility="scoped",
                original_filename="cover.jpg",
                content_type="image/jpeg",
                size_bytes=4,
                sha256="a" * 64,
                width=10,
                height=20,
                storage_backend="local",
                storage_key="garden/cover.jpg",
                status="ready",
                created_at=now,
            )
        )
        db.commit()
    engine.dispose()

    owner_id = "20000000-0000-4000-8000-000000000002"
    first = backfill_assets(settings, {"legacy-subject": owner_id}, now=now)
    second = backfill_assets(settings, {"legacy-subject": owner_id}, now=now)
    assert first.migrated == 1
    assert second.skipped == 1

    engine, factory = create_media_database(settings.database_url)
    with factory() as db:
        asset = db.scalar(select(Asset))
        version = db.scalar(select(AssetVersion))
        assert asset.owner_id == owner_id
        assert asset.access_mode == "delegated"
        assert version.source_fidelity == "migrated_sanitized"
        assert db.scalar(select(func.count()).select_from(AssetBlob)) == 1
        assert db.get(AssetLegacyMediaMap, "media-backfill").asset_id == asset.id
    engine.dispose()
