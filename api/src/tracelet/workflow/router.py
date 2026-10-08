"""Annotations and saved views (docs/API.md section 10a, F9.AC25, F9.AC26).

**The two places an analyst writes** (F8.AC12 as amended, SPEC section 11 row 29), and only
their own: any admin adds a note and changes or deletes their own; an owner deletes anyone's;
every delete is audited with the text (invariant 9). Saved views are each admin's alone, like
display preferences: another admin's view does not exist as far as the API is concerned (404,
owner included), and nothing about them is audited.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from tracelet.analytics.filters import VisitFilter, resolve_window
from tracelet.audit import log as audit
from tracelet.auth.dependencies import Config, CurrentPrincipal, DbSession, client_ip
from tracelet.auth.models import Admin, AdminRole
from tracelet.capture.models import Link
from tracelet.errors import NotAuthor, NotFound, SavedViewExists, SavedViewLimit
from tracelet.net import prefix_of
from tracelet.workflow.models import (
    ANNOTATION_MAX,
    VIEW_NAME_MAX,
    VIEW_PATH_PATTERN,
    VIEW_QUERY_MAX,
    VIEWS_PER_ADMIN,
    Annotation,
    SavedView,
)

router = APIRouter(tags=["workflow"])

MAX_ANNOTATIONS = 500

NoteText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=ANNOTATION_MAX)
]
ViewName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=VIEW_NAME_MAX)
]
ViewPath = Annotated[str, StringConstraints(pattern=VIEW_PATH_PATTERN)]
ViewQuery = Annotated[str, StringConstraints(max_length=VIEW_QUERY_MAX, pattern=r"^[^?#]*$")]


def _uuid(raw: str) -> uuid.UUID:
    """A malformed id is simply not found (404), as elsewhere in the API."""
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFound from exc


# ---------------------------------------------------------------------------
# Annotations (F9.AC25)
# ---------------------------------------------------------------------------


class AuthorOut(BaseModel):
    id: str
    name: str


class AnnotationOut(BaseModel):
    id: str
    at: dt.datetime
    text: str
    link_id: str | None
    link_label: str | None
    # null once the author's account is deleted: shown as "a former admin".
    author: AuthorOut | None
    # Whether the caller wrote it -- the dashboard offers Edit on these only.
    mine: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class AnnotationList(BaseModel):
    items: list[AnnotationOut]


class AnnotationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: dt.datetime
    text: NoteText
    link_id: uuid.UUID | None = None


class AnnotationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: dt.datetime | None = None
    text: NoteText | None = None
    # Explicit null makes the note every-link; absent leaves it as it is.
    link_id: uuid.UUID | None = Field(default=None)


async def _link_exists(db: DbSession, link_id: uuid.UUID | None) -> None:
    if link_id is not None and await db.get(Link, link_id) is None:
        raise NotFound


def _note_out(
    note: Annotation, author: Admin | None, link: Link | None, caller: uuid.UUID
) -> AnnotationOut:
    """An outer join's missing author or link is None (a deleted admin, an every-link note)."""
    return AnnotationOut(
        id=str(note.id),
        at=note.at,
        text=note.text,
        link_id=str(note.link_id) if note.link_id is not None else None,
        link_label=link.label if link is not None else None,
        author=AuthorOut(id=str(author.id), name=author.display_name) if author else None,
        mine=note.created_by == caller,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


async def _out(db: DbSession, note: Annotation, caller: uuid.UUID) -> AnnotationOut:
    author = await db.get(Admin, note.created_by) if note.created_by is not None else None
    link = await db.get(Link, note.link_id) if note.link_id is not None else None
    return _note_out(note, author, link, caller)


@router.get(
    "/api/v1/annotations",
    response_model=AnnotationList,
    summary="Notes in a window (F9.AC25)",
    description=(
        "With `link_id`: that link's notes and every-link notes. Default window: the last 30 "
        f"local days. At most {MAX_ANNOTATIONS}, oldest first."
    ),
)
async def list_annotations(
    principal: CurrentPrincipal,
    db: DbSession,
    settings: Config,
    from_: Annotated[dt.datetime | None, Query(alias="from")] = None,
    to: Annotated[dt.datetime | None, Query()] = None,
    link_id: Annotated[uuid.UUID | None, Query()] = None,
) -> AnnotationList:
    window = resolve_window(VisitFilter(from_=from_, to=to), settings.reporting_tz)
    stmt = (
        select(Annotation, Admin, Link)
        .outerjoin(Admin, Admin.id == Annotation.created_by)
        .outerjoin(Link, Link.id == Annotation.link_id)
        .where(Annotation.at >= window.start, Annotation.at < window.end)
        .order_by(Annotation.at, Annotation.id)
        .limit(MAX_ANNOTATIONS)
    )
    if link_id is not None:
        stmt = stmt.where(or_(Annotation.link_id.is_(None), Annotation.link_id == link_id))
    caller = principal.admin.id
    rows = (await db.execute(stmt)).tuples()
    items = [_note_out(note, author, link, caller) for note, author, link in rows]
    return AnnotationList(items=items)


@router.post(
    "/api/v1/annotations",
    response_model=AnnotationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a note (any admin)",
)
async def create_annotation(
    payload: AnnotationIn, principal: CurrentPrincipal, db: DbSession
) -> AnnotationOut:
    await _link_exists(db, payload.link_id)
    note = Annotation(
        at=payload.at,
        text=payload.text,
        link_id=payload.link_id,
        created_by=principal.admin.id,
    )
    db.add(note)
    await db.flush()
    await db.refresh(note)
    return await _out(db, note, principal.admin.id)


async def _note(db: DbSession, annotation_id: str) -> Annotation:
    note = await db.get(Annotation, _uuid(annotation_id))
    if note is None:
        raise NotFound
    return note


@router.patch(
    "/api/v1/annotations/{annotation_id}",
    response_model=AnnotationOut,
    summary="Change your own note",
)
async def update_annotation(
    annotation_id: str, payload: AnnotationPatch, principal: CurrentPrincipal, db: DbSession
) -> AnnotationOut:
    note = await _note(db, annotation_id)
    if note.created_by != principal.admin.id:
        raise NotAuthor
    fields = payload.model_fields_set
    if "at" in fields and payload.at is not None:
        note.at = payload.at
    if "text" in fields and payload.text is not None:
        note.text = payload.text
    if "link_id" in fields:
        await _link_exists(db, payload.link_id)
        note.link_id = payload.link_id
    await db.flush()
    await db.refresh(note)
    return await _out(db, note, principal.admin.id)


@router.delete(
    "/api/v1/annotations/{annotation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a note: your own, or any as an owner (audited)",
)
async def delete_annotation(
    annotation_id: str,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
    config: Config,
) -> Response:
    note = await _note(db, annotation_id)
    own = note.created_by == principal.admin.id
    if not own and principal.admin.role is not AdminRole.OWNER:
        raise NotAuthor
    detail: dict[str, Any] = {
        "text": note.text,
        "at": note.at.isoformat(),
        "link_id": str(note.link_id) if note.link_id else None,
        "author_id": str(note.created_by) if note.created_by else None,
    }
    await db.execute(delete(Annotation).where(Annotation.id == note.id))
    await audit.record(
        db,
        action=audit.Action.ANNOTATION_DELETED,
        actor_admin_id=principal.admin.id,
        actor_ip_prefix=prefix_of(client_ip(request, config)),
        target_type="annotation",
        target_id=str(note.id),
        detail=detail,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Saved views (F9.AC26, DESIGN E12)
# ---------------------------------------------------------------------------


class SavedViewOut(BaseModel):
    id: str
    name: str
    path: str
    query: str
    created_at: dt.datetime
    updated_at: dt.datetime


class SavedViewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ViewName
    path: ViewPath
    query: ViewQuery = ""


class SavedViewPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ViewName | None = None
    query: ViewQuery | None = None


def _view_out(view: SavedView) -> SavedViewOut:
    return SavedViewOut(
        id=str(view.id),
        name=view.name,
        path=view.path,
        query=view.query,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


async def _own_view(db: DbSession, view_id: str, admin_id: uuid.UUID) -> SavedView:
    view = await db.get(SavedView, _uuid(view_id))
    # Another admin's view is not merely forbidden: for this caller it does not exist.
    if view is None or view.admin_id != admin_id:
        raise NotFound
    return view


async def _name_taken(db: DbSession, admin_id: uuid.UUID, name: str, but: uuid.UUID | None) -> bool:
    stmt = select(SavedView.id).where(SavedView.admin_id == admin_id, SavedView.name == name)
    if but is not None:
        stmt = stmt.where(SavedView.id != but)
    return (await db.execute(stmt)).first() is not None


@router.get(
    "/api/v1/saved-views",
    response_model=list[SavedViewOut],
    summary="Your saved views, by name",
)
async def list_saved_views(principal: CurrentPrincipal, db: DbSession) -> list[SavedViewOut]:
    rows = await db.execute(
        select(SavedView)
        .where(SavedView.admin_id == principal.admin.id)
        .order_by(func.lower(SavedView.name), SavedView.id)
    )
    return [_view_out(v) for v in rows.scalars()]


@router.post(
    "/api/v1/saved-views",
    response_model=SavedViewOut,
    status_code=status.HTTP_201_CREATED,
    summary="Save the current page and its filters under a name",
)
async def create_saved_view(
    payload: SavedViewIn, principal: CurrentPrincipal, db: DbSession
) -> SavedViewOut:
    admin_id = principal.admin.id
    # The admin's row lock serialises their own concurrent saves, so the limit holds.
    await db.execute(select(Admin.id).where(Admin.id == admin_id).with_for_update())
    count = (
        await db.execute(select(func.count()).where(SavedView.admin_id == admin_id))
    ).scalar_one()
    if count >= VIEWS_PER_ADMIN:
        raise SavedViewLimit
    if await _name_taken(db, admin_id, payload.name, None):
        raise SavedViewExists
    view = SavedView(admin_id=admin_id, name=payload.name, path=payload.path, query=payload.query)
    db.add(view)
    try:
        await db.flush()
    except IntegrityError as exc:  # a race on the name: the constraint decides
        raise SavedViewExists from exc
    await db.refresh(view)
    return _view_out(view)


@router.patch(
    "/api/v1/saved-views/{view_id}",
    response_model=SavedViewOut,
    summary="Rename a saved view, or replace its filters",
)
async def update_saved_view(
    view_id: str, payload: SavedViewPatch, principal: CurrentPrincipal, db: DbSession
) -> SavedViewOut:
    view = await _own_view(db, view_id, principal.admin.id)
    if payload.name is not None and payload.name != view.name:
        if await _name_taken(db, principal.admin.id, payload.name, view.id):
            raise SavedViewExists
        view.name = payload.name
    if payload.query is not None:
        view.query = payload.query
    await db.flush()
    await db.refresh(view)
    return _view_out(view)


@router.delete(
    "/api/v1/saved-views/{view_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved view",
)
async def delete_saved_view(view_id: str, principal: CurrentPrincipal, db: DbSession) -> Response:
    view = await _own_view(db, view_id, principal.admin.id)
    await db.execute(delete(SavedView).where(SavedView.id == view.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
