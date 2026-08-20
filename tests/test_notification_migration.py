from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_notification_migration_round_trip_matches_models(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'notifications.db'}"
    monkeypatch.delenv("SHADOW_NOTIFY_DATABASE_URL_FILE", raising=False)
    monkeypatch.delenv("SHADOW_NOTIFY_DATABASE_URL", raising=False)
    config = Config("notification_alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")
    command.check(config)

    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert {
        "notifications",
        "notification_deliveries",
        "notification_channel_targets",
        "notification_channel_principals",
        "notification_chat_ingress_events",
        "notification_operation_probes",
    } <= tables
    engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(database_url)
    assert "notifications" not in set(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, "head")
