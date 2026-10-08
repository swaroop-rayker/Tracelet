"""The geo-database panel (F10.AC3, F10.AC4, SPEC section 11 row 24, DATA_MODEL section 8.3).

One entry per database in the catalogue, whether or not it has ever been installed: what is
serving, its **state**, an update's progress, whether the scheduler updates it, and what the
last release check found. Installing is ``inference.geodb`` -- streamed to disk, validated in
a memory-capped subprocess, swapped by symlink, so a failed update leaves the previous
version serving (RISKS R4). This module only reads, and starts that install when asked.

The state, in this order of precedence:

1. ``updating`` -- an attempt in flight, with its phase and a percent;
2. ``update_failed`` -- the newest attempt failed (the installed copy keeps serving);
3. ``unable_to_update`` -- no credentials, or the last release check could not reach the
   vendor;
4. ``not_installed``;
5. ``update_available`` -- the check found a newer release, or, for a database that is not
   checked over the network, its refresh schedule says it is due;
6. ``up_to_date``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.config import Settings
from tracelet.inference.geodb.catalog import CATALOG, DatabaseSpec
from tracelet.inference.geodb.check import NOT_CHECKED, newer_release
from tracelet.inference.geodb.installer import current_path
from tracelet.inference.geodb.maintenance import REFRESH_DAYS
from tracelet.inference.models import GeoDatabase, GeoDatabaseSettings, GeoDbStatus

# A download row older than this is not still running; the worker that owned it is gone.
UPDATING_FOR: Final = dt.timedelta(hours=1)

# Overall percent at each step after the download, which is 0-80 %. Steps without a
# measurable size get a fixed point, so the number always moves forward.
STEP_PERCENT: Final = {"verifying": 82, "unpacking": 85, "validating": 90, "installing": 97}
DOWNLOAD_SHARE: Final = 80


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
class Progress:
    phase: str
    percent: int | None


@dataclass(frozen=True, slots=True)
class Latest:
    version: str | None
    released_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class DatabaseState:
    name: str
    kind: str
    feeds: str | None
    attribution: str
    configured: bool
    auto_update: bool
    staleness_days: int
    stale: bool
    state: str
    progress: Progress | None
    age_days: int | None
    installed: Installed | None
    latest: Latest | None
    last_attempt: Attempt | None
    check_error: str | None
    checked_at: dt.datetime | None


def progress_of(row: GeoDatabase) -> Progress:
    phase = row.phase or "downloading"
    if phase == "downloading":
        if row.total_bytes and row.progress_bytes is not None:
            share = min(1.0, row.progress_bytes / row.total_bytes)
            return Progress(phase, int(share * DOWNLOAD_SHARE))
        return Progress(phase, 0 if row.progress_bytes is None else None)
    return Progress(phase, STEP_PERCENT.get(phase))


def state_of(
    *,
    spec: DatabaseSpec,
    configured: bool,
    installed: GeoDatabase | None,
    file_present: bool,
    newest: GeoDatabase | None,
    setting: GeoDatabaseSettings | None,
    now: dt.datetime,
) -> tuple[str, Progress | None]:
    """The state and, while updating, the progress. Pure, so it is tested on its own."""
    if (
        newest is not None
        and newest.status is GeoDbStatus.DOWNLOADING
        and now - newest.created_at < UPDATING_FOR
    ):
        return "updating", progress_of(newest)
    if newest is not None and newest.status is GeoDbStatus.FAILED:
        return "update_failed", None
    if not configured or (setting is not None and setting.check_error is not None):
        return "unable_to_update", None
    if installed is None or not file_present:
        return "not_installed", None
    if setting is not None and newer_release(
        installed.version, installed.released_at, setting.latest_version, setting.latest_released_at
    ):
        return "update_available", None
    if spec.name in NOT_CHECKED:
        age = now - (installed.installed_at or installed.created_at)
        if age > dt.timedelta(days=REFRESH_DAYS.get(spec.name, 30)):
            return "update_available", None
    return "up_to_date", None


async def states(db: AsyncSession, settings: Settings) -> list[DatabaseState]:
    now = dt.datetime.now(dt.UTC)
    today = now.date()
    rows = list(
        (await db.execute(select(GeoDatabase).order_by(GeoDatabase.created_at.desc()))).scalars()
    )
    prefs = {row.name: row for row in (await db.execute(select(GeoDatabaseSettings))).scalars()}
    out: list[DatabaseState] = []
    for spec in CATALOG:
        mine = [r for r in rows if r.name == spec.name]
        installed = next((r for r in mine if r.status is GeoDbStatus.INSTALLED), None)
        newest = mine[0] if mine else None
        setting = prefs.get(spec.name)
        configured = spec.download(settings, today) is not None
        file_present = installed is not None and current_path(settings, spec).exists()
        state, progress = state_of(
            spec=spec,
            configured=configured,
            installed=installed,
            file_present=file_present,
            newest=newest,
            setting=setting,
            now=now,
        )
        age = (
            now - (installed.installed_at or installed.created_at)
            if installed is not None
            else None
        )
        out.append(
            DatabaseState(
                name=spec.name,
                kind=str(spec.kind),
                feeds=spec.feeds.value if spec.feeds is not None else None,
                attribution=spec.attribution,
                configured=configured,
                auto_update=setting.auto_update if setting is not None else True,
                staleness_days=spec.staleness_days,
                stale=age is not None and age > dt.timedelta(days=spec.staleness_days),
                state=state,
                progress=progress,
                age_days=age.days if age is not None else None,
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
                latest=(
                    Latest(setting.latest_version, setting.latest_released_at)
                    if setting is not None
                    and (
                        setting.latest_version is not None or setting.latest_released_at is not None
                    )
                    else None
                ),
                last_attempt=(
                    Attempt(
                        status=newest.status.value, at=newest.created_at, error=newest.last_error
                    )
                    if newest is not None
                    else None
                ),
                check_error=(
                    setting.check_error
                    if setting is not None and setting.check_error
                    else (None if configured else "Vendor credentials are not configured.")
                ),
                checked_at=setting.checked_at if setting is not None else None,
            )
        )
    return out
