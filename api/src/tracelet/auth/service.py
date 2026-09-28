"""Authentication use cases.

The routers translate HTTP; this module holds the decisions. Kept separate so the
CLI and the API share one implementation -- the break-glass reset (F8.AC8) must
behave identically to the API path, including writing the same audit row.

Two rules run through everything here:

**No user enumeration, in body or in timing** (F8.AC10). Every failure on the login,
reset-request and recovery paths returns the same shape, and an unknown identifier
burns a dummy Argon2 verification so the response time does not reveal existence.

**Every consequential action writes an audit row**, on the same session, so it
commits or rolls back with the action it describes.
"""

from __future__ import annotations

import datetime as dt
import re
import secrets
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.audit import log as audit
from tracelet.auth import challenges, recovery, sessions, totp
from tracelet.auth.challenges import ChallengeKind
from tracelet.auth.models import (
    Admin,
    AdminRole,
    AdminStatus,
    EnrollmentToken,
    PasswordResetToken,
)
from tracelet.config import Settings
from tracelet.crypto.envelope import Envelope
from tracelet.crypto.hashing import (
    argon2_parameters,
    constant_time_equals,
    hash_password,
    needs_rehash,
    new_token,
    sha256_bytes,
    verify_dummy_password,
    verify_password,
)
from tracelet.errors import (
    AccountLocked,
    DependencyUnavailable,
    MfaInvalid,
    NotFound,
    Unauthenticated,
    ValidationFailed,
)
from tracelet.notify import telegram

log = structlog.get_logger(__name__)

ENROLLMENT_TTL = dt.timedelta(hours=24)
RESET_TTL = dt.timedelta(minutes=30)
MFA_TOKEN_TTL = dt.timedelta(minutes=5)
# Longer than the MFA window: before the admin can produce a code they have to
# add the account to an authenticator app and save ten recovery codes.
ENROLLMENT_CONFIRM_TTL = dt.timedelta(minutes=20)

# After this many consecutive failures the account locks for LOCKOUT_DURATION.
# Separate from rate limiting: the limiter throttles a network, the lockout protects
# one identity from a distributed attempt that stays under the per-prefix limit.
MAX_FAILED_LOGINS = 8
LOCKOUT_DURATION = dt.timedelta(minutes=30)

MIN_PASSWORD_LENGTH = 12


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# ---------------------------------------------------------------------------
# Password policy (F8.AC17)
# ---------------------------------------------------------------------------

# Not a curated breach corpus -- that would be a large data file for little gain at
# two admins. These are the values people actually reach for when a form demands
# twelve characters.
_OBVIOUS_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "passw0rd",
        "changeme",
        "letmein",
        "welcome1",
        "admin123",
        "qwertyuiop",
        "123456789012",
        "iloveyou",
        "tracelet",
        "tracelet123",
        "administrator",
    }
)


# Four characters, so short fragments such as "rk" or "dev" do not make ordinary
# passphrases unusable.
_MIN_TOKEN_LENGTH = 4


def _tokenise(value: str) -> set[str]:
    """Split on anything that is not a letter or digit, keeping useful fragments."""
    return {
        token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) >= _MIN_TOKEN_LENGTH
    }


def validate_password(password: str, *, email: str, site_address: str) -> None:
    """Raise :class:`ValidationFailed` with a field-level error, or return."""
    from tracelet.errors import FieldError  # noqa: PLC0415 - avoids a module import cycle

    errors: list[FieldError] = []
    lowered = password.lower()

    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(
            FieldError(
                field="new_password",
                code="TOO_SHORT",
                message=f"Must be at least {MIN_PASSWORD_LENGTH} characters.",
            )
        )
    if lowered in _OBVIOUS_PASSWORDS:
        errors.append(
            FieldError(
                field="new_password",
                code="TOO_COMMON",
                message="This is a commonly used password. Choose something else.",
            )
        )
    # A password built from the account name or the site name is the first thing
    # an attacker tries against this specific deployment.
    #
    # Tokenised, not substring-matched: swaroop.rayker@... and
    # "swaroop-rayker-secret" share no substring because the separators differ,
    # and swapping a dot for a hyphen is exactly the variation people reach for.
    password_tokens = _tokenise(lowered)
    identifier_tokens = _tokenise(email.split("@", 1)[0])
    if identifier_tokens & password_tokens:
        errors.append(
            FieldError(
                field="new_password",
                code="CONTAINS_IDENTIFIER",
                message="Must not contain your account name.",
            )
        )
    host = site_address.split(":", 1)[0].lower()
    if host and host != "localhost":
        # Only the registrable part: a password containing "com" is not a problem.
        site_tokens = _tokenise(host.split(".")[0])
        if site_tokens & password_tokens:
            errors.append(
                FieldError(
                    field="new_password",
                    code="CONTAINS_SITE_NAME",
                    message="Must not contain the site name.",
                )
            )
    if len(set(password)) < 5:
        errors.append(
            FieldError(
                field="new_password",
                code="TOO_REPETITIVE",
                message="Uses too few distinct characters.",
            )
        )

    if errors:
        raise ValidationFailed("Password does not meet the policy.", errors=errors)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


async def get_by_email(db: AsyncSession, email: str) -> Admin | None:
    return (
        await db.execute(select(Admin).where(Admin.email == email.strip()))
    ).scalar_one_or_none()


async def get_by_id(db: AsyncSession, admin_id: uuid.UUID) -> Admin:
    admin = (await db.execute(select(Admin).where(Admin.id == admin_id))).scalar_one_or_none()
    if admin is None:
        raise NotFound("No such admin.")
    return admin


async def count_active_owners(db: AsyncSession, *, excluding: uuid.UUID | None = None) -> int:
    stmt = (
        select(func.count())
        .select_from(Admin)
        .where(Admin.role == AdminRole.OWNER, Admin.status == AdminStatus.ACTIVE)
    )
    if excluding is not None:
        stmt = stmt.where(Admin.id != excluding)
    return int((await db.execute(stmt)).scalar_one())


# ---------------------------------------------------------------------------
# Step 1: password
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MfaChallenge:
    """Issued after a correct password. Not yet a session."""

    token: str
    expires_at: dt.datetime
    admin_id: uuid.UUID

    def __repr__(self) -> str:
        return f"MfaChallenge(admin_id={self.admin_id}, token=<redacted>)"


# Multi-step auth state lives in the database, never in process memory. There are
# two Gunicorn workers, so a module-level dict made login a coin flip depending on
# which worker received the follow-up request (docs/ERRORS.md E10).
# See tracelet.auth.challenges.


async def authenticate_password(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    ip: str | None,
    trace_id: str | None,
) -> MfaChallenge:
    """Verify the password and issue an MFA challenge.

    Raises :class:`Unauthenticated` for every failure -- wrong password, unknown
    account, disabled account, enrollment incomplete. The caller cannot tell which,
    and neither can an attacker.
    """
    admin = await get_by_email(db, email)
    prefix = sessions.prefix_of(ip)

    if admin is None:
        # Equalise timing against the real Argon2 verification below (F8.AC10).
        verify_dummy_password()
        await audit.record(
            db,
            action=audit.Action.LOGIN_FAILED,
            actor_ip_prefix=prefix,
            trace_id=trace_id,
            detail={"reason": "unknown_identifier"},
            independent=True,
        )
        raise Unauthenticated("Incorrect credentials.")

    if admin.locked_until is not None and admin.locked_until > now():
        await audit.record(
            db,
            action=audit.Action.LOGIN_LOCKED,
            actor_admin_id=admin.id,
            actor_ip_prefix=prefix,
            trace_id=trace_id,
            independent=True,
        )
        retry_seconds = int((admin.locked_until - now()).total_seconds())
        raise AccountLocked(f"Too many failed attempts. Try again in {retry_seconds} seconds.")

    if admin.password_hash is None or admin.status is not AdminStatus.ACTIVE:
        # Enrollment incomplete or account disabled. Still burn the time, so the
        # response is indistinguishable from a wrong password.
        verify_dummy_password()
        await audit.record(
            db,
            action=audit.Action.LOGIN_FAILED,
            actor_admin_id=admin.id,
            actor_ip_prefix=prefix,
            trace_id=trace_id,
            detail={"reason": f"status_{admin.status.value}"},
            independent=True,
        )
        raise Unauthenticated("Incorrect credentials.")

    if not verify_password(admin.password_hash, password):
        failed = admin.failed_login_count + 1
        values: dict[str, object] = {"failed_login_count": failed}
        if failed >= MAX_FAILED_LOGINS:
            values["locked_until"] = now() + LOCKOUT_DURATION
        await db.execute(update(Admin).where(Admin.id == admin.id).values(**values))
        await audit.record(
            db,
            action=audit.Action.LOGIN_FAILED,
            actor_admin_id=admin.id,
            actor_ip_prefix=prefix,
            trace_id=trace_id,
            detail={"reason": "bad_password", "failed_count": failed},
            independent=True,
        )
        raise Unauthenticated("Incorrect credentials.")

    # Correct. Clear the failure counter and upgrade the hash if the policy moved on.
    values = {"failed_login_count": 0, "locked_until": None}
    if needs_rehash(admin.password_hash):
        values["password_hash"] = hash_password(password)
        values["password_params"] = argon2_parameters()
        log.info("password_rehashed", admin_id=str(admin.id))
    await db.execute(update(Admin).where(Admin.id == admin.id).values(**values))

    issued = await challenges.issue(
        db, kind=ChallengeKind.MFA, admin_id=admin.id, ttl=MFA_TOKEN_TTL
    )
    return MfaChallenge(token=issued.token, expires_at=issued.expires_at, admin_id=admin.id)


# ---------------------------------------------------------------------------
# Step 2: TOTP
# ---------------------------------------------------------------------------


async def complete_mfa(
    db: AsyncSession,
    settings: Settings,
    *,
    mfa_token: str,
    code: str,
    ip: str | None,
    user_agent: str | None,
    trace_id: str | None,
) -> tuple[Admin, sessions.IssuedSession]:
    # peek, not consume: mistyping a code must not send the admin back to the
    # password step. The challenge is spent only once a code is accepted.
    claim = await challenges.peek(db, kind=ChallengeKind.MFA, token=mfa_token)
    if claim is None:
        raise MfaInvalid("The sign-in attempt has expired. Start again.")

    admin = await get_by_id(db, claim.admin_id)
    if admin.totp_secret_enc is None or admin.totp_key_version is None:
        raise MfaInvalid("Two-factor authentication is not set up for this account.")

    result = totp.verify(
        sealed=Envelope(key_version=admin.totp_key_version, payload=admin.totp_secret_enc),
        admin_id=admin.id,
        code=code,
        last_counter=admin.totp_last_counter,
        key_path=str(settings.ip_key_file),
    )
    if not result.ok:
        await audit.record(
            db,
            action=audit.Action.MFA_FAILED,
            actor_admin_id=admin.id,
            actor_ip_prefix=sessions.prefix_of(ip),
            trace_id=trace_id,
            detail={"reason": result.reason},
            independent=True,
        )
        # "replayed" is reported as invalid on purpose: telling a caller their code
        # was correct-but-already-used confirms they hold a real code.
        raise MfaInvalid("Invalid or reused code.")

    # Consume the challenge and record the accepted step, without which replay
    # prevention does nothing.
    await challenges.consume(db, kind=ChallengeKind.MFA, token=mfa_token)
    await db.execute(
        update(Admin).where(Admin.id == admin.id).values(totp_last_counter=result.step)
    )

    issued = await sessions.create(db, admin_id=admin.id, ip=ip, user_agent=user_agent)
    await audit.record(
        db,
        action=audit.Action.LOGIN_SUCCEEDED,
        actor_admin_id=admin.id,
        actor_ip_prefix=sessions.prefix_of(ip),
        trace_id=trace_id,
        target_type="session",
        target_id=str(issued.session_id),
    )
    return admin, issued


# ---------------------------------------------------------------------------
# Enrollment (F8.AC15)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnrollmentOffer:
    url: str
    expires_at: dt.datetime

    def __repr__(self) -> str:
        return "EnrollmentOffer(url=<redacted>)"


async def issue_enrollment_token(
    db: AsyncSession,
    settings: Settings,
    *,
    admin_id: uuid.UUID,
    created_by: uuid.UUID | None,
    trace_id: str | None = None,
) -> EnrollmentOffer:
    token = new_token(32)
    expires = now() + ENROLLMENT_TTL
    db.add(
        EnrollmentToken(
            admin_id=admin_id,
            token_hash=sha256_bytes(token),
            expires_at=expires,
            created_by=created_by,
        )
    )
    await audit.record(
        db,
        action=audit.Action.ENROLLMENT_TOKEN_ISSUED,
        actor_admin_id=created_by,
        target_type="admin",
        target_id=str(admin_id),
        trace_id=trace_id,
    )
    return EnrollmentOffer(
        url=f"{settings.public_base_url}/enroll?token={token}", expires_at=expires
    )


async def _peek_single_use_token(
    db: AsyncSession, model: type[EnrollmentToken] | type[PasswordResetToken], token: str
) -> uuid.UUID | None:
    """Find a live token WITHOUT spending it.

    Lets the password policy reject a weak choice while leaving the link usable. The
    caller must still call :func:`_consume_single_use_token` before applying any
    change -- this lookup decides nothing about the race.
    """
    return (
        await db.execute(
            select(model.admin_id).where(
                model.token_hash == sha256_bytes(token),
                model.used_at.is_(None),
                model.expires_at > now(),
            )
        )
    ).scalar_one_or_none()


async def _consume_single_use_token(
    db: AsyncSession, model: type[EnrollmentToken] | type[PasswordResetToken], token: str
) -> uuid.UUID | None:
    """Spend a token atomically. Returns the admin id, or None."""
    # A conditional UPDATE rather than SELECT-then-UPDATE: two concurrent submissions
    # of the same link must not both succeed.
    result = await db.execute(
        update(model)
        .where(
            model.token_hash == sha256_bytes(token),
            model.used_at.is_(None),
            model.expires_at > now(),
        )
        .values(used_at=now())
        .returning(model.admin_id)
    )
    row = result.first()
    return row[0] if row else None


@dataclass(frozen=True, slots=True)
class EnrollmentResult:
    admin: Admin
    otpauth_uri: str
    secret: str
    recovery_codes: list[str]
    confirm_token: str

    def __repr__(self) -> str:
        return f"EnrollmentResult(admin_id={self.admin.id}, secrets=<redacted>)"


async def complete_enrollment(
    db: AsyncSession,
    settings: Settings,
    *,
    token: str,
    password: str,
    trace_id: str | None,
) -> EnrollmentResult:
    """Set the first password, enrol TOTP, and issue recovery codes.

    The account only becomes ``active`` once TOTP is *confirmed*, not here -- the
    CHECK constraint on ``admins`` would reject an active row without
    ``totp_enrolled_at`` anyway.
    """
    admin_id = await _peek_single_use_token(db, EnrollmentToken, token)
    if admin_id is None:
        raise NotFound("This enrollment link is invalid or has expired.")

    admin = await get_by_id(db, admin_id)
    # Validated BEFORE the token is spent, so a rejected password does not cost the
    # admin their invite (docs/ERRORS.md E14).
    validate_password(password, email=admin.email, site_address=settings.site_address)

    if await _consume_single_use_token(db, EnrollmentToken, token) is None:
        # Lost a race with a concurrent submission of the same link.
        raise NotFound("This enrollment link is invalid or has expired.")

    enrollment = totp.generate_enrollment(
        admin_id=admin.id, email=admin.email, key_path=str(settings.ip_key_file)
    )
    codes = await recovery.issue(db, admin.id)

    await db.execute(
        update(Admin)
        .where(Admin.id == admin.id)
        .values(
            password_hash=hash_password(password),
            password_params=argon2_parameters(),
            password_changed_at=now(),
            totp_secret_enc=enrollment.sealed.payload,
            totp_key_version=enrollment.sealed.key_version,
            failed_login_count=0,
            locked_until=None,
        )
    )
    await audit.record(
        db,
        action=audit.Action.ENROLLMENT_COMPLETED,
        actor_admin_id=admin.id,
        target_type="admin",
        target_id=str(admin.id),
        trace_id=trace_id,
    )
    await db.refresh(admin)

    # A one-time token bound to THIS admin, required by /totp/confirm.
    #
    # The account has no session yet and cannot have one -- the CHECK constraint
    # forbids `active` without `totp_enrolled_at` -- so confirmation needs some other
    # proof of who is confirming. Taking the admin id from a request header instead
    # would let anyone activate any pending account with a code from their own
    # authenticator, which is a complete authentication bypass.
    issued = await challenges.issue(
        db,
        kind=ChallengeKind.TOTP_CONFIRM,
        admin_id=admin.id,
        ttl=ENROLLMENT_CONFIRM_TTL,
    )
    confirm_token = issued.token

    return EnrollmentResult(
        admin=admin,
        otpauth_uri=enrollment.otpauth_uri,
        secret=enrollment.secret,
        recovery_codes=codes,
        confirm_token=confirm_token,
    )


async def confirm_totp(
    db: AsyncSession,
    settings: Settings,
    *,
    confirm_token: str,
    code: str,
    ip: str | None,
    user_agent: str | None,
    trace_id: str | None,
) -> tuple[Admin, sessions.IssuedSession]:
    """Prove the authenticator works, then activate the account.

    Identified by the one-time token issued at enrolment, never by a caller-supplied
    admin id.
    """
    claim = await challenges.peek(db, kind=ChallengeKind.TOTP_CONFIRM, token=confirm_token)
    if claim is None:
        raise NotFound("This enrolment step has expired. Use the enrollment link again.")

    admin = await get_by_id(db, claim.admin_id)
    if admin.totp_secret_enc is None or admin.totp_key_version is None:
        raise MfaInvalid("No pending two-factor enrolment.")

    result = totp.verify(
        sealed=Envelope(key_version=admin.totp_key_version, payload=admin.totp_secret_enc),
        admin_id=admin.id,
        code=code,
        last_counter=admin.totp_last_counter,
        key_path=str(settings.ip_key_file),
    )
    if not result.ok:
        raise MfaInvalid("Invalid or reused code.")

    await challenges.consume(db, kind=ChallengeKind.TOTP_CONFIRM, token=confirm_token)
    await db.execute(
        update(Admin)
        .where(Admin.id == admin.id)
        .values(
            totp_enrolled_at=now(),
            totp_last_counter=result.step,
            status=AdminStatus.ACTIVE,
        )
    )
    await audit.record(
        db,
        action=audit.Action.TOTP_ENROLLED,
        actor_admin_id=admin.id,
        target_type="admin",
        target_id=str(admin.id),
        trace_id=trace_id,
    )

    # Sign them in. Both factors were just proved: the password at /enroll and a
    # live code here. Asking for a second code would also be refused as a replay,
    # since the step just accepted is now the stored high-water mark.
    await db.refresh(admin)
    issued = await sessions.create(db, admin_id=admin.id, ip=ip, user_agent=user_agent)
    await audit.record(
        db,
        action=audit.Action.LOGIN_SUCCEEDED,
        actor_admin_id=admin.id,
        actor_ip_prefix=sessions.prefix_of(ip),
        trace_id=trace_id,
        target_type="session",
        target_id=str(issued.session_id),
        detail={"via": "enrollment"},
    )
    return admin, issued


# ---------------------------------------------------------------------------
# Password change and reset
# ---------------------------------------------------------------------------


async def change_password(
    db: AsyncSession,
    settings: Settings,
    *,
    admin: Admin,
    current_password: str,
    new_password: str,
    keep_session_id: uuid.UUID | None,
    ip: str | None,
    trace_id: str | None,
) -> int:
    """Change a password, then revoke every other session.

    Revoking is the point: changing a password is the one action a victim takes
    believing it locks an attacker out, and without this an attacker holding a live
    session keeps it.
    """
    if admin.password_hash is None or not verify_password(admin.password_hash, current_password):
        raise Unauthenticated("Current password is incorrect.")

    validate_password(new_password, email=admin.email, site_address=settings.site_address)

    await db.execute(
        update(Admin)
        .where(Admin.id == admin.id)
        .values(
            password_hash=hash_password(new_password),
            password_params=argon2_parameters(),
            password_changed_at=now(),
        )
    )
    revoked = await sessions.revoke_all_for_admin(
        db, admin.id, reason="password_changed", except_id=keep_session_id
    )
    await audit.record(
        db,
        action=audit.Action.PASSWORD_CHANGED,
        actor_admin_id=admin.id,
        actor_ip_prefix=sessions.prefix_of(ip),
        target_type="admin",
        target_id=str(admin.id),
        trace_id=trace_id,
        detail={"sessions_revoked": revoked},
    )
    return revoked


async def request_password_reset(
    db: AsyncSession,
    settings: Settings,
    *,
    email: str,
    ip: str | None,
    trace_id: str | None,
) -> None:
    """Send a reset link over Telegram.

    **Always returns without error**, whether or not the account exists, has a
    verified chat, or Telegram succeeded. The endpoint answers 202 unconditionally
    (F8.AC10) -- reporting "no such account" or "delivery failed" here would be a
    reliable enumeration oracle. Failures go to the log and the audit trail.
    """
    admin = await get_by_email(db, email)
    prefix = sessions.prefix_of(ip)

    if admin is None or admin.telegram_chat_id is None or admin.telegram_verified_at is None:
        await audit.record(
            db,
            action=audit.Action.PASSWORD_RESET_REQUESTED,
            actor_admin_id=admin.id if admin else None,
            actor_ip_prefix=prefix,
            trace_id=trace_id,
            detail={"delivered": False, "reason": "no_verified_channel"},
        )
        log.info("password_reset_no_channel", has_admin=admin is not None)
        return

    token = new_token(32)
    db.add(
        PasswordResetToken(
            admin_id=admin.id,
            token_hash=sha256_bytes(token),
            expires_at=now() + RESET_TTL,
            requested_ip_prefix=prefix,
        )
    )

    url = f"{settings.public_base_url}/reset?token={token}"
    delivered = True
    try:
        await telegram.send_message(
            bot_token=settings.require("telegram_bot_token", "Password reset over Telegram"),
            chat_id=admin.telegram_chat_id,
            text=telegram.password_reset_message(
                display_name=admin.display_name,
                url=url,
                minutes_valid=int(RESET_TTL.total_seconds() // 60),
            ),
        )
    except (telegram.TelegramError, RuntimeError) as exc:
        delivered = False
        log.error("password_reset_delivery_failed", error=str(exc))

    await audit.record(
        db,
        action=audit.Action.PASSWORD_RESET_REQUESTED,
        actor_admin_id=admin.id,
        actor_ip_prefix=prefix,
        trace_id=trace_id,
        detail={"delivered": delivered},
    )


async def confirm_password_reset(
    db: AsyncSession,
    settings: Settings,
    *,
    token: str,
    new_password: str,
    ip: str | None,
    trace_id: str | None,
) -> None:
    admin_id = await _peek_single_use_token(db, PasswordResetToken, token)
    if admin_id is None:
        raise NotFound("This reset link is invalid or has expired.")

    admin = await get_by_id(db, admin_id)
    # Validated BEFORE the token is spent: being told the password is too weak must
    # not also invalidate the link (docs/ERRORS.md E14).
    validate_password(new_password, email=admin.email, site_address=settings.site_address)

    if await _consume_single_use_token(db, PasswordResetToken, token) is None:
        raise NotFound("This reset link is invalid or has expired.")

    await db.execute(
        update(Admin)
        .where(Admin.id == admin.id)
        .values(
            password_hash=hash_password(new_password),
            password_params=argon2_parameters(),
            password_changed_at=now(),
            failed_login_count=0,
            locked_until=None,
        )
    )
    revoked = await sessions.revoke_all_for_admin(db, admin.id, reason="password_reset")
    await audit.record(
        db,
        action=audit.Action.PASSWORD_RESET_COMPLETED,
        actor_admin_id=admin.id,
        actor_ip_prefix=sessions.prefix_of(ip),
        target_type="admin",
        target_id=str(admin.id),
        trace_id=trace_id,
        detail={"sessions_revoked": revoked},
    )


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------


async def authenticate_recovery_code(
    db: AsyncSession,
    *,
    email: str,
    code: str,
    ip: str | None,
    user_agent: str | None,
    trace_id: str | None,
) -> tuple[Admin, sessions.IssuedSession, int]:
    """Sign in with a recovery code, bypassing both password and TOTP.

    That is the whole point of a recovery code, and why they are Argon2-hashed and
    single-use. Returns the number remaining so the UI can warn when it runs low.
    """
    admin = await get_by_email(db, email)
    if admin is None or admin.status is not AdminStatus.ACTIVE:
        verify_dummy_password()
        raise Unauthenticated("Incorrect credentials.")

    if not await recovery.consume(db, admin.id, code):
        await audit.record(
            db,
            action=audit.Action.LOGIN_FAILED,
            actor_admin_id=admin.id,
            actor_ip_prefix=sessions.prefix_of(ip),
            trace_id=trace_id,
            detail={"reason": "bad_recovery_code"},
            independent=True,
        )
        raise Unauthenticated("Incorrect credentials.")

    issued = await sessions.create(db, admin_id=admin.id, ip=ip, user_agent=user_agent)
    left = await recovery.remaining(db, admin.id)
    await audit.record(
        db,
        action=audit.Action.RECOVERY_CODE_USED,
        actor_admin_id=admin.id,
        actor_ip_prefix=sessions.prefix_of(ip),
        trace_id=trace_id,
        detail={"remaining": left},
    )
    return admin, issued, left


# ---------------------------------------------------------------------------
# Telegram chat verification
# ---------------------------------------------------------------------------

CHAT_VERIFY_TTL = dt.timedelta(minutes=10)


async def begin_chat_verification(
    db: AsyncSession, settings: Settings, *, admin: Admin, chat_id: int
) -> None:
    """Send a numeric code to the proposed chat.

    Proving the chat is reachable *before* trusting it for recovery matters: an
    unverified chat id means reset links go to a chat that may not be the admin's,
    or may not exist at all -- and the failure would only surface when they needed it.
    """
    code = f"{secrets.randbelow(900000) + 100000}"
    await challenges.issue(
        db,
        kind=ChallengeKind.CHAT_VERIFY,
        admin_id=admin.id,
        ttl=CHAT_VERIFY_TTL,
        payload={"code": code, "chat_id": chat_id},
        # The secret is the 6-digit code in the payload, not a token the client
        # holds, so the row is keyed by the admin instead.
        token=str(admin.id),
    )
    # Translated here rather than allowed to propagate. `TelegramError` is a
    # RuntimeError, not a TraceletError, so an uncaught one reaches the global
    # handler as an *unexpected* failure and the admin is told "Internal error" with
    # no detail -- while they sit in front of a form whose input is very likely the
    # thing that is wrong (docs/ERRORS.md E20).
    try:
        token = settings.require("telegram_bot_token", "Telegram chat verification")
        await telegram.send_message(
            bot_token=token,
            chat_id=chat_id,
            text=telegram.chat_verification_message(display_name=admin.display_name, code=code),
        )
    except telegram.TelegramError as exc:
        log.warning("chat_verification_send_failed", permanent=exc.permanent)
        if exc.permanent:
            # Telegram refused it outright, so the chat id is the thing to fix.
            from tracelet.errors import FieldError  # noqa: PLC0415 - avoids an import cycle

            msg = (
                "Telegram would not accept a message for that chat. Check the id, and "
                "make sure you have sent the bot a message first -- a bot cannot open a "
                "conversation."
            )
            raise ValidationFailed(
                msg,
                errors=[
                    FieldError(field="chat_id", code="UNREACHABLE_CHAT", message=msg),
                ],
            ) from exc
        msg = (
            "Could not reach Telegram from the server. The code has not been sent; "
            "try again shortly."
        )
        raise DependencyUnavailable(msg) from exc
    except RuntimeError as exc:
        # settings.require(): the feature is not configured on this deployment.
        log.error("chat_verification_not_configured")
        msg = "Telegram is not configured on this server, so a code cannot be sent."
        raise DependencyUnavailable(msg) from exc


async def confirm_chat_verification(
    db: AsyncSession, *, admin: Admin, code: str, trace_id: str | None
) -> None:
    claim = await challenges.peek_for_admin(db, kind=ChallengeKind.CHAT_VERIFY, admin_id=admin.id)
    if claim is None:
        raise MfaInvalid("No pending chat verification. Start again.")

    expected = str(claim.payload.get("code", ""))
    chat_id = int(claim.payload.get("chat_id", 0))
    if not expected or not constant_time_equals(code.strip(), expected):
        raise MfaInvalid("Incorrect code.")

    await challenges.consume_for_admin(db, kind=ChallengeKind.CHAT_VERIFY, admin_id=admin.id)
    await db.execute(
        update(Admin)
        .where(Admin.id == admin.id)
        .values(telegram_chat_id=chat_id, telegram_verified_at=now())
    )
    await audit.record(
        db,
        action=audit.Action.TELEGRAM_VERIFIED,
        actor_admin_id=admin.id,
        target_type="admin",
        target_id=str(admin.id),
        trace_id=trace_id,
    )
