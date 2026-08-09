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

import io
import struct
from dataclasses import dataclass

import numpy as np
import pytest
from PIL import Image

from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError
from splitsmith.overlay_theme import load_theme
from splitsmith.share_card import MatchCard, RosterEntry, StageCard, stage_figures
from splitsmith.share_card_html import CARD_HEIGHT, CARD_WIDTH, match_card_html
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


def _diff_bbox(a: np.ndarray, b: np.ndarray, *, threshold: int = 24) -> tuple[int, int, int, int] | None:
    """Bounding box of pixels that differ by more than ``threshold`` in any
    channel between two same-shape RGB arrays, or ``None`` if they are
    pixel-identical."""
    diff = np.abs(a.astype(int) - b.astype(int)).max(axis=2)
    ys, xs = np.nonzero(diff > threshold)
    if len(xs) == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


@pytest.mark.integration
def test_a_wrapped_name_does_not_clip_a_descender_or_a_diaeresis() -> None:
    """Regression test for the roster-name clipping defect found during a
    manual visual inspection of a rendered card: ``.display``'s
    line-height (0.92) was tighter than Antonio's own glyph extents, and
    ``overflow: hidden`` plus ``-webkit-line-clamp`` clipped whatever
    didn't fit -- a ``Q``'s descender tail and an umlaut's diaeresis both
    vanished, so "Lindqvist" silently rendered as "Lindovist" and
    "Wikstrom" rendered identically whether or not it carried its
    diaeresis.

    Proof, not assertion-by-eye: render the risky glyph and a same-length,
    same-position, safe stand-in, and diff the two PNGs pixel for pixel.
    Every other pixel on the card is identical between the two renders
    (only one glyph in one roster name differs), so any surviving
    difference is exactly that glyph's own ink.

    The threshold was calibrated against a measured mutation: reverting
    the CSS fix (line-height back to 0.92, dropping the padding-bottom)
    and rerunning this exact comparison gave a diff of ``None`` for Q (a
    fully clipped tail makes "Lindqvist" pixel-identical to "Lindovist")
    and a 1px-tall diff for the diaeresis (antialiasing jitter, not a
    real mark). Fixed, the same comparison gives a 5px-tall diff for Q
    and 4px for the diaeresis. ``>= 3`` sits well above the pre-fix noise
    floor and well below the post-fix signal.
    """
    theme = load_theme("splitsmith")

    def render(name: str) -> np.ndarray:
        card = MatchCard(match_name="Match", match_date=None, stage_count=1, roster=[RosterEntry(name=name)])
        html = match_card_html(card, theme=theme)
        try:
            with ChromiumRasterizer() as rasterizer:
                png = rasterizer.png(html, width=CARD_WIDTH, height=CARD_HEIGHT)
        except RasterizerUnavailableError as exc:
            pytest.skip(str(exc))
        return np.array(Image.open(io.BytesIO(png)).convert("RGB"))

    q_bbox = _diff_bbox(render("Lindovist"), render("Lindqvist"))
    assert q_bbox is not None, "Q's descender produced no pixel difference from a plain O -- clipped"
    assert q_bbox[3] - q_bbox[1] >= 3, f"Q/O diff too shallow to be a real descender: {q_bbox}"

    diaeresis_bbox = _diff_bbox(render("Wikstrom"), render("Wikström"))
    assert diaeresis_bbox is not None, "the diaeresis produced no pixel difference -- clipped"
    diaeresis_height = diaeresis_bbox[3] - diaeresis_bbox[1]
    assert diaeresis_height >= 3, f"diaeresis diff too shallow to be real: {diaeresis_bbox}"
