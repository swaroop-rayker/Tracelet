"""best-guess location in analytics: the map's point index, and stale rollup days

Revision ID: 0008
Revises: 0007
Created: 2026-10-02

ADR-0018. Analytics now counts and plots the best-guess (advisory) location instead of
the strict one, so two things built in 0007 describe the old rule:

  1. ``ix_visits_point_occurred`` was a partial index over visits with a *strict*
     coordinate. Map points are now consented GPS or the best-guess city, so the
     predicate follows them.
  2. Every rollup day built before this revision keyed its location columns on strict
     fields. Forgetting those days (``rollup_state``) makes the read path answer them
     from raw rows at once and the settle job rebuild them under the new rule. Only days
     that still have raw visits are forgotten: a day whose visits were purged by
     retention keeps its rollup, because it could not be rebuilt -- its location keys
     stay strict, and DATA_MODEL section 9 says so.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_visits_point_occurred", table_name="visits")
    op.create_index(
        "ix_visits_point_occurred",
        "visits",
        ["occurred_at"],
        postgresql_where=sa.text("gps_lat IS NOT NULL OR advisory_city IS NOT NULL"),
    )
    op.execute(
        """
        DELETE FROM rollup_state s
        WHERE s.day >= (SELECT (min(v.occurred_at) AT TIME ZONE s.reporting_tz)::date
                        FROM visits v)
        """
    )


def downgrade() -> None:
    # Rollup days rebuilt under the new rule are not reverted: rebuild them with
    # `tracelet analytics rebuild` after downgrading the code.
    op.drop_index("ix_visits_point_occurred", table_name="visits")
    op.create_index(
        "ix_visits_point_occurred",
        "visits",
        ["occurred_at"],
        postgresql_where=sa.text("gps_lat IS NOT NULL OR strict_lat IS NOT NULL"),
    )
