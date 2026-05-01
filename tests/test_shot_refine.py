"""Tests for shot_refine: second-pass timing refinement on confirmed shots."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotRefineConfig
from splitsmith.shot_refine import refine_shot_time


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


def test_refine_recovers_stage3_reverb_anchor(fixtures_dir: Path) -> None:
    """Stage-3 cand #35 was generated 144 ms late on a reverb peak.
    Refinement must pull it back within ~5 ms of the audited onset."""
    audio, sr = load_audio(fixtures_dir / "stage-shots-blacksmith-2026-stage3.wav")
    audit_t = 12.5538
    detector_t = 12.6984

    result = refine_shot_time(audio, sr, detector_t, ShotRefineConfig())

    assert result.accepted, "expected refinement to fire on the reverb-anchor case"
    assert abs(result.time - audit_t) * 1000 < 10.0, (
        f"refined time {result.time:.4f} should be within 10 ms of audit {audit_t} "
        f"(drift {(result.time - audit_t) * 1000:+.1f} ms)"
    )
    assert result.confidence > 0.5


def test_refine_keeps_already_clean_shot(fixtures_dir: Path) -> None:
    """When the original anchor is already on a local envelope peak the
    refinement must NOT re-anchor (otherwise it pulls clean shots toward
    nearby reverb peaks and timing regresses)."""
    audio, sr = load_audio(fixtures_dir / "stage-shots-blacksmith-2026-stage3.wav")
    # Stage-3 audited shot t=2.5271 already coincides with cand #4 at the
    # same time (drift 0 ms in the refresh-script output).
    audit_t = 2.5271
    result = refine_shot_time(audio, sr, audit_t, ShotRefineConfig())
    assert not result.accepted, (
        f"refinement should not have fired on a clean shot; refined "
        f"to {result.time:.4f} (drift {(result.time - audit_t) * 1000:+.1f} ms)"
    )
    # When not accepted the time field equals the original.
    assert result.time == audit_t


def test_refine_at_audio_boundary_does_not_crash(fixtures_dir: Path) -> None:
    """Refining near the start of the audio (where the search window is
    truncated) must not crash and must return the original time."""
    audio, sr = load_audio(fixtures_dir / "stage-shots-blacksmith-2026-stage3.wav")
    result = refine_shot_time(audio, sr, 0.0, ShotRefineConfig())
    assert result.time == 0.0
    assert result.method.startswith("envelope")


def test_refine_rejects_negative_time() -> None:
    audio = np.zeros(48000, dtype=np.float32)
    with pytest.raises(ValueError):
        refine_shot_time(audio, 48000, -0.1, ShotRefineConfig())


def test_refine_rejects_non_mono() -> None:
    audio = np.zeros((48000, 2), dtype=np.float32)
    with pytest.raises(ValueError):
        refine_shot_time(audio, 48000, 0.5, ShotRefineConfig())


def test_refine_aic_method_runs(fixtures_dir: Path) -> None:
    """AIC method should not crash and should return a finite confidence
    even when the result is rejected (busy reverb backgrounds)."""
    audio, sr = load_audio(fixtures_dir / "stage-shots-blacksmith-2026-stage3.wav")
    cfg = ShotRefineConfig(method="aic")
    result = refine_shot_time(audio, sr, 12.6984, cfg)
    assert np.isfinite(result.confidence)
    assert result.method == "aic"
