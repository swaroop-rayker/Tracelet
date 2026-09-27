"""Request-scoped middleware: trace id and access logging.

F15.AC2 requires that every response and every log line carry the same ULID
``trace_id``, so that a report -- "this visit looks wrong" or "I got an error" --
is diagnosable from one identifier without reproducing anything.

ULID rather than UUID4 for two reasons: it sorts by creation time, so logs read
in order, and it gives better index locality when the same value is stored on a
``visits`` row (docs/DATA_MODEL.md §5.1).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from ulid import ULID

log = structlog.get_logger(__name__)

TRACE_HEADER = "X-Trace-Id"

# Paths excluded from access logging. Container healthchecks run every 10
# seconds and would otherwise be most of the log volume.
_QUIET_PATHS = frozenset({"/healthz", "/readyz"})


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Assign a trace id, bind it to the logging context, return it on the response."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = str(ULID())
        request.state.trace_id = trace_id

        # contextvars, not a parameter: every log call in this request --
        # including from code with no reference to the request -- picks it up.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id)

        # The context var is bound before call_next, so it is already in place if
        # the request raises -- which is what makes the error handler's log line
        # and the client's error body carry the same id.
        response = await call_next(request)
        response.headers[TRACE_HEADER] = trace_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit one structured line per request.

    Deliberately records the path template rather than the raw path where one is
    available, so a capture URL with a slug does not turn the log into a list of
    live tracking links.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        if request.url.path in _QUIET_PATHS:
            return response

        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)

        log.info(
            "http_request",
            method=request.method,
            path=path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response
