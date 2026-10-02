"""Authentication endpoints (docs/API.md §4).

Every response shape here is deliberate about what it does **not** say. Login,
password-reset-request and recovery-code failures are indistinguishable from one
another, and ``/reset/request`` returns 202 whether or not the account exists
(F8.AC10). That is enumeration resistance, not a missing feature.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo
from typing import Literal

import structlog
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tracelet.audit import log as audit
from tracelet.auth import recovery, sessions, totp
from tracelet.auth.dependencies import (
    Config,
    CurrentPrincipal,
    DbSession,
    client_ip,
)
from tracelet.auth.models import AdminRole, AdminStatus
from tracelet.auth.service import (
    authenticate_password,
    authenticate_recovery_code,
    begin_chat_verification,
    change_password,
    complete_enrollment,
    complete_mfa,
    confirm_chat_verification,
    confirm_password_reset,
    confirm_totp,
    request_password_reset,
)
from tracelet.auth.types import AdminEmail
from tracelet.errors import NotFound, RateLimited
from tracelet.ratelimit import gcra

log = structlog.get_logger(__name__)

Theme = Literal["semi_dark", "light", "dark"]

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: AdminEmail
    password: str = Field(min_length=1, max_length=512)


class MfaTokenResponse(BaseModel):
    mfa_token: str
    expires_at: dt.datetime


class MfaRequest(BaseModel):
    mfa_token: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=10)


class RecoveryCodeRequest(BaseModel):
    email: AdminEmail
    code: str = Field(min_length=6, max_length=32)


class RecoveryCodeResponse(BaseModel):
    remaining: int
    warn_low: bool


class MeResponse(BaseModel):
    id: str
    email: AdminEmail
    display_name: str
    role: AdminRole
    status: AdminStatus
    timezone: str
    theme: str
    totp_enrolled: bool
    telegram_verified: bool
    recovery_codes_remaining: int
    csrf_token: str
    session_id: str
    session_expires_at: dt.datetime


class ResetRequest(BaseModel):
    email: AdminEmail


class ResetConfirm(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=512)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=512)


class EnrollRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=512)


class EnrollResponse(BaseModel):
    """Returned exactly once. The secret and codes are never retrievable again."""

    secret: str
    otpauth_uri: str
    recovery_codes: list[str]
    hint: str
    confirm_token: str


class TotpConfirmRequest(BaseModel):
    # Bound to the enrolling admin by the server. Never a caller-supplied admin id:
    # that would let anyone activate any pending account.
    confirm_token: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=10)


class SessionSummary(BaseModel):
    id: str
    created_at: dt.datetime
    last_seen_at: dt.datetime
    expires_at: dt.datetime
    ip_prefix: str | None
    current: bool


class TelegramVerifyStart(BaseModel):
    chat_id: int


class TelegramVerifyConfirm(BaseModel):
    code: str = Field(min_length=4, max_length=10)


# ---------------------------------------------------------------------------
# Cookie handling
# ---------------------------------------------------------------------------


def _set_session_cookie(response: Response, *, token: str, expires_at: dt.datetime) -> None:
    """Set the session cookie with every hardening flag (F8.AC2).

    ``__Host-`` is browser-enforced and requires Secure + Path=/ + no Domain, which
    is what stops a sibling subdomain setting a session for us.
    """
    max_age = max(int((expires_at - dt.datetime.now(dt.UTC)).total_seconds()), 0)
    response.set_cookie(
        key=sessions.COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=sessions.COOKIE_NAME, path="/", httponly=True, secure=True)


async def _enforce(db: DbSession, *, key: str, limit: gcra.Limit, detail: str) -> None:
    decision = await gcra.check(db, key=key, limit=limit)
    if not decision.allowed:
        raise RateLimited(detail, retry_after=decision.retry_after_seconds)


# ---------------------------------------------------------------------------
# Login: two steps
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=MfaTokenResponse,
    summary="Step 1 of 2: password",
    description=(
        "Verifies the password and returns a short-lived MFA token. **Not** a session: "
        "TOTP is mandatory, so no route is reachable until step 2 completes. Failures "
        "are indistinguishable for wrong password, unknown account, disabled account "
        "and incomplete enrolment."
    ),
)
async def login(
    payload: LoginRequest, request: Request, db: DbSession, settings: Config
) -> MfaTokenResponse:
    ip = client_ip(request, settings)
    prefix = sessions.prefix_of(ip) or "unknown"

    # Two keys: the identifier protects one account from a distributed attempt, the
    # prefix protects the box from one network. Either alone leaves a gap.
    await _enforce(
        db,
        key=payload.email.lower(),
        limit=gcra.LOGIN_PER_IDENTIFIER,
        detail="Too many sign-in attempts for this account.",
    )
    await _enforce(
        db,
        key=prefix,
        limit=gcra.LOGIN_PER_PREFIX,
        detail="Too many sign-in attempts from this network.",
    )

    challenge = await authenticate_password(
        db,
        email=payload.email,
        password=payload.password,
        ip=ip,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return MfaTokenResponse(mfa_token=challenge.token, expires_at=challenge.expires_at)


@router.post(
    "/mfa",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Step 2 of 2: authenticator code",
    description=(
        "Validates the TOTP code and issues the session cookie. A code at or below the "
        "last accepted time step is rejected as reused, so an observed code cannot be "
        "replayed within its window."
    ),
)
async def mfa(payload: MfaRequest, request: Request, db: DbSession, settings: Config) -> Response:
    ip = client_ip(request, settings)
    await _enforce(
        db,
        key=sessions.prefix_of(ip) or "unknown",
        limit=gcra.MFA_PER_IDENTIFIER,
        detail="Too many code attempts.",
    )

    _, issued = await complete_mfa(
        db,
        settings,
        mfa_token=payload.mfa_token,
        code=payload.code,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
        trace_id=getattr(request.state, "trace_id", None),
    )
    result = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_session_cookie(result, token=issued.token, expires_at=issued.expires_at)
    # Returned in a header rather than the body because the body is empty; the SPA
    # reads it once and keeps it for subsequent state-changing requests.
    result.headers[sessions.CSRF_HEADER] = issued.csrf_token
    return result


@router.post(
    "/recovery-code",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign in with a recovery code",
    description=(
        "Bypasses both password and TOTP -- that is what a recovery code is for. "
        "Single-use. The remaining count is returned in the X-Recovery-Remaining header."
    ),
)
async def use_recovery_code(
    payload: RecoveryCodeRequest, request: Request, db: DbSession, settings: Config
) -> Response:
    ip = client_ip(request, settings)
    # Tighter than the password limit: each attempt costs ten Argon2 verifications at
    # 32 MiB, so this is a memory-amplification vector as well as a credential one.
    await _enforce(
        db,
        key=payload.email.lower(),
        limit=gcra.RECOVERY_PER_IDENTIFIER,
        detail="Too many recovery attempts.",
    )

    _, issued, remaining = await authenticate_recovery_code(
        db,
        email=payload.email,
        code=payload.code,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
        trace_id=getattr(request.state, "trace_id", None),
    )
    result = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_session_cookie(result, token=issued.token, expires_at=issued.expires_at)
    result.headers[sessions.CSRF_HEADER] = issued.csrf_token
    result.headers["X-Recovery-Remaining"] = str(remaining)
    return result


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End this session")
async def logout(
    request: Request, principal: CurrentPrincipal, db: DbSession, settings: Config
) -> Response:
    await sessions.revoke(db, principal.session.id, reason="logout")
    await audit.record(
        db,
        action=audit.Action.LOGOUT,
        actor_admin_id=principal.admin.id,
        actor_ip_prefix=sessions.prefix_of(client_ip(request, settings)),
        trace_id=getattr(request.state, "trace_id", None),
        target_type="session",
        target_id=str(principal.session.id),
    )
    result = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(result)
    return result


@router.get("/me", response_model=MeResponse, summary="The signed-in admin")
async def me(principal: CurrentPrincipal, db: DbSession) -> MeResponse:
    admin = principal.admin
    return MeResponse(
        id=str(admin.id),
        email=admin.email,
        display_name=admin.display_name,
        role=admin.role,
        status=admin.status,
        timezone=admin.timezone,
        theme=admin.theme,
        totp_enrolled=admin.totp_enrolled,
        telegram_verified=admin.telegram_verified_at is not None,
        recovery_codes_remaining=await recovery.remaining(db, admin.id),
        csrf_token=principal.csrf_secret,
        session_id=str(principal.session.id),
        session_expires_at=principal.session.expires_at,
    )


class PreferencesRequest(BaseModel):
    """Display preferences: per admin, persisted, and harmless (F9.AC16)."""

    model_config = ConfigDict(extra="forbid")

    theme: Theme | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            zoneinfo.ZoneInfo(value)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
            msg = "Not an IANA timezone name."
            raise ValueError(msg) from exc
        return value


@router.patch(
    "/me/preferences",
    response_model=MeResponse,
    summary="Change your own theme or display timezone",
    description=(
        "Display only. The theme is semi-dark unless changed (F9.AC16); the timezone is "
        "how timestamps are shown to you, not how analytics are bucketed (ADR-0016). "
        "Not audited: it changes nothing anyone else sees."
    ),
)
async def update_preferences(
    payload: PreferencesRequest, principal: CurrentPrincipal, db: DbSession
) -> MeResponse:
    if payload.theme is not None:
        principal.admin.theme = payload.theme
    if payload.timezone is not None:
        principal.admin.timezone = payload.timezone
    await db.flush()
    return await me(principal, db)


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------


@router.post(
    "/enroll",
    response_model=EnrollResponse,
    summary="Complete enrolment with a one-time token",
    description=(
        "Sets the first password and returns the TOTP secret plus ten recovery codes. "
        "**Returned exactly once** -- neither is retrievable afterwards. The account "
        "becomes active only after /totp/confirm proves the authenticator works."
    ),
)
async def enroll(
    payload: EnrollRequest, request: Request, db: DbSession, settings: Config
) -> EnrollResponse:
    result = await complete_enrollment(
        db,
        settings,
        token=payload.token,
        password=payload.password,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return EnrollResponse(
        secret=result.secret,
        otpauth_uri=result.otpauth_uri,
        recovery_codes=result.recovery_codes,
        hint=totp.provisioning_hint(),
        confirm_token=result.confirm_token,
    )


@router.post(
    "/totp/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Prove the authenticator works, activating the account and signing in",
    description=(
        "Takes the one-time confirm_token returned by /enroll. The account has no "
        "session yet and cannot have one, because the database forbids an active "
        "account without TOTP enrolled -- so the token is what identifies who is "
        "confirming.\n\n"
        "On success this issues the session cookie directly. Both factors have just "
        "been proved, and asking for a second code would be refused as a replay -- "
        "the step just accepted is now the stored high-water mark."
    ),
)
async def totp_confirm(
    payload: TotpConfirmRequest,
    request: Request,
    db: DbSession,
    settings: Config,
) -> Response:
    _, issued = await confirm_totp(
        db,
        settings,
        confirm_token=payload.confirm_token,
        code=payload.code,
        ip=client_ip(request, settings),
        user_agent=request.headers.get("user-agent"),
        trace_id=getattr(request.state, "trace_id", None),
    )
    result = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_session_cookie(result, token=issued.token, expires_at=issued.expires_at)
    result.headers[sessions.CSRF_HEADER] = issued.csrf_token
    return result


@router.post(
    "/totp/regenerate-codes",
    response_model=list[str],
    summary="Issue a fresh set of recovery codes, invalidating the old ones",
)
async def regenerate_codes(
    request: Request, principal: CurrentPrincipal, db: DbSession
) -> list[str]:
    codes = await recovery.issue(db, principal.admin.id)
    await audit.record(
        db,
        action=audit.Action.RECOVERY_CODES_REGENERATED,
        actor_admin_id=principal.admin.id,
        target_type="admin",
        target_id=str(principal.admin.id),
        trace_id=getattr(request.state, "trace_id", None),
    )
    return codes


# ---------------------------------------------------------------------------
# Password change and reset
# ---------------------------------------------------------------------------


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change your own password",
    description=(
        "Requires the current password. Revokes every OTHER session on success, "
        "because changing a password is the action a victim takes believing it locks "
        "an attacker out."
    ),
)
async def change_own_password(
    payload: ChangePasswordRequest,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
    settings: Config,
) -> Response:
    await change_password(
        db,
        settings,
        admin=principal.admin,
        current_password=payload.current_password,
        new_password=payload.new_password,
        keep_session_id=principal.session.id,
        ip=client_ip(request, settings),
        trace_id=getattr(request.state, "trace_id", None),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/reset/request",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a reset link over Telegram",
    description=(
        "**Always returns 202**, whether or not the account exists, has a verified "
        "chat, or delivery succeeded. Reporting any of those would be a reliable "
        "account-enumeration oracle (F8.AC10). Outcomes go to the audit log."
    ),
)
async def reset_request(
    payload: ResetRequest, request: Request, db: DbSession, settings: Config
) -> Response:
    await _enforce(
        db,
        key=payload.email.lower(),
        limit=gcra.RESET_REQUEST_PER_IDENTIFIER,
        detail="Too many reset requests for this account.",
    )
    await request_password_reset(
        db,
        settings,
        email=payload.email,
        ip=client_ip(request, settings),
        trace_id=getattr(request.state, "trace_id", None),
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password using a reset link",
)
async def reset_confirm(
    payload: ResetConfirm, request: Request, db: DbSession, settings: Config
) -> Response:
    await confirm_password_reset(
        db,
        settings,
        token=payload.token,
        new_password=payload.new_password,
        ip=client_ip(request, settings),
        trace_id=getattr(request.state, "trace_id", None),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=list[SessionSummary], summary="Your active sessions")
async def list_sessions(principal: CurrentPrincipal, db: DbSession) -> list[SessionSummary]:
    rows = await sessions.list_for_admin(db, principal.admin.id)
    return [
        SessionSummary(
            id=str(row.id),
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
            ip_prefix=str(row.ip_prefix) if row.ip_prefix else None,
            current=row.id == principal.session.id,
        )
        for row in rows
    ]


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke one of your sessions",
)
async def revoke_session(
    session_id: str,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
) -> Response:
    import uuid as _uuid  # noqa: PLC0415 - local, only needed here

    try:
        target = _uuid.UUID(session_id)
    except ValueError as exc:
        raise NotFound("No such session.") from exc

    # Only your own sessions. Without this check, any admin could revoke another's --
    # a denial-of-service against a colleague, and for an analyst a way to disrupt an
    # owner.
    owned = {row.id for row in await sessions.list_for_admin(db, principal.admin.id)}
    if target not in owned:
        raise NotFound("No such session.")

    await sessions.revoke(db, target, reason="revoked_by_admin")
    await audit.record(
        db,
        action=audit.Action.SESSION_REVOKED,
        actor_admin_id=principal.admin.id,
        trace_id=getattr(request.state, "trace_id", None),
        target_type="session",
        target_id=str(target),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Telegram recovery channel
# ---------------------------------------------------------------------------


@router.post(
    "/telegram/verify/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a verification code to a Telegram chat",
    description=(
        "Proves the chat is reachable before it is trusted for password recovery. An "
        "unverified chat id means reset links go somewhere that may not be yours -- and "
        "you would only find out when you needed it."
    ),
)
async def telegram_verify_start(
    payload: TelegramVerifyStart,
    principal: CurrentPrincipal,
    db: DbSession,
    settings: Config,
) -> Response:
    await begin_chat_verification(db, settings, admin=principal.admin, chat_id=payload.chat_id)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/telegram/verify/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Confirm the chat with the code it received",
)
async def telegram_verify_confirm(
    payload: TelegramVerifyConfirm,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
) -> Response:
    await confirm_chat_verification(
        db,
        admin=principal.admin,
        code=payload.code,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
