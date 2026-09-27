"""Async engine, session factory, and the readiness checks.

The connection pool is bounded deliberately: it is rate-limit layer 3
(ADR-0010), providing backpressure at the database rather than letting the box
collapse. ``config.Settings`` refuses a pool that, multiplied by the worker
count, would exceed the server's ``max_connections``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import structlog
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tracelet.config import Settings

log = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.db_pool_min,
        max_overflow=settings.db_pool_max - settings.db_pool_min,
        # Recycle before any intermediary times a connection out, so the first
        # query after an idle period does not fail.
        pool_recycle=1800,
        pool_pre_ping=True,
        # A wedged checkout must fail fast rather than hang: on the capture path
        # the redirect still has to happen (CLAUDE.md invariant 1).
        pool_timeout=5,
        echo=False,
    )


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _session_factory  # noqa: PLW0603 - process-wide singleton by design
    _engine = create_engine(settings)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def dispose_engine() -> None:
    global _engine, _session_factory  # noqa: PLW0603 - process-wide singleton by design
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_engine() -> AsyncEngine:
    if _engine is None:
        msg = "Database engine is not initialised. init_engine() runs in the app lifespan."
        raise RuntimeError(msg)
    return _engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A transactional session. Commits on success, rolls back on any exception."""
    if _session_factory is None:
        msg = "Session factory is not initialised. init_engine() runs in the app lifespan."
        raise RuntimeError(msg)
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MigrationState:
    """Comparison of the code's migration head against the database's version."""

    current: str | None
    head: str | None
    up_to_date: bool
    detail: str


def _alembic_head() -> str | None:
    """The head revision the deployed code expects.

    Read from the packaged ``alembic/`` directory rather than the database, which
    is what makes the comparison in :func:`migration_state` meaningful.
    """
    for candidate in (Path("/app/alembic.ini"), Path("alembic.ini")):
        if candidate.exists():
            cfg = AlembicConfig(str(candidate))
            cfg.set_main_option("script_location", str(candidate.parent / "alembic"))
            return ScriptDirectory.from_config(cfg).get_current_head()
    return None


async def migration_state() -> MigrationState:
    """Compare the applied revision with the expected head.

    Reported by ``/readyz`` (F15.AC5). A container serving traffic against a
    database that is behind its own migrations is a silent, confusing failure --
    queries fail on columns that exist in the code and not in the database.
    """
    head = _alembic_head()
    current: str | None = None
    try:
        async with get_engine().connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
            current = str(row[0]) if row else None
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return MigrationState(
            current=None,
            head=head,
            up_to_date=False,
            detail=f"could not read alembic_version: {type(exc).__name__}",
        )

    if head is None:
        return MigrationState(current, None, False, "migration scripts not found in image")
    if current is None:
        return MigrationState(None, head, False, "no migrations applied -- run: tl migrate")
    if current != head:
        return MigrationState(
            current, head, False, f"database at {current}, code expects {head} -- run: tl migrate"
        )
    return MigrationState(current, head, True, "up to date")


async def database_reachable() -> tuple[bool, str]:
    """Cheap liveness probe for the database."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return False, f"{type(exc).__name__}: unreachable"
    return True, "ok"


async def postgis_available() -> tuple[bool, str]:
    """Confirm PostGIS is installed.

    Checked at readiness rather than discovered later: geofencing is a headline
    feature (F6) and a missing extension should surface as an unready container,
    not as a confusing query error on the first geofence evaluation.
    """
    try:
        async with get_engine().connect() as conn:
            result = await conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'postgis'")
            )
            row = result.first()
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return False, f"{type(exc).__name__}"
    if row is None:
        return False, "postgis extension not installed"
    return True, str(row[0])
