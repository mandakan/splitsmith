"""Card HTML is pure string building -- no browser in these tests."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from splitsmith.overlay_theme import load_theme
from splitsmith.share_card import (
    MatchCard,
    RosterEntry,
    StageCard,
    stage_figures,
)
from splitsmith.share_card_html import (
    CARD_HEIGHT,
    CARD_WIDTH,
    match_card_html,
    stage_card_html,
)


@dataclass(frozen=True)
class _Shot:
    """The two attributes ``coach.SplitStatInterval`` reads."""

    split: float
    interval_class: str | None


@pytest.fixture
def theme():
    return load_theme("splitsmith")


def _stage_card(**overrides) -> StageCard:
    figures = overrides.pop(
        "figures",
        stage_figures(
            (
                _Shot(split=1.28, interval_class="first_shot"),
                _Shot(split=0.19, interval_class="split"),
                _Shot(split=0.17, interval_class="split"),
            ),
        ),
    )
    base = {
        "stage_number": 3,
        "stage_name": "Per told me to do it!",
        "shooter_name": "Mathias Axell",
        "match_name": "Tallmilan 2026",
        "shot_count": 3,
        "stage_time": 14.74,
        "figures": figures,
    }
    base.update(overrides)
    return StageCard(**base)


def test_card_dimensions_are_declared(theme) -> None:
    assert (CARD_WIDTH, CARD_HEIGHT) == (1200, 630)
    html = stage_card_html(_stage_card(), theme=theme)
    assert "1200px" in html
    assert "630px" in html


def test_stage_card_shows_draw_and_average_split(theme) -> None:
    """The fractional part is wrapped in a dimmed span, so assert on the
    parts rather than a contiguous "1.28"."""
    html = stage_card_html(_stage_card(), theme=theme)
    assert ">1<span" in html and ".28</span>" in html
    assert ">0<span" in html and ".180</span>" in html  # (0.19 + 0.17) / 2
    assert "Draw" in html
    assert "Avg split" in html


def test_stage_card_omits_the_average_when_there_are_no_splits(theme) -> None:
    figures = stage_figures(
        (
            _Shot(split=1.28, interval_class="first_shot"),
            _Shot(split=2.40, interval_class="reload"),
        ),
    )
    html = stage_card_html(_stage_card(figures=figures), theme=theme)
    assert "Draw" in html
    assert "Avg split" not in html
    # Never a placeholder: no zero, no dash standing in for a real figure.
    assert ".000</span>" not in html


def test_names_are_escaped(theme) -> None:
    """A competitor's name is untrusted input, same rule overlay_html holds."""
    html = stage_card_html(_stage_card(shooter_name='Ann "quote" <b>Berg</b>'), theme=theme)
    assert "<b>Berg</b>" not in html
    assert "&lt;b&gt;Berg&lt;/b&gt;" in html


def test_match_card_lists_every_roster_entry(theme) -> None:
    card = MatchCard(
        match_name="Tallmilan 2026",
        match_date="2026-04-26",
        stage_count=7,
        roster=[
            RosterEntry(name="Petra Lind", division="Standard"),
            RosterEntry(name="Anders Berg", division="Production Optics"),
        ],
    )
    html = match_card_html(card, theme=theme)
    assert "Anders Berg" in html
    assert "Petra Lind" in html
    assert "Tallmilan 2026" in html
    # No invented aggregate: the match card carries no summed stage time.
    assert "Total time" not in html


def test_match_card_survives_an_empty_roster(theme) -> None:
    card = MatchCard(match_name="Tallmilan 2026", match_date=None, stage_count=7, roster=[])
    html = match_card_html(card, theme=theme)
    assert "Tallmilan 2026" in html


def _rule_body(html: str, selector: str) -> str:
    """The declaration block for one CSS selector, read back out of the
    document's own ``<style>`` tag rather than assumed from the
    stylesheet source -- so a test using this actually inspects what a
    browser would receive."""
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\}", html, re.DOTALL)
    assert match is not None, f"no {selector!r} rule found in the stylesheet"
    return match.group(1)


def test_every_text_box_hides_overflow(theme) -> None:
    """The categorical fix overlay_html.py exists for: nothing a
    descendant does can paint outside its own box.

    Checks the specific selectors that must carry ``overflow: hidden``
    rather than a raw count across the whole stylesheet -- the
    stylesheet emits a fixed number of ``overflow: hidden`` occurrences
    regardless of content, so a bare count would stay unchanged (and the
    test would stay green) if the rule were dropped from one selector
    while a coincidentally-equal number stayed elsewhere.
    """
    html = stage_card_html(_stage_card(), theme=theme)
    for selector in (
        ".card",
        ".top",
        ".brand",
        ".kick",
        ".body",
        ".display",
        ".figs",
        ".fig",
        ".col",
        ".roster",
        ".rrow",
    ):
        assert "overflow: hidden" in _rule_body(html, selector), selector


def test_bundled_font_faces_are_declared(theme) -> None:
    html = stage_card_html(_stage_card(), theme=theme)
    assert "@font-face" in html
    assert "Antonio" in html
    assert "JetBrains Mono" in html
    # An unquoted file:// @font-face src breaks silently on any path
    # containing a space -- Chromium falls back to a host font with no
    # error, no warning, exactly the failure mode overlay_html.py's own
    # docstring exists to warn about. Both bundled faces must be quoted.
    assert html.count('url("') == 2


def test_a_field_containing_the_literal_mark_placeholder_renders_as_text(theme) -> None:
    """The old implementation built the brand mark by interpolating a
    ``{MARK}`` sentinel into the body and replacing it in a second pass
    over already-escaped user text. ``html.escape`` does not touch ``{``
    or ``}``, so a match/roster name containing the literal substring
    ``{MARK}`` collided with the sentinel and had its own text silently
    swapped for the brand SVG. The mark is now built inline before any
    user text joins the string, so this must render as plain (escaped)
    text and the card must still carry exactly one brand mark."""
    card = MatchCard(
        match_name="{MARK} Cup",
        match_date=None,
        stage_count=1,
        roster=[RosterEntry(name="{MARK}", division="Open")],
    )
    html = match_card_html(card, theme=theme)
    assert "{MARK} Cup" in html
    assert "{MARK}</div>" in html
    assert html.count("<svg") == 1
