"""Fixtures for the integration suite.

Two of these exist because the suite shares a database with the developer's own
dev stack, and neither is incidental:

``_purge_test_admins`` deletes the accounts a test created. Every address the suite
generates is under the reserved ``example.test`` domain, which is what makes a
blanket delete safe. An active owner is left alone when it is the only one, because
the engine refuses to remove the last active owner -- so in CI, where the suite is
the only source of admins, a few rows survive into a database that is discarded
anyway.

``exclusive_owner`` temporarily disables any *other* active owner, because
"demoting the last owner is refused" is a claim about the whole table and cannot be
made while the developer's real owner is sitting in it. It restores them in a
``finally``. If a run is killed between the two, a real owner is left ``disabled``
and one command fixes it:

    docker compose exec db psql -U tracelet -d tracelet \\
      -c "UPDATE admins SET status='active' WHERE email='you@example.com'"
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text, update

from tests.conftest import BASE_URL, SITE_ADDRESS
from tests.integration import helpers
from tracelet.auth.models import Admin, AdminRole, AdminStatus
from tracelet.capture import links
from tracelet.capture.service import link_cache
from tracelet.config import Settings
from tracelet.db.engine import session_scope

# Every address the suite generates lives here. Reserved by RFC 2606, so it cannot
# collide with a real deployment's admin addresses.
TEST_DOMAIN_LIKE = "%@example.test"


@pytest.fixture
def totp_clock() -> helpers.TotpClock:
    """One clock per test, so spent steps are tracked across every code it needs."""
    return helpers.TotpClock()


@pytest.fixture(autouse=True)
async def _clear_rate_limits(db_app: object) -> AsyncIterator[None]:
    """Start every test with an empty limiter.

    Five sign-in attempts per identifier and twenty per prefix per hour is generous
    for a human and nothing for a suite. Without this, tests further down the file
    measure the limiter instead of the auth path -- and fail in an order-dependent
    way that is miserable to diagnose. ``test_rate_limiting`` trips it on purpose.
    """
    del db_app  # ordering only: the engine must exist before any statement runs
    await helpers.clear_rate_limits()
    yield
    await helpers.clear_rate_limits()


@pytest.fixture(autouse=True)
async def _leave_nothing_to_alert_on(db_app: object) -> AsyncIterator[None]:
    """The suite shares the dev database, whose live API delivers real Telegram alerts
    (ERRORS E56). After each test: delete the outbox rows it queued, and the visits it left
    uninferred on links it created -- the live inference job would otherwise infer them
    and alert on them. The API is paused during the suite, so nothing else writes meanwhile.
    """
    del db_app
    async with session_scope() as db:
        high_water = (
            await db.execute(text("SELECT coalesce(max(id), 0) FROM outbox"))
        ).scalar_one()
        started = (await db.execute(text("SELECT now()"))).scalar_one()
    yield
    async with session_scope() as db:
        await db.execute(text("DELETE FROM outbox WHERE id > :h"), {"h": high_water})
        await db.execute(
            text(
                "DELETE FROM visits WHERE inferred_at IS NULL AND stage <> 'rate_limited' "
                "AND link_id IN (SELECT id FROM links WHERE created_at >= :t)"
            ),
            {"t": started},
        )


@pytest.fixture(autouse=True)
async def _purge_test_admins(db_app: object) -> AsyncIterator[None]:
    """Delete the accounts a test created, afterwards."""
    del db_app
    yield
    async with session_scope() as db:
        other_active_owners = int(
            (
                await db.execute(
                    text(
                        "SELECT count(*) FROM admins "
                        "WHERE role = 'owner' AND status = 'active' "
                        "AND email NOT LIKE :pattern"
                    ),
                    {"pattern": TEST_DOMAIN_LIKE},
                )
            ).scalar_one()
        )
        if other_active_owners:
            await db.execute(
                text("DELETE FROM admins WHERE email LIKE :pattern"),
                {"pattern": TEST_DOMAIN_LIKE},
            )
        else:
            # The engine refuses to remove the last active owner, and it is right to.
            await db.execute(
                text(
                    "DELETE FROM admins WHERE email LIKE :pattern "
                    "AND NOT (role = 'owner' AND status = 'active')"
                ),
                {"pattern": TEST_DOMAIN_LIKE},
            )


@pytest.fixture
async def new_client(db_app: object) -> AsyncIterator[helpers.ClientFactory]:
    """A factory for additional clients, each with its own cookie jar.

    Needed wherever two admins, or two sessions of one admin, must be live at the
    same time -- a shared jar would silently overwrite one session with the other.
    """
    async with AsyncExitStack() as stack:

        async def make() -> AsyncClient:
            transport = ASGITransport(app=db_app)  # type: ignore[arg-type]  # see tests/conftest.py
            return await stack.enter_async_context(
                AsyncClient(transport=transport, base_url=BASE_URL)
            )

        yield make


@pytest.fixture
async def owner(
    db_client: AsyncClient, integration_settings: Settings, totp_clock: helpers.TotpClock
) -> helpers.SignedIn:
    """An active owner, signed in on ``db_client``.

    Created through the real enrolment path rather than by writing an active row:
    the CHECK constraint forbids an active account without TOTP enrolled, so there
    is no shortcut -- and the bootstrap-to-active path is exactly what E12 broke.
    """
    invited = await helpers.invite(integration_settings, role=AdminRole.OWNER)
    return await helpers.enroll(db_client, invited, totp_clock)


@pytest.fixture
async def exclusive_owner(owner: helpers.SignedIn) -> AsyncIterator[helpers.SignedIn]:
    """An owner that is the **only** active owner in the database.

    Required by every last-owner assertion: the rule is about the table as a whole,
    so a developer's real owner has to step aside for the duration. Disabling it is
    legal precisely because the test owner is already active, so an active owner
    exists throughout and the constraint trigger never fires.
    """
    async with session_scope() as db:
        others = list(
            (
                await db.execute(
                    select(Admin.id).where(
                        Admin.role == AdminRole.OWNER,
                        Admin.status == AdminStatus.ACTIVE,
                        Admin.id != owner.id,
                    )
                )
            ).scalars()
        )
        if others:
            await db.execute(
                update(Admin).where(Admin.id.in_(others)).values(status=AdminStatus.DISABLED)
            )
    try:
        yield owner
    finally:
        if others:
            async with session_scope() as db:
                await db.execute(
                    update(Admin).where(Admin.id.in_(others)).values(status=AdminStatus.ACTIVE)
                )


@pytest.fixture
def site_address() -> str:
    return SITE_ADDRESS


# ---------------------------------------------------------------------------
# Capture path (M2)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _restore_default_link(db_app: object) -> AsyncIterator[None]:
    """Give a developer's real default link back after a test moves the default.

    A test that makes a ``t-`` link the default clears the real one, and the teardown
    then deletes the ``t-`` link -- leaving a live table with no default at all, which
    F1.AC3 forbids. Requested by ``_purge_test_links`` so it is torn down after it.
    """
    del db_app
    async with session_scope() as db:
        original = (
            await db.execute(
                text(
                    "SELECT id FROM links WHERE is_default AND archived_at IS NULL "
                    "AND slug NOT LIKE 't-%'"
                )
            )
        ).scalar_one_or_none()
    yield
    if original is None:
        return
    async with session_scope() as db:
        await db.execute(
            text(
                "UPDATE links SET is_default = true WHERE id = :id AND archived_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM links WHERE is_default AND archived_at IS NULL)"
            ),
            {"id": original},
        )


@pytest.fixture(autouse=True)
async def _purge_test_links(db_app: object, _restore_default_link: None) -> AsyncIterator[None]:
    """Delete the links a test created, and their visits, afterwards.

    Visits reference links with ON DELETE RESTRICT, so visits go first -- the same
    order the engine insists on. Every link the suite creates has a ``t-`` slug.
    """
    del db_app, _restore_default_link
    yield
    async with session_scope() as db:
        await db.execute(
            text("DELETE FROM visits WHERE link_id IN (SELECT id FROM links WHERE slug LIKE 't-%')")
        )
        await db.execute(text("DELETE FROM links WHERE slug LIKE 't-%'"))


@pytest.fixture(autouse=True)
def _empty_link_cache() -> Iterator[None]:
    """The link cache is process-wide. A link a previous test deleted must not be
    served from it -- that would test the cache, not the code under test."""
    link_cache.entries.clear()
    link_cache.forget_default()
    yield
    link_cache.entries.clear()
    link_cache.forget_default()


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Destination hosts resolve to a public address unless a test says otherwise.

    Link creation resolves the destination (F1.AC2). A test that depended on external
    DNS would fail for reasons unrelated to the code under test.
    """
    table: dict[str, list[str]] = {}

    async def fake(host: str) -> list[str]:
        return table.get(host, ["93.184.216.34"])

    monkeypatch.setattr(links, "resolve_host", fake)
    return table
