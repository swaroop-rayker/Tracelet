"""Versioned weights and thresholds (F4.AC14, ADR-0005 section 6).

Everything that tunes a decision lives here and is stored in ``inference_settings`` --
never in code paths. The defaults below are **version 1**, written to the table the first
time inference runs; after that the table is the only authority, and every past version
is kept so a bad retune can be rolled back and old ``inference_version`` stamps remain
interpretable.

How the numbers combine is documented in ``consensus``; what they were set to and why is
in the comments below. They are starting values, to be re-derived from ground truth in M8
(F4.AC13), not measurements.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from tracelet.inference.types import GeoLevel, InferenceSource

# Bumped whenever the consensus *algorithm* changes. The settings version is stamped
# beside it, so a visit's `inference_version` names both halves of what produced it.
ENGINE_REVISION: Final = "m3.3"
# m3.1 -- first cut; a registry-artifact city collapsed to admin1 by default.
# m3.2 -- the collapse is to country (SPEC section 11 row 11, RISKS R22), and S10 is
#         gone (row 12, R23). Visits stamped m3.1 keep meaning what they meant.
# m3.3 -- S8 (the Cloudflare edge) is its own family: routing is independent evidence
#         from the operator's naming (S6/S7), not a repeat of it.


def inference_version(settings_version: int) -> str:
    return f"{ENGINE_REVISION}+s{settings_version}"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LevelPriors(_Frozen):
    """How far a source is trusted at each level, 0..1.

    A database that is right about the country 99 % of the time can be right about the
    city half the time; one number per source cannot say that.
    """

    country: float = Field(ge=0, le=1)
    admin1: float = Field(ge=0, le=1)
    admin2: float = Field(ge=0, le=1)
    city: float = Field(ge=0, le=1)

    def at(self, level: GeoLevel) -> float:
        return float(getattr(self, level.value))


class SourceSettings(_Frozen):
    enabled: bool = True
    timeout_ms: int = Field(default=800, ge=10, le=10_000)
    priors: LevelPriors


class Thresholds(_Frozen):
    """Minimum confidence for a **strict** value at each level (F4.AC10).

    Below it the strict field is NULL with ``abstain_reason='below_threshold'``; the
    advisory field still carries the best guess.
    """

    country: float = Field(default=0.60, ge=0, le=1)
    # 0.75 with the admin1 priors below means: two agreeing databases and no dissent
    # pass; one database alone, or two against a third, abstain. Chosen on real
    # M3 lookups (Airtel Bengaluru: three databases agree, and 0.70 with the old
    # priors could never be reached), not on ground truth -- M8 re-derives it.
    admin1: float = Field(default=0.75, ge=0, le=1)
    admin2: float = Field(default=0.75, ge=0, le=1)
    city: float = Field(default=0.80, ge=0, le=1)

    def at(self, level: GeoLevel) -> float:
        return float(getattr(self, level.value))


class RegistryArtifact(_Frozen):
    """Suppression rule (a), F4.AC12(a) -- the B1 fix."""

    # A database placing at least this share of an ASN's networks on one point has
    # collapsed the ISP onto its registration address.
    min_modal_share: float = Field(default=0.30, ge=0, le=1)
    # There is deliberately no "collapse depth" here. An uncorroborated artifact city
    # voids admin1 as well, because the artifact's state is the registry's too -- B1's
    # Bangalore visitor was placed in Haryana (SPEC section 11 row 11, RISKS R22).


class InferenceConfig(_Frozen):
    sources: dict[InferenceSource, SourceSettings]
    thresholds: Thresholds = Thresholds()
    # Within one family, each further agreeing source adds this fraction of its own
    # weight: correlated witnesses corroborate a little, not a lot (types.Family).
    within_family_bonus: float = Field(default=0.25, ge=0, le=1)
    # S11: a candidate whose country contradicts the browser timezone keeps this
    # fraction of its weight (F4.AC9).
    tz_penalty: float = Field(default=0.30, ge=0, le=1)
    registry_artifact: RegistryArtifact = RegistryArtifact()
    # Coordinates from consented GPS are only trusted when the browser claims this
    # accuracy or better; a 50 km "fix" is a coarse IP guess in disguise.
    gps_max_accuracy_m: float = Field(default=5_000, gt=0)
    # Street addresses for consented visits, from Nominatim (F4.AC4). The only lookup
    # that sends coordinates rather than a network; off here stops it without touching
    # the rest of inference.
    street_address_enabled: bool = True

    def source(self, source: InferenceSource) -> SourceSettings:
        return self.sources[source]

    @classmethod
    def from_stored(cls, raw: dict[str, Any]) -> InferenceConfig:
        """Load a version from ``inference_settings``, however old.

        Versions are immutable (F4.AC14), so a key this code no longer has must be
        dropped on read, never by rewriting the row. Every retirement is listed here with
        the decision that retired it.
        """
        data = dict(raw)
        artifact = dict(data.get("registry_artifact") or {})
        artifact.pop("collapse_to", None)  # SPEC 11 row 11: always to country now
        data["registry_artifact"] = artifact
        sources = dict(data.get("sources") or {})
        sources.pop(InferenceSource.LATENCY.value, None)  # SPEC 11 row 12: S10 dropped
        data["sources"] = sources
        return cls.model_validate(data)


def _p(country: float, admin1: float, admin2: float, city: float) -> LevelPriors:
    return LevelPriors(country=country, admin1=admin1, admin2=admin2, city=city)


DEFAULT_CONFIG: Final = InferenceConfig(
    sources={
        # S1 -- the visitor's own device. Authoritative when granted (F4.AC1).
        InferenceSource.GPS: SourceSettings(timeout_ms=50, priors=_p(0.99, 0.99, 0.99, 0.99)),
        # S2-S5 -- registry-derived. Right about the country; much less about the city
        # in India (MaxMind's own figure is ~50-60 % within 50 km; ADR-0005).
        InferenceSource.GEOLITE2: SourceSettings(timeout_ms=100, priors=_p(0.95, 0.72, 0.40, 0.45)),
        InferenceSource.IP2LOCATION: SourceSettings(
            timeout_ms=100, priors=_p(0.93, 0.68, 0.35, 0.40)
        ),
        InferenceSource.IPINFO: SourceSettings(timeout_ms=100, priors=_p(0.95, 0.0, 0.0, 0.0)),
        InferenceSource.DBIP: SourceSettings(timeout_ms=100, priors=_p(0.93, 0.68, 0.35, 0.40)),
        # S6 -- a city code in the operator's own PTR record. Rare (Spike A: 2 %), and
        # the strongest free signal when present.
        InferenceSource.RDNS: SourceSettings(timeout_ms=1_200, priors=_p(0.95, 0.85, 0.5, 0.80)),
        # S7 -- a place name inside the ISP's organisation name. Weak by nature.
        InferenceSource.ASN_ORG: SourceSettings(timeout_ms=50, priors=_p(0.6, 0.35, 0.2, 0.25)),
        # S8 -- the Cloudflare edge that served the request: the nearest PoP by routing,
        # which is a metro hint, not a location (RISKS R1).
        InferenceSource.CF_COLO: SourceSettings(timeout_ms=50, priors=_p(0.85, 0.45, 0.2, 0.30)),
        # S9 -- external APIs. Registry-derived like S2-S5.
        InferenceSource.EXTERNAL_API: SourceSettings(
            timeout_ms=1_500, priors=_p(0.93, 0.68, 0.35, 0.40)
        ),
        # S11 -- never proposes, so its priors are unused; it only penalises (F4.AC9).
        InferenceSource.TIMEZONE: SourceSettings(timeout_ms=50, priors=_p(0, 0, 0, 0)),
    }
)
