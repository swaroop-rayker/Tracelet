"""Liveness and readiness probes.

Two endpoints with deliberately different jobs (F15.AC5):

``/healthz`` -- **liveness.** Is the process alive? No auth, no detail, no
dependency checks. It must stay cheap and must not fail because the database is
down: a container that is alive but degraded should keep serving, because the
redirect must never fail (CLAUDE.md invariant 1). Killing the container here
would make an outage worse.

``/readyz`` -- **readiness.** Should this instance receive traffic? Checks the
database, PostGIS, and whether the applied migration matches the head the code
expects. Returns 503 when not ready.

The detailed operational metrics for the System Health page -- CPU, RAM, disk,
geo-database freshness -- are a separate, authenticated surface arriving in M7
under ``/api/v1/health/*``. These two endpoints are public on purpose and
therefore say as little as possible.
"""

from __future__ import annotations

from typing import Literal

import structlog
from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from tracelet.config import get_settings
from tracelet.db.engine import database_reachable, migration_state, postgis_available

log = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"]


class CheckResult(BaseModel):
    ok: bool
    detail: str


class ReadinessResponse(BaseModel):
    ready: bool
    environment: str
    checks: dict[str, CheckResult]


@router.get(
    "/healthz",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description=(
        "Is the process alive. No auth, no detail, no dependency checks. "
        "Deliberately does not fail when the database is down, because a degraded "
        "instance must keep serving redirects."
    ),
)
async def healthz() -> LivenessResponse:
    return LivenessResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Should this instance receive traffic. Verifies the database is reachable, "
        "PostGIS is installed, and the applied migration matches the head the "
        "deployed code expects. Returns 503 when any check fails."
    ),
    responses={503: {"model": ReadinessResponse, "description": "Not ready"}},
)
async def readyz(response: Response) -> ReadinessResponse:
    settings = get_settings()

    db_ok, db_detail = await database_reachable()

    # Short-circuit: PostGIS and the migration check both need a connection, so
    # running them against an unreachable database only produces noise.
    if db_ok:
        gis_ok, gis_detail = await postgis_available()
        migrations = await migration_state()
        migration_ok, migration_detail = migrations.up_to_date, migrations.detail
    else:
        gis_ok, gis_detail = False, "skipped: database unreachable"
        migration_ok, migration_detail = False, "skipped: database unreachable"

    checks = {
        "database": CheckResult(ok=db_ok, detail=db_detail),
        "postgis": CheckResult(ok=gis_ok, detail=gis_detail),
        "migrations": CheckResult(ok=migration_ok, detail=migration_detail),
    }
    ready = all(check.ok for check in checks.values())

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        log.warning(
            "not_ready",
            failing=[name for name, check in checks.items() if not check.ok],
        )

    return ReadinessResponse(ready=ready, environment=settings.env, checks=checks)
