"""Password hashing and keyed hashing.

Two separate concerns that both live here because both are one-way and both depend
on pinned parameters:

**Argon2id for passwords and recovery codes.** The parameters are pinned, not
defaulted, and that is a memory-budget decision as much as a security one --
see :data:`ARGON2_MEMORY_KIB`.

**HMAC-SHA256 for identity and lookup keys.** Session tokens are stored as a hash
so the database never holds a usable credential; from M2, visitor identity uses the
same primitive with a secret pepper (ADR-0006).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import secrets

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Argon2id parameters — PINNED. Read the comment before changing any of these.
# ---------------------------------------------------------------------------

# 32 MiB per hash operation.
#
# This is the single most important number in this file, and it is a **memory**
# decision. The production box has 1 GB with roughly 210 MB committed to
# PostgreSQL. Several Argon2 wrappers default to 64 MiB, and widely-cited guidance
# recommends up to 1 GiB -- at which point ONE login invokes the OOM killer and
# takes the capture endpoint down with it.
#
# 32 MiB x 2 concurrent admin logins (NFR1.AC3) is 64 MB transient, which fits the
# headroom. Logins are additionally serialised by rate limiting.
#
# Raising this without raising the box's memory is a self-inflicted denial of
# service. See CLAUDE.md §5 and docs/ARCHITECTURE.md §6.3.
ARGON2_MEMORY_KIB = 32 * 1024

ARGON2_TIME_COST = 3
ARGON2_PARALLELISM = 1
ARGON2_HASH_LEN = 32
ARGON2_SALT_LEN = 16

_hasher = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_KIB,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
    type=Type.ID,  # Argon2id: the recommended variant
)

# A pre-computed hash of a throwaway value, used to equalise response time for
# accounts that do not exist. Computed lazily so importing this module stays cheap.
_DUMMY_PASSWORD = "tracelet-timing-equalisation-placeholder"  # noqa: S105 - not a credential
_dummy_hash: str | None = None


def hash_password(password: str) -> str:
    """Return an Argon2id PHC string. Never log or return the input."""
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """Constant-time-ish verification. Returns False rather than raising."""
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def verify_dummy_password() -> None:
    """Burn a comparable amount of time for an account that does not exist.

    F8.AC10 requires that login be indistinguishable in **timing** as well as in
    response body for existing and non-existing accounts. Without this, a wrong
    password costs ~50 ms of Argon2 while an unknown address returns immediately,
    and that difference is a reliable account-enumeration oracle.
    """
    global _dummy_hash  # noqa: PLW0603 - process-wide cache, computed once
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash(_DUMMY_PASSWORD)
    with contextlib.suppress(VerifyMismatchError, InvalidHashError):
        _hasher.verify(_dummy_hash, _DUMMY_PASSWORD + "x")


def needs_rehash(stored_hash: str) -> bool:
    """True when the stored hash used weaker parameters than the current policy.

    Lets a parameter change take effect on next login instead of needing a reset.
    """
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def argon2_parameters() -> dict[str, int | str]:
    """The active parameters, for the audit log and the System Health page."""
    return {
        "type": "argon2id",
        "memory_kib": ARGON2_MEMORY_KIB,
        "time_cost": ARGON2_TIME_COST,
        "parallelism": ARGON2_PARALLELISM,
        "hash_len": ARGON2_HASH_LEN,
    }


# ---------------------------------------------------------------------------
# Keyed and unkeyed digests
# ---------------------------------------------------------------------------


def sha256_bytes(value: str | bytes) -> bytes:
    """Plain SHA-256.

    Correct for a **high-entropy** value such as a 256-bit session token: there is
    nothing to brute-force, so a pepper adds no protection. It is NOT correct for a
    low-entropy value -- use :func:`hmac_sha256` with a secret pepper there
    (ADR-0006).
    """
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).digest()


def hmac_sha256(pepper: str | bytes, *parts: str | bytes) -> bytes:
    """Keyed digest over the concatenation of ``parts``.

    The length prefix before each part prevents ambiguity: without it,
    ``("ab", "c")`` and ``("a", "bc")`` would hash identically, which would let two
    different inputs collide into one visitor identity.
    """
    key = pepper.encode("utf-8") if isinstance(pepper, str) else pepper
    mac = hmac.new(key, digestmod=hashlib.sha256)
    for part in parts:
        data = part.encode("utf-8") if isinstance(part, str) else part
        mac.update(len(data).to_bytes(4, "big"))
        mac.update(data)
    return mac.digest()


def constant_time_equals(a: bytes | str, b: bytes | str) -> bool:
    """Timing-safe comparison. Use for every secret comparison."""
    left = a.encode("utf-8") if isinstance(a, str) else a
    right = b.encode("utf-8") if isinstance(b, str) else b
    return hmac.compare_digest(left, right)


def new_token(n_bytes: int = 32) -> str:
    """A URL-safe random token. 32 bytes = 256 bits of entropy."""
    return secrets.token_urlsafe(n_bytes)
