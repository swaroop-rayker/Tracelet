"""System Health's data-lifecycle endpoints: retention and backups (API section 10).

Every write is owner-only and audited (CLAUDE.md invariant 9). A purge is irreversible, so
it runs only against a preview the owner has seen (F10.AC12): the preview's ``as_of`` and
policy come back with the purge, and the purge deletes against exactly those cutoffs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Any, Self

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from tracelet.audit import log as audit
from tracelet.auth.dependencies import (
    Config,
    CurrentPrincipal,
    DbSession,
    OwnerPrincipal,
    client_ip,
)
from tracelet.lifecycle import retention
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
    body: PurgeIn, request: Request, principal: OwnerPrincipal, db: DbSession
) -> PurgeAccepted:
    policy = body.policy.policy()
    retention.check_preview(body.as_of, policy, await retention.current_policy(db))
    await retention.start_manual_purge(
        as_of=body.as_of,
        policy=policy,
        actor=principal.admin.id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return PurgeAccepted(as_of=body.as_of)
