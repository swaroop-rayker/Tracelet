"""Classification through capture and the ADR-0015 job, against the real database (M4).

Covers the done-checks that need more than one visit -- one fingerprint across three
networks is a proxy; many fingerprints behind one prefix is a gateway, not a proxy --
plus identity, the honeypot, server-only visits and failure. Distinct networks are
simulated by a toolkit mapping each test address to an ASN: CI has no geo databases.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.integration import capture_helpers as ch
from tracelet.capture.models import Classification, VisitStage
from tracelet.classify import rules
from tracelet.classify.config import CLASSIFIER_REVISION
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.inference import engine, store
from tracelet.inference.sources import IPAddress, rdns

pytestmark = pytest.mark.integration

BROWSER_HEADERS = ch.BROWSER_HEADERS
DEVICE: dict[str, Any] = {
    "screen": {"w": 412, "h": 915, "dpr": 2.625, "colorDepth": 24, "touchPoints": 5},
    "hardware": {"cores": 8, "deviceMemoryGb": 8},
    "gpu": {"vendor": "Qualcomm", "renderer": "Adreno (TM) 740"},
    "locale": {"tzIana": "Asia/Kolkata", "tzOffsetMin": 330, "languages": ["en-IN", "en"]},
    "hashes": {"canvas": "c4nv45", "webgl": "w3bgl", "font": "f0nt5"},
    "probes": {
        "webdriver": False,
        "chromeObject": True,
        "pluginCount": 5,
        "outerWidth": 412,
        "fontCount": 4,
        "permissionsAnomaly": False,
        "cdpArtefacts": False,
    },
    "geolocation": {"state": "prompt"},
}
NETWORKS = {
    "49.207.12.34": 24309,
    "103.211.52.10": 133982,
    "122.172.80.1": 24560,
    # Two addresses from Reliance Jio's pool, in different /24s (ADR-0031).
    "49.36.12.7": 55836,
    "157.48.201.9": 55836,
}
JIO_A, JIO_B = "49.36.12.7", "157.48.201.9"


@pytest.fixture(autouse=True)
def _toolkit(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test address on its own ASN; a resolver that answers PTR with nothing."""

    async def factory(db: object) -> engine.Toolkit:
        lexicon = await store.load_lexicon(db)  # type: ignore[arg-type]  # the job's session

        def asn_lookup(ip: IPAddress) -> tuple[int | None, str | None]:
            number = NETWORKS.get(str(ip))
            return (number, f"Test ISP {number}") if number else (None, None)

        return engine.Toolkit(lexicon=lexicon, asn_lookup=asn_lookup)

    async def no_ptr(ip: str, timeout_s: float) -> str | None:
        del timeout_s
        return rdns.CANARY[1] if ip == rdns.CANARY[0] else None

    rdns.reset_canary_for_tests()
    monkeypatch.setattr(rdns, "_lookup", no_ptr)
    engine.set_toolkit_factory(factory)
    yield
    engine.set_toolkit_factory(None)
    rdns.reset_canary_for_tests()


async def _visit(
    client: AsyncClient,
    *,
    peer: str = ch.VISITOR_IP,
    enrich: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> uuid.UUID:
    link = await ch.create_link()
    page = await ch.visit(
        client, link.slug, peer=peer, headers=headers if headers is not None else BROWSER_HEADERS
    )
    visit_id = (await ch.latest_visit(link.id)).id
    if enrich is not None:
        response = await client.post(
            f"/api/v1/s/{ch.nonce_from(page)}", json=enrich, headers={"X-Tracelet-Peer-IP": peer}
        )
        assert response.status_code == 204
    else:
        await ch.backdate(visit_id, seconds=91)
        async with session_scope() as db:
            from tracelet.capture import service  # noqa: PLC0415 - the sweeper, for server_only

            await service.sweep(db)
    return visit_id


async def _rules(visit_id: uuid.UUID) -> set[str]:
    visit = await ch.get_visit(visit_id)
    return {s["rule_id"] for s in visit.signals}


# ---------------------------------------------------------------------------


async def test_an_enriched_browser_visit_is_human_with_identity(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    visit_id = await _visit(db_client, enrich=DEVICE)
    await engine.run_once(integration_settings, only=[visit_id])

    visit = await ch.get_visit(visit_id)
    assert visit.classification is Classification.HUMAN, await _rules(visit_id)
    assert visit.classifier_version is not None
    assert visit.classifier_version.startswith(f"{CLASSIFIER_REVISION}+")
    assert visit.visitor_id and visit.session_fp and visit.fingerprint_id
    assert visit.client_probes is not None and visit.client_probes["fontCount"] == 4
    # Assessed where the evidence existed (an ASN, a fingerprint); NULL where not (no
    # exit list installed in CI) -- never a false that asserts something unchecked.
    assert (visit.is_datacenter, visit.is_proxy_suspected, visit.is_tor) == (False, False, None)


async def test_a_server_only_visit_classifies_from_server_signals_alone(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """M4 done-check, and ADR-0006 amendment item 2: no fingerprint_id without a client."""
    visit_id = await _visit(db_client)
    await engine.run_once(integration_settings, only=[visit_id])

    visit = await ch.get_visit(visit_id)
    assert visit.stage is VisitStage.SERVER_ONLY
    assert visit.classification is Classification.HUMAN, await _rules(visit_id)
    assert visit.visitor_id is not None and visit.fingerprint_id is None
    assert "identity.server_only" in await _rules(visit_id)
    # F3.AC5: no fingerprint, no proxy verdict; no exit list in CI, no Tor verdict.
    assert visit.is_proxy_suspected is None
    assert visit.is_tor is None


async def test_a_library_with_a_browser_ua_is_a_bot_from_its_headers(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    visit_id = await _visit(db_client, headers={})  # the test client's own: accept */*
    await engine.run_once(integration_settings, only=[visit_id])

    visit = await ch.get_visit(visit_id)
    assert visit.classification is Classification.BOT
    assert {"hdr.no_fetch_metadata", "hdr.no_accept_language"} <= await _rules(visit_id)


async def test_a_honeypot_hit_classifies_as_automation(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """M4 done-check."""
    link = await ch.create_link()
    page = await ch.visit(db_client, link.slug, headers=BROWSER_HEADERS)
    nonce = ch.nonce_from(page)
    await db_client.get(f"/api/v1/hp/{nonce}", headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP})
    await db_client.post(
        f"/api/v1/s/{nonce}", json=DEVICE, headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP}
    )
    visit_id = (await ch.latest_visit(link.id)).id

    await engine.run_once(integration_settings, only=[visit_id])

    visit = await ch.get_visit(visit_id)
    assert visit.classification is Classification.BOT
    assert "client.honeypot" in await _rules(visit_id)


async def test_one_device_on_three_networks_is_a_proxy(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """M4 done-check, F5.AC7: the same fingerprint_id across three ASNs."""
    ids = []
    for peer in NETWORKS:
        visit_id = await _visit(db_client, peer=peer, enrich=DEVICE)
        await engine.run_once(integration_settings, only=[visit_id])
        ids.append(visit_id)

    first, last = await ch.get_visit(ids[0]), await ch.get_visit(ids[-1])
    assert first.fingerprint_id == last.fingerprint_id, "the same device, by construction"
    assert not first.is_proxy_suspected
    assert last.is_proxy_suspected
    assert "net.fingerprint_across_asns" in await _rules(ids[-1])


async def test_many_devices_behind_one_prefix_is_a_gateway_not_a_proxy(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """M4 done-check: the regression that would misclassify most Indian mobile traffic."""
    ids = []
    for i in range(10):
        device = {**DEVICE, "hashes": {"canvas": f"device-{i}", "webgl": "w", "font": "f"}}
        visit_id = await _visit(db_client, enrich=device)
        await engine.run_once(integration_settings, only=[visit_id])
        ids.append(visit_id)

    last = await ch.get_visit(ids[-1])
    assert not last.is_proxy_suspected
    assert "net.shared_gateway" in await _rules(ids[-1])
    assert last.classification is Classification.HUMAN


async def test_a_classifier_failure_is_unknown_with_a_reason(
    db_client: AsyncClient, integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5.AC14."""

    def broken(*_args: object, **_kwargs: object) -> rules.Verdict:
        msg = "classifier exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr("tracelet.classify.job.classify", broken)
    visit_id = await _visit(db_client, enrich=DEVICE)
    await engine.run_once(integration_settings, only=[visit_id])

    visit = await ch.get_visit(visit_id)
    assert visit.inferred_at is not None, "location still written"
    assert visit.classification is Classification.UNKNOWN
    assert "classifier.engine_error" in await _rules(visit_id)


async def test_an_exploit_probe_on_the_capture_path_is_spam(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    link = await ch.create_link()
    await ch.visit(
        db_client, link.slug, headers=BROWSER_HEADERS, query="?id=1%20UNION%20SELECT%20password"
    )
    visit = await ch.latest_visit(link.id)
    await ch.backdate(visit.id, seconds=91)
    async with session_scope() as db:
        from tracelet.capture import service  # noqa: PLC0415

        await service.sweep(db)
    await engine.run_once(integration_settings, only=[visit.id])

    stored = await ch.get_visit(visit.id)
    assert stored.classification is Classification.SPAM
    probe = next(
        s for s in stored.signals if s["rule_id"] == "capture.exploit_probe" and s["weight"] == 0
    )
    assert probe["detail"] == {"patterns": ["sql_injection"]}
    async with session_scope() as db:
        row = (
            await db.execute(
                text("SELECT to_jsonb(v)::text FROM visits v WHERE id = :i"), {"i": visit.id}
            )
        ).scalar_one()
    assert "UNION" not in row, "the payload itself is never stored"


# ---------------------------------------------------------------------------
# ADR-0031: a phone on mobile data is one visitor across the carrier's pool
# ---------------------------------------------------------------------------


async def _tap(client: AsyncClient, link: Any, peer: str, *, enrich: bool) -> uuid.UUID:
    page = await ch.visit(client, link.slug, peer=peer, headers=BROWSER_HEADERS)
    visit_id = (await ch.latest_visit(link.id)).id
    if enrich:
        response = await client.post(
            f"/api/v1/s/{ch.nonce_from(page)}", json=DEVICE, headers={"X-Tracelet-Peer-IP": peer}
        )
        assert response.status_code == 204
    return visit_id


async def _sweep() -> None:
    async with session_scope() as db:
        from tracelet.capture import service  # noqa: PLC0415 - the sweeper, for server_only

        await service.sweep(db)


async def test_an_enriched_phone_keeps_one_identity_across_the_carriers_pool(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    link = await ch.create_link()
    first = await _tap(db_client, link, JIO_A, enrich=True)
    second = await _tap(db_client, link, JIO_B, enrich=True)
    await engine.run_once(integration_settings, only=[first, second])

    a, b = await ch.get_visit(first), await ch.get_visit(second)
    assert a.ip_prefix != b.ip_prefix
    assert a.visitor_id is not None and a.visitor_id == b.visitor_id
    assert "identity.carrier" in await _rules(first)
    assert a.classifier_version is not None and a.classifier_version.startswith("m9.1+")


async def test_a_resent_request_takes_the_identity_of_the_tap_it_repeats(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    link = await ch.create_link()
    resent = await _tap(db_client, link, JIO_B, enrich=False)
    tap = await _tap(db_client, link, JIO_A, enrich=True)
    # The re-sent request reaches the sweeper; the tap it repeats is 11 s away from it.
    await ch.backdate(resent, seconds=91)
    await ch.backdate(tap, seconds=80)
    await engine.run_once(integration_settings, only=[tap])
    await _sweep()
    await engine.run_once(integration_settings, only=[resent])

    original, repeat = await ch.get_visit(tap), await ch.get_visit(resent)
    assert repeat.stage is VisitStage.SERVER_ONLY
    assert repeat.visitor_id is not None and repeat.visitor_id == original.visitor_id
    same = [s for s in repeat.signals if s["rule_id"] == "identity.same_tap"]
    assert same and same[0]["detail"]["visit_id"] == str(tap)


async def test_a_server_only_visit_more_than_a_minute_away_keeps_its_own_identity(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    link = await ch.create_link()
    tap = await _tap(db_client, link, JIO_A, enrich=True)
    later = await _tap(db_client, link, JIO_B, enrich=False)
    await ch.backdate(later, seconds=91)  # 91 s from the tap: outside the window
    await engine.run_once(integration_settings, only=[tap])
    await _sweep()
    await engine.run_once(integration_settings, only=[later])

    original, other = await ch.get_visit(tap), await ch.get_visit(later)
    assert other.visitor_id is not None and other.visitor_id != original.visitor_id
    assert "identity.same_tap" not in await _rules(later)
