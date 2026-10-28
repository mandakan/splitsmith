"""Speech mask proposals (lab/speech_mask.py, #1778): the pure steps, and the
VAD framing through a stand-in session (no model download in tests)."""

from __future__ import annotations

import numpy as np
import pytest

from splitsmith.config import SpeechMaskConfig
from splitsmith.lab import speech_mask as sm

CFG = SpeechMaskConfig()
HOP = sm.VAD_HOP_S


def _probs(spans: list[tuple[float, float]], seconds: float, level: float = 0.9) -> np.ndarray:
    p = np.zeros(int(seconds / HOP), dtype=np.float32)
    for s, e in spans:
        p[int(s / HOP) : int(e / HOP)] = level
    return p


def test_a_region_opens_at_threshold_and_is_padded() -> None:
    regions = sm.speech_regions(_probs([(1.0, 2.0)], 5.0), CFG)
    assert len(regions) == 1
    assert regions[0].start == pytest.approx(1.0 - CFG.pad_s, abs=HOP)
    assert regions[0].end == pytest.approx(2.0 + CFG.pad_s, abs=HOP)


def test_hysteresis_keeps_a_region_open_between_the_thresholds() -> None:
    p = _probs([(1.0, 1.5)], 5.0)
    p[int(1.5 / HOP) : int(2.0 / HOP)] = (CFG.threshold + CFG.end_threshold) / 2
    (region,) = sm.speech_regions(p, CFG)
    assert region.end == pytest.approx(2.0 + CFG.pad_s, abs=HOP)


def test_a_blip_shorter_than_min_speech_is_dropped() -> None:
    assert sm.speech_regions(_probs([(1.0, 1.0 + CFG.min_speech_s / 2)], 5.0), CFG) == []


def test_close_regions_merge() -> None:
    gap = CFG.merge_gap_s / 2
    regions = sm.speech_regions(_probs([(1.0, 1.5), (1.5 + gap, 2.0)], 5.0), CFG)
    assert len(regions) == 1


def test_padding_stays_inside_the_clip() -> None:
    (region,) = sm.speech_regions(_probs([(0.0, 1.0)], 1.0), CFG, duration=1.0)
    assert region.start == 0.0 and region.end == 1.0


def test_protected_windows_surround_each_time_and_merge() -> None:
    half = CFG.protect_ms / 1000
    windows = sm.protected_windows([2.0, 2.0 + half, 5.0], CFG)
    assert [(w.start, w.end) for w in windows] == [
        (pytest.approx(2.0 - half), pytest.approx(2.0 + 2 * half)),
        (pytest.approx(5.0 - half), pytest.approx(5.0 + half)),
    ]


def test_a_mask_never_covers_a_shot_and_the_overlap_is_reported() -> None:
    speech = [sm.MaskRegion(start=1.0, end=3.0)]
    guard = sm.protected_windows([2.0], CFG)
    proposal = sm.propose_masks(speech, guard, CFG)
    for region in proposal.regions:
        assert region.end <= guard[0].start or region.start >= guard[0].end
    assert proposal.needs_decision
    assert proposal.masked_seconds == pytest.approx(2.0 - 2 * CFG.protect_ms / 1000, abs=1e-3)


def test_speech_away_from_shots_is_masked_whole() -> None:
    speech = [sm.MaskRegion(start=0.5, end=1.5)]
    proposal = sm.propose_masks(speech, sm.protected_windows([4.0], CFG), CFG)
    assert [(r.start, r.end) for r in proposal.regions] == [(0.5, 1.5)]
    assert not proposal.needs_decision


def test_a_piece_too_short_after_the_cut_is_dropped() -> None:
    half = CFG.protect_ms / 1000
    speech = [sm.MaskRegion(start=2.0 - half - 0.05, end=2.5)]
    proposal = sm.propose_masks(speech, sm.protected_windows([2.0], CFG), CFG)
    assert all(r.start >= 2.0 + half for r in proposal.regions)


def test_labelled_times_are_the_shots_and_the_beep() -> None:
    fixture = {"beep_time": 5.0, "shots": [{"time": 6.1}, {"time": None}, {"time": 7.0}]}
    assert sorted(sm.labelled_times(fixture)) == [5.0, 6.1, 7.0]


class _EnergySession:
    """Stands in for the VAD graph: 'speech' when the frame is loud. Checks
    the Silero v5 input contract: 64 samples of context plus a 512 frame."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, _outputs, feeds):  # noqa: ANN001, ANN201
        x = feeds["input"]
        assert x.shape == (1, 64 + sm.VAD_FRAME)
        assert feeds["state"].shape == (2, 1, 128)
        assert int(feeds["sr"]) == sm.VAD_SAMPLE_RATE
        self.calls += 1
        loud = float(np.abs(x[0, 64:]).mean()) > 0.1
        return np.array([[0.9 if loud else 0.0]], dtype=np.float32), feeds["state"]


def test_probabilities_run_one_call_per_frame_in_order() -> None:
    audio = np.zeros(sm.VAD_FRAME * 64, dtype=np.float32)
    audio[sm.VAD_FRAME * 32 : sm.VAD_FRAME * 42] = 0.5  # frames 32..41, from 1.024 s
    session = _EnergySession()
    probs = sm.speech_probabilities(audio, session)
    assert session.calls == 64 == len(probs)
    assert list(np.flatnonzero(probs > 0.5)) == list(range(32, 42))


def test_audio_is_resampled_to_16k() -> None:
    audio = np.zeros(48000, dtype=np.float32)
    assert len(sm.to_vad_rate(audio, 48000)) == 16000


def test_the_pinned_artifact_matches_its_url_version() -> None:
    assert "v5.1.2" in sm.VAD_ARTIFACT.url and sm.METHOD.endswith("5.1.2")
