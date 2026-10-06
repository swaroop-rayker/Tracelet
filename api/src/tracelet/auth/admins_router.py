"""Admin lifecycle management — owner only (docs/API.md §5).

Every route here is gated on the owner role and writes an audit row
(CLAUDE.md invariant 9).

Note what is absent: there is no way to set another admin's password. An owner
invites, and the invitee completes enrolment through a one-time link. That means no
default password exists anywhere in the system (F8.AC15), and an owner cannot
impersonate a colleague by setting a password they know.
"""

from __future__ import annotations

import datetime as dt
import uuid

import structlog
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from tracelet.audit import log as audit
from tracelet.auth import sessions
from tracelet.auth.dependencies import Config, DbSession, OwnerPrincipal, client_ip
from tracelet.auth.models import Admin, AdminRole, AdminStatus, AuditLog
from tracelet.auth.service import get_by_email, get_by_id, issue_enrollment_token
from tracelet.auth.types import AdminEmail
from tracelet.errors import LastOwner, NotFound, ValidationFailed

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/admins", tags=["admins"])


class AdminSummary(BaseModel):
    id: str
    email: AdminEmail
    display_name: str
    role: AdminRole
    status: AdminStatus
    totp_enrolled: bool
    telegram_verified: bool
    last_login_at: dt.datetime | None
    locked_until: dt.datetime | None
    created_at: dt.datetime


class CreateAdminRequest(BaseModel):
    email: AdminEmail
    display_name: str = Field(min_length=1, max_length=120)
    role: AdminRole = AdminRole.ANALYST


class CreateAdminResponse(BaseModel):
    admin: AdminSummary
    enrollment_url: str
    enrollment_expires_at: dt.datetime


class UpdateAdminRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: AdminRole | None = None
    status: AdminStatus | None = None


class EnrollmentTokenResponse(BaseModel):
    url: str
    expires_at: dt.datetime


def _summary(admin: Admin, last_login: dt.datetime | None = None) -> AdminSummary:
    return AdminSummary(
        id=str(admin.id),
        email=admin.email,
        display_name=admin.display_name,
        role=admin.role,
        status=admin.status,
        totp_enrolled=admin.totp_enrolled,
        telegram_verified=admin.telegram_verified_at is not None,
        last_login_at=last_login,
        locked_until=admin.locked_until,
        created_at=admin.created_at,
    )


async def _last_login(db: DbSession, admin_id: uuid.UUID) -> dt.datetime | None:
    return (
        await db.execute(
            select(func.max(AuditLog.occurred_at)).where(
                AuditLog.actor_admin_id == admin_id,
                AuditLog.action == audit.Action.LOGIN_SUCCEEDED,
            )
        )
    ).scalar_one_or_none()


@router.get("", response_model=list[AdminSummary], summary="List admins")
async def list_admins(principal: OwnerPrincipal, db: DbSession) -> list[AdminSummary]:
    del principal
    rows = (await db.execute(select(Admin).order_by(Admin.created_at))).scalars().all()
    return [_summary(row, await _last_login(db, row.id)) for row in rows]


@router.post(
    "",
    response_model=CreateAdminResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite an admin",
    description=(
        "Creates a pending account and returns a one-time enrollment URL. **No password "
        "is ever set here** -- the invitee sets their own through the link, so no "
        "default password exists and an owner cannot impersonate a colleague."
    ),
)
async def create_admin(
    payload: CreateAdminRequest,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> CreateAdminResponse:
    from tracelet.errors import FieldError  # noqa: PLC0415 - avoids an import cycle

    if await get_by_email(db, payload.email) is not None:
        raise ValidationFailed(
            "An admin with this address already exists.",
            errors=[FieldError(field="email", code="ALREADY_EXISTS", message="Already in use.")],
        )

    admin = Admin(
        email=payload.email,
        display_name=payload.display_name,
        role=payload.role,
        status=AdminStatus.PENDING_ENROLLMENT,
    )
    db.add(admin)
    await db.flush()

    offer = await issue_enrollment_token(
        db,
        settings,
        admin_id=admin.id,
        created_by=principal.admin.id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    await audit.record(
        db,
        action=audit.Action.ADMIN_CREATED,
        actor_admin_id=principal.admin.id,
        actor_ip_prefix=sessions.prefix_of(client_ip(request, settings)),
        target_type="admin",
        target_id=str(admin.id),
        trace_id=getattr(request.state, "trace_id", None),
        detail={"role": payload.role.value},
    )
    return CreateAdminResponse(
        admin=_summary(admin),
        enrollment_url=offer.url,
        enrollment_expires_at=offer.expires_at,
    )


@router.get("/{admin_id}", response_model=AdminSummary, summary="One admin")
async def get_admin(admin_id: str, principal: OwnerPrincipal, db: DbSession) -> AdminSummary:
    del principal
    target = await get_by_id(db, _parse_uuid(admin_id))
    return _summary(target, await _last_login(db, target.id))


@router.patch(
    "/{admin_id}",
    response_model=AdminSummary,
    summary="Update role, status or display name",
    description=(
        "Refuses any change that would leave zero active owners. Enforced by a "
        "deferred constraint trigger in the database as well as checked here, because "
        "two concurrent demotions would each pass an application-level count."
    ),
)
async def update_admin(
    admin_id: str,
    payload: UpdateAdminRequest,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> AdminSummary:
    target = await get_by_id(db, _parse_uuid(admin_id))
    trace_id = getattr(request.state, "trace_id", None)
    prefix = sessions.prefix_of(client_ip(request, settings))

    # Serialise concurrent changes to the owner set before reading it. See
    # _lock_active_owners: without this, two simultaneous demotions both pass every
    # check and commit, and the table ends with no active owner.
    if target.role is AdminRole.OWNER and target.status is AdminStatus.ACTIVE:
        await _lock_active_owners(db)
        await db.refresh(target)

    values: dict[str, object] = {}
    if payload.display_name is not None:
        values["display_name"] = payload.display_name
    if payload.role is not None and payload.role is not target.role:
        values["role"] = payload.role
    if payload.status is not None and payload.status is not target.status:
        values["status"] = payload.status

    if not values:
        return _summary(target, await _last_login(db, target.id))

    # Checked BEFORE mutating. The request session is committed on a 4xx (so a
    # failed login still leaves its audit row), which means raising after a partial
    # mutation would commit that mutation -- and the deferred trigger would then
    # fire at commit time, outside any handler, as a 500 (docs/ERRORS.md E13).
    would_stop_being_owner = target.role is AdminRole.OWNER and (
        values.get("role", target.role) is not AdminRole.OWNER
        or values.get("status", target.status) is not AdminStatus.ACTIVE
    )
    if (
        would_stop_being_owner
        and target.status is AdminStatus.ACTIVE
        and await _count_other_active_owners(db, target.id) == 0
    ):
        raise LastOwner("At least one active owner must remain.")

    # The audit detail's "from" must be read now. An ORM-enabled update() synchronises the
    # session, so after it `target.role` and `target.status` already hold the new values, and
    # every role change was recorded as {"from": "owner", "to": "owner"} (docs/ERRORS.md E51).
    role_before = target.role
    status_before = target.status

    try:
        await db.execute(update(Admin).where(Admin.id == target.id).values(**values))
        await db.flush()
        # Backstop for the concurrent case the pre-check cannot see: two
        # simultaneous demotions each observe the other owner still active.
        # SET CONSTRAINTS ALL IMMEDIATE forces the deferred trigger to evaluate
        # here, inside the handler, where the violation becomes a clean 409.
        await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    except IntegrityError as exc:
        await db.rollback()
        raise LastOwner("At least one active owner must remain.") from exc

    if "role" in values:
        await audit.record(
            db,
            action=audit.Action.ADMIN_ROLE_CHANGED,
            actor_admin_id=principal.admin.id,
            actor_ip_prefix=prefix,
            target_type="admin",
            target_id=str(target.id),
            trace_id=trace_id,
            detail={"from": role_before.value, "to": payload.role.value if payload.role else None},
        )
    if "status" in values:
        await audit.record(
            db,
            action=audit.Action.ADMIN_STATUS_CHANGED,
            actor_admin_id=principal.admin.id,
            actor_ip_prefix=prefix,
            target_type="admin",
            target_id=str(target.id),
            trace_id=trace_id,
            detail={
                "from": status_before.value,
                "to": payload.status.value if payload.status else None,
            },
        )
        if payload.status is not AdminStatus.ACTIVE:
            # Disabling must take effect immediately, not at session expiry.
            await sessions.revoke_all_for_admin(db, target.id, reason="admin_disabled")
    if "display_name" in values and "role" not in values and "status" not in values:
        await audit.record(
            db,
            action=audit.Action.ADMIN_UPDATED,
            actor_admin_id=principal.admin.id,
            actor_ip_prefix=prefix,
            target_type="admin",
            target_id=str(target.id),
            trace_id=trace_id,
        )

    await db.refresh(target)
    return _summary(target, await _last_login(db, target.id))


@router.delete(
    "/{admin_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an admin",
    description="Refused if it would leave zero active owners (409 LAST_OWNER).",
)
async def delete_admin(
    admin_id: str,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> Response:
    target = await get_by_id(db, _parse_uuid(admin_id))

    if target.id == principal.admin.id:
        raise ValidationFailed("You cannot delete your own account.")

    # Same serialisation as the PATCH path: two concurrent deletions of two
    # different owners would otherwise each see the other still active.
    if target.role is AdminRole.OWNER and target.status is AdminStatus.ACTIVE:
        await _lock_active_owners(db)
        await db.refresh(target)

    if (
        target.role is AdminRole.OWNER
        and target.status is AdminStatus.ACTIVE
        and await _count_other_active_owners(db, target.id) == 0
    ):
        raise LastOwner("At least one active owner must remain.")

    try:
        await db.execute(delete(Admin).where(Admin.id == target.id))
        await db.flush()
        await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    except IntegrityError as exc:
        await db.rollback()
        raise LastOwner("At least one active owner must remain.") from exc

    await audit.record(
        db,
        action=audit.Action.ADMIN_DELETED,
        actor_admin_id=principal.admin.id,
        actor_ip_prefix=sessions.prefix_of(client_ip(request, settings)),
        target_type="admin",
        target_id=str(target.id),
        trace_id=getattr(request.state, "trace_id", None),
        detail={"email_domain": target.email.split("@")[-1], "role": target.role.value},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{admin_id}/enrollment-token",
    response_model=EnrollmentTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a fresh enrollment link",
)
async def new_enrollment_token(
    admin_id: str,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> EnrollmentTokenResponse:
    target = await get_by_id(db, _parse_uuid(admin_id))
    offer = await issue_enrollment_token(
        db,
        settings,
        admin_id=target.id,
        created_by=principal.admin.id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return EnrollmentTokenResponse(url=offer.url, expires_at=offer.expires_at)


# ---------------------------------------------------------------------------


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFound("No such admin.") from exc


async def _lock_active_owners(db: DbSession) -> None:
    """Take a row lock on every active owner before deciding anything about them.

    Without this, the owner invariant has a real hole (docs/ERRORS.md E16). Two
    concurrent demotions each run ``_count_other_active_owners``, each sees the
    other owner still active because an uncommitted change is invisible across
    transactions, and both proceed. ``SET CONSTRAINTS ALL IMMEDIATE`` does not save
    it -- that fires the deferred trigger *early*, inside a snapshot that also
    cannot see the concurrent change, and consumes the pending event so nothing is
    re-checked at COMMIT.

    ``FOR UPDATE`` makes the second transaction wait for the first to commit, after
    which its next statement gets a fresh snapshot and the count is finally
    truthful. Ordered by id so two transactions can never take the same two rows in
    opposite orders and deadlock. At two admins this locks at most two rows.
    """
    await db.execute(
        select(Admin.id)
        .where(Admin.role == AdminRole.OWNER, Admin.status == AdminStatus.ACTIVE)
        .order_by(Admin.id)
        .with_for_update()
    )


async def _count_other_active_owners(db: DbSession, excluding: uuid.UUID) -> int:
    """Active owners other than ``excluding``.

    Used to reject a demotion before it is applied, which is what produces a clean
    409 rather than a commit-time failure. Correct only while the owner rows are
    locked -- see :func:`_lock_active_owners` -- and backstopped by the deferred
    trigger in the database either way (F8.AC13).
    """
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Admin)
                .where(
                    Admin.role == AdminRole.OWNER,
                    Admin.status == AdminStatus.ACTIVE,
                    Admin.id != excluding,
                )
            )
        ).scalar_one()
    )
