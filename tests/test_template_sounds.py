"""A template's own sounds: ``window.sounds()`` declares cues on the
template's clock, worked out from its data, and the render carries them to
the timeline sidecar the audio dress pass reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError, template_sounds

from .test_overlay_raster import _context_fixture

_TALLY = """<!doctype html><body><script>
  var rows = 3;
  window.duration = function () { return rows * 0.4; };
  window.seek = function (t) {};
  window.sounds = function () {
    var out = [];
    for (var i = 0; i < rows; i++) out.push({play: 'tick.wav', at: i * 0.4});
    out.push({play: 'boom.wav', at: rows * 0.4, gain: 0.9});
    return out;
  };
</script></body>"""


def _frames(tmp_path: Path, html: str):  # type: ignore[no-untyped-def]
    template = tmp_path / "card.html"
    template.write_text(html, encoding="utf-8")
    try:
        with ChromiumRasterizer() as rasterizer:
            out = rasterizer.render_template_frames(
                template, context=_context_fixture(), width=64, height=32, fps=10, max_seconds=5.0
            )
            out.close()
            return out
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))


@pytest.mark.integration
def test_a_template_declares_its_sounds_from_its_own_data(tmp_path: Path) -> None:
    out = _frames(tmp_path, _TALLY)
    assert out.sounds == (
        {"play": "tick.wav", "at": 0},
        {"play": "tick.wav", "at": 0.4},
        {"play": "tick.wav", "at": 0.8},
        {"play": "boom.wav", "at": 1.2000000000000002, "gain": 0.9},
    )


@pytest.mark.integration
def test_a_template_without_sounds_or_with_a_broken_one_declares_none(tmp_path: Path) -> None:
    assert _frames(tmp_path, "<!doctype html><body></body>").sounds == ()
    broken = "<!doctype html><body><script>window.sounds = function () { throw new Error('no'); };</script>"
    assert _frames(tmp_path, broken).sounds == (), "a sound never costs the card"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ()),
        ("tick.wav", ()),
        (
            [{"play": "a.wav"}, "junk", 3, {"bed": "b.ogg", "at": 1}],
            ({"play": "a.wav"}, {"bed": "b.ogg", "at": 1}),
        ),
        ([{"play": "a.wav"}] * 600, ({"play": "a.wav"},) * 256),
    ],
)
def test_what_a_template_answers_is_kept_only_as_a_list_of_cues(value: object, expected: tuple) -> None:
    assert template_sounds(value) == expected
