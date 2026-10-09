"""Writing labels: one path for the API and the CLI (F4.AC15, CLAUDE.md invariant 9).

Every create, change and delete is audited, on the caller's session so the row commits or
rolls back with the change. The audit detail names the truth and the visit, never the
coordinates. Nothing here writes to ``visits``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.accuracy.models import (
    CONNECTION_KINDS,
    NETWORKS,
    NOTES_MAX,
    PLACE_MAX,
    GroundTruthLabel,
)
from tracelet.audit import log as audit
from tracelet.capture.models import Visit
from tracelet.errors import FieldError, GroundTruthExists, NotFound, ValidationFailed
from tracelet.geofence import regions
from tracelet.geofence.evaluate import KEY_SEPARATOR


@dataclass(frozen=True, slots=True)
class LabelValues:
    """A label's content, as the owner states it."""

    cant_tell: bool = False
    country_code: str | None = None
    admin1: str | None = None
    admin2: str | None = None
    city: str | None = None
    # True copies the visit's own GPS fix; False clears the coordinates; None keeps them.
    use_gps: bool | None = None
    connection_kind: str | None = None
    vpn_used: bool | None = None
    network: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class Actor:
    admin_id: uuid.UUID | None  # None: the CLI
    ip_prefix: str | None = None
    trace_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = " ".join(value.split())
    return stripped or None


def normalised(values: LabelValues) -> LabelValues:
    country = _clean(values.country_code)
    return replace(
        values,
        country_code=country.upper() if country else None,
        admin1=_clean(values.admin1),
        admin2=_clean(values.admin2),
        city=_clean(values.city),
        notes=_clean(values.notes),
    )


def validate(values: LabelValues, catalog: regions.Catalog | None) -> list[FieldError]:
    """Every problem at once, as field errors (API section 11)."""
    errors: list[FieldError] = []

    def bad(name: str, code: str, message: str) -> None:
        errors.append(FieldError(field=name, code=code, message=message))

    if values.cant_tell:
        for name in ("country_code", "admin1", "admin2", "city"):
            if getattr(values, name) is not None:
                bad(name, "CANT_TELL_HAS_PLACE", "A can't-tell label names no place.")
        if values.use_gps:
            bad("use_gps", "CANT_TELL_HAS_PLACE", "A can't-tell label has no coordinates.")
    else:
        if values.country_code is None:
            bad("country_code", "REQUIRED", "Say which country, or choose Can't tell.")
        elif len(values.country_code) != 2 or not values.country_code.isalpha():
            bad("country_code", "INVALID", "A country is a two-letter ISO code.")
        if values.admin1 is None and (values.admin2 is not None or values.city is not None):
            bad("admin1", "REQUIRED", "A district or city needs its state.")
        if values.admin1 is not None and values.country_code is not None:
            key = f"{values.country_code}{KEY_SEPARATOR}{values.admin1}"
            if catalog is not None and catalog.unknown([key]):
                bad(
                    "admin1",
                    "UNKNOWN_REGION",
                    f"{values.admin1!r} is not a state the region list knows.",
                )
    for name in ("admin1", "admin2", "city"):
        raw = getattr(values, name)
        if raw is not None and len(raw) > PLACE_MAX:
            bad(name, "TOO_LONG", f"At most {PLACE_MAX} characters.")
    if values.connection_kind is not None and values.connection_kind not in CONNECTION_KINDS:
        bad("connection_kind", "INVALID", f"One of {', '.join(CONNECTION_KINDS)}.")
    if values.network is not None and values.network not in NETWORKS:
        bad("network", "INVALID", f"One of {', '.join(NETWORKS)}.")
    if values.notes is not None and len(values.notes) > NOTES_MAX:
        bad("notes", "TOO_LONG", f"At most {NOTES_MAX} characters.")
    return errors


def _apply(label: GroundTruthLabel, values: LabelValues, visit: Visit) -> None:
    label.cant_tell = values.cant_tell
    label.true_country_code = None if values.cant_tell else values.country_code
    label.true_admin1 = None if values.cant_tell else values.admin1
    label.true_admin2 = None if values.cant_tell else values.admin2
    label.true_city = None if values.cant_tell else values.city
    if values.cant_tell or values.use_gps is False:
        label.true_lat, label.true_lng = None, None
    elif values.use_gps and visit.gps_lat is not None and visit.gps_lng is not None:
        label.true_lat, label.true_lng = visit.gps_lat, visit.gps_lng
    label.connection_kind = values.connection_kind
    label.vpn_used = values.vpn_used
    label.network = values.network
    label.notes = values.notes


def summary(label: GroundTruthLabel) -> dict[str, Any]:
    """What the audit row records: the truth and how the visit was made. No coordinates
    (they would place a person), no notes (free text can hold anything)."""
    return {
        "cant_tell": label.cant_tell,
        "country_code": label.true_country_code,
        "admin1": label.true_admin1,
        "admin2": label.true_admin2,
        "city": label.true_city,
        "has_coordinates": label.true_lat is not None,
        "connection_kind": label.connection_kind,
        "vpn_used": label.vpn_used,
        "network": label.network,
    }


async def _record(
    db: AsyncSession, action: str, label: GroundTruthLabel, actor: Actor, detail: dict[str, Any]
) -> None:
    await audit.record(
        db,
        action=action,
        actor_admin_id=actor.admin_id,
        actor_ip_prefix=actor.ip_prefix,
        target_type="visit",
        target_id=str(label.visit_id),
        trace_id=actor.trace_id,
        detail={**actor.detail, **detail},
    )


async def _visit(db: AsyncSession, visit_id: uuid.UUID) -> Visit:
    visit = await db.get(Visit, visit_id)
    if visit is None:
        msg = "There is no such visit."
        raise NotFound(msg)
    return visit


def _checked(values: LabelValues, catalog: regions.Catalog | None, visit: Visit) -> LabelValues:
    values = normalised(values)
    errors = validate(values, catalog)
    if values.use_gps and (visit.gps_lat is None or visit.gps_lng is None):
        errors.append(
            FieldError(
                field="use_gps", code="NO_GPS_FIX", message="This visit has no GPS fix to copy."
            )
        )
    if errors:
        raise ValidationFailed("The label is not valid.", errors=errors)
    return values


async def create(
    db: AsyncSession,
    visit_id: uuid.UUID,
    values: LabelValues,
    *,
    catalog: regions.Catalog | None,
    actor: Actor,
) -> GroundTruthLabel:
    visit = await _visit(db, visit_id)
    values = _checked(values, catalog, visit)
    label = GroundTruthLabel(visit_id=visit_id, labeled_by=actor.admin_id)
    _apply(label, values, visit)
    try:
        async with db.begin_nested():
            db.add(label)
            await db.flush()
    except IntegrityError as exc:
        raise GroundTruthExists from exc
    await db.refresh(label)
    await _record(db, audit.Action.GROUND_TRUTH_LABELLED, label, actor, {"label": summary(label)})
    return label


async def update(
    db: AsyncSession,
    label: GroundTruthLabel,
    values: LabelValues,
    *,
    catalog: regions.Catalog | None,
    actor: Actor,
) -> GroundTruthLabel:
    visit = await _visit(db, label.visit_id)
    values = _checked(values, catalog, visit)
    before = summary(label)
    _apply(label, values, visit)
    await db.flush()
    await db.refresh(label)
    await _record(
        db,
        audit.Action.GROUND_TRUTH_UPDATED,
        label,
        actor,
        {"before": before, "after": summary(label)},
    )
    return label


async def delete(db: AsyncSession, label: GroundTruthLabel, *, actor: Actor) -> None:
    held = summary(label)
    await _record(db, audit.Action.GROUND_TRUTH_DELETED, label, actor, {"label": held})
    await db.delete(label)
    await db.flush()


def values_of(label: GroundTruthLabel) -> LabelValues:
    """A label's current content, for a PATCH to change part of."""
    return LabelValues(
        cant_tell=label.cant_tell,
        country_code=label.true_country_code,
        admin1=label.true_admin1,
        admin2=label.true_admin2,
        city=label.true_city,
        use_gps=None,
        connection_kind=label.connection_kind,
        vpn_used=label.vpn_used,
        network=label.network,
        notes=label.notes,
    )
