"""ORM models for the rollups. Mirrors migration 0007 (DATA_MODEL section 9).

If the two disagree, the migration is authoritative and this file is wrong.

The three tables share no mixin on purpose: their keys differ, and each is written by
exactly one statement in ``rollup.py``. Making the shape explicit costs some
repetition and buys the ability to read a table's definition in one place.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from tracelet.capture.models import (
    Classification,
    ConnectionClass,
    DeviceClass,
    VisitStage,
    pg_enum,
)
from tracelet.db.base import Base

__all__ = ["DailyCell", "DimDaily", "HourlyCell", "RollupState"]


def _link() -> Mapped[uuid.UUID]:
    return mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("links.id", ondelete="CASCADE"), primary_key=True
    )


class _Cell:
    """The columns both cell tables share, in migration order."""

    link_id: Mapped[uuid.UUID] = _link()
    stage: Mapped[VisitStage] = mapped_column(pg_enum(VisitStage, "visit_stage"), primary_key=True)
    classification: Mapped[Classification] = mapped_column(
        pg_enum(Classification, "classification"), primary_key=True
    )
    device_class: Mapped[DeviceClass] = mapped_column(
        pg_enum(DeviceClass, "device_class"), primary_key=True
    )
    connection_class: Mapped[ConnectionClass] = mapped_column(
        pg_enum(ConnectionClass, "connection_class"), primary_key=True
    )
    # Strict location; '' means the engine abstained (never NULL -- it is a key column).
    country_code: Mapped[str] = mapped_column(Text, primary_key=True)
    admin1: Mapped[str] = mapped_column(Text, primary_key=True)

    visit_count: Mapped[int] = mapped_column(Integer)
    consented_count: Mapped[int] = mapped_column(Integer)
    geofence_inside_count: Mapped[int] = mapped_column(Integer)
    geofence_outside_count: Mapped[int] = mapped_column(Integer)
    has_gps_count: Mapped[int] = mapped_column(Integer)
    has_point_count: Mapped[int] = mapped_column(Integer)
    inferred_count: Mapped[int] = mapped_column(Integer)
    strict_admin2_count: Mapped[int] = mapped_column(Integer)
    strict_city_count: Mapped[int] = mapped_column(Integer)
    conf_country_sum: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    conf_country_n: Mapped[int] = mapped_column(Integer)
    conf_admin1_sum: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    conf_admin1_n: Mapped[int] = mapped_column(Integer)
    conf_admin2_sum: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    conf_admin2_n: Mapped[int] = mapped_column(Integer)
    conf_city_sum: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    conf_city_n: Mapped[int] = mapped_column(Integer)


class DailyCell(_Cell, Base):
    __tablename__ = "rollup_visit_daily"

    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)


class HourlyCell(_Cell, Base):
    __tablename__ = "rollup_visit_hourly"

    # Local wall-clock hour in the reporting timezone, hence no tzinfo (ADR-0016).
    hour: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False), primary_key=True)


class DimDaily(Base):
    """One row per (day, link, classification, dimension, value): every breakdown."""

    __tablename__ = "rollup_visit_dim_daily"

    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    link_id: Mapped[uuid.UUID] = _link()
    classification: Mapped[Classification] = mapped_column(
        pg_enum(Classification, "classification"), primary_key=True
    )
    dimension: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, primary_key=True)
    visit_count: Mapped[int] = mapped_column(Integer)


class RollupState(Base):
    """A day the refresh job has rebuilt, and in which timezone."""

    __tablename__ = "rollup_state"

    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    refreshed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    reporting_tz: Mapped[str] = mapped_column(Text)
