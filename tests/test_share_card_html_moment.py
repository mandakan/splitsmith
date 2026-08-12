"""Moment badge + CompareCard HTML (spec 2026-08-12)."""

from splitsmith.overlay_theme import load_theme
from splitsmith.share_card import CompareCard, StageCard, StageFigures, card_hash
from splitsmith.share_card_html import compare_card_html, stage_card_html

THEME = load_theme("splitsmith")

FIGURES = StageFigures(draw=1.1, avg_split=0.25, split_count=4, interval_count=6, source="coach")


def _stage_card(moment_t: float | None) -> StageCard:
    return StageCard(
        stage_number=3,
        stage_name="Standards",
        shooter_name="Alice",
        match_name="Test Match",
        shot_count=6,
        stage_time=12.34,
        figures=FIGURES,
        moment_t=moment_t,
    )


def test_stage_card_without_moment_renders_no_badge() -> None:
    assert "MOMENT" not in stage_card_html(_stage_card(None), theme=THEME)


def test_stage_card_with_moment_renders_badge() -> None:
    assert "MOMENT 4.32s" in stage_card_html(_stage_card(4.32), theme=THEME)


def test_negative_moment_renders_signed() -> None:
    assert "MOMENT -1.50s" in stage_card_html(_stage_card(-1.5), theme=THEME)


def test_moment_moves_the_card_hash() -> None:
    assert card_hash(_stage_card(None)) != card_hash(_stage_card(4.32))


def test_compare_card_lists_shooters_and_escapes() -> None:
    card = CompareCard(
        stage_number=3,
        stage_name="Standards <b>",
        match_name="Test Match",
        shooter_names=["Alice", "Bob & Carol"],
        moment_t=None,
    )
    html = compare_card_html(card, theme=THEME)
    assert "Standards &lt;b&gt;" in html
    assert "Bob &amp; Carol" in html
    assert "MOMENT" not in html


def test_compare_card_with_moment_renders_badge() -> None:
    card = CompareCard(
        stage_number=3,
        stage_name="Standards",
        match_name="Test Match",
        shooter_names=["Alice"],
        moment_t=2.5,
    )
    assert "MOMENT 2.50s" in compare_card_html(card, theme=THEME)
