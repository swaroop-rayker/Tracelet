"""TOTP second factor (F8.AC4, F8.AC5).

**Mandatory, not optional.** This dashboard holds visitor telemetry and, from M2,
decryptable IP addresses for 30 days. A password alone guarding that is not
defensible, so the ``admins`` table has a CHECK constraint preventing an account
from being ``active`` without ``totp_enrolled_at`` -- the requirement is enforced by
the engine, not by a service-layer guard someone can forget.

**Replay prevention.** A plain "does this code validate" check accepts the same six
digits repeatedly for its whole 30-second window. Someone who observes a code --
over a shoulder, in a screen share, from a phished form -- can reuse it. So the
highest accepted time step is stored per admin and a code at or below it is
rejected (F8.AC5).

The secret is stored AES-256-GCM encrypted with the visit id as AAD, using the same
envelope as ``visits.ip_enc`` (ADR-0007).
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from dataclasses import dataclass
from urllib.parse import quote

import pyotp
import structlog

from tracelet.crypto.envelope import Envelope, open_str, seal_str

log = structlog.get_logger(__name__)

TOTP_DIGITS = 6
TOTP_INTERVAL = 30

# One step either side, so a clock skew of up to 30 seconds still works. Wider
# would materially enlarge the replay window; narrower breaks real phones whose
# clocks drift.
TOTP_SKEW_STEPS = 1

ISSUER = "Tracelet"


@dataclass(frozen=True, slots=True)
class Enrollment:
    """Everything the admin needs to add the account to an authenticator app.

    Returned exactly once, at enrollment. The secret is never retrievable
    afterwards -- losing the authenticator means using a recovery code.
    """

    secret: str
    otpauth_uri: str
    sealed: Envelope

    def __repr__(self) -> str:
        return "Enrollment(secret=<redacted>, otpauth_uri=<redacted>)"


def generate_enrollment(*, admin_id: uuid.UUID, email: str, key_path: str) -> Enrollment:
    """Create a new TOTP secret and its sealed form."""
    secret = pyotp.random_base32()

    # The label is what appears in the authenticator app. Quoted, because an email
    # with a reserved character would otherwise corrupt the URI.
    label = quote(f"{ISSUER}:{email}", safe="")
    otpauth_uri = (
        f"otpauth://totp/{label}"
        f"?secret={secret}&issuer={quote(ISSUER)}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_INTERVAL}"
    )

    # AAD binds the ciphertext to this admin, so an encrypted secret cannot be moved
    # to another row -- swapping two admins' secrets fails rather than
    # authenticating as the wrong person.
    sealed = seal_str(secret, aad=str(admin_id), key_path=key_path)
    return Enrollment(secret=secret, otpauth_uri=otpauth_uri, sealed=sealed)


def current_step(at: float | None = None) -> int:
    """The TOTP time step, which is what gets stored for replay prevention."""
    return int((at if at is not None else time.time()) // TOTP_INTERVAL)


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    step: int | None
    reason: str | None = None


def verify(
    *,
    sealed: Envelope,
    admin_id: uuid.UUID,
    code: str,
    last_counter: int | None,
    key_path: str,
    at: float | None = None,
) -> VerifyResult:
    """Validate a code, rejecting replays.

    Returns the accepted step so the caller can persist it. The caller MUST persist
    it, or replay prevention does nothing.
    """
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != TOTP_DIGITS:
        return VerifyResult(ok=False, step=None, reason="malformed")

    secret = open_str(sealed, aad=str(admin_id), key_path=key_path)
    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL)
    now = at if at is not None else time.time()

    # Checked newest-first, so a legitimate current code is matched before an older
    # in-window one and the stored counter advances as far as it can.
    for offset in range(TOTP_SKEW_STEPS, -TOTP_SKEW_STEPS - 1, -1):
        candidate_time = now + offset * TOTP_INTERVAL
        step = current_step(candidate_time)
        moment = dt.datetime.fromtimestamp(candidate_time, tz=dt.UTC)
        if not totp.verify(code, for_time=moment, valid_window=0):
            continue
        if last_counter is not None and step <= last_counter:
            # Correct code, already spent. This is the case a naive implementation
            # accepts.
            return VerifyResult(ok=False, step=step, reason="replayed")
        return VerifyResult(ok=True, step=step)

    return VerifyResult(ok=False, step=None, reason="invalid")


def provisioning_hint() -> str:
    """Manual-entry guidance.

    No QR code is rendered, deliberately: it would mean another dependency (ES5) to
    save one or two admins a single manual entry, and every authenticator app
    supports typing a base32 secret. Worth revisiting if the admin count grows.
    """
    return (
        f"Add a new account manually in your authenticator app: "
        f"type {TOTP_DIGITS}-digit, time-based, {TOTP_INTERVAL}-second, SHA1, "
        f"and paste the secret exactly as shown."
    )
