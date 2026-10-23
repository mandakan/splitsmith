"""Audit documents on one clock, the clip's (#1497). Cases from Blacksmith
Handgun Open 2026, shooter s_ce10fa76."""

from __future__ import annotations

from splitsmith.audit_clock import (
    carry_to_clip,
    classify,
    clip_camera_problem,
    rebase_to_clip,
    stamp_clip_beep,
    stamp_clip_video,
)


def _doc(beep: float, times: list[float], cands: list[float] | None = None) -> dict:
    return {
        "beep_time": beep,
        "shots": [{"time": t, "ms_after_beep": round((t - beep) * 1000)} for t in times],
        "_candidates_pending_audit": {"candidates": [{"time": t} for t in cands or []]},
        "events": [{"kind": "reload", "start": 4.0, "end": 5.2}],
    }


def test_a_source_clock_audit_moves_onto_the_clip() -> None:
    # Stage 5: audited in May on the full source, beep 19.244; the trim puts
    # the beep at 5.0, so every marker drew 14.244 s late.
    doc = _doc(19.244, [21.0, 21.8, 22.5], cands=[21.0, 23.1])
    assert classify(doc, 5.0) == "shifted"
    out, kind = rebase_to_clip(doc, 5.0)
    assert kind == "shifted"
    assert out["beep_time"] == 5.0
    assert [s["time"] for s in out["shots"]] == [6.756, 7.556, 8.256]
    assert [c["time"] for c in out["_candidates_pending_audit"]["candidates"]] == [6.756, 8.856]
    # Beep-relative things stay: the splits and the regions are unchanged.
    assert [s["ms_after_beep"] for s in out["shots"]] == [s["ms_after_beep"] for s in doc["shots"]]
    assert out["events"] == doc["events"]
    assert doc["beep_time"] == 19.244, "the input is never changed"


def test_a_mixed_audit_keeps_its_shots_and_takes_the_clip_beep() -> None:
    # Stage 1: re-audited on the phone; shots on the clip (6 s and on), beep
    # left at the source's 21.130, so every split read as before the beep.
    doc = _doc(21.130, [6.9, 7.6, 8.4])
    assert all(s["ms_after_beep"] < 0 for s in doc["shots"])
    out, kind = rebase_to_clip(doc, 5.0)
    assert kind == "beep_fixed"
    assert [s["time"] for s in out["shots"]] == [6.9, 7.6, 8.4]
    assert out["beep_time"] == 5.0
    assert [s["ms_after_beep"] for s in out["shots"]] == [1900, 2600, 3400]


def test_an_audit_on_the_clip_or_a_beep_under_the_pre_buffer_is_left_alone() -> None:
    # Stage 6: the beep (3.292) is under the 5 s pre-buffer, so the trim
    # starts at the source's first frame and the clocks agree.
    doc = _doc(3.292, [5.0, 5.6])
    assert rebase_to_clip(doc, 3.292) == (doc, "ok")
    assert rebase_to_clip(doc, None) == (doc, "ok")
    assert rebase_to_clip(None, 5.0) == (None, "ok")
    assert classify({"beep_time": None, "shots": []}, 5.0) == "ok"


def test_the_save_stamps_the_clip_beep_whatever_the_page_fell_back_to() -> None:
    # The desktop fell back to the primary's source beep while the clip's
    # audio had not loaded; the shots it saves are on the clip regardless.
    doc = _doc(21.130, [6.9, 7.6])
    assert stamp_clip_beep(doc, 5.0) is True
    assert doc["beep_time"] == 5.0
    assert [s["ms_after_beep"] for s in doc["shots"]] == [1900, 2600]
    assert stamp_clip_beep(doc, 5.0) is False, "already on the clip: nothing changes"
    assert stamp_clip_beep(doc, None) is False


def _carry(doc: dict, *, from_clip_beep: float | None, to_clip_beep: float) -> tuple[dict, float]:
    return carry_to_clip(
        doc,
        from_clip_beep=from_clip_beep,
        to_clip_beep=to_clip_beep,
        to_video_id="head",
        event_id="e1",
        ts="t",
    )


def test_a_carry_keeps_each_shot_its_time_from_the_beep() -> None:
    doc = {**_doc(5.0, [6.0, 6.4], cands=[5.8]), "clip_video_id": "phone"}
    out, shift = _carry(doc, from_clip_beep=5.0, to_clip_beep=3.25)
    assert shift == -1.75
    assert out["beep_time"] == 3.25
    assert [s["time"] for s in out["shots"]] == [4.25, 4.65]
    assert [c["time"] for c in out["_candidates_pending_audit"]["candidates"]] == [4.05]
    assert [s["ms_after_beep"] for s in out["shots"]] == [1000, 1400]
    assert out["events"] == doc["events"]
    assert out["clip_video_id"] == "head"
    assert out["audit_events"][-1] == {
        "id": "e1",
        "ts": "t",
        "kind": "clip_camera_changed",
        "payload": {"from_video_id": "phone", "to_video_id": "head", "shift_ms": -1750.0},
    }
    assert doc["beep_time"] == 5.0 and "audit_events" not in doc, "the input is never changed"


def test_a_carry_moves_a_source_clock_audit_from_where_audit_drew_it() -> None:
    # Saved before the old primary had a trim: source beep 19.244, clip beep 5.0.
    out, _shift = _carry(_doc(19.244, [21.0]), from_clip_beep=5.0, to_clip_beep=5.0)
    assert out["beep_time"] == 5.0
    assert [s["time"] for s in out["shots"]] == [6.756]


def test_a_carry_moves_a_shot_before_the_beep_with_the_rest() -> None:
    """rebase_to_clip reads a shot before the beep as a mixed document and
    moves only the beep; a carry is a plain shift whatever the shots are."""
    out, _shift = _carry(_doc(5.0, [4.9, 6.0]), from_clip_beep=5.0, to_clip_beep=8.0)
    assert [s["time"] for s in out["shots"]] == [7.9, 9.0]


def test_stamp_clip_video_says_whether_it_changed() -> None:
    doc: dict = {}
    assert stamp_clip_video(doc, "v1") is True
    assert stamp_clip_video(doc, "v1") is False
    assert stamp_clip_video(doc, None) is False
    assert doc == {"clip_video_id": "v1"}


def test_clip_camera_problem_names_a_stamped_other_camera() -> None:
    doc = {"shots": [{"time": 6.0}], "clip_video_id": "phone"}
    kw = {"primary_beep_confirmed": True, "confirmed_others": []}
    assert clip_camera_problem(doc, primary_video_id="phone", **kw) is None
    assert clip_camera_problem(doc, primary_video_id="head", **kw) == ("other_camera", "phone")


def test_clip_camera_problem_flags_the_stranded_shape_before_the_stamp() -> None:
    # Blacksmith stages 5 to 8: head cam primary, beep never confirmed; the
    # phone's confirmed, and the audit made on it.
    doc = {"shots": [{"time": 6.0}]}
    assert clip_camera_problem(
        doc, primary_video_id="head", primary_beep_confirmed=False, confirmed_others=["phone"]
    ) == ("suspect", "phone")
    assert (
        clip_camera_problem(
            doc, primary_video_id="head", primary_beep_confirmed=True, confirmed_others=["phone"]
        )
        is None
    )
    assert (
        clip_camera_problem(
            {"shots": []}, primary_video_id="head", primary_beep_confirmed=False, confirmed_others=["p"]
        )
        is None
    )
