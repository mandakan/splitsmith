# Frames sting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A transition template that declares `<meta name="splitsmith-frames" content="edges">` receives both edges' frames and its clip replaces the boundary's `xfade`, in the single-shooter MP4 and the grid.

**Architecture:** Pure helpers in a new `look/frames.py` (opt-in detection, extraction argv, clamped URL lists, the context with frames). The sandbox gains one renderer-only mount. `look/sting.py` gains a `FramesSting` (decided before the edges, loaded once for its sounds) and `render_frames_sting` (extract, draw, write the alpha MOV), run as the boundary encode's `prepare` in both drivers so a cache hit draws nothing. The boundary command gets a `sting_replaces` mode: black canvas, clip over it, edges' audio crossfaded as today.

**Tech Stack:** Python 3.11, ffmpeg, Playwright Chromium (headless shell), Pillow, pytest (+ xdist), vanilla JS templates.

**Spec:** `docs/superpowers/specs/2026-10-20-frames-sting-design.md`

## Global Constraints

- A sting without the tag, and every non-sting boundary: context, digest and argv byte for byte as on main. Pixels checked with `scripts/render_match_frames.py` / `render_grid_frames.py --transition sting:wipe` against main.
- Never widen the sandbox beyond the one `frames` mount the renderer passes; URL rewriting of `file://` never maps into it.
- No new dependencies. `uv` only, never `pip`. Black line length 110, ruff clean, type hints everywhere, `pathlib.Path`.
- Unit tests mock ffmpeg (a fake runner); integration tests are `@pytest.mark.integration`, build media with `tests/synthetic_media.py`, and must not skip in CI.
- Every ASCII line; What's new copy follows the `whats-new` skill.
- A changed shipped template is recorded with `scripts/record_template_history.py`.
- `segment_cache.KEY_VERSION` 4 -> 5 (Task 6). `export_preview.PREVIEW_REVISION` 9 -> 10 (Task 9).
- Run tests with `uv run pytest <file> -n0` while iterating; the full suite (`uv run pytest`) before each commit that touches a renderer.

## Review Focus

1. **A cache hit with a frames sting.** Expect: no extraction, no browser frames pulled, the boundary's sound cues still on its timeline entry. Pinned in Task 5 (`test_a_cached_frames_sting_extracts_and_draws_nothing`).
2. **An edge shorter than its half (default pads over short trims).** Expect: the URL list repeats the held frame for exactly the pad, same as `tpad`. Pinned in Task 1 (clamping tests) and Task 5 (integration with `head_pad_seconds=0.5`, which leaves no handle).
3. **A stage name that is a `file://` URL into the frames folder.** Expect: stays a string the page cannot load. Pinned in Task 2.
4. **A frames sting whose template throws when `frames` is absent.** Expect: the decision-time load fails -> fade plus a degradation, never a crash. Pinned in Task 4.
5. **An edge from which ffmpeg extracts no frames.** Expect: a cut plus a degradation, as any failed boundary. Pinned in Task 4 (`MotionClipError`) and Task 5 (driver turns it into a cut).

---

### Task 1: The contract primitives (`look/frames.py`)

**Files:**
- Create: `src/splitsmith/look/frames.py`
- Test: `tests/test_look_frames.py`

**Interfaces:**
- Produces:
  - `FRAMES_MOUNT = "frames"`
  - `wants_frames(template: Path) -> bool`
  - `misplaced_frames_tag(template: Path) -> bool` (the tag present with any `content`; used by `looks check` for other slots)
  - `extraction_command(edge: Path, out_dir: Path, *, rate: str, ffmpeg_binary: str) -> tuple[str, ...]`
  - `written_frames(out_dir: Path) -> list[Path]` (sorted `*.jpg`)
  - `edge_frame_urls(written: Sequence[Path], *, count: int, fps: float, pad_seconds: float, end: Literal["tail", "head"], key: str) -> list[str]`
  - `frames_key(base_digest: str, tail_identity: str, head_identity: str) -> str` (16 hex)
  - `with_frames(context: TemplateContext, *, fps: float, count: int, from_urls: list[str], to_urls: list[str]) -> TemplateContext`

- [ ] **Step 1: Write the failing tests**

```python
"""The frames sting's pure contract (issue #1535)."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.look import frames as look_frames
from splitsmith.look.sandbox import ORIGIN
from splitsmith.look.template import TemplateContext


def _template(tmp_path: Path, head: str) -> Path:
    path = tmp_path / "sting.html"
    path.write_text(f"<!doctype html><html><head>{head}</head><body></body></html>", encoding="utf-8")
    return path


def test_the_tag_opts_in_and_only_with_content_edges(tmp_path: Path) -> None:
    assert look_frames.wants_frames(_template(tmp_path, '<meta name="splitsmith-frames" content="edges">'))
    assert look_frames.wants_frames(_template(tmp_path, "<meta content='edges' name='splitsmith-frames'>"))
    assert not look_frames.wants_frames(_template(tmp_path, '<meta name="splitsmith-frames" content="cards">'))
    assert not look_frames.wants_frames(_template(tmp_path, '<meta name="splitsmith-data" content="edges">'))
    assert not look_frames.wants_frames(_template(tmp_path, ""))


def test_a_tag_with_another_content_is_misplaced_not_wanted(tmp_path: Path) -> None:
    other = _template(tmp_path, '<meta name="splitsmith-frames" content="cards">')
    assert look_frames.misplaced_frames_tag(other)
    assert not look_frames.misplaced_frames_tag(_template(tmp_path, ""))


def test_extraction_is_one_ffmpeg_call_at_the_boundary_rate(tmp_path: Path) -> None:
    cmd = look_frames.extraction_command(
        tmp_path / "edge.mp4", tmp_path / "from", rate="30000/1001", ffmpeg_binary="ff"
    )
    assert cmd == (
        "ff", "-hide_banner", "-y", "-loglevel", "error", "-i", str(tmp_path / "edge.mp4"),
        "-vf", "fps=30000/1001", "-q:v", "2", "-start_number", "0", str(tmp_path / "from" / "%04d.jpg"),
    )  # fmt: skip


def _files(tmp_path: Path, n: int) -> list[Path]:
    return [tmp_path / f"{i:04d}.jpg" for i in range(n)]


def test_a_full_edge_maps_frame_for_frame(tmp_path: Path) -> None:
    urls = look_frames.edge_frame_urls(
        _files(tmp_path, 30), count=30, fps=30.0, pad_seconds=0.0, end="tail", key="k"
    )
    assert urls[0] == f"{ORIGIN}/frames/k/from/0000.jpg"
    assert urls[-1] == f"{ORIGIN}/frames/k/from/0029.jpg"


def test_a_tail_pad_repeats_the_last_frame_after_the_footage(tmp_path: Path) -> None:
    # 0.5 s of a 1 s boundary is held: 15 real frames, then the 15th again.
    urls = look_frames.edge_frame_urls(
        _files(tmp_path, 15), count=30, fps=30.0, pad_seconds=0.5, end="tail", key="k"
    )
    assert len(urls) == 30
    assert urls[14] == urls[29] == f"{ORIGIN}/frames/k/from/0014.jpg"
    assert urls[13] == f"{ORIGIN}/frames/k/from/0013.jpg"


def test_a_head_pad_repeats_the_first_frame_before_the_footage(tmp_path: Path) -> None:
    urls = look_frames.edge_frame_urls(
        _files(tmp_path, 15), count=30, fps=30.0, pad_seconds=0.5, end="head", key="k"
    )
    assert urls[:16] == [f"{ORIGIN}/frames/k/to/0000.jpg"] * 16
    assert urls[16] == f"{ORIGIN}/frames/k/to/0001.jpg"
    assert urls[29] == f"{ORIGIN}/frames/k/to/0014.jpg"


def test_an_edge_with_more_frames_than_the_boundary_is_cut_to_count(tmp_path: Path) -> None:
    urls = look_frames.edge_frame_urls(
        _files(tmp_path, 31), count=30, fps=30.0, pad_seconds=0.0, end="tail", key="k"
    )
    assert len(urls) == 30 and urls[-1].endswith("/0029.jpg")


def test_no_frames_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no frames"):
        look_frames.edge_frame_urls([], count=30, fps=30.0, pad_seconds=0.0, end="tail", key="k")


def test_the_key_moves_with_either_edge() -> None:
    base = look_frames.frames_key("d", "tail-a", "head-a")
    assert len(base) == 16
    assert look_frames.frames_key("d", "tail-b", "head-a") != base
    assert look_frames.frames_key("d", "tail-a", "head-b") != base
    assert look_frames.frames_key("e", "tail-a", "head-a") != base


def test_with_frames_adds_the_block_and_nothing_else() -> None:
    ctx = TemplateContext(
        theme={}, data={"transition": {"kind": "sting:push"}, "shooters": []},
        size={"width": 2, "height": 2}, fps=30.0, engine={}, assets={},
    )  # fmt: skip
    out = look_frames.with_frames(ctx, fps=30.0, count=1, from_urls=["a"], to_urls=["b"])
    assert out.data["transition"]["frames"] == {"fps": 30.0, "count": 1, "from": ["a"], "to": ["b"]}
    assert out.data["shooters"] == [] and "frames" not in ctx.data["transition"]
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_look_frames.py -n0 -q`
Expected: FAIL, `ImportError: cannot import name 'frames' from 'splitsmith.look'`.

- [ ] **Step 3: Implement `src/splitsmith/look/frames.py`**

```python
"""The frames sting's contract (issue #1535, spec 2026-10-20).

A ``transition`` slot template that declares
``<meta name="splitsmith-frames" content="edges">`` is handed the pictures
either side of its cut as ``data.transition.frames`` and draws the whole
boundary: its clip replaces the ``xfade``. The renderer extracts each edge
render to JPEGs at the boundary's rate and names them on the template's
virtual origin under the ``frames`` mount, which only the renderer adds
(:func:`splitsmith.look.sandbox.prepare`'s ``frames_dir``).

Where an edge's trim ran out of handle the boundary holds a frame
(``tpad``); the URL lists repeat the held file the same way, so what the
template is told is what the boundary shows.

Pure: argv, names and values; nothing here runs ffmpeg or reads a file
but the template's own bytes.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from .sandbox import ORIGIN
from .template import TemplateContext

FRAMES_MOUNT = "frames"

_META = re.compile(rb"<meta\b[^>]*\bname\s*=\s*[\"']splitsmith-frames[\"'][^>]*>", re.IGNORECASE)
_EDGES = re.compile(rb"\bcontent\s*=\s*[\"']edges[\"']", re.IGNORECASE)

#: What a frames sting is drawn from: JPEG at ffmpeg's ``-q:v 2``, near
#: lossless for a picture that is on screen for a second.
JPEG_QUALITY = "2"


def _tags(template: Path) -> list[bytes]:
    return _META.findall(template.read_bytes())


def wants_frames(template: Path) -> bool:
    """Whether ``template`` declares ``splitsmith-frames`` with ``content="edges"``."""
    return any(_EDGES.search(tag) for tag in _tags(template))


def misplaced_frames_tag(template: Path) -> bool:
    """Whether ``template`` carries a ``splitsmith-frames`` tag at all (for
    ``looks check``: outside the transition slot, or with another content,
    it does nothing)."""
    return bool(_tags(template))


def extraction_command(edge: Path, out_dir: Path, *, rate: str, ffmpeg_binary: str) -> tuple[str, ...]:
    """``edge`` to ``out_dir/0000.jpg, 0001.jpg, ...`` at ``rate``."""
    return (
        ffmpeg_binary, "-hide_banner", "-y", "-loglevel", "error", "-i", str(edge),
        "-vf", f"fps={rate}", "-q:v", JPEG_QUALITY, "-start_number", "0", str(out_dir / "%04d.jpg"),
    )  # fmt: skip


def written_frames(out_dir: Path) -> list[Path]:
    return sorted(out_dir.glob("*.jpg"))


def edge_frame_urls(
    written: Sequence[Path],
    *,
    count: int,
    fps: float,
    pad_seconds: float,
    end: Literal["tail", "head"],
    key: str,
) -> list[str]:
    """``count`` URLs, one per boundary frame, for the files ffmpeg wrote.
    A tail edge shows its footage first and holds its last frame for
    ``pad_seconds``; a head edge holds its first frame for ``pad_seconds``
    and then shows its footage. Raises :class:`ValueError` on no frames."""
    if not written:
        raise ValueError(f"the {end} edge yielded no frames")
    side = "from" if end == "tail" else "to"
    names = [path.name for path in written]
    held = round(pad_seconds * fps)
    indices: list[int] = []
    for n in range(count):
        if end == "tail":
            indices.append(min(n, len(names) - 1))
        else:
            indices.append(min(max(0, n - held), len(names) - 1))
    return [f"{ORIGIN}/{FRAMES_MOUNT}/{key}/{side}/{names[i]}" for i in indices]


def frames_key(base_digest: str, tail_identity: str, head_identity: str) -> str:
    """The folder name the frames live under: the sting's digest and both
    edges' identities (their segment keys, or their paths with no cache)."""
    digest = hashlib.sha256(f"{base_digest}|{tail_identity}|{head_identity}".encode()).hexdigest()
    return digest[:16]


def with_frames(
    context: TemplateContext, *, fps: float, count: int, from_urls: list[str], to_urls: list[str]
) -> TemplateContext:
    """``context`` with ``data.transition.frames`` set."""
    raw = context.model_dump()
    raw["data"]["transition"] = {
        **raw["data"]["transition"],
        "frames": {"fps": fps, "count": count, "from": list(from_urls), "to": list(to_urls)},
    }
    return TemplateContext.model_validate(raw)


__all__ = [
    "FRAMES_MOUNT",
    "JPEG_QUALITY",
    "edge_frame_urls",
    "extraction_command",
    "frames_key",
    "misplaced_frames_tag",
    "wants_frames",
    "with_frames",
    "written_frames",
]
```

Check the head-pad arithmetic against the test: `held = 15`, `n=15 -> max(0, 0) = 0`, `n=16 -> 1`. The test asserts `urls[:16]` are frame 0: n = 0..15 maps to 0. Correct. (`tpad start_duration=0.5` adds 15 cloned frames *before* frame 0, so boundary frame 15 is the real frame 0.)

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run pytest tests/test_look_frames.py -n0 -q`
Expected: 10 passed. Then `uv run ruff check src/splitsmith/look/frames.py tests/test_look_frames.py && uv run black --check -l 110 src/splitsmith/look/frames.py tests/test_look_frames.py`.

- [ ] **Step 5: Mutation drill.** Change `max(0, n - held)` to `n` and confirm the head-pad test fails; change `min(n, len(names) - 1)` to `n % len(names)` and confirm the tail-pad test fails. Restore both.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/look/frames.py tests/test_look_frames.py
git commit -m "feat(looks): the frames sting's contract helpers (#1535)"
```

---

### Task 2: The `frames` mount and the rasterizer's `frames_dir`

**Files:**
- Modify: `src/splitsmith/look/sandbox.py` (`prepare`)
- Modify: `src/splitsmith/overlay/raster.py` (`Rasterizer.render_template_frames` Protocol, `ChromiumRasterizer.render_template_frames`, `_open_template`)
- Test: `tests/test_look_sandbox.py`, `tests/test_look_frames_chromium.py` (new, integration)

**Interfaces:**
- Consumes: `FRAMES_MOUNT` (Task 1)
- Produces:
  - `look_sandbox.prepare(template: Path, context: TemplateContext, *, frames_dir: Path | None = None) -> tuple[Sandbox, TemplateContext]`
  - `Rasterizer.render_template_frames(..., frames_dir: Path | None = None)`: callers only pass it for a frames sting, so existing fakes without the parameter keep working.

- [ ] **Step 1: Failing unit tests in `tests/test_look_sandbox.py`** (append; reuse that file's existing template/context helpers, reading them first to match names):

```python
def test_the_frames_mount_exists_only_when_the_renderer_passes_it(tmp_path: Path) -> None:
    from splitsmith.look.sandbox import ORIGIN, prepare

    template = tmp_path / "look" / "sting.html"
    template.parent.mkdir()
    template.write_text("<!doctype html>", encoding="utf-8")
    frames = tmp_path / "frames"
    (frames / "k" / "from").mkdir(parents=True)
    (frames / "k" / "from" / "0000.jpg").write_bytes(b"\xff\xd8\xff")
    url = f"{ORIGIN}/frames/k/from/0000.jpg"
    without, _ = prepare(template, _ctx())
    assert without.resolve(url) is None
    with_mount, _ = prepare(template, _ctx(), frames_dir=frames)
    assert with_mount.resolve(url) == (frames / "k" / "from" / "0000.jpg").resolve()


def test_user_text_naming_the_frames_folder_is_never_rewritten(tmp_path: Path) -> None:
    from splitsmith.look.sandbox import prepare

    template = tmp_path / "look" / "sting.html"
    template.parent.mkdir()
    template.write_text("<!doctype html>", encoding="utf-8")
    frames = tmp_path / "frames"
    (frames / "k" / "from").mkdir(parents=True)
    jpg = frames / "k" / "from" / "0000.jpg"
    jpg.write_bytes(b"\xff\xd8\xff")
    sneaky = jpg.resolve().as_uri()
    ctx = _ctx(data={"transition": {"to": sneaky}, "shooters": []})
    _, out = prepare(template, ctx, frames_dir=frames)
    assert out.data["transition"]["to"] == sneaky
```

where `_ctx(data=...)` builds a minimal `TemplateContext` (add the helper if the file has none, same shape as `tests/test_look_frames.py`'s).

- [ ] **Step 2: Run, see fail**: `uv run pytest tests/test_look_sandbox.py -n0 -q -k frames`; expected `TypeError: prepare() got an unexpected keyword argument 'frames_dir'`.

- [ ] **Step 3: Implement.** In `sandbox.py`, change the signature and add the mount *after* the rewrite closures captured `mounts`, so `mounted()` never maps a `file://` URL into it:

```python
def prepare(
    template: Path, context: TemplateContext, *, frames_dir: Path | None = None
) -> tuple[Sandbox, TemplateContext]:
    """... (existing text) ... ``frames_dir`` (a frames sting, #1535) is
    mounted as ``/frames/``: the renderer's extracted edge frames, named in
    ``data.transition.frames`` by virtual URL. It is added to the sandbox
    only, never to the folders ``file://`` URLs are rewritten into, so no
    text in the context can name it."""
```

and just before `return sandbox, TemplateContext.model_validate(raw)`:

```python
    if frames_dir is not None:
        sandbox.mounts["frames"] = frames_dir.resolve()
```

(`sandbox.mounts` and the local `mounts` are the same dict object today: `Sandbox(template=..., mounts=mounts)`. Build the sandbox with `mounts=dict(mounts)` so adding to `sandbox.mounts` leaves the rewrite's dict alone; the rewrite runs before the return anyway, but the copy makes the separation explicit.) Update the module docstring's mount list with a `/frames/` bullet.

In `raster.py`: add `frames_dir: Path | None = None` to the Protocol's `render_template_frames` and to `ChromiumRasterizer.render_template_frames`, pass it to `self._open_template(template, context=context, width=width, height=height, frames_dir=frames_dir)`, and in `_open_template` add the parameter and call `look_sandbox.prepare(template, context, frames_dir=frames_dir)`.

- [ ] **Step 4: Chromium test** `tests/test_look_frames_chromium.py` (`pytestmark = pytest.mark.integration`, the `raster` module fixture copied from `tests/test_look_tools_chromium.py`):

```python
def test_a_page_draws_a_mounted_frame(raster, tmp_path: Path) -> None:
    from PIL import Image

    frames = tmp_path / "frames"
    (frames / "k" / "from").mkdir(parents=True)
    Image.new("RGB", (64, 36), (0, 0, 255)).save(frames / "k" / "from" / "0000.jpg", quality=95)
    page = tmp_path / "look" / "sting.html"
    page.parent.mkdir()
    page.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;background:transparent}img{display:block;width:64px;height:36px}</style>"
        "</head><body><img id='f'><script>"
        "var u = window.splitsmith.data.transition.frames.from[0];"
        "window.duration = function () { return 0; };"
        "window.seek = function () { var i = document.getElementById('f'); i.src = u; return i.decode(); };"
        "</script></body></html>",
        encoding="utf-8",
    )
    from splitsmith.look import frames as look_frames

    ctx = look_frames.with_frames(
        _context(), fps=30.0, count=1,
        from_urls=[f"https://look.invalid/frames/k/from/0000.jpg"], to_urls=[],
    )  # fmt: skip
    got = raster.render_template_frames(
        page, context=ctx, width=64, height=36, fps=30.0, max_seconds=1.0, frames_dir=frames
    )
    try:
        pixel = next(iter(got.frames))
    finally:
        got.close()
    r, g, b, a = pixel[4 * (18 * 64 + 32) : 4 * (18 * 64 + 32) + 4]
    assert a == 255 and b > 200 and r < 40, (r, g, b, a)
```

with `_context()` a 64x36 `TemplateContext` whose `data` is `{"transition": {"kind": "sting:x", "duration_seconds": 1.0}, "shooters": []}`.

- [ ] **Step 5: Run**: `uv run pytest tests/test_look_sandbox.py tests/test_look_frames_chromium.py -n0 -q`. Expected: pass (the Chromium test skips only when no browser is installed; CI has one).

- [ ] **Step 6: Mutation drill.** Revert the `frames_dir` pass-through in `_open_template`: the Chromium test must fail (blue pixel missing). Restore.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/look/sandbox.py src/splitsmith/overlay/raster.py tests/test_look_sandbox.py tests/test_look_frames_chromium.py
git commit -m "feat(looks): a renderer-only frames mount on the template sandbox (#1535)"
```

---

### Task 3: `_shared/frames.js`

**Files:**
- Create: `src/splitsmith/data/looks/_shared/frames.js`
- Test: `tests/test_look_frames_chromium.py` (append)

**Interfaces:**
- Produces (JS): `window.splitsmithFrames.at(n) -> Promise<{from: ImageBitmap|null, to: ImageBitmap|null}>`, `window.splitsmithFrames.count` (0 without frames), `window.splitsmithFrames.draw(ctx2d, bitmap, x, y, w, h)` (no-op on `null`). Loaded by a template as `<script src="${assets.shared}/frames.js">`, i.e. `document.write('<script src="' + window.splitsmith.assets.shared + '/frames.js"><\/script>')` like the other shared scripts. Check how `sting-wipe.html` / `card.html` load `_shared/*.js` and copy that form exactly.

Note: any file under `_shared/` joins `shared_digest`, so adding this file invalidates every cached template clip once. That is expected and is why it goes in with the `KEY_VERSION` bump's PR.

- [ ] **Step 1: Failing Chromium tests** (append):

```python
_PAGE = """<!doctype html><html><head><meta charset='utf-8'>
<script>document.write('<script src="' + window.splitsmith.assets.shared + '/frames.js"><\\/script>');</script>
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


def test_frames_js_hands_each_frame_pair_and_draws_it(raster, tmp_path: Path) -> None:
    from PIL import Image

    from splitsmith.look import frames as look_frames

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


def test_frames_js_answers_null_pictures_without_frames(raster, tmp_path: Path) -> None:
    page = tmp_path / "look" / "sting.html"
    page.parent.mkdir()
    page.write_text(_PAGE, encoding="utf-8")
    probe = raster.probe_template(page, context=_context(), width=64, height=36)
    assert probe.errors == ()
```

(`_context()` from Task 2 at 64x36. Two frames at 30 fps over `duration() = 2/30`: the rasterizer samples `ceil(2/30*30) = 2` frames at t=0 and t=2/30, the latter rounded to n=1 by `Math.min(count-1, ...)`.)

- [ ] **Step 2: Run, see fail** (`frames.js` 404s into `blocked`, `splitsmithFrames` undefined): `uv run pytest tests/test_look_frames_chromium.py -n0 -q`.

- [ ] **Step 3: Write `frames.js`**

```js
// The frames sting's helper (#1535): the pictures either side of the cut,
// one pair at a time. window.splitsmith.data.transition.frames names a URL
// per boundary frame for each edge; at(n) loads and decodes frame n's pair
// and closes the previous one, so a template never holds more than two
// decoded frames (all of a 4K boundary would be gigabytes). Without frames
// (the load the renderer makes to read sounds(), or looks check) count is
// 0 and every picture is null.
(function () {
  var data = (window.splitsmith && window.splitsmith.data) || {};
  var frames = (data.transition && data.transition.frames) || null;
  var held = [];

  function load(url) {
    if (!url) return Promise.resolve(null);
    var img = new Image();
    img.src = url;
    return img.decode().then(function () { return createImageBitmap(img); });
  }

  function at(n) {
    if (!frames || !frames.count) return Promise.resolve({ from: null, to: null });
    var i = Math.max(0, Math.min(frames.count - 1, n | 0));
    return Promise.all([load(frames.from[i]), load(frames.to[i])]).then(function (pair) {
      held.forEach(function (bitmap) { if (bitmap) bitmap.close(); });
      held = pair;
      return { from: pair[0], to: pair[1] };
    });
  }

  function draw(g, bitmap, x, y, w, h) {
    if (bitmap) g.drawImage(bitmap, x, y, w, h);
  }

  window.splitsmithFrames = { count: frames ? frames.count : 0, fps: frames ? frames.fps : 0, at: at, draw: draw };
})();
```

- [ ] **Step 4: Run, see pass.** Also run `uv run pytest tests/test_look_template.py -n0 -q` (it inspects `_shared/`).

- [ ] **Step 5: Mutation drill.** Make `at` ignore `n` (always 0): the first test must fail on the last frame. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/data/looks/_shared/frames.js tests/test_look_frames_chromium.py
git commit -m "feat(looks): frames.js hands a sting each frame pair (#1535)"
```

---

### Task 4: `FramesSting` and `render_frames_sting` in `look/sting.py`

**Files:**
- Modify: `src/splitsmith/look/sting.py`
- Test: `tests/test_look_sting.py` (append)

**Interfaces:**
- Consumes: Task 1 helpers; `Rasterizer.render_template_frames(..., frames_dir=)` (Task 2); `write_motion_clip`, `MotionClipError` from `look/motion.py`.
- Produces:

```python
@dataclass
class FramesSting:
    template: Path
    context: TemplateContext      # frames-free
    digest: str                   # template_digest(template, context, fps=, engine_version=)
    sounds: tuple[dict[str, Any], ...]
    seconds: float
    width: int
    height: int
    fps: float

    def close(self) -> None: ...  # no-op; lets drivers treat it like CardMotion in `finally`

def sting_plan(look, kind, *, seconds, from_label, to_label, width, height, fps, rasterizer,
               shooters=(), brand=None) -> CardMotion | FramesSting | None

def render_frames_sting(
    sting: FramesSting, *, tail_edge: Path, head_edge: Path, tail_pad_seconds: float,
    head_pad_seconds: float, rate: str, key: str, frames_root: Path, clip_path: Path,
    rasterizer: Rasterizer, ffmpeg_binary: str, run: Callable[[tuple[str, ...]], None],
) -> None

def frames_sting_filters(input_index: int, *, rate: str, seconds: float, width: int, height: int,
                         out_label: str = "stung") -> tuple[list[str], str]
```

`sting_plan` replaces both drivers' direct `sting_motion` calls; `sting_motion` stays as is (the preview and tests use it). For a tag-less template `sting_plan` returns exactly `sting_motion(...)`.

- [ ] **Step 1: Failing tests** (append to `tests/test_look_sting.py`; it already has `_FakeRasterizer` and `_context`):

```python
from splitsmith.look import frames as look_frames
from splitsmith.look.motion import MotionClipError


class _RecordingRasterizer(_FakeRasterizer):
    def __init__(self) -> None:
        super().__init__(motion_seconds=1.0)
        self.contexts: list = []
        self.frames_dirs: list = []

    def render_template_frames(self, template, *, context, width, height, fps, max_seconds, frames_dir=None):
        self.contexts.append(context)
        self.frames_dirs.append(frames_dir)
        return super().render_template_frames(
            template, context=context, width=width, height=height, fps=fps, max_seconds=max_seconds
        )


def _frames_look(tmp_path: Path, monkeypatch) -> "Look":  # noqa: F821
    """A user Look whose `push` sting declares the tag (built with look_tools.new_look or by
    writing look.json beside a template; copy the construction tests/test_look_tools.py uses
    for a user Look under a monkeypatched user_looks_dir)."""
    ...


def test_a_tagless_sting_plans_as_before(monkeypatch, tmp_path: Path) -> None:
    raster = _RecordingRasterizer()
    plan = look_sting.sting_plan(load_look("splitsmith"), "sting:wipe", rasterizer=raster, **_plan_args())
    assert plan is not None and not isinstance(plan, look_sting.FramesSting)
    assert "frames" not in raster.contexts[0].data["transition"]


def test_a_frames_sting_is_loaded_once_without_frames_for_its_sounds(monkeypatch, tmp_path: Path) -> None:
    look = _frames_look(tmp_path, monkeypatch)
    raster = _RecordingRasterizer()
    plan = look_sting.sting_plan(look, "sting:push", rasterizer=raster, **_plan_args())
    assert isinstance(plan, look_sting.FramesSting)
    assert len(raster.contexts) == 1 and raster.frames_dirs == [None]
    assert "frames" not in plan.context.data["transition"]
    assert plan.digest == template_digest(plan.template, plan.context, fps=30.0, engine_version="fake")


def test_render_frames_sting_extracts_both_edges_then_draws_with_the_mount(monkeypatch, tmp_path: Path) -> None:
    look = _frames_look(tmp_path, monkeypatch)
    raster = _RecordingRasterizer()
    plan = look_sting.sting_plan(look, "sting:push", rasterizer=raster, **_plan_args(width=4, height=2))
    ran: list[tuple[str, ...]] = []

    def run(cmd: tuple[str, ...]) -> None:
        ran.append(cmd)
        out = Path(cmd[-1]).parent
        out.mkdir(parents=True, exist_ok=True)
        for i in range(30):
            (out / f"{i:04d}.jpg").write_bytes(b"\xff\xd8\xff")

    written: list[Path] = []
    monkeypatch.setattr(look_sting, "write_motion_clip", lambda frames, *, out, fps, ffmpeg_binary: written.append(out))
    look_sting.render_frames_sting(
        plan, tail_edge=tmp_path / "t.mp4", head_edge=tmp_path / "h.mp4", tail_pad_seconds=0.0,
        head_pad_seconds=0.0, rate="30", key="k", frames_root=tmp_path / "fr",
        clip_path=tmp_path / "clip.mov", rasterizer=raster, ffmpeg_binary="ff", run=run,
    )  # fmt: skip
    assert [cmd[6] for cmd in ran] == [str(tmp_path / "t.mp4"), str(tmp_path / "h.mp4")]
    assert raster.frames_dirs[-1] == tmp_path / "fr"
    frames = raster.contexts[-1].data["transition"]["frames"]
    assert frames["count"] == 30 and frames["from"][0].endswith("/frames/k/from/0000.jpg")
    assert written == [tmp_path / "clip.mov"]


def test_an_edge_with_no_frames_is_a_motion_clip_error(monkeypatch, tmp_path: Path) -> None:
    look = _frames_look(tmp_path, monkeypatch)
    raster = _RecordingRasterizer()
    plan = look_sting.sting_plan(look, "sting:push", rasterizer=raster, **_plan_args(width=4, height=2))
    with pytest.raises(MotionClipError, match="no frames"):
        look_sting.render_frames_sting(
            plan, tail_edge=tmp_path / "t.mp4", head_edge=tmp_path / "h.mp4", tail_pad_seconds=0.0,
            head_pad_seconds=0.0, rate="30", key="k", frames_root=tmp_path / "fr",
            clip_path=tmp_path / "clip.mov", rasterizer=raster, ffmpeg_binary="ff", run=lambda cmd: None,
        )  # fmt: skip


def test_a_frames_sting_that_fails_to_load_is_none(monkeypatch, tmp_path: Path) -> None:
    look = _frames_look(tmp_path, monkeypatch)

    class _Throws(_RecordingRasterizer):
        def render_template_frames(self, *a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("frames is undefined")

    assert look_sting.sting_plan(look, "sting:push", rasterizer=_Throws(), **_plan_args()) is None


def test_the_replacing_graph_is_a_black_canvas_under_the_clip() -> None:
    parts, out = look_sting.frames_sting_filters(2, rate="30", seconds=1.0, width=640, height=360)
    assert parts[0] == "color=c=black:s=640x360:r=30:d=1,format=rgba[canvas]"
    assert parts[-1].startswith("[canvas][motion]overlay=0:0") and out == "stung"
```

`_plan_args(**over)` returns `{"seconds": 1.0, "from_label": "Stage 1", "to_label": "Stage 2", "width": 64, "height": 36, "fps": 30.0}` updated by `over`. **Write `_frames_look` for real:** read `tests/test_look_tools.py` for how it builds a user Look (it monkeypatches `looks.user_looks_dir`/the provider and writes `look.json` plus a template), then build one named `framesy` whose `transition` slot is `{"push": "push.html"}`, with `push.html` carrying the tag, `window.duration = () => splitsmith.data.transition.duration_seconds` and a `seek` that resolves. Load it with `load_look("framesy")`. No `...` may remain in the committed test.

- [ ] **Step 2: Run, see fail**: `uv run pytest tests/test_look_sting.py -n0 -q`.

- [ ] **Step 3: Implement** in `look/sting.py` (update the module docstring with a paragraph on frames stings):

```python
@dataclass
class FramesSting:
    """A sting that draws the whole boundary from its edges' frames
    (#1535). Decided before the edges exist: loaded once without frames
    for ``sounds()`` (a cached boundary still carries its cues) and keyed
    by ``digest``; drawn by :func:`render_frames_sting` in the boundary
    encode's ``prepare``."""

    template: Path
    context: TemplateContext
    digest: str
    sounds: tuple[dict[str, Any], ...]
    seconds: float
    width: int
    height: int
    fps: float

    def close(self) -> None:
        """Nothing to release: the decision-time load is closed at once."""


def sting_plan(
    look: Look,
    kind: str,
    *,
    seconds: float,
    from_label: str,
    to_label: str,
    width: int,
    height: int,
    fps: float,
    rasterizer: Rasterizer,
    shooters: Sequence[Any] = (),
    brand: dict[str, str | None] | None = None,
) -> CardMotion | FramesSting | None:
    """What a boundary's sting is: an overlay sting as :func:`sting_motion`
    loads it, a :class:`FramesSting` when the template declares
    ``splitsmith-frames``, or ``None`` (the caller falls back to a fade)."""
    template = sting_template_for(look, sting_name(kind))
    if template is None or not wants_frames(template):
        return sting_motion(
            look, kind, seconds=seconds, from_label=from_label, to_label=to_label, width=width,
            height=height, fps=fps, rasterizer=rasterizer, shooters=shooters, brand=brand,
        )  # fmt: skip
    context = sting_context(
        kind=kind, seconds=seconds, from_label=from_label, to_label=to_label, width=width,
        height=height, fps=fps, theme=theme_for(look), shooters=shooters, brand=brand,
    )  # fmt: skip
    try:
        loaded = rasterizer.render_template_frames(
            template, context=context, width=width, height=height, fps=fps, max_seconds=seconds
        )
        try:
            sounds = loaded.sounds
        finally:
            loaded.close()
        digest = template_digest(template, context, fps=fps, engine_version=rasterizer.engine_version())
    except Exception as exc:  # noqa: BLE001 -- one bad template must not lose the render
        logger.warning("could not load the sting %r through %s (%s); the transition is a fade", kind, template, exc)
        return None
    return FramesSting(
        template=template, context=context, digest=digest, sounds=sounds, seconds=seconds,
        width=width, height=height, fps=fps,
    )  # fmt: skip


def render_frames_sting(
    sting: FramesSting,
    *,
    tail_edge: Path,
    head_edge: Path,
    tail_pad_seconds: float,
    head_pad_seconds: float,
    rate: str,
    key: str,
    frames_root: Path,
    clip_path: Path,
    rasterizer: Rasterizer,
    ffmpeg_binary: str,
    run: Callable[[tuple[str, ...]], None],
) -> None:
    """Extract both edges under ``frames_root/key/{from,to}``, draw the
    sting with them and write its alpha clip to ``clip_path``. ``run``
    executes an ffmpeg argv and raises on failure (the driver's own
    runner). Raises :class:`MotionClipError` when an edge yields no frames
    or the template fails; the driver makes that a cut."""
    count = max(1, math.ceil(sting.seconds * sting.fps - 1e-9))
    lists: dict[str, list[str]] = {}
    for end, edge, pad in (("tail", tail_edge, tail_pad_seconds), ("head", head_edge, head_pad_seconds)):
        side = "from" if end == "tail" else "to"
        out_dir = frames_root / key / side
        out_dir.mkdir(parents=True, exist_ok=True)
        run(extraction_command(edge, out_dir, rate=rate, ffmpeg_binary=ffmpeg_binary))
        try:
            lists[side] = edge_frame_urls(
                written_frames(out_dir), count=count, fps=sting.fps, pad_seconds=pad, end=end, key=key  # type: ignore[arg-type]
            )
        except ValueError as exc:
            raise MotionClipError(f"{clip_path.name}: {exc}") from exc
    context = with_frames(sting.context, fps=sting.fps, count=count, from_urls=lists["from"], to_urls=lists["to"])
    try:
        frames = rasterizer.render_template_frames(
            sting.template, context=context, width=sting.width, height=sting.height, fps=sting.fps,
            max_seconds=sting.seconds, frames_dir=frames_root,
        )  # fmt: skip
    except Exception as exc:  # noqa: BLE001 -- the template's failure is the clip's
        raise MotionClipError(f"{clip_path.name}: {exc}") from exc
    write_motion_clip(frames, out=clip_path, fps=sting.fps, ffmpeg_binary=ffmpeg_binary)


def frames_sting_filters(
    input_index: int, *, rate: str, seconds: float, width: int, height: int, out_label: str = "stung"
) -> tuple[list[str], str]:
    """A frames sting's video: its clip over opaque black for the whole
    boundary, in place of the crossfade (what it leaves transparent is
    black, by decision)."""
    canvas = f"color=c=black:s={width}x{height}:r={rate}:d={seconds:g},format=rgba[canvas]"
    parts, label = motion_overlay_filters(
        input_index, rate=rate, seconds=seconds, source_label="canvas", out_label=out_label
    )
    return [canvas, *parts], label
```

Imports to add: `math`, `dataclass`, `Callable`, `Path`, `MotionClipError` and `write_motion_clip` from `.motion` (the test monkeypatches `look_sting.write_motion_clip`, so import the name into this module), and `edge_frame_urls, extraction_command, wants_frames, with_frames, written_frames` from `.frames`. Add the new names to `__all__`.

- [ ] **Step 4: Run, see pass**: `uv run pytest tests/test_look_sting.py tests/test_look_frames.py -n0 -q`.

- [ ] **Step 5: Mutation drill.** Pass `frames_dir=None` in `render_frames_sting`: the mount assertion fails. Drop the `try` around `edge_frame_urls`: the no-frames test fails with `ValueError` instead. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/look/sting.py tests/test_look_sting.py
git commit -m "feat(looks): plan and draw a frames sting from its edges (#1535)"
```

---

### Task 5: The single-shooter boundary

**Files:**
- Modify: `src/splitsmith/mp4_render.py` (`sting_for_boundary` ~line 396, the boundary loop ~890-960, `_build_boundary_command` ~2195)
- Modify: `tests/synthetic_media.py` (add `build_color_video`)
- Test: `tests/test_mp4_render.py` (or whichever unit file pins `_build_boundary_command`: `grep -ln "_build_boundary_command" tests/`), `tests/test_mp4_transitions_integration.py`

**Interfaces:**
- Consumes: `sting_plan`, `FramesSting`, `render_frames_sting`, `frames_sting_filters` (Task 4); `frames_key` (Task 1).
- Produces: `_build_boundary_command(..., sting_clip=None, sting_replaces: bool = False)`; `build_color_video(destination: Path, *, color: str, seconds: float = 4.0) -> Path` in `tests/synthetic_media.py`.

- [ ] **Step 1: Failing argv tests** (unit file):

```python
def test_a_replacing_sting_boundary_draws_the_clip_over_black_and_keeps_the_audio(tmp_path: Path) -> None:
    cmd = mp4_render._build_boundary_command(
        tmp_path / "t.mp4", tmp_path / "h.mp4", kind="fade", seconds=1.0, sequence=_sequence(),
        output_path=tmp_path / "b.mp4", sting_clip=tmp_path / "s.mov", sting_replaces=True,
    )  # fmt: skip
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "xfade" not in graph
    assert "color=c=black:s=1920x1080" in graph
    assert "acrossfade=d=1:c1=tri:c2=tri[aout]" in graph
    assert cmd.count("-i") == 3


def test_an_overlay_sting_and_no_sting_keep_todays_argv(tmp_path: Path) -> None:
    common = dict(kind="fade", seconds=1.0, sequence=_sequence(), output_path=tmp_path / "b.mp4")
    plain = mp4_render._build_boundary_command(tmp_path / "t.mp4", tmp_path / "h.mp4", **common)
    assert plain == mp4_render._build_boundary_command(
        tmp_path / "t.mp4", tmp_path / "h.mp4", sting_replaces=False, **common
    )
    stung = mp4_render._build_boundary_command(
        tmp_path / "t.mp4", tmp_path / "h.mp4", sting_clip=tmp_path / "s.mov", **common
    )
    assert "xfade=transition=fade" in stung[stung.index("-filter_complex") + 1]
```

`_sequence()`: reuse the file's existing `SequenceFormat` helper (1920x1080 at 30000/1001). Also pin the exact pre-change argv: before touching `_build_boundary_command`, run it once for `(no sting)` and `(sting_clip set)` with fixed paths in a Python shell, paste both tuples into a test as literals (`test_boundary_argv_is_byte_identical_to_main`). That literal is the byte-equality proof.

- [ ] **Step 2: Run, see fail** (`unexpected keyword argument 'sting_replaces'`).

- [ ] **Step 3: Implement `sting_replaces`** in `_build_boundary_command`:

```python
    if sting_clip is None:
        parts.append(f"{xfade},format=yuv420p[final]")
    elif sting_replaces:
        # A frames sting (#1535) draws the whole picture: no crossfade.
        sting_parts, stung = frames_sting_filters(
            2, rate=_rate_string(sequence), seconds=seconds, width=sequence.width, height=sequence.height
        )
        parts += [*sting_parts, f"[{stung}]format=yuv420p[final]"]
        inputs += ["-i", str(sting_clip)]
    else:
        ...  # today's branch unchanged
```

The video pads (`tpad` into `[tv]`/`[hv]`) are now unused labels in the replacing graph, and ffmpeg fails on an unconnected output pad. So emit the video `tpad` lines only when `not sting_replaces`, and keep the audio `apad`/`adelay` lines in both modes. Restructure the pad block into separate video and audio lists, and keep the order of today's emitted parts identical when `sting_replaces` is False: the byte-equality literal from Step 1 checks this.

- [ ] **Step 4: Wire the driver.** In `sting_for_boundary`, call `sting_plan(...)` in place of `sting_motion(...)`, and widen its return type to `tuple[CardMotion | FramesSting | None, str]`. In the boundary loop:

```python
            sting: CardMotion | FramesSting | None = None
            ...
                else:
                    sting_clip = work_dir / f"{boundary.name}_sting.mov"
                    boundary_sounds = sting.sounds if isinstance(sting, FramesSting) else sting.frames.sounds
            try:
                ...
                tail_pad = _missing_handle(item, half=half, end="tail")
                head_pad = _missing_handle(nxt, half=half, end="head")
                cmd = _build_boundary_command(
                    ..., tail_pad_seconds=tail_pad, head_pad_seconds=head_pad,
                    sting_clip=sting_clip, sting_replaces=isinstance(sting, FramesSting),
                )
                edge_keys = ... (unchanged)
                prepare_clip: Callable[[], object] | None = None
                if isinstance(sting, FramesSting) and sting_clip is not None:
                    key = frames_key(
                        sting.digest,
                        keys_by_path.get(tail_edge, str(tail_edge)),
                        keys_by_path.get(head_edge, str(head_edge)),
                    )
                    if edge_keys is not None:
                        edge_keys[str(sting_clip)] = key
                    frames_sting = sting
                    clip = sting_clip

                    def prepare_clip() -> None:
                        render_frames_sting(
                            frames_sting, tail_edge=tail_edge, head_edge=head_edge,
                            tail_pad_seconds=tail_pad, head_pad_seconds=head_pad,
                            rate=_rate_string(sequence), key=key, frames_root=work_dir / f"{boundary.name}_frames",
                            clip_path=clip, rasterizer=rasterizer, ffmpeg_binary=ffmpeg_binary,
                            run=lambda argv: _run(argv, runner=boundary_runner),
                        )  # fmt: skip
                elif sting is not None and sting_clip is not None:
                    if edge_keys is not None:
                        edge_keys[str(sting_clip)] = sting.digest
                    prepare_clip = clip_writer(sting, sting_clip)
                boundary_segment = encode(cmd, boundary_out, ..., virtual_inputs=edge_keys, prepare=prepare_clip)
```

Check the runner name: the edge and boundary encodes use whichever runner `encode` uses (it calls `_run(cmd, runner=runner)`; the `boundary_runner` hook named in `rendering.md` belongs to the driver's signature). Use the same runner the boundary's own encode uses, so a test that counts boundary-runner calls sees the extractions. Read `render_mp4`'s parameters to get the exact name. `rasterizer` is non-None whenever `sting` is (`sting_for_boundary` returns `None` without one); add `assert rasterizer is not None` for the type checker. `FFmpegError` from `_run` inside `prepare` already lands in the existing `except (FFmpegError, MotionClipError, _EdgeUnavailableError)`. Put `frames_sting` and `clip` into explicitly bound locals, as shown, so the closure cannot read a later loop iteration's values. The `finally: sting.close()` stays (`FramesSting.close` is a no-op).

- [ ] **Step 5: `build_color_video`** in `tests/synthetic_media.py`, beside `build_synthetic_video` with the same codec arguments:

```python
def build_color_video(destination: Path, *, color: str, seconds: float = 4.0) -> Path:
    """A solid ``color`` clip (an ffmpeg colour name or 0xRRGGBB) with a
    tone, in the canonical synthetic shape: for a test that tells two
    stages apart by colour."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i",
        f"color=c={color}:size={SYNTHETIC_WIDTH}x{SYNTHETIC_HEIGHT}:rate={SYNTHETIC_FPS_NUM}/{SYNTHETIC_FPS_DEN}:duration={seconds:g}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={SYNTHETIC_SAMPLE_RATE}:duration={seconds:g}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-g", str(SYNTHETIC_GOP_FRAMES), "-keyint_min", str(SYNTHETIC_GOP_FRAMES), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(destination),
    ]  # fmt: skip
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return destination
```

- [ ] **Step 6: Failing integration tests** (append to `tests/test_mp4_transitions_integration.py`):

```python
class _PushRasterizer:
    """A frames sting drawn in Python: it reads the frames the renderer
    handed it from the mount and lays the incoming frame's left half beside
    the outgoing frame's right half, so a boundary pixel says which edge it
    came from. Every frame is opaque. Records what it drew."""

    def __init__(self) -> None:
        self.loads = 0
        self.frames_drawn = 0

    def png(self, html, *, width, height):  # type: ignore[no-untyped-def]
        raise AssertionError("not used")

    def render_template(self, template, *, context, width, height):  # type: ignore[no-untyped-def]
        raise AssertionError("not used")

    def engine_version(self) -> str:
        return "push"

    def render_template_frames(self, template, *, context, width, height, fps, max_seconds, frames_dir=None):
        import io
        import math

        from PIL import Image

        from splitsmith.overlay.raster import TemplateFrames

        self.loads += 1
        count = max(1, math.ceil(max_seconds * fps - 1e-9))
        frames = context.data["transition"].get("frames")

        def picture(url: str) -> Image.Image:
            rel = url.split("/frames/", 1)[1]
            with Image.open(frames_dir / rel) as image:
                return image.convert("RGBA").resize((width, height))

        def generate():  # type: ignore[no-untyped-def]
            for n in range(count):
                self.frames_drawn += 1
                out = Image.new("RGBA", (width, height), (0, 0, 0, 255))
                if frames is not None:
                    out.paste(picture(frames["to"][n]).crop((0, 0, width // 2, height)), (0, 0))
                    out.paste(picture(frames["from"][n]).crop((width // 2, 0, width, height)), (width // 2, 0))
                yield out.tobytes()

        return TemplateFrames(duration=max_seconds, frame_count=count, width=width, height=height, frames=generate())


def _colour_stages(tmp_path: Path, *, ffmpeg: str) -> list[StageComposition]:
    stages = []
    for number, colour in ((1, "red"), (2, "blue")):
        trim = build_color_video(tmp_path / f"stage{number}.mp4", color=colour)
        audit = tmp_path / f"stage{number}.json"
        write_audit(audit, (1100, 1320))
        stages.append(StageComposition(
            stage_name=f"Stage {number}", video_path=trim, video=probe_video(trim),
            shots=audit_shots_to_engine_shots(read_audit_data(audit), beep_time_in_source=0.0),
            beep_offset_seconds=1.0, head_pad_seconds=0.5, tail_pad_seconds=1.0,
        ))  # fmt: skip
    return stages


def _mean_rgb_box(path: Path, *, at: float, ffmpeg: str, box: tuple[float, float]) -> tuple[float, float, float]:
    """Mean colour of the frame ``at`` s in, over the horizontal span ``box`` (fractions of the width)."""
    ...  # same as _mean_rgb, then image.crop((int(w*box[0]), 0, int(w*box[1]), h)) before ImageStat


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_frames_sting_replaces_the_crossfade_with_its_edges(tmp_path: Path, monkeypatch) -> None:
    """#1535: mid-boundary the left half is the incoming (blue) stage, the
    right half the outgoing (red) one; a fade would be purple everywhere."""
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    _install_frames_look(tmp_path, monkeypatch)  # a user Look "framesy" with a tagged `push`
    stages = _colour_stages(tmp_path, ffmpeg=ffmpeg)
    comp = composition.from_stage_compositions(
        stages, project_name="m", overlay_theme="framesy",
        transitions=(composition.Transition(from_stage_index=0, to_stage_index=1, kind="sting:push", duration_seconds=1.0),),
    )  # fmt: skip
    raster = _PushRasterizer()
    result = mp4_render.render_mp4(
        comp, output_path=tmp_path / "out.mp4", work_dir=tmp_path / "w", ffmpeg_binary=ffmpeg, rasterizer=raster
    )
    assert result.degradations == ()
    boundary = tmp_path / "w" / "boundary_000.mp4"
    left = _mean_rgb_box(boundary, at=0.5, ffmpeg=ffmpeg, box=(0.05, 0.45))
    right = _mean_rgb_box(boundary, at=0.5, ffmpeg=ffmpeg, box=(0.55, 0.95))
    assert left[2] > 180 and left[0] < 60, left
    assert right[0] > 180 and right[2] < 60, right


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_cached_frames_sting_extracts_and_draws_nothing(tmp_path: Path, monkeypatch) -> None:
    from splitsmith.segment_cache import SegmentCache

    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    _install_frames_look(tmp_path, monkeypatch)
    stages = _colour_stages(tmp_path, ffmpeg=ffmpeg)
    cache = SegmentCache(tmp_path / "cache")  # match the constructor the cache tests use
    sting = composition.Transition(from_stage_index=0, to_stage_index=1, kind="sting:push", duration_seconds=1.0)

    def render(name: str) -> tuple[_PushRasterizer, mp4_render.Mp4RenderResult]:
        raster = _PushRasterizer()
        comp = composition.from_stage_compositions(stages, project_name="m", overlay_theme="framesy", transitions=(sting,))
        result = mp4_render.render_mp4(
            comp, output_path=tmp_path / f"{name}.mp4", work_dir=tmp_path / name,
            ffmpeg_binary=ffmpeg, rasterizer=raster, segment_cache=cache,
        )  # fmt: skip
        return raster, result

    first, _ = render("a")
    assert first.frames_drawn == 30
    second, result = render("b")
    assert second.frames_drawn == 0 and second.loads == 1  # the decision-time load only
    assert not (tmp_path / "b" / "boundary_000_frames").exists()
    assert any(entry.kind == "transition" for entry in result.timeline)
```

Fill `_mean_rgb_box` in from `_mean_rgb` (crop before `ImageStat`) and `_install_frames_look` from Task 4's `_frames_look`. Move that helper into `tests/look_fixtures.py` (new) so the unit and integration files share it. The push template it writes must declare `window.sounds = () => [{play: "x.m4a", at: 0.5}]` and include `sounds/x.m4a` (copy any shipped CC0 file) so the cached run can assert the cue: replace the last line with `assert [entry.cues for entry in result.timeline if entry.kind == "transition"] != [()]`. Confirm what `composition.from_stage_compositions` takes for the Look (`overlay_theme=` here) by reading its signature, and how the existing cache tests build a `SegmentCache`. Both files are in the repo.

- [ ] **Step 7: Run**: `uv run pytest tests/test_mp4_transitions_integration.py tests/<unit file> -n0 -q`. Expected: pass. Then the existing sting tests: `uv run pytest tests/test_look_sting.py tests/test_mp4_transitions_integration.py -n0 -q -k sting`.

- [ ] **Step 8: Mutation drill.** (a) Set `sting_replaces=False` at the call site: the colour test must fail (both halves purple). (b) Drop `edge_keys[str(sting_clip)] = key`: no test fails on that alone, because the boundary key already includes both edge keys. That is expected, and it is why the frames digest joins anyway (a template change must miss). So add `test_a_changed_frames_template_redraws` to the cache test: render, append a comment to the template, render again, assert `frames_drawn == 30`, and confirm that test fails with the line dropped. Restore.

- [ ] **Step 9: Full suite and commit**

```bash
uv run pytest -q
git add src/splitsmith/mp4_render.py tests/synthetic_media.py tests/look_fixtures.py tests/test_mp4_transitions_integration.py tests/<unit file> tests/test_look_sting.py
git commit -m "feat(export): a frames sting draws the whole boundary in the MP4 (#1535)"
```

---

### Task 6: The grid boundary, and caching the grid's stings

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py` (`_GridEncoder.encode` ~3034, `sting_for_boundary` ~3563, the boundary loop ~3956-4010, `build_boundary_segment_command` ~1665)
- Modify: `src/splitsmith/segment_cache.py` (`KEY_VERSION = 5`, with a comment line)
- Test: the grid unit file pinning `build_boundary_segment_command` (`grep -ln build_boundary_segment_command tests/`), `tests/test_compare_mp4_grid_transitions_integration.py`

**Interfaces:**
- Consumes: Tasks 1, 4; `build_color_video` and `tests/look_fixtures.py` (Task 5).
- Produces: `_GridEncoder.encode(..., prepare: Callable[[], object] | None = None)`; `build_boundary_segment_command(..., sting_replaces: bool = False)`.

- [ ] **Step 1: Failing tests.**
  - Argv: the same three cases as Task 5 Step 1 against `build_boundary_segment_command`, with `shooter_labels=("A", "B")` and `canvas=mp4_grid.GridCanvas(640, 360, 30000, 1001)`. The replacing graph has no `xfade`, has `color=c=black:s=640x360`, and still has all three `acrossfade` tracks. Include the byte-equality literal taken from main.
  - Grid overlay-sting caching (new behaviour): render the existing red-sting grid test twice with one `SegmentCache`. Assert the second run's `_RedStingRasterizer.frames_rendered == 0`. On main this fails, because the grid writes the clip eagerly.
  - Frames sting on the grid: as in Task 5, with each shooter's stage 1 red and stage 2 blue (`build_color_video`), `rasterizer=_PushRasterizer()` (import it from the single-shooter integration module or move it into `tests/look_fixtures.py`), `overlay_theme="framesy"` (read `render_grid_mp4`'s signature for the Look parameter; the grid calls it `overlay_theme` and loads `card_look` from it), and `boundary-000.mov`: blue left, red right at 0.5 s, and four streams (`video, audio, audio, audio`).

- [ ] **Step 2: Run, see fail.**

- [ ] **Step 3: `_GridEncoder.encode(prepare=)`**, matching single-shooter's semantics: run `prepare()` before the command when there is no cache, or on a cache miss; never on a hit.

```python
        if self.cache is None:
            if prepare is not None:
                prepare()
            ...
        hit = self.cache.lookup(key, suffix=out.suffix)
        if hit is not None:
            ...
            return hit, ""
        if prepare is not None:
            prepare()
```

`prepare` may raise `MotionClipError` or `_EdgeFailedError`; the boundary loop's `except (_EdgeFailedError, MotionClipError)` already catches both. An extraction failure goes through `_run_ffmpeg`, which returns a `CompletedProcess`, so the `run` passed to `render_frames_sting` must raise: `def run(argv): done = _run_ffmpeg(argv, runner=boundary_runner); if done.returncode != 0: raise _EdgeFailedError(_stderr_text(done))`.

- [ ] **Step 4: `sting_replaces`** in `build_boundary_segment_command`. This is the same split as Task 5: the video `tpad` lines are emitted only without `sting_replaces`, the per-track `apad`/`adelay` lines always, and the replacing branch is `frames_sting_filters(2, rate=rate, seconds=seconds, width=canvas.width, height=canvas.height)`. Order stays identical when False (the literal test checks it).

- [ ] **Step 5: The boundary loop.** `sting_for_boundary` calls `sting_plan`. Replace the eager `write_motion_clip(...)` with a `prepare` passed to `encoder.encode`. For an overlay sting, `prepare = lambda: write_motion_clip(sting.frames, out=sting_clip, fps=canvas.fps, ffmpeg_binary=binary)` (bound to locals) and `edge_keys[str(sting_clip)] = sting.digest`. For a `FramesSting`, use `render_frames_sting` with `frames_root=work / f"{boundary.name}_frames"`, `rate=canvas.rate_string`, and `key=frames_key(sting.digest, encoder.keys_by_path.get(tail_edge, str(tail_edge)), encoder.keys_by_path.get(head_edge, str(head_edge)))`, with the key going into `edge_keys`. Bump `KEY_VERSION` to 5 with a comment line: `# 5: the grid keys a sting boundary by the sting's digest (#1535)`. The grid's `transition` timeline entries: if the grid writes sting cues anywhere (`grep -n cues src/splitsmith/compare/mp4_grid.py`), take them from `FramesSting.sounds` as Task 5 does. If it writes none today, leave that alone.

- [ ] **Step 6: Run**: `uv run pytest tests/test_compare_mp4_grid_transitions_integration.py tests/<grid unit file> -n0 -q`, then `uv run pytest -q -k "segment_cache or grid"`. If a test pins `KEY_VERSION`, update it.

- [ ] **Step 7: Mutation drill.** Restore the eager write: the overlay-sting cache test must fail. Set `sting_replaces=False`: the colour test must fail. Restore.

- [ ] **Step 8: Full suite and commit**

```bash
uv run pytest -q
git add src/splitsmith/compare/mp4_grid.py src/splitsmith/segment_cache.py tests/
git commit -m "feat(compare): frames stings on the grid, and grid stings are cached (#1535)"
```

---

### Task 7: `looks check` for frames stings

**Files:**
- Modify: `src/splitsmith/look/tools.py` (`Prober` protocol ~183, `check_folder` ~890-960, a new `_check_frames_sting`, `_context`)
- Test: `tests/test_look_tools.py` (fake prober), `tests/test_look_tools_chromium.py` (real)

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: `Prober.render_template_frames(...)` (the same signature as `Rasterizer`'s, `frames_dir` included); `sample_edges(root: Path, *, width: int, height: int, count: int) -> tuple[list[Path], list[Path]]` (the moving bar as `from`, the still gradient as `to`, written as JPEGs under `root/k/{from,to}`); `TRANSPARENT_SHARE = 0.005`.

- [ ] **Step 1: Failing tests.**
  - Fake prober (in `test_look_tools.py`): a Look with a tagged sting whose fake `render_template_frames` yields frames that are fully transparent in the left half. `check_look` reports a `warn` item on that sting whose text contains "transparent" and the share (`50 %`). The same Look with fully opaque frames has no such item.
  - Fake prober: a tag on a `title_page` template produces `warn` "splitsmith-frames does nothing outside the transition slot".
  - Chromium (`test_look_tools_chromium.py`): `check_look("broadcast")` and `check_look("splitsmith")` have no error or transparency items for their `push` stings (this test is written here and goes green in Task 8).

- [ ] **Step 2: Run, see fail.**

- [ ] **Step 3: Implement.**

```python
#: A frames sting draws the whole picture; one that leaves more than this
#: share of a frame's pixels below full alpha shows black there in the
#: export. A little allows antialiased edges against the frame's border.
TRANSPARENT_SHARE = 0.005


def sample_edges(root: Path, *, width: int, height: int, count: int) -> tuple[list[Path], list[Path]]:
    """Two sample edges for a frames sting: ``from`` a light bar moving
    across mid grey (a moving clip), ``to`` a still diagonal gradient."""
    froms, tos = root / "k" / "from", root / "k" / "to"
    froms.mkdir(parents=True)
    tos.mkdir(parents=True)
    gradient = Image.linear_gradient("L").resize((width, height)).convert("RGB")
    out_from, out_to = [], []
    for n in range(count):
        frame = Image.new("RGB", (width, height), (110, 110, 110))
        x = int((width - width // 8) * n / max(1, count - 1))
        frame.paste((235, 235, 235), (x, 0, x + width // 8, height))
        out_from.append(froms / f"{n:04d}.jpg")
        frame.save(out_from[-1], quality=90)
        out_to.append(tos / f"{n:04d}.jpg")
        gradient.save(out_to[-1], quality=90)
    return out_from, out_to


def _check_frames_sting(subject: str, template: Path, context: TemplateContext, prober: Prober, work: Path) -> list[CheckItem]:
    count = max(1, math.ceil(context.data["transition"]["duration_seconds"] * CHECK_FPS - 1e-9))
    froms, tos = sample_edges(work / "frames", width=CHECK_WIDTH, height=CHECK_HEIGHT, count=count)
    url = lambda side, p: f"{ORIGIN}/frames/k/{side}/{p.name}"  # noqa: E731
    ctx = with_frames(
        context, fps=CHECK_FPS, count=count,
        from_urls=[url("from", p) for p in froms], to_urls=[url("to", p) for p in tos],
    )  # fmt: skip
    try:
        frames = prober.render_template_frames(
            template, context=ctx, width=CHECK_WIDTH, height=CHECK_HEIGHT, fps=CHECK_FPS,
            max_seconds=context.data["transition"]["duration_seconds"], frames_dir=work / "frames",
        )  # fmt: skip
        try:
            worst = 0.0
            for raw in frames.frames:
                alpha = raw[3::4]
                worst = max(worst, sum(1 for a in alpha if a < 255) / len(alpha))
        finally:
            frames.close()
    except Exception as exc:  # noqa: BLE001 -- a finding, not a crash
        return [CheckItem(subject, "error", f"failed drawing with sample edges: {exc}")]
    if worst > TRANSPARENT_SHARE:
        return [CheckItem(
            subject, "warn",
            f"leaves {worst:.0%} of a frame transparent; a frames sting draws the whole picture, "
            "and what it leaves shows black in the export",
        )]  # fmt: skip
    return []
```

`sum(1 for a in alpha ...)` over a 1280x720 frame is slow in pure Python, so count with `alpha.count(255)` after `bytes(alpha)`: `opaque = bytes(raw[3::4]).count(255)`, `share = 1 - opaque / (len(raw) // 4)`. Read `CHECK_WIDTH`/`CHECK_HEIGHT`/`CHECK_FPS` from the module. In `check_folder`, find where each `(STING_SLOT, variant, own)` target is judged and add `items.extend(_check_frames_sting(subject, own, _context(look, STING_SLOT, variant, normal_text, shooters), prober, Path(tmp)))` when `wants_frames(own)` is true. Then, for every own template in a slot other than `STING_SLOT` (or in `STING_SLOT` with the tag but `content` not `edges`), add the misplaced-tag `warn` when `misplaced_frames_tag(own) and not (slot == STING_SLOT and wants_frames(own))`. Add `render_template_frames` to `Prober`.

- [ ] **Step 4: Run**: `uv run pytest tests/test_look_tools.py -n0 -q`.

- [ ] **Step 5: Mutation drill.** Set `TRANSPARENT_SHARE = 1.0`: the half-transparent test must fail. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/look/tools.py tests/test_look_tools.py tests/test_look_tools_chromium.py
git commit -m "feat(looks): looks check draws a frames sting over sample edges (#1535)"
```

---

### Task 8: The shipped pushes, the starter, history and previews

**Files:**
- Create: `src/splitsmith/data/looks/splitsmith/sting-push.html`
- Modify: `src/splitsmith/data/looks/splitsmith/look.json` (`transition`: add `"push": "sting-push.html"`)
- Modify: `src/splitsmith/data/looks/broadcast/broadcast-sting.html`
- Create: `src/splitsmith/data/looks/_starters/sting-frames.html`; register it in `look/tools.py` (`EDITOR_STARTERS`, the starter map at ~line 75-87: `"sting-frames": {STING_SLOT: {"sting": "sting-frames.html"}}`, following the `"sting"` entry's shape)
- Modify: `src/splitsmith/data/looks/_history.json` (via `scripts/record_template_history.py`)
- Regenerate: Look previews and gallery clips (`scripts/render_look_thumbnails.py --look-previews`)
- Test: `tests/test_look_frames_chromium.py` (append), `tests/test_look_tools_chromium.py` (Task 7's test goes green)

- [ ] **Step 1: Failing Chromium pixel test** for each push. Write sample edges with `look_tools.sample_edges`-style solid frames (`from` all red, `to` all blue, 30 frames, 640x360). Build the context with `sting_context(kind="sting:push", seconds=1.0, ...)` plus `with_frames`. Render through `raster.render_template_frames(..., frames_dir=...)` and check:
  - frame 0: mostly red (the outgoing picture is on screen at the start);
  - the last frame: mostly blue;
  - frame 15: both colours present, with a vertical seam somewhere in the middle 80 %;
  - every frame: alpha is 255 everywhere.

  For Broadcast, frame 15 is the panel (its `surface` colour) with the stage name, so assert only the first and last frames and full alpha.

- [ ] **Step 2: Run, see fail** (the splitsmith Look has no `push`; Broadcast's push leaves the frame transparent).

- [ ] **Step 3: Write `sting-push.html`**

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="splitsmith-frames" content="edges">
<title>sting push</title>
<!--
  A frames sting (#1535): the outgoing stage moves off to the left as the
  incoming one moves in from the right, eased, with a thin accent seam.
  It draws the whole picture from data.transition.frames through
  _shared/frames.js; duration() and sounds() never read the frames.
  Guide: docs/looks/authoring.md#frames-stings
-->
<script>
  document.write('<script src="' + window.splitsmith.assets.shared + '/frames.js"><\/script>');
</script>
<style>
  html, body { margin: 0; padding: 0; background: #000; overflow: hidden; }
  canvas { display: block; }
</style>
</head>
<body>
<canvas id="c"></canvas>
<script>
  var S = window.splitsmith, TR = S.data.transition || {};
  var LENGTH = TR.duration_seconds || 1, W = S.size.width, H = S.size.height;
  var accent = (S.data.shooters && S.data.shooters[0] && S.data.shooters[0].accent) || S.theme.accent;
  var c = document.getElementById('c'); c.width = W; c.height = H;
  var g = c.getContext('2d');
  function ease(p) { return p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2; }
  window.duration = function () { return LENGTH; };
  window.poster = function () { return LENGTH / 2; };
  window.seek = function (t) {
    var F = window.splitsmithFrames;
    var n = Math.round(t * (F.fps || S.fps));
    return F.at(n).then(function (p) {
      var x = Math.round(W * ease(Math.max(0, Math.min(1, t / LENGTH))));
      g.fillStyle = '#000'; g.fillRect(0, 0, W, H);
      F.draw(g, p.from, -x, 0, W, H);
      F.draw(g, p.to, W - x, 0, W, H);
      if (x > 0 && x < W) { g.fillStyle = accent; g.fillRect(W - x - Math.round(H * 0.004), 0, Math.round(H * 0.008), H); }
    });
  };
</script>
</body>
</html>
```

First check how the shipped templates load `_shared/` scripts (`grep -n "shared" src/splitsmith/data/looks/splitsmith/sting-wipe.html src/splitsmith/data/looks/splitsmith/card.html`) and use the same form.

- [ ] **Step 4: Rebuild Broadcast's push.** Keep everything it draws today (panel, accent edge, "Next", the stage name, the mark, `sounds()` unchanged), and add the tag, the `frames.js` script and a full-frame `<canvas>` behind the panel (`z-index` below `#panel`). In `seek(t)`, after `paint(t)`, draw the footage: while the panel comes in (p < 0.4), the outgoing frame pushed left by the panel's leading edge (`-(x + 100)` vw mapped to pixels); while it holds, nothing shows behind it; on the way out (p > 0.6), the incoming frame pushed in behind the trailing edge. Set `html, body` to `background: #000` and return the `at()` Promise from `seek`. Update the header comment: the panel no longer rides a crossfade, it pushes the stages.

- [ ] **Step 5: Starter `sting-frames.html`**: the push above with comments that walk an author through the contract (the tag, `frames.js`, `at(n)`, drawing to the canvas, "`duration()` and `sounds()` never read the frames", "leave nothing transparent").

- [ ] **Step 6: Record history and regenerate previews**

```bash
uv run python scripts/record_template_history.py
uv run python scripts/render_look_thumbnails.py --look-previews
git status --short   # expect _history.json, the two Looks' preview files, any gallery clips for push
```

If the thumbnail script needs an argument or a flag for stings, read its `--help`. Open every regenerated image and look at it: the frames for the gallery clip of a frames sting are drawn over sample edges. If the script hands a sting no frames, the push previews will be black. In that case extend the script to pass `look_tools.sample_edges` frames, and add that file to this task.

- [ ] **Step 7: Run**: `uv run pytest tests/test_look_frames_chromium.py tests/test_look_tools_chromium.py tests/test_look_tools.py tests/test_broadcast_sounds.py -n0 -q`.

- [ ] **Step 8: Mutation drill.** Delete the `<meta>` tag from `sting-push.html`: the pixel test must fail (the sting gets no frames and draws black). Restore.

- [ ] **Step 9: Commit**

```bash
git add src/splitsmith/data/looks src/splitsmith/look/tools.py tests/ scripts/
git commit -m "feat(looks): a push that moves the footage, in splitsmith and Broadcast (#1535)"
```

---

### Task 9: Previews: the rail and `looks preview`

**Files:**
- Modify: `src/splitsmith/export_preview.py` (the `spec.card == "sting"` branch ~540, `_sting_motion` ~900, `PREVIEW_REVISION = 10`)
- Modify: `src/splitsmith/look/tools.py` (`preview_look`'s sting loop ~1103-1130)
- Test: the export preview tests (`grep -ln "card=\"sting\"\|'sting'" tests/test_export_preview*.py`), `tests/test_look_tools.py`

**Interfaces:**
- Consumes: Tasks 1-4, `sample_edges` (Task 7), `grab_frame` (existing).

- [ ] **Step 1: Failing tests.**
  - Preview: with a fake rasterizer (the preview tests already have one) and a frames-tagged sting, the context handed to `render_template_frames` carries `frames` with `count = ceil(STING_PREVIEW_SECONDS * MOTION_FPS)`, `from` URLs all naming the same file and `to` URLs all another, and `frames_dir` set. `grab_frame` is called once more with `at="tail"` (monkeypatch it and record its calls).
  - `looks preview`: the frames sting is drawn with frames from `sample_edges` (assert `frames_dir` is set on the call).

- [ ] **Step 2: Run, see fail.**

- [ ] **Step 3: Implement the rail.** In the sting branch, when `wants_frames(template)`:
  1. Make a tail still. If `trim is not None and ffmpeg_binary`, call `grab_frame(trim, seconds=beep + last_shot + spec.tail_pad_seconds, at="tail", ...)`. Otherwise use `frame` itself, or the demo backdrop when there is none.
  2. Save both stills as JPEG under `work_dir / "sting-frames" / "k" / {"from", "to"} / "0000.jpg"`, resized to the spec size.
  3. Build `with_frames(context, fps=MOTION_FPS or 30.0, count=..., from_urls=[url_from] * count, to_urls=[url_to] * count)`.
  4. Pass `frames_dir=work_dir / "sting-frames"` to `render_template_frames` (the motion path) and to `render_template` (the still path). `render_template` has no `frames_dir` yet. The cleanest fix is to give `Rasterizer.render_template` and `ChromiumRasterizer.render_template` the same optional `frames_dir` parameter, passed through to `_open_template`, and to `_AtTime` as well. Do that, with a test.
  5. In `_sting_motion`, for a frames sting compose over black, not over `frame`. Pass `base=None` and build a black RGBA base.

  Bump `PREVIEW_REVISION` to 10. This is a hard rule from `export-ui.md`: what a preview draws changed, so the revision moves.

- [ ] **Step 4: Implement `preview_look`.** For a frames sting, write `sample_edges` under the temp dir and render with `with_frames` and `frames_dir`.

- [ ] **Step 5: Run** the preview and look-tools tests (`-n0`), then render one real preview through the demo-match harness (CLAUDE.md, "Verifying a screen locally") and look at the WebP: the push must show the stage's tail frame sliding off and its head frame coming in.

- [ ] **Step 6: Mutation drill.** Drop `frames_dir` from the preview call: the preview test must fail. Restore.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/export_preview.py src/splitsmith/look/tools.py src/splitsmith/overlay/raster.py tests/
git commit -m "feat(export): previews draw a frames sting over the stage's frames (#1535)"
```

---

### Task 10: Docs, rules, What's new, the visual check

**Files:**
- Modify: `docs/looks/authoring.md` (a `## Frames stings` section under stings, anchor `frames-stings`)
- Modify: `.claude/rules/rendering.md` (the Stings bullet), `.claude/rules/looks.md` (the sandbox hard rule's mount list; the Templates section)
- Modify: `src/splitsmith/data/whats_new.json` (via the `whats-new` skill)
- Test: `tests/test_whats_new.py`, `uv run python scripts/check_claude_md.py`

- [ ] **Step 1: `authoring.md`**: the tag; the `data.transition.frames` shape; held frames repeat; `frames.js` (`at`, `draw`, `count`, `fps`); "`duration()` and `sounds()` never read the frames; a load without frames must not throw"; output is opaque, and transparent shows black; the edges are as encoded (HUD and lower third burnt in); `looks check`'s sample edges and the transparency warning; the `sting-frames` starter; and a cost note: a frames sting re-draws only when its template, `_shared/` or either edge changes.

- [ ] **Step 2: Rules.** In `rendering.md`'s Stings bullet, add: a template with `splitsmith-frames` is a `FramesSting` (`sting_plan`). It is loaded once without frames for its cues, then drawn in the boundary's `prepare` (`render_frames_sting`) from both edges extracted to JPEG (the URL lists repeat the held frame like `tpad`), keyed by `frames_key` (digest plus both edge keys), and its clip replaces the `xfade` over black (`sting_replaces`, `frames_sting_filters`). A tag-less sting's argv is unchanged. Add the grid's `prepare` and the digest key. In `looks.md`'s sandbox rule, add `/frames/` to the mount list: renderer-only (`prepare(frames_dir=)`), never a target of `file://` rewriting. Name the test that pins it: `test_user_text_naming_the_frames_folder_is_never_rewritten`. Then run `uv run python scripts/check_claude_md.py`.

- [ ] **Step 3: What's new.** Invoke the `whats-new` skill and add one entry. Suggested copy, to be held to the skill's rules: title "A push that moves the footage"; body "The new Push transition slides one stage off as the next slides in. Broadcast's Push now moves the footage too, behind its panel." Run `uv run pytest tests/test_whats_new.py -n0 -q`.

- [ ] **Step 4: Visual check against main.**

```bash
git stash -u   # only if the tree is dirty; otherwise skip
git worktree add ~/.claude-tmp/main-frames origin/main
# from each tree, render the same frames:
uv run python scripts/render_match_frames.py --transition sting:wipe --out ~/.claude-tmp/frames-<tree>/wipe
uv run python scripts/render_grid_frames.py  --transition sting:wipe --out ~/.claude-tmp/frames-<tree>/grid-wipe
# branch only:
uv run python scripts/render_match_frames.py --transition sting:push --out ~/.claude-tmp/frames-branch/push
uv run python scripts/render_grid_frames.py  --transition sting:push --out ~/.claude-tmp/frames-branch/grid-push
```

Read each script's `--help` for its real flags (output directory, Look). Compare the `wipe` PNGs main vs branch byte for byte (`cmp`). They must be identical. Remember the editable-install trap (memory: "Verify the 'before'"): run main's renders with `uv run --project ~/.claude-tmp/main-frames` from inside that worktree, so main's code renders main's frames. Confirm with a `python -c "import splitsmith; print(splitsmith.__file__)"` that prints the worktree path. Publish the push frames, branch and main wipe side by side, as an Artifact (gaspode is headless).

- [ ] **Step 5: Full suite, lint, commit**

```bash
uv run pytest -q
uv run ruff check src tests && uv run black --check -l 110 src tests
git add docs .claude/rules src/splitsmith/data/whats_new.json
git commit -m "docs(looks): frames stings in the guide, the rules and What's new (#1535)"
```

- [ ] **Step 6: Whole-branch review** (CLAUDE.md "Review practice"). Name the claims to verify: (1) tag-less sting argv is byte-identical (the literals came from main); (2) a cache hit draws nothing in both renderers; (3) no `data` text can reach the frames mount; (4) the grid's `KEY_VERSION` bump is the only cache-wide change; (5) every new test fails against main. Tell the reviewer the implementation reports are unverified.
