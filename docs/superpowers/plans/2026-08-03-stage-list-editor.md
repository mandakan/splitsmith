# Stage-List Editor Implementation Plan (#521)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a hosted user add, remove, and rename stages on an existing match from the SPA, without losing audit progress on untouched stages.

**Architecture:** A new `src/splitsmith/ui/stage_edit.py` module owns the diff, the fan-out across shooters, and the artifact purge, following the extracted-module pattern of `match_delete.py` and `shooter_move.py`. `server.py` gets request/response models and one `PUT /api/match/stages` endpoint that delegates to it. `stage_number` is stable for a stage's lifetime: removal leaves a gap and freed numbers are never reused, which avoids an artifact-migration engine. The SPA gets an `EditStagesDrawer` reached from the match overview.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pytest, React + TypeScript (Vite), pnpm.

**Spec:** `docs/superpowers/specs/2026-08-03-stage-list-editor-design.md`. Read it before Task 1.

## Global Constraints

- Python 3.11+, type hints everywhere. `uv` for dependency management, never `pip`.
- Add no new dependencies. The dep list is small on purpose.
- Black formatting, line length **110**. Run `uv run black src tests` before every commit -- CI has a format gate and hand-written test snippets routinely run over 110 columns.
- Ruff for linting. `pathlib.Path` for paths, never strings. f-strings for formatting.
- Pydantic models for all data crossing module boundaries. No dicts of unknown shape.
- Imports grouped stdlib, third-party, local, separated by blank lines. No relative imports beyond a single dot.
- Detection logic stays out of the CLI; `server.py` orchestrates, `stage_edit.py` does the work.
- Every test must fail against pre-change code. Where a status code is identical before and after, assert on the artifact or on a recorded side effect instead -- that is the discriminating assertion.
- Do **not** write "stage_number is the permanent identity" into any docstring. It is stable, not permanent; the spec records the intended future move to renumbering plus reordering.
- Run `uv run pytest tests/<file> -v` for the named tests at each step. Full suite before the final commit.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/splitsmith/ui/stage_edit.py` (create) | Diff, validation, fan-out, purge, summary. No FastAPI imports except `HTTPException`-free -- raises its own exceptions. |
| `src/splitsmith/ui/server.py` (modify) | `StageEditRequest` / `StageEditSummary` wire models, `PUT /api/match/stages`, `AppState.delete_audit`. |
| `src/splitsmith/ui/project.py` (modify) | `MatchProject.unassign_stage_videos`. |
| `src/splitsmith/ui_static/src/lib/api.ts` (modify) | `editMatchStages`; delete dead `createPlaceholderStages`. |
| `src/splitsmith/ui_static/src/components/match/EditStagesDrawer.tsx` (create) | The editor UI. |
| `src/splitsmith/ui_static/src/pages/Home.tsx` (modify) | Entry points, both variants. |
| `tests/test_stage_edit.py` (create) | Unit tests for the engine. |
| `tests/test_ui_server_stage_edit.py` (create) | Endpoint tests through `_MatchClient`. |

## Codebase Orientation

Facts an implementer needs and cannot guess:

- `Match.stages` is `list[MatchStageDefinition]` (`stage_number`, `stage_name`, `stage_rounds`, `placeholder`) in `match.json`. Each shooter's `MatchProject.stages` is `list[StageEntry]`, joined on `stage_number`.
- `state.match()` (`server.py:1255`) returns a `Match` with the state store already bound; `match.save(root)` round-trips through Postgres under optimistic locking in hosted mode and writes `match.json` locally. Same split for `state.shooter_project(slug)` (`server.py:1362`) and `project.save(root)`. A stale version raises `StateConflictError`.
- Storage scope for a shooter is `matches/{match_id}/shooters/{slug}`. Derived artifacts live at `<scope>/audio/<basename>` and `<scope>/trimmed/<basename>` (`audio.py:685-696`). Local mode has no storage bound.
- Per-stage artifact basenames come in two tiers: per-cam (`stage<N>_cam_<video_id>.wav`, `..._audit.wav`, `..._trimmed.mp4`) and legacy (`stage<N>_primary.wav`, `stage<N>_audit.wav`, `stage<N>_trimmed.mp4`, `.params.json`, `.partial.mp4`). A single `stage<N>_*` glob covers both.
- `state.storage.list(prefix)` yields objects with a `.path`; `state.storage.delete(path)` removes one (`match_delete.py:109-118`).
- `project_state.delete_audit(match_id, slug, stage_number) -> int` already exists (`db/project_state.py:206`).
- **Jobs carry no shooter slug** -- `Job` has `stage_number` but nothing identifying the shooter. Since removal is match-level and fans out to every shooter, filtering active jobs on `stage_number` alone is exactly correct.
- `jobs.list()` returns `list[Job]`; `jobs.cancel(job_id)` cancels one. Never use `cancel_active_for_user` -- it kills the user's unrelated work.
- Test helpers: `tests/test_ui_server.py` has `_MatchClient` which rewrites scoped prefixes, and seeding helpers such as `_seed_match_export_project`. The autouse `SPLITSMITH_AUTO_BEEP_DISABLED=1` fixture is scoped to `test_ui_server.py` and is **not** inherited by other modules -- set it explicitly.

---

### Task 1: Stage-list diff

**Files:**
- Create: `src/splitsmith/ui/stage_edit.py`
- Test: `tests/test_stage_edit.py`

**Interfaces:**
- Consumes: `splitsmith.match_model.MatchStageDefinition`.
- Produces:
  - `class StageEditError(Exception)` -- base for validation failures.
  - `class SubmittedStage(BaseModel)`: `stage_number: int | None`, `stage_name: str`, `stage_rounds: StageRounds | None = None`.
  - `class StageListDiff(BaseModel)`: `removed: list[int]`, `added: list[MatchStageDefinition]`, `renamed: list[MatchStageDefinition]`, `unchanged: list[int]`.
  - `def diff_stage_list(existing: list[MatchStageDefinition], submitted: list[SubmittedStage]) -> StageListDiff`

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the stage-list editor engine (#521)."""

import pytest

from splitsmith.match_model import MatchStageDefinition
from splitsmith.ui.stage_edit import (
    StageEditError,
    StageListDiff,
    SubmittedStage,
    diff_stage_list,
)


def _existing(*numbers: int) -> list[MatchStageDefinition]:
    return [MatchStageDefinition(stage_number=n, stage_name=f"Stage {n}") for n in numbers]


def test_rename_only_is_not_a_removal() -> None:
    diff = diff_stage_list(
        _existing(1, 2, 3),
        [
            SubmittedStage(stage_number=1, stage_name="El Presidente"),
            SubmittedStage(stage_number=2, stage_name="Stage 2"),
            SubmittedStage(stage_number=3, stage_name="Stage 3"),
        ],
    )
    assert diff.removed == []
    assert diff.added == []
    assert [s.stage_number for s in diff.renamed] == [1]
    assert diff.renamed[0].stage_name == "El Presidente"
    assert diff.unchanged == [2, 3]


def test_removal_is_detected_by_absence() -> None:
    diff = diff_stage_list(
        _existing(1, 2, 3, 4, 5),
        [
            SubmittedStage(stage_number=n, stage_name=f"Stage {n}")
            for n in (1, 2, 4, 5)
        ],
    )
    assert diff.removed == [3]
    assert diff.added == []


def test_added_rows_allocate_above_the_current_max_not_the_gap() -> None:
    """The freed number 3 must never be handed out again."""
    diff = diff_stage_list(
        _existing(1, 2, 4, 5),
        [
            SubmittedStage(stage_number=1, stage_name="Stage 1"),
            SubmittedStage(stage_number=2, stage_name="Stage 2"),
            SubmittedStage(stage_number=4, stage_name="Stage 4"),
            SubmittedStage(stage_number=5, stage_name="Stage 5"),
            SubmittedStage(stage_number=None, stage_name="Standards"),
        ],
    )
    assert [s.stage_number for s in diff.added] == [6]
    assert diff.added[0].stage_name == "Standards"


def test_two_added_rows_get_consecutive_numbers() -> None:
    diff = diff_stage_list(
        _existing(1, 2),
        [
            SubmittedStage(stage_number=1, stage_name="Stage 1"),
            SubmittedStage(stage_number=2, stage_name="Stage 2"),
            SubmittedStage(stage_number=None, stage_name="A"),
            SubmittedStage(stage_number=None, stage_name="B"),
        ],
    )
    assert [s.stage_number for s in diff.added] == [3, 4]


def test_add_to_an_empty_existing_list_starts_at_one() -> None:
    diff = diff_stage_list([], [SubmittedStage(stage_number=None, stage_name="Only")])
    assert [s.stage_number for s in diff.added] == [1]


def test_unknown_stage_number_is_rejected_not_implicitly_added() -> None:
    with pytest.raises(StageEditError, match="99"):
        diff_stage_list(
            _existing(1, 2),
            [
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
                SubmittedStage(stage_number=99, stage_name="Ghost"),
            ],
        )


def test_empty_submission_is_rejected() -> None:
    with pytest.raises(StageEditError, match="at least one stage"):
        diff_stage_list(_existing(1, 2), [])


def test_duplicate_stage_number_is_rejected() -> None:
    with pytest.raises(StageEditError, match="duplicate"):
        diff_stage_list(
            _existing(1, 2),
            [
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=1, stage_name="Stage 1 again"),
            ],
        )


def test_blank_stage_name_is_rejected() -> None:
    with pytest.raises(StageEditError, match="stage_name"):
        diff_stage_list(
            _existing(1),
            [SubmittedStage(stage_number=1, stage_name="   ")],
        )


def test_stage_name_is_trimmed() -> None:
    diff = diff_stage_list(
        _existing(1),
        [SubmittedStage(stage_number=1, stage_name="  Padded  ")],
    )
    assert diff.renamed[0].stage_name == "Padded"


def test_changed_stage_rounds_counts_as_a_rename() -> None:
    from splitsmith.ui.project import StageRounds

    diff = diff_stage_list(
        _existing(1),
        [
            SubmittedStage(
                stage_number=1,
                stage_name="Stage 1",
                stage_rounds=StageRounds(min_rounds=12),
            )
        ],
    )
    assert [s.stage_number for s in diff.renamed] == [1]
    assert diff.renamed[0].stage_rounds is not None
    assert diff.renamed[0].stage_rounds.min_rounds == 12
```

Before writing the `StageRounds` import, confirm where the model actually lives:

```bash
grep -rn "class StageRounds" src/splitsmith/
```

Use that import path in both the test and the module. If `StageRounds` has required fields other than `min_rounds`, adjust the last test to satisfy them.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stage_edit.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.ui.stage_edit'`

- [ ] **Step 3: Write the implementation**

```python
"""Stage-list editing for an existing match (#521).

Adds, removes, and renames stages on a match that already has audit
progress. ``stage_number`` is stable for a stage's lifetime: removing a
stage leaves a gap and freed numbers are never handed out again, so every
per-stage artifact key (``audit/stage<N>.json``, ``stage<N>_cam_*.wav``,
``stage<N>_cam_*_trimmed.mp4``) stays valid for the stages the user did
not touch. That is what lets this ship without an artifact-migration
engine; see the design doc for the renumber/reorder work it defers.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from splitsmith.match_model import MatchStageDefinition
from splitsmith.ui.project import StageRounds


class StageEditError(Exception):
    """A stage-list submission the server refuses. Maps to HTTP 400."""


class SubmittedStage(BaseModel):
    """One row of the SPA's stage editor.

    ``stage_number is None`` marks a row the user added; the server
    allocates its number. A number the match does not have is an error,
    not an implicit add -- a client sending a stale number should learn
    about it rather than silently create a stage.
    """

    stage_number: int | None = None
    stage_name: str
    stage_rounds: StageRounds | None = None


class StageListDiff(BaseModel):
    """What a submission changes, relative to the match's current list."""

    removed: list[int] = Field(default_factory=list)
    added: list[MatchStageDefinition] = Field(default_factory=list)
    renamed: list[MatchStageDefinition] = Field(default_factory=list)
    unchanged: list[int] = Field(default_factory=list)


def diff_stage_list(
    existing: list[MatchStageDefinition],
    submitted: list[SubmittedStage],
) -> StageListDiff:
    """Diff ``submitted`` against ``existing``. Raises :class:`StageEditError`.

    New rows are numbered from ``max(existing) + 1`` upward, deliberately
    skipping numbers freed by an earlier removal: a worker whose
    cancellation lost the race can still write ``stage<freed>_*`` bytes,
    and never reusing the number keeps that write inert instead of letting
    it reattach to a live stage.
    """
    if not submitted:
        raise StageEditError("a match must keep at least one stage")

    by_number = {s.stage_number: s for s in existing}
    seen: set[int] = set()
    for row in submitted:
        if not row.stage_name.strip():
            raise StageEditError("stage_name must not be blank")
        if row.stage_number is None:
            continue
        if row.stage_number in seen:
            raise StageEditError(f"duplicate stage_number {row.stage_number}")
        if row.stage_number not in by_number:
            raise StageEditError(
                f"no stage {row.stage_number} in this match; "
                "send stage_number=null to add a stage"
            )
        seen.add(row.stage_number)

    diff = StageListDiff()
    diff.removed = sorted(set(by_number) - seen)

    next_number = (max(by_number) if by_number else 0) + 1
    for row in submitted:
        name = row.stage_name.strip()
        if row.stage_number is None:
            diff.added.append(
                MatchStageDefinition(
                    stage_number=next_number,
                    stage_name=name,
                    stage_rounds=row.stage_rounds,
                    placeholder=True,
                )
            )
            next_number += 1
            continue
        current = by_number[row.stage_number]
        if name != current.stage_name or row.stage_rounds != current.stage_rounds:
            diff.renamed.append(
                MatchStageDefinition(
                    stage_number=current.stage_number,
                    stage_name=name,
                    stage_rounds=row.stage_rounds,
                    placeholder=current.placeholder,
                )
            )
        else:
            diff.unchanged.append(current.stage_number)

    diff.unchanged.sort()
    return diff
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stage_edit.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Format and commit**

```bash
uv run black src/splitsmith/ui/stage_edit.py tests/test_stage_edit.py
uv run pytest tests/test_stage_edit.py -q
git add src/splitsmith/ui/stage_edit.py tests/test_stage_edit.py
git commit -m "feat(ui): stage-list diff for the editor (#521)"
```

---

### Task 2: Unassign a stage's videos

**Files:**
- Modify: `src/splitsmith/ui/project.py` (add a method to `MatchProject`, near `init_placeholder_stages` at line 1489)
- Test: `tests/test_ui_project.py`

**Interfaces:**
- Produces: `MatchProject.unassign_stage_videos(self, stage_number: int) -> int` -- moves that stage's videos to `unassigned_videos` with `role = "secondary"`, returns how many moved. No-op returning 0 when the stage does not exist.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui_project.py`. Read an existing test in that file first to match how it builds a `MatchProject` with videos; mirror that construction rather than inventing one.

```python
def test_unassign_stage_videos_moves_videos_to_unassigned(tmp_path: Path) -> None:
    project = MatchProject(name="M")
    project.init_placeholder_stages(3)
    stage = project.stages[1]
    stage.videos = [
        StageVideo(path=Path("a.mp4"), role="primary"),
        StageVideo(path=Path("b.mp4"), role="secondary"),
    ]

    moved = project.unassign_stage_videos(2)

    assert moved == 2
    assert stage.videos == []
    assert {str(v.path) for v in project.unassigned_videos} == {"a.mp4", "b.mp4"}
    assert all(v.role == "secondary" for v in project.unassigned_videos)


def test_unassign_stage_videos_leaves_other_stages_alone(tmp_path: Path) -> None:
    project = MatchProject(name="M")
    project.init_placeholder_stages(3)
    project.stages[0].videos = [StageVideo(path=Path("keep.mp4"), role="primary")]
    project.stages[1].videos = [StageVideo(path=Path("drop.mp4"), role="primary")]

    project.unassign_stage_videos(2)

    assert [str(v.path) for v in project.stages[0].videos] == ["keep.mp4"]
    assert project.stages[0].videos[0].role == "primary"


def test_unassign_stage_videos_unknown_stage_is_a_noop(tmp_path: Path) -> None:
    project = MatchProject(name="M")
    project.init_placeholder_stages(2)
    assert project.unassign_stage_videos(99) == 0
    assert project.unassigned_videos == []
```

If `StageVideo` requires more than `path` and `role`, adjust construction to match the existing tests in the file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui_project.py -k unassign_stage_videos -v`
Expected: FAIL, `AttributeError: 'MatchProject' object has no attribute 'unassign_stage_videos'`

- [ ] **Step 3: Write the implementation**

Add to `MatchProject`, directly after `init_placeholder_stages`:

```python
    def unassign_stage_videos(self, stage_number: int) -> int:
        """Move ``stage_number``'s videos to ``unassigned_videos``.

        Used when a stage is removed from the match (#521). Uploaded
        footage is the only artifact that cannot be regenerated -- in
        hosted it may be the user's sole copy -- so removal releases it
        for re-binding instead of deleting it. Demoting to ``secondary``
        matches :meth:`init_placeholder_stages`: an unassigned video has
        no stage to be primary of, and the next assignment decides the
        role. Returns the number of videos moved.
        """
        for stage in self.stages:
            if stage.stage_number != stage_number:
                continue
            moved = list(stage.videos)
            for video in moved:
                video.role = "secondary"
                self.unassigned_videos.append(video)
            stage.videos = []
            return len(moved)
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui_project.py -k unassign_stage_videos -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Format and commit**

```bash
uv run black src/splitsmith/ui/project.py tests/test_ui_project.py
uv run pytest tests/test_ui_project.py -q
git add src/splitsmith/ui/project.py tests/test_ui_project.py
git commit -m "feat(ui): MatchProject.unassign_stage_videos (#521)"
```

---

### Task 3: Purge a stage's derived artifacts

**Files:**
- Modify: `src/splitsmith/ui/stage_edit.py`
- Test: `tests/test_stage_edit.py`

**Interfaces:**
- Consumes: `StageEditError` from Task 1.
- Produces:
  - `class PurgeCounts(BaseModel)`: `files_deleted: int = 0`, `objects_deleted: int = 0`, `errors: list[str] = []`
  - `def purge_stage_artifacts(project: MatchProject, root: Path, stage_number: int) -> PurgeCounts`

The `stage<N>_` prefix with its trailing underscore is load-bearing: a bare `stage<N>` prefix makes removing stage 3 delete stage 30's artifacts. There is a test for exactly this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stage_edit.py`:

```python
class _FakeObject:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeStorage:
    """Minimal stand-in for the S3/R2 storage backend.

    Mirrors the three methods the purge uses: ``list(prefix)`` yielding
    objects with a ``.path``, and ``delete(path)``. ``fail_on`` makes one
    delete raise so the error-collection path can be exercised.
    """

    def __init__(self, paths: list[str], *, fail_on: str | None = None) -> None:
        self.paths = list(paths)
        self.deleted: list[str] = []
        self.fail_on = fail_on

    def list(self, prefix: str):
        return [_FakeObject(p) for p in self.paths if p.startswith(prefix)]

    def delete(self, path: str) -> None:
        if path == self.fail_on:
            raise RuntimeError("boom")
        self.deleted.append(path)
        self.paths.remove(path)


def _project_with_dirs(tmp_path):
    from splitsmith.ui.project import MatchProject

    project = MatchProject(name="M")
    project.init_placeholder_stages(3)
    project.audio_path(tmp_path).mkdir(parents=True, exist_ok=True)
    project.trimmed_path(tmp_path).mkdir(parents=True, exist_ok=True)
    return project


def test_purge_deletes_local_artifacts_for_the_stage(tmp_path) -> None:
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    audio = project.audio_path(tmp_path)
    trimmed = project.trimmed_path(tmp_path)
    (audio / "stage3_cam_abc.wav").write_bytes(b"x")
    (audio / "stage3_cam_abc_audit.wav").write_bytes(b"x")
    (audio / "stage3_primary.wav").write_bytes(b"x")
    (audio / "stage3_audit.peaks-100.json").write_bytes(b"x")
    (trimmed / "stage3_cam_abc_trimmed.mp4").write_bytes(b"x")
    (trimmed / "stage3_trimmed.params.json").write_bytes(b"x")

    counts = purge_stage_artifacts(project, tmp_path, 3)

    assert counts.files_deleted == 6
    assert counts.errors == []
    assert list(audio.glob("stage3_*")) == []
    assert list(trimmed.glob("stage3_*")) == []


def test_purge_leaves_neighbouring_stages_untouched(tmp_path) -> None:
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    audio = project.audio_path(tmp_path)
    (audio / "stage3_cam_abc.wav").write_bytes(b"x")
    keep = audio / "stage4_cam_abc.wav"
    keep.write_bytes(b"keep")

    purge_stage_artifacts(project, tmp_path, 3)

    assert keep.read_bytes() == b"keep"


def test_purging_stage_3_does_not_delete_stage_30(tmp_path) -> None:
    """The trailing underscore in the glob is what makes this pass."""
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    audio = project.audio_path(tmp_path)
    (audio / "stage3_cam_abc.wav").write_bytes(b"x")
    two_digit = audio / "stage30_cam_abc.wav"
    two_digit.write_bytes(b"keep")

    counts = purge_stage_artifacts(project, tmp_path, 3)

    assert counts.files_deleted == 1
    assert two_digit.read_bytes() == b"keep"


def test_purge_deletes_storage_objects_for_the_stage(tmp_path) -> None:
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    scope = "matches/m1/shooters/me"
    storage = _FakeStorage(
        [
            f"{scope}/audio/stage3_cam_abc.wav",
            f"{scope}/audio/stage30_cam_abc.wav",
            f"{scope}/audio/stage4_cam_abc.wav",
            f"{scope}/trimmed/stage3_cam_abc_trimmed.mp4",
            f"{scope}/trimmed/stage3_cam_abc_trimmed.params.json",
        ]
    )
    project.bind_storage(storage, scope=scope)

    counts = purge_stage_artifacts(project, tmp_path, 3)

    assert counts.objects_deleted == 3
    assert sorted(storage.deleted) == sorted(
        [
            f"{scope}/audio/stage3_cam_abc.wav",
            f"{scope}/trimmed/stage3_cam_abc_trimmed.mp4",
            f"{scope}/trimmed/stage3_cam_abc_trimmed.params.json",
        ]
    )
    assert f"{scope}/audio/stage30_cam_abc.wav" in storage.paths


def test_purge_collects_storage_errors_instead_of_raising(tmp_path) -> None:
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    scope = "matches/m1/shooters/me"
    doomed = f"{scope}/audio/stage3_cam_abc.wav"
    storage = _FakeStorage(
        [doomed, f"{scope}/trimmed/stage3_cam_abc_trimmed.mp4"],
        fail_on=doomed,
    )
    project.bind_storage(storage, scope=scope)

    counts = purge_stage_artifacts(project, tmp_path, 3)

    assert counts.objects_deleted == 1
    assert len(counts.errors) == 1
    assert "boom" in counts.errors[0]


def test_purge_with_no_storage_bound_is_local_only(tmp_path) -> None:
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    (project.audio_path(tmp_path) / "stage3_cam_abc.wav").write_bytes(b"x")

    counts = purge_stage_artifacts(project, tmp_path, 3)

    assert counts.files_deleted == 1
    assert counts.objects_deleted == 0
```

Confirm `bind_storage`'s signature before writing these:

```bash
grep -n "def bind_storage" -A 10 src/splitsmith/ui/project.py
grep -n "def audio_path\|def trimmed_path" -A 5 src/splitsmith/ui/project.py
```

Match the real signature; the plan assumes `bind_storage(storage, scope=...)` setting `_storage` and `_storage_scope`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stage_edit.py -k purge -v`
Expected: FAIL, `ImportError: cannot import name 'purge_stage_artifacts'`

- [ ] **Step 3: Write the implementation**

Add to `stage_edit.py` (extend the imports with `from pathlib import Path` and `from splitsmith.ui.project import MatchProject, StageRounds`):

```python
class PurgeCounts(BaseModel):
    """What a single stage's artifact purge managed to remove."""

    files_deleted: int = 0
    objects_deleted: int = 0
    errors: list[str] = Field(default_factory=list)


def _stage_artifact_glob(stage_number: int) -> str:
    """Glob matching every artifact basename for ``stage_number``.

    Covers both naming tiers in one pattern: per-cam
    (``stage3_cam_<id>.wav``, ``..._audit.wav``, ``..._trimmed.mp4``) and
    legacy (``stage3_primary.wav``, ``stage3_audit.peaks-*.json``,
    ``stage3_trimmed.params.json``).

    The trailing underscore is load-bearing. ``stage3*`` would also match
    ``stage30_cam_x.wav``, so removing stage 3 would silently delete stage
    30's cached audio and trim.
    """
    return f"stage{stage_number}_*"


def purge_stage_artifacts(
    project: MatchProject,
    root: Path,
    stage_number: int,
) -> PurgeCounts:
    """Delete every derived artifact for ``stage_number``, locally and in storage.

    Derived state only -- audio WAVs, peaks, trims and their sidecars. The
    audit doc is purged separately (it lives in ``state_docs`` in hosted
    mode, not in these directories) and the stage's videos are released by
    :meth:`MatchProject.unassign_stage_videos` rather than deleted.

    Best-effort, following ``match_delete``: a failed delete is recorded in
    ``errors`` and the sweep continues. Leaving the stage list unwritten
    because one cache object refused to die would be the worse outcome.
    """
    counts = PurgeCounts()
    pattern = _stage_artifact_glob(stage_number)

    for directory in (project.audio_path(root), project.trimmed_path(root)):
        if not directory.exists():
            continue
        for victim in sorted(directory.glob(pattern)):
            try:
                victim.unlink()
                counts.files_deleted += 1
            except OSError as exc:
                counts.errors.append(f"delete {victim}: {exc}")

    storage = project._storage
    scope = project._storage_scope
    if storage is None or scope is None:
        return counts

    prefix_basename = f"stage{stage_number}_"
    for subdir in ("audio", "trimmed"):
        prefix = f"{scope}/{subdir}/"
        try:
            objects = list(storage.list(prefix))
        except Exception as exc:  # noqa: BLE001 -- best-effort teardown
            counts.errors.append(f"list storage {prefix!r}: {exc}")
            continue
        for obj in objects:
            if not obj.path.rsplit("/", 1)[-1].startswith(prefix_basename):
                continue
            try:
                storage.delete(obj.path)
                counts.objects_deleted += 1
            except Exception as exc:  # noqa: BLE001
                counts.errors.append(f"delete {obj.path!r}: {exc}")

    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stage_edit.py -v`
Expected: PASS, all tests including the 6 new purge tests

- [ ] **Step 5: Prove the underscore guard is load-bearing**

Temporarily change `_stage_artifact_glob` to return `f"stage{stage_number}*"` (drop the underscore) and re-run:

Run: `uv run pytest tests/test_stage_edit.py -k stage_30 -v`
Expected: FAIL -- this confirms the test discriminates. Restore the underscore and re-run to green before committing.

- [ ] **Step 6: Format and commit**

```bash
uv run black src/splitsmith/ui/stage_edit.py tests/test_stage_edit.py
uv run pytest tests/test_stage_edit.py -q
git add src/splitsmith/ui/stage_edit.py tests/test_stage_edit.py
git commit -m "feat(ui): purge a stage's derived artifacts (#521)"
```

---

### Task 4: `AppState.delete_audit`

**Files:**
- Modify: `src/splitsmith/ui/server.py` (add to `AppState`, next to `save_audit` around line 1316)
- Test: `tests/test_ui_server_stage_edit.py` (create)

**Interfaces:**
- Produces: `AppState.delete_audit(self, slug: str, stage_number: int) -> bool` -- True when a doc was removed. Hosted delegates to `project_state.delete_audit(match_id, slug, stage_number)`; local unlinks `audit/stage<N>.json` and its `.bak` sibling.

The local branch must also remove the `.bak` file, because `save_audit` rotates the previous doc there -- leaving it behind means a stale audit survives the purge.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_server_stage_edit.py`. Read the top of `tests/test_ui_server.py` first for the app-construction helper and `_MatchClient`, and import them the same way other modules do.

```python
"""Endpoint + AppState tests for the stage-list editor (#521)."""

import json
import os
from pathlib import Path

import pytest

# The autouse SPLITSMITH_AUTO_BEEP_DISABLED fixture in test_ui_server.py is
# module-scoped and is NOT inherited here, so auto-beep would otherwise fire
# during seeding.
@pytest.fixture(autouse=True)
def _no_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def test_delete_audit_removes_the_local_doc_and_its_backup(tmp_path: Path) -> None:
    from splitsmith.ui.server import build_app  # adjust to the real factory

    app, state = build_app(tmp_path)  # adjust to the real return shape
    audit_file = state._audit_file("me", 3)
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.write_text(json.dumps({"shots": []}), encoding="utf-8")
    backup = audit_file.with_suffix(audit_file.suffix + ".bak")
    backup.write_text(json.dumps({"shots": ["stale"]}), encoding="utf-8")

    assert state.delete_audit("me", 3) is True
    assert not audit_file.exists()
    assert not backup.exists()


def test_delete_audit_is_false_when_no_doc_exists(tmp_path: Path) -> None:
    from splitsmith.ui.server import build_app

    app, state = build_app(tmp_path)
    assert state.delete_audit("me", 99) is False
```

The app factory's real name and signature are not guessed here -- find them:

```bash
grep -n "^def build_app\|^def create_app\|def _make_app" src/splitsmith/ui/server.py tests/test_ui_server.py | head
```

Use whatever `tests/test_ui_server.py` uses to construct an app plus reach its `AppState`, and mirror it.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ui_server_stage_edit.py -k delete_audit -v`
Expected: FAIL, `AttributeError: 'AppState' object has no attribute 'delete_audit'`

- [ ] **Step 3: Write the implementation**

Add to `AppState`, immediately after `save_audit`:

```python
    def delete_audit(self, slug: str, stage_number: int) -> bool:
        """Delete a stage's audit doc. Returns True when one was removed.

        The hosted/local split mirrors :meth:`load_audit` and
        :meth:`save_audit`. Used when a stage is removed from the match
        (#521): the doc describes shots on a stage that no longer exists,
        and leaving it behind risks a stale audit reattaching.

        The local branch also removes the ``.bak`` sibling that
        :meth:`save_audit` rotates the previous doc into -- deleting only
        the live file would leave the previous audit recoverable on disk
        after the user asked for the stage to be gone.
        """
        mid = current_match_id.get()
        store = self.project_state
        if store is not None and mid is not None:
            return run_sync(store.delete_audit(mid, slug, stage_number)) > 0
        audit_file = self._audit_file(slug, stage_number)
        backup = audit_file.with_suffix(audit_file.suffix + ".bak")
        removed = False
        for victim in (audit_file, backup):
            if victim.exists():
                try:
                    victim.unlink()
                    removed = removed or victim == audit_file
                except OSError as exc:
                    raise HTTPException(
                        status_code=500,
                        detail=f"audit delete failed: {exc}",
                    ) from exc
        return removed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ui_server_stage_edit.py -k delete_audit -v`
Expected: PASS, 2 tests

- [ ] **Step 5: Format and commit**

```bash
uv run black src/splitsmith/ui/server.py tests/test_ui_server_stage_edit.py
uv run pytest tests/test_ui_server_stage_edit.py -q
git add src/splitsmith/ui/server.py tests/test_ui_server_stage_edit.py
git commit -m "feat(ui): AppState.delete_audit for hosted and local (#521)"
```

---

### Task 5: Apply the diff across every shooter

**Files:**
- Modify: `src/splitsmith/ui/stage_edit.py`
- Test: `tests/test_stage_edit.py`

**Interfaces:**
- Consumes: `diff_stage_list`, `StageListDiff`, `purge_stage_artifacts`, `PurgeCounts` (Tasks 1 and 3); `MatchProject.unassign_stage_videos` (Task 2).
- Produces:
  - `class ShooterStageEditResult(BaseModel)`: `slug: str`, `videos_unassigned: int = 0`, `audit_docs_deleted: int = 0`, `files_deleted: int = 0`, `objects_deleted: int = 0`
  - `class StageEditSummary(BaseModel)`: `removed: list[int]`, `added: list[int]`, `renamed: list[int]`, `jobs_cancelled: int = 0`, `shooters: list[ShooterStageEditResult]`, `errors: list[str]`
  - `async def apply_stage_edit(*, match, root, submitted, shooter_slugs, load_project, save_project, delete_audit, cancel_jobs) -> StageEditSummary`

The callables are injected rather than reaching for `AppState`, matching how `shooter_move.py` takes `load_*_audit` / `save_target_audit` hooks. That keeps this module testable without a FastAPI app.

Injected callable contracts:

- `load_project(slug: str) -> MatchProject`
- `save_project(slug: str, project: MatchProject) -> None`
- `delete_audit(slug: str, stage_number: int) -> bool`
- `cancel_jobs(stage_numbers: set[int]) -> Awaitable[int]` -- cancels active jobs targeting those stages, returns the count

Ordering is fixed and must not be rearranged: cancel jobs, then per shooter (unassign videos, delete audit, purge caches, apply adds/renames, save project), then save the match doc last. The match doc going last means a crash mid-fan-out leaves the canonical list describing the pre-edit world.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stage_edit.py`:

```python
import asyncio


def _match_with_stages(*numbers: int):
    from splitsmith.match_model import Match

    match = Match(name="M")
    match.stages = _existing(*numbers)
    return match


def _harness(tmp_path, slugs, stage_numbers):
    """Build in-memory projects plus the injected callables."""
    from splitsmith.ui.project import MatchProject

    projects = {}
    for slug in slugs:
        project = MatchProject(name="M")
        project.init_placeholder_stages(max(stage_numbers))
        project.audio_path(tmp_path).mkdir(parents=True, exist_ok=True)
        project.trimmed_path(tmp_path).mkdir(parents=True, exist_ok=True)
        projects[slug] = project

    saved: list[str] = []
    audits_deleted: list[tuple[str, int]] = []
    cancelled: list[set[int]] = []

    async def cancel_jobs(stage_numbers_arg):
        cancelled.append(set(stage_numbers_arg))
        return len(stage_numbers_arg)

    hooks = {
        "load_project": lambda slug: projects[slug],
        "save_project": lambda slug, project: saved.append(slug),
        "delete_audit": lambda slug, n: (audits_deleted.append((slug, n)) or True),
        "cancel_jobs": cancel_jobs,
    }
    return projects, saved, audits_deleted, cancelled, hooks


def test_apply_removes_the_stage_from_every_shooter(tmp_path) -> None:
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    projects, saved, audits_deleted, cancelled, hooks = _harness(
        tmp_path, ["anna", "erik", "mathias"], [1, 2, 3]
    )

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["anna", "erik", "mathias"],
            **hooks,
        )
    )

    assert summary.removed == [3]
    assert [s.stage_number for s in match.stages] == [1, 2]
    for project in projects.values():
        assert [s.stage_number for s in project.stages] == [1, 2]
    assert sorted(saved) == ["anna", "erik", "mathias"]
    assert sorted(audits_deleted) == [("anna", 3), ("erik", 3), ("mathias", 3)]


def test_apply_preserves_untouched_stages_artifacts(tmp_path) -> None:
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    projects, _saved, _audits, _cancelled, hooks = _harness(tmp_path, ["me"], [1, 2, 3])
    audio = projects["me"].audio_path(tmp_path)
    (audio / "stage3_cam_a.wav").write_bytes(b"gone")
    survivor = audio / "stage2_cam_a.wav"
    survivor.write_bytes(b"keep")

    asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["me"],
            **hooks,
        )
    )

    assert survivor.read_bytes() == b"keep"
    assert not (audio / "stage3_cam_a.wav").exists()


def test_apply_cancels_jobs_for_removed_stages_only(tmp_path) -> None:
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    _p, _saved, _audits, cancelled, hooks = _harness(tmp_path, ["me"], [1, 2, 3])

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["me"],
            **hooks,
        )
    )

    assert cancelled == [{3}]
    assert summary.jobs_cancelled == 1


def test_apply_with_no_removals_cancels_nothing(tmp_path) -> None:
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2)
    _p, _saved, audits_deleted, cancelled, hooks = _harness(tmp_path, ["me"], [1, 2])

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Renamed"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["me"],
            **hooks,
        )
    )

    assert cancelled == []
    assert audits_deleted == []
    assert summary.jobs_cancelled == 0
    assert summary.renamed == [1]
    assert match.stages[0].stage_name == "Renamed"


def test_apply_adds_a_stage_to_match_and_every_shooter(tmp_path) -> None:
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2)
    projects, _saved, _audits, _cancelled, hooks = _harness(tmp_path, ["a", "b"], [1, 2])

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
                SubmittedStage(stage_number=None, stage_name="Standards"),
            ],
            shooter_slugs=["a", "b"],
            **hooks,
        )
    )

    assert summary.added == [3]
    assert [s.stage_number for s in match.stages] == [1, 2, 3]
    for project in projects.values():
        assert [s.stage_number for s in project.stages] == [1, 2, 3]
        added = project.stages[-1]
        assert added.stage_name == "Standards"
        assert added.time_seconds == 0.0
        assert added.placeholder is True


def test_apply_reports_per_shooter_counts(tmp_path) -> None:
    from splitsmith.ui.project import StageVideo
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    projects, _saved, _audits, _cancelled, hooks = _harness(
        tmp_path, ["haves", "havenots"], [1, 2, 3]
    )
    projects["haves"].stages[2].videos = [
        StageVideo(path=Path("a.mp4"), role="primary")
    ]

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["haves", "havenots"],
            **hooks,
        )
    )

    by_slug = {s.slug: s for s in summary.shooters}
    assert by_slug["haves"].videos_unassigned == 1
    assert by_slug["havenots"].videos_unassigned == 0
    assert projects["haves"].unassigned_videos[0].role == "secondary"


def test_apply_saves_the_match_doc_after_every_shooter(tmp_path) -> None:
    """Match doc last: a crash mid-fan-out must leave the canonical list
    describing the pre-edit world, never a match promising stages the
    shooters no longer have."""
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    order: list[str] = []
    projects, _saved, _audits, _cancelled, hooks = _harness(tmp_path, ["a", "b"], [1, 2, 3])
    hooks["save_project"] = lambda slug, project: order.append(f"project:{slug}")

    def save_match() -> None:
        order.append("match")

    asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["a", "b"],
            save_match=save_match,
            **hooks,
        )
    )

    assert order == ["project:a", "project:b", "match"]


def test_apply_collects_a_failing_shooter_and_still_commits(tmp_path) -> None:
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    projects, _saved, _audits, _cancelled, hooks = _harness(tmp_path, ["ok", "bad"], [1, 2, 3])

    def delete_audit(slug: str, n: int) -> bool:
        if slug == "bad":
            raise RuntimeError("state store down")
        return True

    hooks["delete_audit"] = delete_audit

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["ok", "bad"],
            **hooks,
        )
    )

    assert len(summary.errors) == 1
    assert "state store down" in summary.errors[0]
    assert [s.stage_number for s in match.stages] == [1, 2]
```

Note the `save_match` hook appears in one test -- include it in the signature as a required keyword so all call sites pass it; update the `_harness` hooks dict to supply a default no-op `save_match` for the other tests, or pass it explicitly in each. Pick one and be consistent.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stage_edit.py -k apply -v`
Expected: FAIL, `ImportError: cannot import name 'apply_stage_edit'`

- [ ] **Step 3: Write the implementation**

Add to `stage_edit.py`:

```python
class ShooterStageEditResult(BaseModel):
    """What the edit did to one shooter."""

    slug: str
    videos_unassigned: int = 0
    audit_docs_deleted: int = 0
    files_deleted: int = 0
    objects_deleted: int = 0


class StageEditSummary(BaseModel):
    """Outcome of a stage-list edit, modelled on ``DeletionSummary``.

    Failures are collected rather than raised: the stage list committing
    matters more than one uncooperative cache object, and the user needs
    to see what did not get cleaned up.
    """

    removed: list[int] = Field(default_factory=list)
    added: list[int] = Field(default_factory=list)
    renamed: list[int] = Field(default_factory=list)
    jobs_cancelled: int = 0
    shooters: list[ShooterStageEditResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


async def apply_stage_edit(
    *,
    match: Any,
    root: Path,
    submitted: list[SubmittedStage],
    shooter_slugs: list[str],
    load_project: Any,
    save_project: Any,
    save_match: Any,
    delete_audit: Any,
    cancel_jobs: Any,
) -> StageEditSummary:
    """Apply a stage-list edit to the match and every shooter in it.

    Ordering is deliberate and mirrors ``match_delete._delete_hosted``:

    1. Cancel jobs targeting removed stages, so no worker rewrites
       artifacts under the purge. Jobs carry no shooter slug, and removal
       is match-wide, so filtering on ``stage_number`` is exactly right.
    2. Per shooter: release videos, delete the audit doc, purge caches,
       apply adds and renames, save.
    3. Save the match doc **last**. A crash mid-fan-out then leaves the
       canonical list describing the pre-edit world rather than promising
       stages the shooters no longer have.

    Cancellation is not instantaneous, so a worker can still land a write
    after step 2's purge. Because freed numbers are never reused, that
    write is inert garbage that can never be read as a live stage.
    """
    diff = diff_stage_list(list(match.stages), submitted)
    summary = StageEditSummary(
        removed=list(diff.removed),
        added=[s.stage_number for s in diff.added],
        renamed=[s.stage_number for s in diff.renamed],
    )

    if diff.removed:
        try:
            summary.jobs_cancelled = await cancel_jobs(set(diff.removed))
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"cancel jobs: {exc}")

    renamed_by_number = {s.stage_number: s for s in diff.renamed}
    removed = set(diff.removed)

    for slug in shooter_slugs:
        result = ShooterStageEditResult(slug=slug)
        try:
            project = load_project(slug)

            for stage_number in diff.removed:
                result.videos_unassigned += project.unassign_stage_videos(stage_number)
                if delete_audit(slug, stage_number):
                    result.audit_docs_deleted += 1
                counts = purge_stage_artifacts(project, root, stage_number)
                result.files_deleted += counts.files_deleted
                result.objects_deleted += counts.objects_deleted
                summary.errors.extend(f"{slug}: {e}" for e in counts.errors)

            project.stages = [s for s in project.stages if s.stage_number not in removed]
            for stage in project.stages:
                update = renamed_by_number.get(stage.stage_number)
                if update is not None:
                    stage.stage_name = update.stage_name
                    stage.stage_rounds = update.stage_rounds
            for definition in diff.added:
                project.stages.append(
                    StageEntry(
                        stage_number=definition.stage_number,
                        stage_name=definition.stage_name,
                        time_seconds=0.0,
                        stage_rounds=definition.stage_rounds,
                        placeholder=True,
                    )
                )
            project.stages.sort(key=lambda s: s.stage_number)
            save_project(slug, project)
        except Exception as exc:  # noqa: BLE001 -- one shooter must not
            # strand the others or the match doc.
            summary.errors.append(f"{slug}: {exc}")
        summary.shooters.append(result)

    match.stages = [s for s in match.stages if s.stage_number not in removed]
    for definition in match.stages:
        update = renamed_by_number.get(definition.stage_number)
        if update is not None:
            definition.stage_name = update.stage_name
            definition.stage_rounds = update.stage_rounds
    match.stages.extend(diff.added)
    match.stages.sort(key=lambda s: s.stage_number)
    save_match()

    return summary
```

Extend the imports at the top of `stage_edit.py`:

```python
from typing import Any

from splitsmith.ui.project import MatchProject, StageEntry, StageRounds
```

Stored order is normalised ascending by `stage_number`, which is the invariant everywhere else in the codebase. Reordering later means dropping that `sort` deliberately, alongside the identity change it needs anyway.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stage_edit.py -v`
Expected: PASS, all tests

- [ ] **Step 5: Format and commit**

```bash
uv run black src/splitsmith/ui/stage_edit.py tests/test_stage_edit.py
uv run pytest tests/test_stage_edit.py -q
git add src/splitsmith/ui/stage_edit.py tests/test_stage_edit.py
git commit -m "feat(ui): apply a stage-list edit across every shooter (#521)"
```

---

### Task 6: `PUT /api/match/stages`

**Files:**
- Modify: `src/splitsmith/ui/server.py` (models near `CreateMatchStageDraft` at line 3138; route near `create_placeholder_stages` at line 7472)
- Test: `tests/test_ui_server_stage_edit.py`

**Interfaces:**
- Consumes: `apply_stage_edit`, `SubmittedStage`, `StageEditSummary`, `StageEditError` (Tasks 1 and 5); `AppState.delete_audit` (Task 4).
- Produces: `PUT /api/match/stages` accepting `{"stages": [{"stage_number": int | null, "stage_name": str, "stage_rounds": {...} | null}]}` and returning a `StageEditSummary` JSON body. 400 on `StageEditError`, 409 on `StateConflictError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui_server_stage_edit.py`. Use `_MatchClient` from `tests/test_ui_server.py` the same way other endpoint tests do, and seed a match with several shooters.

```python
def test_rename_preserves_the_audit_doc(tmp_path: Path) -> None:
    """The discriminating assertion is the artifact, not the status code:
    a rename returns 200 both before and after this change exists."""
    client, state = _seeded_match(tmp_path, stages=3, shooters=["me"])
    audit_file = state._audit_file("me", 2)
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.write_text(json.dumps({"shots": [1.0, 2.0]}), encoding="utf-8")

    resp = client.put(
        "/api/match/stages",
        json={
            "stages": [
                {"stage_number": 1, "stage_name": "Stage 1"},
                {"stage_number": 2, "stage_name": "El Presidente"},
                {"stage_number": 3, "stage_name": "Stage 3"},
            ]
        },
    )

    assert resp.status_code == 200
    assert resp.json()["renamed"] == [2]
    assert json.loads(audit_file.read_text())["shots"] == [1.0, 2.0]


def test_removing_a_stage_keeps_later_stages_audits_byte_identical(tmp_path: Path) -> None:
    client, state = _seeded_match(tmp_path, stages=5, shooters=["me"])
    for n in (3, 4, 5):
        f = state._audit_file("me", n)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"stage": n}), encoding="utf-8")
    before_4 = state._audit_file("me", 4).read_bytes()
    before_5 = state._audit_file("me", 5).read_bytes()

    resp = client.put(
        "/api/match/stages",
        json={
            "stages": [
                {"stage_number": n, "stage_name": f"Stage {n}"}
                for n in (1, 2, 4, 5)
            ]
        },
    )

    assert resp.status_code == 200
    assert resp.json()["removed"] == [3]
    assert not state._audit_file("me", 3).exists()
    assert state._audit_file("me", 4).read_bytes() == before_4
    assert state._audit_file("me", 5).read_bytes() == before_5


def test_add_after_remove_allocates_six_not_the_freed_three(tmp_path: Path) -> None:
    client, _state = _seeded_match(tmp_path, stages=5, shooters=["me"])
    client.put(
        "/api/match/stages",
        json={
            "stages": [
                {"stage_number": n, "stage_name": f"Stage {n}"} for n in (1, 2, 4, 5)
            ]
        },
    )

    resp = client.put(
        "/api/match/stages",
        json={
            "stages": [
                {"stage_number": n, "stage_name": f"Stage {n}"} for n in (1, 2, 4, 5)
            ]
            + [{"stage_number": None, "stage_name": "Standards"}]
        },
    )

    assert resp.status_code == 200
    assert resp.json()["added"] == [6]


def test_removing_every_stage_is_rejected(tmp_path: Path) -> None:
    client, _state = _seeded_match(tmp_path, stages=2, shooters=["me"])
    resp = client.put("/api/match/stages", json={"stages": []})
    assert resp.status_code == 400
    assert "at least one stage" in json.dumps(resp.json())


def test_unknown_stage_number_is_a_400(tmp_path: Path) -> None:
    client, _state = _seeded_match(tmp_path, stages=2, shooters=["me"])
    resp = client.put(
        "/api/match/stages",
        json={
            "stages": [
                {"stage_number": 1, "stage_name": "Stage 1"},
                {"stage_number": 2, "stage_name": "Stage 2"},
                {"stage_number": 99, "stage_name": "Ghost"},
            ]
        },
    )
    assert resp.status_code == 400


def test_removal_fans_out_to_every_shooter(tmp_path: Path) -> None:
    client, _state = _seeded_match(tmp_path, stages=3, shooters=["anna", "erik"])
    resp = client.put(
        "/api/match/stages",
        json={
            "stages": [
                {"stage_number": 1, "stage_name": "Stage 1"},
                {"stage_number": 2, "stage_name": "Stage 2"},
            ]
        },
    )

    assert resp.status_code == 200
    assert sorted(s["slug"] for s in resp.json()["shooters"]) == ["anna", "erik"]
    for slug in ("anna", "erik"):
        project = client.get(f"/api/shooters/{slug}/project").json()
        assert [s["stage_number"] for s in project["stages"]] == [1, 2]
```

Write `_seeded_match(tmp_path, *, stages, shooters)` as a local helper in this module. Build it out of whatever `tests/test_ui_server.py` already uses to create a match with a roster -- read that module and reuse its helper rather than writing a new scaffold. Returns `(client, state)` where `client` is a `_MatchClient` bound to the created match. Confirm the project-fetch route used in the last test actually exists:

```bash
grep -n '"/api/shooters/{slug}/project"' src/splitsmith/ui/server.py
```

Use whatever route the SPA uses to read a shooter's project; do not invent one.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui_server_stage_edit.py -v`
Expected: FAIL, 404 or 405 -- no `PUT /api/match/stages` route exists

- [ ] **Step 3: Write the implementation**

Add the request model near `CreateMatchStageDraft`:

```python
class StageEditRequest(BaseModel):
    """Body for ``PUT /api/match/stages`` (#521).

    The full desired stage list, not a patch. The server diffs it against
    the match's current list, so the SPA does not have to track which rows
    it changed. ``stage_number: null`` marks a newly added row.
    """

    stages: list[stage_edit.SubmittedStage]
```

Add the route near `create_placeholder_stages`:

```python
    @app.put("/api/match/stages", response_model=stage_edit.StageEditSummary)
    async def edit_match_stages(req: StageEditRequest) -> stage_edit.StageEditSummary:
        """Add, remove, and rename stages on an existing match (#521).

        The stage list is a property of the match, so an edit fans out to
        every shooter -- their stage lists have to stay aligned. Removing
        a stage releases its videos to ``unassigned_videos`` and deletes
        its audit doc and derived caches; stages the user did not touch
        keep everything, which is the whole point of never renumbering.
        """
        root = current_match_root.get()
        if root is None:
            raise _no_project_error()
        match = state.match()
        slugs = [s.slug for s in match.shooters]

        async def _cancel(stage_numbers: set[int]) -> int:
            cancelled = 0
            for job in await state.jobs.list():
                if job.stage_number not in stage_numbers:
                    continue
                if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
                    continue
                if await state.jobs.cancel(job.id) is not None:
                    cancelled += 1
            return cancelled

        def _save_project(slug: str, project: MatchProject) -> None:
            project.save(state.shooter_root(slug))

        try:
            return await stage_edit.apply_stage_edit(
                match=match,
                root=root,
                submitted=req.stages,
                shooter_slugs=slugs,
                load_project=state.shooter_project,
                save_project=_save_project,
                save_match=lambda: match.save(root),
                delete_audit=state.delete_audit,
                cancel_jobs=_cancel,
            )
        except stage_edit.StageEditError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except StateConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "stale_match", "message": str(exc)},
            ) from exc
```

Add `stage_edit` to the local imports at the top of `server.py`, alongside `match_model` and `match_delete`. Confirm the roster accessor before using `match.shooters`:

```bash
grep -n "class Shooter(BaseModel)" -A 20 src/splitsmith/match_model.py | grep -n "slug"
grep -n "shooters:" src/splitsmith/match_model.py
```

Also confirm `StateConflictError` is already imported in `server.py`; if not, import it from wherever `project_state` raises it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ui_server_stage_edit.py -v`
Expected: PASS, all tests

- [ ] **Step 5: Prove the audit-preservation test discriminates**

```bash
git stash push src/splitsmith/ui/stage_edit.py
uv run pytest tests/test_ui_server_stage_edit.py -k byte_identical -v
git stash pop
```

Expected: FAIL while stashed. If it passes without the engine, the test is not testing what it claims -- fix the test before continuing.

- [ ] **Step 6: Format and commit**

```bash
uv run black src/splitsmith/ui/server.py tests/test_ui_server_stage_edit.py
uv run pytest tests/test_stage_edit.py tests/test_ui_server_stage_edit.py -q
git add src/splitsmith/ui/server.py tests/test_ui_server_stage_edit.py
git commit -m "feat(ui): PUT /api/match/stages endpoint (#521)"
```

---

### Task 7: Job cancellation is targeted

**Files:**
- Test: `tests/test_ui_server_stage_edit.py`

**Interfaces:**
- Consumes: the endpoint from Task 6.

This task is test-only. It exists because "cancel only the right jobs" is the requirement most likely to regress into `cancel_active_for_user`, and no earlier test proves the negative.

- [ ] **Step 1: Write the failing test**

```python
def test_removal_cancels_only_jobs_for_the_removed_stage(tmp_path: Path) -> None:
    """cancel_active_for_user would kill the stage-1 job too. It must not."""
    client, state = _seeded_match(tmp_path, stages=3, shooters=["me"])

    doomed = _submit_stalled_job(state, stage_number=3)
    bystander = _submit_stalled_job(state, stage_number=1)

    resp = client.put(
        "/api/match/stages",
        json={
            "stages": [
                {"stage_number": 1, "stage_name": "Stage 1"},
                {"stage_number": 2, "stage_name": "Stage 2"},
            ]
        },
    )

    assert resp.status_code == 200
    assert resp.json()["jobs_cancelled"] == 1

    jobs = {j["id"]: j for j in client.get("/api/me/jobs").json()}
    assert jobs[doomed]["status"] == "cancelled"
    assert jobs[bystander]["status"] in ("pending", "running")
```

Write `_submit_stalled_job(state, *, stage_number)` as a local helper that submits a job which blocks until cancelled, and returns its id. Read `src/splitsmith/ui/jobs.py` around `submit` for the real signature, and check how existing tests in `tests/test_ui_server.py` submit jobs -- reuse their approach.

The jobs-list route is **`/api/me/jobs`** and it returns a bare list. `/api/jobs` is rewritten by `_MatchClient` but matches no route and 404s with `{"detail": "api route not found"}`.

CI runners start with no cached ML models, so the app queues a `model_download` job that never appears locally. Assert with membership (`jobs[doomed]`), never equality on the whole list.

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_ui_server_stage_edit.py -k only_jobs -v`
Expected: PASS -- Task 6 already implemented targeted cancellation, so this
test documents the behaviour rather than driving it. A green run here proves
nothing on its own; step 3 is what gives the test its value.

- [ ] **Step 3: Prove it discriminates**

Temporarily replace the `_cancel` closure body in `server.py` with `return await state.jobs.cancel_active_for_user()` and re-run:

Run: `uv run pytest tests/test_ui_server_stage_edit.py -k only_jobs -v`
Expected: FAIL -- the bystander is cancelled. Restore the targeted version and re-run to green.

- [ ] **Step 4: Format and commit**

```bash
uv run black tests/test_ui_server_stage_edit.py
uv run pytest tests/test_ui_server_stage_edit.py -q
git add tests/test_ui_server_stage_edit.py
git commit -m "test(ui): stage removal cancels only its own jobs (#521)"
```

---

### Task 8: API client

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (add near `createPlaceholderStages` at line 2011; delete that function)

**Interfaces:**
- Produces:
  - `export interface StageEditRow { stage_number: number | null; stage_name: string; stage_rounds?: StageRounds | null }`
  - `export interface ShooterStageEditResult { slug: string; videos_unassigned: number; audit_docs_deleted: number; files_deleted: number; objects_deleted: number }`
  - `export interface StageEditSummary { removed: number[]; added: number[]; renamed: number[]; jobs_cancelled: number; shooters: ShooterStageEditResult[]; errors: string[] }`
  - `api.editMatchStages(stages: StageEditRow[]): Promise<StageEditSummary>`

- [ ] **Step 1: Add the types and the client method**

```ts
export interface StageEditRow {
  /** null marks a row the user added; the server allocates the number. */
  stage_number: number | null;
  stage_name: string;
  stage_rounds?: StageRounds | null;
}

export interface ShooterStageEditResult {
  slug: string;
  videos_unassigned: number;
  audit_docs_deleted: number;
  files_deleted: number;
  objects_deleted: number;
}

export interface StageEditSummary {
  removed: number[];
  added: number[];
  renamed: number[];
  jobs_cancelled: number;
  shooters: ShooterStageEditResult[];
  errors: string[];
}
```

```ts
  /** Add, remove, and rename stages on the bound match (#521). Send the
   *  full desired list; the server diffs it. Removing a stage releases its
   *  videos to unassigned and deletes its audit + derived caches on every
   *  shooter. Stage numbers are never reused, so removing one leaves a gap. */
  editMatchStages: (stages: StageEditRow[]) =>
    request<StageEditSummary>("/api/match/stages", {
      method: "PUT",
      json: { stages },
    }),
```

Check that `StageRounds` is already exported from `api.ts`; if it is named differently there, use the existing name.

- [ ] **Step 2: Delete the dead `createPlaceholderStages`**

Remove the function at `api.ts:2011-2018` and its `PlaceholderStagesRequest` type if nothing else references it. Verify first:

```bash
grep -rn "createPlaceholderStages\|PlaceholderStagesRequest" src/splitsmith/ui_static/src
```

Expected: only the definitions. If anything imports it, stop and report -- do not delete a live caller.

Leave the backend `POST /api/shooters/{slug}/project/placeholder-stages` endpoint alone. It is still exercised by `tests/test_ui_server.py` and is a legitimate bootstrap path; only the unused SPA client is going.

- [ ] **Step 3: Typecheck and lint**

```bash
cd src/splitsmith/ui_static
corepack pnpm install --frozen-lockfile
corepack pnpm typecheck
corepack pnpm lint
```

Expected: both clean.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts
git commit -m "feat(ui): editMatchStages client, drop dead createPlaceholderStages (#521)"
```

---

### Task 9: `EditStagesDrawer`

**Files:**
- Create: `src/splitsmith/ui_static/src/components/match/EditStagesDrawer.tsx`

**Interfaces:**
- Consumes: `api.editMatchStages`, `StageEditRow`, `StageEditSummary` (Task 8).
- Produces: `export function EditStagesDrawer(props: { open: boolean; onClose: () => void; stages: StageEntry[]; shooterCount: number; onSaved: (summary: StageEditSummary) => void }): JSX.Element | null`

Read `src/splitsmith/ui_static/src/pages/CreateMatch.tsx:1188-1270` first and reuse its row markup, field components, and class names. Do not invent new visual primitives; this must look like the existing manual-create stage editor.

Behaviour:

- Rows render in the order given, showing `stage_number` as muted secondary identity, never as the row's ordinal. A gapped list (1, 2, 4, 5) must render its real numbers.
- New rows show no number, since the server allocates it.
- A row marked for removal stays visible and struck through until Save; the button toggles.
- Save is disabled when every row is marked for removal, and when any name is blank.
- When at least one row is marked for removal, Save opens a confirm naming the stages and what is lost, multiplied across `shooterCount`. Copy: `Removing stage 3 deletes its audit and trims for all 3 shooters. Its videos move to unassigned so you can re-attach them. Stage numbers are not reused -- the next stage you add will be 7.`
- On success call `onSaved(summary)`. If `summary.errors` is non-empty, surface them; the edit still committed, so do not present it as a failure.

- [ ] **Step 1: Build the component**

Follow the conventions of a neighbouring drawer/dialog. Find one to copy the open/close and focus handling from:

```bash
ls src/splitsmith/ui_static/src/components/match/
grep -rln "role=\"dialog\"\|<Drawer\|Dialog" src/splitsmith/ui_static/src/components | head
```

- [ ] **Step 2: Typecheck and lint**

```bash
cd src/splitsmith/ui_static
corepack pnpm typecheck
corepack pnpm lint
```

Expected: both clean.

- [ ] **Step 3: Commit**

```bash
git add src/splitsmith/ui_static/src/components/match/EditStagesDrawer.tsx
git commit -m "feat(ui): EditStagesDrawer component (#521)"
```

---

### Task 10: Wire the entry points

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Home.tsx`

**Interfaces:**
- Consumes: `EditStagesDrawer` (Task 9).

`Home.tsx` is the match overview and renders two variants (see its header comment at lines 5-13): **active** when at least one stage has a primary video, **empty** otherwise. Both need the entry point -- PR #520 deleted the "Edit stage list" button from one and the "Adjust the stage list" help card from the other.

The Shooters page is deliberately not an entry point: stage lists are match-level, and offering the edit from a per-shooter screen implies a per-shooter stage list that does not exist.

- [ ] **Step 1: Add the drawer and both triggers**

Hold `open` state in `Home.tsx`, render `<EditStagesDrawer>` once, and trigger it from both variants. Pass `shooterCount` from the roster the page already loads (`shooters`). On `onSaved`, refresh the project/roster query the page already uses so the stage grid re-renders -- find how the page currently invalidates after a mutation and use the same mechanism.

- [ ] **Step 2: Typecheck and lint**

```bash
cd src/splitsmith/ui_static
corepack pnpm typecheck
corepack pnpm lint
corepack pnpm build
```

Expected: all clean.

- [ ] **Step 3: Verify in a real browser**

A green typecheck is not evidence the user sees anything -- on #617 a fix reached the table cell and rich ellipsized it away while the assertion passed. Run the app and drive it:

1. Start the server and open the SPA.
2. Create a manual match with 5 stages and 2 shooters.
3. Rename stage 2. Confirm the new name appears in the stage grid and the sidebar.
4. Remove stage 3. Confirm the confirm dialog names the right shooters, and that after saving the grid shows 1, 2, 4, 5 -- with those numbers, not renumbered.
5. Add a stage. Confirm it appears as 6, not 3.
6. Screenshot the gapped grid.

This box has no ffmpeg; see the frontend e2e notes for the `imageio-ffmpeg` workaround if any step needs it.

- [ ] **Step 4: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/Home.tsx
git commit -m "feat(ui): stage-editor entry points on the match overview (#521)"
```

---

### Task 11: Contiguity sweep

**Files:**
- Modify: whatever the sweep finds.
- Test: `tests/test_stage_edit.py` or the owning module's test file.

Gaps are new -- until now stage numbers were always 1..N. The spec's survey found nothing that assumes contiguity (`Pick.tsx`'s `TickStrip` renders anonymous done/todo ticks from a count; the `idx + 1` sites in `server.py` are progress-message cosmetics), but a survey is not a proof. This task is the proof.

- [ ] **Step 1: Search for count-derived stage lists**

```bash
grep -rn "range(1" src/splitsmith --include=*.py | grep -i stage
grep -rn "stage_count" src/splitsmith --include=*.py
grep -rn "stage_count\|stages.length" src/splitsmith/ui_static/src
grep -rn "Array.from" src/splitsmith/ui_static/src | grep -i stage
```

For each hit, decide: does it build a list of stage *numbers* from a count, or does it iterate the actual list? Only the former is a bug. Record each hit and the verdict.

- [ ] **Step 2: Test the export path against a gapped list**

The export and FCPXML paths are the highest-risk consumers. Write a test that runs a match export over a gapped stage list (1, 2, 4, 5) and asserts the output covers exactly those four stages:

```python
def test_export_over_a_gapped_stage_list_covers_every_remaining_stage(tmp_path) -> None:
    ...
```

Build it on `_seed_match_export_project` from `tests/test_ui_server.py` (mock `splitsmith.trim.trim_video` to just write bytes -- this box has no ffmpeg). Note that helper gives every primary `beep_time=5.0`, and the per-stage export endpoint gates on stage time and source reachability, so the seed needs `POST /stages/{n}/time` or it 400s as a placeholder.

- [ ] **Step 3: Fix anything the sweep finds**

For each real bug, write the failing test first, then fix. If the sweep finds nothing, say so explicitly in the commit message rather than committing an empty change.

- [ ] **Step 4: Run the full suite**

```bash
uv run black src tests
uv run pytest -q
```

Expected: green, except the two known flakes -- `test_calibrated_camera_models_endpoint_lists_shipped_models` (`OSError` reaching huggingface.co) and `test_ui_embedded.py::test_sigterm_to_main_exits_clean` (`subprocess.TimeoutExpired`). Both are unrelated to this change; confirm the rest is green.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test(ui): prove the export path handles a gapped stage list (#521)"
```

---

### Task 12: Documentation and PR

**Files:**
- Modify: `SPEC.md`, `CLAUDE.md` if the pipeline description needs it.

- [ ] **Step 1: Document the stage-editor semantics in SPEC.md**

Find the section describing match/stage structure and add: stage lists are editable after creation via `PUT /api/match/stages`; `stage_number` is stable for a stage's lifetime; removal leaves a gap and freed numbers are never reused; removal releases videos to unassigned and deletes the audit doc plus derived caches on every shooter.

- [ ] **Step 2: Open the PR**

The PR title becomes the squash commit and feeds release-please, so it must be a valid conventional-commit subject.

```bash
uv run pytest -q
git push -u origin feat/stage-list-editor
gh pr create --title "feat(ui): stage-list editor in the SPA (#521)" --body "$(cat <<'EOF'
Closes #521.

Add, remove, and rename stages on an existing match from the SPA. Until
now stage editing was CLI-only, which made a wrong stage list
unrecoverable for a hosted user.

`stage_number` is stable for a stage's lifetime: removal leaves a gap and
freed numbers are never reused, so every per-stage artifact key stays
valid for the stages the user did not touch. That is what lets this ship
without an artifact-migration engine. The design doc records the
renumber-and-reorder work this defers and what it will need.

Removal releases the stage's videos to `unassigned_videos` -- uploaded
footage may be the user's only copy -- and deletes its audit doc and
derived caches across every shooter.

Design: `docs/superpowers/specs/2026-08-03-stage-list-editor-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_013tLzY2DNwiR7AVGVoec5n2
EOF
)"
```

- [ ] **Step 3: Watch CI**

Expect the two known flakes. On a failure that is only those, `gh run rerun <id> --failed`. Do not merge on a red run without reading the failure.

---

## Self-Review Notes

Spec coverage check, section by section:

| Spec section | Task |
| --- | --- |
| What already exists / not reusable | Task 8 step 2 (delete the dead client, keep the endpoint) |
| Data model | Tasks 1, 5 |
| Stage identity, gaps, no reuse | Task 1 (allocation), Task 6 (endpoint test) |
| Forward-compat notes, no permanent-identity docstring | Global Constraints |
| Scope: rename all, remove any | Tasks 1, 5, 6 |
| Removal semantics: videos kept, derived purged | Tasks 2, 3, 5 |
| Purge glob underscore guard | Task 3 steps 1 and 5 |
| API shape, 400/409 | Task 6 |
| Execution order, match doc last | Task 5 |
| Targeted job cancellation | Tasks 5, 7 |
| SPA drawer, entry points, Shooters excluded | Tasks 9, 10 |
| Contiguity audit | Task 11 |
| Testing requirements | Tasks 1-7, 10 step 3, 11 |
