# Compare Grid MP4 (Phase 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a merged match's N shooters as one beep-aligned grid MP4 with per-shooter audio tracks, drivable from the CLI and from a local-mode page in the app.

**Architecture:** A new `compare/mp4_grid.py` sits beside the existing `compare/emitter.py`, both fed by the `project_loader` and `layout` modules the compare package already has. One ffmpeg call per stage (`scale` + `pad` + `xstack` + N audio maps), then a `concat`-demuxer stitch with `-c copy`. No Composition IR involvement, no overlay, no transitions, no PIL.

**Tech Stack:** Python 3.11+, Pydantic, Typer, ffmpeg, FastAPI, React + TypeScript, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-04-compare-grid-mp4-and-export-redesign-design.md` (Phase 0 section).

## Global Constraints

- Python 3.11+, type hints everywhere. Black line length 110. Ruff clean.
- `uv` for dependency management, never `pip`. Adding a well-established dependency is allowed and preferred over hand-rolling something a mature library already does well; reuse what the codebase already has before either.
- `pathlib.Path` for paths, never strings. f-strings for formatting.
- Imports grouped stdlib / third-party / local, separated by blank lines. No relative imports beyond a single dot.
- Pydantic models for data crossing module boundaries.
- Detection logic stays out of the CLI; `cli.py` orchestrates only.
- Command construction is pure functions with an injectable `Runner`, mirroring `mp4_render.py` and `trim.py`. Unit tests must not shell out to ffmpeg.
- Real-ffmpeg tests are marked `@pytest.mark.integration`.
- Default canvas is 3840x2160. Every tile targets 1920x1080 in a 2x2.
- Phase 0 renders MP4 only. No overlay, no transitions, no title cards, no hosted-mode storage, no FCPXML on the new UI surface.
- The FCPXML grid (`compare/emitter.py`) and `overlay_render.py` are **not modified by any task in this plan**.

## Critical invariant: uniform stream layout

`concat` with `-c copy` refuses segments whose stream layout differs. Every per-stage segment this renderer produces MUST have:

- exactly 1 video stream, at the canvas size and the pinned frame rate
- exactly N audio streams, N = roster size, in stable alphabetical-label order

A shooter missing a stage's trim therefore contributes a **black video tile AND a silent audio track**, never zero streams. Getting this wrong produces a stitch failure at the very end of a long render.

---

### Task 1: Grid shape helper + stage planning

**Model: Opus.** The beep-alignment and duration math is where silent errors live.

**Files:**
- Modify: `src/splitsmith/compare/layout.py` (add public `grid_shape`)
- Create: `src/splitsmith/compare/mp4_grid.py`
- Test: `tests/test_compare_mp4_grid_plan.py`

**Interfaces:**
- Consumes: `compare.layout.choose_grid`, `compare.project_loader.CompareShooterBundle`, `CompareStageBundle`
- Produces:
  - `layout.grid_shape(kind: GridKind) -> tuple[int, int]` returning `(rows, cols)`
  - `mp4_grid.GridCanvas(width: int, height: int, frame_rate_num: int, frame_rate_den: int)`
  - `mp4_grid.GridTile(label: str, trim_path: Path | None, beep_offset_in_clip: float, seek_seconds: float, row: int, col: int)`
  - `mp4_grid.GridStagePlan(stage_number: int, stage_name: str, tiles: tuple[GridTile, ...], duration_seconds: float, audio_label: str, rows: int, cols: int)`
  - `mp4_grid.build_stage_plans(shooters, *, audio_label, head_pad_seconds, tail_pad_seconds, layout_2up="horizontal") -> tuple[GridStagePlan, ...]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compare_mp4_grid_plan.py
from pathlib import Path

import pytest

from splitsmith.compare import mp4_grid
from splitsmith.compare.layout import grid_shape
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle


def _stage(n: int, *, trim: Path, beep: float, duration: float) -> CompareStageBundle:
    return CompareStageBundle(
        stage_number=n,
        stage_name=f"Stage {n}",
        trim_path=trim,
        audit_path=Path("/nonexistent.json"),
        beep_offset_in_clip=beep,
        duration_seconds=duration,
        width=1920,
        height=1080,
        frame_rate_num=30000,
        frame_rate_den=1001,
    )


def _bundle(label: str, stages: dict[int, CompareStageBundle]) -> CompareShooterBundle:
    return CompareShooterBundle(
        label=label, project_root=Path(f"/p/{label}"), stages_by_number=stages
    )


def test_grid_shape_returns_rows_cols():
    assert grid_shape("2x2") == (2, 2)
    assert grid_shape("2up-h") == (1, 2)
    assert grid_shape("2up-v") == (2, 1)
    assert grid_shape("1up") == (1, 1)


def test_tiles_are_alphabetical_and_slot_stable_across_stages():
    a = _bundle("Anders", {1: _stage(1, trim=Path("/a1.mp4"), beep=2.0, duration=12.0)})
    m = _bundle(
        "Mathias",
        {
            1: _stage(1, trim=Path("/m1.mp4"), beep=3.0, duration=14.0),
            2: _stage(2, trim=Path("/m2.mp4"), beep=1.0, duration=9.0),
        },
    )

    plans = mp4_grid.build_stage_plans(
        [m, a], audio_label="Mathias", head_pad_seconds=1.0, tail_pad_seconds=0.0
    )

    assert [p.stage_number for p in plans] == [1, 2]
    # Alphabetical, and the same label holds the same (row, col) in both stages.
    assert [t.label for t in plans[0].tiles] == ["Anders", "Mathias"]
    assert [t.label for t in plans[1].tiles] == ["Anders", "Mathias"]
    assert plans[0].tiles[0].row == plans[1].tiles[0].row
    assert plans[0].tiles[0].col == plans[1].tiles[0].col


def test_missing_trim_becomes_a_filler_tile_not_a_dropped_slot():
    a = _bundle("Anders", {1: _stage(1, trim=Path("/a1.mp4"), beep=2.0, duration=12.0)})
    m = _bundle(
        "Mathias",
        {
            1: _stage(1, trim=Path("/m1.mp4"), beep=3.0, duration=14.0),
            2: _stage(2, trim=Path("/m2.mp4"), beep=1.0, duration=9.0),
        },
    )

    plans = mp4_grid.build_stage_plans(
        [m, a], audio_label="Mathias", head_pad_seconds=1.0, tail_pad_seconds=0.0
    )

    stage2 = plans[1]
    anders = next(t for t in stage2.tiles if t.label == "Anders")
    assert anders.trim_path is None
    assert len(stage2.tiles) == 2  # slot kept, not dropped


def test_seek_and_duration_are_beep_aligned():
    # head_pad 1.0: a beep at 3.0 seeks to 2.0; a beep at 0.5 clamps to 0.0.
    early = _bundle("Early", {1: _stage(1, trim=Path("/e.mp4"), beep=0.5, duration=10.0)})
    late = _bundle("Late", {1: _stage(1, trim=Path("/l.mp4"), beep=3.0, duration=14.0)})

    plans = mp4_grid.build_stage_plans(
        [early, late], audio_label="Early", head_pad_seconds=1.0, tail_pad_seconds=0.5
    )

    tiles = {t.label: t for t in plans[0].tiles}
    assert tiles["Late"].seek_seconds == pytest.approx(2.0)
    assert tiles["Early"].seek_seconds == pytest.approx(0.0)
    # Duration spans head pad + the longest post-beep run + tail pad.
    # Late runs 14.0 - 3.0 = 11.0 after its beep; Early runs 9.5.
    assert plans[0].duration_seconds == pytest.approx(1.0 + 11.0 + 0.5)


def test_unknown_audio_label_is_rejected():
    a = _bundle("Anders", {1: _stage(1, trim=Path("/a1.mp4"), beep=2.0, duration=12.0)})
    with pytest.raises(ValueError, match="Nobody"):
        mp4_grid.build_stage_plans(
            [a], audio_label="Nobody", head_pad_seconds=0.0, tail_pad_seconds=0.0
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compare_mp4_grid_plan.py -v`
Expected: FAIL -- `ImportError: cannot import name 'mp4_grid'` and `cannot import name 'grid_shape'`.

- [ ] **Step 3: Add `grid_shape` to `layout.py`**

Add below the existing `_GRID_SHAPE` dict:

```python
def grid_shape(kind: GridKind) -> tuple[int, int]:
    """``(rows, cols)`` for a grid kind.

    Public accessor for ``_GRID_SHAPE`` so renderers can lay out cells
    without reaching into a private module global.
    """
    return _GRID_SHAPE[kind]
```

Add `"grid_shape"` to `layout.py`'s `__all__` if it defines one.

- [ ] **Step 4: Create `mp4_grid.py` with the planning half**

```python
"""Direct-to-MP4 renderer for multi-shooter compare grids.

Sits beside :mod:`splitsmith.compare.emitter` (which emits FCPXML) and
consumes the same ``project_loader`` bundles and ``layout`` grid math.
Renders one ffmpeg call per stage -- scale + pad each tile to a uniform
cell, ``xstack`` them into the grid, map every shooter's audio as its
own output track -- then stitches the per-stage temps with the
``concat`` demuxer at ``-c copy``.

Phase 0 scope: no overlay, no transitions, no title cards. The overlay
lands in phase 1 as pre-rendered sprite PNGs; nothing here should make
that harder.

Determinism / testability: command construction is split into pure
functions (:func:`build_stage_command` / :func:`build_concat_command`)
with an injectable runner, mirroring :mod:`splitsmith.mp4_render` and
:mod:`splitsmith.trim`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .layout import Layout2Up, choose_grid, grid_shape
from .project_loader import CompareShooterBundle

Runner = Callable[..., subprocess.CompletedProcess]

DEFAULT_CANVAS_WIDTH = 3840
DEFAULT_CANVAS_HEIGHT = 2160


class GridRenderError(RuntimeError):
    """ffmpeg refused to render a grid stage or the final stitch."""


@dataclass(frozen=True)
class GridCanvas:
    """Output geometry for the whole render.

    Pinned once and applied to every stage: ``concat -c copy`` rejects
    segments whose video parameters differ.
    """

    width: int = DEFAULT_CANVAS_WIDTH
    height: int = DEFAULT_CANVAS_HEIGHT
    frame_rate_num: int = 30000
    frame_rate_den: int = 1001

    @property
    def fps(self) -> float:
        return self.frame_rate_num / self.frame_rate_den


@dataclass(frozen=True)
class GridTile:
    """One shooter's cell in one stage.

    ``trim_path=None`` means the shooter has no trim for this stage: the
    cell renders black and contributes a silent audio track. The slot is
    never dropped -- doing so would shuffle the grid between stages and
    change the stream count, which breaks the concat stitch.
    """

    label: str
    trim_path: Path | None
    beep_offset_in_clip: float
    seek_seconds: float
    row: int
    col: int


@dataclass(frozen=True)
class GridStagePlan:
    """Everything one ffmpeg invocation needs for one stage."""

    stage_number: int
    stage_name: str
    tiles: tuple[GridTile, ...]
    duration_seconds: float
    audio_label: str
    rows: int
    cols: int


def build_stage_plans(
    shooters: Sequence[CompareShooterBundle],
    *,
    audio_label: str,
    head_pad_seconds: float,
    tail_pad_seconds: float,
    layout_2up: Layout2Up = "horizontal",
) -> tuple[GridStagePlan, ...]:
    """Plan one grid stage per stage number present on any shooter.

    Slots are alphabetical by label and stable across stages, matching
    ``compare/emitter.py``'s rule: a label always lands in the same cell
    and a missing trim becomes filler rather than reshuffling the grid.
    """
    labels = sorted(s.label for s in shooters)
    if audio_label not in labels:
        raise ValueError(
            f"audio_label={audio_label!r} matches no shooter. Labels: {', '.join(labels)}"
        )

    by_label = {s.label: s for s in shooters}
    rows, cols = grid_shape(choose_grid(len(labels), layout_2up=layout_2up))

    stage_numbers = sorted({n for s in shooters for n in s.stages_by_number})

    plans: list[GridStagePlan] = []
    for stage_number in stage_numbers:
        tiles: list[GridTile] = []
        post_beep_spans: list[float] = []
        stage_name = ""
        for index, label in enumerate(labels):
            bundle = by_label[label].stages_by_number.get(stage_number)
            row, col = divmod(index, cols)
            if bundle is None:
                tiles.append(
                    GridTile(
                        label=label,
                        trim_path=None,
                        beep_offset_in_clip=0.0,
                        seek_seconds=0.0,
                        row=row,
                        col=col,
                    )
                )
                continue
            stage_name = stage_name or bundle.stage_name
            post_beep_spans.append(bundle.duration_seconds - bundle.beep_offset_in_clip)
            tiles.append(
                GridTile(
                    label=label,
                    trim_path=bundle.trim_path,
                    beep_offset_in_clip=bundle.beep_offset_in_clip,
                    seek_seconds=max(0.0, bundle.beep_offset_in_clip - head_pad_seconds),
                    row=row,
                    col=col,
                )
            )

        duration = head_pad_seconds + max(post_beep_spans, default=0.0) + tail_pad_seconds
        plans.append(
            GridStagePlan(
                stage_number=stage_number,
                stage_name=stage_name or f"Stage {stage_number}",
                tiles=tuple(tiles),
                duration_seconds=duration,
                audio_label=audio_label,
                rows=rows,
                cols=cols,
            )
        )
    return tuple(plans)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_compare_mp4_grid_plan.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Lint and format**

Run: `uv run ruff check src/splitsmith/compare/ tests/ && uv run black --line-length 110 src/splitsmith/compare/ tests/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/compare/layout.py src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_plan.py
git commit -m "feat(compare): plan beep-aligned grid stages for MP4 render"
```

---

### Task 2: ffmpeg command construction

**Model: Opus.** The filter graph and the stream-layout invariant are the whole deliverable.

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py`
- Test: `tests/test_compare_mp4_grid_commands.py`

**Interfaces:**
- Consumes: `GridStagePlan`, `GridTile`, `GridCanvas` from Task 1 — including `GridTile.lead_pad_seconds`

**Lead pad (added after Task 1's review).** A tile whose beep sits closer to its clip start than `head_pad_seconds` cannot supply the full head pad, so `seek_seconds` clamps to 0 and `lead_pad_seconds` carries the shortfall. That shortfall must be synthesised at the front of the tile or its beep lands early and the grid is desynced — the exact failure the grid exists to prevent. For a tile with `lead_pad_seconds > 0`:

- video: append `tpad=start_duration={lead}:start_mode=add:color=black` to that tile's chain
- audio: append `adelay={lead_ms}:all=1` to that tile's audio chain
- the input's `-t` becomes `duration_seconds - lead_pad_seconds`, because the lead pad supplies the remainder

For `lead_pad_seconds == 0.0` (the common case) neither filter is emitted and `-t` is the full stage duration.
- Produces:
  - `mp4_grid.build_stage_command(plan, *, canvas, output_path, ffmpeg_binary="ffmpeg") -> tuple[str, ...]`
  - `mp4_grid.build_concat_command(*, list_path, output_path, ffmpeg_binary="ffmpeg") -> tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compare_mp4_grid_commands.py
from pathlib import Path

from splitsmith.compare import mp4_grid


def _plan(
    *, missing: str | None = None, lead_padded: str | None = None
) -> mp4_grid.GridStagePlan:
    labels = ["Anders", "Erik", "Johan", "Mathias"]
    tiles = []
    for index, label in enumerate(labels):
        row, col = divmod(index, 2)
        tiles.append(
            mp4_grid.GridTile(
                label=label,
                trim_path=None if label == missing else Path(f"/trims/{label}.mp4"),
                beep_offset_in_clip=2.0,
                seek_seconds=1.0,
                lead_pad_seconds=0.5 if label == lead_padded else 0.0,
                row=row,
                col=col,
            )
        )
    return mp4_grid.GridStagePlan(
        stage_number=1,
        stage_name="Stage 1",
        tiles=tuple(tiles),
        duration_seconds=12.5,
        audio_label="Mathias",
        rows=2,
        cols=2,
    )


def _graph(cmd: tuple[str, ...]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def test_every_tile_is_scaled_padded_and_xstacked():
    cmd = mp4_grid.build_stage_command(
        _plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/out/stage1.mp4")
    )
    graph = _graph(cmd)
    # 3840x2160 canvas, 2x2 -> 1920x1080 cells.
    assert graph.count("scale=1920:1080:force_original_aspect_ratio=decrease") == 4
    assert graph.count("pad=1920:1080") == 4
    assert "xstack=inputs=4:layout=0_0|1920_0|0_1080|1920_1080" in graph


def test_audio_track_per_shooter_in_alphabetical_order():
    cmd = mp4_grid.build_stage_command(
        _plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/out/stage1.mp4")
    )
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    # One composited video, then one audio label per shooter.
    assert maps == ["[final]", "[a0]", "[a1]", "[a2]", "[a3]"]
    # Mathias is index 3 alphabetically and is the audio source.
    assert "-disposition:a:3" in cmd
    assert cmd[cmd.index("-disposition:a:3") + 1] == "default"


def test_missing_trim_still_contributes_video_and_a_silent_audio_track():
    cmd = mp4_grid.build_stage_command(
        _plan(missing="Erik"), canvas=mp4_grid.GridCanvas(), output_path=Path("/out/s.mp4")
    )
    joined = " ".join(cmd)
    assert "color=c=black:s=1920x1080" in joined
    assert "anullsrc" in joined
    # The stream layout must not change: still four audio maps.
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == ["[final]", "[a0]", "[a1]", "[a2]", "[a3]"]
    # And Erik's trim is not an input.
    assert "/trims/Erik.mp4" not in joined


def test_seek_and_duration_are_applied_before_each_input():
    cmd = mp4_grid.build_stage_command(
        _plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/out/s.mp4")
    )
    first_input = cmd.index("-i")
    assert cmd[first_input - 4] == "-ss"
    assert cmd[first_input - 3] == "1"
    assert cmd[first_input - 2] == "-t"
    assert cmd[first_input - 1] == "12.5"


def test_output_frame_rate_is_pinned_for_concat_compatibility():
    canvas = mp4_grid.GridCanvas(frame_rate_num=30000, frame_rate_den=1001)
    cmd = mp4_grid.build_stage_command(
        _plan(), canvas=canvas, output_path=Path("/out/s.mp4")
    )
    assert "-r" in cmd
    assert cmd[cmd.index("-r") + 1] == "30000/1001"


def test_a_lead_padded_tile_is_front_padded_so_its_beep_still_lands_on_time():
    # Erik's beep sits closer to his clip start than head_pad, so 0.5s of
    # pad has to be synthesised. Without it his beep lands 0.5s early and
    # the grid is desynced -- the failure the grid exists to prevent.
    cmd = mp4_grid.build_stage_command(
        _plan(lead_padded="Erik"), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4")
    )
    graph = _graph(cmd)
    assert "tpad=start_duration=0.5:start_mode=add:color=black" in graph
    assert "adelay=500:all=1" in graph
    # Only Erik is padded; the other three tiles are untouched.
    assert graph.count("tpad=start_duration") == 1
    assert graph.count("adelay=") == 1
    # Erik's input reads 0.5s less, since the pad supplies the remainder.
    joined = " ".join(cmd)
    assert "-t 12 -i /trims/Erik.mp4" in joined
    assert "-t 12.5 -i /trims/Anders.mp4" in joined


def test_tiles_without_a_lead_pad_emit_no_padding_filters():
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan(), canvas=mp4_grid.GridCanvas(), output_path=Path("/o.mp4")
        )
    )
    assert "tpad=" not in graph
    assert "adelay=" not in graph


def test_concat_command_stream_copies():
    cmd = mp4_grid.build_concat_command(
        list_path=Path("/tmp/list.txt"), output_path=Path("/out/grid.mp4")
    )
    assert cmd[-1] == "/out/grid.mp4"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert "concat" in cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compare_mp4_grid_commands.py -v`
Expected: FAIL -- `AttributeError: module 'splitsmith.compare.mp4_grid' has no attribute 'build_stage_command'`.

- [ ] **Step 3: Implement the command builders**

Append to `src/splitsmith/compare/mp4_grid.py`:

```python
def _cell_size(canvas: GridCanvas, plan: GridStagePlan) -> tuple[int, int]:
    """Uniform cell geometry. Integer division keeps the xstack offsets exact."""
    return canvas.width // plan.cols, canvas.height // plan.rows


def build_stage_command(
    plan: GridStagePlan,
    *,
    canvas: GridCanvas,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
) -> tuple[str, ...]:
    """Build the ffmpeg invocation rendering one grid stage.

    Stream layout is fixed at one video plus one audio track per tile,
    in alphabetical label order, regardless of which shooters actually
    have a trim for this stage. ``concat -c copy`` rejects segments
    whose stream layout differs, so a missing tile contributes a black
    ``color`` source and a silent ``anullsrc`` track rather than
    nothing at all.
    """
    cell_w, cell_h = _cell_size(canvas, plan)
    rate = f"{canvas.frame_rate_num}/{canvas.frame_rate_den}"

    args: list[str] = [ffmpeg_binary, "-hide_banner", "-y"]
    video_index: list[int] = []
    audio_index: list[int] = []
    next_index = 0

    for tile in plan.tiles:
        if tile.trim_path is not None:
            # Seek before -i so ffmpeg fast-seeks; the trim's head buffer
            # absorbs any imprecision, same trade-off as trim.py.
            # A lead-padded tile reads that much less from its source: the
            # synthesised pad at the front supplies the remainder, so the
            # tile still totals ``duration_seconds``.
            args += [
                "-ss",
                f"{tile.seek_seconds:g}",
                "-t",
                f"{plan.duration_seconds - tile.lead_pad_seconds:g}",
                "-i",
                str(tile.trim_path),
            ]
            video_index.append(next_index)
            audio_index.append(next_index)
            next_index += 1
        else:
            args += [
                "-f",
                "lavfi",
                "-t",
                f"{plan.duration_seconds:g}",
                "-i",
                f"color=c=black:s={cell_w}x{cell_h}:r={rate}",
            ]
            video_index.append(next_index)
            next_index += 1
            args += [
                "-f",
                "lavfi",
                "-t",
                f"{plan.duration_seconds:g}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
            audio_index.append(next_index)
            next_index += 1

    args += ["-filter_complex", _build_filter_graph(plan, canvas, video_index, audio_index)]

    args += ["-map", "[final]"]
    for slot in range(len(plan.tiles)):
        args += ["-map", f"[a{slot}]"]

    default_slot = next(i for i, t in enumerate(plan.tiles) if t.label == plan.audio_label)
    for slot in range(len(plan.tiles)):
        args += [
            f"-disposition:a:{slot}",
            "default" if slot == default_slot else "0",
        ]
    for slot, tile in enumerate(plan.tiles):
        args += [f"-metadata:s:a:{slot}", f"title={tile.label}"]

    args += [
        "-r",
        rate,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return tuple(args)


def _build_filter_graph(
    plan: GridStagePlan,
    canvas: GridCanvas,
    video_index: list[int],
    audio_index: list[int],
) -> str:
    """Scale + pad every tile to a uniform cell, then ``xstack`` the grid.

    ``force_original_aspect_ratio=decrease`` plus ``pad`` letterboxes
    each source into its cell, so mixed aspect ratios and mixed source
    resolutions both land correctly. ``setsar=1`` is required or
    ``xstack`` refuses inputs whose sample aspect ratios disagree.
    """
    cell_w, cell_h = _cell_size(canvas, plan)
    rate = f"{canvas.frame_rate_num}/{canvas.frame_rate_den}"
    parts: list[str] = []

    for slot, tile in enumerate(plan.tiles):
        # ``tpad`` goes after ``pad`` so the synthesised black is already
        # cell-sized, and before ``fps`` so the padded frames are counted
        # at the output rate.
        lead = (
            f"tpad=start_duration={tile.lead_pad_seconds:g}:start_mode=add:color=black,"
            if tile.lead_pad_seconds > 0
            else ""
        )
        parts.append(
            f"[{video_index[slot]}:v]setpts=PTS-STARTPTS,"
            f"scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
            f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2,"
            f"{lead}setsar=1,fps={rate}[t{slot}]"
        )

    stack_inputs = "".join(f"[t{slot}]" for slot in range(len(plan.tiles)))
    offsets = "|".join(
        f"{tile.col * cell_w}_{tile.row * cell_h}" for tile in plan.tiles
    )
    parts.append(
        f"{stack_inputs}xstack=inputs={len(plan.tiles)}:layout={offsets}[grid]"
    )
    parts.append("[grid]format=yuv420p[final]")

    for slot, tile in enumerate(plan.tiles):
        # ``aresample=async=1`` keeps a track that starts short from
        # drifting; ``apad`` + ``atrim`` guarantee every track is exactly
        # the stage duration so the segment's streams end together.
        # ``adelay`` mirrors the video's ``tpad`` so a lead-padded tile's
        # audio stays locked to its picture.
        delay_ms = int(round(tile.lead_pad_seconds * 1000))
        lead = f"adelay={delay_ms}:all=1," if delay_ms > 0 else ""
        parts.append(
            f"[{audio_index[slot]}:a]asetpts=PTS-STARTPTS,{lead}aresample=async=1,"
            f"apad,atrim=0:{plan.duration_seconds:g}[a{slot}]"
        )

    return ";".join(parts)


def build_concat_command(
    *,
    list_path: Path,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
) -> tuple[str, ...]:
    """Stitch the per-stage temps without re-encoding."""
    return (
        ffmpeg_binary,
        "-hide_banner",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compare_mp4_grid_commands.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the whole compare suite for regressions**

Run: `uv run pytest tests/test_compare_*.py -v`
Expected: PASS, including the pre-existing FCPXML emitter tests (this task must not touch them).

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_commands.py
git commit -m "feat(compare): build ffmpeg xstack grid + per-shooter audio tracks"
```

---

### Task 3: Render driver

**Model: Opus.** Per-stage failure isolation is the behaviour the spec calls out explicitly.

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py`
- Test: `tests/test_compare_mp4_grid_render.py`

**Interfaces:**
- Consumes: `build_stage_plans`, `build_stage_command`, `build_concat_command`, `GridCanvas`
- Produces:
  - `mp4_grid.StageOutcome(stage_number: int, stage_name: str, ok: bool, error: str | None)`
  - `mp4_grid.GridRenderResult(output_path: Path, stages: tuple[StageOutcome, ...])` with `.failed` property
  - `mp4_grid.render_grid_mp4(shooters, *, audio_label, output_path, canvas=GridCanvas(), head_pad_seconds=1.0, tail_pad_seconds=0.5, layout_2up="horizontal", ffmpeg_binary="ffmpeg", runner=subprocess.run, work_dir=None) -> GridRenderResult`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compare_mp4_grid_render.py
import subprocess
from pathlib import Path

import pytest

from splitsmith.compare import mp4_grid
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle


def _stage(n: int) -> CompareStageBundle:
    return CompareStageBundle(
        stage_number=n,
        stage_name=f"Stage {n}",
        trim_path=Path(f"/trims/s{n}.mp4"),
        audit_path=Path("/nonexistent.json"),
        beep_offset_in_clip=2.0,
        duration_seconds=12.0,
        width=1920,
        height=1080,
        frame_rate_num=30000,
        frame_rate_den=1001,
    )


def _shooters() -> list[CompareShooterBundle]:
    return [
        CompareShooterBundle(
            label=label, project_root=Path(f"/p/{label}"), stages_by_number={1: _stage(1), 2: _stage(2)}
        )
        for label in ("Anders", "Mathias")
    ]


def test_renders_each_stage_then_concats(tmp_path):
    calls: list[tuple[str, ...]] = []

    def runner(cmd, **kwargs):
        calls.append(tuple(cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    result = mp4_grid.render_grid_mp4(
        _shooters(),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        runner=runner,
        work_dir=tmp_path / "work",
    )

    assert len(calls) == 3  # two stages + one concat
    assert calls[-1][calls[-1].index("-f") + 1] == "concat"
    assert all(s.ok for s in result.stages)
    assert result.failed == ()


def test_a_failing_stage_does_not_abort_the_run(tmp_path):
    def runner(cmd, **kwargs):
        if "/work/stage2.mp4" in " ".join(str(c) for c in cmd).replace(str(tmp_path), ""):
            return subprocess.CompletedProcess(cmd, 1, b"", b"boom")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    result = mp4_grid.render_grid_mp4(
        _shooters(),
        audio_label="Mathias",
        output_path=tmp_path / "grid.mp4",
        runner=runner,
        work_dir=tmp_path / "work",
    )

    failed = result.failed
    assert [s.stage_number for s in failed] == [2]
    assert "boom" in failed[0].error
    # Stage 1 still made it into the stitch.
    assert any(s.ok for s in result.stages)


def test_all_stages_failing_raises_rather_than_concatenating_nothing(tmp_path):
    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, b"", b"nope")

    with pytest.raises(mp4_grid.GridRenderError, match="every stage failed"):
        mp4_grid.render_grid_mp4(
            _shooters(),
            audio_label="Mathias",
            output_path=tmp_path / "grid.mp4",
            runner=runner,
            work_dir=tmp_path / "work",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compare_mp4_grid_render.py -v`
Expected: FAIL -- `AttributeError: ... has no attribute 'render_grid_mp4'`.

- [ ] **Step 3: Implement the driver**

Append to `src/splitsmith/compare/mp4_grid.py`:

```python
@dataclass(frozen=True)
class StageOutcome:
    """What happened to one stage of a grid render."""

    stage_number: int
    stage_name: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class GridRenderResult:
    """Result of a whole grid render, including partial failures."""

    output_path: Path
    stages: tuple[StageOutcome, ...]

    @property
    def failed(self) -> tuple[StageOutcome, ...]:
        return tuple(s for s in self.stages if not s.ok)


def render_grid_mp4(
    shooters: Sequence[CompareShooterBundle],
    *,
    audio_label: str,
    output_path: Path,
    canvas: GridCanvas | None = None,
    head_pad_seconds: float = 1.0,
    tail_pad_seconds: float = 0.5,
    layout_2up: Layout2Up = "horizontal",
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
    work_dir: Path | None = None,
) -> GridRenderResult:
    """Render every stage as a grid, then stitch them into one MP4.

    A stage whose ffmpeg call fails is recorded and skipped rather than
    ending the run: a full-match grid re-encode is far too long to lose
    to one bad stage. The stitch runs over whatever succeeded, and the
    caller reports failures from :attr:`GridRenderResult.failed`. Only
    when *every* stage fails does this raise -- there is nothing to
    concatenate and a zero-byte output would be worse than an error.
    """
    canvas = canvas or GridCanvas()
    plans = build_stage_plans(
        shooters,
        audio_label=audio_label,
        head_pad_seconds=head_pad_seconds,
        tail_pad_seconds=tail_pad_seconds,
        layout_2up=layout_2up,
    )
    if not plans:
        raise GridRenderError("no stages to render -- no shooter has an exported trim")

    work = work_dir or output_path.parent / ".compare-grid-work"
    work.mkdir(parents=True, exist_ok=True)

    outcomes: list[StageOutcome] = []
    segments: list[Path] = []
    for plan in plans:
        segment = work / f"stage{plan.stage_number}.mp4"
        cmd = build_stage_command(
            plan, canvas=canvas, output_path=segment, ffmpeg_binary=ffmpeg_binary
        )
        completed = runner(cmd, capture_output=True)
        if completed.returncode != 0:
            stderr = completed.stderr
            detail = stderr.decode(errors="replace") if isinstance(stderr, bytes) else str(stderr)
            outcomes.append(
                StageOutcome(
                    stage_number=plan.stage_number,
                    stage_name=plan.stage_name,
                    ok=False,
                    error=detail.strip()[-2000:],
                )
            )
            continue
        segments.append(segment)
        outcomes.append(
            StageOutcome(stage_number=plan.stage_number, stage_name=plan.stage_name, ok=True)
        )

    if not segments:
        raise GridRenderError(
            f"every stage failed to render ({len(outcomes)} attempted); nothing to stitch"
        )

    list_path = work / "concat.txt"
    list_path.write_text(
        "".join(f"file '{segment}'\n" for segment in segments), encoding="utf-8"
    )
    concat_cmd = build_concat_command(
        list_path=list_path, output_path=output_path, ffmpeg_binary=ffmpeg_binary
    )
    completed = runner(concat_cmd, capture_output=True)
    if completed.returncode != 0:
        stderr = completed.stderr
        detail = stderr.decode(errors="replace") if isinstance(stderr, bytes) else str(stderr)
        raise GridRenderError(f"concat stitch failed: {detail.strip()[-2000:]}")

    return GridRenderResult(output_path=output_path, stages=tuple(outcomes))


__all__ = [
    "DEFAULT_CANVAS_HEIGHT",
    "DEFAULT_CANVAS_WIDTH",
    "GridCanvas",
    "GridRenderError",
    "GridRenderResult",
    "GridStagePlan",
    "GridTile",
    "StageOutcome",
    "build_concat_command",
    "build_stage_command",
    "build_stage_plans",
    "render_grid_mp4",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compare_mp4_grid_render.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_render.py
git commit -m "feat(compare): drive grid MP4 render with per-stage failure isolation"
```

---

### Task 4: CLI `--format mp4`

**Model: Sonnet.** Follows the existing `compare/cli.py` structure once Task 3's signature exists.

**Files:**
- Modify: `src/splitsmith/compare/cli.py` (the `export` command, `src/splitsmith/compare/cli.py:27-134`, and `_export_from_match`, `src/splitsmith/compare/cli.py:219`)
- Test: `tests/test_compare_cli_mp4.py`

**Interfaces:**
- Consumes: `mp4_grid.render_grid_mp4`, `mp4_grid.GridCanvas`
- Produces: `splitsmith compare export <match> --format mp4 --audio-from X -o out.mp4`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compare_cli_mp4.py
from typer.testing import CliRunner

from splitsmith.compare.cli import compare_app

runner = CliRunner()


def test_format_flag_is_documented():
    result = runner.invoke(compare_app, ["export", "--help"])
    assert result.exit_code == 0
    assert "--format" in result.stdout
    assert "mp4" in result.stdout


def test_mp4_format_rejects_a_manifest_source(tmp_path):
    manifest = tmp_path / "m.yaml"
    manifest.write_text("output: out.fcpxml\naudio_from: A\nshooters: []\n", encoding="utf-8")
    result = runner.invoke(
        compare_app, ["export", str(manifest), "--format", "mp4", "-o", str(tmp_path / "o.mp4")]
    )
    assert result.exit_code == 2
    assert "match folder" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compare_cli_mp4.py -v`
Expected: FAIL -- `--format` is not in the help output.

- [ ] **Step 3: Add the flag to the `export` command**

Add this option to `export`'s signature, after `camera`:

```python
    output_format: str = typer.Option(
        "fcpxml",
        "--format",
        help=(
            "Output kind: 'fcpxml' (Final Cut timeline, the default) or "
            "'mp4' (rendered grid video). MP4 requires SOURCE to be a "
            "merged match folder."
        ),
    ),
```

Inside `export`, immediately after the option validation and before the match-folder branch:

```python
    if output_format not in ("fcpxml", "mp4"):
        console.print(f"[red]Error:[/] --format must be 'fcpxml' or 'mp4', got {output_format!r}.")
        raise typer.Exit(code=2)
    if output_format == "mp4" and not (source.is_dir() and is_match_folder(source)):
        console.print(
            "[red]Error:[/] --format mp4 requires SOURCE to be a merged match folder, "
            "not a manifest."
        )
        raise typer.Exit(code=2)
```

Then pass it through the match branch:

```python
        _export_from_match(
            source,
            audio_from=audio_from,
            output=output,
            cameras=cameras,
            output_format=output_format,
        )
```

- [ ] **Step 4: Branch inside `_export_from_match`**

Change the signature to accept `output_format: str = "fcpxml"`, and replace the final emit call with:

```python
    if output_format == "mp4":
        result = mp4_grid.render_grid_mp4(
            shooters,
            audio_label=audio_label,
            output_path=output,
        )
        for outcome in result.failed:
            console.print(
                f"[yellow]Stage {outcome.stage_number} ({outcome.stage_name}) failed:[/] "
                f"{outcome.error}"
            )
        rendered = len(result.stages) - len(result.failed)
        console.print(f"[green]Wrote[/] {output} ({rendered}/{len(result.stages)} stages)")
        return
```

Add `from . import mp4_grid` to the module imports.

**Note for the implementer:** `_export_from_match` currently resolves shooter slugs and loads bundles before calling `emitter_mod.emit_compare_fcpxml`. Read the existing body at `src/splitsmith/compare/cli.py:219-293` and insert the branch where the bundles list is already built -- do not duplicate the loading logic. The variable holding the audio shooter's label may be named differently there; use whatever the existing emit call passes as the audio source.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_compare_cli_mp4.py -v && uv run pytest tests/test_compare_*.py -v`
Expected: PASS, existing FCPXML CLI tests unaffected.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/compare/cli.py tests/test_compare_cli_mp4.py
git commit -m "feat(compare): add --format mp4 to compare export"
```

---

### Task 5: Match-scoped export endpoint (local mode)

**Model: Sonnet.** Well-trodden path in `ui/server.py`; local-mode-only removes the storage branching.

**Files:**
- Modify: `src/splitsmith/ui/server.py` (request model near `src/splitsmith/ui/server.py:4166`; endpoint near the stage-compare block at `src/splitsmith/ui/server.py:11801`)
- Modify: the job worker that dispatches `kind="export"` (find it by searching for `kind == "export"` in the queue consumer)
- Test: `tests/test_compare_grid_endpoint.py`

**Interfaces:**
- Consumes: `mp4_grid.render_grid_mp4`, `compare.project_loader.load_shooter`, `match_model.Match`
- Produces: `POST /api/match/compare-export` accepting `CompareGridRequest`, returning a `Job` snapshot

**Request model:**

```python
class CompareGridRequest(BaseModel):
    """Body for POST /api/match/compare-export (phase 0).

    Local mode only: the response is a Job snapshot the SPA polls, since
    a full-match grid re-encode runs for minutes.
    """

    stage_numbers: list[int]
    audio_from: str
    cameras: dict[str, str] = Field(default_factory=dict)
    canvas_width: int = 3840
    canvas_height: int = 2160
    output_name: str = "compare-grid"
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compare_grid_endpoint.py
import pytest


def test_rejects_empty_stage_selection(match_client):
    response = match_client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [], "audio_from": "mathias"},
    )
    assert response.status_code == 400
    assert "stage_numbers" in response.json()["detail"]


def test_rejects_unknown_audio_shooter(match_client):
    response = match_client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "nobody"},
    )
    assert response.status_code == 400
    assert "nobody" in response.json()["detail"]


def test_reports_missing_trims_rather_than_rendering_filler_for_everyone(match_client):
    # No shooter has an exported trim in this fixture match.
    response = match_client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "trim" in detail.lower()


def test_queues_a_job_when_trims_exist(match_client_with_trims):
    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "compare-grid"
    assert body["status"] in ("pending", "running")
```

**Note for the implementer:** `match_client` / `match_client_with_trims` do not exist yet -- build them from what is already there rather than inventing a new shape. `tests/test_trims_to_compare_e2e.py` has a `chained_match` fixture (`tests/test_trims_to_compare_e2e.py:118`) that seeds a real merged match under `tmp_path` via a `_seed_shooter` helper, and `tests/test_compare_merged_match.py` has `_stub_probe` / `_stub_ffmpeg_runner` / `_seed_legacy_project`. Reuse those seeding helpers for the match on disk, and find the existing FastAPI `TestClient` fixture that the other `/api/match/...` route tests use for the client half.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compare_grid_endpoint.py -v`
Expected: FAIL with 404 (route does not exist).

- [ ] **Step 3: Add the request model**

Add `CompareGridRequest` (above) next to the other export request models around `src/splitsmith/ui/server.py:4166`.

- [ ] **Step 4: Add the endpoint**

Add near the stage-compare block (`src/splitsmith/ui/server.py:11801`), following `export_match`'s validate-then-queue shape (`src/splitsmith/ui/server.py:10628`):

```python
    @app.post("/api/match/compare-export")
    async def export_compare_grid(req: CompareGridRequest) -> JSONResponse:
        """Render the match's shooters as one grid MP4 (phase 0).

        Local mode only -- no Storage writes, no download deliverables.
        Job-queued: a full-match grid re-encode runs for minutes.

        Validation up-front so the SPA shows a clear error before
        queueing: empty selection, unknown audio shooter, and the
        no-trims-at-all case all 400 rather than producing a grid of
        black tiles.
        """
        match_root, match = _resolve_match_context()
        if not req.stage_numbers:
            raise HTTPException(status_code=400, detail="stage_numbers cannot be empty")
        if req.audio_from not in match.shooters:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"audio_from={req.audio_from!r} matches no shooter on this match. "
                    f"Slugs available: {', '.join(match.shooters)}"
                ),
            )

        bundles = _load_compare_bundles(match_root, match, cameras=req.cameras)
        present = sum(
            1
            for bundle in bundles
            for number in req.stage_numbers
            if number in bundle.stages_by_number
        )
        if present == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "no shooter has an exported trim for the selected stages. "
                    "Run `splitsmith match trims` (or export trims per shooter) first."
                ),
            )

        job = await state.jobs.submit(
            kind="compare-grid",
            args={"req": req, "match_root": str(match_root)},
        )
        return JSONResponse(job.model_dump(mode="json"))
```

Extract `_load_compare_bundles` as a module-level helper that walks `match.shooters`, calls `project_loader.load_shooter` for each with the requested camera, and returns the bundle list. The worker in Step 5 uses the same helper.

- [ ] **Step 5: Handle `kind="compare-grid"` in the job worker**

Find the queue consumer that branches on `kind == "export"` and add a sibling branch that calls `mp4_grid.render_grid_mp4`, writing to `<match_root>/exports/<output_name>.mp4`. The job result must carry the output path and the per-stage outcomes:

```python
{
    "output_path": str(result.output_path),
    "stages_rendered": len(result.stages) - len(result.failed),
    "stages_total": len(result.stages),
    "failed": [
        {"stage_number": s.stage_number, "stage_name": s.stage_name, "error": s.error}
        for s in result.failed
    ],
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_compare_grid_endpoint.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Verify local-mode import isolation**

Run: `uv run pytest tests/ -k local_mode_no_hosted_imports -v`
Expected: PASS. `mp4_grid` must not pull in `splitsmith.db`.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/ui/server.py tests/test_compare_grid_endpoint.py
git commit -m "feat(ui): add match-scoped compare-grid MP4 export endpoint"
```

---

### Task 6: API client method

**Model: Sonnet.** Mechanical, follows `exportMatch` at `src/splitsmith/ui_static/src/lib/api.ts:3178`.

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts`
- Test: `src/splitsmith/ui_static/src/lib/api.compareGrid.test.ts`

**Interfaces:**
- Produces:
  - `CompareGridRequestPayload { stage_numbers: number[]; audio_from: string; cameras?: Record<string,string>; canvas_width?: number; canvas_height?: number; output_name?: string }`
  - `CompareGridResult { output_path: string; stages_rendered: number; stages_total: number; failed: Array<{ stage_number: number; stage_name: string; error: string }> }`
  - `api.exportCompareGrid(payload) => Promise<Job>`

- [ ] **Step 1: Write the failing test**

Follow the existing fetch-mocking idiom in `src/splitsmith/ui_static/src/lib/apiErrors.test.ts`: `vi.spyOn(globalThis, "fetch")` plus `vi.restoreAllMocks()` in `afterEach`. Do not use `vi.stubGlobal`; this suite does not.

```ts
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("api.exportCompareGrid", () => {
  it("posts the selection to the match-scoped endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: "job-1", kind: "compare-grid", status: "pending" }),
    } as unknown as Response);

    const job = await api.exportCompareGrid({
      stage_numbers: [1, 2],
      audio_from: "mathias",
    });

    expect(fetchMock).toHaveBeenCalled();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/api/match/compare-export");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string).stage_numbers).toEqual([1, 2]);
    expect(job.kind).toBe("compare-grid");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/api.compareGrid.test.ts`
Expected: FAIL -- `api.exportCompareGrid is not a function`.

- [ ] **Step 3: Add the types and method**

Add the two interfaces next to `MatchExportResult` (`src/splitsmith/ui_static/src/lib/api.ts:891`), and the method next to `exportMatch` (`src/splitsmith/ui_static/src/lib/api.ts:3178`), copying that method's request helper, error handling and JSDoc style exactly. Do not introduce a different fetch wrapper.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/lib/api.compareGrid.test.ts`
Expected: PASS

- [ ] **Step 5: Typecheck**

Run: `cd src/splitsmith/ui_static && pnpm tsc --noEmit`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/lib/api.compareGrid.test.ts
git commit -m "feat(ui): add exportCompareGrid API client method"
```

---

### Task 7: Match-scoped export page

**Model: Sonnet.** Constrained surface; the design judgement lands in phase 3, not here.

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/MatchExport.tsx`
- Create: `src/splitsmith/ui_static/src/pages/matchExportModel.ts`
- Modify: `src/splitsmith/ui_static/src/App.tsx:195` (the `export` redirect route)
- Test: `src/splitsmith/ui_static/src/pages/matchExportModel.test.ts` (logic)
- Test: `src/splitsmith/ui_static/src/pages/MatchExport.test.tsx` (rendering + interaction)
- Modify: `src/splitsmith/ui_static/package.json` (devDeps), and the vitest config block for the jsdom environment

**Interfaces:**
- Consumes: `api.exportCompareGrid`, `api.pollJob`, `api.revealFile`, `api.getMatch` (or the existing match-context hook used by `Compare.tsx`)
- Produces (in `matchExportModel.ts`):
  - `buildCompareGridPayload(input: { stageNumbers: number[]; audioFrom: string; canvas: CanvasChoice; outputName: string }) -> CompareGridRequestPayload`
  - `summarizeGridResult(result: CompareGridResult) -> { headline: string; partial: boolean; failedStages: string[] }`
  - `CANVAS_CHOICES` -- `[{ id: "uhd", label: "4K UHD (3840x2160)", width: 3840, height: 2160 }, { id: "hd", label: "1080p (1920x1080) -- faster", width: 1920, height: 1080 }]`

**Testing approach:** the SPA currently has no React Testing Library, no jsdom, and zero `.test.tsx` files. The user has confirmed new dependencies are welcome where a mature library beats hand-rolling, so this task **adds `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom` and `jsdom` as devDependencies** and writes a real component test. Wire jsdom as the vitest environment in the existing vite config rather than adding a second config file.

Keep the `matchExportModel.ts` extraction regardless: payload construction and result summarising are logic, not rendering, and belong in a plain module that both the component and its test can use. The component test covers rendering and interaction; the model test covers the logic. Write both.

**Behaviour:**
- Route `match/:matchId/export` renders `<MatchExport />` wrapped in `<DesktopGate screen="Match export">`, matching the `Compare` route at `src/splitsmith/ui_static/src/App.tsx:169`.
- `match/:matchId/export/:slug` keeps resolving to the existing shooter `Export` page. **Unchanged.**
- Page contents: shooter list with a radio for the audio source, stage chips (multi-select, all selected by default), a canvas size select (`4K UHD 3840x2160` default, `1080p 1920x1080` as the fast option), a render button, job progress, and a Reveal button on success.
- Partial failure renders a warning listing the failed stages by number and name; it is not treated as a full failure.

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from "vitest";

import {
  CANVAS_CHOICES,
  buildCompareGridPayload,
  summarizeGridResult,
} from "@/pages/matchExportModel";

describe("buildCompareGridPayload", () => {
  it("carries the selection and the chosen canvas", () => {
    const payload = buildCompareGridPayload({
      stageNumbers: [3, 1, 2],
      audioFrom: "mathias",
      canvas: CANVAS_CHOICES[0],
      outputName: "bromma-grid",
    });

    expect(payload.stage_numbers).toEqual([1, 2, 3]);
    expect(payload.audio_from).toBe("mathias");
    expect(payload.canvas_width).toBe(3840);
    expect(payload.canvas_height).toBe(2160);
    expect(payload.output_name).toBe("bromma-grid");
  });

  it("defaults to 4K UHD as the first canvas choice", () => {
    expect(CANVAS_CHOICES[0].width).toBe(3840);
    expect(CANVAS_CHOICES[0].height).toBe(2160);
  });
});

describe("summarizeGridResult", () => {
  it("reports a clean render without a partial warning", () => {
    const summary = summarizeGridResult({
      output_path: "/m/exports/compare-grid.mp4",
      stages_rendered: 2,
      stages_total: 2,
      failed: [],
    });

    expect(summary.partial).toBe(false);
    expect(summary.failedStages).toEqual([]);
    expect(summary.headline).toContain("2");
  });

  it("names the failed stages without calling the whole render a failure", () => {
    const summary = summarizeGridResult({
      output_path: "/m/exports/compare-grid.mp4",
      stages_rendered: 1,
      stages_total: 2,
      failed: [{ stage_number: 2, stage_name: "Stage 2", error: "boom" }],
    });

    expect(summary.partial).toBe(true);
    expect(summary.failedStages).toEqual(["Stage 2"]);
    expect(summary.headline).toContain("1 of 2");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm vitest run src/pages/matchExportModel.test.ts`
Expected: FAIL -- cannot resolve `@/pages/matchExportModel`.

- [ ] **Step 3: Write `matchExportModel.ts`, then build the page around it**

Implement the three exports listed under **Interfaces** so the test passes, then create `MatchExport.tsx` as rendering only -- it calls `buildCompareGridPayload` to build the request and `summarizeGridResult` to render the outcome, holding no payload or summary logic of its own.

Reuse the shooter-loading and match-context pattern from `Compare.tsx` (`src/splitsmith/ui_static/src/pages/Compare.tsx`), and the visual primitives (`Section`, `StageChip`, `SelectField`, the LED CTA `Button`) from `Export.tsx` (`src/splitsmith/ui_static/src/pages/Export.tsx:1090-1500`). Lift those primitives into `src/splitsmith/ui_static/src/components/export/` rather than copy-pasting -- phase 3 folds both pages together and duplicated primitives would have to be reconciled then.

- [ ] **Step 4: Wire the route**

In `src/splitsmith/ui_static/src/App.tsx`, replace

```tsx
<Route path="export" element={<DefaultShooterRedirect base="export" />} />
```

with

```tsx
<Route
  path="export"
  element={
    <DesktopGate screen="Match export">
      <MatchExport />
    </DesktopGate>
  }
/>
```

Leave the `export/:slug` and `export/:slug/:stage` routes exactly as they are.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src/splitsmith/ui_static && pnpm vitest run && pnpm tsc --noEmit`
Expected: PASS, clean typecheck.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/MatchExport.tsx src/splitsmith/ui_static/src/pages/matchExportModel.ts src/splitsmith/ui_static/src/pages/matchExportModel.test.ts src/splitsmith/ui_static/src/App.tsx src/splitsmith/ui_static/src/components/export/
git commit -m "feat(ui): add match-scoped compare-grid export page"
```

---

### Task 8: End-to-end verification on the real 4-shooter match

**Model: Opus.** This is the task that decides whether phase 0 actually shipped.

**Files:**
- Create: `tests/test_compare_mp4_grid_integration.py`

- [ ] **Step 1: Write the integration test**

```python
import json
import shutil
import subprocess

import pytest

from splitsmith.compare import mp4_grid

pytestmark = pytest.mark.integration


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_renders_a_real_grid_with_four_audio_tracks(tmp_path, four_shooter_bundles):
    output = tmp_path / "grid.mp4"
    result = mp4_grid.render_grid_mp4(
        four_shooter_bundles,
        audio_label="Mathias",
        output_path=output,
        canvas=mp4_grid.GridCanvas(width=1920, height=1080),  # small for test speed
        work_dir=tmp_path / "work",
    )

    assert result.failed == ()
    assert output.exists() and output.stat().st_size > 0

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_streams", "-show_format", str(output),
        ],
        capture_output=True,
        check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = [s for s in streams if s["codec_type"] == "video"]
    audio = [s for s in streams if s["codec_type"] == "audio"]

    assert len(video) == 1
    assert (video[0]["width"], video[0]["height"]) == (1920, 1080)
    assert len(audio) == 4, "one audio track per shooter"
    assert audio[3]["disposition"]["default"] == 1, "audio source is the default track"
```

> **Superseded by phase 1b.** The renderer now always ships a mix of every
> shooter as track 1, carrying the `default` disposition, with the
> per-shooter tracks as 2..N+1. So a four-shooter render has **five** audio
> streams and the default is on index 0, not on the `--audio-from` shooter.
> The current checklist is
> `docs/superpowers/plans/2026-08-04-compare-grid-mp4-phase-0-handoff.md`.

**Note for the implementer:** build the `four_shooter_bundles` fixture from real short clips already in `tests/fixtures/`. Per the project's rules, **do not generate fake fixtures** -- if four suitable clips do not exist, ask the user for a sample rather than synthesising audio. `tests/test_trims_to_compare_e2e.py:118`'s `chained_match` fixture shows how a merged match is seeded on disk; `CompareShooterBundle`s can be loaded from it with `project_loader.load_shooter`.

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_compare_mp4_grid_integration.py -v -m integration`
Expected: PASS

- [ ] **Step 3: Drive the real match from the CLI**

Run against the user's actual 4-shooter match:

```bash
uv run splitsmith compare export <match-folder> --format mp4 --audio-from <shooter> -o /tmp/grid.mp4
```

- [ ] **Step 4: Watch the output**

Open `/tmp/grid.mp4`. Confirm by eye, not by exit code:
- four tiles, all playing, beep-aligned (shots line up across tiles)
- audio switchable between five tracks -- the mix, then the four shooters --
  defaulting to the mix (phase 1b; this line originally said four tracks
  defaulting to the chosen shooter)
- no black tile where a trim exists; black tiles only where one genuinely does not

Per the project's review practice, a green suite is not evidence the user sees anything. **Watch the file.**

- [ ] **Step 5: Drive it from the app**

Start the local server, open `match/:matchId/export`, select all stages, pick the audio shooter, render, and confirm the job completes and Reveal opens the file.

- [ ] **Step 6: Commit**

```bash
git add tests/test_compare_mp4_grid_integration.py
git commit -m "test(compare): integration coverage for the grid MP4 render"
```

---

## Self-review notes

**Spec coverage.** Every Phase 0 item in the spec maps to a task: `mp4_grid.py` (1-3), CLI `--format mp4` (4), match-scoped local-mode endpoint (5), `DesktopGate` page with the redirect replaced (6-7), command tests plus a real 4-shooter render (8). The 4K default canvas is Task 1's `GridCanvas`; missing-trim filler is Task 1 plus Task 2; per-stage failure isolation is Task 3.

**Deliberately not in this plan** (spec phases 1-3, each gets its own plan): sprite overlays, `overlay_text.py` extraction, transitions, title cards, hosted mode, FCPXML on the new surface, the full two-axis Export page restructure.

**Known gap the implementer must resolve, not guess at.** Task 5 Step 5 says "find the queue consumer that branches on `kind == 'export'`" without naming the file. I did not read the worker. The implementer must locate it and follow its existing dispatch shape rather than inventing one.

**Constraint discovered while planning, now load-bearing:** `concat -c copy` requires an identical stream layout across segments, which is why missing tiles get an `anullsrc` track and the frame rate is pinned on the canvas. Tasks 2 and 3 both test this; do not "simplify" it away.
