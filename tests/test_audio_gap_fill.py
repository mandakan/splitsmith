"""``_extract_audio`` keeps a joined clip's audio on the video's clock.

Two clips joined with ffmpeg leave a hole in the audio timestamps; decoding
without padding it closed the hole, so the WAV came out short of the clip
(refused by ``wav_problem``) and every sample after the join sat early.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from splitsmith.ui.audio import _extract_audio
from tests.synthetic_media import build_gapped_audio_video, ffmpeg_available

pytestmark = pytest.mark.integration


def _read(wav: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(wav)) as w:
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
    return data, rate


def _peak_hz(samples: np.ndarray, rate: int, start_s: float, end_s: float) -> float:
    window = samples[int(start_s * rate) : int(end_s * rate)]
    spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window))))
    return float(np.fft.rfftfreq(len(window), 1 / rate)[int(np.argmax(spectrum))])


def test_hole_in_the_audio_is_padded_so_the_wav_matches_the_clip(tmp_path: Path) -> None:
    if not ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe not available")
    clip = build_gapped_audio_video(tmp_path / "joined.mp4")
    wav = tmp_path / "joined.wav"

    _extract_audio(clip, wav, 48000, "ffmpeg")

    samples, rate = _read(wav)
    # Audio ends at 3.4 s (the clip's 4.0 s of video outlasts it); a closed
    # hole would end at 2.8 s.
    assert len(samples) / rate == pytest.approx(3.4, abs=0.1)
    # Tones keep their place on the video's clock: 440 Hz before the join,
    # silence across the hole, 880 Hz from the second clip's start.
    assert _peak_hz(samples, rate, 0.2, 1.2) == pytest.approx(440, abs=15)
    assert np.abs(samples[int(1.6 * rate) : int(1.9 * rate)]).max() < 200
    assert _peak_hz(samples, rate, 2.2, 3.2) == pytest.approx(880, abs=15)
