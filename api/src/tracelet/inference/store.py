"""Database access for inference's configuration and reference data.

* ``inference_settings`` -- versioned tuning (F4.AC14). Version 1 is the code default,
  written the first time anything asks for the active version. Every later change is a
  **new** row; the application role cannot rewrite or delete one (migration 0005).
* ``rdns_city_codes`` -- the S6 lexicon, seeded from the versioned data file and
  editable from the dashboard (DATA_MODEL section 8.2).
* ``asn_profiles`` -- read here, computed after each geo-database update.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracelet.inference.config import DEFAULT_CONFIG, InferenceConfig
from tracelet.inference.models import AsnProfile, InferenceSettingsVersion, RdnsCityCode
from tracelet.inference.sources.rdns import Lexicon, LexiconEntry, seed_entries
from tracelet.inference.types import AsnInfo

# Serialises every change of the active version, so two owners saving at once cannot
# both deactivate the old row and leave two actives racing for the partial index.
_SETTINGS_LOCK = "tracelet.inference_settings"


@dataclass(frozen=True, slots=True)
class ActiveSettings:
    version: int
    config: InferenceConfig


async def _lock(db: AsyncSession) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": _SETTINGS_LOCK})


async def active_settings(db: AsyncSession) -> ActiveSettings:
    row = (
        await db.execute(
            select(InferenceSettingsVersion).where(InferenceSettingsVersion.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if row is not None:
        return ActiveSettings(row.version, InferenceConfig.model_validate(row.settings))
    await _lock(db)
    # Seed version 1 from the code default. ON CONFLICT covers a concurrent seeder.
    await db.execute(
        insert(InferenceSettingsVersion)
        .values(
            version=1,
            settings=DEFAULT_CONFIG.model_dump(mode="json"),
            is_active=True,
            note="Built-in defaults (M3).",
        )
        .on_conflict_do_nothing(index_elements=["version"])
    )
    row = (
        await db.execute(
            select(InferenceSettingsVersion).where(InferenceSettingsVersion.is_active.is_(True))
        )
    ).scalar_one()
    return ActiveSettings(row.version, InferenceConfig.model_validate(row.settings))


async def save_new_version(
    db: AsyncSession, config: InferenceConfig, *, note: str | None, actor: uuid.UUID
) -> InferenceSettingsVersion:
    """Store ``config`` as the next version and make it active. The previous version is
    kept, inactive, forever."""
    await active_settings(db)  # guarantees version 1 exists before we number from it
    await _lock(db)
    next_version = (
        int(
            (await db.execute(select(func.max(InferenceSettingsVersion.version)))).scalar_one() or 0
        )
        + 1
    )
    await db.execute(
        update(InferenceSettingsVersion)
        .where(InferenceSettingsVersion.is_active.is_(True))
        .values(is_active=False)
    )
    row = InferenceSettingsVersion(
        version=next_version,
        settings=config.model_dump(mode="json"),
        is_active=True,
        note=note,
        created_by=actor,
    )
    db.add(row)
    await db.flush()
    return row


async def activate_version(db: AsyncSession, version: int) -> InferenceSettingsVersion | None:
    """Roll back (or forward) to an existing version. ``None`` if it does not exist."""
    await _lock(db)
    target = (
        await db.execute(
            select(InferenceSettingsVersion).where(InferenceSettingsVersion.version == version)
        )
    ).scalar_one_or_none()
    if target is None or target.is_active:
        return target
    await db.execute(
        update(InferenceSettingsVersion)
        .where(InferenceSettingsVersion.is_active.is_(True))
        .values(is_active=False)
    )
    await db.execute(
        update(InferenceSettingsVersion)
        .where(InferenceSettingsVersion.id == target.id)
        .values(is_active=True)
    )
    await db.refresh(target)
    return target


async def list_versions(db: AsyncSession) -> list[InferenceSettingsVersion]:
    return list(
        (
            await db.execute(
                select(InferenceSettingsVersion).order_by(InferenceSettingsVersion.version.desc())
            )
        ).scalars()
    )


# ---------------------------------------------------------------------------
# The S6 lexicon
# ---------------------------------------------------------------------------


def _d(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


async def ensure_lexicon_seeded(db: AsyncSession) -> None:
    """Insert the seed file's entries if this lexicon version is not yet in the table.

    Idempotent, and never touches rows an admin has edited: a seed version is loaded
    once, and later edits are the table's own business.
    """
    version, _, entries = seed_entries()
    present = (
        await db.execute(
            select(func.count())
            .select_from(RdnsCityCode)
            .where(RdnsCityCode.lexicon_version == version)
        )
    ).scalar_one()
    if present:
        return
    await db.execute(
        insert(RdnsCityCode)
        .values(
            [
                {
                    "pattern": e.pattern,
                    "code": e.code,
                    "city": e.city,
                    "admin1": e.admin1,
                    "country_code": e.country_code,
                    "lat": _d(e.lat),
                    "lng": _d(e.lng),
                    "confidence": _d(e.confidence),
                    "isp_hint": e.isp_hint,
                    "lexicon_version": e.lexicon_version,
                }
                for e in entries
            ]
        )
        .on_conflict_do_nothing(constraint="uq_rdns_city_codes_pattern")
    )


async def load_lexicon(db: AsyncSession) -> Lexicon:
    await ensure_lexicon_seeded(db)
    _, gate, _ = seed_entries()
    rows = (
        await db.execute(select(RdnsCityCode).where(RdnsCityCode.is_active.is_(True)))
    ).scalars()
    entries = [
        LexiconEntry(
            pattern=r.pattern,
            code=r.code,
            city=r.city,
            admin1=r.admin1,
            country_code=r.country_code,
            lat=float(r.lat) if r.lat is not None else None,
            lng=float(r.lng) if r.lng is not None else None,
            confidence=float(r.confidence),
            lexicon_version=r.lexicon_version,
            isp_hint=r.isp_hint,
        )
        for r in rows
    ]
    return Lexicon(entries, gate)


# ---------------------------------------------------------------------------
# ASN profiles
# ---------------------------------------------------------------------------


async def asn_profile(db: AsyncSession, asn: int | None) -> AsnProfile | None:
    if asn is None:
        return None
    return await db.get(AsnProfile, asn)


def overlay_profile(info: AsnInfo, profile: AsnProfile | None) -> AsnInfo:
    """Add what ``asn_profiles`` knows -- the registry-artifact centroid, and any class the
    databases' own view of the ASN establishes. A profile can add a class but never clear
    one the curated list set: a hosting ASN stays hosting whatever a database thinks."""
    if profile is None:
        return info
    return AsnInfo(
        asn=info.asn,
        org=profile.org or info.org,
        is_mobile=profile.is_mobile or info.is_mobile,
        is_hosting=profile.is_hosting or info.is_hosting,
        is_cgnat=profile.is_cgnat or info.is_cgnat,
        modal_city=profile.modal_city,
        modal_admin1=profile.modal_admin1,
        modal_share=float(profile.modal_share) if profile.modal_share is not None else None,
    )
