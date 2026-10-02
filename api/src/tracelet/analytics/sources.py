"""Choosing between rollups and raw rows for one request (ADR-0016).

A *source* is a subquery with the columns of a rollup table, whichever kind of rows
are behind it. Endpoints aggregate ``source.rel.c.*`` and never ask which it was; the
choice, and the honesty about it, live here.

The rollups answer a request only when **all** of these hold:

1. every filter it sets is a dimension of the table being read;
2. its window starts and ends on bucket boundaries in the reporting timezone;
3. every local day in the window has been built, in the current reporting timezone
   (a day never built would otherwise read as a day with no visits -- B5 again);
4. for hourly buckets, the window lies inside the 14 days the hourly table keeps.

Otherwise the same projection runs over raw rows, and the response says so.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Literal

from sqlalchemy import ColumnElement, Subquery, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.analytics.filters import (
    CELL_FIELDS,
    DIM_FIELDS,
    VisitFilter,
    Window,
    visit_clauses,
)
from tracelet.analytics.models import DailyCell, DimDaily, HourlyCell, RollupState
from tracelet.analytics.projection import (
    CELL_KEYS,
    CELL_MEASURES,
    Dimension,
    Grain,
    cell_select,
    dim_select,
)
from tracelet.analytics.rollup import HOURLY_KEEP_DAYS, today
from tracelet.capture.models import Visit

ComputedFrom = Literal["rollup", "raw"]


@dataclasses.dataclass(frozen=True, slots=True)
class Source:
    rel: Subquery
    computed_from: ComputedFrom
    refreshed_at: dt.datetime | None


async def _built_through(db: AsyncSession, window: Window) -> dt.datetime | None:
    """The oldest refresh among the window's days, or ``None`` if any is unbuilt.

    A day before the oldest retained visit counts as built: ``occurred_at`` is the
    server's receive time, so no visit can ever arrive for it, and its rollup is
    complete by definition -- empty, or written before its visits were purged. Without
    this a year-long calendar on a new deployment always fell back to scanning raw
    rows (measured at the design load: 7 s on one CPU).
    """
    days = window.days()
    rows = (
        await db.execute(
            select(RollupState.day, RollupState.refreshed_at).where(
                RollupState.day.in_(days), RollupState.reporting_tz == window.zone_name
            )
        )
    ).all()
    built = {row[0]: row[1] for row in rows}
    missing = [d for d in days if d not in built]
    if missing:
        oldest_visit = (await db.execute(select(func.min(Visit.occurred_at)))).scalar_one_or_none()
        first_day = oldest_visit.astimezone(window.tz).date() if oldest_visit is not None else None
        if first_day is not None and any(d >= first_day for d in missing):
            return None
    return min(built.values()) if built else dt.datetime.now(dt.UTC)


def _cell_clauses(
    f: VisitFilter, table: type[DailyCell] | type[HourlyCell]
) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    if f.link_id is not None:
        clauses.append(table.link_id == f.link_id)
    if f.stage:
        clauses.append(table.stage.in_(f.stage))
    kept = f.classifications()
    if kept is not None:
        clauses.append(table.classification.in_(kept))
    if f.device_class:
        clauses.append(table.device_class.in_(f.device_class))
    if f.connection_class:
        clauses.append(table.connection_class.in_(f.connection_class))
    if f.country_code is not None:
        clauses.append(table.country_code == f.country_code)
    if f.admin1 is not None:
        clauses.append(table.admin1 == f.admin1)
    return clauses


async def cell_source(db: AsyncSession, f: VisitFilter, window: Window, grain: Grain) -> Source:
    """The cell source for ``f`` over ``window`` at ``grain``."""
    aligned = window.aligned_to_days() if grain is Grain.DAY else window.aligned_to_hours()
    in_hourly_range = grain is Grain.DAY or (
        window.days()[0] >= today(window.zone_name) - dt.timedelta(days=HOURLY_KEEP_DAYS - 1)
    )
    if f.set_fields() <= CELL_FIELDS and aligned and in_hourly_range:
        refreshed = await _built_through(db, window)
        if refreshed is not None:
            return Source(_rollup_cells(f, window, grain), "rollup", refreshed)

    stmt = cell_select(window.zone_name, grain).where(window.range_clause(), *visit_clauses(f))
    return Source(stmt.subquery("cells"), "raw", dt.datetime.now(dt.UTC))


def _rollup_cells(f: VisitFilter, window: Window, grain: Grain) -> Subquery:
    first = window.start.astimezone(window.tz)
    last = window.end.astimezone(window.tz)
    if grain is Grain.DAY:
        table: type[DailyCell] | type[HourlyCell] = DailyCell
        bucket = DailyCell.day
        bounds = [DailyCell.day >= first.date(), DailyCell.day < last.date()]
    else:
        table = HourlyCell
        bucket = HourlyCell.hour
        bounds = [
            HourlyCell.hour >= first.replace(tzinfo=None),
            HourlyCell.hour < last.replace(tzinfo=None),
        ]
    columns = [bucket.label("bucket")]
    columns += [getattr(table, k).label(k) for k in CELL_KEYS[1:]]
    columns += [getattr(table, m).label(m) for m in CELL_MEASURES]
    return select(*columns).where(*bounds, *_cell_clauses(f, table)).subquery("cells")


async def dim_source(
    db: AsyncSession, f: VisitFilter, window: Window, dimension: Dimension
) -> Source:
    """The long-format source for one dimension."""
    if f.set_fields() <= DIM_FIELDS and window.aligned_to_days():
        refreshed = await _built_through(db, window)
        if refreshed is not None:
            first = window.start.astimezone(window.tz).date()
            last = window.end.astimezone(window.tz).date()
            clauses: list[ColumnElement[bool]] = [
                DimDaily.dimension == dimension.value,
                DimDaily.day >= first,
                DimDaily.day < last,
            ]
            if f.link_id is not None:
                clauses.append(DimDaily.link_id == f.link_id)
            kept = f.classifications()
            if kept is not None:
                clauses.append(DimDaily.classification.in_(kept))
            rolled = select(
                DimDaily.day.label("bucket"),
                DimDaily.link_id,
                DimDaily.classification,
                DimDaily.value,
                DimDaily.visit_count,
            ).where(*clauses)
            return Source(rolled.subquery("dims"), "rollup", refreshed)

    stmt = dim_select(window.zone_name, dimension).where(window.range_clause(), *visit_clauses(f))
    return Source(stmt.subquery("dims"), "raw", dt.datetime.now(dt.UTC))
