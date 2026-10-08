"""F4.AC13's targets, as data (SPEC section 11 rows 9 and 30).

The gate compares the point value with the target. With 30 labels that is strict -- one
wrong strict country is 96.7 % -- and deliberately so: a confidently wrong country is the
error ADR-0005 exists to prevent. Targets the SPEC states only in words ("near-100-percent
coverage", "approximately 100 percent") are reported, not gated, until the amendment that
follows the first real labels gives them a number (row 30).

Changing a figure here changes a requirement: it needs a SPEC section 11 row first
(CLAUDE.md section 2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from tracelet.inference.types import GeoLevel

Metric = Literal["strict_precision", "strict_coverage", "advisory_accuracy"]
PopulationName = Literal["all", "consented", "non_consented", "network_only"]
Status = Literal["met", "missed", "unmeasured", "reported"]


@dataclass(frozen=True, slots=True)
class Target:
    population: PopulationName
    level: GeoLevel
    metric: Metric
    # None: stated in words only -- reported, never gated.
    minimum: float | None

    @property
    def id(self) -> str:
        prefix = "" if self.population == "network_only" else f"{self.population}."
        return f"{prefix}{self.level.value}.{self.metric}"


TARGETS: tuple[Target, ...] = (
    # "country strict accuracy >= 99.5 percent at near-100-percent coverage"
    Target("network_only", GeoLevel.COUNTRY, "strict_precision", 0.995),
    Target("network_only", GeoLevel.COUNTRY, "strict_coverage", None),
    # "admin1 strict precision >= 99 percent at coverage >= 85 percent, advisory >= 92"
    Target("network_only", GeoLevel.ADMIN1, "strict_precision", 0.99),
    Target("network_only", GeoLevel.ADMIN1, "strict_coverage", 0.85),
    Target("network_only", GeoLevel.ADMIN1, "advisory_accuracy", 0.92),
    # "city strict precision >= 95 percent, advisory accuracy >= 70 percent"; city
    # strict coverage has no floor yet (row 9).
    Target("network_only", GeoLevel.CITY, "strict_precision", 0.95),
    Target("network_only", GeoLevel.CITY, "strict_coverage", None),
    Target("network_only", GeoLevel.CITY, "advisory_accuracy", 0.70),
    # "consented visits city accuracy approximately 100 percent"
    Target("consented", GeoLevel.CITY, "advisory_accuracy", None),
)


class TargetCheck(BaseModel):
    id: str
    population: PopulationName
    level: GeoLevel
    metric: Metric
    target: float | None
    value: float | None
    n: int
    gated: bool
    status: Status


# (population, level, metric) -> (value, n); value None when n is 0.
Lookup = Callable[[PopulationName, GeoLevel, Metric], tuple[float | None, int]]


def evaluate(lookup: Lookup) -> list[TargetCheck]:
    checks = []
    for t in TARGETS:
        value, n = lookup(t.population, t.level, t.metric)
        status: Status
        if t.minimum is None:
            status = "reported"
        elif value is None:
            status = "unmeasured"
        else:
            status = "met" if value >= t.minimum else "missed"
        checks.append(
            TargetCheck(
                id=t.id,
                population=t.population,
                level=t.level,
                metric=t.metric,
                target=t.minimum,
                value=value,
                n=n,
                gated=t.minimum is not None,
                status=status,
            )
        )
    return checks
