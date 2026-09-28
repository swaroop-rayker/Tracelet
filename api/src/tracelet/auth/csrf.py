"""CSRF protection (F8.AC11).

Two independent checks on every state-changing request, because each covers a case
the other misses:

1. **Double-submit token.** The client must echo the session's CSRF secret in the
   ``X-CSRF-Token`` header. A cross-site attacker can make the browser send the
   cookie, but cannot read it to set a matching header.
2. **Origin validation.** Rejects a request whose ``Origin`` is not ours. Catches
   the case where the token leaks some other way, and costs one string comparison.

``SameSite=Strict`` on the session cookie already blocks the classic attack. These
are defence in depth, not the primary control: cookie behaviour varies across
browsers and versions, and relying on a single browser-enforced attribute for a
dashboard that can decrypt visitor IP addresses is thin.
"""

from __future__ import annotations

import structlog
from fastapi import Request

from tracelet.crypto.hashing import constant_time_equals
from tracelet.errors import CsrfInvalid

log = structlog.get_logger(__name__)

CSRF_HEADER = "X-CSRF-Token"

# GET, HEAD and OPTIONS are required to be side-effect free, so they need no token.
# Any route that mutates state under one of these is the bug, not this exemption.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _allowed_origins(site_address: str) -> set[str]:
    """Origins accepted for state-changing requests.

    Both schemes for ``localhost`` because development serves HTTPS via Caddy's
    internal CA while a direct Vite dev server is plain HTTP.
    """
    if site_address.startswith("localhost") or site_address.startswith("127.0.0.1"):
        return {
            f"http://{site_address}",
            f"https://{site_address}",
            "http://localhost:5173",
        }
    return {f"https://{site_address}"}


def verify(request: Request, *, csrf_secret: str, site_address: str) -> None:
    """Raise :class:`CsrfInvalid` unless both checks pass."""
    if request.method in SAFE_METHODS:
        return

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    allowed = _allowed_origins(site_address)

    if origin is not None:
        if origin not in allowed:
            log.warning("csrf_origin_rejected", method=request.method, path=request.url.path)
            raise CsrfInvalid("Request origin is not permitted.")
    elif referer is not None:
        # Some clients omit Origin on same-origin requests. Fall back to the Referer
        # prefix rather than failing a legitimate request.
        if not any(referer.startswith(candidate) for candidate in allowed):
            log.warning("csrf_referer_rejected", method=request.method, path=request.url.path)
            raise CsrfInvalid("Request referer is not permitted.")
    else:
        # Neither header. A browser sends at least one on a state-changing request, so
        # this is either a non-browser client or a stripped request. Refuse it: the
        # admin API has no non-browser callers.
        log.warning("csrf_no_origin_or_referer", method=request.method, path=request.url.path)
        raise CsrfInvalid("Missing Origin and Referer.")

    supplied = request.headers.get(CSRF_HEADER)
    if not supplied or not constant_time_equals(supplied, csrf_secret):
        log.warning("csrf_token_mismatch", method=request.method, path=request.url.path)
        raise CsrfInvalid("CSRF token missing or incorrect.")
