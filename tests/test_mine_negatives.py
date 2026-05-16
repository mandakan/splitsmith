"""Tests for scripts/mine_negatives.py.

Coverage:
* the audited-stage-window mapping (fixture-time -> full-audio-time)
* end-to-end miner against a synthetic full WAV with transients placed
  inside and outside the stage window -- the inside one must be
  excluded; the outside ones must be tagged pre_beep / post_stage.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "mine_negatives.py"
    spec = importlib.util.spec_from_file_location("mine_negatives", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


miner = _load_module()


def test_stage_window_in_full_maps_to_full_audio_coords() -> None:
    audit = {
        "fixture_window_in_source": [22.4417, 67.2017],
        "beep_time": 0.5,
        "stage_time_seconds": 42.76,
    }
    sidecar = {"full_window_in_source": [0.0, 180.0]}
    beep_in_full, stage_end_in_full = miner._stage_window_in_full(audit, sidecar)
    # beep_in_source = 22.4417 + 0.5 = 22.9417; full_start = 0 -> beep_in_full = 22.9417.
    assert beep_in_full == pytest.approx(22.9417)
    # stage_end_in_full = beep_in_full + stage_time_seconds.
    assert stage_end_in_full == pytest.approx(22.9417 + 42.76)


def test_stage_window_extends_past_stage_time_when_a_shot_is_later() -> None:
    # Mirrors stage-shots-blacksmith-2026-stage3: the last audited shot lands
    # 0.62 s past stage_time_seconds. The exclusion window must cover that,
    # not just stage_time, or we'd mine a real shot as a post_stage negative.
    audit = {
        "fixture_window_in_source": [0.0, 50.0],
        "beep_time": 0.5,
        "stage_time_seconds": 11.43,
        "shots": [
            {"time": 1.0},
            {"time": 6.5},
            {"time": 12.55},  # last shot, 0.62 s after beep+stage_time
        ],
    }
    sidecar = {"full_window_in_source": [0.0, 60.0]}
    beep_in_full, stage_end_in_full = miner._stage_window_in_full(audit, sidecar)
    assert beep_in_full == pytest.approx(0.5)
    # last_shot_from_beep = 12.55 - 0.5 = 12.05 > stage_time 11.43 -- use the larger.
    assert stage_end_in_full == pytest.approx(0.5 + 12.05)


def test_stage_window_falls_back_to_stage_time_when_no_shots_audited() -> None:
    audit = {
        "fixture_window_in_source": [0.0, 50.0],
        "beep_time": 0.5,
        "stage_time_seconds": 10.0,
        # no "shots" key -- some audits may be empty
    }
    sidecar = {"full_window_in_source": [0.0, 60.0]}
    beep_in_full, stage_end_in_full = miner._stage_window_in_full(audit, sidecar)
    assert beep_in_full == pytest.approx(0.5)
    assert stage_end_in_full == pytest.approx(10.5)


def test_stage_window_handles_offset_full_extraction() -> None:
    # Wide WAV starts at t=10s in source coords (we extracted with --pre-pad).
    audit = {
        "fixture_window_in_source": [50.0, 100.0],
        "beep_time": 0.5,
        "stage_time_seconds": 30.0,
    }
    sidecar = {"full_window_in_source": [10.0, 200.0]}
    beep_in_full, stage_end_in_full = miner._stage_window_in_full(audit, sidecar)
    # beep at source-time 50.5; full_start at 10 -> beep_in_full = 40.5.
    assert beep_in_full == pytest.approx(40.5)
    assert stage_end_in_full == pytest.approx(70.5)


def _write_wav_with_transients(path: Path, sr: int, duration_s: float, peak_times_s: list[float]) -> None:
    """Synthesize a near-silent mono WAV with sharp Hann-shaped clicks at given times."""
    n = int(round(sr * duration_s))
    audio = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(0)
    audio += rng.normal(scale=0.001, size=n).astype(np.float32)  # tiny noise floor
    win_s = 0.020
    win_n = int(round(sr * win_s))
    # Half-Hann-shaped pulse with sharp leading edge and short broadband tail.
    envelope = np.hanning(win_n).astype(np.float32)
    pulse = envelope * 0.9
    for t in peak_times_s:
        idx = int(round(t * sr))
        lo = max(0, idx - win_n // 2)
        hi = min(n, lo + win_n)
        audio[lo:hi] = np.clip(audio[lo:hi] + pulse[: hi - lo], -1.0, 1.0)
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def test_mine_one_excludes_in_stage_and_tags_outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures_dir = tmp_path / "fixtures"
    full_dir = fixtures_dir / "full"
    cache_dir = fixtures_dir / ".cache"
    fixtures_dir.mkdir()
    full_dir.mkdir()
    cache_dir.mkdir()
    monkeypatch.setattr(miner, "FIXTURES_DIR", fixtures_dir)
    monkeypatch.setattr(miner, "FULL_DIR", full_dir)
    monkeypatch.setattr(miner, "CACHE_DIR", cache_dir)

    sr = 48000
    duration = 30.0
    # Pre-beep at 5s; in-stage at 12s (will be subtracted); post-stage at 25s.
    pre_t, in_stage_t, post_t = 5.0, 12.0, 25.0
    _write_wav_with_transients(
        full_dir / "stage-shots-x_full.wav",
        sr=sr,
        duration_s=duration,
        peak_times_s=[pre_t, in_stage_t, post_t],
    )

    # Stage from beep_in_full=10 to beep_in_full+stage_time=15 (5s window).
    audit = {
        "fixture_window_in_source": [10.0, 15.5],
        "beep_time": 0.0,  # beep at source-time 10.0
        "stage_time_seconds": 5.0,
    }
    sidecar = {
        "fixture_stem": "stage-shots-x",
        "full_window_in_source": [0.0, duration],
    }
    (fixtures_dir / "stage-shots-x.json").write_text(json.dumps(audit))
    (full_dir / "stage-shots-x_full.json").write_text(json.dumps(sidecar))

    rows = miner.mine_one(
        "stage-shots-x",
        exclusion_pre_pad=1.0,
        exclusion_post_pad=0.5,
        log=lambda _msg: None,
    )
    times = sorted(r["time_in_full"] for r in rows)
    tags = {round(r["time_in_full"], 1): r["region_tag"] for r in rows}

    # The in-stage transient at ~12s must be excluded.
    assert all(not (9.0 <= t <= 16.0) for t in times)
    # The pre_beep and post_stage transients should both survive and be tagged.
    assert any(abs(t - pre_t) < 0.05 for t in times), times
    assert any(abs(t - post_t) < 0.05 for t in times), times
    pre_tagged = [t for t, tag in tags.items() if tag == "pre_beep"]
    post_tagged = [t for t, tag in tags.items() if tag == "post_stage"]
    assert pre_tagged and all(t < 10.0 for t in pre_tagged)
    assert post_tagged and all(t > 15.0 for t in post_tagged)


def test_mine_all_writes_npz_and_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixtures_dir = tmp_path / "fixtures"
    full_dir = fixtures_dir / "full"
    cache_dir = fixtures_dir / ".cache"
    fixtures_dir.mkdir()
    full_dir.mkdir()
    cache_dir.mkdir()
    monkeypatch.setattr(miner, "FIXTURES_DIR", fixtures_dir)
    monkeypatch.setattr(miner, "FULL_DIR", full_dir)
    monkeypatch.setattr(miner, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(miner, "MINED_PATH", cache_dir / "_mined_negatives.npz")
    monkeypatch.setattr(miner, "REPORT_PATH", full_dir / "_mining_report.json")

    sr = 48000
    duration = 30.0
    _write_wav_with_transients(
        full_dir / "stage-shots-x_full.wav",
        sr=sr,
        duration_s=duration,
        peak_times_s=[5.0, 25.0],
    )
    audit = {
        "fixture_window_in_source": [10.0, 15.5],
        "beep_time": 0.0,
        "stage_time_seconds": 5.0,
    }
    sidecar = {
        "fixture_stem": "stage-shots-x",
        "full_window_in_source": [0.0, duration],
    }
    (fixtures_dir / "stage-shots-x.json").write_text(json.dumps(audit))
    (full_dir / "stage-shots-x_full.json").write_text(json.dumps(sidecar))

    rows = miner.mine_all(log=lambda _msg: None)
    assert rows
    npz = np.load(cache_dir / "_mined_negatives.npz", allow_pickle=True)
    assert set(npz.files) == {
        "fixture",
        "time_in_full",
        "confidence",
        "peak_amplitude",
        "region_tag",
    }
    report = json.loads((full_dir / "_mining_report.json").read_text())
    assert report["n_total"] == len(rows)
    assert "stage-shots-x" in report["per_fixture"]
