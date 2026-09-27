"""Per-request database session, committed **before** the response is sent.

This exists because of a real bug (docs/ERRORS.md E11). The obvious pattern --

    async def get_db():
        async with session_scope() as session:
            yield session

-- is wrong in a way that is easy to miss and expensive to diagnose. FastAPI runs the
teardown of a ``yield`` dependency **after the response has been generated**, so a
commit that fails there cannot change the status code. The client is told ``200`` while
the transaction rolled back and nothing was written. A constraint violation at COMMIT
was reported to the caller as complete success.

Middleware can do what the dependency cannot: it holds the response object, so it can
commit first and replace the response if the commit fails.

The second thing this gets right is rollback policy. A 4xx is not necessarily a reason
to discard the transaction -- a failed login must still leave an audit row (F8.AC16),
and that row is written by the same code that then raises ``Unauthenticated``. So:

* **2xx / 3xx** -> commit.
* **4xx** -> commit as well. The handler decided this outcome deliberately, and any row
  it wrote (an audit entry, a failed-attempt counter, a rate-limit bucket) is part of
  that decision.
* **5xx or an unhandled exception** -> roll back. Nobody decided that outcome.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tracelet.db.engine import session_factory
from tracelet.errors import PROBLEM_CONTENT_TYPE, TYPE_BASE

log = structlog.get_logger(__name__)

# Routes that never touch the database skip session creation entirely. Healthchecks
# run every ten seconds and would otherwise open a connection each time, spending
# slots from the pool reserved for maintenance (ARCHITECTURE §6.2).
_NO_DB_PATHS = frozenset({"/healthz"})


class DatabaseSessionMiddleware(BaseHTTPMiddleware):
    """Open a session per request; commit before the response leaves."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in _NO_DB_PATHS:
            return await call_next(request)

        session: AsyncSession = session_factory()()
        request.state.db = session

        try:
            response = await call_next(request)
        except Exception:
            # Nobody chose this outcome, so nothing it wrote should survive.
            await session.rollback()
            await session.close()
            raise

        try:
            if response.status_code < 500:
                await session.commit()
            else:
                await session.rollback()
        except SQLAlchemyError as exc:
            await session.rollback()
            trace_id = getattr(request.state, "trace_id", None)
            # The whole point of doing this in middleware: the response has not been
            # sent, so a failed commit can still be reported as a failure instead of
            # being silently swallowed behind a 200.
            log.error(
                "commit_failed",
                path=request.url.path,
                method=request.method,
                status_would_have_been=response.status_code,
                exc_info=exc,
            )
            return JSONResponse(
                status_code=500,
                media_type=PROBLEM_CONTENT_TYPE,
                content={
                    "type": f"{TYPE_BASE}/internal-error",
                    "title": "Internal error",
                    "status": 500,
                    "detail": None,
                    "instance": request.url.path,
                    "code": "INTERNAL_ERROR",
                    "trace_id": trace_id,
                    "errors": [],
                },
            )
        finally:
            await session.close()

        return response


def request_session(request: Request) -> AsyncSession:
    """The session opened by the middleware for this request."""
    session = getattr(request.state, "db", None)
    if session is None:
        msg = (
            "No database session on this request. DatabaseSessionMiddleware must be "
            "installed, and the path must not be in _NO_DB_PATHS."
        )
        raise RuntimeError(msg)
    return session  # type: ignore[no-any-return]  # set by the middleware above as AsyncSession
