"""Notification settings in ``app_settings`` (DATA_MODEL section 8.6). No secrets.

Today one key: quiet hours (F7.AC9). The bot token and the chat id are secrets and
deployment facts, and stay in the environment (F12.AC3); this module only reports
whether they are set.
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
from tracelet.notify.alerts import QuietHours

QUIET_HOURS_KEY: Final = "notifications.quiet_hours"


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


async def set_quiet_hours(
    db: AsyncSession, value: QuietHours, *, admin_id: uuid.UUID | None
) -> QuietHours:
    """Store ``value``; returns the previous one, for the audit row. ``admin_id`` is
    ``None`` only for a system write."""
    previous = await quiet_hours(db)
    await db.execute(
        pg.insert(AppSetting)
        .values(key=QUIET_HOURS_KEY, value=dataclasses.asdict(value), updated_by=admin_id)
        .on_conflict_do_update(
            index_elements=[AppSetting.key],
            set_={
                "value": dataclasses.asdict(value),
                "updated_by": admin_id,
                "updated_at": func.now(),
            },
        )
    )
    return previous
