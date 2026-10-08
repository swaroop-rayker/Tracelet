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
from sqlalchemy import (
    ColumnElement,
    Date,
    Integer,
    Text,
    and_,
    cast,
    distinct,
    func,
    literal,
    or_,
    select,
    tuple_,
    type_coerce,
)
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
from tracelet.classify.identity import DIGEST_BYTES
from tracelet.config import Settings
from tracelet.errors import ValidationFailed
from tracelet.inference.sources import asn_org

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
    reason: str | None


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
    # Sources (M7.6, F9.AC21): ``unknown`` is "None" -- no referrer, or no tag.
    REFERRER_HOST = "referrer_host"
    UTM_SOURCE = "utm_source"
    UTM_MEDIUM = "utm_medium"
    UTM_CAMPAIGN = "utm_campaign"


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
    reason: str | None


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
            FunnelStep(step="requests", count=mix.total, reason=None),
            FunnelStep(step="captured", count=mix.total - mix.rate_limited, reason=None),
            FunnelStep(step="enriched", count=mix.enriched, reason=None),
            FunnelStep(step="consented", count=consented, reason=None),
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

    # A point is consented GPS, else the best-guess city's coordinates (ADR-0018). Only a
    # *city* guess becomes a point: the deepest advisory level may be a state, and a state
    # drawn as a dot would look like a town.
    lat = func.coalesce(Visit.gps_lat, Visit.advisory_lat)
    lng = func.coalesce(Visit.gps_lng, Visit.advisory_lng)
    grid_lat = func.floor(lat / cell_degrees)
    grid_lng = func.floor(lng / cell_degrees)
    clusters = (
        await db.execute(
            select(func.avg(lat), func.avg(lng), func.count())
            .where(
                window.range_clause(),
                Visit.stage != VisitStage.RATE_LIMITED,
                # Spelled as the partial index's predicate (migration 0008), so the
                # planner reads only visits with a point rather than the whole window.
                or_(Visit.gps_lat.is_not(None), Visit.advisory_city.is_not(None)),
                lat.is_not(None),  # a city whose candidates carried no coordinates
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
    visitor_id: Annotated[
        str,
        Path(min_length=2 * DIGEST_BYTES, max_length=2 * DIGEST_BYTES, pattern="^[0-9a-fA-F]+$"),
    ],
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


# ---------------------------------------------------------------------------
# New and returning visitors (F9.AC22, M7.6)
# ---------------------------------------------------------------------------

ReturnBandName = Literal["under_1h", "1h_1d", "1d_7d", "7d_30d", "over_30d"]
# Upper bounds, in seconds; the last band is open.
RETURN_BANDS: tuple[tuple[ReturnBandName, float], ...] = (
    ("under_1h", 3600.0),
    ("1h_1d", 86400.0),
    ("1d_7d", 7 * 86400.0),
    ("7d_30d", 30 * 86400.0),
    ("over_30d", math.inf),
)
MAX_COHORT_WEEKS = 12


class ReturningDay(BaseModel):
    day: dt.date
    # Visitors (per link) whose first visit among those kept is this day...
    new: int
    # ...and those who had come before.
    returning: int


class Cohort(BaseModel):
    # The local Monday of the week of the first visit.
    week: dt.date
    size: int
    # returned[k]: how many came back k weeks after their first week (0 = later the same
    # week); null for a week that has not begun.
    returned: list[int | None]


class ReturnBand(BaseModel):
    band: ReturnBandName
    count: int


class Returning(BaseModel):
    meta: Meta
    # The oldest visit kept: nothing earlier can be known (visit retention).
    since: dt.date | None
    # Visits in scope with no visitor_id: they cannot be followed, and are not guessed.
    unidentified: int
    days: list[ReturningDay]
    cohorts: list[Cohort]
    return_after: list[ReturnBand]


def _local_day(moment: Expr, zone: str) -> ColumnElement[dt.date]:
    return cast(func.timezone(literal(zone, Text), moment), Date())


def _raw_meta(window: Window, mix: StageMix) -> Meta:
    return Meta(
        start=window.start,
        end=window.end,
        reporting_tz=window.zone_name,
        computed_from="raw",
        refreshed_at=dt.datetime.now(dt.UTC),
        stage_mix=mix,
    )


@router.get(
    "/returning",
    response_model=Returning,
    summary="New and returning visitors, weekly cohorts, time to return (F9.AC22)",
    description=(
        "Per link and visitor_id: a visit is new if it is the visitor's first on that link "
        "among the visits kept. Raw rows only, so limited to visit retention (`since`). "
        "Visits with no visitor_id are counted in `unidentified`, never guessed."
    ),
)
async def returning(
    principal: CurrentPrincipal,
    db: DbSession,
    settings: Config,
    f: Filter,
    weeks: Annotated[int, Query(ge=1, le=MAX_COHORT_WEEKS)] = 8,
) -> Returning:
    del principal
    zone = settings.reporting_tz
    window = resolve_window(f, zone)
    cells = await cell_source(db, f, window, Grain.DAY)
    live = Visit.stage != VisitStage.RATE_LIMITED
    followed = [Visit.visitor_id.is_not(None), live, *visit_clauses(f)]
    pair = tuple_(Visit.visitor_id, Visit.link_id)

    # First and second visit of every (visitor, link) among the visits kept.
    order = func.row_number().over(
        partition_by=(Visit.visitor_id, Visit.link_id), order_by=(Visit.occurred_at, Visit.id)
    )
    ranked = (
        select(
            Visit.visitor_id.label("visitor_id"),
            Visit.link_id.label("link_id"),
            Visit.occurred_at.label("at"),
            order.label("n"),
        )
        .where(*followed)
        .subquery("ranked")
    )
    pairs = (
        select(
            ranked.c.visitor_id,
            ranked.c.link_id,
            func.min(ranked.c.at).label("first_at"),
            func.min(ranked.c.at).filter(ranked.c.n == 2).label("second_at"),
        )
        .group_by(ranked.c.visitor_id, ranked.c.link_id)
        .subquery("pairs")
    )
    joined = and_(pairs.c.visitor_id == Visit.visitor_id, pairs.c.link_id == Visit.link_id)

    # New vs returning per local day.
    day = _local_day(Visit.occurred_at, zone)
    first_day = _local_day(pairs.c.first_at, zone)
    per_day = (
        await db.execute(
            select(
                day,
                func.count(distinct(pair)).filter(first_day == day),
                func.count(distinct(pair)).filter(first_day != day),
            )
            .select_from(Visit)
            .join(pairs, joined)
            .where(*followed, window.range_clause())
            .group_by(day)
        )
    ).all()
    counted = {row[0]: (_int(row[1]), _int(row[2])) for row in per_day}
    days = [
        ReturningDay(day=d, new=counted.get(d, (0, 0))[0], returning=counted.get(d, (0, 0))[1])
        for d in window.days()
    ]

    # Weekly cohorts: first visits inside the window, by the local week they fell in.
    week = cast(
        func.date_trunc("week", func.timezone(literal(zone, Text), pairs.c.first_at)), Date()
    )
    in_window = and_(pairs.c.first_at >= window.start, pairs.c.first_at < window.end)
    sizes: dict[dt.date, int] = {
        row[0]: int(row[1])
        for row in await db.execute(
            select(week, func.count()).select_from(pairs).where(in_window).group_by(week)
        )
    }
    # Whole weeks after the first week: (local day - cohort Monday) / 7, integer division.
    after = type_coerce((day - week).self_group(), Integer).op("/", return_type=Integer)(7)
    came_back = (
        await db.execute(
            select(week, after, func.count(distinct(pair)))
            .select_from(pairs)
            .join(Visit, and_(joined, Visit.occurred_at > pairs.c.first_at))
            .where(in_window, *followed)
            .group_by(week, after)
        )
    ).all()
    back: dict[tuple[dt.date, int], int] = {(r[0], int(r[1])): _int(r[2]) for r in came_back}
    last_day = window.end.astimezone(window.tz).date() - dt.timedelta(days=1)
    cohorts = [
        Cohort(
            week=start,
            size=size,
            returned=[
                back.get((start, k), 0) if start + dt.timedelta(weeks=k) <= last_day else None
                for k in range(weeks + 1)
            ],
        )
        for start, size in sorted(sizes.items())[-MAX_COHORT_WEEKS:]
    ]

    # Time from a first visit (inside the window) to the second.
    gaps = (
        await db.execute(
            select(func.extract("epoch", pairs.c.second_at - pairs.c.first_at)).where(
                in_window, pairs.c.second_at.is_not(None)
            )
        )
    ).scalars()
    bands = dict.fromkeys((name for name, _ in RETURN_BANDS), 0)
    for gap in gaps:
        seconds = float(gap)
        bands[next(name for name, upper in RETURN_BANDS if seconds < upper)] += 1

    oldest = (await db.execute(select(func.min(Visit.occurred_at)))).scalar_one_or_none()
    unidentified = (
        await db.execute(
            select(func.count()).where(
                Visit.visitor_id.is_(None), live, window.range_clause(), *visit_clauses(f)
            )
        )
    ).scalar_one()
    return Returning(
        meta=_raw_meta(window, await _stage_mix(db, cells)),
        since=oldest.astimezone(window.tz).date() if oldest is not None else None,
        unidentified=int(unidentified),
        days=days,
        cohorts=cohorts,
        return_after=[ReturnBand(band=name, count=bands[name]) for name, _ in RETURN_BANDS],
    )


# ---------------------------------------------------------------------------
# Mobile networks by state (F9.AC23, M7.6)
# ---------------------------------------------------------------------------

CarrierFamily = Literal["jio", "airtel", "vi", "bsnl", "other"]
FAMILIES: tuple[CarrierFamily, ...] = ("jio", "airtel", "vi", "bsnl", "other")


class CarrierState(BaseModel):
    # The best-guess state, qualified (IN|Karnataka) -- a best guess, never a statement
    # (ADR-0018) -- with the mean confidence of the visits placed there.
    key: str
    visits: int
    confidence: float | None
    families: dict[CarrierFamily, int]
    mobile: int
    broadband: int
    other_network: int


class Carriers(BaseModel):
    meta: Meta
    states: list[CarrierState]
    # Visits no source could place in a state.
    unplaced: int


def _family(asn: str) -> CarrierFamily:
    name = asn_org.classes().families.get(int(asn)) if asn.isdigit() else None
    for family in FAMILIES:
        if family == name:
            return family
    return "other"


@router.get(
    "/carriers",
    response_model=Carriers,
    summary="Carriers and mobile vs broadband per best-guess state (F9.AC23)",
    description=(
        "States are the best guess (ADR-0018), each with its mean confidence. The carrier "
        "comes from the ASN through asn_classes.json; an unlisted ASN is 'other'."
    ),
)
async def carriers(
    principal: CurrentPrincipal,
    db: DbSession,
    settings: Config,
    f: Filter,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> Carriers:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    dims = await dim_source(db, f, window, Dimension.NETWORK_STATE)
    cells = await cell_source(db, f, window, Grain.DAY)
    states: dict[str, CarrierState] = {}
    unplaced = 0
    for value, count in await _dim_counts(db, dims):
        country, admin1, asn, connection = [*value.split("|"), "", "", "", ""][:4]
        if not country or not admin1:
            unplaced += count
            continue
        key = f"{country}|{admin1}"
        state = states.setdefault(
            key,
            CarrierState(
                key=key,
                visits=0,
                confidence=None,
                families=dict.fromkeys(FAMILIES, 0),
                mobile=0,
                broadband=0,
                other_network=0,
            ),
        )
        state.visits += count
        state.families[_family(asn)] += count
        if connection == "mobile":
            state.mobile += count
        elif connection == "broadband":
            state.broadband += count
        else:
            state.other_network += count
    rel = cells.rel
    confidence = (
        await db.execute(
            select(
                rel.c.country_code,
                rel.c.admin1,
                func.sum(rel.c.conf_admin1_sum),
                func.sum(rel.c.conf_admin1_n),
            )
            .where(_live(rel.c.stage))
            .group_by(rel.c.country_code, rel.c.admin1)
        )
    ).all()
    for country, admin1, total, n in confidence:
        placed = states.get(f"{country}|{admin1}")
        if placed is not None and n:
            placed.confidence = round(float(total) / int(n), 3)
    ranked = sorted(states.values(), key=lambda s: (-s.visits, s.key))
    return Carriers(
        meta=await _meta(db, window, cells, dims), states=ranked[:limit], unplaced=unplaced
    )


# ---------------------------------------------------------------------------
# Capture quality by platform (F9.AC24, M7.6)
# ---------------------------------------------------------------------------

MAX_APP_SERIES = 5


class AppCapture(BaseModel):
    # The in-app browser's host app, or "browser".
    key: str
    captured: int
    enriched: int
    server_only: int
    # Still awaiting enrichment or the 90 s sweeper.
    pending: int
    consented: int


class ShareSeries(BaseModel):
    key: str
    # Enriched over captured, per day; null on a day with no visit from this app.
    enriched_share: list[float | None]


class CaptureQuality(BaseModel):
    meta: Meta
    apps: list[AppCapture]
    buckets: list[dt.datetime]
    series: list[ShareSeries]


@router.get(
    "/capture-quality",
    response_model=CaptureQuality,
    summary="Enriched, server-only and consented by app medium (F9.AC24)",
    description=(
        "The funnel per in-app browser: how much each app lets the page see (RISKS R5). "
        f"The share series covers the {MAX_APP_SERIES} busiest apps."
    ),
)
async def capture_quality(
    principal: CurrentPrincipal, db: DbSession, settings: Config, f: Filter
) -> CaptureQuality:
    del principal
    window = resolve_window(f, settings.reporting_tz)
    dims = await dim_source(db, f, window, Dimension.CAPTURE)
    cells = await cell_source(db, f, window, Grain.DAY)
    rel = dims.rel
    rows = (
        await db.execute(
            select(rel.c.bucket, rel.c.value, func.sum(rel.c.visit_count)).group_by(
                rel.c.bucket, rel.c.value
            )
        )
    ).all()
    apps: dict[str, AppCapture] = {}
    starts = _buckets(window, Grain.DAY)
    index = {b.date(): i for i, b in enumerate(starts)}
    daily: dict[str, list[list[int]]] = defaultdict(lambda: [[0, 0] for _ in starts])
    for bucket, value, total in rows:
        app, stage, consented = [*str(value).split("|"), "", "", "0"][:3]
        count = _int(total)
        enriched = count if stage == VisitStage.ENRICHED else 0
        row = apps.setdefault(
            app,
            AppCapture(key=app, captured=0, enriched=0, server_only=0, pending=0, consented=0),
        )
        row.captured += count
        row.enriched += enriched
        row.server_only += count if stage == VisitStage.SERVER_ONLY else 0
        row.pending += count if stage == VisitStage.SERVER else 0
        row.consented += count if consented == "1" else 0
        position = index.get(_bucket_key(bucket))
        if position is not None:
            daily[app][position][0] += enriched
            daily[app][position][1] += count
    ranked = sorted(apps.values(), key=lambda a: (-a.captured, a.key))
    series = [
        ShareSeries(
            key=a.key,
            enriched_share=[round(e / n, 4) if n else None for e, n in daily[a.key]],
        )
        for a in ranked[:MAX_APP_SERIES]
    ]
    return CaptureQuality(
        meta=await _meta(db, window, cells, dims), apps=ranked, buckets=starts, series=series
    )
