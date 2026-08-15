from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from shadow_sdk.service_auth import load_service_token_hashes


def _read_secret(path: str | None) -> str:
    return Path(path).read_text(encoding="utf-8").strip() if path else ""


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "development"
    database_url: str = "sqlite:///./data/telemetry.db"
    service_token_hashes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    retention_days: int = 400

    def __post_init__(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("SHADOW_ENV must be development, test or production")
        if not 1 <= self.retention_days <= 3650:
            raise ValueError("telemetry retention must be between 1 and 3650 days")
        if self.environment == "production":
            if not self.service_token_hashes:
                raise ValueError("production telemetry service tokens are required")
            if self.database_url.startswith("sqlite"):
                raise ValueError("production telemetry service requires PostgreSQL")

    @classmethod
    def from_env(cls) -> Settings:
        token_file = os.getenv("SHADOW_TELEMETRY_SERVICE_TOKEN_HASHES_FILE")
        database_url = _read_secret(os.getenv("SHADOW_TELEMETRY_DATABASE_URL_FILE")) or os.getenv(
            "SHADOW_TELEMETRY_DATABASE_URL", "sqlite:///./data/telemetry.db"
        )
        return cls(
            environment=os.getenv("SHADOW_ENV", "development"),
            database_url=database_url,
            service_token_hashes=(load_service_token_hashes(token_file) if token_file else {}),
            retention_days=int(os.getenv("SHADOW_TELEMETRY_RETENTION_DAYS", "400")),
        )
