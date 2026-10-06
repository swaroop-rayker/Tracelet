"""Geofence evaluation against the database (ADR-0015, ADR-0020, F6.AC5-AC8).

Runs **inside the inference job's per-visit savepoint**, never on the capture path: the
visit's location, its geofence state and (from M6's outbox) its alert are written
together or not at all (F7.AC5, NFR5.AC2).

Cost (F6.AC8, 5 ms p95): the active geofences are loaded once per job tick. Regions are
decided in Python from two strict codes. Polygons and circles cost one ``ST_Covers``
query under the GiST index, and only when the visit has a geopoint -- without one they
are undetermined and the database is not asked.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.capture.models import GeofenceState, Visit
from tracelet.geofence.evaluate import (
    Result,
    StrictPlace,
    applies,
    combine,
    region_result,
    shape_result,
)
from tracelet.geofence.models import Geofence, NotifyPriority, ShapeKind


@dataclass(frozen=True, slots=True)
class ActiveGeofence:
    id: uuid.UUID
    name: str
    shape_kind: ShapeKind
    region_keys: tuple[str, ...]
    priority: int
    notify_priority: NotifyPriority
    link_ids: tuple[uuid.UUID, ...] | None


@dataclass(frozen=True, slots=True)
class Evaluated:
    geofence: ActiveGeofence
    result: Result


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One visit's evaluation. ``results`` is ordered by priority, highest first, so
    the first inside result is the geofence that decides the alert (F6.AC7)."""

    results: tuple[Evaluated, ...]
    state: GeofenceState | None

    @property
    def matched(self) -> list[uuid.UUID]:
        return [e.geofence.id for e in self.results if e.result.state is GeofenceState.INSIDE]

    @property
    def deciding(self) -> ActiveGeofence | None:
        """The highest-priority geofence the visit is inside, if any."""
        for e in self.results:
            if e.result.state is GeofenceState.INSIDE:
                return e.geofence
        return None


async def load_active(db: AsyncSession) -> list[ActiveGeofence]:
    """Every active geofence, highest priority first (ties broken by id, so the order,
    and therefore the deciding geofence, is stable)."""
    rows = (
        await db.execute(
            select(Geofence)
            .where(Geofence.is_active)
            .order_by(Geofence.priority.desc(), Geofence.id)
        )
    ).scalars()
    return [
        ActiveGeofence(
            id=g.id,
            name=g.name,
            shape_kind=g.shape_kind,
            region_keys=tuple(g.region_keys or ()),
            priority=g.priority,
            notify_priority=g.notify_priority,
            link_ids=tuple(g.link_ids) if g.link_ids is not None else None,
        )
        for g in rows
    ]


async def _covering(
    db: AsyncSession, visit_id: uuid.UUID, shape_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """The shapes whose area covers the visit's geopoint. geography, so geodesic."""
    rows = await db.execute(
        text(
            "SELECT g.id FROM geofences g, visits v "
            "WHERE v.id = :visit AND g.id = ANY(:ids) AND ST_Covers(g.area, v.geopoint)"
        ),
        {"visit": visit_id, "ids": list(shape_ids)},
    )
    return {r[0] for r in rows}


async def evaluate(
    db: AsyncSession,
    fences: Sequence[ActiveGeofence],
    *,
    visit_id: uuid.UUID,
    link_id: uuid.UUID,
    place: StrictPlace,
) -> Evaluation:
    """Evaluate one visit against the geofences that apply to its link. Reads the
    visit's geopoint from the database, so call it after the geopoint is written."""
    applicable = [f for f in fences if applies(f.link_ids, link_id)]
    shapes = [f.id for f in applicable if f.shape_kind is not ShapeKind.REGION]
    covered = await _covering(db, visit_id, shapes) if shapes and place.has_geopoint else set()
    results = tuple(
        Evaluated(
            f,
            region_result(f.region_keys, place)
            if f.shape_kind is ShapeKind.REGION
            else shape_result(f.id in covered, place),
        )
        for f in applicable
    )
    return Evaluation(results, combine([e.result for e in results]))


async def record(db: AsyncSession, visit_id: uuid.UUID, evaluation: Evaluation) -> None:
    await db.execute(
        update(Visit)
        .where(Visit.id == visit_id)
        .values(geofence_state=evaluation.state, matched_geofence_ids=evaluation.matched)
    )
