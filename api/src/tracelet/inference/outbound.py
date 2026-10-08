"""Outbound calls from inference: budgets and circuit breakers (F4.AC7, F11.AC7).

Two third parties are called from inference: ipwho.is (S9) and Nominatim (street
addresses for consented visits, F4.AC4). Both are free services with published limits,
and both must degrade silently -- inference completes on whatever else answered.

* **Budget** -- a GCRA limit in PostgreSQL (ADR-0010), so both workers share one allowance
  and the service's terms hold however many processes are running.
* **Breaker** -- per process. After ``threshold`` consecutive failures the breaker opens and
  every call short-circuits for ``cooldown``; then one trial call is allowed through. A
  dead service costs one timeout per cooldown, not one per visit. **Its state is
  announced** in ``rate_limit_buckets`` as ``breaker:<name>`` with the time it stays open
  until, so System Health's degradation banner (F10.AC14) sees an open breaker whichever
  worker opened it. The decision itself stays in process.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import text

from tracelet.db.engine import session_scope
from tracelet.ratelimit import gcra

# ipwho.is: 1 000 requests a day per client address, free tier, commercial use allowed.
# 900 leaves headroom for manual testing from the same host.
IPWHOIS_PER_DAY: Final = gcra.Limit(
    name="out_ipwhois_d", per_period=900, period=dt.timedelta(days=1), burst=20
)
# Nominatim: an absolute maximum of 1/s, and 4/min for anything running on a schedule --
# which a background job is. The stricter figure governs.
NOMINATIM_PER_MINUTE: Final = gcra.Limit(
    name="out_nominatim_m", per_period=4, period=dt.timedelta(minutes=1), burst=1
)


async def within_budget(key: str, limit: gcra.Limit) -> bool:
    async with session_scope() as db:
        return (await gcra.check(db, key=key, limit=limit)).allowed


@dataclass
class Breaker:
    name: str
    threshold: int = 5
    cooldown_s: float = 120.0
    _failures: int = 0
    _opened_at: float | None = None
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def _now(self) -> float:
        return self._clock()

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "open" if self._now() - self._opened_at < self.cooldown_s else "half_open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> bool:
        """Close the breaker. True if it had been open: the announcement can go."""
        was_open = self._opened_at is not None
        self._failures, self._opened_at = 0, None
        return was_open

    def failure(self) -> bool:
        """Count a failure. True if this one opened (or re-opened) the breaker."""
        self._failures += 1
        if self.state == "half_open" or self._failures >= self.threshold:
            self._opened_at = self._now()
            return True
        return False


IPWHOIS_BREAKER: Final = Breaker("ipwhois")
NOMINATIM_BREAKER: Final = Breaker("nominatim")

BREAKER_KEY_PREFIX: Final = "breaker:"


async def failed(breaker: Breaker) -> None:
    """A failed call. If it opened the breaker, announce until when (F10.AC14)."""
    if breaker.failure():
        until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=breaker.cooldown_s)
        async with session_scope() as db:
            await db.execute(
                text(
                    "INSERT INTO rate_limit_buckets (key, tat, updated_at) VALUES (:k, :t, now()) "
                    "ON CONFLICT (key) DO UPDATE SET tat = EXCLUDED.tat, updated_at = now()"
                ),
                {"k": f"{BREAKER_KEY_PREFIX}{breaker.name}", "t": until},
            )


async def succeeded(breaker: Breaker) -> None:
    """A successful call. If the breaker had been open, withdraw the announcement."""
    if breaker.success():
        async with session_scope() as db:
            await db.execute(
                text("DELETE FROM rate_limit_buckets WHERE key = :k"),
                {"k": f"{BREAKER_KEY_PREFIX}{breaker.name}"},
            )
