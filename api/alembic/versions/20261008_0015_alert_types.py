"""alert types: four outbox kinds, and the strict places each link has seen

Revision ID: 0015
Revises: 0014
Created: 2026-10-08

SPEC F7.AC10-F7.AC15 (section 11 row 27), DATA_MODEL sections 2, 7.1 and 7.2.

1. ``outbox_kind`` gains ``telegram.digest``, ``telegram.spike``, ``telegram.new_place`` and
   ``telegram.returning``. ``ALTER TYPE ... ADD VALUE`` runs outside the migration's
   transaction: a value added inside one cannot be used until it commits, and a later
   migration in the same run might use it.

2. ``link_places``: the strict country and state keys each link has seen, so "first visit
   from a new place" is decided by a primary key that races to one row, and outlives the
   outbox's 30-day purge of delivered rows. Seeded from every stored human visit, earliest
   first, so switching the alert on announces no place already seen.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_KINDS = ("telegram.digest", "telegram.spike", "telegram.new_place", "telegram.returning")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for kind in NEW_KINDS:
            op.execute(f"ALTER TYPE outbox_kind ADD VALUE IF NOT EXISTS '{kind}'")

    op.create_table(
        "link_places",
        sa.Column("link_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("region_key", sa.Text(), nullable=False),
        sa.Column("first_visit_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("link_id", "region_key", name="pk_link_places"),
        sa.ForeignKeyConstraint(
            ["link_id"], ["links.id"], ondelete="CASCADE", name="fk_link_places_link_id"
        ),
    )
    # Each human visit with a strict country contributes its country key and, with a strict
    # state, its state key; DISTINCT ON keeps the earliest visit per (link, key).
    op.execute(
        """
        INSERT INTO link_places (link_id, region_key, first_visit_id, first_seen_at)
        SELECT DISTINCT ON (link_id, region_key) link_id, region_key, id, occurred_at
        FROM (
            SELECT link_id, strict_country_code AS region_key, id, occurred_at
            FROM visits
            WHERE classification = 'human' AND strict_country_code IS NOT NULL
            UNION ALL
            SELECT link_id, strict_country_code || '|' || strict_admin1, id, occurred_at
            FROM visits
            WHERE classification = 'human'
              AND strict_country_code IS NOT NULL AND strict_admin1 IS NOT NULL
        ) seen
        ORDER BY link_id, region_key, occurred_at, id
        """
    )


def downgrade() -> None:
    # PostgreSQL cannot drop an enum value without rewriting every table that uses the type;
    # the four kinds stay, unused (as `latency` does in inference_source).
    op.drop_table("link_places")
