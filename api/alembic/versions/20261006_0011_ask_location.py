"""links.ask_location: ask chosen links' visitors for their location

Revision ID: 0011
Revises: 0010
Created: 2026-10-06

ADR-0021, F1.AC11. Off for every existing link, so nothing changes until the owner turns
it on for a link.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "links",
        sa.Column("ask_location", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("links", "ask_location")
