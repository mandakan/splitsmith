"""A frames sting's page draws the renderer's mounted edge frames (issue #1535)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from splitsmith.look import frames as look_frames
from splitsmith.look.sandbox import ORIGIN
from splitsmith.look.template import TemplateContext, engine_block, shared_url
from splitsmith.overlay.raster import ChromiumRasterizer, RasterizerUnavailableError

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def raster():
    try:
        with ChromiumRasterizer() as r:
            yield r
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")


def _context() -> TemplateContext:
    return TemplateContext(
        theme={"ink": "#ffffff"},
        data={"transition": {"kind": "sting:x", "duration_seconds": 1.0}, "shooters": []},
        size={"width": 64, "height": 36},
        fps=30,
        engine=engine_block(css="body{}"),
        assets={"shared": shared_url()},
    )


def test_a_page_draws_a_mounted_frame(raster: ChromiumRasterizer, tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    (frames / "k" / "from").mkdir(parents=True)
    Image.new("RGB", (64, 36), (0, 0, 255)).save(frames / "k" / "from" / "0000.jpg", quality=95)
    page = tmp_path / "look" / "sting.html"
    page.parent.mkdir()
    page.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;background:transparent}"
        "img{display:block;width:64px;height:36px}</style>"
        "</head><body><img id='f'><script>"
        "var u = window.splitsmith.data.transition.frames.from[0];"
        "window.duration = function () { return 0; };"
        "window.seek = function () {"
        " var i = document.getElementById('f'); i.src = u; return i.decode(); };"
        "</script></body></html>",
        encoding="utf-8",
    )
    ctx = look_frames.with_frames(
        _context(),
        fps=30.0,
        count=1,
        from_urls=[f"{ORIGIN}/{look_frames.FRAMES_MOUNT}/k/from/0000.jpg"],
        to_urls=[],
    )
    got = raster.render_template_frames(
        page, context=ctx, width=64, height=36, fps=30.0, max_seconds=1.0, frames_dir=frames
    )
    try:
        pixel = next(iter(got.frames))
    finally:
        got.close()
    offset = 4 * (18 * 64 + 32)
    r, g, b, a = pixel[offset : offset + 4]
    assert a == 255 and b > 200 and r < 40, (r, g, b, a)
