"""The volume spike (F7.AC11, SPEC section 11 row 27).

Every five minutes, under the scheduler's advisory lock: a link whose human visits in the
last 60 minutes reach the floor **and** exceed *k* times the median of the same 60 minutes
on each of the seven days before. At most one alert per link per local hour, by
``spike:{link}:{YYYY-MM-DDTHH}`` -- the unique key stops a second one.

Raw rows, not the hourly rollup: the window is the last 60 minutes, not a clock hour, and
the rollup lags by up to five minutes. Only links already at the floor get their baseline
counted, each a handful of index range scans on ``(link_id, occurred_at)``. The median is
plain Python (``alerts.median``): no numpy (CLAUDE.md section 5). Visit retention is at
least 8 days, so the 7-day baseline is always there.
"""

from __future__ import annotations

import datetime as dt
import uuid
import zoneinfo
from typing import Final

import structlog
from sqlalchemy import text
from sqlalchemy.dialects import postgresql as pg

from tracelet.config import Settings, get_settings
from tracelet.db.engine import session_scope
from tracelet.geofence.models import NotifyPriority
from tracelet.notify import alerts
from tracelet.notify import settings as notify_settings
from tracelet.notify.outbox import Outbox, OutboxKind

log = structlog.get_logger(__name__)

WINDOW: Final = dt.timedelta(minutes=60)
BASELINE_DAYS: Final = 7

# Live links at the floor in the last 60 minutes.
_BUSY = text(
    "SELECT v.link_id, l.label, l.slug, count(*) AS n FROM visits v "
    "JOIN links l ON l.id = v.link_id "
    "WHERE v.classification = 'human' AND v.occurred_at > :start AND v.occurred_at <= :now "
    "AND l.is_active AND l.archived_at IS NULL "
    "GROUP BY v.link_id, l.label, l.slug HAVING count(*) >= :floor"
)
# The same 60 minutes on each of the seven days before; a day with none counts as 0.
_BASELINE = text(
    "SELECT d, (SELECT count(*) FROM visits v WHERE v.link_id = :link "
    "  AND v.classification = 'human' "
    "  AND v.occurred_at > CAST(:start AS timestamptz) - make_interval(days => d) "
    "  AND v.occurred_at <= CAST(:now AS timestamptz) - make_interval(days => d)) AS n "
    "FROM generate_series(1, :days) AS d"
)


def key_for(link_id: uuid.UUID, now: dt.datetime, reporting_tz: str) -> str:
    hour = now.astimezone(zoneinfo.ZoneInfo(reporting_tz)).strftime("%Y-%m-%dT%H")
    return f"spike:{link_id}:{hour}"


async def run_once(config: Settings | None = None, *, now: dt.datetime | None = None) -> int:
    """Queue a spike alert for each link that has one. Returns how many it queued."""
    config = config or get_settings()
    now = now or dt.datetime.now(dt.UTC)
    queued = 0
    async with session_scope() as db:
        rule = (await notify_settings.alert_types(db)).spike
        if not rule.enabled:
            return 0
        window = {"start": now - WINDOW, "now": now}
        busy = (await db.execute(_BUSY, window | {"floor": rule.floor})).all()
        for row in busy:
            counts = [
                int(r.n)
                for r in await db.execute(
                    _BASELINE, window | {"link": row.link_id, "days": BASELINE_DAYS}
                )
            ]
            usual = alerts.median(counts)
            if not rule.fires(int(row.n), usual):
                continue
            inserted = (
                await db.execute(
                    pg.insert(Outbox)
                    .values(
                        kind=OutboxKind.SPIKE,
                        dedup_key=key_for(row.link_id, now, config.reporting_tz),
                        priority=NotifyPriority.NORMAL,
                        payload={
                            "link": {"id": str(row.link_id), "label": row.label, "slug": row.slug},
                            "count": int(row.n),
                            "usual": usual,
                            "baseline": counts,
                            "at": now.isoformat(),
                            "link_url": f"{config.public_base_url}/links/{row.slug}",
                        },
                    )
                    .on_conflict_do_nothing(index_elements=[Outbox.dedup_key])
                    .returning(Outbox.id)
                )
            ).first()
            if inserted is not None:
                queued += 1
                log.info("spike_queued", link_id=str(row.link_id), count=int(row.n), usual=usual)
    return queued


async def run_job_once() -> int:
    """The scheduler's entry point: no arguments, like the other jobs."""
    return await run_once()
