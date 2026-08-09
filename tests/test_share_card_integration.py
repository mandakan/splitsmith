"""Real Chromium, real PNG. Everything else in the card suite injects a
fake Rasterizer; this is the one test that proves the HTML actually
rasterizes at the declared size.

Marked ``@pytest.mark.integration`` and mirrors ``test_overlay_raster.py``'s
own integration tests: ``RasterizerUnavailableError`` is caught and turned
into a ``pytest.skip`` so a browser-less dev host degrades gracefully, while
CI's ``SPLITSMITH_REQUIRE_INTEGRATION=1`` escalates that same skip to a
failure (see ``tests/conftest.py``'s integration-suite skip gate) -- these
tests need no media, so that gate has nothing to legitimately skip over.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import pytest

from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError
from splitsmith.overlay_theme import load_theme
from splitsmith.share_card import MatchCard, RosterEntry, StageCard, stage_figures
from splitsmith.share_card_render import render_card


@dataclass(frozen=True)
class _Shot:
    split: float
    interval_class: str | None


@pytest.mark.integration
def test_stage_card_rasterizes_to_a_1200x630_png() -> None:
    card = StageCard(
        stage_number=3,
        stage_name="Per told me to do it!",
        shooter_name="Mathias Axell",
        match_name="Tallmilan 2026",
        shot_count=14,
        stage_time=14.74,
        figures=stage_figures(
            (
                _Shot(split=1.28, interval_class="first_shot"),
                _Shot(split=0.19, interval_class="split"),
                _Shot(split=1.85, interval_class="transition"),
            )
        ),
    )
    try:
        with ChromiumRasterizer() as rasterizer:
            png = render_card(card, theme=load_theme("splitsmith"), rasterizer=rasterizer)
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (1200, 630)


@pytest.mark.integration
def test_a_long_stage_name_does_not_change_the_canvas_size() -> None:
    """The box model clamps; nothing in Python measures text."""
    card = MatchCard(
        match_name="Unload, and then show clear -- an unreasonably long match name",
        match_date="2026-04-26",
        stage_count=7,
        roster=[RosterEntry(name=f"Competitor Number {i}", division="Production Optics") for i in range(8)],
    )
    try:
        with ChromiumRasterizer() as rasterizer:
            png = render_card(card, theme=load_theme("splitsmith"), rasterizer=rasterizer)
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))
    assert struct.unpack(">II", png[16:24]) == (1200, 630)
