"""Classification inside the ADR-0015 job (ADR-0011 amendment, item 3).

Runs after location inference for the same visit, because two rules need its output (the
datacenter class, timezone-vs-country), and is written in the same transaction. The only
I/O is four small reads for ``Context``; everything else is the pure ``rules.classify``.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Final

from sqlalchemy import text

from tracelet.capture.models import Visit, VisitStage
from tracelet.classify.config import ClassifierConfig, classifier_version
from tracelet.classify.identity import Identity, IdentityInput, derive
from tracelet.classify.rules import ClassifyInput, Context, Verdict, classify
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.inference.sources import timezone
from tracelet.inference.types import AsnInfo

EARTH_KM: Final = 6371.0088
TRAVEL_WINDOW: Final = dt.timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Classified:
    identity: Identity
    verdict: Verdict
    version: str


def _secret(value: object) -> bytes | None:
    raw = getattr(value, "get_secret_value", lambda: None)()
    return raw.encode() if raw else None


def identity_input(visit: Visit) -> IdentityInput:
    return IdentityInput(
        user_agent=visit.user_agent,
        headers=dict(visit.request_headers or {}),
        ip_prefix=str(visit.ip_prefix.network) if visit.ip_prefix is not None else None,
        enriched=visit.stage is VisitStage.ENRICHED,
        screen_w=visit.screen_w,
        screen_h=visit.screen_h,
        dpr=float(visit.dpr) if visit.dpr is not None else None,
        color_depth=visit.color_depth,
        tz_iana=visit.tz_iana,
        languages=tuple(visit.languages or ()),
        cpu_cores=visit.cpu_cores,
        device_memory_gb=float(visit.device_memory_gb)
        if visit.device_memory_gb is not None
        else None,
        gpu_vendor=visit.gpu_vendor,
        gpu_renderer=visit.gpu_renderer,
        hashes=(visit.canvas_hash, visit.audio_hash, visit.font_hash, visit.webgl_hash),
    )


def classify_input(
    visit: Visit, *, asn: AsnInfo, country: str | None, ptr_masked: str | None
) -> ClassifyInput:
    from tracelet.inference.sources.asn_org import classes  # noqa: PLC0415 - cached data

    org = (asn.org or "").casefold()
    return ClassifyInput(
        user_agent=visit.user_agent,
        headers=dict(visit.request_headers or {}),
        http_version=visit.http_version,
        tls_version=visit.tls_version,
        enriched=visit.stage is VisitStage.ENRICHED,
        honeypot_tripped=visit.honeypot_tripped,
        capture_signals=tuple(str(s.get("rule_id")) for s in visit.signals or ()),
        probes=dict(visit.client_probes or {}),
        screen_w=visit.screen_w,
        screen_h=visit.screen_h,
        touch_points=visit.touch_points,
        cpu_cores=visit.cpu_cores,
        device_memory_gb=float(visit.device_memory_gb)
        if visit.device_memory_gb is not None
        else None,
        gpu_renderer=visit.gpu_renderer,
        tz_countries=timezone.countries_for(visit.tz_iana),
        country=country,
        is_hosting=asn.is_hosting,
        is_mobile_network=asn.is_mobile or asn.is_cgnat,
        is_tor=asn.is_tor,
        is_vpn_org=any(k in org for k in ("vpn", "proxy"))
        or (asn.asn in classes().hosting and "vpn" in classes().hosting[asn.asn].lower()),
        rdns_ptr=ptr_masked,
    )


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi, dlmb = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


async def context(
    visit: Visit,
    identity: Identity,
    *,
    asn: int | None,
    point: tuple[float, float] | None,
    config: ClassifierConfig,
) -> Context:
    prefix = str(visit.ip_prefix.network) if visit.ip_prefix is not None else None
    window = dt.timedelta(hours=config.collision_window_hours)
    fingerprint_asns = prefix_fingerprints = prefix_visits = 0
    travel: float | None = None
    async with session_scope() as db:
        if identity.fingerprint_id is not None:
            rows = await db.execute(
                text(
                    "SELECT DISTINCT asn FROM visits WHERE fingerprint_id = :fp AND asn IS NOT NULL "
                    "AND id <> :id AND occurred_at > :since"
                ),
                {
                    "fp": identity.fingerprint_id,
                    "id": visit.id,
                    "since": visit.occurred_at - window,
                },
            )
            seen = {r[0] for r in rows}
            if asn is not None:
                seen.add(asn)
            fingerprint_asns = len(seen)
            if point is not None:
                previous = (
                    await db.execute(
                        text(
                            "SELECT occurred_at, strict_lat, strict_lng FROM visits "
                            "WHERE fingerprint_id = :fp AND id <> :id AND strict_lat IS NOT NULL "
                            "AND occurred_at BETWEEN :since AND :until "
                            "ORDER BY occurred_at DESC LIMIT 1"
                        ),
                        {
                            "fp": identity.fingerprint_id,
                            "id": visit.id,
                            "since": visit.occurred_at - TRAVEL_WINDOW,
                            "until": visit.occurred_at,
                        },
                    )
                ).first()
                if previous is not None:
                    km = _km(point, (float(previous[1]), float(previous[2])))
                    seconds = (visit.occurred_at - previous[0]).total_seconds()
                    # Same place twice is not travel, however close in time.
                    if km > 50:
                        travel = math.inf if seconds <= 0 else km / (seconds / 3600)
        if prefix is not None:
            prefix_fingerprints = int(
                (
                    await db.execute(
                        text(
                            "SELECT count(DISTINCT fingerprint_id) FROM visits "
                            "WHERE ip_prefix = CAST(:p AS inet) AND occurred_at > :since"
                        ),
                        {"p": prefix, "since": visit.occurred_at - window},
                    )
                ).scalar_one()
            ) + (1 if identity.fingerprint_id is not None else 0)
            prefix_visits = int(
                (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM visits WHERE ip_prefix = CAST(:p AS inet) "
                            "AND occurred_at BETWEEN :since AND :until"
                        ),
                        {
                            "p": prefix,
                            "since": visit.occurred_at
                            - dt.timedelta(minutes=config.rate_window_minutes),
                            "until": visit.occurred_at,
                        },
                    )
                ).scalar_one()
            )
    return Context(
        fingerprint_asns=fingerprint_asns,
        prefix_fingerprints=prefix_fingerprints,
        prefix_visits=prefix_visits,
        travel_kmh=travel,
    )


async def classify_visit(
    visit: Visit,
    *,
    settings: Settings,
    config: ClassifierConfig,
    settings_version: int,
    asn: AsnInfo,
    country: str | None,
    point: tuple[float, float] | None,
    ptr_masked: str | None,
) -> Classified:
    identity = derive(
        identity_input(visit),
        pepper_stable=_secret(settings.pepper_stable),
        pepper_rotating=_secret(settings.pepper_rotating),
        pepper_fp=_secret(settings.pepper_fp),
        day=visit.occurred_at.date(),
    )
    ctx = await context(visit, identity, asn=asn.asn, point=point, config=config)
    verdict = classify(
        classify_input(visit, asn=asn, country=country, ptr_masked=ptr_masked), ctx, config
    )
    return Classified(
        identity=identity, verdict=verdict, version=classifier_version(settings_version)
    )
