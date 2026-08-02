# Audit-Free Trim Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce lossless per-stage trims from a beep and a stage time alone -- no shot detection -- and let each shooter contribute a chosen camera to the multi-shooter compare grid.

**Architecture:** The exporter's audit-JSON gate becomes a read-or-empty so the existing permissive empty-`shots[]` path becomes reachable. A new pure module, `match_trims.py`, classifies every shooter-stage in a match as trim-exportable or skipped and drives `exports.export_stage` with trim-only flags. Camera selection resolves per stage against `camera_mount` with a `role` fallback, shared by both the trim runner and the compare loader.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer + Rich (CLI), FastAPI (SPA endpoints), pytest, React + TypeScript (SPA).

## Global Constraints

- Python 3.11+, type hints on every function. `pathlib.Path` for paths, never strings.
- `uv` for dependency management -- never `pip`. **Add no new dependencies in this plan.**
- Black formatting, line length 110. Ruff clean.
- Imports grouped stdlib, third-party, local, separated by blank lines. No relative imports beyond a single dot.
- Pydantic models for data crossing module boundaries -- no bare dicts of unknown shape.
- Detection logic stays out of the CLI; CLI orchestrates only.
- **Never shell out to ffmpeg in unit tests.** Mock `trim.trim_video`. Integration tests that need real ffmpeg carry `@pytest.mark.integration`.
- Generate no fake audio fixtures. Reuse existing fixtures or stub at the function boundary.
- Run `uv run pytest <file> -v` for tests; `uv run ruff check src tests` and `uv run black --check src tests` before each commit.
- Spec: `docs/superpowers/specs/2026-08-02-audit-free-trim-export-design.md`.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/splitsmith/ui/exports.py` (modify) | Missing audit JSON collapses to zero shots; corrupt still raises. |
| `src/splitsmith/ui/project.py` (modify) | `is_stub_audit` helper; `stage_audit_status` treats stubs as `ready`. |
| `src/splitsmith/ui/server.py` (modify) | Beep review seeds a stub audit doc. |
| `src/splitsmith/camera_select.py` (create) | Pure camera resolution: mount, then role. Used by trims and compare. |
| `src/splitsmith/compare/project_loader.py` (modify) | Read authoritative `project.json`; select the chosen camera's video + trim path. |
| `src/splitsmith/compare/manifest.py` (modify) | `camera` field on `CompareShooter`. |
| `src/splitsmith/compare/emitter.py` (modify) | Stage marker records camera substitutions. |
| `src/splitsmith/compare/cli.py` (modify) | `--camera slug=value` on the match-folder path. |
| `src/splitsmith/match_trims.py` (create) | `plan_trims` (pure classification) + `run_trims` (drives `export_stage`). |
| `src/splitsmith/match_cli.py` (modify) | `splitsmith match trims` verb. |
| `src/splitsmith/ui_static/src/pages/Export.tsx` (modify) | "Trims only" mode + camera picker. |

## Task Dependency Graph

```
Task 1 (exporter gate) ──┐
                         ├─> Task 6 (match_trims core) ─> Task 7 (CLI verb)
Task 2 (stub + status)   │
                         │
Task 3 (loader reads     │
        project.json) ───┤
                         ├─> Task 5 (loader camera) ─> Task 8 (emitter marker)
Task 4 (camera_select) ──┘

Task 9 (SPA) depends on Task 1 only.
```

Tasks 1, 2, 3 and 4 have no dependencies on each other and can run in parallel.

---

### Task 1: Exporter tolerates a missing audit JSON

**Files:**
- Modify: `src/splitsmith/ui/exports.py:213-218`
- Test: `tests/test_ui_exports.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `exports.export_stage` no longer raises `StageExportError` when `audit_path` does not exist. Task 6's `run_trims` relies on this. Signature unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ui_exports.py`. `_audit_payload` already exists in that file; reuse it for the corrupt-file test.

```python
def test_export_stage_missing_audit_writes_trim(tmp_path: Path, monkeypatch) -> None:
    """A stage that never ran shot detection still exports its lossless
    trim: beep + stage time are the only real prerequisites (#214 made
    empty shots[] permissive, but the gate above it was unreachable)."""
    source = tmp_path / "GX010042.MP4"
    source.write_bytes(b"not really video")
    calls: list[dict] = []

    def fake_trim_video(src, dst, **kwargs):
        calls.append({"src": src, "dst": dst, **kwargs})
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"trimmed")

    monkeypatch.setattr(exports_mod.trim, "trim_video", fake_trim_video)

    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=True,
            write_csv=False,
            write_fcpxml=False,
            write_report=False,
        ),
        audit_path=tmp_path / "audit" / "stage1.json",  # deliberately absent
        exports_dir=tmp_path / "exports",
        source_video_path=source,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="El Prez",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )

    assert result.trimmed_video_path is not None
    assert result.trimmed_video_path.exists()
    assert result.shots_written == 0
    assert calls[0]["beep_time"] == 10.0
    assert calls[0]["stage_time"] == 8.0
    assert calls[0]["mode"] == "lossless"


def test_export_stage_missing_audit_skips_csv_with_reason(tmp_path: Path, monkeypatch) -> None:
    """Asking for CSV without shot data is not an error -- the trim ships
    and the CSV skip is surfaced as an anomaly, same as an empty shots[]."""
    source = tmp_path / "GX010042.MP4"
    source.write_bytes(b"not really video")
    monkeypatch.setattr(
        exports_mod.trim,
        "trim_video",
        lambda src, dst, **kw: dst.write_bytes(b"trimmed"),
    )

    result = exports_mod.export_stage(
        request=exports_mod.StageExportRequest(
            stage_number=1,
            write_trim=True,
            write_csv=True,
            write_fcpxml=False,
            write_report=False,
        ),
        audit_path=tmp_path / "audit" / "stage1.json",
        exports_dir=tmp_path / "exports",
        source_video_path=source,
        pre_buffer_seconds=5.0,
        post_buffer_seconds=5.0,
        stage_data=StageData(
            stage_number=1,
            stage_name="El Prez",
            time_seconds=8.0,
            scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
        ),
        beep_time_in_source=10.0,
        config=Config(),
    )

    assert result.csv_path is None
    assert result.trimmed_video_path is not None
    assert any("csv not written: no shots audited" in a for a in result.anomalies)


def test_export_stage_corrupt_audit_still_raises(tmp_path: Path) -> None:
    """A malformed audit file is a real fault -- distinct from 'detection
    never ran' -- and must not be silently treated as zero shots."""
    audit_path = tmp_path / "stage1.json"
    audit_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(exports_mod.StageExportError, match="failed to read audit JSON"):
        exports_mod.export_stage(
            request=exports_mod.StageExportRequest(stage_number=1, write_trim=False),
            audit_path=audit_path,
            exports_dir=tmp_path / "exports",
            source_video_path=None,
            pre_buffer_seconds=5.0,
            post_buffer_seconds=5.0,
            stage_data=StageData(
                stage_number=1,
                stage_name="El Prez",
                time_seconds=8.0,
                scorecard_updated_at=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
            ),
            beep_time_in_source=10.0,
            config=Config(),
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ui_exports.py -k "missing_audit or corrupt_audit" -v`
Expected: the two `missing_audit` tests FAIL with `StageExportError: no audit JSON at ...`. The `corrupt_audit` test PASSES already (existing behavior) -- keep it as a regression guard.

- [ ] **Step 3: Implement the read-or-empty helper**

In `src/splitsmith/ui/exports.py`, add above `export_stage`:

```python
def _read_audit_data(audit_path: Path) -> dict[str, Any]:
    """Return the stage's audit document, or an empty one when absent.

    A missing file means shot detection never ran for this stage. That is
    a legitimate state -- the lossless trim and the FCPXML spine need only
    a beep and a stage time -- so it collapses to zero shots and the
    shot-dependent artefacts skip themselves downstream. A file that
    exists but won't parse is a real fault and still raises.
    """
    if not audit_path.exists():
        return {"shots": []}
    try:
        return json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageExportError(f"failed to read audit JSON {audit_path}: {exc}") from exc
```

Then replace `export_stage`'s current lines 213-218:

```python
    if not audit_path.exists():
        raise StageExportError(f"no audit JSON at {audit_path}; finish auditing this stage first")
    try:
        audit_data = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageExportError(f"failed to read audit JSON {audit_path}: {exc}") from exc
```

with:

```python
    audit_data = _read_audit_data(audit_path)
```

Update the docstring line that reads ``audit_path`` must exist and contain at least one shot.`` to:

```
    ``audit_path`` may be absent -- a stage that never ran shot detection
    still exports its trim, FCPXML spine and report. Artefacts that need
    shots (CSV, overlay, shot markers) skip themselves with a recorded
    reason.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ui_exports.py -v`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run black --check src tests
git add src/splitsmith/ui/exports.py tests/test_ui_exports.py
git commit -m "feat(exports): treat a missing audit JSON as zero shots

A stage with a beep and a stage time can produce its lossless trim and
FCPXML spine without shot detection. The empty-shots[] path from #214 was
unreachable because the existence gate above it raised first. Corrupt
audit JSON still raises -- that is a fault, not an unrun detection."
```

---

### Task 2: Stub audit on beep confirm, and stubs do not read as work-in-progress

**Files:**
- Modify: `src/splitsmith/ui/project.py:440-503` (`stage_audit_status`)
- Modify: `src/splitsmith/ui/server.py:8594-8632` (`set_beep_reviewed`)
- Test: `tests/test_ui_project.py`, `tests/test_ui_server.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `project.STUB_AUDIT_DETECTION: str = "none"` and `project.is_stub_audit(payload: dict[str, Any]) -> bool`. No other task depends on these.

- [ ] **Step 1: Write the failing status tests**

Add to `tests/test_ui_project.py`:

```python
def test_stub_audit_doc_keeps_stage_ready(tmp_path: Path) -> None:
    """A beep-confirm stub is not evidence of audit work. Without this the
    sidebar, Home cards and chip strip all report in_progress for stages
    nobody has opened -- the exact drift StageStatus exists to prevent."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "stage1.json").write_text(
        json.dumps({"shots": [], "detection": "none"}), encoding="utf-8"
    )
    stage = StageEntry(
        stage_number=1,
        stage_name="El Prez",
        time_seconds=8.0,
        videos=[StageVideo(path=Path("/tmp/GX010042.MP4"), role="primary", beep_time=10.0)],
    )

    assert stage_audit_status(stage, audit_dir) == StageStatus.ready


def test_real_audit_without_save_event_is_in_progress(tmp_path: Path) -> None:
    """Regression guard: relaxing the stub case must not relax the real one."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "stage1.json").write_text(
        json.dumps({"shots": [{"shot_number": 1, "time": 5.5}]}), encoding="utf-8"
    )
    stage = StageEntry(
        stage_number=1,
        stage_name="El Prez",
        time_seconds=8.0,
        videos=[StageVideo(path=Path("/tmp/GX010042.MP4"), role="primary", beep_time=10.0)],
    )

    assert stage_audit_status(stage, audit_dir) == StageStatus.in_progress


def test_stub_audit_doc_keeps_stage_ready_hosted(tmp_path: Path) -> None:
    """Hosted mode reads audit_docs, not the filesystem -- same rule."""
    stage = StageEntry(
        stage_number=1,
        stage_name="El Prez",
        time_seconds=8.0,
        videos=[StageVideo(path=Path("/tmp/GX010042.MP4"), role="primary", beep_time=10.0)],
    )
    docs = {1: {"shots": [], "detection": "none"}}

    assert stage_audit_status(stage, tmp_path, audit_docs=docs) == StageStatus.ready
```

Import `stage_audit_status`, `StageStatus`, `StageEntry`, `StageVideo` from `splitsmith.ui.project` at the top of the test file if they are not already imported.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ui_project.py -k "stub_audit or without_save_event" -v`
Expected: both `stub_audit` tests FAIL asserting `in_progress == ready`. The `without_save_event` test PASSES (existing behavior).

- [ ] **Step 3: Implement the stub rule**

In `src/splitsmith/ui/project.py`, add near the `StageStatus` definition:

```python
#: ``detection`` value marking an audit document that exists only because a
#: beep was confirmed -- no detector ever ran. Readers must treat it as
#: equivalent to no audit document at all.
STUB_AUDIT_DETECTION = "none"


def is_stub_audit(payload: dict[str, Any]) -> bool:
    """True when this audit document is a beep-confirm placeholder.

    Seeded by the beep-review endpoint so status surfaces and the lab have
    a concrete document to read instead of inferring from absence. It
    carries no shot data and represents no audit work.
    """
    return payload.get("detection") == STUB_AUDIT_DETECTION
```

In `stage_audit_status`, insert immediately after the `payload` is obtained from either branch and before `events = payload.get("audit_events") or []`:

```python
    if is_stub_audit(payload):
        # Beep-confirm placeholder: same meaning as no audit document.
        return StageStatus.ready
```

Note the hosted branch currently returns early only when `payload is None`; the stub check must sit after both branches converge so it covers hosted and local alike.

- [ ] **Step 4: Run to verify the status tests pass**

Run: `uv run pytest tests/test_ui_project.py -v`
Expected: PASS, whole file.

- [ ] **Step 5: Write the failing endpoint test**

Add to `tests/test_ui_server.py`, following that file's existing client fixture pattern:

```python
def test_beep_review_seeds_stub_audit(client, seeded_project_root: Path) -> None:
    """Confirming a beep leaves a concrete audit document behind so status
    surfaces read a document rather than inferring from absence."""
    audit_file = seeded_project_root / "audit" / "stage1.json"
    assert not audit_file.exists()

    resp = client.post(
        "/api/shooters/default/stages/1/videos/{vid}/beep/review".format(vid=PRIMARY_VIDEO_ID),
        json={"reviewed": True},
    )
    assert resp.status_code == 200

    payload = json.loads(audit_file.read_text(encoding="utf-8"))
    assert payload == {"shots": [], "detection": "none"}


def test_beep_review_does_not_clobber_existing_audit(client, seeded_project_root: Path) -> None:
    """Re-confirming a beep on an audited stage must not wipe shot data."""
    audit_file = seeded_project_root / "audit" / "stage1.json"
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.write_text(
        json.dumps({"shots": [{"shot_number": 1, "time": 5.5}]}), encoding="utf-8"
    )

    resp = client.post(
        "/api/shooters/default/stages/1/videos/{vid}/beep/review".format(vid=PRIMARY_VIDEO_ID),
        json={"reviewed": True},
    )
    assert resp.status_code == 200

    payload = json.loads(audit_file.read_text(encoding="utf-8"))
    assert payload["shots"] == [{"shot_number": 1, "time": 5.5}]
```

Adapt `client`, `seeded_project_root` and `PRIMARY_VIDEO_ID` to the fixtures and helpers already present in `tests/test_ui_server.py` -- read the top of that file first and reuse its project-seeding helper rather than inventing one.

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/test_ui_server.py -k "beep_review_seeds or beep_review_does_not_clobber" -v`
Expected: `seeds_stub_audit` FAILS (no file written). `does_not_clobber` PASSES.

- [ ] **Step 7: Seed the stub in the endpoint**

In `src/splitsmith/ui/server.py`, inside `set_beep_reviewed`, after `project.save(state.shooter_root(slug))` and before the shot-detect kick block:

```python
        # Leave a concrete audit document behind so status surfaces and the
        # lab read a document instead of inferring from absence. Never
        # overwrite a real one -- a re-confirm on an audited stage must not
        # wipe shot data. The exporter does not depend on this existing;
        # projects predating this change have no stub and still export.
        if req.reviewed:
            existing_doc, audit_version = state.load_audit(slug, stage_number)
            if existing_doc is None:
                state.save_audit(
                    slug,
                    stage_number,
                    {"shots": [], "detection": project_mod.STUB_AUDIT_DETECTION},
                    version=audit_version,
                )
```

Use whatever alias `server.py` already imports `splitsmith.ui.project` under; if it imports names directly, import `STUB_AUDIT_DETECTION` alongside them rather than adding a new module alias.

- [ ] **Step 8: Run to verify the endpoint tests pass**

Run: `uv run pytest tests/test_ui_server.py -k "beep_review" -v`
Expected: PASS.

- [ ] **Step 9: Lint and commit**

```bash
uv run ruff check src tests && uv run black --check src tests
git add src/splitsmith/ui/project.py src/splitsmith/ui/server.py tests/test_ui_project.py tests/test_ui_server.py
git commit -m "feat(audit): seed a stub audit doc on beep confirm

Confirming a beep writes {shots: [], detection: none} when no audit
document exists, so status surfaces read a document instead of inferring
from absence. stage_audit_status treats a stub as no-audit and keeps the
stage 'ready' -- without that every beep-confirmed stage would falsely
report in_progress."
```

---

### Task 3: Compare's match loader reads the authoritative project file

**Files:**
- Modify: `src/splitsmith/compare/project_loader.py:141-190` (`load_shooter_from_match`)
- Test: `tests/test_compare_project_loader.py`

**Context (this is a live bug, not a refactor):** `load_shooter_from_match` reads per-stage videos from `shooter.json` via `Match.load_shooter`. `shooter.json` is written once at merge time (`match_model.py:741-755`); every server write afterwards goes to `project.json` (`MatchProject.save`, `project.py:985`, and every `legacy.save(shooter_root)` call site in `server.py`). Nothing syncs the two. So beeps detected *after* the merge are invisible to the compare export, which then emits an all-filler grid. `tests/test_compare_merged_match.py` misses this because it seeds data before merging and never edits afterwards.

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `load_shooter_from_match(match_root, slug, label, *, probe=None)` -- signature unchanged, per-stage data now sourced from `project.json`. Task 5 extends this same function with a `camera` parameter.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_compare_project_loader.py`:

```python
def test_load_shooter_from_match_sees_post_merge_beeps(tmp_path: Path) -> None:
    """Beeps detected after the merge live in project.json; shooter.json is
    a merge-time snapshot nothing updates. Reading the snapshot silently
    drops every stage the user beeped after merging."""
    match_root = _build_two_stage_match(tmp_path)  # helper below
    shooter_root = Match.shooter_root(match_root, "mathias")

    # Simulate the server confirming a beep after the merge: project.json
    # is written, shooter.json is deliberately left stale.
    proj = MatchProject.load(shooter_root)
    proj.stage(2).primary().beep_time = 12.5
    proj.save(shooter_root)

    exports = shooter_root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / f"stage2_{_slugify('Stage Two')}_trimmed.mp4").write_bytes(b"trim")

    bundle = load_shooter_from_match(match_root, "mathias", "Mathias", probe=_stub_probe)

    assert 2 in bundle.stages_by_number
    assert bundle.stages_by_number[2].beep_offset_in_clip == pytest.approx(5.0)
```

Write `_build_two_stage_match(tmp_path)` as a module-level helper in the same file: create a `Match` with two `MatchStageDefinition`s, one shooter slug `mathias`, write both `shooter.json` (via `Match.add_shooter`) and `project.json` (via `MatchProject.save`) with stage 1 fully beeped and stage 2 with `beep_time=None`, plus a stage 1 trim on disk. Mirror the seeding style already used in `tests/test_compare_merged_match.py::_seed_legacy_project`. `_stub_probe` also exists there -- copy it rather than importing across test modules.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_compare_project_loader.py -k post_merge_beeps -v`
Expected: FAIL -- `2 in bundle.stages_by_number` is False, because `shooter.json` still has `beep_time=None` for stage 2.

- [ ] **Step 3: Read per-stage data from project.json**

In `load_shooter_from_match`, replace the `Shooter`-sourced stage walk with the authoritative project. Keep `Match` as the source of stage *names* only:

```python
    match = Match.load(match_root)
    shooter_root = Match.shooter_root(match_root, slug)
    # Per-stage data comes from project.json: it is authoritative for
    # everything the server writes (beeps, roles, buffers). shooter.json is
    # a merge-time snapshot that nothing keeps in sync, so reading it drops
    # any beep confirmed after the merge. Stage *names* still come from the
    # match, which owns the shared stage definitions.
    project = MatchProject.load(shooter_root)
    stage_names: dict[int, str] = {s.stage_number: s.stage_name for s in match.stages}
    pre_buffer = project.trim_pre_buffer_seconds
```

Then iterate `project.stages` instead of `shooter.stages`, using `stage.primary()` (the `MatchProject` helper) rather than the manual `next((v for v in stage.videos if v.role == "primary"), None)`, and fall back to `stage.stage_name` when the match has no definition for that number:

```python
        stage_name = stage_names.get(stage.stage_number, stage.stage_name)
```

Keep `_trim_path_for_shooter_stage` as the trim resolver but source `exports_dir` from the project: replace its `shooter: Shooter` parameter with `project: MatchProject` and read `project.exports_path(shooter_root)`, which already handles the absolute/relative override logic. Delete the now-unused `Shooter` import if nothing else in the module uses it.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_compare_project_loader.py tests/test_compare_merged_match.py -v`
Expected: PASS. The merged-match parity test must still pass -- it is the guard that the manifest and match paths agree.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run black --check src tests
git add src/splitsmith/compare/project_loader.py tests/test_compare_project_loader.py
git commit -m "fix(compare): read per-stage data from project.json, not shooter.json

shooter.json is written once at merge time and nothing syncs it; every
server write goes to project.json. The match-folder compare path was
reading the stale snapshot, so any beep confirmed after the merge was
invisible and its stage became a black filler tile."
```

---

### Task 4: Camera resolution

**Files:**
- Create: `src/splitsmith/camera_select.py`
- Test: `tests/test_camera_select.py`

**Interfaces:**
- Consumes: `splitsmith.ui.project.StageVideo`.
- Produces, relied on by Tasks 5 and 6:

```python
class CameraResolutionError(ValueError): ...

def resolve_camera(videos: list[StageVideo], camera: str | None) -> StageVideo | None
def available_selectors(videos: list[StageVideo]) -> list[str]
def validate_camera(stages_videos: list[list[StageVideo]], camera: str | None) -> None
```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_camera_select.py`:

```python
"""Camera selection: mount first, role as fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.camera_select import (
    CameraResolutionError,
    available_selectors,
    resolve_camera,
    validate_camera,
)
from splitsmith.ui.project import StageVideo


def _video(name: str, *, role: str, mount: str | None = None) -> StageVideo:
    return StageVideo(path=Path(f"/tmp/{name}.MP4"), role=role, camera_mount=mount)


def test_resolves_by_mount() -> None:
    videos = [
        _video("a", role="primary", mount="helmet"),
        _video("b", role="secondary", mount="chest"),
    ]
    assert resolve_camera(videos, "chest").path.name == "b.MP4"


def test_mount_wins_over_role_name_collision() -> None:
    """A mount literally tagged 'primary' is matched as a mount first."""
    videos = [
        _video("a", role="primary", mount="helmet"),
        _video("b", role="secondary", mount="primary"),
    ]
    assert resolve_camera(videos, "primary").path.name == "b.MP4"


def test_falls_back_to_role() -> None:
    videos = [_video("a", role="primary"), _video("b", role="secondary")]
    assert resolve_camera(videos, "primary").path.name == "a.MP4"
    assert resolve_camera(videos, "secondary").path.name == "b.MP4"


def test_none_selects_primary() -> None:
    videos = [_video("a", role="primary"), _video("b", role="secondary")]
    assert resolve_camera(videos, None).path.name == "a.MP4"


def test_secondary_role_with_two_secondaries_raises() -> None:
    """Ingest order must not decide which camera you get."""
    videos = [
        _video("a", role="primary"),
        _video("b", role="secondary"),
        _video("c", role="secondary"),
    ]
    with pytest.raises(CameraResolutionError, match="two or more secondaries"):
        resolve_camera(videos, "secondary")


def test_unresolvable_on_this_stage_returns_none() -> None:
    """Absent on one stage is normal -- caller substitutes the primary."""
    videos = [_video("a", role="primary", mount="helmet")]
    assert resolve_camera(videos, "chest") is None


def test_ignored_videos_are_never_selected() -> None:
    videos = [_video("a", role="primary"), _video("b", role="ignored", mount="chest")]
    assert resolve_camera(videos, "chest") is None


def test_available_selectors_lists_mounts_and_roles() -> None:
    videos = [
        _video("a", role="primary", mount="helmet"),
        _video("b", role="secondary", mount="chest"),
    ]
    assert available_selectors(videos) == ["chest", "helmet", "primary", "secondary"]


def test_validate_camera_raises_when_never_resolvable() -> None:
    """A value matching nothing anywhere in the project is a config error."""
    stages = [[_video("a", role="primary", mount="helmet")]]
    with pytest.raises(CameraResolutionError) as exc:
        validate_camera(stages, "chest")
    assert "helmet" in str(exc.value)
    assert "primary" in str(exc.value)


def test_validate_camera_accepts_partial_availability() -> None:
    """Resolvable on at least one stage is valid; per-stage gaps are normal."""
    stages = [
        [_video("a", role="primary", mount="helmet")],
        [_video("b", role="primary", mount="helmet"), _video("c", role="secondary", mount="chest")],
    ]
    validate_camera(stages, "chest")  # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_camera_select.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'splitsmith.camera_select'`.

- [ ] **Step 3: Implement the module**

Create `src/splitsmith/camera_select.py`:

```python
"""Per-shooter camera selection for trim and compare exports.

``StageVideo.video_id`` hashes ``"<path>#<stage_number>"`` (``project.py``),
so it identifies a file on one stage, not a camera across a match. A choice
that holds for a whole match therefore keys off ``camera_mount`` (the
helmet/chest classification from issue #143) or ``role``.

Resolution is mount-first so a user who tags mounts gets the obvious
behaviour, with ``primary`` / ``secondary`` as the fallback for untagged
projects.
"""

from __future__ import annotations

from .ui.project import StageVideo

#: Role names accepted as selectors when no mount matches.
ROLE_SELECTORS = ("primary", "secondary")


class CameraResolutionError(ValueError):
    """A camera selector matches nothing in a shooter's project."""


def available_selectors(videos: list[StageVideo]) -> list[str]:
    """Every selector that could resolve against ``videos``, sorted.

    Used to build error messages that tell the user what they *can* pick.
    ``ignored`` videos contribute nothing -- they are never selectable.
    """
    selectors: set[str] = set()
    for video in videos:
        if video.role == "ignored":
            continue
        if video.camera_mount:
            selectors.add(video.camera_mount)
        if video.role in ROLE_SELECTORS:
            selectors.add(video.role)
    return sorted(selectors)


def resolve_camera(videos: list[StageVideo], camera: str | None) -> StageVideo | None:
    """Return the video ``camera`` names on this stage, or ``None``.

    ``None`` means "not on this stage" -- normal when a cam was forgotten or
    its battery died -- and the caller substitutes the primary. It does not
    mean the selector is invalid; :func:`validate_camera` decides that once,
    across the whole project.

    ``camera=None`` selects the primary, preserving pre-existing behaviour.
    """
    selectable = [v for v in videos if v.role != "ignored"]
    if camera is None:
        return next((v for v in selectable if v.role == "primary"), None)

    by_mount = [v for v in selectable if v.camera_mount == camera]
    if by_mount:
        return by_mount[0]

    if camera in ROLE_SELECTORS:
        by_role = [v for v in selectable if v.role == camera]
        if camera == "secondary" and len(by_role) > 1:
            raise CameraResolutionError(
                f"stage has two or more secondaries; select by mount instead "
                f"(available: {', '.join(available_selectors(videos))})"
            )
        if by_role:
            return by_role[0]

    return None


def validate_camera(stages_videos: list[list[StageVideo]], camera: str | None) -> None:
    """Raise when ``camera`` resolves on no stage of a shooter's project.

    Resolvable on at least one stage is enough: per-stage gaps are handled
    by substitution, but a value that matches nothing anywhere is a typo or
    a stale config and must fail loudly rather than silently exporting every
    tile from the primary.
    """
    if camera is None:
        return
    every_selector: set[str] = set()
    for videos in stages_videos:
        every_selector.update(available_selectors(videos))
        try:
            if resolve_camera(videos, camera) is not None:
                return
        except CameraResolutionError:
            # Ambiguity on one stage still proves the selector is meaningful.
            return
    raise CameraResolutionError(
        f"camera {camera!r} matches no mount or role in this project "
        f"(available: {', '.join(sorted(every_selector)) or 'none'})"
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_camera_select.py -v`
Expected: PASS, all eleven tests.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run black --check src tests
git add src/splitsmith/camera_select.py tests/test_camera_select.py
git commit -m "feat(camera): per-shooter camera selection by mount with role fallback

video_id hashes path + stage number, so it cannot name a camera across a
match. Selection keys off camera_mount first, then the primary/secondary
role. A selector that resolves nowhere in a project is a config error; one
that misses a single stage is normal and handled by substitution."
```

---

### Task 5: Camera selection through the manifest, loader and compare CLI

**Files:**
- Modify: `src/splitsmith/compare/manifest.py` (`CompareShooter`)
- Modify: `src/splitsmith/compare/project_loader.py` (`load_shooter`, `load_shooter_from_match`, `CompareStageBundle`)
- Modify: `src/splitsmith/compare/cli.py` (`--camera` on the match-folder path)
- Modify: `src/splitsmith/ui/project.py` (`MatchProject.compare_camera` field)
- Test: `tests/test_compare_manifest.py`, `tests/test_compare_project_loader.py`, `tests/test_compare_cli.py`

**Interfaces:**
- Consumes: `camera_select.resolve_camera`, `camera_select.validate_camera`, `camera_select.CameraResolutionError` (Task 4). `load_shooter_from_match` reading `project.json` (Task 3).
- Produces, relied on by Tasks 6 and 8:
  - `MatchProject.compare_camera: str | None = None` (persisted in `project.json`).
  - `CompareShooter.camera: str | None = None`.
  - `CompareStageBundle.camera_mount: str | None` and `CompareStageBundle.substituted: bool`.
  - `load_shooter(project_root, label, *, camera=None, probe=None)` and `load_shooter_from_match(match_root, slug, label, *, camera=None, probe=None)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_compare_manifest.py`:

```python
def test_manifest_accepts_camera_per_shooter(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text(
        "output: out.fcpxml\n"
        "audio_from: Mathias\n"
        "shooters:\n"
        "  - project: ./mathias\n"
        "    label: Mathias\n"
        "    camera: chest\n"
        "  - project: ./anders\n"
        "    label: Anders\n",
        encoding="utf-8",
    )
    manifest = load_manifest(path)
    assert manifest.shooters[0].camera == "chest"
    assert manifest.shooters[1].camera is None
```

Add to `tests/test_compare_project_loader.py`:

```python
def test_load_shooter_selects_secondary_cam_trim_and_beep(tmp_path: Path) -> None:
    """A shooter on a chest cam contributes that cam's trim, aligned on that
    cam's own beep -- secondaries are cut with the same buffers anchored on
    their own beep_time (ui/exports.py), so the offset formula is identical."""
    root = _seed_project_with_two_cams(tmp_path)  # helper below
    bundle = load_shooter(root, "Mathias", camera="chest", probe=_stub_probe)

    stage = bundle.stages_by_number[1]
    assert stage.trim_path.name.endswith("_cam_" + CHEST_VIDEO_ID + "_trimmed.mp4")
    assert stage.beep_offset_in_clip == pytest.approx(5.0)
    assert stage.camera_mount == "chest"
    assert stage.substituted is False


def test_load_shooter_substitutes_primary_when_cam_missing(tmp_path: Path) -> None:
    """Chest cam absent on stage 2 -> that tile uses the primary and says so."""
    root = _seed_project_with_two_cams(tmp_path, chest_on_stage_2=False)
    bundle = load_shooter(root, "Mathias", camera="chest", probe=_stub_probe)

    stage = bundle.stages_by_number[2]
    assert "_cam_" not in stage.trim_path.name
    assert stage.substituted is True


def test_load_shooter_rejects_camera_that_matches_nothing(tmp_path: Path) -> None:
    root = _seed_project_with_two_cams(tmp_path)
    with pytest.raises(CameraResolutionError, match="chest"):
        load_shooter(root, "Mathias", camera="backpack", probe=_stub_probe)
```

Write `_seed_project_with_two_cams(tmp_path, *, chest_on_stage_2=True)` in that test file: a two-stage `MatchProject` where each stage has a primary (mount `helmet`, `beep_time=10.0`) and, subject to the flag, a secondary (mount `chest`, `beep_time=11.0`); write the matching trims to `exports/` -- `stage<N>_<slug>_trimmed.mp4` for primaries and `stage<N>_<slug>_cam_<video_id>_trimmed.mp4` for the chest cam. Expose the chest cam's `video_id` as `CHEST_VIDEO_ID` from the helper so the assertion above can name it. `pre_buffer` stays at the model default of 5.0, so `min(5.0, beep_time)` is 5.0 for both cams.

Add to `tests/test_compare_cli.py` a test that `--camera mathias=chest` on the match-folder path reaches the loader -- monkeypatch `project_loader.load_shooter_from_match` and assert it received `camera="chest"`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_compare_manifest.py tests/test_compare_project_loader.py tests/test_compare_cli.py -k "camera or cam_trim or substitutes" -v`
Expected: FAIL -- `CompareShooter` rejects the extra `camera` key, and `load_shooter` has no `camera` parameter.

- [ ] **Step 3: Add the persisted field and the manifest field**

In `src/splitsmith/ui/project.py`, on `MatchProject`, beside the other trim settings:

```python
    #: Which camera this shooter contributes to a multi-shooter compare grid
    #: and to trim-only runs. Resolved per stage by ``camera_select``: a
    #: ``camera_mount`` value first, then the ``primary`` / ``secondary``
    #: role. ``None`` means the primary. CLI flags and manifest entries
    #: override this per run without persisting.
    compare_camera: str | None = None
```

In `src/splitsmith/compare/manifest.py`, on `CompareShooter`:

```python
    #: Camera selector for this shooter's tiles. A ``camera_mount`` value
    #: ("chest", "helmet") or a role ("primary", "secondary"). ``None``
    #: falls back to the shooter's persisted ``compare_camera``, then the
    #: primary.
    camera: str | None = None
```

- [ ] **Step 4: Thread the camera through the loader**

In `src/splitsmith/compare/project_loader.py`:

Add two fields to `CompareStageBundle`:

```python
    #: Mount of the camera that produced this tile, when tagged. Reporting only.
    camera_mount: str | None = None
    #: True when the requested camera was unavailable on this stage and the
    #: primary stood in. Surfaced in the run summary and the FCPXML marker.
    substituted: bool = False
```

Give both `load_shooter` and `load_shooter_from_match` a keyword-only `camera: str | None = None`. In each, before the stage walk, resolve the effective selector and validate it once against the whole project:

```python
    effective_camera = camera if camera is not None else project.compare_camera
    camera_select.validate_camera(
        [stage.videos for stage in project.stages if not stage.skipped], effective_camera
    )
```

Inside the per-stage loop, replace the hardcoded primary lookup:

```python
        primary = stage.primary()
        chosen = camera_select.resolve_camera(stage.videos, effective_camera)
        substituted = False
        if chosen is None or chosen.beep_time is None:
            # Requested cam absent or unbeeped on this stage: the primary
            # stands in so the grid keeps a live tile, and the substitution
            # is recorded rather than silently applied.
            if chosen is not None or effective_camera is not None:
                substituted = effective_camera is not None
            chosen = primary
        if chosen is None or chosen.beep_time is None:
            continue
```

Resolve the trim path from the chosen video's role -- the primary keeps the plain name, any other camera takes the `_cam_<video_id>_` name that `exports.export_stage` writes at `exports.py:301`:

```python
def trim_path_for_video(
    project: MatchProject,
    project_root: Path,
    stage_number: int,
    stage_name: str,
    video: StageVideo,
) -> Path:
    """Path the exporter writes for ``video`` on this stage.

    Primaries land at ``stage<N>_<slug>_trimmed.mp4``; every other camera
    at ``stage<N>_<slug>_cam_<video_id>_trimmed.mp4``. Mirrors
    ``exports.export_stage``.
    """
    base = f"stage{stage_number}_{_slugify(stage_name)}"
    exports = project.exports_path(project_root)
    if video.role == "primary":
        return exports / f"{base}_trimmed.mp4"
    return exports / f"{base}_cam_{video.video_id}_trimmed.mp4"
```

Populate the bundle from the chosen video: `beep_offset_in_clip=min(pre_buffer, chosen.beep_time)`, `camera_mount=chosen.camera_mount`, `substituted=substituted`. Keep `trim_path_for_stage` as a thin wrapper over `trim_path_for_video` so existing callers and tests keep working.

- [ ] **Step 5: Add `--camera` to the compare CLI**

In `src/splitsmith/compare/cli.py`, add to the `export` command:

```python
    camera: list[str] = typer.Option(
        [],
        "--camera",
        help=(
            "Camera selector for one shooter, as SLUG=VALUE (repeatable). "
            "VALUE is a camera mount ('chest') or a role ('primary', "
            "'secondary'). Overrides the shooter's persisted compare_camera. "
            "Match-folder source only."
        ),
    ),
```

Parse it with a helper that fails on a malformed pair rather than ignoring it:

```python
def _parse_camera_overrides(pairs: list[str]) -> dict[str, str]:
    """Parse ``--camera SLUG=VALUE`` pairs into a dict.

    A pair without '=' is a user error worth stopping for -- silently
    dropping it would export the wrong camera and look like it worked.
    """
    overrides: dict[str, str] = {}
    for pair in pairs:
        slug, sep, value = pair.partition("=")
        if not sep or not slug or not value:
            console.print(f"[red]Error:[/] --camera expects SLUG=VALUE, got {pair!r}")
            raise typer.Exit(code=2)
        overrides[slug] = value
    return overrides
```

Pass `camera=overrides.get(slug)` into `load_shooter_from_match` in `_export_from_match`, and `camera=s.camera` into `load_shooter` on the manifest path. When `--camera` is passed alongside a manifest source, print a warning that the manifest wins, matching how `--audio-from` and `--output` already behave there. Catch `CameraResolutionError` in both paths and exit 2 with the message.

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_compare_manifest.py tests/test_compare_project_loader.py tests/test_compare_cli.py tests/test_compare_merged_match.py -v`
Expected: PASS.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src tests && uv run black --check src tests
git add src/splitsmith/compare src/splitsmith/ui/project.py tests/test_compare_manifest.py tests/test_compare_project_loader.py tests/test_compare_cli.py
git commit -m "feat(compare): per-shooter camera selection

Each shooter can contribute a chosen camera to the grid, persisted as
compare_camera and overridable per run via the manifest or --camera
SLUG=VALUE. Secondaries are cut with the same buffers anchored on their own
beep, so alignment needs no new math -- only the _cam_<id>_ trim path. A
camera missing on one stage falls back to the primary and is flagged."
```

---

### Task 6: `match_trims` core

**Files:**
- Create: `src/splitsmith/match_trims.py`
- Test: `tests/test_match_trims.py`

**Interfaces:**
- Consumes: `exports.export_stage` tolerating a missing audit (Task 1); `camera_select.resolve_camera` / `validate_camera` (Task 4); `MatchProject.compare_camera` (Task 5).
- Produces, relied on by Task 7:

```python
class TrimPlanEntry(BaseModel):
    shooter_slug: str
    stage_number: int
    stage_name: str
    camera: str | None
    eligible: bool
    reason: str | None
    substituted_from: str | None

class TrimResult(BaseModel):
    entry: TrimPlanEntry
    trim_path: Path | None
    skip_reasons: list[str]

def plan_trims(match_root: Path, *, shooters: list[str] | None = None,
               stages: list[int] | None = None,
               cameras: dict[str, str] | None = None,
               force: bool = False) -> list[TrimPlanEntry]

def run_trims(match_root: Path, plan: list[TrimPlanEntry], *,
              progress: Callable[[TrimPlanEntry], None] | None = None) -> list[TrimResult]
```

`reason` is one of `"no_beep"`, `"no_stage_time"`, `"skipped"`, `"source_unreachable"`, `"already_exported"`, or `None` when eligible.

- [ ] **Step 1: Write the failing plan tests**

Create `tests/test_match_trims.py`:

```python
"""Trim-only planning and execution across a match's shooters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith import match_trims
from splitsmith.match_model import Match, MatchStageDefinition
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


def test_plan_marks_stage_without_beep_ineligible(two_shooter_match: Path) -> None:
    plan = match_trims.plan_trims(two_shooter_match)
    entry = _find(plan, "anders", 2)
    assert entry.eligible is False
    assert entry.reason == "no_beep"


def test_plan_marks_stage_without_time_ineligible(two_shooter_match: Path) -> None:
    """Trim length is beep-anchored but sized by the stage time -- no time,
    no trim. Guessing a duration pads the grid for every shooter."""
    plan = match_trims.plan_trims(two_shooter_match)
    entry = _find(plan, "mathias", 3)
    assert entry.eligible is False
    assert entry.reason == "no_stage_time"


def test_plan_skips_existing_trims_unless_forced(two_shooter_match: Path) -> None:
    plan = match_trims.plan_trims(two_shooter_match)
    assert _find(plan, "mathias", 1).reason == "already_exported"
    forced = match_trims.plan_trims(two_shooter_match, force=True)
    assert _find(forced, "mathias", 1).eligible is True


def test_plan_honours_shooter_and_stage_filters(two_shooter_match: Path) -> None:
    plan = match_trims.plan_trims(two_shooter_match, shooters=["anders"], stages=[1])
    assert {(e.shooter_slug, e.stage_number) for e in plan} == {("anders", 1)}


def test_plan_records_camera_substitution(two_shooter_match: Path) -> None:
    """Anders is on 'chest' but stage 1 has no chest cam."""
    plan = match_trims.plan_trims(two_shooter_match, cameras={"anders": "chest"})
    entry = _find(plan, "anders", 1)
    assert entry.eligible is True
    assert entry.substituted_from == "chest"


def test_plan_touches_no_media(two_shooter_match: Path, monkeypatch) -> None:
    """plan_trims must be pure: no ffmpeg, no probing."""
    def explode(*_a, **_kw):
        raise AssertionError("plan_trims must not touch media")

    monkeypatch.setattr(match_trims.exports.trim, "trim_video", explode)
    match_trims.plan_trims(two_shooter_match)


def test_run_trims_writes_only_eligible_stages(two_shooter_match: Path, monkeypatch) -> None:
    written: list[Path] = []

    def fake_trim_video(src, dst, **kwargs):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"trimmed")
        written.append(dst)

    monkeypatch.setattr(match_trims.exports.trim, "trim_video", fake_trim_video)

    plan = match_trims.plan_trims(two_shooter_match)
    results = match_trims.run_trims(two_shooter_match, plan)

    assert {r.entry.stage_number for r in results if r.trim_path} == {1, 2}
    assert all(p.name.endswith("_trimmed.mp4") for p in written)


def test_run_trims_reports_ffmpeg_failure_without_aborting(
    two_shooter_match: Path, monkeypatch
) -> None:
    """One bad stage must not cost the user the other twenty-three."""
    calls = {"n": 0}

    def flaky_trim_video(src, dst, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise match_trims.exports.trim.FFmpegError("boom")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"trimmed")

    monkeypatch.setattr(match_trims.exports.trim, "trim_video", flaky_trim_video)

    plan = match_trims.plan_trims(two_shooter_match)
    results = match_trims.run_trims(two_shooter_match, plan)

    assert any(r.trim_path is None and r.skip_reasons for r in results)
    assert any(r.trim_path is not None for r in results)
```

Write a `two_shooter_match` fixture in the same file building a real match folder on disk: `match.json` with three `MatchStageDefinition`s and shooters `["anders", "mathias"]`; per shooter a `project.json` via `MatchProject.save`. Give `mathias` stage 1 a beep, a stage time and an existing trim in `exports/`; stage 2 a beep and a time and no trim; stage 3 a beep and `time_seconds=0.0`. Give `anders` stage 1 a beep and a time with only a helmet-mounted primary, stage 2 a primary with `beep_time=None`, stage 3 marked `skipped=True`. Create the source video files as small byte blobs so reachability checks pass. Add a module-level `_find(plan, slug, stage)` helper returning the single matching entry.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_match_trims.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'splitsmith.match_trims'`.

- [ ] **Step 3: Implement the module**

Create `src/splitsmith/match_trims.py`:

```python
"""Trim-only export across every shooter in a match.

Produces the lossless per-stage trims a multi-shooter compare grid needs,
from a beep and a stage time alone. Shot detection is not involved: the
grid's emitter never reads shot data, and ``exports.export_stage`` treats a
missing audit document as zero shots.

``plan_trims`` is pure -- it reads project files and classifies, touching no
media -- so ``--dry-run`` shows exactly what a real run would do.
``run_trims`` drives ``exports.export_stage`` with trim-only flags, one
stage at a time, and never lets one failure end the run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from . import camera_select
from .compare.project_loader import trim_path_for_video
from .config import StageData
from .match_model import Match
from .ui import exports
from .ui.project import MatchProject, StageEntry, StageVideo

class TrimPlanEntry(BaseModel):
    """One shooter-stage, classified as trim-exportable or not."""

    shooter_slug: str
    stage_number: int
    stage_name: str
    camera: str | None = None
    eligible: bool = False
    reason: str | None = None
    substituted_from: str | None = None


class TrimResult(BaseModel):
    """What actually happened for one planned entry."""

    entry: TrimPlanEntry
    trim_path: Path | None = None
    skip_reasons: list[str] = []


def parse_camera_overrides(pairs: list[str]) -> dict[str, str]:
    """Parse ``SLUG=VALUE`` camera pairs from the CLI.

    A pair without '=' is a user error worth stopping for -- dropping it
    silently would export the wrong camera and look like it worked. Raises
    ``ValueError``; CLI callers turn that into exit code 2.
    """
    overrides: dict[str, str] = {}
    for pair in pairs:
        slug, sep, value = pair.partition("=")
        if not sep or not slug or not value:
            raise ValueError(f"--camera expects SLUG=VALUE, got {pair!r}")
        overrides[slug] = value
    return overrides


def _choose_video(
    stage: StageEntry, camera: str | None
) -> tuple[StageVideo | None, str | None]:
    """Return the video for this stage plus the camera it stood in for.

    The second element is the requested camera when the primary had to
    substitute, else ``None``. A camera that resolves nowhere in the project
    is caught earlier by ``validate_camera``; here a miss is just this
    stage's gap.
    """
    primary = stage.primary()
    if camera is None:
        return primary, None
    chosen = camera_select.resolve_camera(stage.videos, camera)
    if chosen is not None and chosen.beep_time is not None:
        return chosen, None
    return primary, camera


def plan_trims(
    match_root: Path,
    *,
    shooters: list[str] | None = None,
    stages: list[int] | None = None,
    cameras: dict[str, str] | None = None,
    force: bool = False,
) -> list[TrimPlanEntry]:
    """Classify every shooter-stage in the match. Touches no media.

    Reads ``project.json`` per shooter -- authoritative for beeps and roles;
    ``shooter.json`` is a merge-time snapshot nothing keeps in sync. Stage
    names come from the match's shared definitions.

    Raises ``camera_select.CameraResolutionError`` when a requested camera
    matches nothing anywhere in a shooter's project.
    """
    match = Match.load(match_root)
    wanted_shooters = set(shooters) if shooters else None
    wanted_stages = set(stages) if stages else None
    stage_names = {s.stage_number: s.stage_name for s in match.stages}
    overrides = cameras or {}

    plan: list[TrimPlanEntry] = []
    for slug in match.shooters:
        if wanted_shooters is not None and slug not in wanted_shooters:
            continue
        shooter_root = Match.shooter_root(match_root, slug)
        project = MatchProject.load(shooter_root)
        camera = overrides.get(slug) or project.compare_camera
        camera_select.validate_camera(
            [s.videos for s in project.stages if not s.skipped], camera
        )

        for stage in project.stages:
            if wanted_stages is not None and stage.stage_number not in wanted_stages:
                continue
            entry = TrimPlanEntry(
                shooter_slug=slug,
                stage_number=stage.stage_number,
                stage_name=stage_names.get(stage.stage_number, stage.stage_name),
                camera=camera,
            )
            plan.append(_classify(entry, stage, project, shooter_root, camera, force=force))
    return plan


def _classify(
    entry: TrimPlanEntry,
    stage: StageEntry,
    project: MatchProject,
    shooter_root: Path,
    camera: str | None,
    *,
    force: bool,
) -> TrimPlanEntry:
    """Fill in ``eligible`` / ``reason`` / ``substituted_from``. First match wins."""
    if stage.skipped:
        return entry.model_copy(update={"reason": "skipped"})

    chosen, substituted_from = _choose_video(stage, camera)
    entry = entry.model_copy(update={"substituted_from": substituted_from})
    if chosen is None or chosen.beep_time is None:
        return entry.model_copy(update={"reason": "no_beep"})
    if stage.time_seconds <= 0:
        return entry.model_copy(update={"reason": "no_stage_time"})

    try:
        source = project.resolve_video_path(shooter_root, chosen.path)
    except Exception:  # noqa: BLE001 -- any resolution failure is unreachable
        return entry.model_copy(update={"reason": "source_unreachable"})
    if not source.exists():
        return entry.model_copy(update={"reason": "source_unreachable"})

    target = trim_path_for_video(
        project, shooter_root, stage.stage_number, entry.stage_name, chosen
    )
    if target.exists() and not force:
        return entry.model_copy(update={"reason": "already_exported"})

    return entry.model_copy(update={"eligible": True})


def run_trims(
    match_root: Path,
    plan: list[TrimPlanEntry],
    *,
    progress: Callable[[TrimPlanEntry], None] | None = None,
) -> list[TrimResult]:
    """Write the trim for every eligible entry.

    One stage's failure never ends the run: ffmpeg blowing up on stage 7
    must not cost the user the other twenty-three. Failures come back as a
    ``TrimResult`` with no path and the reason recorded.
    """
    results: list[TrimResult] = []
    for entry in plan:
        if not entry.eligible:
            results.append(TrimResult(entry=entry, skip_reasons=[entry.reason or "ineligible"]))
            continue
        if progress is not None:
            progress(entry)
        results.append(_run_one(match_root, entry))
    return results


def _run_one(match_root: Path, entry: TrimPlanEntry) -> TrimResult:
    """Export one stage's trim. Never raises."""
    shooter_root = Match.shooter_root(match_root, entry.shooter_slug)
    project = MatchProject.load(shooter_root)
    stage = project.stage(entry.stage_number)
    chosen, _ = _choose_video(stage, entry.camera)
    if chosen is None or chosen.beep_time is None:
        return TrimResult(entry=entry, skip_reasons=["beep disappeared between plan and run"])

    source = project.resolve_video_path(shooter_root, chosen.path)
    secondaries = []
    if chosen.role != "primary":
        secondaries.append(
            exports.SecondaryExport(
                video_id=chosen.video_id,
                source_path=source,
                beep_time_in_source=chosen.beep_time,
                label=chosen.camera_mount or "Selected cam",
            )
        )

    try:
        result = exports.export_stage(
            request=exports.StageExportRequest(
                stage_number=entry.stage_number,
                write_trim=True,
                write_csv=False,
                write_fcpxml=False,
                write_report=False,
                write_overlay=False,
            ),
            audit_path=project.audit_path(shooter_root) / f"stage{entry.stage_number}.json",
            exports_dir=project.exports_path(shooter_root),
            source_video_path=source if chosen.role == "primary" else None,
            stage_data=StageData(
                stage_number=stage.stage_number,
                stage_name=entry.stage_name,
                time_seconds=stage.time_seconds,
                scorecard_updated_at=stage.scorecard_updated_at,
            ),
            beep_time_in_source=chosen.beep_time,
            pre_buffer_seconds=project.trim_pre_buffer_seconds,
            post_buffer_seconds=project.trim_post_buffer_seconds,
            config=Config(),
            secondaries=secondaries,
        )
    except (exports.StageExportError, OSError, RuntimeError) as exc:
        return TrimResult(entry=entry, skip_reasons=[str(exc)])

    if chosen.role == "primary":
        path = result.trimmed_video_path
    else:
        path = result.secondary_trimmed_paths.get(chosen.video_id)
    return TrimResult(entry=entry, trim_path=path, skip_reasons=list(result.anomalies))
```

Two details to settle while implementing, both verifiable by running the tests:

- `Config` must be imported from `..config` alongside `StageData`; the sketch above omits the import line.
- When the chosen camera is a secondary, `export_stage` needs `source_video_path` for its own primary-trim branch. Passing `None` (as above) skips the primary trim and writes only the secondary, which is what a trim-only run of a chest cam wants. Confirm against `test_run_trims_writes_only_eligible_stages` and extend that test with a secondary-cam case if the assertion does not already cover it.

Import `exports` as a module (`from .ui import exports`) so tests can monkeypatch `match_trims.exports.trim.trim_video`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_match_trims.py -v`
Expected: PASS, all eight tests.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run black --check src tests
git add src/splitsmith/match_trims.py tests/test_match_trims.py
git commit -m "feat(trims): match-wide trim-only planning and execution

plan_trims classifies every shooter-stage as trim-exportable or skipped
without touching media, so --dry-run shows exactly what a real run does.
run_trims drives export_stage with trim-only flags and turns per-stage
failures into reported skips rather than ending the run."
```

---

### Task 7: `splitsmith match trims` CLI verb

**Files:**
- Modify: `src/splitsmith/match_cli.py`
- Test: `tests/test_match_trims_cli.py` (create)

**Interfaces:**
- Consumes: `match_trims.plan_trims`, `match_trims.run_trims`, `match_trims.TrimPlanEntry`, `match_trims.TrimResult` (Task 6); `camera_select.CameraResolutionError` (Task 4).
- Produces: the `match trims` command. Nothing depends on it.

- [ ] **Step 1: Write the failing CLI tests**

Create `tests/test_match_trims_cli.py`, using `typer.testing.CliRunner` the way `tests/test_compare_cli.py` already does:

```python
def test_dry_run_prints_plan_and_writes_nothing(two_shooter_match: Path, monkeypatch) -> None:
    def explode(*_a, **_kw):
        raise AssertionError("--dry-run must not write")

    monkeypatch.setattr(match_trims.exports.trim, "trim_video", explode)
    result = runner.invoke(app, ["match", "trims", str(two_shooter_match), "--dry-run"])

    assert result.exit_code == 0
    assert "no_stage_time" in result.stdout
    assert "no_beep" in result.stdout


def test_reports_camera_substitution(two_shooter_match: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        match_trims.exports.trim, "trim_video", lambda src, dst, **kw: dst.write_bytes(b"t")
    )
    result = runner.invoke(
        app, ["match", "trims", str(two_shooter_match), "--camera", "anders=chest"]
    )

    assert result.exit_code == 0
    assert "chest -> primary" in result.stdout


def test_exit_code_1_when_nothing_written(empty_match: Path) -> None:
    """A match where every stage is ineligible is a failed run, not a no-op."""
    result = runner.invoke(app, ["match", "trims", str(empty_match)])
    assert result.exit_code == 1


def test_partial_run_exits_zero(two_shooter_match: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        match_trims.exports.trim, "trim_video", lambda src, dst, **kw: dst.write_bytes(b"t")
    )
    result = runner.invoke(app, ["match", "trims", str(two_shooter_match)])
    assert result.exit_code == 0
    assert "skipped" in result.stdout.lower()


def test_bad_camera_pair_exits_2(two_shooter_match: Path) -> None:
    result = runner.invoke(
        app, ["match", "trims", str(two_shooter_match), "--camera", "nonsense"]
    )
    assert result.exit_code == 2
```

Reuse the `two_shooter_match` fixture from Task 6 by moving it into `tests/conftest.py` if it is not already there; add an `empty_match` fixture whose single shooter has one stage with no beep.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_match_trims_cli.py -v`
Expected: FAIL -- `No such command 'trims'`.

- [ ] **Step 3: Implement the verb**

In `src/splitsmith/match_cli.py`, add the command and extend the module docstring's command list with a `trims` entry:

```python
@match_app.command("trims")
def trims(
    match_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Match folder to produce trims for."
    ),
    shooter: list[str] = typer.Option(
        [], "--shooter", help="Limit to these shooter slugs (repeatable)."
    ),
    stage: list[int] = typer.Option(
        [], "--stage", help="Limit to these stage numbers (repeatable)."
    ),
    camera: list[str] = typer.Option(
        [],
        "--camera",
        help=(
            "Camera for one shooter as SLUG=VALUE (repeatable). VALUE is a "
            "camera mount ('chest') or a role ('primary', 'secondary'). "
            "Overrides the shooter's persisted compare_camera."
        ),
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan; write nothing."),
    force: bool = typer.Option(False, "--force", help="Re-cut trims that already exist."),
) -> None:
    """Write lossless per-stage trims for every shooter in a match.

    Needs only a confirmed beep and a stage time per stage -- no shot
    detection. Feeds ``splitsmith compare export``, which reads these trims
    to build the beep-aligned grid.
    """
```

Body: reject a non-match path with exit 2 and the same message `compare export` uses for that case; parse `--camera` with the same `SLUG=VALUE` rules as Task 5 (extract the parser into `match_trims` and import it in both CLIs rather than duplicating it -- move `_parse_camera_overrides` there as `parse_camera_overrides` and have `compare/cli.py` import it); call `plan_trims`, catching `CameraResolutionError` -> exit 2; render a `rich.table.Table` with columns Shooter, Stage, Camera, Status; when `--dry-run`, print and return 0; otherwise call `run_trims` and print the outcome per row plus a summary line `N trims written, M skipped, K substitutions`. Exit 1 only when zero trims were written and at least one entry was eligible or attempted.

Substitution rows render as `chest -> primary` in the Camera column.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_match_trims_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Document the verb**

Add a `splitsmith match trims` entry to `docs/COMMANDS.md` beside the other match commands, and a line under the compare section of `SPEC.md` noting that trims can now be produced without shot detection. Match the surrounding prose style; keep it to a few sentences.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests && uv run black --check src tests
uv run pytest tests/test_match_trims_cli.py tests/test_match_trims.py -v
git add src/splitsmith/match_cli.py src/splitsmith/match_trims.py src/splitsmith/compare/cli.py tests/test_match_trims_cli.py tests/conftest.py docs/COMMANDS.md SPEC.md
git commit -m "feat(cli): splitsmith match trims

Batch trim-only export across a match's shooters, with --dry-run, --force,
and per-shooter --camera. Exits non-zero only when nothing was written at
all; a partial run reports its skips and succeeds."
```

---

### Task 8: Stage markers record camera substitutions

**Files:**
- Modify: `src/splitsmith/compare/emitter.py`
- Test: `tests/test_compare_emitter.py`

**Interfaces:**
- Consumes: `CompareStageBundle.substituted` and `.camera_mount` (Task 5).
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_compare_emitter.py`:

```python
def test_stage_marker_names_camera_substitutions(tmp_path: Path) -> None:
    """A substituted tile is visible on the timeline, not just in a
    terminal the user has already closed."""
    xml = _emit_with_bundles(
        tmp_path,
        substitutions={"Mathias": True},
    )
    markers = [m.get("value") for m in xml.iter("marker")]
    assert any("Mathias: primary" in (m or "") for m in markers)


def test_stage_marker_unchanged_without_substitutions(tmp_path: Path) -> None:
    xml = _emit_with_bundles(tmp_path, substitutions={})
    markers = [m.get("value") for m in xml.iter("marker")]
    assert all("primary" not in (m or "") for m in markers)
```

Write `_emit_with_bundles(tmp_path, *, substitutions)` on top of whatever bundle-construction helper `tests/test_compare_emitter.py` already uses, setting `substituted=True` on the named shooters' stage bundles.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_compare_emitter.py -k marker -v`
Expected: the substitution test FAILS -- the marker reads `Stage 1 -- <name>` with no camera note.

- [ ] **Step 3: Implement the marker suffix**

In `emitter.py`, where the outer spine's `<marker>` value is built, append substitution notes:

```python
    # Substituted tiles are named in the marker so the editor can see, on the
    # timeline, that one shooter's angle changed for this stage.
    substitutions = sorted(
        bundle.label for bundle, stage in stages_present if stage.substituted
    )
    marker_value = f"Stage {stage_number} -- {stage_name}"
    if substitutions:
        noted = ", ".join(f"{label}: primary" for label in substitutions)
        marker_value = f"{marker_value} ({noted})"
```

Adapt the iteration variable names to the emitter's existing locals; do not restructure the surrounding loop.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_compare_emitter.py -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run black --check src tests
git add src/splitsmith/compare/emitter.py tests/test_compare_emitter.py
git commit -m "feat(compare): name camera substitutions in the stage marker"
```

---

### Task 9: SPA "Trims only" mode with a camera picker

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Export.tsx`
- Modify: `src/splitsmith/ui_static/src/lib/api.ts`
- Modify: `src/splitsmith/ui/server.py` (PATCH endpoint for `compare_camera`)
- Test: `tests/test_ui_server.py`

**Interfaces:**
- Consumes: `exports.export_stage` tolerating a missing audit (Task 1); `MatchProject.compare_camera` (Task 5).
- Produces: `PATCH /api/shooters/{slug}/compare-camera` accepting `{"camera": str | null}`.

- [ ] **Step 1: Write the failing endpoint test**

Add to `tests/test_ui_server.py`:

```python
def test_set_compare_camera_persists(client, seeded_project_root: Path) -> None:
    resp = client.patch("/api/shooters/default/compare-camera", json={"camera": "chest"})
    assert resp.status_code == 200
    proj = MatchProject.load(seeded_project_root)
    assert proj.compare_camera == "chest"


def test_set_compare_camera_rejects_unknown_selector(client) -> None:
    """Typing 'chset' must fail here, not silently export every tile from
    the primary."""
    resp = client.patch("/api/shooters/default/compare-camera", json={"camera": "backpack"})
    assert resp.status_code == 400
    assert "backpack" in resp.json()["detail"]


def test_clear_compare_camera(client, seeded_project_root: Path) -> None:
    client.patch("/api/shooters/default/compare-camera", json={"camera": "chest"})
    resp = client.patch("/api/shooters/default/compare-camera", json={"camera": None})
    assert resp.status_code == 200
    assert MatchProject.load(seeded_project_root).compare_camera is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ui_server.py -k compare_camera -v`
Expected: FAIL with 404 -- no such route.

- [ ] **Step 3: Implement the endpoint**

In `server.py`, beside the other per-shooter PATCH endpoints:

```python
class CompareCameraRequest(BaseModel):
    """Body for PATCH /api/shooters/{slug}/compare-camera.

    ``camera`` is a camera mount ("chest") or a role ("primary",
    "secondary"); ``None`` clears the selection back to the primary.
    """

    camera: str | None = None


@app.patch("/api/shooters/{slug}/compare-camera")
def set_compare_camera(slug: str, req: CompareCameraRequest) -> JSONResponse:
    """Persist which camera this shooter contributes to grids and trims.

    Validated against the shooter's actual videos so a typo fails here
    rather than silently exporting every tile from the primary.
    """
    project = state.shooter_project(slug)
    try:
        camera_select.validate_camera(
            [stage.videos for stage in project.stages if not stage.skipped], req.camera
        )
    except camera_select.CameraResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project.compare_camera = req.camera
    project.save(state.shooter_root(slug))
    return JSONResponse(project.model_dump(mode="json"))
```

- [ ] **Step 4: Run to verify the endpoint tests pass**

Run: `uv run pytest tests/test_ui_server.py -k compare_camera -v`
Expected: PASS.

- [ ] **Step 5: Add the API client methods**

In `src/splitsmith/ui_static/src/lib/api.ts`, beside the existing shooter methods:

```ts
  async setCompareCamera(slug: string, camera: string | null): Promise<ProjectResponse> {
    return this.patch(`/api/shooters/${slug}/compare-camera`, { camera });
  },
```

Follow the file's existing request-helper convention rather than calling `fetch` directly; add `compare_camera?: string | null` to the project type that mirrors `MatchProject`.

- [ ] **Step 6: Add the mode and picker to the Export page**

In `Export.tsx`:

1. Widen the `mode` union to `"single" | "compare" | "trims"`.
2. Add a third `ModeOption` after the compare one:

```tsx
<ModeOption
  selected={mode === "trims"}
  onSelect={() => setMode("trims")}
  title="Trims only"
  body="Lossless per-stage trims, no shot detection. Feeds the compare grid."
/>
```

3. When `mode === "trims"`, render a camera `<select>` populated from the distinct `camera_mount` values across the shooter's stages plus `primary` and `secondary`, defaulting to the project's `compare_camera`. On change, call `api.setCompareCamera(slug, value || null)`.
4. In `submitExport`, when `mode === "trims"`, queue one `api.exportStage` job per selected stage with `{ write_trim: true, write_csv: false, write_fcpxml: false, write_report: false, write_overlay: false }`. Reuse the existing per-stage submission loop; do not add a new endpoint.
5. Hide the padding, transition, title and overlay controls in this mode -- none apply to a bare trim.

- [ ] **Step 7: Verify the SPA builds and renders**

```bash
corepack pnpm --dir src/splitsmith/ui_static build
```
Expected: build succeeds with no type errors.

Then start the dev server and confirm by hand: the third mode appears, the camera picker lists the shooter's mounts, and selecting stages plus Generate queues trim-only jobs that appear in JobsPanel.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check src tests && uv run black --check src tests
git add src/splitsmith/ui/server.py src/splitsmith/ui_static/src tests/test_ui_server.py
git commit -m "feat(ui): trims-only export mode with per-shooter camera picker

Queues trim-only jobs through the existing per-stage export endpoint, so
JobsPanel progress works unchanged. compare_camera is validated against the
shooter's real videos on write."
```

---

## Final verification

- [ ] Run the whole suite: `uv run pytest -v`
- [ ] Lint: `uv run ruff check src tests && uv run black --check src tests`
- [ ] End-to-end against a real match folder, four shooters, no shot detection run:

```bash
uv run splitsmith match trims ~/splitsmith/matches/<match> --dry-run
uv run splitsmith match trims ~/splitsmith/matches/<match>
uv run splitsmith compare export ~/splitsmith/matches/<match> \
    --audio-from mathias --camera anders=chest -o /tmp/composite.fcpxml
```

Expected: a 2x2 grid FCPXML whose tiles are beep-aligned, only the audio-source shooter unmuted, black filler where a stage was skipped, and stage markers naming any camera substitutions. Import into Final Cut to confirm the tiles line up on the beep.
