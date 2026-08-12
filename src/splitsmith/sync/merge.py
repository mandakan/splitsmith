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
append-only marker events, so a phone can add, move and remove shots.
Only a shot that *arrived* carrying a persisted string ``id`` is
mergeable: an id minted inside the merge is not convergent across sides,
because ``derive_shot_id`` keys a candidate-less shot off its rounded
time and a nudge therefore changes it. If either side carries an
unstamped shot the whole shot section is skipped with a note.

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
        # stand as they are (now stamped), so nothing can duplicate and
        # nothing can be lost. The condition clears the first time each side
        # saves the stage.
        result.notes.append(
            f"{doc_key}: {local_unstamped} local and {remote_unstamped} remote shot(s) "
            "arrived without a persisted id, so the shot section was not merged and "
            "local shots stand; they are stamped now, so the next sync can merge them"
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

    # Same tripwire as the project merge: remote edits outside the audit
    # whitelist (events + coach fields) should be impossible while the
    # mirror write gate is closed - note them loudly, local wins.
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

    if base is not None:
        base_residue, remote_residue = _shot_residue(base), _shot_residue(remote)
        shared = base_residue.keys() & remote_residue.keys()
        residue_changed = any(base_residue[k] != remote_residue[k] for k in shared)
        if _strip_audit(remote) != _strip_audit(base) or residue_changed:
            result.notes.append(
                f"{doc_key}: remote changed non-whitelisted audit fields; local wins "
                "(mirror write gate should make this impossible - investigate)"
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

    unkeyable = [s for s in merged_shots_list if isinstance(s, dict) and not _has_identity(s)]
    if unkeyable:
        result.notes.append(
            f"{doc_key}: {len(unkeyable)} shot(s) carry neither candidate_number nor "
            "time, so they have no convergent id; local copies kept unmerged"
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

    resolved: dict[str, dict] = {}
    for shot_id in list(local_shots) + [k for k in remote_shots if k not in local_shots]:
        if verdicts.get(shot_id) is False:
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
