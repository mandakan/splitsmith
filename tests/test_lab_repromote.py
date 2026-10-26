"""A re-promote keeps the lab's labels (#1459).

The match audit holds no lab labels, so before the carry a re-promote with
``overwrite=True`` wrote it over the fixture and erased every false-positive
reason, kept-shot subclass, walk decision and history entry on it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.lab.core import PromoteRequest, carry_lab_labels, promote_stage_to_fixture
from splitsmith.lab.promote import write_promoted_fixture

SLUG = "stage-shots-test-2026-stage1-stestabcd"
CAMERA = {"id": "unknown", "mount": "head", "position": "shooter", "audio_source": "internal"}


def _audit(shots: list[float], candidates: list[float], **extra: object) -> dict:
    return {
        "stage_number": 1,
        "beep_time": 1.0,
        "stage_time_seconds": 12.0,
        "shots": [{"shot_number": i + 1, "time": t} for i, t in enumerate(shots)],
        "_candidates_pending_audit": {
            "candidates": [{"candidate_number": i + 1, "time": t} for i, t in enumerate(candidates)]
        },
        **extra,
    }


def _promote(tmp_path: Path, audit: dict, *, window: tuple[float, float] = (10.0, 30.0)):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(exist_ok=True)
    audit_json = audit_dir / "stage1.json"
    audit_json.write_text(json.dumps(audit), encoding="utf-8")
    audit_wav = audit_dir / "stage1.wav"
    audit_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")
    rec = promote_stage_to_fixture(
        PromoteRequest(
            audit_json_path=audit_json,
            audit_wav_path=audit_wav,
            fixture_slug=SLUG,
            fixtures_root=tmp_path / "fx",
            overwrite=True,
            source_video="/raw/clip.MOV",
            fixture_window_in_source=window,
            camera=CAMERA,
        )
    )
    return rec, json.loads((tmp_path / "fx" / f"{SLUG}.json").read_text(encoding="utf-8"))


def _label(tmp_path: Path, *, reasons: dict[str, str], subclasses: dict[float, str], **extra: object) -> None:
    """Hand-label the promoted fixture the way the lab's detail page does."""
    path = tmp_path / "fx" / f"{SLUG}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["_candidates_pending_audit"]["labels_by_time"] = reasons
    for shot in data["shots"]:
        if shot["time"] in subclasses:
            shot["subclass"] = subclasses[shot["time"]]
    data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_reasons_and_subclasses_survive_moves_within_the_tolerance(tmp_path: Path) -> None:
    _promote(tmp_path, _audit([2.0, 3.0], [1.5, 2.0, 2.5, 3.0, 4.0]))
    _label(
        tmp_path,
        reasons={"1.500": "echo", "2.500": "cross_bay", "4.000": "steel_ring"},
        subclasses={2.0: "paper", 3.0: "steel"},
    )

    rec, fixture = _promote(tmp_path, _audit([2.02, 2.985], [1.52, 2.02, 2.48, 2.985, 4.019]))

    assert fixture["_candidates_pending_audit"]["labels_by_time"] == {
        "1.520": "echo",
        "2.480": "cross_bay",
        "4.019": "steel_ring",
    }
    assert [s.get("subclass") for s in fixture["shots"]] == ["paper", "steel"]
    assert rec.label_carry is not None
    assert (rec.label_carry.reasons_carried, rec.label_carry.subclasses_carried) == (3, 2)
    assert rec.label_carry.reasons_dropped == rec.label_carry.subclasses_dropped == []
    assert (rec.n_labeled_rejects, rec.n_labeled_shots) == (3, 2)


def test_a_far_or_now_kept_reason_is_dropped_and_reported(tmp_path: Path) -> None:
    _promote(tmp_path, _audit([2.0], [1.5, 2.0, 2.5]))
    _label(tmp_path, reasons={"1.500": "echo", "2.500": "cross_bay"}, subclasses={})

    # 1.5 moved 40 ms; the 2.5 candidate is now a kept shot.
    rec, fixture = _promote(tmp_path, _audit([2.0, 2.5], [1.54, 2.0, 2.5]))

    assert fixture["_candidates_pending_audit"].get("labels_by_time") == {}
    dropped = {(d.time, d.label, d.why) for d in rec.label_carry.reasons_dropped}
    assert dropped == {(1.5, "echo", "no candidate"), (2.5, "cross_bay", "now a kept shot")}


def test_a_removed_shot_drops_its_subclass_and_an_added_one_gets_none(tmp_path: Path) -> None:
    _promote(tmp_path, _audit([2.0, 3.0], [2.0, 3.0]))
    _label(tmp_path, reasons={}, subclasses={2.0: "paper", 3.0: "steel"})

    rec, fixture = _promote(tmp_path, _audit([2.0, 5.0], [2.0, 5.0]))

    assert [s.get("subclass") for s in fixture["shots"]] == ["paper", None]
    assert [(d.time, d.label, d.why) for d in rec.label_carry.subclasses_dropped] == [
        (3.0, "steel", "no shot")
    ]


def test_a_moved_trim_window_keeps_labels_on_their_sounds(tmp_path: Path) -> None:
    """A re-reviewed beep moves the trim, so every clip time moves with it."""
    _promote(tmp_path, _audit([2.0], [1.5, 2.0]), window=(10.0, 30.0))
    _label(tmp_path, reasons={"1.500": "echo"}, subclasses={2.0: "paper"})

    # The window starts 0.3 s later: the same sounds sit 0.3 s earlier.
    rec, fixture = _promote(tmp_path, _audit([1.7], [1.2, 1.7]), window=(10.3, 30.3))

    assert fixture["_candidates_pending_audit"]["labels_by_time"] == {"1.200": "echo"}
    assert fixture["shots"][0]["subclass"] == "paper"


def test_walk_decisions_and_history_carry_and_the_old_signoff_does_not(tmp_path: Path) -> None:
    _promote(tmp_path, _audit([2.0], [2.0]))
    walk = {
        "kind": "walk_decided",
        "payload": {"method": "walk-1", "stop": "m1", "state": "shot", "time": 2.0},
    }
    _label(
        tmp_path,
        reasons={},
        subclasses={},
        audit_events=[walk, {"kind": "save", "payload": {}}],
        history=[{"at": "2026-10-01T00:00:00+00:00", "action": "review-confirm", "tool_version": "x"}],
        review={"status": "reviewed", "confirmed_at": "2026-10-01T00:00:00+00:00"},
    )

    rec, fixture = _promote(
        tmp_path, _audit([2.0], [2.0], audit_events=[{"kind": "save", "payload": {"n": 1}}])
    )

    assert fixture["audit_events"] == [{"kind": "save", "payload": {"n": 1}}, walk]
    assert [h["action"] for h in fixture["history"]] == ["review-confirm", "re-promoted"]
    assert fixture["history"][-1]["details"]["walk_decisions_carried"] == 1
    assert "review" not in fixture
    assert rec.review_status == "reviewed"  # a match-mode re-audit is a hand audit
    assert rec.walk_decisions == 1


def test_a_first_promote_is_unchanged(tmp_path: Path) -> None:
    audit = _audit([2.0], [2.0])
    rec, fixture = _promote(tmp_path, audit)

    assert rec.label_carry is None
    assert "history" not in fixture
    fixture.pop("promoted_at")
    assert {k: v for k, v in fixture.items() if k in audit} == audit


def test_carry_tolerates_a_fixture_without_candidates() -> None:
    old = {"shots": [], "_candidates_pending_audit": {"labels_by_time": {"1.000": "echo"}}}
    new: dict = {"shots": []}
    carry = carry_lab_labels(old, new)
    assert new["_candidates_pending_audit"]["labels_by_time"] == {"1.000": "echo"}
    assert carry.reasons_carried == 1


def test_a_resnap_keeps_the_reasons_of_the_snap_it_replaces(tmp_path: Path) -> None:
    def trim(src: Path, dst: Path, start: float, end: float) -> None:
        dst.write_bytes(b"RIFF")

    def snap(candidates: list[float]) -> dict:
        return {
            "beep_time": 30.0,
            "shots": [{"time": 32.0, "subclass": "paper"}],
            "_candidates_pending_audit": {
                "candidates": [{"time": t} for t in candidates],
                "labels_by_time": {},
            },
            "history": [{"action": "promote-from-anchor"}],
            "review": {"status": "needs_review"},
        }

    kwargs = {
        "promotion_report": {"counts": {}},
        "secondary_wav": tmp_path / "src.wav",
        "fixtures_root": tmp_path / "fx",
        "slug": "stage-shots-m-2026-stage1-a-cam",
        "trim_wav": trim,
    }
    out = write_promoted_fixture(fixture_data=snap([31.0, 32.0]), **kwargs)
    first = json.loads(out.read_text(encoding="utf-8"))
    first["_candidates_pending_audit"]["labels_by_time"] = {"6.000": "echo"}  # 31.0 on a clip from 25.0
    first["shots"][0]["subclass"] = "steel"
    out.write_text(json.dumps(first), encoding="utf-8")

    out = write_promoted_fixture(fixture_data=snap([31.01, 32.0]), overwrite=True, **kwargs)
    written = json.loads(out.read_text(encoding="utf-8"))
    report = json.loads(
        (tmp_path / "fx" / "stage-shots-m-2026-stage1-a-cam-promotion-report.json").read_text(
            encoding="utf-8"
        )
    )

    assert written["_candidates_pending_audit"]["labels_by_time"] == {"6.010": "echo"}
    assert written["shots"][0]["subclass"] == "steel"
    assert written["review"] == {"status": "needs_review"}
    assert [h["action"] for h in written["history"]] == [
        "promote-from-anchor",
        "promote-from-anchor",
        "re-promoted",
    ]
    assert report["label_carry"]["reasons_carried"] == 1


@pytest.mark.parametrize("bad", ["not json", "[]"])
def test_an_unreadable_old_fixture_is_overwritten_as_before(tmp_path: Path, bad: str) -> None:
    (tmp_path / "fx").mkdir()
    (tmp_path / "fx" / f"{SLUG}.json").write_text(bad, encoding="utf-8")
    rec, _ = _promote(tmp_path, _audit([2.0], [2.0]))
    assert rec.label_carry is None
