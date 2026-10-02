"""Building the rollups (ADR-0016).

A refresh **deletes and re-inserts** whole local days, in one transaction. Re-deriving a
day from its visits, rather than incrementing counters as visits arrive, is what makes a
rollup self-healing: a visit re-inferred, re-classified or purged is reflected the next
time its day is rebuilt, and a bug fixed in a projection is fixed in history by
`tracelet analytics rebuild`.

Two jobs and one command call :func:`refresh_days`:

* ``rollup`` (every 5 minutes) -- yesterday and today. Keeps today live.
* ``rollup_settle`` (daily) -- the last 7 days, plus up to 31 days that have never been
  built, oldest first. On a fresh deployment this back-fills history a month per run
  without a long transaction.
* ``tracelet analytics rebuild`` -- any range, on demand.

All three take one blocking advisory lock, so two refreshes never interleave a delete
and an insert on the same day (which would be a primary-key violation, not merely
wasted work).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import zoneinfo
from collections.abc import Iterable, Sequence

import structlog
from sqlalchemy import Select, delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.analytics.filters import local_midnight
from tracelet.analytics.models import DailyCell, DimDaily, HourlyCell, RollupState
from tracelet.analytics.projection import (
    CELL_KEYS,
    CELL_MEASURES,
    Dimension,
    Grain,
    cell_select,
    dim_select,
)
from tracelet.capture.models import Visit
from tracelet.config import Settings, get_settings
from tracelet.db.engine import session_scope

log = structlog.get_logger(__name__)

HOURLY_KEEP_DAYS = 14
SETTLE_DAYS = 7
BACKFILL_DAYS_PER_RUN = 31

_LOCK_KEY = int.from_bytes(hashlib.sha256(b"tracelet.rollup.refresh").digest()[:8], "big") & (
    0x7FFF_FFFF_FFFF_FFFF
)


def today(zone: str, *, now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.UTC)).astimezone(zoneinfo.ZoneInfo(zone)).date()


def _runs(days: Iterable[dt.date]) -> list[tuple[dt.date, dt.date]]:
    """Group days into contiguous inclusive runs, so each run is one range scan."""
    runs: list[tuple[dt.date, dt.date]] = []
    for day in sorted(set(days)):
        if runs and runs[-1][1] + dt.timedelta(days=1) == day:
            runs[-1] = (runs[-1][0], day)
        else:
            runs.append((day, day))
    return runs


def _grouped_cells(
    zone: str, grain: Grain, start: dt.datetime, end: dt.datetime
) -> Select[tuple[object, ...]]:
    src = (
        cell_select(zone, grain)
        .where(Visit.occurred_at >= start, Visit.occurred_at < end)
        .subquery("src")
    )
    keys = [src.c[k] for k in CELL_KEYS]
    return select(*keys, *(func.sum(src.c[m]) for m in CELL_MEASURES)).group_by(*keys)


def _grouped_dims(
    zone: str, dimension: Dimension, start: dt.datetime, end: dt.datetime
) -> Select[tuple[object, ...]]:
    src = (
        dim_select(zone, dimension)
        .where(Visit.occurred_at >= start, Visit.occurred_at < end)
        .subquery("src")
    )
    keys = [src.c.bucket, src.c.link_id, src.c.classification, src.c.dimension, src.c.value]
    return select(*keys, func.count()).group_by(*keys)


async def _refresh_run(
    db: AsyncSession, first: dt.date, last: dt.date, zone: str, *, hourly_from: dt.date
) -> None:
    tz = zoneinfo.ZoneInfo(zone)
    start = local_midnight(first, tz)
    end = local_midnight(last + dt.timedelta(days=1), tz)
    naive_start = dt.datetime.combine(first, dt.time(0))
    naive_end = dt.datetime.combine(last + dt.timedelta(days=1), dt.time(0))

    await db.execute(delete(DailyCell).where(DailyCell.day.between(first, last)))
    await db.execute(
        delete(HourlyCell).where(HourlyCell.hour >= naive_start, HourlyCell.hour < naive_end)
    )
    await db.execute(delete(DimDaily).where(DimDaily.day.between(first, last)))

    cell_columns = ["day", *CELL_KEYS[1:], *CELL_MEASURES]
    await db.execute(
        insert(DailyCell).from_select(cell_columns, _grouped_cells(zone, Grain.DAY, start, end))
    )
    if last >= hourly_from:
        hourly_start = max(start, local_midnight(hourly_from, tz))
        await db.execute(
            insert(HourlyCell).from_select(
                ["hour", *CELL_KEYS[1:], *CELL_MEASURES],
                _grouped_cells(zone, Grain.HOUR, hourly_start, end),
            )
        )
    for dimension in Dimension:
        await db.execute(
            insert(DimDaily).from_select(
                ["day", "link_id", "classification", "dimension", "value", "visit_count"],
                _grouped_dims(zone, dimension, start, end),
            )
        )


async def refresh_days(
    db: AsyncSession, days: Sequence[dt.date], zone: str, *, now: dt.datetime | None = None
) -> int:
    """Rebuild every rollup for ``days`` (local dates in ``zone``). Returns days rebuilt.

    The caller owns the transaction: nothing here commits, so a failure leaves every
    day exactly as it was.
    """
    if not days:
        return 0
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    current = today(zone, now=now)
    hourly_from = current - dt.timedelta(days=HOURLY_KEEP_DAYS - 1)
    for first, last in _runs(days):
        await _refresh_run(db, first, last, zone, hourly_from=hourly_from)

    # The hourly table keeps 14 days (DATA_MODEL 9.2); the daily ones keep everything.
    await db.execute(
        delete(HourlyCell).where(HourlyCell.hour < dt.datetime.combine(hourly_from, dt.time(0)))
    )
    unique_days = sorted(set(days))
    await db.execute(delete(RollupState).where(RollupState.day.in_(unique_days)))
    await db.execute(
        insert(RollupState),
        [
            {"day": d, "refreshed_at": dt.datetime.now(dt.UTC), "reporting_tz": zone}
            for d in unique_days
        ],
    )
    return len(unique_days)


async def missing_days(db: AsyncSession, zone: str, *, until: dt.date, limit: int) -> list[dt.date]:
    """Days between the first visit and ``until`` never built in ``zone``, oldest first."""
    first_visit = (await db.execute(select(func.min(Visit.occurred_at)))).scalar_one_or_none()
    if first_visit is None:
        return []
    first = first_visit.astimezone(zoneinfo.ZoneInfo(zone)).date()
    if first > until:
        return []
    built = set(
        (
            await db.execute(
                select(RollupState.day).where(
                    RollupState.day.between(first, until), RollupState.reporting_tz == zone
                )
            )
        ).scalars()
    )
    gaps: list[dt.date] = []
    day = first
    while day <= until and len(gaps) < limit:
        if day not in built:
            gaps.append(day)
        day += dt.timedelta(days=1)
    return gaps


# ---------------------------------------------------------------------------
# Scheduler entry points (ADR-0009)
# ---------------------------------------------------------------------------


async def run_live_once(settings: Settings | None = None) -> int:
    """Yesterday and today: what the 5-minute ``rollup`` job rebuilds."""
    zone = (settings or get_settings()).reporting_tz
    current = today(zone)
    async with session_scope() as db:
        built = await refresh_days(db, [current - dt.timedelta(days=1), current], zone)
        await db.commit()
    return built


async def run_settle_once(settings: Settings | None = None) -> int:
    """The last week, plus up to a month of never-built history (``rollup_settle``)."""
    zone = (settings or get_settings()).reporting_tz
    current = today(zone)
    async with session_scope() as db:
        recent = [current - dt.timedelta(days=i) for i in range(SETTLE_DAYS)]
        gaps = await missing_days(db, zone, until=current, limit=BACKFILL_DAYS_PER_RUN)
        built = await refresh_days(db, [*recent, *gaps], zone)
        await db.commit()
    log.info("rollup_settled", days=built, backfilled=len(gaps))
    return built


async def rebuild(since: dt.date, until: dt.date, settings: Settings | None = None) -> int:
    """Rebuild ``[since, until]`` a month per transaction. For the CLI."""
    zone = (settings or get_settings()).reporting_tz
    built = 0
    day = since
    while day <= until:
        chunk_end = min(until, day + dt.timedelta(days=BACKFILL_DAYS_PER_RUN - 1))
        days = [day + dt.timedelta(days=i) for i in range((chunk_end - day).days + 1)]
        async with session_scope() as db:
            built += await refresh_days(db, days, zone)
            await db.commit()
        day = chunk_end + dt.timedelta(days=1)
    return built
