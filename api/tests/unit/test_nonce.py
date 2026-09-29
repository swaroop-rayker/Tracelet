"""The enrichment nonce (F2.AC6): single-use, prefix-bound, 60 seconds.

Single-use is decided by the database and tested in the integration suite. This file
covers everything the token itself must refuse: another network, another visit, a
tampered byte, an expired timestamp, a different key, and garbage.
"""

from __future__ import annotations

import base64
import time
import uuid

import pytest

from tracelet.capture.models import uuid7
from tracelet.capture.nonce import (
    TTL_SECONDS,
    NonceRejectedError,
    derive_key,
    mint,
    verify,
)

KEY = derive_key("test-session-secret")
PREFIX = "49.207.12.0/24"
NOW = 1_790_000_000.0


def _mint(visit_id: uuid.UUID | None = None, prefix: str | None = PREFIX) -> tuple[uuid.UUID, str]:
    vid = visit_id or uuid7()
    return vid, mint(KEY, visit_id=vid, ip_prefix=prefix, now=NOW)


def test_a_fresh_nonce_verifies_to_its_visit() -> None:
    vid, token = _mint()
    assert verify(KEY, token, ip_prefix=PREFIX, now=NOW + 1).visit_id == vid


def test_the_token_is_url_safe_and_fixed_length() -> None:
    """It travels in a URL path, so no padding and no characters that need escaping."""
    _, token = _mint()
    assert len(token) == 48
    assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def test_a_nonce_from_another_network_is_refused() -> None:
    """The M2 done-check: "nonce from a different prefix returns 410"."""
    _, token = _mint()
    with pytest.raises(NonceRejectedError) as caught:
        verify(KEY, token, ip_prefix="203.0.113.0/24", now=NOW + 1)
    assert caught.value.reason == "mac_mismatch"


def test_a_nonce_is_valid_for_sixty_seconds_and_not_one_more() -> None:
    _, token = _mint()
    verify(KEY, token, ip_prefix=PREFIX, now=NOW + TTL_SECONDS)
    with pytest.raises(NonceRejectedError) as caught:
        verify(KEY, token, ip_prefix=PREFIX, now=NOW + TTL_SECONDS + 1)
    assert caught.value.reason == "expired"


def test_a_tampered_byte_is_refused() -> None:
    _, token = _mint()
    flipped = token[:5] + ("A" if token[5] != "A" else "B") + token[6:]
    with pytest.raises(NonceRejectedError):
        verify(KEY, flipped, ip_prefix=PREFIX, now=NOW + 1)


def test_the_visit_id_cannot_be_swapped_for_another() -> None:
    """The MAC covers the visit id: a valid nonce cannot be re-pointed at someone
    else's visit by editing its first sixteen bytes."""
    _, token = _mint()
    raw = bytearray(base64.urlsafe_b64decode(token + "=="))
    raw[:16] = uuid7().bytes
    forged = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    with pytest.raises(NonceRejectedError):
        verify(KEY, forged, ip_prefix=PREFIX, now=NOW + 1)


def test_a_nonce_from_another_key_is_refused() -> None:
    other = derive_key("a-different-secret")
    vid = uuid7()
    token = mint(other, visit_id=vid, ip_prefix=PREFIX, now=NOW)
    with pytest.raises(NonceRejectedError):
        verify(KEY, token, ip_prefix=PREFIX, now=NOW + 1)


@pytest.mark.parametrize("garbage", ["", "x", "!!!!", "A" * 47, "A" * 200, "not base64 at all"])
def test_garbage_is_malformed_not_an_exception(garbage: str) -> None:
    with pytest.raises(NonceRejectedError) as caught:
        verify(KEY, garbage, ip_prefix=PREFIX, now=NOW)
    assert caught.value.reason in {"malformed", "mac_mismatch"}


def test_expiry_can_be_ignored_for_the_honeypot_only() -> None:
    """A late honeypot hit is the evidence wanted; the MAC and the prefix still hold."""
    vid, token = _mint()
    assert verify(KEY, token, ip_prefix=PREFIX, now=NOW + 3600, check_expiry=False).visit_id == vid
    with pytest.raises(NonceRejectedError):
        verify(KEY, token, ip_prefix="203.0.113.0/24", now=NOW + 3600, check_expiry=False)


def test_a_visit_with_no_known_prefix_still_gets_a_working_nonce() -> None:
    vid, token = _mint(prefix=None)
    assert verify(KEY, token, ip_prefix=None, now=NOW + 1).visit_id == vid


def test_the_derived_key_is_not_the_secret() -> None:
    """Domain-separated, so the session secret can never be valid in two places."""
    assert derive_key("s") != b"s"
    assert len(derive_key("s")) == 32
    assert derive_key("s") == derive_key("s")


# ---------------------------------------------------------------------------
# uuid7
# ---------------------------------------------------------------------------


def test_uuid7_sets_the_version_and_variant() -> None:
    value = uuid7()
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_is_time_ordered() -> None:
    """The reason to use it: visits insert at the right edge of the index."""
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()
    assert first < second


def test_uuid7_is_unique() -> None:
    assert len({uuid7() for _ in range(1000)}) == 1000
