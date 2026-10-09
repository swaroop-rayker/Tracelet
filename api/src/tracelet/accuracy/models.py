"""Ground-truth labels and accuracy runs (DATA_MODEL section 10, migration 0018).

A label is truth *beside* a visit: nothing here writes to ``visits``. The CHECKs in
migration 0018 hold the bounds the API validates.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import CHAR, Boolean, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from tracelet.capture.models import uuid7
from tracelet.db.base import Base

CONNECTION_KINDS: Final = ("wifi", "mobile_data", "ethernet")
NETWORKS: Final = ("airtel", "jio", "vi", "bsnl", "act", "other")
PLACE_MAX: Final = 100
NOTES_MAX: Final = 500
RUN_NOTE_MAX: Final = 200


class GroundTruthLabel(Base):
    __tablename__ = "ground_truth_labels"

    id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True, default=uuid7)
    visit_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True),
        ForeignKey("visits.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    cant_tell: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    true_country_code: Mapped[str | None] = mapped_column(CHAR(2))
    true_admin1: Mapped[str | None] = mapped_column(Text)
    true_admin2: Mapped[str | None] = mapped_column(Text)
    true_city: Mapped[str | None] = mapped_column(Text)
    true_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    true_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    connection_kind: Mapped[str | None] = mapped_column(Text)
    vpn_used: Mapped[bool | None] = mapped_column(Boolean)
    network: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    labeled_by: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL")
    )
    labeled_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AccuracyRun(Base):
    """Append-only: the application role cannot UPDATE or DELETE (migration 0018)."""

    __tablename__ = "accuracy_runs"

    id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True, default=uuid7)
    run_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    settings_version: Mapped[int] = mapped_column(Integer, nullable=False)
    inference_version: Mapped[str] = mapped_column(Text, nullable=False)
    classifier_version: Mapped[str] = mapped_column(Text, nullable=False)
    git_sha: Mapped[str | None] = mapped_column(Text)
    label_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)
