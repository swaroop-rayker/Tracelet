"""Health probes against a real PostgreSQL + PostGIS instance.

ES3 and F14.AC7: integration tests never mock the database. These assertions are
only meaningful against a real server -- ``postgis`` being installed, and the
applied Alembic revision matching the head the code expects, cannot be faked
without also faking the thing under test.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tracelet.db.engine import (
    database_reachable,
    get_engine,
    migration_state,
    postgis_available,
)
from tracelet.middleware import TRACE_HEADER

pytestmark = pytest.mark.integration


async def test_healthz_is_cheap_and_unconditional(db_client: AsyncClient) -> None:
    response = await db_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers[TRACE_HEADER]


async def test_readyz_reports_all_three_checks(db_client: AsyncClient) -> None:
    response = await db_client.get("/readyz")
    body = response.json()

    assert set(body["checks"]) == {"database", "postgis", "migrations"}

    assert body["checks"]["database"]["ok"] is True, body["checks"]["database"]["detail"]
    assert body["checks"]["postgis"]["ok"] is True, body["checks"]["postgis"]["detail"]

    # Migrations may legitimately be unapplied in a bare CI database. Readiness
    # must report that accurately rather than claiming ready -- serving traffic
    # against a database behind its own migrations is a silent, confusing failure.
    if body["checks"]["migrations"]["ok"]:
        assert response.status_code == 200
        assert body["ready"] is True
    else:
        assert response.status_code == 503
        assert body["ready"] is False
        assert "migrate" in body["checks"]["migrations"]["detail"]


async def test_database_is_actually_reachable(db_app: object) -> None:
    del db_app  # fixture initialises the engine
    ok, detail = await database_reachable()
    assert ok, detail


async def test_postgis_is_installed(db_app: object) -> None:
    """Checked explicitly because geofencing depends on it (F6).

    A missing extension should surface here, not on the first geofence evaluation
    months later.
    """
    del db_app
    ok, version = await postgis_available()
    assert ok, version
    assert version[0].isdigit(), f"expected a version string, got {version!r}"


async def test_geography_type_and_st_covers_work(db_app: object) -> None:
    """Proves the specific capability ADR-0002 chose PostGIS for.

    ``geography`` rather than ``geometry`` means ST_Covers is geodesically
    correct. The assertion below is a point 500 m from a 1 km circle centred in
    Bengaluru: inside on a sphere, and the kind of thing a planar approximation
    gets subtly wrong.
    """
    del db_app
    async with get_engine().connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT ST_Covers(
                    ST_Buffer(ST_MakePoint(77.5946, 12.9716)::geography, 1000),
                    ST_MakePoint(77.5946, 12.9761)::geography
                )
                """
            )
        )
        assert result.scalar() is True


async def test_migration_state_is_readable(db_app: object) -> None:
    """The head must be discoverable from the image, or /readyz cannot compare."""
    del db_app
    state = await migration_state()
    assert state.head is not None, (
        "Alembic head not found. /readyz cannot verify migration state without the "
        "alembic/ directory present in the image."
    )
    assert state.detail


async def test_app_role_cannot_create_tables(db_app: object) -> None:
    """The three-role split is real, not documentation (docs/DATA_MODEL.md §12).

    The API connects as tracelet_app, which has no DDL. This is the foundation of
    the append-only audit log: from M1, tracelet_app will also have no DELETE on
    audit_log, so a SQL-injection foothold in the application path cannot erase
    the record of itself (NFR5.AC5).
    """
    del db_app
    async with get_engine().connect() as conn:
        role = (await conn.execute(text("SELECT current_user"))).scalar()

        if role != "tracelet_app":
            pytest.skip(f"connected as {role!r}, not tracelet_app; role separation not exercised")

        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(text("CREATE TABLE should_not_exist (id int)"))


async def test_unknown_route_returns_the_project_error_shape(db_client: AsyncClient) -> None:
    """Even a 404 from the framework uses one error shape (ES4)."""
    response = await db_client.get("/no-such-route")
    body = response.json()

    assert response.status_code == 404
    assert body["code"] == "NOT_FOUND"
    assert body["trace_id"] == response.headers[TRACE_HEADER]
    assert response.headers["content-type"].startswith("application/problem+json")
