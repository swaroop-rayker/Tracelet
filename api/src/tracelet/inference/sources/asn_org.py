"""S7 -- what the network's ASN and organisation name say (F4.AC5, F4.AC12(b)(c)).

Two jobs:

* **Classify the network** -- mobile, hosting, CGNAT -- which drives suppression rules
  (b) and (c). A curated ASN list decides first; organisation-name keywords only when the
  ASN is not listed. ``asn_profiles`` (computed after each geo-database update) overrides
  both, because it is built from the databases' own view of that ASN.
* **Propose a place** when the organisation name itself contains one ("... Pune
  Broadband ..."). Weak by nature, and priced accordingly in ``config``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from tracelet.capture.models import AsnType, ConnectionClass
from tracelet.inference.sources import SourceInput
from tracelet.inference.sources.rdns import Lexicon
from tracelet.inference.types import AsnInfo, Candidate, GeoLevel, InferenceSource


@dataclass(frozen=True, slots=True)
class AsnClasses:
    mobile: dict[int, str]
    cgnat: dict[int, str]
    hosting: dict[int, str]
    hosting_keywords: tuple[str, ...]
    mobile_keywords: tuple[str, ...]


@cache
def classes() -> AsnClasses:
    raw: dict[str, Any] = json.loads(
        resources.files("tracelet.inference.data").joinpath("asn_classes.json").read_text()
    )
    return AsnClasses(
        mobile={int(k): v for k, v in raw["mobile"].items()},
        cgnat={int(k): v for k, v in raw["cgnat"].items()},
        hosting={int(k): v for k, v in raw["hosting"].items()},
        hosting_keywords=tuple(raw["hosting_org_keywords"]),
        mobile_keywords=tuple(raw["mobile_org_keywords"]),
    )


def classify(asn: int | None, org: str | None) -> AsnInfo:
    """The network as the curated lists and the org name see it."""
    c = classes()
    name = (org or "").casefold()
    if asn is not None and asn in c.hosting:
        return AsnInfo(asn=asn, org=org, is_hosting=True)
    if asn is not None and asn in c.mobile:
        return AsnInfo(asn=asn, org=org, is_mobile=True)
    if asn is not None and asn in c.cgnat:
        return AsnInfo(asn=asn, org=org, is_cgnat=True)
    if any(k in name for k in c.hosting_keywords):
        return AsnInfo(asn=asn, org=org, is_hosting=True)
    if any(k in name for k in c.mobile_keywords):
        return AsnInfo(asn=asn, org=org, is_mobile=True)
    return AsnInfo(asn=asn, org=org)


def asn_type(info: AsnInfo) -> AsnType:
    if info.asn is None:
        return AsnType.UNKNOWN
    if info.is_hosting:
        return AsnType.HOSTING
    if info.is_mobile or info.is_cgnat:
        return AsnType.MOBILE
    return AsnType.UNKNOWN if info.org is None else AsnType.BROADBAND


def connection_class(info: AsnInfo) -> ConnectionClass:
    """Coarse and honest: only what the ASN classification actually establishes. VPN
    and Tor detection proper is M4's (F5.AC9)."""
    if info.asn is None:
        return ConnectionClass.UNKNOWN
    if info.is_hosting:
        return ConnectionClass.DATACENTER
    if info.is_mobile or info.is_cgnat:
        return ConnectionClass.MOBILE
    return ConnectionClass.BROADBAND if info.org else ConnectionClass.UNKNOWN


_WORDS = re.compile(r"[a-z]+")


async def produce(inp: SourceInput, lexicon: Lexicon) -> list[Candidate]:
    """A place named inside the organisation name, matched against the S6 lexicon's
    full city names only -- never its short codes, which would fire on ordinary words."""
    org = inp.asn.org
    if not org:
        return []
    words = set(_WORDS.findall(org.casefold()))
    entry = lexicon.match_name(words)
    if entry is None:
        return []
    return [
        Candidate(
            source=InferenceSource.ASN_ORG,
            level=GeoLevel.CITY if entry.city else GeoLevel.ADMIN1,
            country_code=entry.country_code,
            admin1=entry.admin1,
            city=entry.city,
            lat=entry.lat,
            lng=entry.lng,
            evidence={"asn": inp.asn.asn, "org": org, "matched": entry.code},
        )
    ]
