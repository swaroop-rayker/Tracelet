"""``profiles_stale`` against a real database and a real installed-database layout.

The quiet-hour rebuild (SPEC section 11 row 34, ERRORS E79) runs only when the stored
profiles were built from other versions than those installed. The suite shares the
development database, whose asn_profiles are real, so they are set aside and restored.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, insert, select

from tracelet.capture.models import AsnType
from tracelet.config import Settings
from tracelet.db.engine import session_scope
from tracelet.inference.geodb.catalog import BY_NAME
from tracelet.inference.geodb.maintenance import profiles_stale
from tracelet.inference.models import AsnProfile

VERSIONS = {"geolite2-asn": "20261010T000000Z-aaaa", "geolite2-city": "20261010T000000Z-bbbb"}


@pytest.fixture
def settings(integration_settings: Settings, tmp_path: Path) -> Settings:
    for name, version in VERSIONS.items():
        spec = BY_NAME[name]
        folder = tmp_path / name / version
        folder.mkdir(parents=True)
        (folder / spec.installed_as).write_bytes(b"not read by profiles_stale")
        (tmp_path / name / "current").symlink_to(version)
    return integration_settings.model_copy(update={"geo_data_dir": tmp_path})


@pytest.fixture
async def own_table() -> AsyncIterator[None]:
    async with session_scope() as db:
        saved: list[dict[str, Any]] = [
            {c.key: getattr(row, c.key) for c in AsnProfile.__table__.columns}
            for row in (await db.execute(select(AsnProfile))).scalars()
        ]
        await db.execute(delete(AsnProfile))
    yield
    async with session_scope() as db:
        await db.execute(delete(AsnProfile))
        for start in range(0, len(saved), 1000):
            await db.execute(insert(AsnProfile).values(saved[start : start + 1000]))


async def _store(versions: dict[str, str]) -> None:
    async with session_scope() as db:
        await db.execute(
            insert(AsnProfile).values(
                asn=64512,
                org="Test network",
                asn_type=AsnType.BROADBAND,
                modal_lat=Decimal("12.972"),
                modal_lng=Decimal("77.594"),
                modal_city="Bengaluru",
                modal_admin1="Karnataka",
                modal_share=None,
                is_registry_artifact_source=False,
                is_mobile=False,
                is_hosting=False,
                is_cgnat=False,
                computed_at=dt.datetime.now(dt.UTC),
                source_db_versions=versions,
            )
        )


@pytest.mark.usefixtures("own_table")
async def test_no_profiles_at_all_is_stale(settings: Settings) -> None:
    assert await profiles_stale(settings)


@pytest.mark.usefixtures("own_table")
async def test_profiles_from_the_installed_versions_are_current(settings: Settings) -> None:
    await _store(VERSIONS)
    assert not await profiles_stale(settings)


@pytest.mark.usefixtures("own_table")
async def test_an_install_since_the_last_rebuild_makes_them_stale(settings: Settings) -> None:
    await _store({**VERSIONS, "geolite2-city": "20261003T000000Z-old0"})
    assert await profiles_stale(settings)


async def test_nothing_installed_is_never_stale(
    integration_settings: Settings, tmp_path: Path
) -> None:
    empty = integration_settings.model_copy(update={"geo_data_dir": tmp_path})
    assert not await profiles_stale(empty)
