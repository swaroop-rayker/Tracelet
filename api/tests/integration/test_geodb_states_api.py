"""Geo-database states, the release check, progress and auto-update, against the real
database (F10.AC3, F10.AC4, SPEC section 11 row 24).

Vendors are intercepted -- an httpx transport, never the network. The suite shares the dev
database, whose geo databases are real: every row a test adds is removed, and the settings
table is put back as it was.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from tests.integration import helpers
from tests.integration.helpers import SignedIn
from tracelet.audit import log as audit
from tracelet.auth.models import AdminRole
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.health import ops_router
from tracelet.inference.geodb import check, maintenance
from tracelet.inference.geodb.installer import InstallResult, Progress

pytestmark = pytest.mark.integration

DBS = "/api/v1/health/databases"
LAST_MODIFIED = "Wed, 07 Oct 2026 06:00:00 GMT"


@pytest.fixture(autouse=True)
async def _geo_tables_restored(db_app: object) -> AsyncIterator[None]:
    del db_app
    async with session_scope() as db:
        saved = [
            dict(r._mapping) for r in await db.execute(text("SELECT * FROM geo_database_settings"))
        ]
        high_water = (
            await db.execute(text("SELECT coalesce(max(id), 0) FROM geo_databases"))
        ).scalar_one()
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM geo_databases WHERE id > :h"), {"h": high_water})
        await db.execute(text("DELETE FROM geo_database_settings"))
        for row in saved:
            await db.execute(
                text(
                    "INSERT INTO geo_database_settings (name, auto_update, latest_version, "
                    "latest_released_at, checked_at, check_error, updated_by, updated_at) VALUES "
                    "(:name, :auto_update, :latest_version, :latest_released_at, :checked_at, "
                    ":check_error, :updated_by, :updated_at)"
                ),
                row,
            )


def _vendor(seen: list[httpx.Request]) -> httpx.AsyncClient:
    """DB-IP has not published this month yet; GeoNames answers with a date; the Tor list
    refuses."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        host, path = request.url.host, request.url.path
        if host == "download.db-ip.com":
            month = dt.datetime.now(dt.UTC).strftime("%Y-%m")
            if month in path:
                return httpx.Response(404)
            return httpx.Response(200, headers={"last-modified": LAST_MODIFIED})
        if "torproject" in host:
            return httpx.Response(403)
        return httpx.Response(200, headers={"last-modified": LAST_MODIFIED})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _by_name(owner: SignedIn) -> dict[str, dict[str, Any]]:
    body = (await owner.client.get(DBS)).json()
    return {d["name"]: d for d in body["databases"]}


async def test_the_check_finds_releases_and_says_when_it_cannot(
    owner: SignedIn, integration_settings: Settings
) -> None:
    seen: list[httpx.Request] = []
    results = {r.name: r for r in await check.check_all(integration_settings, client=_vendor(seen))}

    assert all(r.method == "HEAD" for r in seen), "a check never downloads"
    assert not [r for r in seen if "ip2location" in r.url.host], "metered: never asked"
    dbip = results["dbip-city-lite"]
    assert dbip.error is None and dbip.latest_version is not None
    assert dbip.latest_version != dt.datetime.now(dt.UTC).strftime("%Y-%m"), "fell back a month"
    assert results["tor-exits"].error is not None and "403" in results["tor-exits"].error

    states = await _by_name(owner)
    assert states["tor-exits"]["state"] in ("unable_to_update", "update_failed", "updating")
    assert states["tor-exits"]["check_error"] is not None
    assert states["geonames-admin1"]["latest"]["released_at"].startswith("2026-10-07T06:00")
    for name, d in states.items():
        if not d["configured"]:
            assert d["state"] in ("unable_to_update", "update_failed", "updating"), name


async def test_an_update_in_flight_shows_its_percent(owner: SignedIn) -> None:
    async with session_scope() as db:
        row_id = (
            await db.execute(
                text(
                    "INSERT INTO geo_databases (name, status, staleness_threshold_days) "
                    "VALUES ('tor-exits', 'downloading', 7) RETURNING id"
                )
            )
        ).scalar_one()
    progress = Progress(row_id)
    await progress.bytes(25, 100)
    state = (await _by_name(owner))["tor-exits"]
    assert state["state"] == "updating"
    assert state["progress"] == {"phase": "downloading", "percent": 20}

    await progress.phase("validating")
    state = (await _by_name(owner))["tor-exits"]
    assert state["progress"]["phase"] == "validating" and state["progress"]["percent"] == 90

    refused = await owner.client.post(f"{DBS}/tor-exits/update", headers=owner.headers())
    assert refused.status_code == 409


async def test_a_failed_update_says_why_and_raises_the_banner(owner: SignedIn) -> None:
    async with session_scope() as db:
        await db.execute(
            text(
                "INSERT INTO geo_databases (name, status, staleness_threshold_days, last_error) "
                "VALUES ('dbip-asn-lite', 'failed', 45, 'checksum mismatch')"
            )
        )
    state = (await _by_name(owner))["dbip-asn-lite"]
    assert state["state"] == "update_failed"
    assert state["last_attempt"]["error"] == "checksum mismatch"
    banner = (await owner.client.get("/api/v1/health/degradation")).json()
    condition = next(c for c in banner["conditions"] if c["key"] == "geodb:dbip-asn-lite")
    assert "checksum mismatch" in condition["detail"]


async def test_auto_update_off_is_skipped_by_the_scheduler_only(
    owner: SignedIn, integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed: list[str] = []

    async def install(spec: Any, settings: Settings, **_: Any) -> InstallResult:
        del settings
        installed.append(spec.name)
        return InstallResult(spec.name, "unchanged")

    monkeypatch.setattr(maintenance, "install", install)

    off = await owner.client.patch(
        f"{DBS}/tor-exits", json={"auto_update": False}, headers=owner.headers()
    )
    assert off.status_code == 200, off.text
    assert off.json()["auto_update"] is False
    (detail,) = await helpers.audit_details(owner.id, audit.Action.GEO_DB_TOGGLED)
    assert detail["auto_update"] == {"from": True, "to": False}

    await maintenance.update_all(
        integration_settings, only=["tor-exits"], force=True, respect_auto_update=True
    )
    assert installed == [], "the scheduler leaves it alone"
    await maintenance.update_all(integration_settings, only=["tor-exits"], force=True)
    assert installed == ["tor-exits"], "a manual update still runs"

    on = await owner.client.patch(
        f"{DBS}/tor-exits", json={"auto_update": True}, headers=owner.headers()
    )
    assert on.json()["auto_update"] is True


async def test_geo_database_writes_are_the_owners(
    new_client: Any, integration_settings: Settings, totp_clock: helpers.TotpClock
) -> None:
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)
    assert (await analyst.client.get(DBS)).status_code == 200
    for method, path, body in (
        ("PATCH", f"{DBS}/tor-exits", {"auto_update": False}),
        ("POST", f"{DBS}/check", None),
    ):
        response = await analyst.client.request(method, path, json=body, headers=analyst.headers())
        assert response.status_code == 403, (method, path)


async def test_update_downloads_nothing_when_nothing_is_newer(
    owner: SignedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPEC section 11 row 26: Update asks first; Download again does not."""
    asked: list[str] = []
    installed: list[tuple[str, bool]] = []

    async def nothing_newer(spec: Any, settings: Settings) -> bool:
        del settings
        asked.append(spec.name)
        return False

    async def update_all(settings: Settings, *, only: list[str], force: bool) -> None:
        del settings
        installed.extend((name, force) for name in only)

    monkeypatch.setattr(maintenance, "manual_update_due", nothing_newer)
    monkeypatch.setattr(maintenance, "update_all", update_all)

    same = await owner.client.post(f"{DBS}/geonames-admin1/update", headers=owner.headers())
    assert same.status_code == 200, same.text
    assert same.json() == {"name": "geonames-admin1", "status": "up_to_date"}
    assert asked == ["geonames-admin1"] and installed == []

    again = await owner.client.post(
        f"{DBS}/geonames-admin1/update?force=true", headers=owner.headers()
    )
    assert again.status_code == 202, again.text
    assert again.json()["status"] == "started"
    await asyncio.gather(*list(ops_router._TASKS))
    assert asked == ["geonames-admin1"], "Download again does not ask"
    assert installed == [("geonames-admin1", True)]

    details = await helpers.audit_details(owner.id, audit.Action.GEO_DB_UPDATE_REQUESTED)
    outcomes = sorted((d["force"], d["outcome"]) for d in details)
    assert outcomes == [(False, "up_to_date"), (True, "started")]
