"""Ground truth and accuracy against the real database (F4.AC15, F4.AC17, F9.AC10, ADR-0024,
docs/API.md section 11).

Three things matter most and each has a test: a replay reproduces exactly what the engine
decided; a label never changes a visit's inference; and labels are an owner's, audited.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.integration import capture_helpers as ch
from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.accuracy import replay, store
from tracelet.audit import log as audit
from tracelet.auth.models import AdminRole
from tracelet.capture.models import Visit
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.inference import engine
from tracelet.inference import store as settings_store
from tracelet.inference.sources import rdns
from tracelet.inference.types import LEVELS, Candidate, GeoLevel, InferenceSource
from tracelet.lifecycle.maint import maint_connection
from tracelet.net import IPAddress

pytestmark = pytest.mark.integration

GT = "/api/v1/ground-truth"
S = InferenceSource
RUN_NOTE = "integration test run"

ENRICHMENT: dict[str, Any] = {
    "locale": {"tzIana": "Asia/Kolkata", "tzOffsetMin": 330, "languages": ["en-IN"]},
    "geolocation": {"state": "prompt"},
}


def _db(source: InferenceSource, admin1: str, city: str) -> engine.DbProducer:
    def produce(ip: IPAddress) -> list[Candidate]:
        del ip
        return [
            Candidate(
                source=source, level=GeoLevel.CITY, country_code="IN", admin1=admin1, city=city
            )
        ]

    return produce


@pytest.fixture(autouse=True)
def _toolkit(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Three databases that disagree (two Bengaluru, one Faridabad: B1's shape), an ASN,
    and a resolver with no PTR -- so the decision has something to weigh."""

    async def factory(db: object) -> engine.Toolkit:
        lexicon = await settings_store.load_lexicon(db)  # type: ignore[arg-type]  # the job's session

        def asn_lookup(ip: IPAddress) -> tuple[int | None, str | None]:
            del ip
            return 24309, "Atria Convergence Technologies Pvt. Ltd."

        return engine.Toolkit(
            lexicon=lexicon,
            asn_lookup=asn_lookup,
            databases={
                S.GEOLITE2: _db(S.GEOLITE2, "Karnataka", "Bengaluru"),
                S.DBIP: _db(S.DBIP, "Karnataka", "Bengaluru"),
                S.IP2LOCATION: _db(S.IP2LOCATION, "Haryana", "Faridabad"),
            },
        )

    async def no_ptr(ip: str, timeout_s: float) -> str | None:
        del timeout_s
        return rdns.CANARY[1] if ip == rdns.CANARY[0] else None

    rdns.reset_canary_for_tests()
    monkeypatch.setattr(rdns, "_lookup", no_ptr)
    engine.set_toolkit_factory(factory)
    yield
    engine.set_toolkit_factory(None)
    rdns.reset_canary_for_tests()


@pytest.fixture(autouse=True)
async def _no_test_runs_left(db_app: object, integration_settings: Settings) -> AsyncIterator[None]:
    """Runs are append-only for the application (migration 0018); the maintenance role
    removes the ones these tests recorded, so the owner's Runs tab stays theirs."""
    del db_app
    yield
    if integration_settings.maint_database_url is None:
        return
    async with maint_connection(integration_settings) as conn, conn.begin():
        await conn.execute(text("DELETE FROM accuracy_runs WHERE note = :n"), {"n": RUN_NOTE})


@pytest.fixture
async def analyst(
    owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> SignedIn:
    del owner
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    return await helpers.enroll(await new_client(), invited, totp_clock)


async def _inferred_visit(
    client: AsyncClient, settings: Settings, *, enrichment: dict[str, Any] | None = None
) -> uuid.UUID:
    link = await ch.create_link()
    page = await ch.visit(client, link.slug)
    response = await client.post(
        f"/api/v1/s/{ch.nonce_from(page)}",
        json=enrichment or ENRICHMENT,
        headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP},
    )
    assert response.status_code == 204
    visit = await ch.latest_visit(link.id)
    assert await engine.run_once(settings, only=[visit.id]) == 1
    async with session_scope() as db:
        # The test client reads as a bot, which the queue and the analytics default leave
        # out; the owner's phone would not.
        await db.execute(
            text("UPDATE visits SET classification = 'human' WHERE id = :id"), {"id": visit.id}
        )
    return visit.id


async def _label(who: SignedIn, visit_id: uuid.UUID, **body: Any) -> Any:
    payload = {
        "visit_id": str(visit_id),
        "country_code": "IN",
        "admin1": "Karnataka",
        "city": "Bengaluru",
        "connection_kind": "wifi",
        "vpn_used": False,
        "network": "act",
        **body,
    }
    return await who.client.post(GT, json=payload, headers=who.headers())


def _inference(visit: Visit) -> dict[str, Any]:
    return {
        k: getattr(visit, k)
        for k in (
            "strict_country_code",
            "strict_admin1",
            "strict_admin2",
            "strict_city",
            "advisory_country_code",
            "advisory_admin1",
            "advisory_city",
            "confidence_admin1",
            "conflict_score",
            "inference_version",
        )
    }


# ---------------------------------------------------------------------------
# Replay fidelity (ADR-0024 decision 1)
# ---------------------------------------------------------------------------


async def test_a_replay_reproduces_what_the_engine_decided(
    db_client: AsyncClient, integration_settings: Settings, owner: SignedIn
) -> None:
    visit_id = await _inferred_visit(db_client, integration_settings)
    assert (await _label(owner, visit_id)).status_code == 201

    async with session_scope() as db:
        loaded = await store.load_cases(db, [Visit.id == visit_id])
        active = await settings_store.active_settings(db)
    visit = await ch.get_visit(visit_id)
    (case,) = loaded.cases
    assert {c.source for c in case.candidates} >= {
        InferenceSource.GEOLITE2,
        InferenceSource.DBIP,
        InferenceSource.IP2LOCATION,
    }
    answer = replay.decide(case, active.config)
    stored_strict = {
        GeoLevel.COUNTRY: visit.strict_country_code,
        GeoLevel.ADMIN1: visit.strict_admin1,
        GeoLevel.ADMIN2: visit.strict_admin2,
        GeoLevel.CITY: visit.strict_city,
    }
    stored_advisory = {
        GeoLevel.COUNTRY: visit.advisory_country_code,
        GeoLevel.ADMIN1: visit.advisory_admin1,
        GeoLevel.ADMIN2: visit.advisory_admin2,
        GeoLevel.CITY: visit.advisory_city,
    }
    for level in LEVELS:
        assert answer.strict[level] == stored_strict[level], level
        assert answer.advisory[level] == stored_advisory[level], level


async def test_a_label_never_changes_the_visits_inference(
    db_client: AsyncClient, integration_settings: Settings, owner: SignedIn
) -> None:
    visit_id = await _inferred_visit(db_client, integration_settings)
    before = _inference(await ch.get_visit(visit_id))
    # A truth that disagrees with the engine everywhere.
    created = await _label(owner, visit_id, admin1="Haryana", city="Faridabad")
    assert created.status_code == 201, created.text
    assert _inference(await ch.get_visit(visit_id)) == before


# ---------------------------------------------------------------------------
# Labels: owner-only and audited (invariant 9)
# ---------------------------------------------------------------------------


async def test_an_owner_labels_changes_and_deletes_and_every_step_is_audited(
    db_client: AsyncClient, integration_settings: Settings, owner: SignedIn
) -> None:
    visit_id = await _inferred_visit(db_client, integration_settings)
    created = await _label(owner, visit_id, notes="  phone at home  ")
    assert created.status_code == 201, created.text
    label = created.json()
    assert label["truth"] == {
        "country_code": "IN",
        "admin1": "Karnataka",
        "admin2": None,
        "city": "Bengaluru",
    }
    assert label["notes"] == "phone at home"
    assert label["recorded"]["advisory"]["admin1"] == "Karnataka"
    assert label["labeled_by"]["id"] == str(owner.id)

    detail = (await owner.client.get(f"/api/v1/visits/{visit_id}")).json()
    assert detail["ground_truth_label"]["id"] == label["id"]

    again = await _label(owner, visit_id)
    assert again.status_code == 409 and again.json()["code"] == "GROUND_TRUTH_EXISTS"

    changed = await owner.client.patch(
        f"{GT}/{label['id']}", json={"city": "Mysuru"}, headers=owner.headers()
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["truth"]["city"] == "Mysuru"
    assert changed.json()["network"] == "act", "a field left out is kept"

    cant = await owner.client.patch(
        f"{GT}/{label['id']}", json={"cant_tell": True}, headers=owner.headers()
    )
    assert cant.status_code == 200, cant.text
    assert cant.json()["cant_tell"] is True
    assert cant.json()["truth"]["country_code"] is None

    gone = await owner.client.delete(f"{GT}/{label['id']}", headers=owner.headers())
    assert gone.status_code == 204
    actions = await helpers.audit_actions_for_target(visit_id)
    assert actions.count(audit.Action.GROUND_TRUTH_LABELLED) == 1
    assert actions.count(audit.Action.GROUND_TRUTH_UPDATED) == 2
    assert actions.count(audit.Action.GROUND_TRUTH_DELETED) == 1
    updated = await helpers.audit_details_for_target(visit_id, audit.Action.GROUND_TRUTH_UPDATED)
    assert updated[0]["before"]["city"] == "Bengaluru"
    assert updated[0]["after"]["city"] == "Mysuru"
    labelled = await helpers.audit_details_for_target(visit_id, audit.Action.GROUND_TRUTH_LABELLED)
    assert "notes" not in labelled[0]["label"], "free text stays out of the audit log"


async def test_an_analyst_reads_but_cannot_label(
    db_client: AsyncClient, integration_settings: Settings, analyst: SignedIn
) -> None:
    visit_id = await _inferred_visit(db_client, integration_settings)
    refused = await _label(analyst, visit_id)
    assert refused.status_code == 403 and refused.json()["code"] == "FORBIDDEN_ROLE"
    assert (await analyst.client.get(GT)).status_code == 200
    assert (await analyst.client.get(f"{GT}/metrics")).status_code == 200
    assert (await analyst.client.get(f"{GT}/queue")).status_code == 200
    run = await analyst.client.post(
        f"{GT}/runs", json={"note": RUN_NOTE}, headers=analyst.headers()
    )
    assert run.status_code == 403


async def test_invalid_labels_are_refused_with_every_reason(
    db_client: AsyncClient, integration_settings: Settings, owner: SignedIn
) -> None:
    visit_id = await _inferred_visit(db_client, integration_settings)
    no_country = await _label(owner, visit_id, country_code=None)
    assert no_country.status_code == 422
    fields = {e["field"] for e in no_country.json()["errors"]}
    assert "country_code" in fields

    no_state = await _label(owner, visit_id, admin1=None)
    assert no_state.status_code == 422
    assert {e["field"] for e in no_state.json()["errors"]} == {"admin1"}

    no_fix = await _label(owner, visit_id, use_gps=True)
    assert no_fix.status_code == 422
    assert [e["code"] for e in no_fix.json()["errors"]] == ["NO_GPS_FIX"]

    unknown = await _label(owner, uuid.uuid4())
    assert unknown.status_code == 404


async def test_a_label_goes_with_its_visit(
    db_client: AsyncClient, integration_settings: Settings, owner: SignedIn
) -> None:
    visit_id = await _inferred_visit(db_client, integration_settings)
    assert (await _label(owner, visit_id)).status_code == 201
    async with session_scope() as db:
        await db.execute(text("DELETE FROM visits WHERE id = :id"), {"id": visit_id})
    async with session_scope() as db:
        left = (
            await db.execute(
                text("SELECT count(*) FROM ground_truth_labels WHERE visit_id = :id"),
                {"id": visit_id},
            )
        ).scalar_one()
    assert left == 0


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


async def test_the_queue_offers_unlabelled_visits_and_drops_labelled_ones(
    db_client: AsyncClient, integration_settings: Settings, owner: SignedIn
) -> None:
    visit_id = await _inferred_visit(db_client, integration_settings)
    link_id = (await ch.get_visit(visit_id)).link_id
    queued = (await owner.client.get(f"{GT}/queue", params={"link_id": str(link_id)})).json()
    assert [i["visit"]["id"] for i in queued["items"]] == [str(visit_id)]
    assert queued["items"][0]["conflict_score"] is not None

    assert (await _label(owner, visit_id)).status_code == 201
    after = (await owner.client.get(f"{GT}/queue", params={"link_id": str(link_id)})).json()
    assert after["items"] == []
    assert after["labelled"] >= 1


# ---------------------------------------------------------------------------
# Metrics, runs and the analytics card
# ---------------------------------------------------------------------------


async def test_metrics_score_the_labels_with_counts_and_intervals(
    db_client: AsyncClient, integration_settings: Settings, owner: SignedIn
) -> None:
    visit_id = await _inferred_visit(db_client, integration_settings)
    assert (await _label(owner, visit_id)).status_code == 201

    report = (await owner.client.get(f"{GT}/metrics")).json()
    assert report["label_count"] >= 1
    assert report["inference_version"].endswith(f"+s{report['settings_version']}")
    network = next(p for p in report["populations"] if p["population"] == "network_only")
    admin1 = next(lv for lv in network["levels"] if lv["level"] == "admin1")
    assert admin1["label_count"] >= 1
    assert admin1["advisory_accuracy"]["n"] == admin1["label_count"]
    assert admin1["advisory_accuracy"]["ci95"] is not None
    assert {t["id"] for t in report["targets"]} >= {"admin1.strict_precision"}

    missing = await owner.client.get(f"{GT}/metrics", params={"settings_version": 999_999})
    assert missing.status_code == 404

    card = (await owner.client.get("/api/v1/analytics/accuracy")).json()
    assert card["population"] == "network_only"
    country = next(lv for lv in card["levels"] if lv["level"] == "country")
    assert country["label_count"] >= 1
    assert country["reason"] in (None, "no_strict_emissions")


async def test_an_owner_records_a_run_and_history_cannot_be_rewritten(
    db_client: AsyncClient, integration_settings: Settings, owner: SignedIn
) -> None:
    visit_id = await _inferred_visit(db_client, integration_settings)
    assert (await _label(owner, visit_id)).status_code == 201

    recorded = await owner.client.post(
        f"{GT}/runs", json={"note": RUN_NOTE}, headers=owner.headers()
    )
    assert recorded.status_code == 201, recorded.text
    run = recorded.json()
    assert run["origin"] == "dashboard"
    assert run["label_count"] == run["metrics"]["label_count"]
    assert run["recorded_by"]["id"] == str(owner.id)

    listed = (await owner.client.get(f"{GT}/runs")).json()
    assert run["id"] in {r["id"] for r in listed}
    one = (await owner.client.get(f"{GT}/runs/{run['id']}")).json()
    assert one["metrics"]["inference_version"] == run["inference_version"]

    async with session_scope() as db:
        role = (await db.execute(text("SELECT current_user"))).scalar_one()
    if role != "tracelet_app":
        pytest.skip(f"connected as {role!r}; the role split is not exercised")
    for statement in ("UPDATE accuracy_runs SET passed = NOT passed", "DELETE FROM accuracy_runs"):
        with pytest.raises(Exception, match="permission denied"):
            async with session_scope() as db:
                await db.execute(text(statement))
