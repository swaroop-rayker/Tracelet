"""System Health's data-lifecycle endpoints: retention and backups (API section 10).

Every write is owner-only and audited (CLAUDE.md invariant 9). A purge is irreversible, so
it runs only against a preview the owner has seen (F10.AC12): the preview's ``as_of`` and
policy come back with the purge, and the purge deletes against exactly those cutoffs.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import asdict
from typing import Any, Self

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, text

from tracelet.audit import log as audit
from tracelet.auth.dependencies import (
    Config,
    CurrentPrincipal,
    DbSession,
    OwnerPrincipal,
    client_ip,
)
from tracelet.errors import BackupUnavailable, NotFound
from tracelet.lifecycle import backups, retention
from tracelet.lifecycle.models import Backup, BackupKind, BackupStatus, RestoreCheck, RestoreStatus
from tracelet.net import prefix_of

router = APIRouter(prefix="/api/v1/health", tags=["lifecycle"])


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


class PolicyModel(BaseModel):
    visit_days: int = Field(
        ge=8,
        le=3650,
        description="Visits older than this are deleted. At least 8: rollups re-settle the "
        "last 7 days, so a visit inside that window must still exist.",
    )
    ip_days: int = Field(ge=1, le=3650, description="The encrypted IP is cleared after this.")
    audit_days: int = Field(ge=1, le=3650, description="Audit rows older than this are deleted.")

    @model_validator(mode="after")
    def _ip_within_visit(self) -> Self:
        if self.ip_days > self.visit_days:
            msg = "ip_days cannot be longer than visit_days: the visit carrying the IP is gone."
            raise ValueError(msg)
        return self

    def policy(self) -> retention.Policy:
        return retention.Policy(**self.model_dump())


class CountsModel(BaseModel):
    visits: int
    visit_candidates: int
    ip_addresses: int
    audit_rows: int
    delivered_alerts: int


class LastPurgeOut(BaseModel):
    at: dt.datetime
    trigger: str
    counts: CountsModel
    by: str | None


class RetentionOut(BaseModel):
    policy: PolicyModel
    delivered_alerts_days: int = Field(
        description="Delivered alerts are kept this long; not configurable (ADR-0014)."
    )
    rollups: str = Field(description="Always 'kept forever' (NFR5.AC4).")
    updated_at: dt.datetime
    purge_running: bool
    last_purge: LastPurgeOut | None


class CutoffsModel(BaseModel):
    visits: dt.datetime
    ip: dt.datetime
    audit: dt.datetime
    outbox: dt.datetime


class PreviewOut(BaseModel):
    as_of: dt.datetime = Field(description="Send this back, with the policy, to purge.")
    policy: PolicyModel
    cutoffs: CutoffsModel
    counts: CountsModel


class PurgeIn(BaseModel):
    as_of: dt.datetime
    policy: PolicyModel


class PurgeAccepted(BaseModel):
    as_of: dt.datetime
    status: str = "started"


def _policy_model(policy: retention.Policy) -> PolicyModel:
    return PolicyModel(**asdict(policy))


async def _last_purge(db: DbSession) -> LastPurgeOut | None:
    row = (
        await db.execute(
            text(
                "SELECT occurred_at, detail, actor_admin_id FROM audit_log WHERE action = :a "
                "ORDER BY occurred_at DESC LIMIT 1"
            ),
            {"a": audit.Action.RETENTION_PURGED},
        )
    ).one_or_none()
    if row is None:
        return None
    at, detail, actor = row
    detail = dict(detail or {})
    return LastPurgeOut(
        at=at,
        trigger=str(detail.get("trigger", "")),
        counts=CountsModel(**detail.get("counts", {})),
        by=str(actor) if actor else None,
    )


async def _retention_out(db: DbSession) -> RetentionOut:
    row = await retention.policy_row(db)
    return RetentionOut(
        policy=PolicyModel(
            visit_days=row.visit_days, ip_days=row.ip_days, audit_days=row.audit_days
        ),
        delivered_alerts_days=retention.OUTBOX_DONE_DAYS,
        rollups="kept forever",
        updated_at=row.updated_at,
        purge_running=await retention.purge_running(db),
        last_purge=await _last_purge(db),
    )


def _audit_context(request: Request, config: Config) -> dict[str, Any]:
    return {
        "actor_ip_prefix": prefix_of(client_ip(request, config)),
        "trace_id": getattr(request.state, "trace_id", None),
    }


@router.get(
    "/retention",
    response_model=RetentionOut,
    summary="Retention periods, and the last purge",
)
async def get_retention(principal: CurrentPrincipal, db: DbSession) -> RetentionOut:
    del principal
    return await _retention_out(db)


@router.patch(
    "/retention",
    response_model=RetentionOut,
    summary="Change the retention periods (owner only)",
    description=(
        "Takes all three periods. A shorter IP period also applies to visits already "
        "stored. Nothing is deleted here: the nightly purge, or a previewed purge, does that."
    ),
)
async def change_retention(
    body: PolicyModel,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    config: Config,
) -> RetentionOut:
    before, after = await retention.change_policy(db, body.policy(), actor=principal.admin.id)
    if before != after:
        await audit.record(
            db,
            action=audit.Action.RETENTION_CHANGED,
            actor_admin_id=principal.admin.id,
            target_type="retention_policy",
            target_id="1",
            detail={"from": asdict(before), "to": asdict(after)},
            **_audit_context(request, config),
        )
    return await _retention_out(db)


@router.post(
    "/retention/preview",
    response_model=PreviewOut,
    summary="Dry run: exactly what a purge now would delete (owner only)",
    description="Deletes nothing. Its `as_of` and `policy` are what the purge needs.",
)
async def preview_retention(
    request: Request, principal: OwnerPrincipal, db: DbSession, config: Config
) -> PreviewOut:
    result = await retention.preview(db)
    await audit.record(
        db,
        action=audit.Action.RETENTION_PREVIEWED,
        actor_admin_id=principal.admin.id,
        target_type="retention_policy",
        target_id="1",
        detail={"as_of": result.as_of.isoformat(), "counts": asdict(result.counts)},
        **_audit_context(request, config),
    )
    return PreviewOut(
        as_of=result.as_of,
        policy=_policy_model(result.policy),
        cutoffs=CutoffsModel(**asdict(result.cutoffs)),
        counts=CountsModel(**asdict(result.counts)),
    )


@router.post(
    "/retention/purge",
    response_model=PurgeAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Purge what a preview showed (owner only)",
    description=(
        "Send the preview's `as_of` and `policy`. Refused with `409 "
        "RETENTION_PREVIEW_STALE` if the preview is over 15 minutes old or the periods "
        "have changed, and `409 LIFECYCLE_JOB_RUNNING` if a purge is running. Runs in "
        "the background; the counts deleted are in the audit row `retention.purged` and "
        "in `GET /retention` as `last_purge`."
    ),
)
async def purge_retention(
    body: PurgeIn, request: Request, principal: OwnerPrincipal, db: DbSession, config: Config
) -> PurgeAccepted:
    policy = body.policy.policy()
    retention.check_preview(body.as_of, policy, await retention.current_policy(db))
    await retention.start_manual_purge(
        as_of=body.as_of,
        policy=policy,
        actor=principal.admin.id,
        trace_id=getattr(request.state, "trace_id", None),
        settings=config,
    )
    return PurgeAccepted(as_of=body.as_of)


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------


class RestoreCheckOut(BaseModel):
    id: int
    backup_id: uuid.UUID
    kind: BackupKind
    status: RestoreStatus
    mismatches: dict[str, Any] | None
    error: str | None
    started_at: dt.datetime
    finished_at: dt.datetime | None


class BackupOut(BaseModel):
    id: uuid.UUID
    kind: BackupKind
    status: BackupStatus
    file_name: str | None
    size_bytes: int | None
    sha256: str | None
    tables: int | None = Field(description="Tables in the dump.")
    rows: int | None = Field(description="Rows in the dump, from its own snapshot.")
    error: str | None
    started_at: dt.datetime
    finished_at: dt.datetime | None
    last_downloaded_at: dt.datetime | None
    last_restore_check: RestoreCheckOut | None = Field(
        description="The newest restore check of this backup. A backup never restored is "
        "not yet a backup (F12.AC10)."
    )


class DownloadReminder(BaseModel):
    last_downloaded_at: dt.datetime | None
    reminder_days: int
    overdue: bool = Field(
        description="True when no backup has been downloaded within `reminder_days`: the "
        "download is the only copy off this machine (F12.AC11, RISKS R11)."
    )


class BackupsOut(BaseModel):
    backups: list[BackupOut]
    last_restore_check: RestoreCheckOut | None
    backup_running: bool
    restore_check_running: bool
    download: DownloadReminder


class Started(BaseModel):
    id: str
    status: str = "started"


def _check_out(row: RestoreCheck) -> RestoreCheckOut:
    return RestoreCheckOut.model_validate(row, from_attributes=True)


def _backup_out(row: Backup, check: RestoreCheck | None) -> BackupOut:
    counts = row.row_counts
    return BackupOut(
        id=row.id,
        kind=row.kind,
        status=row.status,
        file_name=row.file_name,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        tables=len(counts) if counts is not None else None,
        rows=sum(int(n) for n in counts.values()) if counts is not None else None,
        error=row.error,
        started_at=row.started_at,
        finished_at=row.finished_at,
        last_downloaded_at=row.last_downloaded_at,
        last_restore_check=_check_out(check) if check is not None else None,
    )


@router.get("/backups", response_model=BackupsOut, summary="Backups and restore checks")
async def list_backups(principal: CurrentPrincipal, db: DbSession, config: Config) -> BackupsOut:
    del principal
    rows = list(
        (await db.execute(select(Backup).order_by(Backup.started_at.desc()).limit(60))).scalars()
    )
    checks = list(
        (
            await db.execute(
                select(RestoreCheck).order_by(RestoreCheck.started_at.desc()).limit(200)
            )
        ).scalars()
    )
    newest_check: dict[uuid.UUID, RestoreCheck] = {}
    for check in checks:
        newest_check.setdefault(check.backup_id, check)
    last_download = max((r.last_downloaded_at for r in rows if r.last_downloaded_at), default=None)
    reminder = dt.timedelta(days=config.backup_download_reminder_days)
    return BackupsOut(
        backups=[_backup_out(r, newest_check.get(r.id)) for r in rows],
        last_restore_check=_check_out(checks[0]) if checks else None,
        backup_running=any(r.status is BackupStatus.RUNNING for r in rows),
        restore_check_running=any(c.status is RestoreStatus.RUNNING for c in checks),
        download=DownloadReminder(
            last_downloaded_at=last_download,
            reminder_days=config.backup_download_reminder_days,
            overdue=last_download is None or dt.datetime.now(dt.UTC) - last_download > reminder,
        ),
    )


@router.post(
    "/backups",
    response_model=Started,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Back up now (owner only)",
    description=(
        "`409 LIFECYCLE_JOB_RUNNING` if a backup is running. The result is its row in "
        "`GET /backups`."
    ),
)
async def start_backup(
    request: Request, principal: OwnerPrincipal, db: DbSession, config: Config
) -> Started:
    backup_id = await backups.begin_backup(BackupKind.MANUAL, principal.admin.id)
    await audit.record(
        db,
        action=audit.Action.BACKUP_REQUESTED,
        actor_admin_id=principal.admin.id,
        target_type="backup",
        target_id=str(backup_id),
        **_audit_context(request, config),
    )
    backups.in_background(backups.run_backup(backup_id, config), name="backup:manual")
    return Started(id=str(backup_id))


@router.get(
    "/backups/{backup_id}/download",
    response_class=FileResponse,
    summary="Download a backup (owner only)",
    description="The only copy that leaves this machine (F12.AC11). Audited.",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_backup(
    backup_id: uuid.UUID,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    config: Config,
) -> FileResponse:
    row = await db.get(Backup, backup_id)
    if row is None:
        raise NotFound("No such backup.")
    path = backups.file_of(row, config)
    row.last_downloaded_at = dt.datetime.now(dt.UTC)
    await audit.record(
        db,
        action=audit.Action.BACKUP_DOWNLOADED,
        actor_admin_id=principal.admin.id,
        target_type="backup",
        target_id=str(backup_id),
        detail={"file_name": row.file_name, "sha256": row.sha256},
        **_audit_context(request, config),
    )
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"tracelet-{row.file_name}",
        headers={"X-Content-SHA256": row.sha256 or ""},
    )


@router.post(
    "/backups/{backup_id}/verify-restore",
    response_model=Started,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restore a backup into a scratch database and check it (owner only)",
    description=(
        "F12.AC10, ADR-0022. `409 BACKUP_UNAVAILABLE` for a backup without a file, `409 "
        "LIFECYCLE_JOB_RUNNING` if a check is running. The result is in `GET /backups`."
    ),
)
async def verify_restore(
    backup_id: uuid.UUID,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    config: Config,
) -> Started:
    row = await db.get(Backup, backup_id)
    if row is None:
        raise NotFound("No such backup.")
    if row.status is not BackupStatus.OK:
        msg = f"This backup is {row.status.value}; only a completed backup can be checked."
        raise BackupUnavailable(msg)
    check_id = await backups.begin_restore_check(backup_id, BackupKind.MANUAL, principal.admin.id)
    await audit.record(
        db,
        action=audit.Action.RESTORE_CHECK_REQUESTED,
        actor_admin_id=principal.admin.id,
        target_type="backup",
        target_id=str(backup_id),
        detail={"restore_check_id": check_id},
        **_audit_context(request, config),
    )
    backups.in_background(backups.run_restore_check(check_id, config), name="restore:manual")
    return Started(id=str(check_id))
