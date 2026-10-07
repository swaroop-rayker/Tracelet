"""Is a newer release out? A light check, no download (SPEC section 11 row 24, F10.AC3).

For each database the vendor is asked with a ``HEAD`` request for the file's
``Last-Modified`` -- the same date the installer records as ``released_at`` -- or, for DB-IP,
whether this month's edition is published yet (its file name is the version). The result is
stored in ``geo_database_settings``: ``latest_released_at`` / ``latest_version``, or
``check_error`` when the vendor could not be asked.

**IP2Location is never asked over the network:** its download URL is metered per token, and a
check must not spend the allowance. Its "update available" comes from its refresh schedule.
A database whose credentials are not configured is not asked either; it is "unable to
update" for that reason.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

import httpx
import structlog
from sqlalchemy.dialects import postgresql as pg

from tracelet.config import Settings, get_settings
from tracelet.db.engine import session_scope
from tracelet.inference.geodb.catalog import CATALOG, DatabaseSpec, Download
from tracelet.inference.geodb.installer import USER_AGENT, http_date
from tracelet.inference.models import GeoDatabaseSettings

log = structlog.get_logger(__name__)

# Metered per token: a HEAD request may count as a download (IP2Location's terms).
NOT_CHECKED: Final = frozenset({"ip2location-lite-db11"})
TIMEOUT_S: Final = 15.0


@dataclass(frozen=True, slots=True)
class Checked:
    name: str
    latest_version: str | None
    latest_released_at: dt.datetime | None
    error: str | None
    checked: bool  # False: not asked (schedule only, or not configured)


async def _head(client: httpx.AsyncClient, dl: Download) -> httpx.Response:
    if dl.auth is None:
        return await client.head(dl.url)
    return await client.head(dl.url, auth=dl.auth)


async def check_one(
    spec: DatabaseSpec, settings: Settings, client: httpx.AsyncClient, today: dt.date
) -> Checked:
    dl = spec.download(settings, today)
    if dl is None:
        return Checked(spec.name, None, None, "Vendor credentials are not configured.", False)
    if spec.name in NOT_CHECKED:
        return Checked(spec.name, None, None, None, False)
    try:
        response = await _head(client, dl)
        if response.status_code == 404 and dl.fallback is not None:
            # DB-IP: this month's edition is not out yet; last month's is the latest.
            dl = dl.fallback
            response = await _head(client, dl)
        if response.status_code in (401, 403):
            return Checked(
                spec.name,
                None,
                None,
                f"The vendor refused the credentials (HTTP {response.status_code}).",
                True,
            )
        if response.status_code >= 400:
            return Checked(
                spec.name, None, None, f"The vendor answered HTTP {response.status_code}.", True
            )
        return Checked(
            spec.name,
            dl.version,
            http_date(response.headers.get("last-modified")),
            None,
            True,
        )
    except httpx.HTTPError as exc:
        return Checked(
            spec.name, None, None, f"Could not reach the vendor ({type(exc).__name__}).", True
        )


async def check_all(
    settings: Settings | None = None, *, client: httpx.AsyncClient | None = None
) -> list[Checked]:
    """Check every database and store the results. Never raises."""
    settings = settings or get_settings()
    today = dt.datetime.now(dt.UTC).date()
    owns = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(TIMEOUT_S, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    results: list[Checked] = []
    try:
        for spec in CATALOG:
            results.append(await check_one(spec, settings, http, today))
    finally:
        if owns:
            await http.aclose()
    now = dt.datetime.now(dt.UTC)
    async with session_scope() as db:
        for r in results:
            values = {
                "latest_version": r.latest_version,
                "latest_released_at": r.latest_released_at,
                "checked_at": now if r.checked else None,
                "check_error": r.error,
            }
            await db.execute(
                pg.insert(GeoDatabaseSettings)
                .values(name=r.name, **values)
                .on_conflict_do_update(index_elements=[GeoDatabaseSettings.name], set_=values)
            )
    failed = [r.name for r in results if r.error and r.checked]
    log.info("geo_databases_checked", checked=sum(r.checked for r in results), failed=failed)
    return results


def newer_release(
    installed_version: str | None,
    installed_released_at: dt.datetime | None,
    latest_version: str | None,
    latest_released_at: dt.datetime | None,
) -> bool:
    """Whether the last check found something newer than what is serving. Pure, so the rule
    is tested on its own. A minute's slack: the installer records the same header."""
    if (
        latest_version is not None
        and installed_version is not None
        and latest_version != installed_version
    ):
        return True
    if latest_released_at is not None and installed_released_at is not None:
        return latest_released_at - installed_released_at > dt.timedelta(minutes=1)
    return False
