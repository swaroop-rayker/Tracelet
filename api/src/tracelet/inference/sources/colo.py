"""S8 -- the Cloudflare edge that served the request (ARCHITECTURE section 3).

Server-side, so it survives the in-app webview where every client signal may be lost.
It is the nearest PoP *by routing*, which is a metro hint rather than a location: a Jio
visitor in Pune may well be served from Mumbai (RISKS R1). The priors in ``config`` say
so -- strong on the country, weak on the city.

``visits.cf_colo`` is only ever set from a verified Cloudflare peer (F13.AC6), so this
source cannot be steered by a forged ``CF-Ray`` header. On the free-subdomain path there
is no Cloudflare and no colo (ADR-0012), and the source is simply empty.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

from tracelet.inference.sources import SourceInput
from tracelet.inference.types import Candidate, GeoLevel, InferenceSource


@cache
def colos() -> dict[str, dict[str, Any]]:
    raw: dict[str, Any] = json.loads(
        resources.files("tracelet.inference.data").joinpath("cf_colos.json").read_text()
    )
    table: dict[str, dict[str, Any]] = raw["colos"]
    return table


async def produce(inp: SourceInput) -> list[Candidate]:
    if not inp.cf_colo:
        return []
    code = inp.cf_colo.strip().upper()
    entry = colos().get(code)
    if entry is None:
        return []
    return [
        Candidate(
            source=InferenceSource.CF_COLO,
            level=GeoLevel.CITY,
            country_code=entry["country"],
            admin1=entry["admin1"],
            city=entry["city"],
            lat=entry["lat"],
            lng=entry["lng"],
            evidence={"colo": code},
        )
    ]
