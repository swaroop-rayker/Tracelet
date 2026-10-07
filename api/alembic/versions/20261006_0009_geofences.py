"""geofences: polygons, circles and regions; the visit's geofence state restated

Revision ID: 0009
Revises: 0008
Created: 2026-10-06

M6. ADR-0020 (geofences without a basemap), SPEC section 11 rows 16 and 18.

1. ``geofences`` is created whole. Beside the polygon and circle F6.AC2 describes, a
   ``region`` geofence holds ``region_keys`` (``IN``, ``IN|Karnataka``) and no area: it is
   matched on the strict country and state, never on a polygon (ADR-0020 decision 2).
   Which columns a shape carries is enforced by the engine, as are ``ST_IsValid`` and the
   2000-vertex cap (F6.AC4).

2. ``visits.geofence_state`` becomes nullable. NULL means "no active geofence applied to
   this visit", distinct from all three values, so ``outside`` never means "there were no
   geofences" (ADR-0020 decision 5). No geofence existed before this revision, so every
   stored value is the old column default rather than an evaluation, and is cleared.

3. Invariant 5 is restated (ADR-0020 decision 6). "``undetermined`` whenever ``geopoint``
   is NULL" became false once a strict state can place a visit inside a region geofence.
   Its purpose -- an abstaining inference is never "outside" or "inside" -- is kept by two
   CHECKs: ``outside`` needs a strict location at some level, ``inside`` needs a match.

4. ``links.notify_policy`` gains ``undetermined`` (default ``normal``), and ``automated``
   is pinned to ``silent`` by a CHECK: automated traffic never notifies (CLAUDE.md
   invariant 6, SPEC section 11 row 18).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS: dict[str, tuple[str, ...]] = {
    "shape_kind": ("polygon", "circle", "region"),
    "notify_priority": ("high", "normal", "silent"),
}

# Bounds the payload, not the evaluation: a region is matched by comparing two codes
# per key. 1000 is every country plus every Indian state many times over.
MAX_REGION_KEYS = 1000
# F6.AC4: bounds ST_Covers' cost per polygon.
MAX_VERTICES = 2000

_OLD_POLICY_DEFAULT = """'{"inside": "high", "outside": "normal", "automated": "silent"}'::jsonb"""
_NEW_POLICY_DEFAULT = (
    """'{"inside": "high", "outside": "normal", "undetermined": "normal", """
    """"automated": "silent"}'::jsonb"""
)


def _enum(name: str) -> pg.ENUM:
    return pg.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        pg.ENUM(*values, name=name).create(bind, checkfirst=True)

    # === geofences: DATA_MODEL section 6.1 ===================================
    op.create_table(
        "geofences",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("shape_kind", _enum("shape_kind"), nullable=False),
        # area, center: geography columns are added below with DDL (no geoalchemy2 in
        # a migration; see 0004).
        sa.Column("radius_m", sa.Numeric(10, 2), nullable=True),
        sa.Column("region_keys", pg.ARRAY(sa.Text()), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "notify_priority",
            _enum("notify_priority"),
            nullable=False,
            server_default="high",
        ),
        # NULL applies to every link. An empty array would apply to none, which is
        # what is_active=false says, so it is not a second way to say it.
        sa.Column("link_ids", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=True),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # SET NULL, as for links: deleting an admin must neither delete nor be blocked
        # by the geofences they drew.
        sa.ForeignKeyConstraint(
            ["created_by"], ["admins.id"], ondelete="SET NULL", name="fk_geofences_created_by"
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 100", name="name_length"),
        sa.CheckConstraint(
            "link_ids IS NULL OR cardinality(link_ids) > 0", name="link_ids_not_empty"
        ),
        sa.CheckConstraint(
            "(shape_kind = 'region') = (region_keys IS NOT NULL)", name="region_keys_iff_region"
        ),
        sa.CheckConstraint(
            "region_keys IS NULL OR ("
            f"cardinality(region_keys) BETWEEN 1 AND {MAX_REGION_KEYS} "
            "AND array_position(region_keys, NULL) IS NULL)",
            name="region_keys_bounded",
        ),
        sa.CheckConstraint(
            "(shape_kind = 'circle') = (radius_m IS NOT NULL)", name="radius_iff_circle"
        ),
        sa.CheckConstraint("radius_m IS NULL OR radius_m > 0", name="radius_positive"),
    )
    op.execute("ALTER TABLE geofences ADD COLUMN area geography(Polygon, 4326)")
    op.execute("ALTER TABLE geofences ADD COLUMN center geography(Point, 4326)")
    # A region has no area; a polygon and a circle (stored buffered) always do.
    op.execute(
        "ALTER TABLE geofences ADD CONSTRAINT ck_geofences_area_iff_shape "
        "CHECK ((shape_kind = 'region') = (area IS NULL))"
    )
    # A circle keeps its centre for round-trip editing (F6.AC2); nothing else has one.
    op.execute(
        "ALTER TABLE geofences ADD CONSTRAINT ck_geofences_center_iff_circle "
        "CHECK ((shape_kind = 'circle') = (center IS NOT NULL))"
    )
    # F6.AC4. The application rejects a self-intersecting ring first, with a specific
    # error code; these are the floor beneath it.
    op.execute(
        "ALTER TABLE geofences ADD CONSTRAINT ck_geofences_area_valid "
        "CHECK (area IS NULL OR ST_IsValid(area::geometry))"
    )
    op.execute(
        "ALTER TABLE geofences ADD CONSTRAINT ck_geofences_vertex_cap "
        f"CHECK (area IS NULL OR ST_NPoints(area::geometry) <= {MAX_VERTICES})"
    )
    op.execute("CREATE INDEX ix_geofences_area ON geofences USING gist (area)")
    op.create_index(
        "ix_geofences_active_priority",
        "geofences",
        [sa.text("priority DESC")],
        postgresql_where=sa.text("is_active"),
    )

    # === visits: the restated invariant 5 ====================================
    op.execute("ALTER TABLE visits DROP CONSTRAINT ck_visits_no_geopoint_is_undetermined")
    op.alter_column("visits", "geofence_state", nullable=True, server_default=None)
    op.execute("UPDATE visits SET geofence_state = NULL WHERE geofence_state IS NOT NULL")
    op.execute(
        "ALTER TABLE visits ADD CONSTRAINT ck_visits_outside_needs_strict "
        "CHECK (geofence_state <> 'outside' OR geopoint IS NOT NULL "
        "OR strict_country_code IS NOT NULL)"
    )
    op.execute(
        "ALTER TABLE visits ADD CONSTRAINT ck_visits_inside_has_match "
        "CHECK (geofence_state <> 'inside' OR cardinality(matched_geofence_ids) > 0)"
    )

    # === links: notify_policy ================================================
    op.execute(
        """
        UPDATE links
        SET notify_policy = notify_policy
            || '{"automated": "silent"}'::jsonb
            || jsonb_build_object(
                   'undetermined', coalesce(notify_policy->>'undetermined', 'normal'))
        """
    )
    op.alter_column("links", "notify_policy", server_default=sa.text(_NEW_POLICY_DEFAULT))
    op.execute(
        "ALTER TABLE links ADD CONSTRAINT ck_links_automated_silent "
        "CHECK (notify_policy->>'automated' = 'silent')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE links DROP CONSTRAINT ck_links_automated_silent")
    op.alter_column("links", "notify_policy", server_default=sa.text(_OLD_POLICY_DEFAULT))
    op.execute("UPDATE links SET notify_policy = notify_policy - 'undetermined'")

    op.execute("ALTER TABLE visits DROP CONSTRAINT ck_visits_inside_has_match")
    op.execute("ALTER TABLE visits DROP CONSTRAINT ck_visits_outside_needs_strict")
    # The geofences are about to be dropped, so no evaluation survives either.
    op.execute(
        "UPDATE visits SET geofence_state = 'undetermined', matched_geofence_ids = '{}'::uuid[]"
    )
    op.alter_column(
        "visits", "geofence_state", nullable=False, server_default=sa.text("'undetermined'")
    )
    op.execute(
        "ALTER TABLE visits ADD CONSTRAINT ck_visits_no_geopoint_is_undetermined "
        "CHECK (geopoint IS NOT NULL OR geofence_state = 'undetermined')"
    )

    op.drop_index("ix_geofences_active_priority", table_name="geofences")
    op.drop_table("geofences")
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE IF EXISTS {name}")
