"""A frames sting's page draws the renderer's mounted edge frames (issue #1535)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from splitsmith.look import frames as look_frames
from splitsmith.look.sandbox import ORIGIN
from splitsmith.look.template import TemplateContext, engine_block, shared_url
from splitsmith.overlay.raster import ChromiumRasterizer, RasterizerUnavailableError, TemplateScriptError

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


_PAGE = """<!doctype html><html><head><meta charset='utf-8'>
<script>document.write('<script src="' + window.splitsmith.assets.shared
  + '/frames.js"><\\/script>');</script>
<style>html,body{margin:0;background:transparent}</style></head><body>
<canvas id='c' width='64' height='36'></canvas><script>
var g = document.getElementById('c').getContext('2d');
window.duration = function () { return 2 / 30; };
window.seek = function (t) {
  var n = Math.min(window.splitsmithFrames.count - 1, Math.round(t * 30));
  return window.splitsmithFrames.at(n).then(function (p) {
    g.clearRect(0, 0, 64, 36);
    window.splitsmithFrames.draw(g, p.from, 0, 0, 32, 36);
    window.splitsmithFrames.draw(g, p.to, 32, 0, 32, 36);
  });
};
</script></body></html>"""


def test_frames_js_hands_each_frame_pair_and_draws_it(raster: ChromiumRasterizer, tmp_path: Path) -> None:
    root = tmp_path / "frames"
    for side, colours in (("from", [(255, 0, 0), (0, 255, 0)]), ("to", [(0, 0, 255), (255, 255, 0)])):
        (root / "k" / side).mkdir(parents=True)
        for i, rgb in enumerate(colours):
            Image.new("RGB", (64, 36), rgb).save(root / "k" / side / f"{i:04d}.jpg", quality=95)
    page = tmp_path / "look" / "sting.html"
    page.parent.mkdir()
    page.write_text(_PAGE, encoding="utf-8")
    url = "https://look.invalid/frames/k/{}/{:04d}.jpg"
    ctx = look_frames.with_frames(
        _context(), fps=30.0, count=2,
        from_urls=[url.format("from", 0), url.format("from", 1)],
        to_urls=[url.format("to", 0), url.format("to", 1)],
    )  # fmt: skip
    got = raster.render_template_frames(
        page, context=ctx, width=64, height=36, fps=30.0, max_seconds=1.0, frames_dir=root
    )
    try:
        shots = list(got.frames)
    finally:
        got.close()

    def px(buf: bytes, x: int) -> tuple[int, ...]:
        i = 4 * (18 * 64 + x)
        return tuple(buf[i : i + 4])

    assert px(shots[0], 8)[0] > 200 and px(shots[0], 56)[2] > 200  # red | blue
    assert px(shots[-1], 8)[1] > 200 and px(shots[-1], 56)[0] > 200  # green | yellow


_NULL_PAGE = """<!doctype html><html><head><meta charset='utf-8'>
<script>document.write('<script src="' + window.splitsmith.assets.shared
  + '/frames.js"><\\/script>');</script>
<style>html,body{margin:0;background:transparent}</style></head><body>
<canvas id='c' width='64' height='36'></canvas><script>
var g = document.getElementById('c').getContext('2d');
window.duration = function () { return 0; };
window.seek = function (t) {
  return window.splitsmithFrames.at(0).then(function (p) {
    var ok = window.splitsmithFrames.count === 0 && p.from === null && p.to === null;
    window.splitsmithFrames.draw(g, p.from, 0, 0, 64, 36);
    g.fillStyle = ok ? '#00ff00' : '#ff0000';
    g.fillRect(0, 0, 64, 36);
  });
};
</script></body></html>"""


def test_frames_js_answers_null_pictures_without_frames(raster: ChromiumRasterizer, tmp_path: Path) -> None:
    page = tmp_path / "look" / "sting.html"
    page.parent.mkdir()
    page.write_text(_NULL_PAGE, encoding="utf-8")
    got = raster.render_template_frames(
        page, context=_context(), width=64, height=36, fps=30.0, max_seconds=1.0
    )
    try:
        shot = next(iter(got.frames))
    finally:
        got.close()
    i = 4 * (18 * 64 + 32)
    r, g, b, a = shot[i : i + 4]
    assert a == 255 and g > 200 and r < 40 and b < 40, (r, g, b, a)


def test_frames_js_rejects_when_a_frame_is_missing(raster: ChromiumRasterizer, tmp_path: Path) -> None:
    root = tmp_path / "frames"
    (root / "k" / "from").mkdir(parents=True)
    (root / "k" / "to").mkdir(parents=True)
    for i in range(2):
        Image.new("RGB", (64, 36), (255, 0, 0)).save(root / "k" / "to" / f"{i:04d}.jpg", quality=95)
    Image.new("RGB", (64, 36), (255, 0, 0)).save(root / "k" / "from" / "0000.jpg", quality=95)
    page = tmp_path / "look" / "sting.html"
    page.parent.mkdir()
    page.write_text(_PAGE, encoding="utf-8")
    url = "https://look.invalid/frames/k/{}/{:04d}.jpg"
    ctx = look_frames.with_frames(
        _context(), fps=30.0, count=2,
        from_urls=[url.format("from", 0), url.format("from", 1)],
        to_urls=[url.format("to", 0), url.format("to", 1)],
    )  # fmt: skip
    with pytest.raises(TemplateScriptError, match="cannot be decoded"):
        got = raster.render_template_frames(
            page, context=ctx, width=64, height=36, fps=30.0, max_seconds=1.0, frames_dir=root
        )
        try:
            list(got.frames)
        finally:
            got.close()
