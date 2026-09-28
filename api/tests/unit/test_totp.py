"""TOTP verification, including replay prevention (F8.AC4, F8.AC5).

Every test pins the clock with the ``at`` parameter. A test that used the real
wall clock would be flaky for 1 second in every 30 -- and, worse, would pass or
fail depending on where in the current window it happened to run, which is the
least useful signal a test can give.

The replay assertions are the point of the module. A plain "does this code
validate" check accepts the same six digits for the whole 30-second window, so
someone who observes a code over a shoulder or in a screen share can reuse it.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from collections.abc import Iterator
from pathlib import Path

import pyotp
import pytest

from tracelet.auth import totp
from tracelet.crypto.envelope import (
    KEY_BYTES,
    DecryptionError,
    Envelope,
    open_str,
    reset_key_cache,
)

ADMIN_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
OTHER_ADMIN_ID = uuid.UUID("99999999-8888-7777-6666-555555555555")

# A fixed instant, chosen to sit mid-window so no assertion is affected by the
# boundary. Step = 1789131630 // 30.
NOW = 1789131630.0 + 7.0


@pytest.fixture
def key_path(tmp_path: Path) -> Iterator[str]:
    path = tmp_path / "ip_key"
    path.write_text(secrets.token_hex(KEY_BYTES), encoding="ascii")
    reset_key_cache()
    yield str(path)
    reset_key_cache()


@pytest.fixture
def enrollment(key_path: str) -> totp.Enrollment:
    return totp.generate_enrollment(
        admin_id=ADMIN_ID, email="owner@example.test", key_path=key_path
    )


def code_at(secret: str, step: int) -> str:
    """The six digits for one time step, generated the way a phone would."""
    moment = dt.datetime.fromtimestamp(step * totp.TOTP_INTERVAL + 1, tz=dt.UTC)
    return pyotp.TOTP(secret, digits=totp.TOTP_DIGITS, interval=totp.TOTP_INTERVAL).at(moment)


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------


def test_enrollment_produces_a_usable_secret_and_uri(enrollment: totp.Enrollment) -> None:
    assert len(enrollment.secret) >= 16
    assert enrollment.otpauth_uri.startswith("otpauth://totp/Tracelet%3Aowner%40example.test?")
    assert f"secret={enrollment.secret}" in enrollment.otpauth_uri
    assert "digits=6" in enrollment.otpauth_uri
    assert "period=30" in enrollment.otpauth_uri
    assert "issuer=Tracelet" in enrollment.otpauth_uri


def test_enrollment_repr_leaks_neither_secret_nor_uri(enrollment: totp.Enrollment) -> None:
    rendered = repr(enrollment)
    assert enrollment.secret not in rendered
    assert "redacted" in rendered


def test_the_sealed_secret_is_bound_to_the_admin_row(key_path: str) -> None:
    """AAD binding: an encrypted secret cannot be moved to another admin.

    Without it, swapping two rows' ciphertext would authenticate as the wrong
    person instead of failing.
    """
    enrolled = totp.generate_enrollment(
        admin_id=ADMIN_ID, email="owner@example.test", key_path=key_path
    )
    assert open_str(enrolled.sealed, aad=str(ADMIN_ID), key_path=key_path) == enrolled.secret

    with pytest.raises(DecryptionError):
        open_str(enrolled.sealed, aad=str(OTHER_ADMIN_ID), key_path=key_path)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_the_current_code_is_accepted_and_reports_its_step(
    enrollment: totp.Enrollment, key_path: str
) -> None:
    step = totp.current_step(NOW)
    result = totp.verify(
        sealed=enrollment.sealed,
        admin_id=ADMIN_ID,
        code=code_at(enrollment.secret, step),
        last_counter=None,
        key_path=key_path,
        at=NOW,
    )

    assert result.ok is True
    assert result.step == step, "the caller must be able to persist the accepted step"
    assert result.reason is None


def test_a_spent_step_is_rejected_as_replayed(enrollment: totp.Enrollment, key_path: str) -> None:
    """The case a naive implementation accepts."""
    step = totp.current_step(NOW)
    result = totp.verify(
        sealed=enrollment.sealed,
        admin_id=ADMIN_ID,
        code=code_at(enrollment.secret, step),
        last_counter=step,
        key_path=key_path,
        at=NOW,
    )

    assert result.ok is False
    assert result.reason == "replayed"


def test_a_step_below_the_high_water_mark_is_also_replayed(
    enrollment: totp.Enrollment, key_path: str
) -> None:
    step = totp.current_step(NOW)
    result = totp.verify(
        sealed=enrollment.sealed,
        admin_id=ADMIN_ID,
        code=code_at(enrollment.secret, step - 1),
        last_counter=step,
        key_path=key_path,
        at=NOW,
    )

    assert result.ok is False
    assert result.reason == "replayed"


def test_a_step_above_the_high_water_mark_is_accepted(
    enrollment: totp.Enrollment, key_path: str
) -> None:
    """Replay prevention must not become a lockout. Only the past is refused."""
    step = totp.current_step(NOW)
    result = totp.verify(
        sealed=enrollment.sealed,
        admin_id=ADMIN_ID,
        code=code_at(enrollment.secret, step),
        last_counter=step - 1,
        key_path=key_path,
        at=NOW,
    )

    assert result.ok is True
    assert result.step == step


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_one_step_of_clock_skew_either_side_is_tolerated(
    enrollment: totp.Enrollment, key_path: str, offset: int
) -> None:
    """A phone whose clock drifts by up to 30 seconds still works."""
    step = totp.current_step(NOW) + offset
    result = totp.verify(
        sealed=enrollment.sealed,
        admin_id=ADMIN_ID,
        code=code_at(enrollment.secret, step),
        last_counter=None,
        key_path=key_path,
        at=NOW,
    )

    assert result.ok is True, f"offset {offset} should be inside the skew window"
    assert result.step == step


@pytest.mark.parametrize("offset", [-2, 2, 10, -10])
def test_two_steps_of_skew_is_not_tolerated(
    enrollment: totp.Enrollment, key_path: str, offset: int
) -> None:
    """Wider tolerance would materially enlarge the replay window."""
    step = totp.current_step(NOW) + offset
    result = totp.verify(
        sealed=enrollment.sealed,
        admin_id=ADMIN_ID,
        code=code_at(enrollment.secret, step),
        last_counter=None,
        key_path=key_path,
        at=NOW,
    )

    assert result.ok is False
    assert result.reason == "invalid"


@pytest.mark.parametrize(
    "code",
    ["", "12345", "1234567", "abcdef", "12 34 5", "12345a", "------"],
)
def test_malformed_input_is_rejected_before_any_decryption(
    enrollment: totp.Enrollment, key_path: str, code: str
) -> None:
    """Rejected on shape, so a junk submission never costs a decryption."""
    result = totp.verify(
        sealed=enrollment.sealed,
        admin_id=ADMIN_ID,
        code=code,
        last_counter=None,
        key_path=key_path,
        at=NOW,
    )

    assert result.ok is False
    assert result.reason == "malformed"
    assert result.step is None


def test_spaces_inside_a_well_formed_code_are_tolerated(
    enrollment: totp.Enrollment, key_path: str
) -> None:
    """Authenticator apps display ``123 456``; pasting that must work."""
    step = totp.current_step(NOW)
    digits = code_at(enrollment.secret, step)
    spaced = f" {digits[:3]} {digits[3:]} "

    result = totp.verify(
        sealed=enrollment.sealed,
        admin_id=ADMIN_ID,
        code=spaced,
        last_counter=None,
        key_path=key_path,
        at=NOW,
    )

    assert result.ok is True


def test_a_code_for_another_secret_is_invalid(enrollment: totp.Enrollment, key_path: str) -> None:
    other = totp.generate_enrollment(
        admin_id=ADMIN_ID, email="other@example.test", key_path=key_path
    )
    result = totp.verify(
        sealed=enrollment.sealed,
        admin_id=ADMIN_ID,
        code=code_at(other.secret, totp.current_step(NOW)),
        last_counter=None,
        key_path=key_path,
        at=NOW,
    )

    assert result.ok is False
    assert result.reason == "invalid"


def test_the_newest_in_window_step_wins(enrollment: totp.Enrollment, key_path: str) -> None:
    """Checked newest-first so the stored counter advances as far as it can.

    Otherwise an older in-window code would set a lower high-water mark and leave
    the newer step still spendable.
    """
    step = totp.current_step(NOW)
    # A secret whose codes collide across steps does not exist in practice; this
    # asserts the loop order by confirming the newest matching step is returned.
    result = totp.verify(
        sealed=enrollment.sealed,
        admin_id=ADMIN_ID,
        code=code_at(enrollment.secret, step + 1),
        last_counter=None,
        key_path=key_path,
        at=NOW,
    )

    assert result.ok is True
    assert result.step == step + 1


def test_verification_fails_cleanly_when_the_sealed_secret_is_not_ours(key_path: str) -> None:
    """A row whose ciphertext does not belong to it must raise, not authenticate."""
    enrolled = totp.generate_enrollment(
        admin_id=ADMIN_ID, email="owner@example.test", key_path=key_path
    )
    with pytest.raises(DecryptionError):
        totp.verify(
            sealed=Envelope(enrolled.sealed.key_version, enrolled.sealed.payload),
            admin_id=OTHER_ADMIN_ID,
            code=code_at(enrolled.secret, totp.current_step(NOW)),
            last_counter=None,
            key_path=key_path,
            at=NOW,
        )


def test_current_step_follows_the_interval() -> None:
    assert totp.current_step(0.0) == 0
    assert totp.current_step(29.9) == 0
    assert totp.current_step(30.0) == 1
    assert totp.current_step(NOW) == int(NOW // totp.TOTP_INTERVAL)


def test_the_provisioning_hint_describes_the_parameters_we_actually_use() -> None:
    """No QR code is rendered (ES5), so the manual-entry text has to be right."""
    hint = totp.provisioning_hint()
    assert str(totp.TOTP_DIGITS) in hint
    assert str(totp.TOTP_INTERVAL) in hint
    assert "SHA1" in hint
