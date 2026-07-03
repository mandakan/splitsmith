# Multi-Stage Single-Take Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Handle one video file that contains N stage runs: declare coverage at attach, detect each stage's beep inside a derived per-stage search window, review the carve-up on a clip-level take overview, with per-stage beep/shot review unchanged downstream.

**Architecture:** The storage layer (`RawVideo.covers_stages`, N StageVideos sharing one source) already models one-file-many-stages. This plan adds: (1) `video_id` disambiguation via a stamped `stage_number` on `StageVideo`; (2) a pure `beep_windows.py` module deriving per-stage search windows from scoreboard timestamps (sequential fallback without them); (3) window-sliced audio extraction in the job layer with offset math, leaving `detect_beep` untouched; (4) coverage plumbing (attach enqueue, coverage edit, suggestion endpoint); (5) take-level peaks + overview API; (6) SPA coverage multi-select and TakeOverview page.

**Tech Stack:** Python 3.11+/Pydantic/FastAPI, ffmpeg via subprocess, pytest; React/TypeScript SPA under `src/splitsmith/ui_static` (pnpm only).

**Spec:** `docs/superpowers/specs/2026-07-03-multi-stage-single-take-design.md`

## Global Constraints

- `uv` for all Python commands (`uv run pytest ...`), never pip.
- Black line length 100; ruff must pass: `uv run ruff check src tests && uv run black --check src tests`.
- Type hints everywhere; Pydantic models for data crossing module boundaries; `pathlib.Path` for paths.
- ffmpeg/ffprobe are mocked in unit tests; real-ffmpeg tests are `@pytest.mark.integration`.
- No fabricated audio fixtures. Reuse `tests/fixtures/beep-test.wav` (real recording, beep at ~2.416 s per `beep-test.json`).
- New text (comments, docstrings, UI copy) uses single ASCII dash "-", never em dash, never "--".
- No new dependencies (Python or npm).
- No compatibility shims: this is pre-production; update call sites and tests, delete obsolete tests for retired behavior.
- SPA verification: `pnpm -C src/splitsmith/ui_static typecheck && pnpm -C src/splitsmith/ui_static build`, plus eslint scoped to touched files. There is no SPA test runner.
- `beep_reviewed` stays the single source of truth for beep review state. The take overview must not introduce a parallel confirmed flag.
- Commit after each task with the trailers:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01G2Z121nV8dPjgu248huLMX`

---

### Task 1: `video_id` disambiguation via stamped `stage_number`

`StageVideo.video_id` is `blake2s(str(path))[:12]` (`src/splitsmith/ui/project.py:380-394`), so N StageVideos sharing one source collide. `StageVideo` has no back-reference to its owning stage, so the hash cannot see the stage without a stored field. Add `stage_number: int | None`, stamped on load and maintained at every mutation site. Cache filenames change for assigned videos; caches rebuild on next access (acceptable, pre-production).

**Files:**
- Modify: `src/splitsmith/ui/project.py` (StageVideo ~278, video_id ~380, assign_video ~1783, register_video ~1671, swap_primary ~1859, MatchProject model validator)
- Modify: `src/splitsmith/ui/server.py:5208` (attach creates StageVideo - stamp stage_number)
- Test: `tests/test_take_video_identity.py` (new)

**Interfaces:**
- Produces: `StageVideo.stage_number: int | None` (stored field, default None); `StageVideo.video_id` = `blake2s(f"{path}#{stage_number}")[:12]` when `stage_number is not None`, else `blake2s(str(path))[:12]` (unchanged for unassigned).
- Produces: `MatchProject._stamp_stage_numbers` model validator - every video in `stages[i].videos` gets `stage_number = stages[i].stage_number`; every video in `unassigned_videos` gets `None`. Runs on every `model_validate` (load) so persisted projects need no migration.

- [ ] **Step 1: Write the failing tests**

```python
"""Identity + coverage semantics for one source file shared by N stages."""

from pathlib import Path

from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


def _project_with_two_stages() -> MatchProject:
    return MatchProject(
        name="take-test",
        stages=[
            StageEntry(stage_number=1, stage_name="One", time_seconds=20.0),
            StageEntry(stage_number=2, stage_name="Two", time_seconds=25.0),
        ],
    )


def test_video_ids_differ_across_stages_for_shared_path() -> None:
    proj = _project_with_two_stages()
    shared = Path("raw/take.mp4")
    proj.stages[0].videos.append(StageVideo(path=shared, role="primary", stage_number=1))
    proj.stages[1].videos.append(StageVideo(path=shared, role="primary", stage_number=2))
    ids = {proj.stages[0].videos[0].video_id, proj.stages[1].videos[0].video_id}
    assert len(ids) == 2


def test_unassigned_video_id_is_path_only_hash() -> None:
    import hashlib

    v = StageVideo(path=Path("raw/take.mp4"))
    assert v.stage_number is None
    expected = hashlib.blake2s(b"raw/take.mp4", digest_size=6).hexdigest()
    assert v.video_id == expected


def test_load_stamps_stage_numbers() -> None:
    proj = _project_with_two_stages()
    shared = Path("raw/take.mp4")
    proj.stages[0].videos.append(StageVideo(path=shared, role="primary"))
    proj.unassigned_videos.append(StageVideo(path=Path("raw/other.mp4")))
    # Round-trip through dump/validate simulates a project load from disk.
    reloaded = MatchProject.model_validate(proj.model_dump(mode="json"))
    assert reloaded.stages[0].videos[0].stage_number == 1
    assert reloaded.unassigned_videos[0].stage_number is None


def test_assign_video_restamps_stage_number() -> None:
    proj = _project_with_two_stages()
    proj.unassigned_videos.append(StageVideo(path=Path("raw/take.mp4")))
    v = proj.assign_video(Path("raw/take.mp4"), to_stage_number=2)
    assert v.stage_number == 2
    back = proj.assign_video(Path("raw/take.mp4"), to_stage_number=None)
    assert back.stage_number is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_take_video_identity.py -v`
Expected: FAIL (`stage_number` unknown field / ids equal).

- [ ] **Step 3: Implement**

In `StageVideo` (after `camera_model`):

```python
    # Owning stage number, stamped by MatchProject on load and by
    # assign_video / attach on every move. None while unassigned. Feeds
    # video_id so N StageVideos sharing one source file (a multi-stage
    # single take) get distinct ids; see the take spec (2026-07-03).
    stage_number: int | None = None
```

Replace the `video_id` body:

```python
    @computed_field  # type: ignore[prop-decorator]
    @property
    def video_id(self) -> str:
        """Stable URL-safe identifier derived from path + owning stage.

        Assigned videos hash "<path>#<stage_number>" so one source file
        covering N stages yields N distinct ids (per-video API routes and
        cache filenames stay collision-free). Unassigned videos keep the
        legacy path-only hash so tray identity is stable across assigns.
        """
        seed = str(self.path)
        if self.stage_number is not None:
            seed = f"{seed}#{self.stage_number}"
        return hashlib.blake2s(seed.encode("utf-8"), digest_size=6).hexdigest()
```

On `MatchProject`, add (near the other validators / after field defs):

```python
    @model_validator(mode="after")
    def _stamp_stage_numbers(self) -> "MatchProject":
        """Keep every StageVideo.stage_number consistent with its container.

        Derived data: the owning list is the truth, the field is a cached
        back-reference so video_id can see the stage. Stamping on every
        validate covers legacy projects (no migration needed) and any
        code path that moved a video without restamping.
        """
        for stage in self.stages:
            for v in stage.videos:
                v.stage_number = stage.stage_number
        for v in self.unassigned_videos:
            v.stage_number = None
        return self
```

In `assign_video`: after `video.role = "secondary"` on the unassign branch add `video.stage_number = None`; after `video.role = effective_role` before `target.videos.append(video)` add `video.stage_number = target.stage_number`.

In `server.py:5208` change `StageVideo(path=video_path, role=role)` to `StageVideo(path=video_path, role=role, stage_number=stage_number)`.

Audit remaining constructors/moves: `register_video` already lands in `unassigned_videos` (default None is correct); `swap_primary` goes through `assign_video`. Run `grep -n "StageVideo(" src/ tests/ -r` and stamp any other site that appends into a stage's `videos` list.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_take_video_identity.py -v`
Expected: PASS.

- [ ] **Step 5: Full-suite sanity + fix fallout**

Run: `uv run pytest -x -q -m "not integration and not docker"`
Expected: tests that hard-coded a path-only hash for assigned videos fail; update them to compute the id via the model (`video.video_id`), never a hand-rolled hash. Delete assertions that enforced the old collision behavior if any exist.

- [ ] **Step 6: Commit** `feat(project): stage-scoped video_id via stamped stage_number`

---

### Task 2: `beep_window` fields + manual window endpoint

**Files:**
- Modify: `src/splitsmith/ui/project.py` (StageVideo)
- Modify: `src/splitsmith/ui/server.py` (new PUT endpoint next to the beep endpoints ~6124+)
- Test: `tests/test_take_video_identity.py` (extend), `tests/test_take_endpoints.py` (new)

**Interfaces:**
- Produces: `StageVideo.beep_window: tuple[float, float] | None = None` (seconds into source; None = whole file) and `StageVideo.beep_window_source: Literal["scoreboard", "sequential", "manual"] | None = None`.
- Produces: `PUT /api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep-window` body `{"start_s": float, "end_s": float}` - persists a manual window, clears beep state, invalidates the trim cache, enqueues detect_beep. Returns the submitted job JSON. 422 when `end_s <= start_s` or `start_s < 0`.

- [ ] **Step 1: Failing model test** (extend `tests/test_take_video_identity.py`)

```python
def test_beep_window_round_trips() -> None:
    v = StageVideo(path=Path("raw/take.mp4"), beep_window=(30.0, 210.0), beep_window_source="scoreboard")
    dumped = v.model_dump(mode="json")
    back = StageVideo.model_validate(dumped)
    assert back.beep_window == (30.0, 210.0)
    assert back.beep_window_source == "scoreboard"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_take_video_identity.py::test_beep_window_round_trips -v` - FAIL.

- [ ] **Step 3: Implement fields** on `StageVideo` (after `stage_number`):

```python
    # Search window (seconds into the source) the last beep detection ran
    # inside, and who chose it. None = whole file (single-stage behavior).
    # Persisted for the audit trail and so the take overview can render
    # exactly what detection looked at. "manual" windows survive re-detect;
    # derived windows are recomputed by each detect job.
    beep_window: tuple[float, float] | None = None
    beep_window_source: Literal["scoreboard", "sequential", "manual"] | None = None
```

- [ ] **Step 4: Failing endpoint test** in `tests/test_take_endpoints.py`. Mirror the client/project fixture style of `tests/test_hosted_raw_upload.py` for hosted bits, but this endpoint must work in local mode - use the local TestClient fixture pattern from an existing per-video beep endpoint test (grep `beep/select` in tests). Assert: 200 sets `beep_window == [start, end]`, `beep_window_source == "manual"`, wipes `beep_time`/`beep_candidates`/`beep_reviewed`, sets `processed["beep"] is False`; a detect_beep job was submitted (monkeypatch `SPLITSMITH_AUTO_BEEP_DISABLED` off and assert via the jobs list endpoint, or monkeypatch `state.jobs.submit`); 422 on `end_s <= start_s`.

- [ ] **Step 5: Implement endpoint** in `server.py`, next to `_submit_detect_beep`:

```python
    class BeepWindowRequest(BaseModel):
        start_s: float
        end_s: float

    @app.put("/api/shooters/{slug}/stages/{stage_number}/videos/{video_id}/beep-window")
    async def set_beep_window(
        slug: str, stage_number: int, video_id: str, body: BeepWindowRequest
    ) -> JSONResponse:
        """Persist a manual beep search window and re-run detection.

        The take overview's drag-to-adjust lands here. Setting a window
        wipes the current beep (a new window is a new claim about where
        the beep lives) and invalidates the trim cache, mirroring the
        manual beep-time endpoint's semantics.
        """
        if body.start_s < 0 or body.end_s <= body.start_s:
            raise HTTPException(status_code=422, detail="end_s must be greater than start_s >= 0")
        project, stage, video = _resolve_stage_video(slug, stage_number, video_id)
        root = state.shooter_root(slug)
        video.beep_window = (body.start_s, body.end_s)
        video.beep_window_source = "manual"
        video.beep_time = None
        video.beep_source = None
        video.beep_confidence = None
        video.beep_peak_amplitude = None
        video.beep_duration_ms = None
        video.beep_candidates = []
        video.beep_reviewed = False
        video.beep_auto_detect_failed = False
        video.processed["beep"] = False
        video.processed["trim"] = False
        audio_helpers.invalidate_video_audit_trim(root, stage_number, video, project=project)
        project.save(root)
        return await _submit_detect_beep(slug, stage_number, video)
```

- [ ] **Step 6: Run** `uv run pytest tests/test_take_endpoints.py tests/test_take_video_identity.py -v` - PASS.
- [ ] **Step 7: Commit** `feat(api): manual beep search window per stage-video`

---

### Task 3: `BeepWindowConfig` + pure `beep_windows.py`

**Files:**
- Create: `src/splitsmith/beep_windows.py`
- Modify: `src/splitsmith/config.py` (new model + field on `Config` at ~407)
- Test: `tests/test_beep_windows.py` (new, pure math - no audio, no I/O)

**Interfaces:**
- Produces (config.py):

```python
class BeepWindowConfig(BaseModel):
    """Search-window derivation for multi-stage single-take videos.

    scorecard_updated_at is typed 1-3 min after the run ends and the run
    ends stage_time after the beep, so the expected beep offset inside
    the file is (scorecard - video_start) - stage_time - scorecard_lead_s.
    The window pads that estimate; clamping and a minimum length keep it
    inside the file and useful even when the estimate is rough.
    """

    scorecard_lead_s: float = 120.0
    pre_pad_s: float = 180.0
    post_pad_s: float = 180.0
    reset_margin_s: float = 45.0
    min_window_s: float = 20.0
    conflict_threshold_s: float = 2.0
```

plus `beep_windows: BeepWindowConfig = Field(default_factory=BeepWindowConfig)` on `Config`.
- Produces (beep_windows.py):

```python
class StagePrior(BaseModel):
    stage_number: int
    scorecard_updated_at: datetime | None
    time_seconds: float

class StageBeepWindow(BaseModel):
    stage_number: int
    start_s: float
    end_s: float
    source: Literal["scoreboard", "sequential"]

def derive_scoreboard_windows(
    video_start: datetime, duration_s: float, priors: list[StagePrior], config: BeepWindowConfig
) -> list[StageBeepWindow]

def sequential_window(
    prior_anchor_s: float | None, duration_s: float, config: BeepWindowConfig
) -> tuple[float, float]

def find_beep_conflicts(beeps: dict[int, float], threshold_s: float) -> set[int]
```

`derive_scoreboard_windows` skips priors with `scorecard_updated_at is None` (callers fall back to sequential for those). Clamp to `[0, duration_s]`; when the clamped window is shorter than `min_window_s`, widen toward the other bound. `sequential_window` returns `(0.0, duration_s)` when `prior_anchor_s is None`, else `(min(prior_anchor_s, duration_s), duration_s)` where the caller passes `prior_anchor_s = prev_beep + prev_stage_time + reset_margin_s` (helper computes nothing about siblings - the impure job layer owns reading project state). `find_beep_conflicts` returns every stage_number participating in any pair closer than `threshold_s`.

- [ ] **Step 1: Failing tests** (representative set - write all of these):

```python
from datetime import UTC, datetime, timedelta

from splitsmith.beep_windows import (
    StagePrior,
    derive_scoreboard_windows,
    find_beep_conflicts,
    sequential_window,
)
from splitsmith.config import BeepWindowConfig

CFG = BeepWindowConfig()
T0 = datetime(2026, 6, 14, 9, 0, 0, tzinfo=UTC)


def _prior(n: int, minutes_after_start: float, stage_time: float = 20.0) -> StagePrior:
    return StagePrior(
        stage_number=n,
        scorecard_updated_at=T0 + timedelta(minutes=minutes_after_start),
        time_seconds=stage_time,
    )


def test_scoreboard_window_centers_on_expected_beep() -> None:
    # scorecard 10 min in, stage 20 s, lead 120 s -> expected beep at 460 s
    [w] = derive_scoreboard_windows(T0, 3600.0, [_prior(3, 10.0)], CFG)
    assert w.stage_number == 3
    assert w.source == "scoreboard"
    assert w.start_s == 460.0 - CFG.pre_pad_s
    assert w.end_s == 460.0 + CFG.post_pad_s


def test_windows_clamp_to_file_bounds() -> None:
    [w] = derive_scoreboard_windows(T0, 300.0, [_prior(1, 2.0)], CFG)
    assert w.start_s == 0.0
    assert w.end_s <= 300.0


def test_min_window_widens_toward_other_bound() -> None:
    # Expected beep lands past the end of a short file; the clamped
    # window must still be at least min_window_s long, hugging the end.
    [w] = derive_scoreboard_windows(T0, 100.0, [_prior(1, 30.0)], CFG)
    assert w.end_s == 100.0
    assert w.end_s - w.start_s >= CFG.min_window_s


def test_prior_without_scorecard_is_skipped() -> None:
    priors = [
        _prior(1, 10.0),
        StagePrior(stage_number=2, scorecard_updated_at=None, time_seconds=20.0),
    ]
    windows = derive_scoreboard_windows(T0, 3600.0, priors, CFG)
    assert [w.stage_number for w in windows] == [1]


def test_sequential_window_from_anchor() -> None:
    assert sequential_window(None, 900.0, CFG) == (0.0, 900.0)
    assert sequential_window(310.0, 900.0, CFG) == (310.0, 900.0)


def test_conflicts_flag_both_stages() -> None:
    assert find_beep_conflicts({1: 100.0, 2: 101.0, 3: 500.0}, 2.0) == {1, 2}
    assert find_beep_conflicts({1: 100.0, 3: 500.0}, 2.0) == set()
```

- [ ] **Step 2: Run** `uv run pytest tests/test_beep_windows.py -v` - FAIL (module missing).

- [ ] **Step 3: Implement** `src/splitsmith/beep_windows.py`:

```python
"""Derive per-stage beep search windows inside a multi-stage single take.

Pure functions: datetimes + seconds in, windows out. No file I/O, no
project access - the job layer (ui/server.py) resolves the video's
wall-clock start, duration, and sibling beeps, then calls in here. Keep
it that way so window math stays unit-testable without audio or ffmpeg.
"""

from __future__ import annotations

from datetime import datetime
from itertools import combinations
from typing import Literal

from pydantic import BaseModel

from .config import BeepWindowConfig


class StagePrior(BaseModel):
    """What we know about one covered stage before detection runs."""

    stage_number: int
    scorecard_updated_at: datetime | None
    time_seconds: float


class StageBeepWindow(BaseModel):
    """A derived search window, seconds into the source file."""

    stage_number: int
    start_s: float
    end_s: float
    source: Literal["scoreboard", "sequential"]


def _clamp(start: float, end: float, duration_s: float, min_window_s: float) -> tuple[float, float]:
    start = max(0.0, min(start, duration_s))
    end = max(0.0, min(end, duration_s))
    if end - start < min_window_s:
        # Widen toward whichever bound has room; a too-short window is
        # worse than a generous one (the detector ranks by silence
        # preference, it does not mind extra quiet audio).
        end = min(duration_s, start + min_window_s)
        start = max(0.0, end - min_window_s)
    return start, end


def derive_scoreboard_windows(
    video_start: datetime,
    duration_s: float,
    priors: list[StagePrior],
    config: BeepWindowConfig,
) -> list[StageBeepWindow]:
    """One window per prior that has a scorecard timestamp.

    Expected beep offset = (scorecard - video_start) - stage_time -
    scorecard_lead_s; the window pads that by pre/post. Priors without a
    scorecard timestamp are skipped - the caller falls back to
    sequential_window for those stages.
    """
    windows: list[StageBeepWindow] = []
    for prior in priors:
        if prior.scorecard_updated_at is None:
            continue
        offset = (prior.scorecard_updated_at - video_start).total_seconds()
        expected = offset - prior.time_seconds - config.scorecard_lead_s
        start, end = _clamp(
            expected - config.pre_pad_s,
            expected + config.post_pad_s,
            duration_s,
            config.min_window_s,
        )
        windows.append(
            StageBeepWindow(
                stage_number=prior.stage_number, start_s=start, end_s=end, source="scoreboard"
            )
        )
    return windows


def sequential_window(
    prior_anchor_s: float | None,
    duration_s: float,
    config: BeepWindowConfig,
) -> tuple[float, float]:
    """Fallback window when no scorecard timestamps exist.

    The caller computes prior_anchor_s = previous stage's beep + its
    stage_time + reset_margin_s (or None for the first covered stage).
    The window always runs to end of file - each found beep narrows the
    next stage's search, never the current one.
    """
    if prior_anchor_s is None:
        return 0.0, duration_s
    return min(prior_anchor_s, duration_s), duration_s


def find_beep_conflicts(beeps: dict[int, float], threshold_s: float) -> set[int]:
    """Stage numbers whose detected beeps sit closer than threshold_s.

    Two stages latching onto the same physical beep is a carve-up error;
    both are flagged (neither silently wins) so the take overview can
    surface the pair for the user to fix.
    """
    flagged: set[int] = set()
    for (stage_a, beep_a), (stage_b, beep_b) in combinations(sorted(beeps.items()), 2):
        if abs(beep_a - beep_b) < threshold_s:
            flagged.add(stage_a)
            flagged.add(stage_b)
    return flagged
```

Add `BeepWindowConfig` to `config.py` (before `Config`) exactly as in Interfaces, and the `beep_windows` field on `Config`.

- [ ] **Step 4: Run** `uv run pytest tests/test_beep_windows.py tests/test_config.py -v` - PASS.
- [ ] **Step 5: Commit** `feat(detect): pure beep-window derivation module + config`

---

### Task 4: Windowed audio slice + offset math in the audio helper layer

`detect_beep` stays untouched. Add window-scoped WAV extraction and thread an optional window through `detect_video_beep`, adding the offset back onto the result.

**Files:**
- Modify: `src/splitsmith/ui/audio.py` (new `ensure_video_window_audio`; window param on `detect_video_beep` / `detect_primary_beep`)
- Test: `tests/test_take_window_audio.py` (new)

**Interfaces:**
- Produces: `ensure_video_window_audio(project_root, stage_number, video, source_video, start_s, end_s, *, sample_rate=48000, ffmpeg_binary="ffmpeg", project=None) -> Path` - extracts `[start_s, end_s)` mono WAV to `<audio_dir>/stage<N>_cam_<video_id>_win_<start_ms>_<end_ms>.wav` via `ffmpeg -ss <start> -t <dur> -i <src> -ac 1 -ar <sr> -vn`. mtime-cached like `ensure_video_audio`; pushed/pulled through the existing storage-cache helpers (`_storage_audio_key` works on any basename).
- Produces: `detect_video_beep(..., window: tuple[float, float] | None = None)` - when window is set, extract the slice, run `detect_beep` on it, then add `window[0]` to `result.time` and every `candidates[i].time` before returning. `detect_primary_beep` gains the same passthrough param.

- [ ] **Step 1: Failing tests**

```python
"""Window slicing + offset math for take-aware beep detection.

Uses the real beep-test.wav fixture (beep at ~2.416 s) for the offset
tests - no ffmpeg needed because we bypass extraction and hand
detect-on-slice the pre-sliced samples. The ffmpeg command itself is
asserted with a stubbed subprocess runner, per the testing rules.
"""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from splitsmith import beep_detect
from splitsmith.config import BeepDetectConfig
from splitsmith.ui import audio as audio_helpers
from splitsmith.ui.project import StageVideo

FIXTURES = Path(__file__).parent / "fixtures"
BEEP_TRUE_S = 2.416  # ground truth from beep-test.json
TOL = 0.015


def test_detect_on_slice_offsets_back_to_source_time(tmp_path: Path) -> None:
    audio, sr = beep_detect.load_audio(FIXTURES / "beep-test.wav")
    window = (1.0, 4.0)  # includes the beep
    sliced = audio[int(window[0] * sr) : int(window[1] * sr)]
    wav = tmp_path / "slice.wav"
    import soundfile as sf

    sf.write(wav, sliced, sr)
    video = StageVideo(path=Path("raw/take.mp4"), stage_number=1)
    with patch.object(audio_helpers, "ensure_video_window_audio", return_value=wav):
        result = audio_helpers.detect_video_beep(
            tmp_path, 1, video, Path("raw/take.mp4"), window=window
        )
    assert result.time == pytest.approx(BEEP_TRUE_S, abs=TOL)
    assert all(c.time >= window[0] for c in result.candidates)


def test_window_excluding_beep_raises_not_found(tmp_path: Path) -> None:
    audio, sr = beep_detect.load_audio(FIXTURES / "beep-test.wav")
    window = (4.0, 8.0)  # after the beep
    sliced = audio[int(window[0] * sr) : int(window[1] * sr)]
    wav = tmp_path / "slice.wav"
    import soundfile as sf

    sf.write(wav, sliced, sr)
    video = StageVideo(path=Path("raw/take.mp4"), stage_number=1)
    with patch.object(audio_helpers, "ensure_video_window_audio", return_value=wav):
        with pytest.raises(beep_detect.BeepNotFoundError):
            audio_helpers.detect_video_beep(
                tmp_path, 1, video, Path("raw/take.mp4"), window=window
            )


def test_window_wav_ffmpeg_args_and_cache(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"RIFF")
        class R:  # minimal CompletedProcess stand-in
            returncode = 0
        return R()

    src = tmp_path / "take.mp4"
    src.write_bytes(b"\x00")
    video = StageVideo(path=Path("raw/take.mp4"), stage_number=2)
    with patch.object(audio_helpers.subprocess, "run", side_effect=fake_run), patch.object(
        audio_helpers.shutil, "which", return_value="/usr/bin/ffmpeg"
    ):
        out1 = audio_helpers.ensure_video_window_audio(tmp_path, 2, video, src, 30.0, 210.0)
        out2 = audio_helpers.ensure_video_window_audio(tmp_path, 2, video, src, 30.0, 210.0)
    assert out1 == out2
    assert len(calls) == 1  # second call was a cache hit
    cmd = calls[0]
    assert cmd[cmd.index("-ss") + 1] == "30.0"
    assert cmd[cmd.index("-t") + 1] == "180.0"
    assert out1.name == f"stage2_cam_{video.video_id}_win_30000_210000.wav"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_take_window_audio.py -v` - FAIL.

- [ ] **Step 3: Implement** in `ui/audio.py`:

```python
def video_window_audio_path(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    start_s: float,
    end_s: float,
    *,
    project: MatchProject | None = None,
) -> Path:
    """Cache path for a window-sliced WAV. The window rides in the name
    (milliseconds) so a changed window is a new cache slot, and the
    basename carries the video_id so the storage-cache key helpers work
    unchanged."""
    audio_dir = project.audio_path(project_root) if project else project_root / "audio"
    return audio_dir / (
        f"stage{stage_number}_cam_{video.video_id}"
        f"_win_{int(round(start_s * 1000))}_{int(round(end_s * 1000))}.wav"
    )


def ensure_video_window_audio(
    project_root: Path,
    stage_number: int,
    video: StageVideo,
    source_video: Path,
    start_s: float,
    end_s: float,
    *,
    sample_rate: int = 48000,
    ffmpeg_binary: str = "ffmpeg",
    project: MatchProject | None = None,
) -> Path:
    """Extract the [start_s, end_s) mono slice of source_video if not cached.

    -ss before -i seeks on the demuxer (fast on long files); detection
    adds start_s back onto every returned timestamp so results stay in
    source-absolute seconds.
    """
    audio_path = video_window_audio_path(
        project_root, stage_number, video, start_s, end_s, project=project
    )
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    src_resolved = source_video.resolve()
    if not src_resolved.exists():
        raise FileNotFoundError(f"video missing on disk: {src_resolved}")
    if audio_path.exists() and audio_path.stat().st_mtime >= src_resolved.stat().st_mtime:
        return audio_path
    if _try_pull_audio_from_storage(project, audio_path):
        return audio_path
    if not shutil.which(ffmpeg_binary):
        raise AudioExtractionError(f"ffmpeg binary not found: {ffmpeg_binary}")
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(start_s),
        "-t",
        str(end_s - start_s),
        "-i",
        str(src_resolved),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-vn",
        str(audio_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise AudioExtractionError(
            f"ffmpeg failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc
    _try_push_audio_to_storage(project, audio_path)
    return audio_path
```

Thread the window through detection (replace the two detect functions' bodies minimally):

```python
def _offset_detection(result: BeepDetection, offset_s: float) -> BeepDetection:
    """Shift a slice-relative detection back into source-absolute time."""
    if offset_s == 0.0:
        return result
    shifted = result.model_copy(deep=True)
    shifted.time += offset_s
    for c in shifted.candidates:
        c.time += offset_s
    return shifted
```

In `detect_video_beep`, add the keyword-only param `window: tuple[float, float] | None = None`; when set (any role), do:

```python
    if window is not None:
        audio_path = ensure_video_window_audio(
            project_root, stage_number, video, source,
            window[0], window[1], ffmpeg_binary=ffmpeg_binary, project=project,
        )
        audio, sr = beep_detect.load_audio(audio_path)
        cfg = config or BeepDetectConfig()
        return _offset_detection(beep_detect.detect_beep(audio, sr, cfg), window[0])
```

before the existing role branch (so windowed detection never routes through the full-file `detect_primary_beep` path). Note: `config.search_window_s` caps the search to the first N seconds of the audio it is given; inside a window slice that cap must not apply - pass `cfg.model_copy(update={"search_window_s": 0.0})` when windowed, and add a line to the docstring saying the window replaces the leading-window heuristic.

- [ ] **Step 4: Run** `uv run pytest tests/test_take_window_audio.py tests/test_beep_detect.py -v` - PASS.
- [ ] **Step 5: Commit** `feat(audio): window-sliced WAV extraction + source-absolute offset detection`

---

### Task 5: Take-aware detect job (window derivation, soft-fail, chaining, persistence)

**Files:**
- Modify: `src/splitsmith/ui/server.py` `_run_detect_beep_for_video` (~1424) + a new `_derive_take_window` helper beside it
- Modify: `src/splitsmith/ui/project.py` `RawVideo` (add `duration_seconds`, `recorded_start`)
- Test: `tests/test_take_detect_job.py` (new; monkeypatch `audio_helpers.detect_video_beep`, `video_probe.probe`, and the jobs backend - no ffmpeg)

**Interfaces:**
- Produces: `RawVideo.duration_seconds: float | None = None` and `RawVideo.recorded_start: datetime | None = None` (wall-clock recording start; client-supplied at attach or backfilled by the worker from `st_birthtime` / `mtime - duration`).
- Produces: `_derive_take_window(proj, root, stage, video, raw) -> tuple[tuple[float, float], str] | None` - None when the video is not part of a multi-stage take (`raw is None or len(raw.covers_stages) < 2`) or when `video.beep_window_source == "manual"` handling below applies. Resolution order inside the job:
  1. `video.beep_window_source == "manual"` - reuse `video.beep_window` verbatim.
  2. Stage has `scorecard_updated_at` and a resolvable video start - `derive_scoreboard_windows` for this stage's prior.
  3. Else sequential: anchor = max over covered stages earlier in `raw.covers_stages` order with a detected `beep_time` of `beep_time + stage.time_seconds + reset_margin_s`; window = `sequential_window(anchor, duration)`.
- Behavior changes in `_run_detect_beep_for_video`:
  - Compute the window before detection; pass it to `detect_video_beep`.
  - Windowed primary `BeepNotFoundError` is a soft failure (mirror the secondary branch: `beep_auto_detect_failed=True`, no raise) - the take overview surfaces "none". Whole-file primary failure keeps raising.
  - Persist `beep_window` / `beep_window_source` in both the working copy and the fresh-save block.
  - Backfill `raw.duration_seconds` / `raw.recorded_start` via `video_probe.probe(source, cache_dir=proj.probes_path(root))` + file stats when missing.
  - Sequential-mode chaining: after a successful windowed detect where the take has no scorecard timestamps, submit detect_beep for the next stage in `covers_stages` order that has no `beep_time` and no active job (same `asyncio.run(state.jobs.submit(...))` pattern as the shot_detect chain at ~1650).

- [ ] **Step 1: Failing tests.** In `tests/test_take_detect_job.py`, build a local-mode project on `tmp_path` with 3 stages, one `RawVideo(storage_path="raw/take.mp4", covers_stages=[2, 3, 1], duration_seconds=1800.0, recorded_start=T0)` and shared-path StageVideos. Monkeypatch `audio_helpers.detect_video_beep` to record the `window` kwarg and return a canned `BeepDetection`. Assert:
  - scoreboard mode: stage 2's job passes the window `derive_scoreboard_windows` predicts, and after the job `video.beep_window`/`beep_window_source == "scoreboard"` are persisted.
  - manual window short-circuits derivation (window kwarg == the stored manual window).
  - windowed primary `BeepNotFoundError` (monkeypatch to raise) sets `beep_auto_detect_failed=True` and does not raise out of the job body.
  - sequential mode (wipe `scorecard_updated_at`): first stage in covers order gets `(0.0, duration)`; after its beep persists, deriving for the next stage starts at `beep + time_seconds + reset_margin_s`.
  - single-coverage raw (`covers_stages=[1]`): window kwarg is None (behavior identical to today).

Call the job body directly via `state.jobs.bodies["detect_beep"]` with a stub `JobHandle` (grep existing tests for the established fake-handle pattern, e.g. in `tests/test_jobs.py` / `tests/test_compute.py`, and reuse it).

- [ ] **Step 2: Run** - FAIL.
- [ ] **Step 3: Implement.** Sketch of the helper (final code adapts to local naming):

```python
    def _derive_take_window(
        root: Path,
        proj: MatchProject,
        stage: StageEntry,
        video: StageVideo,
    ) -> tuple[tuple[float, float], str] | None:
        """Resolve the beep search window for a take-covered video.

        Returns (window, source) or None for plain single-stage videos.
        Impure by design (file stats, ffprobe cache, sibling beeps); the
        math lives in splitsmith.beep_windows.
        """
        raw = proj.find_raw_video(str(video.path))
        if raw is None or len(raw.covers_stages) < 2:
            return None
        if video.beep_window_source == "manual" and video.beep_window is not None:
            return video.beep_window, "manual"
        cfg = process_runtime().config.beep_windows  # or Config() default; match how other configs resolve here
        source = proj.resolve_video_path(root, video.path)
        duration = raw.duration_seconds
        if duration is None:
            try:
                duration = video_probe.probe(source, cache_dir=proj.probes_path(root)).duration
            except video_probe.ProbeError:
                duration = None
            if duration:
                raw.duration_seconds = float(duration)
        start_dt = raw.recorded_start
        if start_dt is None and duration:
            st = source.stat()
            birth = getattr(st, "st_birthtime", None)
            start_dt = (
                datetime.fromtimestamp(birth, tz=UTC)
                if birth is not None
                else datetime.fromtimestamp(st.st_mtime, tz=UTC) - timedelta(seconds=duration)
            )
            raw.recorded_start = start_dt
        if duration is None:
            return None  # cannot bound a window; whole-file detection
        if stage.scorecard_updated_at is not None and start_dt is not None:
            prior = beep_windows.StagePrior(
                stage_number=stage.stage_number,
                scorecard_updated_at=stage.scorecard_updated_at,
                time_seconds=stage.time_seconds,
            )
            windows = beep_windows.derive_scoreboard_windows(start_dt, duration, [prior], cfg)
            if windows:
                w = windows[0]
                return (w.start_s, w.end_s), "scoreboard"
        anchor: float | None = None
        for n in raw.covers_stages:
            if n == stage.stage_number:
                break
            sibling = proj.stage(n)
            sib_primary = next((v for v in sibling.videos if str(v.path) == str(video.path)), None)
            if sib_primary is not None and sib_primary.beep_time is not None:
                cand = sib_primary.beep_time + sibling.time_seconds + cfg.reset_margin_s
                anchor = cand if anchor is None else max(anchor, cand)
        return beep_windows.sequential_window(anchor, duration, cfg), "sequential"
```

Wire into `_run_detect_beep_for_video`: derive after `source = ...`; stash `take_window`; pass `window=take_window[0] if take_window else None` to `detect_video_beep`; on the `BeepNotFoundError` handler change the fatal condition from `if video.role == "primary": raise` to `if video.role == "primary" and take_window is None: raise`, and in the windowed-primary soft path set the same field-wipes as the secondary path minus cross-align (cross-align has no meaning against a sibling of itself). After a successful detect set `video.beep_window, video.beep_window_source = take_window if take_window else (None, None)`; mirror both fields plus `raw.duration_seconds`/`raw.recorded_start` in the fresh-save block. Sequential chaining goes right after the existing shot_detect chain block, guarded on `take_window is not None and take_window[1] == "sequential"`.

The config resolve: check how `_run_detect_beep_for_video` obtains `BeepDetectConfig` today (it uses defaults inside `detect_video_beep`); load `BeepWindowConfig()` the same way the project's other config is loaded (grep `Config()` usage in server.py; if none, instantiate `BeepWindowConfig()` directly and note YAML override arrives via the standard config loader used by the CLI).

- [ ] **Step 4: Run** `uv run pytest tests/test_take_detect_job.py -v` then the full unit suite. PASS; fix fallout (tests asserting primary detect failure raises may need the single-stage guard asserted instead).
- [ ] **Step 5: Commit** `feat(jobs): take-aware windowed beep detection with sequential chaining`

---

### Task 6: Coverage plumbing - shared apply helper, attach enqueue, coverage edit endpoint

**Files:**
- Modify: `src/splitsmith/ui/server.py` (attach endpoint ~5107; new PATCH endpoint; new `_apply_raw_video_coverage` + `_queue_take_detects` helpers)
- Modify: `src/splitsmith/ui/project.py` `attach_raw_video` (~1753: stop force-sorting `covers_stages`; preserve declared order, dedupe, merge appends)
- Modify: `src/splitsmith/ui/server.py` `AttachRawVideoRequest` (~2951: add `duration_seconds: float | None = None`, `recorded_start: datetime | None = None`)
- Test: extend `tests/test_hosted_raw_upload.py` attach tests; new `tests/test_take_coverage.py`

**Interfaces:**
- Produces: `_apply_raw_video_coverage(project, root, storage_path: str, covers: list[int]) -> list[StageVideo]` - diffs `covers` against current per-stage entries for `storage_path`: creates missing StageVideos (`role` auto-promote rule as today, `stage_number` stamped), removes entries for stages no longer covered (invalidating their trim/audio caches via `invalidate_video_audit_trim`), updates `rv.covers_stages = covers` (declared order preserved), removes a matching `unassigned_videos` entry when coverage is first applied. Returns the newly created StageVideos.
- Produces: `PATCH /api/shooters/{slug}/raw-videos/coverage` body `{"filename": str, "covers_stages": list[int]}` - works in local AND hosted mode (identity key = the StageVideo path string `raw/<filename>`, same shape in both modes). 404 when no RawVideo/registered video matches; 422 on unknown stage numbers. Saves, then enqueues detects for created entries. Local-mode ClipDetail and the take overview's coverage editor both call this.
- Produces: `_queue_take_detects(slug, project, created: list[StageVideo])` - for scoreboard-mode takes submit one detect_beep per created video (dedupe via `find_active`); for sequential-mode takes submit only the first covered stage's video (the job chains the rest, Task 5). Skips `processed["beep"]` / manual-beep videos. Uses `asyncio.run(...)` from the sync endpoint (FastAPI sync endpoints run in a threadpool with no running loop). Must NOT call `resolve_video_path` first - in hosted mode that would mirror a multi-GB object into the API container; reachability is the worker's problem.
- Changes: attach endpoint calls `_apply_raw_video_coverage` instead of its inline loop, stamps `duration_seconds`/`recorded_start` from the request body onto the RawVideo, and calls `_queue_take_detects` after save. `attach_raw_video` merge keeps first-seen order: `merged = list(dict.fromkeys([*existing.covers_stages, *rv.covers_stages]))`.

- [ ] **Step 1: Failing tests.**
  - Update `tests/test_hosted_raw_upload.py::test_attach_is_idempotent_merges_covers_stages` - expect order-preserving union (e.g. attach `[5, 6]` then `[1, 6]` yields `[5, 6, 1]`), not sorted.
  - Extend `test_attach_with_covers_stages_creates_stagevideos` - created entries have distinct `video_id`s and correct `stage_number`s; a detect_beep job was submitted per stage (assert on the jobs API or a monkeypatched submit).
  - New `tests/test_take_coverage.py` (local-mode TestClient): register a video, PATCH coverage `[2, 1]` - unassigned entry gone, two StageVideos exist, RawVideo created with declared order; PATCH coverage `[2]` - stage 1's entry removed and its trim cache invalidated (monkeypatch `invalidate_video_audit_trim`, assert called); PATCH with unknown stage - 422; PATCH unknown filename - 404.
- [ ] **Step 2: Run** - FAIL.
- [ ] **Step 3: Implement** per Interfaces. The attach endpoint keeps its existing validation (sanitize, storage stat, unknown-stage 422) and hosted-only guard; the PATCH endpoint does the same stage validation but no storage requirement. Enqueue mode detection: take is "scoreboard-mode" when every covered stage has `scorecard_updated_at`, else sequential.
- [ ] **Step 4: Run** `uv run pytest tests/test_take_coverage.py tests/test_hosted_raw_upload.py -v` - PASS.
- [ ] **Step 5: Commit** `feat(api): coverage apply/edit with per-stage detect enqueue`

---

### Task 7: Coverage suggestion endpoint

**Files:**
- Modify: `src/splitsmith/video_match.py` (pure `stages_in_span`)
- Modify: `src/splitsmith/ui/server.py` (POST endpoint)
- Test: `tests/test_video_match.py` (extend, pure), `tests/test_take_endpoints.py` (extend)

**Interfaces:**
- Produces (video_match.py):

```python
def stages_in_span(
    start: datetime,
    end: datetime,
    stages: Iterable[StageData],
    tolerance_minutes: int,
) -> list[int]:
    """Stage numbers whose match window overlaps [start, end], ordered by
    scorecard_updated_at (shooting order). The coverage suggestion for a
    single take spanning several runs; a one-stage clip returns one hit,
    so the single-file case needs no special path."""
```

Overlap test per stage: `lower, upper = match_window(stage.scorecard_updated_at, tolerance_minutes)`; hit when `start <= upper and end >= lower`. Sort hits by `scorecard_updated_at`.
- Produces: `POST /api/shooters/{slug}/videos/suggest-coverage` body `{"recorded_start": iso datetime | null, "duration_s": float | null, "path": str | null}` - resolves the span from the body, or (local mode) from a registered path via `st_birthtime`/mtime + `video_probe.probe` duration. Returns `{"covers_stages": [ints], "span": {"start": iso, "end": iso} | null}`; empty list + null span when no span is resolvable (caller falls back to manual selection). Uses the project's stages that have `scorecard_updated_at`, tolerance from `VideoMatchConfig().tolerance_minutes`.

- [ ] **Step 1: Failing tests** - pure `stages_in_span` cases in `tests/test_video_match.py` (span covering stages 2+3 of 4 returns `[2, 3]` in scorecard order even when stage numbers are shuffled; zero-length span inside one window returns that stage; span before all windows returns `[]`). Endpoint test in `tests/test_take_endpoints.py`: explicit-span body returns the expected stages; body with all-null fields returns `{"covers_stages": [], "span": null}`.
- [ ] **Step 2: Run** - FAIL. **Step 3: Implement.** **Step 4: Run** - PASS.
- [ ] **Step 5: Commit** `feat(api): coverage suggestion from recording span vs scorecard windows`

---

### Task 8: Take peaks + take overview API

Reuse `splitsmith/waveform.py` `ensure_peaks(audio_path, bins)` (cache lands beside the WAV automatically). The whole-file WAV for a 30-90 min take is extracted at 8 kHz (envelope only) into a take-scoped name so it cannot collide with the 48 kHz per-stage caches.

**Files:**
- Modify: `src/splitsmith/ui/audio.py` (`ensure_take_audio`, `take_audio_path`)
- Modify: `src/splitsmith/ui/server.py` (worker hook in `_run_detect_beep_for_video`; two GET endpoints)
- Test: `tests/test_take_endpoints.py` (extend), `tests/test_take_window_audio.py` (extend for the 8 kHz args)

**Interfaces:**
- Produces: `take_audio_path(project_root, storage_path: str, *, project=None) -> Path` = `<audio_dir>/take_<blake2s(storage_path, digest_size=6).hexdigest()>.wav`; `ensure_take_audio(project_root, storage_path, source, *, sample_rate=8000, ffmpeg_binary="ffmpeg", project=None) -> Path` - same extract/cache/storage-push pattern as `ensure_video_audio`.
- Produces: `GET /api/shooters/{slug}/raw-videos/peaks?filename=<name>&bins=3000` - resolves the take WAV (local: extract on demand; hosted API process: pull `<scope>/audio/take_<hash>.wav` peaks JSON from storage - the worker pushed the WAV and its `.peaks-<bins>.json` after detection; when absent return 202 `{"pending": true}`). Success returns `waveform_helpers.ensure_peaks(...)` payload (same shape as the existing `/stages/{n}/peaks`).
- Produces: `GET /api/shooters/{slug}/raw-videos/overview?filename=<name>` - JSON:

```json
{
  "raw_video": { ...RawVideo dump... },
  "duration_seconds": 1800.0,
  "stages": [
    {
      "stage_number": 2, "stage_name": "Short course", "video_id": "abc123def456",
      "role": "primary", "beep_time": 512.3, "beep_confidence": 0.91,
      "beep_reviewed": true, "beep_window": [420.0, 700.0],
      "beep_window_source": "scoreboard",
      "status": "found"
    }
  ],
  "conflicts": [2, 3]
}
```

`status` per stage-video: `"found"` (beep_time set), `"none"` (`beep_auto_detect_failed`), `"pending"` (neither - not yet detected or job active). `conflicts` = `beep_windows.find_beep_conflicts({stage: beep_time for found}, config.conflict_threshold_s)`. 404 when the filename has no RawVideo. No persisted confirmed state anywhere in this payload beyond the existing per-video `beep_reviewed`.
- Worker hook: at the end of a take-windowed detect job, `ensure_take_audio` + `waveform_helpers.ensure_peaks(wav, 3000)` + push both to storage (WAV push already happens via `_try_push_audio_to_storage`; push the peaks JSON with `storage.write_bytes` under the same scope). Wrap in try/except-log - peaks are a nicety, never fail the detect job.

- [ ] **Step 1: Failing tests** (endpoint tests with stubbed ffmpeg/peaks; assert 202-pending path by leaving storage empty in a hosted-style client). **Step 2: Run** - FAIL. **Step 3: Implement.** **Step 4: Run** - PASS.
- [ ] **Step 5: Commit** `feat(api): take peaks + clip-level overview endpoint`

---

### Task 9: SPA - api client + coverage multi-select at attach (hosted) and in ClipDetail (local)

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (types + 4 methods)
- Modify: `src/splitsmith/ui_static/src/components/AddFootageModal.tsx` (hosted attach step)
- Modify: `src/splitsmith/ui_static/src/pages/ingest/ClipDetail.tsx` (local coverage control)
- Create: `src/splitsmith/ui_static/src/components/ingest/CoverageSelect.tsx`

**Interfaces:**
- api.ts additions (follow the file's existing `request<T>` + doc-comment style):

```ts
export interface CoverageSuggestion { covers_stages: number[]; span: { start: string; end: string } | null }
export interface TakeOverviewStage {
  stage_number: number; stage_name: string; video_id: string; role: string;
  beep_time: number | null; beep_confidence: number | null; beep_reviewed: boolean;
  beep_window: [number, number] | null; beep_window_source: string | null;
  status: "found" | "none" | "pending";
}
export interface TakeOverview {
  raw_video: RawVideoInfo; duration_seconds: number | null;
  stages: TakeOverviewStage[]; conflicts: number[];
}
suggestCoverage(slug, body: { recorded_start?: string | null; duration_s?: number | null; path?: string | null }): Promise<CoverageSuggestion>
setRawVideoCoverage(slug, body: { filename: string; covers_stages: number[] }): Promise<...project dump type used elsewhere...>
takeOverview(slug, filename): Promise<TakeOverview>
takePeaks(slug, filename, bins): Promise<PeaksResult | { pending: true }>
setBeepWindow(slug, stageNumber, videoId, body: { start_s: number; end_s: number }): Promise<JobInfo>
```

Also extend the existing `attachRawVideo` body type with `duration_seconds?: number | null; recorded_start?: string | null`.
- `CoverageSelect` - controlled multi-select of the match's stages (chips with stage number + name, toggle on click, keyboard accessible, selection order preserved and displayed as ordinal badges since declared order = shooting order for scoreboard-less matches). Props: `{ stages: {stage_number: number; stage_name: string}[]; value: number[]; onChange(v: number[]): void; suggested?: number[] }` with a "use suggestion" affordance when `suggested` is non-empty and differs from `value`.
- AddFootageModal: in the uploaded-file row's attach step, probe duration + recorded start client-side before calling attach:

```ts
const probeFile = (file: File): Promise<{ duration_s: number | null; recorded_start: string | null }> =>
  new Promise((resolve) => {
    const el = document.createElement("video");
    el.preload = "metadata";
    el.onloadedmetadata = () => {
      const duration = Number.isFinite(el.duration) ? el.duration : null;
      URL.revokeObjectURL(el.src);
      resolve({
        duration_s: duration,
        recorded_start:
          duration != null && file.lastModified
            ? new Date(file.lastModified - duration * 1000).toISOString()
            : null,
      });
    };
    el.onerror = () => { URL.revokeObjectURL(el.src); resolve({ duration_s: null, recorded_start: null }); };
    el.src = URL.createObjectURL(file);
  });
```

Feed the probed span to `suggestCoverage`, pre-fill `CoverageSelect`, pass the final `covers_stages` (plus `duration_seconds`/`recorded_start`) to `attachRawVideo`. Empty selection keeps today's unassigned-tray behavior. Keep the probe results in component state keyed by filename so attach-after-upload still has them.
- ClipDetail: add a "Covers stages" section rendering `CoverageSelect` seeded from `suggestCoverage({ path })`, applying via `setRawVideoCoverage`. On success refresh project state through the page's existing reload callback.

- [ ] **Step 1: Implement api.ts + CoverageSelect + modal + ClipDetail.** Read each file's local conventions first (the modal's phase state machine, ClipDetail's data loading in `pages/ingest/model.ts`).
- [ ] **Step 2: Verify** `pnpm -C src/splitsmith/ui_static typecheck && pnpm -C src/splitsmith/ui_static build` - PASS; `pnpm -C src/splitsmith/ui_static exec eslint src/lib/api.ts src/components/AddFootageModal.tsx src/components/ingest/CoverageSelect.tsx src/pages/ingest/ClipDetail.tsx` - clean.
- [ ] **Step 3: Commit** `feat(ui): stage coverage declaration with span-based suggestion`

---

### Task 10: SPA - TakeOverview page

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/TakeOverview.tsx`
- Modify: the router registration (grep `pages/Audit` import site, likely `App.tsx` or a routes module) - route `take/:filename` under the existing match/shooter layout (inspect `matchHref.ts` + `MatchShell.tsx` for the canonical path shape and add a `takeHref` helper alongside the existing href builders)
- Modify: `src/splitsmith/ui_static/src/pages/ingest/ClipDetail.tsx` and the ingest clip list row - "Take overview" link for clips whose RawVideo covers 2+ stages; stage pages (BeepReview or the stage header component) get a "part of take <filename>" link when the primary's path matches a multi-covered RawVideo

**Interfaces:**
- Page behavior (reuse `Waveform.tsx` - read its props first; it renders peaks with overlay children in the audit page):
  - Load `takeOverview` + `takePeaks(slug, filename, 3000)`; while peaks are `{pending: true}` poll every few seconds with a "waveform is being generated on the worker" notice.
  - Render the full-file envelope; per covered stage a shaded region for `beep_window` (label = stage number + name), a beep marker at `beep_time` with confidence, and a status pill reusing the design system's `StatusPill` - `found` / `none` / `pending`, with `conflicts` membership rendering an additional warning pill ("shares a beep with stage N"). Color is never the sole carrier - pills carry text, regions carry labels (accessibility memory).
  - Drag a window's edges (pointer events on the region handles; follow the drag/hit-zone patterns from the audit waveform interaction work in `Audit.tsx` / `MarkerLayer.tsx`) then "Re-run detection" calls `setBeepWindow` (source becomes manual server-side) and refetches.
  - Coverage edit: an "Edit coverage" affordance opens `CoverageSelect` (Task 9) pre-filled with current coverage; apply calls `setRawVideoCoverage` and refetches.
  - Each stage row links to the existing per-stage beep review route (grep how BeepReview builds its href). No confirmed/approve button exists on this page.
- [ ] **Step 1: Implement.**
- [ ] **Step 2: Verify** typecheck + build + scoped eslint as in Task 9; then a bounded headless screenshot of the page against a dev server per the UI-verification memory (domcontentloaded, `/match/:matchId` is singular).
- [ ] **Step 3: Commit** `feat(ui): take overview - carve-up review for multi-stage clips`

---

### Task 11: SPEC.md + docs

**Files:**
- Modify: `SPEC.md` (pipeline section ~23-53: one source file may cover N stages; beep detection runs per (stage, video) inside a derived search window; module responsibilities: `beep_windows.py`, take overview endpoints; project layout: `beep_window` fields, `RawVideo.duration_seconds`/`recorded_start`, take WAV/peaks cache names)
- Modify: `CLAUDE.md` only if the detection-pipeline summary section needs the one-line window note (keep minimal)
- [ ] **Step 1: Write.** **Step 2: Commit** `docs: multi-stage single-take pipeline documentation`

---

### Task 12: Verification sweep

- [ ] `uv run ruff check src tests && uv run black --check src tests`
- [ ] `uv run pytest -q -m "not integration and not docker"` - all green
- [ ] `pnpm -C src/splitsmith/ui_static typecheck && pnpm -C src/splitsmith/ui_static build`
- [ ] Scoped eslint over every touched SPA file - clean
- [ ] Grep added lines for "--"/em dash in new prose: `git diff main -- '*.py' '*.ts' '*.tsx' '*.md' | grep '^+' | grep -nE '(--|—)'` and fix hits in NEW text (pre-existing patterns quoted from old code are fine)
- [ ] Commit any fixes; do not open the PR from inside a task (the session driver owns the PR)

## Self-review notes

- Spec coverage: data model (Tasks 1, 2, 5), window derivation (3), detection job (4, 5), ingest UX both modes (6, 7, 9), take overview (8, 10), error handling (soft-fail in 5, conflicts in 3/8, coverage edit in 6, manual window in 2), testing rules woven per task, SPEC.md (11). Global clock-skew nudge and approach B/C: out of scope per spec.
- Code-reality corrections vs the spec: `video_id` needs a stamped `stage_number` field (StageVideo has no stage back-ref); `covers_stages` was force-sorted (breaks declared-order fallback - fixed in Task 6); attach currently enqueues nothing; peaks infra already exists (`waveform.ensure_peaks`) so Task 8 reuses it; hosted suggestion uses client-side duration probing (server cannot ffprobe an R2 object cheaply).
