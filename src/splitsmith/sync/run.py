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
import os
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from ..match_model import load_match_or_legacy
from ..match_project import PROJECT_FILE, MatchProject, atomic_write_json
from ..observability import PhaseTimer
from ..shot_id import ensure_shot_ids
from .base import load_base_doc, save_base_doc
from .client import HostedSyncClient, SyncClientError, SyncVersionConflict
from .merge import MergeResult, merge_audit_doc, merge_project_doc
from .plan import AUDIT_FILENAME_RE, build_push_plan
from .pull import RemoteDoc, plan_pull, remote_doc_key
from .push import PushReport, run_push, timed_phase
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
    shot_ids_migrated: int = 0


def format_sync_message(report: SyncReport) -> str:
    """One-line summary for the sync job's final progress message.

    ``notes`` is counted here for the same reason ``conflicts`` is: it is
    where the merge states a refusal. The unstamped-shot gate's whole
    guarantee is "a stated refusal, not a silent duplicate"
    (``shot_id.ensure_shot_ids``), and a refusal nobody sees is a silent
    one -- ``handle.set_result(...)`` carries the full list, but this
    message is what the jobs panel shows without expanding it.
    """
    message = (
        f"Synced: {report.pulled} pulled, {report.uploaded} uploaded, "
        f"{report.skipped} skipped, {report.docs} docs"
    )
    if report.docs_skipped:
        message += f" ({report.docs_skipped} unchanged)"
    if report.conflicts:
        message += f"; {len(report.conflicts)} conflict(s) resolved - see job details"
    if report.notes:
        message += f"; {len(report.notes)} note(s) - see job details"
    if report.reprocess_videos:
        message += f"; {report.reprocess_videos} video(s) need re-processing"
    if report.shot_ids_migrated:
        message += f"; {report.shot_ids_migrated} audit doc(s) stamped with shot ids"
    return message


def _local_doc_ts(path: Path) -> datetime:
    """LWW tiebreak timestamp for a local doc: its file mtime. Uniform
    across doc kinds (audit docs carry no updated_at field of their own)."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return datetime.fromtimestamp(0, tz=UTC)


def migrate_shot_ids(match_root: Path) -> int:
    """Stamp shot ids across every local audit doc that lacks them.

    The desktop is the sole minter of shot ids for a mirror (#631 Task 7),
    so a legacy document -- one written before shots carried an ``id`` --
    has to be stamped *here*, on the desktop, before the pull. Otherwise
    nothing stamps it: the hosted save boundary no longer mints for a
    mirror, and the merge's unstamped-shot gate would refuse the whole shot
    section on every sync, so a phone's edits to that stage would never be
    adopted.

    Returns the number of documents actually rewritten, which is why the
    caller can report it: a second run finds nothing missing, stamps
    nothing, writes nothing, and returns 0. Unparseable or oddly-shaped
    documents are left alone -- ``build_push_plan``'s preflight is what
    reports those, and it has already run. An unwritable audit directory
    (e.g. a read-only filesystem) raises :class:`SyncClientError` instead
    of letting the underlying ``OSError`` escape -- the migration is
    idempotent and self-healing, so this is a presentation concern, not
    data loss: fixing the permission and re-running finds the same
    documents still missing their ids and stamps them then. ``run_sync``'s
    caller already turns ``SyncClientError`` into the same curated message
    every other sync failure gets, so a bare traceback never reaches the
    jobs panel.
    """
    _, shooter_roots = load_match_or_legacy(match_root)
    migrated = 0
    for shooter_root in shooter_roots.values():
        audit_dir = shooter_root / "audit"
        if not audit_dir.is_dir():
            continue
        for audit_path in sorted(audit_dir.iterdir()):
            if not AUDIT_FILENAME_RE.match(audit_path.name):
                continue
            try:
                doc = json.loads(audit_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(doc, dict):
                continue
            shots = doc.get("shots")
            if not isinstance(shots, list):
                continue
            if ensure_shot_ids([s for s in shots if isinstance(s, dict)]):
                stamps = audit_path.stat()
                try:
                    atomic_write_json(audit_path, doc)
                except OSError as exc:
                    raise SyncClientError(
                        f"could not stamp shot ids on {audit_path} - check that the "
                        f"match directory is writable ({exc})"
                    ) from exc
                # Restore the original mtime. ``_local_doc_ts`` reads file
                # mtime as the merge's LWW tiebreak, so a doc this pass just
                # stamped would otherwise look freshly edited and beat a
                # genuinely newer phone edit in every true conflict on the
                # first sync after upgrade. Pushes are content-hashed
                # (``sync/plan.py``), not mtime-based, so restoring it does
                # not suppress the push of the ids we just wrote.
                os.utime(audit_path, ns=(stamps.st_atime_ns, stamps.st_mtime_ns))
                migrated += 1
    return migrated


def run_sync(
    match_root: Path,
    *,
    client: HostedSyncClient,
    on_progress: Callable[[float, str], None] = lambda p, m: None,
    timer: PhaseTimer | None = None,
) -> SyncReport:
    """Pull hosted changes, merge, then push - the bidirectional cycle."""
    timings: dict[str, float] = {}

    with timed_phase(timings, timer, "preflight"):
        sync_state = load_sync_state(match_root)
        preflight = build_push_plan(match_root, sync_state=sync_state)
        if preflight.errors:
            raise SyncClientError("\n".join(preflight.errors))
        match_id, match_name = preflight.match_id, preflight.match_name

    # After the preflight (a tree that can't push isn't rewritten) and
    # before the pull: the merge keys shot membership on the id, so every
    # local audit doc must carry one before the first remote doc arrives.
    with timed_phase(timings, timer, "migrate_shot_ids"):
        shot_ids_migrated = migrate_shot_ids(match_root)
        if shot_ids_migrated:
            on_progress(0.0, f"stamped shot ids on {shot_ids_migrated} audit doc(s)")

    with timed_phase(timings, timer, "ensure_match"):
        client.ensure_match(match_id, match_name)

    pulled_total = 0
    all_conflicts: list[dict] = []
    all_notes: list[str] = []
    reprocess: set[str] = set()
    merged_docs = 0

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        with timed_phase(timings, timer, "pull"):
            on_progress(0.0, "checking hosted changes")
            manifest = client.get_doc_manifest(match_id)
            changed = plan_pull(manifest, sync_state)
            pulled = [(rd, *client.get_doc(match_id, rd.kind, rd.slug, rd.stage_number)) for rd in changed]
            pulled_total += len(pulled)

        with timed_phase(timings, timer, "merge"):
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
        shot_ids_migrated=shot_ids_migrated,
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
                if not remote_doc.get("shots") and not remote_doc.get("audit_events"):
                    # Metadata-only doc (e.g. a phone triage flag set on a
                    # stage desktop never audited) - materialize it as the
                    # local file instead of skipping. The risk the skip
                    # below guards against (historical events synthesizing
                    # a zero-shot "audited" doc) needs audit_events to draw
                    # on; there are none here, so there is nothing to
                    # synthesize and the doc is safe to write verbatim.
                    # base/version record below so the flag isn't lost to
                    # the next push and isn't re-pulled every sync.
                    audit_path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(audit_path, remote_doc)
                    merged_count += 1
                else:
                    # A missing local audit file is not "start from
                    # nothing" - audit doc membership (whether the file
                    # exists at all) is desktop-owned for docs carrying
                    # real shots/audit_events. Merging into {} would let
                    # historical events synthesize a zero-shot "audited"
                    # doc and push it back over hosted's fuller copy. Skip
                    # like a missing shooter; base/version still record
                    # below so this doc is not re-pulled every sync.
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
