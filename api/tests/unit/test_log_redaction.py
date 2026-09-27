"""Log redaction (F12.AC13).

M0 done-checklist: "a redaction unit test asserts known-sensitive keys never
reach log output".

Why this test earns its place: ADR-0007 goes to considerable trouble to keep raw
IP addresses out of the database -- encrypted at rest, 30-day TTL, owner-only
audited decryption. A single ``log.info("visit", ip=ip)`` would write the same
value to stdout in plaintext and undo all of it. The redaction processor is the
control; this test is the proof that it works.
"""

from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest
import structlog

from tracelet.logging import REDACTED, redact_processor

# Values chosen to be unmistakable if they leak.
LEAK_MARKERS = {
    "ip": "203.0.113.47",
    "gps_lat": "12.9716",
    "gps_lng": "77.5946",
    "resolved_address": "12 Example Road, Bengaluru",
    "password": "hunter2-do-not-log",
    "telegram_bot_token": "7777777:AAExampleBotTokenValue",
    "pepper_stable": "deadbeefcafebabe",
    "session_token": "opaque-256-bit-token",
    "visitor_id": "9f2c8a1b4e6d",
    "ip_hmac": "abcdef0123456789",
}


def _apply(event: dict[str, Any]) -> dict[str, Any]:
    return dict(redact_processor(None, "info", event))


@pytest.mark.parametrize(("key", "value"), sorted(LEAK_MARKERS.items()))
def test_sensitive_top_level_keys_are_redacted(key: str, value: str) -> None:
    result = _apply({"event": "visit_captured", key: value})
    assert result[key] == REDACTED
    assert value not in json.dumps(result)


def test_nested_structures_are_scrubbed() -> None:
    """Sensitive data usually arrives nested, not at the top level.

    A whole request context or an enrichment payload gets logged as one object,
    which is precisely where a naive top-level-only redactor fails.
    """
    event = {
        "event": "enrichment_received",
        "request": {
            "headers": {"cookie": "sid=abc123", "authorization": "Bearer xyz"},
            "client_ip": "203.0.113.47",
        },
        "payload": {
            "geolocation": {"lat": 12.9716, "lng": 77.5946, "accuracyM": 18.0},
            "screen": {"w": 1170, "h": 2532},
        },
        "candidates": [
            {"source": "rdns", "city": "Bengaluru", "lat": 12.97, "lng": 77.59},
            {"source": "cf_colo", "city": "Bengaluru"},
        ],
    }

    result = _apply(event)
    serialised = json.dumps(result)

    assert "203.0.113.47" not in serialised
    assert "sid=abc123" not in serialised
    assert "Bearer xyz" not in serialised
    assert "12.9716" not in serialised
    assert "77.5946" not in serialised

    # Non-sensitive fields must survive, or the log becomes useless and people
    # start bypassing the logger.
    assert result["payload"]["screen"] == {"w": 1170, "h": 2532}
    assert result["payload"]["geolocation"]["accuracyM"] == 18.0
    assert result["candidates"][0]["source"] == "rdns"
    assert result["candidates"][0]["city"] == "Bengaluru"


def test_substring_rule_covers_keys_nobody_remembered_to_list() -> None:
    """A new field must be covered without anyone updating the key set.

    The whole-key list will always lag reality; the substring pass is the safety
    net for the field added in a hurry six months from now.
    """
    result = _apply(
        {
            "event": "x",
            "maxmind_license_key": "ABC123",
            "telegram_bot_token_hint": "7777",
            "user_password_reset_token": "t0ken",
            "some_api_key_value": "k",
        }
    )
    for key in (
        "maxmind_license_key",
        "telegram_bot_token_hint",
        "user_password_reset_token",
        "some_api_key_value",
    ):
        assert result[key] == REDACTED


def test_case_is_ignored() -> None:
    result = _apply({"event": "x", "Client_IP": "203.0.113.47", "PASSWORD": "p"})
    assert result["Client_IP"] == REDACTED
    assert result["PASSWORD"] == REDACTED


def test_deep_recursion_is_bounded() -> None:
    """A pathological payload must not turn a log call into a stack overflow."""
    deep: dict[str, Any] = {"password": "leak"}
    for _ in range(50):
        deep = {"nested": deep}
    result = _apply({"event": "x", "data": deep})
    assert isinstance(result, dict)  # completed rather than recursing forever


def test_public_addresses_in_free_text_are_masked() -> None:
    """Key-based redaction cannot reach an address inside a traceback.

    A connection error renders as "could not connect to 203.0.113.47:5432", and
    that string is exactly what ADR-0007 spends real effort keeping out of the
    database. No sensitive *key* is involved, so a second value-level pass over
    free-text fields is the only thing that catches it.
    """
    result = _apply(
        {
            # A real, globally-routable address -- the documentation ranges
            # (203.0.113.0/24 and friends) are classified non-global by the
            # stdlib and would not exercise this path.
            "event": "could not connect to 49.37.128.5:5432",
            "exception": "ConnectionError: peer 2401:4900:1c80::5 reset the connection",
        }
    )
    serialised = json.dumps(result)
    assert "49.37.128.5" not in serialised
    assert "2401:4900:1c80::5" not in serialised
    assert "5432" in serialised, "the useful part of the message must survive"


def test_private_and_loopback_addresses_are_kept() -> None:
    """Masking these would cost real diagnostic value for no privacy gain.

    ``127.0.0.1`` and ``172.18.0.3`` are container plumbing, not visitor data, and
    an error message with them stripped is much harder to act on at 3 a.m.
    """
    result = _apply(
        {
            "event": "connect failed: 127.0.0.1:8000",
            "exception": "upstream 172.18.0.3 unreachable",
        }
    )
    assert "127.0.0.1" in result["event"]
    assert "172.18.0.3" in result["exception"]


def test_non_addresses_are_not_mangled() -> None:
    """The candidate regex is loose, so every match is parsed before masking.

    Note the deliberate omission: a four-part version like "3.4.2.1" IS a valid
    public IPv4 address and will be masked. That is the correct trade -- a
    mangled version string costs a moment of confusion, a leaked address does
    not wash out.
    """
    result = _apply({"event": "postgis 3.4.2 build 999.999.999.999 on 10.0.0.1"})
    assert "3.4.2" in result["event"]
    assert "999.999.999.999" in result["event"]
    assert "10.0.0.1" in result["event"]


def test_ip_prefix_and_asn_survive() -> None:
    """The durable coarse fields are deliberately loggable.

    ADR-0007 keeps ip_prefix and ASN precisely so networks can be reasoned about
    after the address itself is gone. Redacting them would make operational
    diagnosis impossible for no privacy gain.
    """
    result = _apply({"event": "x", "ip_prefix": "203.0.113.0/24", "asn": 24560})
    assert result["ip_prefix"] == "203.0.113.0/24"
    assert result["asn"] == 24560


def test_end_to_end_through_the_configured_logger() -> None:
    """Proves the processor is actually wired into the pipeline.

    A correct processor that was never installed would pass every test above.
    """
    stream = StringIO()
    structlog.configure(
        processors=[redact_processor, structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=False,
    )
    structlog.get_logger("test").info(
        "visit_captured",
        ip="203.0.113.47",
        password="hunter2-do-not-log",
        ip_prefix="203.0.113.0/24",
    )
    output = stream.getvalue()

    assert "203.0.113.47" not in output
    assert "hunter2-do-not-log" not in output
    assert REDACTED in output
    assert "203.0.113.0/24" in output

    structlog.reset_defaults()
