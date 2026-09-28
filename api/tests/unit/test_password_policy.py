"""Password policy (F8.AC17).

Each ``FieldError`` code has its own test, because the frontend renders the code
rather than the message and a silently renamed code becomes an unexplained
rejection the admin cannot act on.

The tokenised-matching tests are the ones that matter. Substring matching looks
equivalent and is not: ``swaroop.rayker@…`` and ``swaroop-rayker-secret`` share no
substring, because the separator differs -- and swapping a dot for a hyphen is
exactly the variation people reach for (docs/ERRORS.md E15).
"""

from __future__ import annotations

import pytest

from tracelet.auth.service import MIN_PASSWORD_LENGTH, validate_password
from tracelet.errors import ValidationFailed

EMAIL = "swaroop.rayker@example.test"
SITE = "tracelet.example.com"


def codes_for(password: str, *, email: str = EMAIL, site_address: str = SITE) -> set[str]:
    """The set of policy codes a password trips, empty if it is acceptable."""
    try:
        validate_password(password, email=email, site_address=site_address)
    except ValidationFailed as exc:
        return {error.code for error in exc.errors}
    return set()


def test_a_reasonable_passphrase_is_accepted() -> None:
    assert codes_for("Brisk-Lantern-Harbour-42") == set()


def test_every_error_names_the_field_the_form_shows() -> None:
    """The frontend attaches the message to an input by field name."""
    with pytest.raises(ValidationFailed) as caught:
        validate_password("short", email=EMAIL, site_address=SITE)
    assert {error.field for error in caught.value.errors} == {"new_password"}
    assert all(error.message for error in caught.value.errors)


# ---------------------------------------------------------------------------
# One test per code
# ---------------------------------------------------------------------------


def test_too_short() -> None:
    assert MIN_PASSWORD_LENGTH == 12
    assert "TOO_SHORT" in codes_for("Quiet-Gale7")  # 11 characters
    assert "TOO_SHORT" not in codes_for("Quiet-Gale72")  # 12


def test_too_common() -> None:
    assert "TOO_COMMON" in codes_for("password123")
    assert "TOO_COMMON" in codes_for("PASSWORD123"), "matching is case-insensitive"
    assert "TOO_COMMON" in codes_for("tracelet123")


def test_contains_identifier() -> None:
    assert "CONTAINS_IDENTIFIER" in codes_for("swaroop-is-here-77")


def test_contains_identifier_is_tokenised_not_substring_matched() -> None:
    """docs/ERRORS.md E15.

    The local part is ``swaroop.rayker``; the password uses hyphens. A substring
    check finds nothing and waves this straight through.
    """
    assert "CONTAINS_IDENTIFIER" in codes_for("swaroop-rayker-secret")
    assert "CONTAINS_IDENTIFIER" in codes_for("Rayker_Winter_2026")


def test_contains_site_name() -> None:
    assert "CONTAINS_SITE_NAME" in codes_for("tracelet-is-mine-9")


def test_the_site_check_uses_only_the_registrable_label() -> None:
    """A password containing ``com`` or ``example`` is not a problem.

    Only the first label is compared, so the check rejects the deployment's own
    name rather than any word that happens to appear in its domain.
    """
    codes = codes_for("Winter-Melon-Comet-8", site_address="tracelet.example.com")
    assert "CONTAINS_SITE_NAME" not in codes


def test_the_site_check_is_skipped_for_localhost() -> None:
    """Otherwise every development password containing ``local`` is rejected."""
    assert codes_for("Localhost-Winter-8", site_address="localhost") == set()


def test_the_site_check_ignores_the_port() -> None:
    assert "CONTAINS_SITE_NAME" in codes_for("tracelet-winter-8", site_address="tracelet.dev:8443")


def test_too_repetitive() -> None:
    assert "TOO_REPETITIVE" in codes_for("abababababab")
    assert "TOO_REPETITIVE" in codes_for("aaaaaaaaaaaaaaaa")


def test_short_fragments_do_not_make_ordinary_passphrases_unusable() -> None:
    """Tokens shorter than four characters are ignored on purpose.

    Matching on ``rk`` or ``ab`` would reject a large share of perfectly good
    passphrases for no security gain.
    """
    assert codes_for("Rk-Winter-Melon-88", email="rk@example.test") == set()


def test_all_codes_can_fire_together() -> None:
    """The response carries every violation, so one round trip fixes them all."""
    codes = codes_for("swaroop", email=EMAIL, site_address=SITE)
    assert {"TOO_SHORT", "CONTAINS_IDENTIFIER"} <= codes


def test_the_policy_reports_every_violation_rather_than_the_first() -> None:
    codes = codes_for("tracelet", email="tracelet@example.test", site_address=SITE)
    assert {"TOO_SHORT", "TOO_COMMON", "CONTAINS_IDENTIFIER", "CONTAINS_SITE_NAME"} <= codes
