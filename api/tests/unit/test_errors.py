"""The error contract (F15.AC1, F15.AC3, ES4, ADR-0013).

M0 done-checklist: "a deliberately-raised typed error returns the exact RFC 9457
shape with a trace_id".

Raising the errors through a purpose-built test app rather than a debug endpoint
in the real application: a production API should not carry a route whose only job
is to fail. The handler under test is the same one ``create_app`` installs.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tracelet.errors import (
    FieldError,
    ForbiddenRole,
    IpPurged,
    LastOwner,
    NotFound,
    RateLimited,
    TraceletError,
    Unauthenticated,
    ValidationFailed,
    install_error_handlers,
)
from tracelet.middleware import TRACE_HEADER, TraceIdMiddleware

RFC9457_REQUIRED_KEYS = {"type", "title", "status", "code", "trace_id"}


def _raising_app(exc: Exception) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)
    install_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise exc

    return app


async def _get(app: FastAPI, path: str = "/boom") -> tuple[int, dict[str, Any], dict[str, str]]:
    # raise_app_exceptions=False because Starlette's ServerErrorMiddleware sends
    # the 500 response and then RE-RAISES, so the server also logs it. That is the
    # behaviour we want in production -- Gunicorn records the traceback -- but in
    # a test the re-raise would surface instead of the response under test.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
    return response.status_code, response.json(), dict(response.headers)


async def test_typed_error_returns_exact_rfc9457_shape() -> None:
    status, body, headers = await _get(_raising_app(NotFound("No such link.")))

    assert status == 404
    assert body.keys() >= RFC9457_REQUIRED_KEYS
    assert body["type"] == "https://tracelet/errors/not-found"
    assert body["title"] == "Not found"
    assert body["status"] == 404
    assert body["detail"] == "No such link."
    assert body["instance"] == "/boom"
    assert body["code"] == "NOT_FOUND"
    assert body["errors"] == []

    # F15.AC2: the same trace id on the response header and in the body, so one
    # identifier ties a user report to the log line.
    assert body["trace_id"]
    assert headers[TRACE_HEADER.lower()] == body["trace_id"]

    assert headers["content-type"].startswith("application/problem+json")


async def test_validation_error_carries_field_level_detail() -> None:
    """Field association is what lets the UI attach a message to the right input.

    Losing it is a direct contributor to the class of confusing failures behind B5.
    """
    exc = ValidationFailed(
        "One or more fields are invalid.",
        errors=[
            FieldError(
                field="destination_url",
                code="SCHEME_NOT_HTTPS",
                message="Destination must use https.",
            ),
            FieldError(
                field="interstitial_ms",
                code="OUT_OF_RANGE",
                message="Must be between 300 and 1500.",
            ),
        ],
    )
    status, body, _ = await _get(_raising_app(exc))

    assert status == 422
    assert body["code"] == "VALIDATION_FAILED"
    assert [e["field"] for e in body["errors"]] == ["destination_url", "interstitial_ms"]
    assert body["errors"][0]["code"] == "SCHEME_NOT_HTTPS"


async def test_fastapi_validation_errors_use_the_same_shape() -> None:
    """FastAPI's own ``{"detail": [...]}`` envelope must never reach a client.

    Two error formats in one API is exactly what ES4 forbids.
    """
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)
    install_error_handlers(app)

    @app.get("/needs-param")
    async def needs_param(count: int) -> dict[str, int]:
        return {"count": count}

    status, body, _ = await _get(app, "/needs-param?count=not-a-number")

    assert status == 422
    assert body["code"] == "VALIDATION_FAILED"
    assert body.keys() >= RFC9457_REQUIRED_KEYS
    assert body["errors"], "field-level detail must survive the translation"
    assert body["errors"][0]["field"] == "count"


async def test_unexpected_exception_leaks_nothing() -> None:
    """F15.AC3: a 5xx exposes only the trace id.

    No stack trace, no SQL, no internal hostname, not even the exception message.
    """
    secret = "connection refused to db:5432 with password hunter2"
    status, body, _ = await _get(_raising_app(RuntimeError(secret)))

    assert status == 500
    assert body["code"] == "INTERNAL_ERROR"
    assert body["trace_id"]
    assert body["detail"] is None

    serialised = str(body)
    assert secret not in serialised
    assert "hunter2" not in serialised
    assert "Traceback" not in serialised
    assert "db:5432" not in serialised


async def test_rate_limited_sets_retry_after() -> None:
    """F11.AC10: an over-limit response has to tell the caller when to return."""
    status, body, headers = await _get(_raising_app(RateLimited("Slow down.", retry_after=42)))

    assert status == 429
    assert body["code"] == "RATE_LIMITED"
    assert headers["retry-after"] == "42"


async def test_framework_404_is_translated() -> None:
    """A route that does not exist must not return Starlette's own shape."""
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)
    install_error_handlers(app)

    status, body, _ = await _get(app, "/no-such-route")

    assert status == 404
    assert body["code"] == "NOT_FOUND"
    assert body.keys() >= RFC9457_REQUIRED_KEYS


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_code"),
    [
        (Unauthenticated(), 401, "UNAUTHENTICATED"),
        (ForbiddenRole(), 403, "FORBIDDEN_ROLE"),
        (LastOwner(), 409, "LAST_OWNER"),
        (IpPurged(), 410, "IP_PURGED"),
    ],
)
async def test_catalogue_status_and_code(
    exc: TraceletError, expected_status: int, expected_code: str
) -> None:
    """Guards the catalogue in docs/API.md §12.1 against silent drift.

    Two of these encode decisions worth protecting: 401 vs 403 distinguishes
    "not known" from "known but not permitted", and IP_PURGED is a 410 because a
    purged address is an expected outcome rather than a fault (ADR-0007).
    """
    status, body, _ = await _get(_raising_app(exc))
    assert status == expected_status
    assert body["code"] == expected_code


def test_type_uri_derives_from_code() -> None:
    assert NotFound().type_uri == "https://tracelet/errors/not-found"
    assert LastOwner().type_uri == "https://tracelet/errors/last-owner"
    assert IpPurged().type_uri == "https://tracelet/errors/ip-purged"


def test_error_context_is_never_serialised() -> None:
    """Context exists for the log, not for the client."""
    exc = NotFound("gone", context={"internal_query": "SELECT * FROM admins"})
    problem = exc.to_problem(instance="/x", trace_id="01J")
    assert "internal_query" not in problem.model_dump_json()
