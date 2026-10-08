"""When an update downloads, and when it does not (SPEC section 11 row 26).

The scheduler trusts a recent, successful release check over its refresh period; the Update
button asks the vendor first. Both still fetch what is missing, and both fall back when the
answer cannot be compared.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tracelet.config import Settings
from tracelet.inference.geodb import maintenance
from tracelet.inference.geodb.catalog import BY_NAME, DatabaseSpec
from tracelet.inference.geodb.check import Checked
from tracelet.inference.models import GeoDatabase, GeoDatabaseSettings, GeoDbStatus

# The scheduler's refresh period reads the clock, so these rows are relative to it.
NOW = dt.datetime.now(dt.UTC)
TODAY = NOW.date()
SPEC = BY_NAME["geolite2-city"]  # refresh period 7 days
IP2 = BY_NAME["ip2location-lite-db11"]  # never checked
RELEASED = NOW - dt.timedelta(days=40)
SETTINGS = Settings()


@pytest.fixture(autouse=True)
def _file_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(maintenance, "current_path", lambda *_: tmp_path)
    return tmp_path


def _serving(days_ago: int, name: str = SPEC.name) -> GeoDatabase:
    """Installed ``days_ago`` days ago; the release it serves is ``RELEASED``."""
    row = GeoDatabase(
        name=name,
        status=GeoDbStatus.INSTALLED,
        version="20260828T000000Z",
        released_at=RELEASED,
        installed_at=NOW - dt.timedelta(days=days_ago),
    )
    row.created_at = NOW - dt.timedelta(days=days_ago)
    return row


def _latest(days_ago: int = 40, *, failed: bool = False) -> maintenance._Latest:
    return maintenance._Latest(_serving(days_ago), failed)


def _check(
    *,
    released: dt.datetime | None = RELEASED,
    hours_ago: float = 1,
    error: str | None = None,
    name: str = SPEC.name,
) -> GeoDatabaseSettings:
    return GeoDatabaseSettings(
        name=name,
        latest_version=None,
        latest_released_at=released,
        checked_at=NOW - dt.timedelta(hours=hours_ago),
        check_error=error,
    )


def _due(
    latest: maintenance._Latest, row: GeoDatabaseSettings | None, spec: DatabaseSpec = SPEC
) -> bool:
    return maintenance.scheduled_due(spec, latest, row, SETTINGS, today=TODAY, now=NOW)


def test_a_recent_check_that_found_nothing_newer_beats_the_refresh_period() -> None:
    assert maintenance.is_due(SPEC, _latest(), SETTINGS, TODAY), "the period alone says due"
    assert _due(_latest(), _check()) is False


def test_a_recent_check_that_found_a_newer_release_downloads() -> None:
    newer = _check(released=RELEASED + dt.timedelta(days=3))
    assert _due(_latest(days_ago=1), newer) is True


def test_a_failed_or_old_check_falls_back_to_the_refresh_period() -> None:
    assert _due(_latest(), _check(error="HTTP 503")) is True
    assert _due(_latest(days_ago=1), _check(error="HTTP 503")) is False
    assert _due(_latest(), _check(hours_ago=13)) is True
    assert _due(_latest(days_ago=1), _check(hours_ago=13)) is False


def test_an_answer_that_cannot_be_compared_falls_back_to_the_refresh_period() -> None:
    assert _due(_latest(), _check(released=None)) is True
    assert _due(_latest(), None) is True
    assert _due(_latest(days_ago=1), None) is False


def test_ip2location_is_never_checked_so_its_period_decides() -> None:
    old = maintenance._Latest(_serving(40, IP2.name), False)
    assert _due(old, _check(name=IP2.name), IP2) is True


def test_missing_is_always_fetched_and_a_recent_failure_never_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _due(maintenance._Latest(None, False), _check()) is True
    assert _due(_latest(failed=True), _check(released=NOW)) is False
    monkeypatch.setattr(maintenance, "current_path", lambda *_: tmp_path / "gone")
    assert _due(_latest(days_ago=1), _check()) is True


class _Vendor:
    """Stands in for the stored latest row and the release check."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, latest: maintenance._Latest) -> None:
        self.asked: list[str] = []
        self.answer = Checked(SPEC.name, None, RELEASED, None, checked=True)

        async def fake_latest(spec: object, now: dt.datetime) -> maintenance._Latest:
            del spec, now
            return latest

        async def fake_check(spec: object, settings: Settings) -> Checked:
            del settings
            self.asked.append(getattr(spec, "name", "?"))
            return self.answer

        monkeypatch.setattr(maintenance, "_latest", fake_latest)
        monkeypatch.setattr(maintenance, "check_and_store", fake_check)


async def test_update_asks_first_and_skips_an_unchanged_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vendor = _Vendor(monkeypatch, _latest(days_ago=1))
    assert await maintenance.manual_update_due(SPEC, SETTINGS) is False
    assert vendor.asked == [SPEC.name]

    vendor.answer = Checked(SPEC.name, None, RELEASED + dt.timedelta(days=1), None, checked=True)
    assert await maintenance.manual_update_due(SPEC, SETTINGS) is True


async def test_update_downloads_when_the_answer_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vendor = _Vendor(monkeypatch, _latest(days_ago=1))
    vendor.answer = Checked(SPEC.name, None, None, "HTTP 503", checked=True)
    assert await maintenance.manual_update_due(SPEC, SETTINGS) is True
    vendor.answer = Checked(SPEC.name, None, None, None, checked=True)
    assert await maintenance.manual_update_due(SPEC, SETTINGS) is True


async def test_update_does_not_ask_about_what_it_must_fetch_anyway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vendor = _Vendor(monkeypatch, maintenance._Latest(None, False))
    assert await maintenance.manual_update_due(SPEC, SETTINGS) is True
    vendor_ip2 = _Vendor(monkeypatch, maintenance._Latest(_serving(1, IP2.name), False))
    assert await maintenance.manual_update_due(IP2, SETTINGS) is True
    assert vendor.asked == [] and vendor_ip2.asked == [], "IP2Location is metered per token"
