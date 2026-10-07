"""Geofence evaluation, the pure part (ADR-0020 decisions 4 and 5, F6.AC5-AC7).

Every applicable geofence yields one of three results, and **an abstention is never
rounded down to "outside"** (F6.AC6):

* a **region** is decided from the strict country and strict state alone -- never an
  advisory field, which is a guess (ADR-0018);
* a **polygon or circle** is decided by ``ST_Covers(area, geopoint)`` in the database,
  and is undetermined when the visit has no geopoint.

"Outside" always needs a strict location the engine actually stated. That is also what
``ck_visits_outside_needs_strict`` enforces, so a result this module produces can never
be refused by the database.
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from tracelet.capture.models import GeofenceState

KEY_SEPARATOR: Final = "|"


class Reason(enum.StrEnum):
    """Why a geofence could not be decided. Shown by the coordinate test (F6.AC10)."""

    NO_STRICT_COUNTRY = "no_strict_country"
    NO_STRICT_ADMIN1 = "no_strict_admin1"
    NO_GEOPOINT = "no_geopoint"


@dataclass(frozen=True, slots=True)
class Result:
    state: GeofenceState
    reason: Reason | None = None


INSIDE: Final = Result(GeofenceState.INSIDE)
OUTSIDE: Final = Result(GeofenceState.OUTSIDE)


@dataclass(frozen=True, slots=True)
class RegionKey:
    """``IN`` (a country) or ``IN|Karnataka`` (a first-order division of one)."""

    country: str
    admin1: str | None

    @classmethod
    def parse(cls, key: str) -> RegionKey:
        country, sep, admin1 = key.partition(KEY_SEPARATOR)
        return cls(country, admin1 if sep else None)


@dataclass(frozen=True, slots=True)
class StrictPlace:
    """What the engine *stated* about a visit. Advisory fields are deliberately absent."""

    country_code: str | None
    admin1: str | None
    has_geopoint: bool


def region_key_result(key: RegionKey, place: StrictPlace) -> Result:
    """One key against the strict place: ADR-0020 decision 4's table."""
    if place.country_code is None:
        return Result(GeofenceState.UNDETERMINED, Reason.NO_STRICT_COUNTRY)
    if place.country_code != key.country:
        return OUTSIDE
    if key.admin1 is None:
        return INSIDE
    if place.admin1 is None:
        # A strict India contains Karnataka, so it cannot rule it out.
        return Result(GeofenceState.UNDETERMINED, Reason.NO_STRICT_ADMIN1)
    return INSIDE if place.admin1 == key.admin1 else OUTSIDE


def region_result(keys: Iterable[str], place: StrictPlace) -> Result:
    """A region geofence: inside if any key is, outside if every key is."""
    results = [region_key_result(RegionKey.parse(k), place) for k in keys]
    if any(r.state is GeofenceState.INSIDE for r in results):
        return INSIDE
    undecided = [r for r in results if r.state is GeofenceState.UNDETERMINED]
    if undecided:
        return undecided[0]
    return OUTSIDE


def shape_result(covered: bool, place: StrictPlace) -> Result:
    """A polygon or circle, given whether ``ST_Covers`` held for the geopoint."""
    if not place.has_geopoint:
        return Result(GeofenceState.UNDETERMINED, Reason.NO_GEOPOINT)
    return INSIDE if covered else OUTSIDE


def combine(results: Sequence[Result]) -> GeofenceState | None:
    """The visit's state: inside if any, else undetermined if any, else outside.

    ``None`` -- no applicable geofence -- is distinct from all three, so "outside" never
    means "there were no geofences" (ADR-0020 decision 5).
    """
    if not results:
        return None
    states = {r.state for r in results}
    for state in (GeofenceState.INSIDE, GeofenceState.UNDETERMINED):
        if state in states:
            return state
    return GeofenceState.OUTSIDE


def applies(link_ids: Sequence[uuid.UUID] | None, link_id: uuid.UUID) -> bool:
    """A geofence applies to every link when ``link_ids`` is NULL."""
    return link_ids is None or link_id in link_ids
