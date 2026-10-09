# MP4 Transitions on the Boundary Segment (single-shooter) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `splitsmith match export --format mp4` (and the Export page) renders a real crossfade between stages: a curated xfade over one re-encoded boundary segment per transition, the neighbours trimmed, the stitch still a video stream copy.

**Architecture:** `plan_timeline` turns the stage-indexed `Composition.transitions` into spine *boundaries* between consecutive items (a slate, a summary hold and a card count as items, so a transition into a slate or out of a summary is the same code). For a transition of d seconds the renderer encodes two short *edge* renders with the existing command builders (N's last d/2 plus d/2 of handle material after it, N+1's d/2 of handle before plus its first d/2), xfades them into one boundary segment of length d with `acrossfade` on the audio, and encodes the neighbours trimmed by d/2 each. The timeline length is unchanged, so chapters, the duration estimate and `duration_seconds` stay what they were. A stage's handle is the footage beyond its pad that the trim already contains (`head_trim_seconds` before, `tail_trim` after); a card's handle is its own first or last frame. A transition that does not fit is reported as a degradation and rendered as a cut, never clamped. Both trimmed neighbours and the boundary go through `encode`, so the segment cache keys the boundary by its two edge files (content-hashed in `work_dir`) and the edges by their argv, which is what the spec means by "both neighbours' argv plus the transition".

**Scope:** `mp4_render` only. `compare/mp4_grid` encodes action and hold in one segment whose per-tile seeks, overlay sprite and clock windows all derive from the global head pad; giving it a per-stage trim lever is its own plan (follow-up under #1244, after this merges). The request layer keeps refusing transitions for the grid until then.

**Tech Stack:** Python 3.12, ffmpeg `xfade` / `acrossfade` (core filters, present in the project's static build), pytest, the frame scripts.

**Spec:** `docs/superpowers/specs/2026-10-06-rendered-video-first-design.md` section 3. Issue #1244 (absorbs #1156).

## Global Constraints

- Python 3.11+, type hints everywhere, Black 110, Ruff, `pathlib.Path`.
- No new dependencies.
- A render with no transition produces byte-identical ffmpeg argv to today for every segment and the stitch: every new parameter defaults to a value that emits nothing. The 44-frame pixel gate against main (`scripts/render_match_frames.py` slate / lower-third / clean, `scripts/render_grid_frames.py`) stays 44 of 44 identical.
- Degradation policy: a transition never fails a render. A boundary that cannot be built (edge render failed, material short) becomes a cut with one line in `Mp4RenderResult.degradations`.
- Run pytest and the frame scripts with the project's static ffmpeg first on PATH (`desktop/build/bin`); Homebrew's lacks `drawtext`.
- `segment_cache.KEY_VERSION` becomes 3 (new command shapes).
- Curated xfade kinds, in this order: `fade, fadeblack, dissolve, slideleft, slideright, circleopen, zoomin, hblur, smoothleft, wipeleft`.

## Review Focus

1. A transition whose d/2 exceeds a stage's tail pad, head pad, or the handle the trim holds beyond the pad must come out as a cut plus a degradation naming the stage and the pad, never a clamped shorter fade. Test in Task 2 (`test_a_transition_that_does_not_fit_is_a_cut_and_a_degradation`).
2. With transitions on, the stitched duration equals the no-transition duration to one frame, and `compute_chapters` needs no change. Test in Task 4 (`test_transitions_keep_the_timeline_length`).
3. A lower third on a stage that follows a boundary must not restart: its first d/2 shows in the boundary's head edge and the trimmed stage continues it. Test in Task 3 (argv of `lower_third_filters` with `delay`/`skip`) and the frame look in Task 6.
4. An edge render that fails (ffmpeg error, no rasterizer for a card) must leave both neighbours untrimmed. Test in Task 4 (`test_a_failed_edge_leaves_the_neighbours_untrimmed`).
5. The FCPXML path must keep working for every request kind: an xfade kind sent with `--format fcpxml` renders as `zoom` with an anomaly, never a `KeyError`. Test in Task 1.

---

### Task 1: The catalog and the request layer's kinds

**Files:**
- Modify: `src/splitsmith/composition.py` (`TransitionKind`, `XFADE_KINDS`, `xfade_name`, `_lower_transitions`)
- Modify: `src/splitsmith/ui/match_exports.py:52` (`TransitionKind`), `src/splitsmith/ui/exports_api.py:137`, `src/splitsmith/export_presets.py:82`, `src/splitsmith/mcp/server.py:326`, `src/splitsmith/mcp/export_tools.py:214`
- Test: `tests/test_composition.py`, `tests/test_ui_match_exports.py`

**Interfaces:**
- Produces: `composition.XFADE_KINDS: tuple[str, ...]` (the ten names), `composition.FCP_KINDS = ("zoom", "static")`, `composition.TransitionKind = Literal["zoom", "static", "fade", "fadeblack", "dissolve", "slideleft", "slideright", "circleopen", "zoomin", "hblur", "smoothleft", "wipeleft"]`, `composition.xfade_name(kind: TransitionKind) -> str` (identity for xfade kinds, `zoom -> "zoomin"`, `static -> "fadeblack"`), `composition.fcp_kind(kind) -> tuple[Literal["zoom","static"], bool]` (the FCP effect and whether it was a substitution).
- Consumed by Tasks 2 to 6.

- [ ] **Step 1: Failing tests**

```python
# tests/test_composition.py
def test_the_transition_catalog_is_the_curated_xfade_list_plus_the_fcp_effects() -> None:
    from splitsmith import composition

    assert composition.XFADE_KINDS == (
        "fade", "fadeblack", "dissolve", "slideleft", "slideright",
        "circleopen", "zoomin", "hblur", "smoothleft", "wipeleft",
    )
    assert composition.xfade_name("fade") == "fade"
    assert composition.xfade_name("zoom") == "zoomin"
    assert composition.xfade_name("static") == "fadeblack"
    assert composition.fcp_kind("zoom") == ("zoom", False)
    assert composition.fcp_kind("hblur") == ("zoom", True)


def test_lowering_an_xfade_kind_to_fcpxml_substitutes_zoom() -> None:
    from splitsmith import composition

    lowered = composition._lower_transitions(
        (composition.Transition(from_stage_index=0, to_stage_index=1, kind="dissolve", duration_seconds=1.0),)
    )
    assert lowered[0].kind == "zoom"
```

```python
# tests/test_ui_match_exports.py
def test_an_xfade_kind_on_the_fcpxml_path_is_rendered_as_zoom_with_an_anomaly(tmp_path: Path) -> None:
    """Review Focus 5: the frozen emitter never sees a kind it does not know."""
    ...  # build a two-stage request with output_format="fcpxml", transition_kind="dissolve",
         # run export_match with the stubbed probe/ffmpeg the file's other fcpxml tests use,
         # assert result.anomalies contains
         # "transition dissolve is not an FCP effect; the FCPXML uses zoom"
         # and the emitted fcpxml names the zoom effect uid.
```

- [ ] **Step 2: Run, expect `AttributeError: XFADE_KINDS` / `ImportError`.**
- [ ] **Step 3: Implement** in `composition.py`:

```python
XFADE_KINDS: tuple[str, ...] = (
    "fade", "fadeblack", "dissolve", "slideleft", "slideright",
    "circleopen", "zoomin", "hblur", "smoothleft", "wipeleft",
)
FCP_KINDS: tuple[str, ...] = ("zoom", "static")
TransitionKind = Literal[
    "zoom", "static", "fade", "fadeblack", "dissolve", "slideleft", "slideright",
    "circleopen", "zoomin", "hblur", "smoothleft", "wipeleft",
]
_XFADE_FOR_FCP = {"zoom": "zoomin", "static": "fadeblack"}


def xfade_name(kind: TransitionKind) -> str:
    """The ffmpeg xfade transition for a kind: the xfade kinds are their own name; the two FCP effects take the nearest xfade."""
    return _XFADE_FOR_FCP.get(kind, kind)


def fcp_kind(kind: TransitionKind) -> tuple[Literal["zoom", "static"], bool]:
    """The FCP effect a kind lowers to and whether that is a substitution (an anomaly for the caller to report)."""
    if kind in FCP_KINDS:
        return kind, False  # type: ignore[return-value]
    return "zoom", True
```

`_lower_transitions` calls `fcp_kind(t.kind)[0]`. In `match_exports.export_match`, right where transitions are built for the fcpxml path, append `f"transition {t.kind} is not an FCP effect; the FCPXML uses zoom"` once per distinct substituted kind. Widen the five request-layer `Literal`s to `composition.TransitionKind | Literal["none"]` (import it, do not re-type the list).

- [ ] **Step 4: Run the two test files, expect PASS. Lint. Commit:** `feat(transitions): the curated xfade catalog on TransitionKind; FCPXML substitutes zoom with an anomaly (#1244)`

---

### Task 2: Boundaries in the timeline plan and the fit check

**Files:**
- Modify: `src/splitsmith/mp4_render.py` (`_StagePlan`, item dataclasses, `TimelinePlan`, `plan_timeline`, new `_Boundary`, `_narrow_plan`, `_boundary_fit`)
- Test: `tests/test_mp4_render.py`

**Interfaces:**
- Produces:
  - `_Boundary(after_index: int, kind: TransitionKind, duration_seconds: float)`, frozen. `after_index` is the index into `TimelinePlan.items` of the item before the boundary.
  - every spine item gains `head_cut_seconds: float = 0.0` and `tail_cut_seconds: float = 0.0`; `duration_seconds` returns the natural length minus both cuts.
  - `TimelinePlan.boundaries: tuple[_Boundary, ...]` and `TimelinePlan.degradations: tuple[str, ...]` (the fit failures).
  - `_narrow_plan(plan: _StagePlan, *, head_cut: float, tail_cut: float) -> _StagePlan`: `head_trim += head_cut`, `effective -= head_cut + tail_cut`, cams recomputed with `_plan_stage`'s formulas (`delta = (beep - head_trim) - sec.beep`; `seek`/`spine_start`; `visible = max(0, min(cam_dur - seek, effective - spine_start))`). Negative `head_cut` is allowed (a handle before the stage), the primary's `-ss` then reads earlier footage; the caller guarantees `head_trim + head_cut >= 0`.
  - `_boundary_fit(prev, next, *, half: float) -> str | None`: `None` when it fits, else the degradation line. Rules: a `_StageItem` on the *prev* side needs `half <= stage.tail_pad_seconds` ("the last shot stays out of the fade") and `half <= tail_trim` (the handle); on the *next* side `half <= head_pad_seconds` and `half <= head_trim_seconds`. A still, summary or clip item needs `half <= duration_seconds / 2` on either side. Message shape (mirrors the FCPXML wording): `f"transition after stage {name!r} ({d:g}s) exceeds the stage's tail pad ({pad:g}s); increase the pad or shorten the transition: rendered as a cut"` and `... before stage ... head pad ...`; for a card `f"transition into {item.name} ({d:g}s) exceeds half the card ({dur/2:g}s): rendered as a cut"`.
- `_StagePlan` gains `tail_trim_seconds: float` (today only `effective` is kept; the handle check needs the tail).

Boundary placement in `plan_timeline`: for each `Transition(from_stage_index=i, to_stage_index=i+1)`, prev = the last item whose stage index is i (the summary when present, else the stage), next = the first item of stage i+1 (its slate when present, else the stage). The title page and the closing card get no boundary (the request layer builds stage-to-stage transitions only; FCPXML today is the same).

- [ ] **Step 1: Failing tests** (`tests/test_mp4_render.py`)

```python
def test_plan_timeline_places_a_boundary_between_a_summary_and_the_next_slate() -> None:
    comp = _two_stage_composition(titles="slate", summaries=True, transitions=("fade", 1.0))
    plan = mp4_render.plan_timeline(comp)
    names = [getattr(i, "name", i.kind) for i in plan.items]
    assert names == ["title_page", "slate_000", "stage", "summary_000", "slate_001", "stage", "closing"]
    (b,) = plan.boundaries
    assert (b.after_index, b.kind, b.duration_seconds) == (3, "fade", 1.0)
    assert plan.items[3].tail_cut_seconds == 0.5 and plan.items[4].head_cut_seconds == 0.5
    assert plan.items[3].duration_seconds == comp.stages[0].summary.duration_seconds - 0.5


def test_plan_timeline_without_transitions_has_no_boundaries_and_no_cuts() -> None:
    plan = mp4_render.plan_timeline(_two_stage_composition())
    assert plan.boundaries == () and all(i.head_cut_seconds == i.tail_cut_seconds == 0.0 for i in plan.items)


def test_a_transition_that_does_not_fit_is_a_cut_and_a_degradation() -> None:
    """Review Focus 1: pads and handles bound the fade; nothing is clamped."""
    comp = _two_stage_composition(transitions=("fade", 4.0), tail_pad=1.0, head_pad=1.0)
    plan = mp4_render.plan_timeline(comp)
    assert plan.boundaries == ()
    assert plan.degradations == (
        "transition after stage 'Stage 1' (4s) exceeds the stage's tail pad (1s); "
        "increase the pad or shorten the transition: rendered as a cut",
    )
    assert all(i.head_cut_seconds == i.tail_cut_seconds == 0.0 for i in plan.items)


def test_narrow_plan_recomputes_the_cams_from_the_new_head_trim() -> None:
    plan = mp4_render._plan_stage(_stage_with_a_secondary(), _SEQUENCE)
    narrowed = mp4_render._narrow_plan(plan, head_cut=0.5, tail_cut=0.25)
    assert narrowed.head_trim_seconds == plan.head_trim_seconds + 0.5
    assert narrowed.effective_seconds == plan.effective_seconds - 0.75
    cam = narrowed.cam_alignments[0]
    expected = mp4_render._plan_stage(_stage_with_a_secondary(head_pad=_HEAD_PAD - 0.5), _SEQUENCE).cam_alignments[0]
    assert (cam.cam_seek_seconds, cam.cam_spine_start) == (expected.cam_seek_seconds, expected.cam_spine_start)
```

(`_two_stage_composition` and `_stage_with_a_secondary` are new helpers in the test file next to the existing ones at lines 740-771; `_SEQUENCE` is the file's sequence format.)

- [ ] **Step 2: Run, expect `AttributeError: boundaries` / `TypeError` on `transitions=`.**
- [ ] **Step 3: Implement.** Keep `_plan_stage` as is except recording `tail_trim_seconds`. `plan_timeline` builds the items as today, then walks `composition.transitions` sorted by `from_stage_index`, finds prev / next item indices, runs `_boundary_fit` with `half = d / 2`, and either appends a `_Boundary` and replaces the two items with `dataclasses.replace(item, tail_cut_seconds=half)` / `head_cut_seconds=half`, or appends the degradation. `TimelinePlan.duration_seconds` is `sum(item.duration_seconds) + sum(b.duration_seconds)`, which equals the no-transition sum (pin this in Task 4).
- [ ] **Step 4: Run `tests/test_mp4_render.py`, expect PASS; the existing plan tests (740-771) unchanged. Lint. Commit:** `feat(transitions): boundaries and cuts in the single-shooter timeline plan (#1244)`

---

### Task 3: The command builders: edges, the boundary, offsets that emit nothing by default

**Files:**
- Modify: `src/splitsmith/overlay_card.py` (`lower_third_filters`, `lower_third_clip_filters`), `src/splitsmith/mp4_render.py` (`_build_stage_command`, `_build_motion_card_command`, new `_build_boundary_command`, `_build_still_command` untouched)
- Test: `tests/test_overlay_card.py`, `tests/test_mp4_render.py`

**Interfaces:**
- `lower_third_filters(input_index, seconds, *, source_label, delay_seconds: float = 0.0, skip_seconds: float = 0.0)`: shown during `between(t, delay, delay + seconds - skip)`; the fade-out starts at `delay + seconds - skip - 0.5`. For the PNG the image is static so only the timings move. `lower_third_clip_filters(..., rate, source_label, delay_seconds=0.0, skip_seconds=0.0)`: `trim=start={skip}`, `setpts=PTS-STARTPTS+{delay}/TB` (omit the `+...` when delay is 0), the `tpad`/`trim` lengths reduced by `skip`, then the same enable window. With both at 0 the strings are byte-identical to today (`assert ... == ` the current literal in the test).
- `_LowerThirdInput` gains `delay_seconds: float = 0.0`, `skip_seconds: float = 0.0`; `_build_stage_command` passes them through.
- `_build_motion_card_command(..., clip_offset_seconds: float = 0.0, clip_delay_seconds: float = 0.0)`: `-ss {offset:g}` before the clip's `-i` when offset > 0; `setpts=PTS-STARTPTS+{delay}/TB` prepended to the motion overlay chain when delay > 0 (the backdrop shows alone until the clip starts). Argv unchanged at 0 / 0.
- `_build_boundary_command(tail_edge: Path, head_edge: Path, *, kind: TransitionKind, seconds: float, sequence, output_path, ffmpeg_binary="ffmpeg", youtube_preset=False) -> tuple[str, ...]`:

```python
(
    ffmpeg_binary, "-y", "-hide_banner", "-loglevel", "error",
    "-i", str(tail_edge), "-i", str(head_edge),
    "-filter_complex",
    f"[0:v][1:v]xfade=transition={xfade_name(kind)}:duration={seconds:g}:offset=0,format=yuv420p[final];"
    f"[0:a][1:a]acrossfade=d={seconds:g}:c1=tri:c2=tri[aout]",
    "-map", "[final]", "-map", "[aout]",
    "-t", f"{seconds:g}",
    *_encode_args(sequence, youtube_preset=youtube_preset),
    str(output_path),
)
```

  Both edges are exactly `seconds` long, so `offset=0` crossfades the whole boundary. Edges always carry audio (a stage's `[aout]`, a still's `anullsrc`), so `acrossfade` always has two inputs.

- Edge recipes (used by Task 4, defined here as pure helpers returning argv):
  - `_tail_edge_command(item, *, half, ...)` and `_head_edge_command(item, *, half, ...)`:
    - `_StageItem`: `_build_stage_command(_narrow_plan(plan, head_cut=effective - half, tail_cut=-half), ...)` for the tail (the last `half` of the effective range plus `half` of handle after it; the handle exists by the fit check); `_build_stage_command(_narrow_plan(plan, head_cut=-half, tail_cut=effective - half), lower_third=replace(lt, delay_seconds=half))` for the head. The primary audio: the `atrim` window moves with the plan, nothing else to do. The overlay input's `-ss`/`-t` move with the plan; the overlay MOV covers the whole trim (verify in Step 0 below and pin with an assertion in `_grab`-style test if false).
    - `_StillItem` / `_SummaryItem` (still): `_build_still_command(png, seconds=2*half, ...)` for either end (a still's handle is itself).
    - `_StillItem` with a motion clip: head edge `_build_motion_card_command(backdrop, clip, seconds=2*half, clip_delay_seconds=half)`; tail edge `_build_motion_card_command(backdrop, clip, seconds=2*half, clip_offset_seconds=card_seconds - half)` (the clip's `tpad=stop_mode=clone` holds the last frame through the handle).
    - `_ClipItem` (intro/outro): no boundaries reach them in this plan (stage-to-stage only).
  - The trimmed neighbour: `_StageItem` with cuts encodes `_build_stage_command(_narrow_plan(plan, head_cut=item.head_cut_seconds, tail_cut=item.tail_cut_seconds), lower_third=replace(lt, skip_seconds=item.head_cut_seconds))`; stills `seconds=item.duration_seconds`; motion cards `clip_offset_seconds=item.head_cut_seconds`.

- [ ] **Step 0: The overlay MOV covers the trim** (checked while planning: `overlay_render.py` renders "same fps, resolution and duration" as the trimmed clip, `duration_seconds = probe.duration_seconds` at line 563), so an edge's overlay input has frames through the handle. Nothing to do; re-read line 5 and 563 to confirm before relying on it.
- [ ] **Step 1: Failing tests** (argv-exact; one per builder; each with a "zero offsets are byte-identical to the current literal" assertion). Example:

```python
def test_boundary_command_crossfades_two_equal_edges() -> None:
    cmd = mp4_render._build_boundary_command(
        Path("/w/edge_003_tail.mp4"), Path("/w/edge_004_head.mp4"),
        kind="zoom", seconds=1.0, sequence=_SEQUENCE, output_path=Path("/w/boundary_003.mp4"),
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph == (
        "[0:v][1:v]xfade=transition=zoomin:duration=1:offset=0,format=yuv420p[final];"
        "[0:a][1:a]acrossfade=d=1:c1=tri:c2=tri[aout]"
    )
    assert cmd[cmd.index("-t") + 1] == "1"
    assert cmd[-1] == "/w/boundary_003.mp4"


def test_lower_third_filters_with_a_delay_and_a_skip() -> None:
    parts, _ = lower_third_filters(3, 4.0, source_label="base", delay_seconds=0.5)
    assert parts[0] == "[3:v]format=rgba,fade=t=out:st=4:d=0.5:alpha=1[lt]"
    assert parts[1] == "[base][lt]overlay=0:0:enable='between(t,0.5,4.5)'[withlt]"
    parts, _ = lower_third_filters(3, 4.0, source_label="base", skip_seconds=0.5)
    assert parts[1] == "[base][lt]overlay=0:0:enable='between(t,0,3.5)'[withlt]"
    assert lower_third_filters(3, 4.0, source_label="base") == _CURRENT_LITERAL  # parity
```

- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN; run `tests/test_overlay_card.py tests/test_mp4_render.py tests/test_compare_mp4_grid_cards.py` (the grid shares `lower_third_filters`). Lint. Commit:** `feat(transitions): edge, boundary and offset-aware command builders (#1244)`

---

### Task 4: The driver: edges and boundaries through the cache, trimmed neighbours, cut on failure

**Files:**
- Modify: `src/splitsmith/mp4_render.py` (`_render_with_work_dir` 276-560), `src/splitsmith/segment_cache.py` (`KEY_VERSION = 3`)
- Test: `tests/test_mp4_render.py`, `tests/test_segment_cache.py:121`

**Interfaces:**
- The loop becomes three passes over `timeline.items`:
  1. **prepare**: for each item, what today happens before `encode` (rasterize the card, write the motion clip's `prepare` closure, grab the backdrop). Store per item a `_Prepared(png, clip, motion, plan, lower_third)` record; a card that cannot be prepared is skipped exactly as today (and any boundary touching it is dropped with the existing degradation text plus `": the transition around it is a cut"`).
  2. **boundaries**: for each live `_Boundary`, encode `edge_{after:03d}_tail.mp4`, `edge_{after+1:03d}_head.mp4`, then `boundary_{after:03d}.mp4` through `encode(...)` (edges are plain encodes; the boundary command names the two edge paths, which `SegmentCache.key` content-hashes because they sit in `work_dir`; on a cache hit of the boundary the edges are still needed? No: `encode` returns the cached boundary without running ffmpeg, but the edge files must exist for the key to hash. So edges are always encoded (they are short) and the boundary is the cached piece). An `FFmpegError` on an edge or the boundary marks the boundary dead: both neighbours lose their cuts (`replace(item, head_cut_seconds=0.0)` etc.) and a degradation `f"transition after {prev.name} failed to render ({exc}); rendered as a cut"` is recorded (Review Focus 4).
  3. **items**: encode each item with its (possibly restored) cuts as Task 3 specifies, appending `(segment, item.duration_seconds)` and, after an item that opens a live boundary, `(boundary_path, d)`. A boundary counts as `generated` (the stitch re-encodes audio, as for any generated segment).
- Progress: `total_steps = len(items) + 2 * len(live boundaries) + 1`; edges report `label=f"transition {n}"` with `status="encoding"`/`"reused"`.
- `Mp4RenderResult.degradations` includes `timeline.degradations` first.

- [ ] **Step 1: Failing tests**

```python
def test_transitions_keep_the_timeline_length(tmp_path: Path) -> None:
    """Review Focus 2: a centred fade consumes d/2 of each neighbour and the
    boundary is d long, so the stitched length is the cut's length."""
    comp_cut = _two_stage_composition()
    comp_fade = _two_stage_composition(transitions=("fade", 1.0))
    cut = mp4_render.render_mp4(comp_cut, output_path=tmp_path / "a.mp4", work_dir=tmp_path / "wa", runner=_fake_runner)
    fade = mp4_render.render_mp4(comp_fade, output_path=tmp_path / "b.mp4", work_dir=tmp_path / "wb", runner=_fake_runner)
    assert fade.duration_seconds == pytest.approx(cut.duration_seconds)


def test_render_encodes_edges_then_the_boundary_then_trimmed_neighbours(tmp_path: Path) -> None:
    calls = _spy_runner()
    mp4_render.render_mp4(_two_stage_composition(transitions=("dissolve", 1.0)), output_path=..., work_dir=tmp_path, runner=calls)
    outputs = [Path(c[-1]).name for c in calls.argvs]
    assert outputs[:3] == ["edge_000_tail.mp4", "edge_001_head.mp4", "boundary_000.mp4"]
    stage_0 = next(c for c in calls.argvs if c[-1].endswith("stage_000.mp4"))
    assert stage_0[stage_0.index("-t") + 1] == f"{_EFFECTIVE - 0.5:g}"          # tail cut
    stage_1 = next(c for c in calls.argvs if c[-1].endswith("stage_001.mp4"))
    assert stage_1[stage_1.index("-ss") + 1] == f"{_HEAD_TRIM + 0.5:g}"         # head cut
    concat = (tmp_path / "concat.txt").read_text().splitlines()
    assert [Path(l.split("'")[1]).name for l in concat] == ["stage_000.mp4", "boundary_000.mp4", "stage_001.mp4"]
    assert "-c:a" in calls.argvs[-1]  # a boundary is a generated segment: audio re-encoded in the stitch


def test_a_failed_edge_leaves_the_neighbours_untrimmed(tmp_path: Path) -> None:
    """Review Focus 4."""
    runner = _failing_on("edge_000_tail.mp4")
    result = mp4_render.render_mp4(_two_stage_composition(transitions=("fade", 1.0)), ..., runner=runner)
    assert result.degradations == ("transition after stage_000 failed to render (ffmpeg exited 1); rendered as a cut",)
    stage_0 = runner.argv_for("stage_000.mp4")
    assert stage_0[stage_0.index("-t") + 1] == f"{_EFFECTIVE:g}"
    assert [n for n in runner.concat_names()] == ["stage_000.mp4", "stage_001.mp4"]


def test_the_boundary_is_cached_by_its_edges_and_a_recut_edge_misses(tmp_path: Path) -> None:
    ...  # render twice with a SegmentCache: second run encodes the two edges (cheap) and reuses the boundary;
         # change stage 0's trim mtime: the tail edge re-encodes, the boundary misses.


def test_no_transition_render_argv_is_unchanged(tmp_path: Path) -> None:
    """The three-pass driver must emit exactly the argv the one-pass driver did."""
    ...  # compare the full argv list of a no-transition render against the literal list the existing test at 303 asserts
```

Also `tests/test_segment_cache.py:121` -> `KEY_VERSION == 3`.

- [ ] **Step 2: RED (`AttributeError` / argv mismatch). Step 3: implement the three passes; keep the per-item branches' bodies, move them into `_prepare_item` and `_encode_item` functions so the loop reads top-down.**
- [ ] **Step 4: GREEN. Run `tests/test_mp4_render.py tests/test_segment_cache.py tests/test_ui_match_exports.py tests/test_match_cli_export.py` and the frame-gate sets (`render_match_frames.py` slate / lower-third / clean against main: identical). Lint. Commit:** `feat(transitions): boundary segments in the single-shooter render, cached, cut on failure (#1244)`

---

### Task 5: The request layer, the CLI and the Export page open the gate for MP4

**Files:**
- Modify: `src/splitsmith/ui/match_exports.py:599-626` (the gate and the slate rule), `src/splitsmith/match_cli.py` (`match export` has no transition option today, checked while planning: add `--transition KIND` (choices: `none` plus `composition.TransitionKind`, default `none`) and `--transition-seconds S` (default 0.5), threaded into `MatchExportRequestData.transition_kind` / `transition_duration_seconds`), `src/splitsmith/ui/export_preview_api.py` untouched
- Modify: `src/splitsmith/ui_static/src/lib/renderOptions.ts:71-76` (`transitionsSupported(format, mode)`), `lib/lookGallery.ts` (the xfade variants on the transition slot with `modes: ["single"]`, `TRANSITION_FORMATS` per mode), `lib/exportPlan.ts:124` (no added seconds for mp4: the length is preserved), `pages/matchExportModel.ts:204`, `pages/Export.tsx:529,780`, `lib/exportPresets.ts:34,283`
- Modify: `scripts/render_look_thumbnails.py` (a `transition_thumbnail(kind)` that draws the mid-frame of an xfade between two theme colours through ffmpeg, no Chromium) and the ten PNGs under the gallery's thumbnail dir
- Test: `tests/test_ui_match_exports.py`, `tests/test_match_cli_export.py`, `ui_static/src/lib/renderOptions.test.ts`, `lookGallery.test.ts`, `exportPlan.test.ts`, `tests/test_render_look_thumbnails.py`

**Rules:**
- `mp4` + single shooter: transitions reach `composition.transitions` unchanged; the "transitions ignored" anomaly goes only for `mp4`; `fcp7xml` keeps it.
- `mp4` + slates: allowed (the boundary into a slate is the mechanism's point); the FCPXML refusal stays.
- Grid (`compare export`, `exportCompareGrid`): unchanged; `transitionsSupported("mp4", "grid") === false` until the follow-up plan.
- Presets: `ExportPresetBody.transition_kind` accepts the new kinds; an old body with `zoom` still loads (test).

- [ ] **Step 1: Failing tests:** `test_mp4_export_passes_transitions_to_the_renderer` (spy `render_mp4`, assert `composition.transitions == (Transition(0, 1, "fade", 1.0),)` and no "transitions ignored" anomaly); `test_mp4_export_keeps_slates_with_transitions`; `test_fcp7xml_still_ignores_transitions`; vitest: `transitionsSupported("mp4", "single") === true`, `("mp4", "grid") === false`, `("fcpxml", "grid") === true`; `lookGallery` pins the ten variants on `single` only and that every committed thumbnail is referenced; `exportPlan` single-mode estimate unchanged by transitions when the format is mp4.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN (`pnpm --dir src/splitsmith/ui_static test`, `typecheck`, `lint`). Commit:** `feat(transitions): MP4 exports carry transitions; the Look gallery offers the xfade kinds for single-shooter MP4 (#1244)`

---

### Task 6: Frames, docs, the whole-branch gate

**Files:**
- Modify: `scripts/render_match_frames.py` (`--transition KIND`, `--transition-seconds S`; moments `boundary-1-in` (boundary start + 2 frames), `boundary-1-mid`, `boundary-1-out` (boundary end - 2 frames), derived from `plan.items` durations and `plan.boundaries`), `CLAUDE.md` (a paragraph after the Looks/identity ones), `SPEC.md` (`mp4_render.py` line), `docs/superpowers/specs/2026-10-06-rendered-video-first-design.md` (an amendment note under section 3: the centred geometry, the grid follow-up)
- Test: none new; the gate is visual plus the full suite.

- [ ] **Step 1:** add the flags and moments; run with the project ffmpeg: `render_match_frames.py --transition fade --transition-seconds 1 --titles lower-third` and `--transition dissolve --titles slate --summary-hold 2`. Expected: `boundary-1-mid` is a visible blend of stage 1's tail and stage 2's slate; the lower third in `stage-2-head` continues rather than restarts (compare with a `boundary-1-out` frame).
- [ ] **Step 2:** re-render the default sets (no transition flag) and diff against main: 44 of 44 identical.
- [ ] **Step 3:** docs; publish the frames (Urdr, side by side with the cut); full suite with the project ffmpeg; commit `feat(transitions): boundary frames, docs (#1244)`.
