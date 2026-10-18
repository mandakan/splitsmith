"""Finding a stage's beep from another angle's confirmed beep (#1442), on real
corpus audio: HFO Masters 2026 stage 3, a GoPro and the head cam, both beeps
reviewed on spectrograms (2026-10-17)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.beep_cross import MIN_CONTRAST, place_beep
from splitsmith.beep_detect import load_audio

FIXTURES = Path(__file__).parent / "fixtures"
GOPRO = "stage-shots-hfo-masters-2026-stage3-s97dcec94-go3s"
HEADCAM = "stage-shots-hfo-masters-2026-stage3-s97dcec94"
OTHER_STAGE = "stage-shots-hfo-masters-2026-stage12-s97dcec94"


def _clip(stem: str):
    wav = FIXTURES / f"{stem}.wav"
    if not wav.exists():
        pytest.skip(f"{wav.name} not fetched (scripts/fixture_audio.py fetch)")
    audio, sr = load_audio(wav)
    beep = float(json.loads((FIXTURES / f"{stem}.json").read_text())["beep_time"])
    return audio, sr, beep


def test_the_gopros_beep_finds_the_headcams_within_5_ms() -> None:
    ref, ref_sr, ref_beep = _clip(GOPRO)
    other, other_sr, truth = _clip(HEADCAM)
    placed = place_beep(ref, ref_sr, ref_beep, other, other_sr)
    assert placed is not None and placed.confident
    assert placed.tone is not None
    assert abs(placed.time - truth) <= 0.005


def test_another_stages_clip_is_not_a_confident_match() -> None:
    ref, ref_sr, ref_beep = _clip(GOPRO)
    other, other_sr, _ = _clip(OTHER_STAGE)
    placed = place_beep(ref, ref_sr, ref_beep, other, other_sr)
    assert placed is None or placed.contrast < MIN_CONTRAST
