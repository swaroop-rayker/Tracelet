"""The data lifecycle's pure parts: when a nightly job is due, and what a preview cuts."""

from __future__ import annotations

import datetime as dt
import uuid

from tracelet.lifecycle import backups, retention
from tracelet.lifecycle.models import BackupStatus as B

IST = "Asia/Kolkata"


def _utc(year: int, month: int, day: int, hour: int, minute: int) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC)


def test_the_boundary_is_tonights_hour_once_it_has_passed() -> None:
    # 2026-10-07 04:00 IST is 2026-10-06 22:30 UTC: past 02:00 IST, so tonight's 02:00.
    assert retention.local_boundary(_utc(2026, 10, 6, 22, 30), 2, IST) == _utc(2026, 10, 6, 20, 30)


def test_before_the_hour_the_boundary_is_last_nights() -> None:
    # 2026-10-07 01:00 IST: 02:00 has not come yet, so the night before's.
    assert retention.local_boundary(_utc(2026, 10, 6, 19, 30), 2, IST) == _utc(2026, 10, 5, 20, 30)


def test_exactly_on_the_hour_is_due() -> None:
    assert retention.local_boundary(_utc(2026, 10, 6, 20, 30), 2, IST) == _utc(2026, 10, 6, 20, 30)


def test_cutoffs_follow_the_policy_and_the_fixed_alert_period() -> None:
    as_of = _utc(2026, 10, 7, 12, 0)
    cut = retention.cutoffs(as_of, retention.Policy(visit_days=180, ip_days=30, audit_days=365))
    assert cut.visits == as_of - dt.timedelta(days=180)
    assert cut.ip == as_of  # an IP goes when its own stamped expiry has passed
    assert cut.audit == as_of - dt.timedelta(days=365)
    assert cut.outbox == as_of - dt.timedelta(days=retention.OUTBOX_DONE_DAYS)


# --- backups ---------------------------------------------------------------------------


def _kept(*stamps: dt.datetime) -> list[backups.Kept]:
    return [backups.Kept(uuid.UUID(int=i + 1), s) for i, s in enumerate(stamps)]


def test_rotation_keeps_the_newest_of_each_recent_day_and_week() -> None:
    now = _utc(2026, 10, 7, 12, 0)
    nightly = [now - dt.timedelta(days=d, hours=1) for d in range(40)]  # one a night
    keep = backups.to_keep(_kept(*nightly), now=now, tz=IST, daily=7, weekly=4)
    kept = sorted((nightly[i.int - 1] for i in keep), reverse=True)
    # Seven nights, plus the newest of each of the four ISO weeks that reach further back.
    assert kept[:7] == nightly[:7]
    assert len(kept) <= 7 + 4
    assert all(now - k < dt.timedelta(weeks=5) for k in kept)


def test_rotation_keeps_only_the_newest_of_a_day_with_several() -> None:
    now = _utc(2026, 10, 7, 12, 0)
    morning, evening = now - dt.timedelta(hours=6), now - dt.timedelta(hours=1)
    keep = backups.to_keep(_kept(morning, evening), now=now, tz=IST, daily=7, weekly=4)
    assert keep == {uuid.UUID(int=2)}


def test_rotation_always_keeps_the_newest_however_old() -> None:
    now = _utc(2026, 10, 7, 12, 0)
    ancient = now - dt.timedelta(days=400)
    assert backups.to_keep(_kept(ancient), now=now, tz=IST, daily=7, weekly=4) == {uuid.UUID(int=1)}
    assert backups.to_keep([], now=now, tz=IST, daily=7, weekly=4) == set()


def test_the_monthly_check_is_due_from_the_first_of_the_month() -> None:
    # 2026-10-07 is after 1 October 04:00 IST (30 September 22:30 UTC).
    assert backups.month_boundary(_utc(2026, 10, 7, 12, 0), 4, IST) == _utc(2026, 9, 30, 22, 30)
    # 1 October 03:00 IST is before 04:00: September's boundary still applies.
    assert backups.month_boundary(_utc(2026, 9, 30, 21, 30), 4, IST) == _utc(2026, 8, 31, 22, 30)


def test_the_restore_leaves_out_extensions_and_their_data() -> None:
    listing = "\n".join(
        [
            ";",
            "4; 3079 16385 EXTENSION - postgis",
            "5; 0 0 COMMENT - EXTENSION postgis",
            "220; 1259 16400 TABLE public links tracelet_migrate",
            "4500; 0 16400 TABLE DATA public links tracelet_migrate",
            "4501; 0 16390 TABLE DATA public spatial_ref_sys tracelet",
        ]
    )
    kept = backups.restorable(listing, {"links"}).splitlines()
    assert "220; 1259 16400 TABLE public links tracelet_migrate" in kept
    assert "4500; 0 16400 TABLE DATA public links tracelet_migrate" in kept
    assert not [line for line in kept if "EXTENSION" in line or "spatial_ref_sys" in line]


def test_tonights_backup_is_due_until_one_completes() -> None:
    assert backups.nightly_due([]) is True
    assert backups.nightly_due([B.FAILED, B.FAILED]) is True  # retried
    assert backups.nightly_due([B.FAILED] * backups.NIGHTLY_ATTEMPTS) is False  # given up
    assert backups.nightly_due([B.RUNNING]) is False
    assert backups.nightly_due([B.OK]) is False
    # E70: rotation pruned it in favour of a newer backup the same day; it still ran.
    assert backups.nightly_due([B.PRUNED]) is False
