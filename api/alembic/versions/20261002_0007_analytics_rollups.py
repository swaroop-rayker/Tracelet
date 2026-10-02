"""analytics rollups: daily and hourly cells, a long-format dimension table, refresh state

Revision ID: 0007
Revises: 0006
Created: 2026-10-02

M5. The storage half of ADR-0016 (docs/DATA_MODEL.md section 9).

Three decisions are visible in the shape:

  1. **Every measure is additive.** A cell carries counts, and a sum *and* a count for
     each confidence, so an average is computed after summing. The Gate-3 columns
     `avg_confidence_*` and `unique_visitor_count` were not additive -- summing them across
     cells, days or links gives a wrong number -- and are not created.
  2. **`stage` is a key column**, so every analytics response can state the stage mix it
     was computed over (F9.AC20) from the same rows it read.
  3. **Unknown is `''`, not NULL**, in the key columns. A primary key cannot hold NULL,
     and an abstained location is a value worth counting ("how many abstained?"), not a
     row to lose.

Rollups survive raw-row purging (NFR5.AC4), so `link_id` cascades only with the link
itself, which the application never deletes outside tests.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str) -> pg.ENUM:
    # All four were created by migration 0004.
    return pg.ENUM(name=name, create_type=False)


def _link() -> sa.Column[sa.Uuid[object]]:
    return sa.Column(
        "link_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("links.id", ondelete="CASCADE"),
        nullable=False,
    )


def _cell_columns() -> list[sa.Column[object]]:
    """Key dimensions after the bucket, then additive measures (DATA_MODEL 9.1)."""
    count = sa.Integer()
    columns: list[sa.Column[object]] = [
        _link(),
        sa.Column("stage", _enum("visit_stage"), nullable=False),
        sa.Column("classification", _enum("classification"), nullable=False),
        sa.Column("device_class", _enum("device_class"), nullable=False),
        sa.Column("connection_class", _enum("connection_class"), nullable=False),
        sa.Column("country_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("admin1", sa.Text(), nullable=False, server_default=""),
        sa.Column("visit_count", count, nullable=False),
        sa.Column("consented_count", count, nullable=False),
        sa.Column("geofence_inside_count", count, nullable=False),
        sa.Column("geofence_outside_count", count, nullable=False),
        sa.Column("has_gps_count", count, nullable=False),
        sa.Column("has_point_count", count, nullable=False),
        sa.Column("inferred_count", count, nullable=False),
        sa.Column("strict_admin2_count", count, nullable=False),
        sa.Column("strict_city_count", count, nullable=False),
    ]
    for level in ("country", "admin1", "admin2", "city"):
        columns.append(sa.Column(f"conf_{level}_sum", sa.Numeric(12, 3), nullable=False))
        columns.append(sa.Column(f"conf_{level}_n", count, nullable=False))
    return columns


CELL_KEY = (
    "link_id",
    "stage",
    "classification",
    "device_class",
    "connection_class",
    "country_code",
    "admin1",
)


def upgrade() -> None:
    op.create_table(
        "rollup_visit_daily",
        sa.Column("day", sa.Date(), nullable=False),
        *_cell_columns(),
        sa.PrimaryKeyConstraint("day", *CELL_KEY, name="pk_rollup_visit_daily"),
    )
    op.create_table(
        "rollup_visit_hourly",
        # Local wall-clock hour in the reporting timezone (ADR-0016): India is UTC+05:30,
        # so a UTC hour would straddle every Indian one.
        sa.Column("hour", sa.DateTime(timezone=False), nullable=False),
        *_cell_columns(),
        sa.PrimaryKeyConstraint("hour", *CELL_KEY, name="pk_rollup_visit_hourly"),
    )
    op.create_table(
        "rollup_visit_dim_daily",
        sa.Column("day", sa.Date(), nullable=False),
        _link(),
        sa.Column("classification", _enum("classification"), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("visit_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "day", "link_id", "classification", "dimension", "value", name="pk_rollup_visit_dim"
        ),
    )
    # Every breakdown reads one dimension over a range of days.
    op.create_index("ix_rollup_dim_dimension_day", "rollup_visit_dim_daily", ["dimension", "day"])
    # Unique visitors are counted from raw rows (a distinct count does not add across
    # rollup cells, ADR-0016). Visit rows are wide -- about one heap page each at the
    # design load -- so this narrow covering index turns that count into an
    # index-only scan instead of a page read per visit. Measured on one CPU at 90 k
    # visits: 317 ms for a 30-day window through the heap.
    op.create_index(
        "ix_visits_occurred_identity",
        "visits",
        ["occurred_at"],
        postgresql_include=["visitor_id", "classification", "stage", "link_id"],
    )
    # Map points (F9.AC5) are always read raw, and only a few visits have coordinates
    # (a strict city or consented GPS, ADR-0005). A partial index over just those keeps
    # the point query from reading every visit in the window.
    op.create_index(
        "ix_visits_point_occurred",
        "visits",
        ["occurred_at"],
        postgresql_where=sa.text("gps_lat IS NOT NULL OR strict_lat IS NOT NULL"),
    )
    op.create_table(
        "rollup_state",
        # One row per day the refresh job has rebuilt. A day without a row has never been
        # rolled up, so a range touching it is answered from raw rows (ADR-0016).
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reporting_tz", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rollup_state")
    op.drop_index("ix_visits_point_occurred", table_name="visits")
    op.drop_index("ix_visits_occurred_identity", table_name="visits")
    op.drop_index("ix_rollup_dim_dimension_day", table_name="rollup_visit_dim_daily")
    op.drop_table("rollup_visit_dim_daily")
    op.drop_table("rollup_visit_hourly")
    op.drop_table("rollup_visit_daily")
