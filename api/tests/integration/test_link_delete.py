"""Permanent link deletion, against the real database (F1.AC10 as amended, SPEC section 11
row 25, docs/API.md section 6.2).

A link with visits is still refused a plain delete; with ``with_visits=true`` the link, its
visits and their candidates, and its rollups go in one transaction, geofences lose it (one
scoped to it alone is switched off), and the audit row keeps what was deleted. The preview
says exactly that beforehand and deletes nothing. Every geofence made here is ``itest``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.integration import capture_helpers as ch
from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.analytics import rollup
from tracelet.audit import log as audit
from tracelet.auth.models import AdminRole
from tracelet.config import Settings
from tracelet.db.engine import session_scope

pytestmark = pytest.mark.integration

LINKS = "/api/v1/links"


@pytest.fixture(autouse=True)
async def _geofences_removed(db_app: object) -> AsyncIterator[None]:
    del db_app
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM geofences WHERE name LIKE 'itest %'"))


async def _geofence(name: str, link_ids: list[uuid.UUID]) -> uuid.UUID:
    fence_id = uuid.uuid4()
    async with session_scope() as db:
        await db.execute(
            text(
                "INSERT INTO geofences (id, name, shape_kind, region_keys, is_active, link_ids) "
                "VALUES (:id, :n, 'region', ARRAY['IN|Karnataka'], true, CAST(:l AS uuid[]))"
            ),
            {"id": fence_id, "n": name, "l": [str(i) for i in link_ids]},
        )
    return fence_id


async def _with_history(client: AsyncClient) -> tuple[uuid.UUID, str, list[uuid.UUID]]:
    """A link with three visits, a location candidate, and today's rollups."""
    link = await ch.create_link()
    visits = []
    for _ in range(3):
        await ch.visit(client, link.slug)
        visits.append((await ch.latest_visit(link.id)).id)
    async with session_scope() as db:
        await db.execute(
            text(
                "INSERT INTO visit_candidates (visit_id, source, level, raw_confidence, weight, "
                "effective_weight, accepted, latency_ms) "
                "VALUES (:v, 'geolite2', 'country', 0.5, 1, 1, true, 1)"
            ),
            {"v": visits[0]},
        )
    await rollup.run_live_once()
    return link.id, link.slug, visits


async def _left(link_id: uuid.UUID, visits: list[uuid.UUID]) -> dict[str, int]:
    async with session_scope() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM links WHERE id = :l),
                      (SELECT count(*) FROM visits WHERE id = ANY(:v)),
                      (SELECT count(*) FROM visit_candidates WHERE visit_id = ANY(:v)),
                      (SELECT count(*) FROM rollup_visit_daily WHERE link_id = :l)
                      + (SELECT count(*) FROM rollup_visit_hourly WHERE link_id = :l)
                      + (SELECT count(*) FROM rollup_visit_dim_daily WHERE link_id = :l)
                    """
                ),
                {"l": link_id, "v": visits},
            )
        ).one()
    return dict(
        zip(("link", "visits", "candidates", "rollups"), (int(n) for n in row), strict=True)
    )


async def test_the_preview_says_exactly_what_goes_and_deletes_nothing(
    db_client: AsyncClient, owner: SignedIn
) -> None:
    link_id, slug, visits = await _with_history(db_client)
    other = await ch.create_link()
    shared = await _geofence("itest shared", [link_id, other.id])
    alone = await _geofence("itest alone", [link_id])

    before = await _left(link_id, visits)
    preview = (await owner.client.get(f"{LINKS}/{link_id}/delete-preview")).json()
    assert preview["slug"] == slug
    assert (preview["visits"], preview["visit_candidates"]) == (3, 1)
    assert preview["rollup_rows"] == before["rollups"] > 0
    assert [g["id"] for g in preview["geofences_updated"]] == [str(shared)]
    assert [g["id"] for g in preview["geofences_deactivated"]] == [str(alone)]
    assert await _left(link_id, visits) == before


async def test_a_plain_delete_of_a_link_with_visits_is_still_refused(
    db_client: AsyncClient, owner: SignedIn
) -> None:
    link_id, _, visits = await _with_history(db_client)
    refused = await owner.client.delete(f"{LINKS}/{link_id}", headers=owner.headers())
    assert refused.status_code == 409
    assert refused.json()["code"] == "LINK_HAS_VISITS"
    assert (await _left(link_id, visits))["visits"] == 3


async def test_a_permanent_delete_removes_the_link_its_history_and_its_scope(
    db_client: AsyncClient, owner: SignedIn
) -> None:
    link_id, slug, visits = await _with_history(db_client)
    other = await ch.create_link()
    shared = await _geofence("itest shared", [link_id, other.id])
    alone = await _geofence("itest alone", [link_id])
    preview = (await owner.client.get(f"{LINKS}/{link_id}/delete-preview")).json()

    deleted = await owner.client.delete(
        f"{LINKS}/{link_id}", params={"with_visits": "true"}, headers=owner.headers()
    )
    assert deleted.status_code == 204, deleted.text
    assert await _left(link_id, visits) == {"link": 0, "visits": 0, "candidates": 0, "rollups": 0}
    assert (await owner.client.get(f"{LINKS}/{link_id}")).status_code == 404
    assert (await ch.visit(db_client, slug)).status_code == 404  # the slug is gone

    async with session_scope() as db:
        fences = {
            str(i): (list(map(str, ids)), active)
            for i, ids, active in await db.execute(
                text("SELECT id, link_ids, is_active FROM geofences WHERE id = ANY(:f)"),
                {"f": [shared, alone]},
            )
        }
    assert fences[str(shared)] == ([str(other.id)], True)
    assert fences[str(alone)][1] is False, "scoped to the deleted link alone: switched off"

    (detail,) = await ch.audit_details_for(link_id, audit.Action.LINK_DELETED)
    assert detail["with_visits"] is True
    assert (detail["visits"], detail["visit_candidates"]) == (3, 1)
    assert detail["rollup_rows"] == preview["rollup_rows"]
    assert detail["geofences_deactivated"] == ["itest alone"]


async def test_an_archived_link_can_be_deleted_permanently(
    db_client: AsyncClient, owner: SignedIn
) -> None:
    link_id, _, visits = await _with_history(db_client)
    archived = await owner.client.post(f"{LINKS}/{link_id}/archive", headers=owner.headers())
    assert archived.status_code == 200, archived.text
    listed = (await owner.client.get(LINKS, params={"include_archived": "true"})).json()
    assert str(link_id) in {link["id"] for link in listed}

    deleted = await owner.client.delete(
        f"{LINKS}/{link_id}", params={"with_visits": "true"}, headers=owner.headers()
    )
    assert deleted.status_code == 204, deleted.text
    assert (await _left(link_id, visits))["link"] == 0


async def test_an_empty_link_is_deleted_without_with_visits(owner: SignedIn) -> None:
    link = await ch.create_link()
    deleted = await owner.client.delete(f"{LINKS}/{link.id}", headers=owner.headers())
    assert deleted.status_code == 204


async def test_deleting_is_the_owners(
    db_client: AsyncClient,
    new_client: ClientFactory,
    integration_settings: Settings,
    totp_clock: TotpClock,
) -> None:
    link_id, _, _ = await _with_history(db_client)
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)
    assert (await analyst.client.get(f"{LINKS}/{link_id}/delete-preview")).status_code == 403
    refused = await analyst.client.delete(
        f"{LINKS}/{link_id}", params={"with_visits": "true"}, headers=analyst.headers()
    )
    assert refused.status_code == 403
