"""Offline reverse geocoding from GeoNames (F4.AC4) -- pure Python, no numpy (ADR-0005).

Two jobs, one index:

* **Name a point.** Consented GPS (S1) arrives as a coordinate; this turns it into a city,
  state and country without any outbound request.
* **Spell every place the same way.** The sources disagree on names -- "Bangalore" and
  "Bengaluru", "National Capital Territory of Delhi" and "Delhi" -- and consensus groups
  votes by name, so two sources naming one city differently would split its vote.
  Every candidate with coordinates is re-named from its nearest GeoNames place, and the
  source's own names are kept in its evidence.

**Memory.** ``cities1000`` has ~150 k places worldwide. Kept: every place in India
(India-primary, CLAUDE.md section 1) and places of 15 000+ people elsewhere (globally
functional) -- roughly a third of the file. Coordinates live in ``array('d')`` and names
are interned, a few megabytes per worker; lookups use 1-degree grid buckets, so a query
touches nine cells, not the whole table.
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass, replace
from pathlib import Path
from sys import intern
from typing import Final

from tracelet.inference.types import Candidate, GeoLevel, InferenceSource

HOME_COUNTRY: Final = "IN"
ELSEWHERE_MIN_POPULATION: Final = 15_000
CITY_KM: Final = 30.0
ADMIN1_KM: Final = 100.0
COUNTRY_KM: Final = 300.0
# A populated place "covers" a radius that grows with its population: Mumbai's single
# GeoNames point stands for a metro some 40 km across, a town's for a few kilometres.
# Among the places whose cover contains a point, the one it is most *central* to wins
# (distance as a fraction of that cover): South Mumbai is Mumbai, not a nearer small
# place, and central Thane is Thane, not the larger Mumbai whose cover reaches it.
COVER_KM_PER_SQRT_PERSON: Final = 0.006
COVER_MIN_KM: Final = 3.0
COVER_MAX_KM: Final = 25.0
# GeoNames feature codes that are not places a person would say they are in:
# neighbourhoods (PPLX), and historical, abandoned or destroyed places.
SKIPPED_FEATURES: Final = frozenset({"PPLX", "PPLH", "PPLQ", "PPLW", "PPLCH"})
EARTH_KM: Final = 6371.0088


@dataclass(frozen=True, slots=True)
class Place:
    geonameid: int
    name: str
    country: str
    admin1: str | None
    lat: float
    lng: float
    distance_km: float


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def load_admin1(path: Path) -> dict[str, str]:
    """``IN.19`` -> ``Karnataka``, in the ASCII spelling every other source uses."""
    names: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            fields = line.rstrip("\n").split("\t")
            if len(fields) >= 3:
                names[fields[0]] = intern(fields[2] or fields[1])
    return names


def cover_km(population: int) -> float:
    return min(COVER_MAX_KM, max(COVER_MIN_KM, COVER_KM_PER_SQRT_PERSON * math.sqrt(population)))


class ReverseGeocoder:
    def __init__(self, cities: Path, admin1: Path) -> None:
        admin1_names = load_admin1(admin1)
        self._ids = array("q")
        self._lat = array("d")
        self._lng = array("d")
        self._population = array("q")
        self._name: list[str] = []
        self._country: list[str] = []
        self._admin1: list[str | None] = []
        self._grid: dict[tuple[int, int], list[int]] = {}
        with cities.open(encoding="utf-8") as f:
            for line in f:
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 15:
                    continue
                if fields[7] in SKIPPED_FEATURES:
                    continue
                country = fields[8]
                population = int(fields[14] or 0)
                if country != HOME_COUNTRY and population < ELSEWHERE_MIN_POPULATION:
                    continue
                lat, lng = float(fields[4]), float(fields[5])
                index = len(self._name)
                self._ids.append(int(fields[0]))
                self._lat.append(lat)
                self._lng.append(lng)
                self._population.append(population)
                self._name.append(intern(fields[2] or fields[1]))
                self._country.append(intern(country))
                self._admin1.append(admin1_names.get(f"{country}.{fields[10]}"))
                self._grid.setdefault((math.floor(lat), math.floor(lng)), []).append(index)

    def __len__(self) -> int:
        return len(self._name)

    def nearest(self, lat: float, lng: float, max_km: float) -> Place | None:
        cell = (math.floor(lat), math.floor(lng))
        reach = max(1, math.ceil(max_km / 111.0))
        best: int | None = None
        best_km = max_km
        covering: int | None = None
        covering_km = 0.0
        for dlat in range(-reach, reach + 1):
            for dlng in range(-reach, reach + 1):
                for i in self._grid.get((cell[0] + dlat, cell[1] + dlng), ()):
                    km = _haversine_km(lat, lng, self._lat[i], self._lng[i])
                    if km > max_km:
                        continue
                    if km <= best_km:
                        best, best_km = i, km
                    if km <= cover_km(self._population[i]) and (
                        covering is None
                        or km / cover_km(self._population[i])
                        < covering_km / cover_km(self._population[covering])
                    ):
                        covering, covering_km = i, km
        if covering is not None:
            best, best_km = covering, covering_km
        if best is None:
            return None
        return Place(
            geonameid=self._ids[best],
            name=self._name[best],
            country=self._country[best],
            admin1=self._admin1[best],
            lat=self._lat[best],
            lng=self._lng[best],
            distance_km=round(best_km, 1),
        )

    # -----------------------------------------------------------------------

    def place(self, c: Candidate) -> Candidate:
        """Name ``c``'s point in GeoNames terms. Never deepens a network-derived claim:
        only GPS, which *is* a point, is promoted from a coordinate to a place."""
        if c.lat is None or c.lng is None:
            return c
        if c.source is InferenceSource.GPS:
            return self._name_gps(c)
        near = self.nearest(c.lat, c.lng, CITY_KM)
        if near is None or (c.country_code and c.country_code.upper() != near.country):
            return c
        evidence = {
            **c.evidence,
            "source_names": {"admin1": c.admin1, "city": c.city},
            "geonames": {"id": near.geonameid, "distance_km": near.distance_km},
        }
        return replace(
            c,
            country_code=near.country,
            admin1=near.admin1 or c.admin1,
            city=near.name if c.level is GeoLevel.CITY else c.city,
            evidence=evidence,
        )

    def _name_gps(self, c: Candidate) -> Candidate:
        assert c.lat is not None and c.lng is not None
        for max_km, level in (
            (CITY_KM, GeoLevel.CITY),
            (ADMIN1_KM, GeoLevel.ADMIN1),
            (COUNTRY_KM, GeoLevel.COUNTRY),
        ):
            near = self.nearest(c.lat, c.lng, max_km)
            if near is None:
                continue
            if level is GeoLevel.ADMIN1 and near.admin1 is None:
                continue
            return replace(
                c,
                level=level,
                country_code=near.country,
                admin1=near.admin1 if level is not GeoLevel.COUNTRY else None,
                city=near.name if level is GeoLevel.CITY else None,
                evidence={
                    **c.evidence,
                    "reverse_geocoded": {
                        "id": near.geonameid,
                        "place": near.name,
                        "distance_km": near.distance_km,
                    },
                },
            )
        return c
