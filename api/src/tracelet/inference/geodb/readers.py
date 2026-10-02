"""Readers for the installed databases, and the engine toolkit built from them.

Every reader is opened memory-mapped (``maxminddb.MODE_MMAP``, IP2Location
``SHARED_MEMORY``), so a database is page cache shared by both workers, not RSS
(CLAUDE.md section 5). Readers are cached per process and keyed by the real path behind
``current``: when an update swaps the symlink, the next tick opens the new version and
the old reader is closed. No restart, and no request ever sees a half-written file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import IP2Location
import maxminddb
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.config import Settings, get_settings
from tracelet.inference import store
from tracelet.inference.engine import DbProducer, Toolkit
from tracelet.inference.geodb.catalog import BY_NAME, DatabaseSpec
from tracelet.inference.geodb.geonames import ReverseGeocoder
from tracelet.inference.geodb.installer import current_path
from tracelet.inference.sources import IPAddress
from tracelet.inference.types import Candidate, GeoLevel, InferenceSource

log = structlog.get_logger(__name__)

# Which installed database answers "which network is this", in order of preference.
ASN_DATABASES: tuple[str, ...] = ("geolite2-asn", "dbip-asn-lite", "ipinfo-lite")


# ---------------------------------------------------------------------------
# Record -> candidate
# ---------------------------------------------------------------------------


def _english(node: Any) -> str | None:
    if isinstance(node, dict):
        names = node.get("names")
        if isinstance(names, dict):
            value = names.get("en")
            return str(value) if value else None
    return None


def _confidence_from_radius(radius_km: object) -> float:
    """MaxMind's accuracy radius, as a 0..1 quality. Absent means the source did not
    say, which is not the same as confident."""
    if not isinstance(radius_km, int | float):
        return 0.8
    if radius_km <= 25:
        return 1.0
    if radius_km <= 100:
        return 0.8
    if radius_km <= 500:
        return 0.5
    return 0.3


def city_candidate(source: InferenceSource, record: dict[str, Any] | None) -> list[Candidate]:
    """GeoLite2-City and DB-IP City Lite share one record shape."""
    if not record:
        return []
    country = (record.get("country") or {}).get("iso_code")
    if not country:
        return []
    subdivisions = record.get("subdivisions") or []
    admin1 = _english(subdivisions[0]) if subdivisions else None
    admin2 = _english(subdivisions[1]) if len(subdivisions) > 1 else None
    city = _english(record.get("city"))
    location = record.get("location") or {}
    lat, lng = location.get("latitude"), location.get("longitude")
    level = (
        GeoLevel.CITY
        if city
        else GeoLevel.ADMIN2
        if admin2
        else GeoLevel.ADMIN1
        if admin1
        else GeoLevel.COUNTRY
    )
    return [
        Candidate(
            source=source,
            level=level,
            country_code=str(country).upper(),
            admin1=admin1,
            admin2=admin2,
            city=city,
            lat=float(lat) if isinstance(lat, int | float) else None,
            lng=float(lng) if isinstance(lng, int | float) else None,
            raw_confidence=_confidence_from_radius(location.get("accuracy_radius")),
            evidence={"accuracy_radius_km": location.get("accuracy_radius")},
        )
    ]


def ipinfo_candidate(record: dict[str, Any] | None) -> list[Candidate]:
    """IPinfo Lite knows the country only."""
    if not record or not record.get("country_code"):
        return []
    return [
        Candidate(
            source=InferenceSource.IPINFO,
            level=GeoLevel.COUNTRY,
            country_code=str(record["country_code"]).upper(),
        )
    ]


def ip2location_candidate(result: Any) -> list[Candidate]:
    def field(name: str) -> str | None:
        value = getattr(result, name, None)
        text = str(value).strip() if value is not None else ""
        return None if not text or text == "-" or "INVALID" in text.upper() else text

    country = field("country_short")
    if country is None:
        return []
    admin1, city = field("region"), field("city")
    lat, lng = getattr(result, "latitude", None), getattr(result, "longitude", None)
    try:
        lat_f, lng_f = float(str(lat)), float(str(lng))
        located = not (lat_f == 0.0 and lng_f == 0.0)
    except (TypeError, ValueError):
        located = False
    return [
        Candidate(
            source=InferenceSource.IP2LOCATION,
            level=GeoLevel.CITY if city else GeoLevel.ADMIN1 if admin1 else GeoLevel.COUNTRY,
            country_code=country.upper(),
            admin1=admin1,
            city=city,
            lat=lat_f if located else None,
            lng=lng_f if located else None,
        )
    ]


def asn_from_record(name: str, record: dict[str, Any] | None) -> tuple[int | None, str | None]:
    if not record:
        return None, None
    if name == "ipinfo-lite":
        raw = str(record.get("asn") or "")
        number = int(raw[2:]) if raw.upper().startswith("AS") and raw[2:].isdigit() else None
        return number, record.get("as_name")
    number = record.get("autonomous_system_number")
    return (int(number) if isinstance(number, int) else None), record.get(
        "autonomous_system_organization"
    )


# ---------------------------------------------------------------------------
# Opening, per process
# ---------------------------------------------------------------------------


@dataclass
class _Open:
    real: Path
    handle: Any  # maxminddb.Reader or IP2Location.IP2Location


_open: dict[str, _Open] = {}
_geocoder: tuple[tuple[Path, Path], ReverseGeocoder] | None = None
_tor: tuple[Path, frozenset[str]] | None = None


def tor_exits(settings: Settings) -> frozenset[str] | None:
    """The installed Tor exit list, read once per version (a few thousand addresses)."""
    global _tor  # noqa: PLW0603 - a process-wide cache, like the readers
    path = current_path(settings, BY_NAME["tor-exits"])
    if not path.exists():
        return None
    real = path.resolve()
    if _tor is None or _tor[0] != real:
        with real.open(encoding="utf-8") as f:
            exits = frozenset(
                line.strip() for line in f if line.strip() and not line.startswith("#")
            )
        _tor = (real, exits)
        log.info("tor_exits_loaded", exits=len(exits))
    return _tor[1]


def _handle(settings: Settings, spec: DatabaseSpec) -> Any | None:
    path = current_path(settings, spec)
    if not path.exists():
        return None
    real = path.resolve()
    cached = _open.get(spec.name)
    if cached is not None and cached.real == real:
        return cached.handle
    handle: Any
    if spec.kind == "mmdb":
        handle = maxminddb.open_database(str(real), maxminddb.MODE_MMAP)
    elif spec.kind == "ip2location_bin":
        handle = IP2Location.IP2Location(str(real), "SHARED_MEMORY")
    else:
        return None
    if cached is not None:
        try:
            cached.handle.close()
        except Exception as exc:  # noqa: BLE001 - closing a superseded reader must not fail a tick
            log.warning("geo_reader_close_failed", database=spec.name, error=type(exc).__name__)
    _open[spec.name] = _Open(real, handle)
    log.info("geo_reader_opened", database=spec.name, version=real.parent.name)
    return handle


def geocoder(settings: Settings) -> ReverseGeocoder | None:
    """The GeoNames index, built once per process and rebuilt only after an update."""
    global _geocoder  # noqa: PLW0603 - a process-wide cache, like the readers
    cities = current_path(settings, BY_NAME["geonames-cities1000"])
    admin1 = current_path(settings, BY_NAME["geonames-admin1"])
    if not cities.exists() or not admin1.exists():
        return None
    key = (cities.resolve(), admin1.resolve())
    if _geocoder is None or _geocoder[0] != key:
        _geocoder = (key, ReverseGeocoder(key[0], key[1]))
        log.info("geonames_index_built", places=len(_geocoder[1]))
    return _geocoder[1]


def _producer(settings: Settings, spec: DatabaseSpec) -> DbProducer | None:
    handle = _handle(settings, spec)
    if handle is None:
        return None
    source = spec.feeds
    if source is None:
        return None
    if spec.kind == "ip2location_bin":
        return lambda ip: ip2location_candidate(handle.get_all(str(ip)))
    if source is InferenceSource.IPINFO:
        return lambda ip: ipinfo_candidate(handle.get(str(ip)))
    return lambda ip: city_candidate(source, handle.get(str(ip)))


def _asn_lookup(settings: Settings) -> Callable[[IPAddress], tuple[int | None, str | None]] | None:
    opened = [(name, _handle(settings, BY_NAME[name])) for name in ASN_DATABASES]
    usable = [(name, h) for name, h in opened if h is not None]
    if not usable:
        return None

    def lookup(ip: IPAddress) -> tuple[int | None, str | None]:
        for name, handle in usable:
            number, org = asn_from_record(name, handle.get(str(ip)))
            if number is not None:
                return number, org
        return None, None

    return lookup


async def build_toolkit(db: AsyncSession) -> Toolkit:
    """The engine's toolkit from whatever is installed right now."""
    settings = get_settings()
    databases: dict[InferenceSource, DbProducer] = {}
    for spec in BY_NAME.values():
        if spec.feeds is None:
            continue
        producer = _producer(settings, spec)
        if producer is not None:
            databases[spec.feeds] = producer
    gc = geocoder(settings)
    return Toolkit(
        lexicon=await store.load_lexicon(db),
        asn_lookup=_asn_lookup(settings),
        databases=databases,
        place=gc.place if gc is not None else None,
        tor_exits=tor_exits(settings),
    )
