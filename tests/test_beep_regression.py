"""Every fixture the beep detector ranks right stays ranked right (#949).

The unit tests in ``test_beep_detect.py`` run synthetic tones in quiet
buffers, where the failure modes of real audio (gunshots louder than the
beep, in-band chatter merging into its run) cannot occur. This runs the
detector over the labeled calibration corpus instead and pins each
fixture that ``baseline.json`` records as correct at top-1.

Only the correct ones are pinned: the whole corpus costs ~220 CPU-seconds,
the pinned set about half. A detector change that fixes a fixture should
regenerate the baseline so the fix is pinned too::

    uv run python scripts/eval_beep_detector.py --track clip \\
        --json tests/fixtures/beep_calibration/baseline.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.beep_calibration import load_manifest
from splitsmith.beep_detect import detect_beep, load_audio
from splitsmith.config import BeepDetectConfig, BeepRankerConfig

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CALIBRATION_DIR = FIXTURES_DIR / "beep_calibration"


def _pinned() -> list[str]:
    baseline = json.loads((CALIBRATION_DIR / "baseline.json").read_text())
    return sorted(r["stem"] for r in baseline["results"] if r["track"] == "clip" and r["correct_top1"])


def test_the_baseline_pins_a_meaningful_set() -> None:
    """A baseline regenerated where the clips failed to load pins nothing,
    and every case below would vanish without a failure."""
    assert len(_pinned()) >= 65


@pytest.mark.parametrize("stem", _pinned())
def test_a_fixture_ranked_right_stays_ranked_right(stem: str) -> None:
    entry = next(e for e in load_manifest(CALIBRATION_DIR / "manifest.yaml").fixtures if e.stem == stem)
    audio, sample_rate = load_audio(FIXTURES_DIR / entry.clip_wav)

    detected = detect_beep(audio, sample_rate, BeepDetectConfig()).time

    error_ms = (detected - entry.ground_truth_in_clip) * 1000.0
    assert abs(error_ms) <= entry.tolerance_ms, f"{stem}: top-1 is {error_ms:+.1f} ms from the labeled beep"


def _pinned_heuristic() -> list[str]:
    baseline = json.loads((CALIBRATION_DIR / "baseline_heuristic.json").read_text())
    return sorted(r["stem"] for r in baseline["results"] if r["track"] == "clip" and r["correct_top1"])


@pytest.mark.parametrize("stem", _pinned_heuristic()[:5])
def test_the_heuristic_ranker_still_reproduces_today(stem: str) -> None:
    """``ranker: heuristic`` is the escape hatch; it must stay today's detector."""
    entry = next(e for e in load_manifest(CALIBRATION_DIR / "manifest.yaml").fixtures if e.stem == stem)
    audio, sample_rate = load_audio(FIXTURES_DIR / entry.clip_wav)
    config = BeepDetectConfig(ranker=BeepRankerConfig(ranker="heuristic"))

    detected = detect_beep(audio, sample_rate, config).time

    assert abs(detected - entry.ground_truth_in_clip) * 1000.0 <= entry.tolerance_ms
