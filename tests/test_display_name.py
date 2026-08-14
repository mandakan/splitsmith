"""Units for the account display-name normalizer (#867).

The blank-to-None rule is the load-bearing one: #866's attribution
branch publishes ``display_name`` when it is non-blank and falls back to
a generated handle otherwise, so storing ``""`` would publish an empty
author. Storing ``None`` makes the branch's ``isinstance(str)`` guard
and its ``.strip()`` guard agree.
"""

from __future__ import annotations

import pytest

from splitsmith.display_name import MAX_DISPLAY_NAME_LEN, normalize_display_name


# The last case is a non-breaking space, written as an escape because
# it is indistinguishable from a plain space in a source file.
@pytest.mark.parametrize("raw", [None, "", "   ", "\t\t", " \u00a0 "])
def test_blank_becomes_none(raw: str | None) -> None:
    assert normalize_display_name(raw) is None


def test_surrounding_whitespace_is_stripped() -> None:
    assert normalize_display_name("  Anders Berg  ") == "Anders Berg"


def test_internal_whitespace_runs_collapse() -> None:
    assert normalize_display_name("Anders    Berg") == "Anders Berg"
    assert normalize_display_name("Anders \t Berg") == "Anders Berg"


def test_unicode_is_nfc_normalized() -> None:
    """Escape sequences, not literal characters: a decomposed and a
    composed name look identical in a source file, so a literal-vs-literal
    assertion would be trivially true and prove nothing."""
    decomposed = "Ma\u030athias"  # "Ma" + COMBINING RING ABOVE + "thias"
    composed = "M\u00e5thias"  # LATIN SMALL LETTER A WITH RING ABOVE
    assert decomposed != composed  # guard: the two inputs really do differ
    assert normalize_display_name(decomposed) == composed


def test_non_ascii_names_are_allowed() -> None:
    assert normalize_display_name("M\u00e5thias Axell") == "M\u00e5thias Axell"


def test_at_the_length_cap_is_accepted() -> None:
    name = "a" * MAX_DISPLAY_NAME_LEN
    assert normalize_display_name(name) == name


def test_one_over_the_length_cap_is_rejected() -> None:
    with pytest.raises(ValueError, match="60"):
        normalize_display_name("a" * (MAX_DISPLAY_NAME_LEN + 1))


def test_length_is_measured_after_normalizing() -> None:
    """Padding must not count against the cap -- it is removed first."""
    name = "  " + "a" * MAX_DISPLAY_NAME_LEN + "  "
    assert normalize_display_name(name) == "a" * MAX_DISPLAY_NAME_LEN


@pytest.mark.parametrize("bad", ["Anders\nBerg", "Anders\rBerg", "Anders\x00Berg", "Anders\x1bBerg"])
def test_control_characters_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="control"):
        normalize_display_name(bad)


def test_c1_control_characters_are_rejected() -> None:
    """U+0085 NEXT LINE. Invisible in a source file, which is exactly why
    it is written as an escape."""
    with pytest.raises(ValueError, match="control"):
        normalize_display_name("Anders\u0085Berg")


def test_zero_width_joiners_are_rejected() -> None:
    """U+200D is category Cf: invisible, and a way to make two
    identical-looking names compare unequal."""
    with pytest.raises(ValueError, match="control"):
        normalize_display_name("Anders\u200dBerg")
