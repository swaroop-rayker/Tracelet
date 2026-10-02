"""Inference settings: read, change, roll back (API section 10, F4.AC14).

A change never edits a version -- it creates the next one and activates it, and the
previous version stays in the table forever (the application role cannot rewrite or
delete one; migration 0005). Rolling back reactivates an existing version. Both are
owner-only and both write an audit row (CLAUDE.md invariant 9).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from tracelet.audit import log as audit
from tracelet.auth.dependencies import (
    Config,
    CurrentPrincipal,
    DbSession,
    OwnerPrincipal,
    client_ip,
)
from tracelet.errors import NotFound
from tracelet.inference import store
from tracelet.inference.config import ENGINE_REVISION, InferenceConfig, inference_version
from tracelet.net import prefix_of

router = APIRouter(prefix="/api/v1/health/inference", tags=["inference"])


class SettingsVersionOut(BaseModel):
    version: int
    is_active: bool
    note: str | None
    created_at: dt.datetime
    created_by: str | None


class InferenceSettingsOut(BaseModel):
    engine_revision: str
    active_version: int
    inference_version: str = Field(
        description="What a visit inferred now is stamped with (F4.AC16)."
    )
    settings: InferenceConfig
    versions: list[SettingsVersionOut]


class InferenceSettingsChange(BaseModel):
    settings: InferenceConfig = Field(
        description="The complete settings object. It becomes the next version as a whole."
    )
    note: str | None = Field(default=None, max_length=500)


def _changed_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    """Dotted paths that differ, for the audit row: what changed, not only that it did."""
    if isinstance(before, dict) and isinstance(after, dict):
        paths: list[str] = []
        for key in sorted(set(before) | set(after)):
            paths += _changed_paths(before.get(key), after.get(key), f"{prefix}{key}.")
        return paths
    return [] if before == after else [prefix.rstrip(".")]


async def _out(db: DbSession) -> InferenceSettingsOut:
    active = await store.active_settings(db)
    return InferenceSettingsOut(
        engine_revision=ENGINE_REVISION,
        active_version=active.version,
        inference_version=inference_version(active.version),
        settings=active.config,
        versions=[
            SettingsVersionOut(
                version=v.version,
                is_active=v.is_active,
                note=v.note,
                created_at=v.created_at,
                created_by=str(v.created_by) if v.created_by else None,
            )
            for v in await store.list_versions(db)
        ],
    )


@router.get(
    "",
    response_model=InferenceSettingsOut,
    summary="The active inference settings and every retained version",
)
async def get_settings(principal: CurrentPrincipal, db: DbSession) -> InferenceSettingsOut:
    del principal
    return await _out(db)


@router.patch(
    "",
    response_model=InferenceSettingsOut,
    summary="Save a new settings version and make it active (owner only)",
    description=(
        "Never edits a version in place: the submitted settings become the next version, "
        "and every earlier one stays available for rollback (F4.AC14). Visits inferred "
        "from now on carry the new `inference_version`; visits already inferred keep "
        "theirs."
    ),
)
async def change_settings(
    change: InferenceSettingsChange,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> InferenceSettingsOut:
    before = await store.active_settings(db)
    row = await store.save_new_version(
        db, change.settings, note=change.note, actor=principal.admin.id
    )
    await audit.record(
        db,
        action=audit.Action.INFERENCE_SETTINGS_CHANGED,
        actor_admin_id=principal.admin.id,
        actor_ip_prefix=prefix_of(client_ip(request, settings)),
        target_type="inference_settings",
        target_id=str(row.version),
        trace_id=getattr(request.state, "trace_id", None),
        detail={
            "from_version": before.version,
            "to_version": row.version,
            "note": change.note,
            "changed": _changed_paths(
                before.config.model_dump(mode="json"), change.settings.model_dump(mode="json")
            ),
        },
    )
    return await _out(db)


@router.post(
    "/rollback/{version}",
    response_model=InferenceSettingsOut,
    summary="Reactivate an earlier settings version (owner only)",
)
async def rollback(
    version: int,
    request: Request,
    principal: OwnerPrincipal,
    db: DbSession,
    settings: Config,
) -> InferenceSettingsOut:
    before = await store.active_settings(db)
    target = await store.activate_version(db, version)
    if target is None:
        msg = f"There is no inference settings version {version}."
        raise NotFound(msg)
    if target.version != before.version:
        await audit.record(
            db,
            action=audit.Action.INFERENCE_SETTINGS_ROLLED_BACK,
            actor_admin_id=principal.admin.id,
            actor_ip_prefix=prefix_of(client_ip(request, settings)),
            target_type="inference_settings",
            target_id=str(target.version),
            trace_id=getattr(request.state, "trace_id", None),
            detail={"from_version": before.version, "to_version": target.version},
        )
    return await _out(db)
