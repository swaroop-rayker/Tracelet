"""admins: how often System Health refreshes for each admin

Revision ID: 0014
Revises: 0013
Created: 2026-10-08

F10.AC1 ("Polled, with configurable interval"), DATA_MODEL section 3.1.
``admins.health_refresh_seconds``, one of 5, 15, 30 or 60; 15 was the fixed interval it
replaces, so nobody's view changes until they choose.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INTERVALS = (5, 15, 30, 60)


def upgrade() -> None:
    op.add_column(
        "admins",
        sa.Column(
            "health_refresh_seconds", sa.SmallInteger(), nullable=False, server_default="15"
        ),
    )
    op.create_check_constraint(
        "health_refresh_known",
        "admins",
        "health_refresh_seconds IN (" + ", ".join(str(s) for s in INTERVALS) + ")",
    )


def downgrade() -> None:
    op.drop_constraint("health_refresh_known", "admins", type_="check")
    op.drop_column("admins", "health_refresh_seconds")
