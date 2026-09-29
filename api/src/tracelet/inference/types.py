"""Value objects shared by every stage of inference.

Nothing here touches the database or the network, so the consensus stage can be tested
as a pure function of these types.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class InferenceSource(enum.StrEnum):
    """Values match the ``inference_source`` enum created by migration 0004."""

    GPS = "gps"  # S1
    GEOLITE2 = "geolite2"  # S2
    IP2LOCATION = "ip2location"  # S3
    IPINFO = "ipinfo"  # S4
    DBIP = "dbip"  # S5
    RDNS = "rdns"  # S6
    ASN_ORG = "asn_org"  # S7
    CF_COLO = "cf_colo"  # S8
    EXTERNAL_API = "external_api"  # S9
    LATENCY = "latency"  # S10 -- dropped (SPEC 11 row 12); the DB enum value stays, unused
    TIMEZONE = "timezone"  # S11


class GeoLevel(enum.StrEnum):
    """Values match the ``geo_level`` enum created by migration 0005."""

    COUNTRY = "country"
    ADMIN1 = "admin1"
    ADMIN2 = "admin2"
    CITY = "city"
    POINT = "point"


# The levels consensus decides, shallowest first. `point` is not voted on: a
# coordinate is carried by the candidate that won the deepest level.
LEVELS: tuple[GeoLevel, ...] = (GeoLevel.COUNTRY, GeoLevel.ADMIN1, GeoLevel.ADMIN2, GeoLevel.CITY)


class SuppressedReason(enum.StrEnum):
    """Values match the CHECK on ``visit_candidates.suppressed_reason`` (migration 0005)."""

    REGISTRY_ARTIFACT = "registry_artifact"
    MOBILE_ASN = "mobile_asn"
    HOSTING_ASN = "hosting_asn"
    TZ_MISMATCH = "tz_mismatch"
    BELOW_THRESHOLD = "below_threshold"
    OUTVOTED = "outvoted"


class Family(enum.StrEnum):
    """Sources that share upstream data are not independent witnesses.

    Four offline databases agreeing is close to one database agreeing with itself: they
    are all built from the same registry records, which is precisely how B1 happens.
    Consensus therefore counts agreement *within* a family at a discount and agreement
    *across* families in full (``config.InferenceConfig.within_family_bonus``).
    """

    CLIENT = "client"  # what the visitor's own device reported
    DATABASE = "database"  # registry-derived: S2-S5, S9
    NETWORK = "network"  # the operator's own naming: S6 (PTR), S7 (organisation name)
    # Cloudflare's edge is chosen by BGP routing, not by anything the operator names, so
    # it corroborates S6/S7 independently rather than repeating them (m3.3).
    EDGE = "edge"  # S8
    CONTEXT = "context"  # S11 -- rejects, never proposes


FAMILY: dict[InferenceSource, Family] = {
    InferenceSource.GPS: Family.CLIENT,
    InferenceSource.GEOLITE2: Family.DATABASE,
    InferenceSource.IP2LOCATION: Family.DATABASE,
    InferenceSource.IPINFO: Family.DATABASE,
    InferenceSource.DBIP: Family.DATABASE,
    InferenceSource.EXTERNAL_API: Family.DATABASE,
    InferenceSource.RDNS: Family.NETWORK,
    InferenceSource.ASN_ORG: Family.NETWORK,
    InferenceSource.CF_COLO: Family.EDGE,
    InferenceSource.LATENCY: Family.NETWORK,
    InferenceSource.TIMEZONE: Family.CONTEXT,
}

# Suppression rule (a) asks whether anything *other than a database* corroborates the
# winning city (F4.AC12(a)): consented GPS, rDNS or the edge colo.
NON_DATABASE_CORROBORATORS: frozenset[InferenceSource] = frozenset(
    {InferenceSource.GPS, InferenceSource.RDNS, InferenceSource.CF_COLO}
)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One source's claim about where a visit came from (F4.AC6).

    ``level`` is the deepest level the claim reaches; shallower fields are implied by it
    and must be filled in (a city candidate carries its admin1 and country).
    """

    source: InferenceSource
    level: GeoLevel
    country_code: str | None = None
    admin1: str | None = None
    admin2: str | None = None
    city: str | None = None
    lat: float | None = None
    lng: float | None = None
    raw_confidence: float = 1.0
    evidence: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0

    def value_at(self, level: GeoLevel) -> str | None:
        """The candidate's claim at ``level``, normalised for comparison."""
        raw = {
            GeoLevel.COUNTRY: self.country_code,
            GeoLevel.ADMIN1: self.admin1,
            GeoLevel.ADMIN2: self.admin2,
            GeoLevel.CITY: self.city,
        }.get(level)
        return normalise_place(raw) if raw else None


def normalise_place(name: str) -> str:
    """Case- and punctuation-insensitive key, so 'Bengaluru ' and 'bengaluru' agree.

    Deliberately does not attempt transliteration or alias resolution ('Bangalore' vs
    'Bengaluru'): the sources are asked to report GeoNames names, and an alias table
    here would hide a source that does not.
    """
    return " ".join(name.casefold().replace(".", " ").replace("-", " ").split())


@dataclass(frozen=True, slots=True)
class AsnInfo:
    """What is known about the visitor's network, from ``asn_profiles`` or S7."""

    asn: int | None = None
    org: str | None = None
    is_mobile: bool = False
    is_hosting: bool = False
    is_cgnat: bool = False
    modal_city: str | None = None
    modal_admin1: str | None = None
    modal_share: float | None = None


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """Why a source produced what it did -- including nothing.

    A source with no candidate still appears in the derivation (F4.AC11): as disabled,
    timed out, failed, or abstaining for a stated reason.
    """

    source: InferenceSource
    status: str  # "ok", "empty", "disabled", "timeout", "error", "unavailable"
    latency_ms: int
    detail: dict[str, Any] = field(default_factory=dict)
