"""Retention against the real database (F10.AC12, F12.AC7-AC8, ADR-0014).

The headline property is M7's checklist line: **the preview's counts are exactly what the
purge then deletes.** The suite shares the dev database, so every test seeds its own old
rows on a fresh link, and compares the preview with the purge as a whole -- whatever else
the database holds is in both -- then checks its own rows went and its recent ones stayed.

The policy row is shared too; it is put back as it was after every test.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.integration import capture_helpers as ch
from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.audit import log as audit
from tracelet.auth.models import AdminRole
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.lifecycle import retention

pytestmark = pytest.mark.integration

RETENTION = "/api/v1/health/retention"
DEFAULT = {"visit_days": 180, "ip_days": 30, "audit_days": 365}
MARK = "itest-retention"


@pytest.fixture(autouse=True)
async def _policy_restored(db_app: object) -> AsyncIterator[None]:
    """Each test starts at the defaults and the shared policy is put back afterwards --
    through ``change_policy``, because a changed IP period re-dates every stored IP's expiry.
    Restoring the row alone left the dev database's IPs dated by a test's 3- or 5-day period,
    and the next purge cleared them (docs/ERRORS.md E69)."""
    del db_app
    async with session_scope() as db:
        saved = await retention.current_policy(db)
        await retention.change_policy(db, retention.Policy(**DEFAULT), actor=None)
    yield
    async with session_scope() as db:
        await retention.change_policy(db, saved, actor=None)


async def _days_ago(visit_id: uuid.UUID, days: float) -> None:
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE visits SET occurred_at = now() - make_interval(secs => :s) WHERE id = :id"
            ),
            {"s": days * 86400, "id": visit_id},
        )


async def _seed(client: AsyncClient) -> dict[str, Any]:
    """Two visits past 180 days (one with a candidate), a recent visit whose IP has
    expired, a fresh one whose IP has not, two audit rows past 365 days, and a delivered
    alert past 30 days."""
    link = await ch.create_link()
    ids = []
    for _ in range(4):
        await ch.visit(client, link.slug)
        ids.append((await ch.latest_visit(link.id)).id)
    old_a, old_b, expired_ip, fresh = ids
    await _days_ago(old_a, 200)
    await _days_ago(old_b, 400)
    await _days_ago(expired_ip, 10)
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE visits SET ip_enc = '\\x01'::bytea, ip_key_version = 1, "
                "ip_purge_after = now() - interval '1 hour' WHERE id = :id"
            ),
            {"id": expired_ip},
        )
        await db.execute(
            text(
                "UPDATE visits SET ip_enc = '\\x01'::bytea, ip_key_version = 1, "
                "ip_purge_after = now() + interval '20 days' WHERE id = :id"
            ),
            {"id": fresh},
        )
        await db.execute(
            text(
                "INSERT INTO visit_candidates (visit_id, source, level, raw_confidence, weight, "
                "effective_weight, accepted, latency_ms) "
                "VALUES (:v, 'geolite2', 'country', 0.5, 1, 1, true, 1)"
            ),
            {"v": old_a},
        )
        for _ in range(2):
            await db.execute(
                text(
                    "INSERT INTO audit_log (occurred_at, action, target_type, detail) "
                    "VALUES (now() - interval '400 days', 'itest.old', :t, '{}')"
                ),
                {"t": MARK},
            )
        await db.execute(
            text(
                "INSERT INTO outbox (kind, priority, payload, status, attempts, completed_at, "
                "created_at) VALUES ('telegram.visit_alert', 'normal', CAST(:p AS jsonb), "
                "'done', 1, now() - interval '40 days', now() - interval '40 days')"
            ),
            {"p": f'{{"mark": "{MARK}"}}'},
        )
    return {"link": link.id, "old": [old_a, old_b], "expired_ip": expired_ip, "fresh": fresh}


async def _seeded_left(seeded: dict[str, Any]) -> dict[str, int]:
    async with session_scope() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM visits WHERE id = ANY(:old)),
                      (SELECT count(*) FROM visit_candidates WHERE visit_id = ANY(:old)),
                      (SELECT count(*) FROM visits WHERE id = :exp AND ip_enc IS NOT NULL),
                      (SELECT count(*) FROM visits WHERE id = :fresh AND ip_enc IS NOT NULL),
                      (SELECT count(*) FROM audit_log WHERE target_type = :m),
                      (SELECT count(*) FROM outbox WHERE payload->>'mark' = :m)
                    """
                ),
                {
                    "old": seeded["old"],
                    "exp": seeded["expired_ip"],
                    "fresh": seeded["fresh"],
                    "m": MARK,
                },
            )
        ).one()
    keys = ("old_visits", "candidates", "expired_ip", "fresh_ip", "old_audit", "old_outbox")
    return dict(zip(keys, (int(n) for n in row), strict=True))


async def _await_purges() -> None:
    """The manual purge runs in a background task on the test's own loop."""
    tasks = list(retention._TASKS)
    assert tasks, "no purge was started"
    await asyncio.gather(*tasks)  # re-raises a failed purge


async def test_the_purge_deletes_exactly_what_the_preview_counted(
    db_client: AsyncClient, owner: SignedIn
) -> None:
    seeded = await _seed(db_client)
    before = await _seeded_left(seeded)
    # Old audit rows are only deletable by the maintenance role, so an earlier failed run
    # can have left some of its own; the purge takes them all.
    assert before.pop("old_audit") >= 2
    assert before == {
        "old_visits": 2,
        "candidates": 1,
        "expired_ip": 1,
        "fresh_ip": 1,
        "old_outbox": 1,
    }

    shown = await owner.client.post(f"{RETENTION}/preview", headers=owner.headers())
    assert shown.status_code == 200, shown.text
    preview = shown.json()
    counts = preview["counts"]
    # The preview deletes nothing.
    assert (await _seeded_left(seeded))["old_visits"] == 2
    assert counts["visits"] >= 2 and counts["visit_candidates"] >= 1
    assert counts["ip_addresses"] >= 1 and counts["audit_rows"] >= 2
    assert counts["delivered_alerts"] >= 1

    started = await owner.client.post(
        f"{RETENTION}/purge",
        json={"as_of": preview["as_of"], "policy": preview["policy"]},
        headers=owner.headers(),
    )
    assert started.status_code == 202, started.text
    await _await_purges()

    state = (await owner.client.get(RETENTION)).json()
    assert state["purge_running"] is False
    assert state["last_purge"]["trigger"] == "manual"
    assert state["last_purge"]["counts"] == counts  # exactly, category by category
    assert state["last_purge"]["by"] == str(owner.id)

    assert await _seeded_left(seeded) == {
        "old_visits": 0,
        "candidates": 0,
        "expired_ip": 0,
        "fresh_ip": 1,  # its IP has not expired
        "old_audit": 0,
        "old_outbox": 0,
    }
    # The recent visit stays; only its expired IP went.
    assert await ch.visit_count(seeded["link"]) == 2
    actions = await helpers.audit_actions(owner.id)
    assert audit.Action.RETENTION_PREVIEWED in actions
    assert audit.Action.RETENTION_PURGED in actions


async def test_a_purge_needs_a_fresh_preview_of_the_current_policy(owner: SignedIn) -> None:
    preview = (await owner.client.post(f"{RETENTION}/preview", headers=owner.headers())).json()

    old = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=20)
    stale = await owner.client.post(
        f"{RETENTION}/purge",
        json={"as_of": old.isoformat(), "policy": preview["policy"]},
        headers=owner.headers(),
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "RETENTION_PREVIEW_STALE"

    changed = await owner.client.patch(
        RETENTION, json={**DEFAULT, "audit_days": 400}, headers=owner.headers()
    )
    assert changed.status_code == 200, changed.text
    outdated = await owner.client.post(
        f"{RETENTION}/purge",
        json={"as_of": preview["as_of"], "policy": preview["policy"]},
        headers=owner.headers(),
    )
    assert outdated.status_code == 409
    assert outdated.json()["code"] == "RETENTION_PREVIEW_STALE"


async def test_only_one_purge_runs_at_a_time(owner: SignedIn) -> None:
    preview = (await owner.client.post(f"{RETENTION}/preview", headers=owner.headers())).json()
    stack, conn = await retention._locked_connection()
    try:
        state = (await owner.client.get(RETENTION)).json()
        assert state["purge_running"] is True
        refused = await owner.client.post(
            f"{RETENTION}/purge",
            json={"as_of": preview["as_of"], "policy": preview["policy"]},
            headers=owner.headers(),
        )
        assert refused.status_code == 409
        assert refused.json()["code"] == "LIFECYCLE_JOB_RUNNING"
    finally:
        await stack.aclose()
    assert (await owner.client.get(RETENTION)).json()["purge_running"] is False
    del conn


async def test_changing_the_policy_is_validated_audited_and_reaches_old_ips(
    db_client: AsyncClient, owner: SignedIn
) -> None:
    for body, field in (
        ({**DEFAULT, "ip_days": 200}, "ip_days"),
        ({**DEFAULT, "visit_days": 7}, "visit_days"),
        ({**DEFAULT, "audit_days": 0}, "audit_days"),
    ):
        refused = await owner.client.patch(RETENTION, json=body, headers=owner.headers())
        assert refused.status_code == 422, (field, refused.text)

    link = await ch.create_link()
    await ch.visit(db_client, link.slug)
    visit = await ch.latest_visit(link.id)
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE visits SET ip_enc = '\\x01'::bytea, ip_key_version = 1, "
                "ip_purge_after = occurred_at + interval '30 days' WHERE id = :id"
            ),
            {"id": visit.id},
        )

    saved = await owner.client.patch(
        RETENTION, json={**DEFAULT, "ip_days": 5}, headers=owner.headers()
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["policy"] == {**DEFAULT, "ip_days": 5}
    async with session_scope() as db:
        days = (
            await db.execute(
                text(
                    "SELECT extract(epoch FROM ip_purge_after - occurred_at) / 86400 "
                    "FROM visits WHERE id = :id"
                ),
                {"id": visit.id},
            )
        ).scalar_one()
    assert round(float(days)) == 5

    (detail,) = await helpers.audit_details(owner.id, audit.Action.RETENTION_CHANGED)
    assert detail["from"] == DEFAULT
    assert detail["to"] == {**DEFAULT, "ip_days": 5}


async def test_a_captured_ip_expires_by_the_policy(
    db_client: AsyncClient, owner: SignedIn, integration_settings: Settings
) -> None:
    del integration_settings
    await owner.client.patch(RETENTION, json={**DEFAULT, "ip_days": 3}, headers=owner.headers())
    link = await ch.create_link()
    await ch.visit(db_client, link.slug)
    visit = await ch.latest_visit(link.id)
    if visit.ip_enc is None:
        pytest.skip("the suite's environment has no IP key, so nothing is sealed")
    assert visit.ip_purge_after is not None
    assert round((visit.ip_purge_after - visit.occurred_at).total_seconds() / 86400) == 3


async def test_retention_writes_are_the_owners(
    new_client: ClientFactory, integration_settings: Settings, totp_clock: TotpClock
) -> None:
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)
    assert (await analyst.client.get(RETENTION)).status_code == 200
    for method, path, body in (
        ("PATCH", RETENTION, DEFAULT),
        ("POST", f"{RETENTION}/preview", None),
        ("POST", f"{RETENTION}/purge", {"as_of": "2026-01-01T00:00:00Z", "policy": DEFAULT}),
    ):
        response = await analyst.client.request(method, path, json=body, headers=analyst.headers())
        assert response.status_code == 403, (method, path, response.text)


async def test_the_nightly_purge_runs_once_a_night(owner: SignedIn) -> None:
    del owner
    first = await retention.run_nightly_once()
    second = await retention.run_nightly_once()
    assert second is None  # tonight's has run
    if first is None:
        pytest.skip("a scheduled purge had already run tonight in this database")
