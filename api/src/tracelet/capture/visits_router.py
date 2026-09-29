"""Visits (docs/API.md section 7).

M2 serves what M2 captures. The response shapes are the documented ones in full, so
the TypeScript client and the dashboard are built against the final contract; fields
filled by later milestones -- location (M3), classification scores and identity (M4),
geofence (M6) -- are present and ``null`` until then, never absent and never zero
(F3.AC5).

**No endpoint here returns a plaintext IP except ``/ip``**, which is ``owner``-only,
rate-limited, and writes an audit row before it answers (F12.AC4).
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select

from tracelet.audit import log as audit
from tracelet.auth.dependencies import (
    Config,
    CurrentPrincipal,
    DbSession,
    OwnerPrincipal,
    client_ip,
)
from tracelet.capture.models import Classification, Link, Visit, VisitStage
from tracelet.capture.service import DECRYPT_PER_ADMIN
from tracelet.crypto.envelope import DecryptionError, Envelope, open_str
from tracelet.errors import InternalError, IpPurged, NotFound, RateLimited, ValidationFailed
from tracelet.inference.models import VisitCandidate
from tracelet.net import prefix_of
from tracelet.ratelimit import gcra

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/visits", tags=["visits"])

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

MAX_PAGE = 200


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class LinkRef(BaseModel):
    id: str
    slug: str
    label: str


class LocationBlock(BaseModel):
    strict: dict[str, str | None]
    advisory: dict[str, str | None]
    confidence: dict[str, float | None]
    abstain_reason: dict[str, Any]
    primary_source: str | None
    has_gps: bool


class NetworkBlock(BaseModel):
    asn: int | None
    asn_org: str | None
    asn_type: str
    connection_class: str
    ip_prefix: str | None
    is_datacenter: bool | None
    is_vpn_suspected: bool | None
    is_proxy_suspected: bool | None
    cf_colo: str | None
    cf_country: str | None


class DeviceBlock(BaseModel):
    # `class` is the documented wire name (docs/API.md section 7) and a Python
    # keyword, hence the alias.
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    class_: str = Field(alias="class")
    os: str | None
    browser: str | None
    is_inapp_webview: bool
    webview_host: str | None
    screen: str | None
    cpu_cores: int | None
    device_memory_gb: float | None
    gpu_renderer: str | None


class GeofenceBlock(BaseModel):
    state: str
    matched: list[str]


class VisitSummary(BaseModel):
    id: str
    occurred_at: dt.datetime
    link: LinkRef
    stage: VisitStage
    classification: Classification
    bot_score: int | None
    spoof_score: int | None
    location: LocationBlock
    network: NetworkBlock
    device: DeviceBlock
    geofence: GeofenceBlock
    visitor_id: str | None
    is_returning: bool | None


class VisitPage(BaseModel):
    items: list[VisitSummary]
    next_cursor: str | None


class CandidateOut(BaseModel):
    """One row of the derivation trail (F4.AC11, DATA_MODEL section 5.4)."""

    source: str
    level: str
    country_code: str | None
    admin1: str | None
    admin2: str | None
    city: str | None
    lat: float | None
    lng: float | None
    raw_confidence: float
    weight: float
    effective_weight: float
    accepted: bool
    suppressed_reason: str | None
    evidence: dict[str, Any]
    latency_ms: int


class VisitDetail(VisitSummary):
    finalized_at: dt.datetime | None
    inferred_at: dt.datetime | None
    trace_id: str | None
    classifier_version: str | None
    inference_version: str | None
    consent_state: str
    signals: list[dict[str, Any]]
    candidates: list[CandidateOut]
    client: dict[str, Any]
    request: dict[str, Any]
    referer: str | None
    utm: dict[str, str] | None
    honeypot_tripped: bool


class DecryptedIp(BaseModel):
    ip: str
    decrypted_at: dt.datetime


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def _f(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _join(*parts: str | None) -> str | None:
    present = [part for part in parts if part]
    return " ".join(present) if present else None


def _summary(visit: Visit, link: Link) -> VisitSummary:
    return VisitSummary(
        id=str(visit.id),
        occurred_at=visit.occurred_at,
        link=LinkRef(id=str(link.id), slug=link.slug, label=link.label),
        stage=visit.stage,
        classification=visit.classification,
        bot_score=visit.bot_score,
        spoof_score=visit.spoof_score,
        location=LocationBlock(
            strict={
                "country_code": visit.strict_country_code,
                "admin1": visit.strict_admin1,
                "admin2": visit.strict_admin2,
                "city": visit.strict_city,
            },
            advisory={
                "country_code": visit.advisory_country_code,
                "admin1": visit.advisory_admin1,
                "admin2": visit.advisory_admin2,
                "city": visit.advisory_city,
            },
            confidence={
                "country": _f(visit.confidence_country),
                "admin1": _f(visit.confidence_admin1),
                "admin2": _f(visit.confidence_admin2),
                "city": _f(visit.confidence_city),
            },
            abstain_reason=dict(visit.abstain_reason),
            primary_source=visit.geo_source_primary.value if visit.geo_source_primary else None,
            has_gps=visit.gps_lat is not None,
        ),
        network=NetworkBlock(
            asn=visit.asn,
            asn_org=visit.asn_org,
            asn_type=visit.asn_type.value,
            connection_class=visit.connection_class.value,
            # The prefix is the durable network identity, not an address (RW-3).
            ip_prefix=str(visit.ip_prefix) if visit.ip_prefix is not None else None,
            is_datacenter=visit.is_datacenter,
            is_vpn_suspected=visit.is_vpn_suspected,
            is_proxy_suspected=visit.is_proxy_suspected,
            cf_colo=visit.cf_colo,
            cf_country=visit.cf_country,
        ),
        device=DeviceBlock(
            class_=visit.device_class.value,
            os=_join(visit.os_family, visit.os_version),
            browser=_join(visit.ua_family, visit.ua_version),
            is_inapp_webview=visit.is_inapp_webview,
            webview_host=visit.webview_host,
            screen=f"{visit.screen_w}x{visit.screen_h}"
            if visit.screen_w and visit.screen_h
            else None,
            cpu_cores=visit.cpu_cores,
            device_memory_gb=_f(visit.device_memory_gb),
            gpu_renderer=visit.gpu_renderer,
        ),
        geofence=GeofenceBlock(
            state=visit.geofence_state.value,
            matched=[str(g) for g in visit.matched_geofence_ids],
        ),
        visitor_id=visit.visitor_id.hex() if visit.visitor_id else None,
        # Unknown until M4 computes visitor identity -- null, not false.
        is_returning=None,
    )


def _hex(value: bytes | None) -> str | None:
    return value.hex() if value else None


def _candidate(c: VisitCandidate) -> CandidateOut:
    return CandidateOut(
        source=c.source.value,
        level=c.level.value,
        country_code=c.country_code,
        admin1=c.admin1,
        admin2=c.admin2,
        city=c.city,
        lat=_f(c.lat),
        lng=_f(c.lng),
        raw_confidence=float(c.raw_confidence),
        weight=float(c.weight),
        effective_weight=float(c.effective_weight),
        accepted=c.accepted,
        suppressed_reason=c.suppressed_reason,
        evidence=dict(c.evidence),
        latency_ms=c.latency_ms,
    )


def _detail(visit: Visit, link: Link, candidates: list[VisitCandidate]) -> VisitDetail:
    base = _summary(visit, link).model_dump()
    return VisitDetail(
        **base,
        finalized_at=visit.finalized_at,
        inferred_at=visit.inferred_at,
        trace_id=visit.trace_id,
        classifier_version=visit.classifier_version,
        inference_version=visit.inference_version,
        consent_state=visit.consent_state.value,
        signals=list(visit.signals),
        candidates=[_candidate(c) for c in candidates],
        client={
            "screen_w": visit.screen_w,
            "screen_h": visit.screen_h,
            "viewport_w": visit.viewport_w,
            "viewport_h": visit.viewport_h,
            "dpr": _f(visit.dpr),
            "color_depth": visit.color_depth,
            "touch_points": visit.touch_points,
            "cpu_cores": visit.cpu_cores,
            "device_memory_gb": _f(visit.device_memory_gb),
            "gpu_vendor": visit.gpu_vendor,
            "gpu_renderer": visit.gpu_renderer,
            "languages": visit.languages,
            "tz_iana": visit.tz_iana,
            "tz_offset_min": visit.tz_offset_min,
            "canvas_hash": _hex(visit.canvas_hash),
            "audio_hash": _hex(visit.audio_hash),
            "font_hash": _hex(visit.font_hash),
            "webgl_hash": _hex(visit.webgl_hash),
            "gps": {
                "lat": _f(visit.gps_lat),
                "lng": _f(visit.gps_lng),
                "accuracy_m": _f(visit.gps_accuracy_m),
            }
            if visit.gps_lat is not None
            else None,
        },
        request={
            "user_agent": visit.user_agent,
            "http_version": visit.http_version,
            "tls_version": visit.tls_version,
            # Sanitised at capture: no address-bearing or credential header survives
            # to be stored (capture/signals.py).
            "headers": visit.request_headers,
        },
        referer=visit.referer,
        utm=visit.utm,
        honeypot_tripped=visit.honeypot_tripped,
    )


# ---------------------------------------------------------------------------
# Cursor: keyset over (occurred_at, id), newest first
# ---------------------------------------------------------------------------


def _encode_cursor(visit: Visit) -> str:
    raw = f"{visit.occurred_at.isoformat()}|{visit.id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, _, ident = raw.partition("|")
        return dt.datetime.fromisoformat(stamp), uuid.UUID(ident)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        msg = "The cursor is not valid."
        raise ValidationFailed(msg) from exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=VisitPage,
    summary="List visits, newest first",
    description=(
        "Automated traffic -- crawlers, bots and the rest -- is excluded unless "
        "include_automated is set, so the default view is people. The exclusion is a "
        "flag rather than hidden so that what is being left out stays visible."
    ),
)
async def list_visits(
    principal: CurrentPrincipal,
    db: DbSession,
    from_: Annotated[dt.datetime | None, Query(alias="from")] = None,
    to: Annotated[dt.datetime | None, Query()] = None,
    link_id: Annotated[uuid.UUID | None, Query()] = None,
    stage: Annotated[list[VisitStage] | None, Query()] = None,
    classification: Annotated[list[Classification] | None, Query()] = None,
    webview_host: Annotated[str | None, Query(max_length=32)] = None,
    include_automated: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> VisitPage:
    del principal
    stmt = select(Visit, Link).join(Link, Link.id == Visit.link_id)
    if from_ is not None:
        stmt = stmt.where(Visit.occurred_at >= from_)
    if to is not None:
        stmt = stmt.where(Visit.occurred_at < to)
    if link_id is not None:
        stmt = stmt.where(Visit.link_id == link_id)
    if stage:
        stmt = stmt.where(Visit.stage.in_(stage))
    if classification:
        stmt = stmt.where(Visit.classification.in_(classification))
    elif not include_automated:
        stmt = stmt.where(Visit.classification.not_in(AUTOMATED))
    if webview_host is not None:
        stmt = stmt.where(Visit.webview_host == webview_host)
    if cursor is not None:
        at, ident = _decode_cursor(cursor)
        stmt = stmt.where(
            or_(Visit.occurred_at < at, and_(Visit.occurred_at == at, Visit.id < ident))
        )
    stmt = stmt.order_by(Visit.occurred_at.desc(), Visit.id.desc()).limit(limit + 1)

    rows = (await db.execute(stmt)).tuples().all()
    page = rows[:limit]
    return VisitPage(
        items=[_summary(visit, link) for visit, link in page],
        next_cursor=_encode_cursor(page[-1][0]) if len(rows) > limit and page else None,
    )


async def _load(db: DbSession, visit_id: str) -> tuple[Visit, Link]:
    try:
        parsed = uuid.UUID(visit_id)
    except ValueError as exc:
        raise NotFound("No such visit.") from exc
    row = (
        await db.execute(
            select(Visit, Link).join(Link, Link.id == Visit.link_id).where(Visit.id == parsed)
        )
    ).first()
    if row is None:
        raise NotFound("No such visit.")
    return row[0], row[1]


@router.get("/{visit_id}", response_model=VisitDetail, summary="One visit, in full")
async def get_visit(visit_id: str, principal: CurrentPrincipal, db: DbSession) -> VisitDetail:
    del principal
    visit, link = await _load(db, visit_id)
    candidates = (
        await db.execute(
            select(VisitCandidate)
            .where(VisitCandidate.visit_id == visit.id)
            .order_by(VisitCandidate.id)
        )
    ).scalars()
    return _detail(visit, link, list(candidates))


@router.get(
    "/{visit_id}/ip",
    response_model=DecryptedIp,
    summary="Decrypt the address for one visit (owner only)",
    description=(
        "The only way a plaintext address leaves the system. Owner-only, limited to 10 "
        "an hour per admin, and it writes an audit row naming the actor and the visit "
        "before it answers (F12.AC4). 410 IP_PURGED past the TTL is expected, not a "
        "fault (ADR-0007)."
    ),
)
async def decrypt_ip(
    visit_id: str,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> DecryptedIp:
    visit, _ = await _load(db, visit_id)

    decision = await gcra.check(db, key=str(principal.admin.id), limit=DECRYPT_PER_ADMIN)
    if not decision.allowed:
        raise RateLimited(
            "Too many address decryptions. Try again later.",
            retry_after=decision.retry_after_seconds,
        )

    if visit.ip_enc is None or visit.ip_key_version is None:
        raise IpPurged("This address has passed its retention period and was deleted.")

    try:
        ip = open_str(
            Envelope(key_version=visit.ip_key_version, payload=visit.ip_enc),
            aad=str(visit.id),
            key_path=str(settings.ip_key_file),
        )
    except DecryptionError as exc:
        # Wrong key, a row it was not sealed for, or tampering -- indistinguishable by
        # design. A known failure mode, so a typed error the owner can read rather than
        # an unhandled crash; the specifics go to the log, never the address.
        log.error("ip_decrypt_failed", visit_id=str(visit.id))
        msg = (
            "The stored address could not be decrypted. The encryption key may have "
            "changed, or the record may have been altered."
        )
        raise InternalError(msg) from exc

    # Recorded BEFORE the address is returned, on the same transaction: there is no
    # path that yields a plaintext address without the row that says who asked.
    await audit.record(
        db,
        action=audit.Action.IP_DECRYPTED,
        actor_admin_id=principal.admin.id,
        actor_ip_prefix=prefix_of(client_ip(request, settings)),
        target_type="visit",
        target_id=str(visit.id),
        trace_id=getattr(request.state, "trace_id", None),
    )
    return DecryptedIp(ip=ip, decrypted_at=dt.datetime.now(dt.UTC))
