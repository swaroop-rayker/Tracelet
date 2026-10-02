"""S9 -- an external IP-location API, ipwho.is (F4.AC5, F4.AC7).

**One service, not two.** ADR-0005 named ipwho.is and ip-api.com. Checked in M3,
ip-api.com's free endpoint is HTTP only ("256-bit SSL encryption is not available for
this free API") and restricted to non-commercial use, so it fails F4.AC5's own "HTTPS"
and would send a visitor's address across the internet in plaintext. ipwho.is: HTTPS,
no key, commercial use allowed, 1 000 requests a day per client (RISKS R2).

**What leaves the server is the prefix, not the visitor.** Results are cached by /24 (or
/48) anyway, so the lookup is made for the prefix's network address rather than the
visitor's own -- the third party learns which network, never which host. A visitor from
a cached prefix causes no outbound request at all (F4.AC7).

Every refusal is a reason, never a hang: disabled by the global switch, breaker open,
daily budget spent, timeout, or an error answer. Inference completes on the rest.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import ipaddress
from typing import Any, Final

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.inference import outbound
from tracelet.inference.models import GeoCacheEntry
from tracelet.inference.sources import SourceInput, SourceUnavailable
from tracelet.inference.types import Candidate, GeoLevel, InferenceSource
from tracelet.net import prefix_of

SOURCE: Final = "ipwhois"
URL: Final = "https://ipwho.is/{ip}"
FIELDS: Final = "success,message,country_code,region,city,latitude,longitude"
CACHE_TTL: Final = dt.timedelta(days=7)
USER_AGENT: Final = "Tracelet/0.1 (+visitor-intelligence; location inference)"

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client  # noqa: PLW0603 - one pooled client per process
    if _client is None:
        _client = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=False)
    return _client


def candidate(payload: dict[str, Any]) -> list[Candidate]:
    """An ipwho.is answer as a candidate. ``success: false`` means "no answer"."""
    if not payload.get("success") or not payload.get("country_code"):
        return []
    region = payload.get("region") or None
    city = payload.get("city") or None
    lat, lng = payload.get("latitude"), payload.get("longitude")
    return [
        Candidate(
            source=InferenceSource.EXTERNAL_API,
            level=GeoLevel.CITY if city else GeoLevel.ADMIN1 if region else GeoLevel.COUNTRY,
            country_code=str(payload["country_code"]).upper(),
            admin1=str(region) if region else None,
            city=str(city) if city else None,
            lat=float(lat) if isinstance(lat, int | float) else None,
            lng=float(lng) if isinstance(lng, int | float) else None,
            evidence={"service": SOURCE},
        )
    ]


async def _cached(prefix: str) -> dict[str, Any] | None:
    async with session_scope() as db:
        row = (
            await db.execute(
                select(GeoCacheEntry).where(
                    GeoCacheEntry.source == SOURCE,
                    GeoCacheEntry.ip_prefix == prefix,
                    GeoCacheEntry.expires_at > dt.datetime.now(dt.UTC),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        await db.execute(
            update(GeoCacheEntry)
            .where(GeoCacheEntry.source == SOURCE, GeoCacheEntry.ip_prefix == prefix)
            .values(hit_count=GeoCacheEntry.hit_count + 1)
        )
        return dict(row.payload)


async def _store(prefix: str, payload: dict[str, Any]) -> None:
    now = dt.datetime.now(dt.UTC)
    stmt = insert(GeoCacheEntry).values(
        source=SOURCE, ip_prefix=prefix, payload=payload, fetched_at=now, expires_at=now + CACHE_TTL
    )
    async with session_scope() as db:
        await db.execute(
            stmt.on_conflict_do_update(
                constraint="pk_geo_cache",
                set_={"payload": payload, "fetched_at": now, "expires_at": now + CACHE_TTL},
            )
        )


async def lookup(prefix: str, timeout_s: float) -> dict[str, Any]:
    """Ask ipwho.is about ``prefix``'s network address. Raises ``SourceUnavailable``."""
    breaker = outbound.IPWHOIS_BREAKER
    if not breaker.allow():
        raise SourceUnavailable("circuit_open")
    if not await outbound.within_budget(f"out:{SOURCE}", outbound.IPWHOIS_PER_DAY):
        raise SourceUnavailable("daily_budget_spent")
    network = ipaddress.ip_network(prefix, strict=False)
    try:
        response = await _http().get(
            URL.format(ip=network.network_address),
            params={"fields": FIELDS},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_s,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        breaker.failure()
        raise SourceUnavailable(f"request_failed:{type(exc).__name__}") from exc
    breaker.success()
    return payload


async def produce(inp: SourceInput, settings: Settings, timeout_s: float) -> list[Candidate]:
    if not settings.external_geo_enabled:
        raise SourceUnavailable("disabled_by_operator")
    prefix = prefix_of(str(inp.ip)) if inp.ip is not None else None
    if prefix is None:
        raise SourceUnavailable("no_address")
    cached = await _cached(prefix)
    payload = cached if cached is not None else await lookup(prefix, timeout_s)
    if cached is None:
        # Negative answers are cached too: asking again tomorrow is not a different
        # question, and the daily budget is shared by every visitor.
        await _store(prefix, payload)
    return [
        dataclasses.replace(c, evidence={**c.evidence, "cached": cached is not None})
        for c in candidate(payload)
    ]
