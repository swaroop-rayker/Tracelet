"""The enrichment nonce: a single-use, prefix-bound, 60-second capability (F2.AC6).

The capture page is served to anyone, so the enrichment endpoint cannot trust the
caller. What authorises a POST to ``/api/v1/s/{nonce}`` is possession of a token that
only the server could have minted, for **this** visit, from **this** network, within
the last minute.

**Format.** ``base64url(visit_id[16] ‖ expiry[4] ‖ mac[16])`` -- 48 characters, safe in a
URL path. The MAC covers the visit id, the expiry and the client's IP prefix. The prefix
is deliberately *not* in the token: the verifier recomputes the MAC from the prefix it
observes, so a nonce carried to another network simply fails to verify rather than
announcing which network it was minted for.

**Stateless to verify, single-use by the database.** Nothing is stored at mint time. A
replay is caught by the conditional ``UPDATE ... WHERE enrichment_consumed_at IS NULL``
that applies the enrichment, which is the only place single-use can be decided under
concurrency (docs/DATA_MODEL.md section 5.3, invariant 6).

**A known edge, recorded rather than hidden.** A dual-stack browser could, in principle,
load the page over IPv4 and send the POST over IPv6. Browsers reuse the connection for a
same-origin request fired within a second of page load, so this should be rare; it would
surface as a 410 rate on the enrichment endpoint, and the visit is recorded regardless
(the sweeper finalises it as ``server_only``).
"""

from __future__ import annotations

import base64
import binascii
import hmac
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256
from typing import Final

TTL_SECONDS: Final = 60
_MAC_BYTES: Final = 16
_LABEL: Final = b"tracelet.enrichment-nonce.v1"


def derive_key(secret: str) -> bytes:
    """A key used for nothing else.

    Derived from the session secret with a fixed label, so the same secret can never
    produce a value that is valid in two different places.
    """
    return hmac.new(secret.encode("utf-8"), _LABEL, sha256).digest()


def _mac(key: bytes, visit_id: uuid.UUID, expiry: int, ip_prefix: str | None) -> bytes:
    message = b"".join(
        (
            _LABEL,
            visit_id.bytes,
            expiry.to_bytes(4, "big"),
            (ip_prefix or "-").encode("ascii"),
        )
    )
    return hmac.new(key, message, sha256).digest()[:_MAC_BYTES]


def mint(
    key: bytes, *, visit_id: uuid.UUID, ip_prefix: str | None, now: float | None = None
) -> str:
    expiry = int(now if now is not None else time.time()) + TTL_SECONDS
    raw = visit_id.bytes + expiry.to_bytes(4, "big") + _mac(key, visit_id, expiry, ip_prefix)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@dataclass(frozen=True, slots=True)
class Verified:
    visit_id: uuid.UUID


class NonceRejectedError(ValueError):
    """Malformed, forged, expired, or bound to another network.

    Deliberately one exception for all four. The endpoint answers 410 for every one of
    them, and the reason goes to the log -- telling a caller *why* a nonce failed would
    tell them which part to change.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def verify(
    key: bytes,
    token: str,
    *,
    ip_prefix: str | None,
    now: float | None = None,
    check_expiry: bool = True,
) -> Verified:
    """Authenticate a nonce for this network.

    ``check_expiry=False`` is for the honeypot only, where a late hit is the evidence
    wanted. The MAC and the prefix binding hold either way, so disabling expiry never
    lets anyone act on a visit that is not theirs.
    """
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (binascii.Error, ValueError) as exc:
        raise NonceRejectedError("malformed") from exc
    if len(raw) != 16 + 4 + _MAC_BYTES:
        raise NonceRejectedError("malformed")

    visit_id = uuid.UUID(bytes=raw[:16])
    expiry = int.from_bytes(raw[16:20], "big")
    supplied = raw[20:]

    # MAC before expiry, so an attacker cannot learn anything about a forged token's
    # expiry field by watching which check it fails.
    if not hmac.compare_digest(supplied, _mac(key, visit_id, expiry, ip_prefix)):
        raise NonceRejectedError("mac_mismatch")
    if check_expiry and int(now if now is not None else time.time()) > expiry:
        raise NonceRejectedError("expired")
    return Verified(visit_id=visit_id)
