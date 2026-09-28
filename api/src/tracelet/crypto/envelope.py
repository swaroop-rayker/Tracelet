"""AES-256-GCM envelope encryption for sensitive columns (ADR-0007).

Used for ``admins.totp_secret_enc`` from M1 and ``visits.ip_enc`` from M2.

**What this defends, precisely.** A stolen ``pg_dump``, an exfiltrated backup file,
SQL injection reaching the data tables, and compromised database credentials --
all of those yield ciphertext, because the key lives in a file outside the
database volume and is only ever held in process memory.

**What it does not defend.** Root compromise of the VM, or a compromised
application process. Free tier means no KMS, so the key has to live on the same
machine. Stating that plainly is the point; overstating it would be worse than the
limitation itself.

**The AAD matters.** Each ciphertext is bound to the row it belongs to, so a value
cannot be silently transplanted from one row to another -- swapping one admin's
encrypted TOTP secret for another's fails authentication instead of succeeding as
the wrong person.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import structlog
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = structlog.get_logger(__name__)

KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # 96-bit, the GCM-recommended size

# Bumped only when the key itself changes. Stored per row so a rotation can
# re-encrypt incrementally rather than in one transaction (F12.AC5).
CURRENT_KEY_VERSION = 1


class EnvelopeError(RuntimeError):
    """Key material is missing or malformed. Never carries the key in its message."""


class DecryptionError(RuntimeError):
    """Ciphertext failed authentication: wrong key, wrong row, or tampering."""


@dataclass(frozen=True, slots=True)
class Envelope:
    """A sealed value, exactly as stored in a ``bytea`` column."""

    key_version: int
    payload: bytes  # nonce ‖ ciphertext ‖ tag

    def __repr__(self) -> str:
        # Never render the payload. An Envelope in a log line or a traceback must
        # not leak ciphertext length patterns or content.
        return f"Envelope(key_version={self.key_version}, payload=<{len(self.payload)} bytes>)"


def _read_key_file(path: Path) -> bytes:
    if not path.exists():
        msg = (
            f"Encryption key file not found at {path}. Generate one with "
            "`openssl rand -hex 32 > secrets/ip_key` and make sure it is mounted "
            "read-only into the container. See .env.example TRACELET_IP_KEY_FILE."
        )
        raise EnvelopeError(msg)

    raw = path.read_bytes()

    # Accept hex (what the documented generation command produces) or 32 raw bytes.
    #
    # The stripping is deliberately applied ONLY to the hex attempt. `openssl rand
    # -hex 32 > file` leaves a trailing newline, so hex has to tolerate surrounding
    # whitespace -- but a raw 32-byte key is uniformly random, and roughly 4.6% of
    # such keys begin or end with a byte that happens to be ASCII whitespace
    # (0x09-0x0D or 0x20). Stripping first silently removed it and rejected a
    # perfectly good key as "31 bytes" (docs/ERRORS.md E22).
    try:
        key = bytes.fromhex(raw.strip().decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        key = raw

    if len(key) != KEY_BYTES:
        msg = (
            f"Encryption key at {path} is {len(key)} bytes; AES-256 needs exactly "
            f"{KEY_BYTES}. Expected 64 hex characters or 32 raw bytes."
        )
        raise EnvelopeError(msg)

    # Best-effort permission check. Not enforced, because bind mounts on Windows
    # and Docker Desktop do not preserve Unix modes -- failing here would make the
    # documented dev environment unusable for a warning.
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            log.warning(
                "key_file_permissions_loose",
                path=str(path),
                mode=oct(mode),
                expected="0400",
            )
    except OSError:
        pass

    return key


@lru_cache(maxsize=4)
def _aesgcm(key_path: str) -> AESGCM:
    """Load the key once per process.

    Cached because the key is read from disk and then lives only in memory; there
    is no reason to touch the filesystem on every encrypt.
    """
    return AESGCM(_read_key_file(Path(key_path)))


def seal(plaintext: bytes | str, *, aad: str, key_path: str) -> Envelope:
    """Encrypt a value, binding it to ``aad`` (normally the owning row's id)."""
    data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    nonce = os.urandom(NONCE_BYTES)
    ct = _aesgcm(key_path).encrypt(nonce, data, aad.encode("utf-8"))
    return Envelope(key_version=CURRENT_KEY_VERSION, payload=nonce + ct)


def open_envelope(envelope: Envelope, *, aad: str, key_path: str) -> bytes:
    """Decrypt and authenticate. Raises :class:`DecryptionError` on any mismatch.

    A failure here means one of: the wrong key, a ciphertext moved between rows, or
    tampering. All three are indistinguishable by design -- GCM authenticates, it
    does not diagnose.
    """
    if envelope.key_version != CURRENT_KEY_VERSION:
        msg = (
            f"Ciphertext was sealed with key version {envelope.key_version} but the "
            f"loaded key is version {CURRENT_KEY_VERSION}. Run the re-encryption job "
            "before retiring the old key."
        )
        raise DecryptionError(msg)

    if len(envelope.payload) <= NONCE_BYTES:
        msg = "Ciphertext is too short to contain a nonce."
        raise DecryptionError(msg)

    nonce, ct = envelope.payload[:NONCE_BYTES], envelope.payload[NONCE_BYTES:]
    try:
        return _aesgcm(key_path).decrypt(nonce, ct, aad.encode("utf-8"))
    except InvalidTag as exc:
        # Deliberately terse: the caller gets "it failed", the log gets the row.
        msg = "Ciphertext failed authentication."
        raise DecryptionError(msg) from exc


def seal_str(plaintext: str, *, aad: str, key_path: str) -> Envelope:
    return seal(plaintext, aad=aad, key_path=key_path)


def open_str(envelope: Envelope, *, aad: str, key_path: str) -> str:
    return open_envelope(envelope, aad=aad, key_path=key_path).decode("utf-8")


def reset_key_cache() -> None:
    """Clear the cached key. For tests and for key rotation."""
    _aesgcm.cache_clear()
