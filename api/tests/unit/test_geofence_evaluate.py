"""Geofence evaluation, the pure part: ADR-0020 decision 4's table, row by row, and
decision 5's combination. The SQL half (ST_Covers) is in the integration suite."""

from __future__ import annotations

import uuid

import pytest

from tracelet.capture.models import GeofenceState
from tracelet.geofence.evaluate import (
    INSIDE,
    OUTSIDE,
    Reason,
    RegionKey,
    Result,
    StrictPlace,
    applies,
    combine,
    region_key_result,
    region_result,
    shape_result,
)

IN_ = GeofenceState.INSIDE
OUT = GeofenceState.OUTSIDE
UND = GeofenceState.UNDETERMINED


def place(country: str | None, admin1: str | None = None, *, point: bool = False) -> StrictPlace:
    return StrictPlace(country_code=country, admin1=admin1, has_geopoint=point)


def test_a_key_is_a_country_or_a_qualified_division() -> None:
    assert RegionKey.parse("IN") == RegionKey("IN", None)
    assert RegionKey.parse("IN|Karnataka") == RegionKey("IN", "Karnataka")
    # Only the first separator splits: the rest is the division's name as spelled.
    assert RegionKey.parse("IN|A|B") == RegionKey("IN", "A|B")


@pytest.mark.parametrize(
    ("key", "where", "expected", "reason"),
    [
        # A country key
        ("IN", place("IN"), IN_, None),
        ("IN", place("IN", "Goa"), IN_, None),
        ("IN", place("US"), OUT, None),
        ("IN", place(None), UND, Reason.NO_STRICT_COUNTRY),
        # A division key
        ("IN|Karnataka", place("IN", "Karnataka"), IN_, None),
        ("IN|Karnataka", place("IN", "Maharashtra"), OUT, None),
        ("IN|Karnataka", place("US", "Karnataka"), OUT, None),
        ("IN|Karnataka", place("US"), OUT, None),
        # India contains Karnataka, so a strict India alone cannot rule it out.
        ("IN|Karnataka", place("IN"), UND, Reason.NO_STRICT_ADMIN1),
        ("IN|Karnataka", place(None), UND, Reason.NO_STRICT_COUNTRY),
    ],
)
def test_one_key_follows_the_adr_table(
    key: str, where: StrictPlace, expected: GeofenceState, reason: Reason | None
) -> None:
    assert region_key_result(RegionKey.parse(key), where) == Result(expected, reason)


def test_an_admin1_without_a_country_is_never_outside() -> None:
    """The engine states levels top-down, but if it ever did not, "outside" would break
    ck_visits_outside_needs_strict. A stated state alone decides nothing."""
    assert region_key_result(RegionKey.parse("IN|Karnataka"), place(None, "Goa")).state is UND


def test_a_region_is_inside_if_any_key_is() -> None:
    keys = ["IN|Karnataka", "IN|Goa"]
    assert region_result(keys, place("IN", "Goa")) == INSIDE


def test_a_region_is_outside_only_if_every_key_is() -> None:
    keys = ["IN|Karnataka", "IN|Goa"]
    assert region_result(keys, place("IN", "Kerala")) == OUTSIDE
    assert region_result(keys, place("US")) == OUTSIDE


def test_a_region_with_an_undecidable_key_is_undetermined() -> None:
    # No strict country decides nothing; a strict India rules out "US" but not Karnataka.
    assert region_result(["IN|Karnataka", "NP"], place(None)).state is UND
    assert region_result(["US", "IN|Karnataka"], place("IN")).state is UND


def test_a_region_never_reads_advisory_fields() -> None:
    """StrictPlace has no advisory fields to read: the type is the guarantee."""
    assert set(StrictPlace.__slots__) == {"country_code", "admin1", "has_geopoint"}


@pytest.mark.parametrize(
    ("covered", "point", "expected"),
    [(True, True, IN_), (False, True, OUT), (False, False, UND)],
)
def test_a_shape_needs_a_geopoint(covered: bool, point: bool, expected: GeofenceState) -> None:
    result = shape_result(covered, place("IN", "Karnataka", point=point))
    assert result.state is expected
    assert result.reason == (Reason.NO_GEOPOINT if expected is UND else None)


def test_a_strict_state_does_not_decide_a_polygon() -> None:
    """A strict Karnataka is not a point; a polygon inside Karnataka stays undetermined."""
    assert shape_result(False, place("IN", "Karnataka")).state is UND


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ([], None),
        ([OUT], OUT),
        ([OUT, OUT], OUT),
        ([OUT, UND], UND),
        ([UND, IN_], IN_),
        ([OUT, IN_, UND], IN_),
    ],
)
def test_the_visit_state_never_rounds_an_abstention_down(
    states: list[GeofenceState], expected: GeofenceState | None
) -> None:
    assert combine([Result(s) for s in states]) is expected


def test_a_geofence_applies_to_its_links_or_to_all() -> None:
    link, other = uuid.uuid4(), uuid.uuid4()
    assert applies(None, link)
    assert applies((link,), link)
    assert not applies((other,), link)
