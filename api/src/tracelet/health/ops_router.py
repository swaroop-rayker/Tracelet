"""System Health: host metrics, geo databases, degradation and rate limits (API section 10).

Reads are open to any admin; writes are the owner's and audited (CLAUDE.md invariant 9).
The public liveness and readiness probes are ``health/router.py``; this surface is
authenticated and says much more.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Final

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql as pg

from tracelet.audit import log as audit
from tracelet.auth.dependencies import (
    Config,
    CurrentPrincipal,
    DbSession,
    OwnerPrincipal,
    client_ip,
)
from tracelet.errors import FieldError, LifecycleJobRunning, NotFound, ValidationFailed
from tracelet.health import databases, degradation, system
from tracelet.inference.geodb import maintenance
from tracelet.inference.geodb.catalog import BY_NAME
from tracelet.net import prefix_of
from tracelet.notify.settings import AppSetting
from tracelet.ratelimit import gcra, registry

router = APIRouter(prefix="/api/v1/health", tags=["system health"])

_TASKS: set[asyncio.Task[object]] = set()


# ---------------------------------------------------------------------------
# Host metrics
# ---------------------------------------------------------------------------


class UsageOut(BaseModel):
    used: int
    total: int
    percent: float
    warn_percent: int
    state: str = Field(description="ok, warn or critical against the thresholds.")


class DiskOut(UsageOut):
    path: str


class CpuOut(BaseModel):
    percent: float
    count: int
    load: tuple[float, float, float]


class TemperatureOut(BaseModel):
    celsius: float | None
    sensor: str | None
    reason: str | None = Field(description="Why there is no reading, when there is none (RW-5).")


class SystemOut(BaseModel):
    scope: str = Field(description="host, or container when the host's /proc is not mounted.")
    scope_reason: str | None
    sampled_at: dt.datetime
    cpu: CpuOut
    memory: UsageOut
    swap: UsageOut
    disk: DiskOut
    uptime_seconds: int
    database_bytes: int
    temperature: TemperatureOut
    poll_seconds: int = Field(description="How often the dashboard should ask again.")


POLL_SECONDS: Final = 15


def _state(percent: float, warn: int, critical: int | None = None) -> str:
    if critical is not None and percent >= critical:
        return "critical"
    return "warn" if percent >= warn else "ok"


@router.get("/system", response_model=SystemOut, summary="Host metrics (F10.AC1, F10.AC2)")
async def get_system(principal: CurrentPrincipal, db: DbSession, config: Config) -> SystemOut:
    del principal
    s = await system.sample(config)
    size = int((await db.execute(text("SELECT pg_database_size(current_database())"))).scalar_one())
    return SystemOut(
        scope=s.scope,
        scope_reason=s.scope_reason,
        sampled_at=s.sampled_at,
        cpu=CpuOut(percent=s.cpu_percent, count=s.cpu_count, load=s.load),
        memory=UsageOut(
            used=s.memory.used,
            total=s.memory.total,
            percent=s.memory.percent,
            warn_percent=config.memory_warn_percent,
            state=_state(s.memory.percent, config.memory_warn_percent),
        ),
        swap=UsageOut(
            used=s.swap.used,
            total=s.swap.total,
            percent=s.swap.percent,
            warn_percent=config.swap_warn_percent,
            state=_state(s.swap.percent, config.swap_warn_percent),
        ),
        disk=DiskOut(
            used=s.disk.used,
            total=s.disk.total,
            percent=s.disk.percent,
            warn_percent=config.disk_warn_percent,
            state=_state(s.disk.percent, config.disk_warn_percent, config.disk_critical_percent),
            path=s.disk_path,
        ),
        uptime_seconds=s.uptime_seconds,
        database_bytes=size,
        temperature=TemperatureOut(
            celsius=s.temperature.celsius, sensor=s.temperature.sensor, reason=s.temperature.reason
        ),
        poll_seconds=POLL_SECONDS,
    )


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


class ConditionOut(BaseModel):
    key: str
    severity: str = Field(description="critical, warning or notice.")
    title: str
    detail: str
    still_works: str


class DegradationOut(BaseModel):
    conditions: list[ConditionOut]
    checked_at: dt.datetime


@router.get(
    "/degradation",
    response_model=DegradationOut,
    summary="What is degraded now, for the banner (F10.AC14)",
)
async def get_degradation(
    principal: CurrentPrincipal, db: DbSession, config: Config
) -> DegradationOut:
    del principal
    found = await degradation.conditions(db, config)
    return DegradationOut(
        conditions=[
            ConditionOut(
                key=c.key,
                severity=c.severity,
                title=c.title,
                detail=c.detail,
                still_works=c.still_works,
            )
            for c in found
        ],
        checked_at=dt.datetime.now(dt.UTC),
    )


# ---------------------------------------------------------------------------
# Geo databases
# ---------------------------------------------------------------------------


class InstalledOut(BaseModel):
    version: str | None
    released_at: dt.datetime | None
    installed_at: dt.datetime | None
    size_bytes: int | None
    sha256: str | None


class AttemptOut(BaseModel):
    status: str
    at: dt.datetime
    error: str | None


class DatabaseOut(BaseModel):
    name: str
    kind: str
    feeds: str | None = Field(description="The inference source it feeds, if any.")
    attribution: str
    configured: bool = Field(description="False when its vendor credentials are not set.")
    staleness_days: int
    verdict: str = Field(description="up_to_date, stale, missing or not_configured.")
    age_days: int | None
    installed: InstalledOut | None
    last_attempt: AttemptOut | None
    updating: bool


class DatabasesOut(BaseModel):
    databases: list[DatabaseOut]


@router.get("/databases", response_model=DatabasesOut, summary="Geo databases (F10.AC3)")
async def get_databases(principal: CurrentPrincipal, db: DbSession, config: Config) -> DatabasesOut:
    del principal
    return DatabasesOut(
        databases=[
            DatabaseOut(
                name=d.name,
                kind=d.kind,
                feeds=d.feeds,
                attribution=d.attribution,
                configured=d.configured,
                staleness_days=d.staleness_days,
                verdict=d.verdict,
                age_days=d.age_days,
                installed=(
                    InstalledOut(
                        version=d.installed.version,
                        released_at=d.installed.released_at,
                        installed_at=d.installed.installed_at,
                        size_bytes=d.installed.size_bytes,
                        sha256=d.installed.sha256,
                    )
                    if d.installed
                    else None
                ),
                last_attempt=(
                    AttemptOut(
                        status=d.last_attempt.status,
                        at=d.last_attempt.at,
                        error=d.last_attempt.error,
                    )
                    if d.last_attempt
                    else None
                ),
                updating=d.updating,
            )
            for d in await databases.states(db, config)
        ]
    )


class UpdateStarted(BaseModel):
    name: str
    status: str = "started"


@router.post(
    "/databases/{name}/update",
    response_model=UpdateStarted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update one geo database now (owner only)",
    description=(
        "F10.AC4. Downloads, verifies in a memory-capped subprocess and swaps atomically; a "
        "failure leaves the previous version serving and is shown as the last attempt. `409 "
        "LIFECYCLE_JOB_RUNNING` while that database is already updating."
    ),
)
async def update_database(
    name: str, request: Request, principal: OwnerPrincipal, db: DbSession, config: Config
) -> UpdateStarted:
    if name not in BY_NAME:
        raise NotFound("No such geo database.")
    state = next(d for d in await databases.states(db, config) if d.name == name)
    if state.updating:
        raise LifecycleJobRunning(f"{name} is already updating.")
    await audit.record(
        db,
        action=audit.Action.GEO_DB_UPDATE_REQUESTED,
        actor_admin_id=principal.admin.id,
        actor_ip_prefix=prefix_of(client_ip(request, config)),
        target_type="geo_database",
        target_id=name,
        trace_id=getattr(request.state, "trace_id", None),
    )
    task: asyncio.Task[object] = asyncio.create_task(
        maintenance.update_all(config, only=[name], force=True), name=f"geodb:{name}"
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return UpdateStarted(name=name)


# ---------------------------------------------------------------------------
# Rate limits (F11.AC9)
# ---------------------------------------------------------------------------


class LimitValue(BaseModel):
    per_period: int
    period_seconds: int
    burst: int


class LimitOut(LimitValue):
    name: str
    group: str
    label: str
    description: str
    default: LimitValue
    overridden: bool
    ceiling_per_second: float | None


class RateLimitsOut(BaseModel):
    limits: list[LimitOut]
    applies_within_seconds: int = Field(description="Each worker re-reads the limits this often.")


class RateLimitsChange(BaseModel):
    limits: dict[str, LimitValue | None] = Field(
        description="By name: the new value, or null to go back to the default. Names left "
        "out are unchanged."
    )


def _value(limit: gcra.Limit) -> LimitValue:
    return LimitValue(
        per_period=limit.per_period,
        period_seconds=int(limit.period.total_seconds()),
        burst=limit.burst,
    )


async def _stored(db: DbSession) -> dict[str, dict[str, int]]:
    value = (
        await db.execute(select(AppSetting.value).where(AppSetting.key == gcra.OVERRIDES_KEY))
    ).scalar_one_or_none()
    return {str(k): dict(v) for k, v in (value or {}).items() if isinstance(v, dict)}


async def _limits_out(db: DbSession) -> RateLimitsOut:
    overrides = gcra.parse_overrides(await _stored(db))
    return RateLimitsOut(
        limits=[
            LimitOut(
                name=e.limit.name,
                group=e.group,
                label=e.label,
                description=e.description,
                default=_value(e.limit),
                overridden=e.limit.name in overrides,
                ceiling_per_second=e.ceiling_per_second,
                **_value(overrides.get(e.limit.name, e.limit)).model_dump(),
            )
            for e in registry.REGISTRY
        ],
        applies_within_seconds=int(gcra.OVERRIDES_TTL_S),
    )


@router.get("/ratelimits", response_model=RateLimitsOut, summary="Rate limits in force")
async def get_ratelimits(principal: CurrentPrincipal, db: DbSession) -> RateLimitsOut:
    del principal
    return await _limits_out(db)


@router.patch(
    "/ratelimits",
    response_model=RateLimitsOut,
    summary="Change rate limits (owner only)",
    description=(
        "F11.AC9: no redeploy; every worker applies the change within "
        "`applies_within_seconds`. Audited `settings.changed` with the old and new values. "
        "An outbound limit may not exceed its third party's own terms."
    ),
)
async def change_ratelimits(
    body: RateLimitsChange,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    config: Config,
) -> RateLimitsOut:
    errors = [
        FieldError(field=f"limits.{name}", code="INVALID_LIMIT", message=reason)
        for name, value in body.limits.items()
        if (
            reason := (
                f"There is no rate limit named {name!r}."
                if name not in registry.BY_NAME
                else registry.problem(name, value.per_period, value.period_seconds, value.burst)
                if value is not None
                else None
            )
        )
    ]
    if errors:
        raise ValidationFailed("Some rate limits were refused; nothing was saved.", errors=errors)
    before = await _stored(db)
    after = dict(before)
    for name, value in body.limits.items():
        default = registry.BY_NAME[name].limit
        if value is None or value == _value(default):
            after.pop(name, None)
        else:
            after[name] = value.model_dump()
    if after != before:
        await db.execute(
            pg.insert(AppSetting)
            .values(key=gcra.OVERRIDES_KEY, value=after, updated_by=principal.admin.id)
            .on_conflict_do_update(
                index_elements=[AppSetting.key],
                set_={"value": after, "updated_by": principal.admin.id, "updated_at": func.now()},
            )
        )
        await audit.record(
            db,
            action=audit.Action.SETTINGS_CHANGED,
            actor_admin_id=principal.admin.id,
            actor_ip_prefix=prefix_of(client_ip(request, config)),
            target_type="app_settings",
            target_id=gcra.OVERRIDES_KEY,
            trace_id=getattr(request.state, "trace_id", None),
            detail={"key": gcra.OVERRIDES_KEY, "from": before, "to": after},
        )
        await gcra.reload_overrides(db)  # this worker at once; the other within the TTL
    return await _limits_out(db)
