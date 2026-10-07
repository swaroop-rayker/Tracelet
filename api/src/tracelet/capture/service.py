"""The capture path: record the visit, then let the client add to it (ADR-0004).

**The governing rule is CLAUDE.md invariant 1: the redirect must never fail.** Every
function a visitor's request passes through either succeeds or degrades -- the
telemetry, never the journey. That shapes three decisions here:

1. **The capture transaction is owned here, not by the request middleware.** The
   middleware turns a failed commit into a ``500`` Problem Details response, which is
   right for the admin API and wrong for a visitor, who must be redirected whatever
   happened (F15.AC7).
2. **A small in-process cache of live links.** The destination lives in the database,
   so without a cache a database outage would leave nowhere to send anyone. The cache
   is refreshed on every successful lookup and consulted only when the database
   cannot answer. At most a few hundred entries -- links are few -- so its resident
   cost is negligible (docs/ARCHITECTURE.md section 6).
3. **Missing configuration degrades a column, not the visit.** No pepper means no
   ``ip_hmac``; no key file means no ``ip_enc``; no session secret means no enrichment
   nonce. Each is logged loudly, and the visit is still recorded and redirected.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final

import structlog
from sqlalchemy import func, literal, literal_column, select, text, update
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.capture import nonce as nonces
from tracelet.capture import signals as sig
from tracelet.capture import useragent
from tracelet.capture.models import (
    Classification,
    ConsentState,
    Link,
    Visit,
    VisitStage,
    uuid7,
)
from tracelet.capture.schemas import EnrichmentPayload
from tracelet.config import Settings
from tracelet.crypto.envelope import EnvelopeError, seal_str
from tracelet.crypto.hashing import hmac_sha256
from tracelet.db.dml import execute_rowcount
from tracelet.db.engine import session_scope
from tracelet.net import ClientAddress, prefix_of
from tracelet.ratelimit import gcra

log = structlog.get_logger(__name__)

# Stamped on every visit whose classification this module decided (CLAUDE.md
# invariant 10). M2 decides exactly one thing -- "this is a known link-preview
# fetcher" -- and M4's classifier replaces it with the full rule set.
CLASSIFIER_VERSION: Final = "m2-crawler-gate.1"

SWEEP_AFTER: Final = dt.timedelta(seconds=90)
SWEEP_BATCH: Final = 500
STUCK_AFTER: Final = dt.timedelta(minutes=10)
PURGE_BATCH: Final = 1000

_SLUG = re.compile(r"^[a-z0-9-]{4,32}$")

# ---------------------------------------------------------------------------
# Rate limits (ADR-0010). Keyed by IP prefix; both must pass.
# ---------------------------------------------------------------------------

# 30/min with a burst of 10 governs the pace. The hourly limit caps the total; its
# burst is wider so it does not quietly override the per-minute allowance -- GCRA's
# burst is a count of back-to-back requests, and a burst of 10 at 300/hr would mean
# 10 quick requests and then one every 12 seconds, far stricter than 30/min.
CAPTURE_PER_MINUTE = gcra.Limit(
    name="cap_m", per_period=30, period=dt.timedelta(minutes=1), burst=10
)
CAPTURE_PER_HOUR = gcra.Limit(name="cap_h", per_period=300, period=dt.timedelta(hours=1), burst=60)
# One POST per visit, so this only bites a client replaying pages.
ENRICH_PER_PREFIX = gcra.Limit(
    name="enrich", per_period=60, period=dt.timedelta(minutes=1), burst=20
)
HONEYPOT_PER_PREFIX = gcra.Limit(name="hp", per_period=10, period=dt.timedelta(minutes=1), burst=10)
DECRYPT_PER_ADMIN = gcra.Limit(name="ip_dec", per_period=10, period=dt.timedelta(hours=1), burst=5)


# ---------------------------------------------------------------------------
# Links cache
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LinkSnapshot:
    """What the capture path needs to redirect, without the database."""

    id: uuid.UUID
    slug: str
    destination_url: str
    interstitial_ms: int
    ask_location: bool
    is_live: bool

    @classmethod
    def of(cls, link: Link) -> LinkSnapshot:
        return cls(
            id=link.id,
            slug=link.slug.lower(),
            destination_url=link.destination_url,
            interstitial_ms=link.interstitial_ms,
            ask_location=link.ask_location,
            is_live=link.is_live,
        )


_MAX_CACHED_LINKS: Final = 1000


@dataclass
class _LinkCache:
    entries: dict[str, LinkSnapshot] = field(default_factory=dict)
    # The link behind the bare /r/ (F1.AC3). Like `entries`, read only when the
    # database is unreachable.
    default: LinkSnapshot | None = None

    def put(self, snapshot: LinkSnapshot) -> None:
        if snapshot.slug not in self.entries and len(self.entries) >= _MAX_CACHED_LINKS:
            # Links are few; hitting this means something is enumerating slugs that
            # exist. Dropping the oldest insertion is enough to stay bounded.
            self.entries.pop(next(iter(self.entries)))
        self.entries[snapshot.slug] = snapshot

    def forget(self, slug: str) -> None:
        self.entries.pop(slug.lower(), None)
        if self.default is not None and self.default.slug == slug.lower():
            self.default = None

    def forget_default(self) -> None:
        self.default = None

    def get(self, slug: str) -> LinkSnapshot | None:
        return self.entries.get(slug.lower())


link_cache = _LinkCache()


def normalise_slug(raw: str) -> str | None:
    """Lowercased, or ``None`` if it cannot be a slug -- in which case the database is
    not asked at all."""
    candidate = raw.strip().lower()
    return candidate if _SLUG.match(candidate) else None


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


class Outcome(StrEnum):
    CAPTURED = "captured"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    # The visit could not be recorded, but the destination is known: redirect anyway.
    FALLBACK = "fallback"
    # The visit could not be recorded AND the destination is unknown. The only outcome
    # that cannot redirect, because there is nowhere to redirect to.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CaptureResult:
    outcome: Outcome
    link: LinkSnapshot | None = None
    visit_id: uuid.UUID | None = None
    nonce: str | None = None
    webview_host: str | None = None
    os_family: str | None = None


@dataclass(frozen=True, slots=True)
class RequestFacts:
    """Everything the capture path reads from the request, gathered once.

    Kept separate from the Starlette request so the capture logic can be exercised
    without HTTP, and so it is obvious exactly which inputs reach the database.
    """

    client: ClientAddress
    user_agent: str | None
    ch_platform: str | None
    ch_mobile: str | None
    raw_headers: tuple[tuple[str, str], ...]
    query: dict[str, str]
    referer: str | None
    cf_ray: str | None
    cf_ipcountry: str | None
    http_version: str | None
    tls_version: str | None
    trace_id: str | None


def _ip_forms(settings: Settings, visit_id: uuid.UUID, ip: str | None) -> dict[str, Any]:
    """The three stored forms of the address (F12.AC1). Never the address itself."""
    if ip is None:
        return {}
    forms: dict[str, Any] = {"ip_prefix": prefix_of(ip)}

    pepper = settings.pepper_stable.get_secret_value() if settings.pepper_stable else None
    if pepper:
        # Domain-separated, so the stable pepper that M4 also uses for visitor_id can
        # never produce the same value from two different inputs.
        forms["ip_hmac"] = hmac_sha256(pepper, "ip.v1", ip)
    else:
        log.error("capture_missing_pepper", setting="TRACELET_PEPPER_STABLE")

    try:
        sealed = seal_str(ip, aad=str(visit_id), key_path=str(settings.ip_key_file))
    except EnvelopeError:
        log.error("capture_missing_ip_key", path=str(settings.ip_key_file))
    else:
        forms["ip_enc"] = sealed.payload
        forms["ip_key_version"] = sealed.key_version
        # The IP period is the retention policy's (F12.AC7), read in the INSERT itself: a
        # primary-key lookup, and no cache to go stale. The environment's value applies
        # only before the policy row exists.
        forms["ip_purge_after"] = func.now() + func.make_interval(
            0,
            0,
            0,
            func.coalesce(
                literal_column("(SELECT ip_days FROM retention_policy WHERE id = 1)"),
                settings.retention_ip_days,
            ),
        )
    return forms


def _build_visit(
    settings: Settings, link: LinkSnapshot, facts: RequestFacts
) -> tuple[Visit, useragent.ParsedAgent]:
    visit_id = uuid7()
    agent = useragent.parse(
        facts.user_agent, ch_platform=facts.ch_platform, ch_mobile=facts.ch_mobile
    )
    edge = sig.edge_signals(
        cf_ray=facts.cf_ray,
        cf_ipcountry=facts.cf_ipcountry,
        edge_verified=facts.client.edge_verified,
    )

    fired: list[dict[str, Any]] = []
    if agent.crawler:
        fired.append(
            {
                "rule_id": "ua.link_preview_fetcher",
                "category": "crawler",
                "weight": 100,
                "detail": {"fetcher": agent.crawler},
            }
        )
    if probes := sig.exploit_probes(facts.query):
        # F5.AC13: weighted by the classifier, not here -- capture only records it.
        fired.append(
            {
                "rule_id": "capture.exploit_probe",
                "category": "spam",
                "weight": 0,
                "detail": {"patterns": probes},
            }
        )
    if facts.client.forged_edge_header:
        # Evidence only this request can carry -- it is gone once the response is
        # sent. Recorded unweighted: what it *means* is M4's classifier's decision.
        fired.append(
            {
                "rule_id": "edge.unverified_cf_header",
                "category": "spoof",
                "weight": 0,
                "detail": {"behind_cloudflare": settings.behind_cloudflare},
            }
        )

    visit = Visit(
        id=visit_id,
        link_id=link.id,
        stage=VisitStage.SERVER,
        trace_id=facts.trace_id,
        user_agent=sig.clean_user_agent(facts.user_agent, client_ip=facts.client.ip),
        ua_family=agent.ua_family,
        ua_version=agent.ua_version,
        os_family=agent.os_family,
        os_version=agent.os_version,
        device_class=agent.device_class,
        is_inapp_webview=agent.is_inapp_webview,
        webview_host=agent.webview_host,
        classification=Classification.CRAWLER if agent.crawler else Classification.UNKNOWN,
        classifier_version=CLASSIFIER_VERSION,
        signals=fired,
        request_headers=sig.sanitise_headers(facts.raw_headers, client_ip=facts.client.ip),
        http_version=sig.normalise_http_version(facts.http_version),
        tls_version=sig.normalise_tls_version(facts.tls_version),
        cf_colo=edge.cf_colo,
        cf_country=edge.cf_country,
        referer=sig.clean_referer(facts.referer, client_ip=facts.client.ip),
        utm=sig.utm_from(facts.query, client_ip=facts.client.ip),
        **_ip_forms(settings, visit_id, facts.client.ip),
    )
    return visit, agent


async def _lookup(db: AsyncSession, slug: str) -> LinkSnapshot | None:
    link = (await db.execute(select(Link).where(Link.slug == slug))).scalar_one_or_none()
    if link is None:
        link_cache.forget(slug)
        return None
    snapshot = LinkSnapshot.of(link)
    link_cache.put(snapshot)
    return snapshot


async def _lookup_default(db: AsyncSession) -> LinkSnapshot | None:
    link = (
        await db.execute(select(Link).where(Link.is_default.is_(True), Link.archived_at.is_(None)))
    ).scalar_one_or_none()
    if link is None:
        link_cache.forget_default()
        return None
    snapshot = LinkSnapshot.of(link)
    link_cache.put(snapshot)
    link_cache.default = snapshot
    return snapshot


async def capture(settings: Settings, slug: str | None, facts: RequestFacts) -> CaptureResult:
    """Record a visit and decide what the visitor sees. Never raises.

    ``slug=None`` is the bare ``/r/``, which goes through the default link (F1.AC3). An
    inactive default is a 404 like any other inactive link (F1.AC4).

    The ``session_scope`` block commits on exit, so by the time this returns
    ``CAPTURED`` the row is durable -- which is F2.AC2, "the visit exists even if the
    client never runs a line of JavaScript", made literal.
    """
    normalised: str | None = None
    if slug is not None:
        normalised = normalise_slug(slug)
        if normalised is None:
            return CaptureResult(outcome=Outcome.NOT_FOUND)

    link: LinkSnapshot | None = None
    try:
        async with session_scope() as db:
            if normalised is None:
                link = await _lookup_default(db)
            else:
                link = await _lookup(db, normalised)
            if link is None or not link.is_live:
                return CaptureResult(outcome=Outcome.NOT_FOUND)

            prefix = prefix_of(facts.client.ip) or "unknown"
            minute = await gcra.check(db, key=prefix, limit=CAPTURE_PER_MINUTE)
            hour = await gcra.check(db, key=prefix, limit=CAPTURE_PER_HOUR)

            if not (minute.allowed and hour.allowed):
                # F11.AC3: shed the telemetry, never the human. A minimal row makes the
                # shedding visible (DATA_MODEL section 5.3, invariant 8) and carries
                # nothing a client supplied.
                visit_id = uuid7()
                db.add(
                    Visit(
                        id=visit_id,
                        link_id=link.id,
                        stage=VisitStage.RATE_LIMITED,
                        finalized_at=func.now(),
                        trace_id=facts.trace_id,
                        classifier_version=CLASSIFIER_VERSION,
                        ip_prefix=prefix_of(facts.client.ip),
                    )
                )
                log.info(
                    "capture_rate_limited",
                    link_id=str(link.id),
                    limit=minute.limit_name if not minute.allowed else hour.limit_name,
                )
                return CaptureResult(outcome=Outcome.RATE_LIMITED, link=link, visit_id=visit_id)

            visit, agent = _build_visit(settings, link, facts)
            db.add(visit)
            visit_id = visit.id
    except Exception as exc:
        # Deliberately broad: the visitor is redirected whatever happened (F15.AC7).
        cached = link or (
            link_cache.get(normalised) if normalised is not None else link_cache.default
        )
        log.error(
            "capture_failed",
            slug_known=cached is not None,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        if cached is not None and cached.is_live:
            return CaptureResult(outcome=Outcome.FALLBACK, link=cached)
        return CaptureResult(outcome=Outcome.UNAVAILABLE)

    return CaptureResult(
        outcome=Outcome.CAPTURED,
        link=link,
        visit_id=visit_id,
        nonce=_mint_nonce(settings, visit_id, facts.client.ip),
        webview_host=agent.webview_host,
        os_family=agent.os_family,
    )


def _nonce_key(settings: Settings) -> bytes | None:
    secret = settings.session_secret.get_secret_value() if settings.session_secret else None
    return nonces.derive_key(secret) if secret else None


def _mint_nonce(settings: Settings, visit_id: uuid.UUID, ip: str | None) -> str | None:
    key = _nonce_key(settings)
    if key is None:
        # The visit is recorded; it just cannot be enriched. The sweeper finalises it.
        log.error("capture_missing_session_secret", setting="TRACELET_SESSION_SECRET")
        return None
    return nonces.mint(key, visit_id=visit_id, ip_prefix=prefix_of(ip))


# ---------------------------------------------------------------------------
# Enrichment (F2.AC4, F2.AC6)
# ---------------------------------------------------------------------------


class EnrichmentRejected(Exception):  # noqa: N818 - mirrors the NONCE_INVALID wire code
    """Nonce invalid, replayed, or the visit is no longer awaiting enrichment."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _client_hash(value: str | None) -> bytes | None:
    """A client-reported hash, normalised to a fixed-size digest.

    The client can send anything in these fields. Re-hashing bounds the stored size
    and turns an arbitrary string into a stable identifier for collision detection.
    """
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).digest()[:16]


def _decimal(value: float | None, places: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal(places))
    except InvalidOperation:
        return None


_GEO_STATES: Final[dict[str, ConsentState]] = {
    "granted": ConsentState.GRANTED,
    "denied": ConsentState.DENIED,
    # F4.AC1 as amended (SPEC section 11 row 8): permission is neither granted nor
    # denied, and the page never shows a prompt it cannot wait for (RISKS R20).
    "prompt": ConsentState.NOT_ASKED,
    "unavailable": ConsentState.UNAVAILABLE,
    "unsupported": ConsentState.UNAVAILABLE,
    # Permission was already granted, or asked for on a link that asks (ADR-0021), but no
    # position arrived before the page left.
    "timeout": ConsentState.UNAVAILABLE,
}


def enrichment_values(
    payload: EnrichmentPayload,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Map a validated enrichment payload onto visit columns.

    Everything here is a **claim**: the payload is attacker-controlled, and it is
    stored as reported for M4 to cross-check against what the server observed
    (docs/ARCHITECTURE.md section 5.2). Nothing is inferred from it here.
    """
    values: dict[str, Any] = {}
    absences: list[dict[str, Any]] = []

    if payload.screen:
        values |= {
            "screen_w": payload.screen.w,
            "screen_h": payload.screen.h,
            "dpr": _decimal(payload.screen.dpr, "0.01"),
            "color_depth": payload.screen.colorDepth,
            "touch_points": payload.screen.touchPoints,
        }
    if payload.viewport:
        values |= {"viewport_w": payload.viewport.w, "viewport_h": payload.viewport.h}
    if payload.hardware:
        values |= {
            "cpu_cores": payload.hardware.cores,
            "device_memory_gb": _decimal(payload.hardware.deviceMemoryGb, "0.1"),
        }
    if payload.gpu:
        values |= {"gpu_vendor": payload.gpu.vendor, "gpu_renderer": payload.gpu.renderer}
    if payload.locale:
        values |= {
            "tz_iana": payload.locale.tzIana,
            "tz_offset_min": payload.locale.tzOffsetMin,
            "languages": payload.locale.languages,
        }
    if payload.hashes:
        values |= {
            "canvas_hash": _client_hash(payload.hashes.canvas),
            "audio_hash": _client_hash(payload.hashes.audio),
            "font_hash": _client_hash(payload.hashes.font),
            "webgl_hash": _client_hash(payload.hashes.webgl),
        }

    geo = payload.geolocation
    if geo is not None:
        state = _GEO_STATES.get(geo.state, ConsentState.UNAVAILABLE)
        values["consent_state"] = state
        # The CHECK constraint forbids coordinates without consent; enforcing it here
        # too means a bad payload is a quiet no-op rather than an IntegrityError.
        if state is ConsentState.GRANTED and geo.lat is not None and geo.lng is not None:
            values |= {
                "gps_lat": _decimal(geo.lat, "0.000001"),
                "gps_lng": _decimal(geo.lng, "0.000001"),
                "gps_accuracy_m": _decimal(geo.accuracyM, "0.1"),
            }
        if geo.state in {"timeout", "unsupported", "unavailable"}:
            # F3.AC5: an absent value carries its reason, and `signals` is where the
            # data model puts it.
            absences.append(
                {
                    "rule_id": "client.geolocation_absent",
                    "category": "absence",
                    "weight": 0,
                    "detail": {"reason": geo.state},
                }
            )

    if payload.probes is not None:
        # Stored as reported -- unset probes omitted, never coerced to false (F3.AC5).
        # What each one means is the classifier's business (ADR-0011 amendment).
        values["client_probes"] = payload.probes.model_dump(exclude_none=True)

    if payload.honeypot and (payload.honeypot.fieldFilled or payload.honeypot.linkClicked):
        values["honeypot_tripped"] = True

    return values, absences


async def enrich(
    db: AsyncSession,
    settings: Settings,
    *,
    token: str,
    client_ip: str | None,
    payload: EnrichmentPayload,
) -> uuid.UUID:
    """Apply one enrichment. Single-use, prefix-bound, 60 s (F2.AC6, F11.AC4)."""
    key = _nonce_key(settings)
    if key is None:
        raise EnrichmentRejected("not_configured")
    try:
        verified = nonces.verify(key, token, ip_prefix=prefix_of(client_ip))
    except nonces.NonceRejectedError as exc:
        raise EnrichmentRejected(exc.reason) from exc

    values, absences = enrichment_values(payload)
    if absences:
        # Appended, not replaced: the capture path may already have recorded
        # server-side evidence in `signals`.
        values["signals"] = Visit.signals.op("||")(literal(absences, type_=pg.JSONB))

    # The conditional UPDATE is the whole of single-use: a replayed nonce, a visit the
    # sweeper already finalised, and a concurrent double-submit all match zero rows.
    stmt = (
        update(Visit)
        .where(
            Visit.id == verified.visit_id,
            Visit.enrichment_consumed_at.is_(None),
            Visit.stage == VisitStage.SERVER,
        )
        .values(
            **values,
            stage=VisitStage.ENRICHED,
            finalized_at=func.now(),
            enrichment_consumed_at=func.now(),
        )
        .returning(Visit.id)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise EnrichmentRejected("consumed_or_finalised")
    return verified.visit_id


async def trip_honeypot(
    db: AsyncSession, settings: Settings, *, token: str, client_ip: str | None
) -> None:
    """Mark the visit whose page produced this hit. Silent on every failure.

    The expiry is ignored on purpose: an automated client can follow a link long
    after the page loaded, and that late hit is exactly the evidence wanted. The MAC
    and the prefix binding still hold, so a hit cannot be forged for someone else's
    visit.
    """
    key = _nonce_key(settings)
    if key is None:
        return
    try:
        verified = nonces.verify(key, token, ip_prefix=prefix_of(client_ip), check_expiry=False)
    except nonces.NonceRejectedError:
        return
    await db.execute(
        update(Visit).where(Visit.id == verified.visit_id).values(honeypot_tripped=True)
    )


# ---------------------------------------------------------------------------
# Background work: the sweeper and the IP purge
# ---------------------------------------------------------------------------


async def sweep(db: AsyncSession) -> int:
    """Finalise visits whose enrichment never arrived (F2.AC7).

    Not an error path. For social traffic it may be the *primary* path -- that is what
    Spike B exists to find out (docs/ARCHITECTURE.md section 2.1).
    """
    swept = await execute_rowcount(
        db,
        text(
            """
            UPDATE visits SET stage = 'server_only', finalized_at = now()
            WHERE id IN (
                SELECT id FROM visits
                WHERE stage = 'server' AND occurred_at < now() - make_interval(secs => :after)
                ORDER BY occurred_at
                LIMIT :batch
                FOR UPDATE SKIP LOCKED
            )
            """
        ).bindparams(after=SWEEP_AFTER.total_seconds(), batch=SWEEP_BATCH),
    )
    if swept:
        log.info("visits_swept", count=swept)
    return swept


async def stuck_visits(db: AsyncSession) -> int:
    """Visits still unfinalised long after the sweeper should have taken them.

    Non-zero means the sweeper is not running or cannot keep up -- and since the sweeper
    is on the correctness path, that means visits are not being finalised, inferred or
    notified on (ADR-0009). Reported by the scheduler and, from M7, System Health.
    """
    # Two queues, one question: never finalised (the sweeper), or finalised but never
    # located (the inference job, ADR-0015). Either means visits are silently piling up.
    count = await db.execute(
        text(
            "SELECT count(*) FROM visits "
            "WHERE (finalized_at IS NULL AND occurred_at < now() - make_interval(secs => :after)) "
            "OR (finalized_at IS NOT NULL AND inferred_at IS NULL AND stage <> 'rate_limited' "
            "AND finalized_at < now() - make_interval(secs => :after))"
        ),
        {"after": STUCK_AFTER.total_seconds()},
    )
    return int(count.scalar_one())


async def purge_expired_ips(db: AsyncSession) -> int:
    """Null the IP ciphertext past its TTL, keeping the durable forms (F12.AC2).

    ``ip_hmac`` and ``ip_prefix`` survive, so analytics develop no hole at the 30-day
    mark: the purge removes the ability to identify a specific address while keeping
    the ability to reason about networks (ADR-0007).
    """
    purged = await execute_rowcount(
        db,
        text(
            """
            UPDATE visits SET ip_enc = NULL, ip_key_version = NULL
            WHERE id IN (
                SELECT id FROM visits
                WHERE ip_enc IS NOT NULL AND ip_purge_after < now()
                LIMIT :batch
                FOR UPDATE SKIP LOCKED
            )
            """
        ).bindparams(batch=PURGE_BATCH),
    )
    if purged:
        log.info("ips_purged", count=purged)
    return purged


async def run_sweeper_once() -> tuple[int, int]:
    """One sweeper tick in its own transaction. Returns (swept, stuck)."""
    async with session_scope() as db:
        swept = await sweep(db)
        stuck = await stuck_visits(db)
    if stuck:
        log.warning("visits_stuck", count=stuck, older_than_s=int(STUCK_AFTER.total_seconds()))
    return swept, stuck


async def run_ip_purge_once() -> int:
    async with session_scope() as db:
        return await purge_expired_ips(db)
