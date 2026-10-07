"""The geo-database panel (F10.AC3, F10.AC4, DATA_MODEL section 8.3).

One entry per database in the catalogue, whether or not it has ever been installed: what
is serving, how old it is, the verdict, and the newest attempt. Installing is
``inference.geodb`` -- streamed to disk, validated in a memory-capped subprocess, swapped
by symlink, so a failed update leaves the previous version serving (RISKS R4). This module
only reads, and starts that install for one database when the owner asks.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.config import Settings
from tracelet.inference.geodb.catalog import CATALOG, DatabaseSpec
from tracelet.inference.geodb.installer import current_path
from tracelet.inference.models import GeoDatabase, GeoDbStatus

# A download row older than this is not still running; the worker that owned it is gone.
UPDATING_FOR: Final = dt.timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class Installed:
    version: str | None
    released_at: dt.datetime | None
    installed_at: dt.datetime | None
    size_bytes: int | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class Attempt:
    status: str
    at: dt.datetime
    error: str | None


@dataclass(frozen=True, slots=True)
class DatabaseState:
    name: str
    kind: str
    feeds: str | None
    attribution: str
    configured: bool
    staleness_days: int
    verdict: str  # up_to_date, stale, missing, not_configured
    age_days: int | None
    installed: Installed | None
    last_attempt: Attempt | None
    updating: bool


def _verdict(
    spec: DatabaseSpec,
    settings: Settings,
    installed: GeoDatabase | None,
    configured: bool,
    now: dt.datetime,
) -> tuple[str, int | None]:
    if installed is None:
        return ("missing" if configured else "not_configured"), None
    if not current_path(settings, spec).exists():
        return "missing", None
    age = now - (installed.installed_at or installed.created_at)
    return ("stale" if age > dt.timedelta(days=spec.staleness_days) else "up_to_date"), age.days


async def states(db: AsyncSession, settings: Settings) -> list[DatabaseState]:
    now = dt.datetime.now(dt.UTC)
    today = now.date()
    rows = list(
        (await db.execute(select(GeoDatabase).order_by(GeoDatabase.created_at.desc()))).scalars()
    )
    out: list[DatabaseState] = []
    for spec in CATALOG:
        mine = [r for r in rows if r.name == spec.name]
        installed = next((r for r in mine if r.status is GeoDbStatus.INSTALLED), None)
        newest = mine[0] if mine else None
        configured = spec.download(settings, today) is not None
        verdict, age = _verdict(spec, settings, installed, configured, now)
        out.append(
            DatabaseState(
                name=spec.name,
                kind=str(spec.kind),
                feeds=spec.feeds.value if spec.feeds is not None else None,
                attribution=spec.attribution,
                configured=configured,
                staleness_days=spec.staleness_days,
                verdict=verdict,
                age_days=age,
                installed=(
                    Installed(
                        version=installed.version,
                        released_at=installed.released_at,
                        installed_at=installed.installed_at,
                        size_bytes=installed.size_bytes,
                        sha256=installed.sha256,
                    )
                    if installed is not None
                    else None
                ),
                last_attempt=(
                    Attempt(
                        status=newest.status.value, at=newest.created_at, error=newest.last_error
                    )
                    if newest is not None
                    else None
                ),
                updating=newest is not None
                and newest.status is GeoDbStatus.DOWNLOADING
                and now - newest.created_at < UPDATING_FOR,
            )
        )
    return out
