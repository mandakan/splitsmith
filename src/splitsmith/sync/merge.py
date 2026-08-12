"""Pure three-way merge for the bidirectional sync slice.

Desktop is authoritative for everything except the narrow whitelist
mobile is allowed to write (spec 2026-08-10-bidirectional-sync-design):
per-video beep field-groups in project docs, per-shot coach fields and
the append-only ``audit_events`` log in audit docs. Each merge starts
from a deep copy of the local doc and resolves whitelisted units
three-way against the base snapshot: changed on one side wins outright;
changed on both is a true conflict resolved last-writer-wins by doc
timestamp and always surfaced on :attr:`MergeResult.conflicts` - never
silent. Structural membership (stages, videos, shots) is
desktop-authoritative: remote-only additions/removals are noted, not
merged.

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


def _shots_by_number(doc: dict | None) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for shot in (doc or {}).get("shots") or []:
        if isinstance(shot, dict) and shot.get("shot_number") is not None:
            out[int(shot["shot_number"])] = shot
    return out


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

    base_shots = _shots_by_number(base)
    remote_shots = _shots_by_number(remote)
    for shot_number, merged_shot in _shots_by_number(merged).items():
        remote_shot = remote_shots.get(shot_number)
        if remote_shot is None:
            continue
        winner, is_conflict = _resolve_unit(
            _coach_unit(base_shots.get(shot_number, {})),
            _coach_unit(merged_shot),
            _coach_unit(remote_shot),
            local_ts=local_ts,
            remote_ts=remote_ts,
        )
        unit_name = f"shot {shot_number} coach"
        if is_conflict:
            result.conflicts.append(MergeConflict(doc_key=doc_key, unit=unit_name, winner=winner))
        if winner == "remote" and _coach_unit(remote_shot) != _coach_unit(merged_shot):
            for k in COACH_FIELDS:
                if k in remote_shot:
                    merged_shot[k] = copy.deepcopy(remote_shot[k])
                else:
                    merged_shot.pop(k, None)

    for shot_number in remote_shots.keys() - _shots_by_number(merged).keys():
        result.notes.append(
            f"{doc_key}: remote has shot {shot_number} that local lacks - "
            "shot membership is desktop-owned; ignored"
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
        clone = copy.deepcopy(doc or {})
        clone.pop("audit_events", None)
        clone.pop("needs_attention", None)
        for shot in clone.get("shots") or []:
            if isinstance(shot, dict):
                for k in COACH_FIELDS:
                    shot.pop(k, None)
        return clone

    if base is not None and _strip_audit(remote) != _strip_audit(base):
        result.notes.append(
            f"{doc_key}: remote changed non-whitelisted audit fields; local wins "
            "(mirror write gate should make this impossible - investigate)"
        )

    result.changed_vs_local = merged != local
    return result


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
