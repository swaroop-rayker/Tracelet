"""F15.AC6, the graceful-degradation matrix: one test per row, and **each still redirects**.

M7's checklist line. Every row breaks one dependency, then asks the capture path for a
visit: the visitor must be sent on to the destination (CLAUDE.md invariant 1). Then the
row's own degraded behaviour is checked -- and, where System Health owes a signal, the
banner (F10.AC14). Several rows' behaviour is proved in depth by an earlier milestone's
tests; this file is the matrix in one place, with the redirect asserted for every row.

| Row | Earlier, in depth |
|---|---|
| database unreachable | test_capture_path (M2) |
| external geo API timing out | test_inference_outbound (M3) |
| Nominatim unavailable | test_inference_outbound (M3) |
| Telegram unavailable | test_outbox (M6) |
| client JavaScript blocked | test_capture_path, sweeper (M2) |
| geolocation denied | test_enrichment (M2), test_inference_engine (M3) |
"""

from __future__ import annotations

import uuid
from collections import namedtuple
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import psutil
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text

from tests.integration import capture_helpers as ch
from tests.integration.helpers import SignedIn
from tracelet.capture import service
from tracelet.capture.models import VisitStage
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.health import pressure
from tracelet.inference import engine, nominatim, outbound
from tracelet.inference.sources import external
from tracelet.notify import telegram, worker

pytestmark = pytest.mark.integration


def _sent_on(response: Response) -> None:
    """The visitor reaches the destination: the capture page (which forwards) or a 302."""
    if response.status_code == 302:
        assert response.headers["location"] == ch.DESTINATION
    else:
        assert response.status_code in (200, 503), response.status_code
        assert (
            f'content="0;url={ch.DESTINATION}"' in response.text or ch.DESTINATION in response.text
        )


async def _conditions(owner: SignedIn) -> dict[str, dict[str, Any]]:
    body = (await owner.client.get("/api/v1/health/degradation")).json()
    return {c["key"]: c for c in body["conditions"]}


@pytest.fixture
def integration_settings(integration_settings: Settings) -> Settings:
    """Shedding on, as in production; ``_calm_host`` says the host is calm unless a test
    says otherwise."""
    return integration_settings.model_copy(update={"shed_memory_pressure": 20.0})


@pytest.fixture(autouse=True)
def _calm_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pressure, "read_psi", lambda _proc: 0.0)
    pressure.reset_for_tests()


@pytest.fixture
def failing_http(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """ipwho.is times out and Nominatim answers 503: both third parties are down."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ipwho.is":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(external, "_client", client)
    monkeypatch.setattr(nominatim, "_client", client)
    nominatim.reset_for_tests()
    outbound.IPWHOIS_BREAKER.success()
    outbound.NOMINATIM_BREAKER.success()
    yield
    outbound.IPWHOIS_BREAKER.success()
    outbound.NOMINATIM_BREAKER.success()


@pytest.fixture(autouse=True)
async def _clean(db_app: object) -> AsyncIterator[None]:
    del db_app
    async with session_scope() as db:
        high_water = (
            await db.execute(text("SELECT coalesce(max(id), 0) FROM outbox"))
        ).scalar_one()
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM outbox WHERE id > :h"), {"h": high_water})
        await db.execute(text("DELETE FROM rate_limit_buckets WHERE key LIKE 'breaker:%'"))
        await db.execute(text("DELETE FROM rate_limit_buckets WHERE key LIKE 'out:%'"))
        await db.execute(
            text("DELETE FROM geo_cache WHERE ip_prefix = :p"), {"p": ch.VISITOR_PREFIX}
        )
    pressure.reset_for_tests()


async def _enriched(
    client: AsyncClient, *, geolocation: dict[str, Any]
) -> tuple[Response, uuid.UUID]:
    link = await ch.create_link()
    page = await ch.visit(client, link.slug)
    await client.post(
        f"/api/v1/s/{ch.nonce_from(page)}",
        json={"geolocation": geolocation, "locale": {"tzIana": "Asia/Kolkata"}},
        headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP},
    )
    return page, (await ch.latest_visit(link.id)).id


# --- 1. database unreachable ------------------------------------------------------------


class _Unreachable:
    async def __aenter__(self) -> None:
        raise OSError("connection refused")

    async def __aexit__(self, *_: object) -> None:
        return None


async def test_database_unreachable_still_redirects(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = await ch.create_link()
    await ch.visit(db_client, link.slug)  # the link is cached
    monkeypatch.setattr(service, "session_scope", _Unreachable)
    _sent_on(await ch.visit(db_client, link.slug))


# --- 2. an offline geo database missing or corrupt ------------------------------------------


async def test_a_missing_geo_database_skips_its_source_and_raises_the_fault(
    db_client: AsyncClient, owner: SignedIn, integration_settings: Settings
) -> None:
    # The suite installs no geo databases: every offline source is missing.
    page, visit_id = await _enriched(db_client, geolocation={"state": "prompt"})
    _sent_on(page)
    await engine.run_once(integration_settings, only=[visit_id])
    visit = await ch.get_visit(visit_id)
    assert visit.inferred_at is not None, "the other sources continued"
    missing = {
        s["detail"]["source"]
        for s in visit.signals
        if s["detail"].get("reason") == "database_not_installed"
    }
    assert {"geolite2", "dbip"} <= missing
    conditions = await _conditions(owner)
    assert any(key.startswith("geodb:dbip") for key in conditions), "System Health raises it"


# --- 3. an external geo API timing out, and 4. Nominatim unavailable ---------------------------


async def test_third_parties_down_open_the_breaker_and_inference_carries_on(
    db_client: AsyncClient, owner: SignedIn, integration_settings: Settings, failing_http: None
) -> None:
    del failing_http
    live = integration_settings.model_copy(update={"external_geo_enabled": True})
    for _ in range(outbound.IPWHOIS_BREAKER.threshold):
        async with session_scope() as db:
            await db.execute(
                text("DELETE FROM geo_cache WHERE ip_prefix = :p"), {"p": ch.VISITOR_PREFIX}
            )
        page, visit_id = await _enriched(
            db_client,
            geolocation={"state": "granted", "lat": 12.975, "lng": 77.60, "accuracyM": 20},
        )
        _sent_on(page)
        await engine.run_once(live, only=[visit_id])
    visit = await ch.get_visit(visit_id)
    assert visit.inferred_at is not None
    assert visit.resolved_address is None, "no street address; coarser levels unaffected"
    assert outbound.IPWHOIS_BREAKER.state == "open"
    assert "breaker:ipwhois" in await _conditions(owner)


# --- 5. Telegram unavailable --------------------------------------------------------------------


async def test_telegram_down_keeps_the_alert_queued_for_a_retry(
    db_client: AsyncClient, integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = await ch.create_link()
    _sent_on(await ch.visit(db_client, link.slug))
    async with session_scope() as db:
        row_id = (
            await db.execute(
                text(
                    "INSERT INTO outbox (kind, priority, payload) VALUES "
                    "('telegram.visit_alert', 'normal', '{\"visit_id\": \"x\"}'::jsonb) RETURNING id"
                )
            )
        ).scalar_one()

    async def down(**_: Any) -> telegram.SendResult:
        raise telegram.TelegramError("HTTP 502: bad gateway")

    monkeypatch.setattr(telegram, "send_message", down)
    settings = integration_settings.model_copy(update={"telegram_owner_chat_id": 4242})
    await worker.run_once(settings, only=[row_id])
    async with session_scope() as db:
        status, attempts = (
            await db.execute(
                text("SELECT status, attempts FROM outbox WHERE id = :i"), {"i": row_id}
            )
        ).one()
    assert (status, attempts) == ("failed", 1), "retried later; nothing is lost"


# --- 6. client JavaScript blocked ---------------------------------------------------------------


async def test_javascript_blocked_is_captured_server_side_and_swept(db_client: AsyncClient) -> None:
    link = await ch.create_link()
    _sent_on(await ch.visit(db_client, link.slug))  # no enrichment post ever arrives
    visit = await ch.latest_visit(link.id)
    await ch.backdate(visit.id, seconds=120)
    await service.run_sweeper_once()
    swept = await ch.get_visit(visit.id)
    assert swept.stage is VisitStage.SERVER_ONLY
    assert swept.finalized_at is not None


# --- 7. geolocation denied ----------------------------------------------------------------------


async def test_geolocation_denied_has_no_point(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    page, visit_id = await _enriched(db_client, geolocation={"state": "denied"})
    _sent_on(page)
    await engine.run_once(integration_settings, only=[visit_id])
    visit = await ch.get_visit(visit_id)
    assert visit.consent_state.value == "denied"
    assert visit.gps_lat is None and visit.strict_lat is None, "city-level ceiling (F4.AC3)"


# --- 8. disk nearly full ------------------------------------------------------------------------


async def test_disk_nearly_full_keeps_capturing_and_raises_the_banner(
    db_client: AsyncClient, owner: SignedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = namedtuple("usage", "total used free percent")
    monkeypatch.setattr(psutil, "disk_usage", lambda _p: usage(100, 96, 4, 96.0))
    link = await ch.create_link()
    _sent_on(await ch.visit(db_client, link.slug))
    assert (await ch.latest_visit(link.id)).stage is not VisitStage.RATE_LIMITED, "still captured"
    disk = (await _conditions(owner))["disk"]
    assert disk["severity"] == "critical"
    # Purges still run, and free space.
    preview = await owner.client.post("/api/v1/health/retention/preview", headers=owner.headers())
    assert preview.status_code == 200


# --- 9. swap thrashing --------------------------------------------------------------------------


async def test_memory_pressure_sheds_capture_and_still_redirects(
    db_client: AsyncClient, owner: SignedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = await ch.create_link()
    monkeypatch.setattr(pressure, "read_psi", lambda _proc: 45.0)
    pressure.reset_for_tests()

    shed = await ch.visit(db_client, link.slug)
    _sent_on(shed)
    assert shed.status_code == 302, "redirected at once, nothing captured"
    stored = await ch.latest_visit(link.id)
    assert stored.stage is VisitStage.RATE_LIMITED, "recorded, so the shedding is visible"
    shedding = (await _conditions(owner))["shedding"]
    assert shedding["severity"] == "critical"
    assert "redirected" in shedding["still_works"]

    # Pressure gone: capture resumes (after the two-second re-read).
    monkeypatch.setattr(pressure, "read_psi", lambda _proc: 0.0)
    pressure.reset_for_tests()
    resumed = await ch.visit(db_client, link.slug)
    assert resumed.status_code == 200
