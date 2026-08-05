# Compare Grid Phase 1: splits overlay + stage summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Burn an opt-in live splits overlay into the multi-shooter grid MP4, and hand off at each stage's end to a per-tile summary drawn over the shooter's own frozen, blurred tile.

**Architecture:** Overlay content is a step function over shot events, so each state is pre-rendered once as a grid-sized RGBA PNG (`compare/overlay_sprites.py`) and fed to ffmpeg as one concat-demuxer input composited by a single `overlay` filter. The one genuinely per-frame element -- the running clock -- is ffmpeg `drawtext` with a pts expression and never touches PIL. The post-stage summary is one canvas-sized still, blurred once in PIL from per-tile freeze frames, concatenated onto the end of the stage's own segment so the cross-stage stitch stays a dumb `concat -c copy`.

**Tech Stack:** Python 3.11+, Pydantic, dataclasses, Pillow, Typer, ffmpeg, pytest.

**Spec:** `docs/superpowers/specs/2026-08-04-compare-grid-mp4-and-export-redesign-design.md` -- sections "Architecture", "Phase 1", "Phase 2" items 7-9, and "Where the two overlays live in the frame".

**Orientation:** `docs/superpowers/plans/2026-08-04-compare-grid-phase-1-kickoff.md` -- what already ships, the baselines, and how this codebase gets verified.

## Global Constraints

- Python 3.11+, type hints everywhere. Black line length 110. Ruff clean.
- `uv` for dependency management, never `pip`. **No new dependencies in this plan** -- Pillow, ffmpeg and pytest are already present, and everything here is built from them.
- `pathlib.Path` for paths, never strings. f-strings for formatting.
- Imports grouped stdlib / third-party / local, separated by blank lines. No relative imports beyond a single dot.
- Detection logic stays out of the CLI; `cli.py` orchestrates only.
- Command construction is pure functions with an injectable `Runner`, mirroring `mp4_render.py` and `trim.py`. Unit tests must not shell out to ffmpeg.
- Real-ffmpeg tests are marked `@pytest.mark.integration`. **Integration must stay at 0 skipped** -- CI fails the build on any integration skip. A test needing media builds it with `tests/synthetic_media.py`; it never depends on the gitignored `stage_sample.mp4`.
- CLI tests asserting on `--help` output must use `strip_ansi()` from `tests/conftest.py`, and must be checked under `GITHUB_ACTIONS=true` as well as plain.
- **Everything in this plan is opt-in and defaults to off.** A grid render with no overlay flags must produce byte-identical ffmpeg commands to what `main` produces today. There is an explicit test for this (Task 5, Step 1).
- **`compare/emitter.py` (the FCPXML grid) is not modified by any task in this plan.** `overlay_render.py` is modified by exactly one task (Task 1) and only to import what moved out of it.
- **The renderer is offline batch and must never call a network service mid-render.** All scoring data is read from the `MatchProject` already on disk.
- **Ranking is `stage_pct`, never `stage_points`.** Raw points are meaningless across stages and divisions.
- Never render a number that is not present. A missing audit, a `None` scorecard and a manually-timed stage each degrade to drawing less, never to a zero or a guess.

## Invariants that must survive

These are the four the previous phases were built on. Breaking any of them fails at the very last step of a long render, or silently ships a desynced video.

1. **Stream layout is uniform across segments.** Exactly 1 video stream at the canvas size and pinned frame rate, plus exactly **N+1** audio streams (the mix first, then shooters alphabetically), on every segment. Empty grid cells add video only. `concat -c copy` refuses segments that disagree. The summary hold (Milestone B) *extends every stream in the segment together* -- it must never add or drop one.
2. **Beep alignment.** Every tile's beep lands at `head_pad_seconds`, for clamped, unclamped and filler tiles. This broke once when a filter reordering put `setpts` ahead of `tpad`. Treat any reordering of the tile chain with suspicion; the overlay is inserted *after* `xstack`, never inside a tile chain.
3. **No cumulative A/V drift.** Segments carry PCM audio (`SEGMENT_AUDIO_CODEC`) and the stitch does a single AAC encode. Do not reintroduce per-segment AAC -- its encoder priming accumulated to +386ms by stage 12.
4. **Track identity.** MP4 discards `title=`; `handler_name=` is what lands.

## How this code gets verified

Eight defects were found across phases 0 and 1b. Every one reached a green test suite; none were found by reading code. Reviewers will hold each task to this:

- **Mutate your finished code** and confirm the test that claims to cover it goes red. A test that cannot fail is a finding even when everything is green.
- **Render and measure.** Assertions on ffmpeg arg tuples miss ordering bugs, container-metadata lies and anything visual. Every milestone here ends with an integration test that probes decoded pixels.
- **Choose fixture dimensions that can express the failure.** Two earlier defects were invisible to every fixture on the branch because everything used 2 or 4 shooters and 1-2 stages. Overlay tests must include a **3-shooter** roster (a 2x2 grid with one unreached cell) and at least one shooter with **no audit data**.
- **Container metadata lies.** `ffprobe` once reported a 21ms A/V difference on a file that was 372ms out. Measure decoded samples, honouring the edit list.

## File structure

| File | Responsibility |
|---|---|
| `src/splitsmith/overlay_text.py` (new) | Font resolution (bundled / preset / fallback) and shadowed text drawing. Shared by the single-shooter overlay and the grid. Moved verbatim from `overlay_render.py`. |
| `src/splitsmith/overlay_render.py` (modified, Task 1 only) | Imports the moved helpers back. No behaviour change. |
| `src/splitsmith/compare/overlay_data.py` (new) | Reads shot times and scoring for (label, stage) out of the `MatchProject` on disk. Pure of ffmpeg and of PIL. |
| `src/splitsmith/compare/overlay_sprites.py` (new) | The step function: shot events -> ordered overlay states -> content-addressed RGBA PNGs. |
| `src/splitsmith/compare/overlay_summary.py` (new, Milestone B) | Blurs per-tile freeze frames once and composites the canvas-sized summary still. |
| `src/splitsmith/compare/mp4_grid.py` (modified) | Gains an optional overlay plan on `GridStagePlan`, the sprite + `drawtext` graph, and the hold's duration model. |
| `src/splitsmith/compare/cli.py` (modified) | `--overlay`, `--overlay-theme`, `--summary-hold`. |

## Milestones

**Milestone A (Tasks 1-6) is one PR: the live overlay.** It is independently shippable and independently valuable -- a grid with per-tile counters, splits, a running clock and a delta strip, with no summary hold.

**Milestone B (Tasks 7-9) is a second PR: the summary hold.** Do not stack it on an unmerged Milestone A; merge A first, branch B from `main`.

## Model selection

Assigned per task by what the task actually demands, not by how large it looks. The rule that matters: **turn count beats token price.** A cheap model that needs three attempts at the filter graph costs more than an expensive one that gets it right once. Tasks whose plan text already contains the code are transcription plus testing and take the cheap tier; tasks where a wrong answer is plausible-looking and silent take the expensive one.

| Task | Implementer | Task reviewer | Why |
|---|---|---|---|
| 1 Extract `overlay_text` | Sonnet | Sonnet | A verbatim move. The only judgement is the monkeypatch retarget, and the plan names it. |
| 2 `overlay_data` | Opus | Opus | Time origins and seven degradation rules. A shot list off by the head pad looks entirely plausible. |
| 3 Sprite states | Opus | Opus | The ranking and delta maths is the only part of the overlay a viewer can catch being wrong. |
| 4 Sprite rendering | Sonnet | Sonnet | Bounded PIL against a layout spec and a written test file. |
| 5 Filter graph | Opus | Opus | Every invariant in the plan lives here; the previous phases' worst defects were filter-ordering bugs. |
| 6 CLI + integration | Sonnet | Sonnet | Mechanical wiring; the integration test is prescribed assertion by assertion. |
| 7 Hold duration model | Opus | Opus | Duration arithmetic spanning video and audio, whose failure surfaces only at the final stitch. |
| 8 Summary still | Sonnet | Sonnet | Bounded PIL again; the numbers to render are specified, not designed. |
| 9 Hold wiring | Opus | Opus | The seam where the duration model, the graph and the stitch meet. |
| Final whole-branch review (per milestone) | -- | Opus | One defect in phase 0 lived in a seam no single task owned. |

**Fix rounds:** rounds 1-3 resume the original implementer -- its context is already paid for, so a resume is the cheapest correct move. Rounds 4-5 escalate one tier (Sonnet -> Opus) with fresh eyes; a loop that survives three resumes means the implementer cannot see its own problem.

**Keeping the controller's context cheap,** which is where a long plan actually leaks tokens:

- Dispatch each task with its **brief file path**, not its text. Never paste a task body into a prompt.
- Every implementer writes its full report to a **report file** and returns only status, commits, a one-line test summary, and concerns.
- Never paste prior-task summaries into a later dispatch. A fresh subagent needs its task, the interfaces it touches, and the global constraints -- nothing else. The `Interfaces` block on each task exists precisely so that history does not have to travel.
- Reviewers get a **review-package file path**, not a diff pasted inline.

---

### Task 1: Extract `overlay_text.py`

**Model: Sonnet.** A move, not a rewrite -- but the monkeypatch retarget below is the part that is easy to get silently wrong.

**Files:**
- Create: `src/splitsmith/overlay_text.py`
- Modify: `src/splitsmith/overlay_render.py` (delete the moved block, import it back)
- Create: `tests/test_overlay_text.py`
- Modify: `tests/test_overlay_render.py` (retarget monkeypatch targets)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, all in `splitsmith.overlay_text`:
  - `OverlayRenderError(RuntimeError)` -- **moved here**, re-exported from `overlay_render`
  - `_BundledFont` (frozen dataclass: `filename: str`, `variation: str | None = None`)
  - `_BUNDLED_FONTS: dict[str, _BundledFont]`, `_FONT_PRESETS: dict[str, tuple[str, ...]]`, `_FONT_FALLBACKS: tuple[str, ...]`
  - `_LOGGED_FONT_TIERS: set[tuple[str | None, str]]`, `reset_font_log_cache() -> None`
  - `available_font_names() -> tuple[str, ...]`
  - `_log_font_choice(font_name: str | None, tier: str, source: str | None) -> None`
  - `_load_bundled_font(name: str, size: int) -> ImageFont.FreeTypeFont | None`
  - `_load_font(font_path: Path | None, size: int, *, font_name: str | None = None) -> ImageFont.ImageFont`
  - `_draw_text_with_shadow(draw, canvas, xy, text, font, fill, *, stroke_width=2, shadow_offset=3, shadow_blur=6, stroke_color=(0,0,0), shadow_color=(0,0,0)) -> None`

**What moves, exactly.** From `overlay_render.py`: `_LOGGED_FONT_TIERS`, `reset_font_log_cache`, `_log_font_choice`, `OverlayRenderError`, `_BundledFont`, `_BUNDLED_FONTS`, `_FONT_PRESETS`, `_FONT_FALLBACKS`, `available_font_names`, `_load_bundled_font`, `_load_font`, `_draw_text_with_shadow`. Copy each body **verbatim** -- do not reformat, rename or "improve" anything. Nothing else moves; `DefaultTemplate`, `_split_alpha`, `_format_running_total` and the render pipeline stay put.

**Why `OverlayRenderError` moves too:** `_load_font` raises it. Leaving the class in `overlay_render.py` while `_load_font` lives in `overlay_text.py` makes the import circular. Moving the class and re-exporting keeps the *class object identical*, so every existing `except OverlayRenderError` and every `pytest.raises(overlay_render.OverlayRenderError)` keeps working unchanged.

**Two traps this task must handle:**

1. **Monkeypatch targets.** `tests/test_overlay_render.py::test_load_font_pil_default_fallback_warns` patches `overlay_render._FONT_PRESETS`, `overlay_render._FONT_FALLBACKS` and `overlay_render._load_bundled_font`. After the move, `_load_font` reads those names out of `overlay_text`'s globals, so patching `overlay_render` silently patches nothing and the test passes for the wrong reason. **Retarget those three patches to `overlay_text`.**
2. **Logger name.** `_log_font_choice` uses the module logger, so its records move from `splitsmith.overlay_render` to `splitsmith.overlay_text`. If any `caplog` assertion filters on logger name or `caplog.set_level` names the module, update it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_overlay_text.py`:

```python
"""The moved font/text helpers, tested where they now live."""

import logging
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from splitsmith import overlay_render, overlay_text


def test_module_exposes_the_moved_helpers():
    for name in (
        "_load_font",
        "_load_bundled_font",
        "_draw_text_with_shadow",
        "_log_font_choice",
        "available_font_names",
        "reset_font_log_cache",
        "_BUNDLED_FONTS",
        "_FONT_PRESETS",
        "_FONT_FALLBACKS",
    ):
        assert hasattr(overlay_text, name), f"overlay_text is missing {name}"


def test_overlay_render_reexports_the_same_objects():
    # Identity, not equality: existing callers and tests reach these
    # through overlay_render, and an `except` clause matches on the
    # class object.
    assert overlay_render.OverlayRenderError is overlay_text.OverlayRenderError
    assert overlay_render._load_font is overlay_text._load_font
    assert overlay_render._draw_text_with_shadow is overlay_text._draw_text_with_shadow


def test_load_font_unknown_name_raises():
    with pytest.raises(overlay_text.OverlayRenderError):
        overlay_text._load_font(None, 24, font_name="not-a-real-font")


def test_load_font_bundled_returns_a_font():
    font = overlay_text._load_font(None, 24, font_name="splitsmith-mono")
    assert font is not None


def test_draw_text_with_shadow_marks_pixels():
    canvas = Image.new("RGBA", (240, 80), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = overlay_text._load_font(None, 32, font_name="splitsmith-mono")
    overlay_text._draw_text_with_shadow(
        draw, canvas, (10, 10), "1.23", font, (255, 255, 255, 255)
    )
    assert canvas.getextrema()[3][1] > 0, "nothing was drawn"


def test_draw_text_with_shadow_zero_alpha_draws_nothing():
    canvas = Image.new("RGBA", (240, 80), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = overlay_text._load_font(None, 32, font_name="splitsmith-mono")
    overlay_text._draw_text_with_shadow(
        draw, canvas, (10, 10), "1.23", font, (255, 255, 255, 0)
    )
    assert canvas.getextrema()[3][1] == 0


def test_font_log_is_emitted_once_per_tier(caplog):
    overlay_text.reset_font_log_cache()
    with caplog.at_level(logging.DEBUG, logger="splitsmith.overlay_text"):
        overlay_text._load_font(None, 24, font_name="splitsmith-mono")
        overlay_text._load_font(None, 24, font_name="splitsmith-mono")
    matching = [r for r in caplog.records if "splitsmith-mono" in r.getMessage()]
    assert len(matching) == 1
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_overlay_text.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'splitsmith.overlay_text'`.

- [ ] **Step 3: Create `overlay_text.py` by moving the block**

Create `src/splitsmith/overlay_text.py` with this header, then paste the twelve moved definitions verbatim from `overlay_render.py` in their existing order:

```python
"""Font resolution and shadowed text drawing, shared by both overlays.

Extracted from ``overlay_render.py`` so the multi-shooter grid
(``compare/overlay_sprites.py``) can draw the same typography without a
top-level module importing from a subpackage to reach its own helpers.
This is a move: every function body here is byte-identical to the one it
replaced, and ``overlay_render`` re-exports them so its callers and its
tests keep reaching them at the old names.

``OverlayRenderError`` moved with them because ``_load_font`` raises it
and leaving it behind would make the import circular. It keeps its name
-- it is still the overlay pipeline's error -- and ``overlay_render``
re-exports the same class object, so ``except`` clauses elsewhere are
unaffected.
"""

import contextlib
import logging
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)
```

Then, in `overlay_render.py`, delete those twelve definitions and add the re-export next to the other local imports:

```python
from .overlay_text import (
    _BUNDLED_FONTS as _BUNDLED_FONTS,
    _FONT_FALLBACKS as _FONT_FALLBACKS,
    _FONT_PRESETS as _FONT_PRESETS,
    _BundledFont as _BundledFont,
    _draw_text_with_shadow as _draw_text_with_shadow,
    _load_bundled_font as _load_bundled_font,
    _load_font as _load_font,
    _log_font_choice as _log_font_choice,
    OverlayRenderError as OverlayRenderError,
    available_font_names as available_font_names,
    reset_font_log_cache as reset_font_log_cache,
)
```

The `X as X` spelling is an explicit re-export; ruff will otherwise flag every one of these as an unused import. Drop any imports from `overlay_render.py` that nothing else there still uses (`contextlib`, `ImageFilter`, `as_file`, `files` are the likely candidates -- check with ruff rather than by eye).

- [ ] **Step 4: Retarget the monkeypatches in `tests/test_overlay_render.py`**

In `test_load_font_pil_default_fallback_warns`, change the three patch targets from `overlay_render` to `overlay_text` (import the module at the top of the file). Leave the *call* going through whichever module it already used -- that is what proves the re-export works.

If any caplog assertion in that file names the `splitsmith.overlay_render` logger for a font message, change it to `splitsmith.overlay_text`.

- [ ] **Step 5: Run both files**

Run: `uv run pytest tests/test_overlay_text.py tests/test_overlay_render.py tests/test_overlay_theme.py -v`
Expected: PASS, no skips.

- [ ] **Step 6: Prove the retarget was necessary**

Temporarily revert the patch target in `test_load_font_pil_default_fallback_warns` to `overlay_render` and run it.

Run: `uv run pytest tests/test_overlay_render.py::test_load_font_pil_default_fallback_warns -v`
Expected: **FAIL** -- the patch no longer reaches `_load_font`'s globals. If it PASSES, the test is asserting something that no longer depends on the patch; report that as a concern rather than restoring it silently. Restore the correct target afterwards.

- [ ] **Step 7: Full suite + lint**

Run:
```bash
uv run pytest -m "not integration" --ignore=tests/test_hosted_docker_smoke.py -q
uv run ruff check src tests && uv run black --check src tests
```
Expected: 2453 passed / 20 skipped or better (the new file adds tests), ruff and black clean.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/overlay_text.py src/splitsmith/overlay_render.py tests/test_overlay_text.py tests/test_overlay_render.py
git commit -m "refactor: extract overlay_text from overlay_render"
```

---

### Task 2: `compare/overlay_data.py` -- shots and scoring off disk

**Model: Opus.** Degradation rules and time origins are where silent errors live: a shot list measured from the wrong origin looks entirely plausible and is off by the head pad.

**Files:**
- Create: `src/splitsmith/compare/overlay_data.py`
- Test: `tests/test_compare_overlay_data.py`

**Interfaces:**
- Consumes: `compare.project_loader.CompareShooterBundle` / `CompareStageBundle` (fields: `label`, `project_root`, `stages_by_number`, and per stage `stage_number`, `stage_name`, `audit_path`, `beep_offset_in_clip`, `duration_seconds`); `ui.exports.read_audit_data`, `ui.exports.audit_shots_to_engine_shots`; `ui.project.MatchProject`, `StageScorecard`; `config.StageRounds`.
- Produces, all in `splitsmith.compare.overlay_data`:
  - `TileShot(number: int, time_from_beep: float, split: float)` -- frozen dataclass
  - `TileStageData(label: str, stage_number: int, shots: tuple[TileShot, ...], stage_time_seconds: float | None, stage_time_is_manual: bool, scorecard: StageScorecard | None, stage_rounds: StageRounds | None)` -- frozen dataclass, with properties `shot_count -> int`, `last_shot_time -> float | None`, `has_shots -> bool`
  - `load_overlay_data(shooters: Sequence[CompareShooterBundle]) -> dict[tuple[str, int], TileStageData]` keyed by `(label, stage_number)`

**Time origin -- get this right.** `audit_shots_to_engine_shots` returns `Shot.time_from_beep` in seconds after the beep, derived from the audit's `ms_after_beep`. That is exactly the origin the overlay wants, and it is **independent of the trim, the head pad and `beep_offset_in_clip`**. Do not add `beep_offset_in_clip` to it -- that field converts to *clip-local* time, which is a different thing, and the grid's own head pad is applied later by the sprite builder. Pass `beep_time_in_source=0.0` so `time_absolute` degenerates to `time_from_beep` and cannot be mistaken for a source-absolute value downstream.

**Splits.** `audit_shots_to_engine_shots` already computes them: shot 1's split is the draw (equal to `time_from_beep`), shot N>1 is the difference from shot N-1. Reuse it. Do not recompute.

**Degradation, all of which must be tested:**

| Situation | Result |
|---|---|
| No audit file | `read_audit_data` returns `{"shots": []}` -> `shots=()`. Not an error. |
| Audit is a stub (`MatchProject.is_stub_audit`) | Treated as no audit: `shots=()`. |
| Corrupt audit JSON | `read_audit_data` raises. Catch it, log a warning naming the path, and degrade to `shots=()`. One bad file must not fail a 12-stage render. |
| `scorecard is None` | `scorecard=None`, everything else still populated. |
| `time_seconds <= 0` | `stage_time_seconds=None` -- the model treats <=0 as unset. |
| `time_seconds_manual=True` | `stage_time_is_manual=True`, and `scorecard` is normally `None`. Both are recorded; the summary decides what to draw. |
| Shooter has no `project.json` at `project_root` | Every stage for that label degrades to shots=(), scorecard=None. Log once per shooter, not per stage. |

**Why this is a separate module, not a field on `CompareStageBundle`:** `project_loader` feeds `compare/emitter.py` as well, and the FCPXML grid ships clean tiles by decision. Reading every shooter's audit for a path that will never draw them is cost the FCPXML export should not pay.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compare_overlay_data.py`:

```python
"""Shot + scoring data for the grid overlay, read straight off disk."""

import json
from pathlib import Path

import pytest

from splitsmith.compare import overlay_data, project_loader


def _write_audit(root: Path, stage_number: int, ms_after_beep: list[int]) -> Path:
    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / f"stage{stage_number}.json"
    path.write_text(
        json.dumps(
            {
                "stage_time_seconds": 6.0,
                "beep_time": 3.0,
                "shots": [
                    {"shot_number": i + 1, "candidate_number": i + 1, "ms_after_beep": ms}
                    for i, ms in enumerate(ms_after_beep)
                ],
            }
        )
    )
    return path


def _bundle(tmp_path: Path, label: str, *, stage_number: int = 1, audit: Path | None = None):
    root = tmp_path / label
    root.mkdir(parents=True, exist_ok=True)
    stage = project_loader.CompareStageBundle(
        stage_number=stage_number,
        stage_name=f"Stage {stage_number}",
        trim_path=root / "trim.mov",
        audit_path=audit if audit is not None else root / "audit" / f"stage{stage_number}.json",
        beep_offset_in_clip=3.0,
        duration_seconds=9.0,
        width=1920,
        height=1080,
        frame_rate_num=60000,
        frame_rate_den=1001,
        camera_mount=None,
        substituted=False,
    )
    return project_loader.CompareShooterBundle(
        label=label,
        project_root=root,
        project=None,
        stages_by_number={stage_number: stage},
        missing_trims=[],
    )


def test_shot_times_are_measured_from_the_beep(tmp_path):
    audit = _write_audit(tmp_path / "ann", 1, [1200, 1450, 1700])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=audit)])
    tile = data[("ann", 1)]
    assert [round(s.time_from_beep, 3) for s in tile.shots] == [1.2, 1.45, 1.7]


def test_beep_offset_in_clip_does_not_shift_shot_times(tmp_path):
    # beep_offset_in_clip is 3.0 in the fixture. If it leaked into the
    # origin every shot would be 3s late and still look plausible.
    audit = _write_audit(tmp_path / "ann", 1, [1200])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=audit)])
    assert data[("ann", 1)].shots[0].time_from_beep == pytest.approx(1.2)


def test_first_split_is_the_draw_and_later_splits_are_differences(tmp_path):
    audit = _write_audit(tmp_path / "ann", 1, [1200, 1450, 1700])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=audit)])
    splits = [round(s.split, 3) for s in data[("ann", 1)].shots]
    assert splits == [1.2, 0.25, 0.25]


def test_shot_numbers_are_one_based_and_ordered(tmp_path):
    audit = _write_audit(tmp_path / "ann", 1, [1700, 1200, 1450])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "ann", audit=audit)])
    tile = data[("ann", 1)]
    assert [s.number for s in tile.shots] == [1, 2, 3]
    assert [s.time_from_beep for s in tile.shots] == sorted(
        s.time_from_beep for s in tile.shots
    )


def test_missing_audit_degrades_to_no_shots(tmp_path):
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "bo")])
    tile = data[("bo", 1)]
    assert tile.shots == ()
    assert tile.has_shots is False
    assert tile.shot_count == 0
    assert tile.last_shot_time is None


def test_corrupt_audit_degrades_and_warns(tmp_path, caplog):
    root = tmp_path / "cy"
    (root / "audit").mkdir(parents=True)
    bad = root / "audit" / "stage1.json"
    bad.write_text("{not json")
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "cy", audit=bad)])
    assert data[("cy", 1)].shots == ()
    assert any("stage1.json" in r.getMessage() for r in caplog.records)


def test_missing_project_json_degrades_without_raising(tmp_path):
    audit = _write_audit(tmp_path / "dee", 1, [1000])
    data = overlay_data.load_overlay_data([_bundle(tmp_path, "dee", audit=audit)])
    tile = data[("dee", 1)]
    assert tile.scorecard is None
    assert tile.stage_time_seconds is None
    assert tile.shot_count == 1  # the audit still read fine


def test_every_label_stage_pair_is_present_even_when_empty(tmp_path):
    bundles = [_bundle(tmp_path, "ann"), _bundle(tmp_path, "bo")]
    data = overlay_data.load_overlay_data(bundles)
    assert set(data) == {("ann", 1), ("bo", 1)}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_compare_overlay_data.py -v`
Expected: FAIL -- no module `splitsmith.compare.overlay_data`.

- [ ] **Step 3: Implement the module**

```python
"""Shot times and scoring for the grid overlay, read off disk.

Kept out of ``project_loader`` on purpose: that module also feeds
``compare/emitter.py``, and the FCPXML grid ships clean tiles by
decision, so it should not pay to read every shooter's audit.

Everything here is offline. The renderer is batch and must never reach a
network service mid-render, so scoring comes from the ``MatchProject``
already on disk rather than from the scoreboard.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from ..config import StageRounds
from ..ui.exports import audit_shots_to_engine_shots, read_audit_data
from ..ui.project import MatchProject, StageScorecard
from .project_loader import CompareShooterBundle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TileShot:
    """One accepted shot, measured from the beep."""

    number: int
    time_from_beep: float
    split: float


@dataclass(frozen=True)
class TileStageData:
    """Everything the overlay knows about one shooter on one stage."""

    label: str
    stage_number: int
    shots: tuple[TileShot, ...] = ()
    stage_time_seconds: float | None = None
    stage_time_is_manual: bool = False
    scorecard: StageScorecard | None = None
    stage_rounds: StageRounds | None = None

    @property
    def shot_count(self) -> int:
        return len(self.shots)

    @property
    def has_shots(self) -> bool:
        return bool(self.shots)

    @property
    def last_shot_time(self) -> float | None:
        return self.shots[-1].time_from_beep if self.shots else None


def load_overlay_data(
    shooters: Sequence[CompareShooterBundle],
) -> dict[tuple[str, int], TileStageData]:
    """Read shots + scoring for every (label, stage) the roster covers.

    Every pair present in ``stages_by_number`` gets an entry, even when
    nothing could be read for it -- the overlay draws less, it does not
    skip a tile, and a caller should never have to distinguish "absent
    from the mapping" from "present but empty".
    """
    out: dict[tuple[str, int], TileStageData] = {}
    for bundle in shooters:
        project = _load_project(bundle)
        for stage_number, stage in sorted(bundle.stages_by_number.items()):
            out[(bundle.label, stage_number)] = _load_tile(
                bundle, stage, stage_number, project
            )
    return out
```

Then `_load_project(bundle) -> MatchProject | None` (catch and warn once per shooter), and `_load_tile(...)` which:

1. Calls `read_audit_data(stage.audit_path)` inside a `try/except Exception`, warning with the path and degrading to `{"shots": []}`.
2. Treats a stub audit as empty -- check `MatchProject.is_stub_audit` against the loaded dict.
3. Calls `audit_shots_to_engine_shots(audit_data, beep_time_in_source=0.0)` and maps each engine shot to `TileShot(number=i + 1, time_from_beep=shot.time_from_beep, split=shot.split)`, sorted by `time_from_beep` before numbering.
4. Pulls `entry = project.stage(stage_number)` when `project` is not None, and takes `stage_time_seconds = entry.time_seconds if entry.time_seconds > 0 else None`, `stage_time_is_manual = entry.time_seconds_manual`, `scorecard = entry.scorecard`, `stage_rounds = entry.stage_rounds`.

Check `is_stub_audit`'s real signature before wiring it -- if it takes a path rather than a dict, call it accordingly.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_compare_overlay_data.py -v`
Expected: PASS.

- [ ] **Step 5: Mutation check**

Make each of these edits one at a time, run the file, confirm a test fails, then revert:

1. Add `beep_offset_in_clip` to the shot origin -> `test_beep_offset_in_clip_does_not_shift_shot_times` must fail.
2. Drop the `sorted(...)` before numbering -> `test_shot_numbers_are_one_based_and_ordered` must fail.
3. Skip pairs with no audit instead of emitting an empty `TileStageData` -> `test_every_label_stage_pair_is_present_even_when_empty` must fail.
4. Let the corrupt-JSON exception propagate -> `test_corrupt_audit_degrades_and_warns` must fail.

Any mutation that leaves the suite green is a missing test -- write it before moving on.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/compare/overlay_data.py tests/test_compare_overlay_data.py
git commit -m "feat(compare): read shot times and scoring for the grid overlay"
```

---

### Task 3: `overlay_sprites.py` -- the step function

**Model: Opus.** This is the design's core claim: ~30 states per stage instead of ~750 frames. The ranking maths is also the only part of the overlay a viewer can catch being wrong.

**Files:**
- Create: `src/splitsmith/compare/overlay_sprites.py` (states only -- no PIL yet)
- Test: `tests/test_compare_overlay_sprites.py`

**Interfaces:**
- Consumes: `overlay_data.TileStageData`, `overlay_data.TileShot`.
- Produces, all in `splitsmith.compare.overlay_sprites`:
  - `TilePlacement(label: str, row: int, col: int, present: bool)` -- frozen dataclass. `present=False` is a filler tile (the shooter has no trim for this stage); it is drawn as nothing and never ranks.
  - `TilePanel(label: str, row: int, col: int, present: bool, shots_fired: int, expected_shots: int | None, last_split: float | None, rank: int | None, delta_to_leader: float | None)` -- frozen dataclass
  - `OverlayState(start_seconds: float, duration_seconds: float, panels: tuple[TilePanel, ...])` -- frozen dataclass, property `end_seconds`
  - `build_overlay_states(placements: Sequence[TilePlacement], data: Mapping[str, TileStageData], *, head_pad_seconds: float, duration_seconds: float) -> tuple[OverlayState, ...]`

**The rules, precisely:**

1. **Events** are beep-relative times: `0.0`, plus every `shot.time_from_beep` from every *present* tile. Deduplicate on `round(t, 3)` -- two shooters firing in the same millisecond must not produce a zero-length state. Drop any event whose segment time would land at or past `duration_seconds`.
2. **Segment time** is `head_pad_seconds + event`. The pre-beep stretch and the beep state show the same thing (nothing fired), so they are one state: the first state starts at `0.0`.
3. **Boundaries** are the sorted segment times; each state runs to the next one, and the last runs to `duration_seconds`. Durations therefore sum to exactly `duration_seconds`.
4. **A panel at event time `t`:** `shots_fired` is the number of shots with `time_from_beep <= t + 1e-6`; `last_split` is that shot's `split`, or `None` before the first; `expected_shots` is `stage_rounds.expected` or `None`.
5. **Ranking** covers only present tiles with `shots_fired >= 1`. Sort by `shots_fired` descending, then by the time of shot number `shots_fired` ascending. `rank` is 1-based over that order.
6. **`delta_to_leader`** compares like with like: for a tile on shot `k`, it is that tile's time at shot `k` minus the *leader's* time at shot `k`. The leader has at least `k` shots by construction, so it is always defined. The leader's own delta is `0.0`. A tile that has not fired has `rank=None` and `delta_to_leader=None`.

Rule 6 is the one worth stating out loud: comparing a shooter on shot 3 against a leader's elapsed time on shot 8 would show a meaningless lead. Same shot number, or no number at all.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compare_overlay_sprites.py`:

```python
"""The overlay's step function: shot events in, ordered states out."""

import pytest

from splitsmith.compare import overlay_sprites
from splitsmith.compare.overlay_data import TileShot, TileStageData

HEAD_PAD = 1.0
DURATION = 10.0


def _tile(label: str, times: list[float], *, expected: int | None = None) -> TileStageData:
    shots = []
    prev = 0.0
    for i, t in enumerate(times):
        shots.append(TileShot(number=i + 1, time_from_beep=t, split=t - prev))
        prev = t
    rounds = None
    if expected is not None:
        from splitsmith.config import StageRounds

        rounds = StageRounds(expected=expected)
    return TileStageData(
        label=label, stage_number=1, shots=tuple(shots), stage_rounds=rounds
    )


def _placements(*labels: str, absent: tuple[str, ...] = ()) -> list:
    out = []
    for index, label in enumerate(labels):
        row, col = divmod(index, 2)
        out.append(
            overlay_sprites.TilePlacement(
                label=label, row=row, col=col, present=label not in absent
            )
        )
    return out


def _states(placements, data):
    return overlay_sprites.build_overlay_states(
        placements, data, head_pad_seconds=HEAD_PAD, duration_seconds=DURATION
    )


def _panel(state, label):
    return next(p for p in state.panels if p.label == label)


def test_one_state_per_distinct_event_plus_the_opening_state():
    data = {"ann": _tile("ann", [1.0, 1.5]), "bo": _tile("bo", [1.2])}
    states = _states(_placements("ann", "bo"), data)
    # opening + 3 shot events
    assert len(states) == 4


def test_first_state_starts_at_zero_and_shows_nothing_fired():
    data = {"ann": _tile("ann", [1.0])}
    states = _states(_placements("ann"), data)
    assert states[0].start_seconds == 0.0
    assert _panel(states[0], "ann").shots_fired == 0
    assert _panel(states[0], "ann").last_split is None


def test_state_boundaries_are_head_pad_plus_event_time():
    data = {"ann": _tile("ann", [1.0, 1.5])}
    states = _states(_placements("ann"), data)
    assert [round(s.start_seconds, 3) for s in states] == [0.0, 2.0, 2.5]


def test_durations_sum_to_the_segment_duration():
    data = {"ann": _tile("ann", [1.0, 1.5]), "bo": _tile("bo", [1.2])}
    states = _states(_placements("ann", "bo"), data)
    assert sum(s.duration_seconds for s in states) == pytest.approx(DURATION)


def test_no_state_is_zero_length():
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [1.0004])}
    states = _states(_placements("ann", "bo"), data)
    assert all(s.duration_seconds > 0 for s in states)


def test_simultaneous_shots_collapse_to_one_state():
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [1.0])}
    states = _states(_placements("ann", "bo"), data)
    assert len(states) == 2
    last = states[-1]
    assert _panel(last, "ann").shots_fired == 1
    assert _panel(last, "bo").shots_fired == 1


def test_shots_past_the_segment_end_are_dropped():
    data = {"ann": _tile("ann", [1.0, 20.0])}
    states = _states(_placements("ann"), data)
    assert len(states) == 2
    assert states[-1].end_seconds == pytest.approx(DURATION)


def test_last_split_is_the_most_recent_shots_split():
    data = {"ann": _tile("ann", [1.0, 1.25])}
    states = _states(_placements("ann"), data)
    assert _panel(states[1], "ann").last_split == pytest.approx(1.0)
    assert _panel(states[2], "ann").last_split == pytest.approx(0.25)


def test_expected_shot_count_comes_from_stage_rounds():
    data = {"ann": _tile("ann", [1.0], expected=12)}
    states = _states(_placements("ann"), data)
    assert _panel(states[-1], "ann").expected_shots == 12


def test_expected_shot_count_is_none_without_stage_rounds():
    data = {"ann": _tile("ann", [1.0])}
    states = _states(_placements("ann"), data)
    assert _panel(states[-1], "ann").expected_shots is None


def test_rank_orders_by_shots_fired_then_by_time():
    # At t=1.6: ann has 2 shots, bo has 2 shots but slower, cy has 1.
    data = {
        "ann": _tile("ann", [1.0, 1.5]),
        "bo": _tile("bo", [1.1, 1.6]),
        "cy": _tile("cy", [1.2]),
    }
    states = _states(_placements("ann", "bo", "cy"), data)
    last = states[-1]
    assert _panel(last, "ann").rank == 1
    assert _panel(last, "bo").rank == 2
    assert _panel(last, "cy").rank == 3


def test_delta_compares_the_same_shot_number():
    # cy is on shot 1 at 1.2s; the leader's shot 1 was at 1.0s -> +0.2,
    # NOT cy's 1.2 against the leader's shot-2 time of 1.5.
    data = {
        "ann": _tile("ann", [1.0, 1.5]),
        "cy": _tile("cy", [1.2]),
    }
    states = _states(_placements("ann", "cy"), data)
    last = states[-1]
    assert _panel(last, "ann").delta_to_leader == pytest.approx(0.0)
    assert _panel(last, "cy").delta_to_leader == pytest.approx(0.2)


def test_a_tile_that_has_not_fired_has_no_rank_and_no_delta():
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [5.0])}
    states = _states(_placements("ann", "bo"), data)
    at_first_shot = states[1]
    assert _panel(at_first_shot, "bo").rank is None
    assert _panel(at_first_shot, "bo").delta_to_leader is None


def test_filler_tiles_never_rank_and_never_fire():
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [1.1])}
    placements = _placements("ann", "bo", absent=("bo",))
    states = _states(placements, data)
    last = states[-1]
    assert _panel(last, "bo").present is False
    assert _panel(last, "bo").shots_fired == 0
    assert _panel(last, "bo").rank is None
    # ann is alone in the ranking, so ann leads
    assert _panel(last, "ann").rank == 1


def test_a_tile_with_no_audit_still_gets_a_panel_in_every_state():
    data = {"ann": _tile("ann", [1.0]), "bo": TileStageData(label="bo", stage_number=1)}
    states = _states(_placements("ann", "bo"), data)
    for state in states:
        assert {p.label for p in state.panels} == {"ann", "bo"}
        assert _panel(state, "bo").shots_fired == 0


def test_panels_keep_placement_order_and_geometry():
    data = {"ann": _tile("ann", [1.0]), "bo": _tile("bo", [1.1])}
    placements = _placements("ann", "bo")
    states = _states(placements, data)
    for state in states:
        assert [(p.label, p.row, p.col) for p in state.panels] == [
            (p.label, p.row, p.col) for p in placements
        ]


def test_no_shots_at_all_yields_a_single_state():
    data = {"ann": TileStageData(label="ann", stage_number=1)}
    states = _states(_placements("ann"), data)
    assert len(states) == 1
    assert states[0].start_seconds == 0.0
    assert states[0].duration_seconds == pytest.approx(DURATION)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_compare_overlay_sprites.py -v`
Expected: FAIL -- no module `splitsmith.compare.overlay_sprites`.

- [ ] **Step 3: Implement the state builder**

Write `build_overlay_states` following the six rules above. Shape:

```python
def build_overlay_states(
    placements: Sequence[TilePlacement],
    data: Mapping[str, TileStageData],
    *,
    head_pad_seconds: float,
    duration_seconds: float,
) -> tuple[OverlayState, ...]:
    """Ordered overlay states covering the whole stage segment.

    Overlay content changes only when someone fires, so a 30-shot stage
    has ~30 states rather than ~750 frames. Each state is rendered once
    and held; that is the entire reason this path costs PIL draws in the
    tens rather than the hundreds.
    """
    present = {p.label for p in placements if p.present}
    events = {0.0}
    for label in present:
        for shot in data.get(label, _EMPTY).shots:
            events.add(round(shot.time_from_beep, 3))
    starts = sorted(
        {0.0}
        | {
            head_pad_seconds + e
            for e in events
            if head_pad_seconds + e < duration_seconds
        }
    )
    ...
```

Build each state's panels from the *event time* (`start - head_pad_seconds`, floored at 0), not from the start time, so the first state's lookups are all "nothing fired yet".

Rank and delta are computed once per state over the present tiles that have fired.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_compare_overlay_sprites.py -v`
Expected: PASS.

- [ ] **Step 5: Mutation check**

One at a time; each must turn a named test red:

1. Rank by time only, ignoring `shots_fired` -> `test_rank_orders_by_shots_fired_then_by_time`.
2. Compute the delta against the leader's *latest* shot instead of shot `k` -> `test_delta_compares_the_same_shot_number`.
3. Drop the `round(t, 3)` dedup -> `test_simultaneous_shots_collapse_to_one_state` (and probably `test_no_state_is_zero_length`).
4. Include filler tiles in the ranking -> `test_filler_tiles_never_rank_and_never_fire`.
5. Stop clamping the final state to `duration_seconds` -> `test_durations_sum_to_the_segment_duration`.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/compare/overlay_sprites.py tests/test_compare_overlay_sprites.py
git commit -m "feat(compare): model the grid overlay as a step function over shot events"
```

---

### Task 4: Sprite rendering + content-addressed cache

**Model: Sonnet.** Mechanical PIL work with a precise spec, but the pixel assertions must be real.

**Files:**
- Modify: `src/splitsmith/compare/overlay_sprites.py` (add the rendering half)
- Modify: `src/splitsmith/overlay_text.py` (add `materialize_font`)
- Test: `tests/test_compare_overlay_sprite_render.py`

**Interfaces:**
- Consumes: `OverlayState`, `TilePanel` from Task 3; `overlay_text._load_font`, `overlay_text._draw_text_with_shadow`; `overlay_theme.load_theme`, `OverlayTheme`.
- Produces:
  - `overlay_text.materialize_font(font_name: str, dest_dir: Path) -> Path` -- copies a bundled TTF to a real filesystem path and returns it. ffmpeg's `drawtext` needs a path that outlives the process; `importlib.resources.as_file` may hand back a temp file that is deleted on context exit.
  - `overlay_sprites.SpriteGeometry(canvas_width: int, canvas_height: int, rows: int, cols: int)` -- frozen dataclass with properties `cell_width`, `cell_height` (floor division, matching `mp4_grid._cell_size`), `strip_height`
  - `overlay_sprites.render_state(state: OverlayState, geometry: SpriteGeometry, *, theme: OverlayTheme) -> Image.Image` -- RGBA, canvas-sized
  - `overlay_sprites.write_sprite_sequence(states, geometry, *, theme, cache_dir: Path) -> tuple[tuple[Path, float], ...]` -- `(png_path, duration_seconds)` per state, content-addressed
  - `overlay_sprites.write_concat_list(sequence, path: Path) -> Path`

**Layout inside each cell** (mirrors `overlay_render.DefaultTemplate` so the two overlays look like one product):

- `pad = max(24, cell_height // 36)`; type size `big = max(48, cell_height // 14)`.
- **Top left of the cell:** `fired/expected` when `expected_shots` is set, otherwise just `fired`. Omitted entirely when `shots_fired == 0`.
- **Bottom centre of the cell, above the strip:** `last_split` as `0.23s`. Omitted when `None`.
- **Nothing at all** when `present` is False.
- The running clock is **not drawn here**. It is `drawtext` (Task 5), top right of the cell.

**Delta strip:** a band of `strip_height = max(48, canvas_height // 20)` across the bottom of the canvas, with one entry per present tile in rank order: `1 ANN 3.42` for the leader (their elapsed time at their last shot is not known to the sprite -- use the rank number and label only) and `2 BO +0.21` for the rest. Entries are laid out evenly across the canvas width. A tile that has not fired is listed after the ranked ones with no number.

**Deliberate divergence from `DefaultTemplate`, state it in the code comment:** the single-shooter overlay fades the last split out after `split_hold_seconds`. A step function cannot fade without inventing extra states, and in a grid the viewer wants to be able to glance at any moment and read what a shooter's last split was. The split label therefore persists until the next shot.

**Content addressing:** the cache key is a SHA-256 over a stable JSON dump of `(geometry, theme.name, panels)` -- the *inputs*, never the rendered bytes. Filename `sprite-<hex[:16]>.png`. Two states with identical content share one file, so a 30-shot stage where nothing changes between two events writes one PNG.

**No golden-hash test.** The spec suggested one; a hash of rasterized glyphs pins the test to a Pillow version and a font renderer, and it would go red on an unrelated dependency bump while catching nothing a structural assertion misses. What is asserted instead: determinism (same inputs -> same key, different inputs -> different key) and *where the ink lands* (the right quadrant, and no ink in a filler tile's quadrant). Flag this divergence in the implementation report.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compare_overlay_sprite_render.py`:

```python
"""Sprite rendering: where the ink lands, and the cache key."""

from pathlib import Path

import pytest
from PIL import Image

from splitsmith import overlay_text
from splitsmith.compare import overlay_sprites
from splitsmith.overlay_theme import load_theme

GEOMETRY = overlay_sprites.SpriteGeometry(
    canvas_width=1280, canvas_height=720, rows=2, cols=2
)
THEME = load_theme("clean")


def _panel(label, row, col, **kwargs):
    base = dict(
        label=label,
        row=row,
        col=col,
        present=True,
        shots_fired=0,
        expected_shots=None,
        last_split=None,
        rank=None,
        delta_to_leader=None,
    )
    base.update(kwargs)
    return overlay_sprites.TilePanel(**base)


def _state(panels, start=0.0, duration=1.0):
    return overlay_sprites.OverlayState(
        start_seconds=start, duration_seconds=duration, panels=tuple(panels)
    )


def _quadrant(image, geometry, row, col):
    """The tile's own cell, excluding the bottom delta strip."""
    x0 = col * geometry.cell_width
    y0 = row * geometry.cell_height
    x1 = x0 + geometry.cell_width
    y1 = min(y0 + geometry.cell_height, geometry.canvas_height - geometry.strip_height)
    return image.crop((x0, y0, x1, y1))


def _has_ink(image) -> bool:
    return image.getextrema()[3][1] > 0


def test_sprite_is_canvas_sized_rgba():
    image = overlay_sprites.render_state(
        _state([_panel("ann", 0, 0, shots_fired=1)]), GEOMETRY, theme=THEME
    )
    assert image.mode == "RGBA"
    assert image.size == (GEOMETRY.canvas_width, GEOMETRY.canvas_height)


def test_ink_lands_in_the_firing_tiles_own_cell():
    panels = [
        _panel("ann", 0, 0, shots_fired=3, last_split=0.25, rank=1, delta_to_leader=0.0),
        _panel("bo", 0, 1),
        _panel("cy", 1, 0),
        _panel("dee", 1, 1),
    ]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    assert _has_ink(_quadrant(image, GEOMETRY, 0, 0))
    assert not _has_ink(_quadrant(image, GEOMETRY, 0, 1))
    assert not _has_ink(_quadrant(image, GEOMETRY, 1, 0))


def test_a_filler_tile_draws_nothing_in_its_cell():
    panels = [
        _panel("ann", 0, 0, shots_fired=2, rank=1, delta_to_leader=0.0),
        _panel("bo", 0, 1, present=False, shots_fired=0),
    ]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    assert not _has_ink(_quadrant(image, GEOMETRY, 0, 1))


def test_a_tile_that_has_not_fired_draws_no_counter():
    fired = overlay_sprites.render_state(
        _state([_panel("ann", 0, 0, shots_fired=1)]), GEOMETRY, theme=THEME
    )
    unfired = overlay_sprites.render_state(
        _state([_panel("ann", 0, 0, shots_fired=0)]), GEOMETRY, theme=THEME
    )
    assert _has_ink(_quadrant(fired, GEOMETRY, 0, 0))
    assert not _has_ink(_quadrant(unfired, GEOMETRY, 0, 0))


def test_the_delta_strip_draws_across_the_bottom_band():
    panels = [
        _panel("ann", 0, 0, shots_fired=2, rank=1, delta_to_leader=0.0),
        _panel("bo", 0, 1, shots_fired=1, rank=2, delta_to_leader=0.31),
    ]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    strip = image.crop(
        (0, GEOMETRY.canvas_height - GEOMETRY.strip_height, GEOMETRY.canvas_width, GEOMETRY.canvas_height)
    )
    assert _has_ink(strip)


def test_no_strip_ink_before_anyone_fires():
    panels = [_panel("ann", 0, 0), _panel("bo", 0, 1)]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    strip = image.crop(
        (0, GEOMETRY.canvas_height - GEOMETRY.strip_height, GEOMETRY.canvas_width, GEOMETRY.canvas_height)
    )
    assert not _has_ink(strip)


def test_identical_panels_reuse_one_file(tmp_path):
    panels = [_panel("ann", 0, 0, shots_fired=1, last_split=1.0, rank=1, delta_to_leader=0.0)]
    states = [_state(panels, 0.0, 1.0), _state(panels, 1.0, 2.0)]
    sequence = overlay_sprites.write_sprite_sequence(
        states, GEOMETRY, theme=THEME, cache_dir=tmp_path
    )
    assert len(sequence) == 2
    assert sequence[0][0] == sequence[1][0]
    assert len(list(tmp_path.glob("*.png"))) == 1


def test_durations_are_carried_through(tmp_path):
    panels = [_panel("ann", 0, 0)]
    states = [_state(panels, 0.0, 1.5), _state([_panel("ann", 0, 0, shots_fired=1)], 1.5, 2.5)]
    sequence = overlay_sprites.write_sprite_sequence(
        states, GEOMETRY, theme=THEME, cache_dir=tmp_path
    )
    assert [d for _, d in sequence] == [1.5, 2.5]


def test_different_content_gets_a_different_file(tmp_path):
    a = [_panel("ann", 0, 0, shots_fired=1)]
    b = [_panel("ann", 0, 0, shots_fired=2)]
    sequence = overlay_sprites.write_sprite_sequence(
        [_state(a), _state(b)], GEOMETRY, theme=THEME, cache_dir=tmp_path
    )
    assert sequence[0][0] != sequence[1][0]


def test_geometry_is_part_of_the_cache_key(tmp_path):
    panels = [_panel("ann", 0, 0, shots_fired=1)]
    wide = overlay_sprites.SpriteGeometry(
        canvas_width=1920, canvas_height=1080, rows=2, cols=2
    )
    first = overlay_sprites.write_sprite_sequence(
        [_state(panels)], GEOMETRY, theme=THEME, cache_dir=tmp_path
    )
    second = overlay_sprites.write_sprite_sequence(
        [_state(panels)], wide, theme=THEME, cache_dir=tmp_path
    )
    assert first[0][0] != second[0][0]


def test_concat_list_repeats_the_final_entry(tmp_path):
    # The concat demuxer ignores the last entry's duration unless the
    # file is listed once more after it.
    panels = [_panel("ann", 0, 0)]
    sequence = overlay_sprites.write_sprite_sequence(
        [_state(panels, 0.0, 2.0)], GEOMETRY, theme=THEME, cache_dir=tmp_path
    )
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "sprites.txt")
    lines = [ln for ln in list_path.read_text().splitlines() if ln.strip()]
    assert lines[0].startswith("file ")
    assert lines[1] == "duration 2"
    assert lines[-1] == lines[0]


def test_materialize_font_writes_a_readable_file(tmp_path):
    path = overlay_text.materialize_font("splitsmith-mono", tmp_path)
    assert path.is_file()
    assert path.stat().st_size > 0
    assert path.parent == tmp_path
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_compare_overlay_sprite_render.py -v`
Expected: FAIL -- `SpriteGeometry` / `render_state` / `materialize_font` do not exist.

- [ ] **Step 3: Add `materialize_font` to `overlay_text.py`**

```python
def materialize_font(font_name: str, dest_dir: Path) -> Path:
    """Copy a bundled font to a real path that outlives this call.

    ``_load_font`` hands PIL an mmap'd resource, which is fine for a
    process that draws and exits. ffmpeg's ``drawtext`` is a different
    consumer: it needs ``fontfile=`` to name a path that still exists
    when ffmpeg opens it, and ``importlib.resources.as_file`` may be
    handing back a temp file that is unlinked when its context closes.
    """
```

Resolve the name through `_BUNDLED_FONTS`, raise `OverlayRenderError` for an unknown name, and write the bytes into `dest_dir / spec.filename`. Return the destination path. Skip the copy when the destination already exists with the same size.

- [ ] **Step 4: Implement rendering and the cache**

Write `SpriteGeometry`, `render_state`, `write_sprite_sequence`, `write_concat_list` per the layout spec above. `render_state` builds `Image.new("RGBA", (w, h), (0, 0, 0, 0))` and draws through `_draw_text_with_shadow` with the theme's `ink` / `split` / `stroke` colours, exactly as `DefaultTemplate._draw` does.

The concat list format:

```
file '/abs/path/sprite-abc.png'
duration 1.5
file '/abs/path/sprite-def.png'
duration 2.5
file '/abs/path/sprite-def.png'
```

Durations are written with `:g` formatting. The final `file` line repeats the last entry with no duration -- without it the concat demuxer drops the last state to one frame.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_compare_overlay_sprite_render.py -v`
Expected: PASS.

- [ ] **Step 6: Look at one**

Render a sprite to a file you can open and confirm it reads as intended -- this is the step that catches "the fix reached the output and rich ellipsized it away":

```bash
uv run python -c "
from pathlib import Path
from splitsmith.compare import overlay_sprites as s
from splitsmith.overlay_theme import load_theme
g = s.SpriteGeometry(canvas_width=1920, canvas_height=1080, rows=2, cols=2)
panels = (
  s.TilePanel(label='Ann', row=0, col=0, present=True, shots_fired=7, expected_shots=12, last_split=0.23, rank=1, delta_to_leader=0.0),
  s.TilePanel(label='Bo', row=0, col=1, present=True, shots_fired=6, expected_shots=12, last_split=0.31, rank=2, delta_to_leader=0.44),
  s.TilePanel(label='Cy', row=1, col=0, present=True, shots_fired=0, expected_shots=12, last_split=None, rank=None, delta_to_leader=None),
  s.TilePanel(label='Dee', row=1, col=1, present=False, shots_fired=0, expected_shots=None, last_split=None, rank=None, delta_to_leader=None),
)
st = s.OverlayState(start_seconds=0.0, duration_seconds=1.0, panels=panels)
s.render_state(st, g, theme=load_theme('splitsmith')).save('/home/mathias/.claude-tmp/sprite-preview.png')
print('wrote /home/mathias/.claude-tmp/sprite-preview.png')
"
```

Open it. Confirm: counters in the right cells, nothing in Dee's cell, nothing in Cy's cell except what an unfired tile should show, and a legible strip along the bottom. Record what you saw in the implementation report. If text overflows a cell or the strip entries collide, fix the layout now -- it is far cheaper here than after the filter graph exists.

- [ ] **Step 7: Full suite + lint, then commit**

```bash
uv run pytest -m "not integration" --ignore=tests/test_hosted_docker_smoke.py -q
uv run ruff check src tests && uv run black --check src tests
git add src/splitsmith/compare/overlay_sprites.py src/splitsmith/overlay_text.py tests/test_compare_overlay_sprite_render.py
git commit -m "feat(compare): render overlay states to content-addressed sprite PNGs"
```

---

### Task 5: Wire sprites + the `drawtext` clock into the filter graph

**Model: Opus.** Every invariant in this plan lives in this file, and the previous phases' worst defects were filter-ordering bugs that a green command-string test did not see.

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py`
- Test: `tests/test_compare_mp4_grid_overlay.py` (new file -- leave the existing three grid test files alone so a regression in them is unambiguous)

**Interfaces:**
- Consumes: `overlay_sprites.write_sprite_sequence`, `write_concat_list`, `SpriteGeometry`, `build_overlay_states`, `TilePlacement`; `overlay_data.load_overlay_data`; `overlay_text.materialize_font`.
- Produces, in `splitsmith.compare.mp4_grid`:
  - `TileClock(row: int, col: int, start_seconds: float, freeze_seconds: float | None, final_text: str | None)` -- frozen dataclass
  - `StageOverlayPlan(sprite_list_path: Path, font_path: Path, font_size: int, clocks: tuple[TileClock, ...])` -- frozen dataclass
  - `build_stage_command(plan, *, canvas, output_path, ffmpeg_binary="ffmpeg", overlay: StageOverlayPlan | None = None)` -- the new keyword defaults to `None`
  - `render_grid_mp4(..., overlay: bool = False, overlay_theme: str = "splitsmith", ...)`

**Where the overlay goes in the graph -- and where it must not go.**

```
tile chains (UNCHANGED) -> xstack -> [grid]
                                       |
        sprite concat input -> [ovl] --+-> overlay=0:0 -> drawtext xN -> format=yuv420p -> [final]
```

The overlay is composited **after `xstack`, never inside a tile chain**. Invariant 2 broke once because a filter was reordered inside a tile chain; nothing in this task may touch the `tpad` / `setpts` / `scale` / `pad` / `setsar` / `fps` / `tpad` / `trim` sequence, and nothing may touch the audio half of the graph at all.

**Input ordering.** The sprite input is appended **after every tile input and after every unreached-cell input**. Tile input indices are computed by walking the tile list, and a filler tile already takes two inputs where a real one takes one -- inserting the sprite anywhere but last would shift indices behind it and silently put a shooter's audio in someone else's track.

**The sprite chain:**

```
[{sprite_index}:v]format=rgba,fps={rate},setpts=PTS-STARTPTS,
tpad=stop_duration={duration}:stop_mode=clone,trim=0:{duration}[ovl]
```

`stop_mode=clone` holds the last state's alpha rather than padding with opaque black, and the explicit `trim` means the segment's length never depends on `overlay`'s `eof_action` default. Then:

```
[grid][ovl]overlay=0:0:format=auto[ovlgrid]
```

**The clock.** One ticking `drawtext` per tile that has shot data, plus one static `drawtext` holding the final time after that shooter's last shot -- `enable` expressions make them mutually exclusive. Two filters, no per-frame PIL, and the clock stops where the shooter stops instead of running on to the end of the longest tile.

- Ticking: `enable='between(t,{start},{freeze})'`, text is elapsed seconds since `start` to two decimals.
- Static: `enable='gte(t,{freeze})'`, text is `final_text` (the shooter's last shot time, formatted the same way).
- `freeze_seconds is None` (no shot data, or the run has no end) -> the ticking filter runs to the end and there is no static one.
- A tile with no shot data at all gets **no clock**, because a clock over a tile with no counters implies a timed run that was never measured.
- Position: top right of the cell, `x={cell_x}+{cell_w}-tw-{pad}`, `y={cell_y}+{pad}`. `tw` is drawtext's own text-width variable, so right alignment costs nothing.

- [ ] **Step 1: Verify the `drawtext` expression against real ffmpeg BEFORE writing the builder**

The escaping rules inside `filter_complex` for `%{eif:...}` are the part of this task most likely to be wrong in a way that unit tests on strings cannot see. Establish the exact string first:

```bash
cd /home/mathias/.claude-tmp && mkdir -p clockcheck && cd clockcheck
ffmpeg -hide_banner -y -f lavfi -t 4 -i color=c=gray:s=640x360:r=30 \
  -filter_complex "[0:v]drawtext=fontfile='/home/mathias/work/splitsmith/src/splitsmith/data/fonts/JetBrainsMono-Bold.ttf':fontsize=48:fontcolor=white:borderw=3:bordercolor=black:x=20:y=20:text='%{eif\:trunc(t-1)\:d}.%{eif\:trunc(mod((t-1)*100\,100))\:d\:2}':enable='between(t\,1\,3)'[v]" \
  -map "[v]" clock.mp4
ffmpeg -hide_banner -y -ss 2.5 -i clock.mp4 -frames:v 1 at-2.5.png
ffmpeg -hide_banner -y -ss 0.5 -i clock.mp4 -frames:v 1 at-0.5.png
ffmpeg -hide_banner -y -ss 3.5 -i clock.mp4 -frames:v 1 at-3.5.png
```

Open the three PNGs. Expected: `1.50` at t=2.5, nothing at t=0.5, nothing at t=3.5. If ffmpeg errors on the escaping, adjust until it renders and **use the string that worked** as the template in the builder. Paste the working string and what you saw in each frame into the implementation report -- a later reviewer needs to know this was observed, not assumed.

- [ ] **Step 2: Write the failing test**

Create `tests/test_compare_mp4_grid_overlay.py`:

```python
"""The overlay half of the grid's filter graph.

Kept apart from the three existing grid test files so a regression in
the no-overlay path stays unambiguous.
"""

from pathlib import Path

import pytest

from splitsmith.compare import mp4_grid
from splitsmith.compare.mp4_grid import GridCanvas, GridStagePlan, GridTile

CANVAS = GridCanvas(width=1920, height=1080, frame_rate_num=60000, frame_rate_den=1001)


def _tile(label, row, col, *, present=True):
    return GridTile(
        label=label,
        trim_path=Path(f"/tmp/{label}.mov") if present else None,
        beep_offset_in_clip=1.0,
        seek_seconds=0.0,
        lead_pad_seconds=0.0,
        row=row,
        col=col,
    )


def _plan(tiles, *, rows=2, cols=2, duration=10.0):
    return GridStagePlan(
        stage_number=1,
        stage_name="Stage 1",
        tiles=tuple(tiles),
        duration_seconds=duration,
        audio_label=tiles[0].label,
        rows=rows,
        cols=cols,
    )


def _overlay(tmp_path, clocks=()):
    list_path = tmp_path / "sprites.txt"
    list_path.write_text("file '/tmp/a.png'\nduration 10\nfile '/tmp/a.png'\n")
    return mp4_grid.StageOverlayPlan(
        sprite_list_path=list_path,
        font_path=tmp_path / "font.ttf",
        font_size=64,
        clocks=tuple(clocks),
    )


def _graph(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def test_without_overlay_the_graph_is_untouched(tmp_path):
    cmd = mp4_grid.build_stage_command(
        _plan([_tile("ann", 0, 0), _tile("bo", 0, 1)]),
        canvas=CANVAS,
        output_path=tmp_path / "out.mov",
    )
    graph = _graph(cmd)
    assert "overlay=" not in graph
    assert "drawtext" not in graph
    assert graph.endswith("[grid]format=yuv420p[final]")
    assert "-f" not in cmd[cmd.index("-filter_complex"):]


def test_overlay_defaults_to_off(tmp_path):
    plain = mp4_grid.build_stage_command(
        _plan([_tile("ann", 0, 0)]), canvas=CANVAS, output_path=tmp_path / "o.mov"
    )
    explicit = mp4_grid.build_stage_command(
        _plan([_tile("ann", 0, 0)]),
        canvas=CANVAS,
        output_path=tmp_path / "o.mov",
        overlay=None,
    )
    assert plain == explicit


def test_sprite_input_is_appended_after_every_other_input(tmp_path):
    # A filler tile takes two inputs and an unreached cell takes one, so
    # the sprite must land last or every index behind it shifts.
    plan = _plan([_tile("ann", 0, 0), _tile("bo", 0, 1, present=False), _tile("cy", 1, 0)])
    cmd = mp4_grid.build_stage_command(
        plan, canvas=CANVAS, output_path=tmp_path / "o.mov", overlay=_overlay(tmp_path)
    )
    inputs = [i for i, a in enumerate(cmd) if a == "-i"]
    assert cmd[inputs[-1] + 1] == str(tmp_path / "sprites.txt")
    assert cmd[inputs[-1] - 2 : inputs[-1]] == ["-f", "concat"] or "-safe" in cmd


def test_sprite_input_uses_the_concat_demuxer(tmp_path):
    cmd = mp4_grid.build_stage_command(
        _plan([_tile("ann", 0, 0)]),
        canvas=CANVAS,
        output_path=tmp_path / "o.mov",
        overlay=_overlay(tmp_path),
    )
    joined = " ".join(cmd)
    assert "-f concat -safe 0 -i" in joined


def test_tile_input_indices_do_not_move_when_the_overlay_is_added(tmp_path):
    plan = _plan([_tile("ann", 0, 0), _tile("bo", 0, 1, present=False), _tile("cy", 1, 0)])
    plain = _graph(
        mp4_grid.build_stage_command(plan, canvas=CANVAS, output_path=tmp_path / "o.mov")
    )
    with_overlay = _graph(
        mp4_grid.build_stage_command(
            plan, canvas=CANVAS, output_path=tmp_path / "o.mov", overlay=_overlay(tmp_path)
        )
    )
    tile_chains = [p for p in plain.split(";") if p.endswith(("[t0]", "[t1]", "[t2]"))]
    for chain in tile_chains:
        assert chain in with_overlay, f"tile chain changed: {chain}"


def test_audio_graph_is_identical_with_the_overlay_on(tmp_path):
    plan = _plan([_tile("ann", 0, 0), _tile("bo", 0, 1)])
    plain = _graph(
        mp4_grid.build_stage_command(plan, canvas=CANVAS, output_path=tmp_path / "o.mov")
    )
    with_overlay = _graph(
        mp4_grid.build_stage_command(
            plan, canvas=CANVAS, output_path=tmp_path / "o.mov", overlay=_overlay(tmp_path)
        )
    )
    audio_of = lambda g: [p for p in g.split(";") if p.startswith("[") and ":a]" in p or "amix" in p]
    assert audio_of(plain) == audio_of(with_overlay)


def test_track_count_and_maps_are_unchanged_with_the_overlay_on(tmp_path):
    plan = _plan([_tile("ann", 0, 0), _tile("bo", 0, 1)])
    plain = mp4_grid.build_stage_command(
        plan, canvas=CANVAS, output_path=tmp_path / "o.mov"
    )
    with_overlay = mp4_grid.build_stage_command(
        plan, canvas=CANVAS, output_path=tmp_path / "o.mov", overlay=_overlay(tmp_path)
    )
    maps = lambda c: [c[i + 1] for i, a in enumerate(c) if a == "-map"]
    assert maps(plain) == maps(with_overlay)


def test_sprite_chain_is_rgba_and_covers_the_whole_stage(tmp_path):
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path),
        )
    )
    chain = next(p for p in graph.split(";") if p.endswith("[ovl]"))
    assert "format=rgba" in chain
    assert "stop_mode=clone" in chain
    assert "trim=0:10" in chain


def test_overlay_composites_onto_the_stacked_grid_then_converts(tmp_path):
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path),
        )
    )
    assert "[grid][ovl]overlay=0:0" in graph
    assert graph.endswith("format=yuv420p[final]")
    assert graph.index("xstack") < graph.index("overlay=0:0")


def test_a_clock_is_drawn_for_each_tile_that_has_one(tmp_path):
    clocks = (
        mp4_grid.TileClock(row=0, col=0, start_seconds=1.0, freeze_seconds=6.0, final_text="5.00"),
        mp4_grid.TileClock(row=0, col=1, start_seconds=1.0, freeze_seconds=None, final_text=None),
    )
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0), _tile("bo", 0, 1)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path, clocks),
        )
    )
    # ann: ticking + static hold. bo: ticking only.
    assert graph.count("drawtext") == 3
    assert "5.00" in graph


def test_no_clocks_means_no_drawtext(tmp_path):
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path),
        )
    )
    assert "drawtext" not in graph


def test_the_ticking_clock_stops_where_the_static_one_starts(tmp_path):
    clocks = (
        mp4_grid.TileClock(row=0, col=0, start_seconds=1.0, freeze_seconds=6.0, final_text="5.00"),
    )
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path, clocks),
        )
    )
    assert "between(t" in graph
    assert "gte(t" in graph
    assert graph.count("6") >= 2  # the freeze time bounds both filters


def test_clock_is_positioned_inside_its_own_cell(tmp_path):
    clocks = (
        mp4_grid.TileClock(row=1, col=1, start_seconds=1.0, freeze_seconds=None, final_text=None),
    )
    graph = _graph(
        mp4_grid.build_stage_command(
            _plan([_tile("ann", 0, 0), _tile("bo", 0, 1), _tile("cy", 1, 0), _tile("dee", 1, 1)]),
            canvas=CANVAS,
            output_path=tmp_path / "o.mov",
            overlay=_overlay(tmp_path, clocks),
        )
    )
    # cell is 960x540 on a 1920x1080 canvas; the bottom-right cell starts
    # at x=960, y=540.
    assert "960" in graph
    assert "540" in graph
```

- [ ] **Step 3: Run it and watch it fail**

Run: `uv run pytest tests/test_compare_mp4_grid_overlay.py -v`
Expected: FAIL -- `StageOverlayPlan` does not exist.

- [ ] **Step 4: Implement**

Add `TileClock`, `StageOverlayPlan`, the `overlay=None` keyword on `build_stage_command`, and the graph additions in `_build_filter_graph` (pass `overlay` through). Keep the no-overlay path producing the byte-identical string it produces today: build the new parts only when `overlay is not None`.

Use the `drawtext` template you verified in Step 1. Format all times with `:g`.

- [ ] **Step 5: Run every grid test file**

Run:
```bash
uv run pytest tests/test_compare_mp4_grid_overlay.py tests/test_compare_mp4_grid_commands.py \
  tests/test_compare_mp4_grid_plan.py -m "not integration" -v
```
Expected: PASS, and in particular the existing command tests must not have needed a single edit. If you had to change an existing assertion, stop and report it -- the no-overlay path was supposed to be untouched.

- [ ] **Step 6: Wire `render_grid_mp4`**

Add `overlay: bool = False` and `overlay_theme: str = "splitsmith"`. When `overlay` is true, for each stage:

1. `load_overlay_data(shooters)` once for the whole run, not per stage.
2. Build `TilePlacement`s from `plan.tiles` (`present = tile.trim_path is not None`).
3. `build_overlay_states(placements, data_for_this_stage, head_pad_seconds=..., duration_seconds=plan.duration_seconds)`.
4. `write_sprite_sequence(...)` into `work_dir / "sprites"` (shared across stages so the cache does its job), then `write_concat_list` into `work_dir / f"sprites-stage{n}.txt"`.
5. `materialize_font(theme font, work_dir)` once per run.
6. Build the `TileClock`s: `start_seconds = head_pad_seconds`, `freeze_seconds = head_pad_seconds + last_shot_time` when there are shots, `final_text = f"{last_shot_time:.2f}"`. No clock for a tile with no shots.

`head_pad_seconds` is a parameter of `render_grid_mp4` already -- thread the same value, do not hardcode 1.0.

Add tests to the new file using the existing fake-runner pattern from `tests/test_compare_mp4_grid_render.py`: with `overlay=True` a sprite list file exists on disk and appears in the stage command; with `overlay=False` no sprite files are written at all.

- [ ] **Step 7: Mutation check**

1. Insert the sprite input before the unreached-cell inputs -> `test_sprite_input_is_appended_after_every_other_input` must fail.
2. Composite the overlay before `xstack` -> `test_overlay_composites_onto_the_stacked_grid_then_converts` must fail.
3. Drop the `trim` from the sprite chain -> `test_sprite_chain_is_rgba_and_covers_the_whole_stage` must fail.
4. Emit the static clock without the `enable` guard -> `test_the_ticking_clock_stops_where_the_static_one_starts` must fail.
5. Make `overlay=None` still emit the sprite chain -> `test_without_overlay_the_graph_is_untouched` must fail.

- [ ] **Step 8: Full suite, lint, commit**

```bash
uv run pytest -m "not integration" --ignore=tests/test_hosted_docker_smoke.py -q
uv run ruff check src tests && uv run black --check src tests
git add src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_overlay.py
git commit -m "feat(compare): composite the splits overlay into the grid render"
```

---

### Task 6: CLI flag + an integration test that looks at pixels

**Model: Sonnet.** Mechanical wiring, but the integration test is the deliverable that proves any of this reaches a viewer.

**Files:**
- Modify: `src/splitsmith/compare/cli.py`
- Test: `tests/test_compare_cli_mp4.py` (extend)
- Test: `tests/test_compare_grid_overlay_integration.py` (new, `@pytest.mark.integration`)

**Interfaces:**
- Consumes: `mp4_grid.render_grid_mp4(..., overlay=..., overlay_theme=...)`.
- Produces: `splitsmith compare export <match> --format mp4 --overlay [--overlay-theme splitsmith|clean]`.

**Flag contract:** `--overlay/--no-overlay`, default off. `--overlay-theme` defaults to `splitsmith` and accepts the values in `overlay_theme.THEME_NAMES`. `--overlay` with `--format fcpxml` is an error, not a silent no-op -- the FCPXML grid ships clean tiles by decision, so a flag that appears to work and does nothing is worse than a refusal.

- [ ] **Step 1: Write the failing CLI tests**

Add to `tests/test_compare_cli_mp4.py` (using `strip_ansi` from `tests/conftest.py` for any `--help` assertion):

```python
def test_overlay_flag_is_documented(runner):
    result = runner.invoke(compare_app, ["export", "--help"])
    text = strip_ansi(result.output)
    assert "--overlay" in text
    assert "--overlay-theme" in text


def test_overlay_defaults_to_off(monkeypatch, tmp_path, runner):
    captured = {}

    def fake_render(*args, **kwargs):
        captured.update(kwargs)
        return mp4_grid.GridRenderResult(output_path=kwargs["output_path"], stages=())

    monkeypatch.setattr(mp4_grid, "render_grid_mp4", fake_render)
    ...  # invoke `export <match> --format mp4 -o out.mp4`
    assert captured["overlay"] is False


def test_overlay_flag_reaches_the_renderer(monkeypatch, tmp_path, runner):
    ...  # invoke with --overlay --overlay-theme clean
    assert captured["overlay"] is True
    assert captured["overlay_theme"] == "clean"


def test_overlay_with_fcpxml_is_refused(runner, tmp_path):
    result = runner.invoke(
        compare_app, ["export", str(match_dir), "--format", "fcpxml", "--overlay", "-o", str(out)]
    )
    assert result.exit_code != 0
    assert "fcpxml" in strip_ansi(result.output).lower()


def test_unknown_overlay_theme_is_refused(runner, tmp_path):
    result = runner.invoke(compare_app, [... "--overlay", "--overlay-theme", "neon" ...])
    assert result.exit_code != 0
```

Follow the existing fixtures in that file for how a match dir and the `runner` are built -- do not invent a new harness.

- [ ] **Step 2: Run, watch it fail, implement the flags**

Run: `uv run pytest tests/test_compare_cli_mp4.py -v`
Then add the two Typer options and thread them through `_export_from_match` -> `_render_grid_mp4` -> `render_grid_mp4`. Validate the theme name against `THEME_NAMES` and the format combination in `export`, printing the same `[red]Error:[/]` style the neighbouring validations use.

- [ ] **Step 3: Re-run under CI's rich settings**

Run: `GITHUB_ACTIONS=true uv run pytest tests/test_compare_cli_mp4.py -v`
Expected: PASS. rich interleaves ANSI escapes when it detects CI, and a literal substring check that passes locally fails there.

- [ ] **Step 4: Write the integration test**

Create `tests/test_compare_grid_overlay_integration.py`. It builds its inputs with `tests/synthetic_media.py` -- it must never skip in CI.

Roster: **three shooters**, which puts one unreached cell in the 2x2 grid. One of the three has **no audit file**, so the no-data degradation is on the rendered path and not only in unit tests.

```python
@pytest.mark.integration
def test_overlay_reaches_the_rendered_pixels(tmp_path):
    """Render the same stage twice, with and without --overlay, and
    compare decoded frames. A command-string assertion cannot tell you
    the viewer sees anything."""
```

Assertions, all against decoded frames extracted with real ffmpeg:

1. Both renders produce a playable file with **1 video stream and 4 audio streams** (3 shooters + the mix). The overlay must not change the stream layout -- that is invariant 1, and it is what `concat -c copy` enforces.
2. A frame sampled *after* the first shot differs from the corresponding no-overlay frame **inside the firing shooter's own quadrant**, measured as mean absolute pixel difference above a threshold.
3. The same frame is **unchanged in the unreached cell's quadrant** except within the delta strip band. An empty cell is not a shooter.
4. A frame sampled *before* the beep is unchanged everywhere outside the strip band -- nothing is drawn before there is anything to draw.
5. The overlay render's duration matches the no-overlay render's duration to within one frame. The overlay must not extend the segment.
6. Decoded audio sample counts match between the two renders (`nb_frames * 1024 / sample_rate`, honouring the edit list). **Do not compare `ffprobe`'s declared durations** -- container metadata lied by 351ms on this codebase before, and `silencedetect` trusts the same table.

Extract frames with `ffmpeg -ss T -i out.mp4 -frames:v 1 frame.png` and compare with PIL. Put the numeric thresholds in module constants with a comment saying what they were measured at.

- [ ] **Step 5: Run the integration suite the way CI does**

Run:
```bash
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest -m integration \
  --ignore=tests/test_hosted_docker_smoke.py -v
```
Expected: 20+ passed, **0 skipped**. A skip is a build failure in CI.

- [ ] **Step 6: Look at the output**

Render a short overlay grid by hand and open it:

```bash
uv run splitsmith compare export <a real match dir> --format mp4 --overlay \
  --audio-from "<shooter>" -o /home/mathias/.claude-tmp/overlay-preview.mp4
```

If no real match is reachable on this machine, render from the integration test's synthetic inputs instead. Watch it. Report what the counters, splits, clock and strip actually looked like -- position, legibility, whether the clock reads correctly against the shot times. A green suite is not evidence anyone can read the overlay.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/compare/cli.py tests/test_compare_cli_mp4.py tests/test_compare_grid_overlay_integration.py
git commit -m "feat(compare): add --overlay to the grid MP4 export"
```

**Milestone A ends here.** Open the PR, get it merged, and branch Milestone B from `main`.

---

### Task 7: The hold's duration model

**Model: Opus.** Duration arithmetic that spans video and audio in the same segment, guarded by the invariant whose failure mode is a stitch error after every stage has already been encoded.

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py`
- Test: `tests/test_compare_mp4_grid_hold.py` (new)

**Interfaces:**
- Produces:
  - `GridStagePlan.hold_seconds: float = 0.0` -- new field, defaulted so every existing construction site keeps working
  - `GridStagePlan.total_seconds -> float` -- property, `duration_seconds + hold_seconds`
  - `build_stage_plans(..., hold_seconds: float = 0.0)`

**The split between the two durations, precisely:**

- `duration_seconds` stays what it is today: the **action** -- head pad plus the longest post-beep span plus tail pad. Footage, tile chains and the `xstack` all still run for exactly this long.
- `hold_seconds` is the frozen summary that follows it.
- `total_seconds` is the segment. **Every audio chain runs `total_seconds`** (silence through the hold) and the segment's video is the action followed by the still.

Getting this backwards -- extending the tile chains to `total_seconds` -- would run the footage on under the summary instead of freezing it, and would look almost right in a thumbnail.

**Why the hold lives inside the stage's own segment** rather than becoming a segment of its own: the cross-stage stitch stays a dumb `concat -c copy`. A separate hold segment would have to match the stream layout exactly anyway, and would double the number of segments to keep uniform. The spec settled this; do not revisit it in code.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compare_mp4_grid_hold.py` covering:

```python
def test_hold_defaults_to_zero_and_total_equals_duration()
def test_total_seconds_is_action_plus_hold()
def test_hold_seconds_reaches_every_plan(...)          # build_stage_plans(hold_seconds=3.0)
def test_negative_hold_is_rejected(...)                # ValueError, like the existing pad validation
def test_zero_hold_produces_the_command_main_produces_today(...)
def test_audio_chains_run_the_whole_segment_including_the_hold(...)
def test_tile_chains_still_run_only_the_action(...)
def test_every_audio_track_is_the_same_length_as_every_other(...)
def test_stream_counts_are_unchanged_by_the_hold(...)  # N+1 audio, 1 video
```

The last three are invariant 1 written as tests. `test_zero_hold_produces_the_command_main_produces_today` is the default-off guarantee: with `hold_seconds=0.0` the argv must be byte-identical to the pre-change output. Capture that expected argv from `main` before you start.

- [ ] **Step 2: Run, watch it fail, implement**

Add the field, the property, the `hold_seconds` parameter with its validation, and change **only the audio-chain durations** (`apad`, `atrim`) from `duration_seconds` to `total_seconds`. The video half is Task 9.

- [ ] **Step 3: Run every grid test file**

Run: `uv run pytest tests/test_compare_mp4_grid_hold.py tests/test_compare_mp4_grid_plan.py tests/test_compare_mp4_grid_commands.py tests/test_compare_mp4_grid_overlay.py -m "not integration" -v`
Expected: PASS with no edits to the pre-existing files.

- [ ] **Step 4: Mutation check**

1. Make tile chains use `total_seconds` -> `test_tile_chains_still_run_only_the_action` must fail.
2. Leave audio at `duration_seconds` -> `test_audio_chains_run_the_whole_segment_including_the_hold` must fail.
3. Accept a negative hold -> `test_negative_hold_is_rejected` must fail.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/compare/mp4_grid.py tests/test_compare_mp4_grid_hold.py
git commit -m "feat(compare): model the post-stage hold inside the stage segment"
```

---

### Task 8: `overlay_summary.py` -- freeze, blur once, compose

**Model: Sonnet.** Bounded PIL and one ffmpeg extraction call with an injectable runner; the numbers it renders are specified below rather than designed here.

**Files:**
- Create: `src/splitsmith/compare/overlay_summary.py`
- Test: `tests/test_compare_overlay_summary.py`

**Interfaces:**
- Consumes: `overlay_data.TileStageData`, `overlay_sprites.SpriteGeometry`, `TilePlacement`, `overlay_text._load_font` / `_draw_text_with_shadow`, `overlay_theme.OverlayTheme`, `mp4_grid.Runner`.
- Produces:
  - `extract_freeze_frames(plan, *, canvas, work_dir, ffmpeg_binary, runner) -> dict[str, Path]` -- one still per present tile, taken at the last action frame
  - `build_hold_still(placements, data, freezes, geometry, *, theme, blur_radius: int, dim: float) -> Image.Image` -- canvas-sized RGB
  - `write_hold_still(...) -> Path`

**Blur once, not per frame.** The tile is a still, so the blurred, dimmed frame is computed one time in PIL and held. Applying `gblur` to every frame of a multi-second 4K hold costs orders of magnitude more for an identical result. If you find yourself adding a blur filter to the ffmpeg graph, you have taken the wrong path.

**Extraction:** one `ffmpeg -ss <t> -i <trim> -frames:v 1 <png>` per present tile, where `t` is the tile's source-time corresponding to the last action frame (`tile.seek_seconds + plan.duration_seconds - tile.lead_pad_seconds - one_frame`). Go through the injected `runner`, so unit tests never shell out. A tile whose extraction fails degrades to a black cell -- one unreadable trim must not lose the stage.

**What each cell shows** (omit any line whose data is absent -- never draw a zero for a missing number):

- The shooter's label.
- `N shots` (from the audit).
- `Time  12.34` from `stage_time_seconds`. If `stage_time_is_manual`, append ` (manual)`.
- `HF 5.12` from `scorecard.hit_factor`.
- `Stage 87.4%` from `scorecard.stage_pct`. **Never `stage_points`** -- raw points are meaningless across stages and divisions, and `stage_pct` is already persisted.
- `A7 C2 D1 M0 NS0` from the hit counts, omitting any that are `None`.
- `DQ` when `scorecard.dq` is true, and nothing else scoring-related.
- Split statistics from the audit: `Best 0.18  Avg 0.24  Worst 0.41`, computed over shots 2..N (shot 1's "split" is the draw and would drag the average). Show `Draw 1.02` separately.

A tile with no audit and no scorecard shows its label and nothing else. A filler tile shows a black cell with no text.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compare_overlay_summary.py`. Cover, at minimum:

```python
def test_freeze_extraction_asks_for_one_frame_at_the_last_action_moment()  # fake runner, assert argv
def test_freeze_extraction_skips_filler_tiles()
def test_a_failed_extraction_degrades_to_a_black_cell()
def test_blur_is_applied_once_per_tile_not_per_frame()   # count PIL filter calls via monkeypatch
def test_hold_still_is_canvas_sized_rgb()
def test_each_cell_draws_over_its_own_freeze_frame()      # ink lands in the right quadrant
def test_missing_scorecard_omits_the_scoring_lines()
def test_stage_points_never_appears_and_stage_pct_does()
def test_manual_time_is_marked_as_manual()
def test_dq_replaces_the_scoring_lines()
def test_splits_exclude_the_draw_from_best_average_worst()
def test_a_tile_with_no_audit_shows_only_its_label()
def test_a_filler_tile_is_black_with_no_text()
```

`test_stage_points_never_appears_and_stage_pct_does` deserves a scorecard where the two numbers are distinguishable (e.g. `stage_points=143.2`, `stage_pct=87.4`) so the assertion cannot pass by coincidence.

- [ ] **Step 2: Run, watch it fail, implement**

Build the still by pasting each tile's blurred, dimmed freeze frame into its cell, then drawing the text over it with `_draw_text_with_shadow`. Dim by compositing a black layer at `dim` alpha (default `0.45`); blur with `ImageFilter.GaussianBlur(blur_radius)` (default scaled from cell height, `max(8, cell_height // 60)`).

- [ ] **Step 3: Look at one**

Compose a summary still from a synthetic freeze frame and open it, as in Task 4 Step 6. Confirm the numbers are legible over the blurred tile and that nothing overflows a cell. Report what you saw.

- [ ] **Step 4: Mutation check**

1. Use `stage_points` instead of `stage_pct` -> `test_stage_points_never_appears_and_stage_pct_does` must fail.
2. Include shot 1 in the split statistics -> `test_splits_exclude_the_draw_from_best_average_worst` must fail.
3. Draw `0` for a `None` hit count -> `test_missing_scorecard_omits_the_scoring_lines` must fail.
4. Blur inside the per-tile draw loop twice -> `test_blur_is_applied_once_per_tile_not_per_frame` must fail.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/compare/overlay_summary.py tests/test_compare_overlay_summary.py
git commit -m "feat(compare): compose the frozen post-stage summary still"
```

---

### Task 9: Wire the hold into the render, the CLI, and a real file

**Model: Opus.** The seam where the duration model, the graph and the stitch meet -- and the one place a phase-0 defect lived in a seam no single task owned.

**Files:**
- Modify: `src/splitsmith/compare/mp4_grid.py`, `src/splitsmith/compare/cli.py`
- Test: `tests/test_compare_mp4_grid_hold.py` (extend), `tests/test_compare_grid_overlay_integration.py` (extend)

**Interfaces:**
- Produces: `render_grid_mp4(..., summary_hold_seconds: float = 0.0)`; `splitsmith compare export ... --summary-hold SECONDS`.

**The graph change:**

```
[grid][ovl]overlay=0:0[ovlgrid] -> [ovlgrid][hold]concat=n=2:v=1:a=0[joined] -> format=yuv420p[final]
```

with the still supplied as `-loop 1 -t {hold_seconds} -i {hold_png}`, appended **after the sprite input** so nothing behind it shifts. `concat`'s inputs must agree on size, SAR and frame rate: the still input needs the same `fps={rate}` and `setsar=1` treatment the tile chains get.

**The live overlay stops at the freeze.** The sprite chain's `trim` already ends at `duration_seconds`, so the sprite stream simply does not reach the hold -- and it must not, because a frozen shot counter beside a stopped clock reads as a stall rather than a conclusion. Likewise the `drawtext` clocks: their `enable` windows must end at `duration_seconds` at the latest. Verify this rather than assuming: `enable='gte(t,freeze)'` with no upper bound would carry the static clock straight into the summary.

**`--summary-hold` contract:** a float, default `0.0` (off). Non-zero requires `--overlay` -- a summary hold on a grid with no overlay is a design contradiction and should be refused with a message naming `--overlay`, not silently accepted. Values above, say, 30s are almost certainly a typo; accept them but print a warning.

- [ ] **Step 1: Extend the unit tests**

```python
def test_hold_still_input_is_appended_after_the_sprite_input()
def test_hold_is_concatenated_after_the_action_not_composited_over_it()
def test_the_still_input_is_looped_for_exactly_the_hold_duration()
def test_the_sprite_overlay_does_not_reach_the_hold()
def test_clock_enable_windows_end_before_the_hold_begins()
def test_zero_hold_emits_no_still_input_and_no_concat()
def test_stream_layout_is_identical_with_and_without_the_hold()
```

- [ ] **Step 2: Run, watch them fail, implement**

Thread `summary_hold_seconds` from `render_grid_mp4` into `build_stage_plans(hold_seconds=...)`, extract freeze frames, compose the still per stage, and add the input and the `concat` to `build_stage_command`.

- [ ] **Step 3: CLI flag**

Add `--summary-hold` to `compare export`, refuse it without `--overlay`, and add CLI tests mirroring Task 6's (including the `--help` assertion with `strip_ansi` and a `GITHUB_ACTIONS=true` run).

- [ ] **Step 4: Extend the integration test**

Add to `tests/test_compare_grid_overlay_integration.py`, still with the three-shooter roster and one auditless shooter:

1. A **two-stage** render with `--summary-hold 2.0` produces a file whose duration is the two actions plus two holds, measured from **decoded frames**, not from the container's declared duration.
2. The stitch succeeds -- which is the real test of invariant 1, since `concat -c copy` is what rejects a segment whose stream layout drifted.
3. A frame sampled inside the hold is **visibly blurred** relative to the last action frame: assert a drop in high-frequency energy (e.g. the variance of a Laplacian, or mean absolute difference between the frame and its own 3x3 box blur) rather than trying to assert on the text.
4. A frame sampled inside the hold contains the summary's ink: it differs from the plain blurred freeze frame in the tile's own quadrant.
5. Decoded audio runs the **whole** segment including the hold, and every track is the same length. Measure decoded samples honouring the edit list.
6. Two stages, so segment 2's audio has to start where segment 1's ended: assert no cumulative offset between the video and audio ends. Container metadata lies here -- decode.

- [ ] **Step 5: Run the full suites the way CI does**

```bash
uv run pytest -m "not integration" --ignore=tests/test_hosted_docker_smoke.py -q
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest -m integration --ignore=tests/test_hosted_docker_smoke.py -v
uv run ruff check src tests && uv run black --check src tests
```
Expected: unit green, integration green with **0 skipped**, lint clean.

- [ ] **Step 6: Watch the video**

Render two stages with `--overlay --summary-hold 3` and watch the transition from action to summary. Does the live overlay disappear cleanly at the freeze? Does the summary hold long enough to read? Does the next stage start cleanly? Report what you saw; this is the only assertion that covers whether the handoff reads as a conclusion rather than a stall.

- [ ] **Step 7: Update the handoff doc**

`docs/superpowers/plans/2026-08-04-compare-grid-mp4-phase-0-handoff.md` tells the machine holding the real match what to expect from `ffprobe`. If the output shape changed -- and a hold changes durations -- update those expectations. A stale verification checklist is worse than none.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/compare/mp4_grid.py src/splitsmith/compare/cli.py \
  tests/test_compare_mp4_grid_hold.py tests/test_compare_grid_overlay_integration.py \
  docs/superpowers/plans/2026-08-04-compare-grid-mp4-phase-0-handoff.md
git commit -m "feat(compare): hold a frozen stage summary at the end of each stage"
```

---

## Self-review notes

**Spec coverage.** Phase 1 items 4-6 map to Tasks 1, 3-4 and 5-6. The "Where the two overlays live in the frame" section maps to Tasks 7-9. Phase 2 items 7-8 (transitions, title cards, opening screen) are **not** in this plan -- they force a re-encode at the concat seams, which is a separate code path, and the user's decision was that the live overlay and the *stage* summary are the pair that share frames. The end-of-match summary is deferred with them.

**Deliberate divergences from the spec, each flagged where it occurs:**

1. **No golden-hash sprite test** (Task 4). A hash over rasterized glyphs pins the suite to a Pillow version and catches nothing the structural pixel assertions miss.
2. **The last-split label does not fade** (Task 4). `DefaultTemplate` fades it after a hold; a step function cannot fade without inventing states, and in a grid the viewer wants to read any tile at any moment.
3. **Scoring data is loaded in a new module, not added to `CompareStageBundle`** (Task 2), so the FCPXML grid does not pay to read audits it will never draw.

**Type consistency.** `TileStageData` (Task 2) is consumed by name in Tasks 3, 4 and 8. `TilePlacement` and `SpriteGeometry` (Tasks 3, 4) are consumed in Tasks 5 and 8. `StageOverlayPlan` (Task 5) is extended, not replaced, in Task 9. `hold_seconds` / `total_seconds` (Task 7) are consumed in Task 9.

**Known risk, called out rather than hidden:** the `drawtext` escaping in Task 5 is the one thing here that cannot be settled from reading. That is why Task 5 Step 1 renders and looks before any builder code is written.
