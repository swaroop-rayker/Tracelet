"""S11 -- the browser's timezone as a cross-check (F4.AC9).

It never proposes a location. It names the countries a timezone is used in, and
consensus reduces the weight of any candidate whose country is not among them. Its own
row in the derivation trail carries zero weight and shows whether the browser agreed.

The mapping is the IANA ``zone1970.tab`` shipped with the OS timezone database, read
once. If that file is ever missing the check abstains with a reason rather than guessing.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Final

from tracelet.inference.sources import SourceInput, SourceUnavailable
from tracelet.inference.types import Candidate, GeoLevel, InferenceSource

ZONE_TABLES: Final = (
    Path("/usr/share/zoneinfo/zone1970.tab"),
    Path("/usr/share/zoneinfo/zone.tab"),
)


@cache
def zone_countries() -> dict[str, frozenset[str]]:
    """tz name -> the ISO countries using it. Empty if no table exists."""
    for path in ZONE_TABLES:
        if path.is_file():
            return parse_zone_table(path.read_text(encoding="utf-8"))
    return {}


def parse_zone_table(text: str) -> dict[str, frozenset[str]]:
    table: dict[str, frozenset[str]] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        countries = frozenset(c.strip().upper() for c in fields[0].split(",") if c.strip())
        table[fields[2].strip()] = countries
    # Two aliases browsers still report, absent from zone1970.tab.
    if "Asia/Kolkata" in table:
        table.setdefault("Asia/Calcutta", table["Asia/Kolkata"])
    return table


def countries_for(tz_iana: str | None) -> frozenset[str] | None:
    """The countries this timezone is used in; ``None`` when unknown or not given."""
    if not tz_iana:
        return None
    return zone_countries().get(tz_iana.strip())


async def produce(inp: SourceInput) -> list[Candidate]:
    if not inp.tz_iana:
        return []
    if not zone_countries():
        raise SourceUnavailable("zone_table_missing")
    countries = countries_for(inp.tz_iana)
    if not countries:
        return []
    # A row only when the timezone names exactly one country: that is the case where
    # "the browser says X" is a statement worth showing. Multi-country zones still
    # penalise contradicting candidates through `countries_for`.
    if len(countries) != 1:
        return []
    (country,) = tuple(countries)
    return [
        Candidate(
            source=InferenceSource.TIMEZONE,
            level=GeoLevel.COUNTRY,
            country_code=country,
            evidence={"tz": inp.tz_iana},
        )
    ]
