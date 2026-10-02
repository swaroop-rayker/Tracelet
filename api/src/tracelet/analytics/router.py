"""Analytics (docs/API.md section 8, F9.AC2-F9.AC12, F9.AC19, F9.AC20).

Every response carries ``meta``: the window it covers, the reporting timezone its
buckets were cut in, whether it was computed from rollups or raw rows and how fresh
that was, and the **stage mix** of the visits behind it (F9.AC20). A chart that drops
from 80 % enriched to 20 % enriched should say so on its face, not leave the reader to
assume the visitors changed.

**Unknown is reported, not dropped.** Abstained locations, unscored confidences and
unidentified devices come back under an explicit key with a count, so a breakdown's
rows plus its unknowns always add up to the visits it was computed over.

**A figure the system cannot produce yet is ``null`` with a reason** -- notifications
(M6) in the funnel, precision before any ground truth exists (M8) -- never a zero that
a chart would plot as a measurement (F3.AC5).
"""

from __future__ import annotations

import datetime as dt
import enum
import itertools
import math
import uuid
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.analytics.filters import (
    AUTOMATED,
    VisitFilter,
    Window,
    resolve_window,
    visit_clauses,
    visit_filter,
)
from tracelet.analytics.projection import (
    BREAKDOWNS,
    Dimension,
    Expr,
    Grain,
    distinct_visitors,
)
from tracelet.analytics.sources import ComputedFrom, Source, cell_source, dim_source
from tracelet.auth.dependencies import Config, CurrentPrincipal, DbSession
from tracelet.capture.models import Classification, Link, Visit, VisitStage
from tracelet.capture.visits_router import VisitSummary, is_returning_column, summarize
from tracelet.config import Settings
from tracelet.errors import ValidationFailed

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

Filter = Annotated[VisitFilter, Depends(visit_filter)]

# A series or breakdown beyond this many keys folds the rest into "other", so a chart
# stays legible and a response stays small.
MAX_SERIES = 8
MAX_HOURLY_DAYS = 31
UNKNOWN_KEY = ""


# ---------------------------------------------------------------------------
# Shared response pieces
# ---------------------------------------------------------------------------


class StageMix(BaseModel):
    """How complete the visits behind a figure were (F9.AC20)."""

    total: int
    server: int  # still awaiting enrichment or the sweeper
    enriched: int
    server_only: int
    rate_limited: int


class Meta(BaseModel):
    start: dt.datetime
    end: dt.datetime
    reporting_tz: str
    computed_from: ComputedFrom
    refreshed_at: dt.datetime | None
    stage_mix: StageMix


def _int(value: object) -> int:
    """A SUM() result: NULL over no rows, Decimal or int otherwise."""
    if value is None:
        return 0
    if isinstance(value, int | Decimal):
        return int(value)
    msg = f"unexpected aggregate {value!r}"
    raise TypeError(msg)


async def _stage_mix(db: AsyncSession, cells: Source) -> StageMix:
    rel = cells.rel
    rows = (
        await db.execute(select(rel.c.stage, func.sum(rel.c.visit_count)).group_by(rel.c.stage))
    ).all()
    counts = {VisitStage(row[0]): _int(row[1]) for row in rows}
    return StageMix(
        total=sum(counts.values()),
        server=counts.get(VisitStage.SERVER, 0),
        enriched=counts.get(VisitStage.ENRICHED, 0),
        server_only=counts.get(VisitStage.SERVER_ONLY, 0),
        rate_limited=counts.get(VisitStage.RATE_LIMITED, 0),
    )


async def _meta(db: AsyncSession, window: Window, cells: Source, served: Source) -> Meta:
    return Meta(
        start=window.start,
        end=window.end,
        reporting_tz=window.zone_name,
        computed_from=served.computed_from,
        refreshed_at=served.refreshed_at,
        stage_mix=await _stage_mix(db, cells),
    )


def _live(rel_stage: ColumnElement[object]) -> ColumnElement[bool]:
    """Rate-limited rows are shedding records, not visits: counted in the mix only."""
    return rel_stage != VisitStage.RATE_LIMITED


# ---------------------------------------------------------------------------
# Summary (F9.AC2)
# ---------------------------------------------------------------------------


class Kpi(BaseModel):
    key: str
    unit: Literal["count", "ratio"]
    value: float | None
    previous: float | None
    # Relative change for counts; difference in percentage points for ratios.
    change: float | None
    reason: str | None = None


class Summary(BaseModel):
    meta: Meta
    previous_start: dt.datetime
    kpis: list[Kpi]


class _Tally:
    def __init__(self) -> None:
        self.visits = 0
        self.all_classes = 0
        self.humans = 0
        self.automated = 0
        self.consented = 0
        self.inside = 0
        self.outside = 0
        self.enriched = 0
        self.server_only = 0


async def _tally(db: AsyncSession, f: VisitFilter, window: Window) -> _Tally:
    # Read over every classification: the human and bot shares are *about*
    # classification, so the classification filter cannot apply to them.
    cells = await cell_source(db, f.without_classification(), window, Grain.DAY)
    rel = cells.rel
    rows = (
        await db.execute(
            select(
                rel.c.classification,
                rel.c.stage,
                func.sum(rel.c.visit_count),
                func.sum(rel.c.consented_count),
                func.sum(rel.c.geofence_inside_count),
                func.sum(rel.c.geofence_outside_count),
            )
            .where(_live(rel.c.stage))
            .group_by(rel.c.classification, rel.c.stage)
        )
    ).all()
    kept = f.classifications()
    t = _Tally()
    for classification, stage, visits, consented, inside, outside in rows:
        c = Classification(classification)
        n = _int(visits)
        t.all_classes += n
        if c is Classification.HUMAN:
            t.humans += n
        if c in AUTOMATED:
            t.automated += n
        if kept is not None and c not in kept:
            continue
        t.visits += n
        t.consented += _int(consented)
        t.inside += _int(inside)
        t.outside += _int(outside)
        if VisitStage(stage) is VisitStage.ENRICHED:
            t.enriched += n
        elif VisitStage(stage) is VisitStage.SERVER_ONLY:
            t.server_only += n
    return t


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


async def _unique_visitors(
    db: AsyncSession, f: VisitFilter, window: Window, settings: Settings
) -> tuple[int | None, str | None]:
    """Distinct visitors, from raw rows: a distinct count does not add (ADR-0016)."""
    horizon = dt.datetime.now(dt.UTC) - dt.timedelta(days=settings.retention_visit_days)
    if window.start < horizon:
        return None, "past_visit_retention"
    stmt = select(distinct_visitors()).where(
        window.range_clause(), Visit.stage != VisitStage.RATE_LIMITED, *visit_clauses(f)
    )
    return int((await db.execute(stmt)).scalar_one()), None


def _kpi(
    key: str,
    unit: Literal["count", "ratio"],
    now: float | None,
    before: float | None,
    reason: str | None = None,
) -> Kpi:
    change: float | None = None
    if now is not None and before is not None:
        if unit == "ratio":
            change = now - before
        elif before:
            change = (now - before) / before
    return Kpi(key=key, unit=unit, value=now, previous=before, change=change, reason=reason)


@router.get(
    "/summary",
    response_model=Summary,
    summary="KPIs with period-over-period change (F9.AC2)",
    description=(
        "The previous period is the window of equal length immediately before this one. "
        "Human and bot shares are computed across every classification, whatever the "
        "classification filter, because they are about classification. Unique visitors is "
        "always counted from raw rows and is null past the visit retention window."
    ),
)
async def summary(
    principal: CurrentPrincipal, db: DbSession, settings: Config, f: Filter
) -> Summary:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    previous = window.previous()
    now_t = await _tally(db, f, window)
    was_t = await _tally(db, f, previous)
    now_u, now_reason = await _unique_visitors(db, f, window, settings)
    was_u, _ = await _unique_visitors(db, f, previous, settings)

    geofence_reason = None if now_t.inside + now_t.outside else "no_geofence_evaluations"
    kpis = [
        _kpi("visits", "count", now_t.visits, was_t.visits),
        _kpi("unique_visitors", "count", now_u, was_u, now_reason),
        _kpi(
            "human_share",
            "ratio",
            _ratio(now_t.humans, now_t.all_classes),
            _ratio(was_t.humans, was_t.all_classes),
        ),
        _kpi(
            "bot_share",
            "ratio",
            _ratio(now_t.automated, now_t.all_classes),
            _ratio(was_t.automated, was_t.all_classes),
        ),
        _kpi(
            "consent_grant_rate",
            "ratio",
            _ratio(now_t.consented, now_t.visits),
            _ratio(was_t.consented, was_t.visits),
        ),
        _kpi(
            "geofence_hit_rate",
            "ratio",
            _ratio(now_t.inside, now_t.inside + now_t.outside),
            _ratio(was_t.inside, was_t.inside + was_t.outside),
            geofence_reason,
        ),
        _kpi(
            "enrichment_completion_rate",
            "ratio",
            _ratio(now_t.enriched, now_t.enriched + now_t.server_only),
            _ratio(was_t.enriched, was_t.enriched + was_t.server_only),
        ),
    ]
    # The stage mix is of the visits the filter selects, classification included.
    kept_cells = await cell_source(db, f, window, Grain.DAY)
    return Summary(
        meta=await _meta(db, window, kept_cells, kept_cells),
        previous_start=previous.start,
        kpis=kpis,
    )


# ---------------------------------------------------------------------------
# Time series (F9.AC3) and calendar (F9.AC6)
# ---------------------------------------------------------------------------


class SplitBy(enum.StrEnum):
    NONE = "none"
    CLASSIFICATION = "classification"
    DEVICE_CLASS = "device_class"
    CONNECTION_CLASS = "connection_class"
    COUNTRY = "country"
    ADMIN1 = "admin1"
    LINK = "link"


class Metric(enum.StrEnum):
    VISITS = "visits"
    CONSENTED = "consented"


class Series(BaseModel):
    key: str
    label: str
    values: list[int]


class TimeSeries(BaseModel):
    meta: Meta
    bucket: Grain
    buckets: list[dt.datetime]
    series: list[Series]


def _buckets(window: Window, grain: Grain) -> list[dt.datetime]:
    """Every bucket start in the window, as aware local times, oldest first."""
    if grain is Grain.DAY:
        return [dt.datetime.combine(d, dt.time(0), tzinfo=window.tz) for d in window.days()]
    seen: dict[dt.datetime, None] = {}
    moment = window.start.astimezone(dt.UTC).replace(minute=0, second=0, microsecond=0)
    while moment < window.end:
        local = moment.astimezone(window.tz).replace(minute=0, second=0, microsecond=0)
        seen.setdefault(local, None)
        moment += dt.timedelta(hours=1)
    return list(seen)


def _bucket_key(value: object) -> dt.date | dt.datetime:
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, dt.date):
        return value
    msg = f"unexpected bucket {value!r}"
    raise TypeError(msg)


async def _link_labels(db: AsyncSession, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = (
        await db.execute(select(Link.id, Link.slug).where(Link.id.in_([uuid.UUID(i) for i in ids])))
    ).all()
    return {str(row[0]): str(row[1]) for row in rows}


@router.get(
    "/timeseries",
    response_model=TimeSeries,
    summary="Visits by hour or day, optionally split (F9.AC3)",
    description=(
        "Buckets are local to the reporting timezone and zero-filled. A split keeps the "
        f"{MAX_SERIES - 1} largest keys and folds the rest into 'other'. Hourly windows are "
        f"limited to {MAX_HOURLY_DAYS} days."
    ),
)
async def timeseries(
    principal: CurrentPrincipal,
    db: DbSession,
    settings: Config,
    f: Filter,
    bucket: Annotated[Grain, Query()] = Grain.DAY,
    split_by: Annotated[SplitBy, Query()] = SplitBy.NONE,
    metric: Annotated[Metric, Query()] = Metric.VISITS,
) -> TimeSeries:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    if bucket is Grain.HOUR and window.end - window.start > dt.timedelta(days=MAX_HOURLY_DAYS):
        msg = f"Hourly buckets are limited to {MAX_HOURLY_DAYS} days; use daily buckets."
        raise ValidationFailed(msg)
    cells = await cell_source(db, f, window, bucket)
    rel = cells.rel
    measure = rel.c.visit_count if metric is Metric.VISITS else rel.c.consented_count

    split_cols: Sequence[Expr] = {
        SplitBy.NONE: [],
        SplitBy.CLASSIFICATION: [rel.c.classification],
        SplitBy.DEVICE_CLASS: [rel.c.device_class],
        SplitBy.CONNECTION_CLASS: [rel.c.connection_class],
        SplitBy.COUNTRY: [rel.c.country_code],
        SplitBy.ADMIN1: [rel.c.country_code, rel.c.admin1],
        SplitBy.LINK: [rel.c.link_id],
    }[split_by]
    rows = (
        await db.execute(
            select(rel.c.bucket, *split_cols, func.sum(measure))
            .where(_live(rel.c.stage))
            .group_by(rel.c.bucket, *split_cols)
        )
    ).all()

    starts = _buckets(window, bucket)
    index = {_bucket_key(b if bucket is Grain.HOUR else b.date()): i for i, b in enumerate(starts)}
    by_key: dict[str, list[int]] = defaultdict(lambda: [0] * len(starts))
    for row in rows:
        position = index.get(_bucket_key(row[0]))
        if position is None:
            continue
        parts = [str(getattr(p, "value", p)) if p is not None else "" for p in row[1:-1]]
        key = "|".join(parts) if parts else "all"
        if split_by is SplitBy.ADMIN1 and parts[1] == UNKNOWN_KEY:
            key = UNKNOWN_KEY
        by_key[key][position] += _int(row[-1])

    ranked = sorted(by_key.items(), key=lambda kv: (-sum(kv[1]), kv[0]))
    labels = await _link_labels(db, {k for k, _ in ranked}) if split_by is SplitBy.LINK else {}
    series = [
        Series(key=k, label=labels.get(k, _label(k)), values=v) for k, v in ranked[: MAX_SERIES - 1]
    ]
    if len(ranked) >= MAX_SERIES:
        folded = [sum(col) for col in zip(*(v for _, v in ranked[MAX_SERIES - 1 :]), strict=True)]
        series.append(Series(key="other", label="Other", values=folded))
    return TimeSeries(
        meta=await _meta(db, window, cells, cells),
        bucket=bucket,
        buckets=starts,
        series=series,
    )


def _label(key: str) -> str:
    return "Unknown" if key == UNKNOWN_KEY else key.replace("|", " · ")


class CalendarDay(BaseModel):
    day: dt.date
    count: int


class Calendar(BaseModel):
    meta: Meta
    days: list[CalendarDay]


@router.get(
    "/calendar",
    response_model=Calendar,
    summary="Daily volume for the calendar heatmap (F9.AC6)",
)
async def calendar(
    principal: CurrentPrincipal, db: DbSession, settings: Config, f: Filter
) -> Calendar:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    cells = await cell_source(db, f, window, Grain.DAY)
    rel = cells.rel
    rows = (
        await db.execute(
            select(rel.c.bucket, func.sum(rel.c.visit_count))
            .where(_live(rel.c.stage))
            .group_by(rel.c.bucket)
        )
    ).all()
    counts = {_bucket_key(row[0]): _int(row[1]) for row in rows}
    return Calendar(
        meta=await _meta(db, window, cells, cells),
        days=[CalendarDay(day=d, count=counts.get(d, 0)) for d in window.days()],
    )


# ---------------------------------------------------------------------------
# Breakdowns (F9.AC4), signals (F9.AC11), confidence (F9.AC9), source flow (F9.AC7)
# ---------------------------------------------------------------------------


class BreakdownDimension(enum.StrEnum):
    COUNTRY = "country"
    ADMIN1 = "admin1"
    CITY = "city"
    ASN = "asn"
    ISP = "isp"
    DEVICE_CLASS = "device_class"
    BROWSER = "browser"
    APP_MEDIUM = "app_medium"
    OS = "os"
    SCREEN = "screen"
    CONNECTION_CLASS = "connection_class"
    CLASSIFICATION = "classification"


assert {d.value for d in BreakdownDimension} == {d.value for d in BREAKDOWNS}


class BreakdownRow(BaseModel):
    key: str
    count: int
    share: float


class Breakdown(BaseModel):
    meta: Meta
    dimension: BreakdownDimension
    total: int
    rows: list[BreakdownRow]
    # Visits whose value is unknown -- for location, the engine abstained.
    unknown: int
    # Visits under keys beyond the limit.
    other: int


async def _dim_counts(db: AsyncSession, dims: Source) -> list[tuple[str, int]]:
    rel = dims.rel
    rows = (
        await db.execute(select(rel.c.value, func.sum(rel.c.visit_count)).group_by(rel.c.value))
    ).all()
    return sorted(((str(r[0]), _int(r[1])) for r in rows), key=lambda kv: (-kv[1], kv[0]))


@router.get(
    "/breakdown",
    response_model=Breakdown,
    summary="Visits by one dimension (F9.AC4)",
    description=(
        "Location dimensions use the strict fields only; abstentions are counted in "
        "`unknown`. `admin1` and `city` keys are qualified (`IN|Karnataka`, "
        "`IN|Karnataka|Bengaluru`) because names repeat across countries."
    ),
)
async def breakdown(
    principal: CurrentPrincipal,
    db: DbSession,
    settings: Config,
    f: Filter,
    dimension: Annotated[BreakdownDimension, Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Breakdown:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    dims = await dim_source(db, f, window, Dimension(dimension.value))
    cells = await cell_source(db, f, window, Grain.DAY)
    counts = await _dim_counts(db, dims)
    total = sum(n for _, n in counts)
    known = [(k, n) for k, n in counts if k != UNKNOWN_KEY]
    shown = known[:limit]
    return Breakdown(
        meta=await _meta(db, window, cells, dims),
        dimension=dimension,
        total=total,
        rows=[BreakdownRow(key=k, count=n, share=n / total if total else 0.0) for k, n in shown],
        unknown=sum(n for k, n in counts if k == UNKNOWN_KEY),
        other=sum(n for _, n in known[limit:]),
    )


class SignalRow(BaseModel):
    rule_id: str
    category: str
    count: int
    # Of the visits in scope, the share on which this rule fired.
    share: float


class Signals(BaseModel):
    meta: Meta
    visits: int
    rows: list[SignalRow]


@router.get(
    "/signals",
    response_model=Signals,
    summary="Which detection rules fire most often (F9.AC11)",
    description=(
        "Rules of category bot, spoof, spam and network. Absences and inference "
        "bookkeeping are reasons, not detections, and are not counted."
    ),
)
async def signals(
    principal: CurrentPrincipal, db: DbSession, settings: Config, f: Filter
) -> Signals:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    dims = await dim_source(db, f, window, Dimension.SIGNAL)
    cells = await cell_source(db, f, window, Grain.DAY)
    visits = _int(
        (
            await db.execute(
                select(func.sum(cells.rel.c.visit_count)).where(_live(cells.rel.c.stage))
            )
        ).scalar_one()
    )
    rows: list[SignalRow] = []
    for key, n in await _dim_counts(db, dims):
        category, _, rule_id = key.partition("|")
        rows.append(
            SignalRow(
                rule_id=rule_id, category=category, count=n, share=n / visits if visits else 0.0
            )
        )
    return Signals(meta=await _meta(db, window, cells, dims), visits=visits, rows=rows)


class Histogram(BaseModel):
    level: Literal["country", "admin1", "admin2", "city"]
    # Ten bins: [0.0, 0.1), [0.1, 0.2) ... [0.9, 1.0].
    bins: list[int]
    # Visits with no confidence at this level (not inferred, or no candidate).
    unscored: int


class Confidence(BaseModel):
    meta: Meta
    levels: list[Histogram]


_CONF_DIMS: dict[Literal["country", "admin1", "admin2", "city"], Dimension] = {
    "country": Dimension.CONF_COUNTRY,
    "admin1": Dimension.CONF_ADMIN1,
    "admin2": Dimension.CONF_ADMIN2,
    "city": Dimension.CONF_CITY,
}


@router.get(
    "/confidence",
    response_model=Confidence,
    summary="Confidence distribution per level (F9.AC9)",
)
async def confidence(
    principal: CurrentPrincipal, db: DbSession, settings: Config, f: Filter
) -> Confidence:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    cells = await cell_source(db, f, window, Grain.DAY)
    levels: list[Histogram] = []
    served: Source | None = None
    for level, dimension in _CONF_DIMS.items():
        dims = await dim_source(db, f, window, dimension)
        served = served or dims
        bins = [0] * 10
        unscored = 0
        for key, n in await _dim_counts(db, dims):
            if key == UNKNOWN_KEY:
                unscored += n
            else:
                bins[int(key)] += n
        levels.append(Histogram(level=level, bins=bins, unscored=unscored))
    assert served is not None
    return Confidence(meta=await _meta(db, window, cells, served), levels=levels)


class FlowLink(BaseModel):
    source: str
    target: str
    value: int


class SourceFlow(BaseModel):
    meta: Meta
    # Inferred visits in scope. Each visit flows once per source that proposed a
    # candidate for it, so link values sum to more than this.
    visits: int
    sources: list[str]
    levels: list[str]
    links: list[FlowLink]


EMITTED_LEVELS = ["city", "admin2", "admin1", "country", "none"]


@router.get(
    "/source-flow",
    response_model=SourceFlow,
    summary="Inference sources to the level emitted (F9.AC7)",
    description=(
        "One link per (source, emitted level): how many inferred visits that source "
        "proposed a candidate for, by the deepest strict level the engine finally emitted. "
        "`none` as a level is a full abstention; `none` as a source is a visit no source "
        "had anything to say about."
    ),
)
async def source_flow(
    principal: CurrentPrincipal, db: DbSession, settings: Config, f: Filter
) -> SourceFlow:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    dims = await dim_source(db, f, window, Dimension.SOURCE_FLOW)
    cells = await cell_source(db, f, window, Grain.DAY)
    links: list[FlowLink] = []
    for key, n in await _dim_counts(db, dims):
        source, _, level = key.partition(">")
        links.append(FlowLink(source=source, target=level, value=n))
    inferred = _int(
        (
            await db.execute(
                select(func.sum(cells.rel.c.inferred_count)).where(_live(cells.rel.c.stage))
            )
        ).scalar_one()
    )
    sources = sorted({link.source for link in links})
    levels = [lv for lv in EMITTED_LEVELS if any(link.target == lv for link in links)]
    return SourceFlow(
        meta=await _meta(db, window, cells, dims),
        visits=inferred,
        sources=sources,
        levels=levels,
        links=links,
    )


# ---------------------------------------------------------------------------
# Funnel (F9.AC8) and accuracy (F9.AC10)
# ---------------------------------------------------------------------------


class FunnelStep(BaseModel):
    step: Literal["requests", "captured", "enriched", "consented", "notified"]
    count: int | None
    reason: str | None = None


class Funnel(BaseModel):
    meta: Meta
    steps: list[FunnelStep]


@router.get(
    "/funnel",
    response_model=Funnel,
    summary="requests → captured → enriched → consented → notified (F9.AC8)",
    description=(
        "`requests` includes rate-limited requests; `captured` excludes them. `notified` "
        "is null with a reason until Telegram notification is built (M6)."
    ),
)
async def funnel(principal: CurrentPrincipal, db: DbSession, settings: Config, f: Filter) -> Funnel:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    cells = await cell_source(db, f, window, Grain.DAY)
    mix = await _stage_mix(db, cells)
    rel = cells.rel
    consented = _int((await db.execute(select(func.sum(rel.c.consented_count)))).scalar_one())
    return Funnel(
        meta=Meta(
            start=window.start,
            end=window.end,
            reporting_tz=window.zone_name,
            computed_from=cells.computed_from,
            refreshed_at=cells.refreshed_at,
            stage_mix=mix,
        ),
        steps=[
            FunnelStep(step="requests", count=mix.total),
            FunnelStep(step="captured", count=mix.total - mix.rate_limited),
            FunnelStep(step="enriched", count=mix.enriched),
            FunnelStep(step="consented", count=consented),
            FunnelStep(step="notified", count=None, reason="notifications_not_built"),
        ],
    )


class LevelAccuracy(BaseModel):
    level: Literal["country", "admin1", "admin2", "city"]
    # Stated beside every figure (RISKS R9): 30 labels and 3000 are different claims.
    label_count: int
    precision: float | None
    coverage: float | None
    # Not accuracy: the share of inferred visits for which strict emitted this level.
    emission_rate: float | None
    reason: str | None


class Accuracy(BaseModel):
    meta: Meta
    inferred: int
    levels: list[LevelAccuracy]


@router.get(
    "/accuracy",
    response_model=Accuracy,
    summary="Precision and coverage per level, with label counts (F9.AC10)",
    description=(
        "Precision and coverage need the ground-truth set (F4.AC15), which M8 builds. "
        "Until then both are null with reason `no_ground_truth_labels` and `label_count` "
        "is 0. `emission_rate` is reported meanwhile and is explicitly not accuracy: it "
        "says how often strict answered, not whether it was right."
    ),
)
async def accuracy(
    principal: CurrentPrincipal, db: DbSession, settings: Config, f: Filter
) -> Accuracy:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    cells = await cell_source(db, f, window, Grain.DAY)
    rel = cells.rel
    live = _live(rel.c.stage)
    row = (
        await db.execute(
            select(
                func.sum(rel.c.inferred_count),
                func.sum(rel.c.visit_count).filter(rel.c.country_code != UNKNOWN_KEY),
                func.sum(rel.c.visit_count).filter(rel.c.admin1 != UNKNOWN_KEY),
                func.sum(rel.c.strict_admin2_count),
                func.sum(rel.c.strict_city_count),
            ).where(live)
        )
    ).one()
    inferred = _int(row[0])
    emitted = dict(zip(("country", "admin1", "admin2", "city"), map(_int, row[1:]), strict=True))
    levels = [
        LevelAccuracy(
            level=level,
            label_count=0,
            precision=None,
            coverage=None,
            emission_rate=emitted[level] / inferred if inferred else None,
            reason="no_ground_truth_labels",
        )
        for level in ("country", "admin1", "admin2", "city")
    ]
    return Accuracy(meta=await _meta(db, window, cells, cells), inferred=inferred, levels=levels)


# ---------------------------------------------------------------------------
# Geography (F9.AC5)
# ---------------------------------------------------------------------------


class CountryCount(BaseModel):
    country_code: str
    count: int


class Admin1Count(BaseModel):
    country_code: str
    admin1: str
    count: int


class PointCluster(BaseModel):
    lat: float
    lng: float
    count: int


class Geo(BaseModel):
    meta: Meta
    countries: list[CountryCount]
    admin1: list[Admin1Count]
    # Visits for which strict abstained at country: not on the map, but counted.
    abstained: int
    cell_degrees: float
    # Always raw rows: coordinates exist for few visits by design (ADR-0005).
    points_computed_from: Literal["raw"]
    points: list[PointCluster]
    points_truncated: bool


MAX_CLUSTERS = 2000


@router.get(
    "/geo",
    response_model=Geo,
    summary="Choropleth counts and clustered points (F9.AC5)",
    description=(
        "Strict location only. Points are consented GPS where it exists, otherwise the "
        "strict coordinates, which exist only with a strict city (DATA_MODEL 5.3 invariant "
        "11). They are clustered on a grid of `cell_degrees`, server-side."
    ),
)
async def geo(
    principal: CurrentPrincipal,
    db: DbSession,
    settings: Config,
    f: Filter,
    cell_degrees: Annotated[float, Query(ge=0.01, le=10)] = 0.25,
) -> Geo:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    cells = await cell_source(db, f, window, Grain.DAY)
    rel = cells.rel
    rows = (
        await db.execute(
            select(rel.c.country_code, rel.c.admin1, func.sum(rel.c.visit_count))
            .where(_live(rel.c.stage))
            .group_by(rel.c.country_code, rel.c.admin1)
        )
    ).all()
    countries: dict[str, int] = defaultdict(int)
    admin1: list[Admin1Count] = []
    abstained = 0
    for country, region, n in rows:
        count = _int(n)
        if country == UNKNOWN_KEY:
            abstained += count
            continue
        countries[str(country)] += count
        if region != UNKNOWN_KEY:
            admin1.append(Admin1Count(country_code=str(country), admin1=str(region), count=count))

    lat = func.coalesce(Visit.gps_lat, Visit.strict_lat)
    lng = func.coalesce(Visit.gps_lng, Visit.strict_lng)
    grid_lat = func.floor(lat / cell_degrees)
    grid_lng = func.floor(lng / cell_degrees)
    clusters = (
        await db.execute(
            select(func.avg(lat), func.avg(lng), func.count())
            .where(
                window.range_clause(),
                Visit.stage != VisitStage.RATE_LIMITED,
                lat.is_not(None),
                *visit_clauses(f),
            )
            .group_by(grid_lat, grid_lng)
            .order_by(func.count().desc())
            .limit(MAX_CLUSTERS + 1)
        )
    ).all()
    return Geo(
        meta=await _meta(db, window, cells, cells),
        countries=[
            CountryCount(country_code=c, count=n)
            for c, n in sorted(countries.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        admin1=sorted(admin1, key=lambda a: (-a.count, a.country_code, a.admin1)),
        abstained=abstained,
        cell_degrees=cell_degrees,
        points_computed_from="raw",
        points=[
            PointCluster(lat=float(r[0]), lng=float(r[1]), count=int(r[2]))
            for r in clusters[:MAX_CLUSTERS]
        ],
        points_truncated=len(clusters) > MAX_CLUSTERS,
    )


# ---------------------------------------------------------------------------
# One visitor over time (F9.AC12)
# ---------------------------------------------------------------------------


class Drift(BaseModel):
    """What changed between two consecutive visits by the same visitor."""

    at: dt.datetime
    from_visit: str
    to_visit: str
    # Levels whose advisory value changed. Advisory, because the question is "did they
    # move?" and strict abstains too often to answer it; labelled as such in the UI.
    location_changed: list[str]
    distance_km: float | None
    device_changed: list[str]
    network_changed: bool


class VisitorView(BaseModel):
    visitor_id: str
    visit_count: int
    first_seen: dt.datetime | None
    last_seen: dt.datetime | None
    truncated: bool
    visits: list[VisitSummary]
    drift: list[Drift]


MAX_VISITOR_VISITS = 500


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance (haversine), in kilometres."""
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def _point(v: Visit) -> tuple[float, float] | None:
    for lat, lng in ((v.gps_lat, v.gps_lng), (v.advisory_lat, v.advisory_lng)):
        if lat is not None and lng is not None:
            return float(lat), float(lng)
    return None


def _drift(a: Visit, b: Visit) -> Drift:
    location = [
        level
        for level, x, y in (
            ("country", a.advisory_country_code, b.advisory_country_code),
            ("admin1", a.advisory_admin1, b.advisory_admin1),
            ("city", a.advisory_city, b.advisory_city),
        )
        if x != y
    ]
    pa, pb = _point(a), _point(b)
    device = [
        name
        for name, x, y in (
            ("device_class", a.device_class, b.device_class),
            ("os", a.os_family, b.os_family),
            ("browser", a.ua_family, b.ua_family),
            ("screen", (a.screen_w, a.screen_h), (b.screen_w, b.screen_h)),
            ("gpu", a.gpu_renderer, b.gpu_renderer),
        )
        if x != y
    ]
    return Drift(
        at=b.occurred_at,
        from_visit=str(a.id),
        to_visit=str(b.id),
        location_changed=location,
        distance_km=round(_km(pa, pb), 1) if pa and pb else None,
        device_changed=device,
        network_changed=a.asn != b.asn,
    )


@router.get(
    "/visitor/{visitor_id}",
    response_model=VisitorView,
    summary="Every visit by one visitor, with location drift and device changes (F9.AC12)",
    description=(
        f"Oldest first, at most {MAX_VISITOR_VISITS}. Every classification is included: "
        "a visitor who is sometimes classified as a bot is exactly what this view is for."
    ),
)
async def visitor(
    principal: CurrentPrincipal,
    db: DbSession,
    visitor_id: Annotated[str, Path(min_length=64, max_length=64, pattern="^[0-9a-fA-F]+$")],
) -> VisitorView:
    del principal
    vid = bytes.fromhex(visitor_id)
    rows = (
        (
            await db.execute(
                select(Visit, Link, is_returning_column())
                .join(Link, Link.id == Visit.link_id)
                .where(Visit.visitor_id == vid)
                .order_by(Visit.occurred_at, Visit.id)
                .limit(MAX_VISITOR_VISITS + 1)
            )
        )
        .tuples()
        .all()
    )
    page = rows[:MAX_VISITOR_VISITS]
    total = (await db.execute(select(func.count()).where(Visit.visitor_id == vid))).scalar_one()
    visits = [v for v, _, _ in page]
    return VisitorView(
        visitor_id=visitor_id.lower(),
        visit_count=int(total),
        first_seen=visits[0].occurred_at if visits else None,
        last_seen=visits[-1].occurred_at if visits else None,
        truncated=len(rows) > MAX_VISITOR_VISITS,
        visits=[summarize(v, link, is_returning=returning) for v, link, returning in page],
        drift=[_drift(a, b) for a, b in itertools.pairwise(visits)],
    )
