# Single-Shooter Overlay Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `splitsmith export overlay`'s transparent-MOV renderer off its per-frame PIL template and onto the shared declaration/CSS/Chromium overlay engine the compare grid already uses, with the running clock as an ffmpeg `drawtext` filter.

**Architecture:** `build_frame_states` stays and is run-length encoded into ~31 event-shaped runs per stage. Each run is declared as `Group`/`Element` objects, composed into a canvas-sized HTML document by a new `overlay_html.single_html`, rasterized once by Chromium, and its RGBA buffer piped to ffmpeg once per frame the run spans. The clock cannot be a sprite (it changes every frame), so it becomes three `enable`-gated `drawtext` filters built from helpers extracted out of `compare/mp4_grid.py`.

**Tech Stack:** Python 3.11+, Pillow, Playwright (`chromium-headless-shell`), ffmpeg (`prores_ks` / `hevc_videotoolbox`), pytest + pytest-xdist, typer, uv.

**Spec:** `docs/superpowers/specs/2026-08-09-single-shooter-overlay-port-design.md`. Read it before Task 1. Sections 4 (what moves on screen), 6 (error handling) and 7.1 (the 31 pinned tests) are the acceptance criteria.

## Global Constraints

- Python 3.11+. Type hints on every function, including tests.
- `uv` for everything. Never invoke `pip`. Run tests as `uv run pytest ...`.
- **No new dependencies.** Everything this plan needs is already in `pyproject.toml`.
- `pathlib.Path` for paths, never strings. f-strings for formatting.
- Black, line length 110. Ruff for linting. Run `uv run black --check .` and `uv run ruff check .` before every commit.
- Imports grouped stdlib / third-party / local, separated by blank lines. No relative imports beyond a single dot (`.module`, never `..module`) *except* inside `src/splitsmith/compare/`, which already uses `..` to reach the parent package -- follow whatever the file you are editing already does.
- Frozen dataclasses for the overlay pipeline's value types. This subsystem does not use Pydantic; do not introduce it here.
- The test suite runs under xdist (`-n auto --dist load`) by default. Use `-n0` when running a single test. New tests must not depend on execution order or share mutable state outside `tmp_path`.
- Detection logic stays out of the CLI. `cli.py` orchestrates only.
- **Never edit a test to make it pass.** If one of the 31 tests in `tests/test_overlay_render.py` fails in a way this plan did not predict, stop and report it.
- The bundled typeface is JetBrains Mono Bold for every theme. A theme decides colour, never the face.

## File Structure

**Created:**

| path | responsibility |
|---|---|
| `scripts/render_overlay_frames.py` | Build synthetic media, render a single-shooter overlay, composite it over the trim, dump labelled frames. The only way to see what this port changes. |
| `src/splitsmith/overlay_clock.py` | The `drawtext` clock's shared vocabulary: colour literals, the held-text formatter, the elapsed expression, the common option string. Consumed by `compare/mp4_grid.py` and `overlay_render.py`. |
| `src/splitsmith/overlay_single.py` | What a single-shooter overlay frame *says*, and the run-length encoding that decides when it changes. Sibling of `compare/overlay_live.py`. |
| `tests/test_overlay_clock.py` | Unit tests for the extracted helpers. |
| `tests/test_overlay_single.py` | Unit tests for runs and declarations. |

**Modified:**

| path | change |
|---|---|
| `src/splitsmith/overlay_html.py` | Add `single_html`. |
| `src/splitsmith/compare/mp4_grid.py` | `_ffmpeg_color`, `_clock_text` and the inline `common`/`elapsed` strings become calls into `overlay_clock`. Behaviour identical. |
| `src/splitsmith/overlay_render.py` | Remove `Template`, `DefaultTemplate`, `_split_alpha`, `_format_running_total`, `_draw`, the `overlay_text` re-exports. Add the rasterizer, the runs loop and the clock filters. |
| `src/splitsmith/cli.py` | Remove `--font`. Restate `--max-height` / `--max-fps` help. |
| `tests/test_overlay_render.py` | 6 deleted, 5 moved out, 10 gain `rasterizer=`, 10 untouched. |
| `tests/test_overlay_text.py` | Receives the 5 moved font tests. |
| `tests/test_overlay_theme.py` | 5 `DefaultTemplate` call sites re-expressed against `single_html`'s CSS; 1 font re-export test repointed. |
| `tests/test_overlay_html.py` | Gains `single_html` tests. |

**Not modified, deliberately:** `src/splitsmith/ui/exports.py` needs no change -- it never passed a font, and `OverlayRenderError` already becomes a visible skip reason. `src/splitsmith/mcp/` has no font parameter. The SPA has no font control.

---

### Task 1: The frame tool, and the control render

Nothing else in this plan can be reviewed without this. Build it first, run it against unmodified `main`, and keep the output -- that is the "before" half of every comparison later.

**Files:**
- Create: `scripts/render_overlay_frames.py`

**Interfaces:**
- Consumes: `splitsmith.overlay_render.render_overlay` (current signature, unchanged at this point), `tests.synthetic_media.build_synthetic_video`, `tests.compare_fixture.cut_clip`, `tests.compare_fixture.write_audit`.
- Produces: PNG frames at `build/overlay-frames/<moment>.png`. No Python API.

- [ ] **Step 1: Read the two reference files**

Read `scripts/render_grid_frames.py` in full -- it is the pattern. Note especially its module docstring (why moments are frame indices and never timestamp seeks), `_extract` at lines 218-238, and `main()`'s argparse block at 314-368.

Read `src/splitsmith/mp4_render.py:440-460` for the alpha-composite filter shape. Do not invent your own.

- [ ] **Step 2: Write the script**

```python
"""Render a single-shooter overlay and drop labelled frames at named moments.

The counterpart to ``scripts/render_grid_frames.py``, which covers the
compare grid. ``splitsmith export overlay`` produces a *transparent* MOV
meant to sit on V2 in Final Cut over the trimmed clip on V1, so looking
at the MOV on its own tells you almost nothing -- this composites it the
way Final Cut would and then extracts frames.

Run::

    uv run python scripts/render_overlay_frames.py

    # against a different output directory, to diff two revisions
    uv run python scripts/render_overlay_frames.py --out build/overlay-frames-main

It builds its own media (``tests/synthetic_media.py``) and its own audit
(``tests/compare_fixture.write_audit``) -- no real match, nothing that
only exists on one laptop.

Frames come out at **named** moments rather than frame indices the caller
has to work out: ``pre-beep``, ``first-shot``, ``mid-action``,
``last-shot``, ``after-last-shot`` and ``tail-end``.

**Moments are converted to frame indices once, in Python, and extracted
with ``select=eq(n,N)``** -- never by seeking to a timestamp. The
synthetic clip runs at 30000/1001, so a seek that keeps the first frame
at or after a requested time is deciding a tie that sub-tick rounding
breaks in either direction. A frame index is exact at any rate.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# ``tests`` is a package on the repo root, not under ``src``. The fixture
# lives there because it is a fixture -- this tool is a consumer, not its
# owner.
sys.path.insert(0, str(REPO_ROOT))

from splitsmith import overlay_render  # noqa: E402
from tests.compare_fixture import cut_clip, write_audit  # noqa: E402
from tests.synthetic_media import (  # noqa: E402
    SYNTHETIC_FPS_DEN,
    SYNTHETIC_FPS_NUM,
    build_synthetic_video,
    ffmpeg_available,
)

DEFAULT_OUT = REPO_ROOT / "build" / "overlay-frames"

FPS = SYNTHETIC_FPS_NUM / SYNTHETIC_FPS_DEN
CLIP_FRAMES = 300
BEEP_OFFSET_SECONDS = 1.0
# A 12-shot stage at roughly IPSC Production Optics pace: a 1.1s draw
# then splits in the 0.18-0.34s band. Real enough that the counter and
# the split label both change at a plausible rate.
SHOTS_MS = (1100, 1320, 1560, 1740, 1980, 2310, 2530, 2790, 3040, 3280, 3600, 3850)


@dataclass(frozen=True)
class Moment:
    name: str
    index: int
    why: str


def _moments() -> tuple[Moment, ...]:
    def at(seconds: float) -> int:
        return round(seconds * FPS)

    first = BEEP_OFFSET_SECONDS + SHOTS_MS[0] / 1000.0
    last = BEEP_OFFSET_SECONDS + SHOTS_MS[-1] / 1000.0
    mid = BEEP_OFFSET_SECONDS + SHOTS_MS[len(SHOTS_MS) // 2] / 1000.0
    return (
        Moment("pre-beep", at(BEEP_OFFSET_SECONDS / 2), "counter reads 0/M, clock reads 0.00"),
        Moment("first-shot", at(first), "counter goes 1/M, no split yet -- nothing to measure against"),
        Moment("mid-action", at(mid), "counter and split both live, clock ticking"),
        Moment("last-shot", at(last), "counter reads M/M"),
        Moment("after-last-shot", at(last + 0.75), "clock frozen, split still up"),
        Moment("tail-end", CLIP_FRAMES - 2, "the post-buffer -- what the viewer is left looking at"),
    )


def _run(cmd: list[str]) -> None:
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd[:3])}...\n{done.stderr[-2000:]}")


def _composite(trim: Path, overlay: Path, destination: Path, *, ffmpeg: str) -> None:
    """Burn the alpha overlay onto the trim, the way FCP composites V2
    over V1. The filter shape is ``mp4_render._build_stage_filter_graph``'s
    (see ``src/splitsmith/mp4_render.py:454-457``), not a new one."""
    _run(
        [
            ffmpeg, "-hide_banner", "-y", "-v", "error",
            "-i", str(trim), "-i", str(overlay),
            "-filter_complex",
            "[1:v]setpts=PTS-STARTPTS[overlay_v];[0:v][overlay_v]overlay=0:0[out]",
            "-map", "[out]", "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p",
            str(destination),
        ]  # fmt: skip
    )


def _extract(video: Path, index: int, destination: Path, *, ffmpeg: str) -> bool:
    """Write frame ``index`` of ``video`` to ``destination``.

    Returns ``False`` when the index is past the end rather than raising:
    ffmpeg exits 0 and writes nothing in that case, and a caller asking
    for a moment a shorter render does not contain should hear about it
    once, not lose the whole run.
    """
    destination.unlink(missing_ok=True)
    _run(
        [
            ffmpeg, "-hide_banner", "-y", "-v", "error", "-i", str(video),
            "-vf", f"select=eq(n\\,{index})", "-fps_mode", "passthrough",
            "-frames:v", "1", str(destination),
        ]  # fmt: skip
    )
    return destination.exists() and destination.stat().st_size > 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--theme", choices=("splitsmith", "clean"), default="splitsmith")
    parser.add_argument("--keep-video", action="store_true")
    args = parser.parse_args()

    if not ffmpeg_available():
        parser.error("ffmpeg and ffprobe must be on PATH")
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None  # ffmpeg_available() just said so

    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = out / "work"
    work.mkdir()

    source = work / "source.mp4"
    build_synthetic_video(source)
    trim = work / "trim.mp4"
    cut_clip(source, trim, CLIP_FRAMES, ffmpeg=ffmpeg)

    audit = work / "stage1.json"
    write_audit(audit, SHOTS_MS)

    overlay = work / "overlay.mov"
    overlay_render.render_overlay(
        audit_path=audit,
        trimmed_video_path=trim,
        output_path=overlay,
        beep_offset_seconds=BEEP_OFFSET_SECONDS,
        codec="prores-4444",
        theme=args.theme,
        ffmpeg_binary=ffmpeg,
    )

    composed = work / "composed.mp4"
    _composite(trim, overlay, composed, ffmpeg=ffmpeg)

    for moment in _moments():
        target = out / f"{moment.name}.png"
        if _extract(composed, moment.index, target, ffmpeg=ffmpeg):
            print(f"{moment.name:18s} frame {moment.index:4d}  {moment.why}")
        else:
            print(f"{moment.name:18s} frame {moment.index:4d}  SKIPPED (past end of render)")

    if not args.keep_video:
        shutil.rmtree(work)
    print(f"\nframes in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run it on unmodified `main` and keep the output as the control**

```bash
uv run python scripts/render_overlay_frames.py --out build/overlay-frames-main
```

Expected: six lines printed, six PNGs in `build/overlay-frames-main/`. Open them. You should see a white `N/12` top-left, a running clock top-right, and an amber split bottom-centre. If any frame is blank, stop -- the tool is wrong, not the renderer.

- [ ] **Step 4: Prove the render is deterministic**

```bash
uv run python scripts/render_overlay_frames.py --out build/overlay-frames-main2
for f in build/overlay-frames-main/*.png; do
  cmp "$f" "build/overlay-frames-main2/$(basename "$f")" || echo "DIFFERS: $f"
done
```

Expected: no output. Every frame byte-identical. If frames differ between two runs of the *same* code, a before/after diff means nothing and you must find out why before continuing. Delete `build/overlay-frames-main2` afterwards.

- [ ] **Step 5: Commit**

```bash
git add scripts/render_overlay_frames.py
git commit -m "feat(overlay): a frame tool for the single-shooter export

The counterpart to render_grid_frames.py. Builds its own media, renders
the alpha overlay, composites it over the trim the way FCP does, and
drops frames at named moments so two revisions diff."
```

---

### Task 2: Extract the clock helpers into `overlay_clock.py`

Pure refactor. The grid's rendered output must not change by one character.

**Files:**
- Create: `src/splitsmith/overlay_clock.py`
- Create: `tests/test_overlay_clock.py`
- Modify: `src/splitsmith/compare/mp4_grid.py` (delete `_ffmpeg_color` at 786-791 and `_clock_text` at 794-806; rewrite the `common`/`elapsed` construction inside `_clock_filters` at 931-950)

**Interfaces:**
- Consumes: `splitsmith.runtime.quote_filter_value`.
- Produces:
  - `ffmpeg_color(rgb: tuple[int, int, int]) -> str`
  - `clock_text(seconds: float) -> str`
  - `elapsed_text_option(start: str) -> str`
  - `clock_common_options(*, font_path: Path, font_size: int, ink: tuple[int, int, int], stroke: tuple[int, int, int], x_expr: str, y_expr: str) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_overlay_clock.py`:

```python
"""The drawtext clock's shared vocabulary (issue #684).

These assert exact literal strings rather than recomputing them. The
escaping was established against ffmpeg 6.1.1 by measurement, not by
reasoning, and a "harmless tidy" of it has to change a test deliberately.
"""

from pathlib import Path

from splitsmith import overlay_clock


def test_ffmpeg_color_is_hex_because_the_theme_ink_has_no_name() -> None:
    assert overlay_clock.ffmpeg_color((244, 244, 245)) == "0xf4f4f5"
    assert overlay_clock.ffmpeg_color((0, 0, 0)) == "0x000000"


def test_clock_text_truncates_rather_than_rounds() -> None:
    # The held value must never read above the last value the ticking
    # filter drew, so this truncates.
    assert overlay_clock.clock_text(2.567) == "2.56"
    assert overlay_clock.clock_text(0.05) == "0.05"


def test_clock_text_truncates_on_milliseconds_not_in_floating_point() -> None:
    # 2.09 * 100 is 208.99999999999997, which floors to 2.08 and would
    # show the clock jumping backwards at the freeze.
    assert overlay_clock.clock_text(2.09) == "2.09"


def test_the_elapsed_expression_is_character_for_character_what_the_grid_emits() -> None:
    assert overlay_clock.elapsed_text_option("1.5") == (
        r"text='%{eif\:trunc(t-1.5)\:d}.%{eif\:trunc(mod((t-1.5)*100\,100))\:d\:2}'"
    )


def test_the_common_options_are_character_for_character_what_the_grid_emits() -> None:
    assert overlay_clock.clock_common_options(
        font_path=Path("/tmp/JetBrainsMono-Bold.ttf"),
        font_size=64,
        ink=(244, 244, 245),
        stroke=(10, 11, 13),
        x_expr="960+960-tw-24",
        y_expr="540+24",
    ) == (
        "fontfile='/tmp/JetBrainsMono-Bold.ttf':fontsize=64:"
        "fontcolor=0xf4f4f5:borderw=3:bordercolor=0x0a0b0d:"
        "x=960+960-tw-24:y=540+24"
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_overlay_clock.py -n0 -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.overlay_clock'`.

- [ ] **Step 3: Write the module**

Create `src/splitsmith/overlay_clock.py`:

```python
"""The running clock's ``drawtext`` vocabulary, shared by both renderers.

The clock is the one genuinely per-frame element in either overlay: it
changes every frame, so it cannot be a pre-rendered sprite, and it is
positioned by an expression ffmpeg evaluates at draw time when it finally
knows ``tw``/``th``. Both the compare grid (``compare/mp4_grid.py``) and
the single-shooter export (``overlay_render.py``) therefore build
``drawtext`` filters, and before issue #684 only the grid could -- these
helpers lived as module-private functions inside it.

**Nothing here is new.** Every string this module produces is
character-for-character what ``mp4_grid`` built inline, and the tests
assert the literals rather than recomputing them. The escaping was
established against ffmpeg 6.1.1 by measurement: inside ``text='...'``
the ``:`` and ``,`` separators of ``%{eif:...}`` still have to be
backslash-escaped or the filtergraph parser splits the option on them,
and ``%{eif:...:d:2}`` zero-pads so 0.05s renders ``0.05`` and not
``0.5``.

**Known, measured, and deliberately left alone:** the hundredths half of
:func:`elapsed_text_option` reads one hundredth *low* on about 4.6% of
frames. ``t`` arrives as a binary float, so ``mod((t-start)*100,100)``
lands just under the integer it should be and ``trunc`` takes the value
below. Simulated over 95,132 frames (4 frame rates x 4 start offsets):
4.59% of frames affected, **zero** backward steps, and across 112 freeze
scenarios the held text never read below the last value the ticking
filter drew. The properties a viewer can perceive -- a clock that only
counts up, and a final time agreeing with the last ticked one -- all
hold. An epsilon inside the expression only reaches 2.52% and is
identical at 1e-7, 1e-6 and 1e-5: it moves which frames are wrong rather
than making them right. Getting it exact means computing hundredths
outside ffmpeg, i.e. one filter per hundredth -- thousands per stage. Do
not "tidy" this expression into a third form without re-measuring both
numbers.
"""

from __future__ import annotations

from pathlib import Path

from .runtime import quote_filter_value


def ffmpeg_color(rgb: tuple[int, int, int]) -> str:
    """``drawtext`` colour literal. Hex, because it takes named colours
    only from its own table -- the splitsmith theme's ink is
    ``(244, 244, 245)``, which has no name."""
    red, green, blue = rgb
    return f"0x{red:02x}{green:02x}{blue:02x}"


def clock_text(seconds: float) -> str:
    """Format an elapsed time the way the ticking filter renders it.

    Truncated to hundredths rather than rounded, so a held value can
    never read above the last value the ticking filter drew.

    The truncation runs on integer milliseconds and not on
    ``math.floor(seconds * 100) / 100``: ``2.09 * 100`` is
    ``208.99999999999997`` in binary floating point, which floors to
    ``2.08`` and would show the clock jumping backwards at the freeze.
    """
    hundredths = round(seconds * 1000) // 10
    return f"{hundredths // 100}.{hundredths % 100:02d}"


def border_width(font_size: int) -> int:
    """Stroke around the clock's glyphs, floored so small type still
    reads over bright footage."""
    return max(2, font_size // 18)


def elapsed_text_option(start: str) -> str:
    """The ``text='...'`` option for a clock ticking from ``start``.

    ``start`` is pre-formatted by the caller (``f"{seconds:g}"``) so the
    same literal appears in both this expression and the ``enable``
    window that gates it -- two spellings of one number is how a filter
    ends up drawing over its neighbour.
    """
    return (
        f"text='%{{eif\\:trunc(t-{start})\\:d}}." f"%{{eif\\:trunc(mod((t-{start})*100\\,100))\\:d\\:2}}'"
    )


def clock_common_options(
    *,
    font_path: Path,
    font_size: int,
    ink: tuple[int, int, int],
    stroke: tuple[int, int, int],
    x_expr: str,
    y_expr: str,
) -> str:
    """Every ``drawtext`` option a clock filter shares with its siblings.

    ``font_path`` must be a real file that outlives the render --
    ``drawtext`` opens it itself, so a temp file from
    ``importlib.resources.as_file`` will not do. See
    :func:`splitsmith.overlay_text.materialize_font`.
    """
    font = quote_filter_value(str(font_path))
    return (
        f"fontfile={font}:fontsize={font_size}:"
        f"fontcolor={ffmpeg_color(ink)}:"
        f"borderw={border_width(font_size)}:"
        f"bordercolor={ffmpeg_color(stroke)}:"
        f"x={x_expr}:y={y_expr}"
    )
```

- [ ] **Step 4: Run the new tests**

Run: `uv run pytest tests/test_overlay_clock.py -n0 -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Point `mp4_grid` at the new module**

In `src/splitsmith/compare/mp4_grid.py`:

Delete `_ffmpeg_color` (786-791) and `_clock_text` (794-806) entirely. Add to the imports near line 35:

```python
from ..overlay_clock import clock_common_options, clock_text, elapsed_text_option, ffmpeg_color
```

Replace the body of `_clock_filters` from `common = (` through `elapsed = (...)` (lines 941-951) with:

```python
        common = clock_common_options(
            font_path=overlay.font_path,
            font_size=overlay.font_size,
            ink=overlay.ink,
            stroke=overlay.stroke,
            x_expr=x_expr,
            y_expr=y_expr,
        )
        start = f"{clock.start_seconds:g}"
        elapsed = elapsed_text_option(start)
```

Delete the now-unused `font = quote_filter_value(str(overlay.font_path))` line (933). Keep `quote_filter_value` imported -- line 962 still uses it for `final_text`.

Update the two remaining internal references: `_clock_text(last)` at line 1815 becomes `clock_text(last)`.

`_clock_pad` (809-819) stays exactly as it is. It is a one-line wrapper over `CellScale.for_cell(cell_height).pad` with a docstring that earns its keep, and it is grid geometry, not clock vocabulary.

- [ ] **Step 6: Prove the grid's output did not move**

The argv fingerprint tests do NOT cover drawtext -- both build commands without `overlay=`, so `_clock_filters` never runs. The real proof is the substring suite:

```bash
uv run pytest tests/test_compare_mp4_grid_overlay.py tests/test_compare_mp4_grid_hold.py -n0 -q
```

Expected: all pass, zero failures. These assert `enable='gte(t\,1)*lt(t\,6)'`, `trunc(t-1.5)`, `:x=960+960-tw-24:y=540+24:`, `fontcolor=0xf4f4f5`, `bordercolor=0x0a0b0d` and `_clock_text`'s literal returns. If any fails, the extraction changed a character -- fix the extraction, never the test.

Two of those tests call `mp4_grid._clock_text` directly (`tests/test_compare_mp4_grid_overlay.py:525-528, 544-545`). That name no longer exists. Repoint them at `overlay_clock.clock_text` -- an import change, not an assertion change. Note this in the commit message as a deliberate edit.

- [ ] **Step 7: Run the whole grid suite plus linting**

```bash
uv run pytest tests/test_compare_mp4_grid_commands.py tests/test_overlay_layout.py -n0 -q
uv run black --check . && uv run ruff check .
```

Expected: all pass. Both argv fingerprint hashes must still match -- if either moved, the extraction touched something outside the clock.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/overlay_clock.py tests/test_overlay_clock.py \
        src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_overlay.py
git commit -m "refactor(overlay): the drawtext clock's vocabulary becomes shared

Pure extraction ahead of #684's single-shooter port, which needs the
same expression. Every emitted string is character-for-character what
mp4_grid built inline; the substring suite in
test_compare_mp4_grid_overlay.py is the proof.

The two tests calling mp4_grid._clock_text are repointed at
overlay_clock.clock_text -- an import change, no assertion touched."
```

---

### Task 3: `overlay_html.single_html`

**Files:**
- Modify: `src/splitsmith/overlay_html.py` (add after `cell_html`, before `grid_html`)
- Modify: `tests/test_overlay_html.py`

**Interfaces:**
- Consumes: `_style_rules`, `_fit_script`, `_cell_div` (all module-private, already present).
- Produces: `single_html(groups: Sequence[Group], *, width: int, height: int, scale: CellScale, theme: OverlayTheme) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_overlay_html.py`. Follow the file's established style: substring and regex assertions on the raw string, using the existing `_rule(html, selector)` helper. No HTML parser.

```python
# --- single_html (issue #684) ------------------------------------------


def _live_groups() -> tuple[Group, ...]:
    return (
        Group(
            anchor=Anchor.TOP_LEFT,
            flow=Flow.ROW,
            elements=(Element(text="7/32", role=Role.LIVE_PRIMARY),),
        ),
        Group(
            anchor=Anchor.BOTTOM_CENTER,
            flow=Flow.ROW,
            elements=(Element(text="0.21s", role=Role.LIVE_PRIMARY, color=ColorToken.SPLIT),),
        ),
    )


def test_single_html_is_a_whole_document_not_a_fragment() -> None:
    doc = single_html(_live_groups(), width=1920, height=1080, scale=SCALE, theme=THEME)
    assert doc.startswith("<!doctype html>")
    assert "</html>" in doc


def test_single_html_sizes_the_document_to_the_canvas_and_stays_transparent() -> None:
    """``.cell`` is width/height 100%, which only resolves against a sized
    ancestor. ``grid_html`` supplies one via its ``.grid`` wrapper; this
    has no wrapper, so ``html, body`` has to carry the canvas size or the
    cell collapses and every anchor lands in the wrong place."""
    doc = single_html(_live_groups(), width=1920, height=1080, scale=SCALE, theme=THEME)
    rule = _rule(doc, "html, body")
    assert "width: 1920px" in rule
    assert "height: 1080px" in rule
    assert "background: transparent" in rule


def test_single_html_has_no_grid_wrapper() -> None:
    doc = single_html(_live_groups(), width=1920, height=1080, scale=SCALE, theme=THEME)
    assert 'class="grid"' not in doc
    assert "grid-template-columns: repeat(" not in doc


def test_single_html_carries_the_declared_text() -> None:
    doc = single_html(_live_groups(), width=1920, height=1080, scale=SCALE, theme=THEME)
    assert "7/32" in doc
    assert "0.21s" in doc


def test_single_html_carries_the_fit_script() -> None:
    """The rasterizer calls ``window.__splitsmithFit`` on every document.
    The ``&&`` guard makes omitting it safe, but omitting it trades the
    shrink-before-clip policy for bare overflow clipping, for no gain."""
    doc = single_html(_live_groups(), width=1920, height=1080, scale=SCALE, theme=THEME)
    assert "window.__splitsmithFit" in doc


def test_single_html_reads_sizes_off_the_scale_it_is_given() -> None:
    doc = single_html(_live_groups(), width=1920, height=1080, scale=SCALE, theme=THEME)
    assert f"font-size: {SCALE.live_primary}px" in _rule(doc, ".role-live-primary")
```

Add `single_html` to the module's import line at the top of the test file, and `ColorToken` / `Flow` / `Role` / `Element` / `Group` / `Anchor` if not already imported.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_overlay_html.py -n0 -k single_html -v`
Expected: FAIL with `ImportError: cannot import name 'single_html'`.

- [ ] **Step 3: Implement**

Insert into `src/splitsmith/overlay_html.py` between `cell_html` and `grid_html`:

```python
def single_html(
    groups: Sequence[Group],
    *,
    width: int,
    height: int,
    scale: CellScale,
    theme: OverlayTheme,
) -> str:
    """One canvas-sized cell as a whole HTML document (issue #684).

    The single-shooter overlay's counterpart to :func:`grid_html`. There
    is exactly one cell and it is the whole frame, so this takes plain
    pixel dimensions rather than a :class:`SpriteGeometry` -- nothing
    about a single-shooter export has rows, columns or tile placements,
    and borrowing the grid's vocabulary to express "one of one" would be
    the same information spelled twice.

    :func:`cell_html` is nearly this and its docstring names "a future
    single-shooter port" as its reason to exist, but it returns a
    *fragment*. That matters more than it sounds: ``.cell`` is
    ``width: 100%; height: 100%``, which resolves against its containing
    block, and in a grid document that block is a grid item sized by
    ``.grid``'s pixel tracks. A fragment dropped into an empty page has
    no such ancestor, so the cell collapses to its content and every
    anchor lands in the wrong place. This emits the same ``html, body``
    sizing block :func:`grid_html` does -- minus the grid tracks -- so the
    cell fills the canvas.

    ``html``/``body`` stay ``background: transparent``: the rasterizer
    screenshots with ``omit_background=True`` and the result is piped to
    ffmpeg as an alpha layer. An opaque background here would paint the
    whole frame black over the footage.

    Carries the fit-policy ``<script>`` for the same reason both siblings
    do -- see :func:`_fit_script`.
    """
    style = _style_rules(scale=scale, theme=theme)
    page_style = (
        "html, body {\n"
        "margin: 0; padding: 0;\n"
        f"width: {width}px; height: {height}px;\n"
        "background: transparent; overflow: hidden;\n"
        "}"
    )
    return (
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8"><title>overlay</title>'
        f"<style>{style}\n{page_style}</style>"
        f"{_fit_script()}"
        "</head>"
        f"<body>{_cell_div(groups)}</body></html>"
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_overlay_html.py -n0 -q`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Verify it actually rasterizes**

A passing string test does not prove a browser lays it out correctly. Run this once by hand:

```bash
uv run python -c "
from splitsmith.overlay_html import single_html
from splitsmith.overlay_layout import Anchor, CellScale, ColorToken, Element, Flow, Group, Role
from splitsmith.overlay_theme import load_theme
from splitsmith.overlay_raster import ChromiumRasterizer
from PIL import Image
import io
W,H=1920,1080
scale=CellScale.for_cell(H)
g=(Group(anchor=Anchor.TOP_LEFT,flow=Flow.ROW,elements=(Element(text='7/32',role=Role.LIVE_PRIMARY),)),
   Group(anchor=Anchor.BOTTOM_CENTER,flow=Flow.ROW,elements=(Element(text='0.21s',role=Role.LIVE_PRIMARY,color=ColorToken.SPLIT),)))
with ChromiumRasterizer() as r:
    png=r.png(single_html(g,width=W,height=H,scale=scale,theme=load_theme('splitsmith')),width=W,height=H)
im=Image.open(io.BytesIO(png)).convert('RGBA')
print('counter bbox', im.crop((0,0,W//2,H//3)).getbbox(), 'expect near x=pad=', scale.pad)
print('split bbox  ', im.crop((0,H*2//3,W,H)).getbbox(), 'expect x centred on', W//2)
"
```

Expected: counter bbox starting near x=31, y=41 (pad 30 plus a pixel of stroke); split bbox horizontally centred around 960. If the counter is at (0,0) or the split is left-aligned, the `html, body` sizing is not reaching `.cell`.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/overlay_html.py tests/test_overlay_html.py
git commit -m "feat(overlay): single_html composes one canvas-sized cell

cell_html's docstring has named this port as its reason to exist since
#683, but it returns a fragment -- .cell's 100% sizing needs an ancestor
that a fragment has no way to supply."
```

---

### Task 4: `overlay_single.py` -- runs and declarations

**Files:**
- Create: `src/splitsmith/overlay_single.py`
- Create: `tests/test_overlay_single.py`

**Interfaces:**
- Consumes: `splitsmith.overlay_render.FrameState` (unchanged), `overlay_layout`'s `Anchor`/`ColorToken`/`Element`/`Flow`/`Group`/`Role`.
- Produces:
  - `OverlayRun` frozen dataclass with fields `start_frame: int`, `frame_count: int`, `shots_fired: int`, `shot_count: int`, `last_split: float | None`
  - `build_overlay_runs(states: Sequence[FrameState]) -> tuple[OverlayRun, ...]`
  - `run_groups(run: OverlayRun) -> tuple[Group, ...]`

Note on import direction: `overlay_single` imports `FrameState` from `overlay_render`, and `overlay_render` imports `build_overlay_runs`/`run_groups` from `overlay_single`. That is a cycle. Avoid it by having `overlay_single` NOT import `FrameState` -- `build_overlay_runs` takes any sequence of objects with the three attributes it reads, typed as a `Protocol` declared locally. This keeps `overlay_single` at the leaf of the import graph, the way `overlay_layout` and `overlay_html` are.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_overlay_single.py`:

```python
"""Runs and declarations for the single-shooter overlay (issue #684)."""

import pytest

from splitsmith.overlay_layout import Anchor, ColorToken, Role
from splitsmith.overlay_render import build_frame_states
from splitsmith.overlay_single import OverlayRun, build_overlay_runs, run_groups


def _states(shots: list[float], *, beep: float = 1.0, fps: float = 30.0, duration: float = 10.0):
    return build_frame_states(
        shot_times_in_clip=shots,
        beep_time_in_clip=beep,
        fps=fps,
        duration_seconds=duration,
    )


def test_run_count_is_one_per_distinct_shots_fired_value() -> None:
    """A 12-shot stage steps 13 times: once for the pre-beep state and
    once per shot. 600 frames collapse to 13 browser renders."""
    shots = [1.0 + 0.2 * i for i in range(12)]
    runs = build_overlay_runs(_states(shots))
    assert len(runs) == 13
    assert [r.shots_fired for r in runs] == list(range(13))


def test_run_lengths_sum_to_the_frame_count() -> None:
    """The pipe writes one buffer per frame. If the runs do not tile the
    timeline exactly, the MOV is a different length than the trim and
    drifts on the FCP timeline -- the module's first promise."""
    states = _states([1.0 + 0.2 * i for i in range(12)])
    runs = build_overlay_runs(states)
    assert sum(r.frame_count for r in runs) == len(states)


def test_runs_are_contiguous_and_start_at_zero() -> None:
    runs = build_overlay_runs(_states([1.0 + 0.2 * i for i in range(12)]))
    assert runs[0].start_frame == 0
    for earlier, later in zip(runs, runs[1:], strict=True):
        assert earlier.start_frame + earlier.frame_count == later.start_frame


def test_two_shots_inside_one_frame_are_one_boundary_not_two() -> None:
    """Run count is distinct ``shots_fired`` values, not shots plus one.
    At 30fps these two shots both land after frame 60's timestamp and
    before frame 61's, so the counter steps straight from 0 to 2 and
    there is no frame on which to draw the state in between."""
    runs = build_overlay_runs(_states([2.001, 2.005], duration=5.0))
    assert [r.shots_fired for r in runs] == [0, 2]


def test_the_split_survives_to_the_final_frame() -> None:
    """No fade: the last split stays up through the post-buffer. The grid
    convention, chosen deliberately -- a step function has no frames
    between events to ramp alpha across."""
    runs = build_overlay_runs(_states([1.2, 1.5], duration=10.0))
    assert runs[-1].last_split == pytest.approx(0.3)


def test_the_draw_is_drawn_as_shot_ones_split() -> None:
    """Shot 1 has no previous shot, so ``build_frame_states`` reports its
    time from the beep -- the draw. A single-shooter overlay shows it,
    because the draw is a number the shooter cares about. Only the
    pre-beep run has no split at all."""
    runs = build_overlay_runs(_states([1.4], duration=5.0))
    assert runs[0].last_split is None
    assert runs[1].last_split == pytest.approx(0.4)


def test_groups_put_the_counter_top_left_and_the_split_bottom_centre() -> None:
    groups = run_groups(OverlayRun(start_frame=0, frame_count=1, shots_fired=7, shot_count=32, last_split=0.21))
    by_anchor = {g.anchor: g for g in groups}
    assert by_anchor[Anchor.TOP_LEFT].elements[0].text == "7/32"
    assert by_anchor[Anchor.BOTTOM_CENTER].elements[0].text == "0.21s"


def test_the_split_paints_in_the_split_colour_and_the_counter_does_not() -> None:
    groups = run_groups(OverlayRun(start_frame=0, frame_count=1, shots_fired=7, shot_count=32, last_split=0.21))
    by_anchor = {g.anchor: g for g in groups}
    assert by_anchor[Anchor.BOTTOM_CENTER].elements[0].color is ColorToken.SPLIT
    assert by_anchor[Anchor.TOP_LEFT].elements[0].color is None


def test_both_elements_are_live_primary() -> None:
    groups = run_groups(OverlayRun(start_frame=0, frame_count=1, shots_fired=7, shot_count=32, last_split=0.21))
    assert all(e.role is Role.LIVE_PRIMARY for g in groups for e in g.elements)


def test_the_counter_reads_zero_of_m_before_the_first_shot() -> None:
    """Unchanged from today, and deliberately different from the grid's
    rule. Four tiles all reading 0/32 over people standing still is
    noise; on a single-shooter frame it is the only thing on screen and
    it tells the viewer the stage's round count."""
    groups = run_groups(OverlayRun(start_frame=0, frame_count=30, shots_fired=0, shot_count=32, last_split=None))
    assert len(groups) == 1
    assert groups[0].anchor is Anchor.TOP_LEFT
    assert groups[0].elements[0].text == "0/32"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_overlay_single.py -n0 -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.overlay_single'`.

- [ ] **Step 3: Implement**

Create `src/splitsmith/overlay_single.py`:

```python
"""Declaration to sprite: the single-shooter overlay (issue #684).

What one shooter's overlay says while a stage is in progress -- the shot
counter and the last split -- as declared ``Group``/``Element`` objects,
and the run-length encoding that decides how often that changes. This
module is to the single-shooter export exactly what
``compare/overlay_live.py`` is to the grid's per-tile sprites, and it is
deliberately a separate module rather than a parameter on that one: the
two disagree about what to draw before the first shot, and a flag
threaded through ``panel_groups`` to express that would make one
function answer to two products.

**Why runs rather than the grid's state machine.** The grid steps on shot
*events* and then has to snap every resulting boundary onto a whole
output frame (``overlay_sprites.quantize_durations``), because a shot
time is millisecond-grained and lands wherever it lands. Here the
boundaries are frame indices from the start: ``build_frame_states``
already evaluated every frame, so collapsing consecutive frames that say
the same thing produces exactly the same ~31 states with no rounding step
at all. The class of bug ``quantize_durations`` exists to prevent is
absent rather than solved.

**Nothing here measures text**, opens a font, or touches PIL. A run
becomes a document via ``overlay_html.single_html`` and pixels via an
injected ``overlay_raster.Rasterizer``; this module only decides what the
strings are and when they change.

**The clock is not here.** It ticks every frame, so it can never be a
run -- it is an ffmpeg ``drawtext`` filter built from
``overlay_clock``. See ``overlay_render`` for where the two halves meet.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .overlay_layout import Anchor, ColorToken, Element, Flow, Group, Role


class _FrameLike(Protocol):
    """The three fields a run is keyed on.

    A structural type rather than an import of
    ``overlay_render.FrameState``: ``overlay_render`` imports *this*
    module, and naming its type here would close the cycle. Everything
    else in the overlay pipeline (``overlay_layout``, ``overlay_html``,
    ``overlay_theme``) sits at the leaf of the import graph for the same
    reason, and this module belongs there with them.
    """

    shot_count: int
    shots_fired: int
    last_split: float | None


@dataclass(frozen=True)
class OverlayRun:
    """One stretch of frames over which the overlay says the same thing.

    ``frame_count`` is how many frames the run spans, so a caller pipes
    one rasterized buffer exactly that many times. ``start_frame`` is not
    needed to render but is what makes a run addressable in a test and a
    log line -- "the counter changed at frame 41" is a reviewable claim;
    "the third run" is not.

    ``last_split`` is ``None`` only before the first shot. From shot 1 it
    carries what ``build_frame_states`` computed: for shot 1 that is the
    time from the beep -- the draw -- and for every shot after it the gap
    from the previous one. Both are numbers the shooter reads off the
    screen, so both are drawn.
    """

    start_frame: int
    frame_count: int
    shots_fired: int
    shot_count: int
    last_split: float | None


def build_overlay_runs(states: Sequence[_FrameLike]) -> tuple[OverlayRun, ...]:
    """Collapse per-frame states into the runs the overlay actually has.

    Consecutive frames agreeing on ``(shots_fired, last_split)`` become
    one run. For a 30-shot stage that is 31 runs against 600 frames --
    and since a run is one browser render, that ratio is the whole
    reason this port is worth doing.

    Note the count is one per distinct ``shots_fired`` value, **not**
    shots plus one: two shots inside the same frame step the counter
    straight past the intermediate value, and there is no frame on which
    to draw it.

    An empty ``states`` yields no runs, which pipes no frames -- correct
    for a zero-length clip, and unreachable in practice because
    ``render_overlay`` rejects an audit with no shots long before here.
    """
    runs: list[OverlayRun] = []
    for index, state in enumerate(states):
        key = (state.shots_fired, state.last_split)
        if runs and (runs[-1].shots_fired, runs[-1].last_split) == key:
            last = runs[-1]
            runs[-1] = OverlayRun(
                start_frame=last.start_frame,
                frame_count=last.frame_count + 1,
                shots_fired=last.shots_fired,
                shot_count=last.shot_count,
                last_split=last.last_split,
            )
            continue
        runs.append(
            OverlayRun(
                start_frame=index,
                frame_count=1,
                shots_fired=state.shots_fired,
                shot_count=state.shot_count,
                last_split=state.last_split,
            )
        )
    return tuple(runs)


def run_groups(run: OverlayRun) -> tuple[Group, ...]:
    """What the overlay says over one run.

    Two things, in the two places they have always been drawn:

    - the **shot counter** at :attr:`~splitsmith.overlay_layout.Anchor.TOP_LEFT`,
      reading ``"7/32"``;
    - the **last split** at
      :attr:`~splitsmith.overlay_layout.Anchor.BOTTOM_CENTER` in the
      theme's split colour, absent only before the first shot. Shot 1's
      figure is the draw (its time from the beep), which is what
      ``build_frame_states`` puts in ``last_split`` and what today's
      renderer already draws.

    **The counter is drawn from frame zero, reading ``0/32``.** The grid
    (``compare/overlay_live.panel_groups``) deliberately draws nothing
    until a shot fires, and that rule is right *there*: four tiles all
    reading ``0/32`` over four people standing at the start position is a
    number, not information. It is wrong here. On a single-shooter frame
    the counter is the only thing on screen before the beep, and the
    denominator tells the viewer how long the stage is. This difference
    is why the two paths declare their own groups instead of sharing one
    function.

    Both elements are :attr:`~splitsmith.overlay_layout.Role.LIVE_PRIMARY`,
    which at 1x1 resolves to ``max(48, height // 14)`` -- byte-identical
    to what the PIL template computed for both, so the type size does not
    move.
    """
    groups: list[Group] = []
    if run.shot_count > 0:
        groups.append(
            Group(
                anchor=Anchor.TOP_LEFT,
                flow=Flow.ROW,
                elements=(Element(text=f"{run.shots_fired}/{run.shot_count}", role=Role.LIVE_PRIMARY),),
            )
        )
    if run.last_split is not None:
        groups.append(
            Group(
                anchor=Anchor.BOTTOM_CENTER,
                flow=Flow.ROW,
                elements=(
                    Element(
                        text=f"{run.last_split:.2f}s",
                        role=Role.LIVE_PRIMARY,
                        color=ColorToken.SPLIT,
                    ),
                ),
            )
        )
    return tuple(groups)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_overlay_single.py -n0 -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Mutation drill -- prove each test can fail**

A test that passes against the bug it claims to cover is worth nothing. Verify three of them by hand:

1. In `build_overlay_runs`, change the key to `(state.shots_fired,)`. Expected: everything still passes (the split is derived from `shots_fired`, so this is genuinely equivalent) -- so **revert it and instead** change the key to a constant `0`. Expected: `test_run_count_is_one_per_distinct_shots_fired_value` and `test_runs_are_contiguous_and_start_at_zero` FAIL.
2. In `build_overlay_runs`, change `frame_count=last.frame_count + 1` to `+ 2`. Expected: `test_run_lengths_sum_to_the_frame_count` FAILS.
3. In `run_groups`, guard the counter with `if run.shots_fired > 0:` (the grid's rule). Expected: `test_the_counter_reads_zero_of_m_before_the_first_shot` FAILS.

Revert every mutation. If any expected failure does not happen, the test is not testing what it claims -- fix the test before continuing.

- [ ] **Step 6: Lint and commit**

```bash
uv run black --check . && uv run ruff check .
git add src/splitsmith/overlay_single.py tests/test_overlay_single.py
git commit -m "feat(overlay): runs and declarations for the single-shooter overlay

Run-length encodes build_frame_states into event-shaped runs -- 600
frames become 31 renders, with boundaries that are frame indices by
construction rather than millisecond times needing quantisation.

Declares its own groups rather than reusing the grid's: the two disagree
about the pre-beep counter, on purpose."
```

---

### Task 5: Rewrite `overlay_render.py`

The big one. Everything before this was preparation.

**Files:**
- Modify: `src/splitsmith/overlay_render.py`
- Modify: `tests/test_overlay_render.py`
- Modify: `tests/test_overlay_text.py` (receives 5 moved tests)

**Interfaces:**
- Consumes: `overlay_single.build_overlay_runs`, `overlay_single.run_groups`, `overlay_html.single_html`, `overlay_clock.*`, `overlay_raster.ChromiumRasterizer`, `overlay_raster.Rasterizer`, `overlay_raster.RasterizerUnavailableError`, `overlay_raster.INSTALL_HINT`, `runtime.ffmpeg_capabilities`, `overlay_text.resolve_overlay_face`, `overlay_text.overlay_font_file`.
- Produces: `render_overlay(*, audit_path, trimmed_video_path, output_path, beep_offset_seconds, ffmpeg_binary="ffmpeg", probe=None, codec="auto", max_height=None, max_fps=None, theme="splitsmith", rasterizer: Rasterizer | None = None) -> Path`. `template`, `font_name` and `font_path` are gone.

- [ ] **Step 1: Move the five font tests to `tests/test_overlay_text.py`**

Cut these from `tests/test_overlay_render.py` verbatim and paste them into `tests/test_overlay_text.py`:

- `test_load_font_unknown_name_raises` (192-194) -- rename to `test_load_font_unknown_name_raises_via_render_module` is NOT wanted; `test_overlay_text.py` already has a test of that exact name. Drop this one, it is the single true duplicate.
- `test_load_font_known_name_falls_back_when_missing` (197-201)
- `test_available_font_names_includes_known_presets` (204-208)
- `test_load_font_pil_default_fallback_warns` (211-228)
- `test_load_font_bundled_emits_debug_only` (231-238)

In each moved test, change `overlay_render._load_font` to `overlay_text._load_font`, `overlay_render.available_font_names` to `overlay_text.available_font_names`, and `overlay_render.reset_font_log_cache` to `overlay_text.reset_font_log_cache`. Nothing else changes -- same assertions, same monkeypatches (which already target `overlay_text`).

Also in `tests/test_overlay_text.py`, delete `test_overlay_render_reexports_the_same_objects` (26-32). It asserts an identity that this task removes.

Run: `uv run pytest tests/test_overlay_text.py -n0 -q`
Expected: PASS. 4 tests added, 1 deleted.

- [ ] **Step 2: Delete the six mechanism tests**

Delete from `tests/test_overlay_render.py`:
- `test_split_alpha_holds_then_fades_then_zero` (117-129)
- `test_default_template_draws_n_over_m_and_running_total` (135-149)
- `test_default_template_renders_stroke_and_blurred_shadow` (152-189)
- `test_default_template_skips_split_after_fade` (241-258)
- `test_format_running_total_under_minute` (264-266)
- `test_format_running_total_over_minute` (269-270)

Also delete the now-unused `from PIL import Image` import if nothing else in the file uses it.

Do not run the suite yet -- it will fail until Step 4.

- [ ] **Step 3: Write the failing tests for the new behaviour**

Append to `tests/test_overlay_render.py`:

```python
# --- the ported renderer (issue #684) ---------------------------------


class _FakeRasterizer:
    """Records what it was asked to draw and returns a real PNG.

    A real PNG, not a stub: ``render_overlay`` decodes what comes back
    and pipes its bytes, so a fake returning ``b""`` would test a code
    path that cannot exist.
    """

    def __init__(self) -> None:
        self.documents: list[str] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.documents.append(html)
        buffer = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buffer, format="PNG")
        return buffer.getvalue()


def test_the_renderer_rasterizes_once_per_run_not_once_per_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the port. 30 frames, two shots -> three runs
    (nothing fired, one fired, two fired), so three browser renders."""
    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 200}, {"shot_number": 2, "ms_after_beep": 500}]}),
        encoding="utf-8",
    )
    fake = _FakeRasterizer()
    _capture_render_cmd(
        tmp_path, monkeypatch, audit=audit, probe=_meta_30fps(duration=1.0), rasterizer=fake
    )
    assert len(fake.documents) == 3


def test_the_counter_and_split_reach_the_rasterized_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 200}, {"shot_number": 2, "ms_after_beep": 500}]}),
        encoding="utf-8",
    )
    fake = _FakeRasterizer()
    _capture_render_cmd(
        tmp_path, monkeypatch, audit=audit, probe=_meta_30fps(duration=1.0), rasterizer=fake
    )
    assert "0/2" in fake.documents[0]
    assert "1/2" in fake.documents[1]
    assert "2/2" in fake.documents[2]
    # 500ms - 200ms = 0.30s
    assert "0.30s" in fake.documents[2]


def test_a_missing_browser_fails_the_render_with_the_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike the grid, which degrades to clock-only because its MP4 is
    still worth having, the overlay MOV *is* the deliverable here. A
    clock-only MOV looks like a success the user would only discover was
    empty after dropping it on V2 in Final Cut."""
    audit = tmp_path / "stage1.json"
    audit.write_text(json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 100}]}), encoding="utf-8")

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise overlay_raster.RasterizerUnavailableError("no chromium", "detail with a hint")

    monkeypatch.setattr(overlay_render.ChromiumRasterizer, "__enter__", boom)
    monkeypatch.setattr(overlay_render.shutil, "which", lambda _b: "/bin/ffmpeg")
    with pytest.raises(overlay_render.OverlayRenderError) as excinfo:
        overlay_render.render_overlay(
            audit_path=audit,
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=tmp_path / "overlay.mov",
            beep_offset_seconds=0.0,
            probe=_meta_30fps(duration=1.0),
            codec="prores-4444",
        )
    assert overlay_raster.INSTALL_HINT in str(excinfo.value)


def test_the_clock_is_three_drawtext_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-beep 0.00, a ticking window, and a held final value. The grid
    needs only two because it never draws a pre-beep zero; this path has
    always shown one and keeps doing so."""
    audit = tmp_path / "stage1.json"
    audit.write_text(json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 200}]}), encoding="utf-8")
    cmd = _capture_render_cmd(
        tmp_path, monkeypatch, audit=audit, probe=_meta_30fps(duration=2.0),
        rasterizer=_FakeRasterizer(), beep_offset_seconds=1.0,
    )
    graph = cmd[cmd.index("-vf") + 1]
    assert graph.count("drawtext") == 3
    assert r"enable='lt(t\,1)'" in graph
    assert r"enable='gte(t\,1)*lt(t\,1.2)'" in graph
    assert r"enable='gte(t\,1.2)'" in graph


def test_an_ffmpeg_without_drawtext_still_renders_the_counter_and_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other degradation, and it goes the other way: losing the clock
    leaves a file worth having, so it warns rather than failing."""
    audit = tmp_path / "stage1.json"
    audit.write_text(json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 200}]}), encoding="utf-8")
    monkeypatch.setattr(
        overlay_render,
        "ffmpeg_capabilities",
        lambda *_a, **_k: runtime.FFmpegCapabilities(
            binary="ffmpeg", version="6.1.1", drawtext=False, concat_option_keyword=True
        ),
    )
    fake = _FakeRasterizer()
    cmd = _capture_render_cmd(
        tmp_path, monkeypatch, audit=audit, probe=_meta_30fps(duration=1.0), rasterizer=fake
    )
    assert "-vf" not in cmd
    assert len(fake.documents) == 2
```

Add `import io` and `from splitsmith import overlay_raster, runtime` to the test file's imports.

Then the one that reads pixels rather than argv. A correct filter string can still draw two numbers over each other or freeze in the wrong place, and this project has met that exact failure on ffmpeg 6.1.1 -- so the clock's three windows get checked on the picture:

```python
@pytest.mark.integration
def test_the_clock_holds_before_the_beep_ticks_during_and_freezes_after(
    tmp_path: Path,
) -> None:
    """Three windows, asserted on rendered frames rather than on argv.

    No OCR: what matters is behavioural and reads straight off the
    pixels. Two frames inside the same window must be identical in the
    clock's corner (it is holding a value); two frames inside the ticking
    window must differ (it is counting). And the corner must not be
    blank before the beep -- drawing nothing there is exactly the
    regression copying the grid's two-filter clock would have caused.
    """
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")

    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps({"shots": [{"shot_number": 1, "ms_after_beep": 1000}]}), encoding="utf-8"
    )
    output = tmp_path / "overlay.mov"
    with overlay_raster.ChromiumRasterizer() as ras:
        overlay_render.render_overlay(
            audit_path=audit,
            trimmed_video_path=tmp_path / "unused.mp4",
            output_path=output,
            beep_offset_seconds=1.0,
            probe=_meta_30fps(duration=3.0),
            codec="prores-4444",
            rasterizer=ras,
        )

    def corner(frame_index: int) -> Image.Image:
        """The top-right quadrant, where the clock lives."""
        png = tmp_path / f"f{frame_index}.png"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-y", "-v", "error", "-i", str(output),
                "-vf", f"select=eq(n\\,{frame_index})", "-fps_mode", "passthrough",
                "-frames:v", "1", str(png),
            ],  # fmt: skip
            check=True,
        )
        image = Image.open(png).convert("RGBA")
        width, height = image.size
        return image.crop((width // 2, 0, width, height // 3))

    # Pre-beep: two frames, both reading 0.00.
    pre_early, pre_late = corner(6), corner(24)
    assert pre_early.getbbox() is not None, "the clock corner is blank before the beep"
    assert list(pre_early.getdata()) == list(pre_late.getdata())

    # Ticking (beep at 1.0s = frame 30, freeze at 2.0s = frame 60).
    assert list(corner(36).getdata()) != list(corner(50).getdata())

    # Frozen after the last shot: the running total is the stage time,
    # not the clip duration.
    assert list(corner(66).getdata()) == list(corner(86).getdata())
```

Add `import shutil` and `import subprocess` to the test file if not already present.

- [ ] **Step 4: Extend `_capture_render_cmd` to accept the new arguments**

The helper at `tests/test_overlay_render.py:475-526` is used by seven tests. Give it `rasterizer`, `audit`, and `beep_offset_seconds` keyword arguments with defaults that preserve every existing caller's behaviour:

```python
def _capture_render_cmd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    audit: Path | None = None,
    rasterizer: Any = None,
    beep_offset_seconds: float = 0.0,
    **kwargs: Any,
) -> list[str]:
```

Inside, default `audit` to the existing `_write_audit(tmp_path)` call and `rasterizer` to `_FakeRasterizer()`, and pass `rasterizer=rasterizer` and `beep_offset_seconds=beep_offset_seconds` through to `render_overlay`. Existing callers keep working unchanged -- this is the "10 gain one argument" line item, and seven of them get it for free through the helper.

Then add `rasterizer=_FakeRasterizer()` to the three direct `render_overlay` callers that reach the render loop:
- `test_render_overlay_pipes_rgba_frames_and_writes_output` (350-357)
- `test_render_overlay_raises_when_ffmpeg_returns_nonzero`
- `test_render_overlay_writes_real_prores_4444_alpha` -- this one is `@pytest.mark.integration` and gets a **real** `ChromiumRasterizer`, not a fake. Wrap its `render_overlay` call in `with overlay_raster.ChromiumRasterizer() as ras:` and pass `rasterizer=ras`.

- [ ] **Step 5: Run to verify the new tests fail**

Run: `uv run pytest tests/test_overlay_render.py -n0 -q`
Expected: FAIL. The new tests fail on `TypeError: render_overlay() got an unexpected keyword argument 'rasterizer'`.

- [ ] **Step 6: Rewrite `overlay_render.py`**

Delete, in order: the `overlay_text` re-export block (43-75), `Template` (114-124), `DefaultTemplate` (183-289), `_split_alpha` (292-301), `_format_running_total` (304-311), and the `from PIL import Image, ImageDraw` line -- replaced below by an `Image`-only import.

Keep unchanged: `OverlayCodec`, `OVERLAY_CODECS`, `FrameState`, `build_frame_states`, `_shot_times_from_audit`, `_ffmpeg_supports_encoder`, `_resolve_codec`, `_scaled_dimensions`, `_capped_frame_rate`.

New imports:

```python
import contextlib
import io
import tempfile

from PIL import Image

from .config import VideoMetadata
from .fcpxml_gen import probe_video
from .overlay_clock import clock_common_options, clock_text, elapsed_text_option
from .overlay_html import single_html
from .overlay_layout import Anchor, CellScale, anchor_ffmpeg_expr
from .overlay_raster import (
    INSTALL_HINT,
    ChromiumRasterizer,
    Rasterizer,
    RasterizerUnavailableError,
)
from .overlay_single import build_overlay_runs, run_groups
from .overlay_text import OverlayRenderError, overlay_font_file, resolve_overlay_face
from .overlay_theme import ThemeName, load_theme
from .runtime import ffmpeg_capabilities, quote_filter_value
```

Add the clock builder:

```python
def _clock_filter_graph(
    *,
    width: int,
    height: int,
    scale: CellScale,
    font_path: Path,
    beep_offset_seconds: float,
    last_shot_in_clip: float,
    ink: tuple[int, int, int],
    stroke: tuple[int, int, int],
) -> str:
    """The running clock, as three mutually exclusive ``drawtext`` filters.

    The grid needs two -- a ticking window and a held final value. This
    path needs a third because it has always drawn ``0.00`` before the
    beep (``build_frame_states`` clamps ``running_total`` to zero there,
    and the PIL template drew it unconditionally), where the grid draws
    nothing until its beep. Keeping that costs one filter and keeps the
    clock consistent with the counter, which also reads ``0/M`` from
    frame zero.

    The windows are ``lt`` / ``gte`` rather than a ``between``, for the
    reason ``mp4_grid._clock_filters`` documents: ``between(t,a,b)`` and
    ``gte(t,b)`` are both true at exactly ``b``, and a frame landing
    there draws two numbers over each other. Verified for these three
    windows by rendering: at each boundary frame exactly one filter
    draws, and the composite is byte-identical to that filter alone.

    The clock freezes at the last shot rather than running on to the end
    of the clip -- the running total is the stage time, not the clip
    duration, which is what ``build_frame_states`` does in Python for the
    sprite half.
    """
    x_expr, y_expr = anchor_ffmpeg_expr(
        Anchor.TOP_RIGHT, col=0, row=0, cell_w=width, cell_h=height, pad=scale.pad
    )
    common = clock_common_options(
        font_path=font_path,
        font_size=scale.live_primary,
        ink=ink,
        stroke=stroke,
        x_expr=x_expr,
        y_expr=y_expr,
    )
    start = f"{beep_offset_seconds:g}"
    freeze = f"{last_shot_in_clip:g}"
    held = quote_filter_value(clock_text(max(0.0, last_shot_in_clip - beep_offset_seconds)))
    return ",".join(
        (
            f"drawtext={common}:text='0.00':enable='lt(t\\,{start})'",
            f"drawtext={common}:{elapsed_text_option(start)}:"
            f"enable='gte(t\\,{start})*lt(t\\,{freeze})'",
            f"drawtext={common}:text={held}:enable='gte(t\\,{freeze})'",
        )
    )
```

Add `clock_filter: str | None = None` as a keyword to `_build_ffmpeg_cmd`, and insert immediately after the `"-i", "-"` entries:

```python
    if clock_filter is not None:
        cmd += ["-vf", clock_filter]
```

Add the rasterizer resolver:

```python
@contextlib.contextmanager
def _rasterizer_for(supplied: Rasterizer | None) -> Iterator[Rasterizer]:
    """Yield a rasterizer, launching one only when the caller has none.

    A missing browser is a hard failure here, not a degradation. The
    grid can lose its sprites and still hand back an MP4 worth watching;
    this renderer's entire output is the sprites plus a clock, and a
    clock-only MOV is a file that looks like a success until it reaches
    the Final Cut timeline. ``ui/exports.py`` already turns
    ``OverlayRenderError`` into a visible skip reason.
    """
    if supplied is not None:
        yield supplied
        return
    owned = ChromiumRasterizer()
    try:
        active = owned.__enter__()
    except RasterizerUnavailableError as exc:
        raise OverlayRenderError(
            f"cannot render the overlay: {exc.detail} Install it with '{INSTALL_HINT}'."
        ) from exc
    try:
        yield active
    finally:
        owned.__exit__(None, None, None)
```

Add `from collections.abc import Iterator` to the stdlib imports.

Then rewrite `render_overlay`'s body from `if template is None:` (557) to the end:

```python
    scale = CellScale.for_cell(height)
    palette = load_theme(theme)

    states = build_frame_states(
        shot_times_in_clip=shot_times,
        beep_time_in_clip=beep_offset_seconds,
        fps=fps,
        duration_seconds=duration_seconds,
    )
    runs = build_overlay_runs(states)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    rate = f"{rate_num}/{rate_den}"

    # ``drawtext`` opens the font file itself, long after this call, so
    # it has to be a real path that outlives the encode -- not a temp
    # file from ``importlib.resources.as_file``.
    with tempfile.TemporaryDirectory(prefix="splitsmith-overlay-") as work:
        # One bundled face for every theme. A theme decides colour, never
        # the typeface -- see ``compare.overlay_sprites.theme_font_face``
        # for the measurement behind that: only one of the overlay's two
        # halves could ever honour a per-theme face deterministically.
        font_path = overlay_font_file(resolve_overlay_face("splitsmith-mono"), Path(work))
        capabilities = ffmpeg_capabilities(ffmpeg_binary, font_path=font_path)
        clock_filter: str | None = None
        if capabilities.drawtext:
            clock_filter = _clock_filter_graph(
                width=width,
                height=height,
                scale=scale,
                font_path=font_path,
                beep_offset_seconds=beep_offset_seconds,
                last_shot_in_clip=max(shot_times),
                ink=palette.ink,
                stroke=palette.stroke,
            )
        else:
            logger.warning(
                "%s (ffmpeg %s) has no usable drawtext, so the overlay's running clock is "
                "omitted; the shot counter and split labels still render.",
                capabilities.binary,
                capabilities.version,
            )

        cmd = _build_ffmpeg_cmd(
            ffmpeg_binary=ffmpeg_binary,
            codec=resolved_codec,
            width=width,
            height=height,
            rate=rate,
            output_path=output_path,
            clock_filter=clock_filter,
        )

        with _rasterizer_for(rasterizer) as active:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            assert proc.stdin is not None
            try:
                for run in runs:
                    png = active.png(
                        single_html(
                            run_groups(run),
                            width=width,
                            height=height,
                            scale=scale,
                            theme=palette,
                        ),
                        width=width,
                        height=height,
                    )
                    # Decode once per run and write the same buffer for
                    # every frame it spans. The draw is the expensive
                    # part; the pipe is not, and repeating the buffer is
                    # what keeps the output frame-for-frame with the trim
                    # without a concat list to quantize.
                    frame = Image.open(io.BytesIO(png)).convert("RGBA").tobytes()
                    for _ in range(run.frame_count):
                        proc.stdin.write(frame)
                proc.stdin.close()
            except (BrokenPipeError, OSError) as exc:
                proc.kill()
                proc.wait()
                stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
                raise OverlayRenderError(f"ffmpeg failed during render: {stderr or exc}") from exc

            rc = proc.wait()
    if rc != 0:
        stderr_text = ""
        raise OverlayRenderError(f"ffmpeg exited with {rc}: {stderr_text}")
    return output_path
```

Careful with the final error path: `proc.stderr` must be read before leaving the `with` block. Read it into a local inside the block:

```python
            rc = proc.wait()
            stderr_text = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if rc != 0:
        raise OverlayRenderError(f"ffmpeg exited with {rc}: {stderr_text}")
    return output_path
```

Update `render_overlay`'s signature: delete `template`, `font_name`, `font_path`; add `rasterizer: Rasterizer | None = None`. Update its docstring: remove the `template` / `font_name` / `font_path` paragraphs, and add:

```
    ``rasterizer``: injected :class:`~splitsmith.overlay_raster.Rasterizer`.
        Defaults to launching one headless Chromium for this call. A
        caller rendering several stages should supply one so the browser
        starts once (measured: 0.40 s of startup against 3.93 s of
        rasterizing for a 31-run stage). Without a usable browser this
        raises -- see :func:`_rasterizer_for`.
```

Finally, update the module docstring's pipeline list: step 3 becomes "Run-length encode those states and rasterize one document per run through headless Chromium"; step 4 gains "plus a `drawtext` running clock". Delete the `Template` ABC paragraph.

- [ ] **Step 7: Run the overlay render tests**

Run: `uv run pytest tests/test_overlay_render.py -n0 -q`
Expected: PASS. 20 pre-existing + 5 new = 25 tests.

- [ ] **Step 8: Run the integration test with a real browser and real ffmpeg**

Run: `uv run pytest tests/test_overlay_render.py -n0 -m integration -v`
Expected: PASS. This is the one that proves the MOV is real ProRes 4444 with a live alpha channel.

- [ ] **Step 9: Mutation drill on the three most load-bearing new tests**

1. In `_clock_filter_graph`, change the ticking window's `lt` to `le`. Expected: `test_the_clock_is_three_drawtext_filters` FAILS.
2. In `_rasterizer_for`, catch `RasterizerUnavailableError` and `yield` a no-op rasterizer instead of raising. Expected: `test_a_missing_browser_fails_the_render_with_the_install_hint` FAILS.
3. In the pipe loop, change `for _ in range(run.frame_count)` to write once per run. Expected: `test_render_overlay_pipes_rgba_frames_and_writes_output` FAILS on the byte count -- the untouched test that guards the frame-for-frame contract.

Revert all three.

- [ ] **Step 10: Lint and commit**

```bash
uv run black --check . && uv run ruff check .
git add src/splitsmith/overlay_render.py tests/test_overlay_render.py tests/test_overlay_text.py
git commit -m "feat(overlay): the single-shooter export joins the overlay engine

Closes the render half of #684. build_frame_states is run-length encoded
into ~31 runs per stage, each rasterized once through the same
declaration/CSS/Chromium path the grid uses, and piped once per frame it
spans. The clock becomes three enable-gated drawtext filters.

Measured on the dev host at 1920x1080: 12.78s/stage of per-frame PIL
becomes 4.33s of 31 sprite renders plus browser startup.

The PIL template, the split fade and _format_running_total are gone,
along with the six tests describing them. Five font tests move to
test_overlay_text.py, where the functions they cover actually live."
```

---

### Task 6: CLI, and the theme tests

**Files:**
- Modify: `src/splitsmith/cli.py` (the `overlay` command, 1124-1189)
- Modify: `tests/test_overlay_theme.py`

**Interfaces:**
- Consumes: `render_overlay`'s new signature from Task 5.
- Produces: no new API.

- [ ] **Step 1: Remove `--font` from the CLI**

In `src/splitsmith/cli.py`, delete the `font_name` typer option (1154-1156) and the `font_name=font_name,` argument (1184).

Restate the two cap flags' help so they describe what they now trade:

```python
    max_height: int | None = typer.Option(
        None,
        "--max-height",
        help=(
            "Cap output height (aspect preserved). Smaller files and a "
            "cheaper render. FCPXML emits a separate format so FCP scales "
            "it back up."
        ),
    ),
    max_fps: float | None = typer.Option(
        None,
        "--max-fps",
        help=(
            "Cap output frame rate. Source rate kept when below cap. "
            "Trades file size and encode time -- overlay content is drawn "
            "per shot, not per frame, so this no longer changes how much "
            "gets drawn."
        ),
    ),
```

- [ ] **Step 2: Verify the CLI still works end to end**

```bash
uv run splitsmith overlay --help
```
Expected: no `--font` in the output; `--max-height` and `--max-fps` show the new text.

```bash
uv run python scripts/render_overlay_frames.py --out build/overlay-frames-branch
```
Expected: six frames, no traceback. This is also the "after" half of Task 7.

- [ ] **Step 3: Re-express `test_overlay_theme.py`'s DefaultTemplate assertions**

Five call sites (94, 141, 171, 179, 193) construct `overlay_render.DefaultTemplate` to assert a theme's palette reaches the drawn pixels. That class is gone, but the property still matters and nothing else checks it.

Read each test first and preserve what it asserts. The theme now reaches the picture through `single_html`'s CSS, so re-express each against that, in `tests/test_overlay_html.py`'s established style -- `_rule(html, selector)` plus a substring check on the colour:

```python
def test_the_theme_ink_reaches_the_rendered_css() -> None:
    theme = load_theme("splitsmith")
    doc = single_html(
        (Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW,
               elements=(Element(text="7/32", role=Role.LIVE_PRIMARY),)),),
        width=1920, height=1080, scale=CellScale.for_cell(1080), theme=theme,
    )
    red, green, blue = theme.ink
    assert f"rgb({red},{green},{blue})" in doc


def test_the_two_themes_do_not_render_the_same_ink() -> None:
    """The property the DefaultTemplate tests were really guarding: that
    --theme is not decoration. If both themes produced the same CSS the
    flag would be a lie, and no other test would notice."""
    groups = (Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW,
                    elements=(Element(text="7/32", role=Role.LIVE_PRIMARY),)),)
    kwargs = dict(width=1920, height=1080, scale=CellScale.for_cell(1080))
    assert single_html(groups, theme=load_theme("splitsmith"), **kwargs) != single_html(
        groups, theme=load_theme("clean"), **kwargs
    )
```

Repoint `test_available_font_names_includes_bundled_presets` (198-202) from `overlay_render.available_font_names` to `overlay_text.available_font_names`.

- [ ] **Step 4: Run the full suite**

```bash
uv run pytest -q
```

Expected: green, with the count up by the net of this plan's additions and deletions. The baseline before this branch was `2861 passed, 21 skipped`. Any *failure* is a finding -- report it rather than adjusting a test.

```bash
uv run pytest -q -m integration
```
Expected: green, `0 skipped`. CI runs this with `SPLITSMITH_REQUIRE_INTEGRATION=1`, which turns a skip into a failure.

- [ ] **Step 5: Lint and commit**

```bash
uv run black --check . && uv run ruff check .
git add src/splitsmith/cli.py tests/test_overlay_theme.py tests/test_overlay_html.py
git commit -m "feat(cli): drop the inert --font flag from the overlay command

Both halves of the overlay draw in the bundled JetBrains Mono -- the
sprite through overlay_html's @font-face rules, the clock through
theme_font_face -- so --font changed nothing. A flag that does nothing
is worse than no flag.

test_overlay_theme.py's DefaultTemplate assertions are re-expressed
against single_html's CSS, which is where a theme now reaches the
picture."
```

---

### Task 7: Before/after review, and the seam pass

Nothing here changes code unless it finds something. This is the task that catches what the other six missed.

**Files:**
- Modify: whatever the review turns up.

- [ ] **Step 1: Diff the control against the branch**

```bash
uv run python -c "
from PIL import Image, ImageChops
from pathlib import Path
for a in sorted(Path('build/overlay-frames-main').glob('*.png')):
    b = Path('build/overlay-frames-branch') / a.name
    if not b.exists():
        print(f'{a.name}: MISSING on branch'); continue
    ia, ib = Image.open(a).convert('RGB'), Image.open(b).convert('RGB')
    d = ImageChops.difference(ia, ib)
    hist = d.convert('L').histogram()
    total = sum(hist)
    above = sum(hist[60:])
    print(f'{a.name:20s} max_delta={max(i for i,c in enumerate(hist) if c) :3d}  px_above_60={above:7d} ({above/total*100:.2f}%)')
"
```

Read the numbers against section 4 of the spec. Expected changes: a narrower stroke on the counter and split, a differently-shaped shadow, the split label one `pad` higher, and on `tail-end` a split that is now present where it used to have faded out.

**One nuance section 4 states too broadly:** it says the stroke narrows 4px -> 2px, which is true of the *sprite* (CSS reads `CellScale.stroke_width`, 2px at 1080p) but **not** of the clock. `overlay_clock.border_width(77)` is `max(2, 77 // 18)` = 4, exactly what the PIL template used. So the clock's outline should look unchanged while the counter's and split's get finer. A clock stroke that also moved is a finding.

Anything else is a finding.

**Do not treat a small non-zero delta on a frame that should be identical as a regression without checking.** During #693 a 7% pixel difference on a frame drawing nothing turned out to be x264 reallocating bits across a GOP whose other frames changed. Max delta 40, zero pixels above 60: sub-noise.

- [ ] **Step 2: Publish the frames for review**

gaspode is headless -- local files do not reach the user. Build a side-by-side HTML page of every moment, before and after, and publish it as an Artifact. Include the numeric diff table from Step 1 on the same page.

- [ ] **Step 3: Measure the real cost**

```bash
uv run python -c "
import json, time
from pathlib import Path
from splitsmith import overlay_render
from splitsmith.config import VideoMetadata

work = Path('build/overlay-timing')
work.mkdir(parents=True, exist_ok=True)
audit = work / 'stage1.json'
# 30 shots at Production Optics pace over a 20s stage, beep at 1s.
shots = [1100 + int(220 * i) for i in range(30)]
audit.write_text(json.dumps({'shots': [{'shot_number': i + 1, 'ms_after_beep': ms}
                                       for i, ms in enumerate(shots)]}), encoding='utf-8')
probe = VideoMetadata(
    width=1920, height=1080, frame_rate_num=30, frame_rate_den=1, duration_seconds=20.0
)
start = time.perf_counter()
overlay_render.render_overlay(
    audit_path=audit,
    trimmed_video_path=work / 'unused.mp4',
    output_path=work / 'overlay.mov',
    beep_offset_seconds=1.0,
    probe=probe,
    codec='prores-4444',
)
print(f'end to end, 1920x1080, 20s, 30 shots: {time.perf_counter() - start:.2f}s')
"
```

`VideoMetadata`'s constructor arguments must match `src/splitsmith/config.py` -- read it and adjust if the field names differ from the five above.

Report the number. The spec's section 7.4 records 12.78s -> 4.33s for the *drawing* alone; this is the end-to-end figure including encode, which neither measurement covered. Add it to that section alongside the existing table, and delete `build/overlay-timing` afterwards.

- [ ] **Step 4: One whole-branch pass over the seams**

Read the complete diff (`git diff main...HEAD`) in one sitting, looking specifically at what no single task owned:

- Does anything still import a name that Task 5 deleted? `uv run ruff check .` catches unused imports but not a stale reference inside a docstring or an f-string.
- `grep -rn "DefaultTemplate\|_split_alpha\|_format_running_total\|font_name" src/ docs/ --include=*.py --include=*.md` -- every surviving hit should be either in `overlay_text.py` (which legitimately still has `font_name`) or a historical plan document.
- Do the docstrings still describe the code? `overlay_render.py`'s module docstring, `overlay_layout.CellScale`'s class docstring (it names `overlay_sprites.render_state`, which no longer exists), and `overlay_html.cell_html`'s "a future single-shooter port" -- that port now exists and the docstring should say so.
- Is the `#684` issue number cited in each new module's docstring?

- [ ] **Step 5: Confirm the whole suite one final time**

```bash
uv run pytest -q
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest -q -m integration
uv run black --check . && uv run ruff check .
```

Expected: all green, zero integration skips.

- [ ] **Step 6: Commit anything the pass turned up**

```bash
git add -A
git commit -m "docs(overlay): reconcile docstrings with the ported renderer"
```

---

## Out of scope, filed rather than fixed

Two things this work touches and deliberately does not change. Open an issue for each rather than letting them ride:

1. **The SPA's overlay codec options are wrong.** `ui_static/src/pages/Export.tsx:801-805` offers `h264` and `prores422`; `OVERLAY_CODECS` is `auto` / `hevc-alpha` / `prores-4444`. Pre-existing, unrelated to this port, and a user picking either non-auto value today is sending a codec the backend does not know.
2. **`test_the_clock_expression_is_character_for_character_what_it_is_today`'s docstring is wrong.** It claims the two argv fingerprint tests fail on drift; both build commands without `overlay=`, so neither hashes any drawtext text. The test itself is fine -- only its stated justification is false.
