"""Overload detection, the shed buffer and the answer from memory (ADR-0028).

M9's load test left 500 visitors un-redirected on the e2-micro. These pin the pieces that
keep the redirect bounded: when a worker counts as overloaded and when it recovers, what a
shed visit keeps (and never keeps), and what the link cache can answer on its own.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import uuid
from collections.abc import Iterator

import pytest

from tracelet.capture import overload, service
from tracelet.capture.models import VisitStage
from tracelet.config import Settings
from tracelet.health import pressure
from tracelet.net import ClientAddress
from tracelet.worker import scheduler

SETTINGS = Settings(overload_lag_ms=250, overload_inflight=8, overload_buffer=3)
LINK = service.LinkSnapshot(
    id=uuid.uuid4(),
    slug="spring-sale",
    destination_url="https://example.com/sale",
    interstitial_ms=700,
    ask_location=False,
    is_live=True,
)
FACTS = service.RequestFacts(
    client=ClientAddress(ip="49.207.12.34", edge_verified=False, forged_edge_header=False),
    user_agent="Go-http-client/1.1",
    ch_platform=None,
    ch_mobile=None,
    raw_headers=(),
    query={},
    referer=None,
    cf_ray=None,
    cf_ipcountry=None,
    http_version=None,
    tls_version=None,
    trace_id="01M4TEST0000000000000000AB",
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    overload.reset_for_tests()
    overload.configure(SETTINGS)
    monkeypatch.setattr(pressure, "shedding", lambda _settings: False)
    saved = (service.link_cache.entries, service.link_cache.default, service.link_cache.loaded)
    service.link_cache.entries, service.link_cache.default, service.link_cache.loaded = (
        {},
        None,
        False,
    )
    yield
    overload.reset_for_tests()
    service.link_cache.entries, service.link_cache.default, service.link_cache.loaded = saved


# --- when a worker is overloaded --------------------------------------------------------


def test_a_quiet_worker_is_not_overloaded() -> None:
    assert overload.evaluate(SETTINGS) is False


def test_a_lagging_event_loop_overloads_and_recovers_only_well_clear() -> None:
    overload._state.lag_ms = 300
    assert overload.evaluate(SETTINGS) is True
    assert overload.current().reason == "event_loop_lag"
    overload._state.lag_ms = 200  # under the limit, but not under half of it
    assert overload.evaluate(SETTINGS) is True
    overload._state.lag_ms = 100
    assert overload.evaluate(SETTINGS) is False


def test_too_many_captures_in_flight_overload() -> None:
    for _ in range(9):
        overload.started()
    assert overload.evaluate(SETTINGS) is True
    assert overload.current().reason == "captures_in_flight"
    for _ in range(4):
        overload.finished()
    assert overload.evaluate(SETTINGS) is True  # 5 left, more than half the cap of 8
    overload.finished()
    assert overload.evaluate(SETTINGS) is False


def test_memory_pressure_overloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pressure, "shedding", lambda _settings: True)
    assert overload.evaluate(SETTINGS) is True
    assert overload.current().reason == "memory_pressure"


def test_a_failing_memory_signal_never_fails_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_settings: Settings) -> bool:
        raise OSError("no /proc")

    monkeypatch.setattr(pressure, "shedding", broken)
    assert overload.evaluate(SETTINGS) is False


def test_finished_never_goes_below_zero() -> None:
    overload.finished()
    assert overload.current().inflight == 0


# --- what a shed visit keeps ------------------------------------------------------------


def test_a_shed_row_is_minimal_and_never_holds_the_address() -> None:
    overload.record_shed(
        link_id=LINK.id,
        ip_prefix="49.207.12.0/24",
        trace_id="01M4TEST0000000000000000AB",
        classifier_version=service.CLASSIFIER_VERSION,
    )
    (row,) = overload._state.buffer
    assert row["stage"] is VisitStage.RATE_LIMITED
    assert row["link_id"] == LINK.id
    assert row["ip_prefix"] == "49.207.12.0/24"
    assert isinstance(row["occurred_at"], dt.datetime) and row["occurred_at"].tzinfo is not None
    assert "49.207.12.34" not in repr(row)  # CLAUDE.md invariant 4
    assert set(row) == {
        "id",
        "link_id",
        "occurred_at",
        "finalized_at",
        "stage",
        "trace_id",
        "classifier_version",
        "ip_prefix",
    }


def test_the_buffer_is_bounded_and_counts_what_it_drops() -> None:
    for _ in range(5):
        overload.record_shed(link_id=LINK.id, ip_prefix=None, trace_id=None, classifier_version="x")
    now = overload.current()
    assert (now.buffered, now.dropped, now.answered) == (3, 2, 5)


# --- what the cache can answer alone ----------------------------------------------------


def test_before_the_cache_has_loaded_an_unknown_slug_cannot_be_answered() -> None:
    assert service.from_memory("spring-sale", FACTS, record=True).outcome is (
        service.Outcome.UNAVAILABLE
    )


def test_after_it_has_loaded_an_unknown_slug_is_a_404() -> None:
    service.link_cache.replace([LINK], None)
    assert service.from_memory("other-link", FACTS, record=True).outcome is (
        service.Outcome.NOT_FOUND
    )


def test_a_known_live_link_is_answered_and_its_visit_kept() -> None:
    service.link_cache.replace([LINK], None)
    result = service.from_memory("Spring-Sale", FACTS, record=True)
    assert result.outcome is service.Outcome.FROM_MEMORY
    assert result.link == LINK
    assert overload.current().buffered == 1


def test_behind_a_deadline_nothing_is_buffered() -> None:
    """The capture still running records the visit itself; a second row would double it."""
    service.link_cache.replace([LINK], None)
    assert service.from_memory("spring-sale", FACTS, record=False).outcome is (
        service.Outcome.FROM_MEMORY
    )
    assert overload.current().buffered == 0


def test_an_inactive_link_is_a_404_from_memory_too() -> None:
    service.link_cache.replace([dataclasses.replace(LINK, is_live=False)], None)
    assert service.from_memory("spring-sale", FACTS, record=True).outcome is (
        service.Outcome.NOT_FOUND
    )


def test_the_bare_path_uses_the_cached_default() -> None:
    service.link_cache.replace([LINK], LINK)
    assert service.from_memory(None, FACTS, record=True).link == LINK


def test_a_malformed_slug_is_a_404_without_looking() -> None:
    service.link_cache.replace([LINK], None)
    assert service.from_memory("../etc", FACTS, record=True).outcome is service.Outcome.NOT_FOUND


# --- background work yields ---------------------------------------------------------------


async def test_an_overloaded_worker_runs_only_the_outbox_and_the_sweeper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[str] = []

    async def record(job: scheduler.Job) -> bool:
        ran.append(job.name)
        return True

    monkeypatch.setattr(scheduler, "_run_exclusively", record)
    overload._state.overloaded = True

    async def nothing() -> None:
        return None

    for name in ("infer", "rollup", "outbox", "sweeper", "backups"):
        stop = asyncio.Event()
        job = scheduler.Job(name=name, every_seconds=0.01, run=nothing)
        task = asyncio.create_task(scheduler._loop(job, stop))
        await asyncio.sleep(0.03)
        stop.set()
        await task
    assert set(ran) == {"outbox", "sweeper"}
