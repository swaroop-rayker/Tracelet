"""The visit filter (F9.AC13), shared by the visit list, the export and analytics.

One definition, three consumers. If the list, the export and the charts each parsed
their own filters, "visits from Karnataka" would sooner or later mean three slightly
different things, and a chart would disagree with the table under it.

**Location filters match the best-guess (advisory) fields**, as the breakdowns count them
(ADR-0018, SPEC section 11 row 14): clicking a state in a chart selects exactly the visits
it counted. Strict fields still decide geofences and alerts, never a filter.

**Source filters** (M7.6, F9.AC21) match the same expressions the Sources dimensions are
built from (``projection.referrer_host``, ``projection.utm_value``), so a clicked row and
the filter it applies cannot disagree.

A filter renders two ways: as clauses over raw ``visits`` rows, and -- when every
field it sets is a rollup dimension -- as clauses over the rollup tables (ADR-0016).
``rollup_fields`` is what decides between them, so adding a filter field without
deciding its rollup story is impossible to do silently: it is raw-only by default.
"""

from __future__ import annotations

import binascii
import dataclasses
import datetime as dt
import uuid
import zoneinfo
from typing import Annotated

from fastapi import Query
from sqlalchemy import ColumnElement, and_, any_, or_, select

from tracelet.analytics import projection
from tracelet.capture.models import (
    Classification,
    ConnectionClass,
    ConsentState,
    DeviceClass,
    Link,
    Visit,
    VisitStage,
)
from tracelet.classify.identity import DIGEST_BYTES
from tracelet.errors import ValidationFailed

# Excluded unless include_automated is set, so the default view is people.
AUTOMATED: frozenset[Classification] = frozenset(
    {
        Classification.CRAWLER,
        Classification.BOT,
        Classification.SPAM,
        Classification.SPOOFED,
        Classification.DATACENTER,
    }
)

# The default analytics window when the caller gives none: the last 30 local days,
# today included.
DEFAULT_DAYS = 30
# A visitor_id is an HMAC-SHA256 truncated to 128 bits (ADR-0006, identity.DIGEST_BYTES).
VISITOR_ID_BYTES = DIGEST_BYTES


@dataclasses.dataclass(frozen=True, slots=True)
class VisitFilter:
    """Every F9.AC13 filter. ``None`` or empty means "not filtering on this"."""

    from_: dt.datetime | None = None
    to: dt.datetime | None = None
    link_id: uuid.UUID | None = None
    stage: tuple[VisitStage, ...] = ()
    classification: tuple[Classification, ...] = ()
    include_automated: bool = False
    country_code: str | None = None
    admin1: str | None = None
    city: str | None = None
    asn: int | None = None
    device_class: tuple[DeviceClass, ...] = ()
    connection_class: tuple[ConnectionClass, ...] = ()
    consent_state: ConsentState | None = None
    geofence_id: uuid.UUID | None = None
    visitor_id: bytes | None = None
    min_confidence_admin1: float | None = None
    min_confidence_city: float | None = None
    has_gps: bool | None = None
    is_proxy_suspected: bool | None = None
    webview_host: str | None = None
    search: str | None = None
    # M7.6, F9.AC21. Raw-only: no rollup table is keyed on them.
    referrer_host: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None

    # ------------------------------------------------------------------
    # Which fields are set, and can the rollups answer them?
    # ------------------------------------------------------------------

    def set_fields(self) -> frozenset[str]:
        """Names of the fields that actually filter (time range excluded)."""
        names: set[str] = set()
        for field in dataclasses.fields(self):
            if field.name in {"from_", "to", "include_automated"}:
                continue
            value = getattr(self, field.name)
            if value is None or value == ():
                continue
            names.add(field.name)
        return frozenset(names)

    def classifications(self) -> tuple[Classification, ...] | None:
        """The classification set to keep, or ``None`` for "all of them"."""
        if self.classification:
            return self.classification
        if self.include_automated:
            return None
        return tuple(c for c in Classification if c not in AUTOMATED)

    def without_classification(self) -> VisitFilter:
        """The same filter over every classification -- for the human and bot shares."""
        return dataclasses.replace(self, classification=(), include_automated=True)


# Fields each rollup table can filter on (ADR-0016). Everything else is raw-only.
CELL_FIELDS: frozenset[str] = frozenset(
    {
        "link_id",
        "stage",
        "classification",
        "device_class",
        "connection_class",
        "country_code",
        "admin1",
    }
)
DIM_FIELDS: frozenset[str] = frozenset({"link_id", "classification"})


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def visit_clauses(f: VisitFilter) -> list[ColumnElement[bool]]:
    """``f`` as clauses over raw ``visits`` rows. The time range is not included."""
    clauses: list[ColumnElement[bool]] = []
    if f.link_id is not None:
        clauses.append(Visit.link_id == f.link_id)
    if f.stage:
        clauses.append(Visit.stage.in_(f.stage))
    kept = f.classifications()
    if kept is not None:
        clauses.append(Visit.classification.in_(kept))
    # Location filters match the best-guess location, as the breakdowns count it
    # (ADR-0018): clicking a city in a chart selects exactly the visits it counted.
    if f.country_code is not None:
        clauses.append(Visit.advisory_country_code == f.country_code.upper())
    if f.admin1 is not None:
        clauses.append(Visit.advisory_admin1 == f.admin1)
    if f.city is not None:
        clauses.append(Visit.advisory_city == f.city)
    if f.asn is not None:
        clauses.append(Visit.asn == f.asn)
    if f.device_class:
        clauses.append(Visit.device_class.in_(f.device_class))
    if f.connection_class:
        clauses.append(Visit.connection_class.in_(f.connection_class))
    if f.consent_state is not None:
        clauses.append(Visit.consent_state == f.consent_state)
    if f.geofence_id is not None:
        clauses.append(any_(Visit.matched_geofence_ids) == f.geofence_id)
    if f.visitor_id is not None:
        clauses.append(Visit.visitor_id == f.visitor_id)
    if f.min_confidence_admin1 is not None:
        clauses.append(Visit.confidence_admin1 >= f.min_confidence_admin1)
    if f.min_confidence_city is not None:
        clauses.append(Visit.confidence_city >= f.min_confidence_city)
    if f.has_gps is not None:
        clauses.append(Visit.gps_lat.is_not(None) if f.has_gps else Visit.gps_lat.is_(None))
    if f.is_proxy_suspected is not None:
        # NULL means "not assessed" (F3.AC5): it matches neither true nor false.
        clauses.append(Visit.is_proxy_suspected.is_(f.is_proxy_suspected))
    if f.webview_host is not None:
        clauses.append(Visit.webview_host == f.webview_host)
    if f.referrer_host is not None:
        clauses.append(projection.referrer_host() == f.referrer_host.lower())
    for key, value in (
        ("utm_source", f.utm_source),
        ("utm_medium", f.utm_medium),
        ("utm_campaign", f.utm_campaign),
    ):
        if value is not None:
            clauses.append(projection.utm_value(key) == value)
    if f.search:
        pattern = _like(f.search)
        links = select(Link.id).where(or_(Link.slug.ilike(pattern), Link.label.ilike(pattern)))
        clauses.append(
            or_(
                Visit.link_id.in_(links),
                Visit.asn_org.ilike(pattern),
                Visit.strict_city.ilike(pattern),
                Visit.advisory_city.ilike(pattern),
                Visit.ua_family.ilike(pattern),
                Visit.os_family.ilike(pattern),
                Visit.webview_host.ilike(pattern),
            )
        )
    return clauses


# ---------------------------------------------------------------------------
# Time range
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Window:
    """A half-open ``[start, end)`` interval, and the zone its buckets are cut in."""

    start: dt.datetime
    end: dt.datetime
    tz: zoneinfo.ZoneInfo

    @property
    def zone_name(self) -> str:
        return self.tz.key

    def _local(self, moment: dt.datetime) -> dt.datetime:
        return moment.astimezone(self.tz)

    def aligned_to_days(self) -> bool:
        return all(self._local(m).time() == dt.time(0) for m in (self.start, self.end))

    def aligned_to_hours(self) -> bool:
        return all(
            self._local(m).replace(minute=0, second=0, microsecond=0) == self._local(m)
            for m in (self.start, self.end)
        )

    def days(self) -> list[dt.date]:
        """Every local day the window touches, oldest first."""
        first = self._local(self.start).date()
        last = self._local(self.end - dt.timedelta(microseconds=1)).date()
        return [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]

    def previous(self) -> Window:
        """The window of equal length immediately before this one."""
        length = self.end - self.start
        return Window(start=self.start - length, end=self.start, tz=self.tz)

    def range_clause(self) -> ColumnElement[bool]:
        return and_(Visit.occurred_at >= self.start, Visit.occurred_at < self.end)


def local_midnight(day: dt.date, tz: zoneinfo.ZoneInfo) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(0), tzinfo=tz)


def resolve_window(f: VisitFilter, zone: str, *, now: dt.datetime | None = None) -> Window:
    """The window ``f`` asks for, defaulting to the last 30 local days including today."""
    tz = zoneinfo.ZoneInfo(zone)
    current = (now or dt.datetime.now(dt.UTC)).astimezone(tz)
    end = f.to or local_midnight(current.date() + dt.timedelta(days=1), tz)
    start = f.from_ or local_midnight(
        end.astimezone(tz).date() - dt.timedelta(days=DEFAULT_DAYS), tz
    )
    if start.tzinfo is None or end.tzinfo is None:
        msg = "from and to must carry a timezone offset."
        raise ValidationFailed(msg)
    if start >= end:
        msg = "from must be earlier than to."
        raise ValidationFailed(msg)
    if end - start > dt.timedelta(days=800):
        # A little over two years: enough for a calendar heatmap with a full year of
        # comparison, and a bound on what one request can ask the raw path to scan.
        msg = "The time range is limited to 800 days."
        raise ValidationFailed(msg)
    return Window(start=start, end=end, tz=tz)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def _visitor_id(raw: str | None) -> bytes | None:
    if raw is None:
        return None
    try:
        value = bytes.fromhex(raw)
    except (ValueError, binascii.Error) as exc:
        msg = "visitor_id must be the hexadecimal identifier shown on a visit."
        raise ValidationFailed(msg) from exc
    if len(value) != VISITOR_ID_BYTES:
        msg = "visitor_id must be the hexadecimal identifier shown on a visit."
        raise ValidationFailed(msg)
    return value


def visit_filter(
    from_: Annotated[dt.datetime | None, Query(alias="from")] = None,
    to: Annotated[dt.datetime | None, Query()] = None,
    link_id: Annotated[uuid.UUID | None, Query()] = None,
    stage: Annotated[list[VisitStage] | None, Query()] = None,
    classification: Annotated[list[Classification] | None, Query()] = None,
    include_automated: Annotated[bool, Query()] = False,
    country_code: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    admin1: Annotated[str | None, Query(max_length=120)] = None,
    city: Annotated[str | None, Query(max_length=120)] = None,
    asn: Annotated[int | None, Query(ge=0, le=4_294_967_295)] = None,
    device_class: Annotated[list[DeviceClass] | None, Query()] = None,
    connection_class: Annotated[list[ConnectionClass] | None, Query()] = None,
    consent_state: Annotated[ConsentState | None, Query()] = None,
    geofence_id: Annotated[uuid.UUID | None, Query()] = None,
    visitor_id: Annotated[str | None, Query(max_length=2 * VISITOR_ID_BYTES)] = None,
    min_confidence_admin1: Annotated[float | None, Query(ge=0, le=1)] = None,
    min_confidence_city: Annotated[float | None, Query(ge=0, le=1)] = None,
    has_gps: Annotated[bool | None, Query()] = None,
    is_proxy_suspected: Annotated[bool | None, Query()] = None,
    webview_host: Annotated[str | None, Query(max_length=32)] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    referrer_host: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    utm_source: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    utm_medium: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    utm_campaign: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
) -> VisitFilter:
    """Parse the F9.AC13 query parameters. Every one is optional and they compose."""
    return VisitFilter(
        from_=from_,
        to=to,
        link_id=link_id,
        stage=tuple(stage or ()),
        classification=tuple(classification or ()),
        include_automated=include_automated,
        country_code=country_code.upper() if country_code else None,
        admin1=admin1,
        city=city,
        asn=asn,
        device_class=tuple(device_class or ()),
        connection_class=tuple(connection_class or ()),
        consent_state=consent_state,
        geofence_id=geofence_id,
        visitor_id=_visitor_id(visitor_id),
        min_confidence_admin1=min_confidence_admin1,
        min_confidence_city=min_confidence_city,
        has_gps=has_gps,
        is_proxy_suspected=is_proxy_suspected,
        webview_host=webview_host,
        search=search.strip() or None if search else None,
        referrer_host=referrer_host,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
    )
