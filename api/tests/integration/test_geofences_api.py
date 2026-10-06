"""Geofence management, against the real database and PostGIS (F6, docs/API.md section 9).

Every write is owner-only and audited (CLAUDE.md invariant 9). Geometry is checked by
PostGIS itself, so the rejection tests exercise the real ST_IsValidDetail, not a stand-in.

The region catalogue and GeoNames naming are replaced: the suite does not install the
GeoNames files, and what is under test is the API's use of them, not GeoNames. Every
geofence made here is named ``itest ...`` and removed afterwards, because the suite
shares the dev database and an active geofence applies to every visit inferred later.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, select, text

from tests.integration import capture_helpers as ch
from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.audit import log as audit
from tracelet.auth.models import AdminRole
from tracelet.capture.models import Visit
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.geofence import regions
from tracelet.geofence import router as geofence_router
from tracelet.inference.geodb.geonames import PopulatedPlace
from tracelet.inference.types import Candidate

pytestmark = pytest.mark.integration

FENCES = "/api/v1/geofences"

CATALOG = regions.build(
    {"IN.19": "Karnataka", "IN.16": "Maharashtra", "IN.33": "Goa", "NP.01": "Bagmati"}
)
# Bengaluru, and a square around it in GeoJSON order (longitude first).
BENGALURU = {"lat": 12.9716, "lng": 77.5946}
SQUARE = [[[77.4, 12.8], [77.8, 12.8], [77.8, 13.2], [77.4, 13.2], [77.4, 12.8]]]
# A bow tie: the edges cross at (77.6, 13.0).
BOW_TIE = [[[77.4, 12.8], [77.8, 13.2], [77.8, 12.8], [77.4, 13.2], [77.4, 12.8]]]


class _Placer:
    """Names a point as GeoNames would: Bengaluru is in Karnataka, anything else is in
    Maharashtra, and the open sea is nowhere."""

    def place(self, c: Candidate) -> Candidate:
        assert c.lat is not None and c.lng is not None
        if c.lat < 0:
            return c
        admin1 = "Karnataka" if 12 < c.lat < 14 and 77 < c.lng < 78 else "Maharashtra"
        return dataclasses.replace(c, country_code="IN", admin1=admin1)

    def populated(self, country: str, min_population: int) -> list[PopulatedPlace]:
        towns = [
            PopulatedPlace(1, "Mumbai", "Maharashtra", 19.07, 72.88, 12_691_836),
            PopulatedPlace(2, "Pune", "Maharashtra", 18.52, 73.86, 3_124_458),
            PopulatedPlace(3, "Mysore", "Karnataka", 12.30, 76.64, 868_313),
            PopulatedPlace(4, "Udupi", "Karnataka", 13.34, 74.75, 144_960),
            PopulatedPlace(5, "Kundapura", "Karnataka", 13.63, 74.69, 30_444),
        ]
        return [t for t in towns if country == "IN" and t.population >= min_population]


@pytest.fixture(autouse=True)
async def _world(db_app: object, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    del db_app
    monkeypatch.setattr(regions, "catalog", lambda _settings: CATALOG)
    monkeypatch.setattr(geofence_router, "geocoder", lambda _settings: _Placer())
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM geofences WHERE name LIKE 'itest %'"))


def _region(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "itest Karnataka",
        "shape_kind": "region",
        "region_keys": ["IN|Karnataka"],
    } | overrides


def _circle(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "itest Home 2km",
        "shape_kind": "circle",
        "center": BENGALURU,
        "radius_m": 2000,
    } | overrides


def _polygon(coordinates: list[Any] = SQUARE, **overrides: Any) -> dict[str, Any]:
    return {
        "name": "itest Campus",
        "shape_kind": "polygon",
        "geometry": {"type": "Polygon", "coordinates": coordinates},
    } | overrides


async def _create(owner: SignedIn, body: dict[str, Any]) -> dict[str, Any]:
    response = await owner.client.post(FENCES, json=body, headers=owner.headers())
    assert response.status_code == 201, response.text
    return dict(response.json())


def _error_codes(response: Any) -> list[str]:
    return [e["code"] for e in response.json()["errors"]]


# ---------------------------------------------------------------------------
# Create: three shapes (ADR-0020)
# ---------------------------------------------------------------------------


async def test_an_owner_creates_a_region_geofence(owner: SignedIn) -> None:
    fence = await _create(owner, _region(region_keys=["IN|Karnataka", "IN|Goa", "IN|Goa"]))
    assert fence["shape_kind"] == "region"
    assert fence["region_keys"] == ["IN|Karnataka", "IN|Goa"], "de-duplicated, in order"
    assert fence["geometry"] is None and fence["center"] is None
    assert fence["notify_priority"] == "high" and fence["is_active"] is True
    assert fence["link_ids"] is None and fence["unknown_region_keys"] == []
    assert fence["matches_7d"] == 0

    (detail,) = await ch.audit_details_for(uuid.UUID(fence["id"]), audit.Action.GEOFENCE_CREATED)
    assert detail["region_keys"] == ["IN|Karnataka", "IN|Goa"]


async def test_a_circle_round_trips_without_distortion(owner: SignedIn) -> None:
    """The M6 done-check: the centre and radius come back exactly as drawn, and the
    buffered polygon is there to draw."""
    fence = await _create(owner, _circle())
    again = (await owner.client.get(f"{FENCES}/{fence['id']}")).json()
    assert again["center"] == pytest.approx(BENGALURU)
    assert again["radius_m"] == 2000
    ring = again["geometry"]["coordinates"][0]
    assert again["geometry"]["type"] == "Polygon"
    assert len(ring) == 65, "64 sides, closed"


async def test_a_polygon_round_trips_without_distortion(owner: SignedIn) -> None:
    fence = await _create(owner, _polygon())
    again = (await owner.client.get(f"{FENCES}/{fence['id']}")).json()
    assert again["geometry"] == {"type": "Polygon", "coordinates": SQUARE}


async def test_a_self_intersecting_ring_is_refused_with_where_it_crosses(owner: SignedIn) -> None:
    """F6.AC4, and DESIGN 16: the editor marks the offending point on the map."""
    response = await owner.client.post(FENCES, json=_polygon(BOW_TIE), headers=owner.headers())
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "GEOFENCE_INVALID_GEOMETRY"
    (error,) = body["errors"]
    assert "Self-intersection" in error["message"]
    assert error["location"] == pytest.approx({"lat": 13.0, "lng": 77.6})


async def test_more_than_2000_points_is_refused(owner: SignedIn) -> None:
    ring = [[77.4 + i * 1e-5, 12.8] for i in range(1999)] + [[77.8, 13.2], [77.4, 12.8]]
    response = await owner.client.post(FENCES, json=_polygon([ring]), headers=owner.headers())
    assert response.status_code == 422
    assert response.json()["code"] == "GEOFENCE_TOO_MANY_VERTICES"


@pytest.mark.parametrize(
    "body",
    [
        _polygon([[[77.4, 12.8], [77.8, 12.8], [77.8, 13.2], [77.4, 13.2]]]),  # not closed
        _polygon([[[200.0, 12.8], [77.8, 12.8], [77.8, 13.2], [200.0, 12.8]]]),  # range
        _circle(radius_m=10),
        _circle(center={"lat": 91, "lng": 0}),
        _region(region_keys=[]),
        _region(name=""),
        {"name": "itest x", "shape_kind": "hexagon"},
        _region(region_keys=None),
    ],
)
async def test_malformed_bodies_are_a_field_level_422(
    owner: SignedIn, body: dict[str, Any]
) -> None:
    response = await owner.client.post(FENCES, json=body, headers=owner.headers())
    assert response.status_code == 422, response.text
    assert response.json()["errors"]


@pytest.mark.parametrize("key", ["IN|Atlantis", "in", "IN|", "IND", "ZZ"])
async def test_a_region_key_must_be_in_the_catalogue(owner: SignedIn, key: str) -> None:
    response = await owner.client.post(
        FENCES, json=_region(region_keys=["IN|Karnataka", key]), headers=owner.headers()
    )
    assert response.status_code == 422
    assert response.json()["code"] == "GEOFENCE_UNKNOWN_REGION"
    (error,) = response.json()["errors"]
    assert error["field"] == "region_keys.1"


async def test_region_geofences_need_the_catalogue_installed(
    owner: SignedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(regions, "catalog", lambda _settings: None)
    response = await owner.client.post(FENCES, json=_region(), headers=owner.headers())
    assert response.status_code == 503
    assert response.json()["code"] == "GEO_DB_UNAVAILABLE"


async def test_a_geofence_can_be_scoped_to_existing_links_only(owner: SignedIn) -> None:
    link = await ch.create_link()
    fence = await _create(owner, _region(link_ids=[str(link.id)]))
    assert fence["link_ids"] == [str(link.id)]

    response = await owner.client.post(
        FENCES, json=_region(link_ids=[str(uuid.uuid4())]), headers=owner.headers()
    )
    assert response.status_code == 422
    assert _error_codes(response) == ["UNKNOWN_LINK"]


async def test_a_refused_write_leaves_nothing_behind(owner: SignedIn) -> None:
    """E14: the request session commits on a 4xx, so validation must precede writes."""
    async with session_scope() as db:
        before = (await db.execute(text("SELECT count(*) FROM geofences"))).scalar_one()
    await owner.client.post(FENCES, json=_polygon(BOW_TIE), headers=owner.headers())
    await owner.client.post(
        FENCES, json=_region(link_ids=[str(uuid.uuid4())]), headers=owner.headers()
    )
    async with session_scope() as db:
        after = (await db.execute(text("SELECT count(*) FROM geofences"))).scalar_one()
    assert after == before


# ---------------------------------------------------------------------------
# Update and delete
# ---------------------------------------------------------------------------


async def test_an_update_is_audited_with_old_and_new(owner: SignedIn) -> None:
    fence = await _create(owner, _region())
    response = await owner.client.patch(
        f"{FENCES}/{fence['id']}",
        json={"name": "itest Karnataka and Goa", "region_keys": ["IN|Karnataka", "IN|Goa"]},
        headers=owner.headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["region_keys"] == ["IN|Karnataka", "IN|Goa"]

    (detail,) = await ch.audit_details_for(uuid.UUID(fence["id"]), audit.Action.GEOFENCE_UPDATED)
    assert detail["name"] == {"from": "itest Karnataka", "to": "itest Karnataka and Goa"}
    assert detail["region_keys"]["to"] == ["IN|Karnataka", "IN|Goa"]


async def test_a_shape_can_change_kind_with_its_fields(owner: SignedIn) -> None:
    fence = await _create(owner, _polygon())
    url = f"{FENCES}/{fence['id']}"

    incomplete = await owner.client.patch(
        url, json={"shape_kind": "region"}, headers=owner.headers()
    )
    assert incomplete.status_code == 422
    assert _error_codes(incomplete) == ["REQUIRED"]

    changed = await owner.client.patch(
        url, json={"shape_kind": "region", "region_keys": ["IN"]}, headers=owner.headers()
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["shape_kind"], body["region_keys"], body["geometry"]) == ("region", ["IN"], None)


async def test_a_field_for_another_shape_is_refused(owner: SignedIn) -> None:
    fence = await _create(owner, _region())
    response = await owner.client.patch(
        f"{FENCES}/{fence['id']}", json={"radius_m": 500}, headers=owner.headers()
    )
    assert response.status_code == 422
    assert "NOT_FOR_SHAPE" in _error_codes(response)


async def test_a_circle_radius_changes_alone(owner: SignedIn) -> None:
    fence = await _create(owner, _circle())
    response = await owner.client.patch(
        f"{FENCES}/{fence['id']}", json={"radius_m": 5000}, headers=owner.headers()
    )
    assert response.status_code == 200, response.text
    assert response.json()["radius_m"] == 5000
    assert response.json()["center"] == pytest.approx(BENGALURU)


async def test_link_scope_can_be_cleared_back_to_all_links(owner: SignedIn) -> None:
    link = await ch.create_link()
    fence = await _create(owner, _region(link_ids=[str(link.id)]))
    response = await owner.client.patch(
        f"{FENCES}/{fence['id']}", json={"link_ids": None}, headers=owner.headers()
    )
    assert response.json()["link_ids"] is None


async def test_a_deletion_is_audited(owner: SignedIn) -> None:
    fence = await _create(owner, _region())
    response = await owner.client.delete(f"{FENCES}/{fence['id']}", headers=owner.headers())
    assert response.status_code == 204
    assert (await owner.client.get(f"{FENCES}/{fence['id']}")).status_code == 404
    (detail,) = await ch.audit_details_for(uuid.UUID(fence["id"]), audit.Action.GEOFENCE_DELETED)
    assert detail["name"] == "itest Karnataka"


async def test_a_renamed_division_is_flagged_not_dropped(
    owner: SignedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0020 consequences: a GeoNames rename would orphan a key. It is kept and
    reported, so the owner can see it and fix it."""
    fence = await _create(owner, _region(region_keys=["IN|Karnataka", "IN|Goa"]))
    renamed = regions.build({"IN.19": "Karnataka", "IN.33": "Goa State"})
    monkeypatch.setattr(regions, "catalog", lambda _settings: renamed)
    again = (await owner.client.get(f"{FENCES}/{fence['id']}")).json()
    assert again["region_keys"] == ["IN|Karnataka", "IN|Goa"]
    assert again["unknown_region_keys"] == ["IN|Goa"]


# ---------------------------------------------------------------------------
# The catalogue and the coordinate test
# ---------------------------------------------------------------------------


async def test_the_region_list_is_the_catalogue(owner: SignedIn) -> None:
    body = (await owner.client.get(f"{FENCES}/regions")).json()
    assert [c["key"] for c in body["countries"]] == ["IN", "NP"]
    assert {"key": "IN|Karnataka", "code": "IN.19", "country": "IN", "name": "Karnataka"} in body[
        "divisions"
    ]


async def test_a_countrys_places_come_with_their_tiers(owner: SignedIn) -> None:
    """DESIGN §16: metro, tier 1, 2 and 3 by population; towns under 50,000 are left out."""
    response = await owner.client.get(f"{FENCES}/places", params={"country": "IN"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["country"] == "IN"
    assert [(p["name"], p["tier"]) for p in body["places"]] == [
        ("Mumbai", "metro"),
        ("Pune", "tier1"),
        ("Mysore", "tier2"),
        ("Udupi", "tier3"),
    ]
    assert body["places"][0]["admin1"] == "Maharashtra"
    assert response.headers["cache-control"] == "private, max-age=3600"


async def test_places_need_a_country_code_and_geonames(
    owner: SignedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = await owner.client.get(f"{FENCES}/places", params={"country": "india"})
    assert bad.status_code == 422
    monkeypatch.setattr(geofence_router, "geocoder", lambda _settings: None)
    missing = await owner.client.get(f"{FENCES}/places", params={"country": "IN"})
    assert missing.status_code == 503
    assert missing.json()["code"] == "GEO_DB_UNAVAILABLE"


async def test_a_coordinate_is_tested_without_creating_a_visit(owner: SignedIn) -> None:
    """F6.AC10. The coordinate is a GPS fix: a geopoint, named by GeoNames."""
    region = await _create(owner, _region(priority=10))
    square = await _create(owner, _polygon(priority=5))
    elsewhere = await _create(owner, _region(name="itest Goa", region_keys=["IN|Goa"]))
    await _create(owner, _region(name="itest off", is_active=False))
    async with session_scope() as db:
        visits = (await db.execute(select(func.count()).select_from(Visit))).scalar_one()

    response = await owner.client.post(f"{FENCES}/test", json=BENGALURU, headers=owner.headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["placed"] == {"country_code": "IN", "admin1": "Karnataka"}
    assert body["state"] == "inside"
    ours = {r["geofence_id"]: r for r in body["results"]}
    assert ours[region["id"]]["result"] == "inside"
    assert ours[square["id"]]["result"] == "inside"
    assert ours[elsewhere["id"]]["result"] == "outside"
    assert "itest off" not in {r["name"] for r in body["results"]}, "inactive is not tested"

    async with session_scope() as db:
        after = (await db.execute(select(func.count()).select_from(Visit))).scalar_one()
    assert after == visits


async def test_an_unplaceable_coordinate_is_undetermined_for_regions(owner: SignedIn) -> None:
    region = await _create(owner, _region())
    square = await _create(owner, _polygon())
    body = (
        await owner.client.post(
            f"{FENCES}/test", json={"lat": -40.0, "lng": 0.0}, headers=owner.headers()
        )
    ).json()
    assert body["placed"] == {"country_code": None, "admin1": None}
    ours = {r["geofence_id"]: r for r in body["results"]}
    assert ours[region["id"]] | {"geofence_id": ""} == {
        "geofence_id": "",
        "name": "itest Karnataka",
        "result": "undetermined",
        "reason": "no_strict_country",
    }
    # The point itself still decides a shape.
    assert ours[square["id"]]["result"] == "outside"


# ---------------------------------------------------------------------------
# Import and export (F6.AC9)
# ---------------------------------------------------------------------------

_COMPARED = (
    "name",
    "shape_kind",
    "region_keys",
    "geometry",
    "center",
    "radius_m",
    "priority",
    "is_active",
    "notify_priority",
    "link_ids",
    "description",
)


async def _ours_exported(owner: SignedIn) -> list[dict[str, Any]]:
    response = await owner.client.get(f"{FENCES}/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/geo+json")
    assert "geofences.geojson" in response.headers["content-disposition"]
    collection = response.json()
    assert collection["type"] == "FeatureCollection"
    return [f for f in collection["features"] if f["properties"]["name"].startswith("itest ")]


async def test_an_export_re_imports_unchanged(owner: SignedIn) -> None:
    """Every shape survives the round trip: a polygon as a Polygon, a circle as a Point
    with its radius, a region as a feature with no geometry."""
    link = await ch.create_link()
    made = [
        await _create(owner, _region(description="Home state", link_ids=[str(link.id)])),
        await _create(owner, _circle(notify_priority="normal", priority=7)),
        await _create(owner, _polygon(is_active=False)),
    ]
    features = await _ours_exported(owner)
    assert {f["geometry"]["type"] if f["geometry"] else None for f in features} == {
        None,
        "Point",
        "Polygon",
    }
    for fence in made:
        await owner.client.delete(f"{FENCES}/{fence['id']}", headers=owner.headers())

    response = await owner.client.post(
        f"{FENCES}/import",
        json={"type": "FeatureCollection", "features": features},
        headers=owner.headers(),
    )
    assert response.status_code == 201, response.text
    imported = response.json()
    assert len(imported) == 3
    assert {i["id"] for i in imported}.isdisjoint({m["id"] for m in made}), "an import creates"
    by_name = {i["name"]: i for i in imported}
    for original in made:
        again = by_name[original["name"]]
        for key in _COMPARED:
            if key == "center" and original[key] is not None:
                assert again[key] == pytest.approx(original[key]), key
            else:
                assert again[key] == original[key], key

    details = await ch.audit_details_for_action(audit.Action.GEOFENCE_IMPORTED)
    assert details[-1]["count"] == 3


async def test_one_invalid_feature_imports_nothing(owner: SignedIn) -> None:
    features = [
        {
            "type": "Feature",
            "geometry": None,
            "properties": {"name": "itest ok", "region_keys": ["IN"]},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": BOW_TIE},
            "properties": {"name": "itest bow tie"},
        },
        {
            "type": "Feature",
            "geometry": None,
            "properties": {"name": "itest bad", "region_keys": ["IN|Atlantis"]},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [77.5, 12.9]},
            "properties": {"name": "itest no radius"},
        },
    ]
    response = await owner.client.post(
        f"{FENCES}/import",
        json={"type": "FeatureCollection", "features": features},
        headers=owner.headers(),
    )
    assert response.status_code == 422, response.text
    fields = {(e["field"], e["code"]) for e in response.json()["errors"]}
    assert ("features.1.geometry", "GEOFENCE_INVALID_GEOMETRY") in fields
    assert ("features.2.region_keys.0", "GEOFENCE_UNKNOWN_REGION") in fields
    assert any(f.startswith("features.3.") for f, _ in fields)
    assert not any(f.startswith("features.0") for f, _ in fields)
    assert await _ours_exported(owner) == [], "all or nothing"


async def test_an_import_is_bounded(owner: SignedIn) -> None:
    feature = {
        "type": "Feature",
        "geometry": None,
        "properties": {"name": "itest x", "region_keys": ["IN"]},
    }
    response = await owner.client.post(
        f"{FENCES}/import",
        json={"type": "FeatureCollection", "features": [feature] * 201},
        headers=owner.headers(),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Roles (CLAUDE.md invariant 9)
# ---------------------------------------------------------------------------


async def test_an_analyst_can_read_and_test_but_not_write(
    owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    fence = await _create(owner, _region())
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)
    url = f"{FENCES}/{fence['id']}"

    assert (await analyst.client.get(FENCES)).status_code == 200
    assert (await analyst.client.get(url)).status_code == 200
    assert (await analyst.client.get(f"{FENCES}/regions")).status_code == 200
    assert (await analyst.client.get(f"{FENCES}/export")).status_code == 200
    imported = await analyst.client.post(
        f"{FENCES}/import",
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": None,
                    "properties": {"name": "itest x", "region_keys": ["IN"]},
                }
            ],
        },
        headers=analyst.headers(),
    )
    assert imported.status_code == 403
    tested = await analyst.client.post(f"{FENCES}/test", json=BENGALURU, headers=analyst.headers())
    assert tested.status_code == 200
    created = await analyst.client.post(FENCES, json=_region(), headers=analyst.headers())
    patched = await analyst.client.patch(url, json={"name": "itest x"}, headers=analyst.headers())
    deleted = await analyst.client.delete(url, headers=analyst.headers())
    assert created.status_code == patched.status_code == deleted.status_code == 403


async def test_geofences_are_not_public(new_client: ClientFactory) -> None:
    client = await new_client()
    assert (await client.get(FENCES)).status_code == 401
