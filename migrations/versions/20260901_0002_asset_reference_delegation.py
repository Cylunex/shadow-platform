"""Add explicit private asset reference delegation.

Revision ID: 20260901_0002
Revises: 20260819_0001
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0002"
down_revision: str | Sequence[str] | None = "20260819_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "asset_references",
        sa.Column("delegated_by_app_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_asset_references_delegated_by_app_id",
        "asset_references",
        ["delegated_by_app_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_asset_references_delegated_by_app_id", table_name="asset_references"
    )
    op.drop_column("asset_references", "delegated_by_app_id")
