"""The outbox: visit alerts, atomic with the visit (ADR-0009, F7, DATA_MODEL section 7.1).

``enqueue_visit_alert`` runs inside the inference job's per-visit savepoint, after the
geofence evaluation (ADR-0015): a rolled-back visit emits nothing, and a Telegram outage
loses nothing (F7.AC5). ``INSERT ... ON CONFLICT (dedup_key) DO NOTHING`` is the
once-per-local-day rule (F7.AC2): two concurrent visits from one visitor race to one row,
and the database decides which, so no application check can be raced.

The rule has one upgrade (SPEC section 11 row 20): a high-priority alert whose day is
already held by a *normal* alert is queued under the day key plus ``UPGRADE``, unique
too, so concurrent upgrades also race to one row. And one confirmation (row 21): an
outside alert whose day is held by a normal "Location not confirmed" is queued under the
day key plus ``CONFIRMED``, unless the upgrade has gone out -- a normal alert never
follows a high one.

Since M7.5 (row 27) a visit's alert can carry notes -- a new place, a returning visitor --
decided here too (``notes.py``). When the visit's alert is the day's duplicate, each note is
queued alone under its own key instead. The digest and the spike are not visit-bound: their
jobs (``digest.py``, ``spike.py``) insert their own rows.

The worker side -- claim, complete, fail, recover, retry -- is here too, so every state
change of a row is in one file. ``notify/worker.py`` drives it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import uuid
import zoneinfo
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import BigInteger, DateTime, Integer, Text, func, select, text
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tracelet.capture.models import Classification, GeofenceState, Link, Visit, pg_enum
from tracelet.db.base import Base
from tracelet.geofence.models import NotifyPriority
from tracelet.geofence.store import Evaluation
from tracelet.notify import alerts, notes
from tracelet.notify import settings as notify_settings

# An in-flight row older than this was abandoned by a crashed worker (invariant 7).
STALE_LOCK: Final = dt.timedelta(minutes=5)
CLAIM_BATCH: Final = 10
# The day's one upgrade from a normal alert to a high one (SPEC section 11 row 20).
UPGRADE: Final = ":upgrade"
# The day's one confirmed outside after a "Location not confirmed" (SPEC section 11 row 21).
CONFIRMED: Final = ":confirmed"


class OutboxKind(enum.StrEnum):
    VISIT_ALERT = "telegram.visit_alert"
    PASSWORD_RESET = "telegram.password_reset"  # noqa: S105 -- an outbox kind, not a secret
    HEALTH_ALERT = "telegram.health_alert"
    TEST = "telegram.test"
    # M7.5, SPEC section 11 row 27 (migration 0015).
    DIGEST = "telegram.digest"
    SPIKE = "telegram.spike"
    NEW_PLACE = "telegram.new_place"
    RETURNING = "telegram.returning"


class OutboxStatus(enum.StrEnum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class Outbox(Base):
    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[OutboxKind] = mapped_column(pg_enum(OutboxKind, "outbox_kind"), nullable=False)
    dedup_key: Mapped[str | None] = mapped_column(Text, unique=True)
    priority: Mapped[NotifyPriority] = mapped_column(
        pg_enum(NotifyPriority, "notify_priority"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(pg.JSONB, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        pg_enum(OutboxStatus, "outbox_status"), nullable=False, default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Enqueue: inside the inference savepoint
# ---------------------------------------------------------------------------


def _f(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def visit_payload(
    visit: Visit, link: Link, evaluation: Evaluation, *, reporting_tz: str, base_url: str
) -> dict[str, Any]:
    """The alert's facts, self-contained (no FK; DATA_MODEL 7.1 invariant 4)."""
    deciding = evaluation.deciding
    local = visit.occurred_at.astimezone(zoneinfo.ZoneInfo(reporting_tz))
    return {
        "visit_id": str(visit.id),
        "occurred_at": visit.occurred_at.isoformat(),
        "occurred_local": local.strftime("%Y-%m-%d %H:%M %Z"),
        "geofence_state": evaluation.state.value if evaluation.state else None,
        "geofence": {"id": str(deciding.id), "name": deciding.name} if deciding else None,
        "matched": [str(i) for i in evaluation.matched],
        "link": {"id": str(link.id), "label": link.label, "slug": link.slug},
        "location": {
            "strict": {
                "country_code": visit.strict_country_code,
                "admin1": visit.strict_admin1,
                "city": visit.strict_city,
            },
            "advisory": {
                "country_code": visit.advisory_country_code,
                "admin1": visit.advisory_admin1,
                "city": visit.advisory_city,
            },
            "confidence": {
                "country": _f(visit.confidence_country),
                "admin1": _f(visit.confidence_admin1),
                "city": _f(visit.confidence_city),
            },
        },
        "device": {
            "class": visit.device_class.value,
            "os": " ".join(p for p in (visit.os_family, visit.os_version) if p) or None,
            "browser": " ".join(p for p in (visit.ua_family, visit.ua_version) if p) or None,
        },
        "network": {
            "connection_class": visit.connection_class.value,
            "asn": visit.asn,
            "asn_org": visit.asn_org,
        },
        "classification": visit.classification.value,
        "bot_score": visit.bot_score,
        "visit_url": f"{base_url}/visits/{visit.id}",
    }


@dataclass(frozen=True, slots=True)
class Enqueued:
    """Why a visit did or did not queue an alert. Logged, and asserted by tests."""

    queued: bool
    reason: str
    priority: NotifyPriority | None = None
    # A new place or a returning visitor (row 27): on the alert when it was queued...
    notes: tuple[str, ...] = ()
    # ...or queued alone, by kind, when it was the day's duplicate.
    alone: tuple[str, ...] = ()


async def enqueue_visit_alert(
    db: AsyncSession,
    visit_id: uuid.UUID,
    evaluation: Evaluation,
    *,
    reporting_tz: str,
    base_url: str,
) -> Enqueued:
    """Queue the visit's alert, if it is owed one, with its notes (a new place, a returning
    visitor). Call after the visit's location, classification and geofence state are
    written, in the same savepoint."""
    visit = (
        await db.execute(
            select(Visit).where(Visit.id == visit_id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    # CLAUDE.md invariant 6, F7.AC1: automated traffic never notifies, whatever any
    # policy says (ck_links_automated_silent holds the policy side).
    if visit.classification is not Classification.HUMAN:
        return Enqueued(queued=False, reason="not_human")
    # Places are recorded for every human visit, silent or not (F7.AC12).
    note_list = await notes.visit_notes(db, visit, await notify_settings.alert_types(db))
    link = (await db.execute(select(Link).where(Link.id == visit.link_id))).scalar_one()
    deciding = evaluation.deciding
    priority = alerts.resolve_priority(
        link.notify_policy, evaluation.state, deciding.notify_priority if deciding else None
    )
    if priority is NotifyPriority.SILENT:
        return Enqueued(queued=False, reason="silent", priority=priority)
    payload = visit_payload(visit, link, evaluation, reporting_tz=reporting_tz, base_url=base_url)
    kinds = tuple(str(n["kind"]) for n in note_list)
    if note_list:
        payload["notes"] = note_list
    result = await _queue_visit_alert(db, visit, evaluation, priority, payload, reporting_tz)
    if result.queued:
        return dataclasses.replace(result, notes=kinds)
    # The day's alert has already gone out (F7.AC14): each note is a message of its own,
    # normal priority, once by its own key, outside F7.AC2's limits.
    payload.pop("notes", None)
    alone: list[str] = []
    for note in note_list:
        kind = str(note["kind"])
        if await _insert(
            db,
            _note_key(note, visit, reporting_tz),
            NotifyPriority.NORMAL,
            payload | {"note": note},
            kind=_NOTE_KINDS[kind],
        ):
            alone.append(kind)
    return dataclasses.replace(result, alone=tuple(alone))


_NOTE_KINDS: Final = {"new_place": OutboxKind.NEW_PLACE, "returning": OutboxKind.RETURNING}


def _note_key(note: dict[str, Any], visit: Visit, reporting_tz: str) -> str:
    """``newplace:{link}:{region}``, ``return:{link}:{visitor}:{local date}`` (row 27)."""
    if note["kind"] == "new_place":
        return f"newplace:{visit.link_id}:{note['region_key']}"
    day = visit.occurred_at.astimezone(zoneinfo.ZoneInfo(reporting_tz)).date().isoformat()
    who = visit.visitor_id.hex() if visit.visitor_id else "unknown"
    return f"return:{visit.link_id}:{who}:{day}"


async def _queue_visit_alert(
    db: AsyncSession,
    visit: Visit,
    evaluation: Evaluation,
    priority: NotifyPriority,
    payload: dict[str, Any],
    reporting_tz: str,
) -> Enqueued:
    """F7.AC2 with its upgrade (row 20) and its confirmation (row 21)."""
    key = alerts.dedup_key(
        link_id=visit.link_id,
        visitor_id=visit.visitor_id,
        ip_hmac=visit.ip_hmac,
        occurred_at=visit.occurred_at,
        reporting_tz=reporting_tz,
    )
    if await _insert(db, key, priority, payload):
        return Enqueued(queued=True, reason="queued", priority=priority)
    holder = await _holder(db, key)
    if holder is None or holder.priority is not NotifyPriority.NORMAL:
        return Enqueued(queued=False, reason="duplicate", priority=priority)
    # SPEC section 11 row 20: the day's alert was only normal, and this one is high.
    if priority is NotifyPriority.HIGH:
        if await _insert(db, key + UPGRADE, priority, payload):
            return Enqueued(queued=True, reason="upgrade", priority=priority)
    # Row 21: the day's alert was "Location not confirmed", this one is a confirmed
    # outside, and no high alert has gone out since.
    elif (
        holder.state is GeofenceState.UNDETERMINED
        and evaluation.state is GeofenceState.OUTSIDE
        and await _holder(db, key + UPGRADE) is None
        and await _insert(db, key + CONFIRMED, priority, payload)
    ):
        return Enqueued(queued=True, reason="confirmed", priority=priority)
    return Enqueued(queued=False, reason="duplicate", priority=priority)


async def _insert(
    db: AsyncSession,
    key: str,
    priority: NotifyPriority,
    payload: dict[str, Any],
    *,
    kind: OutboxKind = OutboxKind.VISIT_ALERT,
) -> bool:
    """Queue under ``key`` unless a row already holds it; the database decides a race."""
    inserted = (
        await db.execute(
            pg.insert(Outbox)
            .values(
                kind=kind,
                dedup_key=key,
                priority=priority,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=[Outbox.dedup_key])
            .returning(Outbox.id)
        )
    ).first()
    return inserted is not None


@dataclass(frozen=True, slots=True)
class Holder:
    """The alert holding a dedup key: its priority, and the geofence state it reported."""

    priority: NotifyPriority
    state: GeofenceState | None


async def _holder(db: AsyncSession, key: str) -> Holder | None:
    """The alert holding ``key``. A conflicting insert waits for the holder's transaction
    to commit, so the row is visible here; None if nothing holds it -- or, for the day
    key, if a retention purge removed it in between."""
    row = (
        await db.execute(
            select(Outbox.priority, Outbox.payload["geofence_state"].astext).where(
                Outbox.dedup_key == key
            )
        )
    ).first()
    if row is None:
        return None
    priority, state = row
    return Holder(priority, GeofenceState(state) if state is not None else None)


# ---------------------------------------------------------------------------
# The worker's side
# ---------------------------------------------------------------------------


async def recover_abandoned(db: AsyncSession) -> int:
    """Rows a crashed worker left in flight go back to ``failed`` (invariant 7)."""
    result = await db.execute(
        text(
            "UPDATE outbox SET status = 'failed', locked_by = NULL, locked_at = NULL, "
            "last_error = coalesce(last_error, 'abandoned in flight'), "
            "next_attempt_at = now() "
            "WHERE status = 'in_flight' AND locked_at < now() - make_interval(secs => :s)"
        ),
        {"s": STALE_LOCK.total_seconds()},
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def claim(
    db: AsyncSession,
    *,
    worker: str,
    quiet: bool,
    limit: int = CLAIM_BATCH,
    only: Sequence[int] | None = None,
) -> list[Outbox]:
    """Claim due rows with ``FOR UPDATE SKIP LOCKED`` -- correct with two workers
    (invariant 3). During quiet hours only ``high`` is claimed; ``normal`` waits, still
    pending, and goes out when the window closes (F7.AC9). ``only`` restricts the claim
    to named rows, for tests, as the inference job's ``only`` does."""
    rows = await db.execute(
        text(
            "UPDATE outbox SET status = 'in_flight', locked_by = :worker, locked_at = now(), "
            "attempts = attempts + 1 "
            "WHERE id IN ("
            "  SELECT id FROM outbox "
            "  WHERE status IN ('pending', 'failed') AND next_attempt_at <= now() "
            "  AND (NOT :quiet OR priority = 'high') "
            "  AND (CAST(:only AS bigint[]) IS NULL OR id = ANY(:only)) "
            "  ORDER BY priority DESC, next_attempt_at "
            "  LIMIT :limit FOR UPDATE SKIP LOCKED"
            ") RETURNING id"
        ),
        {"worker": worker, "quiet": quiet, "limit": limit, "only": list(only) if only else None},
    )
    ids = [r[0] for r in rows]
    if not ids:
        return []
    claimed = await db.execute(
        select(Outbox)
        .where(Outbox.id.in_(ids))
        .order_by(Outbox.priority.desc(), Outbox.id)
        .execution_options(populate_existing=True)
    )
    return list(claimed.scalars())


async def complete(db: AsyncSession, row_id: int) -> None:
    await db.execute(
        text(
            "UPDATE outbox SET status = 'done', completed_at = now(), locked_by = NULL, "
            "locked_at = NULL, last_error = NULL WHERE id = :id"
        ),
        {"id": row_id},
    )


async def fail(
    db: AsyncSession, row: Outbox, error: str, *, retry_in: dt.timedelta | None, dead: bool
) -> OutboxStatus:
    """Record a failed attempt: ``dead`` once out of attempts or when told the failure
    is permanent, otherwise ``failed`` with the next attempt scheduled."""
    final = dead or row.attempts >= row.max_attempts
    status = OutboxStatus.DEAD if final else OutboxStatus.FAILED
    delay = (
        retry_in
        if retry_in is not None
        else dt.timedelta(seconds=alerts.backoff_seconds(row.attempts))
    )
    await db.execute(
        text(
            "UPDATE outbox SET status = CAST(:status AS outbox_status), locked_by = NULL, "
            "locked_at = NULL, last_error = :error, "
            "completed_at = CASE WHEN :final THEN now() END, "
            "next_attempt_at = now() + make_interval(secs => :delay) WHERE id = :id"
        ),
        {
            "status": status.value,
            "error": error[:500],
            "final": final,
            "delay": delay.total_seconds(),
            "id": row.id,
        },
    )
    return status


async def retry_dead(db: AsyncSession, row_id: int) -> bool:
    """A manual retry (F7.AC6, F10.AC13): a dead row starts again with fresh attempts.
    ``False`` if the row is not dead -- only dead letters are retried by hand."""
    result = await db.execute(
        text(
            "UPDATE outbox SET status = 'pending', attempts = 0, completed_at = NULL, "
            "next_attempt_at = now() WHERE id = :id AND status = 'dead'"
        ),
        {"id": row_id},
    )
    return bool(getattr(result, "rowcount", 0))
