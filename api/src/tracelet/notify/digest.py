"""The daily digest (F7.AC10, SPEC section 11 row 27).

A scheduler job, so one process runs it at a time (advisory lock); and exactly once a day
however often it runs, because the row's ``dedup_key`` is ``digest:{day}`` and a second
insert meets the unique constraint. It summarises the previous local day in the reporting
timezone, from raw rows -- one day, on the ``(occurred_at)`` index, well inside retention.

Rate-limited requests are not visits here, as on the dashboard (API section 8.1). The
states are the best guess (ADR-0018) and the message says so; links and states rank human
visits only (F7.AC1).
"""

from __future__ import annotations

import datetime as dt
import zoneinfo
from typing import Any, Final

import structlog
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.config import Settings, get_settings
from tracelet.db.engine import session_scope
from tracelet.geofence.models import NotifyPriority
from tracelet.notify import settings as notify_settings
from tracelet.notify.outbox import Outbox, OutboxKind

log = structlog.get_logger(__name__)

TOP: Final = 3

_TOTALS = text(
    "SELECT count(*) AS visits, count(*) FILTER (WHERE classification = 'human') AS human "
    "FROM visits WHERE occurred_at >= :start AND occurred_at < :end "
    "AND stage <> 'rate_limited'"
)
_STATES = text(
    "SELECT advisory_country_code || '|' || advisory_admin1 AS key, count(*) AS n "
    "FROM visits WHERE occurred_at >= :start AND occurred_at < :end "
    "AND classification = 'human' AND advisory_country_code IS NOT NULL "
    "AND advisory_admin1 IS NOT NULL "
    "GROUP BY 1 ORDER BY n DESC, key LIMIT :top"
)
_LINKS = text(
    "SELECT l.label, l.slug, count(*) AS n FROM visits v JOIN links l ON l.id = v.link_id "
    "WHERE v.occurred_at >= :start AND v.occurred_at < :end AND v.classification = 'human' "
    "GROUP BY l.id, l.label, l.slug ORDER BY n DESC, l.label LIMIT :top"
)
_DEAD = text("SELECT count(*) FROM outbox WHERE status = 'dead'")


def key_for(day: dt.date) -> str:
    return f"digest:{day.isoformat()}"


async def summary(
    db: AsyncSession, day: dt.date, *, reporting_tz: str, base_url: str
) -> dict[str, Any]:
    """The digest's payload: figures only, self-contained (DATA_MODEL 7.1 invariant 8)."""
    zone = zoneinfo.ZoneInfo(reporting_tz)
    start = dt.datetime.combine(day, dt.time(), tzinfo=zone)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tzinfo=zone)
    window = {"start": start, "end": end, "top": TOP}
    totals = (await db.execute(_TOTALS, window)).one()
    states = (await db.execute(_STATES, window)).all()
    links = (await db.execute(_LINKS, window)).all()
    dead = (await db.execute(_DEAD)).scalar_one()
    return {
        "day": day.isoformat(),
        "label": f"{day:%a} {day.day} {day:%b}",
        "visits": int(totals.visits),
        "human": int(totals.human),
        "states": [{"key": r.key, "count": int(r.n)} for r in states],
        "links": [{"label": r.label, "slug": r.slug, "count": int(r.n)} for r in links],
        "dead": int(dead),
        "dashboard_url": f"{base_url}/",
    }


async def run_once(config: Settings | None = None, *, now: dt.datetime | None = None) -> bool:
    """Queue the day's digest if it is due and not yet queued. True if it queued one."""
    config = config or get_settings()
    now = now or dt.datetime.now(dt.UTC)
    async with session_scope() as db:
        types = await notify_settings.alert_types(db)
        day = types.digest.due(now, config.reporting_tz)
        if day is None:
            return False
        key = key_for(day)
        if (await db.execute(select(Outbox.id).where(Outbox.dedup_key == key))).first():
            return False
        payload = await summary(
            db, day, reporting_tz=config.reporting_tz, base_url=config.public_base_url
        )
        inserted = (
            await db.execute(
                pg.insert(Outbox)
                .values(
                    kind=OutboxKind.DIGEST,
                    dedup_key=key,
                    priority=NotifyPriority.NORMAL,
                    payload=payload,
                )
                .on_conflict_do_nothing(index_elements=[Outbox.dedup_key])
                .returning(Outbox.id)
            )
        ).first()
    if inserted is not None:
        log.info("digest_queued", day=day.isoformat(), visits=payload["visits"])
    return inserted is not None


async def run_job_once() -> bool:
    """The scheduler's entry point: no arguments, like the other jobs."""
    return await run_once()
