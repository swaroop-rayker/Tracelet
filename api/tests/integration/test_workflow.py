"""Annotations and saved views against the real database (F9.AC25, F9.AC26, SPEC section 11
row 29, docs/API.md section 10a).

The role rules are the point: these are the only two places an analyst writes, and only
their own. An owner deletes anyone's note (audited) but never sees another admin's views.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from sqlalchemy import text

from tests.integration import capture_helpers as ch
from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.audit import log as audit
from tracelet.auth.models import AdminRole
from tracelet.config import Settings
from tracelet.db.engine import session_scope

pytestmark = pytest.mark.integration

NOTES = "/api/v1/annotations"
VIEWS = "/api/v1/saved-views"


@pytest.fixture(autouse=True)
async def _no_test_notes_left(db_app: object) -> Any:
    """Notes outlive their author (SET NULL); remove the ones this test wrote."""
    del db_app
    async with session_scope() as db:
        started = (await db.execute(text("SELECT now()"))).scalar_one()
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM annotations WHERE created_at >= :t"), {"t": started})


@pytest.fixture
async def analyst(
    owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> SignedIn:
    del owner
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    return await helpers.enroll(await new_client(), invited, totp_clock)


def _at(hours_ago: int = 1) -> str:
    return (dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours_ago)).isoformat()


async def _add(who: SignedIn, **body: Any) -> dict[str, Any]:
    r = await who.client.post(
        NOTES, json={"at": _at(), "text": "Posted the reel", **body}, headers=who.headers()
    )
    assert r.status_code == 201, r.text
    created: dict[str, Any] = r.json()
    return created


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------


async def test_any_admin_adds_a_note_and_only_the_author_changes_it(
    owner: SignedIn, analyst: SignedIn
) -> None:
    note = await _add(analyst, text="  Diwali campaign starts  ")
    assert note["text"] == "Diwali campaign starts", "trimmed"
    assert note["mine"] is True and note["author"]["name"]

    listed = (await owner.client.get(NOTES)).json()["items"]
    seen = next(n for n in listed if n["id"] == note["id"])
    assert seen["mine"] is False, "the owner sees it, as someone else's"

    edited = await analyst.client.patch(
        f"{NOTES}/{note['id']}", json={"text": "Diwali campaign"}, headers=analyst.headers()
    )
    assert edited.status_code == 200 and edited.json()["text"] == "Diwali campaign"
    by_owner = await owner.client.patch(
        f"{NOTES}/{note['id']}", json={"text": "x"}, headers=owner.headers()
    )
    assert by_owner.status_code == 403 and by_owner.json()["code"] == "NOT_AUTHOR"


async def test_an_analyst_cannot_delete_another_admins_note_but_an_owner_can_audited(
    owner: SignedIn, analyst: SignedIn
) -> None:
    note = await _add(owner, text="Owner's note")
    refused = await analyst.client.delete(f"{NOTES}/{note['id']}", headers=analyst.headers())
    assert refused.status_code == 403 and refused.json()["code"] == "NOT_AUTHOR"

    theirs = await _add(analyst, text="Analyst's note")
    assert (
        await owner.client.delete(f"{NOTES}/{theirs['id']}", headers=owner.headers())
    ).status_code == 204
    details = await ch.audit_details_for_action(audit.Action.ANNOTATION_DELETED)
    assert details[-1]["text"] == "Analyst's note"

    own = await _add(analyst, text="Mine to delete")
    assert (
        await analyst.client.delete(f"{NOTES}/{own['id']}", headers=analyst.headers())
    ).status_code == 204


async def test_notes_are_listed_by_window_and_link(owner: SignedIn) -> None:
    link, other = await ch.create_link(), await ch.create_link()
    everywhere = await _add(owner, text="For every link")
    mine = await _add(owner, text="For this link", link_id=str(link.id))
    elsewhere = await _add(owner, text="For another link", link_id=str(other.id))
    old = await _add(owner, text="Long ago", at=_at(24 * 60))

    ids = {
        n["id"]
        for n in (await owner.client.get(NOTES, params={"link_id": str(link.id)})).json()["items"]
    }
    assert {everywhere["id"], mine["id"]} <= ids
    assert elsewhere["id"] not in ids and old["id"] not in ids
    assert mine["link_label"] == "Integration link"


@pytest.mark.parametrize(
    "body",
    [
        {"at": "2026-10-01T10:00:00+05:30", "text": "   "},
        {"at": "2026-10-01T10:00:00+05:30", "text": "x" * 201},
        {"at": "2026-10-01T10:00:00+05:30", "text": "ok", "visitor": "no"},
        {"text": "no time"},
    ],
)
async def test_a_note_is_validated(owner: SignedIn, body: dict[str, Any]) -> None:
    r = await owner.client.post(NOTES, json=body, headers=owner.headers())
    assert r.status_code == 422, body


async def test_a_note_for_an_unknown_link_is_a_404(owner: SignedIn) -> None:
    r = await owner.client.post(
        NOTES,
        json={"at": _at(), "text": "x", "link_id": "01a11b98-38e0-7b55-9275-000000000000"},
        headers=owner.headers(),
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------


async def test_saved_views_are_private_to_their_admin(owner: SignedIn, analyst: SignedIn) -> None:
    saved = await analyst.client.post(
        VIEWS,
        json={
            "name": "Karnataka, mobile",
            "path": "/sources",
            "query": "range=30d&admin1=Karnataka",
        },
        headers=analyst.headers(),
    )
    assert saved.status_code == 201, saved.text
    view = saved.json()
    assert [v["id"] for v in (await analyst.client.get(VIEWS)).json()] == [view["id"]]
    assert view["id"] not in [v["id"] for v in (await owner.client.get(VIEWS)).json()]
    # Another admin's view does not exist for the caller -- the owner included.
    stranger = await owner.client.delete(f"{VIEWS}/{view['id']}", headers=owner.headers())
    assert stranger.status_code == 404

    renamed = await analyst.client.patch(
        f"{VIEWS}/{view['id']}", json={"name": "Karnataka"}, headers=analyst.headers()
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "Karnataka"
    gone = await analyst.client.delete(f"{VIEWS}/{view['id']}", headers=analyst.headers())
    assert gone.status_code == 204


async def test_a_view_name_is_unique_per_admin_and_fifty_is_the_most(owner: SignedIn) -> None:
    body = {"name": "Default", "path": "/", "query": ""}
    assert (await owner.client.post(VIEWS, json=body, headers=owner.headers())).status_code == 201
    again = await owner.client.post(VIEWS, json=body, headers=owner.headers())
    assert again.status_code == 409 and again.json()["code"] == "SAVED_VIEW_EXISTS"
    for i in range(49):
        r = await owner.client.post(
            VIEWS, json={**body, "name": f"View {i}"}, headers=owner.headers()
        )
        assert r.status_code == 201
    full = await owner.client.post(
        VIEWS, json={**body, "name": "One too many"}, headers=owner.headers()
    )
    assert full.status_code == 409 and full.json()["code"] == "SAVED_VIEW_LIMIT"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "path": "https://evil.example/", "query": ""},
        {"name": "x", "path": "/settings/team", "query": ""},
        {"name": "x", "path": "/links/UPPER", "query": ""},
        {"name": "x", "path": "/", "query": "?range=7d"},
        {"name": "x", "path": "/", "query": "a" * 2001},
        {"name": "", "path": "/", "query": ""},
    ],
)
async def test_a_view_opens_only_a_dashboard_page(owner: SignedIn, body: dict[str, Any]) -> None:
    r = await owner.client.post(VIEWS, json=body, headers=owner.headers())
    assert r.status_code == 422, body
