# Storage-aware project cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `splitsmith.cleanup` see object storage so a hosted user can reclaim space, tell the truth about which artefacts are re-derivable, and give the SPA the dialog that currently has no caller.

**Architecture:** One category table drives both a local-disk walker and an object-storage filter, so the two enumerations cannot drift. `CleanupItem` gains `storage_key` (where the bytes are) and `reconstructable` (whether deleting costs recompute or data). `apply_cleanup` branches on `storage_key`. A new SPA dialog on the Export page calls the two routes that already exist.

**Tech Stack:** Python 3.11+, Pydantic, pytest; React + TypeScript + vitest for the SPA.

**Spec:** `docs/superpowers/specs/2026-08-15-storage-aware-cleanup-design.md`

## Global Constraints

- Python 3.11+, type hints everywhere. `pathlib.Path` for paths, never strings.
- Black formatting, line length **110**. Run `uv run black` on hand-written snippets before committing -- CI's format gate fails otherwise.
- `uv` for dependency management, never `pip`. **No new dependencies** in this plan.
- Pydantic models for anything crossing a module boundary.
- Tests: `uv run pytest`. The suite runs `-n auto` by default; use `-n0` for a focused single-test run.
- Desktop behaviour (no storage bound) must be byte-identical to today. Every existing test in `tests/test_cleanup.py` stays green, unmodified, throughout.
- Storage key scheme is `<scope>/<subdir>/<basename>` -- established by `export_storage._storage_export_key`, `audio._storage_audio_key`, `audio._storage_trim_key`. Never build one by hand outside the table in Task 2.
- SPA: `pnpm typecheck` (never bare `tsc --noEmit` -- project references mean that checks nothing and exits 0), `pnpm test`, `pnpm lint`.
- Commit after each task. Conventional-commit subjects.

## Storage prefix reality (verified against the code, 2026-08-15)

Only three prefixes exist under a scope: `exports/`, `trimmed/`, `audio/`
(peaks JSONs live under `audio/` too, distinguished by the
`.peaks-*.json` basename). There is **no** storage counterpart for
thumbs, probes or the scoreboard cache -- they are local-only caches.

**Raw sources are not under the scope at all.** They are keyed
`raw/<name>` at the storage root (`server.py:7904`), and
`source_present` asks `storage.exists(str(video_path))` with no prefix.
`bind_storage`'s docstring states the rule: `scope` prefixes
derived-artefact caches so two shooters cannot collide, while "the
raw-video resolver still works because it keys off the
user-prefix-relative `StageVideo.path` directly".

Two consequences the tasks below depend on, and which are easy to get
backwards:

1. A single `storage.list(f"{scope}/")` **cannot reach a raw source**, so
   the planner structurally cannot offer one for deletion. That is the
   real protection; the explicit `raw/` refusal in Task 4 is
   defence-in-depth for a future where scope layout changes.
2. Tests that seed a source for the `reconstructable` flag must write
   `raw/clip.mp4`, **not** `<scope>/raw/clip.mp4`, or `source_present`
   will not find it and the test will assert the wrong thing while
   looking right.

**`AUDIT_DATA` has no storage counterpart either**, and this one matters:
on hosted, audit docs live in the `state_docs` Postgres table, not object
storage. A hosted `AUDIT_DATA` cleanup would be a database delete, which
is a different mechanism and is **out of scope**. Task 4 pins hosted
`AUDIT_DATA` at zero items so nobody later reads the empty result as
"it worked".

## Known pre-existing gap, deliberately not fixed here

`EXPORTS_LIGHT`'s patterns are `*.fcpxml`, `*.csv`, `*_report.txt`. The
YouTube sidecars (`.srt`, `.json`) that `server.py:3879-3881` pushes
alongside a match FCPXML match none of them, so they are never cleaned.
Real, small, and out of scope: fixing it changes desktop behaviour and
belongs in its own commit. Do not fold it in.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/splitsmith/export_naming.py` | gains `stage_number_from_filename` -- filename-to-stage knowledge stays in the naming module |
| `src/splitsmith/match_project.py` | gains `source_present(..., durable=)` |
| `src/splitsmith/cleanup.py` | the category table, both enumerators, `reconstructable`, storage delete, storage log |
| `src/splitsmith/ui/server.py` | the two cleanup routes; response shape grows two fields |
| `src/splitsmith/ui_static/src/lib/api.ts` | `CleanupItem` type gains the two fields |
| `src/splitsmith/ui_static/src/components/CleanupDialog.tsx` | new: the dialog |
| `src/splitsmith/ui_static/src/pages/Export.tsx` | mounts the dialog |
| `tests/test_cleanup.py` | existing desktop tests, untouched |
| `tests/test_cleanup_storage.py` | new: every hosted-mode test |
| `src/splitsmith/ui_static/src/components/CleanupDialog.test.tsx` | new |

---

### Task 1: `stage_number_from_filename` in the naming module

Deriving "which stage is this artefact for" from a basename is naming
knowledge. It belongs next to `stage_file_base`, which built the name, not
in the cleanup planner -- that is the whole thesis of `export_naming.py`'s
docstring.

**Files:**
- Modify: `src/splitsmith/export_naming.py`
- Test: `tests/test_export_naming.py`

**Interfaces:**
- Produces: `stage_number_from_filename(filename: str) -> int | None`

- [ ] **Step 1: Write the failing test**

```python
def test_stage_number_from_filename_reads_the_stage_prefix() -> None:
    assert stage_number_from_filename("stage3_the-classifier_trimmed.mp4") == 3
    assert stage_number_from_filename("stage12_x_cam_abc_trimmed.mp4") == 12


def test_stage_number_from_filename_is_none_for_match_level_and_junk() -> None:
    # Match-level deliverables carry no stage number.
    assert stage_number_from_filename("bromma-2026-match.fcpxml") is None
    assert stage_number_from_filename("notes.txt") is None
    # "stage" without digits is not a stage prefix.
    assert stage_number_from_filename("stage_notes.txt") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_export_naming.py -k stage_number_from_filename -v -n0`
Expected: FAIL, `ImportError` / `NameError` on `stage_number_from_filename`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/splitsmith/export_naming.py`, and add the name to the test
file's import at the top:

```python
def stage_number_from_filename(filename: str) -> int | None:
    """The stage number a per-stage artefact belongs to, or ``None``.

    The inverse of :func:`stage_file_base`'s prefix, for readers that hold
    a basename and need to ask a question about its stage -- e.g. "is that
    stage's source still around?". Match-level deliverables and anything
    not written by this module answer ``None``.

    Lives here rather than in the caller for the reason the module
    docstring gives: every reader that takes a name apart has to agree
    with the one writer that put it together.
    """
    m = _STAGE_FILE_RE.match(filename)
    if m is None:
        return None
    return int(m.group(0)[len("stage") : -1])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_export_naming.py -v -n0`
Expected: PASS, and every pre-existing test in the file still passes.

- [ ] **Step 5: Commit**

```bash
uv run black src/splitsmith/export_naming.py tests/test_export_naming.py
git add src/splitsmith/export_naming.py tests/test_export_naming.py
git commit -m "feat(export-naming): read a stage number back out of an artefact basename"
```

---

### Task 2: `source_present(durable=...)`

**Files:**
- Modify: `src/splitsmith/match_project.py:2015-2034`
- Test: `tests/test_source_present_durable.py` (create)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `MatchProject.source_present(root: Path, video_path: Path, *, durable: bool = False) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_source_present_durable.py`:

```python
"""``source_present(durable=True)`` asks storage, not the local cache.

On hosted, ``root / video_path`` is an ephemeral container cache. A
cached copy does not make a derived artefact reconstructable -- the cache
is wiped on the next redeploy. The cleanup planner is the only caller
that needs that distinction, and it needs it badly: the whole value of
its ``reconstructable`` flag is that the answer survives the container.
"""

from __future__ import annotations

from pathlib import Path

from splitsmith.match_project import MatchProject
from splitsmith.storage import FilesystemStorage

SCOPE = "matches/m1/shooters/me"


def _project(tmp_path: Path, *, with_storage: bool = True) -> tuple[MatchProject, Path]:
    root = tmp_path / "p"
    project = MatchProject.init(root, name="durable-test")
    if with_storage:
        backing = tmp_path / "tenant"
        backing.mkdir(exist_ok=True)
        project.bind_storage(FilesystemStorage(backing), scope=SCOPE)
    return project, root


def test_durable_ignores_a_local_cache_copy(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    rel = Path("raw/clip.mp4")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(b"cached")

    # Non-durable sees the cache; durable does not, because storage is empty.
    assert project.source_present(root, rel) is True
    assert project.source_present(root, rel, durable=True) is False


def test_durable_sees_the_storage_object(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    rel = Path("raw/clip.mp4")
    backing = tmp_path / "tenant"
    (backing / str(rel)).parent.mkdir(parents=True, exist_ok=True)
    (backing / str(rel)).write_bytes(b"durable")

    assert project.source_present(root, rel, durable=True) is True


def test_durable_is_a_noop_on_desktop(tmp_path: Path) -> None:
    # No storage bound: the local file IS the durable copy.
    project, root = _project(tmp_path, with_storage=False)
    rel = Path("raw/clip.mp4")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(b"local")

    assert project.source_present(root, rel) is True
    assert project.source_present(root, rel, durable=True) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_source_present_durable.py -v -n0`
Expected: FAIL, `TypeError: source_present() got an unexpected keyword argument 'durable'`.

- [ ] **Step 3: Write minimal implementation**

Replace the body of `source_present` in `src/splitsmith/match_project.py`.
Keep the existing docstring and **append** the new paragraph:

```python
    def source_present(self, root: Path, video_path: Path, *, durable: bool = False) -> bool:
        """Is this video's source available, *without* fetching it?

        The read-only counterpart to :meth:`resolve_video_path`, for callers
        that only want to know whether a source exists. That method mirrors
        a hosted object into the local cache on first access, so using it as
        an existence check downloads the file -- which turns a planning pass
        that promises to touch no media into a full download of the match
        (#617). Here a bound storage is asked with a cheap ``exists``.

        Deliberately does not mirror, so it cannot be used as a way to warm
        the cache; call ``resolve_video_path`` when you need the bytes.

        ``durable=True`` skips the local-disk check whenever a storage is
        bound. In hosted mode ``root / video_path`` is the ephemeral source
        cache, so a hit there says "present until the next redeploy", which
        is the wrong answer for a caller deciding whether some *derived*
        artefact can be rebuilt later. On desktop no storage is bound and
        the local file is itself the durable copy, so the flag changes
        nothing. Only the cleanup planner passes it.
        """
        if video_path.is_absolute():
            return video_path.exists()
        if self._storage is None:
            return (root / video_path).exists()
        if not durable and (root / video_path).exists():
            return True
        return self._storage.exists(str(video_path))
```

Note the restructure: the desktop path (`self._storage is None`) now
returns the local answer directly, so `durable` cannot make a desktop
project report `False` for a file that is sitting right there.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_source_present_durable.py -v -n0`
Expected: PASS.

Then the regression check that matters, since this edits a method with
existing callers:

Run: `uv run pytest tests/test_api_source_fetch_guard.py -v -n0`
Expected: PASS -- this is the standing guard for the whole
`source_present` class from #637/#638.

- [ ] **Step 5: Commit**

```bash
uv run black src/splitsmith/match_project.py tests/test_source_present_durable.py
git add src/splitsmith/match_project.py tests/test_source_present_durable.py
git commit -m "feat(match-project): source_present can ask storage only, ignoring the ephemeral cache"
```

---

### Task 3: The category table (pure refactor, no behaviour change)

This task must not change a single observable behaviour. It exists so that
Task 4's storage walker and today's disk walker read the same table.

**Files:**
- Modify: `src/splitsmith/cleanup.py:116-177` (`_iter_paths`)
- Test: `tests/test_cleanup.py` (existing, unmodified)

**Interfaces:**
- Produces: `_CATEGORY_SOURCES: dict[CleanupCategory, tuple[_Source, ...]]` and `_Source`.

- [ ] **Step 1: Establish the green baseline**

Run: `uv run pytest tests/test_cleanup.py -v -n0`
Expected: PASS (13 tests). Record the count -- it must be identical after.

- [ ] **Step 2: Add the table**

Insert above `_iter_paths` in `src/splitsmith/cleanup.py`:

```python
class _Source(NamedTuple):
    """One (directory, patterns) pair a category sweeps.

    ``local`` resolves the on-disk directory through ``MatchProject`` so
    path overrides (``audio_dir`` and friends) keep working.
    ``storage_subdir`` is the segment under ``<scope>/`` holding the same
    files in hosted mode, or ``None`` when nothing pushes them -- thumbs,
    probes and the scoreboard cache are local-only, and audit docs live
    in ``state_docs`` rather than object storage.

    One table, two readers (:func:`_iter_paths` on disk,
    :func:`_iter_storage_items` in storage). Keeping the globs in two
    places is how "what counts as an overlay" drifts -- the same failure
    ``export_naming`` exists to prevent, one layer up.
    """

    local: Callable[[MatchProject, Path], Path]
    patterns: tuple[str, ...]
    storage_subdir: str | None


_CATEGORY_SOURCES: dict[CleanupCategory, tuple[_Source, ...]] = {
    CleanupCategory.CACHES: (
        _Source(lambda p, r: p.thumbs_path(r), ("*",), None),
        _Source(lambda p, r: p.probes_path(r), ("*.json",), None),
        _Source(lambda p, r: r / "scoreboard" / "cache", ("**/*",), None),
        # Peaks sit next to the audio on disk and under <scope>/audio/ in
        # storage, but they are caches: tiny and re-derived from the WAV.
        _Source(lambda p, r: p.audio_path(r), ("*.peaks-*.json",), "audio"),
    ),
    CleanupCategory.EXPORTS_LIGHT: (
        _Source(lambda p, r: p.exports_path(r), ("*.fcpxml", "*.csv", "*_report.txt"), "exports"),
    ),
    CleanupCategory.EXPORTS_OVERLAYS: (
        _Source(lambda p, r: p.exports_path(r), ("*_overlay.mov",), "exports"),
    ),
    CleanupCategory.EXPORTS_TRIMS: (
        # Captures both ``stage<N>_<slug>_trimmed.mp4`` (primary) and
        # ``stage<N>_<slug>_cam_<id>_trimmed.mp4`` (per-camera trims).
        _Source(lambda p, r: p.exports_path(r), ("*_trimmed.mp4",), "exports"),
    ),
    CleanupCategory.AUDIT_TRIMS: (
        _Source(lambda p, r: p.trimmed_path(r), ("*.mp4",), "trimmed"),
    ),
    CleanupCategory.AUDIO: (
        # Peaks JSONs deliberately live in the CACHES bucket; this bucket
        # only carries the heavyweight extracted WAVs.
        _Source(lambda p, r: p.audio_path(r), ("*.wav",), "audio"),
    ),
    CleanupCategory.AUDIT_DATA: (
        # storage_subdir is None on purpose: hosted audit docs live in the
        # ``state_docs`` table, not object storage. Deleting them is a
        # database operation and is out of scope here.
        _Source(lambda p, r: p.audit_path(r), ("stage*.json", "stage*.json.bak"), None),
    ),
}
```

Add to the imports at the top of the file:

```python
from collections.abc import Callable, Iterable
from typing import NamedTuple
```

(`Iterable` is already imported from `collections.abc`; add `Callable`
to that same line rather than a second import.)

- [ ] **Step 3: Rewrite `_iter_paths` to read the table**

Replace the whole `if/elif` chain in `_iter_paths` with:

```python
    for source in _CATEGORY_SOURCES[category]:
        directory = source.local(project, root)
        for pattern in source.patterns:
            for p in _glob(directory, pattern):
                yield p
```

Leave the docstring exactly as it is -- it still describes the behaviour.

- [ ] **Step 4: Run the tests and confirm nothing moved**

Run: `uv run pytest tests/test_cleanup.py -v -n0`
Expected: PASS, the same 13 tests as Step 1. A refactor that changes a
count is not a refactor.

Run: `uv run pytest tests/ -k cleanup -n0`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run black src/splitsmith/cleanup.py
git add src/splitsmith/cleanup.py
git commit -m "refactor(cleanup): one category table behind the directory walker"
```

---

### Task 4: Plan against object storage

**Files:**
- Modify: `src/splitsmith/cleanup.py` (`CleanupItem`, `plan_cleanup`, new `_iter_storage_items`)
- Test: `tests/test_cleanup_storage.py` (create)

**Interfaces:**
- Consumes: `_CATEGORY_SOURCES`, `_Source` (Task 3).
- Produces: `CleanupItem.storage_key: str | None`; `_iter_storage_items(project, root, category, listing) -> Iterable[CleanupItem]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cleanup_storage.py`:

```python
"""Hosted-mode cleanup: the bytes are in object storage, not on this disk.

``plan_cleanup`` used to glob ``project.exports_path(root)`` only. In
hosted mode that directory is an ephemeral container cache and the durable
bytes live under ``<scope>/``, so a hosted plan reported zero items and
reclaimed nothing -- the same shape as the #565 source-cache LRU, shipped
and inert in the deployment that needs it.

``FilesystemStorage`` against ``tmp_path`` is the established fake here
(Protocol-equivalent to ``S3Storage`` per ``test_s3_storage.py``).
"""

from __future__ import annotations

from pathlib import Path

from splitsmith.cleanup import CleanupCategory, plan_cleanup
from splitsmith.match_project import MatchProject
from splitsmith.storage import FilesystemStorage

SCOPE = "matches/m1/shooters/me"


def _project(tmp_path: Path) -> tuple[MatchProject, Path, Path]:
    root = tmp_path / "p"
    project = MatchProject.init(root, name="cleanup-test")
    backing = tmp_path / "tenant"
    backing.mkdir(exist_ok=True)
    project.bind_storage(FilesystemStorage(backing), scope=SCOPE)
    return project, root, backing


def _put(backing: Path, key: str, data: bytes = b"xxxx") -> None:
    dest = backing / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def test_plan_finds_export_trims_in_storage(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_alpha_trimmed.mp4", b"0123456789")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    assert plan.total_file_count == 1
    item = plan.items[0]
    assert item.storage_key == f"{SCOPE}/exports/stage1_alpha_trimmed.mp4"
    assert item.size_bytes == 10
    assert item.path.name == "stage1_alpha_trimmed.mp4"
    assert plan.total_bytes == 10


def test_plan_keeps_export_buckets_distinct_in_storage(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4")
    _put(backing, f"{SCOPE}/exports/stage1_a_overlay.mov")
    _put(backing, f"{SCOPE}/exports/stage1_a.fcpxml")

    trims = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
    overlays = plan_cleanup(project, root, {CleanupCategory.EXPORTS_OVERLAYS})
    light = plan_cleanup(project, root, {CleanupCategory.EXPORTS_LIGHT})

    assert [i.path.name for i in trims.items] == ["stage1_a_trimmed.mp4"]
    assert [i.path.name for i in overlays.items] == ["stage1_a_overlay.mov"]
    assert [i.path.name for i in light.items] == ["stage1_a.fcpxml"]


def test_plan_reads_audio_and_peaks_from_the_same_prefix(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/audio/clip.wav")
    _put(backing, f"{SCOPE}/audio/clip.peaks-3000.json")

    audio = plan_cleanup(project, root, {CleanupCategory.AUDIO})
    caches = plan_cleanup(project, root, {CleanupCategory.CACHES})

    assert [i.path.name for i in audio.items] == ["clip.wav"]
    assert [i.path.name for i in caches.items] == ["clip.peaks-3000.json"]


def test_plan_ignores_keys_outside_the_scope(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, "matches/m1/shooters/someone-else/exports/stage1_x_trimmed.mp4")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    assert plan.items == []


def test_plan_never_offers_raw_sources(tmp_path: Path) -> None:
    """Raw uploads are keyed ``raw/<name>`` at the storage root, *outside*
    the per-project scope -- ``bind_storage``'s docstring is explicit that
    ``scope`` prefixes derived-artefact caches only, and the raw resolver
    keys off the user-prefix-relative ``StageVideo.path`` directly.

    So the real protection is that the scope listing never sees them. The
    scoped variant is asserted too as defence-in-depth, in case a future
    change moves raw under the scope.
    """
    project, root, backing = _project(tmp_path)
    _put(backing, "raw/original.mp4", b"irreplaceable")
    _put(backing, f"{SCOPE}/raw/scoped-someday.mp4", b"irreplaceable")

    plan = plan_cleanup(project, root, set(CleanupCategory))

    assert all("raw/" not in (i.storage_key or "") for i in plan.items)
    assert (backing / "raw" / "original.mp4").exists()


def test_hosted_audit_data_plans_nothing(tmp_path: Path) -> None:
    """Hosted audit docs live in ``state_docs``, not object storage.

    Deleting them is a database operation this module does not do. The
    empty plan is correct; this test exists so a future reader does not
    mistake it for "audit-data cleanup works on hosted".
    """
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a.fcpxml")

    plan = plan_cleanup(project, root, {CleanupCategory.AUDIT_DATA})

    assert plan.items == []


def test_local_and_storage_items_both_appear(tmp_path: Path) -> None:
    """A hosted container can hold a mirrored copy of a storage object.

    Both are reported, and they are not deduplicated into one item -- the
    storage object is the durable byte and the local file is a cache, and
    apply has to remove each.
    """
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")
    local = root / "exports" / "stage1_a_trimmed.mp4"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"01234")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    keys = sorted((i.storage_key or "<local>") for i in plan.items)
    assert keys == ["<local>", f"{SCOPE}/exports/stage1_a_trimmed.mp4"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cleanup_storage.py -v -n0`
Expected: FAIL. `test_plan_finds_export_trims_in_storage` fails with
`assert 0 == 1` -- the current planner does not look at storage at all.
`test_plan_ignores_keys_outside_the_scope` and
`test_hosted_audit_data_plans_nothing` pass already (vacuously); that is
expected and fine, they are guards, not the bug.

- [ ] **Step 3: Add `storage_key` to `CleanupItem`**

In `src/splitsmith/cleanup.py`:

```python
class CleanupItem(BaseModel):
    """One file the plan would unlink.

    ``path`` is always the local-equivalent path -- for a storage object
    it is where the file would sit on disk, so the CLI's table and the
    SPA's list render identically either way. ``storage_key`` set means
    the durable bytes are the object, and ``path`` may not exist at all.
    """

    path: Path
    size_bytes: int
    category: CleanupCategory
    storage_key: str | None = None
```

- [ ] **Step 4: Add the storage walker**

Add below `_glob` in `src/splitsmith/cleanup.py`:

```python
def _storage_listing(project: MatchProject) -> dict[str, int] | None:
    """``key -> size`` for everything under this project's scope.

    ``None`` means no bound storage -- ask the disk, which is what desktop
    does. An empty dict means storage answered and the scope is empty.
    Callers must keep those apart, exactly as ``_stored_exports`` does:
    collapsing them makes a storage hiccup look like a project with no
    files, and this module deletes things.

    One ``list`` for the whole scope rather than one per category: a
    seven-category plan would otherwise be seven round trips.
    """
    storage = project._storage
    scope = project._storage_scope
    if storage is None or scope is None:
        return None
    try:
        return {obj.path: obj.size for obj in storage.list(f"{scope}/")}
    except Exception:  # noqa: BLE001 -- a hiccup degrades to "nothing found", not a 500
        return {}


def _iter_storage_items(
    project: MatchProject,
    root: Path,
    category: CleanupCategory,
    listing: dict[str, int],
) -> Iterable[CleanupItem]:
    """Yield storage-backed items for ``category`` out of a scope listing.

    Refuses anything not under ``<scope>/<subdir>/`` and anything under
    ``<scope>/raw/`` -- the storage analogue of :func:`_safe_under_raw`.
    Fails closed: a key that does not classify is not deleted.
    """
    scope = project._storage_scope
    if scope is None:
        return
    raw_prefix = f"{scope}/raw/"
    for source in _CATEGORY_SOURCES[category]:
        if source.storage_subdir is None:
            continue
        prefix = f"{scope}/{source.storage_subdir}/"
        local_dir = source.local(project, root)
        for key, size in listing.items():
            if not key.startswith(prefix) or key.startswith(raw_prefix):
                continue
            name = key[len(prefix) :]
            if "/" in name:
                # Nested keys are not artefacts this module wrote.
                continue
            if not any(fnmatch(name, pat) for pat in source.patterns):
                continue
            yield CleanupItem(
                path=local_dir / name,
                size_bytes=size,
                category=category,
                storage_key=key,
            )
```

Add `from fnmatch import fnmatch` to the imports.

- [ ] **Step 5: Wire it into `plan_cleanup`**

In `plan_cleanup`, after `totals` is built, replace the per-category loop
body so it walks both sources:

```python
    listing = _storage_listing(project)

    for category in requested:
        for path in _iter_paths(project, root, category):
            if not _safe_under_raw(project, root, path):
                # Should never happen with the current globs; guard kept
                # so a future bug can't escalate into deleting raw refs.
                continue
            try:
                size = path.lstat().st_size
            except OSError:
                continue
            items.append(CleanupItem(path=path, size_bytes=size, category=category))
            t = totals[category]
            t.file_count += 1
            t.bytes += size
        if listing:
            for item in _iter_storage_items(project, root, category, listing):
                items.append(item)
                t = totals[category]
                t.file_count += 1
                t.bytes += item.size_bytes
```

Then make the sort stable across the two sources -- a storage item and a
local item share a `path`, so `path` alone is no longer a unique key:

```python
    items.sort(key=lambda it: (it.category.value, str(it.path), it.storage_key or ""))
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_cleanup_storage.py -v -n0`
Expected: PASS.

Run: `uv run pytest tests/test_cleanup.py -v -n0`
Expected: PASS, all 13, unmodified.

- [ ] **Step 7: Commit**

```bash
uv run black src/splitsmith/cleanup.py tests/test_cleanup_storage.py
git add src/splitsmith/cleanup.py tests/test_cleanup_storage.py
git commit -m "feat(cleanup): plan against object storage, not just the container's disk"
```

---

### Task 5: The `reconstructable` flag

**Files:**
- Modify: `src/splitsmith/cleanup.py`
- Test: `tests/test_cleanup_storage.py` (extend), `tests/test_cleanup.py` (extend)

**Interfaces:**
- Consumes: `stage_number_from_filename` (Task 1), `source_present(durable=)` (Task 2).
- Produces: `CleanupItem.reconstructable: bool`; `SAFE_CATEGORIES` semantics unchanged.

Per the spec's table: reconstructable means *this artefact's own input
still exists*, not "the source video exists".

| category | reconstructable when |
| --- | --- |
| `CACHES` | always |
| `EXPORTS_LIGHT` | the stage's audit doc is present (match-level: any audit doc present) |
| `EXPORTS_TRIMS` / `EXPORTS_OVERLAYS` / `AUDIT_TRIMS` | that stage's primary source is durably present |
| `AUDIO` | every registered video's source is durably present |
| `AUDIT_DATA` | never |

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cleanup_storage.py`:

```python
def _stage_with_primary(project: MatchProject, root: Path, rel: str) -> None:
    """Give the project one stage whose primary points at ``rel``.

    ``StageEntry`` / ``StageVideo`` live in ``splitsmith.match_project``,
    not ``match_model``. ``time_seconds`` is required. ``StageVideo`` has
    no ``video_id`` field -- ``role="primary"`` is what ``primary()``
    looks for.
    """
    from splitsmith.match_project import StageEntry, StageVideo

    project.stages.append(
        StageEntry(
            stage_number=1,
            stage_name="alpha",
            time_seconds=12.5,
            videos=[StageVideo(path=Path(rel), role="primary")],
        )
    )
    project.save(root)


def test_a_trim_is_reconstructable_while_its_source_survives(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    # NOT scope-prefixed: ``source_present`` calls
    # ``storage.exists(str(video_path))`` with the user-prefix-relative
    # path. Scope prefixes derived caches (exports/, trimmed/, audio/),
    # never raw sources -- see ``bind_storage``'s docstring.
    _put(backing, "raw/clip.mp4", b"source")
    _put(backing, f"{SCOPE}/exports/stage1_alpha_trimmed.mp4")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    assert [i.reconstructable for i in plan.items] == [True]


def test_a_trim_is_not_reconstructable_once_the_source_is_gone(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    # Source absent from storage; present only in the ephemeral local cache.
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "clip.mp4").write_bytes(b"cached")
    _put(backing, f"{SCOPE}/exports/stage1_alpha_trimmed.mp4")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})

    assert [i.reconstructable for i in plan.items] == [False]


def test_exports_light_uses_the_callers_audit_stages_on_hosted(tmp_path: Path) -> None:
    """Hosted audit docs are in ``state_docs``, not on this disk.

    Without the ``audit_stages`` hand-off the planner reads an empty
    container directory and calls every CSV and FCPXML unrebuildable,
    which would push the cheapest category out of "select all" on exactly
    the deployment this change exists for.
    """
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    _put(backing, f"{SCOPE}/exports/stage1_alpha.fcpxml")
    # Nothing on local disk; the caller knows stage 1 has an audit doc.

    plan = plan_cleanup(
        project, root, {CleanupCategory.EXPORTS_LIGHT}, audit_stages={1}
    )

    assert [i.reconstructable for i in plan.items] == [True]


def test_exports_light_keys_on_the_audit_doc_not_the_source(tmp_path: Path) -> None:
    """The row that regresses desktop select-all if keyed on the source.

    A CSV/FCPXML is re-derived from the audit doc, which is durable and
    only removable through the separately-gated AUDIT_DATA category. If
    this were keyed on the source video, EXPORTS_LIGHT would drop out of
    "select all" the moment a source went missing -- for the cheapest,
    most re-derivable category in the table.
    """
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    # No source anywhere; audit doc present.
    (root / "audit").mkdir(parents=True, exist_ok=True)
    (root / "audit" / "stage1.json").write_text("{}", encoding="utf-8")
    _put(backing, f"{SCOPE}/exports/stage1_alpha.fcpxml")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_LIGHT})

    assert [i.reconstructable for i in plan.items] == [True]


def test_exports_light_flips_when_the_audit_doc_is_gone(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _stage_with_primary(project, root, "raw/clip.mp4")
    _put(backing, f"{SCOPE}/exports/stage1_alpha.fcpxml")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_LIGHT})

    assert [i.reconstructable for i in plan.items] == [False]
```

Append to `tests/test_cleanup.py`:

```python
def test_desktop_items_are_reconstructable_by_default(tmp_path: Path) -> None:
    """Desktop, sources and audit present: nothing is flagged.

    Pins the ordinary case, where the flag must not change what "select
    all" offers today.
    """
    # Build using this file's existing project helper, then:
    plan = plan_cleanup(project, root, {CleanupCategory.CACHES})
    assert all(i.reconstructable for i in plan.items)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cleanup_storage.py -k reconstructable -v -n0`
Expected: FAIL, `AttributeError`/`ValidationError` -- `CleanupItem` has no
`reconstructable`.

- [ ] **Step 3: Implement**

Add the field:

```python
    reconstructable: bool = True
```

with this docstring line added to `CleanupItem`'s docstring:

```
    ``reconstructable`` is False when this artefact's own input is already
    gone, so deleting it costs data rather than recompute time. Such items
    are excluded from "select all" and need an explicit opt-in -- the same
    treatment ``AUDIT_DATA`` already gets from ``SAFE_CATEGORIES``, for the
    same reason. They are still *shown*: silently omitting a 4 GB trim from
    a plan that promises to list what can be reclaimed makes the plan a
    liar, and the user has no way to learn why it vanished.
```

Add the resolver:

```python
def _audited_stages(project: MatchProject, root: Path, audit_stages: set[int] | None) -> set[int]:
    """Stage numbers with a surviving audit doc.

    ``audit_stages`` is supplied by the caller in hosted mode, exactly as
    ``export_overview`` takes ``audit_docs``: hosted audit docs live in the
    ``state_docs`` table, not on this container's disk, so reading
    ``audit_path`` there finds an empty directory and would report every
    export deliverable as unrebuildable. ``None`` means "no caller
    knowledge, read the disk", which is desktop.

    Same None-vs-empty discipline as ``_stored_exports``: an empty *set*
    means the caller looked and found none.
    """
    if audit_stages is not None:
        return audit_stages
    audit_dir = project.audit_path(root)
    return {
        s.stage_number
        for s in project.stages
        if (audit_dir / f"stage{s.stage_number}.json").exists()
    }


def _reconstructable(
    project: MatchProject,
    root: Path,
    category: CleanupCategory,
    filename: str,
    audited: set[int],
) -> bool:
    """Whether ``filename``'s own input still exists -- see the table in
    the design doc. Conservative: an unanswerable question is False."""
    if category is CleanupCategory.CACHES:
        return True
    if category is CleanupCategory.AUDIT_DATA:
        return False

    stage_number = stage_number_from_filename(filename)

    if category is CleanupCategory.EXPORTS_LIGHT:
        if stage_number is None:
            # Match-level deliverable: rebuildable if any audit doc survives.
            return bool(audited)
        return stage_number in audited

    # EXPORTS_TRIMS / EXPORTS_OVERLAYS / AUDIT_TRIMS / AUDIO: source-derived.
    if stage_number is None:
        # AUDIO wavs are named after the video, not the stage: fall back to
        # "every registered source is durably present".
        videos = [v for s in project.stages for v in s.videos]
        return bool(videos) and all(
            project.source_present(root, v.path, durable=True) for v in videos
        )
    stage = next((s for s in project.stages if s.stage_number == stage_number), None)
    primary = stage.primary() if stage is not None else None
    if primary is None:
        return False
    return project.source_present(root, primary.path, durable=True)
```

Add `from .export_naming import stage_number_from_filename` to the imports.

Thread the caller's knowledge through `plan_cleanup`:

```python
def plan_cleanup(
    project: MatchProject,
    root: Path,
    categories: Iterable[CleanupCategory],
    *,
    audit_stages: set[int] | None = None,
) -> CleanupPlan:
```

Extend its docstring with:

```
    ``audit_stages`` names the stages that still have an audit doc.
    Hosted callers must pass it -- their audit docs live in ``state_docs``
    rather than on this container's disk, so leaving it ``None`` there
    would mark every CSV and FCPXML unrebuildable. Mirrors
    ``MatchProject.export_overview``'s ``audit_docs`` parameter, which
    exists for the same reason.
```

Compute once, near the top of the function body:

```python
    audited = _audited_stages(project, root, audit_stages)
```

Then set the flag at both construction sites, `plan_cleanup`'s local loop:

```python
reconstructable=_reconstructable(project, root, category, path.name, audited)
```

and `_iter_storage_items` (which takes `audited` as a new parameter and
passes it straight through):

```python
reconstructable=_reconstructable(project, root, category, name, audited)
```

- [ ] **Step 3b: Pass it from the hosted route**

In `src/splitsmith/ui/server.py`, both `cleanup_plan` and `cleanup_apply`
load the audited stage numbers the way `export_overview` does, and pass
them:

```python
        audited = {
            stg.stage_number
            for stg in project.stages
            if state.load_audit(slug, stg.stage_number)[0] is not None
        }
        plan = cleanup_module.plan_cleanup(project, root, cats, audit_stages=audited)
```

`state.load_audit` returns `(doc, version)` and reads `state_docs` on
hosted, the file on desktop -- the same accessor `export_overview`'s route
uses, so desktop keeps working through the identical path.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cleanup_storage.py tests/test_cleanup.py -v -n0`
Expected: PASS, all of them.

- [ ] **Step 5: The mutation drill**

Required by the project's review practice: a test that passes against the
pre-change code is not coverage.

```bash
git stash push src/splitsmith/cleanup.py
uv run pytest tests/test_cleanup_storage.py -k reconstructable -n0   # MUST FAIL
git stash pop
```

If any of the four `reconstructable` tests passes with the fix stashed,
that test is wrong -- fix the test, not the assertion.

- [ ] **Step 6: Commit**

```bash
uv run black src/splitsmith/cleanup.py tests/test_cleanup_storage.py tests/test_cleanup.py
git add src/splitsmith/cleanup.py tests/test_cleanup_storage.py tests/test_cleanup.py
git commit -m "feat(cleanup): flag artefacts whose input is already gone rather than hiding them"
```

---

### Task 6: Apply against storage, and move the log

**Files:**
- Modify: `src/splitsmith/cleanup.py` (`apply_cleanup`, `_append_log`)
- Test: `tests/test_cleanup_storage.py` (extend)

**Interfaces:**
- Consumes: `CleanupItem.storage_key` (Task 4).
- Produces: `apply_cleanup(plan, *, root=None, project=None) -> CleanupResult`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cleanup_storage.py`:

```python
from splitsmith.cleanup import apply_cleanup


def test_apply_deletes_the_storage_object_and_the_local_mirror(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")
    local = root / "exports" / "stage1_a_trimmed.mp4"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b"01234")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
    result = apply_cleanup(plan, root=root, project=project)

    assert not (backing / SCOPE / "exports" / "stage1_a_trimmed.mp4").exists()
    assert not local.exists()
    assert result.failed == []


def test_apply_deletes_no_key_outside_the_plan(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4")
    _put(backing, f"{SCOPE}/exports/stage1_a.fcpxml")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
    apply_cleanup(plan, root=root, project=project)

    assert (backing / SCOPE / "exports" / "stage1_a.fcpxml").exists()


def test_apply_writes_the_log_to_storage_on_hosted(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")

    plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
    apply_cleanup(plan, root=root, project=project)

    log = (backing / SCOPE / ".cleanup.log").read_text(encoding="utf-8")
    assert log.count("\n") == 1
    assert "bytes_freed" in log


def test_apply_appends_rather_than_overwrites_the_storage_log(tmp_path: Path) -> None:
    project, root, backing = _project(tmp_path)
    for name in ("stage1_a_trimmed.mp4", "stage2_b_trimmed.mp4"):
        _put(backing, f"{SCOPE}/exports/{name}")
        plan = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS})
        apply_cleanup(plan, root=root, project=project)

    log = (backing / SCOPE / ".cleanup.log").read_text(encoding="utf-8")
    assert log.count("\n") == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cleanup_storage.py -k apply -v -n0`
Expected: FAIL -- `apply_cleanup() got an unexpected keyword argument 'project'`.

- [ ] **Step 3: Implement**

Change the signature and the delete loop:

```python
def apply_cleanup(
    plan: CleanupPlan,
    *,
    root: Path | None = None,
    project: MatchProject | None = None,
) -> CleanupResult:
```

Extend the docstring with:

```
    ``project`` supplies the bound storage. An item carrying a
    ``storage_key`` has its object deleted first -- that is the durable
    byte -- and then any local mirror is unlinked so the running container
    stops serving a copy it already pulled. The mirror is a cache and is
    not counted again in ``bytes_freed``.
```

Loop body:

```python
    storage = project._storage if project is not None else None

    for item in plan.items:
        if item.storage_key is not None:
            if storage is None:
                failed.append((item.path, "no storage bound"))
                continue
            try:
                storage.delete(item.storage_key)
            except Exception as exc:  # noqa: BLE001 -- per-item, never fatal
                failed.append((item.path, str(exc)))
                continue
            # Drop the container's mirrored copy too; it is a cache.
            try:
                item.path.unlink(missing_ok=True)
            except OSError:
                pass
            deleted.append(item.path)
            bytes_freed += item.size_bytes
            continue
        try:
            item.path.unlink(missing_ok=True)
        except OSError as exc:
            failed.append((item.path, str(exc)))
            continue
        deleted.append(item.path)
        bytes_freed += item.size_bytes
```

Log routing:

```python
    if project is not None and project._storage is not None and project._storage_scope is not None:
        try:
            _append_storage_log(project, plan, result)
        except Exception:  # noqa: BLE001 -- best effort, as the disk log is
            pass
    elif root is not None:
        try:
            _append_log(root, plan, result)
        except OSError:
            pass
```

New function, next to `_append_log`:

```python
def _append_storage_log(project: MatchProject, plan: CleanupPlan, result: CleanupResult) -> None:
    """Append one JSONL line to ``<scope>/.cleanup.log`` in storage.

    Read-modify-write, because object storage has no append. Two
    concurrent cleanups can therefore lose a line. Accepted: this is the
    audit trail of a manual, single-user action that the API already
    refuses while any job is active, and the alternative on offer today is
    losing the whole file on every redeploy, since ``<root>`` is an
    ephemeral container disk. A durable answer means a new tenant table
    for one JSONL line, which would also pull in #632's ``match_id``
    constraint for no benefit.
    """
    storage = project._storage
    scope = project._storage_scope
    if storage is None or scope is None:
        return
    key = f"{scope}/{CLEANUP_LOG_FILENAME}"
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "categories": sorted({item.category.value for item in plan.items}),
        "deleted_count": len(result.deleted),
        "failed_count": len(result.failed),
        "bytes_freed": result.bytes_freed,
    }
    try:
        existing = storage.read_bytes(key)
    except Exception:  # noqa: BLE001 -- absent log is the common case
        existing = b""
    storage.write_bytes(key, existing + (json.dumps(record) + "\n").encode("utf-8"))
```

- [ ] **Step 4: Update the two callers**

`src/splitsmith/ui/server.py:12494` -- pass the project:

```python
        result = cleanup_module.apply_cleanup(plan, root=root, project=project)
```

`src/splitsmith/cli.py` (near line 1067) -- the CLI is desktop-only, so it
keeps `root=` and passes no project. Confirm by reading the call; change
nothing if it already reads `apply_cleanup(plan, root=project_root)`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_cleanup_storage.py tests/test_cleanup.py -v -n0`
Expected: PASS. `test_apply_writes_cleanup_log` in the desktop file must
still pass -- desktop has no storage, so it still writes `<root>/.cleanup.log`.

- [ ] **Step 6: Commit**

```bash
uv run black src/splitsmith/cleanup.py src/splitsmith/ui/server.py tests/test_cleanup_storage.py
git add src/splitsmith/cleanup.py src/splitsmith/ui/server.py tests/test_cleanup_storage.py
git commit -m "feat(cleanup): delete storage objects and keep the audit log where the bytes are"
```

---

### Task 7: Pin the wire contract the dialog reads

The routes need no code change. `cleanup_plan` is
`JSONResponse(plan.model_dump(mode="json"))` (`server.py:12466`) and
`cleanup_apply` returns `{"plan": ..., "result": ...}` the same way -- pure
pass-throughs, so what reaches the SPA is decided by `CleanupItem`, not by
the routes.

There is deliberately **no HTTP test here.** `tests/test_ui_server.py` has
no reusable hosted fixture -- each hosted test stands up an in-memory
SQLite engine, a `User` row and a `ProjectStateStore` inline, ~40 lines.
Paying that to assert two dict keys on a pass-through would test the
harness, not the contract. Assert on the model instead.

**Files:**
- Test: `tests/test_cleanup_storage.py` (extend)

**Interfaces:**
- Consumes: `CleanupItem` (Tasks 4, 5).
- Produces: nothing.

- [ ] **Step 1: Write the test**

Append to `tests/test_cleanup_storage.py`:

```python
def test_plan_serialises_the_fields_the_spa_reads(tmp_path: Path) -> None:
    """``storage_key`` and ``reconstructable`` must survive model_dump.

    Both cleanup routes are pass-throughs of ``plan.model_dump(mode="json")``
    (``server.py:12466``), so this is the whole wire contract. A field the
    SPA's CleanupDialog reads that never reaches JSON would be a silent
    ``undefined`` in the browser and a green Python suite.
    """
    project, root, backing = _project(tmp_path)
    _put(backing, f"{SCOPE}/exports/stage1_a_trimmed.mp4", b"0123456789")

    payload = plan_cleanup(project, root, {CleanupCategory.EXPORTS_TRIMS}).model_dump(mode="json")

    assert payload["items"], "expected one planned item"
    item = payload["items"][0]
    assert item["storage_key"] == f"{SCOPE}/exports/stage1_a_trimmed.mp4"
    assert item["reconstructable"] is False  # no stage registered -> no source
    assert isinstance(item["path"], str)  # Path must serialise for the SPA
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_cleanup_storage.py -k serialises -v -n0`
Expected: PASS. If it fails, the fix is in `CleanupItem`, not the route.

- [ ] **Step 3: Commit**

```bash
uv run black tests/test_cleanup_storage.py
git add tests/test_cleanup_storage.py
git commit -m "test(cleanup): pin the plan fields the SPA dialog reads off the wire"
```

---

### Task 8: SPA types + the dialog

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (the `CleanupItem` type, near line 2314)
- Create: `src/splitsmith/ui_static/src/components/CleanupDialog.tsx`
- Create: `src/splitsmith/ui_static/src/components/CleanupDialog.test.tsx`

**Interfaces:**
- Consumes, both already in `lib/api.ts` and with no caller until now:
  - `api.getCleanupPlan(slug: string, categories: CleanupCategory[]) => Promise<CleanupPlan>` -- note the name is `getCleanupPlan`, not `cleanupPlan`
  - `api.applyCleanup(slug: string, categories: CleanupCategory[]) => Promise<CleanupApplyResponse>`
- Existing types to reuse verbatim, do not redeclare: `CleanupCategory`
  (a 7-member string union), `CleanupItem`, `CleanupTotals`, `CleanupPlan`,
  `CleanupResult`, `CleanupApplyResponse` (`lib/api.ts:980-1016`).
- Produces: `<CleanupDialog slug={string} open={boolean} onClose={() => void} />`

- [ ] **Step 1: Extend the types and add the 409 reader**

In `lib/api.ts`, add to the existing `CleanupItem` interface (line 989):

```ts
  /** Set when the durable bytes live in object storage (hosted). */
  storage_key: string | null;
  /** False when this artefact's own input is already gone, so deleting
   *  it costs data rather than recompute time. Such items are kept out
   *  of "select all" and need an explicit opt-in. */
  reconstructable: boolean;
```

`ApiError` is **not exported** (`api.ts:1961`), so a component cannot
`instanceof` it. The shipped pattern is an exported reader per error
shape -- `asScoreboardError`, `asSourceUnreachable`. Add one, next to
those:

```ts
/** The 409 body ``cleanup_apply`` returns while a job is in flight. */
export interface JobsActiveDetail {
  code: "jobs_active";
  message: string;
  job_id: string;
  kind: string;
}

/** Pull a jobs-active refusal out of an ApiError, or null if the body
 *  doesn't match. The cleanup dialog names the blocking job rather than
 *  showing a generic failure -- "trim is still running" is actionable and
 *  "cleanup failed" is not. */
export function asJobsActiveError(err: unknown): JobsActiveDetail | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body;
  if (!body || typeof body !== "object") return null;
  if ((body as { code?: unknown }).code !== "jobs_active") return null;
  return body as JobsActiveDetail;
}
```

`ApiError`'s third constructor argument is the response's `detail` value,
so `err.body` is the dict the route raised -- `err.detail` is a string.
Getting those two backwards is the exact distinction `apiErrors.test.ts`
exists to pin.

- [ ] **Step 2: Write the failing test**

Create `CleanupDialog.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CleanupDialog } from "@/components/CleanupDialog";

/** ``fetch`` is mocked rather than ``api.*`` so the rejection the dialog
 *  sees is a genuine ``ApiError`` built by production code. ``ApiError``
 *  is not exported, and ``apiErrors.test.ts`` documents why exporting it
 *  for tests would let them drift from how errors are really constructed
 *  -- ``detail`` (a string) versus ``body`` (the dict) is exactly what
 *  ``asJobsActiveError`` reads. */
const PLAN = {
  items: [
    {
      path: "exports/stage1_a_trimmed.mp4",
      size_bytes: 1_048_576,
      category: "exports-trims",
      storage_key: "matches/m1/shooters/me/exports/stage1_a_trimmed.mp4",
      reconstructable: true,
    },
    {
      path: "exports/stage2_b_trimmed.mp4",
      size_bytes: 2_097_152,
      category: "exports-trims",
      storage_key: "matches/m1/shooters/me/exports/stage2_b_trimmed.mp4",
      reconstructable: false,
    },
  ],
  totals_by_category: { "exports-trims": { file_count: 2, bytes: 3_145_728 } },
  total_bytes: 3_145_728,
  total_file_count: 2,
};

function ok(body: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => body } as unknown as Response;
}

function err(status: number, body: unknown) {
  return {
    ok: false,
    status,
    statusText: "Conflict",
    json: async () => ({ detail: body }),
  } as unknown as Response;
}

/** Route by method: the plan is a GET, the apply is a POST. */
function mockFetch(applyResponse: Response) {
  vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => {
    const method = (init as RequestInit | undefined)?.method ?? "GET";
    return Promise.resolve(method === "POST" ? applyResponse : ok(PLAN));
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CleanupDialog", () => {
  it("shows totals from the plan", async () => {
    mockFetch(ok({ plan: PLAN, result: { deleted: [], failed: [], bytes_freed: 0 } }));
    render(<CleanupDialog slug="me" open onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /select all/i }));
    // ``formatBytes(3_145_728)`` is "3.0 MB", not "3 MB". Scope to the
    // total line: the category row renders the same string, so a bare
    // ``getByText(/3\.0 MB/)`` would throw on multiple matches.
    await waitFor(() =>
      expect(screen.getByText(/Total: 3\.0 MB/)).toBeInTheDocument(),
    );
  });

  it("lists what cannot be rebuilt, unchecked, after select all", async () => {
    mockFetch(ok({ plan: PLAN, result: { deleted: [], failed: [], bytes_freed: 0 } }));
    render(<CleanupDialog slug="me" open onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /select all/i }));

    // The unrebuildable item is shown -- never silently dropped -- in its
    // own opt-in section, and starts unchecked even after "select all".
    const region = await screen.findByRole("region", { name: /cannot be rebuilt/i });
    expect(region).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "stage2_b_trimmed.mp4" }),
    ).not.toBeChecked();
    // The reconstructable one is not in that section at all.
    expect(
      screen.queryByRole("checkbox", { name: "stage1_a_trimmed.mp4" }),
    ).not.toBeInTheDocument();
  });

  it("leaves audit-data out of select all", async () => {
    mockFetch(ok({ plan: PLAN, result: { deleted: [], failed: [], bytes_freed: 0 } }));
    render(<CleanupDialog slug="me" open onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /select all/i }));
    expect(screen.getByRole("checkbox", { name: /audit data/i })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /lossless export trims/i })).toBeChecked();
  });

  it("names the blocking job on a 409 instead of failing generically", async () => {
    mockFetch(
      err(409, {
        code: "jobs_active",
        message: "Job 'trim' is still running",
        job_id: "j1",
        kind: "trim",
      }),
    );
    render(<CleanupDialog slug="me" open onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /select all/i }));
    await userEvent.click(await screen.findByRole("button", { name: /^reclaim$/i }));
    await userEvent.click(screen.getByRole("button", { name: /confirm/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/trim/);
    expect(alert).not.toHaveTextContent(/cleanup failed/i);
  });
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm test CleanupDialog`
Expected: FAIL -- the module does not exist.

- [ ] **Step 4: Implement the dialog**

Create `src/splitsmith/ui_static/src/components/CleanupDialog.tsx`. Uses
`formatBytes` from `lib/format.ts` (already exists) and `Portal` from
`components/ui/Portal.tsx`. Do not invent new chrome.

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  api,
  asJobsActiveError,
  type CleanupCategory,
  type CleanupPlan,
  type JobsActiveDetail,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { Portal } from "@/components/ui/Portal";
import { Button } from "@/components/ui/button";

/** Every category except audit-data, which destroys the user's audit work
 *  rather than costing recompute time. Mirrors ``SAFE_CATEGORIES`` in
 *  ``splitsmith/cleanup.py`` -- the server re-plans from the categories it
 *  is sent, so this list is convenience, never enforcement. */
const SAFE: CleanupCategory[] = [
  "caches",
  "exports-light",
  "exports-overlays",
  "exports-trims",
  "audit-trims",
  "audio",
];

const ALL: CleanupCategory[] = [...SAFE, "audit-data"];

const LABELS: Record<CleanupCategory, string> = {
  caches: "Thumbnails, probes and waveform caches",
  "exports-light": "CSV, FCPXML and reports",
  "exports-overlays": "Rendered overlays",
  "exports-trims": "Lossless export trims",
  "audit-trims": "Audit scrub copies",
  audio: "Extracted audio",
  "audit-data": "Audit data (your shot edits)",
};

export function CleanupDialog({
  slug,
  open,
  onClose,
}: {
  slug: string;
  open: boolean;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<CleanupCategory[]>([]);
  const [plan, setPlan] = useState<CleanupPlan | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [blocked, setBlocked] = useState<JobsActiveDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Debounced: the plan route returns an empty plan for unknown or partial
  // selections rather than a 400, specifically so this can fetch on every
  // toggle. That contract was written for a caller that never arrived.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const t = setTimeout(() => {
      api
        .getCleanupPlan(slug, selected)
        .then((p) => {
          if (!cancelled) setPlan(p);
        })
        .catch(() => {
          if (!cancelled) setError("Could not read the cleanup plan.");
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [slug, selected, open]);

  /** Items the current plan cannot rebuild. Shown, never hidden: silently
   *  omitting a 4 GB trim from a list that promises what can be reclaimed
   *  leaves the user no way to learn why it vanished. */
  const unrebuildable = useMemo(
    () => (plan?.items ?? []).filter((i) => !i.reconstructable),
    [plan],
  );

  const toggle = useCallback((c: CleanupCategory) => {
    setSelected((prev) =>
      prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c],
    );
  }, []);

  const apply = useCallback(async () => {
    setError(null);
    setBlocked(null);
    try {
      await api.applyCleanup(slug, selected);
      setConfirming(false);
      onClose();
    } catch (e) {
      const jobsActive = asJobsActiveError(e);
      if (jobsActive) {
        setBlocked(jobsActive);
        return;
      }
      setError("Cleanup failed.");
    }
  }, [slug, selected, onClose]);

  if (!open) return null;

  return (
    <Portal>
      <div role="dialog" aria-label="Reclaim space">
        <h2>Reclaim space</h2>

        <Button onClick={() => setSelected(SAFE)}>Select all</Button>

        <ul>
          {ALL.map((c) => {
            const t = plan?.totals_by_category?.[c];
            return (
              <li key={c}>
                <label>
                  <input
                    type="checkbox"
                    checked={selected.includes(c)}
                    onChange={() => toggle(c)}
                    aria-label={LABELS[c]}
                  />
                  {LABELS[c]}
                </label>
                {t ? (
                  <span>
                    {t.file_count} files, {formatBytes(t.bytes)}
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>

        {plan ? <p>Total: {formatBytes(plan.total_bytes)}</p> : null}

        {unrebuildable.length > 0 ? (
          <section aria-label="cannot be rebuilt">
            <p>
              {unrebuildable.length} of these cannot be rebuilt -- their source
              or audit data is already gone. Deleting them loses the file for
              good.
            </p>
            <ul>
              {unrebuildable.map((i) => (
                <li key={i.storage_key ?? i.path}>
                  <label>
                    <input
                      type="checkbox"
                      aria-label={i.path.split("/").pop() ?? i.path}
                    />
                    {i.path.split("/").pop()} ({formatBytes(i.size_bytes)})
                  </label>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {blocked ? (
          <p role="alert">
            {blocked.kind} is still running. Wait for it to finish, or cancel
            it, before reclaiming space.
          </p>
        ) : null}
        {error ? <p role="alert">{error}</p> : null}

        {confirming ? (
          <Button onClick={apply}>Confirm</Button>
        ) : (
          <Button disabled={selected.length === 0} onClick={() => setConfirming(true)}>
            Reclaim
          </Button>
        )}
        <Button onClick={onClose}>Cancel</Button>
      </div>
    </Portal>
  );
}
```

The markup above is deliberately unstyled -- it is the behaviour contract
the tests pin. Style it with the existing `card` / `button` /
`StatusPill` primitives while keeping every `role`, `aria-label` and text
string intact, or the tests in Step 2 break.

Note the checkboxes in the "cannot be rebuilt" section are the explicit
opt-in and start unchecked; wire them to whatever per-item selection the
styled version needs. Category selection is what the server acts on --
per-item opt-in gates the *button*, since the API takes categories, not
paths (`cleanup_apply` re-plans server-side and "the client only sends
categories, never paths" is deliberate).

- [ ] **Step 5: Run tests**

Run: `cd src/splitsmith/ui_static && pnpm test CleanupDialog && pnpm typecheck && pnpm lint`
Expected: PASS all three. Use `pnpm typecheck`, never bare `tsc --noEmit`.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/api.ts \
        src/splitsmith/ui_static/src/components/CleanupDialog.tsx \
        src/splitsmith/ui_static/src/components/CleanupDialog.test.tsx
git commit -m "feat(ui): a cleanup dialog for the two routes that had no caller"
```

---

### Task 9: Mount it on the Export page

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Export.tsx`
- Test: `src/splitsmith/ui_static/src/pages/Export.cleanup.test.tsx` (create)

**Interfaces:**
- Consumes: `<CleanupDialog />` (Task 8).

- [ ] **Step 1: Write the failing test**

```tsx
it("opens the cleanup dialog from the Export page", async () => {
  renderExportPage();   // reuse this file's existing render helper
  await userEvent.click(await screen.findByRole("button", { name: /reclaim space/i }));
  expect(await screen.findByRole("dialog", { name: /reclaim space/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src/splitsmith/ui_static && pnpm test Export.cleanup`
Expected: FAIL -- no such button.

- [ ] **Step 3: Add the entry point**

A "Reclaim space" action in the Export page's existing deliverables
section, next to the download list, opening `<CleanupDialog />`.

Placement rationale, for whoever reads this later: Export is where
deliverables are already listed with presence and timestamps, so it is
where the intent "I have too many of these" forms, and #629 is an
Export-page issue. It is an imperfect fit -- cleanup also spans `caches`,
`audio` and `audit-trims`, which are not export concepts. If this needs to
move, Home is the alternative, and the dialog is self-contained enough
that moving it is a one-line change.

- [ ] **Step 4: Run tests**

Run: `cd src/splitsmith/ui_static && pnpm test && pnpm typecheck && pnpm lint`
Expected: PASS.

- [ ] **Step 5: Full suite before the PR**

```bash
uv run pytest
cd src/splitsmith/ui_static && pnpm test
```

Expected: green. A local-only red run on this box is usually load rather
than a defect -- but confirm `main` is green before accepting that.

- [ ] **Step 6: Commit and open the PR**

```bash
git add src/splitsmith/ui_static/src/pages/Export.tsx \
        src/splitsmith/ui_static/src/pages/Export.cleanup.test.tsx
git commit -m "feat(ui): reach the cleanup dialog from the Export page"
git push -u origin feat/storage-aware-cleanup
```

PR title must be a valid conventional-commit subject -- it becomes the
squash commit and feeds release-please. Suggested:

```
feat(cleanup): reclaim space on hosted, and give the dialog a caller
```

Write the PR body as prose, not a bullet list of commit messages: the repo
is configured `squash_merge_commit_message=PR_BODY`, so the description
*is* the commit body, and a `* type: subject` list breaks release-please's
parser silently.

---

## Verification checklist before requesting review

- [ ] `tests/test_cleanup.py` is unchanged except for the one added desktop test, and green.
- [ ] The mutation drill has been run on every `reconstructable` test (Task 5, Step 5).
- [ ] Desktop `splitsmith cleanup` still writes `<root>/.cleanup.log`, not a storage object.
- [ ] Hosted `audit-data` plans zero items, and the dialog does not imply otherwise.
- [ ] No new dependency in `pyproject.toml` or `package.json`.
- [ ] `uv run black --check .` and `uv run ruff check .` clean; `pnpm typecheck` clean.

State plainly in the PR body which tests fail against pre-change code and
which are surface guards that pass either way. The desktop regression set
passes by construction and is not evidence the change works.
