"""Replay: a labelled visit's stored candidates, decided again under a chosen version.

Pure, like the consensus it calls (ADR-0024 decision 1): no I/O, no address, no lookup. A
``Case`` is everything ``consensus.decide()`` needs plus the truth to compare against, and
it is the unit the database loader, the fixture and the scorer all share.

**What "right" means.** Place names compare after ``normalise_place`` -- the same key the
engine votes on -- and country codes case-insensitively. A level is right only when the
answer at that level equals the truth **and** no shallower level the truth names is
contradicted: Aurangabad in the wrong state is wrong (B2: a wrong state is never
tolerable). A shallower level the answer left empty does not contradict; strict may skip
the district and still state the city.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from tracelet.inference import consensus
from tracelet.inference.config import InferenceConfig
from tracelet.inference.engine import combine_asn
from tracelet.inference.sources import asn_org
from tracelet.inference.types import (
    LEVELS,
    AsnInfo,
    Candidate,
    GeoLevel,
    InferenceSource,
    normalise_place,
)

Path = Literal["cloudflare", "direct"]


@dataclass(frozen=True, slots=True)
class Truth:
    """Where the visit really was. The truth may stop at any level (DATA_MODEL 10.1)."""

    country_code: str
    admin1: str | None = None
    admin2: str | None = None
    city: str | None = None

    def at(self, level: GeoLevel) -> str | None:
        return {
            GeoLevel.COUNTRY: self.country_code,
            GeoLevel.ADMIN1: self.admin1,
            GeoLevel.ADMIN2: self.admin2,
            GeoLevel.CITY: self.city,
        }.get(level)


@dataclass(frozen=True, slots=True)
class Case:
    """One labelled visit, as the scorer needs it."""

    truth: Truth
    consented: bool
    path: Path
    asn: AsnInfo
    tz_countries: frozenset[str] | None
    candidates: tuple[Candidate, ...]
    network: str | None = None
    connection_kind: str | None = None
    vpn_used: bool | None = None


@dataclass(frozen=True, slots=True)
class Answer:
    """The replayed decision: per level, the strict and advisory values."""

    strict: dict[GeoLevel, str | None]
    advisory: dict[GeoLevel, str | None]
    # The candidates decided over, and whether each was accepted, aligned.
    candidates: tuple[Candidate, ...] = field(default=())
    accepted: tuple[bool, ...] = field(default=())


def network_facts(
    asn: int | None, org: str | None, *, is_tor: bool, profile: AsnInfo | None
) -> AsnInfo:
    """A visit's network as the engine saw it: the curated classification of its ASN and
    organisation, overlaid with ``asn_profiles`` (``profile``, already overlaid on a bare
    ``AsnInfo(asn=...)`` by ``inference.store.overlay_profile``), plus Tor."""
    if asn is None:
        info = AsnInfo()
    else:
        info = combine_asn(asn_org.classify(asn, org), profile or AsnInfo(asn=asn))
    if is_tor:
        info = AsnInfo(
            asn=info.asn,
            org=info.org,
            is_mobile=info.is_mobile,
            is_hosting=info.is_hosting,
            is_cgnat=info.is_cgnat,
            is_tor=True,
            modal_city=info.modal_city,
            modal_admin1=info.modal_admin1,
            modal_share=info.modal_share,
        )
    return info


def decide(case: Case, config: InferenceConfig, *, without_gps: bool = False) -> Answer:
    """Run consensus over the case's candidates. ``without_gps`` drops S1 first: what the
    network alone would have said had the visitor refused (ADR-0024 decision 2)."""
    candidates = tuple(
        c for c in case.candidates if not (without_gps and c.source is InferenceSource.GPS)
    )
    decision = consensus.decide(
        candidates, asn=case.asn, tz_countries=case.tz_countries, config=config
    )
    return Answer(
        strict={level: decision.levels[level].strict for level in LEVELS},
        advisory={level: decision.levels[level].advisory for level in LEVELS},
        candidates=candidates,
        accepted=tuple(v.accepted for v in decision.verdicts),
    )


def same(level: GeoLevel, a: str | None, b: str | None) -> bool:
    """Whether two place values name the same place at ``level``."""
    if a is None or b is None:
        return False
    if level is GeoLevel.COUNTRY:
        return a.strip().upper() == b.strip().upper()
    return normalise_place(a) == normalise_place(b)


def right(truth: Truth, values: dict[GeoLevel, str | None], level: GeoLevel) -> bool:
    """Whether ``values`` is right at ``level`` (see the module docstring)."""
    if not same(level, values.get(level), truth.at(level)):
        return False
    for shallower in LEVELS[: LEVELS.index(level)]:
        stated, true = values.get(shallower), truth.at(shallower)
        if stated is not None and true is not None and not same(shallower, stated, true):
            return False
    return True


def claim(candidate: Candidate) -> dict[GeoLevel, str | None]:
    """A candidate's own values, in the shape ``right`` reads."""
    return {
        GeoLevel.COUNTRY: candidate.country_code,
        GeoLevel.ADMIN1: candidate.admin1,
        GeoLevel.ADMIN2: candidate.admin2,
        GeoLevel.CITY: candidate.city,
    }


def without_identity(info: AsnInfo) -> AsnInfo:
    """The network facts with the ASN number and organisation removed -- what a fixture
    keeps. ``consensus.decide()`` reads only the flags and the modal fields."""
    return AsnInfo(
        is_mobile=info.is_mobile,
        is_hosting=info.is_hosting,
        is_cgnat=info.is_cgnat,
        is_tor=info.is_tor,
        modal_city=info.modal_city,
        modal_admin1=info.modal_admin1,
        modal_share=info.modal_share,
    )


def strip_candidates(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """Candidates reduced to what consensus votes on: no coordinates, evidence or latency.

    Coordinates only choose the point a strict city carries, never a level's value, so a
    fixture without them scores identically (ADR-0024 decision 4)."""
    return tuple(
        Candidate(
            source=c.source,
            level=c.level,
            country_code=c.country_code,
            admin1=c.admin1,
            admin2=c.admin2,
            city=c.city,
            raw_confidence=c.raw_confidence,
        )
        for c in candidates
    )
