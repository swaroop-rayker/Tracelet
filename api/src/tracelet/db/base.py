"""Declarative base and shared naming conventions.

The naming convention matters more than it looks. Without it, Alembic generates
constraint names from PostgreSQL's defaults, which are unstable across
migrations -- and an unnamed constraint is one you cannot reliably drop or alter
later. Every index, constraint and key in this project therefore has a
deterministic name derived from its table and columns.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for every ORM model.

    Used by admin CRUD. The hot capture path uses raw SQL on the same asyncpg
    pool, because its query plans need to be predictable (ADR-0001).
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
