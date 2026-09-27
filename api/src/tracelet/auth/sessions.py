"""Opaque server-side sessions (ADR-0008, F8.AC2, F8.AC3).

Not a JWT, and the reason is worth restating because JWT is the reflex answer: a
JWT **cannot be revoked** before expiry without a server-side denylist -- and once
you have that, you have session state anyway, plus signing-key rotation on top.
Statelessness buys horizontal scaling, which is worth exactly nothing for two
concurrent admins on one VM.

What the design does buy:

* **Instant revocation**, per session.
* **No signing key** to rotate or leak.
* **Binding** to the network prefix and a User-Agent hash, so a stolen cookie
  replayed from elsewhere fails. The cost is real and visible: switching from Wi-Fi
  to mobile data logs you out. For two admins that is a fair trade.
* **The token is never stored.** Only ``sha256(token)``, so a database leak yields
  nothing a client can present.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.auth.models import Session
from tracelet.crypto.hashing import constant_time_equals, new_token, sha256_bytes
from tracelet.db.dml import execute_rowcount

log = structlog.get_logger(__name__)

# The cookie name carries the __Host- prefix, which browsers enforce: it requires
# Secure, Path=/ and no Domain attribute. That is what prevents a sibling subdomain
# from setting a session cookie for us.
COOKIE_NAME = "__Host-tracelet_session"
CSRF_HEADER = "X-CSRF-Token"

# Absolute lifetime, and a sliding idle window. Two clocks on purpose: the idle
# window logs out a forgotten tab, while the absolute expiry bounds how long a
# stolen-but-actively-used session can survive.
ABSOLUTE_LIFETIME = dt.timedelta(hours=12)
IDLE_TIMEOUT = dt.timedelta(minutes=45)

# Refresh at most this often, so an active session does not write to the database on
# every single request.
LAST_SEEN_REFRESH = dt.timedelta(minutes=1)


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def prefix_of(ip: str | None) -> str | None:
    """Coarsen an address to /24 (v4) or /48 (v6).

    Binding on the prefix rather than the exact address is deliberate: a full address
    changes on every CGNAT rebalance and would log a mobile admin out constantly,
    while the prefix still defeats replay from a different network.
    """
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    net = (
        ipaddress.ip_network(f"{addr}/24", strict=False)
        if addr.version == 4
        else ipaddress.ip_network(f"{addr}/48", strict=False)
    )
    return str(net)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A new session. ``token`` and ``csrf_token`` are returned exactly once."""

    session_id: uuid.UUID
    token: str
    csrf_token: str
    expires_at: dt.datetime

    def __repr__(self) -> str:
        return f"IssuedSession(session_id={self.session_id}, token=<redacted>)"


async def create(
    db: AsyncSession,
    *,
    admin_id: uuid.UUID,
    ip: str | None,
    user_agent: str | None,
) -> IssuedSession:
    token = new_token(32)  # 256 bits
    csrf_secret = new_token(32)
    issued_at = now()

    row = Session(
        token_hash=sha256_bytes(token),
        admin_id=admin_id,
        csrf_secret=csrf_secret.encode("utf-8"),
        ip_prefix=prefix_of(ip),
        ua_hash=sha256_bytes(user_agent) if user_agent else None,
        created_at=issued_at,
        last_seen_at=issued_at,
        expires_at=issued_at + ABSOLUTE_LIFETIME,
        idle_expires_at=issued_at + IDLE_TIMEOUT,
    )
    db.add(row)
    await db.flush()

    log.info("session_created", admin_id=str(admin_id), session_id=str(row.id))
    return IssuedSession(
        session_id=row.id,
        token=token,
        csrf_token=csrf_secret,
        expires_at=row.expires_at,
    )


@dataclass(frozen=True, slots=True)
class Resolved:
    session: Session
    csrf_secret: str


async def resolve(
    db: AsyncSession,
    *,
    token: str,
    ip: str | None,
    user_agent: str | None,
) -> Resolved | None:
    """Look up a live session and verify its bindings.

    Returns ``None`` for every failure -- absent, expired, revoked, or mismatched
    binding. The caller cannot distinguish them, and should not: a client has no
    legitimate use for knowing *why* its session was rejected, and the distinction
    would tell an attacker whether a token was ever valid.
    """
    row = (
        await db.execute(select(Session).where(Session.token_hash == sha256_bytes(token)))
    ).scalar_one_or_none()
    if row is None:
        return None

    current = now()
    if not row.is_live(current):
        return None

    # str() is load-bearing. SQLAlchemy returns an ipaddress.IPv4Interface for an
    # INET column, while prefix_of() returns a canonical string, so comparing them
    # directly is always unequal -- which silently rejected EVERY session as a
    # binding mismatch (docs/ERRORS.md E12).
    stored_prefix = str(row.ip_prefix) if row.ip_prefix is not None else None
    if stored_prefix is not None and prefix_of(ip) != stored_prefix:
        log.warning(
            "session_binding_mismatch",
            session_id=str(row.id),
            kind="ip_prefix",
        )
        return None

    if row.ua_hash is not None and (
        user_agent is None or not constant_time_equals(row.ua_hash, sha256_bytes(user_agent))
    ):
        log.warning("session_binding_mismatch", session_id=str(row.id), kind="user_agent")
        return None

    # Slide the idle window, throttled so an active session does not write on every
    # request.
    if current - row.last_seen_at > LAST_SEEN_REFRESH:
        await db.execute(
            update(Session)
            .where(Session.id == row.id)
            .values(last_seen_at=current, idle_expires_at=current + IDLE_TIMEOUT)
        )

    return Resolved(session=row, csrf_secret=row.csrf_secret.decode("utf-8"))


async def revoke(db: AsyncSession, session_id: uuid.UUID, *, reason: str) -> bool:
    affected = await execute_rowcount(
        db,
        update(Session)
        .where(Session.id == session_id, Session.revoked_at.is_(None))
        .values(revoked_at=now(), revoked_reason=reason),
    )
    return affected == 1


async def revoke_all_for_admin(
    db: AsyncSession, admin_id: uuid.UUID, *, reason: str, except_id: uuid.UUID | None = None
) -> int:
    """Revoke every live session for an admin.

    Called on password change and on a completed reset. Without it, an attacker who
    already holds a session keeps it after the victim changes their password --
    which is the one action a victim takes believing it locks the attacker out.
    """
    stmt = (
        update(Session)
        .where(Session.admin_id == admin_id, Session.revoked_at.is_(None))
        .values(revoked_at=now(), revoked_reason=reason)
    )
    if except_id is not None:
        stmt = stmt.where(Session.id != except_id)
    affected = await execute_rowcount(db, stmt)
    log.info("sessions_revoked", admin_id=str(admin_id), count=affected, reason=reason)
    return affected


async def list_for_admin(db: AsyncSession, admin_id: uuid.UUID) -> list[Session]:
    rows = await db.execute(
        select(Session)
        .where(Session.admin_id == admin_id, Session.revoked_at.is_(None))
        .order_by(Session.created_at.desc())
    )
    current = now()
    return [row for row in rows.scalars() if row.is_live(current)]


async def reap_expired(db: AsyncSession) -> int:
    """Delete sessions past their absolute expiry. Housekeeping, not security."""
    from sqlalchemy import delete  # noqa: PLC0415 - local to keep the module import graph flat

    return await execute_rowcount(db, delete(Session).where(Session.expires_at < now()))
