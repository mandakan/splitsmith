"""A frames sting's page draws the renderer's mounted edge frames (issue #1535)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from splitsmith import looks
from splitsmith.look import frames as look_frames
from splitsmith.look import sting as look_sting
from splitsmith.look.sandbox import ORIGIN
from splitsmith.look.sting import sting_context
from splitsmith.look.template import TemplateContext, engine_block, shared_url
from splitsmith.overlay.raster import ChromiumRasterizer, RasterizerUnavailableError, TemplateScriptError
from splitsmith.overlay.theme import theme_for

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


# --- the shipped pushes ------------------------------------------------------------


_PUSH_W, _PUSH_H, _PUSH_FPS, _PUSH_COUNT = 640, 360, 30.0, 30


def _draw_sting(
    raster: ChromiumRasterizer,
    look: looks.Look,
    variant: str,
    root: Path,
    *,
    width: int,
    height: int,
    fps: float,
    count: int,
) -> list[bytes]:
    """The Look's ``variant`` sting drawn over the edges written under
    ``root/k/{from,to}/%04d.jpg``, ``count`` frames at ``fps`` (one second
    when ``count == fps``). It is planned as the renderer plans it
    (``sting_plan``: the tag decides, and the load without frames must
    succeed); only a :class:`FramesSting` is handed the edges, as only it is
    in an export."""
    template = looks.sting_template_for(look, variant)
    assert template is not None
    seconds = count / fps
    kind = f"sting:{variant}"
    plan = look_sting.sting_plan(
        look, kind, seconds=seconds, from_label="Stage 3", to_label="Stage 4",
        width=width, height=height, fps=fps, rasterizer=raster,
    )  # fmt: skip
    assert plan is not None, "the load without frames failed"
    if isinstance(plan, look_sting.FramesSting):
        url = f"{ORIGIN}/{look_frames.FRAMES_MOUNT}/k/{{}}/{{:04d}}.jpg"
        ctx = look_frames.with_frames(
            plan.context, fps=fps, count=count,
            from_urls=[url.format("from", n) for n in range(count)],
            to_urls=[url.format("to", n) for n in range(count)],
        )  # fmt: skip
    else:  # an overlay sting: drawn as it is, with no pictures
        plan.close()
        ctx = sting_context(
            kind=kind, seconds=seconds, from_label="Stage 3", to_label="Stage 4",
            width=width, height=height, fps=fps, theme=theme_for(look),
        )  # fmt: skip
    got = raster.render_template_frames(
        template, context=ctx, width=width, height=height, fps=fps, max_seconds=seconds, frames_dir=root
    )
    try:
        return list(got.frames)
    finally:
        got.close()


def _push_frames(raster: ChromiumRasterizer, look_name: str, tmp_path: Path) -> list[bytes]:
    """The Look's ``push`` sting drawn over a solid red outgoing edge and a
    solid blue incoming one, one second at 30 fps, 640x360."""
    look = looks.load_look(look_name)
    template = looks.sting_template_for(look, "push")
    assert template is not None and template.parent.name == look_name
    root = tmp_path / "frames"
    for side, rgb in (("from", (255, 0, 0)), ("to", (0, 0, 255))):
        (root / "k" / side).mkdir(parents=True)
        for n in range(_PUSH_COUNT):
            Image.new("RGB", (_PUSH_W, _PUSH_H), rgb).save(root / "k" / side / f"{n:04d}.jpg", quality=95)
    return _draw_sting(
        raster, look, "push", root, width=_PUSH_W, height=_PUSH_H, fps=_PUSH_FPS, count=_PUSH_COUNT
    )


def _share(buf: bytes, test) -> float:  # type: ignore[no-untyped-def]
    """The share of pixels of an RGBA frame for which ``test(r, g, b)`` holds
    (every 7th pixel: enough for a share)."""
    hits = total = 0
    for i in range(0, len(buf), 4 * 7):
        total += 1
        hits += bool(test(buf[i], buf[i + 1], buf[i + 2]))
    return hits / total


def _red(r: int, g: int, b: int) -> bool:
    return r > 200 and g < 60 and b < 60


def _blue(r: int, g: int, b: int) -> bool:
    return b > 200 and r < 60 and g < 60


def _column(buf: bytes, x: int) -> tuple[int, int, int]:
    i = 4 * ((_PUSH_H // 2) * _PUSH_W + x)
    return buf[i], buf[i + 1], buf[i + 2]


def _assert_opaque(frames: list[bytes]) -> None:
    for n, buf in enumerate(frames):
        assert bytes(buf[3::4]).count(255) == len(buf) // 4, f"frame {n} is not opaque everywhere"


@pytest.mark.parametrize("look_name", ["splitsmith", "broadcast"])
def test_a_shipped_push_starts_on_the_outgoing_and_ends_on_the_incoming(
    raster: ChromiumRasterizer, look_name: str, tmp_path: Path
) -> None:
    """The push moves the footage: the outgoing picture fills the first
    frame, the incoming one the last, and nothing is ever transparent (a
    frames sting's clip replaces the boundary's crossfade)."""
    frames = _push_frames(raster, look_name, tmp_path)
    assert len(frames) == _PUSH_COUNT
    assert _share(frames[0], _red) > 0.95
    assert _share(frames[-1], _blue) > 0.95
    _assert_opaque(frames)


def test_the_splitsmith_push_shows_both_stages_either_side_of_a_seam(
    raster: ChromiumRasterizer, tmp_path: Path
) -> None:
    """Halfway through, the outgoing stage is on the left and the incoming
    one on the right, the seam somewhere in the middle 80 %."""
    mid = _push_frames(raster, "splitsmith", tmp_path)[15]
    assert _share(mid, _red) > 0.2 and _share(mid, _blue) > 0.2
    lo, hi = _PUSH_W // 10, _PUSH_W - _PUSH_W // 10
    assert _red(*_column(mid, lo)) and _blue(*_column(mid, hi))
    seam = next(x for x in range(lo, hi) if _blue(*_column(mid, x)))
    assert lo < seam < hi
    assert all(_blue(*_column(mid, x)) for x in range(seam, hi))


def test_the_broadcast_panel_pushes_the_stages(raster: ChromiumRasterizer, tmp_path: Path) -> None:
    """A fifth of the way in, the panel covers the left half and the
    outgoing stage has moved right ahead of it, not off to the left with
    black behind; a fifth from the end the incoming stage follows the panel
    out on the left."""
    frames = _push_frames(raster, "broadcast", tmp_path)
    lo, hi = _PUSH_W // 10, _PUSH_W - _PUSH_W // 10
    going = frames[6]  # p = 0.2: the panel halfway in
    assert not _red(*_column(going, lo)) and _red(*_column(going, hi))
    assert 0.4 < _share(going, _red) < 0.6
    coming = frames[24]  # p = 0.8: the panel halfway out
    assert _blue(*_column(coming, lo)) and not _blue(*_column(coming, hi))
    assert 0.4 < _share(coming, _blue) < 0.6


#: Edges big enough that decoding one takes longer than a page frame: a
#: seek that does not return its at() Promise is captured before it draws.
_LAG_EDGE_W, _LAG_EDGE_H = 3000, 1687
_LAG_W, _LAG_H, _LAG_COUNT = 320, 180, 10


def _ease(p: float) -> float:
    return 4 * p**3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def _outgoing_column(case: str, n: int) -> int | None:
    """A column at which frame ``n`` shows the outgoing picture, or None
    where it shows none (the templates' own motion, at one second over
    :data:`_LAG_COUNT` frames)."""
    p = n / _LAG_COUNT
    if case == "broadcast":
        if p >= 0.4:  # the hold and the way out: no outgoing picture
            return None
        left = round((_ease(p / 0.4) * 100) * _LAG_W / 100)  # the panel's leading edge
        return _LAG_W - 3 if left < _LAG_W - 12 else None
    x = round(_LAG_W * _ease(p))
    return 2 if _LAG_W - x > 12 else None


@pytest.mark.parametrize("case", ["splitsmith", "broadcast", "starter"])
def test_a_frames_sting_draws_frame_n_on_frame_n(
    raster: ChromiumRasterizer, case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``seek(t)`` returns the ``at()`` Promise, so a frame is captured once
    it is drawn: frame n shows outgoing picture n, not the one before it or
    the poster's. Large edges make the decode slower than a page frame,
    which is when a seek that drops the Promise falls behind."""
    from splitsmith.look import tools as look_tools

    if case == "starter":
        monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path / "home"))
        look_tools.new_look("mine", starter="sting-frames")
        look, variant = looks.load_look("mine"), "sting"
    else:
        look, variant = looks.load_look(case), "push"
    root = tmp_path / "frames"
    (root / "k" / "from").mkdir(parents=True)
    (root / "k" / "to").mkdir(parents=True)
    for n in range(_LAG_COUNT):
        grey = 20 + 20 * n
        Image.new("RGB", (_LAG_EDGE_W, _LAG_EDGE_H), (grey, grey, grey)).save(
            root / "k" / "from" / f"{n:04d}.jpg", quality=95
        )
        Image.new("RGB", (_LAG_EDGE_W, _LAG_EDGE_H), (0, 0, 255)).save(
            root / "k" / "to" / f"{n:04d}.jpg", quality=95
        )
    frames = _draw_sting(
        raster, look, variant, root, width=_LAG_W, height=_LAG_H, fps=float(_LAG_COUNT), count=_LAG_COUNT
    )
    checked = []
    for n, buf in enumerate(frames):
        column = _outgoing_column("broadcast" if case == "broadcast" else "push", n)
        if column is None:
            continue
        i = 4 * ((_LAG_H // 2) * _LAG_W + column)
        got = tuple(buf[i : i + 3])
        grey = 20 + 20 * n
        assert all(abs(c - grey) <= 8 for c in got), (n, got, grey)
        checked.append(n)
    assert len(checked) >= 3, checked
