"""ORM models for admin authentication.

Mirrors migration 0002. If these and the migration disagree, the migration is
authoritative and this file is wrong -- the database is the thing that actually
enforces the invariants (docs/DATA_MODEL.md §3.1).
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from ipaddress import IPv4Interface, IPv6Interface

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tracelet.db.base import Base


class AdminRole(enum.StrEnum):
    """Two roles only.

    ``owner`` performs every destructive and configuration action, and each one
    writes an audit row (CLAUDE.md invariant 9). ``analyst`` is strictly read-only.
    """

    OWNER = "owner"
    ANALYST = "analyst"


class AdminStatus(enum.StrEnum):
    PENDING_ENROLLMENT = "pending_enrollment"
    ACTIVE = "active"
    DISABLED = "disabled"


_role = pg.ENUM(
    AdminRole, name="admin_role", create_type=False, values_callable=lambda e: [m.value for m in e]
)
_status = pg.ENUM(
    AdminStatus,
    name="admin_status",
    create_type=False,
    values_callable=lambda e: [m.value for m in e],
)


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(pg.CITEXT, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[AdminRole] = mapped_column(_role, nullable=False)
    status: Mapped[AdminStatus] = mapped_column(
        _status, nullable=False, default=AdminStatus.PENDING_ENROLLMENT
    )

    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_params: Mapped[dict[str, object]] = mapped_column(
        pg.JSONB, nullable=False, default=dict
    )
    password_changed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    totp_key_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    totp_enrolled_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_verified_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="Asia/Kolkata")
    theme: Mapped[str] = mapped_column(Text, nullable=False, default="semi_dark")

    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    recovery_codes: Mapped[list[RecoveryCode]] = relationship(
        back_populates="admin", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def is_owner(self) -> bool:
        return self.role is AdminRole.OWNER

    @property
    def is_active(self) -> bool:
        return self.status is AdminStatus.ACTIVE

    @property
    def totp_enrolled(self) -> bool:
        return self.totp_enrolled_at is not None and self.totp_secret_enc is not None

    def __repr__(self) -> str:
        # No email, no hash, no secret. A model in a log line must not leak either
        # the login identifier or anything credential-shaped.
        return f"Admin(id={self.id}, role={self.role.value}, status={self.status.value})"


class RecoveryCode(Base):
    __tablename__ = "admin_recovery_codes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    admin: Mapped[Admin] = relationship(back_populates="recovery_codes")

    def __repr__(self) -> str:
        return f"RecoveryCode(id={self.id}, used={self.used_at is not None})"


class _SingleUseToken(Base):
    """Shared shape for the two single-use token tables."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EnrollmentToken(_SingleUseToken):
    """Activates a newly-created admin. There is no default password (F8.AC15)."""

    __tablename__ = "admin_enrollment_tokens"

    admin_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True), nullable=True)


class PasswordResetToken(_SingleUseToken):
    """Delivered over Telegram (F8.AC7).

    Deliberately a separate table from enrollment: different lifetime, different
    delivery channel, and sharing one table would let an enrollment link reset an
    established account's password.
    """

    __tablename__ = "password_reset_tokens"

    admin_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="CASCADE"), nullable=False
    )
    requested_ip_prefix: Mapped[IPv4Interface | IPv6Interface | None] = mapped_column(
        pg.INET, nullable=True
    )


class Session(Base):
    """An opaque server-side session (ADR-0008).

    Not a JWT. A JWT cannot be revoked before expiry without a server-side
    denylist, at which point you have session state anyway plus key rotation.
    Statelessness buys horizontal scaling, which is worth nothing for two admins on
    one VM.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="CASCADE"), nullable=False
    )
    csrf_secret: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    # Annotated as the driver actually returns it. Declaring this as `str` is what
    # hid E12 from mypy: SQLAlchemy hands back an ipaddress object for INET, so any
    # direct comparison against a string silently fails. Always compare via str().
    ip_prefix: Mapped[IPv4Interface | IPv6Interface | None] = mapped_column(pg.INET, nullable=True)
    ua_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def is_live(self, now: dt.datetime) -> bool:
        """Valid only while unrevoked AND inside both expiry clocks."""
        return self.revoked_at is None and now < self.expires_at and now < self.idle_expires_at

    def __repr__(self) -> str:
        return f"Session(id={self.id}, admin_id={self.admin_id}, revoked={self.revoked_at is not None})"


class AuditLog(Base):
    """Append-only. The application role has no UPDATE or DELETE (NFR5.AC5).

    ``actor_admin_id`` uses ON DELETE SET NULL rather than CASCADE: deleting an
    admin must never delete the record of what they did.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL"), nullable=True
    )
    actor_ip_prefix: Mapped[IPv4Interface | IPv6Interface | None] = mapped_column(
        pg.INET, nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict[str, object]] = mapped_column(pg.JSONB, nullable=False, default=dict)


class RateLimitBucket(Base):
    """GCRA state: one theoretical-arrival-time per key (ADR-0010).

    The only table deliberately excluded from the must-never-be-lost set -- it is
    ephemeral and rebuildable.
    """

    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    tat: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "Admin",
    "AdminRole",
    "AdminStatus",
    "AuditLog",
    "EnrollmentToken",
    "PasswordResetToken",
    "RateLimitBucket",
    "RecoveryCode",
    "Session",
]
