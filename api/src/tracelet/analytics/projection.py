"""Every analytics measure and dimension, defined once as SQL over ``visits`` (ADR-0016).

Two consumers read these definitions:

* the **refresh job** groups them into the rollup tables, and
* the **raw fallback** runs them over the visits a filter selects.

The trick that keeps the two from drifting is that a raw visit is projected as **a
cell of size one**: the same column names as the rollup tables, with ``visit_count =
1`` and each conditional count as ``0`` or ``1``. Code that reads a cell source --
``sum(src.c.visit_count)``, ``sum(src.c.consented_count)`` -- cannot tell which kind
it was handed, and an integration test asserts the two give identical answers.

**Strict location only** in the keys (``country_code``, ``admin1``, the location
dimensions). An analytics count is an assertion about where visits came from, so it is
held to CLAUDE.md invariant 5. Abstentions are counted under ``''``, which the API
reports as "abstained", never silently dropped.
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import (
    Date,
    Integer,
    Select,
    SQLColumnExpression,
    Text,
    and_,
    case,
    cast,
    column,
    distinct,
    func,
    literal,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import ColumnElement

from tracelet.capture.models import ConsentState, GeofenceState, Visit, VisitStage
from tracelet.inference.models import VisitCandidate

# SQLAlchemy's expression types are invariant in their Python type, so a list mixing a
# Label[int] and a Label[str] has no precise element type. Explicit Any is allowed at
# library boundaries (pyproject: disallow_any_explicit = false); this is one.
Expr = SQLColumnExpression[Any]

__all__ = [
    "BREAKDOWNS",
    "CONFIDENCE_LEVELS",
    "Dimension",
    "cell_select",
    "dim_select",
]


class Grain(enum.StrEnum):
    DAY = "day"
    HOUR = "hour"


def local_time(zone: str) -> ColumnElement[Any]:
    """``occurred_at`` as wall-clock time in the reporting zone (no tzinfo)."""
    return func.timezone(literal(zone, Text), Visit.occurred_at)


def bucket(zone: str, grain: Grain) -> ColumnElement[Any]:
    if grain is Grain.DAY:
        return cast(local_time(zone), Date())
    return func.date_trunc("hour", local_time(zone))


def _flag(condition: Expr) -> ColumnElement[int]:
    return case((condition, 1), else_=0)


def _empty(value: Expr) -> ColumnElement[str]:
    return func.coalesce(value, "")


CONFIDENCE_LEVELS: tuple[str, ...] = ("country", "admin1", "admin2", "city")

_CONFIDENCE: dict[str, Expr] = {
    "country": Visit.confidence_country,
    "admin1": Visit.confidence_admin1,
    "admin2": Visit.confidence_admin2,
    "city": Visit.confidence_city,
}


def cell_select(zone: str, grain: Grain) -> Select[Any]:
    """One cell of size one per visit, columns named exactly as the cell tables.

    The caller adds its own ``where`` (a filter and a window) and either groups it
    into a rollup or reads it as a cell source directly.
    """
    columns: list[Expr] = [
        bucket(zone, grain).label("bucket"),
        Visit.link_id.label("link_id"),
        Visit.stage.label("stage"),
        Visit.classification.label("classification"),
        Visit.device_class.label("device_class"),
        Visit.connection_class.label("connection_class"),
        _empty(Visit.strict_country_code).label("country_code"),
        _empty(Visit.strict_admin1).label("admin1"),
        literal(1, Integer).label("visit_count"),
        _flag(Visit.consent_state == ConsentState.GRANTED).label("consented_count"),
        _flag(Visit.geofence_state == GeofenceState.INSIDE).label("geofence_inside_count"),
        _flag(Visit.geofence_state == GeofenceState.OUTSIDE).label("geofence_outside_count"),
        _flag(Visit.gps_lat.is_not(None)).label("has_gps_count"),
        _flag(or_(Visit.gps_lat.is_not(None), Visit.strict_lat.is_not(None))).label(
            "has_point_count"
        ),
        _flag(Visit.inferred_at.is_not(None)).label("inferred_count"),
        _flag(Visit.strict_admin2.is_not(None)).label("strict_admin2_count"),
        _flag(Visit.strict_city.is_not(None)).label("strict_city_count"),
    ]
    for level in CONFIDENCE_LEVELS:
        value = _CONFIDENCE[level]
        columns.append(func.coalesce(value, 0).label(f"conf_{level}_sum"))
        columns.append(_flag(value.is_not(None)).label(f"conf_{level}_n"))
    return select(*columns).select_from(Visit)


CELL_KEYS: tuple[str, ...] = (
    "bucket",
    "link_id",
    "stage",
    "classification",
    "device_class",
    "connection_class",
    "country_code",
    "admin1",
)

CELL_MEASURES: tuple[str, ...] = (
    "visit_count",
    "consented_count",
    "geofence_inside_count",
    "geofence_outside_count",
    "has_gps_count",
    "has_point_count",
    "inferred_count",
    "strict_admin2_count",
    "strict_city_count",
    *(f"conf_{level}_{part}" for level in CONFIDENCE_LEVELS for part in ("sum", "n")),
)


# ---------------------------------------------------------------------------
# Dimensions (the long-format rollup)
# ---------------------------------------------------------------------------


class Dimension(enum.StrEnum):
    """Every dimension the long-format rollup holds.

    The first twelve are F9.AC4's breakdowns. ``admin1`` and ``city`` values are
    qualified (``IN|Karnataka``, ``IN|Karnataka|Bengaluru``) because names repeat
    across countries -- Punjab is a state of India and a province of Pakistan.
    """

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
    # Not breakdowns: the confidence deciles (F9.AC9), fired rules (F9.AC11) and the
    # source-to-level flow (F9.AC7).
    CONF_COUNTRY = "conf_country"
    CONF_ADMIN1 = "conf_admin1"
    CONF_ADMIN2 = "conf_admin2"
    CONF_CITY = "conf_city"
    SIGNAL = "signal"
    SOURCE_FLOW = "source_flow"


BREAKDOWNS: tuple[Dimension, ...] = (
    Dimension.COUNTRY,
    Dimension.ADMIN1,
    Dimension.CITY,
    Dimension.ASN,
    Dimension.ISP,
    Dimension.DEVICE_CLASS,
    Dimension.BROWSER,
    Dimension.APP_MEDIUM,
    Dimension.OS,
    Dimension.SCREEN,
    Dimension.CONNECTION_CLASS,
    Dimension.CLASSIFICATION,
)

# Fired-rule categories counted by the signal-frequency chart. Absences
# (``category: "absence"``) and inference bookkeeping are reasons, not detections.
SIGNAL_CATEGORIES: tuple[str, ...] = ("bot", "spoof", "spam", "network")


def _qualified(*parts: Expr) -> ColumnElement[str]:
    """``a|b|c`` when the last part is known, else ``''``."""
    return case(
        (parts[-1].is_(None), ""),
        else_=func.concat_ws("|", *(func.coalesce(p, "") for p in parts)),
    )


def _decile(value: Expr) -> ColumnElement[str]:
    return case(
        (value.is_(None), ""),
        else_=cast(func.least(cast(func.floor(value * 10), Integer), 9), Text()),
    )


def emitted_level() -> ColumnElement[str]:
    """The deepest strict level the engine emitted, or ``none``."""
    return case(
        (Visit.strict_city.is_not(None), "city"),
        (Visit.strict_admin2.is_not(None), "admin2"),
        (Visit.strict_admin1.is_not(None), "admin1"),
        (Visit.strict_country_code.is_not(None), "country"),
        else_="none",
    )


def _single(dimension: Dimension) -> ColumnElement[str]:
    match dimension:
        case Dimension.COUNTRY:
            return _empty(Visit.strict_country_code)
        case Dimension.ADMIN1:
            return _qualified(Visit.strict_country_code, Visit.strict_admin1)
        case Dimension.CITY:
            return _qualified(Visit.strict_country_code, Visit.strict_admin1, Visit.strict_city)
        case Dimension.ASN:
            return _empty(cast(Visit.asn, Text()))
        case Dimension.ISP:
            return _empty(Visit.asn_org)
        case Dimension.DEVICE_CLASS:
            return cast(Visit.device_class, Text())
        case Dimension.BROWSER:
            return _empty(Visit.ua_family)
        case Dimension.APP_MEDIUM:
            # In-app webviews by host app; everything else is a browser.
            return case(
                (Visit.is_inapp_webview, func.coalesce(Visit.webview_host, "webview")),
                else_="browser",
            )
        case Dimension.OS:
            return _empty(Visit.os_family)
        case Dimension.SCREEN:
            return case(
                (
                    and_(Visit.screen_w.is_not(None), Visit.screen_h.is_not(None)),
                    func.concat(Visit.screen_w, "x", Visit.screen_h),
                ),
                else_="",
            )
        case Dimension.CONNECTION_CLASS:
            return cast(Visit.connection_class, Text())
        case Dimension.CLASSIFICATION:
            return cast(Visit.classification, Text())
        case Dimension.CONF_COUNTRY:
            return _decile(Visit.confidence_country)
        case Dimension.CONF_ADMIN1:
            return _decile(Visit.confidence_admin1)
        case Dimension.CONF_ADMIN2:
            return _decile(Visit.confidence_admin2)
        case Dimension.CONF_CITY:
            return _decile(Visit.confidence_city)
        case Dimension.SIGNAL | Dimension.SOURCE_FLOW:
            msg = f"{dimension} is multi-valued"
            raise ValueError(msg)


def dim_select(zone: str, dimension: Dimension) -> Select[Any]:
    """One row per visit and value, columns named exactly as ``rollup_visit_dim_daily``.

    Rate-limited visits are excluded: they carry no client columns and no inference by
    design (DATA_MODEL 5.3, invariant 8), so every dimension would read "unknown".
    They are counted where they belong -- the stage mix and the funnel.
    """
    day = bucket(zone, Grain.DAY).label("bucket")
    base = (
        Visit.link_id.label("link_id"),
        Visit.classification.label("classification"),
        literal(dimension.value, Text).label("dimension"),
    )
    live = Visit.stage != VisitStage.RATE_LIMITED

    if dimension is Dimension.SIGNAL:
        fired = func.jsonb_array_elements(Visit.signals).table_valued(column("value", JSONB))
        rule = fired.lateral("fired")
        category = rule.c.value["category"].astext
        value = func.concat(category, "|", rule.c.value["rule_id"].astext)
        # DISTINCT per visit: a rule recorded twice on one visit still fired once.
        return (
            select(day, *base, value.label("value"), literal(1, Integer).label("visit_count"))
            .select_from(Visit)
            .join(rule, true())
            .where(live, category.in_(SIGNAL_CATEGORIES))
            .distinct()
            .add_columns(Visit.id.label("visit_id"))
        )

    if dimension is Dimension.SOURCE_FLOW:
        sources = (
            select(VisitCandidate.visit_id, VisitCandidate.source).distinct().subquery("sources")
        )
        source = func.coalesce(cast(sources.c.source, Text()), "none")
        value = func.concat(source, ">", emitted_level())
        return (
            select(day, *base, value.label("value"), literal(1, Integer).label("visit_count"))
            .select_from(Visit)
            .outerjoin(sources, sources.c.visit_id == Visit.id)
            # Only inferred visits: "not yet inferred" is the queue, not an abstention.
            .where(live, Visit.inferred_at.is_not(None))
        )

    return (
        select(
            day,
            *base,
            _single(dimension).label("value"),
            literal(1, Integer).label("visit_count"),
        )
        .select_from(Visit)
        .where(live)
    )


def distinct_visitors() -> ColumnElement[int]:
    return func.count(distinct(Visit.visitor_id))
