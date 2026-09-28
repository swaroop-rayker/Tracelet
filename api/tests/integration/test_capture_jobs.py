"""The sweeper, the IP purge and the scheduler, against the real database.

The sweeper is on the correctness path (ADR-0009): if it stops, visits are never
finalised, never inferred, never notified on. The purge is a privacy promise with a
date on it (F12.AC2). Both are tested by moving rows into the past rather than
waiting, and the scheduler's one-worker guarantee is tested with real concurrency.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError

from tests.integration import capture_helpers as ch
from tracelet.capture import service
from tracelet.capture.models import Visit, VisitStage
from tracelet.db.engine import session_scope
from tracelet.worker import scheduler

pytestmark = pytest.mark.integration


async def _pending(client: AsyncClient) -> Visit:
    link = await ch.create_link()
    await ch.visit(client, link.slug)
    return await ch.latest_visit(link.id)


# ---------------------------------------------------------------------------
# Sweeper (F2.AC7)
# ---------------------------------------------------------------------------


async def test_the_sweeper_finalises_an_abandoned_visit_after_90_seconds(
    db_client: AsyncClient,
) -> None:
    """The M2 done-check: "sweeper finalises an abandoned visit at 90 s as
    ``server_only``"."""
    visit = await _pending(db_client)
    await ch.backdate(visit.id, seconds=91)

    async with session_scope() as db:
        swept = await service.sweep(db)

    assert swept >= 1
    finalised = await ch.get_visit(visit.id)
    assert finalised.stage is VisitStage.SERVER_ONLY
    assert finalised.finalized_at is not None


async def test_the_sweeper_leaves_a_recent_visit_alone(db_client: AsyncClient) -> None:
    """Under 90 s, the enrichment may still be on its way."""
    visit = await _pending(db_client)
    await ch.backdate(visit.id, seconds=60)

    async with session_scope() as db:
        await service.sweep(db)

    assert (await ch.get_visit(visit.id)).stage is VisitStage.SERVER


async def test_the_sweeper_never_touches_a_finalised_visit(db_client: AsyncClient) -> None:
    visit = await _pending(db_client)
    await ch.backdate(visit.id, seconds=91)
    async with session_scope() as db:
        await service.sweep(db)
    first = await ch.get_visit(visit.id)

    async with session_scope() as db:
        await service.sweep(db)

    assert (await ch.get_visit(visit.id)).finalized_at == first.finalized_at


async def test_the_sweeper_skips_rate_limited_rows(db_client: AsyncClient) -> None:
    """They are finalised at birth; there is nothing to wait for."""
    link = await ch.create_link()
    for _ in range(11):
        await ch.visit(db_client, link.slug)
    limited = await ch.latest_visit(link.id)
    assert limited.stage is VisitStage.RATE_LIMITED
    await ch.backdate(limited.id, seconds=600)

    async with session_scope() as db:
        await service.sweep(db)

    assert (await ch.get_visit(limited.id)).stage is VisitStage.RATE_LIMITED


async def test_a_server_stage_visit_cannot_be_finalised_without_leaving_server(
    db_client: AsyncClient,
) -> None:
    """The CHECK that keeps the sweeper's index and its UPDATE in agreement: 'server'
    means unfinalised, and nothing else."""
    visit = await _pending(db_client)
    with pytest.raises(IntegrityError, match="ck_visits_server_stage_is_unfinalized"):
        async with session_scope() as db:
            await db.execute(
                update(Visit).where(Visit.id == visit.id).values(finalized_at=Visit.occurred_at)
            )


async def test_stuck_visits_are_counted(db_client: AsyncClient) -> None:
    """Non-zero means the sweeper is not running -- and since it is on the correctness
    path, that means visits are silently not being finalised."""
    visit = await _pending(db_client)
    await ch.backdate(visit.id, seconds=11 * 60)

    async with session_scope() as db:
        before = await service.stuck_visits(db)
        await service.sweep(db)
        after = await service.stuck_visits(db)

    assert before >= 1
    assert after == before - 1


# ---------------------------------------------------------------------------
# IP purge (F12.AC2)
# ---------------------------------------------------------------------------


async def test_the_purge_nulls_the_ciphertext_and_keeps_the_durable_forms(
    db_client: AsyncClient,
) -> None:
    """The M2 done-check: "IP purge nulls ``ip_enc`` and leaves ``ip_hmac``/``ip_prefix``
    intact". The purge removes the ability to identify an address, not the ability to
    reason about networks (ADR-0007)."""
    visit = await _pending(db_client)
    async with session_scope() as db:
        await db.execute(
            text("UPDATE visits SET ip_purge_after = now() - interval '1 second' WHERE id = :id"),
            {"id": visit.id},
        )

    async with session_scope() as db:
        purged = await service.purge_expired_ips(db)

    assert purged >= 1
    after = await ch.get_visit(visit.id)
    assert after.ip_enc is None
    assert after.ip_key_version is None
    assert after.ip_hmac == visit.ip_hmac
    assert str(after.ip_prefix) == str(visit.ip_prefix)


async def test_the_purge_leaves_an_address_inside_its_ttl(db_client: AsyncClient) -> None:
    visit = await _pending(db_client)
    async with session_scope() as db:
        await service.purge_expired_ips(db)
    assert (await ch.get_visit(visit.id)).ip_enc is not None


async def test_a_ciphertext_cannot_outlive_its_key_version(db_client: AsyncClient) -> None:
    """The CHECK pairing ``ip_enc`` with ``ip_key_version``: a purge that nulled one and
    not the other would leave a key version pointing at nothing."""
    visit = await _pending(db_client)
    with pytest.raises(IntegrityError, match="ck_visits_ip_enc_has_key_version"):
        async with session_scope() as db:
            await db.execute(update(Visit).where(Visit.id == visit.id).values(ip_enc=None))


# ---------------------------------------------------------------------------
# Scheduler (ADR-0009)
# ---------------------------------------------------------------------------


async def test_only_one_worker_runs_a_job_at_a_time(db_app: object) -> None:
    """Two workers both start the loop; the advisory lock makes exactly one run each
    tick. Tested with real concurrency: the job holds the lock while it sleeps."""
    del db_app
    runs = 0

    async def slow() -> None:
        nonlocal runs
        runs += 1
        await asyncio.sleep(0.3)

    job = scheduler.Job(name="test-exclusive", every_seconds=60, run=slow)
    results = await asyncio.gather(*(scheduler._run_exclusively(job) for _ in range(3)))

    assert runs == 1
    assert sorted(results) == [False, False, True]


async def test_the_lock_is_released_when_the_job_finishes(db_app: object) -> None:
    """Transaction-scoped, so there is no lock to leak."""
    del db_app

    async def quick() -> None:
        return None

    job = scheduler.Job(name="test-release", every_seconds=60, run=quick)
    assert await scheduler._run_exclusively(job) is True
    assert await scheduler._run_exclusively(job) is True


async def test_a_failing_job_does_not_stop_the_loop(db_app: object) -> None:
    """One failed tick must never end the sweeper."""
    del db_app
    calls = 0

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            msg = "transient"
            raise RuntimeError(msg)

    job = scheduler.Job(name="test-flaky", every_seconds=0.05, run=flaky)
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler._loop(job, stop))
    await asyncio.sleep(0.4)
    stop.set()
    await task

    assert calls >= 2, "the loop kept going after the first failure"


def test_job_lock_keys_are_stable_and_distinct() -> None:
    names = [job.name for job in scheduler.JOBS]
    keys = [scheduler._lock_key(name) for name in names]
    assert len(set(keys)) == len(keys)
    assert scheduler._lock_key("sweeper") == scheduler._lock_key("sweeper")
    assert all(0 < key < 2**63 for key in keys)
