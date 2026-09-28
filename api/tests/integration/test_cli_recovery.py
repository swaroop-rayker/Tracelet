"""The break-glass CLI paths (F8.AC8).

These exist so that losing the recovery codes *and* the Telegram account is not
permanent lockout. That makes them the last line of defence, and a last line that
fails when it is finally needed is worse than one that was never claimed — so they
are tested against the real database like everything else.

`reset-totp` had a defect that appeared only on the account most likely to need it:
the sole owner (docs/ERRORS.md E17). Clearing TOTP forces the status away from
`active`, and the owner trigger refuses to let the last active owner leave `active`,
so the command aborted with "cannot remove the last active owner". Nothing covered
it, because it works fine for every *other* admin.

The CLI writes its audit rows with no actor — `actor_admin_id` is `NULL`, meaning the
system acted — so the assertions here look the rows up by target.
"""

from __future__ import annotations

import re
import sys

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tests.integration import helpers
from tests.integration.helpers import AUTH, ClientFactory, Invited, SignedIn, TotpClock
from tracelet.auth.models import Admin, AdminRole, AdminStatus, EnrollmentToken
from tracelet.cli import admin as admin_cli
from tracelet.config import Settings
from tracelet.db.engine import session_scope

pytestmark = pytest.mark.integration

ENROLL_URL = re.compile(r"https://\S+/enroll\?token=(\S+)")


class PipedStdin:
    """A non-TTY stdin, as a runbook piping from a password manager would be.

    The CLI reads a password from a TTY prompt when it has one and from stdin when it
    does not -- never from an argument, which would land in shell history and in the
    process table where any other user on the box can read it.
    """

    def __init__(self, line: str) -> None:
        self._line = line

    def isatty(self) -> bool:
        return False

    def readline(self) -> str:
        return self._line


def token_from_output(printed: str) -> str:
    """The token out of what the command actually printed.

    Parsed from stdout rather than fetched from the database on purpose: the operator
    has nothing else to work from, so if the printed URL is wrong or truncated the
    command is broken regardless of what the row says.
    """
    match = ENROLL_URL.search(printed)
    assert match is not None, f"no enrollment URL in output:\n{printed}"
    return match.group(1)


async def live_enrollment_tokens(admin_id: object) -> int:
    async with session_scope() as db:
        rows = await db.execute(
            select(EnrollmentToken.id).where(
                EnrollmentToken.admin_id == admin_id,
                EnrollmentToken.used_at.is_(None),
            )
        )
        return len(rows.scalars().all())


# ---------------------------------------------------------------------------
# reset-totp — the sole owner
# ---------------------------------------------------------------------------


async def test_reset_totp_succeeds_for_the_sole_owner(
    exclusive_owner: SignedIn, capsys: pytest.CaptureFixture[str]
) -> None:
    """docs/ERRORS.md E17 — this used to abort with an integrity error.

    The account must stay `active`, because leaving `active` is exactly what the owner
    trigger refuses. The secret is therefore left in place and the enrollment link does
    the replacing.
    """
    assert await admin_cli._reset_totp(exclusive_owner.email) == 0

    admin = await helpers.reload_admin(exclusive_owner.id)
    assert admin.status is AdminStatus.ACTIVE, "the last active owner may not be deactivated"
    assert admin.totp_secret_enc is not None, (
        "the current authenticator must keep working until the link is used"
    )
    assert await live_enrollment_tokens(exclusive_owner.id) >= 1

    printed = capsys.readouterr().out
    assert "only active owner" in printed
    # The operator has to be told the old authenticator is still live, or they will
    # assume a compromised device has been locked out when it has not.
    assert "keeps working" in printed
    assert token_from_output(printed)


async def test_reset_totp_revokes_the_sole_owners_sessions(
    exclusive_owner: SignedIn, db_client: AsyncClient
) -> None:
    """Even though the account stays active: whoever ran this wants the old sessions gone."""
    assert (await db_client.get(f"{AUTH}/me")).status_code == 200

    assert await admin_cli._reset_totp(exclusive_owner.email) == 0

    assert await helpers.live_session_count(exclusive_owner.id) == 0
    assert (await db_client.get(f"{AUTH}/me")).status_code == 401


async def test_the_reissued_link_replaces_the_sole_owners_credentials(
    exclusive_owner: SignedIn,
    new_client: ClientFactory,
    totp_clock: TotpClock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The point of the whole path: the owner gets back in.

    Driven through the real endpoints, which is what proves the status never had to
    change for re-enrolment to work.
    """
    assert await admin_cli._reset_totp(exclusive_owner.email) == 0
    token = token_from_output(capsys.readouterr().out)

    client = await new_client()
    signed_in = await helpers.enroll(
        client,
        Invited(
            id=exclusive_owner.id,
            email=exclusive_owner.email,
            role=AdminRole.OWNER,
            token=token,
            enrollment_url=f"https://localhost/enroll?token={token}",
        ),
        totp_clock,
        password=helpers.ANOTHER_PASSWORD,
    )

    assert (await client.get(f"{AUTH}/me")).status_code == 200
    admin = await helpers.reload_admin(exclusive_owner.id)
    assert admin.status is AdminStatus.ACTIVE
    assert admin.totp_enrolled_at is not None
    assert signed_in.secret != exclusive_owner.secret, "a new secret, not the old one"
    assert not set(signed_in.recovery_codes) & set(exclusive_owner.recovery_codes)


async def test_the_old_credentials_stop_working_once_the_link_is_used(
    exclusive_owner: SignedIn,
    new_client: ClientFactory,
    totp_clock: TotpClock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ "Keeps working until you use the link" has to mean exactly that."""
    assert await admin_cli._reset_totp(exclusive_owner.email) == 0
    token = token_from_output(capsys.readouterr().out)

    client = await new_client()
    await helpers.enroll(
        client,
        Invited(
            id=exclusive_owner.id,
            email=exclusive_owner.email,
            role=AdminRole.OWNER,
            token=token,
            enrollment_url=f"https://localhost/enroll?token={token}",
        ),
        totp_clock,
        password=helpers.ANOTHER_PASSWORD,
    )

    stale = await new_client()
    refused = await stale.post(
        f"{AUTH}/login",
        json={"email": exclusive_owner.email, "password": exclusive_owner.password},
    )
    assert refused.status_code == 401

    spent = await new_client()
    replayed = await spent.post(
        f"{AUTH}/recovery-code",
        json={"email": exclusive_owner.email, "code": exclusive_owner.recovery_codes[0]},
    )
    assert replayed.status_code == 401, "the previous set of recovery codes is dead"


# ---------------------------------------------------------------------------
# reset-totp — everyone else
# ---------------------------------------------------------------------------


async def test_reset_totp_clears_in_place_for_anyone_who_is_not_the_last_owner(
    exclusive_owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The stricter behaviour, which is correct whenever it is available.

    An analyst's secret is nulled and the account drops to `pending_enrollment`, so a
    compromised authenticator stops working immediately rather than at re-enrolment.
    """
    del exclusive_owner
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    client = await new_client()
    analyst = await helpers.enroll(client, invited, totp_clock)
    assert (await client.get(f"{AUTH}/me")).status_code == 200

    assert await admin_cli._reset_totp(analyst.email) == 0

    admin = await helpers.reload_admin(analyst.id)
    assert admin.status is AdminStatus.PENDING_ENROLLMENT
    assert admin.totp_secret_enc is None
    assert admin.totp_key_version is None
    assert admin.totp_enrolled_at is None
    assert admin.totp_last_counter is None
    assert (await client.get(f"{AUTH}/me")).status_code == 401
    assert "cleared" in capsys.readouterr().out


async def test_reset_totp_clears_in_place_for_an_owner_who_is_not_the_last(
    exclusive_owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """A second owner exists, so the strict path is available again."""
    invited = await helpers.invite(integration_settings, role=AdminRole.OWNER)
    second_client = await new_client()
    second_owner = await helpers.enroll(second_client, invited, totp_clock)

    assert await admin_cli._reset_totp(second_owner.email) == 0

    admin = await helpers.reload_admin(second_owner.id)
    assert admin.status is AdminStatus.PENDING_ENROLLMENT
    assert admin.totp_secret_enc is None
    # And the fixture's owner is untouched, still able to administer.
    assert (await helpers.reload_admin(exclusive_owner.id)).status is AdminStatus.ACTIVE


async def test_reset_totp_is_audited_either_way(
    exclusive_owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """A break-glass path that left no trace would be the most useful thing in the
    system to an attacker. The detail records which of the two behaviours ran.
    """
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    client = await new_client()
    analyst = await helpers.enroll(client, invited, totp_clock)

    assert await admin_cli._reset_totp(analyst.email) == 0
    assert await admin_cli._reset_totp(exclusive_owner.email) == 0

    cleared = await helpers.audit_details_for_target(analyst.id, "admin.totp_reset")
    kept = await helpers.audit_details_for_target(exclusive_owner.id, "admin.totp_reset")
    assert cleared and cleared[-1]["cleared_in_place"] is True
    assert kept and kept[-1]["cleared_in_place"] is False


async def test_reset_totp_reports_an_unknown_address(
    db_app: object, capsys: pytest.CaptureFixture[str]
) -> None:
    del db_app
    assert await admin_cli._reset_totp("nobody@example.test") == 1
    assert "No admin" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# reset-password
# ---------------------------------------------------------------------------


async def test_reset_password_enforces_the_same_policy_as_the_api(
    exclusive_owner: SignedIn,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A CLI that bypassed the policy would be a back door."""
    monkeypatch.setattr(sys, "stdin", PipedStdin("password123\n"))

    assert await admin_cli._reset_password(exclusive_owner.email, revoke_sessions=True) == 1
    assert "rejected" in capsys.readouterr().err


async def test_reset_password_works_with_database_access_alone(
    exclusive_owner: SignedIn,
    db_client: AsyncClient,
    new_client: ClientFactory,
    totp_clock: TotpClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F8.AC8, and it must leave TOTP untouched.

    Restoring a password is not a reason to weaken the second factor, so the owner
    signs in afterwards with the new password and the *same* authenticator.
    """
    monkeypatch.setattr(sys, "stdin", PipedStdin(f"{helpers.ANOTHER_PASSWORD}\n"))

    assert await admin_cli._reset_password(exclusive_owner.email, revoke_sessions=True) == 0

    assert await helpers.live_session_count(exclusive_owner.id) == 0
    assert (await db_client.get(f"{AUTH}/me")).status_code == 401

    admin = await helpers.reload_admin(exclusive_owner.id)
    assert admin.totp_secret_enc is not None, "the second factor is unchanged"
    actions = await helpers.audit_actions_for_target(exclusive_owner.id)
    assert "admin.password_reset_cli" in actions

    client = await new_client()
    assert await helpers.sign_in(
        client,
        email=exclusive_owner.email,
        password=helpers.ANOTHER_PASSWORD,
        secret=exclusive_owner.secret,
        admin_id=exclusive_owner.id,
        clock=totp_clock,
    )


async def test_reset_password_refuses_an_empty_line_on_stdin(
    exclusive_owner: SignedIn,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A runbook piping from an empty variable must not set an empty password."""
    monkeypatch.setattr(sys, "stdin", PipedStdin("\n"))

    assert await admin_cli._reset_password(exclusive_owner.email, revoke_sessions=True) == 1
    assert "No password" in capsys.readouterr().err
    assert (await helpers.reload_admin(exclusive_owner.id)).password_hash is not None


# ---------------------------------------------------------------------------
# bootstrap and list
# ---------------------------------------------------------------------------


async def test_bootstrap_refuses_to_create_a_second_owner(
    exclusive_owner: SignedIn, capsys: pytest.CaptureFixture[str]
) -> None:
    """It creates only the FIRST owner. Anything else goes through an invitation,
    so there is exactly one way an admin comes into existence with a password.
    """
    del exclusive_owner
    assert await admin_cli._bootstrap("someone-else@example.test", "Someone") == 1
    assert "only the FIRST owner" in capsys.readouterr().err


async def test_bootstrap_reissues_a_link_for_an_existing_address(
    exclusive_owner: SignedIn, capsys: pytest.CaptureFixture[str]
) -> None:
    """Idempotent on the address that already exists, which is what makes it safe to
    re-run after losing the first link.
    """
    before = await live_enrollment_tokens(exclusive_owner.id)

    assert await admin_cli._bootstrap(exclusive_owner.email, "Owner") == 0

    printed = capsys.readouterr().out
    assert "already exists" in printed
    assert token_from_output(printed)
    assert await live_enrollment_tokens(exclusive_owner.id) == before + 1


async def test_list_shows_role_status_and_the_code_count(
    exclusive_owner: SignedIn, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one command an operator runs before deciding what to repair."""
    assert await admin_cli._list() == 0

    printed = capsys.readouterr().out
    assert exclusive_owner.email in printed
    assert "owner" in printed
    assert "active" in printed


async def test_the_admins_table_is_readable_when_empty(db_app: object) -> None:
    """Not asserted by driving it to empty — the engine forbids removing the last
    owner, and rightly. This only checks the query the empty case runs.
    """
    del db_app
    async with session_scope() as db:
        rows = await db.execute(select(Admin).order_by(Admin.created_at))
        assert rows.scalars().all() is not None
