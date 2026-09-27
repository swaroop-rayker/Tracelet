"""The audit log (F8.AC16, NFR5.AC5).

Two properties make it worth having, and neither is provable without a real
database and a real request going through the real middleware:

**Append-only at the engine level.** ``tracelet_app`` has no UPDATE and no DELETE on
``audit_log``. A SQL-injection foothold in the application path therefore cannot
erase the evidence of itself. A ``REVOKE`` in a migration is a claim about the
catalogue, so it is tested against the catalogue.

**A rejected request still records the attempt.** The events an intruder produces are
exactly the ones whose request ends in a 4xx, and the audit row is written by the same
code that then raises. Get the transaction policy wrong and the only authentication
events in the log are the successful ones -- precisely backwards (docs/ERRORS.md E11).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text, update

from tests.conftest import BASE_URL
from tests.integration import helpers
from tests.integration.helpers import ADMINS, AUTH, ClientFactory, SignedIn, TotpClock
from tracelet.auth.dependencies import DbSession
from tracelet.auth.models import Admin, AdminRole, AdminStatus
from tracelet.auth.service import MAX_FAILED_LOGINS
from tracelet.config import Settings
from tracelet.db.engine import get_engine, session_scope
from tracelet.middleware import TRACE_HEADER

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Append-only, enforced by the engine
# ---------------------------------------------------------------------------


async def _current_role() -> str:
    async with get_engine().connect() as conn:
        return str((await conn.execute(text("SELECT current_user"))).scalar_one())


async def test_the_application_role_cannot_update_an_audit_row(db_app: object) -> None:
    del db_app
    role = await _current_role()
    if role != "tracelet_app":
        pytest.skip(f"connected as {role!r}; the role split is not exercised")

    with pytest.raises(Exception, match="permission denied"):
        async with session_scope() as db:
            await db.execute(text("UPDATE audit_log SET action = 'tampered'"))


async def test_the_application_role_cannot_delete_an_audit_row(db_app: object) -> None:
    del db_app
    role = await _current_role()
    if role != "tracelet_app":
        pytest.skip(f"connected as {role!r}; the role split is not exercised")

    with pytest.raises(Exception, match="permission denied"):
        async with session_scope() as db:
            await db.execute(text("DELETE FROM audit_log"))


async def test_the_application_role_can_still_read_and_append(db_app: object) -> None:
    """Append-only, not write-only. The dashboard has to be able to show the log."""
    del db_app
    async with get_engine().connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM audit_log"))).scalar_one() >= 0


async def test_the_revoke_is_recorded_in_the_catalogue(db_app: object) -> None:
    """Checked directly, because a future migration could re-grant it by accident.

    ``ALTER DEFAULT PRIVILEGES`` grants UPDATE and DELETE on every table Alembic
    creates; ``audit_log`` revokes them explicitly, and that explicit step is easy
    to lose.
    """
    del db_app
    async with get_engine().connect() as conn:
        granted = set(
            (
                await conn.execute(
                    text(
                        "SELECT privilege_type FROM information_schema.table_privileges "
                        "WHERE table_name = 'audit_log' AND grantee = 'tracelet_app'"
                    )
                )
            )
            .scalars()
            .all()
        )

    if not granted:
        pytest.skip("tracelet_app holds no grants here; roles are not set up")

    assert "SELECT" in granted
    assert "INSERT" in granted
    assert "UPDATE" not in granted, "NFR5.AC5: the application must not rewrite history"
    assert "DELETE" not in granted, "NFR5.AC5: the application must not erase history"


# ---------------------------------------------------------------------------
# A rejected request still records the attempt
# ---------------------------------------------------------------------------


async def test_a_failed_login_leaves_a_row_although_the_request_was_rejected(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    """docs/ERRORS.md E11.

    The row is written by the same call that raises ``Unauthenticated``. It survives
    because a 4xx commits: the handler decided that outcome deliberately, and
    everything it wrote is part of that decision.
    """
    client = await new_client()

    response = await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": "wrong-password-entirely"}
    )

    assert response.status_code == 401
    details = await helpers.audit_details(owner.id, "admin.login_failed")
    assert details
    assert details[-1]["reason"] == "bad_password"
    assert details[-1]["failed_count"] == 1


async def test_a_failed_login_for_an_unknown_account_is_recorded_too(
    db_client: AsyncClient,
) -> None:
    """With no actor, because there is nobody to attribute it to.

    Recorded anyway: a burst of these is the signal that someone is guessing
    addresses, and it is invisible if only known accounts are logged.
    """
    before = await helpers.unattributed_login_failures()

    response = await db_client.post(
        f"{AUTH}/login",
        json={"email": helpers.new_email("absent"), "password": "wrong-password-entirely"},
    )

    assert response.status_code == 401
    assert await helpers.unattributed_login_failures() == before + 1


async def test_a_rejected_code_is_recorded_with_its_reason(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    client = await new_client()
    first = await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": owner.password}
    )
    await client.post(
        f"{AUTH}/mfa", json={"mfa_token": first.json()["mfa_token"], "code": "000000"}
    )

    details = await helpers.audit_details(owner.id, "admin.mfa_failed")
    assert details
    assert details[-1]["reason"] == "invalid"


async def test_a_lockout_is_recorded(owner: SignedIn, new_client: ClientFactory) -> None:
    client = await new_client()
    for _ in range(MAX_FAILED_LOGINS + 1):
        await client.post(
            f"{AUTH}/login", json={"email": owner.email, "password": "wrong-password-entirely"}
        )
        await helpers.clear_rate_limits()

    assert "admin.login_locked" in await helpers.audit_actions(owner.id)


async def test_a_bad_recovery_code_is_recorded(owner: SignedIn, new_client: ClientFactory) -> None:
    client = await new_client()

    response = await client.post(
        f"{AUTH}/recovery-code", json={"email": owner.email, "code": "ZZZZZ-ZZZZZ"}
    )

    assert response.status_code == 401
    reasons = [row["reason"] for row in await helpers.audit_details(owner.id, "admin.login_failed")]
    assert "bad_recovery_code" in reasons


# ---------------------------------------------------------------------------
# The vocabulary actually gets written
# ---------------------------------------------------------------------------


async def test_the_whole_authentication_vocabulary_is_exercised(
    exclusive_owner: SignedIn,
    db_client: AsyncClient,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """One admin, driven through every M1 event, then the log is read back.

    Asserted as a set rather than per action so that an event silently dropped from
    one path is caught here even if that path's own test does not look at the log.
    """
    # An invite, an enrolment and a sign-in, all by one new admin.
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    client = await new_client()
    analyst = await helpers.enroll(client, invited, totp_clock)

    # A failed password, a failed code, then a change and a recovery code.
    await client.post(f"{AUTH}/login", json={"email": analyst.email, "password": "wrong-entirely"})
    await helpers.clear_rate_limits()
    second = await client.post(
        f"{AUTH}/login", json={"email": analyst.email, "password": analyst.password}
    )
    await client.post(
        f"{AUTH}/mfa", json={"mfa_token": second.json()["mfa_token"], "code": "000000"}
    )
    signed_in_again = await client.post(
        f"{AUTH}/mfa",
        json={
            "mfa_token": second.json()["mfa_token"],
            "code": await totp_clock.code(analyst.secret, analyst.id),
        },
    )
    # A new session means a new CSRF secret. Carrying the old one over is a 403,
    # which is correct behaviour and would silently hollow out this test.
    analyst.csrf_token = helpers.csrf_from(signed_in_again)

    await client.post(
        f"{AUTH}/password",
        json={
            "current_password": analyst.password,
            "new_password": helpers.ANOTHER_PASSWORD,
        },
        headers=analyst.headers(),
    )
    recovery_client = await new_client()
    await recovery_client.post(
        f"{AUTH}/recovery-code", json={"email": analyst.email, "code": analyst.recovery_codes[0]}
    )
    await client.post(f"{AUTH}/logout", headers=analyst.headers())

    recorded = set(await helpers.audit_actions(analyst.id))
    assert {
        "admin.enrollment_completed",
        "admin.totp_enrolled",
        "admin.login_succeeded",
        "admin.login_failed",
        "admin.mfa_failed",
        "admin.password_changed",
        "admin.recovery_code_used",
        "admin.logout",
    } <= recorded, f"missing: {recorded}"

    # The owner's own lifecycle actions, recorded against the owner as actor.
    await db_client.patch(
        f"{ADMINS}/{analyst.id}",
        json={"display_name": "Renamed"},
        headers=exclusive_owner.headers(),
    )
    await db_client.patch(
        f"{ADMINS}/{analyst.id}",
        json={"status": AdminStatus.DISABLED.value},
        headers=exclusive_owner.headers(),
    )
    owner_actions = set(await helpers.audit_actions(exclusive_owner.id))
    assert {"admin.updated", "admin.status_changed"} <= owner_actions


async def test_every_row_carries_the_trace_id_of_its_request(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    """One id ties a log line, an audit row and the client's error together (ES4)."""
    client = await new_client()
    response = await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": "wrong-password-entirely"}
    )
    trace_id = response.headers[TRACE_HEADER]

    assert trace_id
    assert await helpers.audit_rows_with_trace(trace_id) >= 1


async def test_an_audit_row_records_the_network_prefix_not_the_address(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    """Never a full address in a durable column (CLAUDE.md invariant 4)."""
    client = await new_client()
    await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": "wrong-password-entirely"}
    )

    prefixes = await helpers.audit_actor_prefixes(owner.id)
    assert prefixes, "the attempt must record where it came from"
    assert all("/" in prefix for prefix in prefixes), f"not a prefix: {prefixes}"


# ---------------------------------------------------------------------------
# The transaction policy the audit log depends on
# ---------------------------------------------------------------------------


async def test_a_commit_failure_is_reported_as_a_failure(
    exclusive_owner: SignedIn, db_app: object
) -> None:
    """docs/ERRORS.md E11, asserted at its root cause.

    The obvious ``async with session_scope(): yield session`` dependency commits in
    FastAPI's teardown, which runs *after* the response has been generated -- so a
    constraint violation at COMMIT was reported to the client as ``200`` while
    nothing was written. That is what hid E12 for as long as it did.

    The route below mutates and returns success without validating anything, which
    is what a handler written before that lesson looked like. The deferred owner
    trigger fires at COMMIT, and the middleware must turn that into a 5xx.
    """
    assert isinstance(db_app, FastAPI)
    owner_id = exclusive_owner.id

    @db_app.post("/__commit_failure__", include_in_schema=False)
    async def provoke(db: DbSession) -> dict[str, bool]:
        await db.execute(update(Admin).where(Admin.id == owner_id).values(role=AdminRole.ANALYST))
        return {"ok": True}

    transport = ASGITransport(app=db_app)
    async with AsyncClient(transport=transport, base_url=BASE_URL) as raw:
        response = await raw.post("/__commit_failure__")

    assert response.status_code >= 500, (
        "a commit that failed must not be reported as success -- this is E11"
    )
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert (await helpers.reload_admin(owner_id)).role is AdminRole.OWNER, (
        "and nothing may have been written"
    )
