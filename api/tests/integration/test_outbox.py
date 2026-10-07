"""Visit alerts through the outbox, against the real database (F7, ADR-0009, NFR5.AC2).

The send is intercepted -- one HTTP client replaced, not the database (ES3) -- so what
is under test is the queue: atomic with the visit, deduplicated by the engine under
concurrency, retried with backoff, dead-lettered, retried by hand, held by quiet hours,
and never fed by automated traffic.

The suite shares the dev database. Every outbox row a test creates is deleted after it,
the worker is only ever pointed at rows the test made (``only=``), and quiet hours are
put back as they were.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration import capture_helpers as ch
from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.audit import log as audit
from tracelet.auth.models import AdminRole
from tracelet.capture.models import GeofenceState
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.geofence import store as geofences
from tracelet.geofence.models import Geofence, NotifyPriority, ShapeKind
from tracelet.geofence.store import Evaluation
from tracelet.inference import engine
from tracelet.inference import store as inference_store
from tracelet.inference.sources import rdns
from tracelet.notify import alerts, outbox, telegram, worker
from tracelet.notify import settings as notify_settings
from tracelet.notify.outbox import Outbox, OutboxKind, OutboxStatus

pytestmark = pytest.mark.integration

OWNER_CHAT = 424242
MTNL_MUMBAI_PTR = "triband-mum-49.207.12.34.mtnl.net.in"


@pytest.fixture
def integration_settings(integration_settings: Settings) -> Settings:
    """With an owner chat, so the worker reaches the (intercepted) send."""
    return integration_settings.model_copy(update={"telegram_owner_chat_id": OWNER_CHAT})


@pytest.fixture(autouse=True)
async def _isolated(db_app: object) -> AsyncIterator[None]:
    """Remove this test's outbox rows and geofences; restore quiet hours."""
    del db_app
    async with session_scope() as db:
        high_water = (
            await db.execute(text("SELECT coalesce(max(id), 0) FROM outbox"))
        ).scalar_one()
        saved = (
            await db.execute(
                text("SELECT value FROM app_settings WHERE key = :k"),
                {"k": notify_settings.QUIET_HOURS_KEY},
            )
        ).scalar_one_or_none()
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM outbox WHERE id > :h"), {"h": high_water})
        await db.execute(text("DELETE FROM geofences WHERE name LIKE 'itest %'"))
        await db.execute(
            text("DELETE FROM app_settings WHERE key = :k"), {"k": notify_settings.QUIET_HOURS_KEY}
        )
        if saved is not None:
            await db.execute(
                text("INSERT INTO app_settings (key, value) VALUES (:k, CAST(:v AS jsonb))"),
                {"k": notify_settings.QUIET_HOURS_KEY, "v": json.dumps(saved)},
            )


@pytest.fixture(autouse=True)
def _only_this_tests_geofences(monkeypatch: pytest.MonkeyPatch) -> None:
    """The job sees only geofences made by this module (named ``itest ``): an owner's
    real dev geofence must not change what these tests observe."""
    load_all = geofences.load_active

    async def ours(db: AsyncSession) -> list[geofences.ActiveGeofence]:
        return [f for f in await load_all(db) if f.name.startswith("itest ")]

    monkeypatch.setattr(geofences, "load_active", ours)


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def lookup(ip: str, timeout_s: float) -> str | None:
        del timeout_s
        if ip == rdns.CANARY[0]:
            return rdns.CANARY[1]
        return MTNL_MUMBAI_PTR if ip == ch.VISITOR_IP else None

    rdns.reset_canary_for_tests()
    monkeypatch.setattr(rdns, "_lookup", lookup)
    yield
    rdns.reset_canary_for_tests()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every message the worker sends, captured instead of delivered."""
    captured: list[dict[str, Any]] = []

    async def capture(**kwargs: Any) -> telegram.SendResult:
        captured.append(kwargs)
        return telegram.SendResult(message_id=len(captured), chat_id=int(kwargs["chat_id"]))

    monkeypatch.setattr(telegram, "send_message", capture)
    return captured


async def _visit(client: AsyncClient) -> tuple[uuid.UUID, uuid.UUID]:
    """A finalised visit from the MTNL Mumbai visitor: strict IN and Maharashtra."""
    link = await ch.create_link()
    page = await ch.visit(client, link.slug, headers=ch.BROWSER_HEADERS)
    await client.post(
        f"/api/v1/s/{ch.nonce_from(page)}",
        # A screen is the classifier's positive evidence that a person's browser ran the
        # page (classify/rules.py _plausible); without it the visit is "unknown".
        json={
            "screen": {"w": 412, "h": 915, "dpr": 2.625, "colorDepth": 24, "touchPoints": 5},
            "geolocation": {"state": "prompt"},
            "locale": {"tzIana": "Asia/Kolkata"},
        },
        headers={"X-Tracelet-Peer-IP": ch.VISITOR_IP},
    )
    visit_id = (await ch.latest_visit(link.id)).id
    async with session_scope() as db:
        await db.execute(text("UPDATE visits SET cf_colo = 'BOM' WHERE id = :id"), {"id": visit_id})
    return link.id, visit_id


async def _fence(link_id: uuid.UUID, keys: list[str], priority: NotifyPriority) -> uuid.UUID:
    fence_id = uuid.uuid4()
    async with session_scope() as db:
        db.add(
            Geofence(
                id=fence_id,
                name=f"itest {keys[0]}",
                shape_kind=ShapeKind.REGION,
                region_keys=keys,
                notify_priority=priority,
                link_ids=[link_id],
            )
        )
    return fence_id


async def _rows_for_visit(visit_id: uuid.UUID) -> list[Outbox]:
    async with session_scope() as db:
        return list(
            (
                await db.execute(
                    select(Outbox).where(Outbox.payload["visit_id"].astext == str(visit_id))
                )
            ).scalars()
        )


async def _row(row_id: int) -> Outbox:
    async with session_scope() as db:
        return (
            await db.execute(
                select(Outbox).where(Outbox.id == row_id).execution_options(populate_existing=True)
            )
        ).scalar_one()


async def _queued(priority: NotifyPriority = NotifyPriority.NORMAL, **overrides: Any) -> int:
    """A visit alert written straight into the queue, as the inference job would."""
    row = Outbox(
        kind=OutboxKind.VISIT_ALERT,
        dedup_key=f"itest:{uuid.uuid4()}",
        priority=priority,
        payload={"visit_id": str(uuid.uuid4()), "link": {"label": "itest", "slug": "itest"}},
        **overrides,
    )
    async with session_scope() as db:
        db.add(row)
        await db.flush()
        return row.id


# ---------------------------------------------------------------------------
# Enqueue: atomic with the visit, human only, deduplicated
# ---------------------------------------------------------------------------


async def test_an_inside_visit_queues_a_high_priority_alert_naming_the_geofence(
    db_client: AsyncClient, integration_settings: Settings, resolver: None
) -> None:
    del resolver
    link_id, visit_id = await _visit(db_client)
    await _fence(link_id, ["IN|Maharashtra"], NotifyPriority.HIGH)

    assert await engine.run_once(integration_settings, only=[visit_id]) == 1

    visit = await ch.get_visit(visit_id)
    # The premise: this visitor is a person. On failure, the rules that fired say why.
    assert visit.classification.value == "human", "; ".join(
        f"{x['rule_id']}={x.get('weight')}:{x.get('detail')}"
        for x in visit.signals
        if not x["rule_id"].startswith("inference.")
    )
    assert visit.geofence_state is GeofenceState.INSIDE
    (row,) = await _rows_for_visit(visit_id)
    assert row.priority is NotifyPriority.HIGH
    assert row.status is OutboxStatus.PENDING
    assert row.payload["geofence"]["name"] == "itest IN|Maharashtra"
    assert row.payload["visit_url"].endswith(f"/visits/{visit_id}")
    assert row.dedup_key is not None and row.dedup_key.startswith(f"visit_alert:{link_id}:")


async def test_an_outside_visit_queues_a_normal_alert(
    db_client: AsyncClient, integration_settings: Settings, resolver: None
) -> None:
    del resolver
    link_id, visit_id = await _visit(db_client)
    await _fence(link_id, ["IN|Karnataka"], NotifyPriority.HIGH)
    await engine.run_once(integration_settings, only=[visit_id])

    visit = await ch.get_visit(visit_id)
    assert (visit.strict_country_code, visit.strict_admin1) == ("IN", "Maharashtra"), (
        visit.abstain_reason
    )
    (row,) = await _rows_for_visit(visit_id)
    assert row.payload["geofence_state"] == "outside"
    assert row.priority is NotifyPriority.NORMAL


async def test_automated_traffic_never_queues_an_alert(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """CLAUDE.md invariant 6, F7.AC1 -- at the last line, whatever the policy says."""
    _, visit_id = await _visit(db_client)
    async with session_scope() as db:
        await db.execute(
            text("UPDATE visits SET classification = 'bot' WHERE id = :id"), {"id": visit_id}
        )
        result = await outbox.enqueue_visit_alert(
            db,
            visit_id,
            Evaluation((), None),
            reporting_tz=integration_settings.reporting_tz,
            base_url="https://x",
        )
    assert (result.queued, result.reason) == (False, "not_human")
    assert await _rows_for_visit(visit_id) == []


async def test_a_silent_geofence_mutes_its_inside_alert(
    db_client: AsyncClient, integration_settings: Settings, resolver: None
) -> None:
    """SPEC section 11 row 18: either the link or the geofence can mute."""
    del resolver
    link_id, visit_id = await _visit(db_client)
    await _fence(link_id, ["IN|Maharashtra"], NotifyPriority.SILENT)
    await engine.run_once(integration_settings, only=[visit_id])
    assert (await ch.get_visit(visit_id)).geofence_state is GeofenceState.INSIDE
    assert await _rows_for_visit(visit_id) == []


async def test_a_rolled_back_visit_emits_no_alert(
    db_client: AsyncClient, integration_settings: Settings, resolver: None
) -> None:
    """NFR5.AC2, the M6 done-check: the alert is in the visit's transaction, so a visit
    that does not commit leaves nothing in the queue."""
    del resolver
    _, visit_id = await _visit(db_client)

    class AbortError(Exception):
        pass

    with pytest.raises(AbortError):
        async with session_scope() as db:
            ((facts, row),) = await engine._claim(db, 1, [visit_id])
            active = await inference_store.active_settings(db)
            # No location (an engine error), but classified for real: a person, so owed
            # an alert -- which is what must not survive the rollback.
            result = await engine._classify(
                engine.engine_error(facts, active.version, "itest"),
                row,
                integration_settings,
                active,
            )
            assert await engine.persist(db, result, [], integration_settings)
            assert len(await _rows_for_visit_in(db, visit_id)) == 1, "queued inside the transaction"
            raise AbortError

    assert await _rows_for_visit(visit_id) == []
    assert (await ch.get_visit(visit_id)).inferred_at is None


async def _rows_for_visit_in(db: AsyncSession, visit_id: uuid.UUID) -> list[Outbox]:
    return list(
        (
            await db.execute(
                select(Outbox).where(Outbox.payload["visit_id"].astext == str(visit_id))
            )
        ).scalars()
    )


async def test_one_visitor_alerts_once_a_day_even_when_visits_race(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """F7.AC2, the M6 done-check, proven concurrently: two visits from one visitor are
    queued from two transactions at once, and the unique key admits exactly one."""
    link = await ch.create_link()
    visits = []
    for _ in range(2):
        await ch.visit(db_client, link.slug)
        visits.append((await ch.latest_visit(link.id)).id)
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE visits SET visitor_id = '\\x0102'::bytea, classification = 'human', "
                "occurred_at = now() WHERE id = ANY(:ids)"
            ),
            {"ids": visits},
        )

    gate = asyncio.Event()

    async def queue(visit_id: uuid.UUID) -> outbox.Enqueued:
        async with session_scope() as db:
            await gate.wait()
            result = await outbox.enqueue_visit_alert(
                db,
                visit_id,
                Evaluation((), None),
                reporting_tz=integration_settings.reporting_tz,
                base_url="https://x",
            )
            await asyncio.sleep(0.2)  # hold the transaction open across the other insert
            return result

    tasks = [asyncio.create_task(queue(v)) for v in visits]
    gate.set()
    results = await asyncio.gather(*tasks)

    assert sorted(r.reason for r in results) == ["duplicate", "queued"]
    async with session_scope() as db:
        rows = (
            await db.execute(
                text("SELECT count(*) FROM outbox WHERE dedup_key LIKE :k"),
                {"k": f"visit_alert:{link.id}:0102:%"},
            )
        ).scalar_one()
    assert rows == 1


async def _same_visitor_visits(
    client: AsyncClient, n: int, *, outside: str = "high"
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """``n`` human visits today from one visitor, on a link whose ``inside`` alert is high,
    ``undetermined`` normal and ``outside`` as given (high by default) -- so the evaluation
    alone picks each priority."""
    link = await ch.create_link()
    visits = []
    for _ in range(n):
        await ch.visit(client, link.slug)
        visits.append((await ch.latest_visit(link.id)).id)
    policy = {"inside": "high", "outside": outside, "undetermined": "normal", "automated": "silent"}
    async with session_scope() as db:
        await db.execute(
            text("UPDATE links SET notify_policy = CAST(:p AS jsonb) WHERE id = :id"),
            {"p": json.dumps(policy), "id": link.id},
        )
        await db.execute(
            text(
                "UPDATE visits SET visitor_id = '\\x0a0b'::bytea, classification = 'human', "
                "occurred_at = now() WHERE id = ANY(:ids)"
            ),
            {"ids": visits},
        )
    return link.id, visits


HIGH = Evaluation((), None)  # no geofence applies: the link's `outside`, high here
NORMAL = Evaluation((), GeofenceState.UNDETERMINED)  # the link's `undetermined`, normal


async def _enqueue(visit_id: uuid.UUID, evaluation: Evaluation, settings: Settings) -> str:
    async with session_scope() as db:
        result = await outbox.enqueue_visit_alert(
            db, visit_id, evaluation, reporting_tz=settings.reporting_tz, base_url="https://x"
        )
    return result.reason


async def _day_rows(link_id: uuid.UUID) -> list[tuple[str, NotifyPriority]]:
    async with session_scope() as db:
        rows = await db.execute(
            select(Outbox.dedup_key, Outbox.priority)
            .where(Outbox.dedup_key.like(f"visit_alert:{link_id}:%"))
            .order_by(Outbox.id)
        )
        return [(key or "", priority) for key, priority in rows]


async def test_a_high_alert_upgrades_a_normal_one_once_a_day(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """SPEC section 11 row 20: location refused, then allowed and confirmed inside, both
    alert -- and that is the day's last alert, whatever comes after."""
    link_id, (v1, v2, v3, v4) = await _same_visitor_visits(db_client, 4)

    assert await _enqueue(v1, NORMAL, integration_settings) == "queued"
    assert await _enqueue(v2, HIGH, integration_settings) == "upgrade"
    assert await _enqueue(v3, HIGH, integration_settings) == "duplicate"
    assert await _enqueue(v4, NORMAL, integration_settings) == "duplicate"

    (base, base_priority), (upgrade, upgrade_priority) = await _day_rows(link_id)
    assert upgrade == base + outbox.UPGRADE
    assert (base_priority, upgrade_priority) == (NotifyPriority.NORMAL, NotifyPriority.HIGH)


async def test_a_high_first_alert_leaves_nothing_to_upgrade(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    link_id, (v1, v2, v3) = await _same_visitor_visits(db_client, 3)

    assert await _enqueue(v1, HIGH, integration_settings) == "queued"
    assert await _enqueue(v2, NORMAL, integration_settings) == "duplicate"
    assert await _enqueue(v3, HIGH, integration_settings) == "duplicate"

    ((_, priority),) = await _day_rows(link_id)
    assert priority is NotifyPriority.HIGH


async def test_concurrent_upgrades_race_to_one(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """The upgrade is a unique key too, so it holds under concurrent visits like the day
    key does (SPEC section 11 row 20)."""
    link_id, (first, *racing) = await _same_visitor_visits(db_client, 3)
    assert await _enqueue(first, NORMAL, integration_settings) == "queued"

    gate = asyncio.Event()

    async def queue(visit_id: uuid.UUID) -> str:
        async with session_scope() as db:
            await gate.wait()
            result = await outbox.enqueue_visit_alert(
                db,
                visit_id,
                HIGH,
                reporting_tz=integration_settings.reporting_tz,
                base_url="https://x",
            )
            await asyncio.sleep(0.2)  # hold the transaction open across the other insert
            return result.reason

    tasks = [asyncio.create_task(queue(v)) for v in racing]
    gate.set()
    assert sorted(await asyncio.gather(*tasks)) == ["duplicate", "upgrade"]
    assert [p for _, p in await _day_rows(link_id)] == [NotifyPriority.NORMAL, NotifyPriority.HIGH]


# On a link whose `outside` is normal: a confirmed outside, and a confirmed inside (high).
OUTSIDE = Evaluation((), GeofenceState.OUTSIDE)
INSIDE = Evaluation((), GeofenceState.INSIDE)


async def test_a_confirmed_outside_follows_location_not_confirmed_once(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """SPEC section 11 row 21, the owner's phone test: location refused, then allowed and
    placed outside -- both alert. A later inside still upgrades (row 20): three alerts, in
    the order not confirmed, outside, inside, and nothing after."""
    link_id, (v1, v2, v3, v4, v5) = await _same_visitor_visits(db_client, 5, outside="normal")

    assert await _enqueue(v1, NORMAL, integration_settings) == "queued"
    assert await _enqueue(v2, OUTSIDE, integration_settings) == "confirmed"
    assert await _enqueue(v3, OUTSIDE, integration_settings) == "duplicate"
    assert await _enqueue(v4, INSIDE, integration_settings) == "upgrade"
    assert await _enqueue(v5, OUTSIDE, integration_settings) == "duplicate"

    (base, _), (confirmed, _), (upgrade, _) = rows = await _day_rows(link_id)
    assert (confirmed, upgrade) == (base + outbox.CONFIRMED, base + outbox.UPGRADE)
    assert [p for _, p in rows] == [
        NotifyPriority.NORMAL,
        NotifyPriority.NORMAL,
        NotifyPriority.HIGH,
    ]


async def test_no_confirmed_outside_after_a_high_alert(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """A normal alert never follows a high one: once the inside upgrade has gone out, a
    confirmed outside that day says nothing."""
    link_id, (v1, v2, v3) = await _same_visitor_visits(db_client, 3, outside="normal")

    assert await _enqueue(v1, NORMAL, integration_settings) == "queued"
    assert await _enqueue(v2, INSIDE, integration_settings) == "upgrade"
    assert await _enqueue(v3, OUTSIDE, integration_settings) == "duplicate"

    assert [p for _, p in await _day_rows(link_id)] == [NotifyPriority.NORMAL, NotifyPriority.HIGH]


async def test_a_confirmed_first_alert_has_nothing_to_confirm(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """Only "Location not confirmed" is followed by a confirmation; an outside first alert
    is the day's alert, and a later unconfirmed visit adds nothing."""
    link_id, (v1, v2, v3) = await _same_visitor_visits(db_client, 3, outside="normal")

    assert await _enqueue(v1, OUTSIDE, integration_settings) == "queued"
    assert await _enqueue(v2, OUTSIDE, integration_settings) == "duplicate"
    assert await _enqueue(v3, NORMAL, integration_settings) == "duplicate"

    assert len(await _day_rows(link_id)) == 1


async def test_concurrent_confirmations_race_to_one(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """The confirmation is a unique key too (SPEC section 11 row 21)."""
    link_id, (first, *racing) = await _same_visitor_visits(db_client, 3, outside="normal")
    assert await _enqueue(first, NORMAL, integration_settings) == "queued"

    gate = asyncio.Event()

    async def queue(visit_id: uuid.UUID) -> str:
        async with session_scope() as db:
            await gate.wait()
            result = await outbox.enqueue_visit_alert(
                db,
                visit_id,
                OUTSIDE,
                reporting_tz=integration_settings.reporting_tz,
                base_url="https://x",
            )
            await asyncio.sleep(0.2)  # hold the transaction open across the other insert
            return result.reason

    tasks = [asyncio.create_task(queue(v)) for v in racing]
    gate.set()
    assert sorted(await asyncio.gather(*tasks)) == ["confirmed", "duplicate"]
    assert len(await _day_rows(link_id)) == 2


# ---------------------------------------------------------------------------
# The worker: deliver, retry, dead-letter, quiet hours
# ---------------------------------------------------------------------------


async def test_a_queued_alert_is_delivered_to_the_owner_chat(
    integration_settings: Settings, sent: list[dict[str, Any]]
) -> None:
    row_id = await _queued(NotifyPriority.HIGH)
    assert await worker.run_once(integration_settings, only=[row_id]) == 1

    (message,) = sent
    assert message["chat_id"] == OWNER_CHAT
    assert "itest" in message["text"]
    row = await _row(row_id)
    assert row.status is OutboxStatus.DONE and row.completed_at is not None
    assert row.locked_at is None and row.attempts == 1


async def test_a_telegram_outage_retries_with_backoff_then_dead_letters_then_a_manual_retry_delivers(
    integration_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    owner: SignedIn,
) -> None:
    """The M6 done-check, end to end."""
    row_id = await _queued(max_attempts=3)

    async def down(**_kwargs: Any) -> telegram.SendResult:
        raise telegram.TelegramError("HTTP 502: bad gateway")

    monkeypatch.setattr(telegram, "send_message", down)
    for attempt in (1, 2, 3):
        assert await worker.run_once(integration_settings, only=[row_id]) == 0
        row = await _row(row_id)
        assert row.attempts == attempt
        if attempt < 3:
            assert row.status is OutboxStatus.FAILED
            assert row.next_attempt_at > dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
            async with session_scope() as db:  # skip the wait
                await db.execute(
                    text("UPDATE outbox SET next_attempt_at = now() WHERE id = :id"), {"id": row_id}
                )
    row = await _row(row_id)
    assert row.status is OutboxStatus.DEAD and row.last_error == "HTTP 502: bad gateway"

    listed = (await owner.client.get("/api/v1/health/outbox", params={"status": "dead"})).json()
    assert row_id in {i["id"] for i in listed["items"]}
    assert listed["counts"]["dead"] >= 1

    retried = await owner.client.post(
        f"/api/v1/health/outbox/{row_id}/retry", headers=owner.headers()
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "pending" and retried.json()["attempts"] == 0
    details = await ch.audit_details_for_action(audit.Action.OUTBOX_RETRIED)
    assert details[-1]["last_error"] == "HTTP 502: bad gateway"

    captured: list[dict[str, Any]] = []

    async def up(**kwargs: Any) -> telegram.SendResult:
        captured.append(kwargs)
        return telegram.SendResult(message_id=9, chat_id=OWNER_CHAT)

    monkeypatch.setattr(telegram, "send_message", up)
    assert await worker.run_once(integration_settings, only=[row_id]) == 1
    assert (await _row(row_id)).status is OutboxStatus.DONE and len(captured) == 1


async def test_a_permanent_rejection_dead_letters_at_once(
    integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    row_id = await _queued()

    async def rejected(**_kwargs: Any) -> telegram.SendResult:
        raise telegram.TelegramError("chat not found", permanent=True)

    monkeypatch.setattr(telegram, "send_message", rejected)
    await worker.run_once(integration_settings, only=[row_id])
    row = await _row(row_id)
    assert (row.status, row.attempts) == (OutboxStatus.DEAD, 1)


async def test_a_rate_limit_waits_as_long_as_telegram_asks(
    integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    row_id = await _queued()

    async def limited(**_kwargs: Any) -> telegram.SendResult:
        raise telegram.TelegramError("429", retry_after=600)

    monkeypatch.setattr(telegram, "send_message", limited)
    await worker.run_once(integration_settings, only=[row_id])
    row = await _row(row_id)
    wait = row.next_attempt_at - dt.datetime.now(dt.UTC)
    assert row.status is OutboxStatus.FAILED
    assert dt.timedelta(seconds=590) < wait <= dt.timedelta(seconds=600)


async def test_quiet_hours_hold_normal_alerts_and_let_high_through(
    integration_settings: Settings, sent: list[dict[str, Any]]
) -> None:
    """F7.AC9. A window covering the whole day except one minute, in UTC, is quiet now."""
    now = dt.datetime.now(dt.UTC)
    end = (now - dt.timedelta(minutes=2)).strftime("%H:%M")
    start = (now - dt.timedelta(minutes=1)).strftime("%H:%M")
    window = alerts.QuietHours(enabled=True, start=start, end=end, timezone="UTC")
    assert window.active(now)
    async with session_scope() as db:
        await notify_settings.set_quiet_hours(db, window, admin_id=None)
    normal = await _queued(NotifyPriority.NORMAL)
    high = await _queued(NotifyPriority.HIGH)

    assert await worker.run_once(integration_settings, only=[normal, high]) == 1
    assert (await _row(high)).status is OutboxStatus.DONE
    held = await _row(normal)
    assert (held.status, held.attempts) == (OutboxStatus.PENDING, 0), "held, not failed"

    async with session_scope() as db:
        await notify_settings.set_quiet_hours(
            db, dataclasses.replace(window, enabled=False), admin_id=None
        )
    assert await worker.run_once(integration_settings, only=[normal]) == 1
    assert len(sent) == 2


async def test_without_telegram_configured_alerts_wait_rather_than_vanish(
    integration_settings: Settings,
) -> None:
    row_id = await _queued()
    unconfigured = integration_settings.model_copy(update={"telegram_owner_chat_id": None})
    await worker.run_once(unconfigured, only=[row_id])
    row = await _row(row_id)
    assert row.status is OutboxStatus.FAILED
    assert row.last_error == worker.NOT_CONFIGURED


async def test_a_row_abandoned_in_flight_is_recovered(
    integration_settings: Settings, sent: list[dict[str, Any]]
) -> None:
    """Invariant 7: a worker that crashed mid-send leaves a lock; it expires."""
    row_id = await _queued()
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE outbox SET status = 'in_flight', locked_by = 'gone', "
                "locked_at = now() - interval '10 minutes', attempts = 1 WHERE id = :id"
            ),
            {"id": row_id},
        )
    assert await worker.run_once(integration_settings, only=[row_id]) == 1
    row = await _row(row_id)
    assert (row.status, row.attempts) == (OutboxStatus.DONE, 2)
    assert len(sent) == 1


# ---------------------------------------------------------------------------
# The engine's floor beneath the worker (DATA_MODEL 7.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("assignments", "constraint"),
    [
        ("priority = 'silent'", "ck_outbox_never_silent"),
        ("status = 'in_flight'", "ck_outbox_lock_iff_in_flight"),
        ("status = 'done'", "ck_outbox_completed_iff_final"),
        ("completed_at = now()", "ck_outbox_completed_iff_final"),
    ],
)
async def test_the_engine_refuses_an_impossible_row(
    db_app: object, assignments: str, constraint: str
) -> None:
    del db_app
    row_id = await _queued()
    with pytest.raises(IntegrityError, match=constraint):
        async with session_scope() as db:
            await db.execute(
                text(f"UPDATE outbox SET {assignments} WHERE id = :id"),  # noqa: S608 -- literals
                {"id": row_id},
            )


# ---------------------------------------------------------------------------
# The API: settings, retry, the test message (F7.AC8, F7.AC9, F10.AC13)
# ---------------------------------------------------------------------------

SETTINGS = "/api/v1/notifications/settings"
QUIET = {"enabled": True, "start": "22:30", "end": "06:30", "timezone": "Asia/Kolkata"}


async def test_quiet_hours_are_set_by_an_owner_and_audited(owner: SignedIn) -> None:
    response = await owner.client.patch(
        SETTINGS, json={"quiet_hours": QUIET}, headers=owner.headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quiet_hours"] | {"active_now": None} == QUIET | {"active_now": None}
    assert body["telegram"] == {"bot_token_set": True, "chat_id_set": True, "chat_verified": False}

    details = await ch.audit_details_for_action(audit.Action.SETTINGS_CHANGED)
    assert details[-1]["to"] == QUIET


@pytest.mark.parametrize(
    "quiet",
    [
        QUIET | {"start": "24:00"},
        QUIET | {"end": "7:00"},
        QUIET | {"timezone": "Mars/Olympus"},
    ],
)
async def test_quiet_hours_are_validated(owner: SignedIn, quiet: dict[str, Any]) -> None:
    response = await owner.client.patch(
        SETTINGS, json={"quiet_hours": quiet}, headers=owner.headers()
    )
    assert response.status_code == 422


async def test_only_a_dead_letter_can_be_retried(owner: SignedIn) -> None:
    row_id = await _queued()
    response = await owner.client.post(
        f"/api/v1/health/outbox/{row_id}/retry", headers=owner.headers()
    )
    assert response.status_code == 409
    assert response.json()["code"] == "OUTBOX_NOT_DEAD"
    missing = await owner.client.post(
        "/api/v1/health/outbox/999999999/retry", headers=owner.headers()
    )
    assert missing.status_code == 404


async def test_the_test_message_reaches_the_owner_chat(
    owner: SignedIn, sent: list[dict[str, Any]]
) -> None:
    response = await owner.client.post("/api/v1/health/telegram/test", headers=owner.headers())
    assert response.status_code == 200, response.text
    (message,) = sent
    assert message["chat_id"] == OWNER_CHAT
    assert "Tracelet test message" in message["text"]


async def test_a_failed_test_message_says_why_without_the_token(
    owner: SignedIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def rejected(**_kwargs: Any) -> telegram.SendResult:
        raise telegram.TelegramError(
            "Telegram rejected the message (HTTP 400): chat not found", permanent=True
        )

    monkeypatch.setattr(telegram, "send_message", rejected)
    response = await owner.client.post("/api/v1/health/telegram/test", headers=owner.headers())
    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "TELEGRAM_DELIVERY_FAILED"
    assert "chat not found" in body["detail"]
    assert "integration-test-bot-token" not in response.text


async def test_an_analyst_reads_but_cannot_change_or_send(
    owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
    sent: list[dict[str, Any]],
) -> None:
    del owner
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)
    row_id = await _queued(status=OutboxStatus.DEAD, completed_at=dt.datetime.now(dt.UTC))

    assert (await analyst.client.get(SETTINGS)).status_code == 200
    assert (await analyst.client.get("/api/v1/health/outbox")).status_code == 200
    patched = await analyst.client.patch(
        SETTINGS, json={"quiet_hours": QUIET}, headers=analyst.headers()
    )
    retried = await analyst.client.post(
        f"/api/v1/health/outbox/{row_id}/retry", headers=analyst.headers()
    )
    tested = await analyst.client.post("/api/v1/health/telegram/test", headers=analyst.headers())
    assert patched.status_code == retried.status_code == tested.status_code == 403
    assert sent == []
