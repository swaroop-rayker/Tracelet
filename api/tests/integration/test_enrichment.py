"""Client enrichment and the honeypot, against the real database (F2.AC4, F2.AC6, F11.AC4).

Single-use is decided by the conditional ``UPDATE`` that applies the enrichment -- the
only place it can be decided under concurrency -- so every test that needs "exactly
once" is here rather than in the unit suite.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration import capture_helpers as ch
from tracelet.capture import nonce as nonces
from tracelet.capture import service
from tracelet.capture.models import Classification, ConsentState, Visit, VisitStage
from tracelet.capture.schemas import MAX_ENRICHMENT_BYTES
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.net import prefix_of

pytestmark = pytest.mark.integration

PAYLOAD = {
    "screen": {"w": 1080, "h": 2400, "dpr": 2.75, "colorDepth": 24, "touchPoints": 5},
    "viewport": {"w": 412, "h": 915},
    "hardware": {"cores": 8, "deviceMemoryGb": 8},
    "gpu": {"vendor": "Qualcomm", "renderer": "Adreno (TM) 740"},
    "locale": {"tzIana": "Asia/Kolkata", "tzOffsetMin": 330, "languages": ["en-IN", "en"]},
    "hashes": {"canvas": "a1b2c3d4", "webgl": "e5f6a7b8", "font": "c9d0e1f2"},
    "geolocation": {"state": "timeout"},
    "honeypot": {"linkClicked": False, "fieldFilled": False},
}


async def _captured(
    client: AsyncClient, *, peer: str = ch.VISITOR_IP, ua: str = ch.CHROME_UA
) -> tuple[str, str]:
    """A fresh visit. Returns (link slug, nonce)."""
    link = await ch.create_link()
    page = await ch.visit(client, link.slug, peer=peer, ua=ua)
    return link.slug, ch.nonce_from(page)


async def _enrich(
    client: AsyncClient,
    token: str,
    payload: object = PAYLOAD,
    *,
    peer: str = ch.VISITOR_IP,
) -> int:
    response = await client.post(
        f"/api/v1/s/{token}", json=payload, headers={"X-Tracelet-Peer-IP": peer}
    )
    return response.status_code


async def _visit_for(slug: str) -> Visit:
    async with session_scope() as db:
        link_id = (
            await db.execute(text("SELECT id FROM links WHERE slug = :s"), {"s": slug})
        ).scalar_one()
    return await ch.latest_visit(link_id)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_enrichment_is_merged_and_finalises_the_visit(db_client: AsyncClient) -> None:
    slug, token = await _captured(db_client)

    assert await _enrich(db_client, token) == 204

    stored = await _visit_for(slug)
    assert stored.stage is VisitStage.ENRICHED
    assert stored.finalized_at is not None
    assert stored.enrichment_consumed_at is not None
    assert (stored.screen_w, stored.screen_h) == (1080, 2400)
    assert str(stored.dpr) == "2.75"
    assert stored.cpu_cores == 8
    assert stored.gpu_renderer == "Adreno (TM) 740"
    assert stored.tz_iana == "Asia/Kolkata"
    assert stored.tz_offset_min == 330
    assert stored.languages == ["en-IN", "en"]
    assert stored.canvas_hash is not None


async def test_a_position_that_did_not_arrive_in_time_is_recorded_with_its_reason(
    db_client: AsyncClient,
) -> None:
    """F3.AC5: an absent value carries its reason."""
    slug, token = await _captured(db_client)
    await _enrich(db_client, token)

    stored = await _visit_for(slug)
    assert stored.consent_state is ConsentState.UNAVAILABLE
    assert stored.gps_lat is None
    assert any(s["rule_id"] == "client.geolocation_absent" for s in stored.signals)


async def test_permission_not_yet_decided_is_recorded_as_not_asked(
    db_client: AsyncClient,
) -> None:
    """F4.AC1 as amended: the page never prompts, so an undecided permission is not_asked."""
    slug, token = await _captured(db_client)
    payload = {**PAYLOAD, "geolocation": {"state": "prompt", "lat": 12.97, "lng": 77.59}}
    await _enrich(db_client, token, payload)

    stored = await _visit_for(slug)
    assert stored.consent_state is ConsentState.NOT_ASKED
    assert stored.gps_lat is None


async def test_granted_coordinates_are_stored(db_client: AsyncClient) -> None:
    slug, token = await _captured(db_client)
    payload = {
        **PAYLOAD,
        "geolocation": {"state": "granted", "lat": 12.9716, "lng": 77.5946, "accuracyM": 18},
    }

    assert await _enrich(db_client, token, payload) == 204

    stored = await _visit_for(slug)
    assert stored.consent_state is ConsentState.GRANTED
    assert str(stored.gps_lat) == "12.971600"


async def test_coordinates_without_consent_are_never_stored(db_client: AsyncClient) -> None:
    """F4.AC3. A payload claiming "denied" with coordinates attached is not an error --
    the coordinates are simply dropped."""
    slug, token = await _captured(db_client)
    payload = {**PAYLOAD, "geolocation": {"state": "denied", "lat": 12.97, "lng": 77.59}}

    assert await _enrich(db_client, token, payload) == 204

    stored = await _visit_for(slug)
    assert stored.consent_state is ConsentState.DENIED
    assert stored.gps_lat is None
    assert stored.gps_lng is None


async def test_the_engine_itself_refuses_coordinates_without_consent(
    db_client: AsyncClient,
) -> None:
    """The CHECK constraint, proved by going around the application entirely -- the
    guarantee must survive a code path that forgets the rule."""
    slug, _ = await _captured(db_client)
    stored = await _visit_for(slug)

    with pytest.raises(IntegrityError, match="ck_visits_gps_requires_consent"):
        async with session_scope() as db:
            await db.execute(
                text(
                    "UPDATE visits SET consent_state = 'denied', gps_lat = 1, gps_lng = 1 "
                    "WHERE id = :id"
                ),
                {"id": stored.id},
            )


async def test_enrichment_does_not_reclassify_a_crawler(db_client: AsyncClient) -> None:
    """The payload is a claim. A fetcher that runs JavaScript stays a fetcher."""
    slug, token = await _captured(db_client, ua=ch.FB_FETCHER_UA)
    await _enrich(db_client, token)
    assert (await _visit_for(slug)).classification is Classification.CRAWLER


# ---------------------------------------------------------------------------
# Single-use, prefix-bound, 60 seconds (F2.AC6, F11.AC4)
# ---------------------------------------------------------------------------


async def test_a_replayed_nonce_is_refused(db_client: AsyncClient) -> None:
    """The M2 done-check: "nonce replay returns 410"."""
    _, token = await _captured(db_client)

    assert await _enrich(db_client, token) == 204
    replay = await db_client.post(
        f"/api/v1/s/{token}", json=PAYLOAD, headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP}
    )

    assert replay.status_code == 410
    assert replay.json()["code"] == "NONCE_INVALID"


async def test_concurrent_submissions_of_one_nonce_succeed_exactly_once(
    db_client: AsyncClient,
) -> None:
    """F11.AC4: "exactly one successful request per nonce, ever". The conditional
    UPDATE is the only thing that can decide this under concurrency."""
    _, token = await _captured(db_client)

    results = await asyncio.gather(*(_enrich(db_client, token) for _ in range(5)))

    assert sorted(results) == [204, 410, 410, 410, 410]


async def test_a_nonce_used_from_another_network_is_refused(db_client: AsyncClient) -> None:
    """The M2 done-check: "nonce from a different prefix returns 410"."""
    _, token = await _captured(db_client, peer=ch.VISITOR_IP)
    assert await _enrich(db_client, token, peer="203.0.113.50") == 410


async def test_another_address_in_the_same_prefix_is_accepted(db_client: AsyncClient) -> None:
    """Bound to the /24, not the address: a CGNAT rebalance mid-page must not cost the
    enrichment."""
    _, token = await _captured(db_client, peer="49.207.12.34")
    assert await _enrich(db_client, token, peer="49.207.12.200") == 204


async def test_an_expired_nonce_is_refused(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    slug, _ = await _captured(db_client)
    stored = await _visit_for(slug)
    assert integration_settings.session_secret is not None
    key = nonces.derive_key(integration_settings.session_secret.get_secret_value())
    stale = nonces.mint(
        key,
        visit_id=stored.id,
        ip_prefix=prefix_of(ch.VISITOR_IP),
        now=time.time() - nonces.TTL_SECONDS - 5,
    )
    assert await _enrich(db_client, stale) == 410


async def test_a_visit_the_sweeper_already_finalised_cannot_be_enriched(
    db_client: AsyncClient,
) -> None:
    """Once ``server_only``, the visit's record is closed."""
    slug, token = await _captured(db_client)
    stored = await _visit_for(slug)
    await ch.backdate(stored.id, seconds=91)
    async with session_scope() as db:
        await service.sweep(db)

    assert await _enrich(db_client, token) == 410


@pytest.mark.parametrize("token", ["garbage", "A" * 48, "x"])
async def test_a_forged_nonce_is_refused(db_client: AsyncClient, token: str) -> None:
    assert await _enrich(db_client, token) == 410


# ---------------------------------------------------------------------------
# Payload bounds
# ---------------------------------------------------------------------------


async def test_an_oversized_payload_is_refused(db_client: AsyncClient) -> None:
    _, token = await _captured(db_client)
    huge = {"gpu": {"renderer": "x" * 200}, "pad": "y" * (MAX_ENRICHMENT_BYTES + 1)}
    assert await _enrich(db_client, token, huge) == 413


async def test_a_malformed_payload_is_a_422_with_field_errors(db_client: AsyncClient) -> None:
    _, token = await _captured(db_client)
    response = await db_client.post(
        f"/api/v1/s/{token}",
        json={"screen": {"w": -5}},
        headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP},
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"].startswith("screen")


async def test_a_rejected_payload_does_not_spend_the_nonce(db_client: AsyncClient) -> None:
    """Validation runs before the conditional UPDATE, so a malformed first attempt
    leaves the nonce usable -- the same lesson as docs/ERRORS.md E15."""
    _, token = await _captured(db_client)
    assert await _enrich(db_client, token, {"screen": {"w": -5}}) == 422
    assert await _enrich(db_client, token) == 204


async def test_an_empty_payload_still_finalises_the_visit(db_client: AsyncClient) -> None:
    """Every field is optional. A browser that could collect nothing still counts."""
    slug, token = await _captured(db_client)
    assert await _enrich(db_client, token, {}) == 204
    assert (await _visit_for(slug)).stage is VisitStage.ENRICHED


# ---------------------------------------------------------------------------
# Honeypot (F5.AC6)
# ---------------------------------------------------------------------------


async def test_a_followed_honeypot_link_marks_the_visit(db_client: AsyncClient) -> None:
    slug, token = await _captured(db_client)

    response = await db_client.get(
        f"/api/v1/hp/{token}", headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP}
    )

    assert response.status_code == 204
    assert (await _visit_for(slug)).honeypot_tripped is True


async def test_a_filled_honeypot_field_marks_the_visit(db_client: AsyncClient) -> None:
    slug, token = await _captured(db_client)
    payload = {**PAYLOAD, "honeypot": {"fieldFilled": True}}
    await _enrich(db_client, token, payload)
    assert (await _visit_for(slug)).honeypot_tripped is True


@pytest.mark.parametrize("token", ["garbage", "A" * 48])
async def test_the_honeypot_answers_the_same_to_anything(
    db_client: AsyncClient, token: str
) -> None:
    """Always 204, so a probe learns nothing from the response."""
    response = await db_client.get(f"/api/v1/hp/{token}")
    assert response.status_code == 204
    assert response.content == b""


async def test_a_honeypot_hit_from_another_network_marks_nothing(db_client: AsyncClient) -> None:
    """Nobody can trip someone else's visit: the prefix binding holds even though the
    honeypot ignores expiry."""
    slug, token = await _captured(db_client, peer=ch.VISITOR_IP)
    await db_client.get(f"/api/v1/hp/{token}", headers={"X-Tracelet-Peer-IP": "203.0.113.50"})
    assert (await _visit_for(slug)).honeypot_tripped is False
