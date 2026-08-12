"""Pure three-way merge for the bidirectional sync slice.

Desktop is authoritative for everything except the narrow whitelist
mobile is allowed to write (spec 2026-08-10-bidirectional-sync-design):
per-video beep field-groups in project docs, per-shot coach fields and
the append-only ``audit_events`` log in audit docs. Each merge starts
from a deep copy of the local doc and resolves whitelisted units
three-way against the base snapshot: changed on one side wins outright;
changed on both is a true conflict resolved last-writer-wins by doc
timestamp and always surfaced on :attr:`MergeResult.conflicts` - never
silent. Structural membership of stages and videos is
desktop-authoritative: remote-only additions/removals are noted, not
merged. Shots are the exception -- they carry a stable ``id``
(``splitsmith.shot_id``) and their membership resolves from the
append-only marker events *corroborated by the other side's shot list*,
so a phone can add, move and remove shots. The corroboration is not
belt-and-braces: ``audit_events`` is a session journal that is never
pruned, so on its own it deletes shots its own writer still holds and
resurrects shots a re-detection superseded -- see ``_merge_shot_section``.
Only a shot that *arrived* carrying a persisted string ``id`` is
mergeable: an id minted inside the merge is not convergent across sides,
because ``derive_shot_id`` keys a candidate-less shot off its rounded
time and a nudge therefore changes it. If either side carries an
unstamped shot the whole shot section is skipped with a note. That gate
narrows the divergence class; the desktop being the sole minter of a
*non-convergent* id for a mirror (``_may_mint_shot_ids`` in ui/server.py,
``migrate_shot_ids`` in sync/run.py, #631 Task 7, shipped) is what closes
it. A hosted save boundary still derives the convergent ``cand-<n>`` for a
detected shot on a mirror -- both sides compute the same id from the same
``candidate_number``, so there is no second-minter risk there (#631 Task 6
fix round 1) -- it only declines to mint the two non-convergent branches
(the time-keyed manual id and the no-key uuid4). So the only way this
gate still fires is a genuinely stale document -- one predating both
sides' stamping, or one holding a legacy candidate-less shot neither side
has migrated yet.

``merge_audit_doc`` is consequently not a deterministic function of its
inputs: ``ensure_shot_ids`` mints a uuid4 for a shot that can derive no
id, so two runs over identical inputs can differ in those ids. Still no
I/O -- callers own loading, timestamps, and writes.

Taking a remote beep group applies the same derivation invalidation a
local beep override does (``_apply_beep_override`` in ui/server.py) -
but only when beep_time itself changed: trim and (for primaries)
shot_detect flags drop, and the video lands on
:attr:`MergeResult.reprocess_video_ids` so the sync report can say "N
videos need re-processing". A confirm-only remote write (beep_reviewed
flips with beep_time unchanged) still replaces the whole group
atomically but does not invalidate derived work (#821). Hosted never
re-derives for mirrors - raw media never leaves the desktop - so this
is where re-derivation gets scheduled.

No I/O in this module; callers own loading, timestamps, and writes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..coach import COACH_FIELDS
from ..shot_id import ensure_shot_ids

#: Every key on a video dict starting with this prefix moves as one
#: atomic merge unit - beep_time without its confidence/candidates
#: would be incoherent. Prefix rule, not a field list, so a future
#: beep_* field never silently splits the group.
_BEEP_PREFIX = "beep_"


@dataclass(frozen=True)
class MergeConflict:
    """One true conflict: both sides changed the same unit since base."""

    doc_key: str
    unit: str
    winner: str  # "local" | "remote"


@dataclass
class MergeResult:
    """Outcome of merging one doc."""

    doc: dict
    conflicts: list[MergeConflict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reprocess_video_ids: list[str] = field(default_factory=list)
    changed_vs_local: bool = False


def _resolve_unit(
    base_u: object, local_u: object, remote_u: object, *, local_ts: datetime, remote_ts: datetime
) -> tuple[str, bool]:
    """Three-way verdict for one unit: (winner, is_conflict).

    An empty/missing base makes both sides look changed - correct, just
    less discriminating (spec: missing base = never synced).
    """
    local_changed = local_u != base_u
    remote_changed = remote_u != base_u
    if not remote_changed:
        return "local", False
    if not local_changed:
        return "remote", False
    if local_u == remote_u:
        return "local", False
    return ("remote" if remote_ts > local_ts else "local"), True


def _beep_group(video: dict) -> dict:
    return {k: v for k, v in video.items() if k.startswith(_BEEP_PREFIX)}


def _videos_by_id(doc: dict | None) -> dict[tuple[int, str], dict]:
    out: dict[tuple[int, str], dict] = {}
    for stage in (doc or {}).get("stages") or []:
        if not isinstance(stage, dict):
            continue
        for video in stage.get("videos") or []:
            if isinstance(video, dict) and video.get("video_id"):
                out[(stage.get("stage_number"), video["video_id"])] = video
    return out


def merge_project_doc(
    base: dict | None,
    local: dict,
    remote: dict,
    *,
    doc_key: str,
    local_ts: datetime,
    remote_ts: datetime,
) -> MergeResult:
    """Merge one shooter's project doc (beep groups per video)."""
    merged = copy.deepcopy(local)
    result = MergeResult(doc=merged)

    base_videos = _videos_by_id(base)
    remote_videos = _videos_by_id(remote)
    merged_videos = _videos_by_id(merged)

    for key, merged_video in merged_videos.items():
        stage_number, video_id = key
        remote_video = remote_videos.get(key)
        if remote_video is None:
            continue  # remote lacks it; local membership is authoritative
        base_u = _beep_group(base_videos.get(key, {}))
        local_u = _beep_group(merged_video)
        remote_u = _beep_group(remote_video)
        winner, is_conflict = _resolve_unit(base_u, local_u, remote_u, local_ts=local_ts, remote_ts=remote_ts)
        unit_name = f"stage {stage_number} video {video_id} beep"
        if is_conflict:
            result.conflicts.append(MergeConflict(doc_key=doc_key, unit=unit_name, winner=winner))
        if winner == "remote" and remote_u != local_u:
            # Only beep_time feeds the trim/shot-detect derivation chain.
            # A confirm-only change (beep_reviewed, beep_source, candidate
            # metadata) must merge without re-queueing work whose inputs
            # did not change (#821).
            derivation_changed = remote_u.get("beep_time") != local_u.get("beep_time")
            for k in list(merged_video):
                if k.startswith(_BEEP_PREFIX):
                    del merged_video[k]
            merged_video.update(copy.deepcopy(remote_u))
            processed = merged_video.setdefault("processed", {})
            processed["beep"] = remote_u.get("beep_time") is not None
            if derivation_changed:
                processed["trim"] = False
                if merged_video.get("role") == "primary":
                    processed["shot_detect"] = False
                result.reprocess_video_ids.append(video_id)

    for key in remote_videos.keys() - merged_videos.keys():
        result.notes.append(
            f"{doc_key}: remote has video {key[1]} in stage {key[0]} that local lacks - "
            "video membership is desktop-owned; ignored"
        )
    _note_non_whitelisted_remote_changes(result, base, merged, remote, doc_key)
    result.changed_vs_local = merged != local
    return result


def _event_key(event: dict) -> object:
    """Union identity for one audit event: its id, else (ts, kind) for
    legacy events written before ids existed."""
    return event.get("id") or (event.get("ts"), event.get("kind"))


def _as_number(value: object) -> float | None:
    """A shot ``time`` / ``beep_time`` as a float, or None if it is not one.

    Audit documents are hand-editable JSON, so a ``time`` can be a string or
    null. The old positional merge never looked at the value; the rebuild
    sorts and subtracts, and both raise on junk.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _unstamped_shot_count(doc: dict | None) -> int:
    """How many of a doc's shots arrived without a persisted string id.

    The gate on the whole shot merge. Non-dict entries are not shots and do
    not count -- they are carried through untouched either way.
    """
    count = 0
    for shot in (doc or {}).get("shots") or []:
        if not isinstance(shot, dict):
            continue
        shot_id = shot.get("id")
        if not (isinstance(shot_id, str) and shot_id):
            count += 1
    return count


def _shots_by_id(doc: dict | None) -> tuple[dict[str, dict], dict[str, int]]:
    """Index a doc's shots by their stable id, plus a count of any collisions.

    ``shot_number`` is positional and renumbers on every insert, so it
    cannot key a merge; ``splitsmith.shot_id`` stamps ``id`` at the save
    boundary. Shots without one predate that and are skipped -- they cannot
    be matched across sides.

    Two shots sharing one persisted id means the source document is
    malformed: ``ensure_shot_ids`` never mints a colliding id, falling back
    to a uuid4 instead. The first occurrence wins and the rest are counted
    rather than silently overwritten, so the caller can say so - this
    module's contract is that nothing is dropped quietly.
    """
    out: dict[str, dict] = {}
    duplicates: dict[str, int] = {}
    for shot in (doc or {}).get("shots") or []:
        if not (isinstance(shot, dict) and isinstance(shot.get("id"), str) and shot["id"]):
            continue
        shot_id = shot["id"]
        if shot_id in out:
            duplicates[shot_id] = duplicates.get(shot_id, 0) + 1
            continue
        out[shot_id] = shot
    return out, duplicates


#: Membership is expressed in the event vocabulary the desktop audit screen
#: already writes -- no new kinds. A shot is present after the newest of
#: these events mentioning its id, and shots with no membership event at all
#: are original detector output.
_MEMBERSHIP_PRESENT = frozenset({"marker_added_manual", "marker_kept"})
_MEMBERSHIP_ABSENT = frozenset({"marker_rejected", "marker_deleted"})


def _membership_verdicts(events: list) -> dict[str, bool]:
    """Latest present/absent verdict per shot id, by event timestamp.

    Ordered by ``ts``, not list position: the event union concatenates two
    histories, so the list order after a merge is not chronological.

    ``ts`` is client-authored -- the SPA stamps it from the browser's clock
    when the event is recorded, and nothing on the way in re-stamps or
    validates it. A skewed clock on either side therefore influences which
    of two competing verdicts for one shot wins, and a badly skewed one can
    make an older verdict outrank a newer one outright. That is why a
    verdict is only allowed to *act* when the other side's document
    corroborates it (see ``_merge_shot_section``): the ordering is a
    heuristic, the corroboration is not.
    """
    latest: dict[str, tuple[str, bool]] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        if not isinstance(kind, str):
            continue
        if kind in _MEMBERSHIP_PRESENT:
            present = True
        elif kind in _MEMBERSHIP_ABSENT:
            present = False
        else:
            continue
        payload = event.get("payload")
        shot_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(shot_id, str) or not shot_id:
            continue
        ts = str(event.get("ts") or "")
        previous = latest.get(shot_id)
        # When two events for the same shot share identical ts, the later entry
        # in the sorted list wins; merge_audit_doc stable-sorts local_events first,
        # so >= implicitly favors remote on tie.
        if previous is None or ts >= previous[0]:
            latest[shot_id] = (ts, present)
    return {shot_id: present for shot_id, (_, present) in latest.items()}


def _membership_event_ids(events: object) -> set[str]:
    """Shot ids one document's own event log mentions in a membership event.

    Not a verdict -- just "this log has an opinion about this shot at all".
    ``_merge_shot_section`` uses it as one of the three ways a side can
    corroborate that it knows a shot exists.
    """
    out: set[str] = set()
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        if not isinstance(kind, str) or (kind not in _MEMBERSHIP_PRESENT and kind not in _MEMBERSHIP_ABSENT):
            continue
        payload = event.get("payload")
        shot_id = payload.get("id") if isinstance(payload, dict) else None
        if isinstance(shot_id, str) and shot_id:
            out.add(shot_id)
    return out


def _coach_unit(shot: dict) -> dict:
    return {k: shot.get(k) for k in COACH_FIELDS}


def merge_audit_doc(
    base: dict | None,
    local: dict,
    remote: dict,
    *,
    doc_key: str,
    local_ts: datetime,
    remote_ts: datetime,
) -> MergeResult:
    """Merge one stage's audit doc (event union + coach fields per shot)."""
    merged = copy.deepcopy(local)
    result = MergeResult(doc=merged)

    # Append-only event union by id, ordered by ts (stable for ties).
    # Only rewrite the list when remote actually adds events - re-sorting
    # a legacy out-of-ts-order local list on its own would churn the doc
    # (and trigger a push) with no remote change to justify it.
    local_events = [e for e in merged.get("audit_events") or [] if isinstance(e, dict)]
    seen = {_event_key(e) for e in local_events}
    remote_new = [
        e for e in (remote.get("audit_events") or []) if isinstance(e, dict) and _event_key(e) not in seen
    ]
    if remote_new:
        merged["audit_events"] = sorted(
            local_events + copy.deepcopy(remote_new), key=lambda e: str(e.get("ts") or "")
        )

    merged_events = merged.get("audit_events") or []
    verdicts = _membership_verdicts(merged_events)

    # Stamp ids on both sides so the document stops being legacy going
    # forward -- but count what was missing FIRST. An id minted here is not
    # convergent across sides: derive_shot_id keys a candidate-less shot off
    # its rounded time, so a nudge changes it, and a nudge is exactly what
    # this merge exists to reconcile. Only a shot that arrived carrying a
    # persisted string id can be matched.
    local_unstamped = _unstamped_shot_count(merged)
    remote_unstamped = _unstamped_shot_count(remote)
    remote_for_merge = copy.deepcopy(remote)
    for doc in (merged, remote_for_merge):
        shots_list = doc.get("shots")
        if isinstance(shots_list, list):
            ensure_shot_ids([s for s in shots_list if isinstance(s, dict)])

    if local_unstamped or remote_unstamped:
        # Refuse the whole section, not just the unstamped shots: a document
        # this old cannot be trusted to key any of its shots. Local's shots
        # stand exactly as they are, so this merge cannot duplicate one of
        # them or drop one of them.
        #
        # That is a guarantee about the local document, not about the user's
        # data. Remote's shots are discarded wholesale here, so a shot the
        # phone added is not adopted, and the desktop's next push overwrites
        # it.
        #
        # The gate only sees shots still unstamped when the merge runs, and it
        # cannot detect that the two sides stamped independently beforehand --
        # which is exactly what would produce the divergence if both sides
        # could still mint a *non-convergent* id. That class is closed now
        # (#631 Task 7, shipped): the desktop is the sole minter of that kind
        # of id for a mirror -- ``migrate_shot_ids`` (sync/run.py) stamps
        # every local legacy document before the pull, and the hosted save
        # boundary (``_may_mint_shot_ids`` in ui/server.py, via
        # ``shot_id.ensure_shot_ids``'s ``mint`` argument) refuses to mint
        # the time-keyed or uuid4 branches on a mirror -- though it still
        # derives the convergent ``cand-<n>`` for a detected shot there
        # (#631 Task 6 fix round 1), which is safe because both sides
        # compute the same id from the same ``candidate_number``. A hosted
        # PUT of a legacy *manual* shot is therefore stored unstamped rather
        # than minting its own diverging id, so the pair described above --
        # a desktop nudge stamping manual-t6520 against a phone accept
        # stamping manual-t6500 for the same shot -- can no longer happen
        # through the normal save boundaries. This gate remains as a
        # backstop for a document that predates the migration and reaches
        # merge before either side has run it.
        result.notes.append(
            f"{doc_key}: {local_unstamped} local and {remote_unstamped} remote shot(s) "
            "arrived without a persisted id, so the shot section was not merged; "
            "local shots stand"
        )
    else:
        _merge_shot_section(
            result,
            base,
            merged,
            remote_for_merge,
            doc_key,
            verdicts=verdicts,
            local_ts=local_ts,
            remote_ts=remote_ts,
        )

    # needs_attention: doc-level LWW unit (triage slice 4). Change
    # detection and equality compare a CONTENT projection - flagged and
    # note only - so two writers who converge on the same flag state
    # don't log a conflict just because flagged_at/updated_at differ;
    # those are stamps, same class as the MatchProject.updated_at noise
    # this same commit exempts below. The LWW tie-break still reads
    # updated_at off the raw (unprojected) objects, and the winning
    # side's full object - all four keys - is what lands in the merged
    # doc. Not routed through _resolve_unit: its equal-unit branch
    # always picks "local", but here the raw objects can differ by
    # stamp alone even when content converges, so that case needs its
    # own newer-stamp tie-break instead.
    def _na_content(value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {"flagged": value.get("flagged"), "note": value.get("note")}

    def _na_ts(value: object) -> datetime:
        # Mirrors the audit-event union's ``str(e.get("ts") or "")``
        # fallback idiom above (missing/malformed sorts first) but as a
        # tz-aware datetime - a naive datetime.min would raise TypeError
        # when compared against an aware stamp.
        raw = value.get("updated_at") if isinstance(value, dict) else None
        if isinstance(raw, str) and raw:
            try:
                dt = datetime.fromisoformat(raw)
            except ValueError:
                pass
            else:
                # A stamp with no UTC offset parses naive; comparing it
                # against an aware stamp (below, or the other side) raises
                # TypeError. Treat naive as UTC rather than crash the pull.
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
        return datetime.min.replace(tzinfo=UTC)

    base_na = (base or {}).get("needs_attention")
    local_na = local.get("needs_attention")
    remote_na = remote.get("needs_attention")
    na_base_content = _na_content(base_na)
    na_local_content = _na_content(local_na)
    na_remote_content = _na_content(remote_na)
    na_local_changed = na_local_content != na_base_content
    na_remote_changed = na_remote_content != na_base_content
    if not na_remote_changed:
        na_winner, na_conflict = "local", False
    elif not na_local_changed:
        na_winner, na_conflict = "remote", False
    else:
        na_winner = "remote" if _na_ts(remote_na) > _na_ts(local_na) else "local"
        na_conflict = na_local_content != na_remote_content
    if na_conflict:
        result.conflicts.append(MergeConflict(doc_key=doc_key, unit="needs_attention", winner=na_winner))
    if na_winner == "remote" and remote_na != local_na:
        if remote_na is not None:
            merged["needs_attention"] = copy.deepcopy(remote_na)
        else:
            merged.pop("needs_attention", None)

    # Same shape as the project merge's tripwire: remote's copy differs from
    # base on a field this merge does not carry, so local's value stands.
    #
    # This is NOT "should be impossible" any more. The audit PUT is
    # whitelisted for a mirror now, and ``buildAuditJson`` (audit-doc.ts)
    # round-trips only shot_number/candidate_number/time/ms_after_beep/
    # source/note/id -- it drops the detector's ensemble_votes,
    # apriori_boost, ensemble_score and confidence, none of which are in
    # ``merged_keys``. A plain, correct phone save of a detector-seeded
    # stage therefore lands here every time. The fields stay desktop-owned
    # (widening merged_keys would let a phone save erase them for real), so
    # the note stays -- but it says which shots and which fields, and makes
    # no claim about a gate.
    def _strip_audit(doc: dict | None) -> dict:
        """Project a doc down to the fields that are still desktop-owned.

        Shots are compared by id, and only for ids present on both sides:
        membership itself is now merged, so a legitimate add or delete must
        not read as a non-whitelisted change.
        """
        clone = copy.deepcopy(doc or {})
        clone.pop("audit_events", None)
        clone.pop("needs_attention", None)
        clone.pop("shots", None)
        return clone

    def _shot_residue(doc: dict | None) -> dict[str, dict]:
        merged_keys = {"id", "time", "ms_after_beep", "shot_number", *COACH_FIELDS}
        index, _ = _shots_by_id(doc)
        return {
            shot_id: {k: v for k, v in shot.items() if k not in merged_keys}
            for shot_id, shot in index.items()
        }

    def _differing_keys(left: dict, right: dict) -> list[str]:
        return sorted(k for k in left.keys() | right.keys() if left.get(k) != right.get(k))

    if base is not None:
        base_residue, remote_residue = _shot_residue(base), _shot_residue(remote)
        details: list[str] = []
        stripped_base, stripped_remote = _strip_audit(base), _strip_audit(remote)
        doc_fields = _differing_keys(stripped_base, stripped_remote)
        if doc_fields:
            details.append(f"document: {', '.join(doc_fields)}")
        for shot_id in sorted(base_residue.keys() & remote_residue.keys()):
            fields = _differing_keys(base_residue[shot_id], remote_residue[shot_id])
            if fields:
                details.append(f"shot {shot_id}: {', '.join(fields)}")
        if details:
            result.notes.append(
                f"{doc_key}: remote differs from base on non-whitelisted audit fields, "
                f"which this merge does not carry, so local's values stand - "
                f"{'; '.join(details)}. A mobile save round-trips only the merged "
                f"fields, so a detector field it never carried reads as a change here."
            )

    result.changed_vs_local = merged != local
    return result


def _merge_shot_section(
    result: MergeResult,
    base: dict | None,
    merged: dict,
    remote_for_merge: dict,
    doc_key: str,
    *,
    verdicts: dict[str, bool],
    local_ts: datetime,
    remote_ts: datetime,
) -> None:
    """Merge membership, time and coach fields for one audit doc's shots.

    Only called when every shot on both sides arrived with a persisted id -
    see the gate in :func:`merge_audit_doc`. Mutates ``result`` and
    ``merged`` in place, the same shape as
    :func:`_note_non_whitelisted_remote_changes`.
    """

    # A shot with neither candidate_number nor time got a minted,
    # non-convergent id, so the two sides disagree about it and keying it
    # would duplicate it on every merge. Hold those aside: local's copies
    # win untouched. See Audit.tsx:2829 for where the shape comes from.
    def _has_identity(shot: dict) -> bool:
        return shot.get("candidate_number") is not None or shot.get("time") is not None

    merged_shots_raw = merged.get("shots")
    merged_shots_list = merged_shots_raw if isinstance(merged_shots_raw, list) else []
    if merged_shots_raw is not None and not isinstance(merged_shots_raw, list):
        result.notes.append(
            f"{doc_key}: local shots is a {type(merged_shots_raw).__name__}, not a list, so it "
            "holds no shots to merge; replaced with the merged list - the document is malformed"
        )

    # Entries that are not dicts at all are not shots and cannot be merged -
    # but they are not ours to discard either, and the rebuild below would
    # swallow them. Carry them through at their original index.
    foreign = [(position, e) for position, e in enumerate(merged_shots_list) if not isinstance(e, dict)]

    def _keyable(doc: dict | None) -> tuple[dict[str, dict], dict[str, int]]:
        index, duplicates = _shots_by_id(doc)
        return {shot_id: shot for shot_id, shot in index.items() if _has_identity(shot)}, duplicates

    base_shots, _ = _keyable(base)
    local_shots, local_duplicates = _keyable(merged)
    remote_shots, remote_duplicates = _keyable(remote_for_merge)

    # Deduplicated by id, first wins, and seeded from the keyable local index
    # so an id already claimed there cannot also land here: one document can
    # carry two entries for one id that differ in keyability, and keying the
    # first while holding the second aside put that id in resolved AND in
    # unkeyable. _shots_by_id has already counted the repeat and the caller
    # notes it, so keeping the second copy would also make that note's "kept
    # the first and dropped 1" untrue.
    unkeyable: list[dict] = []
    unkeyable_ids: set[str] = set(local_shots)
    for shot in merged_shots_list:
        if not isinstance(shot, dict) or _has_identity(shot):
            continue
        shot_id = shot.get("id")
        if isinstance(shot_id, str) and shot_id:
            if shot_id in unkeyable_ids:
                continue
            unkeyable_ids.add(shot_id)
        unkeyable.append(shot)
    if unkeyable:
        result.notes.append(
            f"{doc_key}: {len(unkeyable)} shot(s) carry neither candidate_number nor "
            "time, so they have no convergent id; local copies kept unmerged"
        )

    # A collision drops a shot no matter what we do; the one thing that must
    # not happen is dropping it quietly.
    for side, duplicates in (("local", local_duplicates), ("remote", remote_duplicates)):
        for shot_id in sorted(duplicates):
            dropped = duplicates[shot_id]
            result.notes.append(
                f"{doc_key}: shot id {shot_id} appears {dropped + 1} times in the {side} "
                f"document, which no writer should produce; kept the first and dropped "
                f"{dropped} - the document is malformed"
            )

    # A verdict acts only when the other side's document corroborates it.
    #
    # ``audit_events`` is append-only and never pruned (audit-doc.ts
    # concatenates history on every save), so it is one side's *session
    # journal*, not an authoritative record of what that side's shot list
    # holds. Two measured ways it lies:
    #
    #  - Ctrl+Z in the audit screen restores a rejected marker but writes no
    #    compensating event (Audit.tsx's ``undo``), and ``performSave`` ships
    #    the session's events verbatim. The stale ``marker_rejected`` then
    #    deletes a shot that is present in the saver's own document.
    #  - Re-detection with ``reset`` rewrites ``doc["shots"]`` wholesale and
    #    writes no ``marker_*`` events at all, so the superseded run's shots
    #    have no verdict and were adopted back unconditionally as
    #    "remote-only additions" - phantom shots, and ``cand-<n>`` aliases
    #    two different physical shots across runs.
    #
    # So a delete acts only if the other side actually dropped the shot, and
    # an adoption of a shot the other side has but we do not requires either
    # that it is new since base or that an event says to keep it.
    #
    # Self-consistent once a poisoned log has been pushed: the stale delete
    # then reaches ``remote["audit_events"]`` too, but the shot reaches
    # ``remote_shots`` with it, and the ``not in remote_shots`` clause keeps
    # it. The verdict never becomes actionable by being echoed back.
    remote_event_ids = _membership_event_ids(remote_for_merge.get("audit_events"))

    def _remote_knows(shot_id: str) -> bool:
        return shot_id in base_shots or shot_id in remote_shots or shot_id in remote_event_ids

    def _dropped(shot_id: str) -> bool:
        if verdicts.get(shot_id) is not False:
            return False
        # Remote still carries it: whatever the log says, the other side's
        # own document contradicts the delete.
        if shot_id in remote_shots:
            return False
        return _remote_knows(shot_id)

    # unkeyable and resolved are disjoint only when a shot's keyability is the
    # same on both sides. Local holding an id aside while remote's copy of
    # that id keys (local has no time, remote has one) would otherwise adopt
    # remote's as a remote-only addition and put the id on two entries. Local
    # wins, as everywhere else a shot is held aside.
    remote_only = [
        k
        for k in remote_shots
        if k not in local_shots
        and k not in unkeyable_ids
        and (k not in base_shots or verdicts.get(k) is True)
    ]

    resolved: dict[str, dict] = {}
    for shot_id in list(local_shots) + remote_only:
        if _dropped(shot_id):
            continue
        local_shot = local_shots.get(shot_id)
        remote_shot = remote_shots.get(shot_id)
        if local_shot is None:
            resolved[shot_id] = copy.deepcopy(remote_shot)
            continue
        shot = local_shot
        resolved[shot_id] = shot
        if remote_shot is None:
            continue
        base_shot = base_shots.get(shot_id, {})

        winner, is_conflict = _resolve_unit(
            base_shot.get("time"),
            shot.get("time"),
            remote_shot.get("time"),
            local_ts=local_ts,
            remote_ts=remote_ts,
        )
        if is_conflict:
            result.conflicts.append(
                MergeConflict(doc_key=doc_key, unit=f"shot {shot_id} time", winner=winner)
            )
        if winner == "remote":
            shot["time"] = copy.deepcopy(remote_shot.get("time"))
            # ms_after_beep is derived from time and beep_time. The rebuild
            # below re-derives it whenever it can, but with no usable
            # beep_time it cannot -- and local's stale value against
            # remote's time is the exact contradiction re-derivation exists
            # to prevent. So it travels with the time it belongs to.
            if "ms_after_beep" in remote_shot:
                shot["ms_after_beep"] = copy.deepcopy(remote_shot["ms_after_beep"])
            else:
                shot.pop("ms_after_beep", None)

        winner, is_conflict = _resolve_unit(
            _coach_unit(base_shot),
            _coach_unit(shot),
            _coach_unit(remote_shot),
            local_ts=local_ts,
            remote_ts=remote_ts,
        )
        if is_conflict:
            result.conflicts.append(
                MergeConflict(doc_key=doc_key, unit=f"shot {shot_id} coach", winner=winner)
            )
        if winner == "remote" and _coach_unit(remote_shot) != _coach_unit(shot):
            for key in COACH_FIELDS:
                if key in remote_shot:
                    shot[key] = copy.deepcopy(remote_shot[key])
                else:
                    shot.pop(key, None)

    # Renumber and re-derive. shot_number is display-only now, and
    # ms_after_beep is a function of the merged time -- merging it
    # independently could contradict the time it is derived from.
    #
    # ``interval_class`` is NOT re-derived here, and it can end up stale
    # relative to the times it describes: it classifies the gap to the
    # preceding shot, so a phone-side delete combined with a desktop-side add
    # (or a nudge on either) re-partners a shot with a different neighbour
    # while its stored class still describes the old one. Re-deriving it here
    # is the wrong call -- ``coach.classify_intervals_in_dicts`` walks the
    # whole stage in time order, which is not this function's job, and the
    # class is also hand-settable (``interval_class_source: "manual"``). It
    # self-heals on the next audit save: the save boundary in ui/server.py
    # runs that classifier over the whole list (#775) and leaves manual
    # classes alone. Until then a merged doc can show a class describing a
    # neighbour it no longer has.
    # A time that is not a number sorts with the missing ones rather than
    # raising: audit docs are hand-editable JSON, and the positional merge
    # this replaced never looked at the value, so junk used to survive.
    beep_time = _as_number(merged.get("beep_time"))

    def _order_key(shot: dict) -> tuple[bool, float, str]:
        time = _as_number(shot.get("time"))
        if time is None:
            return (True, 0.0, str(shot.get("id") or ""))
        return (False, time, str(shot.get("id") or ""))

    ordered = sorted([*resolved.values(), *unkeyable], key=_order_key)
    for index, shot in enumerate(ordered, start=1):
        shot["shot_number"] = index
        time = _as_number(shot.get("time"))
        if beep_time is not None and time is not None:
            shot["ms_after_beep"] = int(round((time - beep_time) * 1000))
    # After the renumber, never before: a non-dict entry has no shot_number
    # to assign, and numbering the real shots 1..n contiguously is what the
    # display wants. Ascending positions, so each insert shifts the next.
    for position, entry in foreign:
        ordered.insert(min(position, len(ordered)), entry)
    merged["shots"] = ordered


def _note_non_whitelisted_remote_changes(
    result: MergeResult, base: dict | None, merged: dict, remote: dict, doc_key: str
) -> None:
    """Tripwire for remote edits outside the whitelist.

    While the mirror write gate is closed nothing hosted-side can touch
    non-whitelisted fields, so any diff here is a bug or a future
    surface shipping without a whitelist entry - worth a loud note, not
    silence. Comparison trick: strip the whitelisted units from both
    docs and compare the rest against base's rest.
    """
    if base is None:
        return

    def _strip(doc: dict) -> dict:
        clone = copy.deepcopy(doc)
        # #821: MatchProject.updated_at bumps on every hosted save - not a
        # mobile-write field, just save-noise. Stripping it here stops a
        # spurious "remote changed non-whitelisted fields" note firing on
        # every phone write.
        clone.pop("updated_at", None)
        for stage in clone.get("stages") or []:
            if not isinstance(stage, dict):
                continue
            for video in stage.get("videos") or []:
                if isinstance(video, dict):
                    for k in list(video):
                        if k.startswith(_BEEP_PREFIX):
                            del video[k]
                    video.pop("processed", None)
        return clone

    if _strip(remote) != _strip(base) and _strip(remote) != _strip(merged):
        result.notes.append(
            f"{doc_key}: remote changed non-whitelisted fields; local wins "
            "(mirror write gate should make this impossible - investigate)"
        )
