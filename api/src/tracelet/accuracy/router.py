"""Ground truth and accuracy (docs/API.md section 11, F4.AC15, F4.AC17, F9.AC10).

Reads are open to every admin; writing a label is an owner's (CLAUDE.md invariant 9) and
audited. Recording a run is owner-only but not audited: it changes no configuration and
deletes nothing, and the run row is itself the record.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import replace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, select

from tracelet.accuracy import labels as label_service
from tracelet.accuracy import store
from tracelet.accuracy.metrics import Report
from tracelet.accuracy.models import RUN_NOTE_MAX, AccuracyRun, GroundTruthLabel
from tracelet.accuracy.schemas import AdminRef, LabelList, LabelOut, label_out, label_select
from tracelet.auth.dependencies import (
    Config,
    CurrentPrincipal,
    DbSession,
    OwnerPrincipal,
    client_ip,
)
from tracelet.auth.models import Admin
from tracelet.capture.models import ConsentState, Link, Visit
from tracelet.capture.visits_router import VisitSummary, summarize
from tracelet.errors import NotFound
from tracelet.geofence import regions
from tracelet.net import prefix_of

router = APIRouter(prefix="/api/v1/ground-truth", tags=["ground-truth"])

MAX_LABELS = 1000
MAX_RUNS = 200

ConnectionKind = Literal["wifi", "mobile_data", "ethernet"]
Network = Literal["airtel", "jio", "vi", "bsnl", "act", "other"]
Place = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


def _uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFound from exc


def _actor(request: Request, principal: OwnerPrincipal, config: Config) -> label_service.Actor:
    return label_service.Actor(
        admin_id=principal.admin.id,
        ip_prefix=prefix_of(client_ip(request, config)),
        trace_id=getattr(request.state, "trace_id", None),
    )


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


class LabelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visit_id: uuid.UUID
    cant_tell: bool = False
    country_code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2)] | None = (
        None
    )
    admin1: Place | None = None
    admin2: Place | None = None
    city: Place | None = None
    use_gps: bool | None = None
    connection_kind: ConnectionKind | None = None
    vpn_used: bool | None = None
    network: Network | None = None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class LabelPatch(BaseModel):
    """Any subset; a field sent as null is cleared, a field left out is kept."""

    model_config = ConfigDict(extra="forbid")

    cant_tell: bool | None = None
    country_code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2)] | None = (
        None
    )
    admin1: Place | None = None
    admin2: Place | None = None
    city: Place | None = None
    use_gps: bool | None = None
    connection_kind: ConnectionKind | None = None
    vpn_used: bool | None = None
    network: Network | None = None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


async def _one(db: DbSession, label_id: uuid.UUID) -> LabelOut:
    row = (await db.execute(label_select().where(GroundTruthLabel.id == label_id))).first()
    if row is None:
        raise NotFound
    return label_out(*row)


def _catalog(config: Config) -> regions.Catalog | None:
    # Without the GeoNames admin1 table the state cannot be checked; it is still stored,
    # and a misspelling simply never matches (scored as wrong, visibly).
    return regions.catalog(config)


@router.get(
    "",
    response_model=LabelList,
    summary="Every label, with the engine's recorded answer beside it",
)
async def list_labels(principal: CurrentPrincipal, db: DbSession) -> LabelList:
    del principal
    rows = (
        await db.execute(
            label_select().order_by(GroundTruthLabel.labeled_at.desc()).limit(MAX_LABELS)
        )
    ).all()
    total, cant_tell = await store.label_counts(db)
    return LabelList(items=[label_out(*r) for r in rows], total=total, cant_tell=cant_tell)


def _values(
    payload: LabelIn | LabelPatch, base: label_service.LabelValues
) -> label_service.LabelValues:
    sent = payload.model_fields_set
    fields: dict[str, Any] = {
        name: getattr(payload, name)
        for name in (
            "cant_tell",
            "country_code",
            "admin1",
            "admin2",
            "city",
            "use_gps",
            "connection_kind",
            "vpn_used",
            "network",
            "notes",
        )
        if name in sent
    }
    if fields.get("cant_tell") is None:
        fields.pop("cant_tell", None)
    if fields.get("cant_tell") is True:
        # Can't tell clears the place in one step, as the form's button does.
        fields.update(country_code=None, admin1=None, admin2=None, city=None)
    return replace(base, **fields)


@router.post(
    "",
    response_model=LabelOut,
    status_code=status.HTTP_201_CREATED,
    summary="Label a visit (owner only, audited)",
)
async def create_label(
    payload: LabelIn, request: Request, principal: OwnerPrincipal, db: DbSession, config: Config
) -> LabelOut:
    label = await label_service.create(
        db,
        payload.visit_id,
        _values(payload, label_service.LabelValues()),
        catalog=_catalog(config),
        actor=_actor(request, principal, config),
    )
    return await _one(db, label.id)


async def _label(db: DbSession, label_id: str) -> GroundTruthLabel:
    label = await db.get(GroundTruthLabel, _uuid(label_id))
    if label is None:
        raise NotFound
    return label


@router.patch(
    "/{label_id}",
    response_model=LabelOut,
    summary="Change a label (owner only, audited)",
)
async def update_label(
    label_id: str,
    payload: LabelPatch,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    config: Config,
) -> LabelOut:
    label = await _label(db, label_id)
    await label_service.update(
        db,
        label,
        _values(payload, label_service.values_of(label)),
        catalog=_catalog(config),
        actor=_actor(request, principal, config),
    )
    return await _one(db, label.id)


@router.delete(
    "/{label_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a label (owner only, audited)",
)
async def delete_label(
    label_id: str, request: Request, principal: OwnerPrincipal, db: DbSession, config: Config
) -> Response:
    label = await _label(db, label_id)
    await label_service.delete(db, label, actor=_actor(request, principal, config))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


class QueueItem(BaseModel):
    visit: VisitSummary
    consent_state: ConsentState
    conflict_score: float | None
    agreement_score: float | None


class Queue(BaseModel):
    items: list[QueueItem]
    labelled: int
    remaining: int


@router.get(
    "/queue",
    response_model=Queue,
    summary="Visits worth labelling, most disagreement first",
    description=(
        "`order=conflict` ranks by `conflict_score`, then newest: where sources disagreed "
        "most, a label teaches tuning most. `order=recent` is newest first, for the test "
        "visit you have just made."
    ),
)
async def queue(
    principal: CurrentPrincipal,
    db: DbSession,
    order: Literal["conflict", "recent"] = "conflict",
    link_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Queue:
    del principal
    clauses = store.queue_clauses()
    if link_id is not None:
        clauses.append(Visit.link_id == link_id)
    rows = (
        await db.execute(
            select(Visit, Link)
            .join(Link, Link.id == Visit.link_id)
            .where(*clauses)
            .order_by(*store.queue_order(order))
            .limit(limit)
        )
    ).all()
    remaining = int(
        (await db.execute(select(func.count()).select_from(Visit).where(*clauses))).scalar_one()
    )
    total, cant_tell = await store.label_counts(db)
    return Queue(
        items=[
            QueueItem(
                visit=summarize(visit, link, is_returning=None),
                consent_state=visit.consent_state,
                conflict_score=float(visit.conflict_score)
                if visit.conflict_score is not None
                else None,
                agreement_score=float(visit.agreement_score)
                if visit.agreement_score is not None
                else None,
            )
            for visit, link in rows
        ],
        labelled=total - cant_tell,
        remaining=remaining,
    )


# ---------------------------------------------------------------------------
# Metrics and runs
# ---------------------------------------------------------------------------


@router.get(
    "/metrics",
    response_model=Report,
    summary="Precision, coverage and best-guess accuracy, replayed (F4.AC13, F4.AC17)",
    description=(
        "A replay of the consensus over every labelled visit's stored candidates under "
        "`settings_version` (default: the active one), so a retained version can be scored "
        "without activating it. Every proportion carries `k`, `n` and a 95 % Wilson interval."
    ),
)
async def metrics(
    principal: CurrentPrincipal,
    db: DbSession,
    settings_version: Annotated[int | None, Query(ge=1)] = None,
) -> Report:
    del principal
    return await store.report(db, version=settings_version)


class RunOut(BaseModel):
    id: str
    run_at: dt.datetime
    origin: Literal["cli", "dashboard"]
    settings_version: int
    inference_version: str
    classifier_version: str
    git_sha: str | None
    label_count: int
    passed: bool | None
    note: str | None
    recorded_by: AdminRef | None


class RunDetail(RunOut):
    metrics: Report


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=RUN_NOTE_MAX)] | None
    ) = Field(default=None)


def _run_out(run: AccuracyRun, author: Admin | None) -> RunOut:
    return RunOut(
        id=str(run.id),
        run_at=run.run_at,
        origin="dashboard" if run.origin == "dashboard" else "cli",
        settings_version=run.settings_version,
        inference_version=run.inference_version,
        classifier_version=run.classifier_version,
        git_sha=run.git_sha,
        label_count=run.label_count,
        passed=run.passed,
        note=run.note,
        recorded_by=AdminRef(id=str(author.id), name=author.display_name) if author else None,
    )


@router.get("/runs", response_model=list[RunOut], summary="Recorded measurements, newest first")
async def list_runs(principal: CurrentPrincipal, db: DbSession) -> list[RunOut]:
    del principal
    rows = (
        await db.execute(
            select(AccuracyRun, Admin)
            .outerjoin(Admin, Admin.id == AccuracyRun.recorded_by)
            .order_by(AccuracyRun.run_at.desc())
            .limit(MAX_RUNS)
        )
    ).all()
    return [_run_out(run, author) for run, author in rows]


@router.get("/runs/{run_id}", response_model=RunDetail, summary="One recorded measurement")
async def get_run(run_id: str, principal: CurrentPrincipal, db: DbSession) -> RunDetail:
    del principal
    row = (
        await db.execute(
            select(AccuracyRun, Admin)
            .outerjoin(Admin, Admin.id == AccuracyRun.recorded_by)
            .where(AccuracyRun.id == _uuid(run_id))
        )
    ).first()
    if row is None:
        raise NotFound
    run, author = row
    return RunDetail(
        **_run_out(run, author).model_dump(), metrics=Report.model_validate(run.metrics)
    )


@router.post(
    "/runs",
    response_model=RunDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Score the active version now and record it (owner only)",
)
async def record_run(payload: RunIn, principal: OwnerPrincipal, db: DbSession) -> RunDetail:
    result = await store.report(db)
    run = await store.record_run(
        db, result, origin="dashboard", actor=principal.admin.id, note=payload.note, git_sha=None
    )
    return RunDetail(**_run_out(run, principal.admin).model_dump(), metrics=result)
