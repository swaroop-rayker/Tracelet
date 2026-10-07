"""retention_policy, backups and restore_checks: the data lifecycle

Revision ID: 0012
Revises: 0011
Created: 2026-10-07

M7. ADR-0014, ADR-0022, F10.AC11-AC12, F12.AC7-AC10, DATA_MODEL sections 8.5 and 8.8.

1. ``retention_policy``. One row (``CHECK (id = 1)``), editable from System Health. It is
   written the first time it is read, from the ``TRACELET_RETENTION_*`` defaults, so the
   migration does not have to know them. The encrypted IP cannot outlive the visit that
   carries it, so ``ip_days <= visit_days``.

2. ``backups``. One row per attempt; never deleted, so the history stays when a file is
   rotated away (``pruned``). An ``ok`` row has its file, size, checksum and the counts taken
   in the dump's own snapshot.

3. ``restore_checks``. One row per restore-verification of a backup. At most one of each
   is running at a time, so a second trigger is refused instead of run twice.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS: dict[str, tuple[str, ...]] = {
    "backup_kind": ("scheduled", "manual"),
    "backup_status": ("running", "ok", "failed", "pruned"),
    "restore_status": ("running", "passed", "failed"),
}


def _enum(name: str) -> pg.ENUM:
    return pg.ENUM(*ENUMS[name], name=name, create_type=False)


def _admin_fk(column: str, table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["admins.id"], ondelete="SET NULL", name=f"fk_{table}_{column}"
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        pg.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "retention_policy",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("visit_days", sa.Integer(), nullable=False),
        sa.Column("ip_days", sa.Integer(), nullable=False),
        sa.Column("audit_days", sa.Integer(), nullable=False),
        # Rollups are kept forever (ADR-0014: retention costs detail, not history). A
        # column, not a constant, because DATA_MODEL section 8.5 names it; it is not
        # editable, and the CHECK says so.
        sa.Column("rollup_forever", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.CheckConstraint("rollup_forever", name="rollups_kept"),
        # Rollups re-settle the last 7 days (analytics/rollup.py SETTLE_DAYS); a visit
        # purged inside that window would vanish from a day still being rebuilt.
        sa.CheckConstraint("visit_days BETWEEN 8 AND 3650", name="visit_days_range"),
        sa.CheckConstraint("ip_days BETWEEN 1 AND visit_days", name="ip_within_visit"),
        sa.CheckConstraint("audit_days BETWEEN 1 AND 3650", name="audit_days_range"),
        _admin_fk("updated_by", "retention_policy"),
    )

    op.create_table(
        "backups",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", _enum("backup_kind"), nullable=False),
        sa.Column("status", _enum("backup_status"), nullable=False, server_default="running"),
        sa.Column("file_name", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("row_counts", pg.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("last_downloaded_at", sa.DateTime(timezone=True), nullable=True),
        # Invariant 1: an ok (or pruned) backup has everything that describes its file.
        sa.CheckConstraint(
            "(status IN ('ok', 'pruned')) = (file_name IS NOT NULL AND size_bytes IS NOT NULL "
            "AND sha256 IS NOT NULL AND row_counts IS NOT NULL)",
            name="described_iff_ok",
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)", name="finished_iff_done"
        ),
        sa.CheckConstraint("sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        _admin_fk("requested_by", "backups"),
    )
    # Invariant 2: one backup at a time.
    op.create_index(
        "uq_backups_one_running",
        "backups",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index("ix_backups_started", "backups", [sa.text("started_at DESC")])

    op.create_table(
        "restore_checks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("backup_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", _enum("backup_kind"), nullable=False),
        sa.Column("status", _enum("restore_status"), nullable=False, server_default="running"),
        sa.Column("mismatches", pg.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", pg.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)", name="finished_iff_done"
        ),
        sa.ForeignKeyConstraint(
            ["backup_id"], ["backups.id"], ondelete="RESTRICT", name="fk_restore_checks_backup"
        ),
        _admin_fk("requested_by", "restore_checks"),
    )
    op.create_index(
        "uq_restore_checks_one_running",
        "restore_checks",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index("ix_restore_checks_started", "restore_checks", [sa.text("started_at DESC")])


def downgrade() -> None:
    op.drop_table("restore_checks")
    op.drop_table("backups")
    op.drop_table("retention_policy")
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE IF EXISTS {name}")
