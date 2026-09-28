"""Password hashing and keyed digests.

The parameter assertions below are the most important in the unit suite, and they
assert **numbers** rather than "some parameters are set". Several Argon2 wrappers
default to 64 MiB and widely-cited guidance recommends up to 1 GiB; on a 1 GB box
with ~210 MB committed to PostgreSQL, one login at a library default invokes the OOM
killer and takes the capture endpoint down with it (CLAUDE.md §5).

A test that only checked "memory_cost is configured" would pass after someone
"upgraded" it to the recommended value. Hence the exact figures.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from argon2.low_level import Type

from tracelet.crypto.hashing import (
    ARGON2_HASH_LEN,
    ARGON2_MEMORY_KIB,
    ARGON2_PARALLELISM,
    ARGON2_SALT_LEN,
    ARGON2_TIME_COST,
    _hasher,
    argon2_parameters,
    constant_time_equals,
    hash_password,
    hmac_sha256,
    needs_rehash,
    new_token,
    sha256_bytes,
    verify_dummy_password,
    verify_password,
)

PASSWORD = "Brisk-Lantern-Harbour-42"


# ---------------------------------------------------------------------------
# Pinned parameters (CLAUDE.md §5)
# ---------------------------------------------------------------------------


def test_argon2_memory_is_exactly_32_mib() -> None:
    """32 MiB, not the library default.

    Raising this without raising the box's memory is a self-inflicted denial of
    service: 32 MiB x 2 concurrent admin logins (NFR1.AC3) is 64 MB transient,
    which fits the headroom in docs/ARCHITECTURE.md §6.
    """
    assert ARGON2_MEMORY_KIB == 32 * 1024, "Argon2 memory must stay pinned at 32 MiB"
    assert _hasher.memory_cost == 32 * 1024


def test_argon2_time_cost_and_parallelism_are_pinned() -> None:
    assert ARGON2_TIME_COST == 3
    assert ARGON2_PARALLELISM == 1, "parallelism > 1 multiplies the transient allocation"
    assert _hasher.time_cost == 3
    assert _hasher.parallelism == 1


def test_the_variant_is_argon2id() -> None:
    assert _hasher.type is Type.ID


def test_hash_and_salt_lengths_are_pinned() -> None:
    assert ARGON2_HASH_LEN == 32
    assert ARGON2_SALT_LEN == 16
    assert _hasher.hash_len == 32
    assert _hasher.salt_len == 16


def test_reported_parameters_match_the_hasher() -> None:
    """``argon2_parameters()`` is stored on the row and shown on System Health.

    If it drifted from the real hasher, ``needs_rehash`` decisions and the health
    page would both describe a configuration that is not in use.
    """
    reported = argon2_parameters()
    assert reported == {
        "type": "argon2id",
        "memory_kib": _hasher.memory_cost,
        "time_cost": _hasher.time_cost,
        "parallelism": _hasher.parallelism,
        "hash_len": _hasher.hash_len,
    }


def test_the_encoded_hash_carries_the_pinned_parameters() -> None:
    """Belt and braces: the PHC string itself must say m=32768,t=3,p=1."""
    encoded = hash_password(PASSWORD)
    assert encoded.startswith("$argon2id$")
    assert "m=32768" in encoded
    assert "t=3" in encoded
    assert "p=1" in encoded


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_verify_accepts_the_right_password_and_rejects_others() -> None:
    encoded = hash_password(PASSWORD)
    assert verify_password(encoded, PASSWORD) is True
    assert verify_password(encoded, PASSWORD + "x") is False
    assert verify_password(encoded, "") is False


def test_verify_returns_false_for_a_malformed_stored_hash() -> None:
    """Never raises. A corrupt column must fail the login, not the process."""
    assert verify_password("not-a-phc-string", PASSWORD) is False


def test_two_hashes_of_one_password_differ() -> None:
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_needs_rehash_is_false_at_the_current_policy() -> None:
    assert needs_rehash(hash_password(PASSWORD)) is False


def test_needs_rehash_is_true_for_something_unreadable() -> None:
    """Fail towards rehashing: an unparseable hash must not be treated as current."""
    assert needs_rehash("garbage") is True


def _fastest_of(call: Callable[[], object], runs: int = 3) -> float:
    """Best-case duration. Uses the minimum because noise only ever adds time."""
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        call()
        best = min(best, time.perf_counter() - started)
    return best


def test_dummy_verification_costs_a_comparable_amount_of_time() -> None:
    """F8.AC10, the timing half.

    Without this, a wrong password costs ~50 ms of Argon2 while an unknown address
    returns immediately -- a reliable account-enumeration oracle that no amount of
    identical response bodies can hide.

    The bound is loose on purpose: the assertion that matters is "the same order of
    magnitude", and a tight ratio would be flaky on a shared CI runner.
    """
    encoded = hash_password(PASSWORD)
    verify_dummy_password()  # warm the lazily-computed dummy hash

    real = _fastest_of(lambda: verify_password(encoded, "wrong-password"))
    dummy = _fastest_of(verify_dummy_password)

    assert dummy > 0
    ratio = dummy / real
    assert 0.2 <= ratio <= 5.0, f"dummy verify was {ratio:.2f}x a real one; timing oracle"


# ---------------------------------------------------------------------------
# Keyed and unkeyed digests
# ---------------------------------------------------------------------------


def test_hmac_length_prefixing_separates_ambiguous_parts() -> None:
    """Without the length prefix, ``("ab", "c")`` and ``("a", "bc")`` collide.

    That collision would let two different inputs resolve to one visitor identity
    from M2 (ADR-0006), which is a correctness bug in the analytics rather than
    only a cryptographic nicety.
    """
    assert hmac_sha256("pepper", "ab", "c") != hmac_sha256("pepper", "a", "bc")


def test_hmac_is_stable_and_key_dependent() -> None:
    assert hmac_sha256("pepper", "value") == hmac_sha256("pepper", "value")
    assert hmac_sha256("pepper", "value") != hmac_sha256("other-pepper", "value")


def test_hmac_accepts_bytes_and_str_interchangeably() -> None:
    assert hmac_sha256(b"pepper", b"value") == hmac_sha256("pepper", "value")


def test_hmac_output_is_32_bytes() -> None:
    assert len(hmac_sha256("pepper", "value")) == 32


def test_sha256_matches_across_str_and_bytes() -> None:
    assert sha256_bytes("token") == sha256_bytes(b"token")
    assert len(sha256_bytes("token")) == 32


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("same", "same", True),
        ("same", "different", False),
        (b"same", "same", True),
        ("", "", True),
    ],
)
def test_constant_time_equals(left: bytes | str, right: bytes | str, expected: bool) -> None:
    assert constant_time_equals(left, right) is expected


def test_new_token_is_unique_and_high_entropy() -> None:
    tokens = {new_token() for _ in range(64)}
    assert len(tokens) == 64
    # 32 random bytes base64url-encoded: 43 characters, no padding.
    assert all(len(token) >= 43 for token in tokens)
