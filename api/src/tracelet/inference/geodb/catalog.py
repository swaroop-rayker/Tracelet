"""Every offline database M3 knows how to fetch (F4.AC5, F10.AC3).

A spec says where the file comes from, what credentials it needs, how it is packed, how
to tell a good download from a bad one, and which source reads it. Nothing here does
I/O; ``installer`` does.

Credentials are read from ``Settings`` at the moment of download and never stored,
logged or put anywhere a URL is recorded: two vendors take their token as a query
parameter, so the URL itself is a secret and is never logged (F12.AC13).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Literal

from tracelet.config import Settings
from tracelet.inference.types import InferenceSource

Packing = Literal["none", "gz", "tar.gz", "zip"]
Kind = Literal["mmdb", "ip2location_bin", "geonames_cities", "geonames_admin1", "tor_exits"]


@dataclass(frozen=True, slots=True)
class Download:
    # Neither appears in repr: two vendors carry their token in the URL and MaxMind
    # sends its licence key as basic auth, and a repr ends up in tracebacks and test
    # output (ERRORS.md E31).
    url: str = field(repr=False)
    auth: tuple[str, str] | None = field(default=None, repr=False)
    # A URL that yields the expected SHA-256 of the artifact, where the vendor
    # publishes one. Otherwise integrity rests on the archive's own CRC and on
    # validation opening the file and answering known lookups.
    sha256_url: str | None = None
    version: str | None = None
    # DB-IP publishes a month's file some days into the month; until then, last month's.
    fallback: Download | None = None


UrlBuilder = Callable[[Settings, dt.date], Download | None]


@dataclass(frozen=True, slots=True)
class DatabaseSpec:
    name: str
    kind: Kind
    packing: Packing
    member: str | None  # the file inside an archive; None for a single-file download
    installed_as: str  # the file name under the version directory
    feeds: InferenceSource | None
    attribution: str
    staleness_days: int
    # Refuse anything larger, compressed or not: a runaway download must not fill the
    # 30 GB disk (CLAUDE.md section 5).
    max_bytes: int
    # For an .mmdb: the substring its metadata.database_type must contain.
    database_type: str | None = None

    def download(self, settings: Settings, today: dt.date) -> Download | None:
        """Where to fetch it today, or ``None`` when its credentials are not configured."""
        return _URLS[self.name](settings, today)


def _month(today: dt.date) -> str:
    return f"{today.year:04d}-{today.month:02d}"


def _secret(value: object) -> str | None:
    raw = getattr(value, "get_secret_value", lambda: value)()
    return str(raw) if raw else None


def _dbip(kind: str) -> UrlBuilder:
    def one(month: dt.date) -> Download:
        version = _month(month)
        return Download(
            url=f"https://download.db-ip.com/free/dbip-{kind}-lite-{version}.mmdb.gz",
            version=version,
        )

    def build(settings: Settings, today: dt.date) -> Download:
        del settings
        last_month = today.replace(day=1) - dt.timedelta(days=1)
        current = one(today)
        return Download(current.url, version=current.version, fallback=one(last_month))

    return build


def _maxmind(edition: str) -> UrlBuilder:
    def build(settings: Settings, today: dt.date) -> Download | None:
        del today
        account, key = settings.maxmind_account_id, _secret(settings.maxmind_license_key)
        if not account or not key:
            return None
        base = f"https://download.maxmind.com/geoip/databases/{edition}/download"
        return Download(
            url=f"{base}?suffix=tar.gz",
            auth=(account, key),
            sha256_url=f"{base}?suffix=tar.gz.sha256",
        )

    return build


def _ip2location(settings: Settings, today: dt.date) -> Download | None:
    del today
    token = _secret(settings.ip2location_token)
    if not token:
        return None
    # The IPv6 edition carries IPv4 as well, so one file answers both.
    return Download(url=f"https://www.ip2location.com/download/?token={token}&file=DB11LITEBINIPV6")


def _ipinfo(settings: Settings, today: dt.date) -> Download | None:
    del today
    token = _secret(settings.ipinfo_token)
    if not token:
        return None
    return Download(url=f"https://ipinfo.io/data/ipinfo_lite.mmdb?token={token}")


def _geonames(file: str) -> UrlBuilder:
    def build(settings: Settings, today: dt.date) -> Download:
        del settings, today
        return Download(url=f"https://download.geonames.org/export/dump/{file}")

    return build


def _tor(settings: Settings, today: dt.date) -> Download:
    del settings, today
    return Download(url="https://check.torproject.org/torbulkexitlist")


MB: Final = 1024 * 1024

CATALOG: Final[tuple[DatabaseSpec, ...]] = (
    DatabaseSpec(
        name="dbip-city-lite",
        kind="mmdb",
        packing="gz",
        member=None,
        installed_as="dbip-city-lite.mmdb",
        feeds=InferenceSource.DBIP,
        attribution="IP geolocation by DB-IP (db-ip.com), CC BY 4.0",
        staleness_days=45,
        max_bytes=400 * MB,
        database_type="City",
    ),
    DatabaseSpec(
        name="dbip-asn-lite",
        kind="mmdb",
        packing="gz",
        member=None,
        installed_as="dbip-asn-lite.mmdb",
        feeds=None,  # the network, not a place: S7 and rules (a)-(c)
        attribution="ASN data by DB-IP (db-ip.com), CC BY 4.0",
        staleness_days=45,
        max_bytes=100 * MB,
        database_type="ASN",
    ),
    DatabaseSpec(
        name="geolite2-city",
        kind="mmdb",
        packing="tar.gz",
        member="GeoLite2-City.mmdb",
        installed_as="GeoLite2-City.mmdb",
        feeds=InferenceSource.GEOLITE2,
        attribution="GeoLite2 data created by MaxMind (maxmind.com)",
        staleness_days=14,
        max_bytes=200 * MB,
        database_type="GeoLite2-City",
    ),
    DatabaseSpec(
        name="geolite2-asn",
        kind="mmdb",
        packing="tar.gz",
        member="GeoLite2-ASN.mmdb",
        installed_as="GeoLite2-ASN.mmdb",
        feeds=None,
        attribution="GeoLite2 data created by MaxMind (maxmind.com)",
        staleness_days=14,
        max_bytes=50 * MB,
        database_type="GeoLite2-ASN",
    ),
    DatabaseSpec(
        name="ip2location-lite-db11",
        kind="ip2location_bin",
        packing="zip",
        member="IP2LOCATION-LITE-DB11.IPV6.BIN",
        installed_as="IP2LOCATION-LITE-DB11.IPV6.BIN",
        feeds=InferenceSource.IP2LOCATION,
        attribution="IP2Location LITE data (lite.ip2location.com)",
        staleness_days=45,
        max_bytes=400 * MB,
    ),
    DatabaseSpec(
        name="ipinfo-lite",
        kind="mmdb",
        packing="none",
        member=None,
        installed_as="ipinfo_lite.mmdb",
        feeds=InferenceSource.IPINFO,
        attribution="IP address data powered by IPinfo (ipinfo.io)",
        staleness_days=14,
        max_bytes=200 * MB,
        database_type="ipinfo",
    ),
    DatabaseSpec(
        name="geonames-cities1000",
        kind="geonames_cities",
        packing="zip",
        member="cities1000.txt",
        installed_as="cities1000.txt",
        feeds=None,  # names every place: reverse geocoding (F4.AC4)
        attribution="Place names from GeoNames (geonames.org), CC BY 4.0",
        staleness_days=120,
        max_bytes=100 * MB,
    ),
    DatabaseSpec(
        name="tor-exits",
        kind="tor_exits",
        packing="none",
        member=None,
        installed_as="torbulkexitlist.txt",
        feeds=None,  # classification and location rule (c), not a place (M4, F5.AC9)
        attribution="Tor exit list from the Tor Project (check.torproject.org)",
        staleness_days=3,
        max_bytes=5 * MB,
    ),
    DatabaseSpec(
        name="geonames-admin1",
        kind="geonames_admin1",
        packing="none",
        member=None,
        installed_as="admin1CodesASCII.txt",
        feeds=None,
        attribution="Place names from GeoNames (geonames.org), CC BY 4.0",
        staleness_days=365,
        max_bytes=5 * MB,
    ),
)

BY_NAME: Final = {spec.name: spec for spec in CATALOG}

_URLS: Final[dict[str, UrlBuilder]] = {
    "dbip-city-lite": _dbip("city"),
    "dbip-asn-lite": _dbip("asn"),
    "geolite2-city": _maxmind("GeoLite2-City"),
    "geolite2-asn": _maxmind("GeoLite2-ASN"),
    "ip2location-lite-db11": _ip2location,
    "ipinfo-lite": _ipinfo,
    "geonames-cities1000": _geonames("cities1000.zip"),
    "geonames-admin1": _geonames("admin1CodesASCII.txt"),
    "tor-exits": _tor,
}
