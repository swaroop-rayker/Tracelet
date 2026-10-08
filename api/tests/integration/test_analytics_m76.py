"""M7.6 analytics against the real database: Sources, new and returning visitors, carriers
by state, capture quality by platform (F9.AC21-F9.AC24, SPEC section 11 row 28).

Parity with the rollups is asserted with the other endpoints in ``test_analytics.py``;
this file checks what each figure means. Visits come through the real capture path and are
then shaped with a direct UPDATE, as there: what is under test is counting.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import uuid
import zoneinfo
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update

from tests.integration import capture_helpers as ch
from tests.integration.helpers import SignedIn
from tracelet.analytics import rollup
from tracelet.capture.models import (
    Classification,
    ConnectionClass,
    ConsentState,
    Link,
    Visit,
    VisitStage,
)
from tracelet.capture.signals import referrer_origin
from tracelet.db.engine import session_scope

pytestmark = pytest.mark.integration

A = "/api/v1/analytics"
ZONE = "Asia/Kolkata"
TZ = zoneinfo.ZoneInfo(ZONE)


def _today() -> dt.date:
    return dt.datetime.now(TZ).date()


def _window(first: dt.date, days: int) -> dict[str, str]:
    start = dt.datetime.combine(first, dt.time(0), tzinfo=TZ)
    return {"from": start.isoformat(), "to": (start + dt.timedelta(days=days)).isoformat()}


_peer = iter(range(1, 60_000))


async def _visit(client: AsyncClient, link: Link, **values: Any) -> uuid.UUID:
    """A captured visit, shaped with ``values``: human and enriched unless told otherwise."""
    n = next(_peer)
    await ch.visit(client, link.slug, peer=f"198.51.{100 + n // 250}.{n % 250 + 1}")
    visit = await ch.latest_visit(link.id)
    values.setdefault("stage", VisitStage.ENRICHED)
    values.setdefault("classification", Classification.HUMAN)
    values.setdefault("finalized_at", dt.datetime.now(dt.UTC))
    values.setdefault("inferred_at", dt.datetime.now(dt.UTC))
    async with session_scope() as db:
        await db.execute(update(Visit).where(Visit.id == visit.id).values(**values))
    return visit.id


async def _rebuild(*days: dt.date) -> None:
    async with session_scope() as db:
        await rollup.refresh_days(db, list(days), ZONE)
        await db.commit()


# ---------------------------------------------------------------------------
# The migration cuts stored referrers exactly as capture does (row 28)
# ---------------------------------------------------------------------------


def _migration_origin_sql() -> str:
    path = Path(__file__).parents[2] / "alembic" / "versions" / "20261008_0016_sources.py"
    spec = importlib.util.spec_from_file_location("m0016", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.ORIGIN)


@pytest.mark.parametrize(
    "referer",
    [
        "https://www.google.com/search?q=my+name",
        "https://L.Instagram.com/?u=x&e=AT0token#frag",
        "http://example.org:8080/a/b",
        "android-app://com.google.android.gm/",
        "https://user:secret@example.org/x",
        "  https://example.org",
        "/relative/path",
        "example.org/page",
        "https:///no-host",
    ],
)
async def test_the_migration_cuts_a_stored_referer_as_capture_does(
    db_app: object, referer: str
) -> None:
    del db_app
    sql = _migration_origin_sql().format(value="CAST(:v AS text)")
    async with session_scope() as db:
        cut = (await db.execute(text(f"SELECT {sql}"), {"v": referer})).scalar_one()
    assert cut == referrer_origin(referer)


# ---------------------------------------------------------------------------
# Sources (F9.AC21)
# ---------------------------------------------------------------------------


async def test_sources_rank_referrers_and_tags_and_count_none(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    link = await ch.create_link()
    tagged = {"utm_source": "ig", "utm_medium": "social", "utm_campaign": "diwali"}
    ig = await _visit(db_client, link, referer="https://l.instagram.com", utm=tagged)
    await _visit(db_client, link, referer="https://l.instagram.com")
    await _visit(db_client, link, referer="android-app://com.google.android.gm")
    await _visit(db_client, link)  # no referrer, no tags: None
    await _rebuild(_today())
    params = {"link_id": str(link.id), **_window(_today(), 1)}

    async def ranked(dimension: str, **extra: str) -> dict[str, Any]:
        r = await owner.client.get(
            A + "/breakdown", params={**params, "dimension": dimension, **extra}
        )
        assert r.status_code == 200, r.text
        body: dict[str, Any] = r.json()
        return body

    hosts = await ranked("referrer_host")
    assert [(r["key"], r["count"]) for r in hosts["rows"]] == [
        ("l.instagram.com", 2),
        ("android-app://com.google.android.gm", 1),
    ]
    assert hosts["unknown"] == 1, "no referrer is counted as None, not hidden"
    assert hosts["meta"]["computed_from"] == "rollup"
    campaign = await ranked("utm_campaign")
    assert [(r["key"], r["count"]) for r in campaign["rows"]] == [("diwali", 1)]
    assert campaign["unknown"] == 3

    # The filter a clicked row applies selects exactly the visits it counted.
    listed = await owner.client.get(
        "/api/v1/visits", params={**params, "referrer_host": "L.INSTAGRAM.COM"}
    )
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 2
    one = await owner.client.get("/api/v1/visits", params={**params, "utm_campaign": "diwali"})
    assert [i["id"] for i in one.json()["items"]] == [str(ig)]
    filtered = await ranked("utm_source", referrer_host="l.instagram.com")
    assert filtered["meta"]["computed_from"] == "raw"
    assert filtered["total"] == 2


# ---------------------------------------------------------------------------
# New and returning visitors (F9.AC22)
# ---------------------------------------------------------------------------


async def test_new_and_returning_visitors_cohorts_and_time_to_return(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    link = await ch.create_link()
    today = _today()
    x, y = uuid.uuid4().bytes, uuid.uuid4().bytes

    def at(days_ago: int) -> dt.datetime:
        return dt.datetime.combine(today - dt.timedelta(days=days_ago), dt.time(12), TZ)

    def minutes_ago(n: int) -> dt.datetime:
        # Today, and before the next capture: latest_visit finds each new one.
        return dt.datetime.now(dt.UTC) - dt.timedelta(minutes=n)

    # X: first 10 days ago, back 4 days ago (six days later) and today. Y: today only.
    await _visit(db_client, link, visitor_id=x, occurred_at=at(10))
    await _visit(db_client, link, visitor_id=x, occurred_at=at(4))
    await _visit(db_client, link, visitor_id=x, occurred_at=minutes_ago(4))
    await _visit(db_client, link, visitor_id=y, occurred_at=minutes_ago(3))
    await _visit(db_client, link, visitor_id=None, occurred_at=minutes_ago(2))
    await _visit(
        db_client, link, visitor_id=y, occurred_at=minutes_ago(1), classification=Classification.BOT
    )

    r = await owner.client.get(
        A + "/returning",
        params={
            "link_id": str(link.id),
            "weeks": "2",
            **_window(today - dt.timedelta(days=13), 14),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["computed_from"] == "raw"
    by_day = {d["day"]: (d["new"], d["returning"]) for d in body["days"]}
    assert by_day[(today - dt.timedelta(days=10)).isoformat()] == (1, 0)
    assert by_day[(today - dt.timedelta(days=4)).isoformat()] == (0, 1)
    assert by_day[today.isoformat()] == (1, 1), "the bot visit is not counted"
    assert body["unidentified"] == 1
    assert body["since"] <= (today - dt.timedelta(days=10)).isoformat()

    cohorts = {c["week"]: c for c in body["cohorts"]}
    x_week = (today - dt.timedelta(days=10)) - dt.timedelta(
        days=(today - dt.timedelta(days=10)).weekday()
    )
    assert cohorts[x_week.isoformat()]["size"] == 1
    returned = cohorts[x_week.isoformat()]["returned"]
    assert len(returned) == 3 and sum(v or 0 for v in returned) >= 1
    assert sum(c["size"] for c in body["cohorts"]) == 2

    bands = {b["band"]: b["count"] for b in body["return_after"]}
    assert bands == {"under_1h": 0, "1h_1d": 0, "1d_7d": 1, "7d_30d": 0, "over_30d": 0}


# ---------------------------------------------------------------------------
# Mobile networks by state (F9.AC23)
# ---------------------------------------------------------------------------


async def test_carriers_by_best_guess_state(owner: SignedIn, db_client: AsyncClient) -> None:
    link = await ch.create_link()
    karnataka = {
        "advisory_country_code": "IN",
        "advisory_admin1": "Karnataka",
        "confidence_admin1": Decimal("0.800"),
    }
    await _visit(db_client, link, asn=55836, connection_class=ConnectionClass.MOBILE, **karnataka)
    await _visit(
        db_client, link, asn=24560, connection_class=ConnectionClass.BROADBAND, **karnataka
    )
    await _visit(
        db_client,
        link,
        asn=45609,
        connection_class=ConnectionClass.MOBILE,
        **karnataka | {"confidence_admin1": Decimal("0.500")},
    )
    await _visit(
        db_client, link, asn=64512, connection_class=ConnectionClass.BROADBAND, **karnataka
    )
    await _visit(db_client, link, asn=9829, connection_class=ConnectionClass.BROADBAND)  # unplaced
    await _rebuild(_today())

    r = await owner.client.get(
        A + "/carriers", params={"link_id": str(link.id), **_window(_today(), 1)}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    (state,) = body["states"]
    assert state["key"] == "IN|Karnataka"
    assert state["visits"] == 4
    assert state["families"] == {"jio": 1, "airtel": 2, "vi": 0, "bsnl": 0, "other": 1}
    assert (state["mobile"], state["broadband"], state["other_network"]) == (2, 2, 0)
    assert state["confidence"] == pytest.approx(0.725)
    assert body["unplaced"] == 1


# ---------------------------------------------------------------------------
# Capture quality by platform (F9.AC24)
# ---------------------------------------------------------------------------


async def test_capture_quality_by_app_matches_the_funnel(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    link = await ch.create_link()
    ig = {"is_inapp_webview": True, "webview_host": "instagram"}
    await _visit(db_client, link, **ig)
    await _visit(db_client, link, stage=VisitStage.SERVER_ONLY, **ig)
    await _visit(db_client, link, stage=VisitStage.SERVER_ONLY, **ig)
    await _visit(db_client, link, consent_state=ConsentState.GRANTED)
    await _rebuild(_today())
    params = {"link_id": str(link.id), **_window(_today(), 1)}

    r = await owner.client.get(A + "/capture-quality", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    apps = {a["key"]: a for a in body["apps"]}
    assert apps["instagram"] | {"key": None} == {
        "key": None,
        "captured": 3,
        "enriched": 1,
        "server_only": 2,
        "pending": 0,
        "consented": 0,
    }
    assert (apps["browser"]["captured"], apps["browser"]["consented"]) == (1, 1)
    share = {s["key"]: s["enriched_share"][-1] for s in body["series"]}
    assert share["instagram"] == pytest.approx(1 / 3, abs=1e-4)

    funnel = (await owner.client.get(A + "/funnel", params=params)).json()
    steps = {s["step"]: s["count"] for s in funnel["steps"]}
    assert sum(a["captured"] for a in body["apps"]) == steps["captured"]
    assert sum(a["enriched"] for a in body["apps"]) == steps["enriched"]
