"""The data lifecycle's pure parts: when a nightly job is due, and what a preview cuts."""

from __future__ import annotations

import datetime as dt

from tracelet.lifecycle import retention

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
