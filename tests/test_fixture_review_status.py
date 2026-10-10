"""Fixture review status (#1363): which fixtures a person has checked on their own audio."""

import json
from pathlib import Path

import numpy as np
import pytest

from splitsmith.ensemble import fixtures as fx_module
from splitsmith.fixture_schema import review_status
from splitsmith.lab.promote import write_promoted_fixture

FIXTURES = Path(__file__).parent / "fixtures"


def test_a_hand_audited_fixture_is_reviewed():
    assert review_status({"beep_time": 1.0, "shots": []}) == "reviewed"


def test_a_fixture_snapped_from_an_anchor_needs_review():
    assert review_status({"anchor": {"fixture_slug": "a"}}) == "needs_review"
    history = [{"action": "promote-from-anchor"}]
    assert review_status({"history": history}) == "needs_review"


def test_the_review_block_wins():
    assert review_status({"anchor": {"fixture_slug": "a"}, "review": {"status": "reviewed"}}) == "reviewed"
    assert review_status({"review": {"status": "needs_review"}}) == "needs_review"


def test_an_unknown_status_falls_back_to_the_history():
    assert review_status({"anchor": {"fixture_slug": "a"}, "review": {"status": "maybe"}}) == "needs_review"


def test_the_corpus_marks_exactly_the_snapped_fixtures():
    fx_module.all_fixtures.cache_clear()
    snapped = set()
    for p in FIXTURES.glob("stage-shots-*.json"):
        if p.stem.endswith("-report"):
            continue
        d = json.loads(p.read_text())
        if d.get("anchor") or any(h.get("action") == "promote-from-anchor" for h in d.get("history") or []):
            snapped.add(p.stem)
    assert len(snapped) >= 12
    needs = {f.stem for f in fx_module.all_fixtures() if f.review_status == "needs_review"}
    assert needs == snapped


def test_reviewed_only_leaves_out_the_unreviewed(tmp_path, monkeypatch):
    primary, snapped = "stage-shots-m-2026-stage1-a", "stage-shots-m-2026-stage1-a-cam"
    for name, extra in ((primary, {}), (snapped, {"anchor": {"fixture_slug": "a"}})):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"beep_time": 1.0, "shots": [{"time": 2.0}], **extra})
        )
    monkeypatch.setattr(fx_module, "FIXTURES_DIR", tmp_path)
    fx_module.all_fixtures.cache_clear()
    try:
        assert set(fx_module.fixture_stems()) == {primary, snapped}
        assert fx_module.fixture_stems(reviewed_only=True) == [primary]
    finally:
        fx_module.all_fixtures.cache_clear()


def test_write_promoted_fixture_rebases_to_the_clip(tmp_path):
    cuts = []

    def trim(src: Path, dst: Path, start: float, end: float) -> None:
        cuts.append((start, end))
        dst.write_bytes(b"RIFF")

    data = {
        "beep_time": 30.0,
        "shots": [{"time": 32.0}, {"time": 40.0}],
        "_candidates_pending_audit": {"candidates": [{"time": 31.0}]},
        "review": {"status": "needs_review", "derived_from": "a", "reviewed_at": None},
    }
    out = write_promoted_fixture(
        fixture_data=data,
        promotion_report={"counts": {}},
        secondary_wav=tmp_path / "src.wav",
        fixtures_root=tmp_path / "fx",
        slug="stage-shots-m-2026-stage1-a-cam",
        trim_wav=trim,
        source_video="/Volumes/X9/raw/m/IMG_1.MOV",
    )
    assert cuts == [(25.0, 45.0)]
    written = json.loads(out.read_text())
    assert written["beep_time"] == 5.0
    assert [s["time"] for s in written["shots"]] == [7.0, 15.0]
    assert written["_candidates_pending_audit"]["candidates"][0]["time"] == 6.0
    assert written["fixture_window_in_source"] == [25.0, 45.0]
    assert written["source_video"] == "/Volumes/X9/raw/m/IMG_1.MOV"
    assert written["review"]["status"] == "needs_review"
    assert (tmp_path / "fx" / "stage-shots-m-2026-stage1-a-cam-promotion-report.json").exists()
    assert np.isclose(written["beep_time"] + 25.0, 30.0)


def test_write_promoted_fixture_refuses_to_overwrite(tmp_path):
    root = tmp_path / "fx"
    root.mkdir()
    (root / "s.json").write_text("{}")
    with pytest.raises(FileExistsError):
        write_promoted_fixture(
            fixture_data={"beep_time": 1.0, "shots": []},
            promotion_report={},
            secondary_wav=tmp_path / "src.wav",
            fixtures_root=root,
            slug="s",
            trim_wav=lambda *_a: None,
        )
