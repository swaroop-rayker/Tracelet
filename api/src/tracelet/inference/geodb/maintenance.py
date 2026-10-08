"""Keeping the databases current, and recomputing what depends on them (F10.AC3, ADR-0009).

``update_all`` is both the daily scheduled job and ``tracelet geodb update``. It installs
whatever is due, and after any location or ASN database changes it recomputes
``asn_profiles`` -- a profile built from last month's database would flag the wrong
centroids (ADR-0005: "recomputed after every geo-database update").
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from tracelet.capture.models import AsnType
from tracelet.config import Settings, get_settings
from tracelet.db.engine import session_scope
from tracelet.inference.geodb.catalog import BY_NAME, CATALOG, DatabaseSpec
from tracelet.inference.geodb.check import (
    NOT_CHECKED,
    check_all,
    check_and_store,
    comparable,
    newer_release,
)
from tracelet.inference.geodb.geonames import CITY_KM
from tracelet.inference.geodb.installer import InstallResult, current_path, install
from tracelet.inference.geodb.readers import geocoder
from tracelet.inference.models import AsnProfile, GeoDatabase, GeoDatabaseSettings, GeoDbStatus
from tracelet.inference.sources.asn_org import asn_type, classify

log = structlog.get_logger(__name__)

# How old an installed copy may get before it is fetched again. DB-IP is keyed by its
# monthly edition instead, so it is fetched exactly when a new month's file exists.
REFRESH_DAYS: Final = {
    "geolite2-city": 7,
    "geolite2-asn": 7,
    "ipinfo-lite": 7,
    "ip2location-lite-db11": 30,
    "geonames-cities1000": 30,
    "geonames-admin1": 90,
    # Exits come and go within hours; daily is the floor of what is worth fetching.
    "tor-exits": 1,
}
# After a failed attempt, wait before trying that database again.
RETRY_AFTER: Final = dt.timedelta(hours=6)
PROFILE_TIMEOUT_S: Final = 900.0
# Below this much address space, "all of it on one point" is not evidence of anything: a
# local ISP with one /24 in Delhi is probably *in* Delhi. Such ASNs keep their centroid
# for display, with no modal_share, so rule (a) cannot fire on them.
MIN_PROFILE_ADDRESSES: Final = 16_384  # a /18

LOCATION_OR_ASN: Final = frozenset(
    {"dbip-city-lite", "dbip-asn-lite", "geolite2-city", "geolite2-asn", "ipinfo-lite"}
)


@dataclass(frozen=True, slots=True)
class _Latest:
    installed: GeoDatabase | None
    failed_recently: bool


async def _latest(spec: DatabaseSpec, now: dt.datetime) -> _Latest:
    async with session_scope() as db:
        rows = list(
            (
                await db.execute(
                    select(GeoDatabase)
                    .where(GeoDatabase.name == spec.name)
                    .order_by(GeoDatabase.created_at.desc())
                    .limit(5)
                )
            ).scalars()
        )
    installed = next((r for r in rows if r.status is GeoDbStatus.INSTALLED), None)
    newest = rows[0] if rows else None
    failed_recently = (
        newest is not None
        and newest.status is GeoDbStatus.FAILED
        and newest.created_at > now - RETRY_AFTER
    )
    return _Latest(installed, failed_recently)


def is_due(spec: DatabaseSpec, latest: _Latest, settings: Settings, today: dt.date) -> bool:
    if latest.failed_recently:
        return False
    if latest.installed is None or not current_path(settings, spec).exists():
        return True
    if spec.name.startswith("dbip-"):
        dl = spec.download(settings, today)
        return dl is not None and latest.installed.version != dl.version
    age = dt.datetime.now(dt.UTC) - (latest.installed.installed_at or latest.installed.created_at)
    return age > dt.timedelta(days=REFRESH_DAYS.get(spec.name, 30))


async def _settings_rows() -> dict[str, GeoDatabaseSettings]:
    async with session_scope() as db:
        rows = (await db.execute(select(GeoDatabaseSettings))).scalars()
        return {row.name: row for row in rows}


# A check this recent, and successful, is believed over the refresh period (row 26).
CHECK_TRUSTED_FOR: Final = dt.timedelta(hours=12)


def scheduled_due(
    spec: DatabaseSpec,
    latest: _Latest,
    row: GeoDatabaseSettings | None,
    settings: Settings,
    *,
    today: dt.date,
    now: dt.datetime,
) -> bool:
    """Whether the scheduler should download ``spec`` now (SPEC section 11 rows 24, 26).

    Not after a recent failure. Always when it is not installed or its file is missing.
    When the last release check succeeded within 12 hours and can be compared with the
    installed copy, **only** if it found a newer release -- the refresh period would download
    an unchanged file and spend the vendor's allowance. Otherwise -- IP2Location (never
    checked), a failed or old check, dates that cannot be compared -- the refresh period
    decides, as before, and a newer release found anyway still counts.
    """
    if latest.failed_recently:
        return False
    if latest.installed is None or not current_path(settings, spec).exists():
        return True
    if row is not None:
        args = (
            latest.installed.version,
            latest.installed.released_at,
            row.latest_version,
            row.latest_released_at,
        )
        trusted = (
            spec.name not in NOT_CHECKED
            and row.check_error is None
            and row.checked_at is not None
            and now - row.checked_at < CHECK_TRUSTED_FOR
            and comparable(*args)
        )
        if trusted:
            return newer_release(*args)
        if newer_release(*args):
            return True
    return is_due(spec, latest, settings, today)


async def manual_update_due(spec: DatabaseSpec, settings: Settings) -> bool:
    """The Update button asks the vendor first (SPEC section 11 row 26): download only if
    the check finds a newer release. Downloads without asking when there is nothing to
    compare with -- not installed, file missing, never checked (IP2Location), the check
    failed, or the dates do not compare -- because the owner did ask for an update."""
    now = dt.datetime.now(dt.UTC)
    latest = await _latest(spec, now)
    if latest.installed is None or not current_path(settings, spec).exists():
        return True
    if spec.name in NOT_CHECKED:
        return True
    found = await check_and_store(spec, settings)
    args = (
        latest.installed.version,
        latest.installed.released_at,
        found.latest_version,
        found.latest_released_at,
    )
    if found.error is not None or not comparable(*args):
        return True
    return newer_release(*args)


async def update_all(
    settings: Settings | None = None,
    *,
    only: list[str] | None = None,
    force: bool = False,
    respect_auto_update: bool = False,
) -> list[InstallResult]:
    """Install everything due (or everything named, with ``force``). Never raises.

    Due: see ``scheduled_due`` (SPEC section 11 rows 24 and 26). ``respect_auto_update`` is the scheduler's: a
    database whose automatic updates are off is left alone; a manual update ignores it.
    """
    settings = settings or get_settings()
    today = dt.datetime.now(dt.UTC).date()
    now = dt.datetime.now(dt.UTC)
    rows = await _settings_rows()
    results: list[InstallResult] = []
    for spec in CATALOG:
        if only is not None and spec.name not in only:
            continue
        row = rows.get(spec.name)
        if respect_auto_update and row is not None and not row.auto_update:
            continue
        if not force and not scheduled_due(
            spec, await _latest(spec, now), row, settings, today=today, now=now
        ):
            continue
        results.append(await install(spec, settings, today=today))
    if any(r.status == "installed" and r.name in LOCATION_OR_ASN for r in results):
        await recompute_profiles(settings)
    return results


async def run_job_once() -> int:
    """The scheduler's entry point (ADR-0009): every six hours, one worker at a time --
    check for releases, then update what is due and has automatic updates on."""
    await check_all()
    results = await update_all(respect_auto_update=True)
    return sum(1 for r in results if r.status == "installed")


# ---------------------------------------------------------------------------
# asn_profiles
# ---------------------------------------------------------------------------


def _installed(settings: Settings, names: tuple[str, ...]) -> list[tuple[str, str]]:
    found = []
    for name in names:
        path = current_path(settings, BY_NAME[name])
        if path.exists():
            found.append((name, str(path.resolve())))
    return found


async def recompute_profiles(settings: Settings | None = None) -> int:
    """Rebuild ``asn_profiles`` from the installed databases. Returns the row count."""
    settings = settings or get_settings()
    asn_dbs = _installed(settings, ("geolite2-asn", "dbip-asn-lite"))
    city_dbs = _installed(settings, ("geolite2-city", "dbip-city-lite"))
    if not asn_dbs or not city_dbs:
        log.info("asn_profiles_skipped", reason="an ASN and a city database are both required")
        return 0

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tracelet.inference.geodb.profiles",
        asn_dbs[0][1],
        *(path for _, path in city_dbs),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=PROFILE_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        log.error("asn_profiles_failed", reason="timeout")
        return 0
    if proc.returncode != 0:
        log.error(
            "asn_profiles_failed", reason=f"exit {proc.returncode}", stderr=err.decode()[-300:]
        )
        return 0

    gc = geocoder(settings)
    versions = {name: path.rsplit("/", 2)[-2] for name, path in [*asn_dbs, *city_dbs]}
    rows: list[dict[str, Any]] = []
    for line in out.decode().splitlines():
        p = json.loads(line)
        city, admin1 = p["modal_city"], p["modal_admin1"]
        # Spell the centroid the way candidates are spelled, or rule (a)'s name
        # comparison would silently never match (geonames module docstring).
        if gc is not None:
            near = gc.nearest(p["modal_lat"], p["modal_lng"], CITY_KM)
            if near is not None and near.country == "IN":
                city, admin1 = near.name, near.admin1 or admin1
        significant = p["addresses"] >= MIN_PROFILE_ADDRESSES
        network = classify(p["asn"], p["org"])
        kind = asn_type(network)
        rows.append(
            {
                "asn": p["asn"],
                "org": p["org"],
                "asn_type": kind if kind is not AsnType.UNKNOWN else AsnType.BROADBAND,
                "modal_lat": Decimal(str(p["modal_lat"])),
                "modal_lng": Decimal(str(p["modal_lng"])),
                "modal_city": city,
                "modal_admin1": admin1,
                "modal_share": Decimal(str(p["modal_share"])) if significant else None,
                "is_registry_artifact_source": significant and p["modal_share"] >= 0.30,
                "is_mobile": network.is_mobile,
                "is_hosting": network.is_hosting,
                "is_cgnat": network.is_cgnat,
                "computed_at": dt.datetime.now(dt.UTC),
                "source_db_versions": versions,
            }
        )
    async with session_scope() as db:
        await db.execute(delete(AsnProfile))
        for start in range(0, len(rows), 1000):
            chunk = rows[start : start + 1000]
            await db.execute(insert(AsnProfile).values(chunk))
    log.info("asn_profiles_recomputed", profiles=len(rows), databases=sorted(versions))
    return len(rows)
