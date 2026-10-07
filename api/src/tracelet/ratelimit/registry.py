"""Every rate limit an owner may change from System Health (F11.AC9).

One list, so the dashboard shows each limit with what it protects, and an override for a
name that is not here is refused. Outbound limits carry a **ceiling**: the third party's own
terms (ipwho.is 1 000 a day; Nominatim at most 1 a second), which no setting may exceed --
F11.AC7 makes those budgets a promise to someone else.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

from tracelet.capture import service as capture
from tracelet.inference import outbound
from tracelet.ratelimit import gcra


@dataclass(frozen=True, slots=True)
class Entry:
    limit: gcra.Limit
    group: str  # "capture", "admin" or "outbound"
    label: str
    description: str
    # Most requests per second any setting may allow, for an outbound third party.
    ceiling_per_second: float | None = None


REGISTRY: Final[tuple[Entry, ...]] = (
    Entry(
        capture.CAPTURE_PER_MINUTE,
        "capture",
        "Tracking-link visits per minute",
        "Per network (/24 or /48). Over it, the visitor is still redirected, uncaptured (F11.AC3).",
    ),
    Entry(
        capture.CAPTURE_PER_HOUR,
        "capture",
        "Tracking-link visits per hour",
        "Per network. Caps the total behind the per-minute pace.",
    ),
    Entry(
        capture.ENRICH_PER_PREFIX,
        "capture",
        "Browser enrichment posts per minute",
        "Per network. One per visit, so this only stops replayed pages.",
    ),
    Entry(
        capture.HONEYPOT_PER_PREFIX,
        "capture",
        "Honeypot hits per minute",
        "Per network.",
    ),
    Entry(
        gcra.LOGIN_PER_IDENTIFIER,
        "admin",
        "Sign-in attempts per account",
        "Per email address, with lockout after repeated failures (F8.AC9).",
    ),
    Entry(
        gcra.LOGIN_PER_PREFIX,
        "admin",
        "Sign-in attempts per network",
        "Per network, across every account.",
    ),
    Entry(gcra.MFA_PER_IDENTIFIER, "admin", "Authenticator codes per account", "Per account."),
    Entry(
        gcra.RESET_REQUEST_PER_IDENTIFIER,
        "admin",
        "Password-reset requests per account",
        "Per account; the reset link goes to Telegram.",
    ),
    Entry(gcra.RECOVERY_PER_IDENTIFIER, "admin", "Recovery-code uses per account", "Per account."),
    Entry(
        capture.DECRYPT_PER_ADMIN,
        "admin",
        "IP address reveals per admin",
        "Each reveal is audited (F12.AC4).",
    ),
    Entry(
        outbound.IPWHOIS_PER_DAY,
        "outbound",
        "ipwho.is lookups per day",
        "The free tier allows 1 000 a day from this server.",
        ceiling_per_second=1000 / 86400,
    ),
    Entry(
        outbound.NOMINATIM_PER_MINUTE,
        "outbound",
        "Nominatim street-address lookups per minute",
        "Its usage policy allows at most one a second.",
        ceiling_per_second=1.0,
    ),
)

BY_NAME: Final = {entry.limit.name: entry for entry in REGISTRY}

MAX_PER_PERIOD: Final = 1_000_000
MAX_PERIOD: Final = dt.timedelta(days=7)
MAX_BURST: Final = 10_000


def problem(name: str, per_period: int, period_seconds: int, burst: int) -> str | None:
    """Why this setting is refused, or ``None``."""
    entry = BY_NAME.get(name)
    if entry is None:
        return f"There is no rate limit named {name!r}."
    if not 1 <= per_period <= MAX_PER_PERIOD:
        return f"per_period must be between 1 and {MAX_PER_PERIOD}."
    if not 1 <= period_seconds <= MAX_PERIOD.total_seconds():
        return "period_seconds must be between 1 second and 7 days."
    if not 1 <= burst <= MAX_BURST:
        return f"burst must be between 1 and {MAX_BURST}."
    if (
        entry.ceiling_per_second is not None
        and per_period / period_seconds > entry.ceiling_per_second * 1.0001
    ):
        return f"{entry.label}: above the third party's own limit, which no setting may exceed."
    return None
