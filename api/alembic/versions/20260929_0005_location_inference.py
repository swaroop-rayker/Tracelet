"""location inference: candidates, reference data, versioned settings

Revision ID: 0005
Revises: 0004
Created: 2026-09-29

M3. Implements the storage half of F4 (docs/DATA_MODEL.md sections 5.4, 8.1-8.4, 9.3)
and the work queue of ADR-0015.

Three things are enforced by the **engine** rather than trusted to the application:

  1. A candidate is either accepted, or rejected **with a reason** -- never silently
     dropped (F4.AC6, F4.AC11). The derivation trail is only an audit trail if every
     loser says why it lost.
  2. At most one `inference_settings` row is active, and rows are never deleted by the
     application, so any past `inference_version` stays interpretable (F4.AC14).
  3. At most one installed row per geo database, so an update cannot leave two
     versions both claiming to serve (F10.AC4).

Rate-limited visits never enter the inference queue: they carry no client columns and
no inference by design (DATA_MODEL section 5.3, invariant 8).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS: dict[str, tuple[str, ...]] = {
    "geo_level": ("country", "admin1", "admin2", "city", "point"),
    "geo_db_status": ("installed", "downloading", "failed", "stale"),
}

SUPPRESSED_REASONS = (
    "registry_artifact",
    "mobile_asn",
    "hosting_asn",
    "tz_mismatch",
    "below_threshold",
    "outvoted",
)


def _enum(name: str) -> pg.ENUM:
    if name in ENUMS:
        return pg.ENUM(*ENUMS[name], name=name, create_type=False)
    # Created by migration 0004.
    return pg.ENUM(name=name, create_type=False)


def _now() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        pg.ENUM(*values, name=name).create(bind, checkfirst=True)

    # === visits: the ADR-0015 work queue =====================================
    op.add_column("visits", sa.Column("inferred_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_visits_infer_queue",
        "visits",
        ["finalized_at"],
        postgresql_where=sa.text(
            "finalized_at IS NOT NULL AND inferred_at IS NULL AND stage <> 'rate_limited'"
        ),
    )

    # === visit_candidates -- the derivation trail (section 5.4) ==============
    op.create_table(
        "visit_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("visit_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("source", _enum("inference_source"), nullable=False),
        sa.Column("level", _enum("geo_level"), nullable=False),
        sa.Column("country_code", sa.CHAR(2), nullable=True),
        sa.Column("admin1", sa.Text(), nullable=True),
        sa.Column("admin2", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("raw_confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("weight", sa.Numeric(6, 4), nullable=False),
        sa.Column("effective_weight", sa.Numeric(6, 4), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("suppressed_reason", sa.Text(), nullable=True),
        sa.Column(
            "evidence", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "produced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["visit_id"], ["visits.id"], ondelete="CASCADE", name="fk_visit_candidates_visit"
        ),
        # Invariant 1: accepted, or rejected with a reason. Never both, never neither.
        sa.CheckConstraint(
            "accepted = (suppressed_reason IS NULL)", name="rejection_has_reason"
        ),
        sa.CheckConstraint(
            "suppressed_reason IS NULL OR suppressed_reason IN ("
            + ", ".join(f"'{r}'" for r in SUPPRESSED_REASONS)
            + ")",
            name="known_suppressed_reason",
        ),
        sa.CheckConstraint("raw_confidence BETWEEN 0 AND 1", name="raw_confidence_range"),
        sa.CheckConstraint("weight >= 0 AND effective_weight >= 0", name="weights_non_negative"),
        sa.CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
    )
    op.create_index("ix_visit_candidates_visit", "visit_candidates", ["visit_id"])
    op.create_index(
        "ix_visit_candidates_source", "visit_candidates", ["source", sa.text("produced_at DESC")]
    )
    op.create_index(
        "ix_visit_candidates_accepted",
        "visit_candidates",
        ["visit_id"],
        postgresql_where=sa.text("accepted"),
    )

    # === asn_profiles -- the B1 fix, precomputed (section 8.1) ===============
    op.create_table(
        "asn_profiles",
        sa.Column("asn", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("org", sa.Text(), nullable=True),
        sa.Column("asn_type", _enum("asn_type"), nullable=False, server_default="unknown"),
        sa.Column("modal_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("modal_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("modal_city", sa.Text(), nullable=True),
        sa.Column("modal_admin1", sa.Text(), nullable=True),
        sa.Column("modal_share", sa.Numeric(4, 3), nullable=True),
        sa.Column(
            "is_registry_artifact_source", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("is_mobile", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_hosting", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_cgnat", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "source_db_versions",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "modal_share IS NULL OR modal_share BETWEEN 0 AND 1", name="modal_share_range"
        ),
    )

    # === rdns_city_codes -- the lexicon (section 8.2) ========================
    op.create_table(
        "rdns_city_codes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("admin1", sa.Text(), nullable=True),
        sa.Column("country_code", sa.CHAR(2), nullable=False),
        sa.Column("lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("isp_hint", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("lexicon_version", sa.Integer(), nullable=False),
        _now(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("pattern", "lexicon_version", name="uq_rdns_city_codes_pattern"),
        sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        # A city code must name at least a state; a code that locates nothing is noise.
        sa.CheckConstraint("city IS NOT NULL OR admin1 IS NOT NULL", name="locates_something"),
    )
    op.create_index("ix_rdns_city_codes_active", "rdns_city_codes", ["is_active", "country_code"])

    # === geo_databases (section 8.3) =========================================
    op.create_table(
        "geo_databases",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("status", _enum("geo_db_status"), nullable=False),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "staleness_threshold_days", sa.Integer(), nullable=False, server_default="45"
        ),
        _now(),
        sa.CheckConstraint(
            "status <> 'installed' OR (file_path IS NOT NULL AND sha256 IS NOT NULL)",
            name="installed_has_file",
        ),
    )
    # Invariant 3: at most one installed version per database.
    op.create_index(
        "uq_geo_databases_one_installed",
        "geo_databases",
        ["name"],
        unique=True,
        postgresql_where=sa.text("status = 'installed'"),
    )

    # === inference_settings (section 8.4) ====================================
    op.create_table(
        "inference_settings",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("settings", pg.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        _now(),
        sa.UniqueConstraint("version", name="uq_inference_settings_version"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["admins.id"], ondelete="SET NULL", name="fk_inference_settings_by"
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )
    # Invariant 2, first half: exactly one active version (at most one here; the
    # application seeds version 1 on first use, so "at least one" holds from then on).
    op.create_index(
        "uq_inference_settings_one_active",
        "inference_settings",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    # === geo_cache (section 9.3) ============================================
    op.create_table(
        "geo_cache",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("ip_prefix", pg.INET(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("source", "ip_prefix", name="pk_geo_cache"),
    )
    op.create_index("ix_geo_cache_expires", "geo_cache", ["expires_at"])

    # Invariant 2, second half: the application may add versions and move the active
    # flag, never rewrite or delete one. A column-level REVOKE would be a no-op against
    # the table-level UPDATE the schema default privileges grant, so the table grant is
    # revoked and only `is_active` is granted back -- the same shape as audit_log (0002).
    op.execute("REVOKE UPDATE, DELETE ON TABLE inference_settings FROM tracelet_app")
    op.execute("GRANT UPDATE (is_active) ON TABLE inference_settings TO tracelet_app")


def downgrade() -> None:
    op.drop_table("geo_cache")
    op.drop_index("uq_inference_settings_one_active", table_name="inference_settings")
    op.drop_table("inference_settings")
    op.drop_index("uq_geo_databases_one_installed", table_name="geo_databases")
    op.drop_table("geo_databases")
    op.drop_index("ix_rdns_city_codes_active", table_name="rdns_city_codes")
    op.drop_table("rdns_city_codes")
    op.drop_table("asn_profiles")
    op.drop_table("visit_candidates")
    op.drop_index("ix_visits_infer_queue", table_name="visits")
    op.drop_column("visits", "inferred_at")
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE IF EXISTS {name}")
