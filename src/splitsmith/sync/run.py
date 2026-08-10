"""Bidirectional sync orchestration: pull -> merge -> push.

One ``run_sync`` call drives the whole cycle the slice spec defines:
preflight the local plan for push-blocking errors, adopt the mirror,
then up to three attempts of [pull changed docs -> three-way merge ->
apply locally -> push]. Only a lost optimistic-lock race
(:class:`SyncVersionConflict` - a hosted write landed mid-sync) retries;
every other error propagates. Base snapshots follow the spec's
invariant: base := pulled remote snapshot at apply time, base := pushed
body after each successful PUT, so a crash anywhere replays correctly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from ..match_model import load_match_or_legacy
from ..match_project import PROJECT_FILE, MatchProject, atomic_write_json
from ..observability import PhaseTimer
from .base import load_base_doc, save_base_doc
from .client import HostedSyncClient, SyncClientError, SyncVersionConflict
from .merge import MergeResult, merge_audit_doc, merge_project_doc
from .plan import build_push_plan
from .pull import RemoteDoc, plan_pull, remote_doc_key
from .push import PushReport, _timed_phase, run_push
from .state import SyncState, load_sync_state, save_sync_state

_MAX_ATTEMPTS = 3


class SyncReport(PushReport):
    """PushReport plus the pull/merge side of a bidirectional run."""

    pulled: int = 0
    merged: int = 0
    conflicts: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    reprocess_videos: int = 0
    attempts: int = 1


def format_sync_message(report: SyncReport) -> str:
    """One-line summary for the sync job's final progress message."""
    message = (
        f"Synced: {report.pulled} pulled, {report.uploaded} uploaded, "
        f"{report.skipped} skipped, {report.docs} docs"
    )
    if report.docs_skipped:
        message += f" ({report.docs_skipped} unchanged)"
    if report.conflicts:
        message += f"; {len(report.conflicts)} conflict(s) resolved - see job details"
    if report.reprocess_videos:
        message += f"; {report.reprocess_videos} video(s) need re-processing"
    return message


def _local_doc_ts(path: Path) -> datetime:
    """LWW tiebreak timestamp for a local doc: its file mtime. Uniform
    across doc kinds (audit docs carry no updated_at field of their own)."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return datetime.fromtimestamp(0, tz=UTC)


def run_sync(
    match_root: Path,
    *,
    client: HostedSyncClient,
    on_progress: Callable[[float, str], None] = lambda p, m: None,
    timer: PhaseTimer | None = None,
) -> SyncReport:
    """Pull hosted changes, merge, then push - the bidirectional cycle."""
    timings: dict[str, float] = {}

    with _timed_phase(timings, timer, "preflight"):
        sync_state = load_sync_state(match_root)
        preflight = build_push_plan(match_root, sync_state=sync_state)
        if preflight.errors:
            raise SyncClientError("\n".join(preflight.errors))
        match_id, match_name = preflight.match_id, preflight.match_name

    with _timed_phase(timings, timer, "ensure_match"):
        client.ensure_match(match_id, match_name)

    pulled_total = 0
    all_conflicts: list[dict] = []
    all_notes: list[str] = []
    reprocess: set[str] = set()
    merged_docs = 0

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        with _timed_phase(timings, timer, "pull"):
            on_progress(0.0, "checking hosted changes")
            manifest = client.get_doc_manifest(match_id)
            changed = plan_pull(manifest, sync_state)
            pulled = [(rd, *client.get_doc(match_id, rd.kind, rd.slug, rd.stage_number)) for rd in changed]
            pulled_total += len(pulled)

        with _timed_phase(timings, timer, "merge"):
            result_counts = _apply_pull(match_root, match_id, sync_state, pulled)
            merged_docs += result_counts["merged"]
            all_conflicts.extend(result_counts["conflicts"])
            all_notes.extend(result_counts["notes"])
            reprocess.update(result_counts["reprocess"])
            save_sync_state(match_root, sync_state)

        try:
            push_report = run_push(
                match_root,
                client=client,
                on_progress=on_progress,
                timer=timer,
                sync_state=sync_state,
            )
            break
        except SyncVersionConflict as exc:
            if attempt == _MAX_ATTEMPTS:
                raise SyncClientError(
                    f"sync could not converge after {_MAX_ATTEMPTS} attempts - a hosted "
                    f"write kept landing mid-sync ({exc})"
                ) from exc
            on_progress(0.0, "hosted changed during sync - retrying")

    report = SyncReport(
        **push_report.model_dump(),
        pulled=pulled_total,
        merged=merged_docs,
        conflicts=all_conflicts,
        notes=all_notes,
        reprocess_videos=len(reprocess),
        attempts=attempt,
    )
    report.timings.update(timings)
    return report


def _apply_pull(
    match_root: Path,
    match_id: str,
    sync_state: SyncState,
    pulled: list[tuple[RemoteDoc, dict, int]],
) -> dict:
    """Merge pulled docs into the local tree and update bases/versions.

    Order per doc: merge in memory -> atomic local write (only when the
    merge changed anything) -> base := remote snapshot -> record remote
    version. A crash after any doc leaves a consistent prefix: bases
    updated for exactly the docs whose merged form is on disk, so the
    next run sees merge results as plain local changes (spec invariant).
    """
    match, shooter_roots = load_match_or_legacy(match_root)
    conflicts: list[dict] = []
    notes: list[str] = []
    reprocess: set[str] = set()
    merged_count = 0

    for rd, remote_doc, version in pulled:
        key = remote_doc_key(rd)
        base = load_base_doc(match_root, key)

        if rd.kind == "match":
            # No whitelisted fields on the match doc: local always wins.
            if base is not None and remote_doc != base:
                notes.append(
                    f"{key}: remote changed the match doc; local wins "
                    "(no mobile surface writes it - investigate)"
                )
        elif rd.kind == "project":
            shooter_root = shooter_roots.get(rd.slug)
            if shooter_root is None:
                notes.append(f"{key}: no local shooter {rd.slug!r}; membership is desktop-owned; ignored")
            else:
                project = MatchProject.load(shooter_root)
                local_doc = project.model_dump(mode="json")
                result = merge_project_doc(
                    base,
                    local_doc,
                    remote_doc,
                    doc_key=key,
                    local_ts=_local_doc_ts(shooter_root / PROJECT_FILE),
                    remote_ts=rd.updated_at,
                )
                _collect(result, conflicts, notes, reprocess)
                if result.changed_vs_local:
                    merged_project = MatchProject.model_validate(result.doc)
                    merged_project.save(shooter_root)
                    merged_count += 1
        else:  # audit
            shooter_root = shooter_roots.get(rd.slug)
            audit_path = (
                None if shooter_root is None else shooter_root / "audit" / f"stage{rd.stage_number}.json"
            )
            if audit_path is None:
                notes.append(f"{key}: no local shooter {rd.slug!r}; ignored")
            elif not audit_path.exists():
                # A missing local audit file is not "start from nothing" -
                # audit doc membership (whether the file exists at all) is
                # desktop-owned. Merging into {} would let historical
                # events synthesize a zero-shot "audited" doc and push it
                # back over hosted's fuller copy. Skip like a missing
                # shooter; base/version still record below so this doc
                # is not re-pulled every sync.
                notes.append(
                    f"{key}: no local audit doc for stage {rd.stage_number} ({rd.slug!r}) - "
                    "audit doc membership is desktop-owned; ignored"
                )
            else:
                local_doc = json.loads(audit_path.read_text(encoding="utf-8"))
                result = merge_audit_doc(
                    base,
                    local_doc,
                    remote_doc,
                    doc_key=key,
                    local_ts=_local_doc_ts(audit_path),
                    remote_ts=rd.updated_at,
                )
                _collect(result, conflicts, notes, reprocess)
                if result.changed_vs_local:
                    audit_path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(audit_path, result.doc)
                    merged_count += 1

        save_base_doc(match_root, key, remote_doc)
        sync_state.doc_versions[key] = version

    return {
        "merged": merged_count,
        "conflicts": conflicts,
        "notes": notes,
        "reprocess": reprocess,
    }


def _collect(result: MergeResult, conflicts: list[dict], notes: list[str], reprocess: set[str]) -> None:
    conflicts.extend(asdict(c) for c in result.conflicts)
    notes.extend(result.notes)
    reprocess.update(result.reprocess_video_ids)
