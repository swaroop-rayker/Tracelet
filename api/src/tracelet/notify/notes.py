"""A new place and a returning visitor, decided in the inference savepoint (F7.AC12-F7.AC14).

Both are facts about one human visit, so they are decided where the visit's alert is, and
commit or roll back with it (F7.AC5). ``outbox.enqueue_visit_alert`` then puts them on the
visit's own alert, or -- only when that alert was the day's duplicate -- queues each alone
(SPEC section 11 row 27).

**A new place is judged by ``link_places``, not by an outbox key.** Delivered outbox rows are
purged after 30 days, and a place must not become "new" again. Every human visit with a
strict country records its places here whether the alert is on or not, so switching it on
announces nothing already seen. Strict fields only: a best guess never records a place
(CLAUDE.md invariant 5).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func, select
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tracelet.capture.models import Classification, Visit
from tracelet.db.base import Base
from tracelet.notify import alerts


class LinkPlace(Base):
    __tablename__ = "link_places"

    link_id: Mapped[uuid.UUID] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("links.id", ondelete="CASCADE"), primary_key=True
    )
    region_key: Mapped[str] = mapped_column(Text, primary_key=True)
    first_visit_id: Mapped[uuid.UUID] = mapped_column(pg.UUID(as_uuid=True), nullable=False)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def region_keys(country: str | None, admin1: str | None) -> list[str]:
    """The geofence region keys (ADR-0020) a strict place stands for, broadest first."""
    if not country:
        return []
    return [country, f"{country}|{admin1}"] if admin1 else [country]


async def record_places(db: AsyncSession, visit: Visit) -> list[str]:
    """Record the visit's strict places on its link; return the keys seen for the first
    time, broadest first. Concurrent visits race to one row per key (the primary key)."""
    keys = region_keys(visit.strict_country_code, visit.strict_admin1)
    if not keys:
        return []
    inserted = await db.execute(
        pg.insert(LinkPlace)
        .values(
            [
                {
                    "link_id": visit.link_id,
                    "region_key": key,
                    "first_visit_id": visit.id,
                    "first_seen_at": visit.occurred_at,
                }
                for key in keys
            ]
        )
        .on_conflict_do_nothing(index_elements=[LinkPlace.link_id, LinkPlace.region_key])
        .returning(LinkPlace.region_key)
    )
    new = {row[0] for row in inserted}
    return [key for key in keys if key in new]


async def time_away(db: AsyncSession, visit: Visit) -> dt.timedelta | None:
    """How long since this visitor's previous human visit to the same link, or ``None``
    for a first visit (within retention) or a visit with no ``visitor_id``. Served by the
    ``(visitor_id, occurred_at)`` index."""
    if visit.visitor_id is None:
        return None
    previous = (
        await db.execute(
            select(func.max(Visit.occurred_at)).where(
                Visit.visitor_id == visit.visitor_id,
                Visit.link_id == visit.link_id,
                Visit.classification == Classification.HUMAN,
                Visit.occurred_at < visit.occurred_at,
                Visit.id != visit.id,
            )
        )
    ).scalar_one_or_none()
    if previous is None:
        return None
    return visit.occurred_at - previous


async def visit_notes(
    db: AsyncSession, visit: Visit, types: alerts.AlertTypes
) -> list[dict[str, Any]]:
    """The notes a human visit carries. Places are recorded even when the alert is off."""
    notes: list[dict[str, Any]] = []
    new_places = await record_places(db, visit)
    if types.new_place.enabled and new_places:
        # The deepest new key names it: a new state in a new country says the state.
        notes.append({"kind": "new_place", "region_key": new_places[-1]})
    if types.returning.enabled:
        away = await time_away(db, visit)
        if away is not None and away > dt.timedelta(days=types.returning.after_days):
            notes.append({"kind": "returning", "days": away.days})
    return notes
