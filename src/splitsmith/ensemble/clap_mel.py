"""NumPy mel-spectrogram for CLAP's slim runtime path (issue #377 -- doc 02).

The ONNX CLAP audio trunk takes ``input_features`` of shape
``(B, 1, 1001, 64)`` -- the log-mel produced by HuggingFace's
``ClapFeatureExtractor`` for a 1-s window at 48 kHz padded with the
"repeatpad" policy to 10 s. The slim wheel doesn't import
``transformers`` at runtime, so this module reproduces the same tensor
using only numpy + librosa (BSD-3-Clause), keeping the splitsmith
license surface MIT-only.

Validation against ``ClapFeatureExtractor`` on the reference probe:
L_inf delta = 3.8e-6 (the doc 05 tolerance is 1e-4).

Parameters are pinned to ``laion/clap-htsat-unfused``. Any change to
the upstream model requires rerunning ``scripts/export_clap_onnx.py``
and re-validating parity.
"""

from __future__ import annotations

import functools

import librosa
import numpy as np

# Pinned to the CLAP-HTSAT-unfused config. Sourced from
# ``transformers.ClapFeatureExtractor.from_pretrained(CLAP_MODEL_ID)``.
CLAP_SAMPLING_RATE = 48000
CLAP_N_FFT = 1024
CLAP_HOP_LENGTH = 480
CLAP_N_MELS = 64
CLAP_FMIN = 50.0
CLAP_FMAX = 14000.0
CLAP_NB_MAX_SAMPLES = 480000  # 10-s window the model expects
CLAP_NB_MAX_FRAMES = 1001
CLAP_MEL_FLOOR = 1e-10
CLAP_DB_REFERENCE = 1.0


@functools.lru_cache(maxsize=1)
def _slaney_mel_filter() -> np.ndarray:
    """Return the slaney-normalised mel filter bank, shape ``(N_FFT//2+1, N_MELS)``.

    Equivalent to ``ClapFeatureExtractor.mel_filters_slaney``. Computed
    once per process via ``librosa.filters.mel``; the output is cached
    so per-detection calls don't re-derive it.
    """
    # librosa returns ``(n_mels, n_freqs)``; transformers stores
    # ``(n_freqs, n_mels)``. Transpose so downstream matmuls match.
    return librosa.filters.mel(
        sr=CLAP_SAMPLING_RATE,
        n_fft=CLAP_N_FFT,
        n_mels=CLAP_N_MELS,
        fmin=CLAP_FMIN,
        fmax=CLAP_FMAX,
        htk=False,
        norm="slaney",
    ).T.astype(np.float64)


@functools.lru_cache(maxsize=1)
def _periodic_hann_window() -> np.ndarray:
    """Periodic Hann window of length ``CLAP_N_FFT``.

    ``ClapFeatureExtractor`` uses ``window_function(..., 'hann', periodic=True)``,
    which is ``numpy.hanning(N+1)[:-1]`` rather than the symmetric
    ``numpy.hanning(N)`` librosa picks by default.
    """
    return np.hanning(CLAP_N_FFT + 1)[:-1].astype(np.float64)


def repeatpad(audio: np.ndarray, *, target_samples: int = CLAP_NB_MAX_SAMPLES) -> np.ndarray:
    """Pad ``audio`` to ``target_samples`` by repeating it (CLAP's policy).

    ``ClapFeatureExtractor``'s default ``padding="repeatpad"`` mode tiles
    the audio array until the buffer is full, then truncates to exactly
    ``target_samples``. Zero-padding would push silence frames to the
    -100 dB floor and shift the per-prompt similarity distribution.

    Accepts 1-D ``(T,)`` float arrays; returns float32. Audio already
    longer than ``target_samples`` is truncated.
    """
    flat = np.asarray(audio, dtype=np.float32).reshape(-1)
    if flat.size == 0:
        return np.zeros(target_samples, dtype=np.float32)
    if flat.size >= target_samples:
        return flat[:target_samples]
    reps = (target_samples + flat.size - 1) // flat.size
    tiled = np.tile(flat, reps)
    return tiled[:target_samples]


def log_mel_input_features(audio: np.ndarray) -> np.ndarray:
    """Compute one CLAP ``input_features`` tensor from a 1-D audio array.

    Returns shape ``(1, 1, CLAP_NB_MAX_FRAMES, CLAP_N_MELS)`` float32,
    bit-equivalent (within 1e-5 dB) to
    ``ClapProcessor(audio=[audio], sampling_rate=48000, return_tensors="np")``.

    The slim ONNX runtime feeds this directly to
    ``onnxruntime.InferenceSession.run``.
    """
    padded = repeatpad(audio)

    # librosa.stft uses center=True + pad_mode='reflect' by default, which
    # matches transformers.audio_utils.spectrogram for these parameters.
    stft = librosa.stft(
        padded.astype(np.float64),
        n_fft=CLAP_N_FFT,
        hop_length=CLAP_HOP_LENGTH,
        window=_periodic_hann_window(),
        center=True,
        pad_mode="reflect",
    )

    power = (np.abs(stft) ** 2).astype(np.float64)
    mel = _slaney_mel_filter().T @ power  # (n_mels, n_frames)
    mel = np.maximum(mel, CLAP_MEL_FLOOR)
    log_mel = 10.0 * np.log10(mel / CLAP_DB_REFERENCE)

    # ClapFeatureExtractor returns (T, mels); reshape to the model's
    # expected (B, 1, T, mels) with B=1.
    frames = log_mel.T[:CLAP_NB_MAX_FRAMES]
    return frames[None, None, :, :].astype(np.float32)


def batch_log_mel_input_features(batch: np.ndarray) -> np.ndarray:
    """Vectorised :func:`log_mel_input_features` for a ``(N, T)`` batch.

    Returns shape ``(N, 1, CLAP_NB_MAX_FRAMES, CLAP_N_MELS)`` float32.
    Loops in Python -- librosa's STFT isn't batched -- but the cost is
    dominated by the FFT per row, not loop overhead.
    """
    audio = np.asarray(batch, dtype=np.float32)
    if audio.ndim == 1:
        return log_mel_input_features(audio)
    rows = [log_mel_input_features(row)[0] for row in audio]
    return np.stack(rows, axis=0)
