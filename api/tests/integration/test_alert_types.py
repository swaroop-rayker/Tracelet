"""The M7.5 alert types against the real database (F7.AC10-F7.AC15, SPEC section 11 row 27).

What is under test: a new place and a returning visitor ride on the visit's own alert and
go alone only after the day's duplicate; places are recorded for every human visit and
never for a bot; a silent link stays silent; the digest and the spike are exactly once by
their keys; and the settings are the owner's, audited.

No send leaves the suite (ERRORS E56): the one test that runs the worker intercepts the
HTTP client, and every outbox row a test makes is deleted after it (conftest). The alert
types setting is put back as it was.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration import capture_helpers as ch
from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.audit import log as audit
from tracelet.auth.models import AdminRole
from tracelet.capture.models import Link, Visit
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.geofence import store as geofences
from tracelet.geofence.store import Evaluation
from tracelet.inference import engine
from tracelet.inference.sources import rdns
from tracelet.notify import alerts, digest, notes, outbox, spike, telegram, worker
from tracelet.notify import settings as notify_settings
from tracelet.notify.outbox import Outbox, OutboxKind

pytestmark = pytest.mark.integration

OWNER_CHAT = 424242
MTNL_MUMBAI_PTR = "triband-mum-49.207.12.34.mtnl.net.in"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SETTINGS = "/api/v1/notifications/settings"


@pytest.fixture
def integration_settings(integration_settings: Settings) -> Settings:
    """With an owner chat, so the worker reaches the (intercepted) send."""
    return integration_settings.model_copy(update={"telegram_owner_chat_id": OWNER_CHAT})


@pytest.fixture(autouse=True)
async def _alert_types_restored(db_app: object) -> AsyncIterator[None]:
    """Every test starts from the defaults (all off); the dev setting is put back after."""
    del db_app
    key = notify_settings.ALERT_TYPES_KEY
    async with session_scope() as db:
        saved = (
            await db.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": key})
        ).scalar_one_or_none()
        await db.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": key})
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM app_settings WHERE key = :k"), {"k": key})
        if saved is not None:
            await db.execute(
                text("INSERT INTO app_settings (key, value) VALUES (:k, CAST(:v AS jsonb))"),
                {"k": key, "v": json.dumps(saved)},
            )


@pytest.fixture(autouse=True)
def _no_geofences(monkeypatch: pytest.MonkeyPatch) -> None:
    """An owner's real dev geofence must not change a test visit's priority."""

    async def none(db: AsyncSession) -> list[geofences.ActiveGeofence]:
        del db
        return []

    monkeypatch.setattr(geofences, "load_active", none)


@pytest.fixture(autouse=True)
def _resolver(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The visitor's PTR names an MTNL Mumbai host: strict IN and Maharashtra."""

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _enable(**changes: Any) -> None:
    """Switch types on (or change their settings) as the owner would, minus the API."""
    async with session_scope() as db:
        current = await notify_settings.alert_types(db)
        updated = alerts.AlertTypes(
            digest=changes.get("digest", current.digest),
            spike=changes.get("spike", current.spike),
            new_place=changes.get("new_place", current.new_place),
            returning=changes.get("returning", current.returning),
        )
        await notify_settings.set_alert_types(db, updated, admin_id=None)


async def _visit(client: AsyncClient, link: Link, settings: Settings) -> uuid.UUID:
    """A human visit from the MTNL Mumbai visitor on ``link``, inferred."""
    page = await ch.visit(client, link.slug, headers=ch.BROWSER_HEADERS)
    await client.post(
        f"/api/v1/s/{ch.nonce_from(page)}",
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
    assert await engine.run_once(settings, only=[visit_id]) == 1
    visit = await ch.get_visit(visit_id)
    assert visit.classification.value == "human", "the premise: this visitor is a person"
    assert (visit.strict_country_code, visit.strict_admin1) == ("IN", "Maharashtra")
    return visit_id


async def _rows(visit_id: uuid.UUID) -> list[Outbox]:
    async with session_scope() as db:
        return list(
            (
                await db.execute(
                    select(Outbox)
                    .where(Outbox.payload["visit_id"].astext == str(visit_id))
                    .order_by(Outbox.id)
                )
            ).scalars()
        )


async def _places(link_id: uuid.UUID) -> set[str]:
    async with session_scope() as db:
        rows = await db.execute(
            select(notes.LinkPlace.region_key).where(notes.LinkPlace.link_id == link_id)
        )
        return {r[0] for r in rows}


async def _sql(statement: str, **params: Any) -> None:
    async with session_scope() as db:
        await db.execute(text(statement), params)


# ---------------------------------------------------------------------------
# A new place (F7.AC12, F7.AC14)
# ---------------------------------------------------------------------------


async def test_a_new_place_is_a_line_on_the_visit_alert(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    await _enable(new_place=alerts.NewPlaceType(enabled=True))
    link = await ch.create_link()
    visit_id = await _visit(db_client, link, integration_settings)

    (row,) = await _rows(visit_id)
    assert row.kind is OutboxKind.VISIT_ALERT
    assert row.payload["notes"] == [{"kind": "new_place", "region_key": "IN|Maharashtra"}]
    assert await _places(link.id) == {"IN", "IN|Maharashtra"}


async def test_places_are_recorded_while_off_so_switching_on_announces_no_old_place(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    link = await ch.create_link()
    first = await _visit(db_client, link, integration_settings)
    (row,) = await _rows(first)
    assert "notes" not in row.payload
    assert await _places(link.id) == {"IN", "IN|Maharashtra"}, "recorded while off"

    await _enable(new_place=alerts.NewPlaceType(enabled=True))
    await _sql("DELETE FROM outbox WHERE id = :id", id=row.id)  # so the next visit alerts
    second = await _visit(db_client, link, integration_settings)
    (again,) = await _rows(second)
    assert "notes" not in again.payload, "Maharashtra was already seen on this link"


async def test_a_new_place_after_the_days_alert_is_sent_alone_once(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    await _enable(new_place=alerts.NewPlaceType(enabled=True))
    link = await ch.create_link()
    await _visit(db_client, link, integration_settings)  # takes the day's alert
    # As if the place had never been seen: the next visit is the day's duplicate *and*
    # brings a new place.
    await _sql("DELETE FROM link_places WHERE link_id = :l", l=link.id)

    second = await _visit(db_client, link, integration_settings)
    (row,) = await _rows(second)
    assert row.kind is OutboxKind.NEW_PLACE
    assert row.dedup_key == f"newplace:{link.id}:IN|Maharashtra"
    assert row.priority.value == "normal"
    assert row.payload["note"] == {"kind": "new_place", "region_key": "IN|Maharashtra"}
    assert "notes" not in row.payload


async def test_concurrent_visits_race_to_one_first_place(db_client: AsyncClient) -> None:
    """F7.AC12 under concurrency: two visits placed in Goa, recorded from two transactions
    at once; the primary key admits one first, so only one of them can announce it."""
    link = await ch.create_link()
    ids = []
    for peer in ("198.51.100.201", "198.51.100.202"):
        await ch.visit(db_client, link.slug, headers=ch.BROWSER_HEADERS, peer=peer)
        ids.append((await ch.latest_visit(link.id)).id)
        await _sql(
            "UPDATE visits SET classification = 'human', strict_country_code = 'IN', "
            "strict_admin1 = 'Goa' WHERE id = :id",
            id=ids[-1],
        )

    async def record(visit_id: uuid.UUID) -> list[str]:
        async with session_scope() as db:
            visit = await db.get(Visit, visit_id)
            assert visit is not None
            return await notes.record_places(db, visit)

    first, second = await asyncio.gather(record(ids[0]), record(ids[1]))
    assert sorted([first, second], key=len) == [[], ["IN", "IN|Goa"]]
    assert await _places(link.id) == {"IN", "IN|Goa"}


async def test_a_silent_link_says_nothing_but_still_records_its_places(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    await _enable(
        new_place=alerts.NewPlaceType(enabled=True),
        returning=alerts.ReturningType(enabled=True, after_days=1),
    )
    link = await ch.create_link()
    await _sql(
        'UPDATE links SET notify_policy = notify_policy || \'{"outside": "silent"}\' '
        "WHERE id = :id",
        id=link.id,
    )
    visit_id = await _visit(db_client, link, integration_settings)
    assert await _rows(visit_id) == []
    assert await _places(link.id) == {"IN", "IN|Maharashtra"}


async def test_a_bot_records_no_place_and_says_nothing(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """CLAUDE.md invariant 6: whatever a bot's location, it is not a place a person came from."""
    await _enable(new_place=alerts.NewPlaceType(enabled=True))
    link = await ch.create_link()
    page = await ch.visit(db_client, link.slug, headers=ch.BROWSER_HEADERS)
    del page
    visit_id = (await ch.latest_visit(link.id)).id
    await _sql(
        "UPDATE visits SET classification = 'bot', strict_country_code = 'IN', "
        "strict_admin1 = 'Goa' WHERE id = :id",
        id=visit_id,
    )
    async with session_scope() as db:
        result = await outbox.enqueue_visit_alert(
            db,
            visit_id,
            Evaluation((), None),
            reporting_tz=integration_settings.reporting_tz,
            base_url="https://x",
        )
    assert (result.queued, result.reason) == (False, "not_human")
    assert await _places(link.id) == set()
    assert await _rows(visit_id) == []


# ---------------------------------------------------------------------------
# A returning visitor (F7.AC13, F7.AC14)
# ---------------------------------------------------------------------------


async def test_a_returning_visitor_is_a_line_on_the_visit_alert(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    await _enable(returning=alerts.ReturningType(enabled=True, after_days=7))
    link = await ch.create_link()
    first = await _visit(db_client, link, integration_settings)
    (row,) = await _rows(first)
    assert "notes" not in row.payload, "a first visit is not a return"
    # The first visit was twelve days ago, and its alert long gone.
    await _sql(
        "UPDATE visits SET occurred_at = occurred_at - interval '12 days' WHERE id = :id",
        id=first,
    )
    await _sql("DELETE FROM outbox WHERE id = :id", id=row.id)

    second = await _visit(db_client, link, integration_settings)
    (alert,) = await _rows(second)
    assert alert.kind is OutboxKind.VISIT_ALERT
    assert alert.payload["notes"] == [{"kind": "returning", "days": 12}]


async def test_a_returning_visitor_after_the_days_alert_is_sent_alone_once(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    await _enable(returning=alerts.ReturningType(enabled=True, after_days=7))
    link = await ch.create_link()
    first = await _visit(db_client, link, integration_settings)
    # Today's alert stays (taken by the first visit, keyed by its arrival day); the visit
    # itself is moved twelve days back, so the second visit is both a return and the day's
    # duplicate.
    await _sql(
        "UPDATE visits SET occurred_at = occurred_at - interval '12 days' WHERE id = :id",
        id=first,
    )
    second = await _visit(db_client, link, integration_settings)
    (row,) = await _rows(second)
    visit = await ch.get_visit(second)
    assert visit.visitor_id is not None
    day = visit.occurred_at.astimezone(IST).date().isoformat()
    assert row.kind is OutboxKind.RETURNING
    assert row.dedup_key == f"return:{link.id}:{visit.visitor_id.hex()}:{day}"
    assert row.payload["note"] == {"kind": "returning", "days": 12}


async def test_a_visitor_back_within_the_days_is_not_a_return(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    await _enable(returning=alerts.ReturningType(enabled=True, after_days=7))
    link = await ch.create_link()
    first = await _visit(db_client, link, integration_settings)
    (row,) = await _rows(first)
    await _sql(
        "UPDATE visits SET occurred_at = occurred_at - interval '5 days' WHERE id = :id",
        id=first,
    )
    await _sql("DELETE FROM outbox WHERE id = :id", id=row.id)
    second = await _visit(db_client, link, integration_settings)
    (alert,) = await _rows(second)
    assert "notes" not in alert.payload


# ---------------------------------------------------------------------------
# The digest (F7.AC10)
# ---------------------------------------------------------------------------

DAY = dt.date(2020, 1, 1)  # a day no real visit is on


_peers = iter(range(1, 60_000))


async def _human_visit(
    client: AsyncClient, link: Link, at: dt.datetime, *, state: str, kind: str = "human"
) -> None:
    """A captured visit forced to a classification, a best-guess state and a time. Left
    uninferred, so the conftest removes it after the test. Each from its own documentation
    address (RFC 5737), so a dozen captures never meet the capture rate limit."""
    n = next(_peers)
    peer = f"198.51.{100 + n // 250}.{n % 250 + 1}"
    await ch.visit(client, link.slug, headers=ch.BROWSER_HEADERS, peer=peer)
    visit_id = (await ch.latest_visit(link.id)).id
    await _sql(
        "UPDATE visits SET classification = CAST(:k AS classification), occurred_at = :at, "
        "advisory_country_code = 'IN', advisory_admin1 = :s WHERE id = :id",
        k=kind,
        at=at,
        s=state,
        id=visit_id,
    )


def _ist(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=IST)


async def test_the_digest_summarises_yesterday_exactly_once(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    await _enable(digest=alerts.DigestType(enabled=True, at="09:00"))
    busy, quiet = await ch.create_link(), await ch.create_link()
    for hour in (8, 20):
        await _human_visit(db_client, busy, _ist(DAY, hour), state="Karnataka")
    await _human_visit(db_client, quiet, _ist(DAY, 12), state="Goa")
    await _human_visit(db_client, quiet, _ist(DAY, 13), state="Goa", kind="bot")
    # Just outside the day, either side: not counted.
    await _human_visit(db_client, busy, _ist(DAY, 0) - dt.timedelta(minutes=1), state="Goa")
    await _human_visit(db_client, busy, _ist(DAY + dt.timedelta(days=1), 0), state="Goa")

    morning = _ist(DAY + dt.timedelta(days=1), 9, 5)
    assert await digest.run_once(integration_settings, now=morning)
    assert not await digest.run_once(integration_settings, now=morning + dt.timedelta(hours=1))

    async with session_scope() as db:
        (row,) = (
            await db.execute(select(Outbox).where(Outbox.dedup_key == "digest:2020-01-01"))
        ).scalars()
    assert row.kind is OutboxKind.DIGEST and row.priority.value == "normal"
    payload = row.payload
    assert (payload["visits"], payload["human"]) == (4, 3)
    assert payload["states"] == [
        {"key": "IN|Karnataka", "count": 2},
        {"key": "IN|Goa", "count": 1},
    ], "humans only: the bot's Goa visit is not counted"
    assert [(r["slug"], r["count"]) for r in payload["links"]] == [(busy.slug, 2), (quiet.slug, 1)]
    assert payload["label"] == "Wed 1 Jan"
    assert isinstance(payload["dead"], int)


async def test_the_digest_waits_for_its_time_and_its_switch(
    integration_settings: Settings,
) -> None:
    early = _ist(DAY + dt.timedelta(days=1), 8, 59)
    assert not await digest.run_once(integration_settings, now=early), "off by default"
    await _enable(digest=alerts.DigestType(enabled=True, at="09:00"))
    assert not await digest.run_once(integration_settings, now=early)


async def test_the_worker_sends_a_digest_as_a_digest(
    integration_settings: Settings, sent: list[dict[str, Any]]
) -> None:
    await _enable(digest=alerts.DigestType(enabled=True, at="09:00"))
    assert await digest.run_once(integration_settings, now=_ist(DAY + dt.timedelta(days=1), 9, 5))
    async with session_scope() as db:
        row_id = (
            await db.execute(select(Outbox.id).where(Outbox.dedup_key == "digest:2020-01-01"))
        ).scalar_one()
    assert await worker.run_once(integration_settings, only=[row_id]) == 1
    (message,) = sent
    assert message["chat_id"] == OWNER_CHAT
    assert "Yesterday on Tracelet" in message["text"] and "No visits." in message["text"]


# ---------------------------------------------------------------------------
# The spike (F7.AC11)
# ---------------------------------------------------------------------------


async def _spikes(link_id: uuid.UUID) -> list[Outbox]:
    async with session_scope() as db:
        return list(
            (
                await db.execute(select(Outbox).where(Outbox.dedup_key.like(f"spike:{link_id}:%")))
            ).scalars()
        )


async def test_a_busy_link_raises_one_spike_an_hour(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    await _enable(spike=alerts.SpikeType(enabled=True, floor=3, k=3.0))
    link = await ch.create_link()
    now = dt.datetime.now(dt.UTC)
    for minutes in (5, 20, 50):
        await _human_visit(db_client, link, now - dt.timedelta(minutes=minutes), state="Goa")
    await _human_visit(db_client, link, now - dt.timedelta(minutes=30), state="Goa", kind="bot")

    await spike.run_once(integration_settings, now=now)
    (first,) = await _spikes(link.id)
    assert first.kind is OutboxKind.SPIKE
    assert first.dedup_key == spike.key_for(link.id, now, integration_settings.reporting_tz)
    assert (first.payload["count"], first.payload["usual"]) == (3, 0.0), "humans only"
    assert "location" not in first.payload
    # The same hour again: nothing more.
    await spike.run_once(integration_settings, now=now)
    assert len(await _spikes(link.id)) == 1


async def test_a_link_as_busy_as_usual_is_not_a_spike(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    await _enable(spike=alerts.SpikeType(enabled=True, floor=3, k=3.0))
    link = await ch.create_link()
    now = dt.datetime.now(dt.UTC)
    for minutes in (5, 20, 50):
        await _human_visit(db_client, link, now - dt.timedelta(minutes=minutes), state="Goa")
    # One visit in the same 60 minutes on each of the last seven days: usual is 1, and
    # 3 is not more than 3 x 1.
    for days in range(1, 8):
        when = now - dt.timedelta(days=days, minutes=10)
        await _human_visit(db_client, link, when, state="Goa")
    await spike.run_once(integration_settings, now=now)
    assert await _spikes(link.id) == []


async def test_a_switched_off_spike_checks_nothing(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    link = await ch.create_link()
    now = dt.datetime.now(dt.UTC)
    for minutes in range(1, 30, 2):
        await _human_visit(db_client, link, now - dt.timedelta(minutes=minutes), state="Goa")
    assert await spike.run_once(integration_settings, now=now) == 0


# ---------------------------------------------------------------------------
# The settings (F7.AC15): the owner's, audited, validated
# ---------------------------------------------------------------------------

TYPES = {
    "digest": {"enabled": True, "at": "08:30"},
    "spike": {"enabled": True, "floor": 25, "k": 4.5},
    "new_place": {"enabled": True},
    "returning": {"enabled": False, "after_days": 14},
}


async def test_every_type_is_off_until_an_owner_switches_it_on_and_that_is_audited(
    owner: SignedIn,
) -> None:
    before = (await owner.client.get(SETTINGS)).json()["alert_types"]
    assert before == {
        "digest": {"enabled": False, "at": "09:00"},
        "spike": {"enabled": False, "floor": 10, "k": 3.0},
        "new_place": {"enabled": False},
        "returning": {"enabled": False, "after_days": 7},
    }
    response = await owner.client.patch(
        SETTINGS, json={"alert_types": TYPES}, headers=owner.headers()
    )
    assert response.status_code == 200, response.text
    assert response.json()["alert_types"] == TYPES
    assert (await owner.client.get(SETTINGS)).json()["alert_types"] == TYPES

    details = await ch.audit_details_for_action(audit.Action.SETTINGS_CHANGED)
    assert details[-1] == {"from": before, "to": TYPES}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"alert_types": TYPES | {"digest": {"enabled": True, "at": "9:00"}}},
        {"alert_types": TYPES | {"spike": {"enabled": True, "floor": 0, "k": 3}}},
        {"alert_types": TYPES | {"spike": {"enabled": True, "floor": 10, "k": 1}}},
        {"alert_types": TYPES | {"returning": {"enabled": True, "after_days": 91}}},
        {"alert_types": {k: v for k, v in TYPES.items() if k != "spike"}},
        {"alert_types": TYPES | {"weekly": {"enabled": True}}},
    ],
)
async def test_alert_types_are_validated(owner: SignedIn, body: dict[str, Any]) -> None:
    response = await owner.client.patch(SETTINGS, json=body, headers=owner.headers())
    assert response.status_code == 422, body


async def test_an_analyst_cannot_switch_an_alert_type(
    owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    del owner
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)
    assert (await analyst.client.get(SETTINGS)).status_code == 200
    patched = await analyst.client.patch(
        SETTINGS, json={"alert_types": TYPES}, headers=analyst.headers()
    )
    assert patched.status_code == 403
    async with session_scope() as db:
        assert await notify_settings.alert_types(db) == alerts.AlertTypes()
