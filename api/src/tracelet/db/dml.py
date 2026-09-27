"""Typed helper for DML row counts.

``AsyncSession.execute`` is annotated as returning ``Result[Any]``, but an
INSERT/UPDATE/DELETE actually yields a ``CursorResult``, which is the only one with
``rowcount``. Under ``mypy --strict`` that mismatch is an error at every call site.

The alternative to this helper is a ``cast`` scattered through every module that runs
a conditional UPDATE -- and those casts matter here, because several security
properties depend on reading ``rowcount`` correctly: a single-use recovery code, a
single-use reset token, and "did this revoke actually revoke anything". One
documented cast in one place is easier to audit than nine.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable


async def execute_rowcount(db: AsyncSession, statement: Executable) -> int:
    """Run a DML statement and return the number of rows it affected.

    Used for conditional updates where the count **is** the result: consuming a
    single-use token, spending a recovery code, revoking a session. A caller that
    ignored the count would silently accept a replayed token.
    """
    result = await db.execute(statement)
    # Safe for DML: SQLAlchemy returns a CursorResult for any statement that has a
    # rowcount. It would not be safe for a SELECT, which is why this helper is
    # named for DML and takes no result rows.
    return cast("CursorResult[Any]", result).rowcount
