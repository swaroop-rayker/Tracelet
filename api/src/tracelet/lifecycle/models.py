"""Tables of the data lifecycle (migration 0012, DATA_MODEL sections 8.5 and 8.8)."""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, SmallInteger, Text, func
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from tracelet.capture.models import pg_enum, uuid7
from tracelet.db.base import Base


class BackupKind(enum.StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class BackupStatus(enum.StrEnum):
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    PRUNED = "pruned"


class RestoreStatus(enum.StrEnum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class RetentionPolicy(Base):
    __tablename__ = "retention_policy"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    visit_days: Mapped[int] = mapped_column(Integer, nullable=False)
    ip_days: Mapped[int] = mapped_column(Integer, nullable=False)
    audit_days: Mapped[int] = mapped_column(Integer, nullable=False)
    rollup_forever: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Backup(Base):
    __tablename__ = "backups"

    id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True, default=uuid7)
    kind: Mapped[BackupKind] = mapped_column(pg_enum(BackupKind, "backup_kind"), nullable=False)
    status: Mapped[BackupStatus] = mapped_column(
        pg_enum(BackupStatus, "backup_status"), nullable=False, default=BackupStatus.RUNNING
    )
    file_name: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(Text)
    row_counts: Mapped[dict[str, Any] | None] = mapped_column(pg.JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    last_downloaded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class RestoreCheck(Base):
    __tablename__ = "restore_checks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    backup_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    kind: Mapped[BackupKind] = mapped_column(pg_enum(BackupKind, "backup_kind"), nullable=False)
    status: Mapped[RestoreStatus] = mapped_column(
        pg_enum(RestoreStatus, "restore_status"), nullable=False, default=RestoreStatus.RUNNING
    )
    mismatches: Mapped[dict[str, Any] | None] = mapped_column(pg.JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
