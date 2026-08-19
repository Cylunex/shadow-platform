from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from .asset_models import (
    Asset,
    AssetAuditEvent,
    AssetBlob,
    AssetOutboxEvent,
    AssetReference,
    AssetVersion,
)
from .config import Settings


@dataclass(frozen=True, slots=True)
class AssetLifecycleResult:
    orphaned_trashed: int
    purged_assets: int
    gc_candidates: tuple[str, ...]


def _system_event(db: Session, *, event_type: str, asset: Asset, now: datetime) -> None:
    db.add(
        AssetOutboxEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            aggregate_type="asset",
            aggregate_id=asset.id,
            payload={"asset_id": asset.id, "reason": "retention-policy"},
            created_at=now,
        )
    )
    db.add(
        AssetAuditEvent(
            app_id="platform",
            actor="system:asset-lifecycle",
            action=event_type,
            asset_id=asset.id,
            details={"reason": "retention-policy"},
            created_at=now,
        )
    )


def gc_candidate_ids(
    db: Session,
    settings: Settings,
    *,
    now: datetime,
) -> tuple[str, ...]:
    """Return unreachable old Blobs without deleting data.

    Every non-explicitly-deleted historical version of an active or trashed Asset is a GC root.
    This is deliberately independent from current_version_id and active references.
    """

    minimum_age = now - timedelta(hours=settings.asset_blob_gc_min_age_hours)
    reachable = (
        select(AssetVersion.id)
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .where(
            AssetVersion.blob_id == AssetBlob.id,
            AssetVersion.explicitly_deleted_at.is_(None),
            Asset.lifecycle_state.in_(["active", "trashed"]),
        )
    )
    return tuple(
        db.scalars(
            select(AssetBlob.id)
            .where(
                AssetBlob.deleted_at.is_(None),
                AssetBlob.created_at <= minimum_age,
                ~exists(reachable),
            )
            .order_by(AssetBlob.created_at, AssetBlob.id)
        )
    )


def apply_asset_lifecycle(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    mark_gc: bool = True,
) -> AssetLifecycleResult:
    now = now or datetime.now(UTC)
    orphan_cutoff = now - timedelta(days=settings.asset_orphan_grace_days)
    has_active_reference = exists(
        select(AssetReference.id).where(
            AssetReference.asset_id == Asset.id,
            AssetReference.state == "active",
        )
    )
    orphaned_trashed = 0
    for asset in db.scalars(
        select(Asset).where(
            Asset.lifecycle_state == "active",
            Asset.ownership_mode == "app_managed",
            Asset.zero_referenced_at.is_not(None),
            Asset.zero_referenced_at <= orphan_cutoff,
            ~has_active_reference,
        )
    ):
        asset.lifecycle_state = "trashed"
        asset.trashed_at = now
        asset.purge_after = now + timedelta(days=settings.asset_trash_retention_days)
        _system_event(db, event_type="asset.trashed", asset=asset, now=now)
        orphaned_trashed += 1

    purged_assets = 0
    for asset in db.scalars(
        select(Asset).where(
            Asset.lifecycle_state == "trashed",
            Asset.purge_after.is_not(None),
            Asset.purge_after <= now,
        )
    ):
        asset.lifecycle_state = "purged"
        _system_event(db, event_type="asset.purged", asset=asset, now=now)
        purged_assets += 1

    db.flush()
    candidates = gc_candidate_ids(db, settings, now=now)
    if mark_gc and candidates:
        for blob in db.scalars(select(AssetBlob).where(AssetBlob.id.in_(candidates))):
            blob.gc_marked_at = blob.gc_marked_at or now
    db.commit()
    return AssetLifecycleResult(
        orphaned_trashed=orphaned_trashed,
        purged_assets=purged_assets,
        gc_candidates=candidates,
    )
