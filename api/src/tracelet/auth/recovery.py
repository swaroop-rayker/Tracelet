"""One-time recovery codes (F8.AC6).

One of **three independent** recovery paths, because each fails differently and a
solo-admin system has no help desk (ADR-0008):

1. these codes -- can be lost
2. a Telegram-delivered reset link -- the account can be locked out
3. the break-glass CLI -- needs shell access

Codes are Argon2id-hashed, exactly like a password, because a recovery code **is** a
credential: it bypasses both the password and the second factor. Storing them
hashed means a database leak does not hand over ten ready-made logins.

They are displayed exactly once. That is the correct security property and a real
operational hazard, so the warning belongs in the UI and in the runbook rather than
being softened.
"""

from __future__ import annotations

import secrets
import uuid

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.auth.models import RecoveryCode
from tracelet.crypto.hashing import hash_password, verify_password
from tracelet.db.dml import execute_rowcount

log = structlog.get_logger(__name__)

CODE_COUNT = 10
GROUP_LEN = 5
GROUPS = 2

# Crockford-style: no I, L, O, U, or digits 0/1. Transcription errors are the
# realistic failure mode for a code read off paper months later, and an
# ambiguous glyph turns a working code into a lockout.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"

LOW_REMAINING_WARNING = 3


def _generate_code() -> str:
    groups = ["".join(secrets.choice(ALPHABET) for _ in range(GROUP_LEN)) for _ in range(GROUPS)]
    return "-".join(groups)


def normalise(code: str) -> str:
    """Accept what a human actually types: any case, spaces, missing dashes."""
    cleaned = "".join(ch for ch in code.upper() if ch.isalnum())
    if len(cleaned) != GROUP_LEN * GROUPS:
        return cleaned
    return "-".join(cleaned[i : i + GROUP_LEN] for i in range(0, len(cleaned), GROUP_LEN))


async def issue(session: AsyncSession, admin_id: uuid.UUID) -> list[str]:
    """Replace any existing codes with a fresh set. Returns the plaintext once."""
    # Delete rather than mark used: regenerating must invalidate the old set
    # completely, or a leaked old code stays valid.
    existing = await session.execute(select(RecoveryCode).where(RecoveryCode.admin_id == admin_id))
    for row in existing.scalars():
        await session.delete(row)

    codes = [_generate_code() for _ in range(CODE_COUNT)]
    for code in codes:
        session.add(RecoveryCode(admin_id=admin_id, code_hash=hash_password(code)))

    log.info("recovery_codes_issued", admin_id=str(admin_id), count=len(codes))
    return codes


async def consume(session: AsyncSession, admin_id: uuid.UUID, supplied: str) -> bool:
    """Verify and spend one code. Returns False if no unused code matches.

    Every unused code is checked, because they are salted independently and there is
    no index to look one up by. At ten rows this costs ten Argon2 verifications --
    roughly 500 ms at the pinned 32 MiB. That is acceptable here: recovery is rare
    and rate-limited, and the alternative (an unsalted lookup hash) would make a
    database leak far worse.
    """
    rows = (
        (
            await session.execute(
                select(RecoveryCode).where(
                    RecoveryCode.admin_id == admin_id,
                    RecoveryCode.used_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    candidate = normalise(supplied)

    for row in rows:
        if not verify_password(row.code_hash, candidate):
            continue

        # Conditional UPDATE, not an attribute assignment: two concurrent submissions
        # of the same code would both pass the verify above, and only one may win.
        affected = await execute_rowcount(
            session,
            update(RecoveryCode)
            .where(RecoveryCode.id == row.id, RecoveryCode.used_at.is_(None))
            .values(used_at=sa_now()),
        )
        if affected != 1:
            log.warning("recovery_code_race_lost", admin_id=str(admin_id))
            return False

        log.info("recovery_code_consumed", admin_id=str(admin_id))
        return True

    return False


async def remaining(session: AsyncSession, admin_id: uuid.UUID) -> int:
    rows = await session.execute(
        select(RecoveryCode.id).where(
            RecoveryCode.admin_id == admin_id,
            RecoveryCode.used_at.is_(None),
        )
    )
    return len(rows.scalars().all())


def sa_now() -> object:
    """``now()`` evaluated by the database, not the application.

    Two workers can disagree about the time by seconds; the database cannot disagree
    with itself. Every timestamp written here therefore comes from one clock.
    """
    from sqlalchemy import func  # noqa: PLC0415 - kept local to avoid a module-level import cycle

    return func.now()
