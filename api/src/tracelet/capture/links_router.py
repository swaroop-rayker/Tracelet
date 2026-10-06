"""Tracking-link management (docs/API.md section 6, F1).

Reads are open to any admin; every write is ``owner``-only and writes an audit row
(CLAUDE.md invariant 9).

**The default link.** At most one non-archived link is the default, and that half is
enforced by a partial unique index. *At least one* -- whenever any non-archived link
exists -- is the application's half, because an empty table legitimately has none.
Every operation that can change which link is the default first takes a
transaction-scoped advisory lock, so two concurrent writes cannot both pass a count
that neither can see the other change (the same shape of bug as docs/ERRORS.md E16).

What the default *does* is not specified anywhere yet: RW-2 turned the brief's single
redirect URL into many links "one marked default", and F1.AC3 enforces exactly one,
but no requirement gives it behaviour. It is maintained here as an invariant only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, BeforeValidator, Field, StringConstraints
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from tracelet.audit import log as audit
from tracelet.auth.dependencies import (
    Config,
    CurrentPrincipal,
    DbSession,
    OwnerPrincipal,
    client_ip,
)
from tracelet.capture.links import validate_destination
from tracelet.capture.models import Link, Visit
from tracelet.capture.service import link_cache
from tracelet.errors import (
    DefaultLinkRequired,
    FieldError,
    LinkHasVisits,
    NotFound,
    ValidationFailed,
)
from tracelet.net import prefix_of

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/links", tags=["links"])


def _normalise_slug(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


# Lowercased BEFORE the pattern is checked. StringConstraints(to_lower=True, pattern=...)
# does it the other way round -- the pattern sees the original string -- so "IG-Bio"
# was rejected instead of normalised, disagreeing with the capture path, which accepts
# a slug in any case (docs/ERRORS.md E24).
Slug = Annotated[
    str,
    BeforeValidator(_normalise_slug),
    StringConstraints(pattern=r"^[a-z0-9-]{4,32}$"),
]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Destination = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
Priority = Literal["high", "normal", "silent"]


class NotifyPolicy(BaseModel):
    """Per-link notification priorities (F1.AC5, SPEC section 11 row 18).

    ``inside`` is combined with the matching geofence's own priority, the less urgent
    winning. ``automated`` admits only ``silent``: automated traffic never notifies
    (CLAUDE.md invariant 6), and ``ck_links_automated_silent`` holds the same line.
    """

    inside: Priority = "high"
    outside: Priority = "normal"
    undetermined: Priority = "normal"
    automated: Literal["silent"] = "silent"


class LinkCreate(BaseModel):
    slug: Slug
    label: Label
    destination_url: Destination
    is_active: bool = True
    interstitial_ms: int = Field(default=700, ge=300, le=1500)
    # F1.AC11, ADR-0021: show consent text and the location prompt, waiting up to 15 s.
    ask_location: bool = False
    notify_policy: NotifyPolicy = Field(default_factory=NotifyPolicy)


class LinkUpdate(BaseModel):
    slug: Slug | None = None
    label: Label | None = None
    destination_url: Destination | None = None
    is_active: bool | None = None
    interstitial_ms: int | None = Field(default=None, ge=300, le=1500)
    ask_location: bool | None = None
    notify_policy: NotifyPolicy | None = None


class LinkClone(BaseModel):
    slug: Slug
    label: Label | None = None


class LinkOut(BaseModel):
    id: str
    slug: str
    label: str
    destination_url: str
    capture_url: str
    is_active: bool
    is_default: bool
    notify_policy: NotifyPolicy
    interstitial_ms: int
    ask_location: bool
    cloned_from: str | None
    visit_count: int
    created_at: dt.datetime
    updated_at: dt.datetime
    archived_at: dt.datetime | None


def _out(link: Link, base_url: str, visit_count: int) -> LinkOut:
    return LinkOut(
        id=str(link.id),
        slug=link.slug,
        label=link.label,
        destination_url=link.destination_url,
        capture_url=f"{base_url}/r/{link.slug}",
        is_active=link.is_active,
        is_default=link.is_default,
        notify_policy=NotifyPolicy.model_validate(link.notify_policy),
        interstitial_ms=link.interstitial_ms,
        ask_location=link.ask_location,
        cloned_from=str(link.cloned_from) if link.cloned_from else None,
        visit_count=visit_count,
        created_at=link.created_at,
        updated_at=link.updated_at,
        archived_at=link.archived_at,
    )


async def _visit_count(db: DbSession, link_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).select_from(Visit).where(Visit.link_id == link_id)
            )
        ).scalar_one()
    )


async def _get(db: DbSession, link_id: str) -> Link:
    try:
        parsed = uuid.UUID(link_id)
    except ValueError as exc:
        raise NotFound("No such link.") from exc
    link = (await db.execute(select(Link).where(Link.id == parsed))).scalar_one_or_none()
    if link is None:
        raise NotFound("No such link.")
    return link


async def _lock_defaults(db: DbSession) -> None:
    """Serialise every change to which link is the default. See the module docstring."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('tracelet.links.default'))"))


async def _other_live_links(db: DbSession, excluding: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Link)
                .where(Link.archived_at.is_(None), Link.id != excluding)
            )
        ).scalar_one()
    )


async def _slug_taken(db: DbSession, slug: str, excluding: uuid.UUID | None = None) -> bool:
    stmt = select(Link.id).where(Link.slug == slug)
    if excluding is not None:
        stmt = stmt.where(Link.id != excluding)
    return (await db.execute(stmt)).first() is not None


def _slug_conflict() -> ValidationFailed:
    msg = "This slug is already in use."
    return ValidationFailed(
        msg, errors=[FieldError(field="slug", code="ALREADY_EXISTS", message=msg)]
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("", response_model=list[LinkOut], summary="List links with visit counts")
async def list_links(
    principal: CurrentPrincipal,
    db: DbSession,
    settings: Config,
    include_archived: Annotated[bool, Query()] = False,
) -> list[LinkOut]:
    del principal
    stmt = select(Link).order_by(Link.created_at)
    if not include_archived:
        stmt = stmt.where(Link.archived_at.is_(None))
    links = (await db.execute(stmt)).scalars().all()
    counts = dict(
        (await db.execute(select(Visit.link_id, func.count()).group_by(Visit.link_id)))
        .tuples()
        .all()
    )
    return [_out(link, settings.public_base_url, int(counts.get(link.id, 0))) for link in links]


@router.get("/{link_id}", response_model=LinkOut, summary="One link")
async def get_link(
    link_id: str, principal: CurrentPrincipal, db: DbSession, settings: Config
) -> LinkOut:
    del principal
    link = await _get(db, link_id)
    return _out(link, settings.public_base_url, await _visit_count(db, link.id))


# ---------------------------------------------------------------------------
# Writes -- owner only, all audited
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=LinkOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a link",
    description=(
        "The first non-archived link becomes the default automatically. The destination "
        "must be https, carry no credentials, and resolve to public addresses (F1.AC2)."
    ),
)
async def create_link(
    payload: LinkCreate,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> LinkOut:
    destination = await validate_destination(
        payload.destination_url, own_host=settings.site_address
    )
    if await _slug_taken(db, payload.slug):
        raise _slug_conflict()

    await _lock_defaults(db)
    has_default = (
        await db.execute(
            select(Link.id).where(Link.is_default.is_(True), Link.archived_at.is_(None))
        )
    ).first() is not None

    link = Link(
        slug=payload.slug,
        label=payload.label,
        destination_url=destination,
        is_active=payload.is_active,
        is_default=not has_default,
        interstitial_ms=payload.interstitial_ms,
        ask_location=payload.ask_location,
        notify_policy=payload.notify_policy.model_dump(),
        created_by=principal.admin.id,
    )
    db.add(link)
    try:
        await db.flush()
    except IntegrityError as exc:
        # Lost a race on the slug: citext makes the unique index case-insensitive.
        await db.rollback()
        raise _slug_conflict() from exc
    await db.refresh(link)

    await audit.record(
        db,
        action=audit.Action.LINK_CREATED,
        actor_admin_id=principal.admin.id,
        actor_ip_prefix=prefix_of(client_ip(request, settings)),
        target_type="link",
        target_id=str(link.id),
        trace_id=getattr(request.state, "trace_id", None),
        detail={"slug": link.slug, "destination_url": destination, "is_default": link.is_default},
    )
    return _out(link, settings.public_base_url, 0)


@router.patch(
    "/{link_id}",
    response_model=LinkOut,
    summary="Update a link",
    description=(
        "A destination change takes effect on the next request with no restart, and "
        "is audited with both the old and the new value (F1.AC8). Changing the slug "
        "retires the old one immediately -- use clone to rotate a burned slug while "
        "keeping it working."
    ),
)
async def update_link(
    link_id: str,
    payload: LinkUpdate,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> LinkOut:
    link = await _get(db, link_id)
    changes: dict[str, object] = {}
    detail: dict[str, object] = {}

    # Every check runs before any write: the request session commits on a 4xx, so a
    # handler that mutated and then rejected would commit the mutation (E14).
    if payload.destination_url is not None:
        destination = await validate_destination(
            payload.destination_url, own_host=settings.site_address
        )
        if destination != link.destination_url:
            changes["destination_url"] = destination
            detail["destination_url"] = {"from": link.destination_url, "to": destination}
    if payload.slug is not None and payload.slug != link.slug:
        if await _slug_taken(db, payload.slug, excluding=link.id):
            raise _slug_conflict()
        changes["slug"] = payload.slug
        detail["slug"] = {"from": link.slug, "to": payload.slug}
    for name in ("label", "is_active", "interstitial_ms", "ask_location"):
        value = getattr(payload, name)
        if value is not None and value != getattr(link, name):
            changes[name] = value
            detail[name] = {"from": getattr(link, name), "to": value}
    if payload.notify_policy is not None:
        policy = payload.notify_policy.model_dump()
        if policy != link.notify_policy:
            changes["notify_policy"] = policy
            detail["notify_policy"] = {"from": link.notify_policy, "to": policy}

    if changes:
        old_slug = link.slug
        try:
            await db.execute(update(Link).where(Link.id == link.id).values(**changes))
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise _slug_conflict() from exc
        link_cache.forget(old_slug)
        await db.refresh(link)
        await audit.record(
            db,
            action=audit.Action.LINK_UPDATED,
            actor_admin_id=principal.admin.id,
            actor_ip_prefix=prefix_of(client_ip(request, settings)),
            target_type="link",
            target_id=str(link.id),
            trace_id=getattr(request.state, "trace_id", None),
            detail=detail,
        )
    return _out(link, settings.public_base_url, await _visit_count(db, link.id))


@router.post(
    "/{link_id}/clone",
    response_model=LinkOut,
    status_code=status.HTTP_201_CREATED,
    summary="Clone a link under a new slug",
    description=(
        "Rotates a burned slug: the copy keeps the configuration, and the original "
        "keeps its visits (F1.AC9). The original is left as it is -- deactivate it "
        "separately if the old slug should stop working."
    ),
)
async def clone_link(
    link_id: str,
    payload: LinkClone,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> LinkOut:
    source = await _get(db, link_id)
    if await _slug_taken(db, payload.slug):
        raise _slug_conflict()

    clone = Link(
        slug=payload.slug,
        label=payload.label or source.label,
        destination_url=source.destination_url,
        is_active=True,
        is_default=False,
        interstitial_ms=source.interstitial_ms,
        ask_location=source.ask_location,
        notify_policy=dict(source.notify_policy),
        cloned_from=source.id,
        created_by=principal.admin.id,
    )
    db.add(clone)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise _slug_conflict() from exc
    await db.refresh(clone)

    await audit.record(
        db,
        action=audit.Action.LINK_CLONED,
        actor_admin_id=principal.admin.id,
        actor_ip_prefix=prefix_of(client_ip(request, settings)),
        target_type="link",
        target_id=str(clone.id),
        trace_id=getattr(request.state, "trace_id", None),
        detail={"from_link": str(source.id), "from_slug": source.slug, "slug": clone.slug},
    )
    return _out(clone, settings.public_base_url, 0)


@router.post(
    "/{link_id}/default",
    response_model=LinkOut,
    summary="Make this the default link",
    description="Clears the previous default in the same transaction (F1.AC3).",
)
async def make_default(
    link_id: str,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> LinkOut:
    link = await _get(db, link_id)
    if link.archived_at is not None:
        msg = "An archived link cannot be the default."
        raise ValidationFailed(msg)

    if not link.is_default:
        await _lock_defaults(db)
        previous = (
            await db.execute(
                select(Link.id).where(Link.is_default.is_(True), Link.archived_at.is_(None))
            )
        ).scalar_one_or_none()
        # Clear first, then set: the partial unique index would reject the reverse.
        await db.execute(
            update(Link)
            .where(Link.is_default.is_(True), Link.archived_at.is_(None))
            .values(is_default=False)
        )
        await db.execute(update(Link).where(Link.id == link.id).values(is_default=True))
        link_cache.forget_default()
        await db.refresh(link)
        await audit.record(
            db,
            action=audit.Action.LINK_DEFAULT_CHANGED,
            actor_admin_id=principal.admin.id,
            actor_ip_prefix=prefix_of(client_ip(request, settings)),
            target_type="link",
            target_id=str(link.id),
            trace_id=getattr(request.state, "trace_id", None),
            detail={"previous": str(previous) if previous else None},
        )
    return _out(link, settings.public_base_url, await _visit_count(db, link.id))


@router.post(
    "/{link_id}/archive",
    response_model=LinkOut,
    summary="Archive a link",
    description=(
        "Archived links return 404 from the capture surface and keep their visits. "
        "The default cannot be archived while another link could take its place -- "
        "make that one the default first (409 DEFAULT_LINK_REQUIRED)."
    ),
)
async def archive_link(
    link_id: str,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> LinkOut:
    link = await _get(db, link_id)
    if link.archived_at is None:
        await _lock_defaults(db)
        await db.refresh(link)
        if link.is_default and await _other_live_links(db, link.id) > 0:
            raise DefaultLinkRequired("Make another link the default before archiving this one.")
        await db.execute(
            update(Link).where(Link.id == link.id).values(archived_at=func.now(), is_default=False)
        )
        link_cache.forget(link.slug)
        await db.refresh(link)
        await audit.record(
            db,
            action=audit.Action.LINK_ARCHIVED,
            actor_admin_id=principal.admin.id,
            actor_ip_prefix=prefix_of(client_ip(request, settings)),
            target_type="link",
            target_id=str(link.id),
            trace_id=getattr(request.state, "trace_id", None),
            detail={"slug": link.slug},
        )
    return _out(link, settings.public_base_url, await _visit_count(db, link.id))


@router.delete(
    "/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a link",
    description=(
        "Refused while any visit references it (409 LINK_HAS_VISITS) -- archive it "
        "instead. Historical data is never orphaned (F1.AC10), and the foreign key "
        "enforces that even if this check were bypassed."
    ),
)
async def delete_link(
    link_id: str,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> Response:
    link = await _get(db, link_id)
    await _lock_defaults(db)
    if await _visit_count(db, link.id) > 0:
        raise LinkHasVisits("This link has visits. Archive it instead.")
    if link.is_default and link.archived_at is None and await _other_live_links(db, link.id) > 0:
        raise DefaultLinkRequired("Make another link the default before deleting this one.")

    slug = link.slug
    try:
        await db.delete(link)
        await db.flush()
    except IntegrityError as exc:
        # A visit arrived between the count and the delete. ON DELETE RESTRICT is the
        # backstop; the answer is the same as if the count had seen it.
        await db.rollback()
        raise LinkHasVisits("This link has visits. Archive it instead.") from exc
    link_cache.forget(slug)

    await audit.record(
        db,
        action=audit.Action.LINK_DELETED,
        actor_admin_id=principal.admin.id,
        actor_ip_prefix=prefix_of(client_ip(request, settings)),
        target_type="link",
        target_id=str(link.id),
        trace_id=getattr(request.state, "trace_id", None),
        detail={"slug": slug},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
