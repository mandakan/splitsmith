"""Audit documents on one clock, the clip's (#1497). Cases from Blacksmith
Handgun Open 2026, shooter s_ce10fa76."""

from __future__ import annotations

from splitsmith.audit_clock import classify, rebase_to_clip, stamp_clip_beep


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
