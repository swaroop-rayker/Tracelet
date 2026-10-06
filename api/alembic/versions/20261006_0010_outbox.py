"""outbox and app_settings: visit alerts, atomic with the visit

Revision ID: 0010
Revises: 0009
Created: 2026-10-06

M6. ADR-0009, F7, DATA_MODEL sections 7.1 and 8.6.

1. ``outbox``. An alert is inserted in the same savepoint that writes the visit's
   location, classification and geofence state (ADR-0015, F7.AC5): a rolled-back visit
   cannot emit one, and a Telegram outage cannot lose one. ``UNIQUE(dedup_key)`` is the
   once-per-local-day rule (F7.AC2, SPEC section 11 row 17): two concurrent visits from
   one visitor race to one row, and the engine, not the application, decides the winner.
   The CHECKs keep the worker's states honest: a lock exists exactly while in flight, a
   completion time exactly when finished, and a silent alert is never queued at all.

2. ``app_settings``. Runtime configuration without secrets (F12.AC3). M6 needs it for
   quiet hours (F7.AC9) before M7 builds ``retention_policy``, where DATA_MODEL first
   placed them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS: dict[str, tuple[str, ...]] = {
    # password_reset and test are unused (sent synchronously: ADR-0009 amendment, F7.AC8);
    # kept so the type matches DATA_MODEL section 2 and needs no later ALTER.
    "outbox_kind": (
        "telegram.visit_alert",
        "telegram.password_reset",
        "telegram.health_alert",
        "telegram.test",
    ),
    "outbox_status": ("pending", "in_flight", "done", "failed", "dead"),
}


def _enum(name: str) -> pg.ENUM:
    return pg.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        pg.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("kind", _enum("outbox_kind"), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=True),
        sa.Column(
            "priority",
            pg.ENUM(name="notify_priority", create_type=False),
            nullable=False,
            server_default="normal",
        ),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("status", _enum("outbox_status"), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # F7.AC2: the dedup rule itself. NULL keys (non-visit kinds) never collide.
        sa.UniqueConstraint("dedup_key", name="uq_outbox_dedup_key"),
        sa.CheckConstraint("priority <> 'silent'", name="never_silent"),
        sa.CheckConstraint("(status = 'in_flight') = (locked_at IS NOT NULL)", name="lock_iff_in_flight"),
        sa.CheckConstraint(
            "(status IN ('done', 'dead')) = (completed_at IS NOT NULL)", name="completed_iff_final"
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 20", name="max_attempts_range"),
    )
    # The worker's queue: only rows that may still be sent.
    op.create_index(
        "ix_outbox_due",
        "outbox",
        ["next_attempt_at"],
        postgresql_where=sa.text("status IN ('pending', 'failed')"),
    )
    # The health panel and the header bell: dead letters only (F10.AC13).
    op.create_index(
        "ix_outbox_dead", "outbox", ["status"], postgresql_where=sa.text("status = 'dead'")
    )
    op.create_index("ix_outbox_created", "outbox", [sa.text("created_at DESC")])

    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", pg.JSONB(), nullable=False),
        sa.Column("updated_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["admins.id"], ondelete="SET NULL", name="fk_app_settings_updated_by"
        ),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("ix_outbox_created", table_name="outbox")
    op.drop_index("ix_outbox_dead", table_name="outbox")
    op.drop_index("ix_outbox_due", table_name="outbox")
    op.drop_table("outbox")
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE IF EXISTS {name}")
