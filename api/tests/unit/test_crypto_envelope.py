"""AES-256-GCM envelope encryption (ADR-0007).

Every assertion here is about a failure mode that would be silent if it regressed.
A round trip that works proves almost nothing on its own -- what matters is that a
ciphertext moved to another row, sealed under another key version, or truncated in
transit **fails** rather than decrypting to something plausible.

The key comes from a file this module writes, not from the bind-mounted
``/run/secrets/ip_key``. Unit tests must pass with no container and no mount.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest

from tracelet.crypto.envelope import (
    CURRENT_KEY_VERSION,
    KEY_BYTES,
    NONCE_BYTES,
    DecryptionError,
    Envelope,
    EnvelopeError,
    open_envelope,
    open_str,
    reset_key_cache,
    seal,
    seal_str,
)

AAD = "11111111-2222-3333-4444-555555555555"
OTHER_AAD = "99999999-8888-7777-6666-555555555555"


@pytest.fixture
def key_path(tmp_path: Path) -> Iterator[str]:
    """A fresh hex key file, and a cache cleared around the test.

    The loaded key is cached per path, so a test that reuses a path would read a
    stale key and its failure would be attributed to the wrong thing.
    """
    path = tmp_path / "ip_key"
    path.write_text(secrets.token_hex(KEY_BYTES), encoding="ascii")
    reset_key_cache()
    yield str(path)
    reset_key_cache()


def test_round_trip_returns_the_original_bytes(key_path: str) -> None:
    envelope = seal(b"JBSWY3DPEHPK3PXP", aad=AAD, key_path=key_path)

    assert envelope.key_version == CURRENT_KEY_VERSION
    assert open_envelope(envelope, aad=AAD, key_path=key_path) == b"JBSWY3DPEHPK3PXP"


def test_round_trip_accepts_a_string_and_returns_one(key_path: str) -> None:
    sealed = seal_str("secret-value", aad=AAD, key_path=key_path)
    assert open_str(sealed, aad=AAD, key_path=key_path) == "secret-value"


def test_the_same_plaintext_seals_differently_every_time(key_path: str) -> None:
    """A fresh nonce per seal. Equal ciphertexts would leak equality of secrets."""
    first = seal_str("same", aad=AAD, key_path=key_path)
    second = seal_str("same", aad=AAD, key_path=key_path)
    assert first.payload != second.payload


def test_wrong_aad_fails(key_path: str) -> None:
    """The property that stops a secret being transplanted between rows.

    Swapping one admin's encrypted TOTP secret for another's must fail
    authentication rather than succeed as the wrong person.
    """
    sealed = seal_str("bound-to-one-row", aad=AAD, key_path=key_path)

    with pytest.raises(DecryptionError):
        open_str(sealed, aad=OTHER_AAD, key_path=key_path)


def test_wrong_key_version_fails_before_any_decryption_is_attempted(key_path: str) -> None:
    sealed = seal_str("value", aad=AAD, key_path=key_path)
    from_the_future = Envelope(key_version=CURRENT_KEY_VERSION + 1, payload=sealed.payload)

    with pytest.raises(DecryptionError, match="key version"):
        open_envelope(from_the_future, aad=AAD, key_path=key_path)


def test_truncated_ciphertext_fails(key_path: str) -> None:
    sealed = seal_str("value", aad=AAD, key_path=key_path)
    chopped = Envelope(key_version=sealed.key_version, payload=sealed.payload[:-1])

    with pytest.raises(DecryptionError):
        open_envelope(chopped, aad=AAD, key_path=key_path)


def test_payload_shorter_than_a_nonce_is_rejected_by_length(key_path: str) -> None:
    stub = Envelope(key_version=CURRENT_KEY_VERSION, payload=b"\x00" * NONCE_BYTES)

    with pytest.raises(DecryptionError, match="too short"):
        open_envelope(stub, aad=AAD, key_path=key_path)


def test_a_flipped_bit_in_the_ciphertext_fails(key_path: str) -> None:
    """GCM authenticates. Tampering is detected, not silently decrypted."""
    sealed = seal_str("value", aad=AAD, key_path=key_path)
    payload = bytearray(sealed.payload)
    payload[NONCE_BYTES] ^= 0x01

    with pytest.raises(DecryptionError):
        open_envelope(Envelope(sealed.key_version, bytes(payload)), aad=AAD, key_path=key_path)


def test_a_different_key_fails(tmp_path: Path) -> None:
    first = tmp_path / "key_a"
    second = tmp_path / "key_b"
    first.write_text(secrets.token_hex(KEY_BYTES), encoding="ascii")
    second.write_text(secrets.token_hex(KEY_BYTES), encoding="ascii")
    reset_key_cache()

    sealed = seal_str("value", aad=AAD, key_path=str(first))
    with pytest.raises(DecryptionError):
        open_str(sealed, aad=AAD, key_path=str(second))

    reset_key_cache()


def test_repr_leaks_neither_ciphertext_nor_plaintext(key_path: str) -> None:
    """An Envelope in a log line or a traceback must reveal nothing."""
    sealed = seal_str("JBSWY3DPEHPK3PXP", aad=AAD, key_path=key_path)
    rendered = repr(sealed)

    assert "JBSWY3DPEHPK3PXP" not in rendered
    assert sealed.payload.hex() not in rendered
    assert str(sealed.payload) not in rendered
    assert f"<{len(sealed.payload)} bytes>" in rendered


def test_a_raw_32_byte_key_file_is_accepted(tmp_path: Path) -> None:
    """The documented generation command produces hex; a raw key still works."""
    path = tmp_path / "raw_key"
    path.write_bytes(secrets.token_bytes(KEY_BYTES))
    reset_key_cache()

    sealed = seal_str("value", aad=AAD, key_path=str(path))
    assert open_str(sealed, aad=AAD, key_path=str(path)) == "value"

    reset_key_cache()


# Every ASCII byte `bytes.strip()` removes. A uniformly random 32-byte key begins
# or ends with one of these about 4.6% of the time, which is how E22 reached CI as
# an intermittent failure rather than a deterministic one.
WHITESPACE_BYTES = [
    bytes([0x09]),  # tab
    bytes([0x0A]),  # newline
    bytes([0x0B]),  # vertical tab
    bytes([0x0C]),  # form feed
    bytes([0x0D]),  # carriage return
    bytes([0x20]),  # space
]


@pytest.mark.parametrize("edge", WHITESPACE_BYTES)
def test_a_raw_key_bounded_by_a_whitespace_byte_is_accepted(tmp_path: Path, edge: bytes) -> None:
    """docs/ERRORS.md E22 — the bug the random test found only sometimes.

    Stripping before deciding hex-vs-raw removed a legitimate byte of key material
    and rejected the key as 31 bytes. A key is uniformly random: no byte value in it
    is special, least of all one that happens to be a space.
    """
    key = edge + secrets.token_bytes(KEY_BYTES - 2) + edge
    assert len(key) == KEY_BYTES
    path = tmp_path / "raw_key"
    path.write_bytes(key)
    reset_key_cache()

    sealed = seal_str("value", aad=AAD, key_path=str(path))
    assert open_str(sealed, aad=AAD, key_path=str(path)) == "value"

    reset_key_cache()


def test_a_hex_key_file_tolerates_the_trailing_newline_openssl_writes(tmp_path: Path) -> None:
    """Which is why the stripping exists at all, and must stay for this case."""
    path = tmp_path / "hex_key"
    # Leading spaces and a trailing newline, exactly as a hand-edited file or
    # `openssl rand -hex 32 > file` can leave it.
    raw = "  " + secrets.token_hex(KEY_BYTES) + chr(10)
    path.write_text(raw, encoding="ascii")
    reset_key_cache()

    sealed = seal_str("value", aad=AAD, key_path=str(path))
    assert open_str(sealed, aad=AAD, key_path=str(path)) == "value"

    reset_key_cache()


def test_a_missing_key_file_names_the_path_and_the_fix(tmp_path: Path) -> None:
    reset_key_cache()
    with pytest.raises(EnvelopeError, match="openssl rand -hex 32"):
        seal_str("value", aad=AAD, key_path=str(tmp_path / "absent"))


def test_a_wrong_length_key_is_rejected_with_the_expected_size(tmp_path: Path) -> None:
    path = tmp_path / "short_key"
    path.write_text("deadbeef", encoding="ascii")
    reset_key_cache()

    with pytest.raises(EnvelopeError, match="AES-256 needs exactly"):
        seal_str("value", aad=AAD, key_path=str(path))
