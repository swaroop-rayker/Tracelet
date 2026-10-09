"""F4.AC13's targets, as data (SPEC section 11 rows 9, 30 and 32).

The gate compares the point value with the target. With 30 labels that is strict -- one
wrong strict country is 96.7 % -- and deliberately so: a confidently wrong country is the
error ADR-0005 exists to prevent. A target with no number is reported, never gated.

Row 32 (2026-10-09) set these from the owner's first labels, as **interim** figures until
about 30 labels: every precision target kept, admin1 coverage relaxed to 60 %, city
best-guess accuracy reported rather than gated, and visits labelled *VPN on* moved to their
own population, where emitting any strict country or state is the failure.

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
PopulationName = Literal["all", "consented", "non_consented", "network_only", "vpn"]
Status = Literal["met", "missed", "unmeasured", "reported"]
# A floor (the figure must reach the target) or a ceiling (it must not exceed it).
Direction = Literal["at_least", "at_most"]


@dataclass(frozen=True, slots=True)
class Target:
    population: PopulationName
    level: GeoLevel
    metric: Metric
    # None: no number -- reported, never gated.
    target: float | None
    direction: Direction = "at_least"

    @property
    def id(self) -> str:
        prefix = "" if self.population == "network_only" else f"{self.population}."
        return f"{prefix}{self.level.value}.{self.metric}"


TARGETS: tuple[Target, ...] = (
    # Country: strict precision >= 99.5 % at coverage >= 95 % ("near-100", numbered by row 32).
    Target("network_only", GeoLevel.COUNTRY, "strict_precision", 0.995),
    Target("network_only", GeoLevel.COUNTRY, "strict_coverage", 0.95),
    # Admin1: precision >= 99 % (kept), coverage >= 60 % (interim, row 32), advisory >= 92 %.
    Target("network_only", GeoLevel.ADMIN1, "strict_precision", 0.99),
    Target("network_only", GeoLevel.ADMIN1, "strict_coverage", 0.60),
    Target("network_only", GeoLevel.ADMIN1, "advisory_accuracy", 0.92),
    # City: precision >= 95 % (kept); coverage (row 9) and best guess (row 32) reported only.
    Target("network_only", GeoLevel.CITY, "strict_precision", 0.95),
    Target("network_only", GeoLevel.CITY, "strict_coverage", None),
    Target("network_only", GeoLevel.CITY, "advisory_accuracy", None),
    # "consented visits city accuracy approximately 100 percent" -- reported.
    Target("consented", GeoLevel.CITY, "advisory_accuracy", None),
    # Visits labelled VPN on (row 32): the address describes the VPN, so a strict country or
    # state through it would be confidently wrong. None may be emitted.
    Target("vpn", GeoLevel.COUNTRY, "strict_coverage", 0.0, "at_most"),
    Target("vpn", GeoLevel.ADMIN1, "strict_coverage", 0.0, "at_most"),
)


class TargetCheck(BaseModel):
    id: str
    population: PopulationName
    level: GeoLevel
    metric: Metric
    target: float | None
    direction: Direction
    value: float | None
    n: int
    gated: bool
    status: Status


# (population, level, metric) -> (value, n); value None when n is 0.
Lookup = Callable[[PopulationName, GeoLevel, Metric], tuple[float | None, int]]


def _met(value: float, target: float, direction: Direction) -> bool:
    return value >= target if direction == "at_least" else value <= target


def evaluate(lookup: Lookup) -> list[TargetCheck]:
    checks = []
    for t in TARGETS:
        value, n = lookup(t.population, t.level, t.metric)
        status: Status
        if t.target is None:
            status = "reported"
        elif value is None:
            status = "unmeasured"
        else:
            status = "met" if _met(value, t.target, t.direction) else "missed"
        checks.append(
            TargetCheck(
                id=t.id,
                population=t.population,
                level=t.level,
                metric=t.metric,
                target=t.target,
                direction=t.direction,
                value=value,
                n=n,
                gated=t.target is not None,
                status=status,
            )
        )
    return checks
