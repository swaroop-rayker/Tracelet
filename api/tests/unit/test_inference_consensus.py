"""Weighted consensus, suppression and dual output (F4.AC9-F4.AC12, ADR-0005).

Each test builds candidates by hand and asserts on the decision, because consensus is a
pure function and the rules are easiest to trust when each one is exercised alone.
"""

from __future__ import annotations

from typing import Literal

import pytest

from tracelet.inference.config import DEFAULT_CONFIG, RegistryArtifact
from tracelet.inference.consensus import Decision, decide
from tracelet.inference.types import (
    AsnInfo,
    Candidate,
    GeoLevel,
    InferenceSource,
    SuppressedReason,
)

S = InferenceSource
L = GeoLevel

INDIA = frozenset({"IN"})


def _db(
    source: InferenceSource,
    *,
    city: str = "Bengaluru",
    admin1: str = "Karnataka",
    country: str = "IN",
    lat: float = 12.97,
    lng: float = 77.59,
) -> Candidate:
    return Candidate(
        source=source,
        level=L.CITY,
        country_code=country,
        admin1=admin1,
        city=city,
        lat=lat,
        lng=lng,
    )


def _four_databases(
    *,
    city: str = "Bengaluru",
    admin1: str = "Karnataka",
    country: str = "IN",
    lat: float = 12.97,
    lng: float = 77.59,
) -> list[Candidate]:
    """GeoLite2, IP2Location and DB-IP at city depth, IPinfo Lite at country depth."""
    cities = [
        _db(s, city=city, admin1=admin1, country=country, lat=lat, lng=lng)
        for s in (S.GEOLITE2, S.IP2LOCATION, S.DBIP)
    ]
    return [*cities, Candidate(source=S.IPINFO, level=L.COUNTRY, country_code=country)]


def _faridabad() -> list[Candidate]:
    return _four_databases(city="Faridabad", admin1="Haryana", lat=28.41, lng=77.31)


NO_ASN = AsnInfo()


def _decide(
    candidates: list[Candidate],
    *,
    asn: AsnInfo = NO_ASN,
    tz: frozenset[str] | None = INDIA,
    collapse_to: Literal["admin1", "country"] = "admin1",
) -> Decision:
    config = DEFAULT_CONFIG.model_copy(
        update={"registry_artifact": RegistryArtifact(collapse_to=collapse_to)}
    )
    return decide(candidates, asn=asn, tz_countries=tz, config=config)


def _strict(d: Decision) -> dict[str, str | None]:
    return {level.value: r.strict for level, r in d.levels.items()}


# ---------------------------------------------------------------------------
# The ordinary case: databases alone
# ---------------------------------------------------------------------------


def test_databases_alone_settle_country_and_state_but_not_the_city() -> None:
    """SPEC section 11 row 9: a database-only visit abstains at city, by design."""
    d = _decide(_four_databases())

    assert _strict(d) == {"country": "IN", "admin1": "Karnataka", "admin2": None, "city": None}
    assert d.levels[L.CITY].advisory == "Bengaluru"
    assert d.levels[L.CITY].abstain_reason == "below_threshold"
    assert d.levels[L.ADMIN2].abstain_reason == "no_candidates"


def test_a_strict_state_alone_yields_no_strict_point() -> None:
    """A visitor known only to be in Karnataka must not become a coordinate in
    Bengaluru -- geofencing would act on it (CLAUDE.md invariant 5)."""
    d = _decide(_four_databases())
    assert d.levels[L.ADMIN1].strict == "Karnataka"
    assert d.strict_point is None
    assert d.advisory_point == (12.97, 77.59), "the guess is still visible as a guess"


def test_four_databases_count_for_little_more_than_one() -> None:
    """Correlated sources: agreement inside the registry-derived family is discounted."""
    one = _decide([_db(S.GEOLITE2)]).levels[L.CITY].confidence
    three = _decide([_db(s) for s in (S.GEOLITE2, S.IP2LOCATION, S.DBIP)]).levels[L.CITY].confidence
    assert one is not None and three is not None
    assert three - one < 0.15


def test_an_independent_rdns_code_lifts_the_city_to_strict() -> None:
    rdns = Candidate(
        source=S.RDNS,
        level=L.CITY,
        country_code="IN",
        admin1="Karnataka",
        city="Bengaluru",
        evidence={"matched_code": "blr"},
    )
    d = _decide([*_four_databases(), rdns])

    assert d.levels[L.CITY].strict == "Bengaluru"
    assert d.primary_source is S.RDNS


# ---------------------------------------------------------------------------
# Rule (a): registry artifact -- the B1 fix
# ---------------------------------------------------------------------------

AIRTEL_BROADBAND = AsnInfo(
    asn=24560, org="Bharti Airtel", modal_city="Faridabad", modal_admin1="Haryana", modal_share=0.62
)


def test_an_uncorroborated_artifact_city_collapses_with_its_reason() -> None:
    d = _decide(_faridabad(), asn=AIRTEL_BROADBAND)

    assert d.levels[L.CITY].strict is None
    assert d.levels[L.CITY].abstain_reason == "registry_artifact"
    assert d.levels[L.CITY].advisory == "Faridabad", "advisory still shows what the engine thought"
    rejected = [v for v in d.verdicts if v.suppressed_reason is SuppressedReason.REGISTRY_ARTIFACT]
    assert len(rejected) == 3, "every database that proposed the artifact says why it lost"


def test_the_spec_collapse_still_emits_the_artifact_state() -> None:
    """RISKS R22. F4.AC12(a) as written collapses to admin1 -- and in B1's own example
    the admin1 is the registry's, not the visitor's. This test pins the behaviour the
    SPEC asks for, so the owner's decision on R22 changes a test, not a surprise."""
    d = _decide(_faridabad(), asn=AIRTEL_BROADBAND, collapse_to="admin1")
    assert d.levels[L.ADMIN1].strict == "Haryana"


def test_collapsing_to_country_voids_the_artifact_state_too() -> None:
    d = _decide(_faridabad(), asn=AIRTEL_BROADBAND, collapse_to="country")

    assert _strict(d) == {"country": "IN", "admin1": None, "admin2": None, "city": None}
    assert d.levels[L.ADMIN1].abstain_reason == "registry_artifact"


def test_rdns_corroboration_overrides_the_artifact_rule() -> None:
    rdns = Candidate(
        source=S.RDNS, level=L.CITY, country_code="IN", admin1="Haryana", city="Faridabad"
    )
    d = _decide([*_faridabad(), rdns], asn=AIRTEL_BROADBAND)
    assert d.levels[L.CITY].strict == "Faridabad"


def test_a_low_modal_share_is_not_an_artifact() -> None:
    spread = AsnInfo(asn=1, modal_city="Faridabad", modal_share=0.05)
    d = _decide(_faridabad(), asn=spread)
    assert d.levels[L.CITY].abstain_reason == "below_threshold"


# ---------------------------------------------------------------------------
# Rule (b): mobile and CGNAT
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("asn", [AsnInfo(asn=55836, is_mobile=True), AsnInfo(is_cgnat=True)])
def test_a_mobile_network_never_yields_a_city(asn: AsnInfo) -> None:
    rdns = Candidate(
        source=S.RDNS, level=L.CITY, country_code="IN", admin1="Karnataka", city="Bengaluru"
    )
    d = _decide([*_four_databases(), rdns], asn=asn)

    assert d.levels[L.CITY].strict is None
    assert d.levels[L.CITY].advisory is None, "discarded outright, advisory included"
    assert d.levels[L.CITY].abstain_reason == "mobile_asn"
    assert d.levels[L.ADMIN1].strict == "Karnataka", "the state still stands"
    assert all(
        v.suppressed_reason is SuppressedReason.MOBILE_ASN
        for v, c in zip(d.verdicts, [*_four_databases(), rdns], strict=True)
        if c.city is not None
    )


# ---------------------------------------------------------------------------
# Rule (c): hosting, VPN, Tor
# ---------------------------------------------------------------------------


def test_a_hosting_network_abstains_on_every_strict_level() -> None:
    d = _decide(_four_databases(), asn=AsnInfo(asn=16509, is_hosting=True))

    assert _strict(d) == {"country": None, "admin1": None, "admin2": None, "city": None}
    assert d.levels[L.COUNTRY].abstain_reason == "hosting_asn"
    assert d.levels[L.CITY].abstain_reason == "hosting_asn"
    assert d.levels[L.COUNTRY].advisory == "IN"
    assert d.strict_point is None


def test_consent_outranks_a_vpn() -> None:
    """GPS is not derived from the address, so rule (c) does not apply to it (F4.AC1)."""
    gps = Candidate(
        source=S.GPS,
        level=L.CITY,
        country_code="IN",
        admin1="Karnataka",
        city="Mysuru",
        lat=12.30,
        lng=76.64,
    )
    d = _decide(
        [*_four_databases(country="NL", admin1="North Holland", city="Amsterdam"), gps],
        asn=AsnInfo(is_hosting=True),
        tz=None,
    )

    assert _strict(d)["city"] == "Mysuru"
    assert d.strict_point == (12.30, 76.64)


# ---------------------------------------------------------------------------
# S11 and conflict
# ---------------------------------------------------------------------------


def test_a_timezone_contradiction_weakens_the_contradicted_country() -> None:
    cf = Candidate(
        source=S.CF_COLO, level=L.CITY, country_code="IN", admin1="Maharashtra", city="Mumbai"
    )
    abroad = _four_databases(country="US", admin1="California", city="San Jose")
    d = _decide([*abroad, cf], tz=INDIA)

    assert d.levels[L.COUNTRY].advisory == "IN"
    assert any(v.suppressed_reason is SuppressedReason.TZ_MISMATCH for v in d.verdicts)


def test_disagreement_about_the_country_lowers_confidence_and_raises_conflict() -> None:
    agree = _decide(_four_databases())
    split = _decide(
        [*_four_databases(), _db(S.EXTERNAL_API, country="BD", admin1="Dhaka", city="Dhaka")]
    )

    assert split.conflict_score is not None and split.conflict_score > 0
    assert agree.conflict_score == 0
    a, s = agree.levels[L.COUNTRY].confidence, split.levels[L.COUNTRY].confidence
    assert a is not None and s is not None and s < a


def test_a_state_is_only_chosen_inside_the_chosen_country() -> None:
    """B2: settle the country before any state is considered."""
    stray = _db(S.EXTERNAL_API, country="BD", admin1="Dhaka", city="Dhaka")
    d = _decide([*_four_databases(), stray], tz=None)  # hierarchy alone, no S11
    assert d.levels[L.ADMIN1].advisory == "Karnataka"
    assert d.verdicts[-1].suppressed_reason is SuppressedReason.OUTVOTED


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def test_with_no_candidates_the_country_abstains_with_a_reason() -> None:
    """F4.AC18."""
    d = _decide([])
    assert d.levels[L.COUNTRY].strict is None
    assert d.abstain_reason["country"] == "no_candidates"


@pytest.mark.parametrize(
    "scenario",
    ["plain", "artifact", "mobile", "hosting", "conflict"],
)
def test_every_strict_null_has_a_reason_and_every_rejection_too(scenario: str) -> None:
    """DATA_MODEL 5.3 invariant 4, and the visit_candidates CHECK."""
    cands = {
        "plain": _four_databases(),
        "artifact": _faridabad(),
        "mobile": _four_databases(),
        "hosting": _four_databases(),
        "conflict": [*_four_databases(), _db(S.EXTERNAL_API, country="BD", admin1="Dhaka")],
    }[scenario]
    asn = {
        "artifact": AIRTEL_BROADBAND,
        "mobile": AsnInfo(is_mobile=True),
        "hosting": AsnInfo(is_hosting=True),
    }.get(scenario, AsnInfo())
    d = _decide(cands, asn=asn)

    for level, result in d.levels.items():
        assert (result.strict is None) == (result.abstain_reason is not None), level
    for v in d.verdicts:
        assert v.accepted == (v.suppressed_reason is None)


def test_a_strict_value_always_meets_its_threshold() -> None:
    """DATA_MODEL 5.3 invariant 3."""
    d = _decide(_four_databases())
    for level, result in d.levels.items():
        if result.strict is not None:
            assert result.confidence is not None
            assert result.confidence >= DEFAULT_CONFIG.thresholds.at(level)


def test_the_timezone_row_never_carries_weight() -> None:
    tz_row = Candidate(source=S.TIMEZONE, level=L.COUNTRY, country_code="IN")
    d = _decide([*_four_databases(), tz_row])
    assert d.verdicts[-1].weight == 0 and d.verdicts[-1].effective_weight == 0
    assert d.verdicts[-1].accepted
