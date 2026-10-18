"""Breakdown's regions merge per region by id (spec
2026-10-18-breakdown-hosted-sync-design): one test per row of the spec's
table, the lane rule and ``events_seeded``."""

import json
from datetime import UTC, datetime

from splitsmith.sync.merge import merge_audit_doc

T_OLD = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
T_NEW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _r(
    rid: str, kind: str = "movement", start: float = 1.0, end: float = 2.0, source: str = "manual"
) -> dict:
    return {"id": rid, "kind": kind, "start": start, "end": end, "source": source}


def _doc(*regions: dict, seeded: int | None = None) -> dict:
    doc: dict = {"shots": [], "audit_events": [], "events": list(regions)}
    if seeded is not None:
        doc["events_seeded"] = seeded
    return doc


def _merge(base, local, remote, *, local_ts=T_OLD, remote_ts=T_NEW):
    return merge_audit_doc(
        base, local, remote, doc_key="audit/anna/3", local_ts=local_ts, remote_ts=remote_ts
    )


def _ids(result) -> list[str]:
    return [e["id"] for e in result.doc["events"]]


def test_a_region_changed_on_the_phone_reaches_the_desktop():
    moved = _r("evt-1", start=1.2)
    r = _merge(_doc(_r("evt-1")), _doc(_r("evt-1")), _doc(moved))
    assert r.doc["events"] == [moved]
    assert r.conflicts == [] and r.notes == [] and r.changed_vs_local


def test_a_desktop_edit_survives_a_pull_of_the_old_copy():
    moved = _r("evt-1", end=2.5)
    r = _merge(_doc(_r("evt-1")), _doc(moved), _doc(_r("evt-1")))
    assert r.doc["events"] == [moved]
    assert not r.changed_vs_local


def test_changed_on_both_sides_the_newer_doc_wins_surfaced():
    r = _merge(_doc(_r("evt-1")), _doc(_r("evt-1", start=1.1)), _doc(_r("evt-1", start=1.3)))
    assert r.doc["events"] == [_r("evt-1", start=1.3)]
    assert [(c.unit, c.winner) for c in r.conflicts] == [("event:evt-1", "remote")]


def test_a_delete_on_either_side_holds():
    r = _merge(
        _doc(_r("evt-1"), _r("evt-2", "reload")),
        _doc(_r("evt-2", "reload")),
        _doc(_r("evt-1"), _r("evt-2", "reload")),
    )
    assert _ids(r) == ["evt-2"]
    r = _merge(
        _doc(_r("evt-1"), _r("evt-2", "reload")), _doc(_r("evt-1"), _r("evt-2", "reload")), _doc(_r("evt-1"))
    )
    assert _ids(r) == ["evt-1"]
    assert r.conflicts == []


def test_an_edit_beats_a_delete_on_the_other_side():
    edited = _r("evt-1", end=2.4)
    r = _merge(_doc(_r("evt-1")), _doc(), _doc(edited))
    assert r.doc["events"] == [edited]
    assert [(c.unit, c.winner) for c in r.conflicts] == [("event:evt-1", "remote")]
    r = _merge(_doc(_r("evt-1")), _doc(edited), _doc())
    assert r.doc["events"] == [edited]
    assert [(c.unit, c.winner) for c in r.conflicts] == [("event:evt-1", "local")]


def test_additions_on_each_side_are_both_kept():
    r = _merge(_doc(), _doc(_r("evt-1")), _doc(_r("evt-2", "reload")))
    assert _ids(r) == ["evt-1", "evt-2"]
    assert r.conflicts == []


def test_the_same_region_added_on_both_sides_is_one():
    r = _merge(_doc(), _doc(_r("evt-1")), _doc(_r("evt-1")))
    assert _ids(r) == ["evt-1"] and r.notes == []


def test_one_id_minted_twice_is_two_regions_remote_renamed():
    r = _merge(_doc(), _doc(_r("evt-3", "reload", 5.0, 6.0)), _doc(_r("evt-3", "movement", 1.0, 2.0)))
    assert r.doc["events"] == [_r("evt-3", "reload", 5.0, 6.0), _r("evt-4", "movement", 1.0, 2.0)]
    assert r.notes == ["audit/anna/3: region evt-3 was added on both sides; remote's is now evt-4"]


def test_regions_from_two_sides_that_overlap_in_a_lane_keep_the_newer_docs():
    desk = _r("evt-1", "movement", 1.0, 3.0)
    phone = _r("evt-2", "movement", 2.5, 4.0)
    r = _merge(_doc(), _doc(desk), _doc(phone), local_ts=T_OLD, remote_ts=T_NEW)
    assert _ids(r) == ["evt-2"]
    assert [(c.unit, c.winner) for c in r.conflicts] == [("event:evt-1 (overlaps evt-2)", "remote")]
    r = _merge(_doc(), _doc(desk), _doc(phone), local_ts=T_NEW, remote_ts=T_OLD)
    assert _ids(r) == ["evt-1"]


def test_a_second_draw_from_the_other_side_is_a_collision_however_far_apart():
    r = _merge(_doc(), _doc(_r("evt-1", "draw", 0.0, 1.0)), _doc(_r("evt-2", "draw", 5.0, 6.0)))
    assert _ids(r) == ["evt-2"]


def test_regions_in_different_lanes_may_overlap():
    r = _merge(_doc(), _doc(_r("evt-1", "draw", 0.3, 1.1)), _doc(_r("evt-2", "movement", 0.3, 1.1)))
    assert _ids(r) == ["evt-1", "evt-2"] and r.conflicts == []


def test_a_remote_that_never_carried_regions_leaves_local_alone():
    remote = {"shots": [], "audit_events": []}
    r = _merge(_doc(_r("evt-1")), _doc(_r("evt-1"), _r("evt-2", "reload")), remote)
    assert _ids(r) == ["evt-1", "evt-2"] and not r.changed_vs_local


def test_no_base_merges_as_a_union_and_deletes_nothing():
    r = _merge(None, _doc(_r("evt-1")), _doc(_r("evt-2", "reload")))
    assert _ids(r) == ["evt-1", "evt-2"]


def test_events_seeded_takes_the_larger_side():
    r = _merge(_doc(), _doc(seeded=3), _doc(seeded=4))
    assert r.doc["events_seeded"] == 4
    r = _merge(_doc(), _doc(seeded=4), _doc())
    assert r.doc["events_seeded"] == 4


def test_regions_are_not_reported_as_a_non_whitelisted_change():
    r = _merge(_doc(_r("evt-1")), _doc(_r("evt-1")), _doc(_r("evt-1", start=1.4), seeded=4))
    assert r.notes == []


def test_a_pull_that_brings_a_confirmed_reload_reclassifies_its_gap(tmp_path):
    """The desktop classifies the merged doc against the merged regions, as
    every audit writer does: the gap under a reload confirmed on the phone
    becomes ``reload`` here too."""
    import splitsmith.sync.run as run_mod
    from splitsmith.sync.pull import RemoteDoc
    from splitsmith.sync.state import SyncState

    from .test_ui_server import _seed_match_export_project

    _, project_root = _seed_match_export_project(tmp_path, stage_count=1)
    audit = project_root / "shooters" / "me" / "audit" / "stage1.json"
    shots = [
        {
            "id": f"cand-{n}",
            "candidate_number": n,
            "shot_number": n,
            "ms_after_beep": ms,
            "time": 5 + ms / 1000,
        }
        for n, ms in ((1, 1000), (2, 1250), (3, 4250), (4, 4500))
    ]
    local = {"shots": shots, "audit_events": [], "events": []}
    run_mod.classify_intervals_in_dicts(local["shots"], run_mod.auto_classify_config())
    assert local["shots"][2]["interval_class"] != "reload"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(local), encoding="utf-8")
    remote = json.loads(json.dumps(local))
    remote["events"] = [_r("evt-1", "reload", 1.4, 4.0)]
    rd = RemoteDoc(kind="audit", slug="me", stage_number=1, version=2, updated_at=T_NEW)
    run_mod._apply_pull(project_root, "m", SyncState(), [(rd, remote, 2)])
    merged = json.loads(audit.read_text(encoding="utf-8"))
    assert merged["events"] == remote["events"]
    assert merged["shots"][2]["interval_class"] == "reload"
