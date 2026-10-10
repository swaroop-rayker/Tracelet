"""The asn_profiles rebuild waits for the quiet hour (SPEC section 11 row 34, ERRORS E79).

On the e2-micro a rebuild slows every request about tenfold for six or seven minutes, so a
scheduled database update leaves the profiles stale and this job rebuilds them in the
owner's quiet hour. Staleness is derived from the versions stored with the profiles.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tracelet.config import Settings
from tracelet.inference.geodb import maintenance

# 02:30 in Asia/Kolkata (UTC+05:30) is 21:00 UTC the day before.
INSIDE = dt.datetime(2026, 10, 10, 21, 0, tzinfo=dt.UTC)
OUTSIDE = dt.datetime(2026, 10, 10, 8, 0, tzinfo=dt.UTC)  # 13:30 IST


@pytest.fixture
def settings() -> Settings:
    return Settings(reporting_tz="Asia/Kolkata", geodb_profiles_hour=2)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def stale(_: Settings) -> bool:
        seen.append("stale?")
        return True

    async def rebuild(_: Settings | None = None) -> int:
        seen.append("rebuild")
        return 3293

    monkeypatch.setattr(maintenance, "profiles_stale", stale)
    monkeypatch.setattr(maintenance, "recompute_profiles", rebuild)
    return seen


async def test_outside_the_quiet_hour_nothing_is_even_checked(
    settings: Settings, calls: list[str]
) -> None:
    assert await maintenance.run_profiles_once(settings, now=OUTSIDE) == 0
    assert calls == []


async def test_inside_the_quiet_hour_stale_profiles_are_rebuilt(
    settings: Settings, calls: list[str]
) -> None:
    assert await maintenance.run_profiles_once(settings, now=INSIDE) == 3293
    assert calls == ["stale?", "rebuild"]


async def test_inside_the_quiet_hour_current_profiles_are_left_alone(
    settings: Settings, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def current(_: Settings) -> bool:
        calls.append("stale?")
        return False

    monkeypatch.setattr(maintenance, "profiles_stale", current)
    assert await maintenance.run_profiles_once(settings, now=INSIDE) == 0
    assert calls == ["stale?"]


async def test_the_hour_is_local_to_the_reporting_zone(
    settings: Settings, calls: list[str]
) -> None:
    # 02:00 UTC is 07:30 in India: not the quiet hour there, whatever UTC says ...
    two_utc = dt.datetime(2026, 10, 10, 2, 0, tzinfo=dt.UTC)
    assert await maintenance.run_profiles_once(settings, now=two_utc) == 0
    assert calls == []
    # ... and an owner whose quiet hour is 07:00 local gets the rebuild then.
    morning = Settings(reporting_tz="Asia/Kolkata", geodb_profiles_hour=7)
    assert await maintenance.run_profiles_once(morning, now=two_utc) == 3293


def test_versions_are_what_a_rebuild_stores() -> None:
    asn = [("geolite2-asn", "/data/geoip/geolite2-asn/20261010T064748Z-ab12/GeoLite2-ASN.mmdb")]
    city = [("dbip-city-lite", "/data/geoip/dbip-city-lite/2026-10-c839d407/dbip-city-lite.mmdb")]
    assert maintenance._versions(asn, city) == {
        "geolite2-asn": "20261010T064748Z-ab12",
        "dbip-city-lite": "2026-10-c839d407",
    }
