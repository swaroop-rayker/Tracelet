"""The last active owner cannot be removed (F8.AC13).

Three routes can take an owner away -- demote, disable, delete -- and all three must
answer ``409 LAST_OWNER`` rather than leaving a system nobody can administer.

Two layers enforce it, and both are tested here because each covers a case the other
cannot:

* an **application pre-check**, which produces the clean 409 and runs before any
  mutation. It has to run first: the request session commits on a 4xx, so a handler
  that mutated and *then* rejected would commit the mutation and the deferred trigger
  would fire outside any handler as a 500 (docs/ERRORS.md E14);
* a **deferred constraint trigger** in the database, which is the actual guarantee.
  An application count cannot see a concurrent transaction.

``exclusive_owner`` is what makes any of this assertable: the rule is a statement
about the whole table, so a developer's real owner steps aside for the duration. See
the fixture for what it does and how to undo it by hand.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError

from tests.integration import helpers
from tests.integration.helpers import ADMINS, AUTH, ClientFactory, SignedIn, TotpClock
from tracelet.auth.models import Admin, AdminRole, AdminStatus
from tracelet.config import Settings
from tracelet.db.engine import get_engine, session_scope

pytestmark = pytest.mark.integration


async def test_demoting_the_last_owner_is_refused(
    exclusive_owner: SignedIn, db_client: AsyncClient
) -> None:
    response = await db_client.patch(
        f"{ADMINS}/{exclusive_owner.id}",
        json={"role": AdminRole.ANALYST.value},
        headers=exclusive_owner.headers(),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "LAST_OWNER"
    assert (await helpers.reload_admin(exclusive_owner.id)).role is AdminRole.OWNER


async def test_disabling_the_last_owner_is_refused(
    exclusive_owner: SignedIn, db_client: AsyncClient
) -> None:
    response = await db_client.patch(
        f"{ADMINS}/{exclusive_owner.id}",
        json={"status": AdminStatus.DISABLED.value},
        headers=exclusive_owner.headers(),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "LAST_OWNER"
    assert (await helpers.reload_admin(exclusive_owner.id)).status is AdminStatus.ACTIVE


async def test_the_refusal_leaves_the_owner_able_to_carry_on_working(
    exclusive_owner: SignedIn, db_client: AsyncClient
) -> None:
    """docs/ERRORS.md E14, stated as behaviour rather than as a status code.

    The mutation must not have been applied and committed on the way to the 409, or
    the next request would find an account that is no longer an active owner.
    """
    refused = await db_client.patch(
        f"{ADMINS}/{exclusive_owner.id}",
        json={"role": AdminRole.ANALYST.value},
        headers=exclusive_owner.headers(),
    )
    assert refused.status_code == 409

    still_working = await db_client.get(ADMINS)
    assert still_working.status_code == 200, "the owner must still hold the owner role"


async def test_the_last_owner_cannot_delete_themselves(
    exclusive_owner: SignedIn, db_client: AsyncClient
) -> None:
    """Deletion of the last owner is refused -- and it is refused as a 422.

    Worth stating precisely, because it is not the 409 the other two routes give.
    Sequentially, ``409 LAST_OWNER`` is unreachable on this route: deleting needs the
    owner role, so if the actor is an active owner and the target is someone else,
    the actor *is* a remaining active owner and the target was never the last. The
    only reachable case is the actor deleting themselves, which is rejected on its
    own terms first and would be a mistake at any role.

    The 409 on this route exists for the concurrent case, where two owners delete
    each other at the same time -- see the race test below. Recorded in docs/API.md
    §5 so the documented error set matches what a caller can actually observe.
    """
    response = await db_client.delete(
        f"{ADMINS}/{exclusive_owner.id}", headers=exclusive_owner.headers()
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert (await helpers.reload_admin(exclusive_owner.id)).status is AdminStatus.ACTIVE


async def test_an_owner_may_delete_another_owner_while_one_remains(
    exclusive_owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """The invariant is "not the last one", not "owners are undeletable"."""
    invited = await helpers.invite(integration_settings, role=AdminRole.OWNER)
    second_client = await new_client()
    second_owner = await helpers.enroll(second_client, invited, totp_clock)

    removed = await second_client.delete(
        f"{ADMINS}/{exclusive_owner.id}", headers=second_owner.headers()
    )

    assert removed.status_code == 204, removed.text
    assert await helpers.active_owner_count() == 1
    assert (await second_client.get(ADMINS)).status_code == 200, "the survivor carries on"


async def test_an_owner_may_be_demoted_while_another_active_owner_exists(
    exclusive_owner: SignedIn,
    db_client: AsyncClient,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """The invariant must not become "owners are immutable".

    The rule is narrower than the naive reading: an operation may not *remove the
    last* active owner (docs/ERRORS.md E12).
    """
    invited = await helpers.invite(integration_settings, role=AdminRole.OWNER)
    second_client = await new_client()
    second_owner = await helpers.enroll(second_client, invited, totp_clock)

    response = await db_client.patch(
        f"{ADMINS}/{second_owner.id}",
        json={"role": AdminRole.ANALYST.value},
        headers=exclusive_owner.headers(),
    )

    assert response.status_code == 200
    assert response.json()["role"] == AdminRole.ANALYST.value
    assert "admin.role_changed" in await helpers.audit_actions(exclusive_owner.id)
    # A demoted owner loses owner-only routes on the next request.
    assert (await second_client.get(ADMINS)).status_code == 403


async def test_role_and_status_changes_record_what_they_changed_from(
    exclusive_owner: SignedIn,
    db_client: AsyncClient,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """The audit row says where a change came from, not the new value twice (E51).

    The ORM update synchronises the session, so reading the target after it reported
    every promotion as {"from": "owner", "to": "owner"}.
    """
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst = await helpers.enroll(await new_client(), invited, totp_clock)

    promoted = await db_client.patch(
        f"{ADMINS}/{analyst.id}",
        json={"role": AdminRole.OWNER.value},
        headers=exclusive_owner.headers(),
    )
    disabled = await db_client.patch(
        f"{ADMINS}/{analyst.id}",
        json={"status": "disabled"},
        headers=exclusive_owner.headers(),
    )

    assert promoted.status_code == 200, promoted.text
    assert disabled.status_code == 200, disabled.text
    roles = await helpers.audit_details(exclusive_owner.id, "admin.role_changed")
    statuses = await helpers.audit_details(exclusive_owner.id, "admin.status_changed")
    assert roles[-1] == {"from": "analyst", "to": "owner"}
    assert statuses[-1] == {"from": "active", "to": "disabled"}


async def test_a_pending_owner_does_not_count_as_an_active_one(
    exclusive_owner: SignedIn, db_client: AsyncClient, integration_settings: Settings
) -> None:
    """An invited-but-unenrolled owner cannot administer anything.

    Treating them as a live owner would let "invite a second owner" be enough to
    demote the only one who can actually sign in.
    """
    await helpers.invite(integration_settings, role=AdminRole.OWNER)

    response = await db_client.patch(
        f"{ADMINS}/{exclusive_owner.id}",
        json={"role": AdminRole.ANALYST.value},
        headers=exclusive_owner.headers(),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "LAST_OWNER"


async def test_a_disabled_owner_does_not_count_as_an_active_one(
    exclusive_owner: SignedIn,
    db_client: AsyncClient,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    invited = await helpers.invite(integration_settings, role=AdminRole.OWNER)
    second_client = await new_client()
    second_owner = await helpers.enroll(second_client, invited, totp_clock)

    disabled = await db_client.patch(
        f"{ADMINS}/{second_owner.id}",
        json={"status": AdminStatus.DISABLED.value},
        headers=exclusive_owner.headers(),
    )
    assert disabled.status_code == 200

    response = await db_client.patch(
        f"{ADMINS}/{exclusive_owner.id}",
        json={"role": AdminRole.ANALYST.value},
        headers=exclusive_owner.headers(),
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# The case the application-level count cannot see
# ---------------------------------------------------------------------------


async def test_the_owner_set_is_locked_before_it_is_read(
    exclusive_owner: SignedIn, db_client: AsyncClient, db_app: object
) -> None:
    """docs/ERRORS.md E16 — the serialisation the pre-check depends on.

    Deterministic, unlike the two races below, and it is what actually pins the fix
    in place. An external transaction holds ``FOR UPDATE`` on the active owner rows;
    the handler must block on that lock *before* deciding anything, because a
    decision taken from an unlocked read cannot see a concurrent transaction and
    ``SET CONSTRAINTS ALL IMMEDIATE`` cannot rescue it -- that fires the deferred
    trigger early, in the same blind snapshot, and consumes the pending event so
    nothing is re-checked at COMMIT.
    """
    del db_app
    async with get_engine().connect() as conn:
        transaction = await conn.begin()
        await conn.execute(
            text(
                "SELECT id FROM admins WHERE role = 'owner' AND status = 'active' "
                "ORDER BY id FOR UPDATE"
            )
        )

        pending = asyncio.create_task(
            db_client.patch(
                f"{ADMINS}/{exclusive_owner.id}",
                json={"role": AdminRole.ANALYST.value},
                headers=exclusive_owner.headers(),
            )
        )
        await asyncio.sleep(0.75)
        blocked = pending.done()
        await transaction.rollback()

    response = await asyncio.wait_for(pending, timeout=10)

    assert not blocked, "the handler read the owner set without locking it first"
    assert response.status_code == 409
    assert response.json()["code"] == "LAST_OWNER"


async def test_two_simultaneous_demotions_cannot_both_succeed(
    exclusive_owner: SignedIn,
    db_client: AsyncClient,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """Two owners, two concurrent demotions, one each.

    Without serialisation, each transaction's own count sees the *other* owner still
    active -- an uncommitted change in one transaction is invisible to the other --
    so both pass the application-level check and the table ends with zero active
    owners and nobody able to administer anything.

    The assertion that matters is the count at the end. Exactly one request may
    succeed; *how* the other is refused depends on the interleaving and all three
    outcomes are correct: 409 when its pre-check saw the truth, 403 when it lost its
    own owner role first, 401 when its account went away underneath it.
    """
    invited = await helpers.invite(integration_settings, role=AdminRole.OWNER)
    second_client = await new_client()
    second_owner = await helpers.enroll(second_client, invited, totp_clock)

    first, second = await asyncio.gather(
        db_client.patch(
            f"{ADMINS}/{second_owner.id}",
            json={"role": AdminRole.ANALYST.value},
            headers=exclusive_owner.headers(),
        ),
        second_client.patch(
            f"{ADMINS}/{exclusive_owner.id}",
            json={"role": AdminRole.ANALYST.value},
            headers=second_owner.headers(),
        ),
    )

    statuses = [first.status_code, second.status_code]
    assert sorted(statuses).count(200) == 1, f"exactly one may succeed, got {statuses}"
    refused = first if second.status_code == 200 else second
    assert refused.status_code in {401, 403, 409}, refused.text

    remaining = await helpers.active_owner_count()
    assert remaining == 1, f"{remaining} active owners left; the invariant is broken"


async def test_two_simultaneous_deletions_cannot_both_succeed(
    exclusive_owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """The same race on the delete path, which has its own pre-check."""
    first_invited = await helpers.invite(integration_settings, role=AdminRole.OWNER)
    second_invited = await helpers.invite(integration_settings, role=AdminRole.OWNER)
    first_client = await new_client()
    second_client = await new_client()
    first_owner = await helpers.enroll(first_client, first_invited, totp_clock)
    second_owner = await helpers.enroll(second_client, second_invited, totp_clock)

    # Three active owners now. Remove the fixture's one so the race below is
    # between exactly two, which is the case the pre-check cannot see.
    stepped_down = await first_client.delete(
        f"{ADMINS}/{exclusive_owner.id}", headers=first_owner.headers()
    )
    assert stepped_down.status_code == 204

    first, second = await asyncio.gather(
        first_client.delete(f"{ADMINS}/{second_owner.id}", headers=first_owner.headers()),
        second_client.delete(f"{ADMINS}/{first_owner.id}", headers=second_owner.headers()),
    )

    statuses = [first.status_code, second.status_code]
    assert sorted(statuses).count(204) == 1, f"exactly one may succeed, got {statuses}"
    remaining = await helpers.active_owner_count()
    assert remaining == 1, f"{remaining} active owners left; the invariant is broken"


async def test_an_unknown_target_is_a_404_rather_than_a_conflict(
    exclusive_owner: SignedIn, db_client: AsyncClient
) -> None:
    response = await db_client.patch(
        f"{ADMINS}/{uuid.uuid4()}",
        json={"role": AdminRole.ANALYST.value},
        headers=exclusive_owner.headers(),
    )
    assert response.status_code == 404


async def test_the_engine_level_invariants_are_actually_installed(
    db_app: object,
) -> None:
    """Documentation is not enforcement. These are checked in the catalogue.

    All three exist because two concurrent requests can defeat a Python check
    (docs/DATA_MODEL.md §3.1).
    """
    del db_app
    async with get_engine().connect() as conn:
        triggers = set(
            (
                await conn.execute(
                    text(
                        "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                        "WHERE c.relname = 'admins' AND NOT t.tgisinternal"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {
            "trg_admins_owner_remains_on_update",
            "trg_admins_owner_remains_on_delete",
        } <= triggers

        deferred = (
            await conn.execute(
                text(
                    "SELECT bool_and(tgdeferrable AND tginitdeferred) FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE c.relname = 'admins' AND NOT t.tgisinternal"
                )
            )
        ).scalar()
        assert deferred is True, "the check must run at COMMIT, not per statement"

        checks = set(
            (
                await conn.execute(
                    text(
                        "SELECT conname FROM pg_constraint con "
                        "JOIN pg_class c ON c.oid = con.conrelid "
                        "WHERE c.relname = 'admins' AND con.contype = 'c'"
                    )
                )
            )
            .scalars()
            .all()
        )
        # The project naming convention prefixes every CHECK with ck_<table>_
        # (docs/DATA_MODEL.md): an unnamed constraint is one you cannot alter later.
        assert "ck_admins_active_requires_password_and_totp" in checks, (
            "F8.AC4 is enforced by the engine, not by a service-layer guard"
        )


async def test_an_active_account_without_totp_is_rejected_by_the_database(
    db_app: object, integration_settings: Settings
) -> None:
    """The CHECK the whole auth design rests on.

    Proven by trying to defeat it directly rather than through a route, because a
    route could be changed and the guarantee has to survive that.
    """
    del db_app
    invited = await helpers.invite(integration_settings)

    with pytest.raises(IntegrityError, match="ck_admins_active_requires_password_and_totp"):
        async with session_scope() as db:
            await db.execute(
                update(Admin).where(Admin.id == invited.id).values(status=AdminStatus.ACTIVE)
            )


async def test_deleting_an_admin_keeps_the_record_of_what_they_did(
    exclusive_owner: SignedIn, db_client: AsyncClient, integration_settings: Settings
) -> None:
    """``ON DELETE SET NULL``, not CASCADE.

    Deleting an admin must not delete the audit trail of their actions -- that would
    defeat the point of having one, and would make deletion the most useful thing in
    the system to an attacker.
    """
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    before = await helpers.audit_rows_for_target(invited.id)
    assert before > 0, "the invite itself is audited"

    deleted = await db_client.delete(f"{ADMINS}/{invited.id}", headers=exclusive_owner.headers())

    assert deleted.status_code == 204
    assert await helpers.audit_rows_for_target(invited.id) > before, (
        "the rows describing a deleted admin must survive, plus the deletion itself"
    )
    assert "admin.deleted" in await helpers.audit_actions(exclusive_owner.id)


async def test_the_delete_audit_row_carries_no_full_address(
    exclusive_owner: SignedIn, db_client: AsyncClient, integration_settings: Settings
) -> None:
    """Redacted before write (F12.AC13): the domain, never the whole identifier."""
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)

    await db_client.delete(f"{ADMINS}/{invited.id}", headers=exclusive_owner.headers())

    details = await helpers.audit_details(exclusive_owner.id, "admin.deleted")
    assert details
    assert details[-1]["email_domain"] == "example.test"
    assert invited.email not in str(details[-1])


async def test_an_analyst_cannot_administer_anyone(
    exclusive_owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """Every owner-only route is gated server-side (F8.AC12, CLAUDE.md invariant 9)."""
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    client = await new_client()
    analyst = await helpers.enroll(client, invited, totp_clock)

    patched = await client.patch(
        f"{ADMINS}/{exclusive_owner.id}",
        json={"role": AdminRole.ANALYST.value},
        headers=analyst.headers(),
    )
    deleted = await client.delete(f"{ADMINS}/{exclusive_owner.id}", headers=analyst.headers())
    reissued = await client.post(
        f"{ADMINS}/{exclusive_owner.id}/enrollment-token", headers=analyst.headers()
    )

    assert patched.status_code == 403
    assert deleted.status_code == 403
    assert reissued.status_code == 403
    assert (await client.get(f"{AUTH}/me")).status_code == 200, "but they stay signed in"
