"""Scoring: precision, coverage and best-guess accuracy, with intervals (F4.AC13, F4.AC17).

Pure. Every proportion is ``{k, n, value, ci95}`` -- the count is never separated from the
figure (RISKS R9) -- and the interval is Wilson's score interval, in plain Python: the box
has no room for numpy (CLAUDE.md section 5), and one formula does not need it.

**Populations** (ADR-0024 decision 2, API section 11):

* ``all`` -- every label, replayed as recorded (consented visits keep their GPS);
* ``consented`` / ``non_consented`` -- ``all`` split by consent;
* ``network_only`` -- every label *without a VPN* replayed with the GPS candidate removed.
  The network targets of F4.AC13 are gated here;
* ``vpn`` -- the labels made with a VPN on, network path. The address describes the VPN, so
  the only right answer is to confirm nothing, and that is what is gated (SPEC section 11
  row 32). Kept out of ``network_only``, the per-path figures and the per-source figures,
  where an unavoidable VPN location would count against the sources.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from tracelet.accuracy.replay import Answer, Case, claim, decide, right
from tracelet.accuracy.targets import Metric, PopulationName, TargetCheck, evaluate
from tracelet.inference.config import InferenceConfig
from tracelet.inference.types import LEVELS, Candidate, GeoLevel, InferenceSource

Population = PopulationName
POPULATIONS: tuple[Population, ...] = (
    "network_only",
    "all",
    "consented",
    "non_consented",
    "vpn",
)

# z for a two-sided 95 % interval.
_Z = 1.959963984540054


class Proportion(BaseModel):
    k: int
    n: int
    value: float | None
    ci95: tuple[float, float] | None


def wilson(k: int, n: int) -> Proportion:
    """``k`` successes of ``n``, with the 95 % Wilson score interval.

    Wilson rather than the normal approximation because the figures here sit near 0 and 1
    on small ``n``, where the normal interval leaves [0, 1] and collapses to a point at
    k = n -- claiming certainty from 30 labels.
    """
    if n <= 0:
        return Proportion(k=0, n=0, value=None, ci95=None)
    p = k / n
    z2 = _Z * _Z
    denominator = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denominator
    half = _Z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    return Proportion(
        k=k,
        n=n,
        value=round(p, 4),
        ci95=(round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)),
    )


class LevelReport(BaseModel):
    level: GeoLevel
    # Labels whose truth reaches this level: the denominator of coverage and accuracy.
    label_count: int
    strict_precision: Proportion
    strict_coverage: Proportion
    advisory_accuracy: Proportion


class PopulationReport(BaseModel):
    population: Population
    label_count: int
    levels: list[LevelReport]


class PathReport(BaseModel):
    path: Literal["cloudflare", "direct"]
    label_count: int
    city_strict_precision: Proportion
    city_strict_coverage: Proportion


class SourceLevel(BaseModel):
    level: GeoLevel
    claims: int
    accepted: int
    correct: Proportion


class SourceReport(BaseModel):
    source: InferenceSource
    levels: list[SourceLevel]


class MatrixCell(BaseModel):
    network: str | None
    connection_kind: str | None
    vpn_used: bool | None
    count: int


class Report(BaseModel):
    settings_version: int | None
    inference_version: str
    classifier_version: str
    label_count: int
    cant_tell: int
    pending: int
    populations: list[PopulationReport]
    paths: list[PathReport]
    sources: list[SourceReport]
    matrix: list[MatrixCell]
    targets: list[TargetCheck]
    passed: bool | None

    def population(self, name: Population) -> PopulationReport:
        return next(p for p in self.populations if p.population == name)


@dataclass(frozen=True, slots=True)
class _Scored:
    case: Case
    answer: Answer


def _level(scored: Sequence[_Scored], level: GeoLevel) -> LevelReport:
    reaching = [s for s in scored if s.case.truth.at(level) is not None]
    emitted = [s for s in reaching if s.answer.strict[level] is not None]
    precise = sum(right(s.case.truth, s.answer.strict, level) for s in emitted)
    guessed = sum(right(s.case.truth, s.answer.advisory, level) for s in reaching)
    return LevelReport(
        level=level,
        label_count=len(reaching),
        strict_precision=wilson(precise, len(emitted)),
        strict_coverage=wilson(len(emitted), len(reaching)),
        advisory_accuracy=wilson(guessed, len(reaching)),
    )


def _population(name: Population, scored: Sequence[_Scored]) -> PopulationReport:
    return PopulationReport(
        population=name,
        label_count=len(scored),
        levels=[_level(scored, level) for level in LEVELS],
    )


def _paths(scored: Sequence[_Scored]) -> list[PathReport]:
    out = []
    for path in ("cloudflare", "direct"):
        on_path = [s for s in scored if s.case.path == path]
        city = _level(on_path, GeoLevel.CITY)
        out.append(
            PathReport(
                path=path,
                label_count=len(on_path),
                city_strict_precision=city.strict_precision,
                city_strict_coverage=city.strict_coverage,
            )
        )
    return out


def _sources(scored: Sequence[_Scored]) -> list[SourceReport]:
    """F4.AC17: how often each source's own claim was right.

    One claim per source, level and label: where a source proposed several candidates for
    one visit, its most confident one speaks for it, so ``claims`` counts labels."""
    tallies: dict[tuple[InferenceSource, GeoLevel], list[int]] = {}
    for s in scored:
        best: dict[InferenceSource, tuple[Candidate, bool]] = {}
        for candidate, accepted in zip(s.answer.candidates, s.answer.accepted, strict=True):
            if candidate.source is InferenceSource.TIMEZONE:
                continue  # S11 never proposes a place (F4.AC9)
            held = best.get(candidate.source)
            if held is None or candidate.raw_confidence > held[0].raw_confidence:
                best[candidate.source] = (candidate, accepted)
        for source, (candidate, accepted) in best.items():
            values = claim(candidate)
            for level in LEVELS:
                if values[level] is None or s.case.truth.at(level) is None:
                    continue
                tally = tallies.setdefault((source, level), [0, 0, 0])
                tally[0] += 1
                tally[1] += int(accepted)
                tally[2] += int(right(s.case.truth, values, level))
    out = []
    for source in InferenceSource:
        levels = [
            SourceLevel(level=level, claims=t[0], accepted=t[1], correct=wilson(t[2], t[0]))
            for level in LEVELS
            if (t := tallies.get((source, level))) is not None
        ]
        if levels:
            out.append(SourceReport(source=source, levels=levels))
    return out


def _matrix(cases: Sequence[Case]) -> list[MatrixCell]:
    counts = Counter((c.network, c.connection_kind, c.vpn_used) for c in cases)
    return [
        MatrixCell(network=n, connection_kind=k, vpn_used=v, count=count)
        for (n, k, v), count in sorted(
            counts.items(), key=lambda item: tuple((x is None, str(x)) for x in item[0])
        )
    ]


def _check(populations: Sequence[PopulationReport]) -> list[TargetCheck]:
    by_name = {p.population: p for p in populations}

    def lookup(
        population: PopulationName, level: GeoLevel, metric: Metric
    ) -> tuple[float | None, int]:
        report = next(r for r in by_name[population].levels if r.level == level)
        figure: Proportion = getattr(report, metric)
        return figure.value, figure.n

    return evaluate(lookup)


def score(
    cases: Sequence[Case],
    config: InferenceConfig,
    *,
    settings_version: int | None,
    inference_version: str,
    classifier_version: str,
    cant_tell: int = 0,
    pending: int = 0,
    only: Callable[[Case], bool] | None = None,
) -> Report:
    """Replay every case under ``config`` and score it. ``only`` restricts the cases
    (``/analytics/accuracy`` passes the visit filter's selection this way)."""
    chosen = [c for c in cases if only is None or only(c)]
    recorded = [_Scored(c, decide(c, config)) for c in chosen]
    network = [_Scored(c, decide(c, config, without_gps=True)) for c in chosen]
    plain = [s for s in network if s.case.vpn_used is not True]
    populations = [
        _population("network_only", plain),
        _population("all", recorded),
        _population("consented", [s for s in recorded if s.case.consented]),
        _population("non_consented", [s for s in recorded if not s.case.consented]),
        _population("vpn", [s for s in network if s.case.vpn_used is True]),
    ]
    targets = _check(populations)
    gated = [t for t in targets if t.gated and t.status != "unmeasured"]
    passed = None if not gated else all(t.status == "met" for t in gated)
    return Report(
        settings_version=settings_version,
        inference_version=inference_version,
        classifier_version=classifier_version,
        label_count=len(chosen),
        cant_tell=cant_tell,
        pending=pending,
        populations=populations,
        paths=_paths(plain),
        sources=_sources([s for s in recorded if s.case.vpn_used is not True]),
        matrix=_matrix(chosen),
        targets=targets,
        passed=passed,
    )
