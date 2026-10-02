"""Analytics domain logic that needs no database (ADR-0016)."""

from __future__ import annotations

import datetime as dt
import zoneinfo

import pytest

from tracelet.analytics.filters import CELL_FIELDS, DIM_FIELDS, VisitFilter, resolve_window
from tracelet.analytics.rollup import _runs
from tracelet.analytics.router import _km
from tracelet.capture.models import Classification, DeviceClass
from tracelet.capture.visits_router import _csv_cell
from tracelet.errors import ValidationFailed

IST = zoneinfo.ZoneInfo("Asia/Kolkata")
NOON_UTC = dt.datetime(2026, 10, 2, 6, 30, tzinfo=dt.UTC)  # 12:00 in India


def test_the_default_window_is_thirty_local_days_including_today() -> None:
    window = resolve_window(VisitFilter(), "Asia/Kolkata", now=NOON_UTC)
    assert window.start == dt.datetime(2026, 9, 3, tzinfo=IST)
    assert window.end == dt.datetime(2026, 10, 3, tzinfo=IST)
    assert window.aligned_to_days()
    assert len(window.days()) == 30


def test_a_window_off_the_day_boundary_is_not_day_aligned() -> None:
    """A UTC midnight is 05:30 in India: not a bucket boundary there."""
    f = VisitFilter(
        from_=dt.datetime(2026, 10, 1, tzinfo=dt.UTC), to=dt.datetime(2026, 10, 2, tzinfo=dt.UTC)
    )
    window = resolve_window(f, "Asia/Kolkata")
    assert not window.aligned_to_days()
    assert not window.aligned_to_hours(), "05:30 is not on the hour either"
    assert window.days() == [dt.date(2026, 10, 1), dt.date(2026, 10, 2)]


def test_the_previous_window_abuts_this_one() -> None:
    window = resolve_window(VisitFilter(), "Asia/Kolkata", now=NOON_UTC)
    previous = window.previous()
    assert previous.end == window.start
    assert previous.end - previous.start == window.end - window.start


@pytest.mark.parametrize(
    "f",
    [
        VisitFilter(
            from_=dt.datetime(2026, 10, 2, tzinfo=dt.UTC),
            to=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
        ),
        VisitFilter(
            from_=dt.datetime(2020, 1, 1, tzinfo=dt.UTC), to=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        ),
    ],
)
def test_impossible_windows_are_refused(f: VisitFilter) -> None:
    with pytest.raises(ValidationFailed):
        resolve_window(f, "Asia/Kolkata")


def test_only_rollup_dimensions_may_be_served_from_rollups() -> None:
    """A new filter field is raw-only unless someone decides otherwise (ADR-0016)."""
    assert VisitFilter(classification=(Classification.HUMAN,)).set_fields() <= DIM_FIELDS
    assert VisitFilter(device_class=(DeviceClass.MOBILE,)).set_fields() <= CELL_FIELDS
    assert not VisitFilter(device_class=(DeviceClass.MOBILE,)).set_fields() <= DIM_FIELDS
    assert not VisitFilter(asn=24560).set_fields() <= CELL_FIELDS
    assert not VisitFilter(has_gps=False).set_fields() <= CELL_FIELDS
    assert VisitFilter(include_automated=True).set_fields() == frozenset()


def test_the_default_classifications_exclude_automation() -> None:
    kept = VisitFilter().classifications()
    assert kept is not None
    assert Classification.HUMAN in kept and Classification.UNKNOWN in kept
    assert Classification.BOT not in kept and Classification.CRAWLER not in kept
    assert VisitFilter(include_automated=True).classifications() is None
    assert VisitFilter().without_classification().classifications() is None


def test_days_group_into_contiguous_runs() -> None:
    d = dt.date(2026, 10, 1)
    days = [d, d + dt.timedelta(days=1), d + dt.timedelta(days=5), d, d - dt.timedelta(days=1)]
    assert _runs(days) == [
        (d - dt.timedelta(days=1), d + dt.timedelta(days=1)),
        (d + dt.timedelta(days=5), d + dt.timedelta(days=5)),
    ]


@pytest.mark.parametrize(
    ("value", "cell"),
    [
        ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("Bharti Airtel", "Bharti Airtel"),
        (None, ""),
        (True, "true"),
        (0.5, "0.5"),
    ],
)
def test_csv_cells_cannot_be_formulas(value: object, cell: str) -> None:
    assert _csv_cell(value) == cell


def test_haversine_bengaluru_to_mumbai() -> None:
    assert _km((12.9716, 77.5946), (19.0760, 72.8777)) == pytest.approx(845, rel=0.02)
