"""Structured JSON logging with PII redaction.

The redaction processor is not cosmetic. ADR-0007 goes to real trouble to keep
raw IP addresses out of the database; a single ``log.info("visit", ip=ip)``
would undo all of it by writing the same value to stdout, into the container log,
and from there into anything that collects it.

So the rule (F12.AC13) is absolute:

    **A log line must never contain what the database refuses to store in
    plaintext.**

Enforced two ways: this processor, which strips known-sensitive keys at any depth,
and ``tests/unit/test_log_redaction.py``, which asserts it.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

REDACTED = "[redacted]"

# Key names whose values never reach a log, at any nesting depth.
#
# Matched case-insensitively as whole keys, plus a substring pass for the
# unambiguous stems below. Deliberately broad: a false redaction costs a
# debugging session, a false pass costs a privacy incident.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        # Network identity (ADR-0007)
        "ip",
        "ip_address",
        "ipaddress",
        "client_ip",
        "remote_addr",
        "peer_ip",
        "cf_connecting_ip",
        "x_forwarded_for",
        "x_real_ip",
        "x_tracelet_peer_ip",
        # Precise location (F4.AC3 -- consented coordinates are still sensitive)
        "lat",
        "lng",
        "latitude",
        "longitude",
        "gps_lat",
        "gps_lng",
        "coordinates",
        "resolved_address",
        "address",
        # Credentials and secrets
        "password",
        "new_password",
        "current_password",
        "password_hash",
        "token",
        "bot_token",
        "access_token",
        "refresh_token",
        "session_token",
        "csrf_token",
        "authorization",
        "cookie",
        "set_cookie",
        "secret",
        "session_secret",
        "pepper",
        "api_key",
        "license_key",
        "totp_secret",
        "recovery_code",
        # Derived identity. Pseudonymous, but still a stable identifier.
        "visitor_id",
        "fingerprint_id",
        "session_fp",
        "ip_hmac",
    }
)

# Substrings that are unambiguous enough to redact wherever they appear, so a
# newly-added field such as ``telegram_bot_token_hint`` is covered without
# anyone remembering to extend the set above.
SENSITIVE_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "secret",
    "pepper",
    "token",
    "api_key",
    "apikey",
    "license_key",
    "private_key",
)

_MAX_DEPTH = 6

# Keys whose values are free text and can therefore contain an address that no
# key-based rule would catch -- most importantly a traceback. A connection error
# renders as "could not connect to 203.0.113.47:5432", and that string is exactly
# what ADR-0007 spends real effort keeping out of the database.
#
# Key-based redaction cannot see it, so these fields get a second, value-level
# pass. Best-effort by nature: it masks IP literals, not every conceivable
# encoding of an address.
FREE_TEXT_KEYS: frozenset[str] = frozenset(
    {"event", "exception", "message", "detail", "error", "last_error", "stack"}
)

# Loose match; each candidate is then parsed properly, so a version string such
# as "1.2.3.4" is only masked if it is genuinely a valid address.
_IP_CANDIDATE = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b"  # IPv4
    r"|\b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b"  # IPv6
)


def _mask_addresses(text: str) -> str:
    """Mask globally-routable IP literals in free text, keeping local ones.

    ``is_global`` is the right predicate rather than a hand-rolled list: it is
    false for loopback, private, link-local, multicast, reserved and the
    documentation ranges, and true for exactly the addresses that could identify
    a real visitor.

    Keeping local addresses is deliberate. ``127.0.0.1`` and ``172.18.0.3`` are
    container plumbing, not visitor data, and an error message with them stripped
    is much harder to act on.
    """

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            return raw  # a version string or similar, not an address
        if not address.is_global:
            return raw
        return f"[ip:{REDACTED}]"

    return _IP_CANDIDATE.sub(replace, text)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in SENSITIVE_KEYS:
        return True
    return any(fragment in lowered for fragment in SENSITIVE_SUBSTRINGS)


def _scrub(value: Any, depth: int = 0) -> Any:
    """Recursively redact sensitive keys in nested structures."""
    if depth > _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {
            k: (REDACTED if _is_sensitive(str(k)) else _scrub(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v, depth + 1) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub(v, depth + 1) for v in value)
    return value


def redact_processor(
    logger: object,  # noqa: ARG001 - structlog processor signature
    method_name: str,  # noqa: ARG001 - structlog processor signature
    event_dict: EventDict,
) -> EventDict:
    """Strip sensitive values from every log event, at any depth.

    Two passes, because they catch different things:

    1. **Key-based.** A value under a sensitive key never survives. Handles the
       normal case of structured context being logged.
    2. **Value-based, on free text only.** Masks public IP literals inside
       messages and tracebacks, which no key-based rule can reach.
    """
    result: EventDict = {}
    for key, value in event_dict.items():
        name = str(key)
        if _is_sensitive(name):
            result[key] = REDACTED
        elif name.lower() in FREE_TEXT_KEYS and isinstance(value, str):
            result[key] = _mask_addresses(value)
        else:
            result[key] = _scrub(value)
    return result


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog and route the standard library through it.

    Routing stdlib logging through structlog matters because Uvicorn, Gunicorn,
    SQLAlchemy and Alembic all use it. Without this, half the log stream would be
    unstructured plain text with no ``trace_id`` -- which defeats F15.AC2.
    """
    # Everything -- structlog calls and standard-library records alike -- is
    # rendered by one ProcessorFormatter on one stdlib handler. A single output
    # path is what keeps Uvicorn, Gunicorn, SQLAlchemy and Alembic output in the
    # same format as our own lines, carrying the same trace_id.
    #
    # ``add_logger_name`` requires a stdlib logger, so the factory must be
    # ``stdlib.LoggerFactory``. Pairing it with ``PrintLoggerFactory`` raises
    # AttributeError on the first log call -- see docs/ERRORS.md E3.
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            # Hands the event to the stdlib handler, where the formatter below
            # finishes it.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Applied to records from the standard library, which have not been
        # through the processors above.
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            # Immediately before rendering, so it also scrubs whatever the
            # exception formatter and the foreign chain added. Redaction must be
            # the last thing that touches the event.
            redact_processor,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn duplicates access logs that our own middleware already emits with a
    # trace_id attached, so its access logger is silenced rather than doubled.
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("uvicorn.error").propagate = True
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
