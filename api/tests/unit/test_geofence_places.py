"""Population tiers for the editor's map (DESIGN §16, owner decision 2026-10-06)."""

from __future__ import annotations

import pytest

from tracelet.geofence.places import Tier, tier


@pytest.mark.parametrize(
    ("population", "expected"),
    [
        (12_691_836, Tier.METRO),
        (4_000_000, Tier.METRO),
        (3_999_999, Tier.TIER1),
        (1_000_000, Tier.TIER1),
        (999_999, Tier.TIER2),
        (300_000, Tier.TIER2),
        (299_999, Tier.TIER3),
        (50_000, Tier.TIER3),
        (49_999, None),
        (0, None),
    ],
)
def test_a_population_falls_in_one_band(population: int, expected: Tier | None) -> None:
    assert tier(population) is expected
