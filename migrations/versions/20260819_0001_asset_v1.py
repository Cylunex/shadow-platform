"""建立统一资产服务 v1 数据模型。

Revision ID: 20260819_0001
Revises:
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from media_service.models import MediaObject, UploadIntent

revision: str = "20260819_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # This is the first Alembic revision for a service that previously used create_all().
    # Keep the two legacy compatibility tables as a baseline on both existing and fresh DBs.
    bind = op.get_bind()
    UploadIntent.__table__.create(bind, checkfirst=True)
    MediaObject.__table__.create(bind, checkfirst=True)

    op.create_table(
        "asset_audit_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("action", "app_id", "asset_id", "created_at"):
        op.create_index(f"ix_asset_audit_events_{column}", "asset_audit_events", [column])

    op.create_table(
        "asset_blobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("digest_algorithm", sa.String(length=16), nullable=False),
        sa.Column("digest", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("integrity_state", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gc_marked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("size_bytes > 0", name="ck_asset_blob_positive_size"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "digest_algorithm", "digest", "size_bytes", name="uq_asset_blob_digest_size"
        ),
    )
    for column in ("created_at", "digest", "integrity_state"):
        op.create_index(f"ix_asset_blobs_{column}", "asset_blobs", [column])

    op.create_table(
        "asset_outbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("aggregate_id", "created_at", "event_type"):
        op.create_index(f"ix_asset_outbox_events_{column}", "asset_outbox_events", [column])

    op.create_table(
        "asset_upload_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("ownership_mode", sa.String(length=24), nullable=False),
        sa.Column("access_mode", sa.String(length=24), nullable=False),
        sa.Column("sensitivity", sa.String(length=24), nullable=False),
        sa.Column("retention_policy_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("declared_mime", sa.String(length=255), nullable=False),
        sa.Column("declared_size", sa.BigInteger(), nullable=False),
        sa.Column("staging_key", sa.String(length=512), nullable=False),
        sa.Column("upload_token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("actual_mime", sa.String(length=255), nullable=True),
        sa.Column("media_family", sa.String(length=32), nullable=True),
        sa.Column("actual_size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("technical_metadata", sa.JSON(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("initial_resource_uri", sa.String(length=1024), nullable=True),
        sa.Column("initial_usage_role", sa.String(length=64), nullable=True),
        sa.Column("initial_reference_key", sa.String(length=512), nullable=True),
        sa.Column("initial_binding_mode", sa.String(length=16), nullable=True),
        sa.Column("target_asset_id", sa.String(length=36), nullable=True),
        sa.Column("change_reason", sa.String(length=255), nullable=True),
        sa.Column("asset_id", sa.String(length=36), nullable=True),
        sa.Column("result_version_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("declared_size > 0", name="ck_asset_upload_positive_size"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_id", "idempotency_key", name="uq_asset_upload_idempotency"),
        sa.UniqueConstraint("result_version_id"),
        sa.UniqueConstraint("staging_key"),
    )
    for column in ("app_id", "expires_at", "owner_id", "status", "target_asset_id"):
        op.create_index(f"ix_asset_upload_sessions_{column}", "asset_upload_sessions", [column])

    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_app_id", sa.String(length=64), nullable=False),
        sa.Column("ownership_mode", sa.String(length=24), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        sa.Column("access_mode", sa.String(length=24), nullable=False),
        sa.Column("sensitivity", sa.String(length=24), nullable=False),
        sa.Column("retention_policy_key", sa.String(length=64), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=24), nullable=False),
        sa.Column("current_version_id", sa.String(length=36), nullable=True),
        sa.Column("zero_referenced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "NOT (sensitivity = 'restricted' AND access_mode = 'public')",
            name="ck_asset_restricted_not_public",
        ),
        sa.CheckConstraint(
            "access_mode IN ('private', 'delegated', 'public')", name="ck_asset_access_mode"
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('active', 'trashed', 'purged')", name="ck_asset_lifecycle_state"
        ),
        sa.CheckConstraint(
            "ownership_mode IN ('user_owned', 'app_managed', 'derived')",
            name="ck_asset_ownership_mode",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('normal', 'sensitive', 'restricted')", name="ck_asset_sensitivity"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "access_mode",
        "created_at",
        "created_by_app_id",
        "lifecycle_state",
        "owner_id",
        "ownership_mode",
        "purge_after",
        "sensitivity",
        "zero_referenced_at",
    ):
        op.create_index(f"ix_assets_{column}", "assets", [column])

    op.create_table(
        "asset_blob_locations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("blob_id", sa.String(length=36), nullable=False),
        sa.Column("backend_id", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("backend_version_id", sa.String(length=255), nullable=True),
        sa.Column("backend_checksum", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["blob_id"], ["asset_blobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("backend_id", "object_key", name="uq_asset_blob_location_key"),
    )
    op.create_index("ix_asset_blob_locations_blob_id", "asset_blob_locations", ["blob_id"])
    op.create_index("ix_asset_blob_locations_state", "asset_blob_locations", ["state"])

    op.create_table(
        "asset_legacy_media_map",
        sa.Column("media_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["media_id"], ["media_objects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("media_id"),
    )
    op.create_index(
        "ix_asset_legacy_media_map_asset_id", "asset_legacy_media_map", ["asset_id"], unique=True
    )

    op.create_table(
        "asset_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("blob_id", sa.String(length=36), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("declared_mime", sa.String(length=255), nullable=False),
        sa.Column("detected_mime", sa.String(length=255), nullable=False),
        sa.Column("media_family", sa.String(length=32), nullable=False),
        sa.Column("technical_metadata", sa.JSON(), nullable=False),
        sa.Column("source_fidelity", sa.String(length=32), nullable=False),
        sa.Column("change_reason", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("explicitly_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source_fidelity IN ('original', 'migrated_sanitized', 'derived')",
            name="ck_asset_version_source_fidelity",
        ),
        sa.CheckConstraint(
            "state IN ('processing', 'ready', 'quarantined', 'failed')",
            name="ck_asset_version_state",
        ),
        sa.CheckConstraint("version_number > 0", name="ck_asset_version_positive_number"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["blob_id"], ["asset_blobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "version_number", name="uq_asset_version_number"),
        sa.UniqueConstraint("id", "asset_id", name="uq_asset_version_id_asset"),
    )
    for column in ("asset_id", "blob_id", "created_at", "media_family", "state"):
        op.create_index(f"ix_asset_versions_{column}", "asset_versions", [column])

    op.create_table(
        "asset_derivatives",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("derived_version_id", sa.String(length=36), nullable=False),
        sa.Column("recipe_key", sa.String(length=128), nullable=False),
        sa.Column("recipe_version", sa.String(length=64), nullable=False),
        sa.Column("parameters_hash", sa.String(length=64), nullable=False),
        sa.Column("generator", sa.String(length=128), nullable=False),
        sa.Column("generator_version", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('processing', 'ready', 'failed')", name="ck_asset_derivative_state"
        ),
        sa.CheckConstraint(
            "source_version_id <> derived_version_id", name="ck_asset_derivative_not_self"
        ),
        sa.ForeignKeyConstraint(["derived_version_id"], ["asset_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_version_id"], ["asset_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_version_id",
            "recipe_key",
            "recipe_version",
            "parameters_hash",
            name="uq_asset_derivative_recipe",
        ),
    )
    op.create_index(
        "ix_asset_derivatives_derived_version_id", "asset_derivatives", ["derived_version_id"]
    )
    op.create_index(
        "ix_asset_derivatives_source_version_id", "asset_derivatives", ["source_version_id"]
    )

    op.create_table(
        "asset_processing_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=128), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_version_id"], ["asset_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_asset_processing_job_dedupe"),
    )
    for column in ("available_at", "job_type", "source_version_id", "status"):
        op.create_index(f"ix_asset_processing_jobs_{column}", "asset_processing_jobs", [column])

    op.create_table(
        "asset_references",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("resource_uri", sa.String(length=1024), nullable=False),
        sa.Column("usage_role", sa.String(length=64), nullable=False),
        sa.Column("reference_key", sa.String(length=512), nullable=False),
        sa.Column("binding_mode", sa.String(length=16), nullable=False),
        sa.Column("pinned_version_id", sa.String(length=36), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(binding_mode = 'pinned' AND pinned_version_id IS NOT NULL) OR "
            "(binding_mode = 'latest' AND pinned_version_id IS NULL)",
            name="ck_asset_reference_pinned_semantics",
        ),
        sa.CheckConstraint(
            "binding_mode IN ('pinned', 'latest')", name="ck_asset_reference_binding_mode"
        ),
        sa.CheckConstraint("state IN ('active', 'released')", name="ck_asset_reference_state"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["pinned_version_id", "asset_id"],
            ["asset_versions.id", "asset_versions.asset_id"],
            name="fk_asset_reference_pinned_version_asset",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_id", "reference_key", name="uq_asset_reference_key"),
    )
    op.create_index("ix_asset_reference_active_asset", "asset_references", ["asset_id", "state"])
    for column in ("app_id", "asset_id", "state"):
        op.create_index(f"ix_asset_references_{column}", "asset_references", [column])


def downgrade() -> None:
    for table in (
        "asset_references",
        "asset_processing_jobs",
        "asset_derivatives",
        "asset_versions",
        "asset_legacy_media_map",
        "asset_blob_locations",
        "assets",
        "asset_upload_sessions",
        "asset_outbox_events",
        "asset_blobs",
        "asset_audit_events",
    ):
        op.drop_table(table)
