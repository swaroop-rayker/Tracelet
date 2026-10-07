"""Cities and towns for the geofence editor's map (DESIGN §16, owner decision 2026-10-06).

Tiers are population bands, the same for every country, because India has no single official
"tier" scale (RBI and Census tiers are population classes that put some 500 towns in Tier 1,
while common usage means the eight metros):

| Tier | Population |
|---|---|
| metro | 4,000,000 and over |
| tier1 | 1,000,000 to 3,999,999 |
| tier2 | 300,000 to 999,999 |
| tier3 | 50,000 to 299,999 |

Smaller places are not shown. Populations are GeoNames' city-proper figures, so a city's
tier is GeoNames' word for it: on the 2026 table Surat is a metro and Pune is tier 1, and a
few GeoNames entries carry implausible populations.
"""

from __future__ import annotations

import enum
from typing import Final


class Tier(enum.StrEnum):
    METRO = "metro"
    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"


BANDS: Final = (
    (4_000_000, Tier.METRO),
    (1_000_000, Tier.TIER1),
    (300_000, Tier.TIER2),
    (50_000, Tier.TIER3),
)
SMALLEST: Final = BANDS[-1][0]


def tier(population: int) -> Tier | None:
    """The band a population falls in, or None below the smallest."""
    for floor, band in BANDS:
        if population >= floor:
            return band
    return None
