"""Unit tests for ``splitsmith.waveform`` peak extraction + caching."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from splitsmith import waveform


def test_compute_peaks_returns_bins_in_unit_range() -> None:
    rng = np.random.default_rng(42)
    audio = rng.uniform(-0.3, 0.3, size=48_000).astype(np.float32)

    result = waveform.compute_peaks(audio, sample_rate=48_000, bins=200)

    assert result.bins == 200
    assert len(result.peaks) == 200
    assert result.duration == pytest.approx(1.0)
    assert all(0.0 <= p <= 1.0 for p in result.peaks)
    # Random audio should fill peaks roughly evenly; max is exactly 1.0 by
    # construction (some bin contains the global max).
    assert max(result.peaks) == pytest.approx(1.0)


def test_compute_peaks_silent_audio_is_all_zero() -> None:
    audio = np.zeros(4_800, dtype=np.float32)
    result = waveform.compute_peaks(audio, sample_rate=48_000, bins=64)
    assert result.peaks == [0.0] * 64
    assert result.duration == pytest.approx(0.1)


def test_compute_peaks_localized_burst_is_localized_in_peaks() -> None:
    audio = np.zeros(48_000, dtype=np.float32)
    # Burst at 25-30% of the clip.
    audio[12_000:14_400] = 0.8
    result = waveform.compute_peaks(audio, sample_rate=48_000, bins=100)

    # Bins covering 25-30% should be hot; bins outside should be silent.
    hot = [i for i, v in enumerate(result.peaks) if v > 0.5]
    assert hot, "expected non-empty hot region"
    assert min(hot) >= 20
    assert max(hot) <= 35
    # Outside the burst, peaks must be zero.
    assert all(result.peaks[i] == 0.0 for i in range(0, 20))
    assert all(result.peaks[i] == 0.0 for i in range(35, 100))


def test_compute_peaks_rejects_non_mono() -> None:
    stereo = np.zeros((2, 1000), dtype=np.float32)
    with pytest.raises(ValueError, match="1-D"):
        waveform.compute_peaks(stereo, sample_rate=48_000, bins=10)


def test_compute_peaks_rejects_zero_bins() -> None:
    audio = np.zeros(100, dtype=np.float32)
    with pytest.raises(ValueError, match="bins"):
        waveform.compute_peaks(audio, sample_rate=48_000, bins=0)


def test_ensure_peaks_caches_and_reuses(tmp_path: Path) -> None:
    audio = np.zeros(48_000, dtype=np.float32)
    audio[10_000:11_000] = 0.5
    wav = tmp_path / "stage1_primary.wav"
    sf.write(wav, audio, 48_000)

    first = waveform.ensure_peaks(wav, bins=80)
    cache_file = waveform.cache_path(wav, 80)
    assert cache_file.exists()
    cache_mtime = cache_file.stat().st_mtime

    # Second call must not rewrite the cache file.
    second = waveform.ensure_peaks(wav, bins=80)
    assert cache_file.stat().st_mtime == cache_mtime
    assert second.peaks == first.peaks


def test_ensure_peaks_invalidates_when_audio_newer(tmp_path: Path) -> None:
    audio = np.zeros(48_000, dtype=np.float32)
    audio[1_000:2_000] = 0.5
    wav = tmp_path / "stage1_primary.wav"
    sf.write(wav, audio, 48_000)

    first = waveform.ensure_peaks(wav, bins=50)

    # Rewrite audio (different content); bump mtime to invalidate cache.
    audio2 = np.zeros(48_000, dtype=np.float32)
    audio2[40_000:41_000] = 0.5
    sf.write(wav, audio2, 48_000)
    import os
    import time

    future = time.time() + 5
    os.utime(wav, (future, future))

    second = waveform.ensure_peaks(wav, bins=50)
    # The hot region moved from bin ~1 to bin ~42; peaks must differ.
    assert second.peaks != first.peaks


def test_cached_returns_none_on_miss(tmp_path: Path) -> None:
    audio = np.zeros(1_000, dtype=np.float32)
    wav = tmp_path / "stage1_primary.wav"
    sf.write(wav, audio, 48_000)
    assert waveform.cached(wav, bins=10) is None


def test_peaks_saved_from_an_older_wav_are_never_served_for_a_newer_one(tmp_path: Path) -> None:
    """The race on hosted (2026-10-13): one request computed peaks from a
    19.1 s WAV while another replaced it with the stage's 29.3 s WAV; the
    peaks were saved after the swap, so their file was newer than the WAV
    and were served for it ever after. The phone drew 19.1 s of rows under
    29.3 s of audio. A saved result is trusted only for the WAV it was
    computed from, whatever the timestamps say."""
    import os

    wav = tmp_path / "stage8_cam_x_audit.wav"
    sf.write(wav, np.zeros(19 * 1000, dtype=np.float32), 1000)
    old = waveform.ensure_peaks(wav, bins=50)
    assert old.duration == pytest.approx(19.0)
    cache = waveform.cache_path(wav, 50)
    # The stage's real WAV replaces it, stamped older than the saved peaks.
    sf.write(wav, np.zeros(29 * 1000, dtype=np.float32), 1000)
    older = cache.stat().st_mtime - 10
    os.utime(wav, (older, older))

    assert waveform.ensure_peaks(wav, bins=50).duration == pytest.approx(29.0)


def test_peaks_saved_without_a_source_record_are_computed_again(tmp_path: Path) -> None:
    """Results saved before the source was recorded cannot be trusted; they
    are recomputed once, which heals a stage already serving stale peaks."""
    import json

    wav = tmp_path / "stage1_primary.wav"
    sf.write(wav, np.zeros(5 * 1000, dtype=np.float32), 1000)
    cache = waveform.cache_path(wav, 10)
    cache.write_text(json.dumps({"duration": 2.0, "sample_rate": 1000, "bins": 10, "peaks": [0.0] * 10}))

    assert waveform.ensure_peaks(wav, bins=10).duration == pytest.approx(5.0)


def test_the_source_record_stays_out_of_the_api_payload(tmp_path: Path) -> None:
    wav = tmp_path / "stage1_primary.wav"
    sf.write(wav, np.zeros(1000, dtype=np.float32), 1000)
    result = waveform.ensure_peaks(wav, bins=10)
    assert "source_size" not in result.model_dump() and "source_mtime_ns" not in result.model_dump()
    # ...while the saved file carries it, so the next call is a hit.
    assert waveform.cached(wav, bins=10) is not None


def test_window_peaks_cover_only_the_window_at_full_resolution(tmp_path: Path) -> None:
    """The beep picker draws a few seconds at 1 ms over a source that can run
    minutes: peaks of a slice, placed by ``start``."""
    audio = np.zeros(10 * 1000, dtype=np.float32)
    audio[4_050:4_060] = 0.9  # a click at 4.05 s
    wav = tmp_path / "stage1_cam_x.wav"
    sf.write(wav, audio, 1000)

    win = waveform.ensure_peaks_window(wav, bins=200, start=4.0, end=4.2)
    assert win.duration == pytest.approx(0.2)
    assert win.start == pytest.approx(4.0)
    assert len(win.peaks) == 200
    loud = [i for i, p in enumerate(win.peaks) if p > 0.5]
    assert loud and loud[0] == 50  # 4.05 s is 50 ms into the window, 1 ms bins


def test_window_peaks_clamp_to_the_audio(tmp_path: Path) -> None:
    wav = tmp_path / "stage1_cam_x.wav"
    sf.write(wav, np.zeros(2 * 1000, dtype=np.float32), 1000)
    win = waveform.ensure_peaks_window(wav, bins=100, start=-1.0, end=5.0)
    assert win.start == pytest.approx(0.0)
    assert win.duration == pytest.approx(2.0)
