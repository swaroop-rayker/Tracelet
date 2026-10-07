"""Retention: the policy, an exact dry run, and the batched purge (F10.AC12, F12.AC7-AC8).

**The preview is exact because the purge reuses its cutoffs.** A preview is taken at an
instant ``as_of`` under a policy; every cutoff is derived from those two. The purge is
handed the same ``as_of`` and policy back, refuses if the policy has changed or the
preview is old, and deletes against the same cutoffs. No row can newly fall behind a
fixed cutoff -- visits, audit rows and deliveries are timestamped when they happen -- so
what the preview counted is what the purge deletes. The scheduled purges (the nightly one,
and the 10-minute IP purge) can take some of those rows first; then the manual purge
deletes fewer, and says how many.

Categories, in the order they are purged:

* **visits** older than ``visit_days``, with their ``visit_candidates`` (cascade);
* **IP ciphertext** whose ``ip_purge_after`` has passed, on visits that are being kept
  (a visit about to be deleted is counted once, as a visit);
* **audit rows** older than ``audit_days`` -- only ``tracelet_maint`` may delete them;
* **delivered alerts** older than 30 days (ADR-0014; not configurable, and dead letters
  are never purged).

Rollups are never purged (NFR5.AC4). The purge runs as ``tracelet_maint`` in batches of
1 000, each its own transaction, holding a session advisory lock so only one purge runs.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import time
import uuid
import zoneinfo
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
from typing import Any, Final

import structlog
from sqlalchemy import text
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tracelet.audit import log as audit
from tracelet.config import Settings, get_settings
from tracelet.db.engine import session_scope
from tracelet.errors import LifecycleJobRunning, MaintenanceUnavailable, RetentionPreviewStale
from tracelet.lifecycle.maint import maint_connection
from tracelet.lifecycle.models import RetentionPolicy

log = structlog.get_logger(__name__)

OUTBOX_DONE_DAYS: Final = 30
PREVIEW_VALID_FOR: Final = dt.timedelta(minutes=15)
BATCH: Final = 1000


def _lock_key(name: str) -> int:
    digest = hashlib.sha256(f"tracelet.lifecycle.{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


PURGE_LOCK: Final = _lock_key("purge")


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Policy:
    visit_days: int
    ip_days: int
    audit_days: int


def _policy(row: RetentionPolicy) -> Policy:
    return Policy(visit_days=row.visit_days, ip_days=row.ip_days, audit_days=row.audit_days)


async def policy_row(db: AsyncSession, settings: Settings | None = None) -> RetentionPolicy:
    """The policy row, written from the environment's defaults the first time it is read
    (DATA_MODEL section 8.5)."""
    row = await db.get(RetentionPolicy, 1)
    if row is not None:
        return row
    settings = settings or get_settings()
    await db.execute(
        pg.insert(RetentionPolicy)
        .values(
            id=1,
            visit_days=max(settings.retention_visit_days, 8),
            ip_days=settings.retention_ip_days,
            audit_days=settings.retention_audit_days,
        )
        .on_conflict_do_nothing(index_elements=[RetentionPolicy.id])
    )
    row = await db.get(RetentionPolicy, 1, populate_existing=True)
    assert row is not None  # inserted or already there, one statement ago
    return row


async def current_policy(db: AsyncSession) -> Policy:
    return _policy(await policy_row(db))


async def change_policy(
    db: AsyncSession, new: Policy, *, actor: uuid.UUID | None
) -> tuple[Policy, Policy]:
    """Save the policy. A changed IP period re-stamps the IP expiry of every visit still
    holding one, so the 10-minute IP purge applies the new period to old visits too."""
    row = await policy_row(db)
    before = _policy(row)
    row.visit_days, row.ip_days, row.audit_days = new.visit_days, new.ip_days, new.audit_days
    row.updated_by = actor
    row.updated_at = dt.datetime.now(dt.UTC)
    await db.flush()
    if new.ip_days != before.ip_days:
        await db.execute(
            text(
                "UPDATE visits SET ip_purge_after = occurred_at + make_interval(days => :d) "
                "WHERE ip_enc IS NOT NULL"
            ),
            {"d": new.ip_days},
        )
    return before, new


# ---------------------------------------------------------------------------
# Cutoffs and the dry run
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cutoffs:
    visits: dt.datetime
    ip: dt.datetime
    audit: dt.datetime
    outbox: dt.datetime


def cutoffs(as_of: dt.datetime, policy: Policy) -> Cutoffs:
    return Cutoffs(
        visits=as_of - dt.timedelta(days=policy.visit_days),
        ip=as_of,
        audit=as_of - dt.timedelta(days=policy.audit_days),
        outbox=as_of - dt.timedelta(days=OUTBOX_DONE_DAYS),
    )


@dataclass(frozen=True, slots=True)
class Counts:
    visits: int
    visit_candidates: int
    ip_addresses: int
    audit_rows: int
    delivered_alerts: int


_COUNT_SQL: Final = text(
    """
    SELECT
      (SELECT count(*) FROM visits WHERE occurred_at < :visits),
      (SELECT count(*) FROM visit_candidates c JOIN visits v ON v.id = c.visit_id
        WHERE v.occurred_at < :visits),
      (SELECT count(*) FROM visits WHERE ip_enc IS NOT NULL AND ip_purge_after <= :ip
        AND occurred_at >= :visits),
      (SELECT count(*) FROM audit_log WHERE occurred_at < :audit),
      (SELECT count(*) FROM outbox WHERE status = 'done' AND completed_at < :outbox)
    """
)


async def count(db: AsyncSession, cut: Cutoffs) -> Counts:
    row = (await db.execute(_COUNT_SQL, asdict(cut))).one()
    return Counts(*(int(n) for n in row))


@dataclass(frozen=True, slots=True)
class Preview:
    as_of: dt.datetime
    policy: Policy
    cutoffs: Cutoffs
    counts: Counts


async def preview(db: AsyncSession, *, as_of: dt.datetime | None = None) -> Preview:
    """Exactly what a purge at ``as_of`` under the current policy would delete. Deletes
    nothing."""
    as_of = as_of or dt.datetime.now(dt.UTC)
    policy = await current_policy(db)
    cut = cutoffs(as_of, policy)
    return Preview(as_of=as_of, policy=policy, cutoffs=cut, counts=await count(db, cut))


# ---------------------------------------------------------------------------
# The purge
# ---------------------------------------------------------------------------

_PURGE_VISITS: Final = text(
    """
    WITH doomed AS (
      SELECT id FROM visits WHERE occurred_at < :cut ORDER BY occurred_at LIMIT :batch
      FOR UPDATE
    ), candidates AS (
      DELETE FROM visit_candidates WHERE visit_id IN (SELECT id FROM doomed) RETURNING 1
    ), gone AS (
      DELETE FROM visits WHERE id IN (SELECT id FROM doomed) RETURNING 1
    )
    SELECT (SELECT count(*) FROM gone), (SELECT count(*) FROM candidates)
    """
)
_PURGE_IPS: Final = text(
    """
    UPDATE visits SET ip_enc = NULL, ip_key_version = NULL
    WHERE id IN (
      SELECT id FROM visits
      WHERE ip_enc IS NOT NULL AND ip_purge_after <= :cut AND occurred_at >= :keep
      LIMIT :batch FOR UPDATE
    )
    """
)
_PURGE_AUDIT: Final = text(
    "DELETE FROM audit_log WHERE id IN "
    "(SELECT id FROM audit_log WHERE occurred_at < :cut ORDER BY id LIMIT :batch)"
)
_PURGE_OUTBOX: Final = text(
    "DELETE FROM outbox WHERE id IN (SELECT id FROM outbox WHERE status = 'done' "
    "AND completed_at < :cut ORDER BY id LIMIT :batch)"
)


async def _batches(conn: AsyncConnection, sql: Any, params: dict[str, Any]) -> int:
    total = 0
    while True:
        async with conn.begin():
            done = (await conn.execute(sql, {**params, "batch": BATCH})).rowcount
        total += done
        if done < BATCH:
            return total
        await asyncio.sleep(0)  # let the capture path in between batches


async def _purge_with(conn: AsyncConnection, cut: Cutoffs) -> Counts:
    visits = candidates = 0
    while True:
        async with conn.begin():
            gone, cascaded = (
                await conn.execute(_PURGE_VISITS, {"cut": cut.visits, "batch": BATCH})
            ).one()
        visits, candidates = visits + int(gone), candidates + int(cascaded)
        if gone < BATCH:
            break
        await asyncio.sleep(0)
    return Counts(
        visits=visits,
        visit_candidates=candidates,
        ip_addresses=await _batches(conn, _PURGE_IPS, {"cut": cut.ip, "keep": cut.visits}),
        audit_rows=await _batches(conn, _PURGE_AUDIT, {"cut": cut.audit}),
        delivered_alerts=await _batches(conn, _PURGE_OUTBOX, {"cut": cut.outbox}),
    )


async def _lock(conn: AsyncConnection) -> bool:
    """A *session* advisory lock: it outlives the transaction that takes it, so it is held
    across every batch's commit until the connection closes."""
    async with conn.begin():
        held = (
            await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": PURGE_LOCK})
        ).scalar_one()
    return bool(held)


async def purge_running(db: AsyncSession) -> bool:
    """Whether a purge holds the lock, in either worker."""
    return bool(
        (
            await db.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                    "AND classid = :hi AND objid = :lo AND objsubid = 1 AND granted)"
                ),
                {"hi": PURGE_LOCK >> 32, "lo": PURGE_LOCK & 0xFFFF_FFFF},
            )
        ).scalar_one()
    )


async def _run_locked(
    stack: AsyncExitStack,
    conn: AsyncConnection,
    *,
    as_of: dt.datetime,
    policy: Policy,
    trigger: str,
    actor: uuid.UUID | None,
    trace_id: str | None,
) -> Counts:
    async with stack:
        started = time.monotonic()
        cut = cutoffs(as_of, policy)
        counts = await _purge_with(conn, cut)
        async with session_scope() as db:
            await audit.record(
                db,
                action=audit.Action.RETENTION_PURGED,
                actor_admin_id=actor,
                target_type="retention_policy",
                target_id="1",
                trace_id=trace_id,
                detail={
                    "trigger": trigger,
                    "as_of": as_of.isoformat(),
                    "policy": asdict(policy),
                    "cutoffs": {k: v.isoformat() for k, v in asdict(cut).items()},
                    "counts": asdict(counts),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
        log.info("retention_purged", trigger=trigger, **asdict(counts))
        return counts


async def _locked_connection() -> tuple[AsyncExitStack, AsyncConnection]:
    stack = AsyncExitStack()
    conn = await stack.enter_async_context(maint_connection())
    if not await _lock(conn):
        await stack.aclose()
        raise LifecycleJobRunning("A purge is already running.")
    return stack, conn


def check_preview(as_of: dt.datetime, policy: Policy, current: Policy) -> None:
    """A purge runs only against a fresh preview of the current policy (F10.AC12)."""
    now = dt.datetime.now(dt.UTC)
    if policy != current:
        raise RetentionPreviewStale("The retention periods have changed since the preview.")
    if as_of > now + dt.timedelta(seconds=5) or now - as_of > PREVIEW_VALID_FOR:
        raise RetentionPreviewStale("The preview is more than 15 minutes old.")


_TASKS: set[asyncio.Task[Counts]] = set()


async def start_manual_purge(
    *, as_of: dt.datetime, policy: Policy, actor: uuid.UUID, trace_id: str | None
) -> asyncio.Task[Counts]:
    """Take the lock now, so a second purge is refused in the request, then purge in the
    background (``202``): the counts land in the audit row ``retention.purged``."""
    stack, conn = await _locked_connection()
    task = asyncio.create_task(
        _run_locked(
            stack,
            conn,
            as_of=as_of,
            policy=policy,
            trigger="manual",
            actor=actor,
            trace_id=trace_id,
        ),
        name="retention:manual",
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def purge_now(*, as_of: dt.datetime, policy: Policy, trigger: str) -> Counts:
    """A purge in the caller's task: the scheduler's, and the tests'."""
    stack, conn = await _locked_connection()
    return await _run_locked(
        stack, conn, as_of=as_of, policy=policy, trigger=trigger, actor=None, trace_id=None
    )


# ---------------------------------------------------------------------------
# The nightly purge
# ---------------------------------------------------------------------------


def local_boundary(now: dt.datetime, hour: int, tz: str) -> dt.datetime:
    """The most recent ``hour:00`` in ``tz`` at or before ``now``, as UTC."""
    zone = zoneinfo.ZoneInfo(tz)
    local = now.astimezone(zone)
    boundary = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if boundary > local:
        boundary -= dt.timedelta(days=1)
    return boundary.astimezone(dt.UTC)


async def _last_scheduled_purge(db: AsyncSession) -> dt.datetime | None:
    return (
        await db.execute(
            text(
                "SELECT max(occurred_at) FROM audit_log WHERE action = :a "
                "AND detail->>'trigger' = 'scheduled'"
            ),
            {"a": audit.Action.RETENTION_PURGED},
        )
    ).scalar_one_or_none()


async def run_nightly_once(settings: Settings | None = None) -> Counts | None:
    """Purge once a night, an hour before the backup, if tonight's has not run."""
    settings = settings or get_settings()
    now = dt.datetime.now(dt.UTC)
    boundary = local_boundary(now, (settings.backup_hour - 1) % 24, settings.reporting_tz)
    async with session_scope() as db:
        last = await _last_scheduled_purge(db)
        policy = await current_policy(db)
    if last is not None and last >= boundary:
        return None
    try:
        return await purge_now(as_of=now, policy=policy, trigger="scheduled")
    except LifecycleJobRunning:
        return None
    except MaintenanceUnavailable as exc:
        # The degradation banner says so (F10.AC14); one warning per tick is enough here.
        log.warning("retention_purge_skipped", reason=str(exc))
        return None
