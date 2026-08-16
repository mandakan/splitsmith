# Export-run record (#629) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist a durable record of each export run -- run grouping, duration, selected formats, anomaly count -- and surface it as an export history on the Export page, closing the second half of #629.

**Architecture:** A pure `export_runs` module owns the record shape and the append rule. Persistence goes through a new `AppState.load_export_runs`/`save_export_runs` seam that mirrors `load_audit`/`save_audit` exactly: `state_docs` (new `doc_kind = "export_runs"`) on hosted, a `<shooter_root>/export_runs.json` file on desktop. The two export job bodies write a record on success. A new `GET /api/shooters/{slug}/exports/runs` route serves it, and the export routes move to a new `ui/exports_api.py` router on the way past.

**Tech Stack:** Python 3.11+, pydantic v2, FastAPI, SQLAlchemy async (hosted only), pytest; React 18 + TypeScript + vitest + React Testing Library for the SPA.

**Spec:** `docs/superpowers/specs/2026-08-16-export-run-record-design.md` -- read it before Task 1. The retention decision it argues from is the 2026-08-15 comment on issue #629.

## Global Constraints

- Python 3.11+, type hints everywhere. `pathlib.Path` for paths, never strings. f-strings.
- `uv` for everything: `uv run pytest`, `uv run black`, `uv run ruff`. Never `pip`.
- Black line length 110. Run `uv run black src tests` and `uv run ruff check src tests` before every commit -- CI has a format gate and hand-written test snippets routinely exceed 110 cols.
- Imports: stdlib, third-party, local, separated by blank lines. No relative imports beyond a single dot.
- Pydantic models for anything crossing a module boundary. No dicts of unknown shape.
- **Do not add dependencies.** Everything here uses what is already installed.
- `src/splitsmith/ui/exports_api.py` must never import `splitsmith.ui.server` -- server imports it. A cycle here fails at import time on every install.
- `splitsmith.ui.server` must stay importable on a slim local install with no hosted DB extras. Anything touching `splitsmith.db` imports lazily inside a function. `tests/test_local_mode_no_hosted_imports.py` is the standing guard.
- The test suite runs under xdist by default. Use `-n0` when running a single test file; new tests must not depend on execution order or share mutable state outside `tmp_path`.
- `uv run` rewrites `uv.lock`. Never `git add -A`; stage files by name, and never commit `uv.lock` as part of this work.

---

### Task 1: The pure record module

**Files:**
- Create: `src/splitsmith/export_runs.py`
- Test: `tests/test_export_runs.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `SCHEMA_VERSION: int`
  - `ArtifactKind = Literal["trim", "secondary_trim", "csv", "fcpxml", "report", "overlay", "sidecar", "match_video"]`
  - `RunKind = Literal["stage", "match"]`
  - `class ExportArtifact(BaseModel)`: `filename: str`, `kind: ArtifactKind`
  - `class ExportRun(BaseModel)`: `run_id: str`, `kind: RunKind`, `finished_at: datetime`, `duration_seconds: float`, `stage_numbers: list[int]`, `formats: list[str]`, `anomaly_count: int`, `artifacts: list[ExportArtifact]`
  - `class ExportRunLog(BaseModel)`: `schema_version: int = SCHEMA_VERSION`, `runs: list[ExportRun] = []`
  - `new_run_id() -> str`
  - `load_log(doc: dict | None) -> ExportRunLog`
  - `append_run(doc: dict | None, run: ExportRun) -> dict`
  - `stage_run_formats(*, trim: bool, csv: bool, fcpxml: bool, report: bool, overlay: bool) -> list[str]`
  - `match_run_formats(*, output_format: str, youtube_sidecar: bool) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_export_runs.py`:

```python
"""Tests for the pure export-run record module (#629)."""

from __future__ import annotations

from datetime import UTC, datetime

from splitsmith import export_runs


def _run(run_id: str = "a" * 32, *, stage: int = 1) -> export_runs.ExportRun:
    return export_runs.ExportRun(
        run_id=run_id,
        kind="stage",
        finished_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        duration_seconds=12.5,
        stage_numbers=[stage],
        formats=["trim", "csv"],
        anomaly_count=0,
        artifacts=[export_runs.ExportArtifact(filename="stage1_x_trimmed.mp4", kind="trim")],
    )


def test_append_run_on_absent_doc_starts_a_log() -> None:
    doc = export_runs.append_run(None, _run())
    log = export_runs.load_log(doc)
    assert log.schema_version == export_runs.SCHEMA_VERSION
    assert [r.run_id for r in log.runs] == ["a" * 32]


def test_append_run_is_newest_first() -> None:
    doc = export_runs.append_run(None, _run("a" * 32, stage=1))
    doc = export_runs.append_run(doc, _run("b" * 32, stage=2))
    assert [r.run_id for r in export_runs.load_log(doc).runs] == ["b" * 32, "a" * 32]


def test_load_log_skips_an_unparseable_run_and_keeps_the_rest() -> None:
    """A malformed entry must not cost the whole history, and must not
    raise -- an export is never allowed to fail because bookkeeping is
    unreadable."""
    doc = export_runs.append_run(None, _run())
    doc["runs"].append({"run_id": "broken"})  # missing every required field
    log = export_runs.load_log(doc)
    assert [r.run_id for r in log.runs] == ["a" * 32]


def test_load_log_tolerates_a_doc_of_the_wrong_shape() -> None:
    assert export_runs.load_log(None).runs == []
    assert export_runs.load_log({}).runs == []
    assert export_runs.load_log({"runs": "not-a-list"}).runs == []


def test_new_run_id_is_unique() -> None:
    assert export_runs.new_run_id() != export_runs.new_run_id()


def test_stage_run_formats_lists_only_what_was_requested_in_pipeline_order() -> None:
    assert export_runs.stage_run_formats(
        trim=True, csv=False, fcpxml=True, report=True, overlay=False
    ) == ["trim", "fcpxml", "report"]
    assert export_runs.stage_run_formats(
        trim=False, csv=False, fcpxml=False, report=False, overlay=False
    ) == []


def test_match_run_formats_carries_the_output_format_and_sidecar() -> None:
    assert export_runs.match_run_formats(output_format="mp4", youtube_sidecar=True) == [
        "mp4",
        "youtube-sidecar",
    ]
    assert export_runs.match_run_formats(output_format="fcpxml", youtube_sidecar=False) == ["fcpxml"]
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `uv run pytest tests/test_export_runs.py -n0 -q`
Expected: collection error -- `ModuleNotFoundError: No module named 'splitsmith.export_runs'`.

- [ ] **Step 3: Write the module**

Create `src/splitsmith/export_runs.py`:

```python
"""The durable record of one export run (#629, second half).

An export's *files* are already discoverable from persistent state --
``MatchProject.export_overview`` and ``match_export_files`` list them, and
``download_export_file`` serves them (#858). Four things are not derivable
from a directory listing: which deliverables came out of one invocation,
how long that invocation took, which formats the user selected, and how
many anomalies it reported. This module is the shape of that record.

Pure: no I/O, no storage seam, no FastAPI. Persistence is the caller's
problem -- ``AppState.load_export_runs`` / ``save_export_runs`` pick
``state_docs`` or a local file, and the export job bodies do the writing.

**Reads never raise.** ``load_log`` drops an entry it cannot validate and
keeps the rest. An export must not fail, and a history page must not 500,
because a bookkeeping document is malformed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

#: What an artefact is, for the history row's icon + wording. ``trim`` is
#: the primary lossless cut; ``secondary_trim`` a per-cam one;
#: ``match_video`` the stitched match render when the run asked for mp4.
ArtifactKind = Literal[
    "trim",
    "secondary_trim",
    "csv",
    "fcpxml",
    "report",
    "overlay",
    "sidecar",
    "match_video",
]

RunKind = Literal["stage", "match"]

#: Fixed order for ``stage_run_formats`` -- the pipeline's own order, so
#: two runs asking for the same set always compare equal as lists.
_STAGE_FORMAT_ORDER = ("trim", "csv", "fcpxml", "report", "overlay")


class ExportArtifact(BaseModel):
    """One file a run produced.

    ``filename`` is a basename under the shooter's ``exports/`` dir, never
    a path: that is the key ``download_export_file`` takes, and it is what
    makes a record written by a hosted worker meaningful to the API
    container that serves the link.
    """

    filename: str
    kind: ArtifactKind


class ExportRun(BaseModel):
    """One export invocation.

    ``formats`` is what was *requested*; ``artifacts`` is what was
    *written*. Both are kept on purpose -- "asked for an overlay, got
    none" is exactly what a user comes back to the history to find out.

    ``duration_seconds`` is wall-clock time for the run. Note that
    ``match_exports.MatchExportResult.duration_seconds`` means something
    else entirely (the timeline length of the stitched output); do not
    wire that into this field.
    """

    run_id: str
    kind: RunKind
    finished_at: datetime
    duration_seconds: float
    stage_numbers: list[int]
    formats: list[str]
    anomaly_count: int
    artifacts: list[ExportArtifact] = Field(default_factory=list)


class ExportRunLog(BaseModel):
    """Every run for one shooter in one match, newest first."""

    schema_version: int = SCHEMA_VERSION
    runs: list[ExportRun] = Field(default_factory=list)


def new_run_id() -> str:
    """Unique id for one run.

    uuid4 hex, not a ULID: ordering comes from ``finished_at``, and the
    ulid package is a hosted-only extra while runs are recorded on slim
    local installs too. Same reasoning as ``server._new_event_id``.
    """
    return uuid.uuid4().hex


def load_log(doc: dict | None) -> ExportRunLog:
    """Parse a stored log, skipping entries that no longer validate.

    Never raises. A doc that is not a dict, or whose ``runs`` is not a
    list, yields an empty log; an individual malformed run is dropped and
    its siblings survive.
    """
    if not isinstance(doc, dict):
        return ExportRunLog()
    raw = doc.get("runs")
    if not isinstance(raw, list):
        return ExportRunLog()
    runs: list[ExportRun] = []
    for entry in raw:
        try:
            runs.append(ExportRun.model_validate(entry))
        except Exception:  # noqa: BLE001 -- a bad entry costs itself, nothing else
            continue
    version = doc.get("schema_version")
    return ExportRunLog(
        schema_version=version if isinstance(version, int) else SCHEMA_VERSION,
        runs=runs,
    )


def append_run(doc: dict | None, run: ExportRun) -> dict:
    """Return ``doc`` with ``run`` prepended, as a plain JSON-ready dict.

    Newest-first is the stored order, so a reader never sorts. No cap on
    the number of runs: the retention decision on #629 keeps run records
    indefinitely, and a run is a few hundred bytes.
    """
    log = load_log(doc)
    log.runs.insert(0, run)
    log.schema_version = SCHEMA_VERSION
    return log.model_dump(mode="json")


def stage_run_formats(
    *, trim: bool, csv: bool, fcpxml: bool, report: bool, overlay: bool
) -> list[str]:
    """The formats a per-stage export requested, in pipeline order.

    Takes bare booleans rather than the request model so this module stays
    free of any dependency on the HTTP layer.
    """
    selected = {
        "trim": trim,
        "csv": csv,
        "fcpxml": fcpxml,
        "report": report,
        "overlay": overlay,
    }
    return [name for name in _STAGE_FORMAT_ORDER if selected[name]]


def match_run_formats(*, output_format: str, youtube_sidecar: bool) -> list[str]:
    """The formats a match export requested: its output format, plus the
    YouTube sidecar when one was asked for."""
    out = [output_format]
    if youtube_sidecar:
        out.append("youtube-sidecar")
    return out
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_export_runs.py -n0 -q`
Expected: 7 passed.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run black src/splitsmith/export_runs.py tests/test_export_runs.py
uv run ruff check src/splitsmith/export_runs.py tests/test_export_runs.py
git add src/splitsmith/export_runs.py tests/test_export_runs.py
git commit -m "feat(exports): the export-run record shape (#629)"
```

---

### Task 2: `state_docs` gets an `export_runs` kind

**Files:**
- Modify: `src/splitsmith/db/project_state.py` (add the kind constant + two wrappers next to the audit ones, around line 141-157)
- Test: `tests/test_project_state_store.py`

**Interfaces:**
- Consumes: `ExportRunLog` doc dicts from Task 1 (opaque here -- the store never parses).
- Produces:
  - `ProjectStateStore.load_export_runs(match_id: str, slug: str) -> tuple[dict | None, int]`
  - `ProjectStateStore.save_export_runs(match_id: str, slug: str, doc: dict, *, expected_version: int) -> int`

`ProjectStateStore`'s class docstring states the discipline: *if you add a method here, add an isolation test for it too*. That is not optional -- every statement in this store filters on `user_id`, and the tests are what hold the line.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_project_state_store.py`. Match the file's existing fixture style for building a store (read the top of the file first and reuse its engine/user helpers rather than inventing new ones; the names below assume the existing `store`-building helper -- adapt to what is actually there):

```python
@pytest.mark.asyncio
async def test_export_runs_round_trip_and_version_bump(store_factory) -> None:
    store = await store_factory("runs-owner@test.se")
    doc, version = await store.load_export_runs("m1", "me")
    assert (doc, version) == (None, 0)

    v1 = await store.save_export_runs("m1", "me", {"schema_version": 1, "runs": []}, expected_version=0)
    assert v1 == 1
    v2 = await store.save_export_runs(
        "m1", "me", {"schema_version": 1, "runs": [{"run_id": "x"}]}, expected_version=1
    )
    assert v2 == 2
    doc, version = await store.load_export_runs("m1", "me")
    assert version == 2
    assert doc["runs"] == [{"run_id": "x"}]


@pytest.mark.asyncio
async def test_export_runs_is_isolated_between_users(store_factory) -> None:
    mine = await store_factory("runs-mine@test.se")
    theirs = await store_factory("runs-theirs@test.se")
    await mine.save_export_runs("m1", "me", {"schema_version": 1, "runs": []}, expected_version=0)
    assert await theirs.load_export_runs("m1", "me") == (None, 0)


@pytest.mark.asyncio
async def test_export_runs_does_not_collide_with_the_project_doc(store_factory) -> None:
    """Both are (match_id, slug, stage NULL); only ``doc_kind`` separates
    them. A wrong kind constant would make one INSERT clobber the other's
    identity and raise a conflict here."""
    store = await store_factory("runs-kinds@test.se")
    await store.save_project("m1", "me", {"name": "p"}, expected_version=0)
    await store.save_export_runs("m1", "me", {"schema_version": 1, "runs": []}, expected_version=0)
    proj, _ = await store.load_project("m1", "me")
    assert proj == {"name": "p"}


@pytest.mark.asyncio
async def test_delete_shooter_sweeps_the_export_run_log(store_factory) -> None:
    store = await store_factory("runs-delete@test.se")
    await store.save_export_runs("m1", "me", {"schema_version": 1, "runs": []}, expected_version=0)
    assert await store.delete_shooter("m1", "me") >= 1
    assert await store.load_export_runs("m1", "me") == (None, 0)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_project_state_store.py -n0 -q -k export_runs`
Expected: `AttributeError: 'ProjectStateStore' object has no attribute 'save_export_runs'`.

- [ ] **Step 3: Add the kind and the wrappers**

In `src/splitsmith/db/project_state.py`, extend the kind block:

```python
# The doc kinds. ``slug`` is NULL for ``match``; ``stage_number`` is
# non-NULL only for ``audit``. ``export_runs`` is per-shooter like
# ``project`` and is separated from it by ``doc_kind`` alone.
_KIND_MATCH = "match"
_KIND_PROJECT = "project"
_KIND_AUDIT = "audit"
_KIND_EXPORT_RUNS = "export_runs"
```

and add the wrappers immediately after the audit pair:

```python
    # -- per-shooter export-run log (slug set, stage NULL) -------------

    async def load_export_runs(self, match_id: str, slug: str) -> tuple[dict | None, int]:
        """The shooter's export history doc + version (#629).

        Opaque here: the store never parses it. Shape and the
        skip-a-bad-entry rule live in :mod:`splitsmith.export_runs`.
        """
        return await self._load(match_id, _KIND_EXPORT_RUNS, slug=slug, stage_number=None)

    async def save_export_runs(
        self, match_id: str, slug: str, doc: dict, *, expected_version: int
    ) -> int:
        return await self._save(
            match_id,
            _KIND_EXPORT_RUNS,
            doc,
            expected_version=expected_version,
            slug=slug,
            stage_number=None,
        )
```

`delete_match` and `delete_shooter` need no change: the first sweeps on `match_id` alone, the second on `(match_id, slug)`, and this doc carries both. The fourth test above is what proves that rather than assuming it.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/test_project_state_store.py -n0 -q`
Expected: all pass, including the pre-existing tests.

- [ ] **Step 5: Format, lint, commit**

```bash
uv run black src/splitsmith/db/project_state.py tests/test_project_state_store.py
uv run ruff check src/splitsmith/db/project_state.py tests/test_project_state_store.py
git add src/splitsmith/db/project_state.py tests/test_project_state_store.py
git commit -m "feat(db): an export_runs state doc kind (#629)"
```

---

### Task 3: The `AppState` seam and the append helper

**Files:**
- Modify: `src/splitsmith/ui/server.py` -- add `EXPORT_RUNS_FILE` + `_record_export_run` near `_save_audit_with_remerge` (~line 572-602), and the two `AppState` methods next to `save_audit` (~line 1946-2005)
- Test: `tests/test_export_run_record.py` (new file)

**Interfaces:**
- Consumes: `export_runs.append_run`, `export_runs.ExportRun` (Task 1); `ProjectStateStore.load_export_runs`/`save_export_runs` (Task 2).
- Produces:
  - `splitsmith.ui.server.EXPORT_RUNS_FILE: str` (= `"export_runs.json"`)
  - `AppState.load_export_runs(slug: str) -> tuple[dict | None, int]`
  - `AppState.save_export_runs(slug: str, doc: dict, *, version: int) -> int`
  - `splitsmith.ui.server._record_export_run(state: AppState, slug: str, run: ExportRun) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_export_run_record.py`:

```python
"""The export-run record's persistence seam (#629).

Local mode writes ``<shooter_root>/export_runs.json``; hosted writes a
``state_docs`` row. The append re-loads on a version conflict so two
concurrent export jobs never lose a run, and it never fails the export.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from splitsmith import export_runs
from splitsmith.ui import server as server_mod

from .test_ui_server import _seed_match_export_project


def _run(run_id: str, stage: int) -> export_runs.ExportRun:
    return export_runs.ExportRun(
        run_id=run_id,
        kind="stage",
        finished_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        duration_seconds=1.0,
        stage_numbers=[stage],
        formats=["trim"],
        anomaly_count=0,
        artifacts=[],
    )


@contextmanager
def _match_context(project_root: Path, match_id: str | None = None):
    """Bind the ContextVars the alias middleware sets per request.

    ``AppState.shooter_root`` reads ``current_match_root`` and the hosted
    branch of every state accessor reads ``current_match_id``; neither has
    a process-level fallback. A test that calls a state accessor outside a
    request has to set them itself.
    """
    tok_root = server_mod.current_match_root.set(project_root)
    tok_id = server_mod.current_match_id.set(match_id)
    try:
        yield
    finally:
        server_mod.current_match_root.reset(tok_root)
        server_mod.current_match_id.reset(tok_id)


def test_local_mode_appends_to_a_file_in_the_shooter_root(tmp_path: Path) -> None:
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    shooter_root = project_root / "shooters" / "me"

    with _match_context(project_root):
        server_mod._record_export_run(state, "me", _run("a" * 32, 1))
        server_mod._record_export_run(state, "me", _run("b" * 32, 2))

    doc = json.loads((shooter_root / "export_runs.json").read_text(encoding="utf-8"))
    assert [r["run_id"] for r in doc["runs"]] == ["b" * 32, "a" * 32]
    # Never inside exports/ -- everything there is offered as a deliverable.
    assert not (shooter_root / "exports" / "export_runs.json").exists()


def test_a_write_failure_does_not_raise(tmp_path: Path, monkeypatch, caplog) -> None:
    """The deliverables are the product; the history is bookkeeping. A
    failed record write logs and returns -- a red job row over files that
    wrote correctly is a worse lie than a missing history line."""
    import logging

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(type(state), "save_export_runs", boom, raising=True)
    with caplog.at_level(logging.WARNING), _match_context(project_root):
        server_mod._record_export_run(state, "me", _run("c" * 32, 1))  # must not raise
    assert "export run record" in caplog.text
```

Add `from contextlib import contextmanager` to the test module's imports.

Plus the hosted-mode test, following the idiom already used by
`test_beep_confirm_seeds_the_stub_in_hosted_state_docs` in
`tests/test_ui_server.py` (read it first -- it builds an in-memory SQLite
`ProjectStateStore`, seeds the match doc under the same `match_id` the
alias middleware will set, and injects it into `_state._project_state`):

```python
def test_hosted_mode_appends_to_state_docs(tmp_path: Path) -> None:
    import asyncio as _asyncio

    from splitsmith import match_model as _match_model
    from splitsmith.db import Base, ProjectStateStore, User, create_engine, sessionmaker

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    sf = sessionmaker(engine)

    async def _setup_db() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            user = User(email="export-runs@test.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    uid = _asyncio.run(_setup_db())
    store = ProjectStateStore(sf, user_id=uid)

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    local_match = _match_model.Match.load(project_root)
    match_id = local_match.match_id
    _asyncio.run(store.save_match(match_id, local_match.model_dump(mode="json"), expected_version=0))

    state = client.app.state.splitsmith_state
    old = state._project_state
    state._project_state = store
    try:
        with _match_context(project_root, match_id):
            server_mod._record_export_run(state, "me", _run("d" * 32, 1))
    finally:
        state._project_state = old

    doc, version = _asyncio.run(store.load_export_runs(match_id, "me"))
    assert version == 1
    assert [r["run_id"] for r in doc["runs"]] == ["d" * 32]
    # Local mode must not have been used as a fallback.
    assert not (project_root / "shooters" / "me" / "export_runs.json").exists()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_export_run_record.py -n0 -q`
Expected: `AttributeError: module 'splitsmith.ui.server' has no attribute '_record_export_run'`.

- [ ] **Step 3: Implement the seam**

`current_match_root` and `current_match_id` are `ContextVar`s defined at
`server.py:1385-1386` and set by the alias middleware per request. The
test helper above binds them directly; **do not add a context-manager
helper to production code for the tests' benefit.**

Add near `_AUDIT_SAVE_MAX_ATTEMPTS` (~line 522):

```python
# Filename of the desktop export-run log, in the shooter root. NOT in
# exports/: everything in that directory is listed by
# ``MatchProject._stored_exports`` and offered to the user as a
# deliverable, and the history is not a deliverable.
EXPORT_RUNS_FILE = "export_runs.json"

# How many times ``_record_export_run`` re-loads + re-appends when a
# concurrent export job wins the version race. Batch export runs several
# stages at once against one document, so contention is the normal case,
# not the exotic one.
_EXPORT_RUN_SAVE_MAX_ATTEMPTS = 4
```

Add after `_save_audit_with_remerge`:

```python
def _record_export_run(state: AppState, slug: str, run: export_runs.ExportRun) -> None:
    """Append one run to the shooter's export history. Never raises.

    Re-loads and re-appends on an optimistic-lock conflict rather than
    retrying the same write: a conflict means a concurrent export job's
    run landed first, and re-appending onto the winner's doc keeps both.
    Blind overwrite would silently drop a run.

    Every other failure -- store unavailable, disk full, retries exhausted
    -- logs at WARNING and returns. The deliverables are the product and
    they are already written by the time this is called; failing the job
    over bookkeeping would report a successful export as broken.
    """
    conflict_excs = _state_conflict_excs()
    for _attempt in range(_EXPORT_RUN_SAVE_MAX_ATTEMPTS):
        try:
            doc, version = state.load_export_runs(slug)
            state.save_export_runs(slug, export_runs.append_run(doc, run), version=version)
            return
        except conflict_excs:
            continue
        except Exception as exc:  # noqa: BLE001 -- see docstring
            logger.warning("export run record: not written for %s: %s", slug, exc)
            return
    logger.warning(
        "export run record: lost %d version races for %s; run %s not recorded",
        _EXPORT_RUN_SAVE_MAX_ATTEMPTS,
        slug,
        run.run_id,
    )
```

Import the module at the top of `server.py` with the other local imports: `from .. import export_runs`.

Add the two `AppState` methods immediately after `delete_audit`:

```python
    def load_export_runs(self, slug: str) -> tuple[dict | None, int]:
        """The shooter's export history doc + version (#629).

        Hosted: ``state_docs``. Local: ``<shooter_root>/export_runs.json``
        (version always 0 -- no locking on files). Unlike
        :meth:`load_audit`, an unreadable document degrades to "no
        history" rather than a 500: this is bookkeeping, and it must not
        be able to break a page that would otherwise render.
        """
        mid = current_match_id.get()
        store = self.project_state
        if store is not None and mid is not None:
            return run_sync(store.load_export_runs(mid, slug))
        path = self.shooter_root(slug) / EXPORT_RUNS_FILE
        if not path.exists():
            return None, 0
        try:
            return json.loads(path.read_text(encoding="utf-8")), 0
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("export run record: unreadable at %s: %s", path, exc)
            return None, 0

    def save_export_runs(self, slug: str, doc: dict, *, version: int) -> int:
        """Persist the export history doc; return the new version.

        Hosted: ``state_docs`` under optimistic locking, same contract as
        :meth:`save_audit`. Local: atomic ``.tmp`` -> rename (returns 0).
        """
        mid = current_match_id.get()
        store = self.project_state
        if store is not None and mid is not None:
            return run_sync(store.save_export_runs(mid, slug, doc, expected_version=version))
        path = self.shooter_root(slug) / EXPORT_RUNS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
        except OSError:
            if tmp.exists():
                tmp.unlink()
            raise
        return 0
```

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/test_export_run_record.py -n0 -q`
Expected: 3 passed.

- [ ] **Step 5: Prove the local-mode assertion can fail**

Delete the `tmp.replace(path)` line, re-run, and confirm the first test fails on the missing file. Restore it. A test that passes against the un-implemented code is worth nothing, and this repo has shipped several (see the review practice section of CLAUDE.md).

- [ ] **Step 6: Format, lint, commit**

```bash
uv run black src/splitsmith/ui/server.py tests/test_export_run_record.py
uv run ruff check src/splitsmith/ui/server.py tests/test_export_run_record.py
uv run pytest tests/test_local_mode_no_hosted_imports.py -n0 -q
git add src/splitsmith/ui/server.py tests/test_export_run_record.py
git commit -m "feat(exports): persist an export-run log per shooter (#629)"
```

---

### Task 4: The per-stage export job writes a record

**Files:**
- Modify: `src/splitsmith/ui/server.py` -- `_run_export_for_stage`, immediately before `handle.set_result(...)` (~line 3644)
- Test: `tests/test_export_run_record.py`

**Interfaces:**
- Consumes: `_record_export_run`, `export_runs.ExportRun`, `export_runs.stage_run_formats`, `export_runs.new_run_id`.
- Produces: a stored run with `kind="stage"` after every successful `export` job.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_export_run_record.py`. Read `test_export_result_reports_secondary_trim_filenames` in `tests/test_ui_server.py` first and reuse its shape -- `_seed_match_export_project`, a monkeypatched `trim.trim_video`, `_wait_for_job`:

```python
def test_a_stage_export_records_a_run(tmp_path: Path, monkeypatch) -> None:
    from splitsmith import trim

    from .test_ui_server import _wait_for_job

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)

    def fake_trim_video(source, output_path, **kwargs):  # type: ignore[no-untyped-def]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"TRIMMED")
        return trim.TrimResult(output_path=Path(output_path), start_time=0.0, end_time=10.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)
    assert client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 10.0}).status_code == 200

    resp = client.post(
        "/api/shooters/me/stages/1/export",
        json={
            "write_trim": True,
            "write_csv": False,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": False,
        },
    )
    assert resp.status_code == 200, resp.text
    assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"

    doc = json.loads(
        (project_root / "shooters" / "me" / "export_runs.json").read_text(encoding="utf-8")
    )
    assert len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["kind"] == "stage"
    assert run["stage_numbers"] == [1]
    # Requested formats, not produced files: the run asked for a trim only.
    assert run["formats"] == ["trim"]
    assert run["anomaly_count"] == 0
    assert [a["kind"] for a in run["artifacts"]] == ["trim"]
    assert run["artifacts"][0]["filename"].endswith("_trimmed.mp4")
    assert "/" not in run["artifacts"][0]["filename"]
    # Wall clock, not a timeline length -- and a real measurement, so it is
    # positive and small for a mocked trim.
    assert 0.0 < run["duration_seconds"] < 60.0


def test_a_failed_stage_export_records_nothing(tmp_path: Path, monkeypatch) -> None:
    """The record describes a completed run. A job that raises (here: the
    trim writer produces no clip at all) must leave no history line."""
    from splitsmith import trim

    from .test_ui_server import _wait_for_job

    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)

    def failing_trim(source, output_path, **kwargs):  # type: ignore[no-untyped-def]
        raise trim.FFmpegError("ffmpeg exploded")

    monkeypatch.setattr(trim, "trim_video", failing_trim)
    assert client.post("/api/shooters/me/stages/1/time", json={"time_seconds": 10.0}).status_code == 200

    resp = client.post(
        "/api/shooters/me/stages/1/export",
        json={
            "write_trim": True,
            "write_csv": False,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": False,
        },
    )
    assert _wait_for_job(client, resp.json()["id"])["status"] == "failed"
    assert not (project_root / "shooters" / "me" / "export_runs.json").exists()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_export_run_record.py -n0 -q -k stage_export`
Expected: the first fails on `FileNotFoundError` for `export_runs.json`; the second already passes (nothing writes a record yet) -- note that in the commit message, and re-check it after Step 3 since that is when it becomes meaningful.

- [ ] **Step 3: Write the record**

In `_run_export_for_stage`, after the `_name` helper is defined and immediately before `handle.set_result(...)`:

```python
        # Durable record of this run (#629). Everything above is already
        # written and pushed, so a failure here must not fail the job --
        # ``_record_export_run`` swallows and logs. Placed after the
        # "nothing was written" raise so a failed run leaves no history.
        run_artifacts: list[export_runs.ExportArtifact] = []
        for produced, artifact_kind in (
            (result.trimmed_video_path, "trim"),
            (result.csv_path, "csv"),
            (result.fcpxml_path, "fcpxml"),
            (result.report_path, "report"),
            (result.overlay_path, "overlay"),
        ):
            if produced is not None:
                run_artifacts.append(
                    export_runs.ExportArtifact(filename=produced.name, kind=artifact_kind)
                )
        # ``.values()``, not the mapping: iterating ``secondary_trimmed_paths``
        # yields video-id strings, which is the exact bug
        # ``test_export_result_reports_secondary_trim_filenames`` pins.
        run_artifacts.extend(
            export_runs.ExportArtifact(filename=p.name, kind="secondary_trim")
            for p in result.secondary_trimmed_paths.values()
        )
        _record_export_run(
            state,
            slug,
            export_runs.ExportRun(
                run_id=export_runs.new_run_id(),
                kind="stage",
                finished_at=datetime.now(UTC),
                # Wall clock for the job body. NOT any duration a result
                # object carries -- see export_runs.ExportRun's docstring.
                duration_seconds=handle.timer.build()["total_ms"] / 1000.0,
                stage_numbers=[stage_number],
                formats=export_runs.stage_run_formats(
                    trim=req.write_trim,
                    csv=req.write_csv,
                    fcpxml=req.write_fcpxml,
                    report=req.write_report,
                    overlay=req.write_overlay,
                ),
                anomaly_count=len(reported),
                artifacts=run_artifacts,
            ),
        )
```

What must not change: basenames only, `secondary_trimmed_paths` iterated by `.values()`, and placement *after* the "no trim was written" raise so a failed run leaves no history line.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/test_export_run_record.py -n0 -q`
Expected: all pass.

- [ ] **Step 5: Prove the "failed export records nothing" test can fail**

Move the `_record_export_run(...)` block above the `if req.write_trim and result.trimmed_video_path is None ...: raise RuntimeError(...)` guard, re-run, and confirm `test_a_failed_stage_export_records_nothing` goes red. Move it back. Without this the test is decorative.

- [ ] **Step 6: Format, lint, commit**

```bash
uv run black src/splitsmith/ui/server.py tests/test_export_run_record.py
uv run ruff check src/splitsmith/ui/server.py tests/test_export_run_record.py
git add src/splitsmith/ui/server.py tests/test_export_run_record.py
git commit -m "feat(exports): record a run after each per-stage export (#629)"
```

---

### Task 5: The match export job writes a record

**Files:**
- Modify: `src/splitsmith/ui/server.py` -- `_run_match_export`, immediately before `handle.set_result(...)` (~line 3883)
- Test: `tests/test_export_run_record.py`

**Interfaces:**
- Consumes: same as Task 4, plus `export_runs.match_run_formats`.
- Produces: a stored run with `kind="match"` and every selected stage number in `stage_numbers`.

- [ ] **Step 1: Write the failing test**

`_seed_match_export_project` pre-stages two stages with audit docs and
`stage<N>_stage-<N>_trimmed.mp4` files, and `_stub_match_export_probe`
fakes `fcpxml_gen.probe_video` so no ffprobe runs. Both live in
`tests/test_ui_server.py`; this is exactly the setup
`test_match_export_endpoint_writes_fcpxml` uses. Append to
`tests/test_export_run_record.py`:

```python
def test_a_match_export_records_one_run_spanning_its_stages(tmp_path: Path, monkeypatch) -> None:
    """Run *grouping* is the point: one match export over two stages is one
    history line, not two."""
    from .test_ui_server import _stub_match_export_probe, _wait_for_job

    client, project_root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)

    resp = client.post(
        "/api/shooters/me/export/match",
        json={
            "stage_numbers": [1, 2],
            "head_pad_seconds": 0.5,
            "tail_pad_seconds": 1.0,
            "include_secondaries": True,
            # Trims are pre-staged and no overlay is asked for, so the
            # worker stays on the "skip the per-stage exporter" branch and
            # never shells out to ffmpeg.
            "include_overlay": False,
        },
    )
    assert resp.status_code == 200, resp.text
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final

    doc = json.loads(
        (project_root / "shooters" / "me" / "export_runs.json").read_text(encoding="utf-8")
    )
    assert len(doc["runs"]) == 1
    run = doc["runs"][0]
    assert run["kind"] == "match"
    assert run["stage_numbers"] == [1, 2]
    assert run["formats"] == ["fcpxml"]
    assert [a["kind"] for a in run["artifacts"]] == ["fcpxml"]
    assert run["artifacts"][0]["filename"].endswith("-match.fcpxml")

    # The wall-clock trap, pinned rather than described:
    # ``MatchExportResult.duration_seconds`` is the *stitched timeline's*
    # length, which this fixture makes 4.0s (2 stages x 2.0s effective).
    # The run's duration is how long the job took, which for a fully
    # mocked export is a fraction of a second. Asserting both is what
    # makes the second assertion discriminating -- wire the wrong field in
    # and it reads 4.0.
    assert final["result"]["duration_seconds"] == pytest.approx(4.0, abs=0.1)
    assert run["duration_seconds"] < 2.0
```

Add `import pytest` to the test module's imports.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_export_run_record.py -n0 -q -k match_export`
Expected: `FileNotFoundError` on `export_runs.json`.

- [ ] **Step 3: Write the record**

In `_run_match_export`, before `handle.set_result(...)`:

```python
        # Durable record of this run (#629). One run per invocation across
        # every selected stage -- that grouping is precisely what a
        # directory listing cannot reconstruct.
        match_artifacts = [
            export_runs.ExportArtifact(
                filename=result.fcpxml_path.name,
                kind="match_video" if result.fcpxml_path.suffix.lower() == ".mp4" else "fcpxml",
            )
        ]
        match_artifacts.extend(
            export_runs.ExportArtifact(filename=p.name, kind="sidecar")
            for p in (
                result.fcpxml_path.with_suffix(".srt"),
                result.fcpxml_path.with_suffix(".json"),
            )
            if p.exists()
        )
        _record_export_run(
            state,
            slug,
            export_runs.ExportRun(
                run_id=export_runs.new_run_id(),
                kind="match",
                finished_at=datetime.now(UTC),
                # Wall clock. ``result.duration_seconds`` is the stitched
                # timeline's length, which is a different number entirely.
                duration_seconds=handle.timer.build()["total_ms"] / 1000.0,
                stage_numbers=list(req.stage_numbers),
                formats=export_runs.match_run_formats(
                    output_format=req.output_format,
                    youtube_sidecar=req.youtube_sidecar,
                ),
                anomaly_count=len(result.anomalies),
                artifacts=match_artifacts,
            ),
        )
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run pytest tests/test_export_run_record.py -n0 -q`
Expected: all pass.

- [ ] **Step 5: Run the whole export-adjacent suite**

Run: `uv run pytest tests/test_ui_server.py tests/test_ui_exports.py tests/test_ui_match_exports.py tests/test_api_source_fetch_guard.py -q`
Expected: all pass. These exercise both job bodies heavily; a record write that raises inside a job would show up here as failed jobs.

- [ ] **Step 6: Format, lint, commit**

```bash
uv run black src/splitsmith/ui/server.py tests/test_export_run_record.py
uv run ruff check src/splitsmith/ui/server.py tests/test_export_run_record.py
git add src/splitsmith/ui/server.py tests/test_export_run_record.py
git commit -m "feat(exports): record a run after each match export (#629)"
```

---

### Task 6: Stop the new doc kind from breaking desktop sync

**Files:**
- Modify: `src/splitsmith/sync/pull.py`
- Test: `tests/test_sync_pull.py` (or wherever `plan_pull` is currently tested -- `grep -rn "plan_pull" tests/` and add to that file)

**Interfaces:**
- Consumes: nothing from earlier tasks (independent -- can be done first if you like).
- Produces: `splitsmith.sync.pull.PULLABLE_DOC_KINDS: frozenset[str]`

**Why this task exists.** `ProjectStateStore.list_doc_meta` returns *every* doc kind and is the sync pull manifest. `SyncClient._doc_path` maps anything that is not `match` or `project` to `docs/audit/{slug}/{stage_number}`, so an `export_runs` entry is fetched as `GET /api/sync/matches/{id}/docs/audit/me/None` -- a 422, raised as `SyncClientError`, which **fails the entire sync** for that match. This fires on the first desktop sync after the first hosted export. Verify this claim yourself before fixing it; do not take it on trust.

- [ ] **Step 1: Write the failing test**

```python
def test_plan_pull_ignores_doc_kinds_this_client_cannot_merge() -> None:
    """A hosted-only doc kind must be inert to an old desktop client.

    ``SyncClient._doc_path`` maps any unknown kind onto the audit URL
    shape, so pulling one would GET ``docs/audit/<slug>/None`` and fail
    the whole sync. Allowlist, not denylist: the next kind added should
    be ignored by default rather than sync-breaking.
    """
    manifest = [
        {"doc_kind": "audit", "slug": "me", "stage_number": 1, "version": 3,
         "updated_at": "2026-08-16T10:00:00+00:00"},
        {"doc_kind": "export_runs", "slug": "me", "stage_number": None, "version": 7,
         "updated_at": "2026-08-16T10:00:00+00:00"},
    ]
    changed = plan_pull(manifest, SyncState())
    assert [rd.kind for rd in changed] == ["audit"]
```

Adapt `SyncState()` construction to whatever the existing tests in that file use.

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_sync_pull.py -n0 -q -k cannot_merge`
Expected: FAIL -- `['audit', 'export_runs'] != ['audit']`.

- [ ] **Step 3: Add the allowlist**

In `src/splitsmith/sync/pull.py`:

```python
#: Doc kinds this client knows how to merge locally. ``list_doc_meta``
#: returns every kind in the match, including hosted-only ones like
#: ``export_runs`` (#629) -- and ``SyncClient._doc_path`` maps anything
#: it does not recognise onto the audit URL shape, so pulling an unknown
#: kind fails the whole sync rather than that one doc. An allowlist keeps
#: the next kind added inert by default.
PULLABLE_DOC_KINDS = frozenset({"match", "project", "audit"})
```

and, as the first statement of the `for entry in manifest:` loop in `plan_pull`:

```python
        if entry["doc_kind"] not in PULLABLE_DOC_KINDS:
            continue
```

- [ ] **Step 4: Run it and watch it pass**

Run: `uv run pytest tests/test_sync_pull.py tests/test_sync_api.py -n0 -q`
Expected: all pass.

- [ ] **Step 5: Leave a marker at the dispatch site**

In `src/splitsmith/sync/run.py`, the pulled-doc dispatch ends in `else:  # audit`. Change that comment to record the invariant now holding it up:

```python
        else:  # audit -- the only remaining kind; plan_pull filters on PULLABLE_DOC_KINDS
```

- [ ] **Step 6: Format, lint, commit**

```bash
uv run black src/splitsmith/sync/pull.py src/splitsmith/sync/run.py tests/test_sync_pull.py
uv run ruff check src/splitsmith/sync/pull.py src/splitsmith/sync/run.py tests/test_sync_pull.py
git add src/splitsmith/sync/pull.py src/splitsmith/sync/run.py tests/test_sync_pull.py
git commit -m "fix(sync): ignore doc kinds the desktop client cannot merge (#629)"
```

---

### Task 7: Lift the export routes into `ui/exports_api.py`

**Files:**
- Create: `src/splitsmith/ui/http_errors.py`
- Create: `src/splitsmith/ui/exports_api.py`
- Modify: `src/splitsmith/ui/server.py` (delete the moved code, import it back, include the router)
- Modify: `src/splitsmith/ui/job_journal.py` (~line 88 -- repoint the lazy model import)
- Test: no new tests. This is a pure move; the existing suite is the assertion.

**Interfaces:**
- Consumes: `_record_export_run` stays in `server.py` (the job bodies call it, and they do not move).
- Produces:
  - `splitsmith.ui.http_errors.ensure_source_reachable(stage_number: int | None, source: Path) -> None`
  - `splitsmith.ui.exports_api.router: APIRouter`
  - `splitsmith.ui.exports_api.ExportStageRequest`, `splitsmith.ui.exports_api.MatchExportRequest`

**Behaviour must not change.** Same paths, same status codes, same bodies. #919's rule is that a feature lifts its own routes on the way past; it is not licence to redesign them.

- [ ] **Step 1: Move `_ensure_source_reachable`**

Create `src/splitsmith/ui/http_errors.py` holding the function verbatim from `server.py:259-285`, renamed `ensure_source_reachable` (public -- it now has importers outside its own module). Keep the docstring. In `server.py`, delete the definition and add `from .http_errors import ensure_source_reachable`, then rename all 10 call sites.

- [ ] **Step 2: Run the suite**

Run: `uv run pytest tests/test_ui_server.py tests/test_api_source_fetch_guard.py -q`
Expected: all pass. Commit this step on its own:

```bash
uv run black src/splitsmith/ui/http_errors.py src/splitsmith/ui/server.py
uv run ruff check src/splitsmith/ui/http_errors.py src/splitsmith/ui/server.py
git add src/splitsmith/ui/http_errors.py src/splitsmith/ui/server.py
git commit -m "refactor(ui): lift ensure_source_reachable into its own module (#629)"
```

- [ ] **Step 3: Create the router module**

Create `src/splitsmith/ui/exports_api.py` with this header, then move in the two request models and the four route handlers verbatim:

```python
"""Export routes: per-stage + match export submission, the overview, the
deliverable download, and the run history (#629).

Lifted out of ``server.py`` under #919's standing rule -- every feature
that touches ``server.py`` lifts its own routes to a domain router on the
way past. Paths are unchanged, so the ``/api/matches/{id}/`` alias
middleware, the test harness's ``_SCOPED_PREFIXES`` and every SPA call
site are untouched.

**This module must never import ``server``**: ``server`` imports the two
request models from here, so an import back would be a cycle at load
time. Anything shared goes to a third module -- that is why
``ensure_source_reachable`` lives in ``http_errors``.

The export *job bodies* stay in ``server.py``. Lifting
``register_job_bodies`` is a separate and much larger job.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..match_project import trim_blocker
from . import export_storage
from .http_errors import ensure_source_reachable

router = APIRouter()
```

Each handler gains a `request: Request` parameter and opens with:

```python
    state = request.app.state.splitsmith_state
```

replacing the closure over `state`. The five routes:

| handler | path |
| --- | --- |
| `export_overview` | `GET /api/shooters/{slug}/exports/overview` |
| `download_export_file` | `GET /api/shooters/{slug}/exports/file/{filename:path}` |
| `export_stage` | `POST /api/shooters/{slug}/stages/{stage_number}/export` |
| `export_match` | `POST /api/shooters/{slug}/export/match` |
| `list_export_runs` | `GET /api/shooters/{slug}/exports/runs` (Task 8 -- not yet) |

`export_match` is long and pulls in more helpers than the others. If any helper it needs is a `server.py` module-level private with no other purpose, move it here too; if it has other `server.py` callers, move it to `http_errors.py` or leave the route behind and note why in the module docstring. **Do not create an import from `exports_api` back into `server` under any circumstance.**

- [ ] **Step 4: Wire it up**

In `server.py`:
- Delete the moved handlers and the two request models.
- Add at module level, next to the other `.ui` imports:
  `from .exports_api import ExportStageRequest, MatchExportRequest` (the job bodies annotate with them, and `state.jobs.submit(args={"req": req})` passes them).
- In `create_app`, next to the `sync_router` / `device_router` includes:

```python
    # Export routes (#629 / #919's lift-as-you-go rule). Included here so
    # /api/shooters/{slug}/exports/* passes through the same middleware as
    # the routes that stayed behind.
    from .exports_api import router as exports_router

    app.include_router(exports_router)
```

In `src/splitsmith/ui/job_journal.py`, change the lazy import in `rehydrate_args` from `from .server import ExportStageRequest, MatchExportRequest` to `from .exports_api import ExportStageRequest, MatchExportRequest`, and update the surrounding comment, which currently says the import is lazy to avoid a server import at load time -- the reason now is the same cycle rule.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: everything passes. If a test fails on a 404 for an export path, the router was included before a middleware it needs, or a path string was altered during the move. Also confirm:

Run: `uv run pytest tests/test_local_mode_no_hosted_imports.py -n0 -q`
Expected: pass -- `exports_api` must not have introduced an eager `splitsmith.db` import.

- [ ] **Step 6: Verify the route table by hand**

```bash
uv run python -c "
from splitsmith.ui.server import create_app
app = create_app()
for r in app.routes:
    p = getattr(r, 'path', '')
    if 'export' in p:
        print(sorted(getattr(r, 'methods', []) or []), p)
"
```
Expected: exactly the four routes above (plus `/api/match/templates` if it stayed in `server.py`), each present once. A duplicate means the old definition was not deleted, and FastAPI resolves the first registration -- which would silently keep the old handler live.

- [ ] **Step 7: Commit**

```bash
uv run black src/splitsmith/ui/exports_api.py src/splitsmith/ui/server.py src/splitsmith/ui/job_journal.py
uv run ruff check src/splitsmith/ui/exports_api.py src/splitsmith/ui/server.py src/splitsmith/ui/job_journal.py
git add src/splitsmith/ui/exports_api.py src/splitsmith/ui/server.py src/splitsmith/ui/job_journal.py
git commit -m "refactor(ui): lift the export routes into their own router (#629, #680)"
```

---

### Task 8: `GET /api/shooters/{slug}/exports/runs`

**Files:**
- Modify: `src/splitsmith/ui/exports_api.py`
- Test: `tests/test_export_run_record.py`

**Interfaces:**
- Consumes: `AppState.load_export_runs` (Task 3), `export_runs.load_log` (Task 1), the router (Task 7).
- Produces: `GET /api/shooters/{slug}/exports/runs` -> `{"runs": [ExportRun, ...]}` newest first.

- [ ] **Step 1: Write the failing tests**

```python
def _export_stage_trim_only(client, stage_number: int):
    """Submit a trim-only export and wait for it. The trim writer must
    already be monkeypatched by the caller."""
    from .test_ui_server import _wait_for_job

    resp = client.post(
        f"/api/shooters/me/stages/{stage_number}/export",
        json={
            "write_trim": True,
            "write_csv": False,
            "write_fcpxml": False,
            "write_report": False,
            "write_overlay": False,
        },
    )
    assert resp.status_code == 200, resp.text
    final = _wait_for_job(client, resp.json()["id"])
    assert final["status"] == "succeeded", final
    return final


def test_export_runs_endpoint_serves_the_history_newest_first(tmp_path: Path, monkeypatch) -> None:
    from splitsmith import trim

    client, project_root = _seed_match_export_project(tmp_path, stage_count=2)

    def fake_trim_video(source, output_path, **kwargs):  # type: ignore[no-untyped-def]
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"TRIMMED")
        return trim.TrimResult(output_path=Path(output_path), start_time=0.0, end_time=10.0)

    monkeypatch.setattr(trim, "trim_video", fake_trim_video)
    for n in (1, 2):
        assert (
            client.post(f"/api/shooters/me/stages/{n}/time", json={"time_seconds": 10.0}).status_code
            == 200
        )
        _export_stage_trim_only(client, n)

    resp = client.get("/api/shooters/me/exports/runs")
    assert resp.status_code == 200, resp.text
    runs = resp.json()["runs"]
    assert len(runs) == 2
    # Newest first is the stored order; the client must never have to sort.
    assert runs[0]["stage_numbers"] == [2]
    assert runs[1]["stage_numbers"] == [1]
    assert runs[0]["artifacts"][0]["filename"].endswith("_trimmed.mp4")


def test_export_runs_endpoint_is_empty_before_any_export(tmp_path: Path) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    resp = client.get("/api/shooters/me/exports/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def test_export_runs_endpoint_survives_a_corrupt_log(tmp_path: Path) -> None:
    """Bookkeeping must not 500 a page. A truncated document reads as an
    empty history, not an error."""
    client, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    (project_root / "shooters" / "me" / "export_runs.json").write_text("{not json", encoding="utf-8")
    resp = client.get("/api/shooters/me/exports/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_export_run_record.py -n0 -q -k endpoint`
Expected: 404 `{"detail": "api route not found"}`.

- [ ] **Step 3: Add the route**

In `exports_api.py`, next to `export_overview`:

```python
@router.get("/api/shooters/{slug}/exports/runs")
def list_export_runs(slug: str, request: Request) -> JSONResponse:
    """The shooter's export history, newest first (#629).

    Deliberately separate from ``exports/overview``: the overview answers
    "what can I download now" and this answers "what happened". Run
    grouping, duration, selected formats and anomaly count are the four
    facts a directory listing cannot reconstruct, which is the whole
    reason a record is written at export time.

    A malformed or unreadable log reads as an empty history rather than a
    500 -- ``export_runs.load_log`` drops what it cannot parse.
    """
    state = request.app.state.splitsmith_state
    doc, _version = state.load_export_runs(slug)
    log = export_runs.load_log(doc)
    return JSONResponse({"runs": [r.model_dump(mode="json") for r in log.runs]})
```

Add `from .. import export_runs` to the module imports.

- [ ] **Step 4: Run them and watch them pass**

Run: `uv run pytest tests/test_export_run_record.py -n0 -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run black src/splitsmith/ui/exports_api.py tests/test_export_run_record.py
uv run ruff check src/splitsmith/ui/exports_api.py tests/test_export_run_record.py
git add src/splitsmith/ui/exports_api.py tests/test_export_run_record.py
git commit -m "feat(api): serve the export-run history (#629)"
```

---

### Task 9: SPA client + the history component

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts`
- Create: `src/splitsmith/ui_static/src/components/export/ExportHistory.tsx`
- Test: `src/splitsmith/ui_static/src/components/export/ExportHistory.test.tsx`

**Interfaces:**
- Consumes: `GET /api/shooters/{slug}/exports/runs` (Task 8).
- Produces:
  - `export interface ExportArtifact { filename: string; kind: string }`
  - `export interface ExportRun { run_id: string; kind: "stage" | "match"; finished_at: string; duration_seconds: number; stage_numbers: number[]; formats: string[]; anomaly_count: number; artifacts: ExportArtifact[] }`
  - `api.getExportRuns(slug: string): Promise<{ runs: ExportRun[] }>`
  - `export function ExportHistory(props: { runs: ExportRun[]; exportFileUrl: (filename: string) => string })`
  - `export function stageLabel(stages: number[]): string`

Run SPA commands from `src/splitsmith/ui_static/`. Use `pnpm` via corepack; do not add packages.

- [ ] **Step 1: Add the types + client method**

In `lib/api.ts`, next to `MatchExportFile` / `ExportOverview` (~line 758-772):

```ts
/** One artefact a recorded export run produced. ``filename`` is a
 *  basename under the shooter's ``exports/`` dir -- the same key
 *  {@link exportFileUrl} takes. */
export interface ExportArtifact {
  filename: string;
  kind: string;
}

/** One export invocation, as recorded at export time (#629).
 *
 *  ``formats`` is what the user *asked* for; ``artifacts`` is what got
 *  written. Both are kept so "asked for an overlay, got none" is visible
 *  in the history rather than inferred from an absence.
 *
 *  ``duration_seconds`` is wall-clock time for the run, not the length of
 *  any rendered timeline. */
export interface ExportRun {
  run_id: string;
  kind: "stage" | "match";
  finished_at: string;
  duration_seconds: number;
  stage_numbers: number[];
  formats: string[];
  anomaly_count: number;
  artifacts: ExportArtifact[];
}
```

and next to the other `/exports/` calls in the `api` object:

```ts
  /** The shooter's export history, newest first (#629). Persistent: a
   *  record written at export time, not the in-session job result. */
  getExportRuns: (slug: string) =>
    request<{ runs: ExportRun[] }>(
      `/api/shooters/${encodeURIComponent(slug)}/exports/runs`,
    ),
```

Match the surrounding `request<...>` / `scopeRequestPath` idiom exactly -- read the neighbouring methods first; `exportFileUrl` builds a bare path because it is consumed as an `<a href download>`, while a fetch goes through `request`.

- [ ] **Step 2: Write the failing component test**

Create `src/splitsmith/ui_static/src/components/export/ExportHistory.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ExportRun } from "@/lib/api";

import { ExportHistory } from "@/components/export/ExportHistory";

function run(over: Partial<ExportRun> = {}): ExportRun {
  return {
    run_id: "r1",
    kind: "stage",
    finished_at: "2026-08-16T12:00:00Z",
    duration_seconds: 12.5,
    stage_numbers: [3],
    formats: ["trim", "csv"],
    anomaly_count: 0,
    artifacts: [{ filename: "stage3_wall_trimmed.mp4", kind: "trim" }],
    ...over,
  };
}

describe("ExportHistory", () => {
  // Exact strings, not regexes: getByText matches an element's whole
  // textContent, so a regex also matches every ancestor row and container
  // and fails with "found multiple elements". Exact matching pins the
  // leaf, which is also what makes these assertions specify the output.
  it("renders a stage run with its stage, formats and duration", () => {
    render(<ExportHistory runs={[run()]} exportFileUrl={(f) => `/dl/${f}`} />);
    expect(screen.getByText("Stage 3")).toBeInTheDocument();
    expect(screen.getByText("trim, csv")).toBeInTheDocument();
    expect(screen.getByText("12.5s")).toBeInTheDocument();
  });

  it("groups a match run's stages into one row", () => {
    render(
      <ExportHistory
        runs={[run({ kind: "match", stage_numbers: [1, 2, 3], formats: ["fcpxml"] })]}
        exportFileUrl={(f) => `/dl/${f}`}
      />,
    );
    expect(screen.getByText("Stages 1-3")).toBeInTheDocument();
  });

  it("lists non-contiguous stages instead of implying a range", () => {
    render(
      <ExportHistory
        runs={[run({ kind: "match", stage_numbers: [1, 2, 4] })]}
        exportFileUrl={(f) => `/dl/${f}`}
      />,
    );
    expect(screen.getByText("Stages 1, 2, 4")).toBeInTheDocument();
  });

  it("links each artefact to its download URL by basename", () => {
    render(<ExportHistory runs={[run()]} exportFileUrl={(f) => `/dl/${f}`} />);
    const link = screen.getByRole("link", { name: "stage3_wall_trimmed.mp4" });
    expect(link).toHaveAttribute("href", "/dl/stage3_wall_trimmed.mp4");
    expect(link).toHaveAttribute("download", "stage3_wall_trimmed.mp4");
  });

  it("shows the anomaly count only when there is one", () => {
    const { rerender } = render(
      <ExportHistory runs={[run()]} exportFileUrl={(f) => `/dl/${f}`} />,
    );
    expect(screen.queryByText(/anomal/i)).not.toBeInTheDocument();
    rerender(
      <ExportHistory runs={[run({ anomaly_count: 2 })]} exportFileUrl={(f) => `/dl/${f}`} />,
    );
    expect(screen.getByText("2 anomalies")).toBeInTheDocument();
  });

  it("renders an empty state rather than an empty list", () => {
    render(<ExportHistory runs={[]} exportFileUrl={(f) => `/dl/${f}`} />);
    expect(screen.getByText("No exports yet")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run it and watch it fail**

Run (from `src/splitsmith/ui_static/`): `pnpm vitest run src/components/export/ExportHistory.test.tsx`
Expected: FAIL -- cannot resolve `@/components/export/ExportHistory`.

- [ ] **Step 4: Write the component**

Create `src/splitsmith/ui_static/src/components/export/ExportHistory.tsx`:

```tsx
import { Download } from "lucide-react";

import type { ExportRun } from "@/lib/api";

/** "Stage 3", "Stages 1-3" for a contiguous run, "Stages 1, 2, 4"
 *  otherwise. A range label over a gapped selection would be a lie, and
 *  gaps are normal since #521 let a stage be removed without renumbering. */
export function stageLabel(stages: number[]): string {
  if (stages.length === 0) return "No stages";
  if (stages.length === 1) return `Stage ${stages[0]}`;
  const sorted = [...stages].sort((a, b) => a - b);
  const contiguous = sorted.every((n, i) => i === 0 || n === sorted[i - 1] + 1);
  return contiguous
    ? `Stages ${sorted[0]}-${sorted[sorted.length - 1]}`
    : `Stages ${sorted.join(", ")}`;
}

/** What each export run produced, newest first (#629).
 *
 *  Purely presentational: every input is a prop, nothing is fetched here.
 *  The list arrives already ordered by the server -- do not re-sort, or a
 *  clock skew between two workers becomes a reordering bug.
 *
 *  Rendered in both deployment modes. Only the *reveal* affordance was
 *  ever desktop-specific; the download endpoint reads local disk on
 *  desktop and object storage on hosted, so one link works for both. */
export function ExportHistory({
  runs,
  exportFileUrl,
}: {
  runs: ExportRun[];
  exportFileUrl: (filename: string) => string;
}) {
  return (
    <section className="mt-6 rounded-lg border border-rule">
      <h2 className="border-b border-rule px-5 py-3 font-display text-[0.6875rem] font-bold uppercase tracking-[0.1em] text-ink-2">
        Export history
      </h2>
      {runs.length === 0 ? (
        <p className="px-5 py-4 text-[0.8125rem] text-muted">No exports yet</p>
      ) : (
        <ul className="divide-y divide-rule">
          {runs.map((r) => (
            <li key={r.run_id} className="px-5 py-3">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="font-display text-[0.8125rem] font-semibold text-ink">
                  {stageLabel(r.stage_numbers)}
                </span>
                <span className="font-mono text-[0.6875rem] uppercase tracking-[0.04em] text-muted">
                  {r.formats.join(", ")}
                </span>
                <span className="font-mono text-[0.6875rem] text-muted tabular-nums">
                  {r.duration_seconds.toFixed(1)}s
                </span>
                {r.anomaly_count > 0 && (
                  <span className="font-mono text-[0.6875rem] text-warn tabular-nums">
                    {r.anomaly_count} {r.anomaly_count === 1 ? "anomaly" : "anomalies"}
                  </span>
                )}
                <time
                  dateTime={r.finished_at}
                  className="ml-auto font-mono text-[0.6875rem] text-muted tabular-nums"
                >
                  {new Date(r.finished_at).toLocaleString()}
                </time>
              </div>
              <div className="mt-1.5 flex flex-col gap-0.5">
                {r.artifacts.map((a) => (
                  <a
                    key={a.filename}
                    href={exportFileUrl(a.filename)}
                    download={a.filename}
                    className="inline-flex items-center gap-1.5 font-mono text-[0.6875rem] text-led hover:text-led-soft"
                  >
                    <Download className="size-3" /> {a.filename}
                  </a>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
```

Two things to check against the real codebase rather than trusting this
draft: `text-warn` must be a token that exists (grep the theme; use
whatever the anomaly wording elsewhere uses if it does not), and the
`Download` icon import must match how `Export.tsx` imports from
`lucide-react`. The accessible name of each link is the filename text --
the icon contributes nothing, which is what makes the `getByRole` query
in the test work.

- [ ] **Step 5: Run it and watch it pass**

Run: `pnpm vitest run src/components/export/ExportHistory.test.tsx`
Expected: 6 passed.

- [ ] **Step 6: Typecheck, lint, commit**

```bash
cd src/splitsmith/ui_static && pnpm tsc --noEmit && pnpm lint
cd - && git add src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/components/export/
git commit -m "feat(ui): an export-history component + client (#629)"
```

---

### Task 10: Wire the history into the Export page

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Export.tsx`

**Interfaces:**
- Consumes: `api.getExportRuns`, `ExportHistory` (Task 9).
- Produces: nothing new.

- [ ] **Step 1: Find the existing overview fetch and its post-job refetch**

`Export.tsx` holds `const [overview, setOverview] = useState<ExportOverview | null>(null)` (~line 146) and refetches the overview when an export job reaches a terminal state. Read that effect before writing anything -- the run list must refetch on exactly the same trigger, or a user watches a job finish and sees no new history line until they reload.

- [ ] **Step 2: Add the state + fetch**

```tsx
  const [runs, setRuns] = useState<ExportRun[]>([]);
```

Load it wherever the overview is loaded, and refetch it wherever the overview is refetched. A failed fetch sets `[]` and does not surface an error toast -- the history is secondary to the page's purpose and must never block it.

- [ ] **Step 3: Render it**

Place `<ExportHistory runs={runs} exportFileUrl={(f) => (slug ? api.exportFileUrl(slug, f) : "#")} />` in the main column, below the stage cards. **Both modes** -- a desktop user has as much use for "what did I export and when" as a hosted one; only the *download* affordance was ever hosted-specific, and here it works in both (`download_export_file` reads local disk on desktop).

- [ ] **Step 4: Verify against a running app, not just the tests**

Start the dev server and click through: run an export, watch the history line appear without a reload, click an artefact link and confirm the file downloads. A rendered-output check is required here, not optional -- this repo has shipped a fix that reached the DOM and was then ellipsized away by the renderer, with a green assertion over it the whole time.

```bash
cd src/splitsmith/ui_static && pnpm dev   # then drive the Export page in a browser
```

If a browser is not available in the environment, say so explicitly in the task report rather than claiming the UI works.

- [ ] **Step 5: Run the SPA suite**

Run (from `src/splitsmith/ui_static/`): `pnpm vitest run`
Expected: all pass. Note the wall-clock time of the slowest test; if anything in this task pushed a test past ~6s, say so -- `TEST_BUDGET_MS = 30_000` in `vite.config.ts` was derived from the worst observed test and is not to be moved silently.

- [ ] **Step 6: Typecheck, lint, commit**

```bash
cd src/splitsmith/ui_static && pnpm tsc --noEmit && pnpm lint
cd - && git add src/splitsmith/ui_static/src/pages/Export.tsx
git commit -m "feat(ui): show the export history on the Export page (#629)"
```

---

### Task 11: Whole-branch pass, docs, and the eviction follow-up

**Files:**
- Modify: `SPEC.md` (Module responsibilities)
- Modify: `CLAUDE.md` (only if a durable invariant belongs there)

**Interfaces:** none.

This is the cross-cutting read CLAUDE.md's review practice calls for: on PR #612 every substantive defect was found this way and none by the test suite, and the worst one lived in a seam no single task owned.

- [ ] **Step 1: Run everything**

```bash
uv run pytest -q
cd src/splitsmith/ui_static && pnpm vitest run && pnpm tsc --noEmit && pnpm lint
```
Expected: green. A green suite is evidence the change broke nothing known -- not evidence it works.

- [ ] **Step 2: Read the branch diff end to end**

```bash
git diff main...HEAD
```

Specifically check the seams no single task owned:
- Does anything else enumerate state doc kinds? `grep -rn "doc_kind" src/splitsmith --include='*.py'` and confirm each hit either handles `export_runs` or is provably kind-agnostic.
- Does the match-delete cascade remove the new doc? `src/splitsmith/ui/match_delete.py` sweeps via `delete_match`, which filters on `match_id` alone -- confirm by reading, and if a shooter-removal path uses something narrower, check that too.
- Does `splitsmith.cleanup` see the new desktop file? It globs project directories; `export_runs.json` in the shooter root must not be planned for deletion as an unrecognised artefact, and equally must not be silently swept. Read `plan_cleanup`'s categories and confirm which side of the line it falls on.
- Is there any remaining path where a record write can raise into a job body? `grep -n "_record_export_run" src/splitsmith/ui/server.py` and read each call site's surroundings.

- [ ] **Step 3: Prove one test per task can actually fail**

For Task 4's and Task 8's headline assertions, delete the production line that satisfies them, run, watch red, restore. Several tests on an earlier branch would have passed against the bug they claimed to cover; a minute each is the only real proof.

- [ ] **Step 4: Update SPEC.md**

Add `export_runs.py` to the module responsibilities list, one line: the record shape and append rule for export history; persistence is `AppState.load_export_runs` / `save_export_runs` (state_docs hosted, `<shooter_root>/export_runs.json` desktop). Add `ui/exports_api.py` alongside it as the home of the export routes.

- [ ] **Step 5: Decide whether CLAUDE.md needs anything**

Two candidates, both genuinely non-obvious and both cheap to state in a sentence:
- `sync.pull.PULLABLE_DOC_KINDS` is an allowlist because an unknown kind fails the *whole* sync, not one doc.
- The run record's `duration_seconds` is wall clock, while `MatchExportResult.duration_seconds` is timeline length.

Add them only if they read as durable project invariants rather than as change notes.

- [ ] **Step 6: File the eviction follow-up**

Open an issue titled *"exports: size-capped LRU eviction of derived video, guarded on source presence"*, carrying the second half of the 2026-08-15 retention decision on #629: derived video only, `source_present` as the guard (not `resolve_video_path` -- see #637/#638), the run record stays and its evicted artefacts render as **Re-export**, the cap is user-visible config, and the mechanism ships with eviction off by default until a real season's storage has been measured. Link it from #629 and from #919.

- [ ] **Step 7: Commit and open the PR**

```bash
git add SPEC.md CLAUDE.md
git commit -m "docs: record the export-run record's seams (#629)"
```

PR body: what shipped, what the retention decision deferred, and the one behavioural risk worth a reviewer's eye (the route lift in Task 7 -- same paths, no behaviour change intended). Keep the squash body short: a many-commit squash body breaks release-please's parser and the change vanishes from the changelog with CI still green.

---

## Notes for the executor

- **Task 6 is independent.** It fixes a defect the rest of this plan introduces, but it needs nothing from the other tasks and can go first if that is convenient.
- **Task 7 is the risky one.** It is a pure move with no behaviour change, which means the suite is the whole safety net; take the route-table verification in Step 6 seriously rather than trusting the diff.
- **Do not trust this plan's test code as correct.** It is a draft written without running it. Every test here must be seen to fail before the implementation and pass after; where a snippet is wrong, fix the snippet rather than weakening the assertion.
- The seeding helpers referenced (`_seed_match_export_project`, `_wait_for_job`) live in `tests/test_ui_server.py`. Read them before importing them; the autouse `SPLITSMITH_AUTO_BEEP_DISABLED=1` fixture in that file is **not** inherited by other modules that import its helpers, so an auto-beep job may fire in `tests/test_export_run_record.py` where it does not in `test_ui_server.py`.
