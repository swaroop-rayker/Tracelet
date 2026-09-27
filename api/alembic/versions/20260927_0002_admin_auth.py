"""admin auth: admins, sessions, recovery codes, enrollment tokens, audit log

Revision ID: 0002
Revises: 0001
Created: 2026-09-27

M1. Implements F8 (admin authentication and account security) plus the
append-only audit log that every later milestone writes to.

Three things here are enforced by the **engine**, not by application code, because
each of them can be defeated by two concurrent requests that both pass a Python
check (docs/DATA_MODEL.md §3.1):

  1. At least one active owner must always exist (F8.AC13) -- a DEFERRABLE
     constraint trigger, because two simultaneous demotions would each see the
     other's owner still present.
  2. An account cannot be active without TOTP enrolled (F8.AC4) -- a CHECK.
  3. audit_log is append-only (NFR5.AC5) -- a table-level REVOKE, so a
     SQL-injection foothold in the application path cannot erase its own traces.

rate_limit_buckets arrives here rather than in M7 as originally planned: login
rate limiting (F8.AC9) needs shared state across both Uvicorn workers now, and the
same table serves the full L2 limiter in M2 (ADR-0010). Noted in
docs/DATA_MODEL.md and docs/MILESTONES.md.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    admin_role = pg.ENUM("owner", "analyst", name="admin_role", create_type=False)
    admin_status = pg.ENUM(
        "pending_enrollment", "active", "disabled", name="admin_status", create_type=False
    )
    admin_role.create(op.get_bind(), checkfirst=True)
    admin_status.create(op.get_bind(), checkfirst=True)

    # === admins ============================================================
    op.create_table(
        "admins",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        # The admin's OWN address, used as the login identifier. Not visitor data.
        sa.Column("email", pg.CITEXT(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", admin_role, nullable=False),
        sa.Column("status", admin_status, nullable=False, server_default="pending_enrollment"),
        # NULL until enrollment completes. There is no default password anywhere in
        # this system, by design (F8.AC15).
        sa.Column("password_hash", sa.Text(), nullable=True),
        # Recorded so a future parameter change can rehash on next login rather
        # than forcing a reset.
        sa.Column(
            "password_params", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        # AES-256-GCM, same envelope as visits.ip_enc (ADR-0007).
        sa.Column("totp_secret_enc", pg.BYTEA(), nullable=True),
        sa.Column("totp_key_version", sa.SmallInteger(), nullable=True),
        sa.Column("totp_enrolled_at", sa.DateTime(timezone=True), nullable=True),
        # Highest accepted time step. A code cannot be replayed inside its own
        # window (F8.AC5).
        sa.Column("totp_last_counter", sa.BigInteger(), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("theme", sa.Text(), nullable=False, server_default="semi_dark"),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("email", name="uq_admins_email"),
        # F8.AC4: an account cannot reach the dashboard without a second factor.
        # A CHECK rather than a service-layer guard, because this is the property
        # the whole auth design rests on.
        sa.CheckConstraint(
            "status <> 'active' OR (password_hash IS NOT NULL AND totp_enrolled_at IS NOT NULL)",
            name="active_requires_password_and_totp",
        ),
        sa.CheckConstraint(
            "theme IN ('semi_dark', 'light', 'dark')", name="theme_known"
        ),
        # A chat id may only be used for recovery once verified (F8.AC7).
        sa.CheckConstraint(
            "telegram_chat_id IS NULL OR telegram_verified_at IS NOT NULL "
            "OR status = 'pending_enrollment'",
            name="telegram_verified_before_use",
        ),
    )
    op.create_index(
        "ix_admins_active", "admins", ["status"], postgresql_where=sa.text("status = 'active'")
    )

    # --- F8.AC13: the last active owner cannot be removed ------------------
    #
    # Note the precise formulation. The naive version -- "an active owner must
    # always exist" -- is FALSE during bootstrap: the first owner is created
    # pending_enrollment, and every UPDATE on the way to activating them (setting a
    # password, enrolling TOTP) happens while no active owner exists. A global
    # assertion rejects the whole enrolment at COMMIT.
    #
    # What must actually hold is narrower: an operation may not TAKE AWAY the last
    # active owner. So the check runs only when the row being changed was an active
    # owner and is about to stop being one. That also means it costs nothing on
    # ordinary updates -- a failed-login counter bump on an owner does not trigger a
    # count query.
    #
    # DEFERRABLE INITIALLY DEFERRED so it evaluates at COMMIT: a transaction may
    # legitimately pass through a zero-owner state (promote B, then demote A), and an
    # immediate check would reject that. Deferring also closes the race an
    # application-level count cannot -- two concurrent demotions each see the other
    # owner still present and both proceed.
    op.execute(
        """
        CREATE FUNCTION assert_owner_remains() RETURNS TRIGGER
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM admins WHERE role = 'owner' AND status = 'active'
            ) THEN
                RAISE EXCEPTION
                    'cannot remove the last active owner'
                    USING ERRCODE = 'integrity_constraint_violation',
                          HINT = 'promote another admin to owner first';
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_admins_owner_remains_on_update
        AFTER UPDATE ON admins
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        WHEN (
            OLD.role = 'owner' AND OLD.status = 'active'
            AND (NEW.role <> 'owner' OR NEW.status <> 'active')
        )
        EXECUTE FUNCTION assert_owner_remains();
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_admins_owner_remains_on_delete
        AFTER DELETE ON admins
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        WHEN (OLD.role = 'owner' AND OLD.status = 'active')
        EXECUTE FUNCTION assert_owner_remains();
        """
    )

    # === admin_recovery_codes ==============================================
    op.create_table(
        "admin_recovery_codes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("admin_id", pg.UUID(as_uuid=True), nullable=False),
        # Argon2id, same as a password: a recovery code IS a credential.
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admins.id"], ondelete="CASCADE", name="fk_recovery_admin"
        ),
    )
    op.create_index("ix_recovery_admin", "admin_recovery_codes", ["admin_id"])
    # Supports the "fewer than 3 remaining" warning without scanning used codes.
    op.create_index(
        "ix_recovery_unused",
        "admin_recovery_codes",
        ["admin_id"],
        postgresql_where=sa.text("used_at IS NULL"),
    )

    # === admin_enrollment_tokens ===========================================
    op.create_table(
        "admin_enrollment_tokens",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("admin_id", pg.UUID(as_uuid=True), nullable=False),
        # SHA-256 of a 256-bit token. High entropy, so no pepper is needed.
        sa.Column("token_hash", pg.BYTEA(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admins.id"], ondelete="CASCADE", name="fk_enrollment_admin"
        ),
        sa.UniqueConstraint("token_hash", name="uq_enrollment_token_hash"),
    )
    op.create_index("ix_enrollment_admin", "admin_enrollment_tokens", ["admin_id"])

    # === password_reset_tokens =============================================
    # Separate from enrollment: different lifetime, different delivery channel
    # (Telegram, F8.AC7), and conflating them would let an enrollment link reset an
    # established account's password.
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("admin_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", pg.BYTEA(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_ip_prefix", pg.INET(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admins.id"], ondelete="CASCADE", name="fk_reset_admin"
        ),
        sa.UniqueConstraint("token_hash", name="uq_reset_token_hash"),
    )
    op.create_index("ix_reset_admin", "password_reset_tokens", ["admin_id"])

    # === sessions ==========================================================
    op.create_table(
        "sessions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        # SHA-256 of the opaque token. The token itself is NEVER stored, so a
        # database leak yields nothing a client can present (ADR-0008).
        sa.Column("token_hash", pg.BYTEA(), nullable=False),
        sa.Column("admin_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("csrf_secret", pg.BYTEA(), nullable=False),
        # Binding. A stolen cookie replayed from another network fails.
        sa.Column("ip_prefix", pg.INET(), nullable=True),
        sa.Column("ua_hash", pg.BYTEA(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admins.id"], ondelete="CASCADE", name="fk_sessions_admin"
        ),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_index("ix_sessions_admin_created", "sessions", ["admin_id", "created_at"])
    op.create_index(
        "ix_sessions_live",
        "sessions",
        ["expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # === audit_log =========================================================
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # NULL actor means the system acted (a scheduled job, the CLI).
        sa.Column("actor_admin_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_ip_prefix", pg.INET(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("detail", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # ON DELETE SET NULL, not CASCADE: deleting an admin must not delete the
        # record of what they did. That would defeat the point of an audit log.
        sa.ForeignKeyConstraint(
            ["actor_admin_id"], ["admins.id"], ondelete="SET NULL", name="fk_audit_actor"
        ),
    )
    op.create_index("ix_audit_occurred", "audit_log", [sa.text("occurred_at DESC")])
    op.create_index(
        "ix_audit_actor_occurred", "audit_log", ["actor_admin_id", sa.text("occurred_at DESC")]
    )
    op.create_index("ix_audit_action_occurred", "audit_log", ["action", sa.text("occurred_at DESC")])
    op.create_index("ix_audit_detail", "audit_log", ["detail"], postgresql_using="gin")

    # --- NFR5.AC5: append-only, enforced by the engine ----------------------
    #
    # The schema default privileges granted UPDATE and DELETE along with everything
    # else; both are revoked here for the application role. Retention purging runs
    # as tracelet_maint, which keeps them.
    #
    # This is what makes the audit log trustworthy: a SQL-injection foothold in the
    # application path cannot erase the evidence of itself.
    op.execute("REVOKE UPDATE, DELETE ON TABLE audit_log FROM tracelet_app")
    op.execute("REVOKE UPDATE ON TABLE audit_log FROM tracelet_maint")

    # === rate_limit_buckets ================================================
    # GCRA state (ADR-0010). Lives in PostgreSQL rather than process memory
    # because two Uvicorn workers must share one allowance -- per-process state
    # would silently grant double every limit, which is worse than no limit
    # because it looks like it works (F11.AC8).
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key", sa.Text(), primary_key=True),
        # Theoretical arrival time: the whole of GCRA's state, one timestamp.
        sa.Column("tat", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_rate_limit_updated", "rate_limit_buckets", ["updated_at"])


def downgrade() -> None:
    op.drop_table("rate_limit_buckets")
    op.drop_table("audit_log")
    op.drop_table("sessions")
    op.drop_table("password_reset_tokens")
    op.drop_table("admin_enrollment_tokens")
    op.drop_table("admin_recovery_codes")
    op.execute("DROP TRIGGER IF EXISTS trg_admins_owner_remains_on_update ON admins")
    op.execute("DROP TRIGGER IF EXISTS trg_admins_owner_remains_on_delete ON admins")
    op.drop_table("admins")
    op.execute("DROP FUNCTION IF EXISTS assert_owner_remains()")
    op.execute("DROP TYPE IF EXISTS admin_status")
    op.execute("DROP TYPE IF EXISTS admin_role")
