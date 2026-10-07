"""Visit-alert rules: priority, deduplication, quiet hours, backoff and the message
(F7, SPEC section 11 rows 17 and 18, ADR-0020 decision 7)."""

from __future__ import annotations

import datetime as dt
import random
import uuid

import pytest

from tracelet.capture.models import GeofenceState
from tracelet.geofence.models import NotifyPriority
from tracelet.notify import alerts

HIGH, NORMAL, SILENT = NotifyPriority.HIGH, NotifyPriority.NORMAL, NotifyPriority.SILENT
IN_, OUT, UND = GeofenceState.INSIDE, GeofenceState.OUTSIDE, GeofenceState.UNDETERMINED
DEFAULTS = {"inside": "high", "outside": "normal", "undetermined": "normal", "automated": "silent"}

# ---------------------------------------------------------------------------
# Priority: "either can mute" (SPEC section 11 row 18)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("policy", "state", "deciding", "expected"),
    [
        # The defaults reproduce ADR-0020 decision 7.
        (DEFAULTS, IN_, HIGH, HIGH),
        (DEFAULTS, OUT, None, NORMAL),
        (DEFAULTS, UND, None, NORMAL),
        (DEFAULTS, None, None, NORMAL),
        # Inside: the less urgent of the link and the geofence; either can mute.
        (DEFAULTS, IN_, NORMAL, NORMAL),
        (DEFAULTS, IN_, SILENT, SILENT),
        (DEFAULTS | {"inside": "normal"}, IN_, HIGH, NORMAL),
        (DEFAULTS | {"inside": "silent"}, IN_, HIGH, SILENT),
        # The link's own keys for everything else.
        (DEFAULTS | {"outside": "silent"}, OUT, None, SILENT),
        (DEFAULTS | {"outside": "high"}, None, None, HIGH),
        (DEFAULTS | {"undetermined": "silent"}, UND, None, SILENT),
        # A policy stored before migration 0009, or damaged, falls back to the defaults.
        ({"inside": "high", "outside": "normal"}, UND, None, NORMAL),
        ({"inside": "loud"}, IN_, HIGH, HIGH),
    ],
)
def test_the_priority_is_resolved_from_link_and_geofence(
    policy: dict[str, str],
    state: GeofenceState | None,
    deciding: NotifyPriority | None,
    expected: NotifyPriority,
) -> None:
    assert alerts.resolve_priority(policy, state, deciding) is expected


# ---------------------------------------------------------------------------
# Deduplication: one per link and visitor per local day (row 17)
# ---------------------------------------------------------------------------

LINK = uuid.UUID("01890000-0000-7000-8000-000000000001")
IST = "Asia/Kolkata"


def _key(at: dt.datetime, visitor: bytes | None = b"\x01\x02", ip: bytes | None = b"\xff") -> str:
    return alerts.dedup_key(
        link_id=LINK, visitor_id=visitor, ip_hmac=ip, occurred_at=at, reporting_tz=IST
    )


def test_the_day_is_the_local_day_of_arrival() -> None:
    # 18:29 UTC is 23:59 IST; 18:31 UTC is 00:01 IST the next day.
    before = _key(dt.datetime(2026, 10, 6, 18, 29, tzinfo=dt.UTC))
    after = _key(dt.datetime(2026, 10, 6, 18, 31, tzinfo=dt.UTC))
    morning = _key(dt.datetime(2026, 10, 6, 1, 0, tzinfo=dt.UTC))
    assert before.endswith(":2026-10-06")
    assert after.endswith(":2026-10-07")
    assert morning == before, "UTC 01:00 and IST 23:59 are one Indian day"


def test_the_key_names_link_and_visitor() -> None:
    at = dt.datetime(2026, 10, 6, 6, 0, tzinfo=dt.UTC)
    assert _key(at) == f"visit_alert:{LINK}:0102:2026-10-06"
    assert _key(at, visitor=None) == f"visit_alert:{LINK}:ip:ff:2026-10-06"
    assert _key(at, visitor=b"\x03") != _key(at)


# ---------------------------------------------------------------------------
# Quiet hours (F7.AC9)
# ---------------------------------------------------------------------------


def _at_ist(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(
        2026, 10, 6, hour, minute, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))
    )


@pytest.mark.parametrize(
    ("start", "end", "hour", "minute", "quiet"),
    [
        ("23:00", "07:00", 23, 0, True),
        ("23:00", "07:00", 3, 0, True),
        ("23:00", "07:00", 6, 59, True),
        ("23:00", "07:00", 7, 0, False),
        ("23:00", "07:00", 22, 59, False),
        ("13:00", "14:00", 13, 30, True),
        ("13:00", "14:00", 14, 0, False),
        ("09:00", "09:00", 9, 0, False),
    ],
)
def test_the_window_may_cross_midnight(
    start: str, end: str, hour: int, minute: int, quiet: bool
) -> None:
    window = alerts.QuietHours(enabled=True, start=start, end=end, timezone=IST)
    assert window.active(_at_ist(hour, minute)) is quiet


def test_disabled_quiet_hours_are_never_active() -> None:
    assert not alerts.QuietHours(enabled=False).active(_at_ist(3))


def test_the_window_is_read_in_its_own_timezone() -> None:
    window = alerts.QuietHours(enabled=True, start="23:00", end="07:00", timezone="UTC")
    # 06:00 IST is 00:30 UTC, inside; 04:00 IST is 22:30 UTC, outside -- though both are
    # night in India. The window is the timezone it names, not the reader's.
    assert window.active(_at_ist(6))
    assert not window.active(_at_ist(4))


@pytest.mark.parametrize(
    ("value", "ok"),
    [("00:00", True), ("23:59", True), ("24:00", False), ("7:00", False), ("07:60", False)],
)
def test_times_are_hh_mm(value: str, ok: bool) -> None:
    assert alerts.QuietHours.valid_time(value) is ok


# ---------------------------------------------------------------------------
# Backoff with full jitter (F7.AC6)
# ---------------------------------------------------------------------------


def test_backoff_doubles_up_to_an_hour_with_jitter_beneath() -> None:
    rng = random.Random(7)  # noqa: S311 -- a seeded test, not a secret
    for attempts, ceiling in [(1, 30), (2, 60), (3, 120), (8, 3600), (20, 3600)]:
        samples = [alerts.backoff_seconds(attempts, rng) for _ in range(200)]
        assert min(samples) >= 0 and max(samples) <= ceiling
        assert max(samples) > ceiling * 0.8, "jitter spans the whole range"


# ---------------------------------------------------------------------------
# The message (F7.AC3, F7.AC4)
# ---------------------------------------------------------------------------

PAYLOAD = {
    "visit_id": "v1",
    "occurred_local": "2026-10-06 21:14 IST",
    "geofence_state": "inside",
    "geofence": {"name": "Karnataka"},
    "link": {"label": "Instagram bio", "slug": "ig-bio"},
    "location": {
        "strict": {"country_code": "IN", "admin1": "Karnataka", "city": None},
        "advisory": {"country_code": "IN", "admin1": "Karnataka", "city": "Bengaluru"},
        "confidence": {"country": 0.99, "admin1": 0.91, "city": 0.42},
    },
    "device": {"class": "mobile", "os": "Android 14", "browser": "Chrome 131"},
    "network": {"connection_class": "mobile", "asn": 45609, "asn_org": "Bharti Airtel"},
    "classification": "human",
    "bot_score": 4,
    "visit_url": "https://tracelet.example/visits/v1",
}


def test_an_inside_alert_names_the_geofence_and_everything_f7_ac4_lists() -> None:
    text = alerts.render(PAYLOAD, priority=HIGH)
    assert text.startswith("🔴 <b>Inside Karnataka</b>")
    for fragment in (
        "Instagram bio",
        "2026-10-06 21:14 IST",
        "Karnataka, IN (confirmed",
        "Android 14",
        "AS45609",
        "Bharti Airtel",
        "human (bot score 4)",
        'href="https://tracelet.example/visits/v1"',
    ):
        assert fragment in text, fragment


def test_an_undetermined_alert_says_so_and_shows_the_best_guess() -> None:
    payload = PAYLOAD | {
        "geofence_state": "undetermined",
        "geofence": None,
        "location": {
            "strict": {},
            "advisory": {"city": "Bengaluru", "admin1": "Karnataka", "country_code": "IN"},
            "confidence": {"city": 0.42},
        },
    }
    text = alerts.render(payload, priority=NORMAL)
    assert "Location not confirmed" in text
    assert "Bengaluru, Karnataka, IN (best guess, 42 % at city)" in text


def test_a_message_escapes_every_value() -> None:
    """A link label, an ISP name and a browser string are all someone else's text."""
    payload = PAYLOAD | {
        "link": {"label": "<script>x</script>", "slug": "a&b"},
        "network": {"asn_org": '<a href="evil">ISP</a>'},
        "geofence": {"name": "<b>"},
    }
    text = alerts.render(payload, priority=HIGH)
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert '<a href="evil">' not in text
    assert "Inside &lt;b&gt;" in text


def test_a_high_priority_outside_alert_is_marked() -> None:
    text = alerts.render(PAYLOAD | {"geofence_state": "outside"}, priority=HIGH)
    assert text.startswith("‼️")
