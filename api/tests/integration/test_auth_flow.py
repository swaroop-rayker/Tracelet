"""The admin authentication flow, end to end, against a real database (F8).

This file is the permanent form of a throwaway harness that caught eight bugs by
hand (docs/ERRORS.md E8-E15). Every section below corresponds to something that
was once broken and would have shipped, so the assertions are deliberately about
observable behaviour at the wire -- status codes, cookie attributes, headers and
audit rows -- rather than about internal state.

Nothing here is mocked. The database, the Argon2 hashing, the AES-256-GCM envelope
and the TOTP verification are all the real implementations (ES3, F14.AC7).
"""

from __future__ import annotations

import time
import uuid

import pytest
from httpx import AsyncClient

from tests.integration import helpers
from tests.integration.helpers import (
    ADMINS,
    ANOTHER_PASSWORD,
    AUTH,
    STRONG_PASSWORD,
    ClientFactory,
    Invited,
    SignedIn,
    TotpClock,
)
from tracelet.auth.models import AdminRole, AdminStatus
from tracelet.auth.recovery import CODE_COUNT
from tracelet.auth.service import MAX_FAILED_LOGINS
from tracelet.auth.sessions import COOKIE_NAME
from tracelet.config import Settings
from tracelet.notify import telegram

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------


async def test_the_enrollment_link_is_https(integration_settings: Settings) -> None:
    """docs/ERRORS.md E8.

    An http link is not cosmetic. The session cookie carries ``Secure``, so a
    browser refuses to store it, and enrolment completes while silently failing to
    sign the admin in.
    """
    invited = await helpers.invite(integration_settings)
    assert invited.enrollment_url.startswith("https://")


async def test_enrolment_returns_the_secret_and_ten_codes_exactly_once(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    invited = await helpers.invite(integration_settings)

    response = await db_client.post(
        f"{AUTH}/enroll", json={"token": invited.token, "password": STRONG_PASSWORD}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["recovery_codes"]) == CODE_COUNT
    assert len(set(body["recovery_codes"])) == CODE_COUNT
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert body["secret"] in body["otpauth_uri"]
    # No QR code is rendered (ES5), so the manual-entry hint is the whole
    # instruction the enrolling admin gets.
    assert "authenticator" in body["hint"].lower()
    assert body["confirm_token"]


async def test_an_enrollment_token_is_single_use(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    invited = await helpers.invite(integration_settings)
    payload = {"token": invited.token, "password": STRONG_PASSWORD}

    assert (await db_client.post(f"{AUTH}/enroll", json=payload)).status_code == 200
    replayed = await db_client.post(f"{AUTH}/enroll", json=payload)

    assert replayed.status_code == 404
    assert replayed.json()["code"] == "NOT_FOUND"


async def test_a_weak_password_does_not_burn_the_enrollment_link(
    db_client: AsyncClient, integration_settings: Settings, totp_clock: TotpClock
) -> None:
    """docs/ERRORS.md E15.

    The token used to be consumed before the password was validated, so being told
    "too common" also destroyed the invite -- and the invitee had to go back to an
    owner for a fresh one.
    """
    invited = await helpers.invite(integration_settings)

    rejected = await db_client.post(
        f"{AUTH}/enroll", json={"token": invited.token, "password": "password123"}
    )
    assert rejected.status_code == 422
    assert {error["code"] for error in rejected.json()["errors"]} >= {"TOO_COMMON"}

    # The same link still works.
    signed_in = await helpers.enroll(db_client, invited, totp_clock)
    assert signed_in.id == invited.id


async def test_login_is_refused_until_totp_is_confirmed(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    """The account is not ``active`` yet, and the database will not let it be.

    A CHECK constraint forbids ``active`` without ``totp_enrolled_at``, so this is
    not a service-layer guard someone can forget (F8.AC4).
    """
    invited = await helpers.invite(integration_settings)
    await db_client.post(
        f"{AUTH}/enroll", json={"token": invited.token, "password": STRONG_PASSWORD}
    )

    response = await db_client.post(
        f"{AUTH}/login", json={"email": invited.email, "password": STRONG_PASSWORD}
    )

    assert response.status_code == 401
    assert (await helpers.reload_admin(invited.id)).status is AdminStatus.PENDING_ENROLLMENT


async def test_a_wrong_confirmation_code_is_rejected_and_leaves_the_account_pending(
    db_client: AsyncClient, integration_settings: Settings
) -> None:
    invited = await helpers.invite(integration_settings)
    enrolled = await db_client.post(
        f"{AUTH}/enroll", json={"token": invited.token, "password": STRONG_PASSWORD}
    )

    response = await db_client.post(
        f"{AUTH}/totp/confirm",
        json={"confirm_token": enrolled.json()["confirm_token"], "code": "000000"},
    )

    assert response.status_code == 401
    assert (await helpers.reload_admin(invited.id)).status is AdminStatus.PENDING_ENROLLMENT


async def test_confirming_totp_activates_the_account_and_signs_in_directly(
    db_client: AsyncClient, integration_settings: Settings, totp_clock: TotpClock
) -> None:
    """docs/ERRORS.md E12.

    Every update on the way from ``pending_enrollment`` to ``active`` happens while
    no active owner exists, which the first formulation of the owner trigger
    asserted was impossible. It failed at COMMIT, and E11 reported that as a 200.
    """
    invited = await helpers.invite(integration_settings, role=AdminRole.OWNER)

    signed_in = await helpers.enroll(db_client, invited, totp_clock)

    admin = await helpers.reload_admin(invited.id)
    assert admin.status is AdminStatus.ACTIVE
    assert admin.totp_enrolled is True
    assert admin.totp_last_counter is not None, "the accepted step must be persisted"
    assert signed_in.csrf_token

    me = await db_client.get(f"{AUTH}/me")
    assert me.status_code == 200, "confirming must issue the session, not just activate"


async def test_the_session_cookie_carries_every_hardening_flag(
    db_client: AsyncClient, integration_settings: Settings, totp_clock: TotpClock
) -> None:
    """F8.AC2.

    ``__Host-`` is browser-enforced and requires Secure, Path=/ and no Domain --
    which is what stops a sibling subdomain setting a session cookie for us.
    """
    invited = await helpers.invite(integration_settings)
    enrolled = await db_client.post(
        f"{AUTH}/enroll", json={"token": invited.token, "password": STRONG_PASSWORD}
    )
    body = enrolled.json()
    confirmed = await db_client.post(
        f"{AUTH}/totp/confirm",
        json={
            "confirm_token": body["confirm_token"],
            "code": await totp_clock.code(body["secret"], invited.id),
        },
    )

    raw = confirmed.headers["set-cookie"]
    assert COOKIE_NAME.startswith("__Host-")
    assert raw.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in raw
    assert "Secure" in raw
    assert "SameSite=strict" in raw.replace("SameSite=Strict", "SameSite=strict")
    assert "Path=/" in raw
    assert "Domain=" not in raw


async def test_a_confirm_token_cannot_activate_a_different_account(
    db_client: AsyncClient, integration_settings: Settings, totp_clock: TotpClock
) -> None:
    """The confirm token identifies who is confirming, never a supplied admin id.

    Taking the admin from the request instead would let anyone activate any pending
    account with a code from their own authenticator -- a complete bypass.
    """
    first = await helpers.invite(integration_settings)
    second = await helpers.invite(integration_settings)

    enrolled_first = await db_client.post(
        f"{AUTH}/enroll", json={"token": first.token, "password": STRONG_PASSWORD}
    )
    enrolled_second = await db_client.post(
        f"{AUTH}/enroll", json={"token": second.token, "password": ANOTHER_PASSWORD}
    )

    # A live code for the SECOND admin's secret, against the FIRST admin's token.
    crossed = await db_client.post(
        f"{AUTH}/totp/confirm",
        json={
            "confirm_token": enrolled_first.json()["confirm_token"],
            "code": await totp_clock.code(enrolled_second.json()["secret"], second.id),
        },
    )

    assert crossed.status_code == 401
    assert (await helpers.reload_admin(first.id)).status is AdminStatus.PENDING_ENROLLMENT
    assert (await helpers.reload_admin(second.id)).status is AdminStatus.PENDING_ENROLLMENT


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def test_three_consecutive_logins_all_succeed(
    owner: SignedIn, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    """docs/ERRORS.md E10 — the regression test for per-worker auth state.

    The MFA challenge used to live in a module-level dict. With two Gunicorn
    workers the follow-up request found it about half the time, so login was a coin
    flip: the first attempt returned 401 and the next 404. A per-process store
    fails this test roughly half the time under the real deployment.

    Each login uses a fresh TOTP step, because the accepted step is the stored
    high-water mark and reusing one is a replay by definition.
    """
    for attempt in range(3):
        client = await new_client()
        csrf = await helpers.sign_in(
            client,
            email=owner.email,
            password=owner.password,
            secret=owner.secret,
            admin_id=owner.id,
            clock=totp_clock,
        )
        assert csrf, f"attempt {attempt + 1} produced no CSRF token"
        me = await client.get(f"{AUTH}/me")
        assert me.status_code == 200, f"attempt {attempt + 1}: {me.text}"


async def test_a_totp_code_cannot_be_replayed(
    owner: SignedIn, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    """F8.AC5.

    Someone who observes a code -- over a shoulder, in a screen share, from a
    phished form -- must not be able to reuse it inside its own 30-second window.
    """
    code = await totp_clock.code(owner.secret, owner.id)

    first_client = await new_client()
    first = await first_client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": owner.password}
    )
    accepted = await first_client.post(
        f"{AUTH}/mfa", json={"mfa_token": first.json()["mfa_token"], "code": code}
    )
    assert accepted.status_code == 204

    second_client = await new_client()
    second = await second_client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": owner.password}
    )
    replayed = await second_client.post(
        f"{AUTH}/mfa", json={"mfa_token": second.json()["mfa_token"], "code": code}
    )

    assert replayed.status_code == 401
    # Reported as invalid rather than "already used": telling a caller their code
    # was correct-but-spent confirms they hold a real code.
    assert replayed.json()["code"] in {"MFA_INVALID", "UNAUTHENTICATED"}
    reasons = [
        row.get("reason") for row in await helpers.audit_details(owner.id, "admin.mfa_failed")
    ]
    assert "replayed" in reasons


async def test_a_wrong_code_does_not_send_the_admin_back_to_the_password_step(
    owner: SignedIn, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    """The MFA challenge is peeked, not consumed, until a code is accepted.

    Burning the challenge on a typo would make a mistyped digit cost the whole
    sign-in.
    """
    client = await new_client()
    first = await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": owner.password}
    )
    mfa_token = first.json()["mfa_token"]

    wrong = await client.post(f"{AUTH}/mfa", json={"mfa_token": mfa_token, "code": "000000"})
    assert wrong.status_code == 401

    right = await client.post(
        f"{AUTH}/mfa",
        json={"mfa_token": mfa_token, "code": await totp_clock.code(owner.secret, owner.id)},
    )
    assert right.status_code == 204, "the same challenge must still be usable"


async def test_the_mfa_challenge_is_consumed_once_a_code_is_accepted(
    owner: SignedIn, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    client = await new_client()
    first = await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": owner.password}
    )
    mfa_token = first.json()["mfa_token"]

    accepted = await client.post(
        f"{AUTH}/mfa",
        json={"mfa_token": mfa_token, "code": await totp_clock.code(owner.secret, owner.id)},
    )
    assert accepted.status_code == 204

    reused = await client.post(
        f"{AUTH}/mfa",
        json={"mfa_token": mfa_token, "code": await totp_clock.code(owner.secret, owner.id)},
    )
    assert reused.status_code == 401


async def test_step_one_does_not_issue_a_session(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    """TOTP is mandatory, so a correct password alone reaches nothing (F8.AC4)."""
    client = await new_client()
    first = await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": owner.password}
    )

    assert first.status_code == 200
    assert COOKIE_NAME not in first.cookies
    assert (await client.get(f"{AUTH}/me")).status_code == 401


# ---------------------------------------------------------------------------
# Session, CSRF and roles
# ---------------------------------------------------------------------------


async def test_me_reports_the_signed_in_admin(owner: SignedIn, db_client: AsyncClient) -> None:
    """docs/ERRORS.md E13 lived here.

    ``sessions.resolve`` compared a canonical prefix string against the
    ``IPv4Interface`` SQLAlchemy returns for an ``INET`` column. Always unequal, so
    every authenticated request failed as a binding mismatch -- while the model
    annotation claimed ``str``, which is why mypy could not see it.
    """
    response = await db_client.get(f"{AUTH}/me")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(owner.id)
    assert body["email"] == owner.email
    assert body["role"] == AdminRole.OWNER.value
    assert body["status"] == AdminStatus.ACTIVE.value
    assert body["totp_enrolled"] is True
    assert body["recovery_codes_remaining"] == CODE_COUNT
    assert body["csrf_token"] == owner.csrf_token
    assert body["session_id"]


async def test_without_a_cookie_every_authenticated_route_is_401(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    del owner
    client = await new_client()

    assert (await client.get(f"{AUTH}/me")).status_code == 401
    assert (await client.get(f"{AUTH}/sessions")).status_code == 401
    assert (await client.get(ADMINS)).status_code == 401


async def test_a_state_changing_request_without_a_csrf_token_is_refused(
    owner: SignedIn, db_client: AsyncClient, site_address: str
) -> None:
    """F8.AC11. SameSite=Strict already blocks the classic attack; this is depth."""
    response = await db_client.post(
        f"{AUTH}/password",
        json={"current_password": owner.password, "new_password": ANOTHER_PASSWORD},
        headers=helpers.origin_headers(site_address),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_INVALID"


async def test_a_foreign_origin_is_refused_even_with_a_valid_token(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    response = await db_client.post(
        f"{AUTH}/password",
        json={"current_password": owner.password, "new_password": ANOTHER_PASSWORD},
        headers={"Origin": "https://evil.example", "X-CSRF-Token": owner.csrf_token},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_INVALID"


async def test_a_request_with_neither_origin_nor_referer_is_refused(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """The admin API has no non-browser callers, and a browser sends one or other."""
    response = await db_client.post(
        f"{AUTH}/password",
        json={"current_password": owner.password, "new_password": ANOTHER_PASSWORD},
        headers={"X-CSRF-Token": owner.csrf_token},
    )

    assert response.status_code == 403


async def test_safe_methods_need_no_csrf_token(owner: SignedIn, db_client: AsyncClient) -> None:
    del owner
    assert (await db_client.get(f"{AUTH}/me")).status_code == 200


async def test_an_owner_can_list_admins(owner: SignedIn, db_client: AsyncClient) -> None:
    response = await db_client.get(ADMINS)

    assert response.status_code == 200
    assert str(owner.id) in {row["id"] for row in response.json()}


async def test_an_analyst_cannot_reach_an_owner_only_route(
    integration_settings: Settings, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    """Authorisation is checked server-side on every route (F8.AC12).

    The frontend reflects the role; it never decides it.
    """
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    client = await new_client()
    analyst = await helpers.enroll(client, invited, totp_clock)

    listed = await client.get(ADMINS)
    created = await client.post(
        ADMINS,
        json={"email": helpers.new_email(), "display_name": "Nope"},
        headers=analyst.headers(),
    )

    assert listed.status_code == 403
    assert listed.json()["code"] == "FORBIDDEN_ROLE"
    assert created.status_code == 403


async def test_logout_revokes_the_session_immediately(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    response = await db_client.post(f"{AUTH}/logout", headers=owner.headers())

    assert response.status_code == 204
    assert (await db_client.get(f"{AUTH}/me")).status_code == 401
    assert await helpers.live_session_count(owner.id) == 0


async def test_a_session_can_be_listed_and_revoked(
    owner: SignedIn, db_client: AsyncClient, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    second = await new_client()
    await helpers.sign_in(
        second,
        email=owner.email,
        password=owner.password,
        secret=owner.secret,
        admin_id=owner.id,
        clock=totp_clock,
    )

    listed = await db_client.get(f"{AUTH}/sessions")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 2
    assert sum(1 for row in rows if row["current"]) == 1

    other_id = next(row["id"] for row in rows if not row["current"])
    revoked = await db_client.delete(f"{AUTH}/sessions/{other_id}", headers=owner.headers())

    assert revoked.status_code == 204
    assert (await second.get(f"{AUTH}/me")).status_code == 401
    assert (await db_client.get(f"{AUTH}/me")).status_code == 200, "our own session survives"


async def test_one_admin_cannot_revoke_another_admins_session(
    owner: SignedIn,
    db_client: AsyncClient,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """Otherwise an analyst could disrupt an owner at will."""
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst_client = await new_client()
    analyst = await helpers.enroll(analyst_client, invited, totp_clock)

    own_session = (await db_client.get(f"{AUTH}/sessions")).json()[0]["id"]
    response = await analyst_client.delete(
        f"{AUTH}/sessions/{own_session}", headers=analyst.headers()
    )

    assert response.status_code == 404, "another admin's session must not even be visible"
    assert (await db_client.get(f"{AUTH}/me")).status_code == 200
    del owner


async def test_a_malformed_session_id_is_a_404_not_a_500(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    response = await db_client.delete(f"{AUTH}/sessions/not-a-uuid", headers=owner.headers())
    assert response.status_code == 404


async def test_disabling_an_account_takes_effect_on_the_next_request(
    owner: SignedIn,
    db_client: AsyncClient,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """Not at session expiry -- otherwise "disabled" means nothing for 12 hours."""
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst_client = await new_client()
    analyst = await helpers.enroll(analyst_client, invited, totp_clock)
    assert (await analyst_client.get(f"{AUTH}/me")).status_code == 200

    disabled = await db_client.patch(
        f"{ADMINS}/{analyst.id}",
        json={"status": AdminStatus.DISABLED.value},
        headers=owner.headers(),
    )

    assert disabled.status_code == 200
    assert (await analyst_client.get(f"{AUTH}/me")).status_code == 401


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------


async def test_changing_a_password_requires_the_current_one(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    response = await db_client.post(
        f"{AUTH}/password",
        json={"current_password": "not-the-password", "new_password": ANOTHER_PASSWORD},
        headers=owner.headers(),
    )

    assert response.status_code == 401


async def test_a_new_password_must_meet_the_policy(owner: SignedIn, db_client: AsyncClient) -> None:
    response = await db_client.post(
        f"{AUTH}/password",
        json={"current_password": owner.password, "new_password": "password123"},
        headers=owner.headers(),
    )

    assert response.status_code == 422
    assert "TOO_COMMON" in {error["code"] for error in response.json()["errors"]}


async def test_a_password_built_from_the_account_name_is_refused(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """The first thing an attacker tries against this specific deployment."""
    local_part = owner.email.split("@", 1)[0]
    response = await db_client.post(
        f"{AUTH}/password",
        json={"current_password": owner.password, "new_password": f"{local_part}-winter-88"},
        headers=owner.headers(),
    )

    assert response.status_code == 422
    assert "CONTAINS_IDENTIFIER" in {error["code"] for error in response.json()["errors"]}


async def test_changing_a_password_revokes_every_other_session(
    owner: SignedIn, db_client: AsyncClient, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    """The one action a victim takes believing it locks an attacker out.

    Without this, an attacker holding a live session keeps it.
    """
    other = await new_client()
    await helpers.sign_in(
        other,
        email=owner.email,
        password=owner.password,
        secret=owner.secret,
        admin_id=owner.id,
        clock=totp_clock,
    )
    assert (await other.get(f"{AUTH}/me")).status_code == 200

    changed = await db_client.post(
        f"{AUTH}/password",
        json={"current_password": owner.password, "new_password": ANOTHER_PASSWORD},
        headers=owner.headers(),
    )

    assert changed.status_code == 204
    assert (await other.get(f"{AUTH}/me")).status_code == 401
    assert (await db_client.get(f"{AUTH}/me")).status_code == 200, "the current session is kept"


async def test_the_old_password_stops_working_and_the_new_one_starts(
    owner: SignedIn, db_client: AsyncClient, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    changed = await db_client.post(
        f"{AUTH}/password",
        json={"current_password": owner.password, "new_password": ANOTHER_PASSWORD},
        headers=owner.headers(),
    )
    assert changed.status_code == 204

    client = await new_client()
    with_old = await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": owner.password}
    )
    assert with_old.status_code == 401

    csrf = await helpers.sign_in(
        client,
        email=owner.email,
        password=ANOTHER_PASSWORD,
        secret=owner.secret,
        admin_id=owner.id,
        clock=totp_clock,
    )
    assert csrf


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------


async def test_a_recovery_code_signs_in_bypassing_both_factors(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    """That is what a recovery code is for, and why it is Argon2-hashed."""
    client = await new_client()

    response = await client.post(
        f"{AUTH}/recovery-code", json={"email": owner.email, "code": owner.recovery_codes[0]}
    )

    assert response.status_code == 204
    assert response.headers["x-recovery-remaining"] == str(CODE_COUNT - 1)
    assert (await client.get(f"{AUTH}/me")).status_code == 200


async def test_the_remaining_count_decrements_and_a_spent_code_is_refused(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    first = await new_client()
    used = await first.post(
        f"{AUTH}/recovery-code", json={"email": owner.email, "code": owner.recovery_codes[0]}
    )
    assert used.headers["x-recovery-remaining"] == str(CODE_COUNT - 1)

    second = await new_client()
    replayed = await second.post(
        f"{AUTH}/recovery-code", json={"email": owner.email, "code": owner.recovery_codes[0]}
    )
    assert replayed.status_code == 401

    third = await new_client()
    next_code = await third.post(
        f"{AUTH}/recovery-code", json={"email": owner.email, "code": owner.recovery_codes[1]}
    )
    assert next_code.headers["x-recovery-remaining"] == str(CODE_COUNT - 2)


async def test_a_code_typed_in_lower_case_with_spaces_is_accepted(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    """A code is read off paper months later by someone who has lost access.

    Insisting on exact formatting would turn a valid code into a lockout.
    """
    client = await new_client()
    mangled = owner.recovery_codes[0].replace("-", " ").lower()

    response = await client.post(
        f"{AUTH}/recovery-code", json={"email": owner.email, "code": f" {mangled} "}
    )

    assert response.status_code == 204


async def test_regenerating_codes_invalidates_the_old_set(
    owner: SignedIn, db_client: AsyncClient, new_client: ClientFactory
) -> None:
    """Otherwise a leaked old code stays valid after the admin "rotated" them."""
    regenerated = await db_client.post(f"{AUTH}/totp/regenerate-codes", headers=owner.headers())

    assert regenerated.status_code == 200
    fresh = regenerated.json()
    assert len(fresh) == CODE_COUNT
    assert not set(fresh) & set(owner.recovery_codes)

    client = await new_client()
    stale = await client.post(
        f"{AUTH}/recovery-code", json={"email": owner.email, "code": owner.recovery_codes[0]}
    )
    assert stale.status_code == 401

    another = await new_client()
    assert (
        await another.post(f"{AUTH}/recovery-code", json={"email": owner.email, "code": fresh[0]})
    ).status_code == 204


# ---------------------------------------------------------------------------
# Password reset over the Telegram channel
# ---------------------------------------------------------------------------


async def test_a_reset_request_for_an_unknown_account_still_returns_202(
    db_client: AsyncClient,
) -> None:
    """F8.AC10. Reporting "no such account" here is an enumeration oracle."""
    response = await db_client.post(
        f"{AUTH}/reset/request", json={"email": helpers.new_email("absent")}
    )
    assert response.status_code == 202


async def test_a_reset_request_without_a_verified_chat_is_audited_as_undelivered(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """The caller still sees 202; the truth goes to the audit log."""
    response = await db_client.post(f"{AUTH}/reset/request", json={"email": owner.email})

    assert response.status_code == 202
    details = await helpers.audit_details(owner.id, "admin.password_reset_requested")
    assert details
    assert details[-1]["delivered"] is False
    assert details[-1]["reason"] == "no_verified_channel"


async def test_a_reset_token_enforces_the_password_policy_without_burning_itself(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """docs/ERRORS.md E15, the reset half.

    A rejected password used to consume the link, which on a time-limited reset
    means asking for another one and waiting for it to arrive.
    """
    token = await helpers.seed_reset_token(owner.id)

    rejected = await db_client.post(
        f"{AUTH}/reset/confirm", json={"token": token, "new_password": "password123"}
    )
    assert rejected.status_code == 422

    accepted = await db_client.post(
        f"{AUTH}/reset/confirm", json={"token": token, "new_password": ANOTHER_PASSWORD}
    )
    assert accepted.status_code == 204, "the same link must still work"


async def test_a_completed_reset_is_single_use_and_revokes_every_session(
    owner: SignedIn, db_client: AsyncClient, new_client: ClientFactory
) -> None:
    token = await helpers.seed_reset_token(owner.id)

    completed = await db_client.post(
        f"{AUTH}/reset/confirm", json={"token": token, "new_password": ANOTHER_PASSWORD}
    )
    assert completed.status_code == 204
    assert await helpers.live_session_count(owner.id) == 0
    assert (await db_client.get(f"{AUTH}/me")).status_code == 401

    replayed = await db_client.post(
        f"{AUTH}/reset/confirm", json={"token": token, "new_password": STRONG_PASSWORD}
    )
    assert replayed.status_code == 404

    client = await new_client()
    with_old = await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": owner.password}
    )
    assert with_old.status_code == 401


async def test_totp_is_still_required_after_a_reset(
    owner: SignedIn, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    """A reset restores the password, not the second factor.

    If a Telegram compromise alone granted a session, Telegram would effectively
    be the only credential (RISKS R18).
    """
    token = await helpers.seed_reset_token(owner.id)
    client = await new_client()
    await client.post(
        f"{AUTH}/reset/confirm", json={"token": token, "new_password": ANOTHER_PASSWORD}
    )

    first = await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": ANOTHER_PASSWORD}
    )
    assert first.status_code == 200
    assert "mfa_token" in first.json()
    assert (await client.get(f"{AUTH}/me")).status_code == 401, "no session before step 2"

    second = await client.post(
        f"{AUTH}/mfa",
        json={
            "mfa_token": first.json()["mfa_token"],
            "code": await totp_clock.code(owner.secret, owner.id),
        },
    )
    assert second.status_code == 204


async def test_an_expired_reset_token_is_refused(owner: SignedIn, db_client: AsyncClient) -> None:
    token = await helpers.seed_reset_token(owner.id, ttl_minutes=-1)

    response = await db_client.post(
        f"{AUTH}/reset/confirm", json={"token": token, "new_password": ANOTHER_PASSWORD}
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Telegram chat verification
# ---------------------------------------------------------------------------


async def test_a_chat_is_only_trusted_once_the_code_it_received_is_confirmed(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """Proving reachability before trusting the chat for recovery.

    An unverified chat id means reset links go somewhere that may not be the
    admin's -- and they would only find out when they needed it.
    """
    await helpers.seed_chat_verification(owner.id, chat_id=424242, code="135790")

    wrong = await db_client.post(
        f"{AUTH}/telegram/verify/confirm", json={"code": "111111"}, headers=owner.headers()
    )
    assert wrong.status_code == 401
    assert (await helpers.reload_admin(owner.id)).telegram_verified_at is None

    right = await db_client.post(
        f"{AUTH}/telegram/verify/confirm", json={"code": "135790"}, headers=owner.headers()
    )
    assert right.status_code == 204

    admin = await helpers.reload_admin(owner.id)
    assert admin.telegram_chat_id == 424242
    assert admin.telegram_verified_at is not None
    assert "admin.telegram_verified" in await helpers.audit_actions(owner.id)


async def test_confirming_with_no_pending_verification_is_refused(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    response = await db_client.post(
        f"{AUTH}/telegram/verify/confirm", json={"code": "135790"}, headers=owner.headers()
    )
    assert response.status_code == 401


async def test_a_chat_verification_code_is_single_use(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    await helpers.seed_chat_verification(owner.id, chat_id=424242, code="135790")
    payload = {"code": "135790"}

    assert (
        await db_client.post(
            f"{AUTH}/telegram/verify/confirm", json=payload, headers=owner.headers()
        )
    ).status_code == 204
    replayed = await db_client.post(
        f"{AUTH}/telegram/verify/confirm", json=payload, headers=owner.headers()
    )
    assert replayed.status_code == 401


async def test_a_verified_chat_makes_the_reset_link_deliverable(
    owner: SignedIn, db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delivery branch, with the Telegram call intercepted.

    The real send was verified by hand against the live bot; the permanent suite
    must not message a real chat, and CI has neither a token nor a network path to
    Telegram. Intercepting one HTTP client is not mocking the database (ES3) -- what
    is under test here is that a *verified* chat turns a reset request into a live
    token and an audit row saying it was delivered.
    """
    sent: list[dict[str, object]] = []

    async def capture(**kwargs: object) -> telegram.SendResult:
        sent.append(kwargs)
        chat_id = kwargs["chat_id"]
        assert isinstance(chat_id, int)
        return telegram.SendResult(message_id=1, chat_id=chat_id)

    monkeypatch.setattr(telegram, "send_message", capture)

    await helpers.seed_chat_verification(owner.id, chat_id=424242, code="135790")
    await db_client.post(
        f"{AUTH}/telegram/verify/confirm", json={"code": "135790"}, headers=owner.headers()
    )

    response = await db_client.post(f"{AUTH}/reset/request", json={"email": owner.email})

    assert response.status_code == 202
    assert len(sent) == 1
    assert sent[0]["chat_id"] == 424242
    body = sent[0]["text"]
    assert isinstance(body, str)
    assert "https://localhost/reset?token=" in body
    assert "authenticator" in body, "the message must say the second factor is still needed"

    details = await helpers.audit_details(owner.id, "admin.password_reset_requested")
    assert details[-1]["delivered"] is True
    assert await helpers.live_reset_token_count(owner.id) == 1


# ---------------------------------------------------------------------------
# Enumeration resistance (F8.AC10)
# ---------------------------------------------------------------------------


async def test_a_known_and_an_unknown_account_are_indistinguishable(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    known_client = await new_client()
    unknown_client = await new_client()

    known = await known_client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": "wrong-password-entirely"}
    )
    unknown = await unknown_client.post(
        f"{AUTH}/login",
        json={"email": helpers.new_email("absent"), "password": "wrong-password-entirely"},
    )

    assert known.status_code == unknown.status_code == 401
    assert known.json()["code"] == unknown.json()["code"]
    assert known.json()["detail"] == unknown.json()["detail"]
    assert known.json()["title"] == unknown.json()["title"]


async def test_login_timing_does_not_reveal_whether_an_account_exists(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    """The half of F8.AC10 that identical response bodies cannot provide.

    Without the dummy Argon2 verification, a wrong password costs ~50 ms while an
    unknown address returns immediately. Measured at 1.01x by hand; the bound here
    is loose enough for a shared CI runner but tight enough to catch a missing
    dummy verify, which shows up as a ratio in the tens.
    """

    async def attempt(email: str) -> float:
        client = await new_client()
        started = time.perf_counter()
        response = await client.post(
            f"{AUTH}/login", json={"email": email, "password": "wrong-password-entirely"}
        )
        elapsed = time.perf_counter() - started
        assert response.status_code == 401
        await helpers.clear_rate_limits()
        return elapsed

    known = min([await attempt(owner.email) for _ in range(3)])
    unknown = min([await attempt(helpers.new_email("absent")) for _ in range(3)])

    ratio = max(known, unknown) / min(known, unknown)
    assert ratio < 3.0, f"timing differed by {ratio:.2f}x; account-enumeration oracle"


# ---------------------------------------------------------------------------
# Rate limiting (F8.AC9)
# ---------------------------------------------------------------------------


async def test_the_login_limiter_engages_after_the_burst_allowance(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    """The one test that deliberately leaves the limiter armed.

    State lives in PostgreSQL rather than process memory: two workers with
    per-process buckets would silently grant double every limit, which is worse
    than no limit because it looks like it works (ADR-0010).
    """
    client = await new_client()
    statuses: list[int] = []

    for _ in range(9):
        response = await client.post(
            f"{AUTH}/login", json={"email": owner.email, "password": "wrong-password-entirely"}
        )
        statuses.append(response.status_code)
        if response.status_code == 429:
            assert response.json()["code"] == "RATE_LIMITED"
            assert int(response.headers["retry-after"]) > 0
            break

    assert 401 in statuses, "the first attempts must reach the auth path"
    assert statuses[-1] == 429, f"limiter never engaged: {statuses}"


async def test_the_limiter_is_keyed_per_identifier(
    owner: SignedIn,
    integration_settings: Settings,
    new_client: ClientFactory,
    totp_clock: TotpClock,
) -> None:
    """One account being hammered must not lock a colleague out."""
    invited = await helpers.invite(integration_settings, role=AdminRole.ANALYST)
    analyst_client = await new_client()
    analyst = await helpers.enroll(analyst_client, invited, totp_clock)

    client = await new_client()
    for _ in range(6):
        await client.post(
            f"{AUTH}/login", json={"email": owner.email, "password": "wrong-password-entirely"}
        )

    other = await new_client()
    response = await other.post(
        f"{AUTH}/login", json={"email": analyst.email, "password": analyst.password}
    )
    assert response.status_code == 200, "a different identifier has its own bucket"


# ---------------------------------------------------------------------------
# Account lockout
# ---------------------------------------------------------------------------


async def test_repeated_failures_lock_the_account(
    owner: SignedIn, new_client: ClientFactory
) -> None:
    """Separate from rate limiting: the limiter throttles a network, the lockout
    protects one identity from a distributed attempt that stays under the limit.
    """
    client = await new_client()

    for _ in range(MAX_FAILED_LOGINS):
        await client.post(
            f"{AUTH}/login", json={"email": owner.email, "password": "wrong-password-entirely"}
        )
        # The limiter would answer 429 long before the counter reaches its
        # threshold, and this test is about the counter.
        await helpers.clear_rate_limits()

    admin = await helpers.reload_admin(owner.id)
    assert admin.failed_login_count >= MAX_FAILED_LOGINS
    assert admin.locked_until is not None

    locked = await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": owner.password}
    )
    assert locked.status_code == 423
    assert locked.json()["code"] == "ACCOUNT_LOCKED"
    assert "admin.login_locked" in await helpers.audit_actions(owner.id)


async def test_a_successful_login_clears_the_failure_counter(
    owner: SignedIn, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    """Otherwise the legitimate user stays punished for their own typos."""
    client = await new_client()
    await client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": "wrong-password-entirely"}
    )
    assert (await helpers.reload_admin(owner.id)).failed_login_count == 1

    await helpers.clear_rate_limits()
    await helpers.sign_in(
        client,
        email=owner.email,
        password=owner.password,
        secret=owner.secret,
        admin_id=owner.id,
        clock=totp_clock,
    )

    assert (await helpers.reload_admin(owner.id)).failed_login_count == 0


# ---------------------------------------------------------------------------
# Owner-only admin lifecycle
# ---------------------------------------------------------------------------


async def test_an_owner_invites_without_setting_a_password(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    """F8.AC15. No default password exists anywhere in this system.

    An owner cannot set a colleague's password, so an owner cannot impersonate one.
    """
    email = helpers.new_email("invited")

    response = await db_client.post(
        ADMINS,
        json={"email": email, "display_name": "Invited Analyst", "role": AdminRole.ANALYST.value},
        headers=owner.headers(),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["enrollment_url"].startswith("https://")
    assert body["admin"]["status"] == AdminStatus.PENDING_ENROLLMENT.value
    assert body["admin"]["totp_enrolled"] is False

    invited_id = uuid.UUID(body["admin"]["id"])
    assert (await helpers.reload_admin(invited_id)).password_hash is None


async def test_inviting_a_duplicate_address_is_a_422(
    owner: SignedIn, db_client: AsyncClient
) -> None:
    response = await db_client.post(
        ADMINS,
        json={"email": owner.email, "display_name": "Duplicate"},
        headers=owner.headers(),
    )

    assert response.status_code == 422
    assert "ALREADY_EXISTS" in {error["code"] for error in response.json()["errors"]}


async def test_an_invited_admin_can_complete_the_whole_flow(
    owner: SignedIn, db_client: AsyncClient, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    """The invite-to-signed-in path, through the API the owner actually uses."""
    created = await db_client.post(
        ADMINS,
        json={"email": helpers.new_email("invited"), "display_name": "Invited"},
        headers=owner.headers(),
    )
    body = created.json()
    invited = Invited(
        id=uuid.UUID(body["admin"]["id"]),
        email=body["admin"]["email"],
        role=AdminRole.ANALYST,
        token=helpers.token_from(body["enrollment_url"]),
        enrollment_url=body["enrollment_url"],
    )

    client = await new_client()
    signed_in = await helpers.enroll(client, invited, totp_clock)

    assert (await client.get(f"{AUTH}/me")).json()["role"] == AdminRole.ANALYST.value
    assert signed_in.recovery_codes


async def test_a_fresh_enrollment_link_can_be_reissued(
    owner: SignedIn, db_client: AsyncClient, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    """The 24-hour link expires, and an invitee who missed it needs another."""
    created = await db_client.post(
        ADMINS,
        json={"email": helpers.new_email("invited"), "display_name": "Invited"},
        headers=owner.headers(),
    )
    admin_id = created.json()["admin"]["id"]

    reissued = await db_client.post(
        f"{ADMINS}/{admin_id}/enrollment-token", headers=owner.headers()
    )

    assert reissued.status_code == 201
    assert reissued.json()["url"].startswith("https://")

    client = await new_client()
    signed_in = await helpers.enroll(
        client,
        Invited(
            id=uuid.UUID(admin_id),
            email=created.json()["admin"]["email"],
            role=AdminRole.ANALYST,
            token=helpers.token_from(reissued.json()["url"]),
            enrollment_url=reissued.json()["url"],
        ),
        totp_clock,
    )
    assert signed_in.id == uuid.UUID(admin_id)


async def test_an_unknown_admin_id_is_a_404(owner: SignedIn, db_client: AsyncClient) -> None:
    assert (await db_client.get(f"{ADMINS}/{uuid.uuid4()}")).status_code == 404
    assert (await db_client.get(f"{ADMINS}/not-a-uuid")).status_code == 404
    del owner


async def test_a_no_op_patch_changes_nothing(owner: SignedIn, db_client: AsyncClient) -> None:
    before = await helpers.audit_actions(owner.id)

    response = await db_client.patch(f"{ADMINS}/{owner.id}", json={}, headers=owner.headers())

    assert response.status_code == 200
    assert await helpers.audit_actions(owner.id) == before


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_starting_a_second_sign_in_invalidates_the_first(
    owner: SignedIn, new_client: ClientFactory, totp_clock: TotpClock
) -> None:
    """``challenges.issue`` replaces any outstanding challenge of the same kind.

    Without that, two concurrent challenges for one account both stay valid and the
    window for using an abandoned one stays open for its full five minutes.
    """
    first_client = await new_client()
    second_client = await new_client()

    first = await first_client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": owner.password}
    )
    second = await second_client.post(
        f"{AUTH}/login", json={"email": owner.email, "password": owner.password}
    )
    assert first.status_code == second.status_code == 200

    abandoned = await first_client.post(
        f"{AUTH}/mfa",
        json={
            "mfa_token": first.json()["mfa_token"],
            "code": await totp_clock.code(owner.secret, owner.id),
        },
    )
    assert abandoned.status_code == 401

    current = await second_client.post(
        f"{AUTH}/mfa",
        json={
            "mfa_token": second.json()["mfa_token"],
            "code": await totp_clock.code(owner.secret, owner.id),
        },
    )
    assert current.status_code == 204


async def test_a_telegram_rejection_blames_the_chat_id_not_the_server(
    owner: SignedIn, db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/ERRORS.md E20.

    Telegram refusing outright — a wrong chat id, or a bot the admin has never
    messaged — is the operator's input to fix, and they are sitting in front of the
    field that holds it. Reporting it as `500 Internal error` tells them nothing and
    points them at the wrong thing.
    """

    async def refuse(**kwargs: object) -> telegram.SendResult:
        del kwargs
        raise telegram.TelegramError("chat not found", permanent=True)

    monkeypatch.setattr(telegram, "send_message", refuse)

    response = await db_client.post(
        f"{AUTH}/telegram/verify/start", json={"chat_id": 1}, headers=owner.headers()
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert {error["field"] for error in body["errors"]} == {"chat_id"}
    assert "sent the bot a message first" in body["errors"][0]["message"]


async def test_an_unreachable_telegram_is_a_503_that_says_so(
    owner: SignedIn, db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transport failure is the server's problem, and retrying may fix it.

    This is the shape the Norton TLS-interception outage took: every outbound
    connection failing certificate verification, surfacing as an unhandled
    RuntimeError and therefore as a bare 500.
    """

    async def unreachable(**kwargs: object) -> telegram.SendResult:
        del kwargs
        raise telegram.TelegramError("ConnectError: certificate verify failed")

    monkeypatch.setattr(telegram, "send_message", unreachable)

    response = await db_client.post(
        f"{AUTH}/telegram/verify/start", json={"chat_id": 424242}, headers=owner.headers()
    )

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "try again" in body["detail"]
    # The detail must survive: the 5xx handler strips nothing for a typed error, and
    # an admin who is told only "Internal error" has no idea whether to retry.
    assert body["trace_id"]


async def test_a_failed_send_leaves_no_half_finished_challenge(
    owner: SignedIn, db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The challenge row is written before the send, so a failure must not leave one.

    Otherwise a later code from a *previous* successful attempt could be confirmed
    against a chat id the admin has since corrected.
    """

    async def refuse(**kwargs: object) -> telegram.SendResult:
        del kwargs
        raise telegram.TelegramError("chat not found", permanent=True)

    monkeypatch.setattr(telegram, "send_message", refuse)

    await db_client.post(
        f"{AUTH}/telegram/verify/start", json={"chat_id": 1}, headers=owner.headers()
    )

    confirmed = await db_client.post(
        f"{AUTH}/telegram/verify/confirm", json={"code": "000000"}, headers=owner.headers()
    )
    assert confirmed.status_code == 401
    assert (await helpers.reload_admin(owner.id)).telegram_chat_id is None
