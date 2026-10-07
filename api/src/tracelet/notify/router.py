"""Notifications: settings, the delivery log, manual retry and the test message
(docs/API.md sections 9a and 10, F7, F10.AC13).

Reads are open to any admin; every write is ``owner``-only and audited (CLAUDE.md
invariant 9). The bot token and chat id are secrets and stay in the environment
(F12.AC3): this API says only whether they are set, never what they are.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import zoneinfo
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from tracelet.audit import log as audit
from tracelet.auth.dependencies import (
    Config,
    CurrentPrincipal,
    DbSession,
    OwnerPrincipal,
    Principal,
    client_ip,
)
from tracelet.auth.models import Admin
from tracelet.errors import NotFound, OutboxNotDead, TelegramDeliveryFailed
from tracelet.geofence.models import NotifyPriority
from tracelet.net import prefix_of
from tracelet.notify import alerts, outbox, telegram
from tracelet.notify import settings as notify_settings
from tracelet.notify.outbox import Outbox, OutboxKind, OutboxStatus

router = APIRouter(tags=["notifications"])

MAX_PAGE = 100


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class QuietHoursIn(BaseModel):
    enabled: bool
    start: str = Field(examples=["23:00"])
    end: str = Field(examples=["07:00"])
    timezone: str = Field(examples=["Asia/Kolkata"])

    @field_validator("start", "end")
    @classmethod
    def _hh_mm(cls, value: str) -> str:
        if not alerts.QuietHours.valid_time(value):
            msg = "A time is HH:MM, 00:00 to 23:59."
            raise ValueError(msg)
        return value

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        try:
            zoneinfo.ZoneInfo(value)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
            msg = "An IANA timezone, such as Asia/Kolkata."
            raise ValueError(msg) from exc
        return value


class QuietHoursOut(QuietHoursIn):
    active_now: bool


class TelegramOut(BaseModel):
    bot_token_set: bool
    chat_id_set: bool
    # Whether the owner chat is one an owner has verified through the bot (F8.AC7).
    chat_verified: bool


class NotificationSettingsOut(BaseModel):
    telegram: TelegramOut
    quiet_hours: QuietHoursOut


class NotificationSettingsIn(BaseModel):
    quiet_hours: QuietHoursIn


class OutboxCounts(BaseModel):
    pending: int
    in_flight: int
    failed: int
    dead: int
    # Pending normal alerts that quiet hours are holding right now (F7.AC9).
    held: int


class DeliveryOut(BaseModel):
    id: int
    kind: OutboxKind
    priority: NotifyPriority
    status: OutboxStatus
    attempts: int
    max_attempts: int
    created_at: dt.datetime
    next_attempt_at: dt.datetime
    completed_at: dt.datetime | None
    last_error: str | None
    visit_id: str | None
    link_label: str | None
    geofence_name: str | None
    geofence_state: str | None


class OutboxOut(BaseModel):
    counts: OutboxCounts
    items: list[DeliveryOut]
    next_cursor: int | None


class TestSentOut(BaseModel):
    delivered_at: dt.datetime
    message_id: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _audit(request: Request, principal: Principal, config: Config) -> dict[str, Any]:
    return {
        "actor_admin_id": principal.admin.id,
        "actor_ip_prefix": prefix_of(client_ip(request, config)),
        "trace_id": getattr(request.state, "trace_id", None),
    }


async def _chat_verified(db: DbSession, chat_id: int | None) -> bool:
    if chat_id is None:
        return False
    row = await db.execute(
        select(func.count())
        .select_from(Admin)
        .where(Admin.telegram_chat_id == chat_id, Admin.telegram_verified_at.is_not(None))
    )
    return bool(row.scalar_one())


def _quiet_out(value: alerts.QuietHours) -> QuietHoursOut:
    return QuietHoursOut(
        **dataclasses.asdict(value), active_now=value.active(dt.datetime.now(dt.UTC))
    )


def _delivery(row: Outbox) -> DeliveryOut:
    payload = row.payload or {}
    geofence = payload.get("geofence") or {}
    link = payload.get("link") or {}
    return DeliveryOut(
        id=row.id,
        kind=row.kind,
        priority=row.priority,
        status=row.status,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        created_at=row.created_at,
        next_attempt_at=row.next_attempt_at,
        completed_at=row.completed_at,
        last_error=row.last_error,
        visit_id=payload.get("visit_id"),
        link_label=link.get("label"),
        geofence_name=geofence.get("name"),
        geofence_state=payload.get("geofence_state"),
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/notifications/settings",
    response_model=NotificationSettingsOut,
    summary="Telegram status and quiet hours",
)
async def get_settings(
    principal: CurrentPrincipal, db: DbSession, config: Config
) -> NotificationSettingsOut:
    del principal
    return NotificationSettingsOut(
        telegram=TelegramOut(
            bot_token_set=config.telegram_bot_token is not None,
            chat_id_set=config.telegram_owner_chat_id is not None,
            chat_verified=await _chat_verified(db, config.telegram_owner_chat_id),
        ),
        quiet_hours=_quiet_out(await notify_settings.quiet_hours(db)),
    )


@router.patch(
    "/api/v1/notifications/settings",
    response_model=NotificationSettingsOut,
    summary="Change quiet hours",
    description="Quiet hours hold normal-priority alerts until the window closes (F7.AC9).",
)
async def update_settings(
    payload: NotificationSettingsIn,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    config: Config,
) -> NotificationSettingsOut:
    value = alerts.QuietHours(**payload.quiet_hours.model_dump())
    previous = await notify_settings.set_quiet_hours(db, value, admin_id=principal.admin.id)
    if previous != value:
        await audit.record(
            db,
            action=audit.Action.SETTINGS_CHANGED,
            target_type="app_settings",
            target_id=notify_settings.QUIET_HOURS_KEY,
            detail={"from": dataclasses.asdict(previous), "to": dataclasses.asdict(value)},
            **_audit(request, principal, config),
        )
    return await get_settings(principal, db, config)


# ---------------------------------------------------------------------------
# The outbox (F10.AC13) and the test message (F7.AC8)
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/health/outbox",
    response_model=OutboxOut,
    summary="Delivery counts and the delivery log, newest first",
)
async def list_outbox(
    principal: CurrentPrincipal,
    db: DbSession,
    status: Annotated[
        Literal["pending", "in_flight", "done", "failed", "dead"] | None, Query()
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    cursor: Annotated[int | None, Query(ge=1)] = None,
) -> OutboxOut:
    del principal
    # A loop, not dict(result): a Result has .keys(), so dict() reads it as a mapping.
    grouped = await db.execute(select(Outbox.status, func.count()).group_by(Outbox.status))
    counted: dict[OutboxStatus, int] = {}
    for status_, count in grouped.tuples():
        counted[status_] = count
    quiet = (await notify_settings.quiet_hours(db)).active(dt.datetime.now(dt.UTC))
    held = 0
    if quiet:
        held = (
            await db.execute(
                select(func.count())
                .select_from(Outbox)
                .where(
                    Outbox.status.in_([OutboxStatus.PENDING, OutboxStatus.FAILED]),
                    Outbox.priority != NotifyPriority.HIGH,
                )
            )
        ).scalar_one()
    stmt = select(Outbox).order_by(Outbox.id.desc()).limit(limit + 1)
    if status is not None:
        stmt = stmt.where(Outbox.status == OutboxStatus(status))
    if cursor is not None:
        stmt = stmt.where(Outbox.id < cursor)
    rows = list((await db.execute(stmt)).scalars())
    more = len(rows) > limit
    rows = rows[:limit]
    return OutboxOut(
        counts=OutboxCounts(
            pending=counted.get(OutboxStatus.PENDING, 0),
            in_flight=counted.get(OutboxStatus.IN_FLIGHT, 0),
            failed=counted.get(OutboxStatus.FAILED, 0),
            dead=counted.get(OutboxStatus.DEAD, 0),
            held=int(held),
        ),
        items=[_delivery(r) for r in rows],
        next_cursor=rows[-1].id if more and rows else None,
    )


@router.post(
    "/api/v1/health/outbox/{outbox_id}/retry",
    response_model=DeliveryOut,
    summary="Requeue a dead-lettered delivery",
    description="Starts it again with fresh attempts (F7.AC6). Only a dead letter.",
)
async def retry_outbox(
    outbox_id: int,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    config: Config,
) -> DeliveryOut:
    row = (await db.execute(select(Outbox).where(Outbox.id == outbox_id))).scalar_one_or_none()
    if row is None:
        raise NotFound("No such delivery.")
    if not await outbox.retry_dead(db, outbox_id):
        raise OutboxNotDead(f"This delivery is {row.status.value}, not dead.")
    await audit.record(
        db,
        action=audit.Action.OUTBOX_RETRIED,
        target_type="outbox",
        target_id=str(outbox_id),
        detail={"last_error": row.last_error, "attempts": row.attempts},
        **_audit(request, principal, config),
    )
    again = (
        await db.execute(
            select(Outbox).where(Outbox.id == outbox_id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    return _delivery(again)


@router.post(
    "/api/v1/health/telegram/test",
    response_model=TestSentOut,
    summary="Send a test message to the owner chat",
    description=(
        "Sent at once, not through the outbox: the owner is watching for the answer "
        "(F7.AC8). A failure is 502 with Telegram's own reason, without the token."
    ),
)
async def send_test(
    request: Request, principal: OwnerPrincipal, db: DbSession, config: Config
) -> TestSentOut:
    token = config.telegram_bot_token.get_secret_value() if config.telegram_bot_token else None
    chat_id = config.telegram_owner_chat_id
    if not token or chat_id is None:
        raise TelegramDeliveryFailed(
            "Telegram is not configured: set TRACELET_TELEGRAM_BOT_TOKEN and "
            "TRACELET_TELEGRAM_OWNER_CHAT_ID."
        )
    now = dt.datetime.now(dt.UTC)
    try:
        sent = await telegram.send_message(
            bot_token=token, chat_id=chat_id, text=alerts.probe_message(at=now)
        )
    except telegram.TelegramError as exc:
        raise TelegramDeliveryFailed(str(exc)) from exc
    await audit.record(
        db,
        action=audit.Action.TELEGRAM_TEST_SENT,
        target_type="telegram",
        detail={"message_id": sent.message_id},
        **_audit(request, principal, config),
    )
    return TestSentOut(delivered_at=now, message_id=sent.message_id)
