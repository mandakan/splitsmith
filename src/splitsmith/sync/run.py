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
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from ..coach import FIELD_STAGE_NOTE
from ..match_model import load_match_or_legacy
from ..match_project import PROJECT_FILE, MatchProject, atomic_write_json
from ..observability import PhaseTimer
from ..shot_id import ensure_shot_ids
from .auto_state import load_auto_prefs, update_auto_prefs
from .base import load_base_doc, save_base_doc
from .client import HostedSyncClient, SyncClientError, SyncMirrorGone, SyncVersionConflict
from .merge import MergeResult, merge_audit_doc, merge_project_doc
from .plan import AUDIT_FILENAME_RE, build_push_plan, doc_identity_key
from .pull import RemoteDoc, plan_pull, remote_doc_key
from .push import PushReport, run_push, timed_phase
from .state import STAGE_NOTE_SCHEMA, SyncState, load_sync_state, save_sync_state

_MAX_ATTEMPTS = 3

MIRROR_GONE_MESSAGE = (
    "this match is no longer on hosted (it was deleted there). Sync is off for it; "
    "use Publish again to upload it as a new copy"
)


class SyncReport(PushReport):
    """PushReport plus the pull/merge side of a bidirectional run."""

    pulled: int = 0
    merged: int = 0
    conflicts: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    reprocess_videos: int = 0
    attempts: int = 1
    shot_ids_migrated: int = 0
    #: Web renditions (#1031) transcoded from existing trims this run,
    #: before the push, so they went up with it.
    web_trims_cut: int = 0


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


def migrate_shot_ids(match_root: Path, *, audit_lock: AbstractContextManager | None = None) -> int:
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
            # Held per doc, like the pull's merge (#1075): a local audit
            # save landing between this read and the write below is lost.
            with audit_lock if audit_lock is not None else nullcontext():
                migrated += _stamp_one(audit_path)
    return migrated


def _stamp_one(audit_path: Path) -> int:
    """Stamp missing shot ids on one audit doc; 1 when it was rewritten."""
    try:
        doc = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(doc, dict):
        return 0
    shots = doc.get("shots")
    if not isinstance(shots, list):
        return 0
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
        return 1
    return 0


def backfill_web_trims(
    match_root: Path,
    *,
    ffmpeg_binary: str = "ffmpeg",
    on_progress: Callable[[float, str], None] = lambda p, m: None,
) -> tuple[int, list[str]]:
    """Cut the web rendition (#1031) for every trim under the match that
    lacks one, so the push that follows carries it. Sync is the moment a
    trim goes over the WAN, which is why the transcode lives here rather
    than in a separate verb: a match trimmed before the rendition existed
    is fixed by the next sync, with no re-trim from the source. Returns
    ``(cut, errors)``; an error is per trim and never aborts the sync.
    """
    from .. import trim as trim_module

    config = trim_module.web_trim_config()
    total_cut = 0
    errors: list[str] = []
    shooters_dir = match_root / "shooters"
    if not shooters_dir.is_dir():
        return 0, errors
    for shooter_root in sorted(p for p in shooters_dir.iterdir() if p.is_dir()):
        slug = shooter_root.name

        def _progress(i: int, n: int, trimmed: Path, slug: str = slug) -> None:
            on_progress(0.0, f"encoding web clip {i}/{n} for {slug}")

        cut, errs = trim_module.backfill_web_trims(
            shooter_root / "trimmed", config, ffmpeg_binary=ffmpeg_binary, on_progress=_progress
        )
        total_cut += len(cut)
        errors.extend(f"shooter {slug!r}: {e}" for e in errs)
    return total_cut, errors


def run_sync(
    match_root: Path,
    *,
    client: HostedSyncClient,
    on_progress: Callable[[float, str], None] = lambda p, m: None,
    timer: PhaseTimer | None = None,
    ffmpeg_binary: str = "ffmpeg",
    audit_lock: AbstractContextManager | None = None,
    full_media: bool | None = None,
) -> SyncReport:
    """Pull hosted changes, merge, then push - the bidirectional cycle.

    ``audit_lock`` is held around each audit doc's read-merge-write so a
    local audit PUT's compare-and-save (spec 2026-09-27 s5) cannot
    interleave with it; the server passes ``AppState.audit_lock``.
    """
    timings: dict[str, float] = {}

    with timed_phase(timings, timer, "preflight"):
        sync_state = load_sync_state(match_root)
        if full_media is None:
            full_media = load_auto_prefs(match_root).full_media
        preflight = build_push_plan(match_root, sync_state=sync_state, full_media=full_media)
        if preflight.errors:
            raise SyncClientError("\n".join(preflight.errors))
        match_id, match_name = preflight.match_id, preflight.match_name

    # Before anything writes, and above all before ensure_match: that call
    # adopts the mirror, so a sync of a match deleted on hosted would
    # quietly bring it back. Docs pushed before and none there now means
    # it was deleted; stop and say so.
    with timed_phase(timings, timer, "mirror_check"):
        if sync_state.doc_versions and not client.find_doc_manifest(match_id):
            update_auto_prefs(match_root, lambda p: setattr(p, "hosted_deleted_at", datetime.now(UTC)))
            raise SyncMirrorGone(MIRROR_GONE_MESSAGE)

    # After the preflight (a tree that can't push isn't touched) and
    # before the push plans: the renditions have to exist to be planned.
    with timed_phase(timings, timer, "web_trims"):
        web_trims_cut, web_trim_notes = backfill_web_trims(
            match_root, ffmpeg_binary=ffmpeg_binary, on_progress=on_progress
        )

    # After the preflight (a tree that can't push isn't rewritten) and
    # before the pull: the merge keys shot membership on the id, so every
    # local audit doc must carry one before the first remote doc arrives.
    with timed_phase(timings, timer, "migrate_shot_ids"):
        shot_ids_migrated = migrate_shot_ids(match_root, audit_lock=audit_lock)
        if shot_ids_migrated:
            on_progress(0.0, f"stamped shot ids on {shot_ids_migrated} audit doc(s)")

    with timed_phase(timings, timer, "ensure_match"):
        client.ensure_match(match_id, match_name)

    # After the mirror check, which reads doc_versions. In memory only until
    # the first pull has merged: from then on every pulled base is a
    # note-aware snapshot, and running this again would strip a note the
    # merge has seen (a later hosted clear would lose to it). A failure
    # before that pull leaves the file below the schema and the next run
    # repeats this, harmlessly.
    stage_note_migration = sync_state.schema_version < STAGE_NOTE_SCHEMA
    if stage_note_migration:
        _repull_audit_docs_for_stage_note(match_root, sync_state)

    pulled_total = 0
    all_conflicts: list[dict] = []
    all_notes: list[str] = list(web_trim_notes)
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
            result_counts = _apply_pull(match_root, match_id, sync_state, pulled, audit_lock=audit_lock)
            merged_docs += result_counts["merged"]
            all_conflicts.extend(result_counts["conflicts"])
            all_notes.extend(result_counts["notes"])
            reprocess.update(result_counts["reprocess"])
            if stage_note_migration:
                sync_state.schema_version = STAGE_NOTE_SCHEMA
            save_sync_state(match_root, sync_state)

        try:
            push_report = run_push(
                match_root,
                client=client,
                on_progress=on_progress,
                timer=timer,
                sync_state=sync_state,
                full_media=full_media,
            )
            break
        except SyncVersionConflict as exc:
            # A doc pushed before that the manifest no longer lists was
            # deleted on hosted; every retry would PUT the same stale
            # version into the same 409.
            listed = {doc_identity_key(e["doc_kind"], e.get("slug"), e.get("stage_number")) for e in manifest}
            if exc.key is not None and exc.key not in listed and sync_state.doc_versions.get(exc.key):
                raise SyncClientError(
                    f"{exc.key} was deleted on hosted after the last sync, so its local edits "
                    "cannot be pushed over it"
                ) from exc
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
        web_trims_cut=web_trims_cut,
    )
    report.timings.update(timings)
    return report


def _repull_audit_docs_for_stage_note(match_root: Path, sync_state: SyncState) -> None:
    """The one-time stage-note migration (#1376), for a sync state written
    by an install older than the field: every audit doc is pulled once more
    and its base stops claiming a note.

    That install could not have seen a stage note. Hosted may hold one it
    never merged: its push kept the note (``put_audit_doc``) and it
    recorded that version as seen, or it pulled the doc, recorded the
    remote (with the note) as base and kept its own doc without it. Either
    way ``plan_pull`` compares versions only, so the note would not reach
    this desktop until some other hosted write moved the doc, and a local
    note written meanwhile would overwrite it unseen. So: forget each audit
    version (the next pull fetches the doc once) and drop ``stage_note``
    from each base (the merge then adopts the hosted note, and a local note
    written before that pull meets it as a conflict, not as an edit of it).
    Idempotent until the first pull after it has merged, which is when
    ``run_sync`` marks it done; a failure before that runs it again."""
    for key in [k for k in sync_state.doc_versions if k.startswith("audit/")]:
        base = load_base_doc(match_root, key)
        if base is not None and FIELD_STAGE_NOTE in base:
            del base[FIELD_STAGE_NOTE]
            save_base_doc(match_root, key, base)
        del sync_state.doc_versions[key]


def _apply_pull(
    match_root: Path,
    match_id: str,
    sync_state: SyncState,
    pulled: list[tuple[RemoteDoc, dict, int]],
    *,
    audit_lock: AbstractContextManager | None = None,
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
    lock = audit_lock if audit_lock is not None else nullcontext()

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
        else:  # audit -- the only remaining kind; plan_pull filters on PULLABLE_DOC_KINDS
            with lock:
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
