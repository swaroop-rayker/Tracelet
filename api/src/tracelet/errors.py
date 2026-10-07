"""Typed error hierarchy and the single RFC 9457 handler.

ES4 and F15.AC1 require one consistent JSON error shape. The way to actually get
that -- rather than a convention that drifts -- is one exception hierarchy and
exactly **one** place where an exception becomes a response. That place is
:func:`install_error_handlers`.

Two properties worth stating, because both are easy to lose later:

* **A 5xx exposes only the trace id** (F15.AC3). No stack trace, no SQL, no
  internal hostname. The detail goes to the log under the same ``trace_id``,
  which is what makes a user report diagnosable from a single identifier.

* **A renderable error shape is a structural defence against B5.** If every
  failure has a shape the frontend knows, an unanticipated error cannot produce
  an unrendered blank panel.

The catalogue here must stay in step with ``docs/API.md`` §12.1.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"
TYPE_BASE = "https://tracelet/errors"


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


class GeoPoint(BaseModel):
    lat: float
    lng: float


class FieldError(BaseModel):
    """One field-level validation failure.

    ``location`` points at the failure on a map -- where a geofence ring crosses itself
    (F6.AC4, DESIGN section 16) -- and is omitted, not ``null``, when there is none, so
    every other error keeps its shape.
    """

    field: str
    code: str
    message: str
    location: GeoPoint | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_location(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("location") is None:
            data.pop("location", None)
        return data


class Problem(BaseModel):
    """RFC 9457 Problem Details.

    ``code`` is an addition to the RFC: ``type`` URIs are awkward to switch on in
    client code, and the generated TypeScript client needs a stable discriminator.
    ``errors`` is an addition because field-level validation is the most common
    failure and callers need it structured rather than prose.
    """

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: str
    trace_id: str | None = None
    errors: list[FieldError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


class TraceletError(Exception):
    """Base class for every expected failure.

    Subclasses declare their HTTP status, machine code and human title. Anything
    that is *not* a subclass of this is by definition unexpected, and is reported
    as ``INTERNAL_ERROR`` with nothing but a trace id.
    """

    # Declared as ordinary class attributes rather than ClassVar so a single
    # instance can override them -- needed by HttpError below, which carries a
    # status decided at runtime by the framework.
    status: int = 500
    code: str = "INTERNAL_ERROR"
    title: str = "Internal error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        errors: list[FieldError] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.errors = errors or []
        # Structured context for the log only. Never serialised to a client.
        self.context = context or {}

    @property
    def type_uri(self) -> str:
        return f"{TYPE_BASE}/{self.code.lower().replace('_', '-')}"

    def to_problem(self, *, instance: str | None, trace_id: str | None) -> Problem:
        return Problem(
            type=self.type_uri,
            title=self.title,
            status=self.status,
            detail=self.detail,
            instance=instance,
            code=self.code,
            trace_id=trace_id,
            errors=self.errors,
        )


# --- 4xx: client ------------------------------------------------------------


class ValidationFailed(TraceletError):
    status = 422
    code = "VALIDATION_FAILED"
    title = "Validation failed"


class Unauthenticated(TraceletError):
    status = 401
    code = "UNAUTHENTICATED"
    title = "Authentication required"


class MfaRequired(TraceletError):
    status = 401
    code = "MFA_REQUIRED"
    title = "Second factor required"


class MfaInvalid(TraceletError):
    status = 401
    code = "MFA_INVALID"
    title = "Invalid or reused code"


class TotpNotEnrolled(TraceletError):
    """TOTP is mandatory; no dashboard route is reachable before enrolment."""

    status = 403
    code = "TOTP_NOT_ENROLLED"
    title = "Two-factor enrolment incomplete"


class ForbiddenRole(TraceletError):
    """A valid session lacking the required role.

    Distinct from :class:`Unauthenticated` on purpose: 403 means "you are known
    but not permitted", 401 means "you are not known".
    """

    status = 403
    code = "FORBIDDEN_ROLE"
    title = "Insufficient role"


class CsrfInvalid(TraceletError):
    status = 403
    code = "CSRF_INVALID"
    title = "CSRF validation failed"


class AccountLocked(TraceletError):
    status = 423
    code = "ACCOUNT_LOCKED"
    title = "Account temporarily locked"


class NotFound(TraceletError):
    status = 404
    code = "NOT_FOUND"
    title = "Not found"


class LastOwner(TraceletError):
    """Refuses an action that would leave zero active owners (F8.AC13)."""

    status = 409
    code = "LAST_OWNER"
    title = "At least one owner must remain"


class LinkHasVisits(TraceletError):
    """Historical data must never be orphaned; archive instead (F1.AC10)."""

    status = 409
    code = "LINK_HAS_VISITS"
    title = "Link has recorded visits"


class DefaultLinkRequired(TraceletError):
    status = 409
    code = "DEFAULT_LINK_REQUIRED"
    title = "A default link is required"


class OutboxNotDead(TraceletError):
    """Only a dead letter is retried by hand (F7.AC6); anything else is still in hand."""

    status = 409
    code = "OUTBOX_NOT_DEAD"
    title = "Only a dead-lettered delivery can be retried"


class RetentionPreviewStale(TraceletError):
    """A purge runs only against the preview it was shown (F10.AC12): that preview is too
    old, or the policy has changed since, so its counts would no longer be exact."""

    status = 409
    code = "RETENTION_PREVIEW_STALE"
    title = "Preview again before purging"


class LifecycleJobRunning(TraceletError):
    """A purge, backup or restore check of the same kind is already running."""

    status = 409
    code = "LIFECYCLE_JOB_RUNNING"
    title = "Already running"


class BackupUnavailable(TraceletError):
    """The backup is not one that has a file: it failed, is still running, or was rotated
    away."""

    status = 409
    code = "BACKUP_UNAVAILABLE"
    title = "This backup has no file"


class NonceInvalid(TraceletError):
    """Enrichment nonce expired, already consumed, or bound to another prefix."""

    status = 410
    code = "NONCE_INVALID"
    title = "Enrichment token is no longer valid"


class IpPurged(TraceletError):
    """The encrypted IP passed its TTL.

    An expected outcome, not a fault (ADR-0007). The UI must present it as such.
    """

    status = 410
    code = "IP_PURGED"
    title = "Address no longer retained"


class GeofenceInvalidGeometry(TraceletError):
    status = 422
    code = "GEOFENCE_INVALID_GEOMETRY"
    title = "Invalid geofence geometry"


class GeofenceTooManyVertices(TraceletError):
    status = 422
    code = "GEOFENCE_TOO_MANY_VERTICES"
    title = "Geofence has too many vertices"


class GeofenceUnknownRegion(TraceletError):
    """A region key the catalogue does not list (ADR-0020)."""

    status = 422
    code = "GEOFENCE_UNKNOWN_REGION"
    title = "Unknown region"


class PayloadTooLarge(TraceletError):
    status = 413
    code = "PAYLOAD_TOO_LARGE"
    title = "Payload too large"


class RateLimited(TraceletError):
    status = 429
    code = "RATE_LIMITED"
    title = "Rate limited"

    def __init__(
        self,
        detail: str | None = None,
        *,
        retry_after: int = 60,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail, context=context)
        self.retry_after = retry_after


class HttpError(TraceletError):
    """A framework-raised 4xx with no more specific mapping.

    The status arrives at runtime from Starlette, which is why the base class
    keeps status/code/title as overridable instance attributes.
    """

    code = "HTTP_ERROR"
    title = "Request failed"

    def __init__(self, *, status: int, detail: str | None = None) -> None:
        super().__init__(detail)
        self.status = status


# --- 5xx: server and dependencies ------------------------------------------


class GeoDbUnavailable(TraceletError):
    """A geo source is missing or corrupt. Inference degrades, it does not fail."""

    status = 503
    code = "GEO_DB_UNAVAILABLE"
    title = "Geolocation database unavailable"


class ExternalSourceUnavailable(TraceletError):
    """A circuit breaker is open. Informational; inference continues."""

    status = 503
    code = "EXTERNAL_SOURCE_UNAVAILABLE"
    title = "External source unavailable"


class TelegramDeliveryFailed(TraceletError):
    """The test message (F7.AC8) did not arrive. ``detail`` is Telegram's own reason,
    redacted of the token, because it is what the owner can act on ("chat not found")."""

    status = 502
    code = "TELEGRAM_DELIVERY_FAILED"
    title = "Telegram did not deliver the message"


class DependencyUnavailable(TraceletError):
    status = 503
    code = "DEPENDENCY_UNAVAILABLE"
    title = "Dependency unavailable"


class MaintenanceUnavailable(TraceletError):
    """The maintenance role is not configured or not set up (ADR-0022). ``detail`` names
    the missing step, because the owner can act on it."""

    status = 503
    code = "MAINTENANCE_UNAVAILABLE"
    title = "Maintenance is not set up"


class InternalError(TraceletError):
    status = 500
    code = "INTERNAL_ERROR"
    title = "Internal error"


# ---------------------------------------------------------------------------
# The single handler
# ---------------------------------------------------------------------------


def _trace_id(request: Request) -> str | None:
    value = getattr(request.state, "trace_id", None)
    return str(value) if value else None


def _response(problem: Problem, *, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json"),
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register the handlers. This is the only place an exception becomes a response."""

    @app.exception_handler(TraceletError)
    async def _handle_tracelet(request: Request, exc: TraceletError) -> JSONResponse:
        trace_id = _trace_id(request)
        problem = exc.to_problem(instance=request.url.path, trace_id=trace_id)

        # 5xx is a fault on our side and is logged at error level with full
        # context; 4xx is the client's and is informational.
        logger = log.bind(code=exc.code, status=exc.status, **exc.context)
        if exc.status >= 500:
            logger.error("request_failed", exc_info=exc)
        else:
            logger.info("request_rejected")

        headers: dict[str, str] | None = None
        if isinstance(exc, RateLimited):
            headers = {"Retry-After": str(exc.retry_after)}

        return _response(problem, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Translate FastAPI's validation errors into the project's shape.

        Without this, FastAPI returns its own ``{"detail": [...]}`` envelope and
        the API would have two error formats -- exactly what ES4 forbids.
        """
        errors = [
            FieldError(
                field=".".join(str(p) for p in err["loc"][1:]) or str(err["loc"][0]),
                code=str(err["type"]).upper(),
                message=str(err["msg"]),
            )
            for err in exc.errors()
        ]
        wrapped = ValidationFailed("One or more fields are invalid.", errors=errors)
        log.info("request_rejected", code=wrapped.code, status=wrapped.status)
        return _response(wrapped.to_problem(instance=request.url.path, trace_id=_trace_id(request)))

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Cover 404s and anything else raised by the framework itself.

        Without this, a plain 404 would return Starlette's own
        ``{"detail": "Not Found"}`` and the API would have two error shapes.
        """
        mapped: TraceletError
        if exc.status_code == 404:
            mapped = NotFound()
        elif exc.status_code >= 500:
            mapped = InternalError()
        else:
            mapped = HttpError(status=exc.status_code, detail=str(exc.detail))
        return _response(mapped.to_problem(instance=request.url.path, trace_id=_trace_id(request)))

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Last resort. Nothing internal reaches the client (F15.AC3)."""
        trace_id = _trace_id(request)
        log.error(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            exc_info=exc,
        )
        problem = Problem(
            type=f"{TYPE_BASE}/internal-error",
            title="Internal error",
            status=500,
            detail=None,
            instance=request.url.path,
            code="INTERNAL_ERROR",
            trace_id=trace_id,
            errors=[],
        )
        return _response(problem)
