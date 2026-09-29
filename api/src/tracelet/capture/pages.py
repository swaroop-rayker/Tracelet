"""Rendering for the public pages, and the headers every one of them carries.

**Every response gets its own CSP nonce.** The capture page's CSS and script are
inline, so ``'self'`` would not authorise them and ``'unsafe-inline'`` would authorise
anything an injection managed to write. A fresh nonce per response authorises exactly
the blocks this server rendered (F13.AC2). Caddy leaves the CSP on these routes to the
application for exactly this reason.

**``Cache-Control: no-store`` is load-bearing, not hygiene.** A cached interstitial is
served without reaching the server, so the visit is never recorded -- the one failure
this whole design exists to prevent (F2.AC2).
"""

from __future__ import annotations

import secrets
from typing import Any, Final
from urllib.parse import quote, urlsplit

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from starlette.responses import HTMLResponse, RedirectResponse, Response

_env = Environment(
    loader=PackageLoader("tracelet.capture", "templates"),
    autoescape=select_autoescape(["html"]),
    # A missing variable is a bug, never a silently empty string in a page that a
    # visitor sees.
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

_BASE_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Robots-Tag": "noindex, nofollow",
}


def _csp(nonce: str) -> str:
    # connect-src 'self' is the enrichment POST and nothing else; there is no
    # third-party request anywhere on these pages (F2.AC12).
    return (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        f"style-src 'nonce-{nonce}'; "
        "connect-src 'self'; "
        "img-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    )


def render(template: str, *, status: int = 200, **context: Any) -> HTMLResponse:
    csp_nonce = secrets.token_urlsafe(16)
    html = _env.get_template(template).render(csp_nonce=csp_nonce, **context)
    return HTMLResponse(
        html,
        status_code=status,
        headers={**_BASE_HEADERS, "Content-Security-Policy": _csp(csp_nonce)},
    )


def webview_affordance(webview_host: str | None, os_family: str | None) -> str | None:
    """Which "open in your browser" control to offer (F2.AC10), if any."""
    if webview_host is None:
        return None
    if os_family == "Android":
        return "android"
    if os_family == "iOS":
        return "ios"
    return None


def android_intent(destination: str) -> str:
    """Hand the destination to Chrome, falling back to the plain URL.

    ``browser_fallback_url`` is what makes this safe on a device without Chrome: the
    intent resolves to the ordinary link instead of an error page.
    """
    without_scheme = destination.removeprefix("https://")
    return (
        f"intent://{without_scheme}#Intent;scheme=https;package=com.android.chrome;"
        f"S.browser_fallback_url={quote(destination, safe='')};end"
    )


def capture_page(
    *,
    destination: str,
    interstitial_ms: int,
    nonce: str | None,
    webview_host: str | None,
    os_family: str | None,
    enrich: bool = True,
    status: int = 200,
) -> HTMLResponse:
    affordance = webview_affordance(webview_host, os_family) if enrich else None
    return render(
        "capture.html",
        status=status,
        destination=destination,
        destination_host=urlsplit(destination).hostname or destination,
        enrich=enrich,
        nonce=nonce,
        webview=affordance,
        intent_url=android_intent(destination) if affordance == "android" else "",
        config={"dest": destination, "nonce": nonce, "ms": interstitial_ms},
    )


def redirect(destination: str) -> Response:
    """The rate-limited path: straight to the destination, nothing captured (F11.AC3)."""
    return RedirectResponse(destination, status_code=302, headers=_BASE_HEADERS)


def last_resort(destination: str | None) -> Response:
    """Used only if rendering itself fails.

    No template, no Jinja, no nonce -- a fixed string that cannot throw. If the
    destination is known it still gets the visitor there (F15.AC7); the URL is
    percent-safe because it came from a validated ``links`` row, and is quoted anyway.
    """
    if destination:
        safe = quote(destination, safe=":/?#[]@!$&'()*+,;=%")
        body = (
            '<!doctype html><meta charset="utf-8">'
            f'<meta http-equiv="refresh" content="0;url={safe}">'
            f'<a href="{safe}">Continue</a>'
        )
        return HTMLResponse(body, status_code=503, headers=_BASE_HEADERS)
    return HTMLResponse(
        '<!doctype html><meta charset="utf-8"><p>Temporarily unavailable.</p>',
        status_code=503,
        headers=_BASE_HEADERS,
    )
