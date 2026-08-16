"""Tiered project cleanup -- plan + apply (issue: reclaim disk space).

The disk footprint of a project grows fast: rendered overlays and lossless
trims are hundreds of MB to multi-GB each, audit-mode trims and extracted
audio are similar order. Most of these are recreatable from the source
video + audit JSON, but recomputing them costs minutes of ffmpeg time, so
the user picks which categories to drop.

Two-phase API:

- :func:`plan_cleanup` walks the project's resolved directories and returns
  a :class:`CleanupPlan` (file list + per-category totals). Pure: no
  deletion happens here. Callers can preview the plan, render it, decide.
- :func:`apply_cleanup` walks the plan, unlinks each file, and returns a
  :class:`CleanupResult`. Records to ``<root>/.cleanup.log`` (JSONL) when
  ``root`` is given so the user has an audit trail of what was reclaimed.

Categories are independent toggles, NOT a strict hierarchy. The CLI and
SPA both build the requested set from per-category flags / checkboxes.

What is NEVER touched:

- ``project.json`` -- contains user's video assignments and beep times.
- ``raw/`` -- the symlinks that point at the user's original sources.
- The original source video files themselves.

The :class:`CleanupCategory.AUDIT_DATA` bucket *is* destructive (drops
the user's audit work). It is excluded from the convenience ``--all`` /
"select all" affordance and gated by an explicit opt-in.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, Field

from .export_naming import stage_number_from_filename, video_id_from_filename
from .match_project import MatchProject

logger = logging.getLogger(__name__)

# Filename for the per-project cleanup audit trail. JSONL so multiple
# cleanups append cleanly. Hidden so it doesn't clutter Finder.
CLEANUP_LOG_FILENAME = ".cleanup.log"


class CleanupCategory(StrEnum):
    """Logical buckets the user can independently toggle.

    The string values are the wire format -- CLI flags use them with the
    ``-`` separator (``exports-light``, ``audit-data``) and the SPA passes
    them through unchanged. Adding a new bucket means: extend this enum,
    extend the glob mapping in :func:`_iter_paths`, and add the SPA
    checkbox + CLI flag.
    """

    CACHES = "caches"
    EXPORTS_LIGHT = "exports-light"
    EXPORTS_OVERLAYS = "exports-overlays"
    EXPORTS_TRIMS = "exports-trims"
    AUDIT_TRIMS = "audit-trims"
    AUDIO = "audio"
    AUDIT_DATA = "audit-data"


# Categories considered safe enough to include in --all / "select all".
# AUDIT_DATA is excluded; users opt in explicitly via --include-audit.
SAFE_CATEGORIES: frozenset[CleanupCategory] = frozenset(
    c for c in CleanupCategory if c is not CleanupCategory.AUDIT_DATA
)


class CleanupItem(BaseModel):
    """One file the plan would unlink.

    ``path`` is always the local-equivalent path -- for a storage object
    it is where the file would sit on disk, so the CLI's table and the
    SPA's list render identically either way. ``storage_key`` set means
    the durable bytes are the object, and ``path`` may not exist at all.

    ``reconstructable`` is False when this artefact's own input is already
    gone, so deleting it costs data rather than recompute time. Such items
    are excluded from "select all" and need an explicit opt-in -- the same
    treatment ``AUDIT_DATA`` already gets from ``SAFE_CATEGORIES``, for the
    same reason. They are still *shown*: silently omitting a 4 GB trim from
    a plan that promises to list what can be reclaimed makes the plan a
    liar, and the user has no way to learn why it vanished.
    """

    path: Path
    size_bytes: int
    category: CleanupCategory
    storage_key: str | None = None
    reconstructable: bool = True


class CleanupTotals(BaseModel):
    """Per-category roll-up surfaced in the plan + UI dialog."""

    file_count: int = 0
    bytes: int = 0


class CleanupPlan(BaseModel):
    """Side-effect description returned by :func:`plan_cleanup`.

    The plan is sortable and JSON-serialisable; the SPA renders totals
    and the CLI prints them via Rich. ``items`` is sorted by (category,
    path, storage_key) so the CLI plan output and the SPA preview agree.
    """

    items: list[CleanupItem] = Field(default_factory=list)
    totals_by_category: dict[CleanupCategory, CleanupTotals] = Field(default_factory=dict)
    total_bytes: int = 0
    total_file_count: int = 0


class CleanupResult(BaseModel):
    """Outcome of :func:`apply_cleanup`."""

    deleted: list[Path] = Field(default_factory=list)
    failed: list[tuple[Path, str]] = Field(default_factory=list)
    bytes_freed: int = 0


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


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
    CleanupCategory.AUDIT_TRIMS: (_Source(lambda p, r: p.trimmed_path(r), ("*.mp4",), "trimmed"),),
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


def _iter_paths(
    project: MatchProject,
    root: Path,
    category: CleanupCategory,
) -> Iterable[Path]:
    """Yield every file the given category would target.

    All directory access goes through ``MatchProject`` resolvers so path
    overrides (audio_dir, exports_dir, etc.) are respected. Missing dirs
    yield nothing rather than raising -- a fresh project that has never
    run a job has empty cache dirs and the cleanup should report zero,
    not crash.

    Symlinks are NOT yielded -- defence-in-depth so a user-placed
    symlink (e.g. someone pointing audio_dir at a shared drive with a
    softlink convention) can never resolve into the original source.
    """
    for source in _CATEGORY_SOURCES[category]:
        directory = source.local(project, root)
        for pattern in source.patterns:
            yield from _glob(directory, pattern)


def _glob(directory: Path, pattern: str) -> Iterable[Path]:
    """Glob ``directory`` for ``pattern`` while tolerating missing dirs.

    ``rglob`` is used when the pattern starts with ``**`` so the
    scoreboard cache (which has subdirs by content_type) is fully
    swept. Symlinks and non-files are skipped at the source.
    """
    if not directory.exists():
        return
    if pattern.startswith("**"):
        # rglob('**/*') over a missing dir would have raised; we guarded
        # above. Strip the leading '**/' so rglob does not double-prefix.
        suffix = pattern[3:] or "*"
        iterator = directory.rglob(suffix)
    else:
        iterator = directory.glob(pattern)
    for p in iterator:
        if p.is_symlink():
            continue
        if not p.is_file():
            continue
        yield p


class _StorageListingFailed(dict):
    """Sentinel returned by :func:`_storage_listing` when ``storage.list()``
    raised.

    A plain ``dict`` subclass on purpose: it stays falsy, so the existing
    ``if listing:`` gate in :func:`plan_cleanup` behaves exactly as it does
    for a genuine empty scope (skip iterating storage items -- there is
    nothing this pass can honestly offer from storage either way). But it
    is a distinct *type*, so a caller that cares -- today just the log
    line below, potentially a future one -- can always tell "storage
    answered and there is nothing there" apart from "we never actually
    asked". Collapsing the two into one ``{}`` is exactly the M1
    whole-branch finding: a caller reading the plan has no way to know the
    local-mirror-only items it lists are not a complete cleanup.
    """


def _storage_listing(project: MatchProject) -> dict[str, int] | None:
    """``key -> size`` for everything under this project's scope.

    ``None`` means no bound storage -- ask the disk, which is what desktop
    does. An empty dict means storage answered and the scope is empty.
    Callers must keep those apart, exactly as ``_stored_exports`` does:
    collapsing them makes a storage hiccup look like a project with no
    files, and this module deletes things.

    One ``list`` for the whole scope rather than one per category: a
    seven-category plan would otherwise be seven round trips.

    A ``storage.list()`` failure (network blip, throttling, a permission
    error) degrades to :class:`_StorageListingFailed` rather than
    propagating, logged as a warning so an operator can tell a quiet outage
    apart from a project that genuinely has nothing in storage. The plan
    that comes back in that case still walks the container's local
    mirrors -- refusing to plan *anything* over a storage hiccup would
    block cleanup of files that have nothing to do with the outage -- but
    it can only ever *under-report* what is reclaimable, never claim an
    object was durably freed when it was not: ``apply_cleanup`` only
    deletes a storage object for an item that actually carries a
    ``storage_key``, and this function is the only source of those.

    This is the opposite failure policy from
    ``MatchProject.source_present(durable=True)``, which re-raises
    anything but a 404 (see ``S3Storage.exists``) instead of degrading.
    That is deliberate, not an inconsistency: a wrong ``source_present``
    answer flips ``reconstructable``, which gates whether deleting a file
    destroys data, so it must fail loud. This function only decides what
    shows up as a *candidate* to delete, so a quiet degrade cannot itself
    cause data loss -- it can only leave storage bytes unreclaimed, which
    is the safe direction to fail in.
    """
    storage = project._storage
    scope = project._storage_scope
    if storage is None or scope is None:
        return None
    try:
        return {obj.path: obj.size for obj in storage.list(f"{scope}/")}
    except Exception:  # noqa: BLE001 -- a hiccup degrades to "nothing found", not a 500
        logger.warning(
            "cleanup: storage.list(%r) failed; planning against local mirrors only",
            f"{scope}/",
            exc_info=True,
        )
        return _StorageListingFailed()


def _iter_storage_items(
    project: MatchProject,
    root: Path,
    category: CleanupCategory,
    listing: dict[str, int],
    ctx: _PlanContext,
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
                reconstructable=_reconstructable(ctx, category, name),
            )


def _safe_under_raw(project: MatchProject, root: Path, candidate: Path) -> bool:
    """Defence-in-depth: refuse any item that resolves under ``raw/``.

    The cleanup never globs into ``raw/``, so this should never fire,
    but a typo in a future glob (or a symlink we missed) shouldn't be
    able to delete a source-video reference.
    """
    try:
        raw = project.raw_path(root).resolve()
    except OSError:
        return True
    try:
        candidate.resolve().relative_to(raw)
    except (OSError, ValueError):
        return True
    return False


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
    return {s.stage_number for s in project.stages if (audit_dir / f"stage{s.stage_number}.json").exists()}


class _PlanContext:
    """Shared, memoised state for one :func:`plan_cleanup` call.

    Two things are expensive enough to be worth sharing across every item
    in a plan rather than recomputed per item (the I3 whole-branch
    finding: 20 stages x 2 cameras produced 1680 ``storage.exists()``
    calls against one ``list()``):

    - :meth:`source_present` memoises ``MatchProject.source_present`` per
      distinct video path, so N artefacts deriving from the same source
      (a primary's trim, overlay and audit trim, say) HEAD it once.
    - :meth:`audio_all_sources_present` computes the ``AUDIO`` row's
      whole-project "every registered source survives" answer once,
      instead of rebuilding the video list and re-checking every source
      for every wav in the category.
    """

    def __init__(self, project: MatchProject, root: Path, audited: set[int]) -> None:
        self.project = project
        self.root = root
        self.audited = audited
        self._source_cache: dict[str, bool] = {}
        self._audio_all_present: bool | None = None

    def source_present(self, video_path: Path) -> bool:
        key = str(video_path)
        if key not in self._source_cache:
            self._source_cache[key] = self.project.source_present(self.root, video_path, durable=True)
        return self._source_cache[key]

    def audio_all_sources_present(self) -> bool:
        if self._audio_all_present is None:
            videos = [v for s in self.project.stages for v in s.videos]
            self._audio_all_present = bool(videos) and all(self.source_present(v.path) for v in videos)
        return self._audio_all_present


def _reconstructable(
    ctx: _PlanContext,
    category: CleanupCategory,
    filename: str,
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
            return bool(ctx.audited)
        return stage_number in ctx.audited

    # AUDIO: audio caches are stage-prefixed (``stage<N>_cam_<video_id>.wav``,
    # legacy ``stage<N>_primary.wav`` / ``stage<N>_audit.wav``), so
    # ``stage_number`` almost always parses -- but a wav can derive from any
    # registered video on that stage, including secondaries and legacy names
    # that carry no video id, and a filename alone cannot say which one wrote
    # it. Keying on just that stage's primary would call a wav reconstructable
    # while the video it actually came from is gone. Take the conservative
    # whole-project answer instead: this only ever moves an item *into*
    # "needs explicit opt-in", never out of it.
    if category is CleanupCategory.AUDIO:
        return ctx.audio_all_sources_present()

    # EXPORTS_TRIMS / EXPORTS_OVERLAYS / AUDIT_TRIMS: keyed on the specific
    # source *this* artefact derives from, not blindly on the stage's
    # primary (C1 whole-branch finding). Every per-camera artefact --
    # ``stage<N>_<slug>_cam_<video_id>_trimmed.mp4`` (export trim),
    # ``stage<N>_cam_<video_id>_trimmed.mp4`` (audit trim), and any future
    # per-camera overlay -- embeds its ``video_id`` in the name and must be
    # checked against that camera's own source: a secondary's trim survives
    # (or is lost) independently of whether the primary's source does.
    # Only a filename with no ``_cam_`` segment is the primary's own.
    if stage_number is None:
        return False
    stage = next((s for s in ctx.project.stages if s.stage_number == stage_number), None)
    if stage is None:
        return False
    cam_id = video_id_from_filename(filename)
    if cam_id is not None:
        video = stage.find_video_by_id(cam_id)
        if video is None:
            # Artefacts written before the take spec embed the path-only
            # ``legacy_video_id``, so the current-scheme lookup misses on
            # every one of them. Their source is present; only the id
            # scheme changed, and reporting them unrebuildable both hides
            # them from "select all" and states something untrue (#922).
            video = next((v for v in stage.videos if v.legacy_video_id == cam_id), None)
        if video is None:
            # Fail closed: a cam id we cannot resolve to a registered video
            # cannot be proven reconstructable.
            return False
        return ctx.source_present(video.path)
    primary = stage.primary()
    if primary is None:
        return False
    return ctx.source_present(primary.path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def plan_cleanup(
    project: MatchProject,
    root: Path,
    categories: Iterable[CleanupCategory],
    *,
    audit_stages: set[int] | None = None,
) -> CleanupPlan:
    """Build a :class:`CleanupPlan` for the given categories.

    Idempotent and read-only: never deletes, never mutates the project.
    Empty selection returns an empty plan. Categories whose target
    directory is missing contribute zero items but still appear in
    ``totals_by_category`` (with zeros) so the SPA can show the row
    without re-checking.

    ``audit_stages`` names the stages that still have an audit doc.
    Hosted callers must pass it -- their audit docs live in ``state_docs``
    rather than on this container's disk, so leaving it ``None`` there
    would mark every CSV and FCPXML unrebuildable. Mirrors
    ``MatchProject.export_overview``'s ``audit_docs`` parameter, which
    exists for the same reason.
    """
    requested: set[CleanupCategory] = set(categories)

    items: list[CleanupItem] = []
    totals: dict[CleanupCategory, CleanupTotals] = {c: CleanupTotals() for c in requested}

    listing = _storage_listing(project)
    audited = _audited_stages(project, root, audit_stages)
    ctx = _PlanContext(project, root, audited)

    for category in requested:
        # Keyed by path so a storage item can supersede a local mirror at
        # the same path (I2 whole-branch finding): right after an export
        # job, hosted commonly has both a durable object and the
        # container's local copy of it, and counting both doubled every
        # byte total the plan reports. The storage object is the durable
        # byte; ``apply_cleanup``'s storage branch already unlinks the
        # local mirror as a side effect of deleting it, so keeping a
        # separate local ``CleanupItem`` for the same path was never
        # buying an extra deletion, only an extra count.
        by_path: dict[Path, CleanupItem] = {}
        for path in _iter_paths(project, root, category):
            if not _safe_under_raw(project, root, path):
                # Should never happen with the current globs; guard kept
                # so a future bug can't escalate into deleting raw refs.
                continue
            try:
                size = path.lstat().st_size
            except OSError:
                continue
            by_path[path] = CleanupItem(
                path=path,
                size_bytes=size,
                category=category,
                reconstructable=_reconstructable(ctx, category, path.name),
            )
        if listing:
            for item in _iter_storage_items(project, root, category, listing, ctx):
                by_path[item.path] = item
        for item in by_path.values():
            items.append(item)
            t = totals[category]
            t.file_count += 1
            t.bytes += item.size_bytes

    items.sort(key=lambda it: (it.category.value, str(it.path), it.storage_key or ""))
    return CleanupPlan(
        items=items,
        totals_by_category=totals,
        total_bytes=sum(t.bytes for t in totals.values()),
        total_file_count=sum(t.file_count for t in totals.values()),
    )


def apply_cleanup(
    plan: CleanupPlan,
    *,
    root: Path | None = None,
    project: MatchProject | None = None,
) -> CleanupResult:
    """Delete every file in ``plan``; never raises on individual failures.

    Errors are recorded per-file in :attr:`CleanupResult.failed` so the
    caller can surface them. Already-missing files (e.g. concurrent
    delete by another process) are not failures: ``unlink(missing_ok=True)``
    silently succeeds. Bytes are tallied from the planned size, not
    re-stat'd post-delete.

    ``project`` supplies the bound storage. An item carrying a
    ``storage_key`` has its object deleted first -- that is the durable
    byte -- and then any local mirror is unlinked so the running container
    stops serving a copy it already pulled. The mirror is a cache and is
    not counted again in ``bytes_freed``.

    When ``root`` is given, appends one JSONL line to
    ``<root>/.cleanup.log`` summarising the run. Missing log directory
    is created. Logging is best-effort: a write failure does not
    invalidate an otherwise-successful cleanup. On hosted (``project``
    bound to storage), the log is written to storage instead -- see
    :func:`_append_storage_log`.
    """
    deleted: list[Path] = []
    failed: list[tuple[Path, str]] = []
    bytes_freed = 0

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

    result = CleanupResult(deleted=deleted, failed=failed, bytes_freed=bytes_freed)

    if project is not None and project._storage is not None and project._storage_scope is not None:
        try:
            _append_storage_log(project, plan, result)
        except Exception:  # noqa: BLE001 -- best effort, as the disk log is not load-bearing for
            # the cleanup result itself; a write failure here must never turn an
            # otherwise-successful cleanup into an error.
            pass
    elif root is not None:
        try:
            _append_log(root, plan, result)
        except OSError:
            pass

    return result


def _append_log(root: Path, plan: CleanupPlan, result: CleanupResult) -> None:
    """Append one JSONL summary line to ``<root>/.cleanup.log``.

    Schema is intentionally compact: the file is for human review, not
    rehydration. Bumping fields here is safe -- old lines stay valid.
    """
    log_path = root / CLEANUP_LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "categories": sorted({item.category.value for item in plan.items}),
        "deleted_count": len(result.deleted),
        "failed_count": len(result.failed),
        "bytes_freed": result.bytes_freed,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


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

    The read is only allowed to fail one way: a missing key, which is the
    genuine first-write case and reads as ``FileNotFoundError`` on both
    backends (``FilesystemStorage`` and ``S3Storage`` both normalise a
    missing object to that exception; see ``storage.py``). Any other
    exception -- permission denied, throttling, a network blip -- means we
    do not actually know what is in the object, so we must not write:
    treating that as "no log yet" would silently overwrite the entire
    accumulated audit trail with a single record. Skip the append and
    return; the caller already treats this whole call as best-effort.
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
    except FileNotFoundError:
        existing = b""
    storage.write_bytes(key, existing + (json.dumps(record) + "\n").encode("utf-8"))
