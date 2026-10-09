"""The database side of accuracy: labelled visits as cases, and recorded runs (ADR-0024).

Reads ``ground_truth_labels``, ``visits``, ``visit_candidates`` and ``asn_profiles``; writes
``accuracy_runs``. Never writes to ``visits``: a label is compared with the inference, never
applied to it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import ColumnElement, UnaryExpression, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.accuracy.metrics import Report, score
from tracelet.accuracy.models import AccuracyRun, GroundTruthLabel
from tracelet.accuracy.replay import Case, Truth, network_facts
from tracelet.capture.models import Classification, ConsentState, Visit, VisitStage
from tracelet.classify.config import classifier_version
from tracelet.errors import NotFound
from tracelet.inference import store as settings_store
from tracelet.inference.config import InferenceConfig, inference_version
from tracelet.inference.models import InferenceSettingsVersion, VisitCandidate
from tracelet.inference.sources import timezone
from tracelet.inference.types import AsnInfo, Candidate

# Generous: labels are owner-entered by hand, so hundreds, not thousands.
MAX_CASES: Final = 5000


@dataclass(frozen=True, slots=True)
class Loaded:
    cases: tuple[Case, ...]
    cant_tell: int
    # Labelled, but the visit is still in the inference queue: nothing to replay yet.
    pending: int


def _float(value: object) -> float | None:
    return None if value is None else float(str(value))


async def load_cases(db: AsyncSession, where: Sequence[ColumnElement[bool]] = ()) -> Loaded:
    """Every labelled visit (or those ``where`` selects, as clauses over ``Visit``)."""
    rows = (
        await db.execute(
            select(GroundTruthLabel, Visit)
            .join(Visit, Visit.id == GroundTruthLabel.visit_id)
            .where(*where)
            .order_by(GroundTruthLabel.labeled_at)
            .limit(MAX_CASES)
        )
    ).all()
    cant_tell = sum(1 for label, _ in rows if label.cant_tell)
    scored = [(label, visit) for label, visit in rows if not label.cant_tell]
    pending = sum(1 for _, visit in scored if visit.inferred_at is None)
    scored = [(label, visit) for label, visit in scored if visit.inferred_at is not None]
    if not scored:
        return Loaded(cases=(), cant_tell=cant_tell, pending=pending)

    ids = [visit.id for _, visit in scored]
    by_visit: dict[uuid.UUID, list[Candidate]] = {i: [] for i in ids}
    # By id: the order the engine produced them in, so ties break as they did.
    for row in (
        await db.execute(
            select(VisitCandidate)
            .where(VisitCandidate.visit_id.in_(ids))
            .order_by(VisitCandidate.id)
        )
    ).scalars():
        by_visit[row.visit_id].append(
            Candidate(
                source=row.source,
                level=row.level,
                country_code=row.country_code,
                admin1=row.admin1,
                admin2=row.admin2,
                city=row.city,
                lat=_float(row.lat),
                lng=_float(row.lng),
                raw_confidence=float(row.raw_confidence),
            )
        )

    profiles: dict[int, AsnInfo] = {}
    for asn in {visit.asn for _, visit in scored if visit.asn is not None}:
        profiles[asn] = settings_store.overlay_profile(
            AsnInfo(asn=asn), await settings_store.asn_profile(db, asn)
        )

    cases = tuple(
        Case(
            truth=Truth(
                country_code=label.true_country_code or "",
                admin1=label.true_admin1,
                admin2=label.true_admin2,
                city=label.true_city,
            ),
            consented=visit.consent_state is ConsentState.GRANTED,
            path="cloudflare" if visit.cf_colo else "direct",
            asn=network_facts(
                visit.asn,
                visit.asn_org,
                is_tor=bool(visit.is_tor),
                profile=profiles.get(visit.asn) if visit.asn is not None else None,
            ),
            tz_countries=timezone.countries_for(visit.tz_iana),
            candidates=tuple(by_visit[visit.id]),
            network=label.network,
            connection_kind=label.connection_kind,
            vpn_used=label.vpn_used,
        )
        for label, visit in scored
    )
    return Loaded(cases=cases, cant_tell=cant_tell, pending=pending)


async def settings_for(db: AsyncSession, version: int | None) -> tuple[int, InferenceConfig]:
    """The active version, or a retained one. ``NotFound`` for a version never saved."""
    if version is None:
        active = await settings_store.active_settings(db)
        return active.version, active.config
    row = (
        await db.execute(
            select(InferenceSettingsVersion).where(InferenceSettingsVersion.version == version)
        )
    ).scalar_one_or_none()
    if row is None:
        msg = f"There is no inference settings version {version}."
        raise NotFound(msg)
    return row.version, InferenceConfig.from_stored(row.settings)


def score_loaded(loaded: Loaded, version: int, config: InferenceConfig) -> Report:
    return score(
        loaded.cases,
        config,
        settings_version=version,
        inference_version=inference_version(version),
        classifier_version=classifier_version(version),
        cant_tell=loaded.cant_tell,
        pending=loaded.pending,
    )


async def report(
    db: AsyncSession,
    *,
    version: int | None = None,
    config: InferenceConfig | None = None,
    where: Sequence[ColumnElement[bool]] = (),
) -> Report:
    """Score the labels under a retained version (default: active), or under ``config`` --
    a proposed version that is not saved, numbered as the active one it would replace."""
    number, stored = await settings_for(db, version)
    return score_loaded(await load_cases(db, where), number, config or stored)


async def record_run(
    db: AsyncSession,
    result: Report,
    *,
    origin: Literal["cli", "dashboard"],
    actor: uuid.UUID | None,
    note: str | None,
    git_sha: str | None,
) -> AccuracyRun:
    assert result.settings_version is not None  # a recorded run always names its version
    run = AccuracyRun(
        origin=origin,
        settings_version=result.settings_version,
        inference_version=result.inference_version,
        classifier_version=result.classifier_version,
        git_sha=git_sha,
        label_count=result.label_count,
        metrics=result.model_dump(mode="json"),
        passed=result.passed,
        recorded_by=actor,
        note=note,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


def queue_clauses() -> list[ColumnElement[bool]]:
    """Visits worth offering for a label: inferred, not rate-limited, not a bot, unlabelled."""
    labelled = select(GroundTruthLabel.visit_id).scalar_subquery()
    return [
        Visit.inferred_at.is_not(None),
        Visit.stage != VisitStage.RATE_LIMITED,
        Visit.classification != Classification.BOT,
        Visit.id.not_in(labelled),
    ]


def queue_order(order: Literal["conflict", "recent"]) -> tuple[UnaryExpression[Any], ...]:
    """Most disagreement first (then newest), or newest first (API section 11)."""
    if order == "conflict":
        return (Visit.conflict_score.desc().nulls_last(), Visit.occurred_at.desc(), Visit.id.asc())
    return (Visit.occurred_at.desc(), Visit.id.asc())


async def label_of_visit(db: AsyncSession, visit_id: uuid.UUID) -> GroundTruthLabel | None:
    return (
        await db.execute(select(GroundTruthLabel).where(GroundTruthLabel.visit_id == visit_id))
    ).scalar_one_or_none()


async def label_counts(db: AsyncSession) -> tuple[int, int]:
    """(labels, of which "can't tell")."""
    total, cant = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(GroundTruthLabel.cant_tell.is_(True)),
            ).select_from(GroundTruthLabel)
        )
    ).one()
    return int(total), int(cant)
