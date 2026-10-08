"""Scoring a replay: intervals, what "right" means, populations, per source (ADR-0024).

Pure functions, so each rule is exercised alone with hand-built cases.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tracelet.accuracy.metrics import Report, score, wilson
from tracelet.accuracy.replay import Case, Truth, decide, network_facts, right
from tracelet.inference.config import DEFAULT_CONFIG
from tracelet.inference.types import AsnInfo, Candidate, GeoLevel, InferenceSource

S = InferenceSource
L = GeoLevel
INDIA = frozenset({"IN"})


def _db(
    source: InferenceSource, admin1: str = "Karnataka", city: str | None = "Bengaluru"
) -> Candidate:
    return Candidate(
        source=source,
        level=L.CITY if city else L.ADMIN1,
        country_code="IN",
        admin1=admin1,
        city=city,
    )


def _gps(admin1: str = "Karnataka", city: str = "Bengaluru") -> Candidate:
    return Candidate(
        source=S.GPS, level=L.CITY, country_code="IN", admin1=admin1, city=city, raw_confidence=0.99
    )


def _case(*candidates: Candidate, truth: Truth | None = None, consented: bool = False) -> Case:
    return Case(
        truth=truth or Truth("IN", "Karnataka", None, "Bengaluru"),
        consented=consented,
        path="direct",
        asn=AsnInfo(),
        tz_countries=INDIA,
        candidates=candidates,
    )


def _score(*cases: Case) -> Report:
    return score(
        cases,
        DEFAULT_CONFIG,
        settings_version=1,
        inference_version="m3.4+s1",
        classifier_version="m4.1+s1",
    )


# --- Wilson ------------------------------------------------------------------


def test_wilson_matches_published_values() -> None:
    # Reference values from the Wilson score formula, z = 1.96.
    half = wilson(5, 10)
    assert half.value == 0.5
    assert half.ci95 == pytest.approx((0.2366, 0.7634), abs=1e-4)
    every = wilson(31, 31)
    assert every.value == 1.0
    # Never a zero-width interval at k = n: 31 of 31 is not certainty (RISKS R9).
    assert every.ci95 is not None
    assert every.ci95[0] == pytest.approx(0.8897, abs=1e-4)
    assert every.ci95[1] == 1.0


def test_wilson_with_nothing_measured_is_null_not_zero() -> None:
    nothing = wilson(0, 0)
    assert (nothing.k, nothing.n, nothing.value, nothing.ci95) == (0, 0, None, None)


def test_wilson_interval_stays_inside_zero_and_one() -> None:
    none = wilson(0, 3)
    assert none.ci95 is not None
    assert none.ci95[0] == 0.0
    assert 0.0 < none.ci95[1] < 1.0


# --- what "right" means --------------------------------------------------------


def test_a_right_city_in_the_wrong_state_is_wrong() -> None:
    truth = Truth("IN", "Maharashtra", None, "Aurangabad")
    stated = {L.COUNTRY: "IN", L.ADMIN1: "Bihar", L.ADMIN2: None, L.CITY: "Aurangabad"}
    assert not right(truth, stated, L.CITY)


def test_names_compare_as_the_engine_votes_case_and_punctuation_insensitive() -> None:
    truth = Truth("in", "Tamil Nadu", None, "Chennai")
    stated = {L.COUNTRY: "IN", L.ADMIN1: "tamil-nadu", L.ADMIN2: None, L.CITY: " chennai "}
    assert right(truth, stated, L.COUNTRY)
    assert right(truth, stated, L.ADMIN1)
    assert right(truth, stated, L.CITY)


def test_a_level_the_answer_skipped_does_not_contradict() -> None:
    truth = Truth("IN", "Karnataka", "Bengaluru Urban", "Bengaluru")
    stated = {L.COUNTRY: "IN", L.ADMIN1: "Karnataka", L.ADMIN2: None, L.CITY: "Bengaluru"}
    assert right(truth, stated, L.CITY)
    assert not right(truth, stated, L.ADMIN2)  # nothing stated is not right


# --- populations ---------------------------------------------------------------


def test_network_only_drops_gps_and_consented_keeps_it() -> None:
    # GPS says Bengaluru; the network alone says Mysuru. Truth: Bengaluru.
    case = _case(
        _gps(),
        _db(S.GEOLITE2, city="Mysuru"),
        _db(S.DBIP, city="Mysuru"),
        consented=True,
    )
    assert decide(case, DEFAULT_CONFIG).advisory[L.CITY] == "Bengaluru"
    assert decide(case, DEFAULT_CONFIG, without_gps=True).advisory[L.CITY] == "Mysuru"
    report = _score(case)
    consented = report.population("consented").levels[3]
    network = report.population("network_only").levels[3]
    assert consented.advisory_accuracy.k == 1
    assert network.advisory_accuracy.k == 0
    assert report.population("non_consented").label_count == 0


def test_precision_counts_only_emissions_and_coverage_counts_labels() -> None:
    agreeing = _case(_db(S.GEOLITE2), _db(S.DBIP), _db(S.IP2LOCATION))
    lone = _case(_db(S.IP2LOCATION, admin1="Haryana", city="Faridabad"))
    report = _score(agreeing, lone)
    admin1 = report.population("network_only").levels[1]
    assert admin1.label_count == 2
    assert (admin1.strict_precision.k, admin1.strict_precision.n) == (1, 1)
    assert (admin1.strict_coverage.k, admin1.strict_coverage.n) == (1, 2)
    # The lone database is the best guess, and wrong (B1).
    assert (admin1.advisory_accuracy.k, admin1.advisory_accuracy.n) == (1, 2)


def test_a_truth_that_stops_at_the_state_is_not_scored_at_city() -> None:
    case = _case(_db(S.GEOLITE2), _db(S.DBIP), truth=Truth("IN", "Karnataka"))
    city = _score(case).population("network_only").levels[3]
    assert city.label_count == 0
    assert city.advisory_accuracy.value is None


# --- per source (F4.AC17) ------------------------------------------------------


def test_per_source_counts_one_claim_per_label_and_says_who_was_wrong() -> None:
    cases = [
        _case(_db(S.GEOLITE2), _db(S.DBIP, city="Mysuru"), _db(S.DBIP, city="Mysuru")),
        _case(_db(S.GEOLITE2), _db(S.DBIP)),
    ]
    sources = {s.source: s for s in _score(*cases).sources}
    geolite_city = next(lv for lv in sources[S.GEOLITE2].levels if lv.level is L.CITY)
    dbip_city = next(lv for lv in sources[S.DBIP].levels if lv.level is L.CITY)
    assert (geolite_city.claims, geolite_city.correct.k) == (2, 2)
    assert (dbip_city.claims, dbip_city.correct.k) == (2, 1)


def test_the_matrix_counts_labels_by_network_connection_and_vpn() -> None:
    plain = _case(_db(S.GEOLITE2))
    jio = replace(plain, network="jio", connection_kind="mobile_data", vpn_used=False)
    jio_vpn = replace(jio, vpn_used=True)
    report = _score(jio, jio, jio_vpn, plain)
    assert [(m.network, m.connection_kind, m.vpn_used, m.count) for m in report.matrix] == [
        ("jio", "mobile_data", False, 2),
        ("jio", "mobile_data", True, 1),
        (None, None, None, 1),
    ]


# --- network facts --------------------------------------------------------------


def test_network_facts_overlay_the_profile_and_keep_tor() -> None:
    profile = AsnInfo(asn=64500, modal_city="Delhi", modal_admin1="Delhi", modal_share=0.4)
    facts = network_facts(64500, "Example Broadband", is_tor=True, profile=profile)
    assert facts.is_tor
    assert facts.modal_share == 0.4
    assert network_facts(None, None, is_tor=False, profile=None) == AsnInfo()
