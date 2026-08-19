# Corpus Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate every splitsmith match onto `/Volumes/X9` as one multi-shooter match per event, with all raw footage reachable from X9 alone, and make a future path break fail loudly instead of silently shrinking the training corpus.

**Architecture:** Two halves. The repo half adds one shared resolver for fixture `source_video` (used by five scripts), a fixture path-rewrite migration, and a consolidation library whose reconciliation rules are pure functions over dicts and Paths, tested against `tmp_path` trees. The data half is a sequence of operational phases driven by `scripts/consolidate_matches.py`, each producing a JSON report, with deletion gated behind a human-read verification report.

**Tech Stack:** Python 3.11+, `uv`, pytest (parallel via `-n auto`), Pydantic, pathlib. Existing splitsmith modules reused: `match_model.plan_merge` / `execute_merge`, `relink.inspect_links` / `index_search_root` / `plan_relink` / `apply_relink`, `sync.plan.build_push_plan`, `sync.state.load_sync_state`.

**Spec:** `docs/superpowers/specs/2026-08-19-corpus-consolidation-design.md`

## Global Constraints

- Python 3.11+, type hints everywhere, `pathlib.Path` never strings, f-strings, Black line length 110, Ruff clean.
- `uv` for everything. Never `pip`.
- Imports grouped stdlib / third-party / local, separated by blank lines. No relative imports beyond a single dot.
- Pydantic models for data crossing module boundaries. No dicts of unknown shape.
- No new dependencies. The dep list is small on purpose.
- Tests must not depend on execution order or share mutable state outside `tmp_path` (the suite runs `-n auto --dist load`). Use `-n0` when debugging one test.
- Never generate fake video/audio fixtures. Synthetic *project trees* under `tmp_path` are fine and expected; synthetic *media* is not.
- Canonical rewritten fixture path form: `/Volumes/X9/matches/<match-slug>/shooters/<s_id>/raw/<filename>`.
- Canonical raw layout: `/Volumes/X9/raw/<year>-<match-slug>/<shooter>/<hand|head>/`.
- The ten final match slugs: `blacksmith-handgun-open-2026`, `bofors-bombardment-2026`, `ess-black-handgun-2026`, `hfo-masters-2026`, `jinglebell-challenge-2026`, `oden-cup-2026`, `stockholm-ipsc-open-2026`, `tallmilan-2025`, `tallmilan-2026`, `vads-easter-shoot-2026`.
- The share `blacksmith-handgun-2026` (scoreboard 27046, 8 stages) and X9's `2026-black-handgun` (scoreboard 25460, 12 stages) are DIFFERENT EVENTS. Never merge, alias, or cross-link them.
- Filenames never change during this migration. Only directories move. Every rewrite is a pure directory substitution.
- No destructive filesystem operation runs outside Task 14, which is gated on human approval.

---

## Part A -- Repo changes (PR-able, touches no data)

### Task 1: Shared `source_video` resolver

**Files:**
- Create: `src/splitsmith/fixture_sources.py`
- Test: `tests/test_fixture_sources.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MissingSourceVideoError(RuntimeError)`; `resolve_source_video(truth: dict, fixture: str, *, allow_missing: bool = False) -> Path | None`. Returns the Path when reachable, `None` only when `allow_missing=True` and it is not, raises `MissingSourceVideoError` otherwise. Tasks 2 and 3 import both names.

- [ ] **Step 1: Write the failing test**

```python
"""One place decides what an unreachable fixture source video means.

Five scripts read ``source_video`` to pull frames from the original
recording, and each used to skip a fixture it could not reach. A skip is
invisible in a build log that scrolls, so the corpus a model was trained
on shrank without anyone deciding it should. These pin the loud default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.fixture_sources import MissingSourceVideoError, resolve_source_video


def test_returns_the_path_when_the_video_is_reachable(tmp_path: Path) -> None:
    video = tmp_path / "stage_1.mov"
    video.write_bytes(b"not really a video, but it exists")

    resolved = resolve_source_video({"source_video": str(video)}, "stage-shots-x-stage1-s0fe3d797")

    assert resolved == video


def test_raises_naming_the_fixture_and_the_path_when_unreachable(tmp_path: Path) -> None:
    missing = tmp_path / "unmounted" / "stage_1.mov"

    with pytest.raises(MissingSourceVideoError) as excinfo:
        resolve_source_video({"source_video": str(missing)}, "stage-shots-x-stage1-s0fe3d797")

    message = str(excinfo.value)
    assert "stage-shots-x-stage1-s0fe3d797" in message
    assert str(missing) in message
    assert "--allow-missing-video" in message


def test_raises_when_the_fixture_has_no_source_video_at_all() -> None:
    with pytest.raises(MissingSourceVideoError):
        resolve_source_video({}, "stage-shots-x-stage1-s0fe3d797")

    with pytest.raises(MissingSourceVideoError):
        resolve_source_video({"source_video": ""}, "stage-shots-x-stage1-s0fe3d797")


def test_allow_missing_downgrades_both_failures_to_none(tmp_path: Path) -> None:
    missing = tmp_path / "unmounted" / "stage_1.mov"

    assert resolve_source_video({"source_video": str(missing)}, "fix", allow_missing=True) is None
    assert resolve_source_video({}, "fix", allow_missing=True) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fixture_sources.py -n0 -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.fixture_sources'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Resolve a fixture's ``source_video``, loudly.

Five scripts (``build_ensemble_artifacts``, ``regression_voter_e``,
``build_sweep_signals``, ``probe_visual_voter``,
``sweep_multiframe_voter_e``) read ``source_video`` off a fixture's audit
JSON to pull frames out of the original recording. Each used to skip a
fixture whose video could not be reached, which silently shrinks the
corpus a model is built from -- a build over half a corpus looks exactly
like a build over all of it. This module is the single place that
decision lives, and its default is to fail.
"""

from __future__ import annotations

from pathlib import Path


class MissingSourceVideoError(RuntimeError):
    """A fixture's ``source_video`` is absent from the JSON or unreachable on disk."""


def resolve_source_video(
    truth: dict,
    fixture: str,
    *,
    allow_missing: bool = False,
) -> Path | None:
    """Return the reachable ``source_video`` Path for ``fixture``.

    Raises :class:`MissingSourceVideoError` when the fixture names no
    video or names one that is not on disk. ``allow_missing=True``
    downgrades both cases to ``None`` for callers that have explicitly
    opted into a partial corpus.
    """
    raw = truth.get("source_video") or ""
    if not raw:
        if allow_missing:
            return None
        raise MissingSourceVideoError(
            f"{fixture}: fixture JSON has no source_video. "
            f"Pass --allow-missing-video to proceed without it."
        )

    path = Path(raw)
    if not path.exists():
        if allow_missing:
            return None
        raise MissingSourceVideoError(
            f"{fixture}: source_video {path} is unreachable. "
            f"Mount the volume holding it (the corpus lives under /Volumes/X9), "
            f"or pass --allow-missing-video to proceed without it."
        )
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_fixture_sources.py -n0 -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/fixture_sources.py tests/test_fixture_sources.py
git commit -m "feat(lab): one loud resolver for a fixture's source_video"
```

---

### Task 2: Artifact build fails on an unreachable source video

**Files:**
- Modify: `scripts/build_ensemble_artifacts.py` (`_build_visual_universe` around `:639-650`, `build_artifacts` at `:891`, `main` at `:1125`)
- Test: `tests/test_build_ensemble_preconditions.py` (append)

**Interfaces:**
- Consumes: `splitsmith.fixture_sources.resolve_source_video`, `MissingSourceVideoError` (Task 1).
- Produces: `build_artifacts(..., allow_missing_video: bool = False)`; CLI flag `--allow-missing-video`. Task 13's rebuild gate invokes the CLI without the flag.

Note: `src/splitsmith/ui/server.py:15368` calls `build_artifacts` in-process for the "Rebuild calibration" button and passes no such argument, so it inherits the strict default. That is intended -- an unmounted X9 must fail that job rather than quietly retrain on a partial corpus. `BuildError` and `MissingSourceVideoError` are both real exceptions, so the job records a failure (`SystemExit` would not; see #945).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_build_ensemble_preconditions.py`:

```python
def test_visual_universe_raises_when_a_source_video_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unmounted drive must fail the build, not shrink the corpus.

    The old behaviour appended the fixture to ``skipped_no_video`` and
    carried on, so a build over 83 of 161 fixtures produced artifacts
    that looked exactly like a full build.
    """
    from splitsmith.fixture_sources import MissingSourceVideoError

    mod = _build_mod()
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    fix = "stage-shots-hfo-masters-2026-stage1-s0fe3d797"
    (fixtures_dir / f"{fix}.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "beep_time": 1.0,
                "shots": [{"time": 2.0}],
                "source_video": str(tmp_path / "unmounted" / "stage_1.mov"),
                "fixture_window_in_source": [0.0, 10.0],
            }
        )
    )
    (fixtures_dir / f"{fix}.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(mod, "FIXTURES_DIR", fixtures_dir)

    with pytest.raises(MissingSourceVideoError) as excinfo:
        mod._build_visual_universe([fix], 75.0, log=lambda _msg: None)

    assert fix in str(excinfo.value)


def test_visual_universe_skips_unreachable_video_when_explicitly_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _build_mod()
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    fix = "stage-shots-hfo-masters-2026-stage1-s0fe3d797"
    (fixtures_dir / f"{fix}.json").write_text(
        json.dumps(
            {
                "stage_number": 1,
                "beep_time": 1.0,
                "shots": [{"time": 2.0}],
                "source_video": str(tmp_path / "unmounted" / "stage_1.mov"),
                "fixture_window_in_source": [0.0, 10.0],
            }
        )
    )
    (fixtures_dir / f"{fix}.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(mod, "FIXTURES_DIR", fixtures_dir)

    rows, missing = mod._build_visual_universe(
        [fix], 75.0, allow_missing_video=True, log=lambda _msg: None
    )

    assert rows == []
    assert missing == [fix]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_ensemble_preconditions.py -n0 -v -k source_video`
Expected: FAIL. The first test fails because no exception is raised (the fixture is skipped); the second fails with `TypeError: _build_visual_universe() got an unexpected keyword argument 'allow_missing_video'`.

- [ ] **Step 3: Write the implementation**

In `scripts/build_ensemble_artifacts.py`, add to the import block near the other `splitsmith` imports:

```python
from splitsmith.fixture_sources import resolve_source_video
```

Change the `_build_visual_universe` signature to accept the flag:

```python
def _build_visual_universe(
    fixtures: list[str],
    tolerance_ms: float,
    *,
    rebuild: bool = False,
    allow_missing_video: bool = False,
    log: Callable[[str], None] = print,
) -> tuple[list[dict], list[str]]:
```

Replace the two skip blocks (currently `source_video_str = truth.get("source_video") or ""` through the second `continue`) with:

```python
        window = truth.get("fixture_window_in_source") or [0.0, 0.0]
        source_video = resolve_source_video(truth, fix, allow_missing=allow_missing_video)
        if source_video is None:
            skipped_no_video.append(fix)
            continue
```

Thread the flag through `build_artifacts`:

```python
def build_artifacts(
    fixtures: list[str] | None = None,
    *,
    target_recall: float = 0.95,
    tolerance_ms: float = 75.0,
    mining_cap_ratio: float = DEFAULT_NEG_CAP_RATIO,
    use_mined_negatives: bool = False,
    voter_e: bool = True,
    voter_e_target_recall: float = DEFAULT_VOTER_E_TARGET_RECALL,
    rebuild_visual: bool = False,
    allow_missing_video: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
```

and at its `_build_visual_universe` call site:

```python
        visual_universe, missing_video = _build_visual_universe(
            fixtures,
            tolerance_ms,
            rebuild=rebuild_visual,
            allow_missing_video=allow_missing_video,
            log=log,
        )
```

In `main()`, register the flag and pass it:

```python
    p.add_argument(
        "--allow-missing-video",
        action="store_true",
        help=(
            "Build Voter E features from only the fixtures whose source_video "
            "is reachable. OFF by default: a partial corpus produces artifacts "
            "indistinguishable from a full build, so an unmounted drive must "
            "fail the build rather than silently shrink it."
        ),
    )
```

and add `allow_missing_video=args.allow_missing_video,` to the `build_artifacts(...)` call in `main()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_build_ensemble_preconditions.py -n0 -v`
Expected: all pass, including the pre-existing precondition tests.

- [ ] **Step 5: Prove the test would have caught the old behaviour**

Temporarily revert the `_build_visual_universe` body to the old `skipped_no_video.append(fix); continue` form, run `uv run pytest tests/test_build_ensemble_preconditions.py -n0 -k unreachable`, and confirm it FAILS. Restore the fix. Per CLAUDE.md, a test that passes against the pre-change code is not evidence of anything.

- [ ] **Step 6: Commit**

```bash
git add scripts/build_ensemble_artifacts.py tests/test_build_ensemble_preconditions.py
git commit -m "fix(model): an unreachable source_video fails the build instead of shrinking the corpus"
```

---

### Task 3: The four eval/sweep scripts get the same flag

**Files:**
- Modify: `scripts/regression_voter_e.py:110`, `scripts/build_sweep_signals.py:117`, `scripts/probe_visual_voter.py:68`, `scripts/sweep_multiframe_voter_e.py:108-112`
- Test: `tests/test_fixture_source_cli_contract.py`

**Interfaces:**
- Consumes: `resolve_source_video`, `MissingSourceVideoError` (Task 1).
- Produces: nothing other tasks import.

- [ ] **Step 1: Write the failing test**

```python
"""Every script that reads source_video offers the same opt-out.

Four scripts besides the artifact build resolve a fixture's source
video. If one of them keeps skipping silently, the corpus can still
shrink under it -- and the inconsistency is exactly the kind of thing
that gets rediscovered a year later.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = [
    "regression_voter_e",
    "build_sweep_signals",
    "probe_visual_voter",
    "sweep_multiframe_voter_e",
]


def _load(name: str):
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_imports_the_shared_resolver(name: str) -> None:
    mod = _load(name)
    assert hasattr(mod, "resolve_source_video"), (
        f"{name} must resolve source_video through splitsmith.fixture_sources "
        "so the skip decision has one implementation"
    )


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_exposes_allow_missing_video(name: str) -> None:
    mod = _load(name)
    parser = mod.build_parser()
    options = {action.option_strings[0] for action in parser._actions if action.option_strings}
    assert "--allow-missing-video" in options
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fixture_source_cli_contract.py -n0 -v`
Expected: FAIL -- `AttributeError: module has no attribute 'resolve_source_video'` and `'build_parser'`.

- [ ] **Step 3: Write the implementation**

In each of the four scripts:

1. Add the import beside the existing `splitsmith` imports:

```python
from splitsmith.fixture_sources import resolve_source_video
```

2. Extract the existing inline `argparse.ArgumentParser(...)` construction in `main()` into a module-level `build_parser() -> argparse.ArgumentParser` that returns the fully-configured parser, and have `main()` call `parser = build_parser()`. Do not change any existing flag, default, or help string while moving them.

3. Add to each `build_parser()`:

```python
    p.add_argument(
        "--allow-missing-video",
        action="store_true",
        help=(
            "Process only the fixtures whose source_video is reachable. "
            "OFF by default so an unmounted volume fails loudly."
        ),
    )
```

4. Replace each script's existing reachability check with the shared resolver, threading `args.allow_missing_video` down to the call site:

- `regression_voter_e.py:110`, replace `if not d.get("source_video") or not Path(d["source_video"]).exists(): continue` with:

```python
        source_video = resolve_source_video(d, fix, allow_missing=allow_missing_video)
        if source_video is None:
            continue
```

- `build_sweep_signals.py:117`, inside `_locate_source_video`, replace the existence check with the same two lines, and give `_locate_source_video` an `allow_missing_video: bool` keyword parameter passed from its caller at `:234`.

- `probe_visual_voter.py:68`, replace `src = data.get("source_video")` plus its guard with the resolver call.

- `sweep_multiframe_voter_e.py:108-112`, replace the `source_video_str` block and its `.exists()` guard with the resolver call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fixture_source_cli_contract.py -n0 -v`
Expected: 8 passed.

- [ ] **Step 5: Confirm nothing else broke**

Run: `uv run pytest tests/ -x -q -k "voter or sweep or ensemble"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/regression_voter_e.py scripts/build_sweep_signals.py scripts/probe_visual_voter.py scripts/sweep_multiframe_voter_e.py tests/test_fixture_source_cli_contract.py
git commit -m "fix(lab): the four eval scripts resolve source_video through the shared loud resolver"
```

---

### Task 4: Fixture path rewrite migration

**Files:**
- Create: `scripts/migrate_fixtures_raw_root.py`
- Test: `tests/test_migrate_fixtures_raw_root.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `rewrite_source_video(source_video: str, mapping: dict[str, str]) -> RewriteOutcome` where `RewriteOutcome` is a Pydantic model with fields `original: str`, `rewritten: str | None`, `status: Literal["rewritten", "already_canonical", "unmapped"]`. Task 13 runs this script.

The mapping is directory-prefix to directory-prefix. Filenames are never touched.

- [ ] **Step 1: Write the failing test**

```python
"""Fixture source paths move by directory substitution, never by guess.

78 of 161 fixtures point at directories this consolidation retires. The
rewrite has to be mechanical and auditable: a prefix it does not know
about is reported, never rewritten to something plausible.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


def _mod():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module("migrate_fixtures_raw_root")
    finally:
        sys.path.pop(0)


MAPPING = {
    "/Volumes/X9/matches/vads-easter-shoot-2026-anton/raw": (
        "/Volumes/X9/matches/vads-easter-shoot-2026/shooters/s_9540b345/raw"
    ),
    "/Volumes/mathias/skytte/video/raw/tallmilan-2026/martin/handheld": (
        "/Volumes/X9/matches/tallmilan-2026/shooters/s_36ed6e4e/raw"
    ),
}


def test_rewrites_a_legacy_project_path_keeping_the_filename() -> None:
    mod = _mod()
    outcome = mod.rewrite_source_video(
        "/Volumes/X9/matches/vads-easter-shoot-2026-anton/raw/IMG_1295.mov", MAPPING
    )
    assert outcome.status == "rewritten"
    assert outcome.rewritten == (
        "/Volumes/X9/matches/vads-easter-shoot-2026/shooters/s_9540b345/raw/IMG_1295.mov"
    )


def test_rewrites_a_share_path_to_the_shooter_raw_form() -> None:
    mod = _mod()
    outcome = mod.rewrite_source_video(
        "/Volumes/mathias/skytte/video/raw/tallmilan-2026/martin/handheld/martin_stage_4.MOV",
        MAPPING,
    )
    assert outcome.status == "rewritten"
    assert outcome.rewritten == (
        "/Volumes/X9/matches/tallmilan-2026/shooters/s_36ed6e4e/raw/martin_stage_4.MOV"
    )


def test_a_path_already_in_canonical_form_is_left_alone() -> None:
    mod = _mod()
    canonical = "/Volumes/X9/matches/hfo-masters-2026/shooters/s_f88d8aa0/raw/IMG_9001.MOV"
    outcome = mod.rewrite_source_video(canonical, MAPPING)
    assert outcome.status == "already_canonical"
    assert outcome.rewritten is None


def test_an_unknown_prefix_is_reported_never_guessed() -> None:
    mod = _mod()
    outcome = mod.rewrite_source_video("/Users/mathias/Downloads/Gone/IMG_0001.MOV", MAPPING)
    assert outcome.status == "unmapped"
    assert outcome.rewritten is None


def test_the_run_is_idempotent_over_a_fixture_tree(tmp_path: Path) -> None:
    mod = _mod()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    doc = {
        "stage_number": 1,
        "source_video": "/Volumes/X9/matches/vads-easter-shoot-2026-anton/raw/IMG_1295.mov",
    }
    target = fixtures / "stage-shots-vads-stage1-s0fe3d797.json"
    target.write_text(json.dumps(doc))

    first = mod.run(fixtures_root=fixtures, mapping=MAPPING, dry_run=False)
    second = mod.run(fixtures_root=fixtures, mapping=MAPPING, dry_run=False)

    assert first.rewritten == 1
    assert second.rewritten == 0
    assert second.already_canonical == 1
    written = json.loads(target.read_text())
    assert written["source_video"] == (
        "/Volumes/X9/matches/vads-easter-shoot-2026/shooters/s_9540b345/raw/IMG_1295.mov"
    )
    assert written["stage_number"] == 1


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    mod = _mod()
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    original = "/Volumes/X9/matches/vads-easter-shoot-2026-anton/raw/IMG_1295.mov"
    target = fixtures / "stage-shots-vads-stage1-s0fe3d797.json"
    target.write_text(json.dumps({"source_video": original}))

    report = mod.run(fixtures_root=fixtures, mapping=MAPPING, dry_run=True)

    assert report.rewritten == 1
    assert json.loads(target.read_text())["source_video"] == original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_migrate_fixtures_raw_root.py -n0 -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'migrate_fixtures_raw_root'`

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""Repoint fixture ``source_video`` paths at the consolidated X9 corpus.

The consolidation retires the legacy single-shooter project folders and
moves raw footage onto X9, which invalidates 78 of the 161 fixture
``source_video`` values. The canonical form after this migration is

    /Volumes/X9/matches/<match-slug>/shooters/<s_id>/raw/<filename>

-- the same form the 83 already-correct fixtures use. It resolves through
the project's own ``raw/`` symlink, so a future raw reorganisation is
absorbed by relinking instead of another fixture rewrite.

The rewrite is a pure directory substitution: filenames never change
during the consolidation, so a fixture whose directory prefix is not in
the mapping is REPORTED, never rewritten to something plausible.

Usage:
    uv run python scripts/migrate_fixtures_raw_root.py --dry-run
    uv run python scripts/migrate_fixtures_raw_root.py
    uv run python scripts/migrate_fixtures_raw_root.py --mapping build/consolidation/raw_mapping.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

FIXTURES_ROOT = Path(__file__).parent.parent / "tests" / "fixtures"
CANONICAL_PREFIX = "/Volumes/X9/matches/"
CANONICAL_MARKER = "/shooters/"


class RewriteOutcome(BaseModel):
    """What the rewrite decided for one ``source_video`` value."""

    original: str
    rewritten: str | None
    status: Literal["rewritten", "already_canonical", "unmapped"]


class RunReport(BaseModel):
    """Aggregate result of one pass over the fixture tree."""

    rewritten: int = 0
    already_canonical: int = 0
    unmapped: int = 0
    unmapped_paths: list[str] = []


def rewrite_source_video(source_video: str, mapping: dict[str, str]) -> RewriteOutcome:
    """Map one ``source_video`` onto its consolidated location.

    ``mapping`` is directory-prefix to directory-prefix. The longest
    matching prefix wins, so a mapping may contain both a match-level and
    a shooter-level entry without ambiguity.
    """
    path = Path(source_video)
    parent = str(path.parent)

    if source_video.startswith(CANONICAL_PREFIX) and CANONICAL_MARKER in source_video:
        return RewriteOutcome(original=source_video, rewritten=None, status="already_canonical")

    matches = [old for old in mapping if parent == old or parent.startswith(f"{old}/")]
    if not matches:
        return RewriteOutcome(original=source_video, rewritten=None, status="unmapped")

    longest = max(matches, key=len)
    suffix = parent[len(longest) :].lstrip("/")
    new_parent = Path(mapping[longest]) / suffix if suffix else Path(mapping[longest])
    return RewriteOutcome(
        original=source_video,
        rewritten=str(new_parent / path.name),
        status="rewritten",
    )


def run(*, fixtures_root: Path, mapping: dict[str, str], dry_run: bool) -> RunReport:
    """Rewrite every fixture under ``fixtures_root``. Idempotent."""
    report = RunReport()
    for fixture_path in sorted(fixtures_root.glob("*.json")):
        try:
            doc = json.loads(fixture_path.read_text())
        except json.JSONDecodeError:
            continue
        if not isinstance(doc, dict) or not doc.get("source_video"):
            continue

        outcome = rewrite_source_video(doc["source_video"], mapping)
        if outcome.status == "already_canonical":
            report.already_canonical += 1
            continue
        if outcome.status == "unmapped":
            report.unmapped += 1
            report.unmapped_paths.append(outcome.original)
            continue

        report.rewritten += 1
        if not dry_run:
            doc["source_video"] = outcome.rewritten
            fixture_path.write_text(json.dumps(doc, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    parser.add_argument("--fixtures-root", type=Path, default=FIXTURES_ROOT)
    parser.add_argument(
        "--mapping",
        type=Path,
        required=True,
        help="JSON object of old directory prefix -> new directory prefix, "
        "as emitted by scripts/consolidate_matches.py plan.",
    )
    args = parser.parse_args()

    mapping = json.loads(args.mapping.read_text())
    report = run(fixtures_root=args.fixtures_root, mapping=mapping, dry_run=args.dry_run)

    print(f"rewritten:         {report.rewritten}")
    print(f"already canonical: {report.already_canonical}")
    print(f"unmapped:          {report.unmapped}")
    for path in report.unmapped_paths:
        print(f"  unmapped: {path}")
    if report.unmapped:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_migrate_fixtures_raw_root.py -n0 -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_fixtures_raw_root.py tests/test_migrate_fixtures_raw_root.py
git commit -m "feat(lab): fixture source_video rewrite for the consolidated corpus"
```

---

## Part B -- Consolidation library

### Task 5: Inventory

**Files:**
- Create: `scripts/consolidate_lib.py`
- Test: `tests/test_consolidate_inventory.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ShooterInventory`, `ProjectInventory` (Pydantic); `inventory_project(root: Path) -> ProjectInventory`. Tasks 6, 7 and 8 import from this module.

`ProjectInventory` fields: `root: Path`, `kind: Literal["legacy", "match"]`, `match_id: str | None`, `shooters: list[ShooterInventory]`.
`ShooterInventory` fields: `slug: str | None`, `root: Path`, `shooter_token: str | None`, `audit_docs: dict[str, str]` (filename to sha256), `media_counts: dict[str, int]`, `media_bytes: dict[str, int]`, `link_targets: dict[str, str]`, `broken_links: list[str]`.

- [ ] **Step 1: Write the failing test**

```python
"""The inventory is the only record of what existed before deletion.

Every later comparison -- did a doc survive, did media shrink, is a link
broken -- reads this. It must describe a legacy single-shooter project
and a merged multi-shooter match in the same shape, so the reconciler
does not care which it was handed.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path


def _mod():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module("consolidate_lib")
    finally:
        sys.path.pop(0)


def _legacy_project(root: Path, *, token: str | None = "s97dcec94") -> Path:
    root.mkdir(parents=True)
    project = {"schema_version": 2, "name": "Tallmilan 2026", "stages": []}
    if token is not None:
        project["shooter_token"] = token
    (root / "project.json").write_text(json.dumps(project))
    (root / "audit").mkdir()
    (root / "audit" / "stage1.json").write_text('{"stage_number": 1}')
    (root / "audit" / "stage1.json.bak").write_text('{"stage_number": 1, "stale": true}')
    (root / "trimmed").mkdir()
    (root / "trimmed" / "stage1_trimmed.mp4").write_bytes(b"0" * 128)
    (root / "raw").mkdir()
    return root


def test_inventories_a_legacy_project_as_a_single_shooter(tmp_path: Path) -> None:
    mod = _mod()
    root = _legacy_project(tmp_path / "tallmilan-2026")

    inv = mod.inventory_project(root)

    assert inv.kind == "legacy"
    assert len(inv.shooters) == 1
    shooter = inv.shooters[0]
    assert shooter.shooter_token == "s97dcec94"
    assert set(shooter.audit_docs) == {"stage1.json"}, ".bak files are not audit docs"
    assert shooter.audit_docs["stage1.json"] == hashlib.sha256(b'{"stage_number": 1}').hexdigest()
    assert shooter.media_counts["trimmed"] == 1
    assert shooter.media_bytes["trimmed"] == 128


def test_inventories_a_merged_match_shooter_by_shooter(tmp_path: Path) -> None:
    mod = _mod()
    match_root = tmp_path / "tallmilan-2026-merged"
    (match_root / "shooters").mkdir(parents=True)
    (match_root / "match.json").write_text(
        json.dumps({"schema_version": 4, "match_id": "tallmilan-2026-abc", "name": "Tallmilan 2026"})
    )
    _legacy_project(match_root / "shooters" / "s_aaa", token="s97dcec94")
    _legacy_project(match_root / "shooters" / "s_bbb", token="s36ed6e4e")

    inv = mod.inventory_project(match_root)

    assert inv.kind == "match"
    assert inv.match_id == "tallmilan-2026-abc"
    assert sorted(s.slug for s in inv.shooters) == ["s_aaa", "s_bbb"]


def test_records_broken_symlinks_by_name(tmp_path: Path) -> None:
    mod = _mod()
    root = _legacy_project(tmp_path / "blacksmith-2026")
    (root / "raw" / "IMG_2979.MOV").symlink_to(tmp_path / "gone" / "IMG_2979.MOV")
    real = tmp_path / "present.MOV"
    real.write_bytes(b"x")
    (root / "raw" / "IMG_2986.MOV").symlink_to(real)

    inv = mod.inventory_project(root)

    shooter = inv.shooters[0]
    assert shooter.broken_links == ["IMG_2979.MOV"]
    assert shooter.link_targets["IMG_2986.MOV"] == str(real)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_consolidate_inventory.py -n0 -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'consolidate_lib'`

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""Pure helpers for the one-off X9 corpus consolidation.

Inventory, reconciliation and verification live here as pure functions
over Pydantic models so they can be tested against ``tmp_path`` trees.
``scripts/consolidate_matches.py`` is the only caller that touches the
real filesystem.

See docs/superpowers/specs/2026-08-19-corpus-consolidation-design.md.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

MEDIA_DIRS = ("trimmed", "audio", "probes", "thumbs", "exports")


class ShooterInventory(BaseModel):
    """Everything about one shooter's data that must survive the migration."""

    slug: str | None
    root: Path
    shooter_token: str | None
    audit_docs: dict[str, str] = Field(default_factory=dict)
    media_counts: dict[str, int] = Field(default_factory=dict)
    media_bytes: dict[str, int] = Field(default_factory=dict)
    link_targets: dict[str, str] = Field(default_factory=dict)
    broken_links: list[str] = Field(default_factory=list)


class ProjectInventory(BaseModel):
    """A legacy project or a merged match, described identically."""

    root: Path
    kind: Literal["legacy", "match"]
    match_id: str | None = None
    shooters: list[ShooterInventory] = Field(default_factory=list)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _inventory_shooter(root: Path, slug: str | None) -> ShooterInventory:
    project_file = root / "project.json"
    token: str | None = None
    if project_file.exists():
        doc = json.loads(project_file.read_text())
        token = doc.get("shooter_token")

    audit_dir = root / "audit"
    audit_docs = (
        {path.name: _sha256(path) for path in sorted(audit_dir.glob("*.json"))}
        if audit_dir.is_dir()
        else {}
    )

    media_counts: dict[str, int] = {}
    media_bytes: dict[str, int] = {}
    for name in MEDIA_DIRS:
        directory = root / name
        if not directory.is_dir():
            media_counts[name] = 0
            media_bytes[name] = 0
            continue
        files = [p for p in directory.rglob("*") if p.is_file()]
        media_counts[name] = len(files)
        media_bytes[name] = sum(p.stat().st_size for p in files)

    link_targets: dict[str, str] = {}
    broken_links: list[str] = []
    raw_dir = root / "raw"
    if raw_dir.is_dir():
        for entry in sorted(raw_dir.iterdir()):
            if entry.name == ".DS_Store":
                continue
            if entry.is_symlink():
                link_targets[entry.name] = str(Path(entry.readlink()))
            if not entry.exists():
                broken_links.append(entry.name)

    return ShooterInventory(
        slug=slug,
        root=root,
        shooter_token=token,
        audit_docs=audit_docs,
        media_counts=media_counts,
        media_bytes=media_bytes,
        link_targets=link_targets,
        broken_links=broken_links,
    )


def inventory_project(root: Path) -> ProjectInventory:
    """Describe a project at ``root``, legacy or merged, in one shape.

    ``.bak`` files are deliberately not audit docs: the migration must
    never promote a stale backup into the position of a real document.
    """
    match_file = root / "match.json"
    if match_file.exists():
        doc = json.loads(match_file.read_text())
        shooters_dir = root / "shooters"
        shooters = [
            _inventory_shooter(child, child.name)
            for child in sorted(shooters_dir.iterdir())
            if child.is_dir()
        ] if shooters_dir.is_dir() else []
        return ProjectInventory(
            root=root, kind="match", match_id=doc.get("match_id"), shooters=shooters
        )

    return ProjectInventory(root=root, kind="legacy", shooters=[_inventory_shooter(root, None)])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_consolidate_inventory.py -n0 -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/consolidate_lib.py tests/test_consolidate_inventory.py
git commit -m "feat(migration): inventory legacy projects and merged matches in one shape"
```

---

### Task 6: Reconciliation rules

**Files:**
- Modify: `scripts/consolidate_lib.py` (append)
- Test: `tests/test_consolidate_reconcile.py`

**Interfaces:**
- Consumes: `ShooterInventory`, `ProjectInventory`, `inventory_project` (Task 5).
- Produces: `ReconcileAction` (Pydantic: `kind: Literal["copy_audit_doc", "copy_media", "set_shooter_token"]`, `source: Path`, `destination: Path`, `detail: str`); `SafetyViolation` (Pydantic: `source: Path`, `document: str`, `reason: str`); `ReconcilePlan` (Pydantic: `actions: list[ReconcileAction]`, `violations: list[SafetyViolation]`, `deletable: bool`); `plan_reconcile(source: ShooterInventory, destination: ShooterInventory) -> ReconcilePlan`. Task 8 executes the plan.

`deletable` is True only when every audit doc in `source` has a counterpart in `destination` **after** the planned actions are applied.

- [ ] **Step 1: Write the failing test**

```python
"""The four reconciliation rules, and the guard that outranks them.

Measured facts these encode: the merged copies hold newer audit docs
than their legacy sources (more audit_events, later timestamps), the X9
copies hold the media the home copies had stripped, and
blacksmith-handgun-open-2026's mathias shooter is missing 7 of 8 audit
docs that legacy blacksmith-2026 still has. A migration that assumes
containment destroys those 7.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def _mod():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module("consolidate_lib")
    finally:
        sys.path.pop(0)


def _shooter(root: Path, *, audits: dict[str, str], token: str | None = None, trimmed: int = 0) -> Path:
    root.mkdir(parents=True)
    project = {"schema_version": 2, "name": "M", "stages": []}
    if token is not None:
        project["shooter_token"] = token
    (root / "project.json").write_text(json.dumps(project))
    (root / "audit").mkdir()
    for name, body in audits.items():
        (root / "audit" / name).write_text(body)
    (root / "trimmed").mkdir()
    for index in range(trimmed):
        (root / "trimmed" / f"stage{index + 1}_trimmed.mp4").write_bytes(b"0" * 64)
    return root


def test_destination_wins_where_both_sides_have_the_doc(tmp_path: Path) -> None:
    mod = _mod()
    source = mod.inventory_project(
        _shooter(tmp_path / "legacy", audits={"stage1.json": '{"v": "old"}'})
    ).shooters[0]
    destination = mod.inventory_project(
        _shooter(tmp_path / "merged", audits={"stage1.json": '{"v": "new"}'})
    ).shooters[0]

    plan = mod.plan_reconcile(source, destination)

    assert [a for a in plan.actions if a.kind == "copy_audit_doc"] == []
    assert plan.deletable is True


def test_source_fills_a_gap_the_destination_has(tmp_path: Path) -> None:
    mod = _mod()
    source = mod.inventory_project(
        _shooter(
            tmp_path / "legacy",
            audits={f"stage{n}.json": json.dumps({"stage_number": n}) for n in range(1, 9)},
        )
    ).shooters[0]
    destination = mod.inventory_project(
        _shooter(tmp_path / "merged", audits={"stage4.json": json.dumps({"stage_number": 4})})
    ).shooters[0]

    plan = mod.plan_reconcile(source, destination)

    copied = sorted(a.destination.name for a in plan.actions if a.kind == "copy_audit_doc")
    assert copied == [f"stage{n}.json" for n in (1, 2, 3, 5, 6, 7, 8)]
    assert plan.deletable is True, "after the copies, nothing is left behind"


def test_media_is_unioned_with_the_destination_winning_collisions(tmp_path: Path) -> None:
    mod = _mod()
    source = mod.inventory_project(
        _shooter(tmp_path / "x9", audits={"stage1.json": "{}"}, trimmed=3)
    ).shooters[0]
    destination = mod.inventory_project(
        _shooter(tmp_path / "merged", audits={"stage1.json": "{}"}, trimmed=1)
    ).shooters[0]

    plan = mod.plan_reconcile(source, destination)

    copied = sorted(a.destination.name for a in plan.actions if a.kind == "copy_media")
    assert copied == ["stage2_trimmed.mp4", "stage3_trimmed.mp4"]


def test_shooter_token_is_carried_over_when_the_destination_lacks_one(tmp_path: Path) -> None:
    mod = _mod()
    source = mod.inventory_project(
        _shooter(tmp_path / "home", audits={"stage1.json": "{}"}, token="s97dcec94")
    ).shooters[0]
    destination = mod.inventory_project(
        _shooter(tmp_path / "x9", audits={"stage1.json": "{}"}, token=None)
    ).shooters[0]

    plan = mod.plan_reconcile(source, destination)

    token_actions = [a for a in plan.actions if a.kind == "set_shooter_token"]
    assert len(token_actions) == 1
    assert token_actions[0].detail == "s97dcec94"


def test_an_existing_destination_token_is_never_overwritten(tmp_path: Path) -> None:
    mod = _mod()
    source = mod.inventory_project(
        _shooter(tmp_path / "home", audits={"stage1.json": "{}"}, token="s97dcec94")
    ).shooters[0]
    destination = mod.inventory_project(
        _shooter(tmp_path / "x9", audits={"stage1.json": "{}"}, token="s36ed6e4e")
    ).shooters[0]

    plan = mod.plan_reconcile(source, destination)

    assert [a for a in plan.actions if a.kind == "set_shooter_token"] == []


def test_a_bak_only_document_does_not_count_as_a_counterpart(tmp_path: Path) -> None:
    mod = _mod()
    source_root = _shooter(tmp_path / "legacy", audits={"stage1.json": '{"real": true}'})
    dest_root = _shooter(tmp_path / "merged", audits={})
    (dest_root / "audit" / "stage1.json.bak").write_text('{"stale": true}')

    plan = mod.plan_reconcile(
        mod.inventory_project(source_root).shooters[0],
        mod.inventory_project(dest_root).shooters[0],
    )

    copied = [a for a in plan.actions if a.kind == "copy_audit_doc"]
    assert len(copied) == 1
    assert copied[0].destination.name == "stage1.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_consolidate_reconcile.py -n0 -v`
Expected: FAIL, `AttributeError: module 'consolidate_lib' has no attribute 'plan_reconcile'`

- [ ] **Step 3: Write the implementation**

Append to `scripts/consolidate_lib.py`:

```python
class ReconcileAction(BaseModel):
    """One filesystem change the reconciler wants to make."""

    kind: Literal["copy_audit_doc", "copy_media", "set_shooter_token"]
    source: Path
    destination: Path
    detail: str = ""


class SafetyViolation(BaseModel):
    """A document that would be lost if the source were deleted."""

    source: Path
    document: str
    reason: str


class ReconcilePlan(BaseModel):
    """Actions to run, plus whether the source may then be deleted."""

    actions: list[ReconcileAction] = Field(default_factory=list)
    violations: list[SafetyViolation] = Field(default_factory=list)
    deletable: bool = False


def plan_reconcile(source: ShooterInventory, destination: ShooterInventory) -> ReconcilePlan:
    """Plan the merge of one source shooter into its destination.

    Rules, in order:

    1. ``shooter_token`` is carried over when the source has one and the
       destination does not. An existing destination token is never
       overwritten.
    2. Audit docs: the destination wins where both sides have one -- the
       merged copies were measured to carry strictly more ``audit_events``
       with later timestamps. Where only the source has one, copy it in.
       ``.bak`` files are never counterparts and never promoted.
    3. Media: union, destination wins on name collision.
    4. The source may be deleted only if every document it holds has a
       counterpart in the destination once these actions are applied.
    """
    plan = ReconcilePlan()

    if source.shooter_token and not destination.shooter_token:
        plan.actions.append(
            ReconcileAction(
                kind="set_shooter_token",
                source=source.root / "project.json",
                destination=destination.root / "project.json",
                detail=source.shooter_token,
            )
        )

    for name in sorted(source.audit_docs):
        if name in destination.audit_docs:
            continue
        plan.actions.append(
            ReconcileAction(
                kind="copy_audit_doc",
                source=source.root / "audit" / name,
                destination=destination.root / "audit" / name,
                detail="source-only document",
            )
        )

    for media_dir in MEDIA_DIRS:
        source_dir = source.root / media_dir
        if not source_dir.is_dir():
            continue
        destination_dir = destination.root / media_dir
        existing = {p.name for p in destination_dir.iterdir()} if destination_dir.is_dir() else set()
        for entry in sorted(source_dir.iterdir()):
            if not entry.is_file() or entry.name in existing:
                continue
            plan.actions.append(
                ReconcileAction(
                    kind="copy_media",
                    source=entry,
                    destination=destination_dir / entry.name,
                    detail=media_dir,
                )
            )

    planned_docs = {a.destination.name for a in plan.actions if a.kind == "copy_audit_doc"}
    for name in sorted(source.audit_docs):
        if name not in destination.audit_docs and name not in planned_docs:
            plan.violations.append(
                SafetyViolation(
                    source=source.root,
                    document=name,
                    reason="present in source, absent from destination, not scheduled for copy",
                )
            )
    plan.deletable = not plan.violations
    return plan
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_consolidate_reconcile.py -n0 -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/consolidate_lib.py tests/test_consolidate_reconcile.py
git commit -m "feat(migration): reconciliation rules with a no-delete-without-counterpart guard"
```

---

### Task 7: Verification predicates

**Files:**
- Modify: `scripts/consolidate_lib.py` (append)
- Test: `tests/test_consolidate_verify.py`

**Interfaces:**
- Consumes: `ProjectInventory`, `ShooterInventory`, `inventory_project` (Task 5).
- Produces: `VerifyFinding` (Pydantic: `check: str`, `subject: str`, `detail: str`); `verify_documents_survived(before: ProjectInventory, after: ProjectInventory) -> list[VerifyFinding]`; `verify_media_not_shrunk(before: ProjectInventory, after: ProjectInventory) -> list[VerifyFinding]`; `verify_no_broken_links(after: ProjectInventory) -> list[VerifyFinding]`; `verify_tokens_preserved(before: ProjectInventory, after: ProjectInventory) -> list[VerifyFinding]`. An empty list means the check passed. Task 8 aggregates them.

Shooters are paired by `shooter_token` when both sides have one, falling back to `slug`.

- [ ] **Step 1: Write the failing test**

```python
"""Verification is what stands between the migration and rm -rf.

Each predicate returns findings, never a bool: the report has to say
which document, which shooter, which byte count, or it cannot be read by
a human deciding whether to delete 3 GB of originals.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def _mod():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module("consolidate_lib")
    finally:
        sys.path.pop(0)


def _match(root: Path, shooters: dict[str, dict]) -> Path:
    (root / "shooters").mkdir(parents=True)
    (root / "match.json").write_text(json.dumps({"match_id": "m-1", "name": "M"}))
    for slug, spec in shooters.items():
        shooter = root / "shooters" / slug
        shooter.mkdir()
        project = {"schema_version": 2, "name": "M", "stages": []}
        if spec.get("token"):
            project["shooter_token"] = spec["token"]
        (shooter / "project.json").write_text(json.dumps(project))
        (shooter / "audit").mkdir()
        for name in spec.get("audits", []):
            (shooter / "audit" / name).write_text(json.dumps({"doc": name}))
        (shooter / "trimmed").mkdir()
        for index in range(spec.get("trimmed", 0)):
            (shooter / "trimmed" / f"stage{index + 1}_trimmed.mp4").write_bytes(b"0" * 64)
        (shooter / "raw").mkdir()
    return root


def test_a_lost_document_is_reported_by_name(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "s97dcec94", "audits": ["stage1.json", "stage2.json"]}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_a": {"token": "s97dcec94", "audits": ["stage1.json"]}})
    )

    findings = mod.verify_documents_survived(before, after)

    assert len(findings) == 1
    assert findings[0].subject == "s97dcec94"
    assert "stage2.json" in findings[0].detail


def test_documents_that_all_survived_report_nothing(tmp_path: Path) -> None:
    mod = _mod()
    spec = {"s_a": {"token": "s97dcec94", "audits": ["stage1.json", "stage2.json"]}}
    before = mod.inventory_project(_match(tmp_path / "before", spec))
    after = mod.inventory_project(_match(tmp_path / "after", spec))

    assert mod.verify_documents_survived(before, after) == []


def test_shrunk_media_is_reported_with_both_byte_counts(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "t", "audits": [], "trimmed": 3}})
    )
    after = mod.inventory_project(
        _match(tmp_path / "after", {"s_a": {"token": "t", "audits": [], "trimmed": 1}})
    )

    findings = mod.verify_media_not_shrunk(before, after)

    assert len(findings) == 1
    assert "192" in findings[0].detail and "64" in findings[0].detail


def test_a_broken_link_after_migration_is_a_finding(tmp_path: Path) -> None:
    mod = _mod()
    root = _match(tmp_path / "after", {"s_a": {"token": "t", "audits": []}})
    (root / "shooters" / "s_a" / "raw" / "IMG_1.MOV").symlink_to(tmp_path / "gone" / "IMG_1.MOV")

    findings = mod.verify_no_broken_links(mod.inventory_project(root))

    assert len(findings) == 1
    assert "IMG_1.MOV" in findings[0].detail


def test_a_dropped_shooter_token_is_a_finding(tmp_path: Path) -> None:
    mod = _mod()
    before = mod.inventory_project(
        _match(tmp_path / "before", {"s_a": {"token": "s97dcec94", "audits": []}})
    )
    after = mod.inventory_project(_match(tmp_path / "after", {"s_a": {"audits": []}}))

    findings = mod.verify_tokens_preserved(before, after)

    assert len(findings) == 1
    assert "s97dcec94" in findings[0].detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_consolidate_verify.py -n0 -v`
Expected: FAIL, `AttributeError: module 'consolidate_lib' has no attribute 'verify_documents_survived'`

- [ ] **Step 3: Write the implementation**

Append to `scripts/consolidate_lib.py`:

```python
class VerifyFinding(BaseModel):
    """One thing that is wrong. An empty list of these means a pass."""

    check: str
    subject: str
    detail: str


def _pair_shooters(
    before: ProjectInventory, after: ProjectInventory
) -> list[tuple[ShooterInventory, ShooterInventory | None]]:
    """Pair by shooter_token where available, else by slug.

    Tokens are stable across the migration and slugs are not (a legacy
    project has no slug until it becomes a shooter), so the token is the
    stronger key when both sides carry one.
    """
    by_token = {s.shooter_token: s for s in after.shooters if s.shooter_token}
    by_slug = {s.slug: s for s in after.shooters if s.slug}
    pairs: list[tuple[ShooterInventory, ShooterInventory | None]] = []
    for shooter in before.shooters:
        counterpart = None
        if shooter.shooter_token:
            counterpart = by_token.get(shooter.shooter_token)
        if counterpart is None and shooter.slug:
            counterpart = by_slug.get(shooter.slug)
        pairs.append((shooter, counterpart))
    return pairs


def _subject(shooter: ShooterInventory) -> str:
    return shooter.shooter_token or shooter.slug or str(shooter.root)


def verify_documents_survived(
    before: ProjectInventory, after: ProjectInventory
) -> list[VerifyFinding]:
    """Every audit doc that existed before must exist after."""
    findings: list[VerifyFinding] = []
    for shooter, counterpart in _pair_shooters(before, after):
        if counterpart is None:
            findings.append(
                VerifyFinding(
                    check="documents_survived",
                    subject=_subject(shooter),
                    detail=f"no counterpart shooter found in {after.root}",
                )
            )
            continue
        lost = sorted(set(shooter.audit_docs) - set(counterpart.audit_docs))
        if lost:
            findings.append(
                VerifyFinding(
                    check="documents_survived",
                    subject=_subject(shooter),
                    detail=f"missing after migration: {', '.join(lost)}",
                )
            )
    return findings


def verify_media_not_shrunk(
    before: ProjectInventory, after: ProjectInventory
) -> list[VerifyFinding]:
    """Per media directory, the destination must hold at least as many bytes."""
    findings: list[VerifyFinding] = []
    for shooter, counterpart in _pair_shooters(before, after):
        if counterpart is None:
            continue
        for media_dir in MEDIA_DIRS:
            was = shooter.media_bytes.get(media_dir, 0)
            now = counterpart.media_bytes.get(media_dir, 0)
            if now < was:
                findings.append(
                    VerifyFinding(
                        check="media_not_shrunk",
                        subject=_subject(shooter),
                        detail=f"{media_dir}: {was} bytes before, {now} bytes after",
                    )
                )
    return findings


def verify_no_broken_links(after: ProjectInventory) -> list[VerifyFinding]:
    """No shooter may hold a raw/ entry that does not resolve."""
    return [
        VerifyFinding(
            check="no_broken_links",
            subject=_subject(shooter),
            detail=f"broken: {', '.join(shooter.broken_links)}",
        )
        for shooter in after.shooters
        if shooter.broken_links
    ]


def verify_tokens_preserved(
    before: ProjectInventory, after: ProjectInventory
) -> list[VerifyFinding]:
    """A shooter that had a token must still have it."""
    findings: list[VerifyFinding] = []
    for shooter, counterpart in _pair_shooters(before, after):
        if not shooter.shooter_token:
            continue
        if counterpart is None or counterpart.shooter_token != shooter.shooter_token:
            findings.append(
                VerifyFinding(
                    check="tokens_preserved",
                    subject=_subject(shooter),
                    detail=f"shooter_token {shooter.shooter_token} not present after migration",
                )
            )
    return findings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_consolidate_verify.py -n0 -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/consolidate_lib.py tests/test_consolidate_verify.py
git commit -m "feat(migration): verification predicates that name what is wrong"
```

---

### Task 8: The consolidation CLI

**Files:**
- Create: `scripts/consolidate_matches.py`
- Test: `tests/test_consolidate_cli.py`

**Interfaces:**
- Consumes: everything from `consolidate_lib` (Tasks 5-7); `splitsmith.sync.plan.build_push_plan`, `splitsmith.sync.state.load_sync_state`.
- Produces: CLI `inventory` / `reconcile` / `verify` subcommands writing JSON reports under `build/consolidation/`; `UnsafePlanError`; `AmbiguousShooterError`; `single_shooter(inventory: ProjectInventory) -> ShooterInventory`. Tasks 9-17 invoke the CLI.

`apply_reconcile(plan: ReconcilePlan, *, dry_run: bool) -> list[str]` lives here, not in the library: it is the only code that mutates the filesystem.

- [ ] **Step 1: Write the failing test**

```python
"""The CLI is the only thing allowed to touch the filesystem.

The rules were proven in isolation; what these pin is that apply
executes exactly what the plan said, refuses to run when the plan has a
safety violation, and preserves nanosecond mtimes -- an mtime that
changes re-uploads every trimmed mp4 in a synced match, because
sync/plan.py skips on (size, mtime_ns) with no content-hash fallback.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


def _cli():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module("consolidate_matches")
    finally:
        sys.path.pop(0)


def _lib():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        return importlib.import_module("consolidate_lib")
    finally:
        sys.path.pop(0)


def _shooter(root: Path, *, audits: dict[str, str], token: str | None = None) -> Path:
    root.mkdir(parents=True)
    project = {"schema_version": 2, "name": "M", "stages": []}
    if token is not None:
        project["shooter_token"] = token
    (root / "project.json").write_text(json.dumps(project))
    (root / "audit").mkdir()
    for name, body in audits.items():
        (root / "audit" / name).write_text(body)
    (root / "trimmed").mkdir()
    (root / "raw").mkdir()
    return root


def test_apply_copies_the_missing_documents(tmp_path: Path) -> None:
    cli, lib = _cli(), _lib()
    source = _shooter(tmp_path / "legacy", audits={f"stage{n}.json": json.dumps({"n": n}) for n in (1, 2)})
    destination = _shooter(tmp_path / "merged", audits={"stage1.json": json.dumps({"n": 1})})
    plan = lib.plan_reconcile(
        lib.inventory_project(source).shooters[0], lib.inventory_project(destination).shooters[0]
    )

    cli.apply_reconcile(plan, dry_run=False)

    assert (destination / "audit" / "stage2.json").exists()
    assert json.loads((destination / "audit" / "stage2.json").read_text()) == {"n": 2}


def test_apply_sets_the_shooter_token_without_disturbing_the_rest(tmp_path: Path) -> None:
    cli, lib = _cli(), _lib()
    source = _shooter(tmp_path / "home", audits={"stage1.json": "{}"}, token="s97dcec94")
    destination = _shooter(tmp_path / "x9", audits={"stage1.json": "{}"})
    plan = lib.plan_reconcile(
        lib.inventory_project(source).shooters[0], lib.inventory_project(destination).shooters[0]
    )

    cli.apply_reconcile(plan, dry_run=False)

    doc = json.loads((destination / "project.json").read_text())
    assert doc["shooter_token"] == "s97dcec94"
    assert doc["name"] == "M"
    assert doc["schema_version"] == 2


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    cli, lib = _cli(), _lib()
    source = _shooter(tmp_path / "legacy", audits={"stage1.json": "{}", "stage2.json": "{}"})
    destination = _shooter(tmp_path / "merged", audits={"stage1.json": "{}"})
    plan = lib.plan_reconcile(
        lib.inventory_project(source).shooters[0], lib.inventory_project(destination).shooters[0]
    )

    cli.apply_reconcile(plan, dry_run=True)

    assert not (destination / "audit" / "stage2.json").exists()


def test_reconcile_refuses_a_match_root_with_several_shooters(tmp_path: Path) -> None:
    """Passing a match root would silently reconcile against shooters[0].

    The destination of a reconcile is always one shooter. A match root
    holding three of them is a caller mistake, and picking the first is
    the worst possible response to it.
    """
    cli, lib = _cli(), _lib()
    match_root = tmp_path / "merged"
    (match_root / "shooters").mkdir(parents=True)
    (match_root / "match.json").write_text(json.dumps({"match_id": "m-1", "name": "M"}))
    _shooter(match_root / "shooters" / "s_a", audits={"stage1.json": "{}"})
    _shooter(match_root / "shooters" / "s_b", audits={"stage1.json": "{}"})

    with pytest.raises(cli.AmbiguousShooterError) as excinfo:
        cli.single_shooter(lib.inventory_project(match_root))

    assert "s_a" in str(excinfo.value)


def test_single_shooter_accepts_a_shooter_directory(tmp_path: Path) -> None:
    cli, lib = _cli(), _lib()
    root = _shooter(tmp_path / "s_a", audits={"stage1.json": "{}"}, token="s97dcec94")

    shooter = cli.single_shooter(lib.inventory_project(root))

    assert shooter.shooter_token == "s97dcec94"


def test_apply_refuses_a_plan_carrying_a_safety_violation(tmp_path: Path) -> None:
    cli, lib = _cli(), _lib()
    plan = lib.ReconcilePlan(
        actions=[],
        violations=[
            lib.SafetyViolation(source=tmp_path, document="stage3.json", reason="unscheduled")
        ],
        deletable=False,
    )

    with pytest.raises(cli.UnsafePlanError):
        cli.apply_reconcile(plan, dry_run=False)


def test_copied_media_keeps_its_nanosecond_mtime(tmp_path: Path) -> None:
    """A changed mtime re-uploads the file to the hosted instance."""
    import os

    cli, lib = _cli(), _lib()
    source = _shooter(tmp_path / "x9", audits={"stage1.json": "{}"})
    clip = source / "trimmed" / "stage1_trimmed.mp4"
    clip.write_bytes(b"0" * 64)
    os.utime(clip, ns=(1700000000123456789, 1700000000123456789))
    destination = _shooter(tmp_path / "merged", audits={"stage1.json": "{}"})
    plan = lib.plan_reconcile(
        lib.inventory_project(source).shooters[0], lib.inventory_project(destination).shooters[0]
    )

    cli.apply_reconcile(plan, dry_run=False)

    copied = destination / "trimmed" / "stage1_trimmed.mp4"
    assert copied.stat().st_mtime_ns == clip.stat().st_mtime_ns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_consolidate_cli.py -n0 -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'consolidate_matches'`

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""Drive the one-off consolidation of the match corpus onto X9.

The rules live in ``consolidate_lib`` as pure functions. This module is
the only code that mutates the filesystem, and every mutation is
preceded by a plan the caller can print.

Subcommands:
    inventory  Snapshot projects to build/consolidation/<label>.json
    reconcile  Plan (and with --apply, execute) a source -> destination merge
    verify     Compare two inventories and report every finding

Usage:
    uv run python scripts/consolidate_matches.py inventory --label phase0 \
        --root /Volumes/X9/matches --root ~/matches --root ~/Splitsmith
    uv run python scripts/consolidate_matches.py reconcile \
        --source /Volumes/X9/matches/blacksmith-2026 \
        --destination /Volumes/X9/matches/blacksmith-handgun-open-2026/shooters/s_ce10fa76
    uv run python scripts/consolidate_matches.py verify --before phase0 --after phase7
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from consolidate_lib import (  # noqa: E402
    ProjectInventory,
    ReconcilePlan,
    ShooterInventory,
    inventory_project,
    plan_reconcile,
    verify_documents_survived,
    verify_media_not_shrunk,
    verify_no_broken_links,
    verify_tokens_preserved,
)

REPORT_DIR = Path(__file__).parent.parent / "build" / "consolidation"


class UnsafePlanError(RuntimeError):
    """A plan with safety violations must never be applied."""


class AmbiguousShooterError(RuntimeError):
    """A reconcile target resolved to more than one shooter."""


def single_shooter(inventory: ProjectInventory) -> ShooterInventory:
    """The one shooter in ``inventory``, or raise.

    Reconciliation is always shooter-to-shooter. Handing this a match
    root would otherwise silently pick ``shooters[0]`` and merge one
    competitor's data into another's.
    """
    if len(inventory.shooters) != 1:
        slugs = ", ".join(str(s.slug) for s in inventory.shooters)
        raise AmbiguousShooterError(
            f"{inventory.root} holds {len(inventory.shooters)} shooters ({slugs}); "
            f"pass a specific shooters/<slug> directory instead"
        )
    return inventory.shooters[0]


def apply_reconcile(plan: ReconcilePlan, *, dry_run: bool) -> list[str]:
    """Execute ``plan``. Returns one human-readable line per action.

    ``shutil.copy2`` preserves nanosecond mtimes on APFS, which is load-
    bearing: ``sync/plan.py`` skips an upload only when size AND
    mtime_ns match what sync_state recorded, with no content-hash
    fallback, so a copy that loses precision re-uploads every trimmed
    mp4 in the match.
    """
    if plan.violations:
        raise UnsafePlanError(
            "refusing to apply a plan with "
            f"{len(plan.violations)} safety violation(s): "
            + "; ".join(f"{v.document} ({v.reason})" for v in plan.violations)
        )

    performed: list[str] = []
    for action in plan.actions:
        if action.kind == "set_shooter_token":
            performed.append(f"set shooter_token={action.detail} on {action.destination}")
            if not dry_run:
                doc = json.loads(action.destination.read_text())
                doc["shooter_token"] = action.detail
                action.destination.write_text(json.dumps(doc, indent=2) + "\n")
            continue

        performed.append(f"copy {action.source} -> {action.destination}")
        if not dry_run:
            action.destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(action.source, action.destination)
    return performed


def _iter_projects(root: Path) -> list[Path]:
    return [child for child in sorted(root.expanduser().iterdir()) if child.is_dir()]


def cmd_inventory(args: argparse.Namespace) -> None:
    projects: list[ProjectInventory] = []
    for root in args.root:
        for project_root in _iter_projects(root):
            if not (project_root / "project.json").exists() and not (
                project_root / "match.json"
            ).exists():
                continue
            projects.append(inventory_project(project_root))

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{args.label}.json"
    out.write_text(
        json.dumps([json.loads(p.model_dump_json()) for p in projects], indent=2) + "\n"
    )
    print(f"inventoried {len(projects)} project(s) -> {out}")
    for project in projects:
        broken = sum(len(s.broken_links) for s in project.shooters)
        docs = sum(len(s.audit_docs) for s in project.shooters)
        flag = f"  BROKEN LINKS: {broken}" if broken else ""
        print(f"  {project.root.name:38s} {project.kind:6s} shooters={len(project.shooters)} docs={docs}{flag}")


def cmd_reconcile(args: argparse.Namespace) -> None:
    source = single_shooter(inventory_project(args.source))
    destination = single_shooter(inventory_project(args.destination))
    plan = plan_reconcile(source, destination)

    for action in plan.actions:
        print(f"  {action.kind}: {action.source} -> {action.destination}")
    for violation in plan.violations:
        print(f"  VIOLATION: {violation.document} -- {violation.reason}")
    print(f"actions={len(plan.actions)} violations={len(plan.violations)} deletable={plan.deletable}")

    if args.apply:
        for line in apply_reconcile(plan, dry_run=False):
            print(f"  applied: {line}")


def cmd_verify(args: argparse.Namespace) -> None:
    before = [ProjectInventory(**doc) for doc in json.loads((REPORT_DIR / f"{args.before}.json").read_text())]
    after = [ProjectInventory(**doc) for doc in json.loads((REPORT_DIR / f"{args.after}.json").read_text())]
    after_by_name = {p.root.name: p for p in after}

    findings = []
    for project in before:
        counterpart = after_by_name.get(project.root.name)
        if counterpart is None:
            continue
        findings.extend(verify_documents_survived(project, counterpart))
        findings.extend(verify_media_not_shrunk(project, counterpart))
        findings.extend(verify_tokens_preserved(project, counterpart))
    for project in after:
        findings.extend(verify_no_broken_links(project))

    out = REPORT_DIR / f"verify-{args.before}-vs-{args.after}.json"
    out.write_text(json.dumps([json.loads(f.model_dump_json()) for f in findings], indent=2) + "\n")
    for finding in findings:
        print(f"  {finding.check}: {finding.subject} -- {finding.detail}")
    print(f"{len(findings)} finding(s) -> {out}")
    if findings:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_inv = sub.add_parser("inventory")
    p_inv.add_argument("--label", required=True)
    p_inv.add_argument("--root", type=Path, action="append", required=True)
    p_inv.set_defaults(func=cmd_inventory)

    p_rec = sub.add_parser("reconcile")
    p_rec.add_argument("--source", type=Path, required=True)
    p_rec.add_argument("--destination", type=Path, required=True)
    p_rec.add_argument("--apply", action="store_true", help="Execute the plan. Off by default.")
    p_rec.set_defaults(func=cmd_reconcile)

    p_ver = sub.add_parser("verify")
    p_ver.add_argument("--before", required=True)
    p_ver.add_argument("--after", required=True)
    p_ver.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_consolidate_cli.py -n0 -v`
Expected: 7 passed

- [ ] **Step 5: Run the whole suite and lint**

Run: `uv run pytest tests/ -q` then `uv run ruff check scripts/ src/ tests/` and `uv run black --check scripts/ src/ tests/`
Expected: suite green, lint clean.

- [ ] **Step 6: Commit and open the PR**

```bash
git add scripts/consolidate_matches.py tests/test_consolidate_cli.py
git commit -m "feat(migration): consolidation CLI over the inventory, reconcile and verify rules"
gh pr create --title "Corpus consolidation tooling" --body "$(cat <<'BODY'
Tooling for consolidating the match corpus onto X9, per
docs/superpowers/specs/2026-08-19-corpus-consolidation-design.md.

- one loud resolver for a fixture's source_video; five scripts stop
  silently skipping unreachable videos
- fixture source_video rewrite for the consolidated layout
- inventory / reconcile / verify library plus the CLI that drives them

No data is migrated by this PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

---

## Part C -- The migration itself

These tasks operate on real data. They are not TDD; each ends with a
report to read. **Nothing here deletes anything** except Task 14, which
is gated on explicit human approval.

### Task 9: Phase 0 -- inventory and baseline

**Files:**
- Create: `build/consolidation/phase0.json`, `build/consolidation/baseline.txt`
- Modify: none

- [ ] **Step 1: Confirm both volumes are mounted**

```bash
ls /Volumes/X9/matches >/dev/null && ls /Volumes/mathias/skytte/video/raw >/dev/null && echo "both mounted"
```
Expected: `both mounted`. If the share is absent, STOP -- phase 1 cannot run.

- [ ] **Step 2: Back up the project registry**

```bash
cp ~/.splitsmith/projects.json ~/.splitsmith/projects.json.bak.before-consolidation
```

- [ ] **Step 3: Carry shooter_token onto the X9 legacy copies**

This must run before the baseline inventory (Step 4), not after it. A
legacy project's only pairing key (once its identity is resolved through
the rename map) is `shooter_token` -- it has no `slug`. If the baseline
snapshot is taken first, five projects
(`jinglebells-challenge-2026-anton`, `tallmilan-2025`, `tallmilan-2026`,
`tallmilan-2026-janne`, `tallmilan-2026-martin`) have no token to pair on
at that snapshot and `verify` reports failure on an otherwise-perfect
migration. Doing it here, before phase 0, also means pairing this step's
own source against destination by same-named directory
(`~/matches/tallmilan-2026` to `/Volumes/X9/matches/tallmilan-2026`) is
unambiguous; doing it after the merge would mean matching a home project
against an opaque `s_<hex>` slug by competitor name.

```bash
cd /Users/mathias/work/splitsmith-lab
for name in blacksmith-2026 blacksmith-handgun-2026-anton blacksmith-handgun-2026-martin \
            tallmilan-2025 tallmilan-2026 tallmilan-2026-janne tallmilan-2026-martin; do
  echo "== $name"
  uv run python scripts/consolidate_matches.py reconcile \
    --source ~/matches/$name --destination /Volumes/X9/matches/$name
done
```

Expected per pair: at most one `set_shooter_token` action, zero
`copy_audit_doc` actions (the audit docs were measured byte-identical),
`violations=0`. `blacksmith-handgun-2026-anton` already agrees on both
sides and should show no actions at all.

Re-run each with `--apply` once the plans read correctly.

- [ ] **Step 4: Take the inventory**

```bash
cd /Users/mathias/work/splitsmith-lab
uv run python scripts/consolidate_matches.py inventory --label phase0 \
  --root /Volumes/X9/matches --root ~/matches --root ~/Splitsmith
```
Expected: 26 projects inventoried, with `BROKEN LINKS` flagged on `blacksmith-2026` (8, both copies), `tallmilan-2025` (6, both copies) and `ess-black-handgun-2026` (9).

- [ ] **Step 5: Record the artifact-build baseline**

```bash
uv run python -c "
import json, pathlib
docs = [json.loads(p.read_text()) for p in pathlib.Path('tests/fixtures').glob('*.json')]
reachable = sum(1 for d in docs if isinstance(d, dict) and d.get('source_video') and pathlib.Path(d['source_video']).exists())
total = sum(1 for d in docs if isinstance(d, dict) and d.get('source_video'))
print(f'fixtures_with_source_video={total}')
print(f'reachable_now={reachable}')
" | tee build/consolidation/baseline.txt
```
Expected: `fixtures_with_source_video=161`, `reachable_now=161`.

- [ ] **Step 6: Commit the baseline**

```bash
git add build/consolidation/phase0.json build/consolidation/baseline.txt
git commit -m "chore(migration): phase 0 inventory and fixture reachability baseline"
```

---

### Task 10: Phase 1 -- raw consolidation

Per match: copy or rename into the canonical tree, then relink that
match's symlinks, before moving to the next. Never rename every
directory first -- a match must not sit with broken links across a phase
boundary.

- [ ] **Step 1: Copy the share footage into the canonical tree**

```bash
for pair in \
  "blacksmith-handgun-2026:2026-blacksmith-handgun-open" \
  "bofors-bombardment:2026-bofors-bombardment" \
  "jinglebell-challenge-2026:2026-jinglebell-challenge" \
  "tallmilan-2025:2025-tallmilan" \
  "tallmilan-2026:2026-tallmilan" \
  "vads-easter-shoot-2026:2026-vads-easter-shoot"; do
  src="/Volumes/mathias/skytte/video/raw/${pair%%:*}"
  dst="/Volumes/X9/raw/${pair##*:}"
  echo "== $src -> $dst"
  ditto "$src" "$dst"
done
```
`ditto` preserves metadata and is the macOS-native choice here. Expect roughly 48 GB copied; X9 has 764 GB free.

- [ ] **Step 2: Normalise camera directory names in the copied tree**

```bash
for d in /Volumes/X9/raw/*/*/handheld; do [ -d "$d" ] && mv "$d" "${d%/handheld}/hand"; done
for d in /Volumes/X9/raw/*/*/headcam;  do [ -d "$d" ] && mv "$d" "${d%/headcam}/head"; done
find /Volumes/X9/raw -maxdepth 3 -type d | sort
```
Expected: every camera directory is now `hand` or `head`. This also renames `2026-hfo-masters`' directories, which is intended.

- [ ] **Step 3: Rename the two mis-slugged existing trees and quarantine loose directories**

```bash
mv /Volumes/X9/raw/2026-black-handgun /Volumes/X9/raw/2026-ess-black-handgun
mv /Volumes/X9/raw/2026-oden /Volumes/X9/raw/2026-oden-cup
mkdir -p /Volumes/X9/raw/_unsorted
mv "/Volumes/X9/raw/2026-ess-black-handgun/2026-black-handgun-from-martin" /Volumes/X9/raw/_unsorted/ 2>/dev/null
mv "/Volumes/X9/raw/2026-stockholm-ipsc-open/Stockholm IPSC Open 2026"* /Volumes/X9/raw/_unsorted/ 2>/dev/null
ls /Volumes/X9/raw /Volumes/X9/raw/_unsorted
```
Nothing is deleted. `_unsorted/` is a holding pen to be reviewed later.

- [ ] **Step 4: Relink every match against the new tree**

For each match root, run the relink planner and apply it:

```bash
uv run python - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "src")
from splitsmith.match_project import MatchProject
from splitsmith import relink

SEARCH_ROOT = Path("/Volumes/X9/raw")
index = relink.index_search_root(SEARCH_ROOT)

roots = []
for base in (Path("/Volumes/X9/matches"), Path.home() / "matches", Path.home() / "Splitsmith"):
    if not base.exists():
        continue
    for project in sorted(base.iterdir()):
        if (project / "match.json").exists():
            roots.extend(sorted((project / "shooters").iterdir()))
        elif (project / "project.json").exists():
            roots.append(project)

for root in roots:
    if not (root / "project.json").exists():
        continue
    project = MatchProject.load(root)
    links = relink.inspect_links(project, root)
    candidates = relink.plan_relink(links, index)
    decisions = [
        (c.link_path, c.chosen_path) for c in candidates if c.chosen_path is not None
    ]
    ambiguous = [c.name for c in candidates if c.ambiguous]
    if decisions:
        relink.apply_relink(decisions)
    print(f"{root}: relinked={len(decisions)} ambiguous={ambiguous}")
PY
```
Expected: every project reports `ambiguous=[]`. Any ambiguous name means two files share a basename under `/Volumes/X9/raw` -- STOP and resolve by hand before continuing.

- [ ] **Step 5: Verify zero broken links**

```bash
uv run python scripts/consolidate_matches.py inventory --label phase1 \
  --root /Volumes/X9/matches --root ~/matches --root ~/Splitsmith
```
Expected: no `BROKEN LINKS` flag on any project, including `blacksmith-2026`, `tallmilan-2025` and `ess-black-handgun-2026`, whose links were dead before this phase. The two `ess-black-handgun-2026` shooters with no raw entries at all still report zero links -- that is the known gap to report, not repair.

- [ ] **Step 6: Commit the phase report**

```bash
git add build/consolidation/phase1.json
git commit -m "chore(migration): phase 1 raw consolidation, zero broken links"
```

---

### Task 11: Phase 2 -- merge the remaining legacy projects

Tokens were already carried onto the X9 legacy copies, in Task 9 Step 3
-- before the phase 0 baseline, not after this task's merges. Doing it
that early keeps the source/destination pairing unambiguous (same-named
directories) and gives every legacy project an identity to pair on from
the very first inventory.

- [ ] **Step 1: Merge tallmilan-2026 (3 shooters)**

```bash
cd /Users/mathias/work/splitsmith-lab
uv run splitsmith match merge \
  /Volumes/X9/matches/tallmilan-2026 \
  /Volumes/X9/matches/tallmilan-2026-janne \
  /Volumes/X9/matches/tallmilan-2026-martin \
  --output /Volumes/X9/matches/.tallmilan-2026-merged \
  --dry-run
```
Read the plan. Then re-run without `--dry-run`. The output goes to a dot-prefixed temporary directory because the final slug collides with an existing legacy directory name.

- [ ] **Step 2: Merge bofors-bombardment-2026 (2 shooters)**

```bash
uv run splitsmith match merge \
  /Volumes/X9/matches/bofors-bombardment-2026 \
  /Volumes/X9/matches/bofors-bombardment-2026-martin \
  --output /Volumes/X9/matches/.bofors-bombardment-2026-merged \
  --dry-run
```
Then re-run without `--dry-run`.

- [ ] **Step 3: Convert the two solo shooters**

```bash
uv run splitsmith match merge /Volumes/X9/matches/tallmilan-2025 \
  --output /Volumes/X9/matches/.tallmilan-2025-merged --dry-run
uv run splitsmith match merge /Volumes/X9/matches/jinglebells-challenge-2026-anton \
  --output /Volumes/X9/matches/.jinglebell-challenge-2026-merged \
  --name "Jinglebell Challenge 2026" --dry-run
```
`jinglebells-challenge-2026-anton` has no `scoreboard_match_id`, so `--name` is required; `plan_merge` raises `MergeConflictError` without it. Re-run both without `--dry-run`.

- [ ] **Step 4: Swap the merged directories into their final slugs**

```bash
for pair in \
  ".tallmilan-2026-merged:tallmilan-2026" \
  ".bofors-bombardment-2026-merged:bofors-bombardment-2026" \
  ".tallmilan-2025-merged:tallmilan-2025" \
  ".jinglebell-challenge-2026-merged:jinglebell-challenge-2026"; do
  tmp="/Volumes/X9/matches/${pair%%:*}"
  final="/Volumes/X9/matches/${pair##*:}"
  legacy_backup="/Volumes/X9/matches/_legacy/${pair##*:}"
  mkdir -p /Volumes/X9/matches/_legacy
  [ -e "$final" ] && mv "$final" "$legacy_backup"
  mv "$tmp" "$final"
  echo "$final ready; legacy at $legacy_backup"
done
```
The legacy originals move to `_legacy/`, they are not deleted. Task 14 removes that directory once verification passes.

- [ ] **Step 5: Relink the newly merged matches**

Re-run the relink script from Task 10 Step 4. The merged shooters carry copies of the legacy `raw/` symlinks and must be re-pointed the same way.

- [ ] **Step 6: Inventory and commit**

```bash
uv run python scripts/consolidate_matches.py inventory --label phase2 \
  --root /Volumes/X9/matches --root ~/matches --root ~/Splitsmith
git add build/consolidation/phase2.json
git commit -m "chore(migration): phase 2 legacy merges"
```

---

### Task 12: Phase 3 -- reconcile merged against legacy

- [ ] **Step 1: Plan every reconciliation**

```bash
cd /Users/mathias/work/splitsmith-lab
uv run python scripts/consolidate_matches.py reconcile \
  --source /Volumes/X9/matches/blacksmith-2026 \
  --destination ~/matches/blacksmith-handgun-open-2026/shooters/s_ce10fa76 \
  --reconcile-log build/consolidation/reconcile-log.json
```
Expected: 7 `copy_audit_doc` actions (`stage1,2,3,5,6,7,8.json`), `violations=0`, `deletable_after_apply=True`.

Every reconcile below writes to that same log, which Task 16's `verify`
reads. A record is keyed by `(source, destination)`, so re-running a pair
after fixing something supersedes its earlier verdict rather than adding
to it. A plan-only run is recorded as not applied and does **not**
satisfy the gate: step 3 and step 4 are what make these records count.

- [ ] **Step 2: Diff each restored document against its local .bak first**

```bash
for n in 1 2 3 5 6 7 8; do
  echo "== stage$n"
  diff <(python3 -m json.tool "/Volumes/X9/matches/blacksmith-2026/audit/stage$n.json") \
       <(python3 -m json.tool ~/matches/blacksmith-handgun-open-2026/shooters/s_ce10fa76/audit/stage$n.json.bak) \
    && echo "   identical to the local .bak"
done
```
Any stage that differs is reported to the user before applying. The spec commits to reporting disagreements, not silently resolving them.

- [ ] **Step 3: Apply the blacksmith restore**

```bash
uv run python scripts/consolidate_matches.py reconcile \
  --source /Volumes/X9/matches/blacksmith-2026 \
  --destination ~/matches/blacksmith-handgun-open-2026/shooters/s_ce10fa76 \
  --reconcile-log build/consolidation/reconcile-log.json \
  --apply
```

- [ ] **Step 4: Reconcile the remaining merged/legacy pairs**

```bash
LOG=build/consolidation/reconcile-log.json
uv run python scripts/consolidate_matches.py reconcile --reconcile-log "$LOG" \
  --source /Volumes/X9/matches/blacksmith-handgun-2026-anton \
  --destination ~/matches/blacksmith-handgun-open-2026/shooters/s_46039db3 --apply
uv run python scripts/consolidate_matches.py reconcile --reconcile-log "$LOG" \
  --source /Volumes/X9/matches/blacksmith-handgun-2026-martin \
  --destination ~/matches/blacksmith-handgun-open-2026/shooters/s_b3d21334 --apply
uv run python scripts/consolidate_matches.py reconcile --reconcile-log "$LOG" \
  --source /Volumes/X9/matches/vads-easter-shoot-2026-anton \
  --destination /Volumes/X9/matches/vads-easter-shoot-2026/shooters/s_9540b345 --apply
uv run python scripts/consolidate_matches.py reconcile --reconcile-log "$LOG" \
  --source /Volumes/X9/matches/vads-easter-shoot-2026-martin \
  --destination /Volumes/X9/matches/vads-easter-shoot-2026/shooters/s_fac64ec6 --apply
```
Expected on each: zero `copy_audit_doc` actions (both sides hold all docs, destination wins), some `copy_media` actions, `violations=0`.

- [ ] **Step 5: Move every reconciled legacy directory aside and commit**

Task 11's swap loop only moved the four slugs whose merged output
collided with an existing name. The blacksmith trio and the vads pair
were reconciled into already-merged destinations and are still sitting
in `/Volumes/X9/matches`, so they move now:

```bash
mkdir -p /Volumes/X9/matches/_legacy
for name in blacksmith-2026 blacksmith-handgun-2026-anton blacksmith-handgun-2026-martin \
            vads-easter-shoot-2026-anton vads-easter-shoot-2026-martin; do
  mv "/Volumes/X9/matches/$name" /Volumes/X9/matches/_legacy/
done
uv run python scripts/consolidate_matches.py inventory --label phase3 \
  --root /Volumes/X9/matches --root ~/matches --root ~/Splitsmith
git add build/consolidation/phase3.json
git commit -m "chore(migration): phase 3 reconciliation, blacksmith audit docs restored"
```

---

### Task 13: Phase 4 -- add Anton to tallmilan-2026

Anton has 5 clips for a 7-stage match. His slot will be incomplete and
that is expected, not a defect. No detection or audit is run.

- [ ] **Step 1: Create the shooter slot through the API**

There is no `match` CLI verb for this (`match_cli.py` exposes `merge`,
`info`, `rename-shooter-slugs`, `trims` only). Use the same endpoint the
SPA uses, so the slug is minted opaquely and the match's stage
definitions are mirrored into the new shooter's `project.json`:

```bash
cd /Users/mathias/work/splitsmith-lab
uv run splitsmith ui --project /Volumes/X9/matches/tallmilan-2026 &
sleep 5
curl -sS -X POST http://127.0.0.1:8000/api/match/shooters \
  -H 'content-type: application/json' \
  -d '{"name": "Anton Johansson"}' | python3 -m json.tool
```

Record the minted `s_<hex>` slug from the response. Do not hand-write
`match.json` -- `add_shooter` also creates the subdirectory tree and
saves `shooter.json`, and a hand edit produces a match that loads but
has no shooter directory.

- [ ] **Step 2: Register the footage**

```bash
SLUG=s_xxxxxxxx  # the slug minted in step 1
curl -sS -X POST "http://127.0.0.1:8000/api/shooters/$SLUG/videos/scan" \
  -H 'content-type: application/json' \
  -d '{"source_dir": "/Volumes/X9/raw/2026-tallmilan/anton/hand", "link_mode": "symlink"}' \
  | python3 -m json.tool
```

Expected: 5 videos registered as symlinks. `link_mode` must be
`symlink`, not `copy` -- the raw tree is the single copy on X9 and a
second one inside the project would double the footage on disk and
diverge from every other shooter's layout.

- [ ] **Step 3: Confirm the stage assignment proposal**

```bash
uv run splitsmith match info /Volumes/X9/matches/tallmilan-2026
```
Expected: Anton appears with 5 of 7 stages populated. Record which two
stages have no footage in the phase report. Then stop the server:

```bash
kill %1
```

- [ ] **Step 4: Verify no links broke**

```bash
uv run python scripts/consolidate_matches.py inventory --label phase4 \
  --root /Volumes/X9/matches --root ~/matches --root ~/Splitsmith
```
Expected: no broken links; `tallmilan-2026` now shows 4 shooters.

- [ ] **Step 5: Commit**

```bash
git add build/consolidation/phase4.json
git commit -m "chore(migration): phase 4 anton added to tallmilan-2026 (5 of 7 stages)"
```

---

### Task 14: Phase 5 -- move the local matches onto X9

- [ ] **Step 1: Record the pre-move push plans**

```bash
cd /Users/mathias/work/splitsmith-lab
uv run python - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "src")
from splitsmith.sync.plan import build_push_plan
from splitsmith.sync.state import load_sync_state

for root in [Path.home() / "matches/blacksmith-handgun-open-2026", Path.home() / "Splitsmith/oden-cup-2026"]:
    plan = build_push_plan(root, sync_state=load_sync_state(root))
    print(f"{root}: media={len(plan.media)} docs={len(plan.docs)} skipped_media={plan.media_skipped}")
PY
```
Record these numbers. `media` may be non-zero if there is genuinely unpushed work; what matters is that it does not GROW after the move.

- [ ] **Step 2: Move with metadata preserved**

```bash
ditto ~/matches/blacksmith-handgun-open-2026 /Volumes/X9/matches/blacksmith-handgun-open-2026
ditto ~/Splitsmith/oden-cup-2026 /Volumes/X9/matches/oden-cup-2026
```
`ditto` is a copy, not a move. The originals stay until Task 16.

- [ ] **Step 3: Assert the push plan did not grow**

```bash
uv run python - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "src")
from splitsmith.sync.plan import build_push_plan
from splitsmith.sync.state import load_sync_state

pairs = [
    (Path.home() / "matches/blacksmith-handgun-open-2026", Path("/Volumes/X9/matches/blacksmith-handgun-open-2026")),
    (Path.home() / "Splitsmith/oden-cup-2026", Path("/Volumes/X9/matches/oden-cup-2026")),
]
failed = False
for old, new in pairs:
    before = build_push_plan(old, sync_state=load_sync_state(old))
    after = build_push_plan(new, sync_state=load_sync_state(new))
    ok = len(after.media) <= len(before.media)
    failed |= not ok
    print(f"{new.name}: media before={len(before.media)} after={len(after.media)} {'OK' if ok else 'REGRESSED'}")
raise SystemExit(1 if failed else 0)
PY
```
Expected: both `OK`. A regression means mtimes were not preserved -- STOP, delete the X9 copy, and redo the move with a method that preserves them. Re-uploading here costs gigabytes.

- [ ] **Step 4: Check the other three synced matches are untouched**

```bash
uv run python - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "src")
from splitsmith.sync.plan import build_push_plan
from splitsmith.sync.state import load_sync_state

for name in ("ess-black-handgun-2026", "hfo-masters-2026", "stockholm-ipsc-open-2026"):
    root = Path("/Volumes/X9/matches") / name
    plan = build_push_plan(root, sync_state=load_sync_state(root))
    print(f"{name}: media={len(plan.media)} docs={len(plan.docs)}")
PY
```
Expected: `media=0` for all three. These were never moved; a non-zero count means phase 1's relink disturbed something and must be investigated before proceeding.

- [ ] **Step 5: Rewrite the project registry**

```bash
uv run python - <<'PY'
import json
from pathlib import Path

registry = Path.home() / ".splitsmith" / "projects.json"
doc = json.loads(registry.read_text())
moved = 0
for entry in doc.get("projects", []):
    path = Path(entry["path"])
    if path.parent in (Path.home() / "matches", Path.home() / "Splitsmith"):
        entry["path"] = str(Path("/Volumes/X9/matches") / path.name)
        moved += 1
registry.write_text(json.dumps(doc, indent=2) + "\n")
print(f"repointed {moved} registry entries at X9")
PY
```
Then remove entries whose `path` no longer exists (the retired legacy slugs) by opening the app once and confirming the picker lists exactly ten matches.

- [ ] **Step 6: Commit**

```bash
uv run python scripts/consolidate_matches.py inventory --label phase5 --root /Volumes/X9/matches
git add build/consolidation/phase5.json
git commit -m "chore(migration): phase 5 local matches moved to X9, registry repointed"
```

---

### Task 15: Phase 6 -- rewrite fixture paths and prove the corpus did not shrink

- [ ] **Step 1: Build the mapping file**

```bash
cd /Users/mathias/work/splitsmith-lab
uv run python - <<'PY'
import json
from pathlib import Path

REPORT = Path("build/consolidation/raw_mapping.json")
mapping = {}
for match_root in sorted(Path("/Volumes/X9/matches").iterdir()):
    shooters = match_root / "shooters"
    if not shooters.is_dir():
        continue
    for shooter in sorted(shooters.iterdir()):
        raw_dir = shooter / "raw"
        if not raw_dir.is_dir():
            continue
        destination = str(raw_dir)
        for entry in raw_dir.iterdir():
            if entry.is_symlink():
                mapping[str(Path(entry.readlink()).parent)] = destination
REPORT.parent.mkdir(parents=True, exist_ok=True)
REPORT.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")
print(f"{len(mapping)} directory mappings -> {REPORT}")
PY
```
The mapping is derived from where each shooter's symlinks actually point after phase 1, so it cannot drift from reality. Legacy project raw directories that no longer exist are added by hand from `build/consolidation/phase0.json` if any fixture still references them.

- [ ] **Step 2: Dry-run the rewrite**

```bash
uv run python scripts/migrate_fixtures_raw_root.py --dry-run \
  --mapping build/consolidation/raw_mapping.json
```
Expected: `rewritten: 78`, `already canonical: 83`, `unmapped: 0`. Any unmapped path is printed -- add its prefix to the mapping and re-run rather than letting the script guess.

- [ ] **Step 3: Apply the rewrite**

```bash
uv run python scripts/migrate_fixtures_raw_root.py \
  --mapping build/consolidation/raw_mapping.json
```

- [ ] **Step 4: Prove every fixture now resolves**

```bash
uv run python -c "
import json, pathlib
docs = [(p, json.loads(p.read_text())) for p in pathlib.Path('tests/fixtures').glob('*.json')]
bad = [str(p) for p, d in docs if isinstance(d, dict) and d.get('source_video') and not pathlib.Path(d['source_video']).exists()]
total = sum(1 for _, d in docs if isinstance(d, dict) and d.get('source_video'))
print(f'with source_video={total} unreachable={len(bad)}')
for path in bad: print('  ', path)
"
```
Expected: `with source_video=161 unreachable=0`. This is the number the phase 0 baseline recorded.

- [ ] **Step 5: Rebuild the artifacts without the escape hatch**

```bash
uv run python scripts/build_ensemble_artifacts.py 2>&1 | tee build/consolidation/artifact-rebuild.log
```
The absence of `--allow-missing-video` is the point: the build now fails if any fixture is unreachable. Expected: success, and `n_visual_skipped_missing_video` of 0 in the written calibration.

- [ ] **Step 6: Confirm the corpus did not shrink**

```bash
uv run python -c "
import json, pathlib
cal = json.loads(pathlib.Path('src/splitsmith/data/ensemble_calibration.json').read_text())
prov = cal.get('voter_e_provenance', {})
print('visual candidates:', prov.get('n_visual_candidates'))
print('skipped missing video:', prov.get('n_visual_skipped_missing_video'))
print('calibration fixtures:', len(cal.get('calibration_fixtures', [])))
"
```
Expected: `skipped missing video: 0`, and a fixture count at or above the phase 0 baseline. A lower count is a failed migration.

- [ ] **Step 7: Run the full suite and commit**

```bash
uv run pytest tests/ -q
git add tests/fixtures build/consolidation src/splitsmith/data
git commit -m "chore(migration): repoint fixture source_video at the consolidated corpus"
```

---

### Task 16: Phase 7 -- verify, then stop

- [ ] **Step 1: Final inventory**

```bash
cd /Users/mathias/work/splitsmith-lab
uv run python scripts/consolidate_matches.py inventory --label phase7 --root /Volumes/X9/matches
```
Expected: exactly ten matches, all `kind=match`, zero broken links.

- [ ] **Step 2: Full verification against the phase 0 baseline**

```bash
uv run python scripts/consolidate_matches.py verify --before phase0 --after phase7 \
  --rename-map scripts/consolidation_rename_map.json \
  --reconcile-log build/consolidation/reconcile-log.json
```
Expected: `0 blocking finding(s)`, `25 project pair(s) resolved`, and a
non-zero reconcile-outcome count. The command exits 1 if any blocking
finding exists.

`--rename-map` is required and there is no fallback: which after-project
each before-project became is declared, not inferred. A before-project
with no entry, or one whose declared destination is absent from the
phase 7 inventory, is a blocking finding naming it. If verify reports
`project_mapped`, add the entry to
`scripts/consolidation_rename_map.json` -- do not work around it.
A missing or empty reconcile log is blocking too, as is a record whose
`deletable: true` was only planned and never applied.

- [ ] **Step 3: Confirm every match still loads**

```bash
for m in /Volumes/X9/matches/*/; do
  [ -f "$m/match.json" ] || continue
  echo "== $(basename $m)"
  uv run splitsmith match info "$m" >/dev/null && echo "   ok"
done
```
Expected: `ok` for all ten.

- [ ] **Step 4: Confirm no synced match wants to re-upload**

Re-run the Task 14 Step 4 script over all five synced matches (now including the two that moved). Expected: `media=0` for every one.

- [ ] **Step 5: Write the report and STOP**

```bash
uv run python scripts/consolidate_matches.py verify --before phase0 --after phase7 \
  --rename-map scripts/consolidation_rename_map.json \
  --reconcile-log build/consolidation/reconcile-log.json \
  > build/consolidation/final-report.txt 2>&1
git add build/consolidation
git commit -m "chore(migration): phase 7 verification report"
```

**Hand the report to the user and stop.** Task 17 does not run until they have read it and said to proceed. What is still on disk at this point: `~/matches`, `~/Splitsmith`, `/Volumes/X9/matches/_legacy/`, and the untouched share. Nothing has been lost yet, and that property must survive this checkpoint.

---

### Task 17: Phase 8 -- deletion (gated on explicit approval)

**Do not start this task without the user saying to proceed after reading the Task 16 report.**

- [ ] **Step 1: Re-verify immediately before deleting**

```bash
cd /Users/mathias/work/splitsmith-lab
uv run python scripts/consolidate_matches.py verify --before phase0 --after phase7 \
  --rename-map scripts/consolidation_rename_map.json \
  --reconcile-log build/consolidation/reconcile-log.json
```
Expected: `0 blocking finding(s)`. If anything changed since Task 16, STOP.

- [ ] **Step 2: Delete the legacy holding directory**

```bash
du -sh /Volumes/X9/matches/_legacy
rm -rf /Volumes/X9/matches/_legacy
```

- [ ] **Step 3: Delete the local roots**

```bash
du -sh ~/matches ~/Splitsmith
rm -rf ~/matches ~/Splitsmith
```

- [ ] **Step 4: Confirm the end state**

```bash
ls /Volumes/X9/matches
uv run python scripts/consolidate_matches.py inventory --label final --root /Volumes/X9/matches
uv run python -c "
import json, pathlib
doc = json.loads((pathlib.Path.home() / '.splitsmith/projects.json').read_text())
for entry in doc['projects']:
    print(entry['path'], pathlib.Path(entry['path']).exists())
"
```
Expected: ten matches, zero broken links, every registry path exists and starts with `/Volumes/X9/matches`.

- [ ] **Step 5: Commit**

```bash
git add build/consolidation/final.json
git commit -m "chore(migration): phase 8 legacy roots removed, corpus lives on X9"
```

---

## Notes for the executor

- The share `/Volumes/mathias` is never written to and never deleted from. It remains a second copy of the footage that entered X9 in phase 1.
- If any step reports an ambiguous relink candidate, a safety violation, or a growing push plan, stop and report rather than working around it. Every one of those signals means the plan's assumptions no longer hold.
- `_unsorted/` under `/Volumes/X9/raw` holds directories nobody has classified. Leaving it is correct; deleting it is not part of this plan.
