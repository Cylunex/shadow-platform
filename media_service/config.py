from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from shadow_sdk.service_auth import load_service_token_hashes

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

ALLOWED_ASSET_MIME_TYPES = frozenset(
    {
        *ALLOWED_IMAGE_MIME_TYPES,
        "application/epub+zip",
        "application/json",
        "application/octet-stream",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/warc",
        "application/xml",
        "application/zip",
        "audio/mpeg",
        "audio/wav",
        "text/csv",
        "text/html",
        "text/markdown",
        "text/plain",
        "video/mp4",
        "video/webm",
    }
)


def _read_secret(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8").strip()


def _read_token_file(path: str | None) -> dict[str, tuple[str, ...]]:
    if not path:
        return {}
    return load_service_token_hashes(path)


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "development"
    database_url: str = "sqlite:///./data/media.db"
    storage_root: Path = Path("./uploads")
    public_base_url: str = "http://127.0.0.1:8400"
    service_token_hashes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    access_signing_key: str = ""
    max_upload_bytes: int = 15 * 1024 * 1024
    upload_ttl_seconds: int = 15 * 60
    access_ttl_seconds: int = 5 * 60
    soft_delete_retention_days: int = 7
    strip_metadata: bool = True
    allowed_mime_types: frozenset[str] = ALLOWED_IMAGE_MIME_TYPES
    asset_max_upload_bytes: int = 512 * 1024 * 1024
    asset_upload_ttl_seconds: int = 24 * 60 * 60
    asset_trash_retention_days: int = 30
    asset_orphan_grace_days: int = 7
    asset_blob_gc_min_age_hours: int = 24
    asset_allowed_mime_types: frozenset[str] = ALLOWED_ASSET_MIME_TYPES

    def __post_init__(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("SHADOW_ENV must be development, test or production")
        parsed = urlsplit(self.public_base_url)
        local = parsed.hostname in {"127.0.0.1", "localhost", "testserver"}
        if not parsed.hostname or (
            parsed.scheme != "https" and not (local and parsed.scheme == "http")
        ):
            raise ValueError("media public URL must use HTTPS except in local development")
        if self.access_signing_key and len(self.access_signing_key) < 32:
            raise ValueError("media access signing key must contain at least 32 characters")
        if self.max_upload_bytes <= 0 or self.upload_ttl_seconds <= 0:
            raise ValueError("media upload limits must be positive")
        if self.asset_max_upload_bytes <= 0 or self.asset_upload_ttl_seconds <= 0:
            raise ValueError("asset upload limits must be positive")
        if not 1 <= self.soft_delete_retention_days <= 365:
            raise ValueError("media soft-delete retention must be between 1 and 365 days")
        if not 1 <= self.asset_trash_retention_days <= 3650:
            raise ValueError("asset trash retention must be between 1 and 3650 days")
        if not 1 <= self.asset_orphan_grace_days <= 365:
            raise ValueError("asset orphan grace must be between 1 and 365 days")
        if not 1 <= self.asset_blob_gc_min_age_hours <= 24 * 365:
            raise ValueError("asset Blob GC minimum age must be between 1 hour and 1 year")
        if self.environment == "production":
            if not self.service_token_hashes:
                raise ValueError("production media service tokens are required")
            if len(self.access_signing_key) < 32:
                raise ValueError("production media access signing key is required")
            if self.database_url.startswith("sqlite"):
                raise ValueError("production media service requires PostgreSQL")

    @classmethod
    def from_env(cls) -> Settings:
        database_url = _read_secret(os.getenv("SHADOW_MEDIA_DATABASE_URL_FILE")) or os.getenv(
            "SHADOW_MEDIA_DATABASE_URL", "sqlite:///./data/media.db"
        )
        return cls(
            environment=os.getenv("SHADOW_ENV", "development"),
            database_url=database_url,
            storage_root=Path(os.getenv("SHADOW_MEDIA_STORAGE_ROOT", "./uploads")),
            public_base_url=os.getenv(
                "SHADOW_MEDIA_PUBLIC_BASE_URL", "http://127.0.0.1:8400"
            ).rstrip("/"),
            service_token_hashes=_read_token_file(
                os.getenv("SHADOW_MEDIA_SERVICE_TOKEN_HASHES_FILE")
            ),
            access_signing_key=_read_secret(os.getenv("SHADOW_MEDIA_ACCESS_SIGNING_KEY_FILE")),
            max_upload_bytes=int(os.getenv("SHADOW_MEDIA_MAX_UPLOAD_BYTES", str(15 * 1024 * 1024))),
            upload_ttl_seconds=int(os.getenv("SHADOW_MEDIA_UPLOAD_TTL_SECONDS", str(15 * 60))),
            access_ttl_seconds=int(os.getenv("SHADOW_MEDIA_ACCESS_TTL_SECONDS", str(5 * 60))),
            soft_delete_retention_days=int(
                os.getenv("SHADOW_MEDIA_SOFT_DELETE_RETENTION_DAYS", "7")
            ),
            strip_metadata=os.getenv("SHADOW_MEDIA_STRIP_METADATA", "true").lower()
            not in {"0", "false", "no"},
            asset_max_upload_bytes=int(
                os.getenv("SHADOW_ASSET_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))
            ),
            asset_upload_ttl_seconds=int(
                os.getenv("SHADOW_ASSET_UPLOAD_TTL_SECONDS", str(24 * 60 * 60))
            ),
            asset_trash_retention_days=int(os.getenv("SHADOW_ASSET_TRASH_RETENTION_DAYS", "30")),
            asset_orphan_grace_days=int(os.getenv("SHADOW_ASSET_ORPHAN_GRACE_DAYS", "7")),
            asset_blob_gc_min_age_hours=int(os.getenv("SHADOW_ASSET_BLOB_GC_MIN_AGE_HOURS", "24")),
        )
