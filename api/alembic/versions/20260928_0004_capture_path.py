"""capture path: links and visits

Revision ID: 0004
Revises: 0003
Created: 2026-09-28

M2. Implements F1 (tracking links) and the storage half of F2/F3 (capture).

The `visits` table is created in full here, including the columns M3-M6 populate
(location, classification, geofence). Creating it once, whole, is cheaper than four
successive ALTERs on the busiest table in the system, and it lets every later
milestone write to columns whose constraints already exist -- in particular the
consent/GPS CHECK, which must never be a later addition.

Four invariants are enforced by the **engine**, because each is the kind of property
a future code path can quietly violate (docs/DATA_MODEL.md section 5.3):

  1. No GPS coordinates or street address unless consent was granted (F4.AC3, OOS4).
  2. An abstaining location is never silently treated as "outside" a geofence
     (F6.AC6): no geopoint means geofence_state is 'undetermined'.
  3. `stage='server'` exactly when the visit is not yet finalised, so the 90 s
     sweeper's partial index and its UPDATE cannot disagree about what is pending.
  4. The IP ciphertext and its key version are present or absent together, so a
     purge can never leave a key version pointing at nothing.

**There is no plaintext IP column.** Not "usually empty" -- it does not exist
(ADR-0007). `request_headers` is the one column that could have smuggled one in, via
X-Forwarded-For and friends; the capture path strips every IP-bearing header before
it is stored, and a test asserts it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS: dict[str, tuple[str, ...]] = {
    "visit_stage": ("server", "enriched", "server_only", "rate_limited"),
    "classification": ("human", "bot", "crawler", "datacenter", "spam", "spoofed", "unknown"),
    "consent_state": ("granted", "denied", "unavailable", "not_asked", "blocked_by_webview"),
    "connection_class": (
        "broadband",
        "mobile",
        "datacenter",
        "vpn_suspected",
        "tor",
        "business",
        "unknown",
    ),
    "asn_type": (
        "broadband",
        "mobile",
        "hosting",
        "business",
        "education",
        "government",
        "unknown",
    ),
    "device_class": ("mobile", "tablet", "desktop", "tv", "server", "bot", "unknown"),
    "geofence_state": ("inside", "outside", "undetermined"),
    "inference_source": (
        "gps",
        "geolite2",
        "ip2location",
        "ipinfo",
        "dbip",
        "rdns",
        "asn_org",
        "cf_colo",
        "external_api",
        "latency",
        "timezone",
    ),
}


def _enum(name: str) -> pg.ENUM:
    return pg.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        pg.ENUM(*values, name=name).create(bind, checkfirst=True)

    # === links =============================================================
    op.create_table(
        "links",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", pg.CITEXT(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        # The ONLY source of a redirect target anywhere in the system (F1.AC7,
        # F13.AC3). The CHECK is a floor; the application also verifies the host
        # resolves to a public address (F1.AC2), which SQL cannot.
        sa.Column("destination_url", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "notify_policy",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text(
                """'{"inside": "high", "outside": "normal", "automated": "silent"}'::jsonb"""
            ),
        ),
        sa.Column("interstitial_ms", sa.Integer(), nullable=False, server_default="700"),
        sa.Column("cloned_from", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("slug", name="uq_links_slug"),
        sa.ForeignKeyConstraint(
            ["cloned_from"], ["links.id"], ondelete="SET NULL", name="fk_links_cloned_from"
        ),
        # SET NULL: deleting an admin must not delete, or be blocked by, the links
        # they created.
        sa.ForeignKeyConstraint(
            ["created_by"], ["admins.id"], ondelete="SET NULL", name="fk_links_created_by"
        ),
        # Cast to text so the match is case-SENSITIVE. citext's own ~ operator is
        # case-insensitive and would let 'IG-Bio' through; the application lowercases,
        # and this makes sure nothing else can store a slug it did not normalise.
        sa.CheckConstraint("slug::text ~ '^[a-z0-9-]{4,32}$'", name="slug_format"),
        sa.CheckConstraint(
            "destination_url LIKE 'https://%' AND length(destination_url) <= 2048",
            name="destination_https",
        ),
        sa.CheckConstraint("interstitial_ms BETWEEN 300 AND 1500", name="interstitial_range"),
        # An archived link cannot be the default: "exactly one default" is a
        # statement about non-archived links (F1.AC3).
        sa.CheckConstraint("archived_at IS NULL OR NOT is_default", name="archived_not_default"),
    )
    # AT MOST one default, enforced by the engine. "At least one" is the
    # application's half, because an empty links table legitimately has none.
    op.create_index(
        "uq_links_one_default",
        "links",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default AND archived_at IS NULL"),
    )
    # The capture hot path only ever looks up live links.
    op.create_index(
        "ix_links_live_slug",
        "links",
        ["slug"],
        postgresql_where=sa.text("is_active AND archived_at IS NULL"),
    )

    # === visits ============================================================
    op.create_table(
        "visits",
        # --- identity and lifecycle ----------------------------------------
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),  # UUIDv7, time-ordered
        sa.Column("link_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("stage", _enum("visit_stage"), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        # Makes the enrichment nonce single-use without a separate table (F2.AC6).
        sa.Column("enrichment_consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        # --- network: F12.AC1, RW-3, ADR-0007 -------------------------------
        # Nullable so a missing pepper degrades the telemetry rather than failing
        # the insert and losing the visit (CLAUDE.md invariant 1).
        sa.Column("ip_hmac", pg.BYTEA(), nullable=True),
        sa.Column("ip_prefix", pg.INET(), nullable=True),
        sa.Column("ip_enc", pg.BYTEA(), nullable=True),
        sa.Column("ip_key_version", sa.SmallInteger(), nullable=True),
        sa.Column("ip_purge_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("asn", sa.Integer(), nullable=True),
        sa.Column("asn_org", sa.Text(), nullable=True),
        sa.Column("asn_type", _enum("asn_type"), nullable=False, server_default="unknown"),
        sa.Column("rdns_ptr", sa.Text(), nullable=True),
        sa.Column(
            "connection_class",
            _enum("connection_class"),
            nullable=False,
            server_default="unknown",
        ),
        # NULL means "not yet assessed", which is what they are until M4. A default
        # of false would assert "not a datacenter" with no evidence (F3.AC5).
        sa.Column("is_datacenter", sa.Boolean(), nullable=True),
        sa.Column("is_vpn_suspected", sa.Boolean(), nullable=True),
        sa.Column("is_tor", sa.Boolean(), nullable=True),
        sa.Column("is_proxy_suspected", sa.Boolean(), nullable=True),
        sa.Column("cf_colo", sa.CHAR(3), nullable=True),
        sa.Column("cf_country", sa.CHAR(2), nullable=True),
        # --- client identity: ADR-0006, computed from M4 --------------------
        sa.Column("visitor_id", pg.BYTEA(), nullable=True),
        sa.Column("session_fp", pg.BYTEA(), nullable=True),
        sa.Column("fingerprint_id", pg.BYTEA(), nullable=True),
        # --- device and browser: F3.AC2, F3.AC3 ----------------------------
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ua_family", sa.Text(), nullable=True),
        sa.Column("ua_version", sa.Text(), nullable=True),
        sa.Column("os_family", sa.Text(), nullable=True),
        sa.Column("os_version", sa.Text(), nullable=True),
        sa.Column("device_class", _enum("device_class"), nullable=False, server_default="unknown"),
        sa.Column("is_inapp_webview", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("webview_host", sa.Text(), nullable=True),
        sa.Column("screen_w", sa.Integer(), nullable=True),
        sa.Column("screen_h", sa.Integer(), nullable=True),
        sa.Column("viewport_w", sa.Integer(), nullable=True),
        sa.Column("viewport_h", sa.Integer(), nullable=True),
        sa.Column("dpr", sa.Numeric(4, 2), nullable=True),
        sa.Column("color_depth", sa.Integer(), nullable=True),
        sa.Column("touch_points", sa.Integer(), nullable=True),
        sa.Column("cpu_cores", sa.Integer(), nullable=True),
        sa.Column("device_memory_gb", sa.Numeric(4, 1), nullable=True),
        sa.Column("gpu_vendor", sa.Text(), nullable=True),
        sa.Column("gpu_renderer", sa.Text(), nullable=True),
        sa.Column("languages", pg.ARRAY(sa.Text()), nullable=True),
        sa.Column("tz_iana", sa.Text(), nullable=True),
        sa.Column("tz_offset_min", sa.Integer(), nullable=True),
        sa.Column("canvas_hash", pg.BYTEA(), nullable=True),
        sa.Column("audio_hash", pg.BYTEA(), nullable=True),
        sa.Column("font_hash", pg.BYTEA(), nullable=True),
        sa.Column("webgl_hash", pg.BYTEA(), nullable=True),
        # --- classification: F5 --------------------------------------------
        sa.Column(
            "classification", _enum("classification"), nullable=False, server_default="unknown"
        ),
        sa.Column("bot_score", sa.SmallInteger(), nullable=True),
        sa.Column("spoof_score", sa.SmallInteger(), nullable=True),
        sa.Column("agreement_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("conflict_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("honeypot_tripped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("header_order_hash", pg.BYTEA(), nullable=True),
        sa.Column("http_version", sa.Text(), nullable=True),
        sa.Column("tls_version", sa.Text(), nullable=True),
        sa.Column("classifier_version", sa.Text(), nullable=True),
        sa.Column("signals", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        # The full header SET (F3.AC1). Not in the Gate-3 data model, which listed
        # only the order hash; added so the AC is actually met. IP-bearing and
        # credential headers are stripped before storage -- see capture/signals.py.
        sa.Column("request_headers", pg.JSONB(), nullable=True),
        # --- location: F4 --------------------------------------------------
        sa.Column(
            "consent_state", _enum("consent_state"), nullable=False, server_default="not_asked"
        ),
        sa.Column("gps_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("gps_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("gps_accuracy_m", sa.Numeric(8, 1), nullable=True),
        sa.Column("resolved_address", sa.Text(), nullable=True),
        sa.Column("strict_country_code", sa.CHAR(2), nullable=True),
        sa.Column("strict_admin1", sa.Text(), nullable=True),
        sa.Column("strict_admin2", sa.Text(), nullable=True),
        sa.Column("strict_city", sa.Text(), nullable=True),
        sa.Column("strict_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("strict_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("advisory_country_code", sa.CHAR(2), nullable=True),
        sa.Column("advisory_admin1", sa.Text(), nullable=True),
        sa.Column("advisory_admin2", sa.Text(), nullable=True),
        sa.Column("advisory_city", sa.Text(), nullable=True),
        sa.Column("advisory_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("advisory_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("confidence_country", sa.Numeric(4, 3), nullable=True),
        sa.Column("confidence_admin1", sa.Numeric(4, 3), nullable=True),
        sa.Column("confidence_admin2", sa.Numeric(4, 3), nullable=True),
        sa.Column("confidence_city", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "abstain_reason", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("geo_source_primary", _enum("inference_source"), nullable=True),
        sa.Column("inference_version", sa.Text(), nullable=True),
        # geopoint is added below with DDL -- see the note there.
        # --- geofence: F6 --------------------------------------------------
        sa.Column(
            "matched_geofence_ids",
            pg.ARRAY(pg.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column(
            "geofence_state",
            _enum("geofence_state"),
            nullable=False,
            server_default="undetermined",
        ),
        # --- referral ------------------------------------------------------
        sa.Column("referer", sa.Text(), nullable=True),
        sa.Column("utm", pg.JSONB(), nullable=True),
        # --- constraints ---------------------------------------------------
        # RESTRICT: a link with visits cannot be deleted, only archived (F1.AC10).
        sa.ForeignKeyConstraint(
            ["link_id"], ["links.id"], ondelete="RESTRICT", name="fk_visits_link"
        ),
        # F4.AC3, OOS4: no coordinates and no street address without consent.
        # A CHECK, not a convention, because this is the property the whole privacy
        # posture rests on.
        sa.CheckConstraint(
            "consent_state = 'granted' OR ("
            "gps_lat IS NULL AND gps_lng IS NULL "
            "AND gps_accuracy_m IS NULL AND resolved_address IS NULL)",
            name="gps_requires_consent",
        ),
        # 'server' means "awaiting enrichment or the sweeper", and nothing else. The
        # sweeper's partial index and its UPDATE both key on stage='server'; this
        # makes it impossible for the two to disagree about what is pending.
        sa.CheckConstraint(
            "(stage = 'server') = (finalized_at IS NULL)", name="server_stage_is_unfinalized"
        ),
        # A purge nulls both together; a key version pointing at nothing is a bug.
        sa.CheckConstraint(
            "(ip_enc IS NULL) = (ip_key_version IS NULL)", name="ip_enc_has_key_version"
        ),
        sa.CheckConstraint("bot_score BETWEEN 0 AND 100", name="bot_score_range"),
        sa.CheckConstraint("spoof_score BETWEEN 0 AND 100", name="spoof_score_range"),
    )

    # geography(Point, 4326) has no SQLAlchemy core type without geoalchemy2, which
    # M2 does not need (ES5) -- geofencing is M6, and M6 brings it. Plain DDL instead.
    op.execute("ALTER TABLE visits ADD COLUMN geopoint geography(Point, 4326)")
    # F6.AC6: an abstaining inference is never silently treated as "outside".
    op.execute(
        "ALTER TABLE visits ADD CONSTRAINT ck_visits_no_geopoint_is_undetermined "
        "CHECK (geopoint IS NOT NULL OR geofence_state = 'undetermined')"
    )

    # --- indexes: docs/DATA_MODEL.md section 5.2 -----------------------------
    op.create_index("ix_visits_occurred", "visits", [sa.text("occurred_at DESC")])
    op.create_index("ix_visits_link_occurred", "visits", ["link_id", sa.text("occurred_at DESC")])
    op.create_index(
        "ix_visits_visitor_occurred", "visits", ["visitor_id", sa.text("occurred_at DESC")]
    )
    op.create_index(
        "ix_visits_classification_occurred",
        "visits",
        ["classification", sa.text("occurred_at DESC")],
    )
    op.create_index("ix_visits_ip_prefix", "visits", ["ip_prefix"])
    op.create_index(
        "ix_visits_fingerprint_occurred",
        "visits",
        ["fingerprint_id", sa.text("occurred_at DESC")],
    )
    op.execute("CREATE INDEX ix_visits_geopoint ON visits USING gist (geopoint)")
    op.create_index(
        "ix_visits_strict_geo", "visits", ["strict_country_code", "strict_admin1"]
    )
    op.create_index("ix_visits_signals", "visits", ["signals"], postgresql_using="gin")
    # The 90 s sweeper (F2.AC7). Stays tiny: only visits still awaiting a decision.
    op.create_index(
        "ix_visits_sweeper",
        "visits",
        ["occurred_at"],
        postgresql_where=sa.text("stage = 'server'"),
    )
    # The IP purge job (F12.AC2). Only rows still holding ciphertext.
    op.create_index(
        "ix_visits_ip_purge",
        "visits",
        ["ip_purge_after"],
        postgresql_where=sa.text("ip_enc IS NOT NULL"),
    )
    # Stuck-visit detection. Distinct from the sweeper index in principle -- the
    # CHECK above makes the two predicates equivalent today, but they answer
    # different questions and the next stage added must not merge them silently.
    op.create_index(
        "ix_visits_unfinalized",
        "visits",
        ["occurred_at"],
        postgresql_where=sa.text("finalized_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("visits")
    op.drop_index("ix_links_live_slug", table_name="links")
    op.drop_index("uq_links_one_default", table_name="links")
    op.drop_table("links")
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE IF EXISTS {name}")
