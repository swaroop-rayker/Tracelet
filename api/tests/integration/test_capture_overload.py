"""A bounded redirect against the real database (ADR-0028, CLAUDE.md invariant 1).

M9's load test left 500 visitors un-redirected on the e2-micro, waiting on a capture that
needed the database. These prove the three ways a capture is now bounded: answered from
memory when the worker is overloaded (with the database unreachable, to show it is not
touched), answered at the deadline while the visit still records behind it, and still
answered by the database when the cache cannot say.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from tracelet.capture import overload, service
from tracelet.capture import router as capture_router
from tracelet.capture.models import Visit, VisitStage
from tracelet.config import Settings
from tracelet.db.engine import session_scope

from . import capture_helpers as ch


@pytest.fixture(autouse=True)
def _fresh_state() -> Iterator[None]:
    overload.reset_for_tests()
    saved = (service.link_cache.entries, service.link_cache.default)
    yield
    overload.reset_for_tests()
    service.link_cache.entries, service.link_cache.default = saved


@pytest.fixture
async def drained() -> AsyncIterator[None]:
    """Captures left recording behind a deadline finish before the test's teardown."""
    yield
    if capture_router._behind:
        await asyncio.gather(*capture_router._behind)


def _deadline(db_app: object, settings: Settings, ms: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """The app's settings with a short capture deadline, for this test only."""
    fast = settings.model_copy(update={"capture_deadline_ms": ms})
    monkeypatch.setattr(db_app.state, "settings", fast)  # type: ignore[attr-defined]  # the fixture types the app as object


def _slow_capture(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    real = service.capture

    async def slow(
        settings: Settings, slug: str | None, facts: service.RequestFacts
    ) -> service.CaptureResult:
        await asyncio.sleep(seconds)
        return await real(settings, slug, facts)

    monkeypatch.setattr(service, "capture", slow)


class _Unreachable:
    async def __aenter__(self) -> None:
        raise ConnectionRefusedError("database unreachable (test)")

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_an_overloaded_worker_redirects_without_the_database(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = await ch.create_link()
    await service.refresh_link_cache()
    monkeypatch.setattr(overload, "evaluate", lambda _settings: True)
    monkeypatch.setattr(service, "session_scope", _Unreachable)

    response = await ch.visit(db_client, link.slug)

    assert response.status_code == 302
    assert response.headers["location"] == ch.DESTINATION
    assert overload.current().buffered == 1

    monkeypatch.undo()
    assert await overload.flush() == 1
    async with session_scope() as db:
        (row,) = (await db.execute(select(Visit).where(Visit.link_id == link.id))).scalars()
    assert row.stage is VisitStage.RATE_LIMITED
    assert str(row.ip_prefix) == ch.VISITOR_PREFIX
    assert row.ip_hmac is None and row.ip_enc is None  # nothing beyond the minimal row
    assert row.finalized_at is not None


async def test_overloaded_a_link_the_cache_does_not_know_falls_back_to_the_database(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link newer than the cache's last load (or a worker that has just started): a 404
    from memory would strand a visitor of a live link; the database, bounded, decides.
    Found by the memory-pressure degradation test, ERRORS E82."""
    await service.refresh_link_cache()
    link = await ch.create_link()  # after the load
    monkeypatch.setattr(overload, "evaluate", lambda _settings: True)

    response = await ch.visit(db_client, link.slug)

    assert response.status_code == 200  # the database's answer: the capture page
    assert ch.DESTINATION in response.text
    assert overload.current().buffered == 0


async def test_an_overloaded_worker_still_404s_an_unknown_slug(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await service.refresh_link_cache()
    monkeypatch.setattr(overload, "evaluate", lambda _settings: True)
    response = await ch.visit(db_client, ch.new_slug())
    assert response.status_code == 404
    assert overload.current().buffered == 0


@pytest.mark.usefixtures("drained")
async def test_a_slow_capture_is_answered_at_the_deadline_and_still_recorded(
    db_app: object,
    db_client: AsyncClient,
    integration_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    link = await ch.create_link()
    await service.refresh_link_cache()
    _deadline(db_app, integration_settings, 200, monkeypatch)
    _slow_capture(monkeypatch, 0.6)

    started = time.monotonic()
    response = await ch.visit(db_client, link.slug)
    elapsed = time.monotonic() - started

    assert response.status_code == 302
    assert response.headers["location"] == ch.DESTINATION
    assert elapsed < 0.55, f"answered after {elapsed:.2f} s, not at the deadline"
    await asyncio.gather(*capture_router._behind)
    assert await ch.visit_count(link.id) == 1
    visit = await ch.latest_visit(link.id)
    assert visit.stage is not VisitStage.RATE_LIMITED  # the full capture, recorded behind
    assert overload.current().inflight == 0


@pytest.mark.usefixtures("drained")
async def test_past_the_deadline_a_link_the_cache_does_not_know_waits_for_the_database(
    db_app: object,
    db_client: AsyncClient,
    integration_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await service.refresh_link_cache()
    link = await ch.create_link()  # newer than the cache's last load
    _deadline(db_app, integration_settings, 100, monkeypatch)
    _slow_capture(monkeypatch, 0.3)

    response = await ch.visit(db_client, link.slug)

    assert response.status_code == 200  # the database's answer: the capture page
    assert ch.DESTINATION in response.text


@pytest.mark.usefixtures("db_app")  # the engine
async def test_the_link_cache_loads_live_links_and_the_default_not_archived_ones() -> None:
    live = await ch.create_link()
    archived = await ch.create_link(archived=True)
    async with session_scope() as db:
        current_default = (
            await db.execute(
                text("SELECT slug FROM links WHERE is_default AND archived_at IS NULL")
            )
        ).scalar_one_or_none()

    count = await service.refresh_link_cache()

    assert count >= 1
    assert service.link_cache.get(live.slug) is not None
    assert service.link_cache.get(archived.slug) is None
    cached_default = service.link_cache.default
    assert (cached_default.slug if cached_default else None) == current_default
