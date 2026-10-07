"""Visit alerts, the pure part (F7, SPEC section 11 rows 17 and 18, ADR-0020 decision 7).

What decides whether a visit alerts, at which priority, under which deduplication key,
whether quiet hours hold it, when a failed send is retried, and what the message says.
No I/O, so every rule is unit-tested directly. The outbox (``notify/outbox.py``) and the
worker (``notify/worker.py``) do the writing and the sending.

**The message is rendered at send time from a self-contained payload**, not stored as
text: a payload outlives its visit (no FK, DATA_MODEL 7.1 invariant 4), and a wording fix
then applies to alerts still queued. Telegram parses the message as HTML, and almost every
value in it -- link label, ISP name, browser, a GeoNames place -- is text someone else
chose, so every value is escaped. Nothing is ever inserted unescaped but the fixed tags.
"""

from __future__ import annotations

import datetime as dt
import html
import random
import re
import uuid
import zoneinfo
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from tracelet.capture.models import GeofenceState
from tracelet.geofence.models import NotifyPriority

URGENCY: Final = {NotifyPriority.SILENT: 0, NotifyPriority.NORMAL: 1, NotifyPriority.HIGH: 2}

# Link policy defaults, as migration 0009 backfilled them (SPEC section 11 row 18).
POLICY_DEFAULTS: Final = {
    "inside": NotifyPriority.HIGH,
    "outside": NotifyPriority.NORMAL,
    "undetermined": NotifyPriority.NORMAL,
}


def _policy(policy: Mapping[str, Any], key: str) -> NotifyPriority:
    value = policy.get(key)
    try:
        return NotifyPriority(value) if value is not None else POLICY_DEFAULTS[key]
    except ValueError:
        return POLICY_DEFAULTS[key]


def less_urgent(a: NotifyPriority, b: NotifyPriority) -> NotifyPriority:
    return a if URGENCY[a] <= URGENCY[b] else b


def resolve_priority(
    policy: Mapping[str, Any],
    state: GeofenceState | None,
    deciding: NotifyPriority | None,
) -> NotifyPriority:
    """SPEC section 11 row 18, "either can mute".

    * inside -- the less urgent of the link's ``inside`` and the deciding geofence's own;
    * outside, and no applicable geofence -- the link's ``outside``;
    * undetermined -- the link's ``undetermined``.
    """
    if state is GeofenceState.INSIDE:
        link = _policy(policy, "inside")
        return less_urgent(link, deciding) if deciding is not None else link
    if state is GeofenceState.UNDETERMINED:
        return _policy(policy, "undetermined")
    return _policy(policy, "outside")


def dedup_key(
    *,
    link_id: uuid.UUID,
    visitor_id: bytes | None,
    ip_hmac: bytes | None,
    occurred_at: dt.datetime,
    reporting_tz: str,
) -> str:
    """One alert per link and visitor per local day (F7.AC2, SPEC section 11 row 17).

    The day is the visit's arrival date in the reporting timezone, so a retry or a
    re-inference of the same visit produces the same key. Without a ``visitor_id`` the
    network stands in for the visitor, rather than alerting on every request.
    """
    day = occurred_at.astimezone(zoneinfo.ZoneInfo(reporting_tz)).date().isoformat()
    who = visitor_id.hex() if visitor_id else f"ip:{ip_hmac.hex()}" if ip_hmac else "unknown"
    return f"visit_alert:{link_id}:{who}:{day}"


# ---------------------------------------------------------------------------
# Quiet hours (F7.AC9)
# ---------------------------------------------------------------------------

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True, slots=True)
class QuietHours:
    enabled: bool = False
    start: str = "23:00"
    end: str = "07:00"
    timezone: str = "Asia/Kolkata"

    @staticmethod
    def valid_time(value: str) -> bool:
        return bool(_HHMM.match(value))

    def _minutes(self, value: str) -> int:
        hours, minutes = value.split(":")
        return int(hours) * 60 + int(minutes)

    def active(self, now: dt.datetime) -> bool:
        """Whether ``now`` falls in the window. A window whose end is before its start
        crosses midnight; one whose start equals its end is empty."""
        if not self.enabled:
            return False
        local = now.astimezone(zoneinfo.ZoneInfo(self.timezone))
        minute = local.hour * 60 + local.minute
        start, end = self._minutes(self.start), self._minutes(self.end)
        if start == end:
            return False
        if start < end:
            return start <= minute < end
        return minute >= start or minute < end


# ---------------------------------------------------------------------------
# Retry (F7.AC6)
# ---------------------------------------------------------------------------

BACKOFF_BASE_S: Final = 30.0
BACKOFF_CAP_S: Final = 3600.0


def backoff_seconds(attempts: int, rng: random.Random | None = None) -> float:
    """Full jitter: uniform in ``[0, min(cap, base * 2^(attempts-1))]``. Spreads retries
    after an outage instead of sending them back in one burst."""
    ceiling = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** max(0, attempts - 1))
    return (rng or random).uniform(0, ceiling)


# ---------------------------------------------------------------------------
# The message (F7.AC3, F7.AC4)
# ---------------------------------------------------------------------------


def _e(value: object) -> str:
    return html.escape(str(value), quote=False)


def _place(location: Mapping[str, Any]) -> tuple[str, bool]:
    """The deepest named place, and whether it is confirmed (strict). The strict location
    is shown where the engine stated one; the best guess only where it abstained."""
    for kind in ("strict", "advisory"):
        values = location.get(kind) or {}
        parts = [values.get(k) for k in ("city", "admin1", "country_code")]
        named = [str(p) for p in parts if p]
        if named:
            return ", ".join(named), kind == "strict"
    return "", False


def _confidence(location: Mapping[str, Any]) -> str:
    confidence = location.get("confidence") or {}
    for level in ("city", "admin1", "country"):
        value = confidence.get(level)
        if value is not None:
            return f"{round(float(value) * 100)} % at {'state' if level == 'admin1' else level}"
    return "no confidence"


def render(payload: Mapping[str, Any], *, priority: NotifyPriority) -> str:
    """The Telegram message, as Telegram-flavoured HTML. Every value escaped."""
    state = payload.get("geofence_state")
    geofence = payload.get("geofence") or {}
    location = payload.get("location") or {}
    place, confirmed = _place(location)

    if state == GeofenceState.INSIDE:
        headline = f"🔴 <b>Inside {_e(geofence.get('name', 'a geofence'))}</b>"
    elif state == GeofenceState.UNDETERMINED:
        headline = "⚪ <b>Location not confirmed</b>: could not be checked against your geofences"
    elif state == GeofenceState.OUTSIDE:
        headline = "🔵 <b>New visitor, outside your geofences</b>"
    else:
        headline = "🔵 <b>New visitor</b>"
    if priority is NotifyPriority.HIGH and state != GeofenceState.INSIDE:
        headline = f"‼️ {headline}"

    link = payload.get("link") or {}
    device = payload.get("device") or {}
    network = payload.get("network") or {}
    lines = [
        headline,
        "",
        f"<b>Link</b> {_e(link.get('label', ''))} (/{_e(link.get('slug', ''))})",
        f"<b>When</b> {_e(payload.get('occurred_local', payload.get('occurred_at', '')))}",
    ]
    if place:
        mark = "confirmed" if confirmed else "best guess"
        lines.append(f"<b>Where</b> {_e(place)} ({mark}, {_e(_confidence(location))})")
    else:
        lines.append("<b>Where</b> unknown: every source abstained")
    described = " · ".join(
        _e(v) for v in (device.get("class"), device.get("os"), device.get("browser")) if v
    )
    if described:
        lines.append(f"<b>Device</b> {described}")
    asn = network.get("asn")
    isp = " · ".join(
        _e(v)
        for v in (
            network.get("connection_class"),
            f"AS{asn}" if asn else None,
            network.get("asn_org"),
        )
        if v
    )
    if isp:
        lines.append(f"<b>Network</b> {isp}")
    score = payload.get("bot_score")
    lines.append(
        f"<b>Classified</b> {_e(payload.get('classification', 'human'))}"
        + (f" (bot score {_e(score)})" if score is not None else "")
    )
    url = payload.get("visit_url")
    if url:
        lines.extend(["", f'<a href="{html.escape(str(url), quote=True)}">Open the visit</a>'])
    return "\n".join(lines)


def probe_message(*, at: dt.datetime) -> str:
    """F7.AC8's test message: proves the token and the chat, and says nothing else."""
    return (
        "✅ <b>Tracelet test message</b>\n"
        f"Alerts will arrive in this chat. Sent {_e(at.strftime('%Y-%m-%d %H:%M %Z'))}."
    )
