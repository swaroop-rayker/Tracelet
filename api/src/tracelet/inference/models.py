"""ORM models for location inference. Mirrors migration 0005.

If this file and the migration disagree, the migration is authoritative: the engine
enforces the invariants (every rejected candidate has a reason; one active settings
version; one installed version per geo database), not these classes.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from decimal import Decimal
from ipaddress import IPv4Interface, IPv6Interface
from typing import Any

from sqlalchemy import CHAR, BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from tracelet.capture.models import AsnType
from tracelet.db.base import Base
from tracelet.inference.types import GeoLevel, InferenceSource


def _pg_enum(py_enum: type[enum.StrEnum], name: str) -> pg.ENUM:
    return pg.ENUM(
        py_enum, name=name, create_type=False, values_callable=lambda e: [m.value for m in e]
    )


class GeoDbStatus(enum.StrEnum):
    INSTALLED = "installed"
    DOWNLOADING = "downloading"
    FAILED = "failed"
    STALE = "stale"


INFERENCE_SOURCE = _pg_enum(InferenceSource, "inference_source")


class VisitCandidate(Base):
    """One row of the derivation trail (DATA_MODEL section 5.4)."""

    __tablename__ = "visit_candidates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    visit_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("visits.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[InferenceSource] = mapped_column(INFERENCE_SOURCE, nullable=False)
    level: Mapped[GeoLevel] = mapped_column(_pg_enum(GeoLevel, "geo_level"), nullable=False)
    country_code: Mapped[str | None] = mapped_column(CHAR(2))
    admin1: Mapped[str | None] = mapped_column(Text)
    admin2: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    raw_confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    effective_weight: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    suppressed_reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False, default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    produced_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AsnProfile(Base):
    """The B1 fix, precomputed (DATA_MODEL section 8.1)."""

    __tablename__ = "asn_profiles"

    asn: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    org: Mapped[str | None] = mapped_column(Text)
    asn_type: Mapped[AsnType] = mapped_column(
        _pg_enum(AsnType, "asn_type"), nullable=False, default=AsnType.UNKNOWN
    )
    modal_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    modal_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    modal_city: Mapped[str | None] = mapped_column(Text)
    modal_admin1: Mapped[str | None] = mapped_column(Text)
    modal_share: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    is_registry_artifact_source: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_mobile: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_hosting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_cgnat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source_db_versions: Mapped[dict[str, Any]] = mapped_column(
        pg.JSONB, nullable=False, default=dict
    )


class RdnsCityCode(Base):
    """One entry of the S6 lexicon (DATA_MODEL section 8.2)."""

    __tablename__ = "rdns_city_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str | None] = mapped_column(Text)
    admin1: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    isp_hint: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    lexicon_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GeoDatabase(Base):
    """One installed, failed or in-flight offline database (DATA_MODEL section 8.3)."""

    __tablename__ = "geo_databases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str | None] = mapped_column(Text)
    released_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    installed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    file_path: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[GeoDbStatus] = mapped_column(
        _pg_enum(GeoDbStatus, "geo_db_status"), nullable=False
    )
    last_check_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    staleness_threshold_days: Mapped[int] = mapped_column(Integer, nullable=False, default=45)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # An attempt in flight says how far it has got (SPEC section 11 row 24, migration 0013).
    phase: Mapped[str | None] = mapped_column(Text)
    progress_bytes: Mapped[int | None] = mapped_column(BigInteger)
    total_bytes: Mapped[int | None] = mapped_column(BigInteger)


class GeoDatabaseSettings(Base):
    """Per database: whether the scheduler updates it, and the last release check
    (DATA_MODEL section 8.3, SPEC section 11 row 24)."""

    __tablename__ = "geo_database_settings"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    auto_update: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    latest_version: Mapped[str | None] = mapped_column(Text)
    latest_released_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    check_error: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(pg.UUID(as_uuid=True))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class InferenceSettingsVersion(Base):
    """One retained version of the tuning (DATA_MODEL section 8.4, F4.AC14).

    The application role can insert rows and move ``is_active``; it cannot rewrite or
    delete a version (migration 0005).
    """

    __tablename__ = "inference_settings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    settings: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GeoCacheEntry(Base):
    """An external lookup cached by prefix (DATA_MODEL section 9.3, F4.AC7)."""

    __tablename__ = "geo_cache"

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    ip_prefix: Mapped[IPv4Interface | IPv6Interface] = mapped_column(pg.INET, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
