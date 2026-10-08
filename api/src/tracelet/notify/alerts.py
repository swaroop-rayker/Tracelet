"""Visit alerts, the pure part (F7, SPEC section 11 rows 17, 18 and 27, ADR-0020 decision 7).

What decides whether a visit alerts, at which priority, under which deduplication key,
whether quiet hours hold it, when a failed send is retried, and what the message says --
for visit alerts and, since M7.5, the digest, the spike, a new place and a returning visitor.
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
from dataclasses import dataclass, field
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
# Alert types (F7.AC10-F7.AC15, SPEC section 11 row 27). Every one off by default.
# ---------------------------------------------------------------------------

SPIKE_FLOOR_RANGE: Final = (1, 1000)
SPIKE_K_RANGE: Final = (1.5, 20.0)
RETURNING_DAYS_RANGE: Final = (1, 90)


@dataclass(frozen=True, slots=True)
class DigestType:
    enabled: bool = False
    at: str = "09:00"  # HH:MM in the reporting timezone

    def due(self, now: dt.datetime, reporting_tz: str) -> dt.date | None:
        """The day whose digest is due at ``now``: yesterday, once ``at`` has passed today.
        ``None`` before then, or when switched off. A missed day is never sent later."""
        if not self.enabled:
            return None
        local = now.astimezone(zoneinfo.ZoneInfo(reporting_tz))
        hours, minutes = (int(p) for p in self.at.split(":"))
        if (local.hour, local.minute) < (hours, minutes):
            return None
        return local.date() - dt.timedelta(days=1)


@dataclass(frozen=True, slots=True)
class SpikeType:
    enabled: bool = False
    floor: int = 10
    k: float = 3.0

    def fires(self, count: int, usual: float) -> bool:
        """At least the floor, and more than k times the usual (F7.AC11)."""
        return self.enabled and count >= self.floor and count > self.k * usual


@dataclass(frozen=True, slots=True)
class NewPlaceType:
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class ReturningType:
    enabled: bool = False
    after_days: int = 7


@dataclass(frozen=True, slots=True)
class AlertTypes:
    digest: DigestType = field(default_factory=DigestType)
    spike: SpikeType = field(default_factory=SpikeType)
    new_place: NewPlaceType = field(default_factory=NewPlaceType)
    returning: ReturningType = field(default_factory=ReturningType)


def median(values: list[int]) -> float:
    """The plain median: no numpy (CLAUDE.md section 5)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def region_label(region_key: str) -> str:
    """``IN|Karnataka`` reads "Karnataka, IN", as the alert's own place line does."""
    country, _, admin1 = region_key.partition("|")
    return f"{admin1}, {country}" if admin1 else country


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
    notes = [line for line in (_note_line(n) for n in payload.get("notes") or []) if line]
    if notes:
        lines.extend(["", *notes])
    url = payload.get("visit_url")
    if url:
        lines.extend(["", f'<a href="{html.escape(str(url), quote=True)}">Open the visit</a>'])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The M7.5 messages (F7.AC10-F7.AC14). Every value escaped, as above.
# ---------------------------------------------------------------------------

KIND_DIGEST: Final = "telegram.digest"
KIND_SPIKE: Final = "telegram.spike"
KIND_NEW_PLACE: Final = "telegram.new_place"
KIND_RETURNING: Final = "telegram.returning"


def _note_line(note: Mapping[str, Any]) -> str | None:
    """A new place or a returning visitor, as a line on the visit's alert (row 27). A new
    place is always strict (F7.AC12), so it needs no "best guess" mark."""
    if note.get("kind") == "new_place" and note.get("region_key"):
        return f"📍 First visit from {_e(region_label(str(note['region_key'])))} on this link"
    if note.get("kind") == "returning" and note.get("days") is not None:
        days = int(note["days"])
        return f"↩️ Back after {days} day{'' if days == 1 else 's'}"
    return None


def _visit_lines(payload: Mapping[str, Any]) -> list[str]:
    link = payload.get("link") or {}
    lines = [
        f"<b>Link</b> {_e(link.get('label', ''))} (/{_e(link.get('slug', ''))})",
        f"<b>When</b> {_e(payload.get('occurred_local', payload.get('occurred_at', '')))}",
    ]
    place, confirmed = _place(payload.get("location") or {})
    if place:
        lines.append(f"<b>Where</b> {_e(place)} ({'confirmed' if confirmed else 'best guess'})")
    url = payload.get("visit_url")
    if url:
        lines.extend(["", f'<a href="{html.escape(str(url), quote=True)}">Open the visit</a>'])
    return lines


def render_note(payload: Mapping[str, Any]) -> str:
    """A new place or a returning visitor sent alone, because the visit's own alert had
    already gone out that day (F7.AC14)."""
    line = _note_line(payload.get("note") or {}) or "Visitor note"
    return "\n".join([f"<b>{line}</b>", "", *_visit_lines(payload)])


def _ranked(rows: list[tuple[str, int]]) -> str:
    return " · ".join(f"{_e(name)} {count}" for name, count in rows)


def render_digest(payload: Mapping[str, Any]) -> str:
    """Yesterday in one message (F7.AC10). States are the best guess, and say so."""
    visits = int(payload.get("visits", 0))
    human = int(payload.get("human", 0))
    day = _e(payload.get("label", payload.get("day", "")))
    lines = [f"🗓 <b>Yesterday on Tracelet</b> ({day})", ""]
    if visits == 0:
        lines.append("No visits.")
    else:
        lines.append(
            f"<b>Visits</b> {visits}, of which {human} people ({round(human * 100 / visits)} %)"
        )
        states = [
            (region_label(str(r.get("key", ""))), int(r.get("count", 0)))
            for r in payload.get("states") or []
        ]
        if states:
            lines.append(f"<b>Top states</b> (best guess) {_ranked(states)}")
        links = [
            (str(r.get("label", "")), int(r.get("count", 0))) for r in payload.get("links") or []
        ]
        if links:
            lines.append(f"<b>Top links</b> {_ranked(links)}")
    dead = int(payload.get("dead", 0))
    if dead:
        lines.append(f"⚠️ <b>{dead} alert{'' if dead == 1 else 's'} dead-lettered</b>: see Alerts")
    url = payload.get("dashboard_url")
    if url:
        lines.extend(["", f'<a href="{html.escape(str(url), quote=True)}">Open the dashboard</a>'])
    return "\n".join(lines)


def render_spike(payload: Mapping[str, Any]) -> str:
    """A link far busier than usual for the time of day (F7.AC11). Names no place."""
    link = payload.get("link") or {}
    usual = float(payload.get("usual", 0))
    lines = [
        f"📈 <b>Busy link</b>: {_e(link.get('label', ''))} (/{_e(link.get('slug', ''))})",
        "",
        f"<b>Last 60 minutes</b> {int(payload.get('count', 0))} people",
        f"<b>Usually</b> {usual:g} (the median for the same 60 minutes on the last 7 days)",
    ]
    url = payload.get("link_url")
    if url:
        lines.extend(["", f'<a href="{html.escape(str(url), quote=True)}">Open the link</a>'])
    return "\n".join(lines)


def render_message(kind: str, payload: Mapping[str, Any], *, priority: NotifyPriority) -> str:
    """The message for an outbox row of ``kind``, rendered at send time."""
    if kind == KIND_DIGEST:
        return render_digest(payload)
    if kind == KIND_SPIKE:
        return render_spike(payload)
    if kind in (KIND_NEW_PLACE, KIND_RETURNING):
        return render_note(payload)
    return render(payload, priority=priority)


def probe_message(*, at: dt.datetime) -> str:
    """F7.AC8's test message: proves the token and the chat, and says nothing else."""
    return (
        "✅ <b>Tracelet test message</b>\n"
        f"Alerts will arrive in this chat. Sent {_e(at.strftime('%Y-%m-%d %H:%M %Z'))}."
    )
