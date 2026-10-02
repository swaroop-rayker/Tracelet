"""Helpers for the capture-path integration tests (M2).

Links created here use a ``t-`` slug prefix, which is what the teardown in
``conftest.py`` keys on. A visit references its link with ``ON DELETE RESTRICT``, so the
cleanup deletes visits first and links second -- the same order the engine insists on.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import select, text, update

from tracelet.capture.models import Link, Visit
from tracelet.db.engine import session_scope

DESTINATION = "https://example.com/landing"
IG_ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-S918B Build/UP1A.231005.007; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/131.0.6778.39 Mobile "
    "Safari/537.36 Instagram 356.0.0.41.101 Android"
)
CHROME_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36"
)
FB_FETCHER_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"

# A distinctive visitor address, used to prove it appears nowhere it should not.
VISITOR_IP = "49.207.12.34"
VISITOR_PREFIX = "49.207.12.0/24"


def new_slug() -> str:
    return f"t-{uuid.uuid4().hex[:10]}"


async def create_link(
    *,
    slug: str | None = None,
    destination: str = DESTINATION,
    is_active: bool = True,
    archived: bool = False,
    is_default: bool = False,
    interstitial_ms: int = 700,
) -> Link:
    """Insert a link directly, bypassing the API (which has its own tests)."""
    async with session_scope() as db:
        link = Link(
            slug=slug or new_slug(),
            label="Integration link",
            destination_url=destination,
            is_active=is_active,
            is_default=is_default,
            interstitial_ms=interstitial_ms,
            notify_policy={"inside": "high", "outside": "normal", "automated": "silent"},
            archived_at=dt.datetime.now(dt.UTC) if archived else None,
        )
        db.add(link)
        await db.flush()
        await db.refresh(link)
        return link


async def visit(
    client: AsyncClient,
    slug: str,
    *,
    ua: str = CHROME_UA,
    peer: str | None = VISITOR_IP,
    headers: dict[str, str] | None = None,
    query: str = "",
) -> Response:
    """GET the capture URL as a visitor on ``peer``.

    ``X-Tracelet-Peer-IP`` is what Caddy sets from the TCP peer in production; setting
    it here stands in for being a different network.
    """
    sent = {"User-Agent": ua, **(headers or {})}
    if peer is not None:
        sent["X-Tracelet-Peer-IP"] = peer
    return await client.get(f"/r/{slug}{query}", headers=sent)


def nonce_from(response: Response) -> str:
    match = re.search(r'"nonce":\s*"([A-Za-z0-9_-]+)"', response.text)
    assert match, "no enrichment nonce in the capture page"
    return match.group(1)


async def latest_visit(link_id: uuid.UUID) -> Visit:
    async with session_scope() as db:
        return (
            await db.execute(
                select(Visit)
                .where(Visit.link_id == link_id)
                .order_by(Visit.occurred_at.desc(), Visit.id.desc())
                .limit(1)
            )
        ).scalar_one()


async def get_visit(visit_id: uuid.UUID) -> Visit:
    async with session_scope() as db:
        return (await db.execute(select(Visit).where(Visit.id == visit_id))).scalar_one()


async def visit_count(link_id: uuid.UUID) -> int:
    async with session_scope() as db:
        return int(
            (
                await db.execute(
                    text("SELECT count(*) FROM visits WHERE link_id = :id"), {"id": link_id}
                )
            ).scalar_one()
        )


async def backdate(visit_id: uuid.UUID, *, seconds: float) -> None:
    """Move a visit into the past, to reach the sweeper without waiting 90 s."""
    async with session_scope() as db:
        await db.execute(
            update(Visit)
            .where(Visit.id == visit_id)
            .values(occurred_at=Visit.occurred_at - dt.timedelta(seconds=seconds))
        )


async def row_as_text(visit_id: uuid.UUID) -> str:
    """Every column of the row as one string, for "does this value appear anywhere"."""
    async with session_scope() as db:
        return str(
            (
                await db.execute(
                    text("SELECT to_jsonb(v)::text FROM visits v WHERE id = :id"),
                    {"id": visit_id},
                )
            ).scalar_one()
        )


async def audit_details_for(target_id: uuid.UUID, action: str) -> list[dict[str, Any]]:
    async with session_scope() as db:
        rows = await db.execute(
            text("SELECT detail FROM audit_log WHERE target_id = :t AND action = :a ORDER BY id"),
            {"t": str(target_id), "a": action},
        )
        return [dict(row) for row in rows.scalars()]


async def audit_details_for_action(action: str) -> list[dict[str, Any]]:
    """Every audit detail recorded for ``action``, oldest first."""
    async with session_scope() as db:
        rows = await db.execute(
            text("SELECT detail FROM audit_log WHERE action = :a ORDER BY id"), {"a": action}
        )
        return [dict(row) for row in rows.scalars()]
