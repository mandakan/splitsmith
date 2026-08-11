"""Three-way merge engine conflict matrix (bidirectional sync slice)."""

from datetime import UTC, datetime

from splitsmith.sync.merge import merge_audit_doc, merge_project_doc

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
    s = {"shot_number": n, "time": float(n), "interval_class": "split", "interval_class_source": "auto"}
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


def test_coach_conflict_lww_and_shot_membership_is_local():
    base = _audit([_shot(1)], [])
    local = _audit([_shot(1, coaching_note="mine"), _shot(2)], [])
    remote = _audit([_shot(1, coaching_note="theirs")], [])
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["shots"][0]["coaching_note"] == "theirs"  # remote newer
    assert len(r.doc["shots"]) == 2  # local shot list authoritative
    assert len(r.conflicts) == 1


def test_audit_non_whitelisted_remote_change_noted_local_wins():
    base = _audit([_shot(1)], [])
    local = _audit([_shot(1)], [])
    remote = _audit([_shot(1, time=9.9)], [])  # shot time is not whitelisted
    r = merge_audit_doc(base, local, remote, doc_key="audit/anna/3", local_ts=T_OLD, remote_ts=T_NEW)
    assert r.doc["shots"][0]["time"] == 1.0
    assert any("non-whitelisted" in n for n in r.notes)


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
