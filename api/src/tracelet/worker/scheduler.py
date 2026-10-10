"""Periodic work: the 90 s sweeper and the IP purge (ADR-0009).

An asyncio task inside each API worker, not a separate container. The memory
argument is ADR-0009's: a worker container costs roughly 110 MB for work measured in
milliseconds per minute.

**Exactly one worker runs each job**, because there are two and both start this loop.
Each tick takes ``pg_try_advisory_xact_lock`` on a per-job key before doing anything:
the worker that gets it runs the job, the other skips the tick. The lock is
*transaction*-scoped, so it is released by the same commit that makes the work
durable -- there is no lock to leak if a worker dies mid-job, and no long-lived
connection held just to keep one.

**The sweeper is on the correctness path, not housekeeping.** If it stops, visits are
never finalised, never inferred, never notified on. So a failed tick is logged and the
loop carries on; nothing short of cancellation stops it.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

import structlog
from sqlalchemy import text

from tracelet.analytics import rollup
from tracelet.capture import overload, service
from tracelet.db.engine import session_scope
from tracelet.inference import engine as inference
from tracelet.inference.geodb import maintenance as geodb
from tracelet.lifecycle import backups, retention
from tracelet.notify import digest, spike
from tracelet.notify import worker as notify

log = structlog.get_logger(__name__)


def _lock_key(name: str) -> int:
    """A stable 63-bit advisory-lock key per job name.

    Derived rather than hand-assigned so two jobs can never collide on a key someone
    forgot to keep unique.
    """
    digest = hashlib.sha256(f"tracelet.job.{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    every_seconds: float
    run: Callable[[], Awaitable[object]]


async def _run_exclusively(job: Job) -> bool:
    """Run ``job`` if no other worker is running it. Returns whether it ran."""
    async with session_scope() as db:
        acquired = (
            await db.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _lock_key(job.name)}
            )
        ).scalar_one()
        if not acquired:
            return False
        # The job opens its own transaction; the lock is held for the length of this
        # one, which outlives it -- so the other worker cannot start the same job
        # until this one has committed.
        await job.run()
        return True


# Jobs that keep running while this worker is overloaded (ADR-0028). The outbox carries
# alerts, which must not wait; the sweeper finalises visits. Everything else -- inference,
# rollups, updates, backups -- is off the visitor's path (NFR2.AC6) and catches up after.
RUN_UNDER_OVERLOAD: Final = frozenset({"outbox", "sweeper"})


async def _loop(job: Job, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            if job.name in RUN_UNDER_OVERLOAD or not overload.is_overloaded():
                await _run_exclusively(job)
        except Exception as exc:
            # Deliberately broad: one failed tick must never end the loop. The sweeper
            # is on the correctness path (ADR-0009).
            log.error("job_failed", job=job.name, error_type=type(exc).__name__, exc_info=exc)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=job.every_seconds)


JOBS: tuple[Job, ...] = (
    # Every 30 s claims visits older than 90 s, so an abandoned visit is finalised
    # between 90 and 120 s after it arrived.
    Job(name="sweeper", every_seconds=30, run=service.run_sweeper_once),
    # The IP TTL is days; ten minutes of slack on a 30-day clock is irrelevant, and
    # running it often keeps each batch small.
    Job(name="ip_purge", every_seconds=600, run=service.run_ip_purge_once),
    # Location inference over finalised visits (ADR-0015). Two seconds is the latency
    # between a visit ending and its location being known; nothing a visitor sees
    # waits on it.
    Job(name="infer", every_seconds=2, run=inference.run_job_once),
    # Offline geo databases (F10.AC3). Checked every six hours, fetched only when due
    # -- DB-IP monthly, the keyed vendors weekly. The asn_profiles an update makes stale
    # are rebuilt by the next job, in the quiet hour (SPEC section 11 row 34, E79).
    Job(name="geodb_update", every_seconds=6 * 3600, run=geodb.run_job_once),
    # Checked every 15 minutes; rebuilds only inside TRACELET_GEODB_PROFILES_HOUR and only
    # when the profiles were built from other database versions than those installed.
    Job(name="asn_profiles", every_seconds=900, run=geodb.run_profiles_once),
    # Analytics rollups (ADR-0016): yesterday and today every five minutes, so a visit
    # reaches the charts the same day; the last week and any never-built history daily.
    Job(name="rollup", every_seconds=300, run=rollup.run_live_once),
    Job(name="rollup_settle", every_seconds=24 * 3600, run=rollup.run_settle_once),
    # Telegram alerts from the outbox (ADR-0009, F7.AC6). Five seconds on top of the
    # two of inference: an alert arrives within seconds of the visit ending.
    Job(name="outbox", every_seconds=5, run=notify.run_job_once),
    # The M7.5 alert types (F7.AC10, F7.AC11): each does nothing while switched off. The
    # digest is due once a day and its dedup_key makes it exactly once; the spike looks at
    # the last 60 minutes, at most one alert per link per hour.
    Job(name="digest", every_seconds=300, run=digest.run_job_once),
    Job(name="spike", every_seconds=300, run=spike.run_job_once),
    # Retention (F12.AC8): checked every 15 minutes, purges once a night, an hour before
    # the backup. Every job also runs when a worker starts, and workers recycle, so the
    # nightly ones decide for themselves whether they are due.
    Job(name="retention", every_seconds=900, run=retention.run_nightly_once),
    # The nightly backup and the monthly restore check (ADR-0014, ADR-0022), on the same
    # 15-minute check: each runs when it is due and not before.
    Job(name="backups", every_seconds=900, run=backups.run_scheduled_once),
)


class Scheduler:
    """Owns the job loops for one worker process."""

    def __init__(self, jobs: tuple[Job, ...] = JOBS) -> None:
        self._jobs = jobs
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(_loop(job, self._stop), name=f"job:{job.name}")
            for job in self._jobs
        ]
        log.info("scheduler_started", jobs=[job.name for job in self._jobs])

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
