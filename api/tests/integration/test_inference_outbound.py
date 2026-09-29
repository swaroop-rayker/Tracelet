"""S9 and Nominatim against the real database, with HTTP intercepted (F4.AC7, F4.AC4).

Covers the M3 done-checks "external API timeout opens the breaker; inference completes on
remaining sources" and "consented visit resolves street address; non-consented stores
no coordinates". Nothing here reaches a real third party.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.integration import capture_helpers as ch
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.inference import engine, nominatim, outbound
from tracelet.inference.sources import external, rdns

pytestmark = pytest.mark.integration

IPWHOIS_BENGALURU = {
    "success": True,
    "country_code": "IN",
    "region": "Karnataka",
    "city": "Bengaluru",
    "latitude": 12.97,
    "longitude": 77.59,
}


@pytest.fixture
def live(integration_settings: Settings) -> Settings:
    return integration_settings.model_copy(update={"external_geo_enabled": True})


Responder = Callable[[httpx.Request], httpx.Response]


@dataclass
class Calls:
    """What the intercepted transport saw, and how it answers each service."""

    requests: list[httpx.Request] = field(default_factory=list)
    ipwhois: Responder = lambda _r: httpx.Response(200, json=IPWHOIS_BENGALURU)
    nominatim: Responder = lambda _r: httpx.Response(404)

    def to(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[Calls]:
    """Every outbound request, intercepted; plus a resolver that answers PTR."""
    seen = Calls()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.requests.append(request)
        return (seen.ipwhois if request.url.host == "ipwho.is" else seen.nominatim)(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(external, "_client", client)
    monkeypatch.setattr(nominatim, "_client", client)

    async def lookup(ip: str, timeout_s: float) -> str | None:
        del timeout_s
        return rdns.CANARY[1] if ip == rdns.CANARY[0] else None

    rdns.reset_canary_for_tests()
    monkeypatch.setattr(rdns, "_lookup", lookup)
    nominatim.reset_for_tests()
    outbound.IPWHOIS_BREAKER.success()
    outbound.NOMINATIM_BREAKER.success()
    yield seen
    outbound.IPWHOIS_BREAKER.success()
    outbound.NOMINATIM_BREAKER.success()
    rdns.reset_canary_for_tests()


@pytest.fixture(autouse=True)
async def _clear_cache_and_budgets(db_app: object) -> None:
    del db_app
    async with session_scope() as db:
        await db.execute(
            text("DELETE FROM geo_cache WHERE ip_prefix = :p"), {"p": ch.VISITOR_PREFIX}
        )
        await db.execute(text("DELETE FROM rate_limit_buckets WHERE key LIKE 'out:%'"))


async def _visit(client: AsyncClient, *, gps: tuple[float, float] | None = None) -> uuid.UUID:
    link = await ch.create_link()
    page = await ch.visit(client, link.slug)
    geo: dict[str, Any] = {"state": "prompt"}
    if gps is not None:
        geo = {"state": "granted", "lat": gps[0], "lng": gps[1], "accuracyM": 20}
    await client.post(
        f"/api/v1/s/{ch.nonce_from(page)}",
        json={"geolocation": geo, "locale": {"tzIana": "Asia/Kolkata"}},
        headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP},
    )
    return (await ch.latest_visit(link.id)).id


async def _candidate_sources(visit_id: uuid.UUID) -> set[str]:
    async with session_scope() as db:
        rows = await db.execute(
            text("SELECT source::text FROM visit_candidates WHERE visit_id = :v"), {"v": visit_id}
        )
        return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# S9
# ---------------------------------------------------------------------------


async def test_s9_asks_about_the_network_never_the_visitor(
    db_client: AsyncClient, live: Settings, calls: Calls
) -> None:
    visit_id = await _visit(db_client)
    await engine.run_once(live, only=[visit_id])

    (request,) = calls.to("ipwho.is")
    assert request.url.path == "/49.207.12.0", "the prefix's network address"
    assert ch.VISITOR_IP not in str(request.url)
    assert "external_api" in await _candidate_sources(visit_id)


async def test_a_second_visitor_from_the_same_network_costs_no_request(
    db_client: AsyncClient, live: Settings, calls: Calls
) -> None:
    """F4.AC7: cached by prefix, so a repeat network triggers no outbound call."""
    first = await _visit(db_client)
    await engine.run_once(live, only=[first])
    second = await _visit(db_client)
    await engine.run_once(live, only=[second])

    assert len(calls.to("ipwho.is")) == 1
    assert "external_api" in await _candidate_sources(second)


async def test_timeouts_open_the_breaker_and_inference_completes_without_it(
    db_client: AsyncClient, live: Settings, calls: Calls
) -> None:
    """The M3 done-check. Each failing call costs one attempt until the breaker opens;
    after that no request is made at all, and every visit is still inferred."""

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    calls.ipwhois = timeout
    visits = []
    for _ in range(outbound.IPWHOIS_BREAKER.threshold + 2):
        async with session_scope() as db:
            await db.execute(
                text("DELETE FROM geo_cache WHERE ip_prefix = :p"), {"p": ch.VISITOR_PREFIX}
            )
        visit_id = await _visit(db_client)
        await engine.run_once(live, only=[visit_id])
        visits.append(visit_id)

    assert outbound.IPWHOIS_BREAKER.state == "open"
    assert len(calls.to("ipwho.is")) == outbound.IPWHOIS_BREAKER.threshold
    last = await ch.get_visit(visits[-1])
    assert last.inferred_at is not None
    reasons = {
        s["detail"].get("reason")
        for s in last.signals
        if s["detail"].get("source") == "external_api"
    }
    assert reasons == {"circuit_open"}


async def test_the_operator_switch_stops_every_external_call(
    db_client: AsyncClient, integration_settings: Settings, calls: Calls
) -> None:
    visit_id = await _visit(db_client, gps=(12.975, 77.60))
    await engine.run_once(integration_settings, only=[visit_id])  # switch off

    assert calls.requests == []
    visit = await ch.get_visit(visit_id)
    assert visit.resolved_address is None


# ---------------------------------------------------------------------------
# Nominatim
# ---------------------------------------------------------------------------


async def test_a_consented_visit_gets_a_street_address(
    db_client: AsyncClient, live: Settings, calls: Calls
) -> None:
    calls.nominatim = lambda _r: httpx.Response(
        200, json={"display_name": "MG Road, Bengaluru, Karnataka, 560001, India"}
    )
    visit_id = await _visit(db_client, gps=(12.975, 77.60))
    await engine.run_once(live, only=[visit_id])

    visit = await ch.get_visit(visit_id)
    assert visit.resolved_address == "MG Road, Bengaluru, Karnataka, 560001, India"
    (request,) = calls.to("nominatim.openstreetmap.org")
    assert request.headers["user-agent"].startswith("Tracelet/")
    assert request.url.params["lat"] == "12.975000"


async def test_a_visit_without_consent_is_never_sent_to_nominatim(
    db_client: AsyncClient, live: Settings, calls: Calls
) -> None:
    visit_id = await _visit(db_client)
    await engine.run_once(live, only=[visit_id])

    assert calls.to("nominatim.openstreetmap.org") == []
    visit = await ch.get_visit(visit_id)
    assert visit.resolved_address is None and visit.gps_lat is None


async def test_nominatim_failing_leaves_the_visit_located_and_says_why(
    db_client: AsyncClient, live: Settings, calls: Calls
) -> None:
    calls.nominatim = lambda _r: httpx.Response(503)
    visit_id = await _visit(db_client, gps=(12.975, 77.60))
    await engine.run_once(live, only=[visit_id])

    visit = await ch.get_visit(visit_id)
    assert visit.resolved_address is None
    assert visit.inferred_at is not None, "a failed address lookup never fails inference"
    reasons = [
        s["detail"]["reason"]
        for s in visit.signals
        if s["rule_id"] == "inference.street_address_absent"
    ]
    assert reasons == ["request_failed:HTTPStatusError"]
