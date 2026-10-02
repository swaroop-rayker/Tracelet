"""The enrichment payload (docs/API.md section 3).

**Every field is optional and every field is a claim.** The payload is produced by
JavaScript running in the visitor's browser -- or by anyone who has read the page
source -- so it is attacker-controlled by construction. It is validated for *shape*
and *size* here and stored as reported; whether it is *true* is M4's classifier's
question, answered by cross-checking it against what the server observed.

The bounds are generous for a real device and tight for a hostile one. None of them
should ever reject a genuine phone, tablet or desktop; all of them stop a single POST
turning into an oversized row.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ShortText = Annotated[str, StringConstraints(max_length=256)]
HashText = Annotated[str, StringConstraints(max_length=128)]
# BCP 47 tags are short; 35 is the RFC's practical maximum.
LanguageTag = Annotated[str, StringConstraints(min_length=1, max_length=35)]
TimeZone = Annotated[
    str, StringConstraints(max_length=64, pattern=r"^[A-Za-z_]+(?:/[A-Za-z0-9_+-]+){0,3}$")
]


class _Part(BaseModel):
    # Unknown keys are ignored rather than rejected: a newer page talking to an older
    # API must degrade to "fewer signals", not to a 422 that loses all of them.
    model_config = ConfigDict(extra="ignore")


class Screen(_Part):
    w: int | None = Field(default=None, ge=1, le=20_000)
    h: int | None = Field(default=None, ge=1, le=20_000)
    dpr: float | None = Field(default=None, gt=0, le=16)
    colorDepth: int | None = Field(default=None, ge=1, le=64)  # noqa: N815 - wire name
    touchPoints: int | None = Field(default=None, ge=0, le=64)  # noqa: N815 - wire name


class Viewport(_Part):
    w: int | None = Field(default=None, ge=1, le=20_000)
    h: int | None = Field(default=None, ge=1, le=20_000)


class Hardware(_Part):
    cores: int | None = Field(default=None, ge=1, le=512)
    deviceMemoryGb: float | None = Field(default=None, gt=0, le=1024)  # noqa: N815 - wire name


class Gpu(_Part):
    vendor: ShortText | None = None
    renderer: ShortText | None = None


class Locale(_Part):
    tzIana: TimeZone | None = None  # noqa: N815 - wire name
    tzOffsetMin: int | None = Field(default=None, ge=-840, le=840)  # noqa: N815 - wire name
    languages: list[LanguageTag] | None = Field(default=None, max_length=16)


class Hashes(_Part):
    canvas: HashText | None = None
    audio: HashText | None = None
    font: HashText | None = None
    webgl: HashText | None = None


class Probes(_Part):
    """Accepted so the contract is stable, not yet persisted.

    What a headless-browser probe *means* is M4's decision. Storing raw values before
    that decision exists would only fix a schema around a guess.
    """

    webdriver: bool | None = None
    chromeObject: bool | None = None  # noqa: N815 - wire name
    pluginCount: int | None = Field(default=None, ge=0, le=1000)  # noqa: N815 - wire name
    permissionsAnomaly: bool | None = None  # noqa: N815 - wire name
    fontCount: int | None = Field(default=None, ge=0, le=10_000)  # noqa: N815 - wire name
    outerWidth: int | None = Field(default=None, ge=0, le=20_000)  # noqa: N815 - wire name


GeoState = Literal["granted", "denied", "prompt", "unavailable", "unsupported", "timeout"]


class Geolocation(_Part):
    state: GeoState
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    accuracyM: float | None = Field(default=None, ge=0, le=10_000_000)  # noqa: N815 - wire name


class Honeypot(_Part):
    linkClicked: bool = False  # noqa: N815 - wire name
    fieldFilled: bool = False  # noqa: N815 - wire name


class Timing(_Part):
    pageLoadMs: int | None = Field(default=None, ge=0, le=600_000)  # noqa: N815 - wire name
    collectMs: int | None = Field(default=None, ge=0, le=600_000)  # noqa: N815 - wire name


class EnrichmentPayload(_Part):
    screen: Screen | None = None
    viewport: Viewport | None = None
    hardware: Hardware | None = None
    gpu: Gpu | None = None
    locale: Locale | None = None
    hashes: Hashes | None = None
    probes: Probes | None = None
    geolocation: Geolocation | None = None
    honeypot: Honeypot | None = None
    timing: Timing | None = None


# Large enough for any real payload several times over; small enough that the endpoint
# cannot be used to write kilobytes per request. Caddy's 64 KB body cap is the outer
# bound (L1); this is the route's own.
MAX_ENRICHMENT_BYTES = 16 * 1024
