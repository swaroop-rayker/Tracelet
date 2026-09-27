"""Short-lived multi-step authentication state.

Holds the three tokens that bridge two requests: the MFA challenge between password
and TOTP, the confirm token issued at enrolment, and the Telegram chat-verification
code.

**These live in the database, not in process memory**, and that is the whole point of
the module. An earlier version used a module-level dict; with two Gunicorn workers the
follow-up request landed on the wrong worker about half the time and login became a
coin flip (docs/ERRORS.md E10). Any state that must survive from one HTTP request to
the next is shared state, and on this deployment shared means PostgreSQL (ADR-0010
makes the same argument for rate-limit buckets).
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    LargeBinary,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tracelet.crypto.hashing import new_token, sha256_bytes
from tracelet.db.base import Base
from tracelet.db.dml import execute_rowcount

log = structlog.get_logger(__name__)


class ChallengeKind(enum.StrEnum):
    MFA = "mfa"
    TOTP_CONFIRM = "totp_confirm"
    CHAT_VERIFY = "chat_verify"


_kind = pg.ENUM(
    ChallengeKind,
    name="auth_challenge_kind",
    create_type=False,
    values_callable=lambda e: [m.value for m in e],
)


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[ChallengeKind] = mapped_column(_kind, nullable=False)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False, default=dict)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"AuthChallenge(kind={self.kind.value}, admin_id={self.admin_id})"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True, slots=True)
class Issued:
    token: str
    expires_at: dt.datetime

    def __repr__(self) -> str:
        return "Issued(token=<redacted>)"


async def issue(
    db: AsyncSession,
    *,
    kind: ChallengeKind,
    admin_id: uuid.UUID,
    ttl: dt.timedelta,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> Issued:
    """Create a challenge, replacing any outstanding one of the same kind.

    Replacing matters: starting a second login attempt must invalidate the first, or
    two concurrent challenges for one account both stay valid and the window for
    using an abandoned one stays open.
    """
    await db.execute(
        delete(AuthChallenge).where(AuthChallenge.admin_id == admin_id, AuthChallenge.kind == kind)
    )

    value = token or new_token(32)
    expires = _now() + ttl
    db.add(
        AuthChallenge(
            kind=kind,
            admin_id=admin_id,
            token_hash=sha256_bytes(value),
            payload=payload or {},
            expires_at=expires,
        )
    )
    await db.flush()
    return Issued(token=value, expires_at=expires)


@dataclass(frozen=True, slots=True)
class Claim:
    admin_id: uuid.UUID
    payload: dict[str, Any]


async def peek(db: AsyncSession, *, kind: ChallengeKind, token: str) -> Claim | None:
    """Look up a live challenge **without** consuming it.

    Used where a wrong answer must not burn the challenge -- mistyping a TOTP code
    should not force the admin back to the password step.
    """
    row = (
        await db.execute(
            select(AuthChallenge).where(
                AuthChallenge.kind == kind,
                AuthChallenge.token_hash == sha256_bytes(token),
                AuthChallenge.used_at.is_(None),
                AuthChallenge.expires_at > _now(),
            )
        )
    ).scalar_one_or_none()
    return Claim(admin_id=row.admin_id, payload=dict(row.payload)) if row else None


async def consume(db: AsyncSession, *, kind: ChallengeKind, token: str) -> Claim | None:
    """Spend a challenge atomically. Returns ``None`` if it was not live.

    A conditional UPDATE rather than select-then-update, so two concurrent
    submissions of the same token cannot both succeed.
    """
    result = await db.execute(
        update(AuthChallenge)
        .where(
            AuthChallenge.kind == kind,
            AuthChallenge.token_hash == sha256_bytes(token),
            AuthChallenge.used_at.is_(None),
            AuthChallenge.expires_at > _now(),
        )
        .values(used_at=_now())
        .returning(AuthChallenge.admin_id, AuthChallenge.payload)
    )
    row = result.first()
    if row is None:
        return None
    return Claim(admin_id=row[0], payload=dict(row[1] or {}))


async def consume_for_admin(
    db: AsyncSession, *, kind: ChallengeKind, admin_id: uuid.UUID
) -> Claim | None:
    """Spend the outstanding challenge for one admin, by id rather than by token.

    Used by chat verification, where the secret the admin types is the 6-digit code in
    the payload, not the row's token.
    """
    result = await db.execute(
        update(AuthChallenge)
        .where(
            AuthChallenge.kind == kind,
            AuthChallenge.admin_id == admin_id,
            AuthChallenge.used_at.is_(None),
            AuthChallenge.expires_at > _now(),
        )
        .values(used_at=_now())
        .returning(AuthChallenge.admin_id, AuthChallenge.payload)
    )
    row = result.first()
    if row is None:
        return None
    return Claim(admin_id=row[0], payload=dict(row[1] or {}))


async def peek_for_admin(
    db: AsyncSession, *, kind: ChallengeKind, admin_id: uuid.UUID
) -> Claim | None:
    row = (
        await db.execute(
            select(AuthChallenge).where(
                AuthChallenge.kind == kind,
                AuthChallenge.admin_id == admin_id,
                AuthChallenge.used_at.is_(None),
                AuthChallenge.expires_at > _now(),
            )
        )
    ).scalar_one_or_none()
    return Claim(admin_id=row.admin_id, payload=dict(row.payload)) if row else None


async def reap(db: AsyncSession) -> int:
    """Delete consumed and expired challenges. Housekeeping."""
    return await execute_rowcount(
        db,
        delete(AuthChallenge).where(
            (AuthChallenge.expires_at < _now()) | (AuthChallenge.used_at.is_not(None))
        ),
    )
