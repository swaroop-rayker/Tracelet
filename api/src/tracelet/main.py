"""Application factory and lifespan.

M0 wires the skeleton: configuration, logging, the trace id, the error contract,
the database engine and the two probes. No product feature lives here yet, and
that is deliberate -- M0's only job is to make M1 through M9 cheap.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from tracelet.config import Settings, get_settings
from tracelet.db.engine import dispose_engine, init_engine
from tracelet.errors import install_error_handlers
from tracelet.health.router import router as health_router
from tracelet.logging import configure_logging
from tracelet.middleware import AccessLogMiddleware, TraceIdMiddleware

log = structlog.get_logger(__name__)

__version__ = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    init_engine(settings)
    log.info(
        "startup",
        version=__version__,
        environment=settings.env,
        site_address=settings.site_address,
        behind_cloudflare=settings.behind_cloudflare,
        workers=settings.web_concurrency,
        # Deliberately logged: on the free-subdomain path the deployment loses
        # edge TLS, the CF-Ray colo geo signal, CF-IPCountry and layer-0 DDoS
        # absorption, and keeps the B4 browser-trust problem (ADR-0012, RISKS
        # R10). Worth seeing in the log at every boot rather than forgetting.
        edge="cloudflare" if settings.behind_cloudflare else "origin-only",
    )

    # The engine is not connected to here at startup on purpose. A database that
    # is slow to accept connections must not prevent the process from starting --
    # /readyz reports it instead, and the redirect path degrades rather than
    # failing (NFR3.AC2).

    try:
        yield
    finally:
        await dispose_engine()
        log.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Takes settings as a parameter so tests can construct an app with a specific
    configuration without touching the environment or the cached singleton.
    """
    resolved = settings or get_settings()

    configure_logging(
        level=resolved.log_level,
        # Human-readable locally, JSON in production where something collects it.
        json_output=resolved.is_production,
    )

    app = FastAPI(
        title="Tracelet",
        version=__version__,
        summary="Visitor intelligence platform",
        description=(
            "Two surfaces sharing one deployment: a public capture surface "
            "(`/r/{slug}`) and an authenticated admin API (`/api/v1/*`). "
            "See docs/API.md."
        ),
        lifespan=lifespan,
        # Interactive docs are a development convenience, not a production
        # surface. An unauthenticated schema browser on a system holding visitor
        # telemetry is free reconnaissance.
        docs_url=None if resolved.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if resolved.is_production else "/openapi.json",
    )
    app.state.settings = resolved

    # Order matters: the outermost middleware runs first, so the trace id is
    # bound before anything else can log.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(TraceIdMiddleware)

    install_error_handlers(app)

    app.include_router(health_router)

    return app


app = create_app()
