"""GCRA rate limiting, with state in PostgreSQL (ADR-0010).

**Why GCRA.** The Generic Cell Rate Algorithm holds its entire state in one
timestamp per key -- the "theoretical arrival time". That gives smooth rate limiting
with a configurable burst from one column and one comparison: no sliding-window log
to append to and prune, no fixed-window boundary that permits a 2x burst.

**Why PostgreSQL and not process memory.** There are two Uvicorn workers. Per-process
state would grant each one a full allowance, silently doubling every limit -- which
is worse than having no limit, because it looks like it works (F11.AC8).

**Why not Redis.** It would cost roughly 40 MB and a third stateful container for a
workload of 500 requests a day. At this volume the write is one small upsert per
limited request. If contention ever shows up in ``pg_stat_statements``, the next step
is an in-process front cache with PostgreSQL as the authority -- not a new service.

M1 uses this for login limiting (F8.AC9). M2 extends it to the capture path.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.db.dml import execute_rowcount

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Limit:
    """A rate limit.

    ``burst`` is what makes this usable for real traffic: a human who mistypes a
    password three times quickly is not an attacker, and a limiter with no burst
    allowance treats them as one.
    """

    name: str
    per_period: int
    period: dt.timedelta
    burst: int = 1

    @property
    def emission_interval(self) -> dt.timedelta:
        """Minimum spacing between requests at the sustained rate."""
        return self.period / self.per_period

    @property
    def delay_tolerance(self) -> dt.timedelta:
        """How far ahead of the sustained rate a caller may run."""
        return self.emission_interval * self.burst


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    retry_after_seconds: int
    limit_name: str


# The whole algorithm, as one statement. Done in SQL rather than read-modify-write in
# Python because two workers hitting the same key concurrently must not both read the
# old TAT and both decide they are allowed. ON CONFLICT ... DO UPDATE makes the
# read, the decision and the write a single atomic operation.
_GCRA_SQL = text(
    """
    WITH params AS (
        SELECT
            now()                                   AS t_now,
            CAST(:emission_us AS double precision)  AS emission_us,
            CAST(:tolerance_us AS double precision) AS tolerance_us
    ),
    existing AS (
        SELECT b.tat FROM rate_limit_buckets b WHERE b.key = :key
    ),
    computed AS (
        SELECT
            p.t_now,
            -- A new or long-idle key starts at now: no accumulated credit beyond
            -- the burst allowance.
            GREATEST(COALESCE(e.tat, p.t_now), p.t_now) AS tat,
            p.emission_us,
            p.tolerance_us
        FROM params p LEFT JOIN existing e ON true
    ),
    decision AS (
        SELECT
            t_now,
            tat,
            emission_us,
            -- Allowed when the new TAT would not exceed now + tolerance.
            (tat + (emission_us * interval '1 microsecond'))
                <= (t_now + (tolerance_us * interval '1 microsecond')) AS allowed,
            tat + (emission_us * interval '1 microsecond')             AS next_tat
        FROM computed
    )
    INSERT INTO rate_limit_buckets AS b (key, tat, updated_at)
    SELECT :key, CASE WHEN d.allowed THEN d.next_tat ELSE d.tat END, d.t_now FROM decision d
    ON CONFLICT (key) DO UPDATE
        SET tat = EXCLUDED.tat, updated_at = EXCLUDED.updated_at
    RETURNING
        b.tat,
        (SELECT allowed FROM decision)                                  AS allowed,
        GREATEST(
            0,
            CEIL(EXTRACT(EPOCH FROM (b.tat - (SELECT t_now FROM decision))))
        )                                                                AS retry_after
    """
)


async def check(db: AsyncSession, *, key: str, limit: Limit) -> Decision:
    """Consume one unit against ``key``. Never raises; returns a decision.

    ``key`` must be namespaced by limit and subject, e.g.
    ``login:user@example.com`` or ``cap:203.0.113.0/24``, or two different limits
    would share one bucket.
    """
    namespaced = f"{limit.name}:{key}"
    row = (
        await db.execute(
            _GCRA_SQL,
            {
                "key": namespaced,
                "emission_us": limit.emission_interval.total_seconds() * 1_000_000,
                "tolerance_us": limit.delay_tolerance.total_seconds() * 1_000_000,
            },
        )
    ).one()

    allowed = bool(row.allowed)
    retry_after = int(row.retry_after or 0)

    if not allowed:
        log.info("rate_limited", limit=limit.name, retry_after=retry_after)

    return Decision(
        allowed=allowed,
        retry_after_seconds=max(retry_after, 1) if not allowed else 0,
        limit_name=limit.name,
    )


async def reset(db: AsyncSession, *, key: str, limit: Limit) -> None:
    """Clear one bucket. Used after a successful login, and by tests.

    Resetting on success matters: without it, five failed attempts followed by a
    correct password still leaves the account limited, punishing the legitimate user
    for their own typos.
    """
    await db.execute(
        text("DELETE FROM rate_limit_buckets WHERE key = :key"), {"key": f"{limit.name}:{key}"}
    )


async def cleanup(db: AsyncSession, *, older_than: dt.timedelta = dt.timedelta(days=1)) -> int:
    """Delete stale buckets.

    Needed because keys are per-identifier and per-prefix, so the table would
    otherwise grow without bound. Scheduled from M6.
    """
    return await execute_rowcount(
        db,
        text("DELETE FROM rate_limit_buckets WHERE updated_at < now() - :age").bindparams(
            age=older_than
        ),
    )


# ---------------------------------------------------------------------------
# The M1 limits (F8.AC9). Values are calibrated to NFR1.AC3 -- two concurrent
# admins -- with enough burst that ordinary mistyping is not punished.
# ---------------------------------------------------------------------------

LOGIN_PER_IDENTIFIER = Limit(
    name="login_id", per_period=5, period=dt.timedelta(minutes=15), burst=5
)
LOGIN_PER_PREFIX = Limit(name="login_ip", per_period=20, period=dt.timedelta(hours=1), burst=10)
MFA_PER_IDENTIFIER = Limit(name="mfa_id", per_period=10, period=dt.timedelta(minutes=15), burst=5)
RESET_REQUEST_PER_IDENTIFIER = Limit(
    name="reset_req", per_period=3, period=dt.timedelta(hours=1), burst=3
)
# Deliberately tight: each attempt costs ten Argon2 verifications at 32 MiB, so this
# endpoint is a memory-amplification vector as much as a credential one.
RECOVERY_PER_IDENTIFIER = Limit(
    name="recovery", per_period=5, period=dt.timedelta(hours=1), burst=3
)
