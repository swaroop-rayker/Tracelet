"""Offline databases without the databases: record parsing, GeoNames naming, the catalog.

CI has no geo databases, and must not need them. Everything here runs on literal records
and tiny fixture files; the installer's behaviour against real I/O is in
``tests/integration/test_geodb_installer.py``.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from tracelet.config import Settings
from tracelet.inference.geodb import geonames, readers
from tracelet.inference.geodb.catalog import BY_NAME
from tracelet.inference.geodb.profiles import _place
from tracelet.inference.types import Candidate, GeoLevel, InferenceSource

S = InferenceSource

# ---------------------------------------------------------------------------
# Records -> candidates
# ---------------------------------------------------------------------------

GEOLITE_BENGALURU = {
    "country": {"iso_code": "IN"},
    "subdivisions": [{"names": {"en": "Karnataka"}}],
    "city": {"names": {"en": "Bengaluru"}},
    "location": {"latitude": 12.9634, "longitude": 77.5855, "accuracy_radius": 20},
}


def test_a_city_record_becomes_a_city_candidate() -> None:
    (c,) = readers.city_candidate(S.GEOLITE2, GEOLITE_BENGALURU)
    assert (c.level, c.country_code, c.admin1, c.city) == (
        GeoLevel.CITY,
        "IN",
        "Karnataka",
        "Bengaluru",
    )
    assert c.raw_confidence == 1.0, "a 20 km radius is a tight fix"


def test_a_wide_accuracy_radius_lowers_confidence() -> None:
    record: dict[str, object] = dict(GEOLITE_BENGALURU)
    record["location"] = {"latitude": 12.9634, "longitude": 77.5855, "accuracy_radius": 1000}
    (c,) = readers.city_candidate(S.GEOLITE2, record)
    assert c.raw_confidence == 0.3


def test_a_country_only_record_claims_only_the_country() -> None:
    (c,) = readers.city_candidate(S.DBIP, {"country": {"iso_code": "in"}})
    assert (c.level, c.country_code, c.city) == (GeoLevel.COUNTRY, "IN", None)


@pytest.mark.parametrize("record", [None, {}, {"country": {}}])
def test_an_empty_record_yields_nothing(record: dict[str, object] | None) -> None:
    assert readers.city_candidate(S.DBIP, record) == []


def test_ipinfo_lite_knows_only_the_country() -> None:
    (c,) = readers.ipinfo_candidate({"country_code": "IN", "asn": "AS24560"})
    assert (c.source, c.level, c.country_code) == (S.IPINFO, GeoLevel.COUNTRY, "IN")


def test_ip2location_dashes_mean_unknown() -> None:
    result = SimpleNamespace(
        country_short="IN", region="Karnataka", city="-", latitude=0.0, longitude=0.0
    )
    (c,) = readers.ip2location_candidate(result)
    assert (c.level, c.admin1, c.city, c.lat) == (GeoLevel.ADMIN1, "Karnataka", None, None)
    assert readers.ip2location_candidate(SimpleNamespace(country_short="-")) == []


@pytest.mark.parametrize(
    ("name", "record", "expected"),
    [
        (
            "dbip-asn-lite",
            {"autonomous_system_number": 24560, "autonomous_system_organization": "Bharti Airtel"},
            (24560, "Bharti Airtel"),
        ),
        ("ipinfo-lite", {"asn": "AS55836", "as_name": "Reliance Jio"}, (55836, "Reliance Jio")),
        ("ipinfo-lite", {"asn": "", "as_name": None}, (None, None)),
        ("dbip-asn-lite", None, (None, None)),
    ],
)
def test_asn_records(
    name: str, record: dict[str, object] | None, expected: tuple[int | None, str | None]
) -> None:
    assert readers.asn_from_record(name, record) == expected


# ---------------------------------------------------------------------------
# GeoNames
# ---------------------------------------------------------------------------


def _row(
    gid: int,
    name: str,
    ascii_name: str,
    lat: float,
    lng: float,
    feature: str,
    cc: str,
    a1: str,
    pop: int,
) -> str:
    fields = [
        str(gid),
        name,
        ascii_name,
        "",
        str(lat),
        str(lng),
        "P",
        feature,
        cc,
        "",
        a1,
        "",
        "",
        "",
        str(pop),
    ]
    fields += ["", "", "Asia/Kolkata", "2024-01-01"]
    return "\t".join(fields)


@pytest.fixture
def gazetteer(tmp_path: Path) -> geonames.ReverseGeocoder:
    cities = tmp_path / "cities1000.txt"
    cities.write_text(
        "\n".join(
            [
                _row(
                    1275339, "Mumbai", "Mumbai", 19.07283, 72.88261, "PPLA", "IN", "16", 12_691_836
                ),
                _row(1272866, "Dhārāvi", "Dharavi", 19.05, 72.86667, "PPLX", "IN", "16", 700_000),
                _row(1254661, "Thāne", "Thane", 19.19704, 72.96355, "PPL", "IN", "16", 1_841_488),
                _row(
                    1277333,
                    "Bengaluru",
                    "Bengaluru",
                    12.97194,
                    77.59369,
                    "PPLA",
                    "IN",
                    "19",
                    8_443_675,
                ),
                _row(9999001, "Tinyville", "Tinyville", 12.99, 77.61, "PPL", "IN", "19", 1_200),
                _row(
                    2643743, "London", "London", 51.50853, -0.12574, "PPLC", "GB", "ENG", 8_961_989
                ),
                _row(9999002, "Hamlet", "Hamlet", 51.9, -0.5, "PPL", "GB", "ENG", 2_000),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    admin1 = tmp_path / "admin1CodesASCII.txt"
    admin1.write_text(
        "IN.16\tMahārāshtra\tMaharashtra\t1264418\nIN.19\tKarnataka\tKarnataka\t1267701\n"
        "GB.ENG\tEngland\tEngland\t6269131\n",
        encoding="utf-8",
    )
    return geonames.ReverseGeocoder(cities, admin1)


def test_small_places_abroad_are_left_out_to_save_memory(
    gazetteer: geonames.ReverseGeocoder,
) -> None:
    """All of India, 15 000+ elsewhere, and never a neighbourhood (PPLX)."""
    assert len(gazetteer) == 5  # Dharavi (PPLX) and Hamlet (2 000, abroad) dropped


def test_a_metro_covers_its_own_outskirts(gazetteer: geonames.ReverseGeocoder) -> None:
    """South Mumbai is ~13 km from Mumbai's single GeoNames point; it is still Mumbai,
    not whichever smaller place happens to be nearer."""
    near = gazetteer.nearest(18.958, 72.832, geonames.CITY_KM)
    assert near is not None and near.name == "Mumbai"


def test_names_are_ascii(gazetteer: geonames.ReverseGeocoder) -> None:
    near = gazetteer.nearest(19.197, 72.964, geonames.CITY_KM)
    assert near is not None and (near.name, near.admin1) == ("Thane", "Maharashtra")


def test_a_source_s_spelling_is_replaced_and_kept_as_evidence(
    gazetteer: geonames.ReverseGeocoder,
) -> None:
    c = Candidate(
        source=S.DBIP,
        level=GeoLevel.CITY,
        country_code="IN",
        admin1="Karnataka",
        city="Bangalore",
        lat=12.97,
        lng=77.59,
    )
    placed = gazetteer.place(c)
    assert placed.city == "Bengaluru"
    assert placed.evidence["source_names"]["city"] == "Bangalore"


def test_placing_never_deepens_a_network_claim(gazetteer: geonames.ReverseGeocoder) -> None:
    """A database's admin1-level claim keeps its level, even with coordinates."""
    c = Candidate(
        source=S.DBIP,
        level=GeoLevel.ADMIN1,
        country_code="IN",
        admin1="Karnataka",
        lat=12.97,
        lng=77.59,
    )
    assert gazetteer.place(c).city is None


def test_a_placement_across_a_border_is_left_alone(gazetteer: geonames.ReverseGeocoder) -> None:
    c = Candidate(
        source=S.DBIP, level=GeoLevel.CITY, country_code="FR", city="Somewhere", lat=51.5, lng=-0.12
    )
    assert gazetteer.place(c) == c


def test_a_gps_point_is_named(gazetteer: geonames.ReverseGeocoder) -> None:
    gps = Candidate(source=S.GPS, level=GeoLevel.POINT, lat=12.975, lng=77.60)
    named = gazetteer.place(gps)
    assert (named.level, named.country_code, named.admin1, named.city) == (
        GeoLevel.CITY,
        "IN",
        "Karnataka",
        "Bengaluru",
    )
    assert (named.lat, named.lng) == (12.975, 77.60), "the coordinate stays the visitor's own"


def test_a_gps_point_far_from_any_town_names_only_what_it_can(
    gazetteer: geonames.ReverseGeocoder,
) -> None:
    gps = Candidate(source=S.GPS, level=GeoLevel.POINT, lat=13.6, lng=77.59)  # ~70 km north
    named = gazetteer.place(gps)
    assert (named.level, named.admin1, named.city) == (GeoLevel.ADMIN1, "Karnataka", None)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_a_keyed_database_without_credentials_is_not_configured() -> None:
    # Explicitly empty: a developer's .env must not decide what this test sees.
    settings = Settings.model_validate(
        {
            "MAXMIND_ACCOUNT_ID": "",
            "MAXMIND_LICENSE_KEY": "",
            "IP2LOCATION_TOKEN": "",
            "IPINFO_TOKEN": "",
        }
    )
    for name in ("geolite2-city", "geolite2-asn", "ip2location-lite-db11", "ipinfo-lite"):
        assert BY_NAME[name].download(settings, dt.date(2026, 9, 29)) is None, name


def test_db_ip_falls_back_to_last_month() -> None:
    dl = BY_NAME["dbip-city-lite"].download(Settings(), dt.date(2026, 10, 2))
    assert dl is not None and dl.version == "2026-10"
    assert dl.fallback is not None and dl.fallback.version == "2026-09"
    assert dl.fallback.url.endswith("dbip-city-lite-2026-09.mmdb.gz")


def test_maxmind_uses_basic_auth_and_a_published_checksum() -> None:
    settings = Settings.model_validate(
        {"MAXMIND_ACCOUNT_ID": "123", "MAXMIND_LICENSE_KEY": "not-a-real-key"}
    )
    dl = BY_NAME["geolite2-city"].download(settings, dt.date(2026, 9, 29))
    assert dl is not None
    assert dl.auth == ("123", "not-a-real-key")
    assert "not-a-real-key" not in dl.url, "the key travels in the auth header, not the URL"
    assert dl.sha256_url is not None


def test_credentials_are_secret_in_settings() -> None:
    settings = Settings.model_validate({"IPINFO_TOKEN": "tok-123"})
    assert isinstance(settings.ipinfo_token, SecretStr)
    assert "tok-123" not in repr(settings)


def test_a_country_only_claim_is_never_given_a_state(gazetteer: geonames.ReverseGeocoder) -> None:
    """ERRORS.md E30: a country-level record's coordinates are the country's centroid;
    naming that point would invent a state vote the source never cast."""
    c = Candidate(
        source=S.GEOLITE2, level=GeoLevel.COUNTRY, country_code="IN", lat=12.97, lng=77.59
    )
    assert gazetteer.place(c) == c


def test_placing_renames_only_what_was_claimed(gazetteer: geonames.ReverseGeocoder) -> None:
    c = Candidate(
        source=S.DBIP,
        level=GeoLevel.CITY,
        country_code="IN",
        city="Bangalore",
        lat=12.97,
        lng=77.59,
    )
    placed = gazetteer.place(c)
    assert (placed.admin1, placed.city) == (None, "Bengaluru")


def test_a_profile_ignores_records_without_a_city() -> None:
    """ERRORS.md E30, in asn_profiles."""
    centroid = {"country": {"iso_code": "IN"}, "location": {"latitude": 22.0, "longitude": 79.0}}
    assert _place(centroid) is None
    assert _place(GEOLITE_BENGALURU) is not None


def test_a_download_never_shows_its_credentials() -> None:
    """ERRORS.md E31: a repr lands in tracebacks and test output."""
    settings = Settings.model_validate(
        {"MAXMIND_ACCOUNT_ID": "123", "MAXMIND_LICENSE_KEY": "sekret-key", "IPINFO_TOKEN": "tok-9"}
    )
    today = dt.date(2026, 9, 29)
    for name in ("geolite2-city", "ipinfo-lite"):
        dl = BY_NAME[name].download(settings, today)
        assert dl is not None
        assert "sekret-key" not in repr(dl) and "tok-9" not in repr(dl), name
