# Footage sort reference panel: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On Sort footage, open a panel beside a clip that shows every stage as a frame from a shooter who already has footage on it, plays any of them, marks the stages the assigned shooter still lacks, and sets the clip's stage in one click.

**Architecture:** The server's `SortView` grows a `references` list (one per shooter and stage with reference footage, from the shooter's project or from this sort's checked clips) and serves a cached frame per project reference, built by a job. The SPA derives tiles and "has it" in `lib/footageSort.ts` and renders them in a `ReferencePanel` mounted by `AssignSheet`.

**Tech Stack:** FastAPI + Pydantic (local-only router), ffmpeg through `thumbnail._publish`, React + TypeScript, vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-10-21-footage-sort-reference-panel-design.md`

## Global Constraints

- Local only: every new route calls `_local_only()`; nothing hosted.
- A request never runs ffmpeg: frames come from the scan pass or the `footage_sort_frames` job.
- Copy: ASCII, no dash punctuation, `Label` for tracked caps, `numeral` for stage ordinals, no coloured fills, one primary per view (the sheet's Use this).
- Types: after any model change run `uv run python scripts/export_openapi.py` then `pnpm gen:api` in `src/splitsmith/ui_static`; aliases in `lib/api.ts`, no hand-written twins.
- Tests must fail without the change they cover.
- Python: black 110, ruff; TS: `pnpm typecheck`, `pnpm eslint`.
- Git in a worktree: use `/usr/bin/git`.

## Review Focus

1. A project primary whose source file is unreachable and has no trim: the tile must say "no clip", never a broken player (Task 2 test `test_unreachable_raw_file_is_no_reference`).
2. A legacy-named audit trim (pre take-spec id): must still count as `"trim"` (Task 1 keeps `resolve_trim_for_read` in the path; Task 2 test `test_trim_resolution_uses_the_read_resolver`).
3. A trim shorter than beep + 2 s (ffmpeg seeks past the end and emits nothing): the frame must still be made (Task 2 test `test_extract_frame_falls_back_to_the_start`).
4. The clip being assigned must never count as "has it" for its own shooter (Task 4 test `excludes the clip being assigned`).
5. Reopening a scan whose project references have no frames must queue the build once, not on every poll (Task 3 test `test_a_poll_queues_one_frames_job`).

---

### Task 0: Branch

- [ ] `/usr/bin/git checkout -b feat/footage-sort-reference-panel` on main; commit the uncommitted stage-name work first as `feat(ui): name the stage in Sort footage's stage list` (it is the base this builds on).
- [ ] Commit the spec and this plan: `docs: footage sort reference panel design and plan`.

### Task 1: One trim resolver for reads

**Files:**
- Modify: `src/splitsmith/ui/audio.py` (after `resolve_trim_for_read`)
- Modify: `src/splitsmith/export_preview.py:344-360` (`_trim_for` becomes a call)
- Test: `tests/test_footage_sort_reference.py` (new, also used by Task 2)

**Produces:** `stage_trim_for_read(project: MatchProject, root: Path, stage_number: int) -> tuple[Path | None, float]`: the export trim `exports/<stage_file_base>_trimmed.mp4` when present, else `resolve_trim_for_read(root, n, primary, project=project)`, with the clip-local beep `min(project.trim_pre_buffer_seconds, primary.beep_time)`; `(None, 0.0)` with no primary or no beep. Exactly `_trim_for`'s body today.

- [ ] Test `test_stage_trim_for_read_prefers_the_export_trim`: a tmp project with one stage, a primary with `beep_time=7.0`, `trim_pre_buffer_seconds=5.0`, an empty file at the export trim path -> `(that path, 5.0)`; without it and with the audit trim at `trimmed_video_path(...)` -> `(audit path, 5.0)`; with neither -> `(None, 5.0)`; with no beep -> `(None, 0.0)`. Pin all four.
- [ ] Run: `uv run pytest -n0 tests/test_footage_sort_reference.py -k stage_trim -v`. Expected: ImportError.
- [ ] Move `_trim_for`'s body into `ui/audio.stage_trim_for_read` and make `_trim_for` return it (keep `_trim_for` for its callers and monkeypatches).
- [ ] Run the test and `uv run pytest -n0 tests/test_export_preview*.py -q`. Expected: pass.
- [ ] Commit: `refactor(ui): one trim resolver for reads`.

### Task 2: Reference resolution and frames (pure module)

**Files:**
- Create: `src/splitsmith/ui/footage_sort_reference.py`
- Test: `tests/test_footage_sort_reference.py`

**Interfaces, produces:**

```python
class ProjectReference(BaseModel):
    shooter: str; stage: int; video_path: str; video_id: str
    stream_kind: Literal["trim", "auto"]; source_file: Path; frame_at: float

def project_reference(project: MatchProject, root: Path, slug: str, stage_number: int) -> ProjectReference | None
def frame_path(cache_dir: Path, ref: ProjectReference) -> Path
def cached_frame(cache_dir: Path, ref: ProjectReference) -> Path | None
def ensure_frame(cache_dir: Path, ref: ProjectReference, *, ffmpeg_binary: str = "ffmpeg") -> Path
def extract_frame(source: Path, dest: Path, *, at_seconds: float, ffmpeg_binary: str = "ffmpeg") -> None
```

- `project_reference`: the stage's `primary()`; `(trim, beep) = stage_trim_for_read(...)`; with a trim: `stream_kind="trim"`, `source_file=trim`, `frame_at = beep + 2.0` (beep is 0.0 with no beep, giving 2.0); without: the raw file must `is_file()`, else `None`; `stream_kind="auto"`, `source_file=raw`, `frame_at=1.0`. Missing stage (`KeyError`) or no primary -> `None`.
- `frame_path`: `cache_dir / f"{slug}-stage{N:02d}-{video_id}-{source_file.stat().st_mtime_ns}.jpg"`.
- `extract_frame`: `thumbnail._publish(dest, cmd, timeout=10.0, what=...)` with `-ss at_seconds`, `-frames:v 1`, `scale=480:-2`, `-q:v 4`; on `ThumbnailError` retry once at `0.5` s (the fallback for a short clip); the second failure raises.
- `ensure_frame`: `cached_frame` or extract into `frame_path`.

- [ ] Tests (tmp projects built with `MatchProject.init` + `StageEntry` + `StageVideo`, as `tests/test_footage_sort_api.py::_match_app` does):
  - `test_project_reference_with_a_trim_is_trim_kind`: audit trim file present, beep 7.0, pre-buffer 5.0 -> `stream_kind == "trim"`, `frame_at == 7.0`, `source_file` is the trim.
  - `test_project_reference_without_a_trim_plays_the_raw_file`: raw file exists -> `"auto"`, `frame_at == 1.0`.
  - `test_unreachable_raw_file_is_no_reference`: raw path missing, no trim -> `None`.
  - `test_trim_resolution_uses_the_read_resolver`: monkeypatch `ui.audio.resolve_trim_for_read` to return a legacy path -> that path is `source_file`.
  - `test_frame_path_changes_with_the_source_mtime`: touch the trim with `os.utime` -> different name.
  - `test_extract_frame_falls_back_to_the_start` (`@pytest.mark.integration`, uses the `source_clip` fixture): `at_seconds=10_000` on a short clip -> the jpg exists.
- [ ] Run: `uv run pytest -n0 tests/test_footage_sort_reference.py -v`. Expected: fail on import.
- [ ] Implement the module.
- [ ] Run again (with `~/.claude-tmp/with-project-ffmpeg.sh` for the integration test). Expected: pass.
- [ ] Commit: `feat(ui): resolve a stage's reference footage and its frame`.

### Task 3: SortView references, the frame route and the job

**Files:**
- Modify: `src/splitsmith/ui/footage_sort_api.py` (models near `ShooterRef`; `_shooters`; `_view`; `run_footage_sort_scan` between thumbnails and strips; new route after `get_strip`; `get_scan` becomes async)
- Modify: `src/splitsmith/ui/server.py:3930-3934` (register `footage_sort_api.FRAMES_JOB_KIND`)
- Test: `tests/test_footage_sort_api.py`
- Regenerate: `src/splitsmith/ui_static/openapi.json`, `src/splitsmith/ui_static/src/lib/api/types.gen.ts`

**Produces:**

```python
class ShooterRef(BaseModel):
    key: str; name: str; stages: list[int]
    stages_with_footage: list[int]   # stages whose StageEntry has any video

class SortReference(BaseModel):
    shooter: str; stage: int
    source: Literal["project", "sort"]
    video_path: str | None; video_id: str | None   # project
    clip_id: str | None                             # sort
    stream_kind: Literal["trim", "auto"]            # sort is always "auto"
    frame: bool                                     # project: cached frame exists; sort: the clip's thumbnail exists

class SortView: ... references: list[SortReference]; frames_pending: int
FRAMES_JOB_KIND = "footage_sort_frames"
REFERENCE_DIR = "reference"   # under _sort_dir(state)
def run_footage_sort_frames(handle, *, state, scan_id) -> None
GET /api/match/footage-sort/{scan_id}/reference-frames/{slug}/{stage}.jpg -> FileResponse | 404
```

- `_view` builds references: for every shooter and every match stage, `project_reference(...)` first (source `project`, `frame = cached_frame(...) is not None`); else the checked, not-imported clip whose proposal is (shooter, stage), the `role == "primary"` one first (source `sort`, `frame = clip.thumbnail`). `frames_pending` = project references with `frame=False`.
- `get_scan` (async): after `_view` via `run_in_threadpool`, if `frames_pending > 0` and `await state.jobs.find_active(kind=FRAMES_JOB_KIND)` is `None`, `await state.jobs.submit(kind=FRAMES_JOB_KIND, args={"scan_id": scan_id})`.
- `run_footage_sort_frames`: for each project reference of the scan without a frame, `ensure_frame`; a failure is logged and skipped (never fails the job).
- `run_footage_sort_scan`: the same loop after the thumbnails, before the strips (call one shared `_build_frames(state, record, handle)`).
- The route resolves `project_reference` for (slug, stage) and serves `cached_frame`, else 404.

- [ ] Tests, each on `_match_app` + `_shared_folder`:
  - `test_view_lists_sort_references_for_checked_clips`: after the scan, `references` has `("alice", 1, "sort", clip_id IMG_0001)` and `("bob", 1, "sort", IMG_0002)`, `frame` true; uncheck alice's clip through decisions -> alice's reference is gone.
  - `test_an_import_turns_the_reference_into_a_project_one`: import alice -> `("alice", 1, "project", stream_kind "auto")`, `stages_with_footage == [1]`; the frame route 404s immediately; poll the view until `frames_pending == 0` (<= 30 s) -> the route answers `image/jpeg`.
  - `test_a_trimmed_stage_streams_the_trim`: copy `source_clip` to `trimmed_video_path(...)` for alice stage 1 after assigning the video with `beep_time=1.0` -> `stream_kind == "trim"`.
  - `test_a_poll_queues_one_frames_job`: with a project reference lacking a frame, two GETs -> exactly one active `footage_sort_frames` job (`state.jobs.find_active`/`list`).
- [ ] Run: `uv run pytest -n0 tests/test_footage_sort_api.py -k "reference or frames" -v` (with the ffmpeg wrapper). Expected: KeyError / 404 failures.
- [ ] Implement; register the job in `server.py`.
- [ ] Run the sort API file and `tests/test_openapi_export.py`; export OpenAPI and `pnpm gen:api`. Expected: pass, both generated files changed.
- [ ] Commit: `feat(ui): reference footage and frames on the sort view`.

### Task 4: SPA derivations

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (alias `SortReference = Schema["SortReference"]`), `lib/api/match.ts` (`footageSortReferenceFrameUrl(scanId, slug, stage)`)
- Modify: `src/splitsmith/ui_static/src/lib/footageSort.ts`
- Test: `src/splitsmith/ui_static/src/lib/footageSort.test.ts` (fixture gains `stages_with_footage`, `references`, `frames_pending`)

**Produces:**

```ts
export interface ReferenceTile { stage: number; name: string; reference: SortReference | null; frameUrl: string | null; has: boolean }
export function referenceShooters(view: SortView): ShooterRef[]           // with >= 1 reference, most first, then name
export function defaultReferenceShooter(view: SortView, assigning: string): string | null  // first of the above not equal to assigning, else the first
export function referenceTiles(view: SortView, referenceShooter: string, assigning: string, clipId: string): ReferenceTile[]
export function missingCount(tiles: ReferenceTile[]): number              // tiles with !has
export function referenceVideoUrl(view: SortView, ref: SortReference): string
```

- `has`: `stages_with_footage` of `assigning` includes the stage, or a clip with `checked`, `imported_by === null`, proposal `(assigning, stage)` and `clip_id !== clipId`.
- `frameUrl`: project + `frame` -> `api.footageSortReferenceFrameUrl`; sort + `frame` -> `api.footageSortThumbUrl(scanId, clip.index)`; else null.
- `referenceVideoUrl`: project -> `api.videoStreamUrl(slug, video_path, stream_kind, null, stage)`; sort -> `api.footageSortVideoUrl(scanId, clip.index)`.

- [ ] Tests: shooters ranked by reference count; default skips the assigning shooter; tiles cover every `view.stages` entry in order with `has` from the project and from the sort; `excludes the clip being assigned`; frame and video URLs per source.
- [ ] Run `pnpm vitest run src/lib/footageSort.test.ts`. Expected: fail (missing exports).
- [ ] Implement; run again. Expected: pass; `pnpm typecheck` clean.
- [ ] Commit: `feat(ui): derive the sort's reference tiles`.

### Task 5: The panel, the sheet wiring, What's new

**Files:**
- Create: `src/splitsmith/ui_static/src/components/footage/ReferencePanel.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/FootageSort.tsx` (page state `compare`, `referenceShooter`; `AssignSheet` props and the Compare button under the Stage picker; mount the panel inside `AssignSheet`)
- Modify: `src/splitsmith/data/whats_new.json`
- Test: `src/splitsmith/ui_static/src/pages/FootageSort.test.tsx`

**Produces:** `ReferencePanel({ view, referenceShooter, onReferenceShooter, assigning, clipId, onUseStage, onClose })`, rendered through `Portal` as `fixed inset-y-0 right-[440px] z-chrome hidden w-[480px] md:flex flex-col bg-surface border-l border-rule-strong`, `role="complementary"` `aria-label="Compare with a shooter"`.

- Header: "Compare with" `Label`, a `<select aria-label="Reference shooter">` of `referenceShooters`, a Close button.
- A `<video controls muted playsInline>` keyed by the chosen stage, `src = referenceVideoUrl`; under it "Use stage NN" (`Button`, size sm, disabled with no stage chosen) -> `onUseStage(stage)`.
- `Label` heading `Stages · N still missing for <name>`; a 3-column grid of `<button aria-label="Stage NN <name>">` tiles with the frame as background (`bg-cover`), the ordinal in `numeral`, the name, "has it" dimmed (`opacity-50`) when `has`, "no clip" muted and disabled when `reference === null`, outline `ring-1 ring-ink` on the chosen tile.
- `AssignSheet` gains `compare: boolean; onCompare: (open: boolean) => void; referenceShooter: string | null; onReferenceShooter: (key: string) => void`; the Compare button is `hidden md:inline-flex`, disabled with `title="No shooter has footage yet"` when `referenceShooters(view)` is empty; `onUseStage` is the sheet's `setStage`. The page keeps `compare` and `referenceShooter` in `useState` so they survive the sheet's per-clip remount; `referenceShooter ?? defaultReferenceShooter(view, shooter)`.
- What's new entry, id `sort-footage-compare`, title "Compare a clip with a sorted shooter", body naming Sort footage and the Compare with a shooter button.

- [ ] Page tests (fixture gains `stages_with_footage: [1, 2]` for mathias, references for mathias on stages 1 and 2 from the project, `frames_pending: 0`):
  - `compares a clip with a sorted shooter`: open VID_1, press Compare with a shooter, the panel lists "Stage 01 Lucky Seven" and "Stage 02 Long Way Round" tiles; click stage 2, press Use stage 02, the Stage combobox reads "2"; press Use this -> `putFootageSortDecisions` called with an override `{ clip_id: "head/VID_1.mp4", shooter: "mathias", stage: 2 }` (match the shape `assignClip` emits today).
  - `the panel survives the next clip`: open, Compare, Next (ArrowDown), the panel is still there.
  - `marks the stages the shooter already has`: assigning anton with a checked anton clip on stage 1 in the fixture -> tile 1 says "has it" and the heading says "1 still missing".
- [ ] Run `pnpm vitest run src/pages/FootageSort.test.tsx`. Expected: fail (no button).
- [ ] Implement; run page tests, `pnpm typecheck`, eslint on the touched files, `uv run pytest -n0 tests/test_whats_new.py`. Expected: pass.
- [ ] Commit: `feat(ui): compare a clip with a sorted shooter on Sort footage`.

### Task 6: Look at it, then review

- [ ] `uv run python scripts/seed_demo_match.py ~/.claude-tmp/demo-match --media`; start the server per CLAUDE.md on port 5175 with `SPLITSMITH_AUTO_SYNC=0`; start a sort on the demo's footage folder, open a clip, press Compare; screenshot at 1280x800 with Playwright and send it.
- [ ] One review pass over the branch (the project's review practice): verify the frame fallback, the legacy trim path, the "has it" exclusion, and that each new test fails without its change.
- [ ] Run the full affected suites: `tests/test_footage_sort*.py`, `tests/test_openapi_export.py`, `pnpm vitest run`, `pnpm typecheck`, `pnpm lint`.
- [ ] Open the PR with a conventional title: `feat(ui): compare a clip with a sorted shooter on Sort footage`.
