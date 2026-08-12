"""Three-way merge engine conflict matrix (bidirectional sync slice)."""

import copy
from datetime import UTC, datetime

import pytest

from splitsmith.sync.merge import _membership_verdicts, merge_audit_doc, merge_project_doc

T_OLD = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
T_NEW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _video(**over):
    v = {
        "video_id": "vid1",
        "role": "primary",
        "beep_time": 1.0,
        "beep_source": "auto",
        "beep_reviewed": False,
        "beep_confidence": 0.5,
        "processed": {"beep": True, "trim": True, "shot_detect": True},
    }
    v.update(over)
    return v


def _project(video):
    return {"stages": [{"stage_number": 3, "videos": [video]}]}


def test_remote_only_beep_change_wins_and_invalidates():
    base = _project(_video())
    local = _project(_video())
    remote = _project(_video(beep_time=2.5, beep_source="manual", beep_reviewed=True))
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_OLD, remote_ts=T_NEW)
    v = r.doc["stages"][0]["videos"][0]
    assert v["beep_time"] == 2.5 and v["beep_source"] == "manual"
    assert v["processed"] == {"beep": True, "trim": False, "shot_detect": False}
    assert r.reprocess_video_ids == ["vid1"]
    assert r.conflicts == [] and r.changed_vs_local is True


def test_confirm_only_remote_change_wins_without_invalidating() -> None:
    """#821: a phone confirm flips beep_reviewed with beep_time unchanged.
    The group still moves atomically, but trim/shot_detect derive from
    beep_time alone - re-running them would burn ffmpeg minutes to
    produce identical output."""
    base = _project(_video(beep_time=2.5))
    local = _project(_video(beep_time=2.5))
    remote = _project(_video(beep_time=2.5, beep_reviewed=True))
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_OLD, remote_ts=T_NEW)
    v = r.doc["stages"][0]["videos"][0]
    assert v["beep_reviewed"] is True
    assert v["processed"].get("trim") is not False
    assert r.reprocess_video_ids == []
    assert r.changed_vs_local is True


def test_local_only_beep_change_kept_no_reprocess():
    base = _project(_video())
    local = _project(_video(beep_time=9.9, beep_source="manual"))
    remote = _project(_video())
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_NEW, remote_ts=T_OLD)
    assert r.doc == local and r.reprocess_video_ids == [] and r.changed_vs_local is False


def test_both_same_value_no_conflict():
    changed = _video(beep_time=2.5, beep_source="manual")
    r = merge_project_doc(
        _project(_video()),
        _project(changed),
        _project(dict(changed)),
        doc_key="project/anna",
        local_ts=T_OLD,
        remote_ts=T_NEW,
    )
    assert r.conflicts == [] and r.reprocess_video_ids == []


def test_true_conflict_remote_newer_wins_and_logs():
    base = _project(_video())
    local = _project(_video(beep_time=5.0, beep_source="manual"))
    remote = _project(_video(beep_time=2.5, beep_source="manual"))
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["stages"][0]["videos"][0]["beep_time"] == 2.5
    assert len(r.conflicts) == 1 and r.conflicts[0].winner == "remote"
    assert r.reprocess_video_ids == ["vid1"]


def test_true_conflict_local_newer_wins_and_logs():
    base = _project(_video())
    local = _project(_video(beep_time=5.0, beep_source="manual"))
    remote = _project(_video(beep_time=2.5, beep_source="manual"))
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_NEW, remote_ts=T_OLD)
    assert r.doc["stages"][0]["videos"][0]["beep_time"] == 5.0
    assert len(r.conflicts) == 1 and r.conflicts[0].winner == "local"
    assert r.reprocess_video_ids == []


def test_empty_base_treats_both_sides_as_changed():
    local = _project(_video(beep_time=5.0))
    remote = _project(_video(beep_time=2.5))
    r = merge_project_doc(None, local, remote, doc_key="project/anna", local_ts=T_NEW, remote_ts=T_OLD)
    assert r.doc["stages"][0]["videos"][0]["beep_time"] == 5.0
    assert len(r.conflicts) == 1


def test_remote_extra_video_is_noted_not_merged():
    base = _project(_video())
    local = _project(_video())
    remote = {"stages": [{"stage_number": 3, "videos": [_video(), _video(video_id="vid2")]}]}
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_OLD, remote_ts=T_NEW)
    assert len(r.doc["stages"][0]["videos"]) == 1
    assert any("vid2" in n for n in r.notes)


def test_non_whitelisted_remote_change_local_wins_with_note():
    base = _project(_video())
    local = _project(_video())
    remote = _project(_video())
    remote["stages"][0]["skipped"] = True  # not in any whitelist
    r = merge_project_doc(base, local, remote, doc_key="project/anna", local_ts=T_OLD, remote_ts=T_NEW)
    assert "skipped" not in r.doc["stages"][0]
    assert any("non-whitelisted" in n for n in r.notes)


# -- audit docs ------------------------------------------------------


def _shot(n, **over):
    # The persisted id is not decoration: the merge refuses the whole shot
    # section of a document where either side carries an unstamped shot, so
    # a helper without one would exercise the refusal, not the merge.
    s = {
        "id": f"cand-{n}",
        "candidate_number": n,
        "shot_number": n,
        "time": float(n),
        "interval_class": "split",
        "interval_class_source": "auto",
    }
    s.update(over)
    return s


def _audit(shots, events):
    return {"shots": shots, "audit_events": events}


E1 = {"id": "e1", "ts": "2026-08-10T10:00:00+00:00", "kind": "save", "payload": {}}
E2 = {"id": "e2", "ts": "2026-08-10T11:00:00+00:00", "kind": "coach_patch", "payload": {}}
E3 = {"id": "e3", "ts": "2026-08-10T12:00:00+00:00", "kind": "accept", "payload": {}}


def test_event_union_by_id_sorted_by_ts():
    base = _audit([], [E1])
    local = _audit([], [E1, E2])
    remote = _audit([], [E1, E3])
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    assert [e["id"] for e in r.doc["audit_events"]] == ["e1", "e2", "e3"]
    assert r.conflicts == [] and r.changed_vs_local is True


def test_event_union_legacy_idless_dedupes_by_ts_kind():
    legacy = {"ts": "2026-08-10T09:00:00+00:00", "kind": "save", "payload": {}}
    base = _audit([], [legacy])
    local = _audit([], [legacy])
    remote = _audit([], [dict(legacy), E3])  # same legacy event round-tripped, plus one new
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    ids = [(e.get("id"), e["ts"], e["kind"]) for e in r.doc["audit_events"]]
    assert len(ids) == 2  # legacy not doubled


def test_coach_fields_remote_only_change_wins():
    base = _audit([_shot(1)], [])
    local = _audit([_shot(1)], [])
    remote = _audit(
        [_shot(1, interval_class="draw", interval_class_source="manual", coaching_note="slow")], []
    )
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    s = r.doc["shots"][0]
    assert s["interval_class"] == "draw" and s["coaching_note"] == "slow"


def test_coach_conflict_lww_and_shot_absent_from_remote_survives():
    base = _audit([_shot(1)], [])
    local = _audit([_shot(1, coaching_note="mine"), _shot(2)], [])
    remote = _audit([_shot(1, coaching_note="theirs")], [])
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["shots"][0]["coaching_note"] == "theirs"  # remote newer
    # Shot 2 survives because no event carries a delete verdict for it, not
    # because the local shot list is authoritative -- membership is merged
    # now, and a marker_rejected on cand-2 would remove it.
    assert len(r.doc["shots"]) == 2
    assert len(r.conflicts) == 1


def test_audit_non_whitelisted_remote_change_noted_local_wins():
    # Shot ``time`` used to be the non-whitelisted field this asserted on;
    # it is merged now, so the tripwire needs a field that still isn't -
    # here the detector's own ``confidence`` on a shot both sides carry.
    base = _audit([_shot(1, id="cand-1", candidate_number=1, confidence=0.9)], [])
    local = _audit([_shot(1, id="cand-1", candidate_number=1, confidence=0.9)], [])
    remote = _audit([_shot(1, id="cand-1", candidate_number=1, confidence=0.1)], [])
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["shots"][0]["confidence"] == 0.9
    assert any("non-whitelisted" in n for n in r.notes)


def test_audit_remote_shot_nudge_is_not_a_tripwire():
    # The converse of the above, and the reason it had to change: a phone
    # moving a shot is a whitelisted write now, so it must merge silently
    # rather than log "remote changed non-whitelisted audit fields".
    base = _audit([_shot(1, id="cand-1", candidate_number=1)], [])
    local = _audit([_shot(1, id="cand-1", candidate_number=1)], [])
    remote = _audit([_shot(1, id="cand-1", candidate_number=1, time=9.9)], [])
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["shots"][0]["time"] == 9.9
    assert not r.notes


# needs_attention (triage slice 4)


def _na(flagged, ts, note=None):
    return {"flagged": flagged, "flagged_at": ts if flagged else None, "note": note, "updated_at": ts}


def test_needs_attention_remote_only_change_wins():
    base = _audit([], [])
    local = _audit([], [])
    remote = _audit([], [])
    remote["needs_attention"] = _na(True, "2026-08-11T10:00:00+00:00")
    r = merge_audit_doc(base, local, remote, doc_key="audit/alice/1", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["needs_attention"]["flagged"] is True
    assert not r.conflicts and not r.notes


def test_needs_attention_local_clear_kept_when_remote_unchanged():
    base = _audit([], [])
    base["needs_attention"] = _na(True, "2026-08-11T09:00:00+00:00")
    local = _audit([], [])
    local["needs_attention"] = _na(False, "2026-08-11T10:00:00+00:00")
    remote = _audit([], [])
    remote["needs_attention"] = _na(True, "2026-08-11T09:00:00+00:00")
    r = merge_audit_doc(base, local, remote, doc_key="audit/alice/1", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["needs_attention"]["flagged"] is False
    assert not r.conflicts


def test_needs_attention_true_conflict_remote_newer_wins_and_logs():
    base = _audit([], [])
    base["needs_attention"] = _na(False, "2026-08-11T08:00:00+00:00")
    local = _audit([], [])
    local["needs_attention"] = _na(True, "2026-08-11T09:00:00+00:00", "local note")
    remote = _audit([], [])
    remote["needs_attention"] = _na(True, "2026-08-11T10:00:00+00:00", "check")
    r = merge_audit_doc(base, local, remote, doc_key="audit/alice/1", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["needs_attention"] == remote["needs_attention"]
    assert [c.unit for c in r.conflicts] == ["needs_attention"]
    assert r.conflicts[0].winner == "remote"


def test_needs_attention_true_conflict_local_newer_wins_and_logs():
    base = _audit([], [])
    base["needs_attention"] = _na(False, "2026-08-11T08:00:00+00:00")
    local = _audit([], [])
    local["needs_attention"] = _na(True, "2026-08-11T10:00:00+00:00", "local note")
    remote = _audit([], [])
    remote["needs_attention"] = _na(True, "2026-08-11T09:00:00+00:00", "check")
    r = merge_audit_doc(base, local, remote, doc_key="audit/alice/1", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["needs_attention"] == local["needs_attention"]
    assert [c.unit for c in r.conflicts] == ["needs_attention"]
    assert r.conflicts[0].winner == "local"


def test_needs_attention_converged_content_no_phantom_conflict():
    # Both sides flag with the same note at different times - content
    # projection {flagged, note} is identical on both sides, so this
    # must NOT log a conflict even though the raw objects' stamps
    # differ. The newer-stamped object (full four keys) is what lands
    # in the merged doc.
    base = _audit([], [])
    base["needs_attention"] = _na(False, "2026-08-11T08:00:00+00:00")
    local = _audit([], [])
    local["needs_attention"] = _na(True, "2026-08-11T09:00:00+00:00", "same note")
    remote = _audit([], [])
    remote["needs_attention"] = _na(True, "2026-08-11T10:30:00+00:00", "same note")
    r = merge_audit_doc(base, local, remote, doc_key="audit/alice/1", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["needs_attention"] == remote["needs_attention"]
    assert r.conflicts == []


def test_needs_attention_naive_timestamp_does_not_raise():
    # One side's updated_at has no UTC offset (e.g. a client that wrote
    # datetime.isoformat() without tzinfo) - fromisoformat parses it
    # naive, and comparing that against the aware remote/local stamp used
    # to raise TypeError. It must not raise, and LWW still resolves to
    # the newer side by wall-clock time.
    base = _audit([], [])
    base["needs_attention"] = _na(False, "2026-08-11T08:00:00+00:00")
    local = _audit([], [])
    local["needs_attention"] = _na(True, "2026-08-11T09:00:00+00:00", "local note")
    remote = _audit([], [])
    remote["needs_attention"] = _na(True, "2026-08-11T10:00:00", "no offset")  # naive
    r = merge_audit_doc(base, local, remote, doc_key="audit/alice/1", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["needs_attention"] == remote["needs_attention"]
    assert [c.unit for c in r.conflicts] == ["needs_attention"]
    assert r.conflicts[0].winner == "remote"


def test_needs_attention_not_a_tripwire():
    # remote adds the key; the non-whitelisted-fields note must NOT fire
    base = _audit([], [])
    local = _audit([], [])
    remote = _audit([], [])
    remote["needs_attention"] = _na(True, "2026-08-11T10:00:00+00:00")
    r = merge_audit_doc(base, local, remote, doc_key="audit/alice/1", local_ts=T_OLD, remote_ts=T_NEW)
    assert not r.notes


def test_project_updated_at_stamp_is_not_a_tripwire():
    # 821: hosted saves bump MatchProject.updated_at; that alone must not
    # fire the non-whitelisted-change note
    base = _project(_video())
    local = _project(_video())
    remote = _project(_video())
    remote["updated_at"] = "2026-08-11T10:00:00+00:00"
    r = merge_project_doc(base, local, remote, doc_key="project/alice", local_ts=T_OLD, remote_ts=T_NEW)
    assert not r.notes


# -- membership verdicts (Task 4) ----------------------------------------


def _ev(kind: str, shot_id: str, ts: str) -> dict:
    return {"id": f"{kind}-{ts}", "ts": ts, "kind": kind, "payload": {"id": shot_id}}


def test_membership_reads_the_existing_marker_vocabulary() -> None:
    verdicts = _membership_verdicts(
        [
            _ev("marker_added_manual", "manual-a", "2026-08-12T10:00:00Z"),
            _ev("marker_kept", "cand-4", "2026-08-12T10:01:00Z"),
            _ev("marker_rejected", "cand-9", "2026-08-12T10:02:00Z"),
            _ev("marker_deleted", "manual-b", "2026-08-12T10:03:00Z"),
        ]
    )
    assert verdicts == {
        "manual-a": True,
        "cand-4": True,
        "cand-9": False,
        "manual-b": False,
    }


def test_latest_event_wins_regardless_of_list_order() -> None:
    """A union merge concatenates two histories; order is not chronological."""
    verdicts = _membership_verdicts(
        [
            _ev("marker_rejected", "cand-4", "2026-08-12T11:00:00Z"),
            _ev("marker_kept", "cand-4", "2026-08-12T10:00:00Z"),
        ]
    )
    assert verdicts == {"cand-4": False}


def test_unrelated_and_malformed_events_are_ignored() -> None:
    verdicts = _membership_verdicts(
        [
            {"kind": "save", "ts": "2026-08-12T10:00:00Z", "payload": {}},
            {"kind": "marker_time_changed", "ts": "2026-08-12T10:01:00Z", "payload": {"id": "cand-4"}},
            {"kind": "marker_kept", "ts": "2026-08-12T10:02:00Z"},
            {"kind": ["marker_kept"], "ts": "2026-08-12T10:03:00Z", "payload": {"id": "x"}},
            "not a dict",
        ]
    )
    assert verdicts == {}


# -- shot membership and timing merged by id (Task 5) --------------------
#
# ``_id_shot``/``_id_doc`` rather than reusing ``_shot``/``_audit`` above:
# a second module-level ``_shot`` would shadow the first and break every
# pre-existing audit test in this file.

_LOCAL_TS = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
_REMOTE_TS = datetime(2026, 8, 12, 13, 0, tzinfo=UTC)


def _id_shot(shot_id: str, number: int, time: float, candidate: int | None = None) -> dict:
    return {
        "id": shot_id,
        "shot_number": number,
        "candidate_number": candidate,
        "time": time,
        "ms_after_beep": int(round((time - 5.0) * 1000)),
        "source": "manual" if candidate is None else "detected",
    }


def _id_doc(shots: list[dict], events: list[dict] | None = None) -> dict:
    return {"beep_time": 5.0, "shots": shots, "audit_events": events or []}


def test_remote_added_shot_is_adopted() -> None:
    """This is the behaviour merge.py previously refused outright."""
    base = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    local = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    remote = _id_doc(
        [_id_shot("cand-4", 1, 6.0, 4), _id_shot("manual-x", 2, 6.5)],
        [_ev("marker_added_manual", "manual-x", "2026-08-12T12:30:00Z")],
    )
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert [s["id"] for s in result.doc["shots"]] == ["cand-4", "manual-x"]
    assert [s["shot_number"] for s in result.doc["shots"]] == [1, 2]


def test_remote_delete_removes_a_locally_present_shot() -> None:
    base = _id_doc([_id_shot("cand-4", 1, 6.0, 4), _id_shot("cand-9", 2, 6.5, 9)])
    local = _id_doc([_id_shot("cand-4", 1, 6.0, 4), _id_shot("cand-9", 2, 6.5, 9)])
    remote = _id_doc(
        [_id_shot("cand-4", 1, 6.0, 4)],
        [_ev("marker_rejected", "cand-9", "2026-08-12T12:30:00Z")],
    )
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert [s["id"] for s in result.doc["shots"]] == ["cand-4"]


def test_remote_nudge_wins_and_recomputes_ms_after_beep() -> None:
    base = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    local = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    remote = _id_doc([_id_shot("cand-4", 1, 6.02, 4)])
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    merged = result.doc["shots"][0]
    assert merged["time"] == 6.02
    assert merged["ms_after_beep"] == 1020


def test_both_sides_nudged_is_a_surfaced_conflict() -> None:
    base = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    local = _id_doc([_id_shot("cand-4", 1, 6.01, 4)])
    remote = _id_doc([_id_shot("cand-4", 1, 6.02, 4)])
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert result.doc["shots"][0]["time"] == 6.02  # remote_ts is newer
    assert [c.unit for c in result.conflicts] == ["shot cand-4 time"]


def test_a_deleted_shot_is_not_resurrected_by_the_other_side() -> None:
    """Local deleted it; remote still carries it with no newer verdict."""
    base = _id_doc([_id_shot("cand-9", 1, 6.5, 9)])
    local = _id_doc([], [_ev("marker_rejected", "cand-9", "2026-08-12T12:30:00Z")])
    remote = _id_doc([_id_shot("cand-9", 1, 6.5, 9)])
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert result.doc["shots"] == []


def test_a_legacy_doc_with_no_ids_keeps_its_shots() -> None:
    """Documents written before shot ids shipped must survive a merge.

    These shots carry no id, so the document fails the unstamped gate and
    the shot section is refused outright: local's list is never rebuilt and
    the shots come through untouched, stamped for the future. (Before the
    gate they were keyed on ids minted mid-merge, which is what let a nudged
    manual shot duplicate.)
    """
    legacy = {
        "beep_time": 5.0,
        "shots": [
            {"shot_number": 1, "candidate_number": 4, "time": 6.0, "ms_after_beep": 1000},
            {"shot_number": 2, "candidate_number": None, "time": 6.5, "ms_after_beep": 1500},
        ],
        "audit_events": [],
    }
    result = merge_audit_doc(
        copy.deepcopy(legacy),
        copy.deepcopy(legacy),
        copy.deepcopy(legacy),
        doc_key="stage1",
        local_ts=_LOCAL_TS,
        remote_ts=_REMOTE_TS,
    )
    assert len(result.doc["shots"]) == 2
    assert [s["id"] for s in result.doc["shots"]] == ["cand-4", "manual-t6500"]


def test_a_shot_with_no_identity_is_kept_once_not_duplicated() -> None:
    """A shot with neither candidate_number nor time has no convergent id.

    The anchor carries a persisted id, so the document clears the unstamped
    gate and this exercises the hold-aside rather than the refusal. Keying
    it would still be wrong -- the id is a uuid4 someone happened to save --
    so it must be carried through exactly once.
    """
    anchor = {"id": "anchor-1", "shot_number": 1, "candidate_number": None, "time": None}
    base = {"beep_time": 5.0, "shots": [dict(anchor)], "audit_events": []}
    local = {"beep_time": 5.0, "shots": [dict(anchor)], "audit_events": []}
    remote = {"beep_time": 5.0, "shots": [dict(anchor)], "audit_events": []}
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert len(result.doc["shots"]) == 1
    assert any("no convergent id" in note for note in result.notes)


def test_promote_then_delete_round_trips_to_absent() -> None:
    """Promote a rejected candidate, then delete it again on the other side.

    The newest verdict must win, not the fact that a promote happened at all.
    """
    base = _id_doc([])
    local = _id_doc(
        [_id_shot("cand-9", 1, 6.5, 9)],
        [_ev("marker_kept", "cand-9", "2026-08-12T12:10:00Z")],
    )
    remote = _id_doc(
        [],
        [
            _ev("marker_kept", "cand-9", "2026-08-12T12:10:00Z"),
            _ev("marker_rejected", "cand-9", "2026-08-12T12:20:00Z"),
        ],
    )
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert result.doc["shots"] == []


def test_a_delete_verdict_acts_when_base_holds_the_shot_and_remote_dropped_it() -> None:
    """Pins the ``base_shots`` clause of ``_remote_knows`` (#846).

    ``_remote_knows`` corroborates a delete verdict three ways, and only
    the ``remote_event_ids`` one was pinned -- deleting either of the
    other two left the whole suite green. This is the ``base_shots``
    case, and it is the ordinary three-way-merge shape: the shot existed
    at base, remote's document no longer carries it, so remote deleted
    it.

    Local still holds the shot *and* a ``marker_rejected`` for it, which
    is the Ctrl+Z shape ``_merge_shot_section`` documents: undo restores
    a rejected marker without writing a compensating event, so the log
    disagrees with the document it was saved beside. The verdict is
    stale on its own and must not act on its own -- but here remote's
    document independently corroborates it, and remote's log says
    nothing at all, so ``base_shots`` is the only clause that can carry
    the delete.
    """
    base = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    local = _id_doc(
        [_id_shot("cand-4", 1, 6.0, 4)],
        [_ev("marker_rejected", "cand-4", "2026-08-12T12:10:00Z")],
    )
    remote = _id_doc([])
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert result.doc["shots"] == []


def test_delete_then_promote_round_trips_to_present() -> None:
    """The reverse order, to prove the verdict is time-ordered not kind-ordered."""
    base = _id_doc([_id_shot("cand-9", 1, 6.5, 9)])
    local = _id_doc(
        [],
        [_ev("marker_rejected", "cand-9", "2026-08-12T12:10:00Z")],
    )
    remote = _id_doc(
        [_id_shot("cand-9", 1, 6.5, 9)],
        [
            _ev("marker_rejected", "cand-9", "2026-08-12T12:10:00Z"),
            _ev("marker_kept", "cand-9", "2026-08-12T12:20:00Z"),
        ],
    )
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert [s["id"] for s in result.doc["shots"]] == ["cand-9"]


def test_shots_with_no_membership_event_are_kept() -> None:
    """Original detector output carries no events and must survive."""
    base = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    local = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    remote = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert [s["id"] for s in result.doc["shots"]] == ["cand-4"]


def test_duplicate_persisted_shot_id_keeps_one_and_says_so() -> None:
    """Two shots sharing one persisted id means a malformed document.

    ensure_shot_ids never mints a colliding id -- it falls back to a uuid4 --
    so this can only come from a bad writer. One copy is unavoidably lost;
    the point is that it is lost loudly, per merge.py's never-silent contract.
    """
    doc = _id_doc([_id_shot("cand-4", 1, 6.0, 4), _id_shot("cand-4", 2, 6.5, 4)])
    result = merge_audit_doc(
        copy.deepcopy(doc),
        copy.deepcopy(doc),
        copy.deepcopy(doc),
        doc_key="stage1",
        local_ts=_LOCAL_TS,
        remote_ts=_REMOTE_TS,
    )
    assert [s["id"] for s in result.doc["shots"]] == ["cand-4"]
    assert result.doc["shots"][0]["time"] == 6.0  # the first occurrence, not the last
    assert any("cand-4" in note and "malformed" in note for note in result.notes)


def test_non_dict_entries_in_shots_survive_the_rebuild() -> None:
    """The rebuild must not swallow data it does not understand.

    Before the rebuild existed these rode through untouched; dropping them
    now would be a silent loss of exactly the kind this module refuses.
    """

    def build() -> dict:
        return {
            "beep_time": 5.0,
            "shots": ["junk", _id_shot("cand-4", 2, 6.0, 4), 42],
            "audit_events": [],
        }

    result = merge_audit_doc(
        build(), build(), build(), doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert result.doc["shots"][0] == "junk"
    assert result.doc["shots"][1]["id"] == "cand-4"
    assert result.doc["shots"][2] == 42
    assert result.doc["shots"][1]["shot_number"] == 1  # real shots number 1..n


def test_a_shots_value_that_is_not_a_list_is_replaced_loudly() -> None:
    """A shots value that is not a list holds no shots, but say so."""

    def build() -> dict:
        return {"beep_time": 5.0, "shots": {"not": "a list"}, "audit_events": []}

    result = merge_audit_doc(
        build(), build(), build(), doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert result.doc["shots"] == []
    assert any("not a list" in note for note in result.notes)


# -- only a persisted id is mergeable (fix round 2) ----------------------
#
# derive_shot_id keys a candidate-less shot off its ROUNDED TIME, so a nudge
# changes the derived id -- and a nudge is the case the merge exists for. An
# id minted inside the merge is therefore not convergent across sides, and
# the shot section refuses to merge when either side carries an unstamped
# shot. See the 2026-08-12 correction in the plan.


def test_a_truthy_non_string_id_does_not_vanish() -> None:
    """Critical 1: a shot with id=42 was dropped by every path at once.

    ensure_shot_ids skipped it (truthy), _shots_by_id skipped it (not a str),
    _has_identity was True so it missed the unkeyable hold-aside, and it is a
    dict so it missed the non-dict passthrough. 100% loss, no note.
    """

    def build() -> dict:
        return {
            "beep_time": 5.0,
            "shots": [{"id": 42, "shot_number": 1, "candidate_number": 4, "time": 6.0}],
            "audit_events": [],
        }

    result = merge_audit_doc(
        build(), build(), build(), doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert len(result.doc["shots"]) == 1
    assert result.doc["shots"][0]["id"] == "cand-4"  # stamped with a real one


def test_a_legacy_manual_shot_nudged_on_one_side_does_not_duplicate() -> None:
    """Critical 2: the exact case the merge exists for, measured duplicating.

    Local has it at 6.5 s and remote at 6.52 s -- one shot someone moved.
    Derived, those are manual-t6500 and manual-t6520, unioned as two shots.
    """
    base = {"beep_time": 5.0, "shots": [{"shot_number": 1, "time": 6.5}], "audit_events": []}
    local = {"beep_time": 5.0, "shots": [{"shot_number": 1, "time": 6.5}], "audit_events": []}
    remote = {"beep_time": 5.0, "shots": [{"shot_number": 1, "time": 6.52}], "audit_events": []}
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert len(result.doc["shots"]) == 1
    assert result.doc["shots"][0]["time"] == 6.5  # local's, unmerged
    assert any("without a persisted id" in note for note in result.notes)


def test_a_legacy_collision_fallback_does_not_multiply_shots() -> None:
    """Critical 3: same cause via the uuid4 collision fallback -- 2 in, 3 out.

    Two shots share a candidate_number, so the second gets a minted id that
    differs on each side.
    """

    def build() -> dict:
        return {
            "beep_time": 5.0,
            "shots": [
                {"shot_number": 1, "candidate_number": 4, "time": 6.0},
                {"shot_number": 2, "candidate_number": 4, "time": 6.5},
            ],
            "audit_events": [],
        }

    result = merge_audit_doc(
        build(), build(), build(), doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert len(result.doc["shots"]) == 2
    assert any("without a persisted id" in note for note in result.notes)


def test_the_unstamped_note_names_both_side_counts() -> None:
    """The note has to be actionable: which side, and how many."""
    base = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    local = _id_doc([_id_shot("cand-4", 1, 6.0, 4), {"shot_number": 2, "time": 7.0}])
    remote = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    note = next(n for n in result.notes if "without a persisted id" in n)
    assert "1 local" in note and "0 remote" in note


def test_one_unstamped_shot_blocks_the_whole_shot_section() -> None:
    """The gate is per document, not per shot: a remote nudge on a properly
    stamped shot is refused too, because the document cannot be trusted."""
    base = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    local = _id_doc([_id_shot("cand-4", 1, 6.0, 4), {"shot_number": 2, "time": 7.0}])
    remote = _id_doc([_id_shot("cand-4", 1, 6.5, 4), {"id": "m2", "shot_number": 2, "time": 7.0}])
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert result.doc["shots"][0]["time"] == 6.0  # remote's nudge NOT taken
    assert result.conflicts == []


def test_a_coach_note_on_an_identity_less_shot_is_declined_and_noted() -> None:
    """The one behaviour this task changes, pinned.

    Both sides carry the same persisted id, so the document passes the
    stamped gate -- but the shot has neither candidate_number nor time, so
    the merge still holds it aside rather than trusting the id. The remote
    coach note must be neither silently applied nor silently lost.
    """

    def build(**over: object) -> dict:
        shot = {"id": "anchor-1", "shot_number": 1, "candidate_number": None, "time": None}
        shot.update(over)
        return {"beep_time": 5.0, "shots": [shot], "audit_events": []}

    result = merge_audit_doc(
        build(),
        build(),
        build(coaching_note="from-hosted", interval_class="draw"),
        doc_key="stage1",
        local_ts=_LOCAL_TS,
        remote_ts=_REMOTE_TS,
    )
    assert len(result.doc["shots"]) == 1
    assert "coaching_note" not in result.doc["shots"][0]  # declined, not applied
    assert any("no convergent id" in note for note in result.notes)  # not silent


def test_a_non_numeric_time_does_not_crash_the_sort() -> None:
    """Minor: the old code survived a junk time; the rebuild's sort key did not."""

    def build() -> dict:
        return {
            "beep_time": 5.0,
            "shots": [
                {"id": "cand-4", "candidate_number": 4, "time": 6.0},
                {"id": "junk-1", "candidate_number": 7, "time": "not a number"},
            ],
            "audit_events": [],
        }

    result = merge_audit_doc(
        build(), build(), build(), doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert {s["id"] for s in result.doc["shots"]} == {"cand-4", "junk-1"}


def test_ms_after_beep_travels_with_its_time_when_beep_time_is_absent() -> None:
    """Important 4: without a beep_time the rebuild cannot re-derive, so the
    winner's ms_after_beep must travel with the winner's time or the two
    contradict each other -- the exact thing re-derivation exists to prevent."""
    base = {
        "shots": [{"id": "cand-4", "candidate_number": 4, "time": 6.0, "ms_after_beep": 1000}],
        "audit_events": [],
    }
    local = {
        "shots": [{"id": "cand-4", "candidate_number": 4, "time": 6.0, "ms_after_beep": 1000}],
        "audit_events": [],
    }
    remote = {
        "shots": [{"id": "cand-4", "candidate_number": 4, "time": 9.0, "ms_after_beep": 4000}],
        "audit_events": [],
    }
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    merged = result.doc["shots"][0]
    assert merged["time"] == 9.0
    assert merged["ms_after_beep"] == 4000  # remote's, not local's stale 1000


# -- one id, one entry (fix round 3) ------------------------------------


def test_a_remote_shot_is_not_adopted_when_local_holds_its_id_aside() -> None:
    """unkeyable and resolved are only disjoint when keyability agrees.

    Local's copy has no time, so it is diverted to the hold-aside; remote's
    has one, so it keys and was adopted as a remote-only addition. Same id,
    two entries -- and the document then self-inflicts the duplicate-id note
    on the next merge.
    """
    base = {
        "beep_time": 5.0,
        "shots": [{"id": "anchor-1", "shot_number": 1, "time": None}],
        "audit_events": [],
    }
    local = copy.deepcopy(base)
    remote = {
        "beep_time": 5.0,
        "shots": [{"id": "anchor-1", "shot_number": 1, "time": 6.0}],
        "audit_events": [],
    }
    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)
    assert [s["id"] for s in result.doc["shots"]] == ["anchor-1"]
    assert result.doc["shots"][0]["time"] is None  # local's copy wins


def test_two_identity_less_shots_sharing_an_id_appear_once() -> None:
    """The hold-aside must not smuggle a duplicate id past _shots_by_id.

    _shots_by_id counts the collision and the note fires, but unkeyable kept
    both copies, so the note's "kept the first and dropped 1" was a lie.
    """

    def build() -> dict:
        return {
            "beep_time": 5.0,
            "shots": [
                {"id": "anchor-1", "shot_number": 1, "time": None},
                {"id": "anchor-1", "shot_number": 2, "time": None},
            ],
            "audit_events": [],
        }

    result = merge_audit_doc(
        build(), build(), build(), doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS
    )
    assert len(result.doc["shots"]) == 1
    assert any("malformed" in note for note in result.notes)


def test_no_id_appears_twice_across_the_keyability_matrix() -> None:
    """Every combination of keyable/identity-less on each side, at once.

    One entry per id per side. The sibling test below covers the family this
    one structurally cannot reach: two entries for one id on a single side.
    """
    local_shots = [
        {"id": "both-keyable", "candidate_number": 1, "time": 1.0},
        {"id": "local-aside", "shot_number": 2, "time": None},
        {"id": "remote-aside", "candidate_number": 3, "time": 3.0},
        {"id": "both-aside", "shot_number": 4, "time": None},
        {"id": "local-only", "candidate_number": 5, "time": 5.0},
    ]
    remote_shots = [
        {"id": "both-keyable", "candidate_number": 1, "time": 1.5},
        {"id": "local-aside", "candidate_number": 2, "time": 2.0},
        {"id": "remote-aside", "shot_number": 3, "time": None},
        {"id": "both-aside", "shot_number": 4, "time": None},
        {"id": "remote-only", "candidate_number": 6, "time": 6.0},
    ]
    result = merge_audit_doc(
        {"beep_time": 5.0, "shots": copy.deepcopy(local_shots), "audit_events": []},
        {"beep_time": 5.0, "shots": copy.deepcopy(local_shots), "audit_events": []},
        {"beep_time": 5.0, "shots": copy.deepcopy(remote_shots), "audit_events": []},
        doc_key="stage1",
        local_ts=_LOCAL_TS,
        remote_ts=_REMOTE_TS,
    )
    ids = [s["id"] for s in result.doc["shots"] if isinstance(s, dict)]
    assert len(ids) == len(set(ids)), f"duplicate id in merged output: {ids}"
    assert set(ids) == {
        "both-keyable",
        "local-aside",
        "remote-aside",
        "both-aside",
        "local-only",
        "remote-only",
    }


@pytest.mark.parametrize("keyable_first", [True, False])
@pytest.mark.parametrize("side", ["local", "remote"])
def test_one_side_carrying_two_entries_for_one_id(side: str, keyable_first: bool) -> None:
    """Two entries for one id on a single side, differing in keyability.

    The dedupe seeded unkeyable_ids only from ids it had already seen inside
    its own loop, so it never noticed the id was also claimed by a keyable
    shot in local_shots: the id landed in resolved AND in unkeyable, and
    ordered emitted it twice. Only the keyable-first ordering exposes it,
    which is why the single-entry matrix above missed it entirely.
    """
    pair = [
        {"id": "X", "shot_number": 1, "candidate_number": 1, "time": 1.0},
        {"id": "X", "shot_number": 2, "time": None},
    ]
    if not keyable_first:
        pair.reverse()
    other = [{"id": "Y", "candidate_number": 9, "time": 9.0}]
    doubled = {"beep_time": 5.0, "shots": copy.deepcopy(pair), "audit_events": []}
    single = {"beep_time": 5.0, "shots": copy.deepcopy(other), "audit_events": []}

    local, remote = (doubled, single) if side == "local" else (single, doubled)
    result = merge_audit_doc(
        copy.deepcopy(local),
        copy.deepcopy(local),
        copy.deepcopy(remote),
        doc_key="stage1",
        local_ts=_LOCAL_TS,
        remote_ts=_REMOTE_TS,
    )
    ids = [s["id"] for s in result.doc["shots"] if isinstance(s, dict)]
    assert len(ids) == len(set(ids)), f"duplicate id in merged output: {ids}"
    assert any("malformed" in note for note in result.notes)


# -- a verdict acts only when the other side corroborates it -------------
#
# ``audit_events`` is one side's session journal, never pruned. Both probes
# below were measured against the pre-fix merge; neither needs the newly
# opened audit PUT, since the shipped triage accept surface bumps the
# remote version, which is enough to route a document into the merge.


def _triaged(doc: dict) -> dict:
    """The same doc after a phone triage accept bumped its remote version.

    ``needs_attention`` is a whitelisted doc-level unit and is stripped by
    the tripwire projection, so this changes nothing about the shot merge -
    it is only what makes the document eligible to be pulled at all.
    """
    out = copy.deepcopy(doc)
    out["needs_attention"] = {
        "flagged": True,
        "flagged_at": "2026-08-12T12:40:00+00:00",
        "note": None,
        "updated_at": "2026-08-12T12:40:00+00:00",
    }
    return out


def test_an_undone_reject_does_not_delete_the_shot_it_restored() -> None:
    """Ctrl+Z restores the marker but writes no compensating event.

    ``Audit.tsx``'s ``undo`` puts the marker back and ``performSave`` ships
    ``sessionEventsRef.current`` verbatim, so the stale ``marker_rejected``
    is saved alongside the shot it rejected. Measured against the pre-fix
    merge: local ['cand-1', 'cand-2', 'cand-3'] merged to
    ['cand-1', 'cand-3'] with no note - the desktop deletes a shot from its
    own document and pushes the loss.
    """
    shots = [_id_shot("cand-1", 1, 6.0, 1), _id_shot("cand-2", 2, 6.4, 2), _id_shot("cand-3", 3, 6.9, 3)]
    base = _id_doc(copy.deepcopy(shots))
    local = _id_doc(
        copy.deepcopy(shots),
        [_ev("marker_rejected", "cand-2", "2026-08-12T12:30:00Z")],
    )
    remote = _triaged(base)

    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)

    assert [s["id"] for s in result.doc["shots"]] == ["cand-1", "cand-2", "cand-3"]
    assert result.notes == []


def test_a_superseded_detection_run_is_not_readopted_from_remote() -> None:
    """``_run_shot_detect`` with ``reset`` writes no ``marker_*`` events.

    It sets ``doc["shots"] = []`` and reseeds from the new candidates, so
    the superseded run's shots carry no verdict at all and were adopted back
    unconditionally as remote-only additions. Measured against the pre-fix
    merge: a re-detection that went 8 candidates -> 5 came out of the merge
    with 8 shots and no note. ``cand-<n>`` also aliases two different
    physical shots across runs - remote's cand-4 is at a different time
    from local's - so this is not even a stale copy of the same shot.
    """
    previous_run = [_id_shot(f"cand-{n}", n, 6.0 + 0.3 * n, n) for n in range(1, 9)]
    new_run = [_id_shot(f"cand-{n}", n, 6.1 + 0.5 * n, n) for n in range(1, 6)]
    base = _id_doc(copy.deepcopy(previous_run))
    local = _id_doc(copy.deepcopy(new_run))
    remote = _triaged(base)

    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)

    assert [s["id"] for s in result.doc["shots"]] == ["cand-1", "cand-2", "cand-3", "cand-4", "cand-5"]
    assert [s["time"] for s in result.doc["shots"]] == [t["time"] for t in new_run]
    assert result.notes == []


def test_a_genuinely_new_remote_shot_is_still_adopted() -> None:
    """The corroboration must not close the door the branch opened.

    A shot the phone added is absent from base, so it is adopted whether or
    not its ``marker_added_manual`` event survived the round trip.
    """
    base = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    local = _id_doc([_id_shot("cand-4", 1, 6.0, 4)])
    remote = _id_doc([_id_shot("cand-4", 1, 6.0, 4), _id_shot("manual-x", 2, 6.5)])

    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)

    assert [s["id"] for s in result.doc["shots"]] == ["cand-4", "manual-x"]


def test_a_stale_delete_stays_inert_after_it_has_been_pushed() -> None:
    """The self-consistency property, pinned.

    Once the poisoned log has been pushed, ``remote["audit_events"]`` carries
    the stale ``marker_rejected`` too - so "the log knows about it" is true
    on both sides. ``remote_shots`` carries the shot with it, and that clause
    is what keeps it. A verdict must not become actionable merely by being
    echoed back.
    """
    shots = [_id_shot("cand-1", 1, 6.0, 1), _id_shot("cand-2", 2, 6.4, 2)]
    events = [_ev("marker_rejected", "cand-2", "2026-08-12T12:30:00Z")]
    base = _id_doc(copy.deepcopy(shots), copy.deepcopy(events))
    local = _id_doc(copy.deepcopy(shots), copy.deepcopy(events))
    remote = _triaged(base)

    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)

    assert [s["id"] for s in result.doc["shots"]] == ["cand-1", "cand-2"]


def test_a_corroborated_delete_still_acts() -> None:
    """The guard narrows the rule; it must not disable it.

    Remote dropped the shot and its log says why, so the delete acts - this
    is the ordinary phone-deletes-a-shot path and it has to keep working.
    """
    base = _id_doc([_id_shot("cand-4", 1, 6.0, 4), _id_shot("cand-9", 2, 6.5, 9)])
    local = _id_doc([_id_shot("cand-4", 1, 6.0, 4), _id_shot("cand-9", 2, 6.5, 9)])
    remote = _id_doc(
        [_id_shot("cand-4", 1, 6.0, 4)],
        [_ev("marker_rejected", "cand-9", "2026-08-12T12:30:00Z")],
    )

    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)

    assert [s["id"] for s in result.doc["shots"]] == ["cand-4"]


# -- the tripwire note has to be true ------------------------------------


def _detector_shot(**over: object) -> dict:
    """One detector-seeded shot as the desktop writes it."""
    shot = {
        "id": "cand-1",
        "shot_number": 1,
        "candidate_number": 1,
        "time": 6.0,
        "ms_after_beep": 1000,
        "source": "detected",
        "confidence": 0.91,
        "ensemble_votes": 3,
        "ensemble_score": 0.84,
        "apriori_boost": 0.0,
    }
    shot.update(over)
    return shot


def _spa_shot(**over: object) -> dict:
    """The same shot as ``buildAuditJson`` (audit-doc.ts) round-trips it.

    Only shot_number/candidate_number/time/ms_after_beep/source (plus note
    and id when present) survive; the detector's own fields are dropped.
    """
    shot = {
        "id": "cand-1",
        "shot_number": 1,
        "candidate_number": 1,
        "time": 6.0,
        "ms_after_beep": 1000,
        "source": "detected",
    }
    shot.update(over)
    return shot


def test_spa_shaped_remote_shot_is_not_a_tripwire() -> None:
    """A plain, correct phone save of a detector-seeded stage must not note.

    ``buildAuditJson`` (audit-doc.ts) never sends ensemble_votes/
    ensemble_score/apriori_boost/confidence at all - it only round-trips
    shot_number/candidate_number/time/ms_after_beep/source(/note/id). Those
    fields are simply absent from the remote shot, not changed on it, so
    the residue comparison - now projected to keys present on both sides -
    must not read the omission as a change. This used to fire the note on
    every shot of every detector-seeded stage a phone saved; that was the
    bug (issue found on this branch).
    """
    base = _id_doc([_detector_shot()])
    local = _id_doc([_detector_shot()])
    remote = _id_doc([_spa_shot(time=6.05, ms_after_beep=1050)])

    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)

    assert not any("non-whitelisted" in n for n in result.notes)
    # The desktop-only fields still stand untouched, and the phone's
    # whitelisted nudge still lands.
    merged = result.doc["shots"][0]
    assert merged["confidence"] == 0.91 and merged["ensemble_votes"] == 3
    assert merged["time"] == 6.05


def test_tripwire_still_fires_when_a_shared_desktop_field_actually_changes() -> None:
    """The fix narrows the comparison to shared keys; it must not gut it.

    Here ``confidence`` is present on *both* sides (unlike a real SPA
    payload) but the remote's value genuinely differs from base's - a
    surface that does send the field but changes it must still be caught.
    Only ``confidence`` differs, so only it is named; the untouched
    detector fields must not appear.
    """
    base = _id_doc([_detector_shot()])
    local = _id_doc([_detector_shot()])
    remote = _id_doc([_detector_shot(confidence=0.45)])

    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)

    note = next(n for n in result.notes if "non-whitelisted" in n)
    assert "should make this impossible" not in note
    assert "shot cand-1" in note
    assert "confidence" in note
    for field in ("apriori_boost", "ensemble_score", "ensemble_votes"):
        assert field not in note, note
    # Named, not merged: local's value still stands.
    assert result.doc["shots"][0]["confidence"] == 0.91


def test_the_tripwire_note_names_a_doc_level_field_too() -> None:
    base = _id_doc([_detector_shot()])
    local = _id_doc([_detector_shot()])
    remote = _id_doc([_detector_shot()])
    remote["detection"] = "manual"

    result = merge_audit_doc(base, local, remote, doc_key="stage1", local_ts=_LOCAL_TS, remote_ts=_REMOTE_TS)

    note = next(n for n in result.notes if "non-whitelisted" in n)
    assert "document: detection" in note
    assert "should make this impossible" not in note
