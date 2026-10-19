import pytest

from splitsmith.looks import HIGHLIGHT_SLOT, highlight_template_for, load_look
from splitsmith.overlay.theme import theme_for
from splitsmith.reel.cards import reel_card_context
from splitsmith.reel.plan import ReelCard


def test_shipped_look_has_the_trailer_highlight():
    path = highlight_template_for(load_look("splitsmith"))
    assert path is not None and path.name == "highlight-trailer.html"


def test_unknown_variant_is_none_not_a_fallback():
    assert highlight_template_for(load_look("splitsmith"), "kart") is None


def test_context_carries_the_card_as_data():
    card = ReelCard(
        "004_callout", "callout", 1.2, "FASTEST SPLIT", figure="0.19", unit="SEC", lines=("Stage 3",)
    )
    ctx = reel_card_context(card, width=1080, height=1920, fps=30, theme=theme_for(load_look("splitsmith")))
    assert ctx.data["highlight"] == {
        "kind": "callout",
        "text": "FASTEST SPLIT",
        "figure": "0.19",
        "unit": "SEC",
        "lines": ["Stage 3"],
        "duration_seconds": 1.2,
    }
    assert HIGHLIGHT_SLOT == "highlight"


@pytest.mark.integration
def test_trailer_card_renders_frames_in_chromium():
    from splitsmith.overlay.raster import ChromiumRasterizer, RasterizerUnavailableError
    from splitsmith.reel.cards import reel_card_motion

    owner = ChromiumRasterizer()
    try:
        raster = owner.__enter__()
    except RasterizerUnavailableError:
        pytest.skip("no Chromium")
    try:
        for kind in ("intro", "title", "slam", "callout", "end"):
            card = ReelCard("x", kind, 1.0, "FASTEST DRAW", figure="0.94", unit="SEC", lines=("Stage 1",))
            motion = reel_card_motion(
                load_look("splitsmith"), card, width=540, height=960, fps=30, rasterizer=raster
            )
            assert motion is not None and motion.animated
            frames = list(motion.frames.frames)
            assert len(frames) == 30
            motion.close()
    finally:
        owner.__exit__(None, None, None)
