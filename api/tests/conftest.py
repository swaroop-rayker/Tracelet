"""Shared fixtures.

Integration tests run against a **real** PostgreSQL + PostGIS instance and never
mock the database (ES3, F14.AC7). Locally that is the ``db`` compose service;
in CI it is a ``postgis/postgis`` service container. The connection URL comes
from ``TRACELET_DATABASE_URL`` either way, so there is exactly one code path.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from tracelet.config import Settings
from tracelet.db.engine import dispose_engine, init_engine
from tracelet.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Settings for a unit test. No database is touched."""
    return Settings(
        env="development",
        log_level="WARNING",
        site_address="localhost",
    )


@pytest.fixture
def app(settings: Settings) -> Iterator[object]:
    """An app instance with no database engine initialised.

    Suitable for unit tests of the error contract and routing. Anything touching
    the database belongs in tests/integration.
    """
    yield create_app(settings)


@pytest.fixture
async def client(app: object) -> AsyncIterator[AsyncClient]:
    """In-process HTTP client. Exercises the real middleware and handler stack."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]  # fixture is typed loosely to avoid importing FastAPI here
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


@pytest.fixture
def integration_settings() -> Settings:
    url = os.environ.get("TRACELET_DATABASE_URL")
    if not url:
        pytest.skip("TRACELET_DATABASE_URL is not set; integration tests need a real database")
    return Settings(env="development", log_level="WARNING", database_url=SecretStr(url))


@pytest.fixture
async def db_app(integration_settings: Settings) -> AsyncIterator[object]:
    """An app with a live engine against the real database."""
    application = create_app(integration_settings)
    init_engine(integration_settings)
    try:
        yield application
    finally:
        await dispose_engine()


@pytest.fixture
async def db_client(db_app: object) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=db_app)  # type: ignore[arg-type]  # see client fixture
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
