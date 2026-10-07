"""The geofence invariants the engine enforces, tested around the application.

ADR-0020 decision 6 restated DATA_MODEL section 5.3 invariant 5: an abstaining inference
is never "outside" or "inside". Each test writes with raw SQL, because the guarantee
has to survive a code path that forgets it. Section 6.1's shape rules are tested the
same way: the application validates first, and these are the floor beneath it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration import capture_helpers as ch
from tracelet.capture.models import Visit
from tracelet.db.engine import session_scope

pytestmark = pytest.mark.integration

SQUARE = "POLYGON((77.5 12.9, 77.7 12.9, 77.7 13.1, 77.5 13.1, 77.5 12.9))"
# A bow tie: the ring crosses itself, so it is not a valid polygon (F6.AC4).
BOW_TIE = "POLYGON((77.5 12.9, 77.7 13.1, 77.7 12.9, 77.5 13.1, 77.5 12.9))"


async def _visit(client: AsyncClient) -> Visit:
    link = await ch.create_link()
    await ch.visit(client, link.slug)
    return await ch.latest_visit(link.id)


async def _set(visit_id: uuid.UUID, assignments: str) -> None:
    async with session_scope() as db:
        await db.execute(
            text(f"UPDATE visits SET {assignments} WHERE id = :id"),  # noqa: S608 -- test literals
            {"id": visit_id},
        )


# ---------------------------------------------------------------------------
# visits: DATA_MODEL section 5.3 invariant 5, as restated
# ---------------------------------------------------------------------------


async def test_a_new_visit_has_no_geofence_state(db_client: AsyncClient) -> None:
    """NULL is "not evaluated", distinct from all three states (ADR-0020 decision 5)."""
    visit = await _visit(db_client)
    assert visit.geofence_state is None
    assert visit.matched_geofence_ids == []


async def test_a_visit_with_no_strict_location_cannot_be_outside(
    db_client: AsyncClient,
) -> None:
    visit = await _visit(db_client)
    with pytest.raises(IntegrityError, match="ck_visits_outside_needs_strict"):
        await _set(visit.id, "geofence_state = 'outside'")


async def test_a_strict_country_alone_can_place_a_visit_outside(db_client: AsyncClient) -> None:
    """A strict Maharashtra or a strict foreign country rules out a region geofence with
    no geopoint at all; the old invariant 5 forbade exactly this."""
    visit = await _visit(db_client)
    await _set(visit.id, "strict_country_code = 'US', geofence_state = 'outside'")
    assert (await ch.get_visit(visit.id)).geofence_state == "outside"


async def test_inside_requires_a_matched_geofence(db_client: AsyncClient) -> None:
    visit = await _visit(db_client)
    with pytest.raises(IntegrityError, match="ck_visits_inside_has_match"):
        await _set(visit.id, "strict_country_code = 'IN', geofence_state = 'inside'")


async def test_an_abstaining_visit_can_be_undetermined(db_client: AsyncClient) -> None:
    visit = await _visit(db_client)
    await _set(visit.id, "geofence_state = 'undetermined'")
    assert (await ch.get_visit(visit.id)).geofence_state == "undetermined"


# ---------------------------------------------------------------------------
# geofences: DATA_MODEL section 6.1
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _remove_test_geofences(db_app: object) -> AsyncIterator[None]:
    """The suite shares the dev database: leave no geofence behind."""
    del db_app
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM geofences WHERE name LIKE 'itest %'"))


async def _insert_geofence(columns: dict[str, str]) -> None:
    """Insert one geofence from SQL expressions, so a test can write what the
    application never would. Inactive, so no visit inferred meanwhile is evaluated
    against it."""
    row = {"id": "gen_random_uuid()", "name": "'itest fence'", "is_active": "false", **columns}
    names = ", ".join(row)
    values = ", ".join(row.values())
    async with session_scope() as db:
        await db.execute(text(f"INSERT INTO geofences ({names}) VALUES ({values})"))  # noqa: S608


def _area(wkt: str) -> str:
    return f"ST_GeogFromText('SRID=4326;{wkt}')"


async def test_each_shape_carries_exactly_its_own_columns(db_app: object) -> None:
    del db_app
    await _insert_geofence({"shape_kind": "'polygon'", "area": _area(SQUARE)})
    await _insert_geofence(
        {
            "shape_kind": "'circle'",
            "area": _area(SQUARE),
            "center": "ST_GeogFromText('SRID=4326;POINT(77.6 13.0)')",
            "radius_m": "1000",
        }
    )
    await _insert_geofence(
        {"shape_kind": "'region'", "region_keys": "ARRAY['IN|Karnataka', 'IN|Goa']"}
    )


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        # A region has keys and no area.
        ({"shape_kind": "'region'"}, "ck_geofences_region_keys_iff_region"),
        (
            {"shape_kind": "'region'", "region_keys": "ARRAY['IN']", "area": _area(SQUARE)},
            "ck_geofences_area_iff_shape",
        ),
        (
            {"shape_kind": "'region'", "region_keys": "ARRAY[]::text[]"},
            "ck_geofences_region_keys_bounded",
        ),
        (
            {"shape_kind": "'region'", "region_keys": "ARRAY['IN', NULL]"},
            "ck_geofences_region_keys_bounded",
        ),
        # A polygon has an area and nothing else.
        ({"shape_kind": "'polygon'"}, "ck_geofences_area_iff_shape"),
        (
            {"shape_kind": "'polygon'", "area": _area(SQUARE), "region_keys": "ARRAY['IN']"},
            "ck_geofences_region_keys_iff_region",
        ),
        (
            {"shape_kind": "'polygon'", "area": _area(SQUARE), "radius_m": "10"},
            "ck_geofences_radius_iff_circle",
        ),
        # A circle keeps its centre and radius for round-trip editing (F6.AC2).
        (
            {"shape_kind": "'circle'", "area": _area(SQUARE), "radius_m": "1000"},
            "ck_geofences_center_iff_circle",
        ),
        # F6.AC4: validity, below the application's own specific error.
        ({"shape_kind": "'polygon'", "area": _area(BOW_TIE)}, "ck_geofences_area_valid"),
        # An empty link list would apply to nothing; is_active says that.
        (
            {"shape_kind": "'polygon'", "area": _area(SQUARE), "link_ids": "ARRAY[]::uuid[]"},
            "ck_geofences_link_ids_not_empty",
        ),
        ({"shape_kind": "'polygon'", "area": _area(SQUARE), "name": "''"}, "name_length"),
    ],
)
async def test_the_engine_refuses_a_malformed_geofence(
    db_app: object, columns: dict[str, str], constraint: str
) -> None:
    del db_app
    with pytest.raises(IntegrityError, match=constraint):
        await _insert_geofence(columns)


async def test_the_vertex_cap_bounds_evaluation_cost(db_app: object) -> None:
    """F6.AC4: 2000 points at most. A 2001-point ring is valid geometry, refused only
    for its cost."""
    del db_app
    ring = "ST_Segmentize(ST_GeomFromText('SRID=4326;" + SQUARE + "'), 0.0003)::geography"
    with pytest.raises(IntegrityError, match="ck_geofences_vertex_cap"):
        await _insert_geofence({"shape_kind": "'polygon'", "area": ring})
