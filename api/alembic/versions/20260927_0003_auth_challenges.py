"""auth_challenges: short-lived multi-step auth state, shared across workers

Revision ID: 0003
Revises: 0002
Created: 2026-09-27

Fixes a real defect introduced in M1 (docs/ERRORS.md E10).

Three short-lived tokens were held in a module-level dict: the MFA challenge between
password and TOTP, the confirm token issued at enrolment, and the Telegram chat
verification code. With ``TRACELET_WEB_CONCURRENCY=2`` there are **two Gunicorn
workers with separate memory**, so whether a token was found depended on which worker
received the follow-up request. Login succeeded roughly half the time.

The original code carried a comment arguing this was acceptable because "the admin
simply retries". That was wrong, and worth recording: a rationalised tradeoff in a
comment is how a bug survives review.

One table with a ``kind`` discriminator rather than three: all three have identical
shape and lifecycle (single-use, short TTL, bound to an admin), and the enrollment and
reset tokens stay separate because those have genuinely different lifetimes and
delivery paths.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    challenge_kind = pg.ENUM(
        "mfa",
        "totp_confirm",
        "chat_verify",
        name="auth_challenge_kind",
        create_type=False,
    )
    challenge_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "auth_challenges",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("kind", challenge_kind, nullable=False),
        sa.Column("admin_id", pg.UUID(as_uuid=True), nullable=False),
        # SHA-256 of a 256-bit token. High entropy, so no pepper is needed. For
        # chat_verify the "token" is the admin id, since the 6-digit code is the
        # secret and lives in payload.
        sa.Column("token_hash", pg.BYTEA(), nullable=False),
        # Kind-specific extras: the proposed chat_id and the numeric code for
        # chat_verify. Never a credential that must survive a leak.
        sa.Column("payload", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admins.id"], ondelete="CASCADE", name="fk_challenge_admin"
        ),
        sa.UniqueConstraint("kind", "token_hash", name="uq_challenge_kind_token"),
    )
    op.create_index("ix_challenge_admin_kind", "auth_challenges", ["admin_id", "kind"])
    # Supports the reaper without scanning consumed rows.
    op.create_index(
        "ix_challenge_live",
        "auth_challenges",
        ["expires_at"],
        postgresql_where=sa.text("used_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("auth_challenges")
    op.execute("DROP TYPE IF EXISTS auth_challenge_kind")
