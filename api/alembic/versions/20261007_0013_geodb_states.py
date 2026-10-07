"""geo databases: an attempt's progress, and per-database settings

Revision ID: 0013
Revises: 0012
Created: 2026-10-07

SPEC section 11 row 24, DATA_MODEL section 8.3.

1. ``geo_databases`` gains ``phase``, ``progress_bytes`` and ``total_bytes``: an attempt in
   flight writes how far it has got, so either worker can report "Updating 42 %".

2. ``geo_database_settings``: one row per database name -- whether the scheduler updates it,
   and what the last release check found (or why it could not check).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PHASES = ("downloading", "verifying", "unpacking", "validating", "installing")


def upgrade() -> None:
    op.add_column("geo_databases", sa.Column("phase", sa.Text(), nullable=True))
    op.add_column("geo_databases", sa.Column("progress_bytes", sa.BigInteger(), nullable=True))
    op.add_column("geo_databases", sa.Column("total_bytes", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "phase_known",
        "geo_databases",
        "phase IS NULL OR phase IN (" + ", ".join(f"'{p}'" for p in PHASES) + ")",
    )

    op.create_table(
        "geo_database_settings",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("auto_update", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("latest_version", sa.Text(), nullable=True),
        sa.Column("latest_released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_error", sa.Text(), nullable=True),
        sa.Column("updated_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["admins.id"],
            ondelete="SET NULL",
            name="fk_geo_database_settings_updated_by",
        ),
    )


def downgrade() -> None:
    op.drop_table("geo_database_settings")
    op.drop_constraint("phase_known", "geo_databases", type_="check")
    op.drop_column("geo_databases", "total_bytes")
    op.drop_column("geo_databases", "progress_bytes")
    op.drop_column("geo_databases", "phase")
