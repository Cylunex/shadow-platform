from __future__ import annotations

import json

from notification_service.config import Settings
from notification_service.database import create_database
from notification_service.worker import cleanup


def main() -> None:
    settings = Settings.from_env()
    engine, session_factory = create_database(settings.database_url)
    try:
        with session_factory() as db:
            result = cleanup(db, settings.retention_days)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
