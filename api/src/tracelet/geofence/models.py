"""The ``geofences`` table (DATA_MODEL section 6.1, migration 0009, ADR-0020).

``area`` and ``center`` are ``geography`` columns and are deliberately **unmapped**, as
``visits.geopoint`` is: every read and write of them is SQL that names its PostGIS
function, so there is no geometry library in the process at all (ADR-0002).
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from tracelet.capture.models import pg_enum, uuid7
from tracelet.db.base import Base


class ShapeKind(enum.StrEnum):
    POLYGON = "polygon"
    CIRCLE = "circle"
    REGION = "region"


class NotifyPriority(enum.StrEnum):
    HIGH = "high"
    NORMAL = "normal"
    SILENT = "silent"


class Geofence(Base):
    __tablename__ = "geofences"

    id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    shape_kind: Mapped[ShapeKind] = mapped_column(pg_enum(ShapeKind, "shape_kind"), nullable=False)
    radius_m: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    region_keys: Mapped[list[str] | None] = mapped_column(pg.ARRAY(Text))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_priority: Mapped[NotifyPriority] = mapped_column(
        pg_enum(NotifyPriority, "notify_priority"),
        nullable=False,
        default=NotifyPriority.HIGH,
    )
    link_ids: Mapped[list[uuid.UUID] | None] = mapped_column(pg.ARRAY(pg.UUID(as_uuid=True)))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
