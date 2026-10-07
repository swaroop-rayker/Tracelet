"""A geo database's state and progress, as rules (SPEC section 11 row 24)."""

from __future__ import annotations

import datetime as dt

from tracelet.health import databases
from tracelet.inference.geodb.catalog import BY_NAME
from tracelet.inference.geodb.check import newer_release
from tracelet.inference.models import GeoDatabase, GeoDatabaseSettings, GeoDbStatus

NOW = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.UTC)
SPEC = BY_NAME["geolite2-city"]
IP2 = BY_NAME["ip2location-lite-db11"]


def _row(status: GeoDbStatus, *, minutes_ago: int = 60, **values: object) -> GeoDatabase:
    row = GeoDatabase(name=SPEC.name, status=status, **values)
    row.created_at = NOW - dt.timedelta(minutes=minutes_ago)
    return row


def _installed(released: dt.datetime | None = None, days_ago: int = 1) -> GeoDatabase:
    return _row(
        GeoDbStatus.INSTALLED,
        minutes_ago=days_ago * 1440,
        version="20261006T000000Z",
        released_at=released or NOW - dt.timedelta(days=days_ago),
        installed_at=NOW - dt.timedelta(days=days_ago),
    )


def _state(**overrides: object) -> tuple[str, databases.Progress | None]:
    installed = _installed()
    values: dict[str, object] = {
        "spec": SPEC,
        "configured": True,
        "installed": installed,
        "file_present": True,
        "newest": installed,
        "setting": None,
        "now": NOW,
    }
    values.update(overrides)
    return databases.state_of(**values)  # type: ignore[arg-type]  # a test's loose dict


def test_up_to_date_by_default() -> None:
    assert _state() == ("up_to_date", None)


def test_a_newer_release_is_an_update_available() -> None:
    setting = GeoDatabaseSettings(
        name=SPEC.name, latest_released_at=NOW, latest_version=None, check_error=None
    )
    assert _state(setting=setting)[0] == "update_available"


def test_an_attempt_in_flight_is_updating_with_a_percent() -> None:
    downloading = _row(
        GeoDbStatus.DOWNLOADING,
        minutes_ago=2,
        phase="downloading",
        progress_bytes=50,
        total_bytes=100,
    )
    state, progress = _state(newest=downloading)
    assert state == "updating"
    assert progress == databases.Progress("downloading", 40)  # half of the 80 % download share


def test_each_step_after_the_download_moves_the_percent_forward() -> None:
    seen: list[int] = []
    for phase in ("verifying", "unpacking", "validating", "installing"):
        percent = databases.progress_of(
            _row(GeoDbStatus.DOWNLOADING, minutes_ago=2, phase=phase)
        ).percent
        assert percent is not None
        seen.append(percent)
    assert seen == sorted(seen) and all(80 < p < 100 for p in seen)


def test_no_length_means_no_percent_yet() -> None:
    row = _row(GeoDbStatus.DOWNLOADING, minutes_ago=2, phase="downloading", progress_bytes=1234)
    assert databases.progress_of(row) == databases.Progress("downloading", None)


def test_an_abandoned_attempt_is_not_updating() -> None:
    stuck = _row(GeoDbStatus.DOWNLOADING, minutes_ago=180, phase="downloading")
    assert _state(newest=stuck)[0] != "updating"


def test_a_failed_newest_attempt_is_update_failed() -> None:
    failed = _row(GeoDbStatus.FAILED, minutes_ago=5, last_error="checksum mismatch")
    assert _state(newest=failed)[0] == "update_failed"


def test_no_credentials_or_an_unreachable_vendor_is_unable_to_update() -> None:
    assert _state(configured=False)[0] == "unable_to_update"
    unreachable = GeoDatabaseSettings(name=SPEC.name, check_error="Could not reach the vendor.")
    assert _state(setting=unreachable)[0] == "unable_to_update"


def test_never_installed_is_not_installed() -> None:
    assert _state(installed=None, newest=None, file_present=False)[0] == "not_installed"


def test_an_unchecked_database_is_due_by_its_schedule() -> None:
    old = _installed(days_ago=40)
    assert _state(spec=IP2, installed=old, newest=old)[0] == "update_available"
    fresh = _installed(days_ago=3)
    assert _state(spec=IP2, installed=fresh, newest=fresh)[0] == "up_to_date"


def test_newer_release_compares_editions_and_dates() -> None:
    assert newer_release("2026-09", None, "2026-10", None) is True
    assert newer_release("2026-10", None, "2026-10", None) is False
    assert newer_release(None, NOW, None, NOW + dt.timedelta(seconds=30)) is False  # slack
    assert newer_release(None, NOW, None, NOW + dt.timedelta(days=2)) is True
    assert newer_release(None, None, None, NOW) is False  # nothing to compare
