"""The maintenance role's connection (DATA_MODEL section 12, ADR-0022).

``tracelet_maint`` is the only role that may delete audit rows or create the scratch
database, so purges, backups and the restore check use it, and nothing else does. It is
used a few times a day, so it gets no pool: each use opens a connection and closes it,
and the application's pool budget (config.py) is untouched -- these come out of the
maintenance reserve.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tracelet.config import Settings, get_settings
from tracelet.errors import MaintenanceUnavailable

FEATURE = "Purges, backups and the restore check"


def maint_url(settings: Settings | None = None) -> URL:
    settings = settings or get_settings()
    if settings.maint_database_url is None:
        raise MaintenanceUnavailable(
            "TRACELET_MAINT_DATABASE_URL is not set, so the maintenance role cannot connect."
        )
    return make_url(settings.maint_database_url.get_secret_value())


@functools.lru_cache(maxsize=4)
def _engine(url: str) -> AsyncEngine:
    return create_async_engine(url, poolclass=NullPool)


def maint_engine(settings: Settings | None = None) -> AsyncEngine:
    return _engine(maint_url(settings).render_as_string(hide_password=False))


@asynccontextmanager
async def maint_connection(
    settings: Settings | None = None, *, autocommit: bool = False
) -> AsyncIterator[AsyncConnection]:
    """A connection as ``tracelet_maint``. The caller opens each transaction with
    ``conn.begin()``, because a purge commits batch by batch. ``autocommit`` is for the
    statements PostgreSQL refuses inside a transaction: CREATE and DROP DATABASE."""
    async with maint_engine(settings).connect() as conn:
        if autocommit:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        yield conn


def libpq_env(database: str | None = None, settings: Settings | None = None) -> dict[str, str]:
    """The environment for ``pg_dump`` and ``pg_restore``. The password travels in
    ``PGPASSWORD``, never on the command line, where ``/proc`` would show it."""
    url = maint_url(settings)
    return {
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "",
        "PGPASSWORD": url.password if isinstance(url.password, str) else "",
        "PGDATABASE": database or url.database or "",
        "PGCONNECT_TIMEOUT": "10",
        "PGAPPNAME": "tracelet-maint",
    }
