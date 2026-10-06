"""The region catalogue: keys spelled as strict states are (ADR-0020 decision 2)."""

from __future__ import annotations

import pytest

from tracelet.geofence import regions


def test_the_catalogue_is_built_from_the_admin1_table() -> None:
    catalog = regions.build({"IN.19": "Karnataka", "IN.16": "Maharashtra", "NP.01": "Bagmati"})
    assert catalog.countries == ("IN", "NP")
    assert [d.key for d in catalog.divisions] == ["IN|Karnataka", "IN|Maharashtra", "NP|Bagmati"]
    assert catalog.divisions[0].code == "IN.19"
    assert catalog.unknown(["IN", "IN|Karnataka", "IN|Goa", "US"]) == ["IN|Goa", "US"]


def test_unusable_rows_are_skipped_and_a_repeated_name_is_one_key() -> None:
    catalog = regions.build({"IN.19": "Karnataka", "IN.99": "Karnataka", "XYZ.1": "No", "IN.2": ""})
    assert [d.code for d in catalog.divisions] == ["IN.19"]
    assert catalog.countries == ("IN",)


@pytest.mark.parametrize(
    ("key", "ok"),
    [
        ("IN", True),
        ("IN|Karnataka", True),
        ("IN|Jammu and Kashmir", True),
        ("in", False),
        ("IND", False),
        ("IN|", False),
        ("IN| ", False),
        ("ÏN", False),
    ],
)
def test_a_key_is_an_iso_country_and_an_optional_name(key: str, ok: bool) -> None:
    assert regions.well_formed(key) is ok
