from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_IMAGE_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/heic",
        "image/heif",
    }
)


def _read_secret(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8").strip()


def _read_token_file(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw.items()
    ):
        raise ValueError("media service token file must be a JSON object of app_id to token")
    return raw


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str = "sqlite:///./data/media.db"
    storage_root: Path = Path("./uploads")
    public_base_url: str = "http://127.0.0.1:8400"
    service_tokens: dict[str, str] = field(default_factory=dict)
    access_signing_key: str = ""
    max_upload_bytes: int = 15 * 1024 * 1024
    upload_ttl_seconds: int = 15 * 60
    access_ttl_seconds: int = 5 * 60
    soft_delete_retention_days: int = 7
    allowed_mime_types: frozenset[str] = ALLOWED_IMAGE_MIME_TYPES

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.getenv("SHADOW_MEDIA_DATABASE_URL", "sqlite:///./data/media.db"),
            storage_root=Path(os.getenv("SHADOW_MEDIA_STORAGE_ROOT", "./uploads")),
            public_base_url=os.getenv(
                "SHADOW_MEDIA_PUBLIC_BASE_URL", "http://127.0.0.1:8400"
            ).rstrip("/"),
            service_tokens=_read_token_file(os.getenv("SHADOW_MEDIA_SERVICE_TOKENS_FILE")),
            access_signing_key=_read_secret(os.getenv("SHADOW_MEDIA_ACCESS_SIGNING_KEY_FILE")),
            max_upload_bytes=int(os.getenv("SHADOW_MEDIA_MAX_UPLOAD_BYTES", str(15 * 1024 * 1024))),
            upload_ttl_seconds=int(os.getenv("SHADOW_MEDIA_UPLOAD_TTL_SECONDS", str(15 * 60))),
            access_ttl_seconds=int(os.getenv("SHADOW_MEDIA_ACCESS_TTL_SECONDS", str(5 * 60))),
        )
