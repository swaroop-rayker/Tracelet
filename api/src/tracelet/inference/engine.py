"""Runs the producers, decides, and persists (ADR-0015).

The job claims finalised, uninferred visits; for each it decrypts the address in memory,
runs every enabled source concurrently under its own timeout, lets ``consensus`` decide,
and writes the result -- location columns, network columns, every candidate row -- in one
short, conditional transaction. **It never raises past a visit** (F4.AC18): a failing
source is an outcome in the derivation trail, and a failing engine writes
``country=NULL`` with ``engine_error`` and moves on, so one poisoned row cannot wedge
the queue.
"""

from __future__ import annotations

import asyncio
import dataclasses
import ipaddress
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import func, literal, select, text, update
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.capture.models import Classification, ConsentState, Visit, VisitStage
from tracelet.classify.job import Classified, classify_visit
from tracelet.config import Settings, get_settings
from tracelet.crypto.envelope import DecryptionError, Envelope, open_str
from tracelet.db.engine import session_scope
from tracelet.geofence import store as geofences
from tracelet.geofence.evaluate import StrictPlace
from tracelet.inference import consensus, nominatim, store
from tracelet.inference.config import InferenceConfig, inference_version
from tracelet.inference.models import VisitCandidate
from tracelet.inference.sources import (
    Gps,
    IPAddress,
    SourceInput,
    SourceUnavailable,
    asn_org,
    colo,
    external,
    gps,
    rdns,
    timezone,
)
from tracelet.inference.sources.asn_org import asn_type, connection_class
from tracelet.inference.sources.rdns import Lexicon
from tracelet.inference.types import (
    AsnInfo,
    Candidate,
    GeoLevel,
    InferenceSource,
    SourceOutcome,
)
from tracelet.notify import outbox

log = structlog.get_logger(__name__)

BATCH: Final = 20
# How many visits are inferred at once. Each is mostly waiting on DNS or HTTP, so a
# little concurrency hides latency without adding meaningful memory.
CONCURRENCY: Final = 4

AsnLookup = Callable[[IPAddress], tuple[int | None, str | None]]
DbProducer = Callable[[IPAddress], list[Candidate]]
Placer = Callable[[Candidate], Candidate]


@dataclass(frozen=True, slots=True)
class Toolkit:
    """What the engine can consult. Built once per tick, so a database installed or a
    lexicon edited between ticks is picked up without a restart.

    ``asn_lookup``, ``databases`` and ``place`` stay empty until the offline databases
    are installed (M3 step 2); every source that needs one then reports itself
    unavailable with that reason, rather than silently producing nothing.
    """

    lexicon: Lexicon
    asn_lookup: AsnLookup | None = None
    databases: dict[InferenceSource, DbProducer] = field(default_factory=dict)
    place: Placer | None = None
    # The Tor Project's exit list (M4). None when not installed: is_tor stays unassessed.
    tor_exits: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class VisitFacts:
    id: uuid.UUID
    ip_enc: bytes | None
    ip_key_version: int | None
    consent_state: ConsentState
    gps_lat: Decimal | None
    gps_lng: Decimal | None
    gps_accuracy_m: Decimal | None
    tz_iana: str | None
    cf_colo: str | None


@dataclass(frozen=True, slots=True)
class Inferred:
    visit_id: uuid.UUID
    version: str
    candidates: tuple[Candidate, ...]
    outcomes: tuple[SourceOutcome, ...]
    decision: consensus.Decision | None
    asn: AsnInfo
    ptr_masked: str | None
    error: str | None = None
    # Street level, consented visits only (F4.AC3). The CHECK constraint refuses it on
    # any other visit, so a bug here fails loudly rather than storing it.
    resolved_address: str | None = None
    address_absent: str | None = None
    # M4: classification joins the same write (ADR-0011 amendment, item 3).
    classified: Classified | None = None
    classify_error: str | None = None
    # Whether the Tor exit list was installed when this visit was inferred: without it,
    # is_tor is unassessed (NULL), not "not Tor" (F3.AC5).
    tor_assessed: bool = False


# ---------------------------------------------------------------------------
# One visit
# ---------------------------------------------------------------------------

_DATABASE_SOURCES: Final = (
    InferenceSource.GEOLITE2,
    InferenceSource.IP2LOCATION,
    InferenceSource.IPINFO,
    InferenceSource.DBIP,
)


def _ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _decrypt(facts: VisitFacts, settings: Settings) -> IPAddress | None:
    if facts.ip_enc is None or facts.ip_key_version is None:
        return None
    try:
        return ipaddress.ip_address(
            open_str(
                Envelope(key_version=facts.ip_key_version, payload=facts.ip_enc),
                aad=str(facts.id),
                key_path=str(settings.ip_key_file),
            )
        )
    except (DecryptionError, ValueError, OSError):
        # Never the address, never the ciphertext -- only that it failed.
        log.warning("inference_ip_unavailable", visit_id=str(facts.id))
        return None


async def _timed(
    source: InferenceSource,
    timeout_ms: int,
    work: Callable[[], Awaitable[list[Candidate]]],
) -> tuple[list[Candidate], SourceOutcome]:
    started = time.monotonic()
    try:
        found = await asyncio.wait_for(work(), timeout=timeout_ms / 1000)
    except TimeoutError:
        return [], SourceOutcome(source, "timeout", _ms(started))
    except SourceUnavailable as exc:
        return [], SourceOutcome(source, "unavailable", _ms(started), {"reason": exc.reason})
    except Exception as exc:  # noqa: BLE001 - one source failing must not fail the visit (F4.AC18)
        # Deliberately broad: one source failing must not fail the visit (F4.AC18).
        log.error("inference_source_failed", source=source.value, error_type=type(exc).__name__)
        return [], SourceOutcome(source, "error", _ms(started), {"error": type(exc).__name__})
    latency = _ms(started)
    stamped = [dataclasses.replace(c, latency_ms=latency) for c in found]
    return stamped, SourceOutcome(source, "ok" if stamped else "empty", latency)


def _from_database(
    producer: DbProducer | None, ip: IPAddress | None
) -> Callable[[], Awaitable[list[Candidate]]]:
    async def run() -> list[Candidate]:
        if producer is None:
            raise SourceUnavailable("database_not_installed")
        if ip is None:
            raise SourceUnavailable("no_address")
        return producer(ip)

    return run


async def infer_visit(
    facts: VisitFacts,
    *,
    settings: Settings,
    config: InferenceConfig,
    settings_version: int,
    toolkit: Toolkit,
    profile_of: Callable[[int | None], Awaitable[AsnInfo]],
) -> Inferred:
    version = inference_version(settings_version)
    ip = _decrypt(facts, settings)

    # --- context: the network the visit came from -----------------------------
    asn_number: int | None = None
    org: str | None = None
    if ip is not None and toolkit.asn_lookup is not None:
        try:
            asn_number, org = toolkit.asn_lookup(ip)
        except Exception as exc:  # noqa: BLE001 - F4.AC18: a failure degrades, never raises
            log.error("inference_asn_lookup_failed", error_type=type(exc).__name__)
    asn = await profile_of(asn_number) if asn_number is not None else AsnInfo()
    if asn_number is not None:
        classified = asn_org.classify(asn_number, org)
        asn = _combine(classified, asn)
    if ip is not None and toolkit.tor_exits is not None and str(ip) in toolkit.tor_exits:
        asn = dataclasses.replace(asn, is_tor=True)

    outcomes: list[SourceOutcome] = []
    candidates: list[Candidate] = []

    # --- S6's PTR is resolved first: it is stored whether or not it matches -----
    ptr: str | None = None
    rdns_settings = config.source(InferenceSource.RDNS)
    if not rdns_settings.enabled:
        outcomes.append(SourceOutcome(InferenceSource.RDNS, "disabled", 0))
    elif ip is None:
        outcomes.append(
            SourceOutcome(InferenceSource.RDNS, "unavailable", 0, {"reason": "no_address"})
        )
    else:
        started = time.monotonic()
        try:
            ptr = await rdns.resolve(ip, rdns_settings.timeout_ms / 1000)
            found = [
                dataclasses.replace(c, latency_ms=_ms(started))
                for c in rdns.produce(ptr, toolkit.lexicon)
            ]
            candidates.extend(found)
            detail = {} if ptr else {"reason": "no_ptr_record"}
            outcomes.append(
                SourceOutcome(
                    InferenceSource.RDNS, "ok" if found else "empty", _ms(started), detail
                )
            )
        except SourceUnavailable as exc:
            outcomes.append(
                SourceOutcome(
                    InferenceSource.RDNS, "unavailable", _ms(started), {"reason": exc.reason}
                )
            )

    inp = SourceInput(
        ip=ip,
        gps=_gps(facts),
        tz_iana=facts.tz_iana,
        cf_colo=facts.cf_colo,
        asn=asn,
    )

    # --- every other source, concurrently, each under its own timeout ----------
    work: dict[InferenceSource, Callable[[], Awaitable[list[Candidate]]]] = {
        InferenceSource.GPS: lambda: gps.produce(inp, config),
        InferenceSource.ASN_ORG: lambda: asn_org.produce(inp, toolkit.lexicon),
        InferenceSource.CF_COLO: lambda: colo.produce(inp),
        InferenceSource.TIMEZONE: lambda: timezone.produce(inp),
        # S9 arrives in M3 step 3. (S10 was dropped: SPEC section 11 row 12.)
        InferenceSource.EXTERNAL_API: lambda: external.produce(
            inp, settings, config.source(InferenceSource.EXTERNAL_API).timeout_ms / 1000 * 0.9
        ),
    }
    for source in _DATABASE_SOURCES:
        work[source] = _from_database(toolkit.databases.get(source), ip)

    runnable: list[tuple[InferenceSource, Callable[[], Awaitable[list[Candidate]]]]] = []
    for source, fn in work.items():
        if config.source(source).enabled:
            runnable.append((source, fn))
        else:
            outcomes.append(SourceOutcome(source, "disabled", 0))
    results = await asyncio.gather(
        *(_timed(s, config.source(s).timeout_ms, fn) for s, fn in runnable)
    )
    for found, outcome in results:
        candidates.extend(found)
        outcomes.append(outcome)

    # --- name every place the same way, then decide -----------------------------
    if toolkit.place is not None:
        candidates = [_place(toolkit.place, c) for c in candidates]
    decision = consensus.decide(
        candidates,
        asn=asn,
        tz_countries=timezone.countries_for(facts.tz_iana),
        config=config,
    )
    address, address_absent = await _street_address(facts, inp, settings, config)
    return Inferred(
        visit_id=facts.id,
        version=version,
        candidates=tuple(candidates),
        outcomes=tuple(sorted(outcomes, key=lambda o: list(InferenceSource).index(o.source))),
        decision=decision,
        asn=asn,
        ptr_masked=rdns.mask_ptr(ptr) if ptr else None,
        resolved_address=address,
        address_absent=address_absent,
        tor_assessed=toolkit.tor_exits is not None,
    )


async def _street_address(
    facts: VisitFacts, inp: SourceInput, settings: Settings, config: InferenceConfig
) -> tuple[str | None, str | None]:
    """(address, reason it is absent). Only a visit with consented GPS is ever asked
    about (F4.AC3); every other visit has no address and needs no reason."""
    gps_point = inp.gps
    if facts.consent_state is not ConsentState.GRANTED or gps_point is None:
        return None, None
    if not config.street_address_enabled:
        return None, "disabled"
    try:
        address = await nominatim.street_address(settings, gps_point.lat, gps_point.lng)
    except SourceUnavailable as exc:
        return None, exc.reason
    return address, None if address else "no_address_known"


def _combine(classified: AsnInfo, profiled: AsnInfo) -> AsnInfo:
    """The curated classification, plus whatever ``asn_profiles`` adds."""
    return AsnInfo(
        asn=classified.asn,
        org=classified.org or profiled.org,
        is_mobile=classified.is_mobile or profiled.is_mobile,
        is_hosting=classified.is_hosting or profiled.is_hosting,
        is_cgnat=classified.is_cgnat or profiled.is_cgnat,
        is_tor=classified.is_tor or profiled.is_tor,
        modal_city=profiled.modal_city,
        modal_admin1=profiled.modal_admin1,
        modal_share=profiled.modal_share,
    )


def _place(placer: Placer, c: Candidate) -> Candidate:
    try:
        return placer(c)
    except Exception as exc:  # noqa: BLE001 - F4.AC18: a failure degrades, never raises
        log.error("inference_place_failed", source=c.source.value, error_type=type(exc).__name__)
        return c


def _gps(facts: VisitFacts) -> Gps | None:
    # The CHECK constraint already guarantees coordinates only exist with consent; this
    # is the same rule stated where it is used.
    if facts.consent_state is not ConsentState.GRANTED:
        return None
    if facts.gps_lat is None or facts.gps_lng is None:
        return None
    return Gps(
        lat=float(facts.gps_lat),
        lng=float(facts.gps_lng),
        accuracy_m=float(facts.gps_accuracy_m) if facts.gps_accuracy_m is not None else None,
    )


def engine_error(facts: VisitFacts, settings_version: int, error: str) -> Inferred:
    """F4.AC18: the visit is still written, with the country abstained and the reason."""
    return Inferred(
        visit_id=facts.id,
        version=inference_version(settings_version),
        candidates=(),
        outcomes=(),
        decision=None,
        asn=AsnInfo(),
        ptr_masked=None,
        error=error,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _dec(value: float | None, places: str) -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(Decimal(places))


def _absences(outcomes: Sequence[SourceOutcome]) -> list[dict[str, Any]]:
    """F3.AC5: a source that did not answer says why, in ``signals``."""
    return [
        {
            "rule_id": "inference.source_absent",
            "category": "absence",
            "weight": 0,
            "detail": {
                "source": o.source.value,
                "status": o.status,
                "latency_ms": o.latency_ms,
                **o.detail,
            },
        }
        for o in outcomes
        if o.status not in ("ok",)
    ]


def _visit_values(result: Inferred) -> dict[str, Any]:
    values: dict[str, Any] = {
        "inference_version": result.version,
        "inferred_at": func.now(),
    }
    if result.decision is None:
        values |= {
            "abstain_reason": dict.fromkeys(
                ("country", "admin1", "admin2", "city"), result.error or "engine_error"
            ),
        }
        return values

    d = result.decision
    levels = d.levels
    values |= {
        "strict_country_code": levels[GeoLevel.COUNTRY].strict,
        "strict_admin1": levels[GeoLevel.ADMIN1].strict,
        "strict_admin2": levels[GeoLevel.ADMIN2].strict,
        "strict_city": levels[GeoLevel.CITY].strict,
        "advisory_country_code": levels[GeoLevel.COUNTRY].advisory,
        "advisory_admin1": levels[GeoLevel.ADMIN1].advisory,
        "advisory_admin2": levels[GeoLevel.ADMIN2].advisory,
        "advisory_city": levels[GeoLevel.CITY].advisory,
        "confidence_country": _dec(levels[GeoLevel.COUNTRY].confidence, "0.001"),
        "confidence_admin1": _dec(levels[GeoLevel.ADMIN1].confidence, "0.001"),
        "confidence_admin2": _dec(levels[GeoLevel.ADMIN2].confidence, "0.001"),
        "confidence_city": _dec(levels[GeoLevel.CITY].confidence, "0.001"),
        "strict_lat": _dec(d.strict_point[0], "0.000001") if d.strict_point else None,
        "strict_lng": _dec(d.strict_point[1], "0.000001") if d.strict_point else None,
        "advisory_lat": _dec(d.advisory_point[0], "0.000001") if d.advisory_point else None,
        "advisory_lng": _dec(d.advisory_point[1], "0.000001") if d.advisory_point else None,
        "abstain_reason": d.abstain_reason,
        "agreement_score": _dec(d.agreement_score, "0.001"),
        "conflict_score": _dec(d.conflict_score, "0.001"),
        "geo_source_primary": d.primary_source,
        "asn": result.asn.asn,
        "asn_org": result.asn.org,
        "asn_type": asn_type(result.asn),
        "connection_class": connection_class(result.asn),
        "rdns_ptr": result.ptr_masked,
    }
    return values


def _candidate_rows(result: Inferred) -> list[dict[str, Any]]:
    if result.decision is None:
        return []
    rows: list[dict[str, Any]] = []
    for c, v in zip(result.candidates, result.decision.verdicts, strict=True):
        rows.append(
            {
                "visit_id": result.visit_id,
                "source": c.source,
                "level": c.level,
                "country_code": c.country_code.upper() if c.country_code else None,
                "admin1": c.admin1,
                "admin2": c.admin2,
                "city": c.city,
                "lat": _dec(c.lat, "0.000001"),
                "lng": _dec(c.lng, "0.000001"),
                "raw_confidence": _dec(min(max(c.raw_confidence, 0.0), 1.0), "0.001"),
                "weight": _dec(v.weight, "0.0001"),
                "effective_weight": _dec(v.effective_weight, "0.0001"),
                "accepted": v.accepted,
                "suppressed_reason": v.suppressed_reason.value if v.suppressed_reason else None,
                "evidence": c.evidence,
                "latency_ms": c.latency_ms,
            }
        )
    return rows


async def persist(
    db: AsyncSession,
    result: Inferred,
    fences: Sequence[geofences.ActiveGeofence] | None = None,
    config: Settings | None = None,
) -> bool:
    """Write one result. ``False`` if another run already inferred this visit.

    Geofences are evaluated here, in the same savepoint as the location they are
    evaluated against (ADR-0015, F6.AC5), and the visit's alert is queued in that same
    savepoint (F7.AC5): a visit that rolls back emits nothing, and one that commits
    cannot lose its alert. ``fences`` is the tick's active geofences, loaded once;
    ``None`` loads them.
    """
    values = _visit_values(result)
    absences = _absences(result.outcomes)
    if result.error is not None:
        absences.append(
            {
                "rule_id": "inference.engine_error",
                "category": "absence",
                "weight": 0,
                "detail": {"error": result.error},
            }
        )
    if result.address_absent is not None:
        absences.append(
            {
                "rule_id": "inference.street_address_absent",
                "category": "absence",
                "weight": 0,
                "detail": {"reason": result.address_absent},
            }
        )
    if result.resolved_address is not None:
        values["resolved_address"] = result.resolved_address
    values |= _classification_values(result, absences)
    if absences:
        values["signals"] = Visit.signals.op("||")(literal(absences, type_=pg.JSONB))
    row = (
        await db.execute(
            update(Visit)
            .where(Visit.id == result.visit_id, Visit.inferred_at.is_(None))
            .values(**values)
            .returning(Visit.link_id)
        )
    ).first()
    if row is None:
        return False
    link_id: uuid.UUID = row[0]
    rows = _candidate_rows(result)
    if rows:
        await db.execute(pg.insert(VisitCandidate).values(rows))
    point = result.decision.strict_point if result.decision else None
    if point is not None:
        # geography has no ORM mapping until M6 (DATA_MODEL 5.3). Longitude first.
        await db.execute(
            text(
                "UPDATE visits SET geopoint = "
                "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography WHERE id = :id"
            ),
            {"lng": point[1], "lat": point[0], "id": result.visit_id},
        )
    # Strict fields only: a geofence never acts on a guess (ADR-0018, ADR-0020).
    place = StrictPlace(
        country_code=values.get("strict_country_code"),
        admin1=values.get("strict_admin1"),
        has_geopoint=point is not None,
    )
    if fences is None:
        fences = await geofences.load_active(db)
    evaluation = await geofences.evaluate(
        db, fences, visit_id=result.visit_id, link_id=link_id, place=place
    )
    await geofences.record(db, result.visit_id, evaluation)
    config = config or get_settings()
    enqueued = await outbox.enqueue_visit_alert(
        db,
        result.visit_id,
        evaluation,
        reporting_tz=config.reporting_tz,
        base_url=config.public_base_url,
    )
    if enqueued.queued:
        log.info(
            "visit_alert_queued",
            visit_id=str(result.visit_id),
            priority=enqueued.priority.value if enqueued.priority else None,
        )
    return True


# ---------------------------------------------------------------------------
# The job (ADR-0015)
# ---------------------------------------------------------------------------

_toolkit_factory: Callable[[AsyncSession], Awaitable[Toolkit]] | None = None


def set_toolkit_factory(factory: Callable[[AsyncSession], Awaitable[Toolkit]] | None) -> None:
    """How the job builds its toolkit. M3 step 2 installs one that opens the readers."""
    global _toolkit_factory  # noqa: PLW0603 - a process-wide hook, set once at startup
    _toolkit_factory = factory


async def _default_toolkit(db: AsyncSession) -> Toolkit:
    return Toolkit(lexicon=await store.load_lexicon(db))


async def _claim(
    db: AsyncSession, batch: int, only: Sequence[uuid.UUID] | None
) -> list[tuple[VisitFacts, Visit]]:
    """The rows to work on, whole: classification reads far more of the visit than
    inference does, and the session keeps loaded rows usable after it closes."""
    stmt = (
        select(Visit)
        .where(
            Visit.finalized_at.is_not(None),
            Visit.inferred_at.is_(None),
            Visit.stage != VisitStage.RATE_LIMITED,
        )
        .order_by(Visit.finalized_at)
        .limit(batch)
    )
    if only is not None:
        stmt = stmt.where(Visit.id.in_(list(only)))
    rows = list((await db.execute(stmt)).scalars())
    return [
        (
            VisitFacts(
                v.id,
                v.ip_enc,
                v.ip_key_version,
                v.consent_state,
                v.gps_lat,
                v.gps_lng,
                v.gps_accuracy_m,
                v.tz_iana,
                v.cf_colo,
            ),
            v,
        )
        for v in rows
    ]


async def run_once(
    settings: Settings, *, batch: int = BATCH, only: Sequence[uuid.UUID] | None = None
) -> int:
    """One tick: infer up to ``batch`` visits. Returns how many were written.

    ``only`` restricts the tick to named visits -- for tests, and for deliberately
    re-running inference on chosen visits once their ``inferred_at`` is cleared.
    """
    async with session_scope() as db:
        claimed = await _claim(db, batch, only)
        if not claimed:
            return 0
        facts = [f for f, _ in claimed]
        rows = {f.id: row for f, row in claimed}
        active = await store.active_settings(db)
        toolkit = await (_toolkit_factory or _default_toolkit)(db)
        profiles: dict[int, AsnInfo] = {}

    async def profile_of(asn: int | None) -> AsnInfo:
        if asn is None:
            return AsnInfo()
        if asn not in profiles:
            async with session_scope() as db:
                profiles[asn] = store.overlay_profile(
                    AsnInfo(asn=asn), await store.asn_profile(db, asn)
                )
        return profiles[asn]

    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(f: VisitFacts) -> Inferred:
        async with gate:
            try:
                inferred = await infer_visit(
                    f,
                    settings=settings,
                    config=active.config,
                    settings_version=active.version,
                    toolkit=toolkit,
                    profile_of=profile_of,
                )
            except Exception as exc:
                log.error(
                    "inference_engine_failed",
                    visit_id=str(f.id),
                    error_type=type(exc).__name__,
                    exc_info=exc,
                )
                inferred = engine_error(f, active.version, "engine_error")
            return await _classify(inferred, rows[f.id], settings, active)

    results = await asyncio.gather(*(one(f) for f in facts))
    by_id = {f.id: f for f in facts}
    written = 0
    async with session_scope() as db:
        fences = await geofences.load_active(db)
        for result in results:
            # One savepoint per visit: a row the database refuses (a CHECK the engine
            # did not anticipate) must not roll back its neighbours, and must not come
            # back on every tick -- it is written as an engine error instead.
            try:
                async with db.begin_nested():
                    written += int(await persist(db, result, fences, settings))
            except DBAPIError as exc:
                log.error(
                    "inference_write_refused",
                    visit_id=str(result.visit_id),
                    error_type=type(exc.orig).__name__ if exc.orig else type(exc).__name__,
                )
                fallback = engine_error(by_id[result.visit_id], active.version, "write_refused")
                async with db.begin_nested():
                    written += int(await persist(db, fallback, fences, settings))
    if written:
        log.info("visits_inferred", count=written, inference_version=results[0].version)
    return written


async def run_job_once() -> int:
    """The scheduler's entry point: no arguments, like the sweeper's."""
    return await run_once(get_settings())


async def _classify(
    inferred: Inferred, visit: Visit, settings: Settings, active: store.ActiveSettings
) -> Inferred:
    """Classification never fails a visit (F5.AC14): a failure is 'unknown' with a reason."""
    decision = inferred.decision
    country = decision.levels[GeoLevel.COUNTRY].advisory if decision is not None else None
    try:
        classified = await classify_visit(
            visit,
            settings=settings,
            config=active.config.classifier,
            settings_version=active.version,
            asn=inferred.asn,
            country=country,
            point=decision.strict_point if decision is not None else None,
            ptr_masked=inferred.ptr_masked,
        )
    except Exception as exc:  # noqa: BLE001 - F5.AC14: classification degrades, never raises
        log.error("classifier_failed", visit_id=str(visit.id), error_type=type(exc).__name__)
        return dataclasses.replace(inferred, classify_error=type(exc).__name__)
    return dataclasses.replace(inferred, classified=classified)


def _classification_values(result: Inferred, signals: list[dict[str, Any]]) -> dict[str, Any]:
    """The classifier's columns, and its fired rules appended to ``signals`` (F5.AC2)."""
    if result.classified is None:
        signals.append(
            {
                "rule_id": "classifier.engine_error",
                "category": "absence",
                "weight": 0,
                "detail": {"error": result.classify_error or "not_run"},
            }
        )
        return {"classification": Classification.UNKNOWN}
    c = result.classified
    v = c.verdict
    signals.extend(s.as_json() for s in v.signals)
    if c.identity.missing_peppers:
        signals.append(
            {
                "rule_id": "identity.pepper_missing",
                "category": "absence",
                "weight": 0,
                "detail": {"peppers": list(c.identity.missing_peppers)},
            }
        )
    if c.identity.server_only:
        signals.append(
            {
                "rule_id": "identity.server_only",
                "category": "absence",
                "weight": 0,
                "detail": {"reason": "no_client_enrichment"},
            }
        )
    return {
        "classification": v.classification,
        "bot_score": v.bot_score,
        "spoof_score": v.spoof_score,
        "classifier_version": c.version,
        "visitor_id": c.identity.visitor_id,
        "session_fp": c.identity.session_fp,
        "fingerprint_id": c.identity.fingerprint_id,
        # Each flag is NULL unless its evidence existed (F3.AC5): no ASN, no network
        # verdict; no exit list, no Tor verdict; no fingerprint, no proxy verdict.
        "is_datacenter": v.is_datacenter if result.asn.asn is not None else None,
        "is_vpn_suspected": v.is_vpn_suspected if result.asn.asn is not None else None,
        "is_tor": v.is_tor if result.tor_assessed else None,
        "is_proxy_suspected": (
            v.is_proxy_suspected if c.identity.fingerprint_id is not None else None
        ),
    }
