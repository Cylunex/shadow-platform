from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class AssetBlob(Base):
    __tablename__ = "asset_blobs"
    __table_args__ = (
        UniqueConstraint(
            "digest_algorithm", "digest", "size_bytes", name="uq_asset_blob_digest_size"
        ),
        CheckConstraint("size_bytes > 0", name="ck_asset_blob_positive_size"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    digest_algorithm: Mapped[str] = mapped_column(String(16), default="sha256")
    digest: Mapped[str] = mapped_column(String(128), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    integrity_state: Mapped[str] = mapped_column(String(24), default="healthy", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    gc_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssetBlobLocation(Base):
    __tablename__ = "asset_blob_locations"
    __table_args__ = (
        UniqueConstraint("backend_id", "object_key", name="uq_asset_blob_location_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    blob_id: Mapped[str] = mapped_column(
        ForeignKey("asset_blobs.id", ondelete="RESTRICT"), index=True
    )
    backend_id: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(1024))
    backend_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    backend_checksum: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="available", index=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "ownership_mode IN ('user_owned', 'app_managed', 'derived')",
            name="ck_asset_ownership_mode",
        ),
        CheckConstraint(
            "access_mode IN ('private', 'delegated', 'public')",
            name="ck_asset_access_mode",
        ),
        CheckConstraint(
            "sensitivity IN ('normal', 'sensitive', 'restricted')",
            name="ck_asset_sensitivity",
        ),
        CheckConstraint(
            "lifecycle_state IN ('active', 'trashed', 'purged')",
            name="ck_asset_lifecycle_state",
        ),
        CheckConstraint(
            "NOT (sensitivity = 'restricted' AND access_mode = 'public')",
            name="ck_asset_restricted_not_public",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    created_by_app_id: Mapped[str] = mapped_column(String(64), index=True)
    ownership_mode: Mapped[str] = mapped_column(String(24), index=True)
    display_name: Mapped[str] = mapped_column(String(512))
    access_mode: Mapped[str] = mapped_column(String(24), default="private", index=True)
    sensitivity: Mapped[str] = mapped_column(String(24), default="normal", index=True)
    retention_policy_key: Mapped[str] = mapped_column(String(64), default="standard")
    lifecycle_state: Mapped[str] = mapped_column(String(24), default="active", index=True)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    zero_referenced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class AssetVersion(Base):
    __tablename__ = "asset_versions"
    __table_args__ = (
        UniqueConstraint("asset_id", "version_number", name="uq_asset_version_number"),
        UniqueConstraint("id", "asset_id", name="uq_asset_version_id_asset"),
        CheckConstraint("version_number > 0", name="ck_asset_version_positive_number"),
        CheckConstraint(
            "state IN ('processing', 'ready', 'quarantined', 'failed')",
            name="ck_asset_version_state",
        ),
        CheckConstraint(
            "source_fidelity IN ('original', 'migrated_sanitized', 'derived')",
            name="ck_asset_version_source_fidelity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    blob_id: Mapped[str] = mapped_column(
        ForeignKey("asset_blobs.id", ondelete="RESTRICT"), index=True
    )
    original_filename: Mapped[str] = mapped_column(String(512))
    declared_mime: Mapped[str] = mapped_column(String(255))
    detected_mime: Mapped[str] = mapped_column(String(255))
    media_family: Mapped[str] = mapped_column(String(32), index=True)
    technical_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    source_fidelity: Mapped[str] = mapped_column(String(32), default="original")
    change_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(24), default="ready", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    explicitly_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AssetReference(Base):
    __tablename__ = "asset_references"
    __table_args__ = (
        UniqueConstraint("app_id", "reference_key", name="uq_asset_reference_key"),
        ForeignKeyConstraint(
            ["pinned_version_id", "asset_id"],
            ["asset_versions.id", "asset_versions.asset_id"],
            name="fk_asset_reference_pinned_version_asset",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "binding_mode IN ('pinned', 'latest')", name="ck_asset_reference_binding_mode"
        ),
        CheckConstraint(
            "(binding_mode = 'pinned' AND pinned_version_id IS NOT NULL) OR "
            "(binding_mode = 'latest' AND pinned_version_id IS NULL)",
            name="ck_asset_reference_pinned_semantics",
        ),
        CheckConstraint("state IN ('active', 'released')", name="ck_asset_reference_state"),
        Index("ix_asset_reference_active_asset", "asset_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)
    app_id: Mapped[str] = mapped_column(String(64), index=True)
    resource_uri: Mapped[str] = mapped_column(String(1024))
    usage_role: Mapped[str] = mapped_column(String(64))
    reference_key: Mapped[str] = mapped_column(String(512))
    delegated_by_app_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    binding_mode: Mapped[str] = mapped_column(String(16), default="pinned")
    pinned_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssetDerivative(Base):
    __tablename__ = "asset_derivatives"
    __table_args__ = (
        UniqueConstraint(
            "source_version_id",
            "recipe_key",
            "recipe_version",
            "parameters_hash",
            name="uq_asset_derivative_recipe",
        ),
        CheckConstraint(
            "source_version_id <> derived_version_id", name="ck_asset_derivative_not_self"
        ),
        CheckConstraint(
            "state IN ('processing', 'ready', 'failed')", name="ck_asset_derivative_state"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_version_id: Mapped[str] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="RESTRICT"), index=True
    )
    derived_version_id: Mapped[str] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="RESTRICT"), index=True
    )
    recipe_key: Mapped[str] = mapped_column(String(128))
    recipe_version: Mapped[str] = mapped_column(String(64))
    parameters_hash: Mapped[str] = mapped_column(String(64))
    generator: Mapped[str] = mapped_column(String(128))
    generator_version: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssetUploadSession(Base):
    __tablename__ = "asset_upload_sessions"
    __table_args__ = (
        UniqueConstraint("app_id", "idempotency_key", name="uq_asset_upload_idempotency"),
        CheckConstraint("declared_size > 0", name="ck_asset_upload_positive_size"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    ownership_mode: Mapped[str] = mapped_column(String(24))
    access_mode: Mapped[str] = mapped_column(String(24))
    sensitivity: Mapped[str] = mapped_column(String(24))
    retention_policy_key: Mapped[str] = mapped_column(String(64), default="standard")
    display_name: Mapped[str] = mapped_column(String(512))
    original_filename: Mapped[str] = mapped_column(String(512))
    declared_mime: Mapped[str] = mapped_column(String(255))
    declared_size: Mapped[int] = mapped_column(BigInteger)
    staging_key: Mapped[str] = mapped_column(String(512), unique=True)
    upload_token_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    actual_mime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_family: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actual_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    technical_metadata: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    initial_resource_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    initial_usage_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    initial_reference_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    initial_binding_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    change_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssetProcessingJob(Base):
    __tablename__ = "asset_processing_jobs"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_asset_processing_job_dedupe"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(128), index=True)
    source_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("asset_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssetOutboxEvent(Base):
    __tablename__ = "asset_outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssetAuditEvent(Base):
    __tablename__ = "asset_audit_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    app_id: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(128), index=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AssetLegacyMediaMap(Base):
    __tablename__ = "asset_legacy_media_map"

    media_id: Mapped[str] = mapped_column(
        ForeignKey("media_objects.id", ondelete="RESTRICT"), primary_key=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
