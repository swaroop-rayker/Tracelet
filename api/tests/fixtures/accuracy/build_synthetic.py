"""Build ``synthetic.json``: the committed accuracy fixture CI always runs (ADR-0024).

Made-up candidate sets shaped like real ones -- India, the registry databases, a few PTR
city codes, mobile and broadband networks, some consented visits -- and nothing about any
real visit. It is built to exercise the gate, not to estimate accuracy:

* under the code defaults every gated F4.AC13 target is met;
* a few visits have only one database naming the *wrong* state, so a lowered admin1
  threshold emits a wrong strict state and fails precision;
* a few have the databases split, so a raised threshold abstains and fails coverage.

``tests/unit/test_accuracy_gate.py`` holds those three facts. Regenerate with
``python tests/fixtures/accuracy/build_synthetic.py`` (from ``api/``) after changing it.
"""

from __future__ import annotations

from pathlib import Path

from tracelet.accuracy import fixture
from tracelet.accuracy.replay import Case, Truth
from tracelet.inference.types import AsnInfo, Candidate, GeoLevel, InferenceSource

S = InferenceSource
INDIA = frozenset({"IN"})

PLACES: tuple[tuple[str, str], ...] = (
    ("Karnataka", "Bengaluru"),
    ("Maharashtra", "Mumbai"),
    ("Maharashtra", "Pune"),
    ("Telangana", "Hyderabad"),
    ("Tamil Nadu", "Chennai"),
    ("Delhi", "New Delhi"),
    ("West Bengal", "Kolkata"),
    ("Gujarat", "Ahmedabad"),
)
ELSEWHERE: dict[str, tuple[str, str]] = {
    "Karnataka": ("Haryana", "Faridabad"),
    "Maharashtra": ("Uttar Pradesh", "Noida"),
    "Telangana": ("Andhra Pradesh", "Vijayawada"),
    "Tamil Nadu": ("Kerala", "Kochi"),
    "Delhi": ("Haryana", "Gurugram"),
    "West Bengal": ("Bihar", "Patna"),
    "Gujarat": ("Rajasthan", "Jaipur"),
}


def db(
    source: InferenceSource, admin1: str, city: str | None, confidence: float = 1.0
) -> Candidate:
    return Candidate(
        source=source,
        level=GeoLevel.CITY if city else GeoLevel.ADMIN1,
        country_code="IN",
        admin1=admin1,
        city=city,
        raw_confidence=confidence,
    )


def country_only(source: InferenceSource) -> Candidate:
    return Candidate(source=source, level=GeoLevel.COUNTRY, country_code="IN")


def case(
    admin1: str,
    city: str | None,
    candidates: list[Candidate],
    *,
    mobile: bool = False,
    consented: bool = False,
    path: str = "direct",
    network: str | None = None,
    kind: str | None = None,
) -> Case:
    return Case(
        truth=Truth(country_code="IN", admin1=admin1, city=city),
        consented=consented,
        path="cloudflare" if path == "cloudflare" else "direct",
        asn=AsnInfo(is_mobile=mobile),
        tz_countries=INDIA,
        candidates=(country_only(S.IPINFO), *candidates),
        network=network,
        connection_kind=kind,
        vpn_used=False,
    )


def build() -> list[Case]:
    cases: list[Case] = []
    for i in range(24):
        # Broadband, the three databases agreeing: a strict state, a best-guess city.
        admin1, city = PLACES[i % len(PLACES)]
        cases.append(
            case(
                admin1,
                city,
                [
                    db(S.GEOLITE2, admin1, city),
                    db(S.DBIP, admin1, city),
                    db(S.IP2LOCATION, admin1, city),
                ],
                network=("act", "airtel", "bsnl")[i % 3],
                kind="wifi",
            )
        )
    for i in range(6):
        # A PTR city code corroborating the databases: a strict city.
        admin1, city = PLACES[i % 4]
        cases.append(
            case(
                admin1,
                city,
                [
                    db(S.RDNS, admin1, city, 0.95),
                    db(S.GEOLITE2, admin1, city),
                    db(S.DBIP, admin1, city),
                ],
                network="airtel",
                kind="wifi",
                path="cloudflare" if i % 2 else "direct",
            )
        )
    for i in range(6):
        # Mobile: the state from two databases, the city never strict (rule b).
        admin1, city = PLACES[i % len(PLACES)]
        cases.append(
            case(
                admin1,
                city,
                [db(S.GEOLITE2, admin1, city), db(S.DBIP, admin1, city)],
                mobile=True,
                network=("jio", "vi")[i % 2],
                kind="mobile_data",
            )
        )
    for i in range(4):
        # Consented: GPS names the true city; the network alone agrees on the state only.
        admin1, city = PLACES[i]
        other = (
            next(c for a, c in PLACES if a == admin1 and c != city)
            if admin1 == "Maharashtra"
            else city
        )
        cases.append(
            case(
                admin1,
                city,
                [
                    Candidate(
                        source=S.GPS,
                        level=GeoLevel.CITY,
                        country_code="IN",
                        admin1=admin1,
                        city=city,
                        raw_confidence=0.99,
                    ),
                    db(S.GEOLITE2, admin1, other),
                    db(S.DBIP, admin1, other),
                ],
                consented=True,
                network="jio",
                kind="mobile_data",
            )
        )
    for i in range(3):
        # Split: two databases right, one wrong -- abstains at the default threshold.
        admin1, city = PLACES[i]
        wrong_admin1, wrong_city = ELSEWHERE[admin1]
        cases.append(
            case(
                admin1,
                city,
                [
                    db(S.GEOLITE2, admin1, city),
                    db(S.DBIP, admin1, city),
                    db(S.IP2LOCATION, wrong_admin1, wrong_city),
                ],
                network="bsnl",
                kind="wifi",
            )
        )
    for i in range(2):
        # One database alone, naming the wrong state (B1): abstains at the default
        # threshold; a lowered one emits it, and precision fails.
        admin1, city = PLACES[i]
        wrong_admin1, wrong_city = ELSEWHERE[admin1]
        cases.append(
            case(
                admin1,
                city,
                [db(S.IP2LOCATION, wrong_admin1, wrong_city, 0.8)],
                network="other",
                kind="wifi",
            )
        )
    return cases


def main() -> None:
    out = Path(__file__).with_name("synthetic.json")
    text = fixture.dumps(build(), settings_version=None, config=None)
    out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
