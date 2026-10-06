"""Weighted consensus, suppression and dual output (F4.AC9-F4.AC12, ADR-0005).

A pure function: candidates, what is known about the network, and the browser's
timezone in; a located visit and a verdict for every candidate out. No I/O, so every
rule below is unit-tested directly.

**How a level is decided.** Levels are decided shallowest first, and a candidate only
votes at a level if it agrees with every level already chosen -- B2's tolerance made
structural: a wrong city inside the right state is survivable, a wrong state is not, so
the state is settled before any city is considered.

For each value ``v`` proposed at a level, every supporting candidate ``c`` has a weight

    q(c) = prior(source, level) x raw_confidence x tz_factor

and support for ``v`` is a noisy-OR over *families* (``types.Family``)::

    family_support = 1 - (1 - q_max) x prod(1 - bonus x q_other)   within one family
    support(v)     = 1 - prod(1 - family_support)                   across families
    confidence(v)  = support(v) x share(v)

``share`` is v's fraction of all weight at that level, so disagreement lowers confidence
even when one side is strongly supported. Independent agreement raises confidence;
correlated agreement -- four registry databases repeating one registry record -- raises
it only a little. That asymmetry is most of the B1 fix before any suppression runs.

**Consent outranks the network.** The three suppression rules describe what an *IP
address* can and cannot say (F4.AC12). Consented GPS (S1) is not derived from the address,
so it is exempt from all three: a visitor on a VPN who has already granted location is
located by GPS, not abstained on because of their VPN (F4.AC1: GPS outranks every other
source).

**Strict and advisory.** Advisory is the best guess from every candidate the rules allow
at all. Strict is threshold-gated, excludes hosting-ASN candidates, and applies the
registry-artifact collapse. Every strict NULL carries a reason (DATA_MODEL 5.3 inv. 4).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from tracelet.inference.config import InferenceConfig
from tracelet.inference.types import (
    FAMILY,
    LEVELS,
    NON_DATABASE_CORROBORATORS,
    AsnInfo,
    Candidate,
    Family,
    GeoLevel,
    InferenceSource,
    SuppressedReason,
    normalise_place,
)

# Deterministic tie-break when two values have identical confidence: prefer the value
# backed by the more authoritative source, then the alphabetically first key.
_SOURCE_RANK: dict[InferenceSource, int] = {s: i for i, s in enumerate(InferenceSource)}

_RULE_REASONS: frozenset[str] = frozenset(
    {
        SuppressedReason.REGISTRY_ARTIFACT.value,
        SuppressedReason.MOBILE_ASN.value,
        SuppressedReason.HOSTING_ASN.value,
    }
)


@dataclass(frozen=True, slots=True)
class LevelResult:
    strict: str | None
    advisory: str | None
    confidence: float | None
    abstain_reason: str | None


@dataclass(frozen=True, slots=True)
class Verdict:
    """What consensus concluded about one candidate -- one ``visit_candidates`` row."""

    weight: float
    effective_weight: float
    accepted: bool
    suppressed_reason: SuppressedReason | None


@dataclass(frozen=True, slots=True)
class Decision:
    levels: dict[GeoLevel, LevelResult]
    strict_point: tuple[float, float] | None
    advisory_point: tuple[float, float] | None
    agreement_score: float | None
    conflict_score: float | None
    primary_source: InferenceSource | None
    verdicts: tuple[Verdict, ...]  # aligned with the input candidates

    @property
    def abstain_reason(self) -> dict[str, str]:
        return {
            level.value: r.abstain_reason
            for level, r in self.levels.items()
            if r.strict is None and r.abstain_reason is not None
        }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Vote:
    index: int
    candidate: Candidate
    q: float


@dataclass(slots=True)
class _Choice:
    key: str
    display: str
    confidence: float
    support: float
    share: float
    votes: list[_Vote]


@dataclass(slots=True)
class _Pass:
    """One walk down the levels over one pool of candidates."""

    chosen: dict[GeoLevel, _Choice] = field(default_factory=dict)
    had_candidates: dict[GeoLevel, bool] = field(default_factory=dict)


def _is_gps(c: Candidate) -> bool:
    return c.source is InferenceSource.GPS


def _tz_factor(c: Candidate, tz_countries: frozenset[str] | None, config: InferenceConfig) -> float:
    if not tz_countries or c.country_code is None or _is_gps(c):
        return 1.0
    return 1.0 if c.country_code.upper() in tz_countries else config.tz_penalty


def _support(votes: Sequence[_Vote], bonus: float) -> float:
    by_family: dict[Family, list[float]] = {}
    for v in votes:
        by_family.setdefault(FAMILY[v.candidate.source], []).append(v.q)
    miss = 1.0
    for qs in by_family.values():
        qs.sort(reverse=True)
        family_miss = 1.0 - qs[0]
        for q in qs[1:]:
            family_miss *= 1.0 - bonus * q
        miss *= family_miss
    return 1.0 - miss


def _agrees_with_chosen(c: Candidate, chosen: dict[GeoLevel, _Choice]) -> bool:
    for level, choice in chosen.items():
        value = c.value_at(level)
        if value is not None and value != choice.key:
            return False
    return True


def _walk(
    pool: Iterable[tuple[int, Candidate]],
    *,
    config: InferenceConfig,
    tz: dict[int, float],
    city_blocked: frozenset[int],
) -> _Pass:
    """Decide each level in turn over ``pool``. Thresholds are not applied here."""
    members = list(pool)
    result = _Pass()
    for level in LEVELS:
        votes: list[_Vote] = []
        for index, c in members:
            if c.value_at(level) is None or not _agrees_with_chosen(c, result.chosen):
                continue
            if level in (GeoLevel.ADMIN2, GeoLevel.CITY) and index in city_blocked:
                continue
            q = config.source(c.source).priors.at(level) * c.raw_confidence * tz[index]
            if q > 0:
                votes.append(_Vote(index, c, min(q, 0.999)))
        result.had_candidates[level] = bool(votes)
        if not votes:
            continue
        total = sum(v.q for v in votes)
        groups: dict[str, list[_Vote]] = {}
        for v in votes:
            key = v.candidate.value_at(level)
            assert key is not None  # filtered above
            groups.setdefault(key, []).append(v)
        options: list[_Choice] = []
        for key, members_at in groups.items():
            support = _support(members_at, config.within_family_bonus)
            share = sum(v.q for v in members_at) / total
            best = max(members_at, key=lambda v: v.q)
            display = _display(best.candidate, level)
            options.append(_Choice(key, display, support * share, support, share, members_at))
        options.sort(
            key=lambda o: (
                -o.confidence,
                -o.support,
                min(_SOURCE_RANK[v.candidate.source] for v in o.votes),
                o.key,
            )
        )
        result.chosen[level] = options[0]
    return result


def _display(c: Candidate, level: GeoLevel) -> str:
    raw = {
        GeoLevel.COUNTRY: c.country_code,
        GeoLevel.ADMIN1: c.admin1,
        GeoLevel.ADMIN2: c.admin2,
        GeoLevel.CITY: c.city,
    }[level]
    assert raw is not None
    return raw.upper() if level is GeoLevel.COUNTRY else raw.strip()


def _point(choice: _Choice | None) -> tuple[float, float] | None:
    if choice is None:
        return None
    located = [
        v for v in choice.votes if v.candidate.lat is not None and v.candidate.lng is not None
    ]
    if not located:
        return None
    best = max(located, key=lambda v: v.q).candidate
    assert best.lat is not None and best.lng is not None
    return (best.lat, best.lng)


def _deepest(p: _Pass, levels: Iterable[GeoLevel]) -> _Choice | None:
    found: _Choice | None = None
    for level in LEVELS:
        if level in levels and level in p.chosen:
            found = p.chosen[level]
    return found


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def decide(
    candidates: Sequence[Candidate],
    *,
    asn: AsnInfo,
    tz_countries: frozenset[str] | None,
    config: InferenceConfig,
) -> Decision:
    indexed = list(enumerate(candidates))
    locating = [(i, c) for i, c in indexed if c.source is not InferenceSource.TIMEZONE]
    tz = {i: _tz_factor(c, tz_countries, config) for i, c in indexed}

    reasons: dict[int, SuppressedReason] = {}

    # Rule (b) -- mobile and CGNAT ASNs: no network-derived candidate may place the
    # visitor at city (or district) depth. Its country and state still count.
    city_blocked: frozenset[int] = frozenset()
    if asn.is_mobile or asn.is_cgnat:
        city_blocked = frozenset(
            i
            for i, c in locating
            if not _is_gps(c) and (c.value_at(GeoLevel.CITY) or c.value_at(GeoLevel.ADMIN2))
        )
        for i in city_blocked:
            reasons[i] = SuppressedReason.MOBILE_ASN

    advisory = _walk(locating, config=config, tz=tz, city_blocked=city_blocked)

    # Rule (c) -- hosting, VPN and Tor ASNs: the address describes infrastructure, so
    # nothing derived from it may be acted on. Advisory still shows the guess.
    strict_pool = locating
    if asn.is_hosting or asn.is_tor:
        strict_pool = [(i, c) for i, c in locating if _is_gps(c)]
        for i, c in locating:
            if not _is_gps(c):
                reasons[i] = SuppressedReason.HOSTING_ASN
    strict = (
        advisory
        if strict_pool is locating
        else _walk(strict_pool, config=config, tz=tz, city_blocked=city_blocked)
    )

    levels: dict[GeoLevel, LevelResult] = {}
    blocked_by: str | None = None
    artifact_levels = _registry_artifact(strict, asn, config)
    for level in LEVELS:
        a = advisory.chosen.get(level)
        s = strict.chosen.get(level)
        strict_value: str | None = None
        reason: str | None = None
        if blocked_by is not None:
            reason = blocked_by
        elif level in artifact_levels:
            reason = SuppressedReason.REGISTRY_ARTIFACT.value
        elif (asn.is_hosting or asn.is_tor) and s is None and advisory.had_candidates.get(level):
            reason = SuppressedReason.HOSTING_ASN.value
        elif (
            (asn.is_mobile or asn.is_cgnat)
            and level in (GeoLevel.ADMIN2, GeoLevel.CITY)
            and s is None
            and city_blocked
        ):
            reason = SuppressedReason.MOBILE_ASN.value
        elif s is None:
            reason = "no_candidates"
        elif s.confidence < config.thresholds.at(level):
            reason = SuppressedReason.BELOW_THRESHOLD.value
        else:
            strict_value = s.display
        # Strict is hierarchical: once a level abstains for any reason other than
        # having no candidates at all, every deeper level abstains with it. A
        # suppression rule carries down as itself, so the city of a registry-artifact
        # visit says `registry_artifact`, not merely that its parent abstained.
        if strict_value is None and reason not in (None, "no_candidates") and blocked_by is None:
            blocked_by = reason if reason in _RULE_REASONS else "parent_abstained"
        confidence = (
            s.confidence
            if strict_value is not None and s is not None
            else (a.confidence if a is not None else None)
        )
        levels[level] = LevelResult(
            strict=strict_value,
            advisory=a.display if a is not None else None,
            confidence=round(confidence, 3) if confidence is not None else None,
            abstain_reason=reason if strict_value is None else None,
        )

    for i in _artifact_members(strict, artifact_levels):
        reasons.setdefault(i, SuppressedReason.REGISTRY_ARTIFACT)

    # A strict *point* exists only when the strict *city* does. A visitor known only to
    # be in Karnataka must not become a coordinate in central Bengaluru: geofencing
    # (M6) would treat that as a location we believe (CLAUDE.md invariant 5).
    strict_point = (
        _point(strict.chosen.get(GeoLevel.CITY)) if levels[GeoLevel.CITY].strict else None
    )
    deepest_advisory = _deepest(advisory, set(LEVELS))
    advisory_point = _point(deepest_advisory)

    verdicts = tuple(
        _verdict(i, c, advisory=advisory, reasons=reasons, tz=tz, config=config) for i, c in indexed
    )
    primary = (
        max(deepest_advisory.votes, key=lambda v: v.q).candidate.source
        if deepest_advisory is not None
        else None
    )
    agreement, conflict = _scores(advisory)
    return Decision(
        levels=levels,
        strict_point=strict_point,
        advisory_point=advisory_point,
        agreement_score=agreement,
        conflict_score=conflict,
        primary_source=primary,
        verdicts=verdicts,
    )


def _registry_artifact(strict: _Pass, asn: AsnInfo, config: InferenceConfig) -> frozenset[GeoLevel]:
    """Rule (a), F4.AC12(a): which strict levels an uncorroborated artifact city voids."""
    city = strict.chosen.get(GeoLevel.CITY)
    rule = config.registry_artifact
    if (
        city is None
        or asn.modal_city is None
        or asn.modal_share is None
        or asn.modal_share < rule.min_modal_share
        or city.key != normalise_place(asn.modal_city)
    ):
        return frozenset()
    if any(v.candidate.source in NON_DATABASE_CORROBORATORS for v in city.votes):
        return frozenset()
    # To country, not admin1: the database that placed the city on the registry address
    # placed the state there too (SPEC section 11 row 11, RISKS R22).
    return frozenset({GeoLevel.ADMIN1, GeoLevel.ADMIN2, GeoLevel.CITY})


def _artifact_members(strict: _Pass, levels: frozenset[GeoLevel]) -> list[int]:
    if GeoLevel.CITY not in levels:
        return []
    city = strict.chosen[GeoLevel.CITY]
    return [v.index for v in city.votes]


def _verdict(
    index: int,
    c: Candidate,
    *,
    advisory: _Pass,
    reasons: dict[int, SuppressedReason],
    tz: dict[int, float],
    config: InferenceConfig,
) -> Verdict:
    deepest = c.level if c.level in LEVELS else _deepest_asserted(c)
    prior = config.source(c.source).priors.at(deepest) if deepest is not None else 0.0
    weight = round(prior * c.raw_confidence, 4)
    effective = round(weight * tz[index], 4)
    if c.source is InferenceSource.TIMEZONE:
        # S11 never votes (F4.AC9); its row shows whether the browser agreed.
        weight = effective = 0.0
    if index in reasons:
        return Verdict(weight, effective, False, reasons[index])
    agrees = _agrees_with_chosen(c, advisory.chosen) and any(
        c.value_at(level) is not None for level in LEVELS
    )
    if agrees:
        return Verdict(weight, effective, True, None)
    reason = SuppressedReason.TZ_MISMATCH if tz[index] < 1.0 else SuppressedReason.OUTVOTED
    return Verdict(weight, effective, False, reason)


def _deepest_asserted(c: Candidate) -> GeoLevel | None:
    found: GeoLevel | None = None
    for level in LEVELS:
        if c.value_at(level) is not None:
            found = level
    return found


def _scores(advisory: _Pass) -> tuple[float | None, float | None]:
    """``agreement_score``: the winning state's share of all state-level weight (the
    country's, where no source named a state). ``conflict_score``: the weight that named
    a *different country* -- the disagreement that matters most (F5.AC10)."""
    country = advisory.chosen.get(GeoLevel.COUNTRY)
    if country is None:
        return None, None
    admin1 = advisory.chosen.get(GeoLevel.ADMIN1)
    agreement = (admin1 or country).share
    return round(agreement, 3), round(1.0 - country.share, 3)
