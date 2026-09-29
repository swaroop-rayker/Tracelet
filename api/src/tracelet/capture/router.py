"""The public capture surface (docs/API.md section 3).

Route names are deliberately boring -- ``/r/``, ``/api/v1/s/``, ``/api/v1/hp/`` --
because content blockers match URL substrings like ``track`` and ``collect`` and kill
the request silently in the visitor's browser (B6, F2.AC11). A CI test enforces it.

**``GET /r/{slug}`` never lets an exception reach a visitor** (F15.AC7). The capture
service already degrades rather than raising; the handler wraps that again, and the
last line of defence is a fixed string that cannot itself throw.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, Response, status
from pydantic import ValidationError

from tracelet.auth.dependencies import Config, DbSession, client_address
from tracelet.capture import pages, service
from tracelet.capture.schemas import MAX_ENRICHMENT_BYTES, EnrichmentPayload
from tracelet.config import Settings
from tracelet.errors import (
    FieldError,
    NonceInvalid,
    PayloadTooLarge,
    RateLimited,
    ValidationFailed,
)
from tracelet.net import prefix_of
from tracelet.ratelimit import gcra

log = structlog.get_logger(__name__)

router = APIRouter(tags=["capture"])


def _facts(request: Request, settings: Settings) -> service.RequestFacts:
    headers = request.headers
    return service.RequestFacts(
        client=client_address(request, settings),
        user_agent=headers.get("user-agent"),
        ch_platform=headers.get("sec-ch-ua-platform"),
        ch_mobile=headers.get("sec-ch-ua-mobile"),
        raw_headers=tuple(
            (name.decode("latin-1"), value.decode("latin-1")) for name, value in headers.raw
        ),
        query=dict(request.query_params),
        referer=headers.get("referer"),
        cf_ray=headers.get("cf-ray"),
        cf_ipcountry=headers.get("cf-ipcountry"),
        # Set by Caddy from its own view of the connection, overwriting anything the
        # client sent -- the application only ever sees HTTP/1.1 from Caddy.
        http_version=headers.get("x-tracelet-proto"),
        tls_version=headers.get("x-tracelet-tls"),
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get(
    "/r/{slug}",
    include_in_schema=False,
    summary="Capture a visit and continue to the link's destination",
)
async def capture(slug: str, request: Request, settings: Config) -> Response:
    """F2.AC1: 200 text/html, never a 3xx -- except when rate-limited (F11.AC3)."""
    return await _capture(slug, request, settings)


# Both spellings, registered explicitly: relying on slash redirection would answer the
# bare path with a 3xx, which F2.AC1 rules out.
@router.get("/r", include_in_schema=False, summary="Capture through the default link")
@router.get("/r/", include_in_schema=False, summary="Capture through the default link")
async def capture_default(request: Request, settings: Config) -> Response:
    """F1.AC3 as amended: the bare capture path goes through the default link."""
    return await _capture(None, request, settings)


async def _capture(slug: str | None, request: Request, settings: Settings) -> Response:
    destination: str | None = None
    try:
        result = await service.capture(settings, slug, _facts(request, settings))
        destination = result.link.destination_url if result.link else None

        if result.outcome is service.Outcome.CAPTURED and result.link is not None:
            return pages.capture_page(
                destination=result.link.destination_url,
                interstitial_ms=result.link.interstitial_ms,
                nonce=result.nonce,
                webview_host=result.webview_host,
                os_family=result.os_family,
            )
        if result.outcome is service.Outcome.RATE_LIMITED and result.link is not None:
            return pages.redirect(result.link.destination_url)
        if result.outcome is service.Outcome.FALLBACK and result.link is not None:
            # Not recorded, but the visitor still gets where they were going.
            return pages.capture_page(
                destination=result.link.destination_url,
                interstitial_ms=result.link.interstitial_ms,
                nonce=None,
                webview_host=None,
                os_family=None,
                enrich=False,
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if result.outcome is service.Outcome.UNAVAILABLE:
            return pages.render("unavailable.html", status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return pages.render("not_found.html", status=status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        # Deliberately broad: rendering failed after capture decided what to do. The
        # visitor is still sent on if the destination is known (F15.AC7).
        log.error("capture_render_failed", error_type=type(exc).__name__, exc_info=exc)
        return pages.last_resort(destination)


@router.post(
    "/api/v1/s/{nonce}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Client enrichment for one visit",
    description=(
        "Additive and always allowed to fail -- nothing a visitor sees depends on it "
        "(ADR-0004). Authorised by a single-use nonce bound to the visit and the IP "
        "prefix, valid for 60 seconds. Every field is optional and every field is a "
        "claim: the payload is attacker-controlled by construction."
    ),
    responses={410: {"description": "Nonce expired, consumed, or from another network"}},
)
async def enrich(nonce: str, request: Request, db: DbSession, settings: Config) -> Response:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_ENRICHMENT_BYTES:
        raise PayloadTooLarge("Enrichment payload is too large.")
    body = await request.body()
    if len(body) > MAX_ENRICHMENT_BYTES:
        raise PayloadTooLarge("Enrichment payload is too large.")

    try:
        payload = EnrichmentPayload.model_validate_json(body or b"{}")
    except ValidationError as exc:
        raise ValidationFailed(
            "Enrichment payload is invalid.",
            errors=[
                FieldError(
                    field=".".join(str(part) for part in err["loc"]) or "body",
                    code=str(err["type"]).upper(),
                    message=str(err["msg"]),
                )
                for err in exc.errors()[:20]
            ],
        ) from exc

    ip = client_address(request, settings).ip
    decision = await gcra.check(db, key=prefix_of(ip) or "unknown", limit=service.ENRICH_PER_PREFIX)
    if not decision.allowed:
        raise RateLimited("Too many requests.", retry_after=decision.retry_after_seconds)

    try:
        await service.enrich(db, settings, token=nonce, client_ip=ip, payload=payload)
    except service.EnrichmentRejected as exc:
        # One response for every reason. The reason goes to the log: telling a caller
        # *why* a nonce failed tells them which part to change.
        log.info("enrichment_rejected", reason=exc.reason)
        raise NonceInvalid("This enrichment request is no longer valid.") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/v1/hp/{token}", include_in_schema=False)
async def honeypot(token: str, request: Request, db: DbSession, settings: Config) -> Response:
    """F5.AC6. Always ``204``, whatever happened, so a probe learns nothing."""
    try:
        ip = client_address(request, settings).ip
        decision = await gcra.check(
            db, key=prefix_of(ip) or "unknown", limit=service.HONEYPOT_PER_PREFIX
        )
        if decision.allowed:
            await service.trip_honeypot(db, settings, token=token, client_ip=ip)
    except Exception as exc:
        # Deliberately broad: a honeypot that answered differently on failure would
        # tell a probe something.
        log.error("honeypot_failed", error_type=type(exc).__name__, exc_info=exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/privacy", include_in_schema=False)
async def privacy(settings: Config) -> Response:
    """F2.AC13. Public, and rendered from live configuration."""
    return pages.render(
        "privacy.html",
        ip_days=settings.retention_ip_days,
        visit_days=settings.retention_visit_days,
        external_geo_enabled=settings.external_geo_enabled,
    )
