"""System Health's endpoints against the real database (F10, F11.AC9, API section 10).

The suite shares the dev database: the rate-limit overrides and any outbox, backup or
breaker rows a test makes are put back or removed afterwards.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections import namedtuple
from collections.abc import AsyncIterator
from typing import Any

import psutil
import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.audit import log as audit
from tracelet.auth.models import AdminRole
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.health import ops_router
from tracelet.inference import outbound
from tracelet.inference.geodb import maintenance
from tracelet.lifecycle import backups
from tracelet.lifecycle.models import BackupKind
from tracelet.ratelimit import gcra

pytestmark = pytest.mark.integration

HEALTH = "/api/v1/health"


@pytest.fixture(autouse=True)
async def _shared_state_restored(db_app: object) -> AsyncIterator[None]:
    del db_app
    async with session_scope() as db:
        saved = (
            await db.execute(text("SELECT value FROM app_settings WHERE key = 'ratelimits'"))
        ).scalar_one_or_none()
        high_water = (
            await db.execute(text("SELECT coalesce(max(id), 0) FROM outbox"))
        ).scalar_one()
        since = (await db.execute(text("SELECT now()"))).scalar_one()
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM app_settings WHERE key = 'ratelimits'"))
        if saved is not None:
            await db.execute(
                text(
                    "INSERT INTO app_settings (key, value) VALUES ('ratelimits', CAST(:v AS jsonb))"
                ),
                {"v": json.dumps(saved)},
            )
        await db.execute(text("DELETE FROM outbox WHERE id > :h"), {"h": high_water})
        await db.execute(text("DELETE FROM restore_checks WHERE started_at >= :t"), {"t": since})
        await db.execute(text("DELETE FROM backups WHERE started_at >= :t"), {"t": since})
        await db.execute(text("DELETE FROM rate_limit_buckets WHERE key LIKE 'breaker:%'"))
        await gcra.reload_overrides(db)
    outbound.IPWHOIS_BREAKER.success()


async def test_system_metrics_are_real_and_say_whose_they_are(owner: SignedIn) -> None:
    response = await owner.client.get(f"{HEALTH}/system")
    assert response.status_code == 200, response.text
    body = response.json()
    # The suite runs without the host mount, so it must say these are the container's.
    assert body["scope"] in ("host", "container")
    if body["scope"] == "container":
        assert "not mounted" in body["scope_reason"]
    assert 0.0 <= body["cpu"]["percent"] <= 100.0
    assert body["cpu"]["count"] >= 1 and len(body["cpu"]["load"]) == 3
    assert 0 < body["memory"]["used"] < body["memory"]["total"]
    assert 0 < body["disk"]["used"] < body["disk"]["total"]
    assert body["disk"]["state"] in ("ok", "warn", "critical")
    assert body["uptime_seconds"] > 0
    assert body["database_bytes"] > 1_000_000
    temperature = body["temperature"]
    assert (temperature["celsius"] is None) == (temperature["reason"] is not None)


async def test_the_geo_database_panel_lists_every_database(owner: SignedIn) -> None:
    body = (await owner.client.get(f"{HEALTH}/databases")).json()
    names = {d["name"] for d in body["databases"]}
    assert {"dbip-city-lite", "geolite2-city", "tor-exits"} <= names
    for d in body["databases"]:
        assert d["verdict"] in ("up_to_date", "stale", "missing", "not_configured")
        if d["verdict"] == "up_to_date":
            assert d["installed"]["sha256"] and d["installed"]["size_bytes"]


async def test_an_update_is_started_once_and_audited(
    owner: SignedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[list[str] | None] = []
    release = asyncio.Event()

    async def update_all(
        settings: Settings, *, only: list[str] | None = None, force: bool = False
    ) -> list[Any]:
        del settings
        assert force
        called.append(only)
        await release.wait()
        return []

    monkeypatch.setattr(maintenance, "update_all", update_all)
    assert (
        await owner.client.post(f"{HEALTH}/databases/nope/update", headers=owner.headers())
    ).status_code == 404
    started = await owner.client.post(
        f"{HEALTH}/databases/tor-exits/update", headers=owner.headers()
    )
    assert started.status_code == 202, started.text
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*ops_router._TASKS)
    assert called == [["tor-exits"]]
    assert audit.Action.GEO_DB_UPDATE_REQUESTED in await helpers.audit_actions(owner.id)


async def test_rate_limits_change_without_a_restart(owner: SignedIn) -> None:
    listed = (await owner.client.get(f"{HEALTH}/ratelimits")).json()
    cap = next(item for item in listed["limits"] if item["name"] == "cap_m")
    assert cap["overridden"] is False and cap["per_period"] == cap["default"]["per_period"]

    changed = await owner.client.patch(
        f"{HEALTH}/ratelimits",
        json={"limits": {"cap_m": {"per_period": 90, "period_seconds": 60, "burst": 15}}},
        headers=owner.headers(),
    )
    assert changed.status_code == 200, changed.text
    cap = next(item for item in changed.json()["limits"] if item["name"] == "cap_m")
    assert (cap["overridden"], cap["per_period"], cap["burst"]) == (True, 90, 15)
    async with session_scope() as db:
        effective = await gcra.effective(db, gcra.Limit("cap_m", 30, dt.timedelta(minutes=1), 10))
    assert (effective.per_period, effective.burst) == (90, 15)

    (detail,) = await helpers.audit_details(owner.id, audit.Action.SETTINGS_CHANGED)
    assert detail["key"] == "ratelimits"
    assert detail["to"]["cap_m"]["per_period"] == 90

    reset = await owner.client.patch(
        f"{HEALTH}/ratelimits", json={"limits": {"cap_m": None}}, headers=owner.headers()
    )
    cap = next(item for item in reset.json()["limits"] if item["name"] == "cap_m")
    assert cap["overridden"] is False


async def test_a_refused_rate_limit_saves_nothing(owner: SignedIn) -> None:
    refused = await owner.client.patch(
        f"{HEALTH}/ratelimits",
        json={
            "limits": {
                "cap_m": {"per_period": 60, "period_seconds": 60, "burst": 10},
                "out_nominatim_m": {"per_period": 120, "period_seconds": 60, "burst": 1},
                "made_up": {"per_period": 1, "period_seconds": 1, "burst": 1},
            }
        },
        headers=owner.headers(),
    )
    assert refused.status_code == 422
    fields = {e["field"] for e in refused.json()["errors"]}
    assert fields == {"limits.out_nominatim_m", "limits.made_up"}
    listed = (await owner.client.get(f"{HEALTH}/ratelimits")).json()
    assert not [item for item in listed["limits"] if item["overridden"]]


async def test_degradation_names_what_is_wrong_and_what_still_works(
    owner: SignedIn, integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_scope() as db:
        await db.execute(
            text(
                "INSERT INTO outbox (kind, priority, payload, status, attempts, completed_at) "
                "VALUES ('telegram.visit_alert', 'normal', '{}'::jsonb, 'dead', 8, now())"
            )
        )
    for _ in range(outbound.IPWHOIS_BREAKER.threshold):
        await outbound.failed(outbound.IPWHOIS_BREAKER)
    failed = await backups.begin_backup(BackupKind.MANUAL, None)
    await backups._finish_backup(failed, error="itest: disk on fire")
    usage = namedtuple("usage", "total used free percent")
    monkeypatch.setattr(psutil, "disk_usage", lambda _p: usage(100, 97, 3, 97.0))

    body = (await owner.client.get(f"{HEALTH}/degradation")).json()
    by_key = {c["key"]: c for c in body["conditions"]}
    assert by_key["disk"]["severity"] == "critical"
    assert by_key["backups"]["severity"] == "critical"
    assert "disk on fire" in by_key["backups"]["detail"]
    assert by_key["outbox"]["severity"] == "warning"
    assert by_key["breaker:ipwhois"]["severity"] == "warning"
    for condition in body["conditions"]:
        assert condition["still_works"]
    severities = [c["severity"] for c in body["conditions"]]
    assert severities == sorted(severities, key=["critical", "warning", "notice"].index)

    # A recovered service withdraws its own condition.
    await outbound.succeeded(outbound.IPWHOIS_BREAKER)
    body = (await owner.client.get(f"{HEALTH}/degradation")).json()
    assert "breaker:ipwhois" not in {c["key"] for c in body["conditions"]}
    del integration_settings


async def test_health_writes_are_the_owners(
    new_client: ClientFactory, integration_settings: Settings, totp_clock: TotpClock
) -> None:
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)
    for path in ("/system", "/databases", "/degradation", "/ratelimits"):
        assert (await analyst.client.get(f"{HEALTH}{path}")).status_code == 200, path
    for method, path in (("PATCH", "/ratelimits"), ("POST", "/databases/tor-exits/update")):
        response = await analyst.client.request(
            method, f"{HEALTH}{path}", json={"limits": {}}, headers=analyst.headers()
        )
        assert response.status_code == 403, (method, path)


async def test_health_is_not_public(db_client: AsyncClient) -> None:
    for path in ("/system", "/databases", "/degradation", "/ratelimits", "/backups", "/retention"):
        assert (await db_client.get(f"{HEALTH}{path}")).status_code == 401, path
