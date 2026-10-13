"""Peak extraction for the audit screen waveform.

The audit screen renders a static waveform of the primary's cached audio,
overlaid with markers (issue #15). Computing peaks server-side keeps the
client cheap: a single JSON GET returns ``bins`` normalized magnitudes
(0..1) and the audio duration. The browser draws bars on a canvas.

Peaks cache as JSON next to the source audio:

    <audio_dir>/stage<N>_primary.peaks-<bins>.json

The cache is keyed by ``(audio_path, bins)`` and invalidated by mtime: a
re-extracted WAV (newer mtime) triggers re-computation. Bins are
parameterized so the same audio can serve waveforms at different widths
without paying full decode cost twice.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from . import beep_detect


class PeaksResult(BaseModel):
    """Wire shape returned by :func:`compute_peaks` and the peaks endpoint."""

    duration: float
    sample_rate: int
    bins: int
    peaks: list[float]
    #: The WAV these peaks were computed from (size, mtime_ns), taken before
    #: it was read. Cache bookkeeping, never part of the API payload.
    source_size: int | None = Field(default=None, exclude=True)
    source_mtime_ns: int | None = Field(default=None, exclude=True)


def compute_peaks(audio: np.ndarray, sample_rate: int, bins: int) -> PeaksResult:
    """Bucket ``audio`` into ``bins`` peak magnitudes normalized to [0, 1].

    Each bin's value is ``max(abs(samples_in_bin)) / max(abs(audio))`` so the
    visual scale is independent of the recording's absolute level. Silent
    audio returns all zeros (no division by zero).
    """
    if bins < 1:
        raise ValueError(f"bins must be >= 1, got {bins}")
    if audio.ndim != 1:
        raise ValueError(f"audio must be 1-D, got shape {audio.shape}")

    n = audio.shape[0]
    duration = float(n) / float(sample_rate) if sample_rate > 0 else 0.0

    if n == 0:
        return PeaksResult(
            duration=0.0,
            sample_rate=int(sample_rate),
            bins=bins,
            peaks=[0.0] * bins,
        )

    abs_audio = np.abs(audio).astype(np.float32, copy=False)
    global_max = float(abs_audio.max())

    # Edges via linspace: handles n < bins gracefully (some bins may be empty).
    edges = np.linspace(0, n, bins + 1, dtype=np.int64)
    out = np.zeros(bins, dtype=np.float32)
    for i in range(bins):
        start, end = int(edges[i]), int(edges[i + 1])
        if end > start:
            out[i] = float(abs_audio[start:end].max())

    if global_max > 0.0:
        out = out / global_max

    return PeaksResult(
        duration=duration,
        sample_rate=int(sample_rate),
        bins=bins,
        peaks=out.tolist(),
    )


def cache_path(audio_path: Path, bins: int) -> Path:
    """Return where the peaks JSON for ``(audio_path, bins)`` should live."""
    return audio_path.with_name(f"{audio_path.stem}.peaks-{bins}.json")


def cached(audio_path: Path, bins: int) -> PeaksResult | None:
    """Return cached peaks for ``audio_path`` at ``bins`` resolution, or ``None``.

    A saved result is valid only for the WAV it was computed from: its
    recorded size and mtime must match the file now. Timestamps alone are
    not enough (2026-10-13): a request computing peaks from an older WAV
    while another replaced it saved its result after the swap, newer than
    the new WAV, and it was served for that WAV ever after. A result saved
    without the record is a miss, so old caches are recomputed once.
    """
    cf = cache_path(audio_path, bins)
    if not cf.exists():
        return None
    try:
        st = audio_path.stat()
        hit = PeaksResult.model_validate_json(cf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if hit.source_size != st.st_size or hit.source_mtime_ns != st.st_mtime_ns:
        return None
    return hit


def ensure_peaks(audio_path: Path, bins: int) -> PeaksResult:
    """Return peaks for ``audio_path`` at ``bins`` resolution, computing if needed.

    Decodes the WAV via :func:`beep_detect.load_audio`, computes peaks, and
    persists the JSON next to the audio. Subsequent calls for the same
    ``(audio_path, bins)`` are O(file read).
    """
    hit = cached(audio_path, bins)
    if hit is not None and hit.bins == bins:
        return hit

    # Recorded before reading: a WAV replaced while this runs leaves a record
    # that no longer matches, and the next call recomputes.
    st = audio_path.stat()
    audio, sr = beep_detect.load_audio(audio_path)
    result = compute_peaks(audio, sr, bins)
    result.source_size = st.st_size
    result.source_mtime_ns = st.st_mtime_ns

    cf = cache_path(audio_path, bins)
    cf.parent.mkdir(parents=True, exist_ok=True)
    # A per-process temporary name and an atomic rename, so two requests
    # writing the same cache never interleave.
    tmp = cf.with_name(f"{cf.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
    record = {**result.model_dump(mode="json"), "source_size": st.st_size, "source_mtime_ns": st.st_mtime_ns}
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    tmp.replace(cf)
    return result
