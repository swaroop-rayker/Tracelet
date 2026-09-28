"""Visits API, against the real database (docs/API.md section 7, F12.AC4).

The decrypt endpoint is the one read in the system that must leave a trace, so its
tests check the audit row as carefully as the answer. Everything else checks that the
list and detail views never expose what the capture path worked to keep out.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.integration import capture_helpers as ch
from tests.integration import helpers
from tests.integration.helpers import ClientFactory, SignedIn, TotpClock
from tracelet.auth.models import AdminRole
from tracelet.config import Settings
from tracelet.db.engine import session_scope

pytestmark = pytest.mark.integration

VISITS = "/api/v1/visits"


async def _visited(client: AsyncClient, *, ua: str = ch.CHROME_UA, count: int = 1) -> str:
    """A link with ``count`` visits from distinct networks. Returns the link id."""
    link = await ch.create_link()
    for i in range(count):
        await ch.visit(client, link.slug, ua=ua, peer=f"49.207.{i}.34")
    return str(link.id)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def test_the_default_view_excludes_automated_traffic(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """The M2 done-check: an Instagram prefetch is "excluded from default views"."""
    link = await ch.create_link()
    await ch.visit(db_client, link.slug, ua=ch.CHROME_UA)
    await ch.visit(db_client, link.slug, ua=ch.FB_FETCHER_UA, peer="203.0.113.70")

    default = await owner.client.get(VISITS, params={"link_id": str(link.id)})
    everything = await owner.client.get(
        VISITS, params={"link_id": str(link.id), "include_automated": "true"}
    )

    assert [v["classification"] for v in default.json()["items"]] == ["unknown"]
    assert sorted(v["classification"] for v in everything.json()["items"]) == [
        "crawler",
        "unknown",
    ]


async def test_crawlers_have_a_dedicated_view(owner: SignedIn, db_client: AsyncClient) -> None:
    """F2.AC8: excluded by default, visible when asked for."""
    link_id = await _visited(db_client, ua=ch.FB_FETCHER_UA)
    response = await owner.client.get(
        VISITS, params={"link_id": link_id, "classification": "crawler"}
    )
    assert [v["classification"] for v in response.json()["items"]] == ["crawler"]


async def test_the_list_is_newest_first_and_pages_without_repeats(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    link_id = await _visited(db_client, count=5)

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, str] = {"link_id": link_id, "limit": "2"}
        if cursor:
            params["cursor"] = cursor
        page = (await owner.client.get(VISITS, params=params)).json()
        seen.extend(v["id"] for v in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 5
    assert len(set(seen)) == 5, "keyset pagination must never repeat a row"
    stamps = [
        v["occurred_at"]
        for v in (await owner.client.get(VISITS, params={"link_id": link_id})).json()["items"]
    ]
    assert stamps == sorted(stamps, reverse=True)


async def test_a_bad_cursor_is_a_422(owner: SignedIn) -> None:
    response = await owner.client.get(VISITS, params={"cursor": "not-a-cursor"})
    assert response.status_code == 422


async def test_later_milestone_fields_are_null_not_zero(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """F3.AC5. Location (M3) and identity (M4) are unknown in M2 -- null, never a
    fabricated zero or false that a chart would plot as data."""
    link_id = await _visited(db_client)
    item = (await owner.client.get(VISITS, params={"link_id": link_id})).json()["items"][0]

    assert item["location"]["strict"]["country_code"] is None
    assert item["location"]["confidence"]["country"] is None
    assert item["bot_score"] is None
    assert item["visitor_id"] is None
    assert item["is_returning"] is None
    assert item["network"]["is_datacenter"] is None
    assert item["geofence"]["state"] == "undetermined"
    assert item["device"]["class"] == "mobile", "the documented wire name, not class_"


async def test_the_list_needs_a_session(new_client: ClientFactory) -> None:
    client = await new_client()
    assert (await client.get(VISITS)).status_code == 401


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


async def test_the_detail_view_carries_the_raw_record_without_the_address(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """The M2 done-check, API half: "no plaintext IP anywhere in ... API responses"."""
    link = await ch.create_link()
    await ch.visit(
        db_client,
        link.slug,
        peer=ch.VISITOR_IP,
        headers={"X-Forwarded-For": ch.VISITOR_IP, "Accept-Language": "en-IN"},
    )
    visit_id = str((await ch.latest_visit(link.id)).id)

    response = await owner.client.get(f"{VISITS}/{visit_id}")

    assert response.status_code == 200
    body = response.json()
    assert ch.VISITOR_IP not in response.text
    assert body["network"]["ip_prefix"] == ch.VISITOR_PREFIX
    assert body["request"]["headers"]["accept-language"] == "en-IN"
    assert "x-forwarded-for" not in body["request"]["headers"]
    assert body["trace_id"]
    assert body["candidates"] == []
    assert body["classifier_version"] == "m2-crawler-gate.1"


@pytest.mark.parametrize("visit_id", ["not-a-uuid", "01900000-0000-7000-8000-000000000000"])
async def test_an_unknown_visit_is_a_404(owner: SignedIn, visit_id: str) -> None:
    assert (await owner.client.get(f"{VISITS}/{visit_id}")).status_code == 404


# ---------------------------------------------------------------------------
# Decrypt (F12.AC4)
# ---------------------------------------------------------------------------


async def test_an_owner_can_decrypt_and_it_is_audited(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """The M2 done-check: "owner decrypt writes an audit row". The row names the actor
    and the visit, and exists before the answer does."""
    link = await ch.create_link()
    await ch.visit(db_client, link.slug, peer=ch.VISITOR_IP)
    visit = await ch.latest_visit(link.id)

    response = await owner.client.get(f"{VISITS}/{visit.id}/ip")

    assert response.status_code == 200
    assert response.json()["ip"] == ch.VISITOR_IP
    async with session_scope() as db:
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT actor_admin_id FROM audit_log "
                        "WHERE action = 'visit.ip_decrypted' AND target_id = :v"
                    ),
                    {"v": str(visit.id)},
                )
            )
            .scalars()
            .all()
        )
    assert list(rows) == [owner.id]


async def test_an_analyst_cannot_decrypt(
    owner: SignedIn,
    db_client: AsyncClient,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """The M2 done-check: "analyst gets 403"."""
    del owner
    link = await ch.create_link()
    await ch.visit(db_client, link.slug)
    visit = await ch.latest_visit(link.id)
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)

    response = await analyst.client.get(f"{VISITS}/{visit.id}/ip")

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN_ROLE"
    assert ch.VISITOR_IP not in response.text


async def test_a_purged_address_is_a_410_not_a_fault(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """ADR-0007: past its TTL the address is gone, and that is expected."""
    link = await ch.create_link()
    await ch.visit(db_client, link.slug)
    visit = await ch.latest_visit(link.id)
    async with session_scope() as db:
        await db.execute(
            text("UPDATE visits SET ip_enc = NULL, ip_key_version = NULL WHERE id = :id"),
            {"id": visit.id},
        )

    response = await owner.client.get(f"{VISITS}/{visit.id}/ip")

    assert response.status_code == 410
    assert response.json()["code"] == "IP_PURGED"


async def test_decryption_is_rate_limited(owner: SignedIn, db_client: AsyncClient) -> None:
    """10 an hour per admin, with a burst of five (ADR-0007)."""
    link = await ch.create_link()
    await ch.visit(db_client, link.slug)
    visit = await ch.latest_visit(link.id)

    statuses = [(await owner.client.get(f"{VISITS}/{visit.id}/ip")).status_code for _ in range(6)]

    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429


async def test_decryption_is_bound_to_the_row(owner: SignedIn, db_client: AsyncClient) -> None:
    """A ciphertext moved to another visit fails to open: the AAD is the visit id, so
    swapping rows is detected rather than decrypting as the wrong visitor (ADR-0007)."""
    link = await ch.create_link()
    await ch.visit(db_client, link.slug, peer=ch.VISITOR_IP)
    await ch.visit(db_client, link.slug, peer="203.0.113.80")
    async with session_scope() as db:
        source, target = (
            (
                await db.execute(
                    text("SELECT id FROM visits WHERE link_id = :l ORDER BY occurred_at"),
                    {"l": link.id},
                )
            )
            .scalars()
            .all()
        )
        await db.execute(
            text(
                "UPDATE visits SET ip_enc = (SELECT ip_enc FROM visits WHERE id = :src) "
                "WHERE id = :dst"
            ),
            {"src": source, "dst": target},
        )

    response = await owner.client.get(f"{VISITS}/{target}/ip")

    assert response.status_code == 500, "a transplanted ciphertext must not decrypt"
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert ch.VISITOR_IP not in response.text
