"""S1 -- consented browser geolocation (F4.AC1, F4.AC3).

Only ever present when ``consent_state='granted'``: the database refuses coordinates
without consent (``ck_visits_gps_requires_consent``), so this source cannot see any.

The browser gives a coordinate, not a place. Naming the place -- country, state, city --
is reverse geocoding, which ``geonames`` does offline (F4.AC4); until it has run, the
candidate is a bare point, which carries weight in no vote.
"""

from __future__ import annotations

from tracelet.inference.config import InferenceConfig
from tracelet.inference.sources import SourceInput
from tracelet.inference.types import Candidate, GeoLevel, InferenceSource


async def produce(inp: SourceInput, config: InferenceConfig) -> list[Candidate]:
    gps = inp.gps
    if gps is None:
        return []
    evidence: dict[str, object] = {"accuracy_m": gps.accuracy_m}
    confidence = 1.0
    if gps.accuracy_m is None or gps.accuracy_m > config.gps_max_accuracy_m:
        # A browser "fix" tens of kilometres wide is itself an IP guess. It is kept, and
        # visible, but it does not get to outrank the network.
        confidence = 0.3
        evidence["coarse"] = True
    return [
        Candidate(
            source=InferenceSource.GPS,
            level=GeoLevel.POINT,
            lat=gps.lat,
            lng=gps.lng,
            raw_confidence=confidence,
            evidence=evidence,
        )
    ]
