"""The inference flow diagram (F10.AC8) and runtime source toggles (F10.AC7).

M7's checklist: the diagram shows the enabled sources and what fired for a chosen visit,
and toggling a source changes behaviour **without a restart** -- the next visit inferred
after the change, in the same process, is inferred under it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.integration import capture_helpers as ch
from tests.integration.helpers import SignedIn
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.inference import engine
from tracelet.inference.sources import rdns

pytestmark = pytest.mark.integration

SETTINGS = "/api/v1/health/inference"
FLOW = f"{SETTINGS}/flow"
BENGALURU = {"state": "granted", "lat": 12.9716, "lng": 77.5946, "accuracyM": 18}


@pytest.fixture(autouse=True)
async def _active_version_restored(db_app: object) -> AsyncIterator[None]:
    del db_app
    async with session_scope() as db:
        active = (
            await db.execute(text("SELECT version FROM inference_settings WHERE is_active"))
        ).scalar_one_or_none()
    yield
    if active is not None:
        async with session_scope() as db:
            await db.execute(
                text("UPDATE inference_settings SET is_active = false WHERE is_active")
            )
            await db.execute(
                text("UPDATE inference_settings SET is_active = true WHERE version = :v"),
                {"v": active},
            )
    rdns.reset_canary_for_tests()


async def _set_source(owner: SignedIn, source: str, *, enabled: bool) -> dict[str, Any]:
    current = (await owner.client.get(SETTINGS)).json()["settings"]
    current["sources"][source] = {**current["sources"][source], "enabled": enabled}
    saved = await owner.client.patch(
        SETTINGS,
        json={"settings": current, "note": f"itest {source} {enabled}"},
        headers=owner.headers(),
    )
    assert saved.status_code == 200, saved.text
    return dict(saved.json())


async def _inferred_visit(client: AsyncClient, settings: Settings) -> str:
    link = await ch.create_link()
    page = await ch.visit(client, link.slug)
    await client.post(
        f"/api/v1/s/{ch.nonce_from(page)}",
        json={"geolocation": BENGALURU, "locale": {"tzIana": "Asia/Kolkata"}},
        headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP},
    )
    visit_id = (await ch.latest_visit(link.id)).id
    await engine.run_once(settings, only=[visit_id])
    return str(visit_id)


async def test_the_diagram_lists_every_source_in_order_with_its_switch(owner: SignedIn) -> None:
    body = (await owner.client.get(FLOW)).json()
    assert body["stages"] == [
        "capture",
        "sources",
        "suppression",
        "consensus",
        "classification",
        "geofence",
        "alert",
    ]
    codes = [s["code"] for s in body["sources"]]
    assert codes == ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S11"]
    assert [s["order"] for s in body["sources"]] == list(range(1, 11))
    families = {f["family"]: f["sources"] for f in body["families"]}
    assert families["client"] == ["gps"]
    assert "registry_artifact" in {r["rule"] for r in body["rules"]}
    assert [level["level"] for level in body["levels"]] == ["country", "admin1", "admin2", "city"]
    assert body["sample"] is None


async def test_a_toggle_applies_to_the_next_visit_without_a_restart(
    owner: SignedIn, db_client: AsyncClient, integration_settings: Settings
) -> None:
    off = await _set_source(owner, "rdns", enabled=False)
    flow = (await owner.client.get(FLOW)).json()
    assert flow["inference_version"] == off["inference_version"]
    assert next(s for s in flow["sources"] if s["source"] == "rdns")["enabled"] is False

    first = await _inferred_visit(db_client, integration_settings)
    sample = (await owner.client.get(FLOW, params={"sample_visit_id": first})).json()["sample"]
    rdns_outcome = next(s for s in sample["sources"] if s["source"] == "rdns")
    assert rdns_outcome["status"] == "disabled"
    assert sample["inference_version"] == off["inference_version"]

    on = await _set_source(owner, "rdns", enabled=True)
    second = await _inferred_visit(db_client, integration_settings)
    sample = (await owner.client.get(FLOW, params={"sample_visit_id": second})).json()["sample"]
    assert next(s for s in sample["sources"] if s["source"] == "rdns")["status"] != "disabled"
    assert sample["inference_version"] == on["inference_version"]


async def test_the_overlay_shows_what_fired_was_suppressed_and_each_levels_outcome(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """The overlay reads what inference recorded. The suite has no geo databases, so the
    record is written here: GeoLite2 accepted, DB-IP suppressed as a registry artifact, rDNS
    unavailable with its reason, and a strict country with an abstaining city."""
    link = await ch.create_link()
    await ch.visit(db_client, link.slug)
    visit_id = (await ch.latest_visit(link.id)).id
    absent = [
        {
            "rule_id": "inference.source_absent",
            "category": "absence",
            "weight": 0,
            "detail": {"source": "rdns", "status": "unavailable", "reason": "no_ptr_record"},
        }
    ]
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE visits SET strict_country_code = 'IN', advisory_country_code = 'IN', "
                "advisory_admin1 = 'Karnataka', advisory_city = 'Bengaluru', "
                "confidence_country = 0.97, confidence_admin1 = 0.70, confidence_city = 0.40, "
                "geo_source_primary = 'geolite2', inference_version = 'itest', "
                'abstain_reason = \'{"admin1": "below_threshold", "city": "below_threshold"}\', '
                "signals = CAST(:s AS jsonb) WHERE id = :id"
            ),
            {"id": visit_id, "s": json.dumps(absent)},
        )
        for source, accepted, reason in (
            ("geolite2", True, None),
            ("dbip", False, "registry_artifact"),
        ):
            await db.execute(
                text(
                    "INSERT INTO visit_candidates (visit_id, source, level, country_code, admin1, "
                    "city, raw_confidence, weight, effective_weight, accepted, suppressed_reason, "
                    "latency_ms) VALUES (:v, CAST(:src AS inference_source), 'city', 'IN', "
                    "'Karnataka', :city, 0.9, 1, :w, :a, :r, 3)"
                ),
                {
                    "v": visit_id,
                    "src": source,
                    "city": "Bengaluru" if accepted else "Faridabad",
                    "w": 0.8 if accepted else 0.0,
                    "a": accepted,
                    "r": reason,
                },
            )

    sample = (await owner.client.get(FLOW, params={"sample_visit_id": str(visit_id)})).json()[
        "sample"
    ]
    by_source = {s["source"]: s for s in sample["sources"]}
    assert by_source["geolite2"]["status"] == "fired"
    assert by_source["geolite2"]["candidates"][0]["value"] == "Bengaluru, Karnataka, IN"
    assert by_source["dbip"]["status"] == "suppressed"
    assert by_source["dbip"]["reason"] == "registry_artifact"
    assert (by_source["rdns"]["status"], by_source["rdns"]["reason"]) == (
        "unavailable",
        "no_ptr_record",
    )
    assert by_source["gps"]["status"] == "silent"  # nothing recorded for it
    assert sample["rules_fired"] == ["registry_artifact"]
    assert sample["geo_source_primary"] == "geolite2"
    levels = {level["level"]: level for level in sample["levels"]}
    assert (levels["country"]["strict"], levels["country"]["confidence"]) == ("IN", 0.97)
    assert levels["city"]["strict"] is None
    assert (levels["city"]["advisory"], levels["city"]["abstain_reason"]) == (
        "Bengaluru",
        "below_threshold",
    )
    assert sample["inference_version"] == "itest"
    assert len(sample["sources"]) == 10


async def test_an_unknown_sample_visit_is_404(owner: SignedIn) -> None:
    missing = "01a10000-0000-7000-8000-000000000000"
    assert (await owner.client.get(FLOW, params={"sample_visit_id": missing})).status_code == 404
