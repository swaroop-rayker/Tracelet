"""Recovery-code generation and normalisation (F8.AC6).

Normalisation is the whole unit surface here -- consuming a code needs a database
and lives in the integration suite. It matters more than it looks: a recovery code
is read off paper months later, typed by someone who has just lost access, and a
normaliser that insists on exact formatting turns a valid code into a lockout.
"""

from __future__ import annotations

import pytest

from tracelet.auth.recovery import (
    ALPHABET,
    CODE_COUNT,
    GROUP_LEN,
    GROUPS,
    LOW_REMAINING_WARNING,
    _generate_code,
    normalise,
)


def test_a_generated_code_has_the_documented_shape() -> None:
    code = _generate_code()
    groups = code.split("-")

    assert len(groups) == GROUPS
    assert all(len(group) == GROUP_LEN for group in groups)
    assert all(character in ALPHABET for group in groups for character in group)


def test_the_alphabet_excludes_every_ambiguous_glyph() -> None:
    """Crockford-style.

    Transcription error is the realistic failure mode for a code read off paper,
    and an ambiguous glyph turns a working code into a lockout.
    """
    for ambiguous in "ILOU01":
        assert ambiguous not in ALPHABET, f"{ambiguous} is easily misread"


def test_generated_codes_do_not_repeat() -> None:
    codes = {_generate_code() for _ in range(200)}
    assert len(codes) == 200


def test_the_documented_counts_are_what_the_ui_promises() -> None:
    assert CODE_COUNT == 10
    assert LOW_REMAINING_WARNING == 3


@pytest.mark.parametrize(
    "supplied",
    [
        "ABCDE-FGHJK",
        "abcde-fghjk",
        "AbCdE-fGhJk",
        "ABCDEFGHJK",
        "ABCDE FGHJK",
        "  abcde fghjk  ",
        "abcde--fghjk",
        "ABCDE_FGHJK",
        "A B C D E F G H J K",
    ],
)
def test_normalisation_accepts_what_a_human_actually_types(supplied: str) -> None:
    """Any case, spaces, missing or doubled dashes, other separators."""
    assert normalise(supplied) == "ABCDE-FGHJK"


def test_normalisation_is_idempotent() -> None:
    once = normalise("abcde fghjk")
    assert normalise(once) == once


def test_a_wrong_length_input_is_returned_without_grouping() -> None:
    """Deliberately not reformatted.

    A too-short entry is a typo, not a code. Grouping it anyway would produce
    something that looks valid and fails for an unexplained reason.
    """
    assert normalise("ABCDE") == "ABCDE"
    assert normalise("ABCDE-FGHJK-LMNPQ") == "ABCDEFGHJKLMNPQ"


def test_normalisation_of_empty_input_does_not_raise() -> None:
    assert normalise("") == ""
    assert normalise("----") == ""
