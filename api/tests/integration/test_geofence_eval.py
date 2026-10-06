"""Geofence evaluation inside the inference job, against PostGIS (F6.AC5-AC8, ADR-0020).

The visitor here is the MTNL Mumbai address of the inference suite: the engine states a
strict India and a strict Maharashtra, and no city, so the visit has **no geopoint**.
That is the shape of almost all Indian ISP traffic (ADR-0020 context), and the reason
region geofences exist.

The suite shares the dev database, and a geofence applies to every visit inferred after
it. So every geofence here is named ``itest ...`` and deleted afterwards, and the job's
loader is narrowed to the geofences the current test made: neither the owner's dev
geofences nor another test's can change a result here.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration import capture_helpers as ch
from tracelet.capture.models import GeofenceState
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.geofence import store
from tracelet.geofence.evaluate import Reason, StrictPlace
from tracelet.geofence.models import Geofence, NotifyPriority, ShapeKind
from tracelet.inference import engine
from tracelet.inference.sources import rdns

pytestmark = pytest.mark.integration

MTNL_MUMBAI_PTR = "triband-mum-49.207.12.34.mtnl.net.in"
# Central Mumbai, and a square around it; and a square around Bengaluru.
MUMBAI = (19.076, 72.8777)
AROUND_MUMBAI = "POLYGON((72.7 18.9, 73.0 18.9, 73.0 19.3, 72.7 19.3, 72.7 18.9))"
AROUND_BENGALURU = "POLYGON((77.4 12.8, 77.8 12.8, 77.8 13.2, 77.4 13.2, 77.4 12.8))"


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def lookup(ip: str, timeout_s: float) -> str | None:
        del timeout_s
        if ip == rdns.CANARY[0]:
            return rdns.CANARY[1]
        return MTNL_MUMBAI_PTR if ip == ch.VISITOR_IP else None

    rdns.reset_canary_for_tests()
    monkeypatch.setattr(rdns, "_lookup", lookup)
    yield
    rdns.reset_canary_for_tests()


@pytest.fixture
async def made(db_app: object, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[set[uuid.UUID]]:
    """The geofences this test made; the job sees only these."""
    del db_app
    ids: set[uuid.UUID] = set()
    load_all = store.load_active

    async def only_ours(db: AsyncSession) -> list[store.ActiveGeofence]:
        return [f for f in await load_all(db) if f.id in ids]

    monkeypatch.setattr(store, "load_active", only_ours)
    yield ids
    async with session_scope() as db:
        await db.execute(text("DELETE FROM geofences WHERE name LIKE 'itest %'"))


async def _fence(
    made: set[uuid.UUID],
    *,
    keys: Sequence[str] | None = None,
    polygon: str | None = None,
    links: Sequence[uuid.UUID] | None = None,
    priority: int = 0,
    active: bool = True,
) -> uuid.UUID:
    fence = Geofence(
        id=uuid.uuid4(),
        name=f"itest {len(made)}",
        shape_kind=ShapeKind.REGION if keys is not None else ShapeKind.POLYGON,
        region_keys=list(keys) if keys is not None else None,
        priority=priority,
        is_active=active,
        notify_priority=NotifyPriority.HIGH,
        link_ids=list(links) if links is not None else None,
    )
    async with session_scope() as db:
        if polygon is None:
            db.add(fence)
            await db.flush()
        else:
            # area is unmapped (geofence/models.py): the row is written in SQL.
            await db.execute(
                text(
                    "INSERT INTO geofences (id, name, shape_kind, area, priority, is_active, "
                    "link_ids) VALUES (:id, :name, 'polygon', "
                    "ST_GeogFromText('SRID=4326;' || :wkt), :priority, :active, :links)"
                ),
                {
                    "id": fence.id,
                    "name": fence.name,
                    "wkt": polygon,
                    "priority": priority,
                    "active": active,
                    "links": fence.link_ids,
                },
            )
    made.add(fence.id)
    return fence.id


async def _visit(client: AsyncClient) -> tuple[uuid.UUID, uuid.UUID]:
    link = await ch.create_link()
    page = await ch.visit(client, link.slug)
    await client.post(
        f"/api/v1/s/{ch.nonce_from(page)}",
        json={"geolocation": {"state": "prompt"}, "locale": {"tzIana": "Asia/Kolkata"}},
        headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP},
    )
    visit_id = (await ch.latest_visit(link.id)).id
    # A Mumbai edge, as in the inference suite: with the PTR, it is what makes the state
    # strict. Written directly because the test client is not a Cloudflare peer.
    async with session_scope() as db:
        await db.execute(text("UPDATE visits SET cf_colo = 'BOM' WHERE id = :id"), {"id": visit_id})
    return link.id, visit_id


async def _infer(settings: Settings, visit_id: uuid.UUID) -> Any:
    assert await engine.run_once(settings, only=[visit_id]) == 1
    visit = await ch.get_visit(visit_id)
    # The premise of this module: strict India and Maharashtra, and no strict city, so
    # no geopoint (DATA_MODEL 5.3 invariant 11).
    assert (visit.strict_country_code, visit.strict_admin1) == ("IN", "Maharashtra")
    assert visit.strict_city is None
    return visit


# ---------------------------------------------------------------------------
# Through the job: region geofences on strict codes
# ---------------------------------------------------------------------------


async def test_no_applicable_geofence_leaves_the_state_null(
    db_client: AsyncClient, integration_settings: Settings, resolver: None, made: set[uuid.UUID]
) -> None:
    """NULL, not "outside": nothing was evaluated (ADR-0020 decision 5). A geofence
    scoped to another link, and an inactive one, do not apply."""
    del resolver
    await _fence(made, keys=["IN"], links=[(await ch.create_link()).id])
    await _fence(made, keys=["IN"], active=False)
    _, visit_id = await _visit(db_client)

    visit = await _infer(integration_settings, visit_id)
    assert visit.geofence_state is None
    assert visit.matched_geofence_ids == []


async def test_a_strict_state_places_a_visit_inside_its_region(
    db_client: AsyncClient, integration_settings: Settings, resolver: None, made: set[uuid.UUID]
) -> None:
    del resolver
    link_id, visit_id = await _visit(db_client)
    inside = await _fence(made, keys=["IN|Maharashtra"], links=[link_id])
    await _fence(made, keys=["IN|Karnataka"], links=[link_id])

    visit = await _infer(integration_settings, visit_id)
    assert visit.geofence_state is GeofenceState.INSIDE
    assert visit.matched_geofence_ids == [inside]


async def test_a_strict_state_elsewhere_is_outside(
    db_client: AsyncClient, integration_settings: Settings, resolver: None, made: set[uuid.UUID]
) -> None:
    """Strict Maharashtra rules out Karnataka, and a strict India rules out Nepal."""
    del resolver
    link_id, visit_id = await _visit(db_client)
    await _fence(made, keys=["IN|Karnataka", "NP"], links=[link_id])

    visit = await _infer(integration_settings, visit_id)
    assert visit.geofence_state is GeofenceState.OUTSIDE
    assert visit.matched_geofence_ids == []


async def test_an_abstaining_inference_is_undetermined_not_outside(
    db_client: AsyncClient, integration_settings: Settings, resolver: None, made: set[uuid.UUID]
) -> None:
    """The M6 done-check (F6.AC6). The visit has no geopoint, so a polygon around a
    different city cannot be decided -- even though the strict state says Maharashtra
    and the polygon is in Karnataka. Shapes are matched on the point, never on codes."""
    del resolver
    link_id, visit_id = await _visit(db_client)
    await _fence(made, polygon=AROUND_BENGALURU, links=[link_id])
    await _fence(made, keys=["IN|Karnataka"], links=[link_id])

    visit = await _infer(integration_settings, visit_id)
    assert visit.geofence_state is GeofenceState.UNDETERMINED
    assert visit.matched_geofence_ids == []


async def test_an_engine_error_is_undetermined(
    db_client: AsyncClient,
    integration_settings: Settings,
    made: set[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A visit the engine could not locate at all states nothing, so it can be neither
    inside nor outside anything."""

    async def broken(*_args: object, **_kwargs: object) -> engine.Inferred:
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "infer_visit", broken)
    link_id, visit_id = await _visit(db_client)
    await _fence(made, keys=["IN"], links=[link_id])

    assert await engine.run_once(integration_settings, only=[visit_id]) == 1
    visit = await ch.get_visit(visit_id)
    assert visit.strict_country_code is None
    assert visit.geofence_state is GeofenceState.UNDETERMINED


# ---------------------------------------------------------------------------
# The SQL half: ST_Covers on the geopoint
# ---------------------------------------------------------------------------


async def _with_point(client: AsyncClient, lat_lng: tuple[float, float]) -> tuple[Any, Any]:
    link_id, visit_id = await _visit(client)
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE visits SET geopoint = "
                "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography WHERE id = :id"
            ),
            {"lat": lat_lng[0], "lng": lat_lng[1], "id": visit_id},
        )
    return link_id, visit_id


async def _evaluate(link_id: uuid.UUID, visit_id: uuid.UUID, place: StrictPlace) -> Any:
    async with session_scope() as db:
        fences = await store.load_active(db)
        return await store.evaluate(db, fences, visit_id=visit_id, link_id=link_id, place=place)


async def test_a_polygon_covers_the_geopoint(db_client: AsyncClient, made: set[uuid.UUID]) -> None:
    link_id, visit_id = await _with_point(db_client, MUMBAI)
    mumbai = await _fence(made, polygon=AROUND_MUMBAI, links=[link_id], priority=1)
    await _fence(made, polygon=AROUND_BENGALURU, links=[link_id], priority=2)

    result = await _evaluate(link_id, visit_id, StrictPlace("IN", "Maharashtra", True))
    assert result.state is GeofenceState.INSIDE
    assert result.matched == [mumbai]
    assert result.deciding.id == mumbai


async def test_overlapping_geofences_are_all_recorded_and_the_highest_decides(
    db_client: AsyncClient, made: set[uuid.UUID]
) -> None:
    """F6.AC7."""
    link_id, visit_id = await _with_point(db_client, MUMBAI)
    low = await _fence(made, polygon=AROUND_MUMBAI, links=[link_id], priority=1)
    high = await _fence(made, keys=["IN|Maharashtra"], links=[link_id], priority=50)

    result = await _evaluate(link_id, visit_id, StrictPlace("IN", "Maharashtra", True))
    assert result.matched == [high, low]
    assert result.deciding.id == high


async def test_without_a_geopoint_the_database_is_not_asked(
    db_client: AsyncClient, made: set[uuid.UUID]
) -> None:
    link_id, visit_id = await _visit(db_client)
    await _fence(made, polygon=AROUND_MUMBAI, links=[link_id])

    result = await _evaluate(link_id, visit_id, StrictPlace("IN", "Maharashtra", False))
    ((evaluated),) = result.results
    assert evaluated.result.reason is Reason.NO_GEOPOINT


async def test_evaluation_stays_under_5ms_at_p95(
    db_client: AsyncClient, made: set[uuid.UUID]
) -> None:
    """F6.AC8, against more geofences than an owner would draw: 20 polygons and 20
    regions on one link, a visit with a point."""
    link_id, visit_id = await _with_point(db_client, MUMBAI)
    for i in range(20):
        await _fence(made, polygon=AROUND_BENGALURU, links=[link_id], priority=i)
        await _fence(made, keys=["IN|Karnataka", "IN|Goa"], links=[link_id], priority=i)
    place = StrictPlace("IN", "Maharashtra", True)
    timings = []
    async with session_scope() as db:
        fences = await store.load_active(db)
        for _ in range(60):
            started = time.perf_counter()
            await store.evaluate(db, fences, visit_id=visit_id, link_id=link_id, place=place)
            timings.append((time.perf_counter() - started) * 1000)
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    assert p95 < 5.0, f"p95 {p95:.2f} ms"
