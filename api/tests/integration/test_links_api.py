"""Tracking-link management, against the real database (F1, docs/API.md section 6).

Every write is owner-only and audited (CLAUDE.md invariant 9). The default-link
invariant is tested twice: through the API, and by going around it with SQL, because
"at most one default" is the engine's job and must hold for a code path that forgets.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration import capture_helpers as ch
from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.auth.models import AdminRole
from tracelet.config import Settings
from tracelet.db.engine import session_scope

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("public_dns")]

LINKS = "/api/v1/links"


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "slug": ch.new_slug(),
        "label": "Instagram bio",
        "destination_url": "https://example.com/landing",
    }
    body.update(overrides)
    return body


async def _create(owner: SignedIn, **overrides: object) -> dict[str, object]:
    response = await owner.client.post(LINKS, json=_body(**overrides), headers=owner.headers())
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _defaults() -> list[str]:
    async with session_scope() as db:
        rows = await db.execute(
            text("SELECT slug FROM links WHERE is_default AND archived_at IS NULL")
        )
        return list(rows.scalars())


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_an_owner_creates_a_link(owner: SignedIn) -> None:
    link = await _create(owner, slug="t-ig-bio", interstitial_ms=900)

    assert link["slug"] == "t-ig-bio"
    assert link["interstitial_ms"] == 900
    assert link["capture_url"] == "https://localhost/r/t-ig-bio"
    assert link["notify_policy"] == {
        "inside": "high",
        "outside": "normal",
        "undetermined": "normal",
        "automated": "silent",
    }
    assert link["visit_count"] == 0


async def test_the_first_live_link_becomes_the_default(owner: SignedIn) -> None:
    """F1.AC3: exactly one default whenever any live link exists."""
    if await _defaults():
        pytest.skip("a non-test default link already exists in this database")
    first = await _create(owner)
    second = await _create(owner)

    assert first["is_default"] is True
    assert second["is_default"] is False


async def test_a_new_link_is_immediately_capturable(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    link = await _create(owner)
    response = await ch.visit(db_client, str(link["slug"]))
    assert response.status_code == 200


async def test_a_slug_is_normalised_to_lower_case(owner: SignedIn) -> None:
    link = await _create(owner, slug="T-UPPER-CASE")
    assert link["slug"] == "t-upper-case"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"slug": "ab"}, "slug"),
        ({"slug": "has space"}, "slug"),
        ({"slug": "a" * 33}, "slug"),
        ({"destination_url": "http://example.com/"}, "destination_url"),
        ({"destination_url": "https://user:pw@example.com/"}, "destination_url"),
        ({"interstitial_ms": 299}, "interstitial_ms"),
        ({"interstitial_ms": 1501}, "interstitial_ms"),
        ({"label": ""}, "label"),
        ({"notify_policy": {"inside": "loud"}}, "notify_policy"),
        # Automated traffic never notifies (CLAUDE.md invariant 6, SPEC s11 row 18).
        ({"notify_policy": {"automated": "high"}}, "notify_policy"),
        ({"notify_policy": {"undetermined": "loud"}}, "notify_policy"),
    ],
)
async def test_invalid_input_is_a_field_level_422(
    owner: SignedIn, overrides: dict[str, object], field: str
) -> None:
    response = await owner.client.post(LINKS, json=_body(**overrides), headers=owner.headers())
    assert response.status_code == 422
    assert any(field in error["field"] for error in response.json()["errors"])


async def test_a_destination_resolving_to_a_private_address_is_refused(
    owner: SignedIn, public_dns: dict[str, list[str]]
) -> None:
    """F1.AC2: visitors must never be sent to an internal host by mistake."""
    public_dns["intranet.example"] = ["10.0.0.5"]
    response = await owner.client.post(
        LINKS,
        json=_body(destination_url="https://intranet.example/"),
        headers=owner.headers(),
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "NOT_PUBLIC"


async def test_a_duplicate_slug_is_refused_in_any_case(owner: SignedIn) -> None:
    await _create(owner, slug="t-taken")
    response = await owner.client.post(LINKS, json=_body(slug="T-TAKEN"), headers=owner.headers())
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "ALREADY_EXISTS"


async def test_the_engine_refuses_a_slug_the_application_did_not_normalise(
    db_app: object,
) -> None:
    """citext's own ``~`` is case-insensitive; the CHECK casts to text so it is not."""
    del db_app
    with pytest.raises(IntegrityError, match="ck_links_slug_format"):
        async with session_scope() as db:
            await db.execute(
                text(
                    "INSERT INTO links (id, slug, label, destination_url) "
                    "VALUES (gen_random_uuid(), 't-UPPER', 'x', 'https://example.com/')"
                )
            )


async def test_the_engine_refuses_a_non_https_destination(db_app: object) -> None:
    del db_app
    with pytest.raises(IntegrityError, match="ck_links_destination_https"):
        async with session_scope() as db:
            await db.execute(
                text(
                    "INSERT INTO links (id, slug, label, destination_url) "
                    "VALUES (gen_random_uuid(), 't-plain-http', 'x', 'http://example.com/')"
                )
            )


async def test_the_engine_refuses_an_automated_policy_that_notifies(db_app: object) -> None:
    """Invariant 6 below the API: a link written around the application still cannot
    ask for alerts on automated traffic."""
    del db_app
    policy = (
        '{"inside": "high", "outside": "normal", "undetermined": "normal", "automated": "high"}'
    )
    with pytest.raises(IntegrityError, match="ck_links_automated_silent"):
        async with session_scope() as db:
            await db.execute(
                text(
                    "INSERT INTO links (id, slug, label, destination_url, notify_policy) "
                    "VALUES (gen_random_uuid(), 't-loud-bots', 'x', 'https://example.com/', "
                    "CAST(:policy AS jsonb))"
                ),
                {"policy": policy},
            )


# ---------------------------------------------------------------------------
# Roles (CLAUDE.md invariant 9)
# ---------------------------------------------------------------------------


async def test_an_analyst_can_read_but_not_write(
    owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    link = await _create(owner)
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)

    assert (await analyst.client.get(LINKS)).status_code == 200
    assert (await analyst.client.get(f"{LINKS}/{link['id']}")).status_code == 200
    created = await analyst.client.post(LINKS, json=_body(), headers=analyst.headers())
    patched = await analyst.client.patch(
        f"{LINKS}/{link['id']}", json={"label": "x"}, headers=analyst.headers()
    )
    deleted = await analyst.client.delete(f"{LINKS}/{link['id']}", headers=analyst.headers())

    assert created.status_code == patched.status_code == deleted.status_code == 403


async def test_links_are_not_public(new_client: ClientFactory) -> None:
    client = await new_client()
    assert (await client.get(LINKS)).status_code == 401


# ---------------------------------------------------------------------------
# Update (F1.AC8)
# ---------------------------------------------------------------------------


async def test_a_destination_change_takes_effect_on_the_next_request(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """F1.AC8: no restart. The capture path reads the row on every request."""
    link = await _create(owner)
    await ch.visit(db_client, str(link["slug"]))  # also primes the link cache

    patched = await owner.client.patch(
        f"{LINKS}/{link['id']}",
        json={"destination_url": "https://example.org/new"},
        headers=owner.headers(),
    )
    assert patched.status_code == 200

    page = await ch.visit(db_client, str(link["slug"]), peer="203.0.113.60")
    assert '"dest": "https://example.org/new"' in page.text


async def test_a_destination_change_is_audited_with_old_and_new(owner: SignedIn) -> None:
    link = await _create(owner)
    await owner.client.patch(
        f"{LINKS}/{link['id']}",
        json={"destination_url": "https://example.org/new"},
        headers=owner.headers(),
    )

    details = await ch.audit_details_for(uuid.UUID(str(link["id"])), "link.updated")
    assert details[-1]["destination_url"] == {
        "from": "https://example.com/landing",
        "to": "https://example.org/new",
    }


async def test_a_slug_change_retires_the_old_slug(owner: SignedIn, db_client: AsyncClient) -> None:
    link = await _create(owner)
    old = str(link["slug"])
    new = ch.new_slug()

    await owner.client.patch(f"{LINKS}/{link['id']}", json={"slug": new}, headers=owner.headers())

    assert (await ch.visit(db_client, old)).status_code == 404
    assert (await ch.visit(db_client, new, peer="203.0.113.61")).status_code == 200


async def test_deactivating_a_link_stops_capture(owner: SignedIn, db_client: AsyncClient) -> None:
    """F1.AC4: an inactive slug is a 404 and is never redirected."""
    link = await _create(owner)
    await owner.client.patch(
        f"{LINKS}/{link['id']}", json={"is_active": False}, headers=owner.headers()
    )
    assert (await ch.visit(db_client, str(link["slug"]))).status_code == 404


# ---------------------------------------------------------------------------
# Clone (F1.AC9)
# ---------------------------------------------------------------------------


async def test_a_clone_keeps_the_configuration_and_leaves_the_visits_behind(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    link = await _create(owner, interstitial_ms=1200)
    await ch.visit(db_client, str(link["slug"]))

    response = await owner.client.post(
        f"{LINKS}/{link['id']}/clone", json={"slug": ch.new_slug()}, headers=owner.headers()
    )

    assert response.status_code == 201
    clone = response.json()
    assert clone["cloned_from"] == link["id"]
    assert clone["interstitial_ms"] == 1200
    assert clone["destination_url"] == link["destination_url"]
    assert clone["is_default"] is False
    assert clone["visit_count"] == 0
    original = (await owner.client.get(f"{LINKS}/{link['id']}")).json()
    assert original["visit_count"] == 1, "history stays with the burned slug"


# ---------------------------------------------------------------------------
# Default (F1.AC3)
# ---------------------------------------------------------------------------


async def test_asking_for_location_is_off_by_default_audited_and_cloned(owner: SignedIn) -> None:
    """F1.AC11, ADR-0021."""
    link = await _create(owner)
    assert link["ask_location"] is False

    response = await owner.client.patch(
        f"{LINKS}/{link['id']}", json={"ask_location": True}, headers=owner.headers()
    )
    assert response.status_code == 200, response.text
    assert response.json()["ask_location"] is True
    (detail,) = await ch.audit_details_for(uuid.UUID(str(link["id"])), "link.updated")
    assert detail["ask_location"] == {"from": False, "to": True}

    clone = await owner.client.post(
        f"{LINKS}/{link['id']}/clone", json={"slug": ch.new_slug()}, headers=owner.headers()
    )
    assert clone.status_code == 201, clone.text
    assert clone.json()["ask_location"] is True


async def test_making_a_link_default_clears_the_previous_one(owner: SignedIn) -> None:
    await _create(owner)
    second = await _create(owner)

    response = await owner.client.post(f"{LINKS}/{second['id']}/default", headers=owner.headers())

    assert response.status_code == 200
    assert response.json()["is_default"] is True
    assert await _defaults() == [second["slug"]]


async def test_the_engine_refuses_a_second_default(db_app: object) -> None:
    """ "At most one" is the partial unique index's job, not the application's."""
    del db_app
    with pytest.raises(IntegrityError, match="uq_links_one_default"):
        async with session_scope() as db:
            for slug in ("t-default-a", "t-default-b"):
                await db.execute(
                    text(
                        "INSERT INTO links (id, slug, label, destination_url, is_default) "
                        "VALUES (gen_random_uuid(), :s, 'x', 'https://example.com/', true)"
                    ),
                    {"s": slug},
                )


async def test_the_default_cannot_be_archived_while_another_could_replace_it(
    owner: SignedIn,
) -> None:
    if await _defaults():
        pytest.skip("a non-test default link already exists in this database")
    first = await _create(owner)
    await _create(owner)

    response = await owner.client.post(f"{LINKS}/{first['id']}/archive", headers=owner.headers())

    assert response.status_code == 409
    assert response.json()["code"] == "DEFAULT_LINK_REQUIRED"


# ---------------------------------------------------------------------------
# Archive and delete (F1.AC4, F1.AC10)
# ---------------------------------------------------------------------------


async def test_an_archived_link_is_a_404_and_keeps_its_visits(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    await _create(owner)  # so the one archived below is not the only live link
    link = await _create(owner)
    await ch.visit(db_client, str(link["slug"]))

    response = await owner.client.post(f"{LINKS}/{link['id']}/archive", headers=owner.headers())

    assert response.status_code == 200
    assert response.json()["archived_at"] is not None
    assert (await ch.visit(db_client, str(link["slug"]), peer="203.0.113.62")).status_code == 404
    assert response.json()["visit_count"] == 1


async def test_a_link_with_visits_cannot_be_deleted(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """F1.AC10: historical data is never orphaned. Archive instead."""
    await _create(owner)
    link = await _create(owner)
    await ch.visit(db_client, str(link["slug"]))

    response = await owner.client.delete(f"{LINKS}/{link['id']}", headers=owner.headers())

    assert response.status_code == 409
    assert response.json()["code"] == "LINK_HAS_VISITS"


async def test_the_engine_refuses_to_orphan_visits(db_client: AsyncClient) -> None:
    """``ON DELETE RESTRICT`` is the backstop the API check sits in front of."""
    link = await ch.create_link()
    await ch.visit(db_client, link.slug)
    with pytest.raises(IntegrityError, match="fk_visits_link"):
        async with session_scope() as db:
            await db.execute(text("DELETE FROM links WHERE id = :id"), {"id": link.id})


async def test_a_link_without_visits_can_be_deleted(owner: SignedIn) -> None:
    await _create(owner)
    link = await _create(owner)
    response = await owner.client.delete(f"{LINKS}/{link['id']}", headers=owner.headers())
    assert response.status_code == 204
    assert (await owner.client.get(f"{LINKS}/{link['id']}")).status_code == 404


async def test_every_write_is_audited(owner: SignedIn) -> None:
    """CLAUDE.md invariant 9."""
    await _create(owner)
    link = await _create(owner)
    link_id = uuid.UUID(str(link["id"]))
    await owner.client.patch(
        f"{LINKS}/{link_id}", json={"label": "Renamed"}, headers=owner.headers()
    )
    clone = await owner.client.post(
        f"{LINKS}/{link_id}/clone", json={"slug": ch.new_slug()}, headers=owner.headers()
    )
    await owner.client.post(f"{LINKS}/{link_id}/default", headers=owner.headers())
    await owner.client.post(f"{LINKS}/{clone.json()['id']}/archive", headers=owner.headers())

    for action in ("link.created", "link.updated", "link.default_changed"):
        assert await ch.audit_details_for(link_id, action), f"no {action} row"
    clone_id = uuid.UUID(clone.json()["id"])
    assert await ch.audit_details_for(clone_id, "link.cloned")
    assert await ch.audit_details_for(clone_id, "link.archived")
