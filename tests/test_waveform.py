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
