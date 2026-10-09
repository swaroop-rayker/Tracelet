"""Label responses, shared by the ground-truth API and the visit detail (API sections 7, 11).

Separate from the router so ``capture.visits_router`` can show a visit's label without an
import cycle.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.accuracy.models import GroundTruthLabel
from tracelet.auth.models import Admin
from tracelet.capture.models import ConsentState, Link, Visit


class LabelLink(BaseModel):
    id: str
    slug: str
    label: str


class AdminRef(BaseModel):
    id: str
    name: str


class TruthOut(BaseModel):
    country_code: str | None
    admin1: str | None
    admin2: str | None
    city: str | None


class RecordedOut(BaseModel):
    """What the visit holds -- from the version it was inferred under, not a replay."""

    inference_version: str | None
    strict: dict[str, str | None]
    advisory: dict[str, str | None]


class LabelOut(BaseModel):
    id: str
    visit_id: str
    occurred_at: dt.datetime
    link: LabelLink
    consent_state: ConsentState
    cant_tell: bool
    truth: TruthOut
    has_coordinates: bool
    connection_kind: str | None
    vpn_used: bool | None
    network: str | None
    notes: str | None
    labeled_by: AdminRef | None
    labeled_at: dt.datetime
    updated_at: dt.datetime
    recorded: RecordedOut


class LabelList(BaseModel):
    items: list[LabelOut]
    total: int
    cant_tell: int


def label_out(label: GroundTruthLabel, visit: Visit, link: Link, author: Admin | None) -> LabelOut:
    return LabelOut(
        id=str(label.id),
        visit_id=str(label.visit_id),
        occurred_at=visit.occurred_at,
        link=LabelLink(id=str(link.id), slug=link.slug, label=link.label),
        consent_state=visit.consent_state,
        cant_tell=label.cant_tell,
        truth=TruthOut(
            country_code=label.true_country_code,
            admin1=label.true_admin1,
            admin2=label.true_admin2,
            city=label.true_city,
        ),
        has_coordinates=label.true_lat is not None,
        connection_kind=label.connection_kind,
        vpn_used=label.vpn_used,
        network=label.network,
        notes=label.notes,
        labeled_by=AdminRef(id=str(author.id), name=author.display_name) if author else None,
        labeled_at=label.labeled_at,
        updated_at=label.updated_at,
        recorded=RecordedOut(
            inference_version=visit.inference_version,
            strict={
                "country_code": visit.strict_country_code,
                "admin1": visit.strict_admin1,
                "admin2": visit.strict_admin2,
                "city": visit.strict_city,
            },
            advisory={
                "country_code": visit.advisory_country_code,
                "admin1": visit.advisory_admin1,
                "admin2": visit.advisory_admin2,
                "city": visit.advisory_city,
            },
        ),
    )


def label_select() -> Select[tuple[GroundTruthLabel, Visit, Link, Admin]]:
    return (
        select(GroundTruthLabel, Visit, Link, Admin)
        .join(Visit, Visit.id == GroundTruthLabel.visit_id)
        .join(Link, Link.id == Visit.link_id)
        .outerjoin(Admin, Admin.id == GroundTruthLabel.labeled_by)
    )


async def label_for_visit(db: AsyncSession, visit_id: uuid.UUID) -> LabelOut | None:
    """The visit's label, for the visit detail (API section 7)."""
    row = (await db.execute(label_select().where(GroundTruthLabel.visit_id == visit_id))).first()
    return label_out(*row) if row is not None else None
