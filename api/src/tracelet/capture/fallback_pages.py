"""Redirect pages Caddy serves when the api cannot answer a capture (ADR-0029).

When ``/r/...`` gets no answer from the api within 3 s, or the api cannot be reached
(restarting, killed, wedged, saturated), Caddy serves ``<slug>.html`` from a volume this
module writes: a ``meta refresh`` to the link's stored destination. Invariant 1 then holds
with only Caddy running. The visit is not recorded; that is the last stage of ADR-0028's
degradation.

Every page holds a destination an authenticated admin stored -- nothing from a request
reaches a page (CLAUDE.md invariant 3). Only live links have a page; a deactivated or
archived link's page is removed at once, so it stops redirecting (F1.AC4). Two fixed names
can never collide with a slug (``[a-z0-9-]`` only): ``_default.html`` for the bare ``/r/``
and ``_unavailable.html`` for anything Caddy cannot match.

Writes are atomic (a temporary file, then a rename), skipped when the content is unchanged,
and never raise: a page that cannot be written leaves the previous one, and is logged.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from tracelet.capture.service import LinkSnapshot

log = structlog.get_logger(__name__)

DEFAULT_PAGE: Final = "_default.html"
UNAVAILABLE_PAGE: Final = "_unavailable.html"

_PAGE: Final = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta http-equiv="refresh" content="0;url={url}">
<title>Redirecting…</title>
<link rel="icon" href="data:,">
</head>
<body><p><a href="{url}">Continue</a></p></body>
</html>
"""

_UNAVAILABLE: Final = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Temporarily unavailable</title>
<link rel="icon" href="data:,">
</head>
<body><p>This link is temporarily unavailable. Please try again shortly.</p></body>
</html>
"""


def render(destination: str) -> str:
    """The redirect page for one destination, escaped for an HTML attribute."""
    return _PAGE.format(url=html.escape(destination, quote=True))


def _write(path: Path, content: str) -> bool:
    """Atomically replace ``path`` with ``content``; False when it was already that."""
    try:
        if path.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return True


def sync(directory: Path, links: list[LinkSnapshot], default: LinkSnapshot | None) -> int:
    """Make ``directory`` hold exactly one page per live link, the default's and the
    unavailable page. Returns how many files changed. Never raises."""
    try:
        if not directory.is_dir():
            log.warning("fallback_pages_dir_missing", path=str(directory))
            return 0
        wanted: dict[str, str] = {
            f"{link.slug}.html": render(link.destination_url) for link in links if link.is_live
        }
        if default is not None and default.is_live:
            wanted[DEFAULT_PAGE] = render(default.destination_url)
        wanted[UNAVAILABLE_PAGE] = _UNAVAILABLE
        changed = sum(_write(directory / name, content) for name, content in wanted.items())
        for stale in directory.glob("*.html"):
            if stale.name not in wanted:
                stale.unlink(missing_ok=True)
                changed += 1
    except OSError as exc:
        log.error("fallback_pages_sync_failed", error_type=type(exc).__name__)
        return 0
    return changed
