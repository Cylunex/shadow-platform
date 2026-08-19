from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from media_service.models import MediaObject, UploadIntent


def test_asset_migration_round_trip_preserves_legacy_tables(tmp_path, monkeypatch):
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path}"
    engine = create_engine(database_url)
    UploadIntent.__table__.create(engine)
    MediaObject.__table__.create(engine)
    engine.dispose()

    monkeypatch.delenv("SHADOW_MEDIA_DATABASE_URL_FILE", raising=False)
    monkeypatch.delenv("SHADOW_MEDIA_DATABASE_URL", raising=False)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert {"assets", "asset_blobs", "asset_versions", "asset_references"} <= tables
    engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert {"media_objects", "media_upload_intents"} <= tables
    assert "assets" not in tables
    engine.dispose()


def test_asset_migration_can_initialize_a_fresh_database(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'fresh.db'}"
    monkeypatch.delenv("SHADOW_MEDIA_DATABASE_URL_FILE", raising=False)
    monkeypatch.delenv("SHADOW_MEDIA_DATABASE_URL", raising=False)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert {
        "media_objects",
        "media_upload_intents",
        "assets",
        "asset_blobs",
        "asset_versions",
    } <= tables
    engine.dispose()
