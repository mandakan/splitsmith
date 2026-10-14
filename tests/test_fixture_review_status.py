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


def test_the_corpus_marks_the_snapped_and_the_flagged_fixtures():
    fx_module.all_fixtures.cache_clear()
    expected = set()
    for p in FIXTURES.glob("stage-shots-*.json"):
        if p.stem.endswith("-report"):
            continue
        d = json.loads(p.read_text())
        snapped = d.get("anchor") or any(
            h.get("action") == "promote-from-anchor" for h in d.get("history") or []
        )
        status = (d.get("review") or {}).get("status")
        # A snap needs review until its review block says reviewed.
        if (snapped and status != "reviewed") or status == "needs_review":
            expected.add(p.stem)
    assert len(expected) >= 12
    needs = {f.stem for f in fx_module.all_fixtures() if f.review_status == "needs_review"}
    assert needs == expected


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


def test_a_promoted_shot_is_seated_on_its_rise_foot_on_the_fixture_clip(tmp_path):
    """A secondary's review is adjustments only (2026-10-14): the snapped
    shots land on their rise foot on the very clip the fixture keeps, which
    is what the review page's Seat reads, and say how far they moved."""
    import soundfile as sf

    from splitsmith.beep_detect import load_audio
    from splitsmith.rise_foot import peak_envelope, rise_foot

    sr = 48000

    def trim(src: Path, dst: Path, start: float, end: float) -> None:
        audio = np.zeros(int((end - start) * sr), dtype=np.float32) + 0.001
        # A shot rising at 32.0 s in the source: up in 3 ms, then a decay.
        i = int((32.0 - start) * sr)
        rise, tail = int(0.003 * sr), int(0.03 * sr)
        audio[i : i + rise] = np.linspace(0.05, 0.9, rise, dtype=np.float32)
        audio[i + rise : i + rise + tail] = 0.9 * np.exp(-np.arange(tail) / (0.01 * sr)).astype(np.float32)
        sf.write(dst, audio, sr)

    data = {
        "beep_time": 30.0,
        "anchor": {"fixture_slug": "a", "snap_window_ms": 60, "seated": True},
        "shots": [
            {"shot_number": 1, "time": 32.008, "source": "promoted", "snap_displacement_ms": 8.0},
            {"shot_number": 2, "time": 34.0, "source": "promoted", "snap_displacement_ms": 1.0},
        ],
    }
    out = write_promoted_fixture(
        fixture_data=data,
        promotion_report={"counts": {}},
        secondary_wav=tmp_path / "src.wav",
        fixtures_root=tmp_path / "fx",
        slug="s",
        trim_wav=trim,
    )
    written = json.loads(out.read_text())
    audio, rate = load_audio(out.with_suffix(".wav"))
    peaks, dur = peak_envelope(audio, rate)
    first, second = written["shots"]
    foot = rise_foot(peaks, dur, first["time"])
    assert foot is not None and abs(first["time"] - foot) < 1e-6
    assert first["seat_ms"] < 0
    assert first["time"] < 32.008 - 25.0
    # Silence near the second: left where the snap put it, no seat recorded.
    assert second["time"] == 34.0 - 25.0
    assert "seat_ms" not in second


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
