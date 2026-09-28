"""Helpers for driving the real authentication flow over HTTP.

Everything here talks to the actual endpoints and the actual database. There is no
test double for either, because most of the eight M1 bugs (docs/ERRORS.md E8-E15)
were invisible at any layer above the wire: a cookie the browser would refuse, an
``INET`` column compared against a string, a commit that ran after the response.

Two traps are handled by code in this module rather than by care in each test, and
both wasted real time when they were handled by care:

**TOTP steps are single-use.** The accepted step becomes the stored high-water mark,
so a test that reuses a step gets ``reason="replayed"`` and looks like a product bug.
:class:`TotpClock` tracks spent steps per admin and waits for a rollover only when it
has to -- it uses the ``+1`` skew window first, so three consecutive logins need one
wait rather than two.

**Rate-limit buckets are shared.** Five sign-in attempts per identifier and twenty per
prefix per hour is generous for a human and nothing for a test suite. The autouse
fixture in ``conftest`` clears them, so tests measure the auth path rather than the
limiter.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pyotp
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text

from tracelet.auth import challenges, service, totp
from tracelet.auth.models import (
    Admin,
    AdminRole,
    AdminStatus,
    AuditLog,
    PasswordResetToken,
)
from tracelet.config import Settings
from tracelet.crypto.hashing import new_token, sha256_bytes
from tracelet.db.engine import session_scope

# Satisfies the policy in every fixture: 24 characters, no token shared with the
# generated email local parts, and nothing resembling the site name.
STRONG_PASSWORD = "Brisk-Lantern-Harbour-42"
ANOTHER_PASSWORD = "Copper-Meadow-Sundial-19"

AUTH = "/api/v1/auth"
ADMINS = "/api/v1/admins"

# What the ``new_client`` fixture hands back: each call yields a client with its
# own cookie jar, so two sessions can be live at once.
ClientFactory = Callable[[], Awaitable[AsyncClient]]


def origin_headers(site_address: str = "localhost") -> dict[str, str]:
    """The ``Origin`` a browser would send. Required on state-changing requests.

    Passed explicitly at every call site rather than set as a client default, so
    that the tests which assert a *missing* or *foreign* origin is refused read as
    deliberate rather than as a client quirk.
    """
    return {"Origin": f"https://{site_address}"}


def new_email(prefix: str = "m1") -> str:
    """A unique login identifier.

    ``example.test`` is reserved, so nothing here can accidentally address a real
    mailbox -- which matters because an admin address is only a login identifier
    and this system sends no email at all.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.test"


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


@dataclass
class TotpClock:
    """Produces codes that are never a replay of a step already spent.

    Keyed by admin id, because the high-water mark is stored per admin. Uses the
    next step above the last one spent, which the server accepts while it is within
    its one-step skew window -- so a second code costs nothing and only a third
    waits for the window to roll over.
    """

    spent: dict[str, int] = field(default_factory=dict)

    async def code(self, secret: str, admin_id: uuid.UUID | str) -> str:
        key = str(admin_id)
        target = self.spent.get(key, totp.current_step() - 1) + 1

        # The server checks steps now-1, now and now+1. Anything beyond that has to
        # wait for real time to catch up. ASYNC110 wants an Event here, but there is
        # nothing to signal: the thing being waited on is the wall clock.
        while target > totp.current_step() + totp.TOTP_SKEW_STEPS:  # noqa: ASYNC110
            await asyncio.sleep(totp.TOTP_INTERVAL - (time.time() % totp.TOTP_INTERVAL) + 0.5)

        self.spent[key] = target
        return self.at(secret, target)

    @staticmethod
    def at(secret: str, step: int) -> str:
        moment = dt.datetime.fromtimestamp(step * totp.TOTP_INTERVAL + 1, tz=dt.UTC)
        return pyotp.TOTP(secret, digits=totp.TOTP_DIGITS, interval=totp.TOTP_INTERVAL).at(moment)

    def forget(self, admin_id: uuid.UUID | str) -> None:
        """Drop the tracked step, for a secret that has been replaced."""
        self.spent.pop(str(admin_id), None)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Invited:
    """A pending account plus its live enrollment token."""

    id: uuid.UUID
    email: str
    role: AdminRole
    token: str
    enrollment_url: str


async def invite(
    settings: Settings,
    *,
    role: AdminRole = AdminRole.ANALYST,
    email: str | None = None,
    display_name: str = "Integration Test",
) -> Invited:
    """Create a pending admin and an enrollment token, straight in the database.

    Deliberately not through ``POST /api/v1/admins``: that needs a signed-in owner,
    which needs an invited admin, which is what this creates. The owner route has
    its own tests.
    """
    address = email or new_email()
    async with session_scope() as db:
        admin = Admin(
            email=address,
            display_name=display_name,
            role=role,
            status=AdminStatus.PENDING_ENROLLMENT,
        )
        db.add(admin)
        await db.flush()
        admin_id = admin.id
        offer = await service.issue_enrollment_token(
            db, settings, admin_id=admin_id, created_by=None
        )

    return Invited(
        id=admin_id,
        email=address,
        role=role,
        token=token_from(offer.url),
        enrollment_url=offer.url,
    )


def token_from(url: str) -> str:
    """The opaque token out of an enrollment or reset URL."""
    _, _, token = url.partition("token=")
    assert token, f"no token in {url!r}"
    return token


@dataclass
class SignedIn:
    """An active admin with a live session on one client."""

    id: uuid.UUID
    email: str
    role: AdminRole
    password: str
    secret: str
    recovery_codes: list[str]
    csrf_token: str
    client: AsyncClient

    def headers(self, site_address: str = "localhost") -> dict[str, str]:
        """Everything a browser sends on a state-changing request."""
        return {**origin_headers(site_address), "X-CSRF-Token": self.csrf_token}


def csrf_from(response: Response) -> str:
    """Read the CSRF token out of a response header.

    Read case-insensitively on purpose: the stack normalises ``X-CSRF-Token`` to
    ``X-Csrf-Token``, and an exact-case lookup in the frontend was a real bug.
    """
    token = str(response.headers.get("x-csrf-token") or "")
    assert token, f"no CSRF token on {response.request.method} {response.request.url.path}"
    return token


async def enroll(
    client: AsyncClient,
    invited: Invited,
    clock: TotpClock,
    *,
    password: str = STRONG_PASSWORD,
) -> SignedIn:
    """Run the whole enrolment path and end up signed in.

    ``/totp/confirm`` issues the session directly: both factors have just been
    proved, and asking for a second code would be refused as a replay.
    """
    enrolled = await client.post(
        f"{AUTH}/enroll", json={"token": invited.token, "password": password}
    )
    assert enrolled.status_code == 200, enrolled.text
    body = enrolled.json()

    confirmed = await client.post(
        f"{AUTH}/totp/confirm",
        json={
            "confirm_token": body["confirm_token"],
            "code": await clock.code(body["secret"], invited.id),
        },
    )
    assert confirmed.status_code == 204, confirmed.text

    return SignedIn(
        id=invited.id,
        email=invited.email,
        role=invited.role,
        password=password,
        secret=body["secret"],
        recovery_codes=list(body["recovery_codes"]),
        csrf_token=csrf_from(confirmed),
        client=client,
    )


async def sign_in(
    client: AsyncClient,
    *,
    email: str,
    password: str,
    secret: str,
    admin_id: uuid.UUID,
    clock: TotpClock,
) -> str:
    """Both login steps. Returns the CSRF token for the new session."""
    first = await client.post(f"{AUTH}/login", json={"email": email, "password": password})
    assert first.status_code == 200, first.text

    second = await client.post(
        f"{AUTH}/mfa",
        json={
            "mfa_token": first.json()["mfa_token"],
            "code": await clock.code(secret, admin_id),
        },
    )
    assert second.status_code == 204, second.text
    return csrf_from(second)


# ---------------------------------------------------------------------------
# Database inspection
# ---------------------------------------------------------------------------


async def audit_actions(admin_id: uuid.UUID) -> list[str]:
    """Every audit action recorded against one admin, oldest first."""
    async with session_scope() as db:
        rows = await db.execute(
            select(AuditLog.action).where(AuditLog.actor_admin_id == admin_id).order_by(AuditLog.id)
        )
        return list(rows.scalars())


async def audit_details(admin_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    """The ``detail`` payloads for one action, oldest first."""
    async with session_scope() as db:
        rows = await db.execute(
            select(AuditLog.detail)
            .where(AuditLog.actor_admin_id == admin_id, AuditLog.action == action)
            .order_by(AuditLog.id)
        )
        return [dict(row) for row in rows.scalars()]


async def reload_admin(admin_id: uuid.UUID) -> Admin:
    async with session_scope() as db:
        return (await db.execute(select(Admin).where(Admin.id == admin_id))).scalar_one()


async def live_session_count(admin_id: uuid.UUID) -> int:
    async with session_scope() as db:
        count = await db.execute(
            text(
                "SELECT count(*) FROM sessions "
                "WHERE admin_id = :admin_id AND revoked_at IS NULL AND expires_at > now()"
            ),
            {"admin_id": str(admin_id)},
        )
        return int(count.scalar_one())


async def live_reset_token_count(admin_id: uuid.UUID) -> int:
    async with session_scope() as db:
        count = await db.execute(
            text(
                "SELECT count(*) FROM password_reset_tokens "
                "WHERE admin_id = :admin_id AND used_at IS NULL AND expires_at > now()"
            ),
            {"admin_id": str(admin_id)},
        )
        return int(count.scalar_one())


async def clear_rate_limits() -> None:
    async with session_scope() as db:
        await db.execute(text("DELETE FROM rate_limit_buckets"))


async def seed_reset_token(admin_id: uuid.UUID, *, ttl_minutes: int = 30) -> str:
    """Insert a live password-reset token and return its plaintext.

    Delivery is Telegram, and the permanent suite does not send real messages: it
    would need a live token and network in CI, and a test that messages a real chat
    is a test nobody wants to run twice. The delivery path was verified by hand
    against the real bot; what is worth regression-testing is everything the token
    then unlocks.
    """
    token = new_token(32)
    async with session_scope() as db:
        db.add(
            PasswordResetToken(
                admin_id=admin_id,
                token_hash=sha256_bytes(token),
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=ttl_minutes),
                requested_ip_prefix=None,
            )
        )
    return token


async def seed_chat_verification(admin_id: uuid.UUID, *, chat_id: int, code: str) -> None:
    """Stage a chat-verification challenge without sending a Telegram message."""
    async with session_scope() as db:
        await challenges.issue(
            db,
            kind=challenges.ChallengeKind.CHAT_VERIFY,
            admin_id=admin_id,
            ttl=dt.timedelta(minutes=10),
            payload={"code": code, "chat_id": chat_id},
            token=str(admin_id),
        )


async def active_owner_count() -> int:
    async with session_scope() as db:
        count = await db.execute(
            select(func.count())
            .select_from(Admin)
            .where(Admin.role == AdminRole.OWNER, Admin.status == AdminStatus.ACTIVE)
        )
        return int(count.scalar_one())


async def audit_rows_for_target(target_id: uuid.UUID) -> int:
    """Rows naming an admin as the target, regardless of who acted.

    Used to prove ``ON DELETE SET NULL`` rather than CASCADE: deleting an admin
    must not delete the record of what they did.
    """
    async with session_scope() as db:
        count = await db.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.target_id == str(target_id))
        )
        return int(count.scalar_one())


async def unattributed_login_failures() -> int:
    """Failed logins with no actor: an attempt against an address that does not exist."""
    async with session_scope() as db:
        count = await db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "admin.login_failed",
                AuditLog.actor_admin_id.is_(None),
            )
        )
        return int(count.scalar_one())


async def audit_rows_with_trace(trace_id: str) -> int:
    async with session_scope() as db:
        count = await db.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.trace_id == trace_id)
        )
        return int(count.scalar_one())


async def audit_actor_prefixes(admin_id: uuid.UUID) -> list[str]:
    async with session_scope() as db:
        rows = await db.execute(
            select(AuditLog.actor_ip_prefix).where(
                AuditLog.actor_admin_id == admin_id,
                AuditLog.actor_ip_prefix.is_not(None),
            )
        )
        return [str(row) for row in rows.scalars()]


async def audit_actions_for_target(target_id: uuid.UUID) -> list[str]:
    """Actions recorded *about* an admin, regardless of who acted.

    The CLI acts as the system, so its rows carry ``actor_admin_id = NULL`` and name
    the admin in ``target_id`` instead. Looking those up by actor finds nothing.
    """
    async with session_scope() as db:
        rows = await db.execute(
            select(AuditLog.action)
            .where(AuditLog.target_id == str(target_id))
            .order_by(AuditLog.id)
        )
        return list(rows.scalars())


async def audit_details_for_target(target_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    async with session_scope() as db:
        rows = await db.execute(
            select(AuditLog.detail)
            .where(AuditLog.target_id == str(target_id), AuditLog.action == action)
            .order_by(AuditLog.id)
        )
        return [dict(row) for row in rows.scalars()]
