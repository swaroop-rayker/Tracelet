"""The region catalogue: every key a region geofence can name (ADR-0020 decision 2).

Built from the GeoNames admin1 table the engine names strict states from
(``inference/geodb/geonames.py``), so a key is spelled exactly as a strict state is:
``IN|Karnataka`` matches ``strict_admin1 = 'Karnataka'`` because both come from one file.

Countries are the ISO codes that table covers, which is every country GeoNames divides.
Their display names are the browser's (``Intl.DisplayNames``): the API has no country
name table, and does not need one to match a strict ISO code.

Memory: about 4 000 short strings, a few hundred kilobytes, built once per installed
version of the file and rebuilt only after a geo-database update (M7).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tracelet.config import Settings
from tracelet.geofence.evaluate import KEY_SEPARATOR, RegionKey
from tracelet.inference.geodb.catalog import BY_NAME
from tracelet.inference.geodb.geonames import load_admin1
from tracelet.inference.geodb.installer import current_path


@dataclass(frozen=True, slots=True)
class Division:
    key: str
    code: str
    country: str
    name: str


@dataclass(frozen=True, slots=True)
class Catalog:
    countries: tuple[str, ...]
    divisions: tuple[Division, ...]
    keys: frozenset[str]

    def unknown(self, keys: Iterable[str]) -> list[str]:
        """The keys this catalogue does not list, in their given order."""
        return [k for k in keys if k not in self.keys]


def build(admin1: dict[str, str]) -> Catalog:
    """From ``{"IN.19": "Karnataka", ...}``."""
    divisions: dict[str, Division] = {}
    countries: set[str] = set()
    for code, name in admin1.items():
        country, _, _ = code.partition(".")
        if len(country) != 2 or not name:
            continue
        countries.add(country)
        key = f"{country}{KEY_SEPARATOR}{name}"
        # Two codes with one name in one country would be one key; the first is kept.
        divisions.setdefault(key, Division(key, code, country, name))
    ordered = tuple(sorted(divisions.values(), key=lambda d: (d.country, d.name)))
    return Catalog(
        countries=tuple(sorted(countries)),
        divisions=ordered,
        keys=frozenset(countries) | frozenset(divisions),
    )


_cached: tuple[tuple[Path, int], Catalog] | None = None


def catalog(settings: Settings) -> Catalog | None:
    """The catalogue for the installed admin1 file, or ``None`` if none is installed."""
    global _cached  # noqa: PLW0603 - a process-wide cache, like the geo readers
    path = current_path(settings, BY_NAME["geonames-admin1"])
    if not path.exists():
        return None
    real = path.resolve()
    stamp = (real, real.stat().st_mtime_ns)
    if _cached is None or _cached[0] != stamp:
        _cached = (stamp, build(load_admin1(real)))
    return _cached[1]


def well_formed(key: str) -> bool:
    """``CC`` or ``CC|Name``: upper-case ISO country, and a non-empty name."""
    parsed = RegionKey.parse(key)
    if len(parsed.country) != 2 or not parsed.country.isascii() or not parsed.country.isupper():
        return False
    return parsed.admin1 is None or bool(parsed.admin1.strip())
