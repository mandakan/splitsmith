"""Cascade delete for a project/match and all the resources it owns.

The picker's old "forget" only dropped the ``recent_projects`` row, which
in hosted mode left an invisible-but-still-billing orphan: the ``matches``
row, every ``state_docs`` doc, the R2 storage prefix, and any enqueued
compute all survived with no UI path back to them. This module replaces
that with a real teardown.

The work lives here (not inline in ``server.py``) so it can be unit-tested
against a fake :class:`~splitsmith.storage.Storage` + sqlite stores without
spinning the FastAPI app.

**Modes diverge.** Hosted mode (``state.matches_store is not None``) cleans
DB rows + object storage + the in-memory registry. Local desktop mode has
no storage/matches/state stores, so a delete reduces to dropping the picker
row plus an opt-in ``rmtree`` of the on-disk match folder.

**Best-effort, not transactional.** S3 deletes can't join a DB transaction
and there's no atomic multi-object delete here, so every step is made
idempotent and wrapped so one failure is recorded in ``errors`` without
aborting the rest. The picker row is dropped *last* so a crash mid-cascade
leaves the entry visible to retry cleanly.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..match_model import MATCH_FILE

if TYPE_CHECKING:
    from .server import AppState

logger = logging.getLogger(__name__)


@dataclass
class DeletionSummary:
    """What the cascade actually removed, for the SPA to report back.

    Returned even on partial failure (with ``errors`` populated) so the
    UI can show "removed X, N errors" rather than a bare 500.
    """

    match_id: str | None
    recent_project_removed: bool = False
    match_row_removed: bool = False
    state_docs_removed: int = 0
    storage_objects_deleted: int = 0
    raw_uploads_deleted: list[str] = field(default_factory=list)
    raw_uploads_skipped_shared: list[str] = field(default_factory=list)
    jobs_cancelled: int = 0
    local_dir_removed: bool = False
    errors: list[str] = field(default_factory=list)


def _raw_paths_from_docs(docs: list[tuple[str, dict]]) -> set[str]:
    """Collect every ``raw_videos[].storage_path`` across project docs.

    Reads the raw dict shape rather than validating ``MatchProject`` so a
    schema migration or a malformed legacy doc can't crash a teardown.
    """
    paths: set[str] = set()
    for _slug, doc in docs:
        for rv in doc.get("raw_videos", []) or []:
            storage_path = rv.get("storage_path") if isinstance(rv, dict) else None
            if isinstance(storage_path, str) and storage_path:
                paths.add(storage_path)
    return paths


async def _safe_to_delete_raws(
    state: AppState, match_id: str, attached_raws: set[str]
) -> tuple[set[str], set[str]]:
    """Split this match's raws into (safe-to-delete, still-shared-elsewhere).

    A raw object is safe to delete only when no *other* match of the same
    user still references it (raws are shared by reference, not copied).
    O(matches x project-docs) reads -- a user's match count is bounded, so
    this is fine; revisit with a covering query if it ever isn't.
    """
    assert state.matches_store is not None and state.project_state is not None
    still_referenced: set[str] = set()
    for other in await state.matches_store.list():
        if other.match_id == match_id:
            continue
        other_docs = await state.project_state.list_project_docs(other.match_id)
        still_referenced |= _raw_paths_from_docs(other_docs)
    safe = attached_raws - still_referenced
    skipped = attached_raws & still_referenced
    return safe, skipped


def _delete_storage_prefix(state: AppState, prefix: str, summary: DeletionSummary) -> None:
    """List + delete every object under ``prefix``; record per-object errors.

    Materialises the listing first so we don't mutate a generator we're
    iterating (the filesystem backend walks via ``rglob``). ``prefix`` is
    normalised with a trailing slash so ``matches/<id>`` can't also sweep a
    sibling ``matches/<id>extra>``.
    """
    assert state.storage is not None
    normalised = prefix.rstrip("/") + "/"
    try:
        objects = list(state.storage.list(normalised))
    except Exception as exc:  # noqa: BLE001 -- best-effort teardown
        summary.errors.append(f"list storage {normalised!r}: {exc}")
        return
    for obj in objects:
        try:
            state.storage.delete(obj.path)
            summary.storage_objects_deleted += 1
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"delete {obj.path!r}: {exc}")


async def _delete_hosted(
    state: AppState,
    *,
    path: str,
    match_id: str | None,
    storage_prefix: str | None,
    delete_raw_uploads: bool,
    summary: DeletionSummary,
) -> None:
    # 1. Stop in-flight compute first so no worker re-writes storage/state
    #    under us. Coarse (cancels the user's other active jobs too) -- see
    #    PostgresJobBackend.cancel_active_for_user.
    try:
        summary.jobs_cancelled = await state.jobs.cancel_active_for_user()
    except Exception as exc:  # noqa: BLE001
        summary.errors.append(f"cancel jobs: {exc}")

    if match_id is None:
        # Older recent-projects rows predate match_id (record_open allows
        # None). Nothing in the stores/storage to key off -- degrade to a
        # picker-row drop and say so.
        summary.errors.append("no match_id on picker row; removed picker entry only")
        summary.recent_project_removed = await _drop_picker_row(state, path, summary)
        return

    # 2. Read project docs to harvest this match's attached raws.
    attached_raws: set[str] = set()
    if state.project_state is not None:
        try:
            docs = await state.project_state.list_project_docs(match_id)
            attached_raws = _raw_paths_from_docs(docs)
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"read project docs: {exc}")

    # 3. Compute the safe-to-delete raw set (only if opted in).
    safe_raws: set[str] = set()
    if delete_raw_uploads and attached_raws and state.matches_store is not None:
        try:
            safe_raws, skipped = await _safe_to_delete_raws(state, match_id, attached_raws)
            summary.raw_uploads_skipped_shared = sorted(skipped)
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"raw refcount: {exc}")

    # 4. Delete the match's own storage prefix.
    if state.storage is not None and storage_prefix:
        _delete_storage_prefix(state, storage_prefix, summary)

    # 5. Delete the safe raw objects.
    if state.storage is not None:
        for raw_path in sorted(safe_raws):
            try:
                state.storage.delete(raw_path)
                summary.raw_uploads_deleted.append(raw_path)
            except Exception as exc:  # noqa: BLE001
                summary.errors.append(f"delete raw {raw_path!r}: {exc}")

    # 6. Delete the match's state docs (match + project + audit, all kinds).
    if state.project_state is not None:
        try:
            summary.state_docs_removed = await state.project_state.delete_match(match_id)
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"delete state docs: {exc}")

    # 7. Delete the matches-registry row.
    if state.matches_store is not None:
        try:
            summary.match_row_removed = await state.matches_store.delete(match_id)
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"delete match row: {exc}")

    # 8. Drop the in-memory MatchRegistry cache entry, else a stale id keeps
    #    resolving to a now-deleted root.
    state.matches.forget(match_id)

    # 9. Drop the picker row (last, so a retry re-runs cleanly).
    summary.recent_project_removed = await _drop_picker_row(state, path, summary)


async def _delete_local(
    state: AppState,
    *,
    path: str,
    match_id: str | None,
    delete_local_files: bool,
    summary: DeletionSummary,
) -> None:
    if delete_local_files:
        _rmtree_match_dir(path, summary)
    if match_id:
        state.matches.forget(match_id)
    summary.recent_project_removed = await _drop_picker_row(state, path, summary)


def _rmtree_match_dir(path: str, summary: DeletionSummary) -> None:
    """Remove the on-disk match folder, guarded by the match.json marker.

    The marker check is the safety rail: it refuses to ``rmtree`` a
    directory that isn't actually a splitsmith match, so a malformed or
    empty ``path`` can't nuke an unrelated tree.
    """
    target = Path(path).expanduser().resolve()
    if not (target / MATCH_FILE).is_file():
        summary.errors.append(
            f"refusing to delete {str(target)!r}: no {MATCH_FILE} marker (not a match folder)"
        )
        return
    try:
        shutil.rmtree(target, ignore_errors=True)
        summary.local_dir_removed = not target.exists()
        if not summary.local_dir_removed:
            summary.errors.append(f"delete folder {str(target)!r}: some files could not be removed")
    except Exception as exc:  # noqa: BLE001
        summary.errors.append(f"delete folder {str(target)!r}: {exc}")


async def _drop_picker_row(state: AppState, path: str, summary: DeletionSummary) -> bool:
    try:
        return await state.recent_projects.remove(Path(path))
    except Exception as exc:  # noqa: BLE001
        summary.errors.append(f"remove picker row: {exc}")
        return False


async def delete_match_cascade(
    state: AppState,
    *,
    path: str,
    match_id: str | None,
    storage_prefix: str | None,
    delete_local_files: bool,
    delete_raw_uploads: bool,
) -> DeletionSummary:
    """Tear down a project/match and everything it owns. See module docstring.

    ``delete_local_files`` is honoured only in local mode; ``delete_raw_uploads``
    only in hosted mode. The server enforces these semantics regardless of
    which flags the client sent, so a wrong client-mode guess can't cause
    damage.
    """
    summary = DeletionSummary(match_id=match_id)
    if state.matches_store is not None:
        await _delete_hosted(
            state,
            path=path,
            match_id=match_id,
            storage_prefix=storage_prefix,
            delete_raw_uploads=delete_raw_uploads,
            summary=summary,
        )
    else:
        await _delete_local(
            state,
            path=path,
            match_id=match_id,
            delete_local_files=delete_local_files,
            summary=summary,
        )
    if summary.errors:
        logger.warning(
            "project delete for match_id=%s completed with %d error(s): %s",
            match_id,
            len(summary.errors),
            "; ".join(summary.errors),
        )
    return summary
