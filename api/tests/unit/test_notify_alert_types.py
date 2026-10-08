"""The M7.5 alert types, the pure part: when each is due, what each says, and how a damaged
setting is read (F7.AC10-F7.AC15, SPEC section 11 row 27)."""

from __future__ import annotations

import datetime as dt

import pytest

from tracelet.geofence.models import NotifyPriority
from tracelet.notify import alerts, notes
from tracelet.notify import settings as notify_settings

IST = "Asia/Kolkata"
NORMAL = NotifyPriority.NORMAL


def _at_ist(day: int, hour: int, minute: int = 0) -> dt.datetime:
    """An IST wall-clock time on 2026-10-``day``, as the UTC instant a job sees."""
    local = dt.datetime(2026, 10, day, hour, minute, tzinfo=dt.timezone(dt.timedelta(hours=5.5)))
    return local.astimezone(dt.UTC)


# ---------------------------------------------------------------------------
# Defaults: every type off (row 27)
# ---------------------------------------------------------------------------


def test_every_type_is_off_by_default_with_the_approved_values() -> None:
    types = alerts.AlertTypes()
    assert not any(
        (
            types.digest.enabled,
            types.spike.enabled,
            types.new_place.enabled,
            types.returning.enabled,
        )
    )
    assert (types.digest.at, types.spike.floor, types.spike.k, types.returning.after_days) == (
        "09:00",
        10,
        3.0,
        7,
    )


# ---------------------------------------------------------------------------
# The digest is due once the set time has passed, for yesterday, in the reporting zone
# ---------------------------------------------------------------------------


def test_the_digest_is_due_for_yesterday_after_its_time() -> None:
    digest = alerts.DigestType(enabled=True, at="09:00")
    assert digest.due(_at_ist(8, 8, 59), IST) is None
    assert digest.due(_at_ist(8, 9, 0), IST) == dt.date(2026, 10, 7)
    assert digest.due(_at_ist(8, 23, 59), IST) == dt.date(2026, 10, 7)


def test_the_digest_day_is_cut_in_the_reporting_zone_not_utc() -> None:
    """00:30 IST on the 8th is still the 7th in UTC: the day summarised is the 7th IST."""
    digest = alerts.DigestType(enabled=True, at="00:15")
    assert digest.due(_at_ist(8, 0, 30), IST) == dt.date(2026, 10, 7)


def test_a_switched_off_digest_is_never_due() -> None:
    assert alerts.DigestType(enabled=False).due(_at_ist(8, 12), IST) is None


# ---------------------------------------------------------------------------
# The spike: the floor AND k times the median
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "usual", "fires"),
    [
        (10, 0.0, True),  # at the floor, from nothing
        (9, 0.0, False),  # under the floor, however unusual
        (30, 10.0, False),  # 3x is not *more than* 3x
        (31, 10.0, True),
        (500, 200.0, False),  # busy, but busy is normal for it
    ],
)
def test_a_spike_needs_the_floor_and_more_than_k_times_usual(
    count: int, usual: float, fires: bool
) -> None:
    assert alerts.SpikeType(enabled=True, floor=10, k=3.0).fires(count, usual) is fires


def test_a_switched_off_spike_never_fires() -> None:
    assert not alerts.SpikeType(enabled=False).fires(1000, 0.0)


@pytest.mark.parametrize(
    ("values", "expected"),
    [([], 0.0), ([4], 4.0), ([0, 0, 0, 1, 9, 9, 9], 1.0), ([1, 2, 3, 4], 2.5)],
)
def test_the_median_is_plain_python(values: list[int], expected: float) -> None:
    assert alerts.median(values) == expected


# ---------------------------------------------------------------------------
# Places: the geofence region keys, broadest first, strict only
# ---------------------------------------------------------------------------


def test_region_keys_and_their_labels() -> None:
    assert notes.region_keys("IN", "Karnataka") == ["IN", "IN|Karnataka"]
    assert notes.region_keys("IN", None) == ["IN"]
    assert notes.region_keys(None, "Karnataka") == []
    assert alerts.region_label("IN|Karnataka") == "Karnataka, IN"
    assert alerts.region_label("IN") == "IN"


# ---------------------------------------------------------------------------
# The messages
# ---------------------------------------------------------------------------

VISIT = {
    "visit_id": "00000000-0000-0000-0000-000000000001",
    "occurred_local": "2026-10-08 10:00 IST",
    "geofence_state": None,
    "link": {"label": "Reel", "slug": "reel"},
    "location": {
        "strict": {"country_code": "IN", "admin1": "Karnataka", "city": None},
        "advisory": {"country_code": "IN", "admin1": "Karnataka", "city": "Bengaluru"},
        "confidence": {"country": 0.99, "admin1": 0.9, "city": 0.4},
    },
    "device": {"class": "mobile"},
    "network": {},
    "classification": "human",
    "visit_url": "https://t.example/visits/1",
}


def test_notes_are_lines_on_the_visit_alert() -> None:
    payload = VISIT | {
        "notes": [
            {"kind": "new_place", "region_key": "IN|Karnataka"},
            {"kind": "returning", "days": 12},
        ]
    }
    text = alerts.render_message("telegram.visit_alert", payload, priority=NORMAL)
    assert "First visit from Karnataka, IN on this link" in text
    assert "Back after 12 days" in text
    assert text.index("Back after") < text.index("Open the visit")


def test_a_note_sent_alone_names_the_link_and_the_strict_place() -> None:
    text = alerts.render_message(
        "telegram.new_place",
        VISIT | {"note": {"kind": "new_place", "region_key": "IN|Karnataka"}},
        priority=NORMAL,
    )
    assert text.startswith("<b>📍 First visit from Karnataka, IN on this link</b>")
    assert "Reel (/reel)" in text and "Karnataka, IN (confirmed)" in text
    one_day = alerts.render_message(
        "telegram.returning", VISIT | {"note": {"kind": "returning", "days": 1}}, priority=NORMAL
    )
    assert "Back after 1 day</b>" in one_day


def test_the_digest_marks_states_as_a_best_guess_and_escapes_labels() -> None:
    text = alerts.render_message(
        "telegram.digest",
        {
            "day": "2026-10-07",
            "label": "Wed 7 Oct",
            "visits": 40,
            "human": 30,
            "states": [{"key": "IN|Karnataka", "count": 12}, {"key": "IN|Goa", "count": 3}],
            "links": [{"label": "<b>Reel</b>", "slug": "reel", "count": 20}],
            "dead": 2,
            "dashboard_url": "https://t.example/",
        },
        priority=NORMAL,
    )
    assert "Visits</b> 40, of which 30 people (75 %)" in text
    assert "Top states</b> (best guess) Karnataka, IN 12 · Goa, IN 3" in text
    assert "&lt;b&gt;Reel&lt;/b&gt; 20" in text and "<b>Reel</b>" not in text
    assert "2 alerts dead-lettered" in text


def test_an_empty_day_says_so() -> None:
    text = alerts.render_digest({"day": "2026-10-07", "visits": 0, "human": 0, "dead": 0})
    assert "No visits." in text and "dead-lettered" not in text


def test_the_spike_names_no_place() -> None:
    text = alerts.render_message(
        "telegram.spike",
        {"link": {"label": "Reel", "slug": "reel"}, "count": 31, "usual": 2.5},
        priority=NORMAL,
    )
    assert "Busy link</b>: Reel (/reel)" in text
    assert "31 people" in text and "Usually</b> 2.5" in text
    assert "Where" not in text


# ---------------------------------------------------------------------------
# A damaged setting is read leniently, and only ever quieter
# ---------------------------------------------------------------------------


def test_a_damaged_setting_falls_back_to_defaults_and_off() -> None:
    types = notify_settings._alert_types(
        {
            "digest": {"enabled": "yes", "at": "9am"},
            "spike": {"enabled": True, "floor": 0, "k": "3"},
            "new_place": [],
            "returning": {"enabled": True, "after_days": 365},
        }
    )
    assert types.digest == alerts.DigestType()  # "yes" is not True; "9am" is not HH:MM
    assert types.spike == alerts.SpikeType(enabled=True)  # bad floor and k take defaults
    assert types.new_place == alerts.NewPlaceType()
    assert types.returning == alerts.ReturningType(enabled=True, after_days=7)
    assert notify_settings._alert_types(None) == alerts.AlertTypes()
