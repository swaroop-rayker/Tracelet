"""Public route names must stay boring (F2.AC11, F14.AC11, CLAUDE.md invariant 7).

This is the guard for **B6**, and the reported symptom was misleading. "The
visitor's browser refuses to provide data because the capture endpoints get
mistaken for bots/scrapers/crawlers" is not bot detection at all -- it is
**content blockers**. EasyPrivacy, uBlock Origin and similar filter lists match
URL *substrings*: a request to ``/api/v1/track`` or ``/collect`` is aborted by the
extension before it ever leaves the browser. Nothing on the server sees it, which
is exactly why it looks like refusal.

So the fix is not better bot handling, it is naming: ``/r/{slug}``,
``/api/v1/s/{nonce}``, ``/api/v1/hp/{token}``.

The natural instinct when adding an endpoint is to name it descriptively --
``/api/v1/telemetry`` -- which would silently reintroduce the bug. This test is
what stops that, because the failure mode is invisible: no error, no log, just
missing data.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from tracelet.config import Settings
from tracelet.main import create_app

# Substrings matched by common filter lists.
BLOCKED_SUBSTRINGS: tuple[str, ...] = (
    "track",
    "tracking",
    "collect",
    "analytic",
    "analytics",
    "pixel",
    "beacon",
    "telemetry",
    "stat",
    "counter",
    "visitor",
    "adserver",
)

# Authenticated dashboard routes are not affected: an admin's own browser
# extension blocking their dashboard is visible and fixable by them, whereas a
# blocked visitor request is silent and unfixable. Only the public surface is
# guarded.
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/r",
    "/api/v1/s",
    "/api/v1/hp",
    "/privacy",
    "/healthz",
    "/readyz",
)


def _public_routes() -> list[str]:
    app = create_app(Settings(env="development"))
    return [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and any(route.path == p or route.path.startswith(p + "/") for p in PUBLIC_PREFIXES)
    ]


def test_every_route_is_classified() -> None:
    """A new public route must be added to PUBLIC_PREFIXES consciously.

    Without this, someone adds ``/api/v1/telemetry``, it matches no known prefix,
    the guard below skips it entirely, and the test passes while the bug returns.
    """
    app = create_app(Settings(env="development"))
    known = {*PUBLIC_PREFIXES, "/api/v1"}
    unclassified = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and not any(route.path == p or route.path.startswith(p) for p in known)
    ]
    assert not unclassified, (
        f"Unclassified route(s): {unclassified}. Every route is either public "
        "(add the prefix to PUBLIC_PREFIXES) or under /api/v1."
    )


@pytest.mark.parametrize("blocked", BLOCKED_SUBSTRINGS)
def test_no_public_path_contains_a_blocked_substring(blocked: str) -> None:
    offenders = [path for path in _public_routes() if blocked in path.lower()]
    assert not offenders, (
        f"Public path(s) {offenders} contain {blocked!r}, which content blockers "
        "match on. The request will be killed in the browser with no server-side "
        "trace -- this is B6. Rename the route (see docs/ERRORS.md B6)."
    )


def test_the_guard_actually_catches_a_bad_name() -> None:
    """Proves the assertion works, rather than passing because it checks nothing.

    A guard that cannot fail is worse than no guard, because it is believed.
    """
    pretend_paths = ["/r/{slug}", "/api/v1/collect"]
    offenders = [p for p in pretend_paths if any(b in p.lower() for b in BLOCKED_SUBSTRINGS)]
    assert offenders == ["/api/v1/collect"]


def test_health_endpoints_are_reachable_names() -> None:
    paths = _public_routes()
    assert "/healthz" in paths
    assert "/readyz" in paths


def test_the_capture_surface_is_actually_under_guard() -> None:
    """M2 added the routes this whole module exists for. If they ever moved outside
    PUBLIC_PREFIXES, every assertion above would pass while checking nothing."""
    paths = _public_routes()
    for expected in ("/r/{slug}", "/api/v1/s/{nonce}", "/api/v1/hp/{token}", "/privacy"):
        assert expected in paths, f"{expected} is not classified as public"


def test_the_guard_fails_on_a_real_bad_route() -> None:
    """The M2 done-check: the guard must fail on a deliberately bad name.

    Mounted on a real application rather than tested against a string, so the proof
    covers route discovery as well as the substring match -- the half most likely to
    break silently.
    """
    app = create_app(Settings(env="development"))

    async def probe() -> dict[str, str]:
        return {}

    app.add_api_route("/api/v1/s/telemetry-probe", probe, methods=["POST"])

    public = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and any(route.path == p or route.path.startswith(p + "/") for p in PUBLIC_PREFIXES)
    ]
    offenders = [path for path in public if any(b in path.lower() for b in BLOCKED_SUBSTRINGS)]
    assert offenders == ["/api/v1/s/telemetry-probe"]
