"""The outbox worker (ADR-0009, F7.AC6, F7.AC7, F7.AC9).

A scheduler job, so it runs in exactly one process at a time (advisory lock), and it
never touches the visitor path: a Telegram failure delays an alert, it cannot fail a
capture (F7.AC7). Each tick:

1. returns rows abandoned in flight by a crashed worker to ``failed``;
2. claims due rows with ``SKIP LOCKED`` -- only ``high`` during quiet hours;
3. **commits the claim**, then sends each message outside any transaction, so a slow
   Telegram never holds a row lock or a connection;
4. marks each row ``done``, or ``failed`` with backoff and jitter, or ``dead``.

A rejection Telegram calls permanent (a wrong chat, a blocked bot, a revoked token)
dead-letters at once: it will not fix itself, and the owner sees it at once rather
than after eight attempts. A manual retry resends it once the cause is fixed.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from collections.abc import Sequence
from typing import Final

import structlog

from tracelet.config import Settings, get_settings
from tracelet.db.engine import session_scope
from tracelet.notify import alerts, outbox, telegram
from tracelet.notify import settings as notify_settings

log = structlog.get_logger(__name__)

WORKER: Final = f"{socket.gethostname()}:{os.getpid()}"
NOT_CONFIGURED: Final = "Telegram is not configured: set the bot token and the owner chat id."


async def run_once(config: Settings | None = None, *, only: Sequence[int] | None = None) -> int:
    """One tick. Returns how many messages were delivered. ``only`` restricts the tick
    to named rows (tests)."""
    config = config or get_settings()
    async with session_scope() as db:
        recovered = await outbox.recover_abandoned(db)
        quiet = (await notify_settings.quiet_hours(db)).active(dt.datetime.now(dt.UTC))
        rows = await outbox.claim(db, worker=WORKER, quiet=quiet, only=only)
    if recovered:
        log.warning("outbox_recovered_abandoned", count=recovered)

    token = config.telegram_bot_token.get_secret_value() if config.telegram_bot_token else None
    chat_id = config.telegram_owner_chat_id
    delivered = 0
    for row in rows:
        try:
            if not token or chat_id is None:
                raise telegram.TelegramError(NOT_CONFIGURED)
            text = alerts.render_message(row.kind.value, row.payload, priority=row.priority)
            await telegram.send_message(bot_token=token, chat_id=chat_id, text=text)
        except telegram.TelegramError as exc:
            retry_in = dt.timedelta(seconds=exc.retry_after) if exc.retry_after else None
            async with session_scope() as db:
                status = await outbox.fail(db, row, str(exc), retry_in=retry_in, dead=exc.permanent)
            log.warning(
                "outbox_delivery_failed",
                outbox_id=row.id,
                attempts=row.attempts,
                status=status.value,
                permanent=exc.permanent,
            )
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the others
            async with session_scope() as db:
                status = await outbox.fail(
                    db, row, f"{type(exc).__name__}", retry_in=None, dead=False
                )
            log.error(
                "outbox_delivery_error",
                outbox_id=row.id,
                error_type=type(exc).__name__,
                status=status.value,
            )
        else:
            async with session_scope() as db:
                await outbox.complete(db, row.id)
            delivered += 1
    if delivered:
        log.info("outbox_delivered", count=delivered)
    return delivered


async def run_job_once() -> int:
    """The scheduler's entry point: no arguments, like the other jobs."""
    return await run_once()
