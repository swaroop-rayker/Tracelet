"""Alembic environment.

Migrations connect as ``tracelet_migrate`` -- the DDL-only role -- using the
synchronous psycopg driver. The application itself uses asyncpg; Alembic has no
reason to be async, and a sync driver keeps migration failures easy to read.

The URL comes from ``TRACELET_MIGRATE_DATABASE_URL`` at runtime, so no
credentials are stored in ``alembic.ini``.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from tracelet.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("TRACELET_MIGRATE_DATABASE_URL")
    if not url:
        msg = (
            "TRACELET_MIGRATE_DATABASE_URL is not set. Migrations must connect as "
            "tracelet_migrate (the DDL-only role). See .env.example."
        )
        raise RuntimeError(msg)
    # Alembic runs synchronously; reject an async DSN rather than failing later
    # with an opaque driver error.
    if "+asyncpg" in url:
        msg = (
            "TRACELET_MIGRATE_DATABASE_URL must use a synchronous driver "
            "(postgresql+psycopg://), not asyncpg."
        )
        raise RuntimeError(msg)
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting. Used to review a migration."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            # PostGIS manages spatial_ref_sys and the topology schema itself.
            # Without this, autogenerate proposes dropping them on every run.
            include_schemas=False,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


def _include_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,  # noqa: ARG001 - required by the Alembic hook signature
    compare_to: object,  # noqa: ARG001 - required by the Alembic hook signature
) -> bool:
    """Keep PostGIS's own objects out of autogenerate.

    Without this, every ``--autogenerate`` run proposes dropping the tables and
    views the extension installed and manages itself.
    """
    postgis_owned = {"spatial_ref_sys", "geography_columns", "geometry_columns"}
    return not (type_ == "table" and name in postgis_owned)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
