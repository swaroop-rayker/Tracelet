"""Analytics against the real database (ADR-0016, F9.AC2-F9.AC20, docs/API.md section 8).

The central test is **parity**: every endpoint returns the same figures whether it is
served from the rollups or from raw rows. Both paths read one projection, and this is
what proves it -- a projection change that broke one path and not the other fails here.

Visits are made through the real capture path, then given the attributes a test needs
with a direct UPDATE: inference and classification have their own suites, and what is
under test here is counting, not deciding.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import uuid
import zoneinfo
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select, update

from tests.integration import capture_helpers as ch
from tests.integration.helpers import SignedIn
from tracelet.analytics import rollup
from tracelet.analytics.models import DailyCell, HourlyCell, RollupState
from tracelet.capture.models import (
    Classification,
    ConnectionClass,
    DeviceClass,
    Link,
    Visit,
    VisitStage,
)
from tracelet.db.engine import session_scope
from tracelet.inference.models import VisitCandidate
from tracelet.inference.types import GeoLevel, InferenceSource

pytestmark = pytest.mark.integration

A = "/api/v1/analytics"
ZONE = "Asia/Kolkata"
TZ = zoneinfo.ZoneInfo(ZONE)
VISITOR_A = bytes(range(32))
VISITOR_B = bytes(range(1, 33))


def _today() -> dt.date:
    return dt.datetime.now(TZ).date()


def _window(first: dt.date, days: int = 1) -> dict[str, str]:
    start = dt.datetime.combine(first, dt.time(0), tzinfo=TZ)
    return {"from": start.isoformat(), "to": (start + dt.timedelta(days=days)).isoformat()}


async def _visit(client: AsyncClient, link: Link, peer: str, **values: Any) -> uuid.UUID:
    """A visit through the real capture path, then shaped with ``values``."""
    await ch.visit(client, link.slug, peer=peer)
    visit = await ch.latest_visit(link.id)
    values.setdefault("stage", VisitStage.ENRICHED)
    values.setdefault("finalized_at", dt.datetime.now(dt.UTC))
    async with session_scope() as db:
        await db.execute(update(Visit).where(Visit.id == visit.id).values(**values))
    return visit.id


def _candidate(visit_id: uuid.UUID, source: InferenceSource, *, accepted: bool) -> VisitCandidate:
    return VisitCandidate(
        visit_id=visit_id,
        source=source,
        level=GeoLevel.CITY,
        country_code="IN",
        admin1="Karnataka",
        city="Bengaluru",
        raw_confidence=Decimal("0.900"),
        weight=Decimal("1.0"),
        effective_weight=Decimal("1.0"),
        accepted=accepted,
        suppressed_reason=None if accepted else "outvoted",
        evidence={},
        latency_ms=3,
    )


@pytest.fixture
async def dataset(db_client: AsyncClient) -> dict[str, Any]:
    """Five visits on one link, today, covering every dimension the endpoints read."""
    link = await ch.create_link()
    now = dt.datetime.now(dt.UTC)
    bot_signal = {"rule_id": "client.webdriver", "category": "bot", "weight": 60, "detail": {}}
    absence = {"rule_id": "identity.server_only", "category": "absence", "weight": 0, "detail": {}}
    v1 = await _visit(
        db_client,
        link,
        "49.207.1.10",
        classification=Classification.HUMAN,
        device_class=DeviceClass.MOBILE,
        connection_class=ConnectionClass.BROADBAND,
        strict_country_code="IN",
        strict_admin1="Karnataka",
        strict_city="Bengaluru",
        strict_lat=Decimal("12.971600"),
        strict_lng=Decimal("77.594600"),
        advisory_country_code="IN",
        advisory_admin1="Karnataka",
        advisory_city="Bengaluru",
        advisory_lat=Decimal("12.971600"),
        advisory_lng=Decimal("77.594600"),
        confidence_country=Decimal("0.990"),
        confidence_admin1=Decimal("0.910"),
        confidence_city=Decimal("0.970"),
        inferred_at=now,
        asn=24560,
        asn_org="Bharti Airtel",
        visitor_id=VISITOR_A,
        screen_w=1080,
        screen_h=2400,
        signals=[],
    )
    v2 = await _visit(
        db_client,
        link,
        "49.207.2.10",
        stage=VisitStage.SERVER_ONLY,
        classification=Classification.HUMAN,
        device_class=DeviceClass.MOBILE,
        connection_class=ConnectionClass.MOBILE,
        strict_country_code="IN",
        strict_admin1="Karnataka",
        advisory_country_code="IN",
        advisory_admin1="Maharashtra",
        advisory_city="Mumbai",
        advisory_lat=Decimal("19.076000"),
        advisory_lng=Decimal("72.877700"),
        confidence_country=Decimal("0.950"),
        confidence_admin1=Decimal("0.800"),
        confidence_city=Decimal("0.300"),
        abstain_reason={"city": "below_threshold"},
        inferred_at=now,
        asn=55836,
        asn_org="Reliance Jio Infocomm",
        visitor_id=VISITOR_A,
        signals=[absence],
    )
    v3 = await _visit(
        db_client,
        link,
        "49.207.3.10",
        classification=Classification.BOT,
        device_class=DeviceClass.DESKTOP,
        connection_class=ConnectionClass.DATACENTER,
        abstain_reason={"country": "hosting_asn"},
        inferred_at=now,
        asn=16509,
        asn_org="Amazon.com",
        visitor_id=VISITOR_B,
        signals=[bot_signal, absence],
    )
    v4 = await _visit(db_client, link, "49.207.4.10", classification=Classification.HUMAN)
    async with session_scope() as db:
        db.add_all(
            [
                _candidate(v1, InferenceSource.GEOLITE2, accepted=True),
                _candidate(v1, InferenceSource.RDNS, accepted=True),
                _candidate(v2, InferenceSource.GEOLITE2, accepted=False),
            ]
        )
    # Rebuild today, so a test reads the same figures whichever path serves it. On a dev
    # database the running scheduler may have built every day in a default window, and
    # a rollup built before these visits existed would be up to five minutes stale --
    # correct behaviour (meta.refreshed_at says so), but not what these tests measure.
    await _built_today()
    return {"link": link, "ids": [v1, v2, v3, v4]}


async def _built_today() -> None:
    async with session_scope() as db:
        await rollup.refresh_days(db, [_today()], ZONE)
        await db.commit()


def _comparable(body: dict[str, Any]) -> dict[str, Any]:
    """A response without the fields that legitimately differ between the two paths."""
    copy: dict[str, Any] = json.loads(json.dumps(body))
    copy["meta"].pop("computed_from")
    copy["meta"].pop("refreshed_at")
    return copy


ENDPOINTS: list[tuple[str, dict[str, str]]] = [
    ("/summary", {}),
    ("/timeseries", {"bucket": "day"}),
    ("/timeseries", {"bucket": "hour", "split_by": "classification"}),
    ("/timeseries", {"bucket": "day", "split_by": "admin1"}),
    ("/calendar", {}),
    *[("/breakdown", {"dimension": d}) for d in ("country", "admin1", "city", "isp", "screen")],
    ("/signals", {}),
    ("/confidence", {}),
    ("/source-flow", {}),
    ("/funnel", {}),
    ("/accuracy", {}),
    ("/geo", {}),
]


@pytest.mark.parametrize(("path", "extra"), ENDPOINTS)
async def test_rollups_and_raw_rows_give_the_same_answer(
    owner: SignedIn, dataset: dict[str, Any], path: str, extra: dict[str, str]
) -> None:
    """ADR-0016's core claim: one projection, two paths, identical figures."""
    await _built_today()
    params = {
        "link_id": str(dataset["link"].id),
        "include_automated": "true",
        **_window(_today()),
        **extra,
    }
    rolled = await owner.client.get(A + path, params=params)
    # has_gps=false matches every visit here and is not a rollup dimension, so it
    # forces the raw path over exactly the same rows.
    raw = await owner.client.get(A + path, params={**params, "has_gps": "false"})

    assert rolled.status_code == 200, rolled.text
    assert raw.status_code == 200, raw.text
    assert rolled.json()["meta"]["computed_from"] == "rollup"
    assert raw.json()["meta"]["computed_from"] == "raw"
    assert _comparable(rolled.json()) == _comparable(raw.json())


async def test_a_day_never_built_is_answered_from_raw_rows_not_reported_empty(
    owner: SignedIn, dataset: dict[str, Any]
) -> None:
    """A missing rollup must not read as "no visits" -- that would be B5 again."""
    # Today plus 400 days nobody has built, because they have not happened.
    params = {"link_id": str(dataset["link"].id), **_window(_today(), days=401)}
    body = (await owner.client.get(A + "/calendar", params=params)).json()
    assert body["meta"]["computed_from"] == "raw"
    assert body["days"][0]["count"] == 3, "today's non-automated visits, counted live"


async def test_every_response_states_its_stage_mix(
    owner: SignedIn, dataset: dict[str, Any]
) -> None:
    """F9.AC20."""
    params = {"link_id": str(dataset["link"].id), "include_automated": "true"}
    for path, extra in ENDPOINTS:
        body = (await owner.client.get(A + path, params={**params, **extra})).json()
        mix = body["meta"]["stage_mix"]
        assert mix["total"] == 4, path
        assert (mix["enriched"], mix["server_only"]) == (3, 1), path
        assert body["meta"]["reporting_tz"] == ZONE


async def test_the_summary_counts_and_shares(owner: SignedIn, dataset: dict[str, Any]) -> None:
    body = (
        await owner.client.get(A + "/summary", params={"link_id": str(dataset["link"].id)})
    ).json()
    kpis = {k["key"]: k for k in body["kpis"]}
    # Default view excludes automated traffic: three of the four visits.
    assert kpis["visits"]["value"] == 3
    assert kpis["unique_visitors"]["value"] == 1, "v4 has no visitor_id; v1 and v2 share one"
    # Shares are over every classification, whatever the filter.
    assert kpis["human_share"]["value"] == pytest.approx(3 / 4)
    assert kpis["bot_share"]["value"] == pytest.approx(1 / 4)
    assert kpis["geofence_hit_rate"]["value"] is None
    assert kpis["geofence_hit_rate"]["reason"] == "no_geofence_evaluations"
    assert kpis["enrichment_completion_rate"]["value"] == pytest.approx(2 / 3)


async def test_location_breakdowns_count_strict_only_and_report_abstentions(
    owner: SignedIn, dataset: dict[str, Any]
) -> None:
    """Invariant 5: v2's advisory Mumbai must not be counted as a city."""
    params = {"link_id": str(dataset["link"].id), "include_automated": "true"}
    city = (await owner.client.get(A + "/breakdown", params={**params, "dimension": "city"})).json()
    assert [(r["key"], r["count"]) for r in city["rows"]] == [("IN|Karnataka|Bengaluru", 1)]
    assert city["unknown"] == 3
    assert city["total"] == 4

    admin1 = (
        await owner.client.get(A + "/breakdown", params={**params, "dimension": "admin1"})
    ).json()
    assert [(r["key"], r["count"]) for r in admin1["rows"]] == [("IN|Karnataka", 2)]


async def test_signals_rank_detections_and_ignore_absences(
    owner: SignedIn, dataset: dict[str, Any]
) -> None:
    params = {"link_id": str(dataset["link"].id), "include_automated": "true"}
    body = (await owner.client.get(A + "/signals", params=params)).json()
    assert [(r["rule_id"], r["category"], r["count"]) for r in body["rows"]] == [
        ("client.webdriver", "bot", 1)
    ]
    assert body["rows"][0]["share"] == pytest.approx(1 / 4)


async def test_the_source_flow_ends_each_source_at_the_level_emitted(
    owner: SignedIn, dataset: dict[str, Any]
) -> None:
    params = {"link_id": str(dataset["link"].id), "include_automated": "true"}
    body = (await owner.client.get(A + "/source-flow", params=params)).json()
    links = {(x["source"], x["target"]): x["value"] for x in body["links"]}
    assert links == {
        ("geolite2", "city"): 1,
        ("rdns", "city"): 1,
        ("geolite2", "admin1"): 1,
        ("none", "none"): 1,  # v3: inferred, no source had anything, full abstention
    }
    assert body["visits"] == 3, "v4 was never inferred, so it is queue, not abstention"


async def test_the_funnel_and_accuracy_say_what_they_cannot_know_yet(
    owner: SignedIn, dataset: dict[str, Any]
) -> None:
    """F3.AC5: null with a reason, never a zero a chart would plot."""
    params = {"link_id": str(dataset["link"].id), "include_automated": "true"}
    funnel = (await owner.client.get(A + "/funnel", params=params)).json()
    steps = {s["step"]: s for s in funnel["steps"]}
    assert steps["requests"]["count"] == 4
    assert steps["enriched"]["count"] == 3
    assert steps["notified"]["count"] is None
    assert steps["notified"]["reason"] == "notifications_not_built"

    accuracy = (await owner.client.get(A + "/accuracy", params=params)).json()
    for level in accuracy["levels"]:
        assert level["label_count"] == 0
        assert level["precision"] is None
        assert level["reason"] == "no_ground_truth_labels"
    emitted = {lv["level"]: lv["emission_rate"] for lv in accuracy["levels"]}
    assert emitted["country"] == pytest.approx(2 / 3)
    assert emitted["city"] == pytest.approx(1 / 3)


async def test_the_map_plots_strict_points_only(owner: SignedIn, dataset: dict[str, Any]) -> None:
    params = {"link_id": str(dataset["link"].id), "include_automated": "true"}
    body = (await owner.client.get(A + "/geo", params=params)).json()
    assert body["countries"] == [{"country_code": "IN", "count": 2}]
    assert body["abstained"] == 2
    assert [(round(p["lat"], 2), p["count"]) for p in body["points"]] == [(12.97, 1)]


async def test_days_are_local_to_the_reporting_timezone(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """00:15 in India is 18:45 the previous day in UTC; it belongs to the Indian day."""
    link = await ch.create_link()
    local = dt.datetime.combine(_today(), dt.time(0, 15), tzinfo=TZ)
    await _visit(db_client, link, "49.207.9.10", occurred_at=local.astimezone(dt.UTC))
    yesterday = _today() - dt.timedelta(days=1)
    async with session_scope() as db:
        await rollup.refresh_days(db, [yesterday, _today()], ZONE)
        await db.commit()
    params = {"link_id": str(link.id), **_window(yesterday, days=2)}
    rolled = (await owner.client.get(A + "/calendar", params=params)).json()
    raw = (await owner.client.get(A + "/calendar", params={**params, "has_gps": "false"})).json()
    assert rolled["meta"]["computed_from"] == "rollup"
    assert [d["count"] for d in rolled["days"]] == [0, 1]
    assert [d["count"] for d in raw["days"]] == [0, 1]


async def test_the_visitor_view_shows_drift(owner: SignedIn, dataset: dict[str, Any]) -> None:
    del dataset  # provides VISITOR_A's two visits
    body = (await owner.client.get(f"{A}/visitor/{VISITOR_A.hex()}")).json()
    assert body["visit_count"] == 2
    assert [v["is_returning"] for v in body["visits"]] == [False, True]
    (drift,) = body["drift"]
    assert drift["location_changed"] == ["admin1", "city"]
    assert drift["distance_km"] == pytest.approx(845, rel=0.05), "Bengaluru to Mumbai"
    assert drift["network_changed"] is True


async def test_a_bad_window_is_a_422(owner: SignedIn) -> None:
    later = dt.datetime.now(dt.UTC)
    params = {"from": later.isoformat(), "to": (later - dt.timedelta(days=1)).isoformat()}
    assert (await owner.client.get(A + "/summary", params=params)).status_code == 422
    hours = {**_window(_today(), days=40), "bucket": "hour"}
    assert (await owner.client.get(A + "/timeseries", params=hours)).status_code == 422


async def test_analytics_needs_a_session(db_client: AsyncClient) -> None:
    assert (await db_client.get(A + "/summary")).status_code == 401


# ---------------------------------------------------------------------------
# Visit list filters and export (F9.AC13, F9.AC15)
# ---------------------------------------------------------------------------

VISITS = "/api/v1/visits"


async def test_list_filters_compose(owner: SignedIn, dataset: dict[str, Any]) -> None:
    link_id = str(dataset["link"].id)
    v1, v2, _, _ = dataset["ids"]

    async def ids(**params: str) -> list[str]:
        body = (await owner.client.get(VISITS, params={"link_id": link_id, **params})).json()
        return [item["id"] for item in body["items"]]

    assert await ids(country_code="in", sort="oldest") == [str(v1), str(v2)]
    assert await ids(search="jio") == [str(v2)]
    assert await ids(country_code="IN", connection_class="broadband") == [str(v1)]
    assert await ids(min_confidence_city="0.9") == [str(v1)]
    assert await ids(visitor_id=VISITOR_A.hex(), sort="oldest") == [str(v1), str(v2)]
    assert await ids(asn="16509") == [], "a bot, excluded by default"
    assert await ids(asn="16509", include_automated="true") != []


async def test_a_malformed_visitor_id_is_a_422(owner: SignedIn) -> None:
    response = await owner.client.get(VISITS, params={"visitor_id": "zz"})
    assert response.status_code == 422


async def test_the_csv_export_streams_every_row_without_the_address(
    owner: SignedIn, dataset: dict[str, Any]
) -> None:
    link_id = str(dataset["link"].id)
    v1 = dataset["ids"][0]
    async with session_scope() as db:
        # Attacker-controlled text that a spreadsheet would execute.
        await db.execute(
            update(Visit).where(Visit.id == v1).values(asn_org='=HYPERLINK("http://x")')
        )
    response = await owner.client.get(
        VISITS + "/export", params={"link_id": link_id, "include_automated": "true"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 4
    by_id = {r["id"]: r for r in rows}
    assert by_id[str(v1)]["asn_org"].startswith("'="), "formula neutralised"
    assert by_id[str(v1)]["strict_city"] == "Bengaluru"
    for address in ("49.207.1.10", "49.207.2.10", "49.207.3.10", "49.207.4.10"):
        assert address not in response.text


async def test_the_ndjson_export_is_one_visit_per_line(
    owner: SignedIn, dataset: dict[str, Any]
) -> None:
    response = await owner.client.get(
        VISITS + "/export", params={"link_id": str(dataset["link"].id), "format": "ndjson"}
    )
    lines = response.text.strip().split("\n")
    assert len(lines) == 3, "the default view, automated excluded"
    assert {json.loads(line)["id"] for line in lines} == {str(i) for i in dataset["ids"]} - {
        str(dataset["ids"][2])
    }
    assert json.loads(lines[0])["device"]["class"] in {"mobile", "unknown"}


# ---------------------------------------------------------------------------
# Preferences (F9.AC16)
# ---------------------------------------------------------------------------


async def test_the_theme_is_persisted_per_admin(owner: SignedIn) -> None:
    before = (await owner.client.get("/api/v1/auth/me")).json()
    assert before["theme"] == "semi_dark", "semi-dark is the default"
    changed = await owner.client.patch(
        "/api/v1/auth/me/preferences", json={"theme": "light"}, headers=owner.headers()
    )
    assert changed.status_code == 200
    assert (await owner.client.get("/api/v1/auth/me")).json()["theme"] == "light"


async def test_preferences_refuse_unknown_values(owner: SignedIn) -> None:
    for payload in ({"theme": "neon"}, {"timezone": "Mars/Olympus"}, {"role": "owner"}):
        response = await owner.client.patch(
            "/api/v1/auth/me/preferences", json=payload, headers=owner.headers()
        )
        assert response.status_code == 422, payload


# ---------------------------------------------------------------------------
# The refresh jobs (ADR-0016)
# ---------------------------------------------------------------------------


async def _state(day: dt.date) -> dt.datetime | None:
    async with session_scope() as db:
        row = await db.get(RollupState, day)
        return row.refreshed_at if row is not None and row.reporting_tz == ZONE else None


async def test_the_live_job_rebuilds_yesterday_and_today(dataset: dict[str, Any]) -> None:
    del dataset
    started = dt.datetime.now(dt.UTC)
    assert await rollup.run_live_once() == 2
    for day in (_today(), _today() - dt.timedelta(days=1)):
        refreshed = await _state(day)
        assert refreshed is not None and refreshed >= started


async def test_a_never_built_day_is_found_for_back_fill(db_client: AsyncClient) -> None:
    link = await ch.create_link()
    day = _today() - dt.timedelta(days=3)
    moment = dt.datetime.combine(day, dt.time(12), tzinfo=TZ)
    await _visit(db_client, link, "49.207.8.10", occurred_at=moment.astimezone(dt.UTC))
    async with session_scope() as db:
        await db.execute(delete(RollupState).where(RollupState.day == day))
    async with session_scope() as db:
        gaps = await rollup.missing_days(db, ZONE, until=day, limit=100_000)
    assert day in gaps

    async with session_scope() as db:
        await rollup.refresh_days(db, [day], ZONE)
        await db.commit()
    async with session_scope() as db:
        assert day not in await rollup.missing_days(db, ZONE, until=day, limit=100_000)


async def test_hourly_cells_older_than_fourteen_days_are_dropped(db_client: AsyncClient) -> None:
    link = await ch.create_link()
    old = _today() - dt.timedelta(days=20)
    moment = dt.datetime.combine(old, dt.time(12), tzinfo=TZ)
    await _visit(db_client, link, "49.207.7.10", occurred_at=moment.astimezone(dt.UTC))
    async with session_scope() as db:
        await rollup.refresh_days(db, [old], ZONE)
        await db.commit()
    async with session_scope() as db:
        daily = (
            await db.execute(
                select(func.count()).where(DailyCell.link_id == link.id, DailyCell.day == old)
            )
        ).scalar_one()
        hourly = (
            await db.execute(select(func.count()).where(HourlyCell.link_id == link.id))
        ).scalar_one()
    assert (daily, hourly) == (1, 0), "daily history is kept; the hourly table keeps 14 days"
