from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from telemetry_service.config import Settings
from telemetry_service.database import create_database
from telemetry_service.models import LLMUsage


def cleanup_telemetry(settings: Settings, *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=settings.retention_days)
    engine, session_factory = create_database(settings.database_url)
    try:
        with session_factory() as db:
            result = db.execute(delete(LLMUsage).where(LLMUsage.started_at < cutoff))
            db.commit()
            return result.rowcount or 0
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete expired Shadow LLM telemetry")
    parser.parse_args()
    print(f"deleted_events={cleanup_telemetry(Settings.from_env())}")


if __name__ == "__main__":
    main()
