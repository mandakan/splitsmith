"""Per-candidate feature extractors for the 4-voter ensemble.

Three families of features:

* **Hand-crafted** -- pure ``numpy`` derivatives of the audio (RMS,
  attack, reverb-tail, multi-resolution envelope ratios). Cheap;
  computed on every call.
* **CLAP** -- per-prompt cosine similarity in CLAP's shared
  audio-text embedding space. Loaded once via ``load_clap_runtime``.
* **PANN** -- AudioSet ``Gunshot, gunfire`` class probability. Loaded
  once via ``load_pann_runtime``.

The CLAP prompt bank lives here (not in calibration.py) because the
trained GBDT classifier in voter C was fit on similarities to *these*
prompts, in this order. ``ensemble_calibration.json`` records which
prompt strings were used so loading checks the bank still matches.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import librosa
import numpy as np

from .agc_state import compute_agc_features

# Shot-positive prompts (used for the (shot - not-shot) differential
# voter B keys on, and as a column subset of voter C's feature vector).
CLAP_PROMPTS_SHOT: tuple[str, ...] = (
    "a single gunshot at close range",
    "a loud handgun shot recorded with a body-worn microphone",
    "a sharp pistol shot in an outdoor competition",
    "a rapid sequence of pistol shots",
)

# Negative / distractor prompts, ordered to match the on-disk CLAP cache
# the calibration script writes. Order matters for voter C's column
# layout; appending here without retraining will desync the model.
CLAP_PROMPTS_NOT_SHOT: tuple[str, ...] = (
    "a distant gunshot echo from another shooting bay",
    "ambient outdoor environment noise",
    "wind blowing into a microphone",
    "footsteps on gravel",
    "a person speaking",
    "a metallic clang from a steel target falling",
)

CLAP_PROMPTS: tuple[str, ...] = CLAP_PROMPTS_SHOT + CLAP_PROMPTS_NOT_SHOT

CLAP_SR = 48000  # CLAP-HTSAT native rate
CLAP_MODEL_ID = "laion/clap-htsat-unfused"
CLAP_WINDOW_S = 1.0  # 1 s window centred on each candidate

PANN_SR = 32000  # PANN CNN14 native rate
PANN_WINDOW_S = 1.0
PANN_GUNSHOT_CLASS_INDEX = 427  # AudioSet ontology

# BEATs (Microsoft, 2022) -- alternative Voter D backbone. Same AudioSet
# ontology so the gunshot class index is unchanged; native sample rate is
# 16 kHz vs PANN's 32 kHz. The checkpoint isn't bundled (Microsoft hosts
# the .pt directly); point ``SPLITSMITH_BEATS_CHECKPOINT`` at a downloaded
# checkpoint, or pass ``checkpoint_path=`` explicitly to ``load_beats_runtime``.
BEATS_SR = 16000
BEATS_WINDOW_S = 1.0
BEATS_GUNSHOT_CLASS_INDEX = 427  # AudioSet ontology, identical to PANN
BEATS_CHECKPOINT_ENV = "SPLITSMITH_BEATS_CHECKPOINT"

VOTER_D_BACKEND_PANN = "pann"
VOTER_D_BACKEND_BEATS = "beats"
VALID_VOTER_D_BACKENDS: tuple[str, ...] = (VOTER_D_BACKEND_PANN, VOTER_D_BACKEND_BEATS)

_HAND_FEATURE_NAMES: tuple[str, ...] = (
    "peak_amp",
    "confidence",
    "rms_pre",
    "rms_post",
    "rms_ratio",
    "attack",
    "gap_prev",
    "ms_after_beep",
    "tail_amp",
    "ratio_1_20",
    "ratio_5_20",
    "agc_state",
    "time_since_last_loud_event",
    "peak_floor_ratio",
    # Cross-bay discriminators (issue #108): close shots have a peaked
    # 1-4 kHz spectrum and low spectral flatness; distant / muffled shots
    # from neighbouring bays arrive flatter and lose the mid-band
    # dominance. Together these two features account for ~6 % of the
    # GBDT's split importance and halve the cross_bay+echo FP count in
    # threshold-only eval (see PR's ablation table).
    "spectral_flatness",
    "spectral_peak_ratio",
    # Test-time augmentation (issue #92): how many of 4 detector perturbations
    # (+/-2 dB amplitude, +/-5 ms time shift) plus the original produced a
    # candidate within 15 ms of this candidate's time. Range: 1.0 .. 5.0.
    # Real shots are robust to small perturbations; FPs barely clearing the
    # smoothed-envelope threshold drop out under one or more.
    "tta_agreement",
)

# Onset window for spectral features (issue #108): 50 ms gives ~20 Hz
# frequency resolution at 48 kHz, enough to separate the 0-500 Hz /
# 1-4 kHz / 8+ kHz bands.
_SPECTRAL_WINDOW_MS: float = 50.0
_BAND_LOW_HI: float = 500.0
_BAND_MID_LO: float = 1000.0
_BAND_MID_HI: float = 4000.0
_BAND_HIGH_LO: float = 8000.0

HAND_FEATURE_DIM: int = len(_HAND_FEATURE_NAMES)

# Camera-class one-hot block for Voter C (issue #139). Frozen vocabulary
# so the shipped GBDT artifact stays aligned with the runtime input;
# adding a new class requires bumping ``VOTER_C_FEATURE_DIM``, retraining,
# and reshipping. Order matters: the column index of each class must
# match between training (build_ensemble_artifacts.py) and runtime
# (api.py / voter_c_feature_matrix).
CAMERA_CLASS_FEATURE_NAMES: tuple[str, ...] = ("headcam", "handheld")
CAMERA_CLASS_FEATURE_DIM: int = len(CAMERA_CLASS_FEATURE_NAMES)
_CAMERA_CLASS_TO_INDEX: dict[str, int] = {
    name: idx for idx, name in enumerate(CAMERA_CLASS_FEATURE_NAMES)
}

VOTER_C_FEATURE_DIM: int = HAND_FEATURE_DIM + len(CLAP_PROMPTS) + 1 + CAMERA_CLASS_FEATURE_DIM


def camera_class_one_hot(camera_classes: list[str] | np.ndarray, n_rows: int) -> np.ndarray:
    """Build the camera-class one-hot block for Voter C.

    ``camera_classes`` may be a single class name (broadcast to all rows)
    or a per-row sequence. Unknown classes default to ``headcam`` so the
    runtime fallback path matches the calibration default class.
    """
    out = np.zeros((n_rows, CAMERA_CLASS_FEATURE_DIM), dtype=np.float64)
    if isinstance(camera_classes, str):
        idx = _CAMERA_CLASS_TO_INDEX.get(camera_classes, 0)
        if n_rows:
            out[:, idx] = 1.0
        return out
    for row, cls in enumerate(camera_classes):
        if row >= n_rows:
            break
        idx = _CAMERA_CLASS_TO_INDEX.get(str(cls), 0)
        out[row, idx] = 1.0
    return out


@dataclass
class ClapRuntime:
    """Pre-loaded CLAP model + pre-encoded text embeddings."""

    model: Any
    processor: Any
    text_embeddings: np.ndarray  # (P, D), L2-normalised


@dataclass
class PannRuntime:
    """Pre-loaded PANN audio tagger."""

    tagger: Any


@dataclass
class BeatsRuntime:
    """Pre-loaded BEATs audio tagger.

    ``model`` is the loaded ``BEATs`` instance from microsoft/unilm
    (or any object exposing a compatible ``extract_features`` /
    ``predict_logits`` surface; ``compute_beats_gunshot_probs`` keys on
    a callable ``predict_probs(batch_tensor) -> probs_tensor`` that the
    loader installs as a thin adapter over whichever upstream API the
    installed BEATs build provides). ``device`` is forwarded so callers
    that mock the runtime can skip torch entirely.
    """

    model: Any
    device: str = "cpu"


def _slice_window(audio: np.ndarray, sr: int, t: float, win_s: float) -> np.ndarray:
    """Centre a fixed-length window on ``t``; symmetric zero-pad if at edges."""
    target_len = int(round(sr * win_s))
    half = target_len // 2
    centre = int(round(t * sr))
    lo = max(0, centre - half)
    hi = min(audio.size, centre + (target_len - half))
    chunk = audio[lo:hi].astype(np.float32, copy=False)
    if chunk.size < target_len:
        pad = target_len - chunk.size
        left = pad // 2
        right = pad - left
        chunk = np.pad(chunk, (left, right), mode="constant")
    return chunk[:target_len]


def _smoothed_peak(seg: np.ndarray, win_ms: float, sr: int) -> float:
    """Peak of ``seg`` after a moving-average smoothing of width ``win_ms``."""
    if seg.size == 0:
        return 0.0
    w = max(1, int(round(win_ms * 1e-3 * sr)))
    if w >= seg.size:
        return float(seg.mean())
    k = np.ones(w, dtype=np.float64) / w
    return float(np.convolve(seg, k, mode="valid").max())


def _spectral_flatness_and_peak_ratio(seg: np.ndarray, sr: int) -> tuple[float, float]:
    """Spectral flatness + 1-4 kHz / (low + high) energy ratio over ``seg``.

    Cross-bay shots arrive through air + obstacles: their spectra are
    flatter and lose the 1-4 kHz dominance our close shots have. Both
    numbers fall to 0 on empty or sub-FFT-sized segments so the loop
    stays branchless.
    """
    if seg.size < 16:
        return 0.0, 0.0
    n = seg.size
    window = np.hanning(n)
    spec = np.abs(np.fft.rfft(seg.astype(np.float64) * window))
    power = spec * spec
    eps = 1e-12
    geo = float(np.exp(np.mean(np.log(power + eps))))
    arith = float(power.mean()) + eps
    flatness = geo / arith

    freqs = np.fft.rfftfreq(n, d=1.0 / sr)
    low = power[freqs < _BAND_LOW_HI].sum()
    mid = power[(freqs >= _BAND_MID_LO) & (freqs <= _BAND_MID_HI)].sum()
    high = power[freqs > _BAND_HIGH_LO].sum()
    peak_ratio = float(mid / (low + high + eps))
    return flatness, peak_ratio


def compute_hand_features(
    audio: np.ndarray,
    sample_rate: int,
    candidate_times: np.ndarray,
    beep_time: float,
    confidences: np.ndarray,
    peak_amplitudes: np.ndarray,
    tta_agreement: np.ndarray,
) -> np.ndarray:
    """Per-candidate hand-crafted feature matrix, shape ``(N, HAND_FEATURE_DIM)``.

    Mirrors the feature set the calibration script trains voter C on; any
    drift here desyncs the GBDT and silently degrades precision. If you
    add or reorder features, rebuild artifacts.

    ``tta_agreement`` is the per-candidate output of
    ``splitsmith.ensemble.tta.compute_tta_agreement`` (range 1..5). Required
    rather than defaulted so a calling site can't silently feed zeros into a
    GBDT that was trained against real agreement counts.
    """
    n = audio.size
    sorted_t = np.sort(candidate_times)
    out = np.zeros((len(candidate_times), HAND_FEATURE_DIM), dtype=np.float64)
    win = int(0.050 * sample_rate)
    pre10 = int(0.010 * sample_rate)
    abs_audio = None  # lazy-built below if needed

    agc = compute_agc_features(audio, sample_rate, candidate_times, peak_amplitudes)
    spectral_half = int(round(_SPECTRAL_WINDOW_MS * 1e-3 * sample_rate / 2.0))

    for k, t in enumerate(candidate_times):
        idx = int(round(float(t) * sample_rate))
        pre_lo, pre_hi = max(0, idx - win), idx
        post_lo, post_hi = idx, min(n, idx + win)
        rms_pre = (
            float(np.sqrt(np.mean(audio[pre_lo:pre_hi].astype(np.float64) ** 2)))
            if pre_hi > pre_lo
            else 0.0
        )
        rms_post = (
            float(np.sqrt(np.mean(audio[post_lo:post_hi].astype(np.float64) ** 2)))
            if post_hi > post_lo
            else 0.0
        )

        a_lo = max(0, idx - pre10)
        pre_amp = float(np.max(np.abs(audio[a_lo:idx]))) if idx > a_lo else 0.0
        peak_amp = float(peak_amplitudes[k])
        attack = (peak_amp - pre_amp) / 0.010

        # gap_prev: time since the previous candidate (or 5 s if first).
        j = int(np.searchsorted(sorted_t, float(t)))
        gap_prev = float(sorted_t[j] - sorted_t[j - 1]) if j > 0 else 5.0

        # Reverb tail (50-200 ms after local peak; absolute, not normalised).
        peak_search_n = int(0.010 * sample_rate)
        psearch_hi = min(n, idx + peak_search_n)
        if abs_audio is None:
            abs_audio = np.abs(audio.astype(np.float64))
        peak_local_idx = (
            idx + int(np.argmax(abs_audio[idx:psearch_hi])) if psearch_hi > idx else idx
        )
        tail_lo = min(n, peak_local_idx + int(0.050 * sample_rate))
        tail_hi = min(n, peak_local_idx + int(0.200 * sample_rate))
        tail_amp = float(abs_audio[tail_lo:tail_hi].mean()) if tail_hi > tail_lo else 0.0

        # Multi-resolution envelope ratios in [-25 ms, +25 ms] around the
        # local peak. Validated +2.9 pp precision lift on the 8-fixture
        # calibration set (LOFO); keep in sync with build_ensemble_fixture.
        mr_lo = max(0, peak_local_idx - int(0.025 * sample_rate))
        mr_hi = min(n, peak_local_idx + int(0.025 * sample_rate))
        seg = abs_audio[mr_lo:mr_hi]
        p_1 = _smoothed_peak(seg, 1.0, sample_rate)
        p_5 = _smoothed_peak(seg, 5.0, sample_rate)
        p_20 = _smoothed_peak(seg, 20.0, sample_rate)
        ratio_1_20 = p_1 / (p_20 + 1e-9)
        ratio_5_20 = p_5 / (p_20 + 1e-9)

        out[k, 0] = peak_amp
        out[k, 1] = float(confidences[k])
        out[k, 2] = rms_pre
        out[k, 3] = rms_post
        out[k, 4] = rms_post / (rms_pre + 1e-6)
        out[k, 5] = attack
        out[k, 6] = gap_prev
        out[k, 7] = (float(t) - beep_time) * 1000.0
        out[k, 8] = tail_amp
        out[k, 9] = ratio_1_20
        out[k, 10] = ratio_5_20
        out[k, 11] = agc.agc_state[k]
        out[k, 12] = agc.time_since_last_loud_event[k]
        out[k, 13] = agc.peak_floor_ratio[k]

        spec_lo = max(0, peak_local_idx - spectral_half)
        spec_hi = min(n, peak_local_idx + spectral_half)
        flatness, peak_ratio = _spectral_flatness_and_peak_ratio(
            audio[spec_lo:spec_hi].astype(np.float64), sample_rate
        )
        out[k, 14] = flatness
        out[k, 15] = peak_ratio
        out[k, 16] = float(tta_agreement[k])
    return out


def load_clap_runtime() -> ClapRuntime:
    """Load the CLAP model + pre-encode the prompt bank.

    First call downloads ~600 MB to the HF cache. Reuse the returned
    runtime across detections.
    """
    import torch
    from transformers import ClapModel, ClapProcessor

    model = ClapModel.from_pretrained(CLAP_MODEL_ID)
    processor = ClapProcessor.from_pretrained(CLAP_MODEL_ID)
    model.eval()

    text_inputs = processor(text=list(CLAP_PROMPTS), return_tensors="pt", padding=True)
    with torch.no_grad():
        out = model.get_text_features(**dict(text_inputs))
    text_emb_t = out.pooler_output if hasattr(out, "pooler_output") else out
    text_emb = text_emb_t.cpu().numpy().astype(np.float32)
    text_emb = text_emb / (np.linalg.norm(text_emb, axis=1, keepdims=True) + 1e-9)
    return ClapRuntime(model=model, processor=processor, text_embeddings=text_emb)


def compute_clap_similarities(
    audio: np.ndarray,
    sample_rate: int,
    candidate_times: np.ndarray,
    runtime: ClapRuntime,
) -> np.ndarray:
    """Per-candidate cosine similarity to every prompt, shape ``(N, P)``.

    Audio is resampled to ``CLAP_SR`` once; per-candidate windows are then
    sliced from the resampled stream so the model sees its native rate.
    """
    import torch

    if not len(candidate_times):
        return np.zeros((0, len(CLAP_PROMPTS)), dtype=np.float32)
    if sample_rate != CLAP_SR:
        clap_audio = librosa.resample(
            audio.astype(np.float32), orig_sr=sample_rate, target_sr=CLAP_SR
        )
    else:
        clap_audio = audio.astype(np.float32)

    windows = [_slice_window(clap_audio, CLAP_SR, float(t), CLAP_WINDOW_S) for t in candidate_times]

    audio_embeddings: list[np.ndarray] = []
    batch_size = 16
    for i in range(0, len(windows), batch_size):
        batch = windows[i : i + batch_size]
        inputs = runtime.processor(audio=batch, sampling_rate=CLAP_SR, return_tensors="pt")
        with torch.no_grad():
            out = runtime.model.get_audio_features(**dict(inputs))
        emb = out.pooler_output if hasattr(out, "pooler_output") else out
        audio_embeddings.append(emb.cpu().numpy())
    audio_emb = np.concatenate(audio_embeddings, axis=0).astype(np.float32)
    audio_emb = audio_emb / (np.linalg.norm(audio_emb, axis=1, keepdims=True) + 1e-9)
    return (audio_emb @ runtime.text_embeddings.T).astype(np.float32)


def clap_diff_from_similarities(sims: np.ndarray) -> np.ndarray:
    """Voter B's signal: mean(shot-prompt sims) - mean(not-shot sims)."""
    n_shot = len(CLAP_PROMPTS_SHOT)
    if sims.size == 0:
        return np.zeros(0, dtype=np.float32)
    shot_mean = sims[:, :n_shot].mean(axis=1)
    not_mean = sims[:, n_shot:].mean(axis=1)
    return (shot_mean - not_mean).astype(np.float32)


def load_pann_runtime() -> PannRuntime:
    """Load the PANN CNN14 audio tagger.

    First call downloads ~80 MB to ``~/panns_data/``. Reuse across
    detections.
    """
    from panns_inference import AudioTagging

    return PannRuntime(tagger=AudioTagging(checkpoint_path=None, device="cpu"))


def compute_pann_gunshot_probs(
    audio: np.ndarray,
    sample_rate: int,
    candidate_times: np.ndarray,
    runtime: PannRuntime,
) -> np.ndarray:
    """Per-candidate ``Gunshot, gunfire`` class probability, shape ``(N,)``."""
    if not len(candidate_times):
        return np.zeros(0, dtype=np.float32)
    if sample_rate != PANN_SR:
        pann_audio = librosa.resample(
            audio.astype(np.float32), orig_sr=sample_rate, target_sr=PANN_SR
        )
    else:
        pann_audio = audio.astype(np.float32)
    batch = np.stack(
        [_slice_window(pann_audio, PANN_SR, float(t), PANN_WINDOW_S) for t in candidate_times],
        axis=0,
    )
    clipwise, _embedding = runtime.tagger.inference(batch)
    clipwise = np.asarray(clipwise, dtype=np.float32)
    return clipwise[:, PANN_GUNSHOT_CLASS_INDEX].astype(np.float32)


def load_beats_runtime(
    checkpoint_path: Path | str | None = None, device: str = "cpu"
) -> BeatsRuntime:
    """Load the Microsoft BEATs audio tagger.

    BEATs is not packaged on PyPI as of this writing; the loader expects
    the official microsoft/unilm BEATs codebase to be importable as
    ``beats`` (e.g. ``pip install -e <unilm>/beats``) and a checkpoint
    ``.pt`` to be on disk. The checkpoint location is resolved in this
    order:

    1. The ``checkpoint_path`` argument.
    2. The ``SPLITSMITH_BEATS_CHECKPOINT`` environment variable.

    The loader attaches a ``predict_probs(batch_tensor) -> probs_tensor``
    adapter that the inference helper keys on, so callers don't need to
    track the upstream API differences between BEATs releases.

    First call is slow (~360 MB checkpoint); reuse the returned runtime.
    """
    resolved = checkpoint_path or os.environ.get(BEATS_CHECKPOINT_ENV)
    if not resolved:
        raise RuntimeError(
            "BEATs checkpoint not provided. Pass ``checkpoint_path=`` to "
            f"``load_beats_runtime`` or set ``{BEATS_CHECKPOINT_ENV}`` to "
            "a downloaded BEATs .pt file (see microsoft/unilm/beats)."
        )
    ckpt = Path(resolved)
    if not ckpt.exists():
        raise FileNotFoundError(
            f"BEATs checkpoint not found at {ckpt}. Download a checkpoint "
            "from microsoft/unilm/beats and point ``checkpoint_path`` / "
            f"``{BEATS_CHECKPOINT_ENV}`` at the .pt file."
        )

    try:
        import torch

        # Defer the BEATs import; it lives in microsoft/unilm and isn't
        # on PyPI. A clear ImportError beats a cryptic KeyError later.
        from beats.BEATs import BEATs, BEATsConfig  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover -- only hit when BEATs isn't installed
        raise RuntimeError(
            "BEATs is not importable. Install the microsoft/unilm BEATs "
            "package (``pip install -e <unilm>/beats``) and ``torch``, "
            "then retry."
        ) from exc

    state = torch.load(str(ckpt), map_location=device)
    cfg = BEATsConfig(state["cfg"])
    model = BEATs(cfg)
    model.load_state_dict(state["model"])
    model.eval()
    model.to(device)

    @torch.no_grad()
    def _predict_probs(batch: torch.Tensor) -> torch.Tensor:
        # BEATs returns logits shaped (B, n_classes); upstream releases
        # vary between calling the predictor ``predict`` and exposing
        # logits via ``extract_features``. Try both, sigmoid the logits
        # to match PANN's clipwise probabilities.
        if hasattr(model, "predict"):
            logits, _padding_mask = model.predict(batch, padding_mask=None)
        else:  # pragma: no cover -- exercised by alt BEATs builds
            logits, _ = model.extract_features(batch, padding_mask=None)
        return torch.sigmoid(logits)

    model.predict_probs = _predict_probs  # type: ignore[attr-defined]
    return BeatsRuntime(model=model, device=device)


def compute_beats_gunshot_probs(
    audio: np.ndarray,
    sample_rate: int,
    candidate_times: np.ndarray,
    runtime: BeatsRuntime,
) -> np.ndarray:
    """Per-candidate ``Gunshot, gunfire`` class probability via BEATs, shape ``(N,)``.

    Mirrors ``compute_pann_gunshot_probs`` so the Voter D dispatcher can
    route between backends without per-call branching beyond runtime
    selection. Audio is resampled to ``BEATS_SR``; per-candidate windows
    are sliced from the resampled stream.
    """
    if not len(candidate_times):
        return np.zeros(0, dtype=np.float32)

    import torch

    if sample_rate != BEATS_SR:
        beats_audio = librosa.resample(
            audio.astype(np.float32), orig_sr=sample_rate, target_sr=BEATS_SR
        )
    else:
        beats_audio = audio.astype(np.float32)
    batch = np.stack(
        [_slice_window(beats_audio, BEATS_SR, float(t), BEATS_WINDOW_S) for t in candidate_times],
        axis=0,
    )
    batch_t = torch.from_numpy(batch).to(runtime.device)
    probs_t = runtime.model.predict_probs(batch_t)
    probs = probs_t.detach().cpu().numpy().astype(np.float32)
    return probs[:, BEATS_GUNSHOT_CLASS_INDEX].astype(np.float32)


def voter_c_feature_matrix(
    hand_features: np.ndarray,
    clap_sims: np.ndarray,
    clap_diff: np.ndarray,
    camera_classes: list[str] | np.ndarray | str | None = None,
) -> np.ndarray:
    """Stack the GBDT input vector ``[hand | clap_sims | clap_diff | camera_class_onehot]``.

    Column order matches the calibration script. Drift here -- adding
    features, reordering CLAP prompts, reordering camera classes -- silently
    desyncs voter C, so any change requires rebuilding the shipped GBDT.

    ``camera_classes`` (issue #139) is a per-row class label (or a single
    string broadcast to every row). ``None`` and unknown classes fall back
    to ``headcam`` so legacy callers keep working byte-identically.
    """
    n_rows = hand_features.shape[0]
    if n_rows == 0:
        return np.zeros((0, VOTER_C_FEATURE_DIM), dtype=np.float64)
    classes_input: list[str] | np.ndarray | str
    if camera_classes is None:
        classes_input = "headcam"
    else:
        classes_input = camera_classes
    cam_block = camera_class_one_hot(classes_input, n_rows)
    return np.concatenate(
        [
            hand_features,
            clap_sims.astype(np.float64),
            clap_diff.astype(np.float64)[:, None],
            cam_block,
        ],
        axis=1,
    )
