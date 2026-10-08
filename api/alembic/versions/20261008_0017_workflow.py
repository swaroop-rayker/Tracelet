"""workflow: annotations and saved views

Revision ID: 0017
Revises: 0016
Created: 2026-10-08

SPEC F9.AC25, F9.AC26 and section 11 row 29; DATA_MODEL section 9a. Admin-entered data only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PATHS = r"^/(visits|geography|breakdowns|sources|returning|compare|links(/[a-z0-9-]{4,32})?)?$"


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "annotations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("link_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["link_id"], ["links.id"], ondelete="CASCADE", name="fk_annotations_link_id"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["admins.id"], ondelete="SET NULL", name="fk_annotations_created_by"
        ),
        sa.CheckConstraint(
            "char_length(btrim(text)) BETWEEN 1 AND 200", name="text_length"
        ),
    )
    op.create_index("ix_annotations_at", "annotations", ["at"])

    op.create_table(
        "saved_views",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("admin_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admins.id"], ondelete="CASCADE", name="fk_saved_views_admin_id"
        ),
        sa.UniqueConstraint("admin_id", "name", name="uq_saved_views_admin_name"),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 60", name="name_length"
        ),
        sa.CheckConstraint(f"path ~ '{PATHS}'", name="path_known"),
        sa.CheckConstraint("char_length(query) <= 2000", name="query_length"),
    )


def downgrade() -> None:
    op.drop_table("saved_views")
    op.drop_index("ix_annotations_at", table_name="annotations")
    op.drop_table("annotations")
