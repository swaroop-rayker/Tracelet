"""ORM models for the capture path: tracking links and visits.

Mirrors migration 0004. If the two disagree, the migration is authoritative and this
file is wrong -- the database enforces the invariants, not these classes
(docs/DATA_MODEL.md sections 4 and 5).

`visits.geopoint` is deliberately unmapped. It is a PostGIS `geography` column with no
core SQLAlchemy type, geoalchemy2 is an M6 dependency (ES5), and nothing before M6
reads or writes it.
"""

from __future__ import annotations

import datetime as dt
import enum
import os
import time
import uuid
from decimal import Decimal
from ipaddress import IPv4Interface, IPv6Interface
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from tracelet.db.base import Base

__all__ = [
    "AsnType",
    "Classification",
    "ConnectionClass",
    "ConsentState",
    "DeviceClass",
    "GeofenceState",
    "Link",
    "Visit",
    "VisitStage",
    "uuid7",
]


def uuid7() -> uuid.UUID:
    """A time-ordered UUID (RFC 9562 version 7).

    Visits are inserted in time order and read newest-first, so a random UUID4 key
    scatters inserts across the whole B-tree while a v7 key appends to its right edge.
    Python 3.14 adds ``uuid.uuid7``; this is the 3.13 equivalent, and small enough
    that a dependency for it would not pass ES5.
    """
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & 0xFFFF_FFFF_FFFF) << 80 | rand
    value = (value & ~(0xF << 76)) | (0x7 << 76)  # version 7
    value = (value & ~(0x3 << 62)) | (0x2 << 62)  # RFC 9562 variant
    return uuid.UUID(int=value)


# ---------------------------------------------------------------------------
# Enumerations -- values match migration 0004 exactly
# ---------------------------------------------------------------------------


class VisitStage(enum.StrEnum):
    """How complete a visit is (F3.AC6).

    ``server`` is the only non-final stage: the row exists and is waiting for either
    enrichment or the sweeper. A CHECK constraint makes that equivalence structural.
    """

    SERVER = "server"
    ENRICHED = "enriched"
    SERVER_ONLY = "server_only"
    RATE_LIMITED = "rate_limited"


class Classification(enum.StrEnum):
    HUMAN = "human"
    BOT = "bot"
    CRAWLER = "crawler"
    DATACENTER = "datacenter"
    SPAM = "spam"
    SPOOFED = "spoofed"
    UNKNOWN = "unknown"


class ConsentState(enum.StrEnum):
    GRANTED = "granted"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    NOT_ASKED = "not_asked"
    BLOCKED_BY_WEBVIEW = "blocked_by_webview"


class ConnectionClass(enum.StrEnum):
    BROADBAND = "broadband"
    MOBILE = "mobile"
    DATACENTER = "datacenter"
    VPN_SUSPECTED = "vpn_suspected"
    TOR = "tor"
    BUSINESS = "business"
    UNKNOWN = "unknown"


class AsnType(enum.StrEnum):
    BROADBAND = "broadband"
    MOBILE = "mobile"
    HOSTING = "hosting"
    BUSINESS = "business"
    EDUCATION = "education"
    GOVERNMENT = "government"
    UNKNOWN = "unknown"


class DeviceClass(enum.StrEnum):
    MOBILE = "mobile"
    TABLET = "tablet"
    DESKTOP = "desktop"
    TV = "tv"
    SERVER = "server"
    BOT = "bot"
    UNKNOWN = "unknown"


class GeofenceState(enum.StrEnum):
    INSIDE = "inside"
    OUTSIDE = "outside"
    UNDETERMINED = "undetermined"


def _pg_enum(py_enum: type[enum.StrEnum], name: str) -> pg.ENUM:
    return pg.ENUM(
        py_enum, name=name, create_type=False, values_callable=lambda e: [m.value for m in e]
    )


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------


class Link(Base):
    """A tracking link (F1).

    ``destination_url`` is the only source of a redirect target anywhere in the system
    (F1.AC7, F13.AC3). Nothing a visitor sends -- query, header or path -- ever chooses
    where they are sent.
    """

    __tablename__ = "links"

    id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True, default=uuid7)
    slug: Mapped[str] = mapped_column(pg.CITEXT, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    destination_url: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_policy: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False)
    interstitial_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=700)
    cloned_from: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("links.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_live(self) -> bool:
        """Reachable from the capture surface (F1.AC4, F2.AC14)."""
        return self.is_active and self.archived_at is None

    def __repr__(self) -> str:
        # No destination: it may carry a campaign parameter someone considers private.
        return f"Link(id={self.id}, slug={self.slug!r}, live={self.is_live})"


# ---------------------------------------------------------------------------
# visits
# ---------------------------------------------------------------------------


class Visit(Base):
    """One request to the capture surface (F2, F3).

    **There is no plaintext IP attribute**, because there is no plaintext IP column
    (ADR-0007). ``ip_enc`` is AES-256-GCM with the visit id as AAD; ``ip_hmac`` and
    ``ip_prefix`` are the durable, non-reversible forms.
    """

    __tablename__ = "visits"

    # --- identity and lifecycle ---------------------------------------------
    id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), primary_key=True, default=uuid7)
    link_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("links.id", ondelete="RESTRICT"), nullable=False
    )
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    stage: Mapped[VisitStage] = mapped_column(_pg_enum(VisitStage, "visit_stage"), nullable=False)
    finalized_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    enrichment_consumed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str | None] = mapped_column(Text)

    # --- network ------------------------------------------------------------
    ip_hmac: Mapped[bytes | None] = mapped_column(LargeBinary)
    # Annotated as the driver returns it (docs/ERRORS.md E13): compare via str().
    ip_prefix: Mapped[IPv4Interface | IPv6Interface | None] = mapped_column(pg.INET)
    ip_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    ip_key_version: Mapped[int | None] = mapped_column(SmallInteger)
    ip_purge_after: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    asn: Mapped[int | None] = mapped_column(Integer)
    asn_org: Mapped[str | None] = mapped_column(Text)
    asn_type: Mapped[AsnType] = mapped_column(
        _pg_enum(AsnType, "asn_type"), nullable=False, default=AsnType.UNKNOWN
    )
    rdns_ptr: Mapped[str | None] = mapped_column(Text)
    connection_class: Mapped[ConnectionClass] = mapped_column(
        _pg_enum(ConnectionClass, "connection_class"),
        nullable=False,
        default=ConnectionClass.UNKNOWN,
    )
    is_datacenter: Mapped[bool | None] = mapped_column(Boolean)
    is_vpn_suspected: Mapped[bool | None] = mapped_column(Boolean)
    is_tor: Mapped[bool | None] = mapped_column(Boolean)
    is_proxy_suspected: Mapped[bool | None] = mapped_column(Boolean)
    cf_colo: Mapped[str | None] = mapped_column(CHAR(3))
    cf_country: Mapped[str | None] = mapped_column(CHAR(2))

    # --- client identity (ADR-0006, from M4) --------------------------------
    visitor_id: Mapped[bytes | None] = mapped_column(LargeBinary)
    session_fp: Mapped[bytes | None] = mapped_column(LargeBinary)
    fingerprint_id: Mapped[bytes | None] = mapped_column(LargeBinary)

    # --- device and browser -------------------------------------------------
    user_agent: Mapped[str | None] = mapped_column(Text)
    ua_family: Mapped[str | None] = mapped_column(Text)
    ua_version: Mapped[str | None] = mapped_column(Text)
    os_family: Mapped[str | None] = mapped_column(Text)
    os_version: Mapped[str | None] = mapped_column(Text)
    device_class: Mapped[DeviceClass] = mapped_column(
        _pg_enum(DeviceClass, "device_class"), nullable=False, default=DeviceClass.UNKNOWN
    )
    is_inapp_webview: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    webview_host: Mapped[str | None] = mapped_column(Text)
    screen_w: Mapped[int | None] = mapped_column(Integer)
    screen_h: Mapped[int | None] = mapped_column(Integer)
    viewport_w: Mapped[int | None] = mapped_column(Integer)
    viewport_h: Mapped[int | None] = mapped_column(Integer)
    dpr: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    color_depth: Mapped[int | None] = mapped_column(Integer)
    touch_points: Mapped[int | None] = mapped_column(Integer)
    cpu_cores: Mapped[int | None] = mapped_column(Integer)
    device_memory_gb: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    gpu_vendor: Mapped[str | None] = mapped_column(Text)
    gpu_renderer: Mapped[str | None] = mapped_column(Text)
    languages: Mapped[list[str] | None] = mapped_column(pg.ARRAY(Text))
    tz_iana: Mapped[str | None] = mapped_column(Text)
    tz_offset_min: Mapped[int | None] = mapped_column(Integer)
    canvas_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    audio_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    font_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    webgl_hash: Mapped[bytes | None] = mapped_column(LargeBinary)

    # --- classification ------------------------------------------------------
    classification: Mapped[Classification] = mapped_column(
        _pg_enum(Classification, "classification"),
        nullable=False,
        default=Classification.UNKNOWN,
    )
    bot_score: Mapped[int | None] = mapped_column(SmallInteger)
    spoof_score: Mapped[int | None] = mapped_column(SmallInteger)
    agreement_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    conflict_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    honeypot_tripped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    header_order_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    http_version: Mapped[str | None] = mapped_column(Text)
    tls_version: Mapped[str | None] = mapped_column(Text)
    classifier_version: Mapped[str | None] = mapped_column(Text)
    signals: Mapped[list[dict[str, Any]]] = mapped_column(pg.JSONB, nullable=False, default=list)
    request_headers: Mapped[dict[str, str] | None] = mapped_column(pg.JSONB)

    # --- location (M3) ------------------------------------------------------
    consent_state: Mapped[ConsentState] = mapped_column(
        _pg_enum(ConsentState, "consent_state"),
        nullable=False,
        default=ConsentState.NOT_ASKED,
    )
    gps_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    gps_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    gps_accuracy_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    resolved_address: Mapped[str | None] = mapped_column(Text)
    strict_country_code: Mapped[str | None] = mapped_column(CHAR(2))
    strict_admin1: Mapped[str | None] = mapped_column(Text)
    strict_admin2: Mapped[str | None] = mapped_column(Text)
    strict_city: Mapped[str | None] = mapped_column(Text)
    advisory_country_code: Mapped[str | None] = mapped_column(CHAR(2))
    advisory_admin1: Mapped[str | None] = mapped_column(Text)
    advisory_admin2: Mapped[str | None] = mapped_column(Text)
    advisory_city: Mapped[str | None] = mapped_column(Text)
    confidence_country: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    confidence_admin1: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    confidence_admin2: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    confidence_city: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    abstain_reason: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False, default=dict)
    inference_version: Mapped[str | None] = mapped_column(Text)

    # --- geofence (M6) ------------------------------------------------------
    matched_geofence_ids: Mapped[list[uuid.UUID]] = mapped_column(
        pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False, default=list
    )
    geofence_state: Mapped[GeofenceState] = mapped_column(
        _pg_enum(GeofenceState, "geofence_state"),
        nullable=False,
        default=GeofenceState.UNDETERMINED,
    )

    # --- referral -----------------------------------------------------------
    referer: Mapped[str | None] = mapped_column(Text)
    utm: Mapped[dict[str, str] | None] = mapped_column(pg.JSONB)

    def __repr__(self) -> str:
        # Never the ciphertext, never a header, never the user agent.
        return f"Visit(id={self.id}, stage={self.stage.value}, class={self.classification.value})"
