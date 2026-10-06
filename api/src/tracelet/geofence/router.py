"""Geofence management (docs/API.md section 9, F6, ADR-0020).

Reads are open to any admin; every write is ``owner``-only and writes an audit row
(CLAUDE.md invariant 9).

**Every check runs before any write.** The request session commits on a 4xx (E14), so a
handler that wrote and then rejected would commit the write. Geometry is validated by
PostGIS inside a savepoint, which is rolled back whatever it finds.

Geometry never enters Python as a type: ``area`` and ``center`` are unmapped, and each
statement that touches them names its PostGIS function (geofence/models.py).
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints, TypeAdapter, model_validator
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError

from tracelet.audit import log as audit
from tracelet.auth.dependencies import (
    Config,
    CurrentPrincipal,
    DbSession,
    OwnerPrincipal,
    Principal,
    client_ip,
)
from tracelet.capture.models import GeofenceState, Link
from tracelet.errors import (
    FieldError,
    GeoDbUnavailable,
    GeofenceInvalidGeometry,
    GeofenceTooManyVertices,
    GeofenceUnknownRegion,
    GeoPoint,
    NotFound,
    ValidationFailed,
)
from tracelet.geofence import regions, store
from tracelet.geofence.evaluate import StrictPlace
from tracelet.geofence.models import Geofence, NotifyPriority, ShapeKind
from tracelet.inference.geodb.readers import geocoder
from tracelet.inference.types import Candidate, GeoLevel, InferenceSource
from tracelet.net import prefix_of

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/geofences", tags=["geofences"])

MAX_VERTICES = 2000  # F6.AC4; ck_geofences_vertex_cap is the floor beneath this
MAX_REGION_KEYS = 1000
MIN_RADIUS_M = 50.0
MAX_RADIUS_M = 1_000_000.0
# 16 segments per quarter: a circle is stored as a 64-sided polygon. At the largest
# radius the chord sits within about 0.12 % of the arc.
CIRCLE_QUAD_SEGS = 16


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Priority = Annotated[int, Field(ge=-1_000_000, le=1_000_000)]
NotifyLevel = Literal["high", "normal", "silent"]
LinkIds = Annotated[list[uuid.UUID], Field(min_length=1, max_length=500)]
RegionKeys = Annotated[
    list[Annotated[str, StringConstraints(min_length=2, max_length=200)]],
    Field(min_length=1, max_length=MAX_REGION_KEYS),
]
Radius = Annotated[float, Field(ge=MIN_RADIUS_M, le=MAX_RADIUS_M)]
Position = Annotated[list[float], Field(min_length=2, max_length=3)]
Ring = Annotated[list[Position], Field(min_length=4)]


class LatLng(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class PolygonGeometry(BaseModel):
    """A GeoJSON ``Polygon``: longitude first (RFC 7946), each ring closed."""

    type: Literal["Polygon"]
    coordinates: Annotated[list[Ring], Field(min_length=1)]

    @model_validator(mode="after")
    def _rings(self) -> PolygonGeometry:
        for ring in self.coordinates:
            if ring[0][:2] != ring[-1][:2]:
                msg = "Each ring must end where it starts."
                raise ValueError(msg)
            for position in ring:
                lng, lat = position[0], position[1]
                if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                    msg = "Coordinates are longitude, latitude, within range."
                    raise ValueError(msg)
        return self

    @property
    def vertices(self) -> int:
        return sum(len(r) for r in self.coordinates)


class _Common(BaseModel):
    name: Name
    description: Description | None = None
    priority: Priority = 0
    is_active: bool = True
    notify_priority: NotifyLevel = "high"
    link_ids: LinkIds | None = None


class RegionCreate(_Common):
    shape_kind: Literal["region"]
    region_keys: RegionKeys


class CircleCreate(_Common):
    shape_kind: Literal["circle"]
    center: LatLng
    radius_m: Radius


class PolygonCreate(_Common):
    shape_kind: Literal["polygon"]
    geometry: PolygonGeometry


GeofenceCreate = Annotated[
    RegionCreate | CircleCreate | PolygonCreate, Field(discriminator="shape_kind")
]


class GeofenceUpdate(BaseModel):
    """Any subset. ``description`` and ``link_ids`` may be set to ``null`` explicitly
    (no description; every link), which is why "absent" is read from the fields set."""

    name: Name | None = None
    description: Description | None = None
    priority: Priority | None = None
    is_active: bool | None = None
    notify_priority: NotifyLevel | None = None
    link_ids: LinkIds | None = None
    shape_kind: Literal["region", "circle", "polygon"] | None = None
    region_keys: RegionKeys | None = None
    center: LatLng | None = None
    radius_m: Radius | None = None
    geometry: PolygonGeometry | None = None


class GeofenceOut(BaseModel):
    id: str
    name: str
    description: str | None
    shape_kind: ShapeKind
    region_keys: list[str] | None
    # The drawn polygon; for a circle, its buffered polygon, for drawing.
    geometry: dict[str, Any] | None
    center: LatLng | None
    radius_m: float | None
    priority: int
    is_active: bool
    notify_priority: NotifyPriority
    link_ids: list[str] | None
    unknown_region_keys: list[str]
    matches_7d: int
    created_by: str | None
    created_at: dt.datetime
    updated_at: dt.datetime


class CountryOut(BaseModel):
    key: str


class DivisionOut(BaseModel):
    key: str
    code: str
    country: str
    name: str


class RegionsOut(BaseModel):
    countries: list[CountryOut]
    divisions: list[DivisionOut]


class PlacedOut(BaseModel):
    country_code: str | None
    admin1: str | None


class TestResultOut(BaseModel):
    geofence_id: str
    name: str
    result: GeofenceState
    reason: str | None


class TestOut(BaseModel):
    placed: PlacedOut
    state: GeofenceState | None
    results: list[TestResultOut]


# ---------------------------------------------------------------------------
# Validation -- read-only, before any write
# ---------------------------------------------------------------------------


def _catalog(config: Config) -> regions.Catalog:
    catalog = regions.catalog(config)
    if catalog is None:
        msg = "The region list is not installed yet: the GeoNames admin1 table is missing."
        raise GeoDbUnavailable(msg)
    return catalog


def _region_keys(keys: list[str], config: Config) -> list[str]:
    """De-duplicated, in order; every key well formed and in the catalogue."""
    unique = list(dict.fromkeys(keys))
    catalog = _catalog(config)
    errors = [
        FieldError(
            field=f"region_keys.{i}",
            code="GEOFENCE_UNKNOWN_REGION",
            message=f"{key!r} is not a country or state the region list knows.",
        )
        for i, key in enumerate(unique)
        if not regions.well_formed(key) or key in catalog.unknown([key])
    ]
    if errors:
        raise GeofenceUnknownRegion("Unknown region.", errors=errors)
    return unique


async def _check_polygon(db: DbSession, geometry: PolygonGeometry) -> str:
    """The GeoJSON to store, once PostGIS has called it valid (F6.AC4)."""
    if geometry.vertices > MAX_VERTICES:
        raise GeofenceTooManyVertices(
            f"{geometry.vertices} points; at most {MAX_VERTICES}.",
            errors=[
                FieldError(
                    field="geometry.coordinates",
                    code="GEOFENCE_TOO_MANY_VERTICES",
                    message=f"At most {MAX_VERTICES} points.",
                )
            ],
        )
    geojson = json.dumps(geometry.model_dump())
    try:
        async with db.begin_nested() as savepoint:
            row = (
                await db.execute(
                    text(
                        "SELECT valid, reason, ST_Y(location), ST_X(location) "
                        "FROM ST_IsValidDetail(ST_SetSRID(ST_GeomFromGeoJSON(:g), 4326))"
                    ),
                    {"g": geojson},
                )
            ).one()
            await savepoint.rollback()
    except DBAPIError as exc:
        raise GeofenceInvalidGeometry(
            "PostGIS could not read the polygon.",
            errors=[
                FieldError(
                    field="geometry",
                    code="GEOFENCE_INVALID_GEOMETRY",
                    message="Not a readable polygon.",
                )
            ],
        ) from exc
    valid, reason, lat, lng = row
    if not valid:
        where = GeoPoint(lat=float(lat), lng=float(lng)) if lat is not None else None
        raise GeofenceInvalidGeometry(
            f"Invalid polygon: {reason}.",
            errors=[
                FieldError(
                    field="geometry",
                    code="GEOFENCE_INVALID_GEOMETRY",
                    message=str(reason),
                    location=where,
                )
            ],
        )
    return geojson


async def _link_ids(db: DbSession, ids: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
    if ids is None:
        return None
    unique = list(dict.fromkeys(ids))
    known = set((await db.execute(select(Link.id).where(Link.id.in_(unique)))).scalars())
    errors = [
        FieldError(field=f"link_ids.{i}", code="UNKNOWN_LINK", message="No such link.")
        for i, link_id in enumerate(unique)
        if link_id not in known
    ]
    if errors:
        raise ValidationFailed("One or more fields are invalid.", errors=errors)
    return unique


# ---------------------------------------------------------------------------
# The shape's columns, as SQL
# ---------------------------------------------------------------------------

_POINT = "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography"


def _shape_sql(
    kind: ShapeKind,
    *,
    keys: list[str] | None = None,
    center: LatLng | None = None,
    radius_m: float | None = None,
    geojson: str | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """``{column: SQL expression}`` and its parameters, for exactly one shape. Every
    shape column is always written, so a shape change can never leave a stray one
    behind (and the ``*_iff_*`` CHECKs would refuse it if it did)."""
    params: dict[str, Any] = {"shape_kind": kind.value}
    columns = {
        "shape_kind": "CAST(:shape_kind AS shape_kind)",
        "area": "NULL",
        "center": "NULL",
        "radius_m": "NULL",
        "region_keys": "NULL",
    }
    if kind is ShapeKind.REGION:
        columns["region_keys"] = "CAST(:region_keys AS text[])"
        params["region_keys"] = keys
    elif kind is ShapeKind.CIRCLE:
        assert center is not None and radius_m is not None
        columns["center"] = _POINT
        columns["area"] = f"ST_Buffer({_POINT}, :radius_m, 'quad_segs={CIRCLE_QUAD_SEGS}')"
        columns["radius_m"] = ":radius_m"
        params |= {"lat": center.lat, "lng": center.lng, "radius_m": radius_m}
    else:
        columns["area"] = "ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326)::geography"
        params["geojson"] = geojson
    return columns, params


async def _prepared_shape(
    db: DbSession,
    config: Config,
    kind: ShapeKind,
    *,
    keys: list[str] | None,
    center: LatLng | None,
    radius_m: float | None,
    geometry: PolygonGeometry | None,
) -> tuple[dict[str, str], dict[str, Any]]:
    if kind is ShapeKind.REGION:
        assert keys is not None
        return _shape_sql(kind, keys=_region_keys(keys, config))
    if kind is ShapeKind.CIRCLE:
        return _shape_sql(kind, center=center, radius_m=radius_m)
    assert geometry is not None
    return _shape_sql(kind, geojson=await _check_polygon(db, geometry))


# ---------------------------------------------------------------------------
# Reading back
# ---------------------------------------------------------------------------


async def _matches_7d(db: DbSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = await db.execute(
        text(
            "SELECT m.id, count(*) FROM visits v, unnest(v.matched_geofence_ids) AS m(id) "
            "WHERE v.occurred_at >= now() - interval '7 days' "
            "AND cardinality(v.matched_geofence_ids) > 0 AND m.id = ANY(:ids) "
            "GROUP BY m.id"
        ),
        {"ids": ids},
    )
    return {r[0]: int(r[1]) for r in rows}


async def _read(db: DbSession, config: Config, ids: list[uuid.UUID] | None) -> list[GeofenceOut]:
    """Geofences with their geometry, highest priority first. ``None`` reads all."""
    geometry = (
        await db.execute(
            text(
                "SELECT id, ST_AsGeoJSON(area, 6), ST_Y(center::geometry), "
                "ST_X(center::geometry) FROM geofences "
                "WHERE (CAST(:ids AS uuid[]) IS NULL OR id = ANY(:ids))"
            ),
            {"ids": ids},
        )
    ).all()
    shapes = {r[0]: (r[1], r[2], r[3]) for r in geometry}
    # populate_existing: a row this request just changed in SQL is read afresh, not
    # served stale from the identity map.
    stmt = (
        select(Geofence)
        .order_by(Geofence.priority.desc(), Geofence.name, Geofence.id)
        .execution_options(populate_existing=True)
    )
    if ids is not None:
        stmt = stmt.where(Geofence.id.in_(ids))
    fences = list((await db.execute(stmt)).scalars())
    matches = await _matches_7d(db, [g.id for g in fences])
    catalog = regions.catalog(config)
    out = []
    for g in fences:
        area, lat, lng = shapes.get(g.id, (None, None, None))
        out.append(
            GeofenceOut(
                id=str(g.id),
                name=g.name,
                description=g.description,
                shape_kind=g.shape_kind,
                region_keys=g.region_keys,
                geometry=json.loads(area) if area else None,
                center=LatLng(lat=lat, lng=lng) if lat is not None else None,
                radius_m=float(g.radius_m) if g.radius_m is not None else None,
                priority=g.priority,
                is_active=g.is_active,
                notify_priority=g.notify_priority,
                link_ids=[str(i) for i in g.link_ids] if g.link_ids is not None else None,
                # Without a catalogue nothing can be called unknown.
                unknown_region_keys=catalog.unknown(g.region_keys or []) if catalog else [],
                matches_7d=matches.get(g.id, 0),
                created_by=str(g.created_by) if g.created_by else None,
                created_at=g.created_at,
                updated_at=g.updated_at,
            )
        )
    return out


async def _get(db: DbSession, geofence_id: str) -> Geofence:
    try:
        parsed = uuid.UUID(geofence_id)
    except ValueError as exc:
        raise NotFound("No such geofence.") from exc
    fence = (await db.execute(select(Geofence).where(Geofence.id == parsed))).scalar_one_or_none()
    if fence is None:
        raise NotFound("No such geofence.")
    return fence


async def _one(db: DbSession, config: Config, geofence_id: uuid.UUID) -> GeofenceOut:
    (out,) = await _read(db, config, [geofence_id])
    return out


def _audit_kwargs(
    request: Request, principal: Principal, config: Config, target: uuid.UUID
) -> dict[str, Any]:
    return {
        "actor_admin_id": principal.admin.id,
        "actor_ip_prefix": prefix_of(client_ip(request, config)),
        "target_type": "geofence",
        "target_id": str(target),
        "trace_id": getattr(request.state, "trace_id", None),
    }


# ---------------------------------------------------------------------------
# Import and export (F6.AC9)
# ---------------------------------------------------------------------------

MAX_IMPORT_FEATURES = 200
_CREATE: TypeAdapter[RegionCreate | CircleCreate | PolygonCreate] = TypeAdapter(GeofenceCreate)
# A create's checked shape SQL, its parameters and its links.
Prepared = tuple[dict[str, str], dict[str, Any], list[uuid.UUID] | None]


class FeatureIn(BaseModel):
    type: Literal["Feature"]
    geometry: dict[str, Any] | None
    properties: dict[str, Any] = Field(default_factory=dict)


class FeatureCollectionIn(BaseModel):
    type: Literal["FeatureCollection"]
    features: Annotated[list[FeatureIn], Field(min_length=1, max_length=MAX_IMPORT_FEATURES)]


_PROPERTIES = ("name", "description", "priority", "is_active", "notify_priority", "link_ids")


def _feature_body(feature: FeatureIn) -> dict[str, Any]:
    """A feature as a create body: the shape from its geometry, the rest from its
    properties. ``id`` and unknown properties are ignored -- an import creates."""
    props = feature.properties
    body: dict[str, Any] = {k: props[k] for k in _PROPERTIES if k in props}
    geometry = feature.geometry
    if geometry is None:
        body |= {"shape_kind": "region", "region_keys": props.get("region_keys")}
    elif geometry.get("type") == "Point":
        coordinates = geometry.get("coordinates") or [None, None]
        body |= {
            "shape_kind": "circle",
            "center": {"lat": coordinates[1], "lng": coordinates[0]},
            "radius_m": props.get("radius_m"),
        }
    else:
        body |= {"shape_kind": "polygon", "geometry": geometry}
    return body


def _prefixed(prefix: str, errors: list[FieldError]) -> list[FieldError]:
    return [e.model_copy(update={"field": f"{prefix}.{e.field}"}) for e in errors]


async def _prepare(db: DbSession, config: Config, payload: GeofenceCreate) -> Prepared:
    """Everything a create checks, before anything is written (E14)."""
    shape, params = await _prepared_shape(
        db,
        config,
        ShapeKind(payload.shape_kind),
        keys=payload.region_keys if isinstance(payload, RegionCreate) else None,
        center=payload.center if isinstance(payload, CircleCreate) else None,
        radius_m=payload.radius_m if isinstance(payload, CircleCreate) else None,
        geometry=payload.geometry if isinstance(payload, PolygonCreate) else None,
    )
    return shape, params, await _link_ids(db, payload.link_ids)


async def _insert(
    db: DbSession,
    payload: GeofenceCreate,
    prepared: Prepared,
    created_by: uuid.UUID,
) -> uuid.UUID:
    shape, params, links = prepared
    fence_id = uuid.uuid4()
    columns = {
        "id": ":id",
        "name": ":name",
        "description": ":description",
        "priority": ":priority",
        "is_active": ":is_active",
        "notify_priority": "CAST(:notify_priority AS notify_priority)",
        "link_ids": "CAST(:link_ids AS uuid[])",
        "created_by": ":created_by",
        **shape,
    }
    await db.execute(
        text(
            f"INSERT INTO geofences ({', '.join(columns)}) "  # noqa: S608 -- fixed column names
            f"VALUES ({', '.join(columns.values())})"
        ),
        {
            **params,
            "id": fence_id,
            "name": payload.name,
            "description": payload.description,
            "priority": payload.priority,
            "is_active": payload.is_active,
            "notify_priority": payload.notify_priority,
            "link_ids": links,
            "created_by": created_by,
        },
    )
    return fence_id


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[GeofenceOut], summary="List geofences")
async def list_geofences(
    principal: CurrentPrincipal, db: DbSession, config: Config
) -> list[GeofenceOut]:
    del principal
    return await _read(db, config, None)


@router.get(
    "/regions",
    response_model=RegionsOut,
    summary="Every country and division a region geofence can name",
    description=(
        "Built from the GeoNames admin1 table the engine names strict states from, so a "
        "key is spelled exactly as a strict state is (ADR-0020). Country names are left "
        "to the browser."
    ),
)
async def list_regions(
    principal: CurrentPrincipal, config: Config, response: Response
) -> RegionsOut:
    del principal
    catalog = _catalog(config)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return RegionsOut(
        countries=[CountryOut(key=c) for c in catalog.countries],
        divisions=[
            DivisionOut(key=d.key, code=d.code, country=d.country, name=d.name)
            for d in catalog.divisions
        ],
    )


@router.post(
    "/test",
    response_model=TestOut,
    summary="Evaluate a coordinate against the active geofences",
    description=(
        "Treated as a consented GPS fix: the coordinate is the geopoint, and its country "
        "and state are named from GeoNames as S1's are. Creates no visit (F6.AC10)."
    ),
)
async def test_coordinate(
    point: LatLng, principal: CurrentPrincipal, db: DbSession, config: Config
) -> TestOut:
    del principal
    country: str | None = None
    admin1: str | None = None
    gc = geocoder(config)
    if gc is not None:
        named = gc.place(
            Candidate(
                source=InferenceSource.GPS, level=GeoLevel.POINT, lat=point.lat, lng=point.lng
            )
        )
        country, admin1 = named.country_code, named.admin1
    place = StrictPlace(country_code=country, admin1=admin1, has_geopoint=True)
    fences = await store.load_active(db)
    evaluation = await store.evaluate_point(db, fences, lat=point.lat, lng=point.lng, place=place)
    return TestOut(
        placed=PlacedOut(country_code=country, admin1=admin1),
        state=evaluation.state,
        results=[
            TestResultOut(
                geofence_id=str(e.geofence.id),
                name=e.geofence.name,
                result=e.result.state,
                reason=e.result.reason.value if e.result.reason else None,
            )
            for e in evaluation.results
        ],
    )


@router.get(
    "/export",
    summary="Every geofence as GeoJSON",
    description=(
        "A FeatureCollection in the import format, so an export re-imports unchanged: a "
        "polygon is a Polygon, a circle a Point with `radius_m`, a region a feature with "
        "no geometry and `region_keys`."
    ),
    responses={200: {"content": {"application/geo+json": {}}}},
)
async def export_geofences(principal: CurrentPrincipal, db: DbSession, config: Config) -> Response:
    del principal
    features = []
    for g in await _read(db, config, None):
        properties: dict[str, Any] = {
            "id": g.id,
            "name": g.name,
            "description": g.description,
            "priority": g.priority,
            "is_active": g.is_active,
            "notify_priority": g.notify_priority.value,
            "link_ids": g.link_ids,
        }
        geometry: dict[str, Any] | None
        if g.shape_kind is ShapeKind.REGION:
            geometry = None
            properties["region_keys"] = g.region_keys
        elif g.shape_kind is ShapeKind.CIRCLE:
            assert g.center is not None
            geometry = {"type": "Point", "coordinates": [g.center.lng, g.center.lat]}
            properties["radius_m"] = g.radius_m
        else:
            geometry = g.geometry
        features.append({"type": "Feature", "geometry": geometry, "properties": properties})
    return Response(
        content=json.dumps({"type": "FeatureCollection", "features": features}),
        media_type="application/geo+json",
        headers={"Content-Disposition": 'attachment; filename="geofences.geojson"'},
    )


@router.post(
    "/import",
    response_model=list[GeofenceOut],
    status_code=status.HTTP_201_CREATED,
    summary="Create geofences from GeoJSON, all or nothing",
    description=(
        "Each feature is checked exactly as a create would be; one invalid feature fails "
        "the request with its errors addressed as `features.{i}.<field>`, and nothing is "
        "saved (F6.AC9)."
    ),
)
async def import_geofences(
    collection: FeatureCollectionIn,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    config: Config,
) -> list[GeofenceOut]:
    checked: list[tuple[RegionCreate | CircleCreate | PolygonCreate, Prepared]] = []
    errors: list[FieldError] = []
    for i, feature in enumerate(collection.features):
        prefix = f"features.{i}"
        try:
            payload = _CREATE.validate_python(_feature_body(feature))
        except PydanticValidationError as exc:
            errors.extend(
                FieldError(
                    field=".".join([prefix, *(str(p) for p in err["loc"][1:])]),
                    code=str(err["type"]).upper(),
                    message=str(err["msg"]),
                )
                for err in exc.errors()
            )
            continue
        try:
            checked.append((payload, await _prepare(db, config, payload)))
        except (
            ValidationFailed,
            GeofenceInvalidGeometry,
            GeofenceTooManyVertices,
            GeofenceUnknownRegion,
        ) as exc:
            errors.extend(_prefixed(prefix, exc.errors))
    if errors:
        raise ValidationFailed(
            "One or more features are invalid; nothing was imported.", errors=errors
        )

    ids = [
        await _insert(db, payload, prepared, principal.admin.id) for payload, prepared in checked
    ]
    await audit.record(
        db,
        action=audit.Action.GEOFENCE_IMPORTED,
        detail={"count": len(ids), "names": [p.name for p, _ in checked]},
        actor_admin_id=principal.admin.id,
        actor_ip_prefix=prefix_of(client_ip(request, config)),
        target_type="geofence",
        trace_id=getattr(request.state, "trace_id", None),
    )
    out = {o.id: o for o in await _read(db, config, ids)}
    return [out[str(i)] for i in ids]


@router.get("/{geofence_id}", response_model=GeofenceOut, summary="One geofence")
async def get_geofence(
    geofence_id: str, principal: CurrentPrincipal, db: DbSession, config: Config
) -> GeofenceOut:
    del principal
    return await _one(db, config, (await _get(db, geofence_id)).id)


@router.post(
    "",
    response_model=GeofenceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a geofence",
)
async def create_geofence(
    payload: GeofenceCreate,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    config: Config,
) -> GeofenceOut:
    prepared = await _prepare(db, config, payload)
    fence_id = await _insert(db, payload, prepared, principal.admin.id)
    await audit.record(
        db,
        action=audit.Action.GEOFENCE_CREATED,
        detail={
            "name": payload.name,
            "shape_kind": payload.shape_kind,
            "region_keys": prepared[1].get("region_keys"),
            "notify_priority": payload.notify_priority,
            "priority": payload.priority,
            "is_active": payload.is_active,
        },
        **_audit_kwargs(request, principal, config, fence_id),
    )
    return await _one(db, config, fence_id)


@router.patch(
    "/{geofence_id}",
    response_model=GeofenceOut,
    summary="Update a geofence",
    description=(
        "Any subset. Changing `shape_kind` needs that shape's fields too. Past visits "
        "keep the result they were evaluated with."
    ),
)
async def update_geofence(
    geofence_id: str,
    payload: GeofenceUpdate,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    config: Config,
) -> GeofenceOut:
    fence = await _get(db, geofence_id)
    given = payload.model_fields_set
    kind = ShapeKind(payload.shape_kind) if payload.shape_kind else fence.shape_kind
    shape_fields = {"region_keys", "center", "radius_m", "geometry"} & given

    # --- validate everything first (E14) ------------------------------------
    for field in ("name", "priority", "is_active", "notify_priority", "shape_kind"):
        if field in given and getattr(payload, field) is None:
            raise ValidationFailed(
                "One or more fields are invalid.",
                errors=[FieldError(field=field, code="REQUIRED", message="Cannot be null.")],
            )
    shape: dict[str, str] | None = None
    params: dict[str, Any] = {}
    if kind is not fence.shape_kind or shape_fields:
        needed = {
            ShapeKind.REGION: {"region_keys"},
            ShapeKind.POLYGON: {"geometry"},
            ShapeKind.CIRCLE: {"center", "radius_m"},
        }[kind]
        wrong = shape_fields - needed
        missing = needed - given
        if kind is ShapeKind.CIRCLE and fence.shape_kind is ShapeKind.CIRCLE:
            missing = set()  # one of centre and radius may be changed alone
        if wrong or missing:
            raise ValidationFailed(
                "The shape's fields do not match its kind.",
                errors=[
                    *(
                        FieldError(field=f, code="NOT_FOR_SHAPE", message=f"Not a {kind} field.")
                        for f in sorted(wrong)
                    ),
                    *(
                        FieldError(field=f, code="REQUIRED", message=f"A {kind} needs it.")
                        for f in sorted(missing)
                    ),
                ],
            )
        center = payload.center
        radius = payload.radius_m
        if kind is ShapeKind.CIRCLE and (center is None or radius is None):
            current = await _one(db, config, fence.id)
            center = center or current.center
            radius = radius if radius is not None else current.radius_m
        shape, params = await _prepared_shape(
            db,
            config,
            kind,
            keys=payload.region_keys,
            center=center,
            radius_m=radius,
            geometry=payload.geometry,
        )
    links = await _link_ids(db, payload.link_ids) if "link_ids" in given else None

    # --- then write -----------------------------------------------------------
    sets: dict[str, str] = {}
    detail: dict[str, Any] = {}
    for field in ("name", "description", "priority", "is_active", "notify_priority"):
        if field in given:
            value = getattr(payload, field)
            if value != getattr(fence, field):
                cast = (
                    "CAST(:notify_priority AS notify_priority)"
                    if field == "notify_priority"
                    else f":{field}"
                )
                sets[field] = cast
                params[field] = value
                detail[field] = {"from": getattr(fence, field), "to": value}
    if "link_ids" in given:
        current_links = list(fence.link_ids) if fence.link_ids is not None else None
        if links != current_links:
            sets["link_ids"] = "CAST(:link_ids AS uuid[])"
            params["link_ids"] = links
            detail["link_ids"] = {
                "from": [str(i) for i in current_links] if current_links else None,
                "to": [str(i) for i in links] if links else None,
            }
    if shape is not None:
        sets |= shape
        detail["shape"] = {"from": fence.shape_kind.value, "to": kind.value}
        if kind is ShapeKind.REGION:
            detail["region_keys"] = {"from": fence.region_keys, "to": params["region_keys"]}

    if sets:
        sets["updated_at"] = "now()"
        params["id"] = fence.id
        assignments = ", ".join(f"{c} = {e}" for c, e in sets.items())
        await db.execute(
            text(f"UPDATE geofences SET {assignments} WHERE id = :id"),  # noqa: S608 -- fixed names
            params,
        )
        await audit.record(
            db,
            action=audit.Action.GEOFENCE_UPDATED,
            detail=json.loads(json.dumps(detail, default=str)),
            **_audit_kwargs(request, principal, config, fence.id),
        )
    return await _one(db, config, fence.id)


@router.delete(
    "/{geofence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a geofence",
    description="Visits that matched it keep its id in `matched_geofence_ids`.",
)
async def delete_geofence(
    geofence_id: str,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    config: Config,
) -> Response:
    fence = await _get(db, geofence_id)
    detail = {"name": fence.name, "shape_kind": fence.shape_kind.value}
    await db.execute(delete(Geofence).where(Geofence.id == fence.id))
    await audit.record(
        db,
        action=audit.Action.GEOFENCE_DELETED,
        detail=detail,
        **_audit_kwargs(request, principal, config, fence.id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
