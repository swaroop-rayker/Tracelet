"""Tracking-link rules: destination validation, the default invariant (F1).

Kept apart from the router so the same rules apply wherever a link is written.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from typing import Final
from urllib.parse import urlsplit

import structlog

from tracelet.errors import FieldError, ValidationFailed

log = structlog.get_logger(__name__)

MAX_DESTINATION: Final = 2048
RESOLVE_TIMEOUT_S: Final = 3.0

Resolver = Callable[[str], Awaitable[list[str]]]


async def _system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(
        loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout=RESOLVE_TIMEOUT_S
    )
    return sorted({str(info[4][0]) for info in infos})


# Module-level so a test can substitute it. The destination is resolved by the real
# system resolver in production; a test that depended on external DNS would fail for
# reasons that have nothing to do with the code under test.
resolve_host: Resolver = _system_resolver


def _reject(message: str, code: str) -> ValidationFailed:
    return ValidationFailed(
        message, errors=[FieldError(field="destination_url", code=code, message=message)]
    )


async def validate_destination(url: str, *, own_host: str) -> str:
    """Return the destination if it is acceptable, else raise a field-level 422.

    F1.AC2: ``https`` only, no embedded credentials, at most 2048 characters, and the
    host must resolve to public addresses only.

    **What the resolution check is, and is not.** Tracelet never fetches the
    destination -- the visitor's browser does -- so this is not server-side request
    forgery defence. It exists so an owner cannot, by mistake, send visitors to an
    internal or unresolvable host, and so a link does not point anywhere that makes it
    look like an open redirector (B4). DNS can change after the check; that is
    accepted, because the destination is still only ever the value an owner wrote.
    """
    cleaned = url.strip()
    if len(cleaned) > MAX_DESTINATION:
        msg = f"Must be at most {MAX_DESTINATION} characters."
        raise _reject(msg, "TOO_LONG")

    parts = urlsplit(cleaned)
    if parts.scheme != "https":
        msg = "Must start with https://."
        raise _reject(msg, "NOT_HTTPS")
    if parts.username is not None or parts.password is not None:
        msg = "Must not contain a username or password."
        raise _reject(msg, "CREDENTIALS_IN_URL")
    host = parts.hostname
    if not host:
        msg = "Must include a host name."
        raise _reject(msg, "NO_HOST")

    # A destination on this deployment's own host would send a visitor back into the
    # capture surface, and a link to another link loops.
    if host.lower() == own_host.split(":", 1)[0].lower():
        msg = "Must not point back at this site."
        raise _reject(msg, "SELF_REFERENCE")

    try:
        addresses = await resolve_host(host)
    except (OSError, TimeoutError) as exc:
        log.info("destination_unresolvable", error_type=type(exc).__name__)
        msg = "The host name does not resolve."
        raise _reject(msg, "UNRESOLVABLE") from exc

    if not addresses:
        msg = "The host name does not resolve."
        raise _reject(msg, "UNRESOLVABLE")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            # Every address, not the first: a split-horizon name with one public and
            # one private record is exactly the case worth refusing.
            msg = "The host must resolve to public addresses only."
            raise _reject(msg, "NOT_PUBLIC")
    return cleaned
