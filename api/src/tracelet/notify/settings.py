"""Notification settings in ``app_settings`` (DATA_MODEL section 8.6). No secrets.

Two keys: quiet hours (F7.AC9) and, since M7.5, the alert types (F7.AC10-F7.AC15). The bot
token and the chat id are secrets and deployment facts, and stay in the environment
(F12.AC3); this module only reports whether they are set.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any, Final

from sqlalchemy import DateTime, ForeignKey, Text, func, select
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tracelet.db.base import Base
from tracelet.notify import alerts
from tracelet.notify.alerts import QuietHours

QUIET_HOURS_KEY: Final = "notifications.quiet_hours"
ALERT_TYPES_KEY: Final = "notifications.alert_types"


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _quiet_hours(value: dict[str, Any] | None) -> QuietHours:
    """A stored value read leniently: a missing or damaged field takes its default, so
    a bad row can never stop alerts from going out."""
    if not value:
        return QuietHours()
    default = QuietHours()
    start = value.get("start", default.start)
    end = value.get("end", default.end)
    return QuietHours(
        enabled=bool(value.get("enabled", default.enabled)),
        start=start if isinstance(start, str) and QuietHours.valid_time(start) else default.start,
        end=end if isinstance(end, str) and QuietHours.valid_time(end) else default.end,
        timezone=str(value.get("timezone") or default.timezone),
    )


async def quiet_hours(db: AsyncSession) -> QuietHours:
    row = (
        await db.execute(select(AppSetting.value).where(AppSetting.key == QUIET_HOURS_KEY))
    ).scalar_one_or_none()
    return _quiet_hours(row)


async def _store(
    db: AsyncSession, key: str, value: dict[str, Any], admin_id: uuid.UUID | None
) -> None:
    await db.execute(
        pg.insert(AppSetting)
        .values(key=key, value=value, updated_by=admin_id)
        .on_conflict_do_update(
            index_elements=[AppSetting.key],
            set_={"value": value, "updated_by": admin_id, "updated_at": func.now()},
        )
    )


async def set_quiet_hours(
    db: AsyncSession, value: QuietHours, *, admin_id: uuid.UUID | None
) -> QuietHours:
    """Store ``value``; returns the previous one, for the audit row. ``admin_id`` is
    ``None`` only for a system write."""
    previous = await quiet_hours(db)
    await _store(db, QUIET_HOURS_KEY, dataclasses.asdict(value), admin_id)
    return previous


# ---------------------------------------------------------------------------
# Alert types (F7.AC10-F7.AC15). Read leniently, like quiet hours.
# ---------------------------------------------------------------------------


def _section(value: dict[str, Any] | None, name: str) -> dict[str, Any]:
    section = (value or {}).get(name)
    return section if isinstance(section, dict) else {}


def _int_in(raw: object, bounds: tuple[int, int], default: int) -> int:
    if isinstance(raw, int) and not isinstance(raw, bool) and bounds[0] <= raw <= bounds[1]:
        return raw
    return default


def _float_in(raw: object, bounds: tuple[float, float], default: float) -> float:
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        number = float(raw)
        if bounds[0] <= number <= bounds[1]:
            return number
    return default


def _alert_types(value: dict[str, Any] | None) -> alerts.AlertTypes:
    """A missing or damaged field takes its default -- which is "off", so a damaged row
    can only ever make Tracelet quieter."""
    d, s, n, r = (
        _section(value, "digest"),
        _section(value, "spike"),
        _section(value, "new_place"),
        _section(value, "returning"),
    )
    digest_at = d.get("at")
    defaults = alerts.AlertTypes()
    return alerts.AlertTypes(
        digest=alerts.DigestType(
            enabled=d.get("enabled") is True,
            at=digest_at
            if isinstance(digest_at, str) and QuietHours.valid_time(digest_at)
            else defaults.digest.at,
        ),
        spike=alerts.SpikeType(
            enabled=s.get("enabled") is True,
            floor=_int_in(s.get("floor"), alerts.SPIKE_FLOOR_RANGE, defaults.spike.floor),
            k=_float_in(s.get("k"), alerts.SPIKE_K_RANGE, defaults.spike.k),
        ),
        new_place=alerts.NewPlaceType(enabled=n.get("enabled") is True),
        returning=alerts.ReturningType(
            enabled=r.get("enabled") is True,
            after_days=_int_in(
                r.get("after_days"), alerts.RETURNING_DAYS_RANGE, defaults.returning.after_days
            ),
        ),
    )


async def alert_types(db: AsyncSession) -> alerts.AlertTypes:
    row = (
        await db.execute(select(AppSetting.value).where(AppSetting.key == ALERT_TYPES_KEY))
    ).scalar_one_or_none()
    return _alert_types(row)


async def set_alert_types(
    db: AsyncSession, value: alerts.AlertTypes, *, admin_id: uuid.UUID | None
) -> alerts.AlertTypes:
    """Store ``value``; returns the previous one, for the audit row."""
    previous = await alert_types(db)
    await _store(db, ALERT_TYPES_KEY, dataclasses.asdict(value), admin_id)
    return previous
