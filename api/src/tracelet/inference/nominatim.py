"""Street addresses for consented visits, from OpenStreetMap's Nominatim (F4.AC3, F4.AC4).

Only ever called with coordinates the visitor's own browser reported under an existing
grant; the database refuses an address on any other visit
(``ck_visits_gps_requires_consent``). Nothing else about the visit is sent.

The public service's policy (checked in M3): at most one request a second, and **four a
minute for anything running on a schedule** -- a background job is -- so the stricter
figure is the budget; a User-Agent that identifies the application; results cached;
ODbL attribution wherever an address is shown.

**The cache is in memory, not a table.** Rounded to four decimals (~11 m), bounded, one
day. A persisted cache would be a second copy of consented addresses that outlives the
visits' retention; this one dies with the worker, and still stops a visitor who returns
from the same place from causing a second request.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Final

import httpx

from tracelet.config import Settings
from tracelet.inference import outbound
from tracelet.inference.sources import SourceUnavailable

URL: Final = "https://nominatim.openstreetmap.org/reverse"
CACHE_SIZE: Final = 256
CACHE_TTL_S: Final = 86_400.0
MAX_ADDRESS_CHARS: Final = 500

_cache: OrderedDict[tuple[float, float], tuple[float, str | None]] = OrderedDict()
_client: httpx.AsyncClient | None = None


def user_agent(settings: Settings) -> str:
    """Identifies the application, as the policy requires -- never a library default."""
    contact = f"; {settings.acme_email}" if settings.acme_email else ""
    return f"Tracelet/0.1 (+https://{settings.site_address}{contact})"


def _http(settings: Settings) -> httpx.AsyncClient:
    global _client  # noqa: PLW0603 - one pooled client per process
    if _client is None:
        _client = httpx.AsyncClient(headers={"User-Agent": user_agent(settings)})
    return _client


def _key(lat: float, lng: float) -> tuple[float, float]:
    return (round(lat, 4), round(lng, 4))


def cached(lat: float, lng: float) -> tuple[bool, str | None]:
    entry = _cache.get(_key(lat, lng))
    if entry is None or time.monotonic() - entry[0] > CACHE_TTL_S:
        return False, None
    _cache.move_to_end(_key(lat, lng))
    return True, entry[1]


def remember(lat: float, lng: float, address: str | None) -> None:
    _cache[_key(lat, lng)] = (time.monotonic(), address)
    _cache.move_to_end(_key(lat, lng))
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)


async def street_address(
    settings: Settings, lat: float, lng: float, *, timeout_s: float = 3.0
) -> str | None:
    """The street-level address, or ``None`` if Nominatim knows none. Raises
    ``SourceUnavailable`` when it could not be asked."""
    if not settings.external_geo_enabled:
        raise SourceUnavailable("disabled_by_operator")
    hit, address = cached(lat, lng)
    if hit:
        return address
    if not outbound.NOMINATIM_BREAKER.allow():
        raise SourceUnavailable("circuit_open")
    if not await outbound.within_budget("out:nominatim", outbound.NOMINATIM_PER_MINUTE):
        raise SourceUnavailable("rate_budget_spent")
    try:
        response = await _http(settings).get(
            URL,
            params={"format": "jsonv2", "lat": f"{lat:.6f}", "lon": f"{lng:.6f}", "zoom": 18},
            # Per request, not only on the client: the policy forbids a library default,
            # and this must hold whichever client sends it.
            headers={"User-Agent": user_agent(settings)},
            timeout=timeout_s,
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        await outbound.failed(outbound.NOMINATIM_BREAKER)
        raise SourceUnavailable(f"request_failed:{type(exc).__name__}") from exc
    await outbound.succeeded(outbound.NOMINATIM_BREAKER)
    name = body.get("display_name") if isinstance(body, dict) else None
    address = str(name)[:MAX_ADDRESS_CHARS] if name else None
    remember(lat, lng, address)
    return address


def reset_for_tests() -> None:
    _cache.clear()
