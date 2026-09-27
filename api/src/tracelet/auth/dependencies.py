"""Request dependencies: database session, current admin, role gates, CSRF.

Authorisation is checked **server-side on every route** (F8.AC12). The frontend
merely reflects the result; it never decides it. A dependency rather than middleware,
so a route that forgets to declare one fails closed at review time instead of
silently becoming public.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.auth import csrf, sessions
from tracelet.auth.models import Admin, AdminStatus
from tracelet.auth.models import Session as SessionRow
from tracelet.config import Settings, get_settings
from tracelet.db.request_session import request_session
from tracelet.errors import ForbiddenRole, TotpNotEnrolled, Unauthenticated

log = structlog.get_logger(__name__)


def get_db(request: Request) -> AsyncSession:
    """The session opened by DatabaseSessionMiddleware for this request.

    Deliberately NOT a ``yield`` dependency that commits on teardown: FastAPI runs
    that teardown after the response has been generated, so a failed commit cannot
    change the status code and the client is told 200 for a transaction that rolled
    back. The middleware holds the response and can still fail it
    (docs/ERRORS.md E11).
    """
    return request_session(request)


DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_config(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


Config = Annotated[Settings, Depends(get_config)]


def client_ip(request: Request, settings: Settings) -> str | None:
    """The visitor's address, trusting a forwarding header only when it is safe to.

    ``X-Tracelet-Peer-IP`` is set by our own Caddy from ``{remote_host}``, overwriting
    anything the client sent, so it cannot be forged. ``CF-Connecting-IP`` is trusted
    only when that peer is inside a Cloudflare range (F13.AC6) -- otherwise any
    visitor could choose their own apparent address and defeat both rate limiting and
    geolocation at once.

    M1 uses the peer address. The Cloudflare range check arrives with the capture path
    in M2, where a forged address would actually corrupt stored data.
    """
    peer = request.headers.get("x-tracelet-peer-ip")
    if peer:
        return peer.split(",")[0].strip()
    if settings.behind_cloudflare:
        # Placeholder until M2 adds verified-range checking. Deliberately NOT reading
        # CF-Connecting-IP yet: reading it without the range check would be worse than
        # not reading it, because it would look implemented.
        log.debug("cloudflare_ip_extraction_pending_m2")
    return request.client.host if request.client else None


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller."""

    admin: Admin
    session: SessionRow
    csrf_secret: str

    @property
    def is_owner(self) -> bool:
        return self.admin.is_owner


async def _resolve_principal(request: Request, db: AsyncSession, settings: Settings) -> Principal:
    token = request.cookies.get(sessions.COOKIE_NAME)
    if not token:
        raise Unauthenticated("Sign in to continue.")

    resolved = await sessions.resolve(
        db,
        token=token,
        ip=client_ip(request, settings),
        user_agent=request.headers.get("user-agent"),
    )
    if resolved is None:
        # Every rejection reason collapses to one response: absent, expired, revoked
        # or mismatched binding. A client has no legitimate use for the distinction.
        raise Unauthenticated("Your session has ended. Sign in again.")

    from tracelet.auth.service import get_by_id  # noqa: PLC0415 - avoids an import cycle

    admin = await get_by_id(db, resolved.session.admin_id)

    if admin.status is not AdminStatus.ACTIVE:
        # Disabling an account has to take effect on the next request, not at session
        # expiry, or "disabled" means nothing for up to 12 hours.
        await sessions.revoke(db, resolved.session.id, reason="admin_not_active")
        raise Unauthenticated("This account is no longer active.")

    if not admin.totp_enrolled:
        raise TotpNotEnrolled("Finish two-factor enrolment before continuing.")

    return Principal(admin=admin, session=resolved.session, csrf_secret=resolved.csrf_secret)


async def current_principal(request: Request, db: DbSession, settings: Config) -> Principal:
    """An authenticated caller, with CSRF verified on state-changing requests."""
    principal = await _resolve_principal(request, db, settings)
    csrf.verify(request, csrf_secret=principal.csrf_secret, site_address=settings.site_address)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def require_owner(principal: CurrentPrincipal) -> Principal:
    """Gate for destructive and configuration actions (CLAUDE.md invariant 9)."""
    if not principal.is_owner:
        log.warning(
            "role_denied", admin_id=str(principal.admin.id), role=principal.admin.role.value
        )
        raise ForbiddenRole("This action requires the owner role.")
    return principal


OwnerPrincipal = Annotated[Principal, Depends(require_owner)]
