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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import librosa
import numpy as np

from .agc_state import compute_agc_features
from .backend import Backend, SplitsmithBackendError, select_backend

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
_CAMERA_CLASS_TO_INDEX: dict[str, int] = {name: idx for idx, name in enumerate(CAMERA_CLASS_FEATURE_NAMES)}

# +1 for clap_diff, +1 for gunshot_prob (folded in from voter D).
VOTER_C_FEATURE_DIM: int = HAND_FEATURE_DIM + len(CLAP_PROMPTS) + 1 + 1 + CAMERA_CLASS_FEATURE_DIM


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
    """CLAP inference state, backend-agnostic.

    ``encode_audio`` takes an ``(N, T_samples)`` float32 batch at
    ``CLAP_SR`` and returns ``(N, D)`` L2-normalised embeddings in
    numpy. ``text_embeddings`` is ``(P, D)``, pre-encoded once at load
    time so per-detection runs only pay the audio cost.

    The torch and ONNX implementations both wrap their backend-specific
    objects (``transformers.ClapModel``, ``onnxruntime.InferenceSession``)
    inside the callable so voter code stays backend-agnostic. ``model``
    and ``processor`` are retained as opaque ``Any`` slots so the torch
    branch can keep references alive for the lifetime of the runtime;
    consumers should not read them directly.
    """

    encode_audio: Callable[[np.ndarray], np.ndarray]
    text_embeddings: np.ndarray  # (P, D), L2-normalised
    backend: Backend
    model: Any = None
    processor: Any = None


@dataclass
class PannRuntime:
    """PANN gunshot-class inference state, backend-agnostic.

    ``predict_gunshot_prob`` takes an ``(N, T_samples)`` float32 batch
    at ``PANN_SR`` and returns an ``(N,)`` numpy vector of the AudioSet
    ``Gunshot, gunfire`` class probability per row.
    """

    predict_gunshot_prob: Callable[[np.ndarray], np.ndarray]
    backend: Backend
    tagger: Any = None


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
            float(np.sqrt(np.mean(audio[pre_lo:pre_hi].astype(np.float64) ** 2))) if pre_hi > pre_lo else 0.0
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
        peak_local_idx = idx + int(np.argmax(abs_audio[idx:psearch_hi])) if psearch_hi > idx else idx
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


def _build_clap_runtime_torch() -> ClapRuntime:
    """Construct a torch-backed :class:`ClapRuntime`. Dev / contributor path."""
    import torch
    from transformers import ClapModel, ClapProcessor

    model = ClapModel.from_pretrained(CLAP_MODEL_ID)
    processor = ClapProcessor.from_pretrained(CLAP_MODEL_ID)
    model.eval()

    text_inputs = processor(text=list(CLAP_PROMPTS), return_tensors="pt", padding=True)
    with torch.no_grad():
        text_out = model.get_text_features(**dict(text_inputs))
    text_emb_t = text_out.pooler_output if hasattr(text_out, "pooler_output") else text_out
    text_emb = text_emb_t.cpu().numpy().astype(np.float32)
    text_emb = text_emb / (np.linalg.norm(text_emb, axis=1, keepdims=True) + 1e-9)

    def encode_audio(batch: np.ndarray) -> np.ndarray:
        """``(N, T)`` float32 audio -> ``(N, D)`` L2-normalised embeddings."""
        inputs = processor(
            audio=list(batch),
            sampling_rate=CLAP_SR,
            return_tensors="pt",
        )
        with torch.no_grad():
            audio_out = model.get_audio_features(**dict(inputs))
        emb = audio_out.pooler_output if hasattr(audio_out, "pooler_output") else audio_out
        emb_np = emb.cpu().numpy().astype(np.float32)
        return emb_np / (np.linalg.norm(emb_np, axis=1, keepdims=True) + 1e-9)

    return ClapRuntime(
        encode_audio=encode_audio,
        text_embeddings=text_emb,
        backend=Backend.TORCH,
        model=model,
        processor=processor,
    )


def load_clap_runtime() -> ClapRuntime:
    """Load CLAP through the selected backend.

    First call on the torch path downloads ~600 MB to the HF cache;
    ONNX path will fetch the slim artifacts from R2 once doc 02 ships.
    Reuse the returned runtime across detections.
    """
    backend = select_backend()
    if backend is Backend.TORCH:
        return _build_clap_runtime_torch()
    if backend is Backend.ONNX:
        raise NotImplementedError("ONNX CLAP runtime not implemented yet (issue #377 phase 'ONNX exports')")
    raise SplitsmithBackendError(f"Unknown backend {backend!r}")


def compute_clap_similarities(
    audio: np.ndarray,
    sample_rate: int,
    candidate_times: np.ndarray,
    runtime: ClapRuntime,
) -> np.ndarray:
    """Per-candidate cosine similarity to every prompt, shape ``(N, P)``.

    Audio is resampled to ``CLAP_SR`` once; per-candidate windows are
    then sliced from the resampled stream so the model sees its native
    rate. Inference goes through ``runtime.encode_audio`` so this
    function is backend-agnostic and free of ``import torch``.
    """
    if not len(candidate_times):
        return np.zeros((0, len(CLAP_PROMPTS)), dtype=np.float32)
    if sample_rate != CLAP_SR:
        clap_audio = librosa.resample(audio.astype(np.float32), orig_sr=sample_rate, target_sr=CLAP_SR)
    else:
        clap_audio = audio.astype(np.float32)

    windows = np.stack(
        [_slice_window(clap_audio, CLAP_SR, float(t), CLAP_WINDOW_S) for t in candidate_times],
        axis=0,
    )

    audio_embeddings: list[np.ndarray] = []
    batch_size = 16
    for i in range(0, windows.shape[0], batch_size):
        audio_embeddings.append(runtime.encode_audio(windows[i : i + batch_size]))
    audio_emb = np.concatenate(audio_embeddings, axis=0).astype(np.float32)
    return (audio_emb @ runtime.text_embeddings.T).astype(np.float32)


def clap_diff_from_similarities(sims: np.ndarray) -> np.ndarray:
    """Voter B's signal: mean(shot-prompt sims) - mean(not-shot sims)."""
    n_shot = len(CLAP_PROMPTS_SHOT)
    if sims.size == 0:
        return np.zeros(0, dtype=np.float32)
    shot_mean = sims[:, :n_shot].mean(axis=1)
    not_mean = sims[:, n_shot:].mean(axis=1)
    return (shot_mean - not_mean).astype(np.float32)


def _build_pann_runtime_torch() -> PannRuntime:
    """Construct a torch-backed :class:`PannRuntime`. Dev / contributor path."""
    from panns_inference import AudioTagging

    tagger = AudioTagging(checkpoint_path=None, device="cpu")

    def predict_gunshot_prob(batch: np.ndarray) -> np.ndarray:
        clipwise, _embedding = tagger.inference(batch)
        return np.asarray(clipwise, dtype=np.float32)[:, PANN_GUNSHOT_CLASS_INDEX].astype(np.float32)

    return PannRuntime(
        predict_gunshot_prob=predict_gunshot_prob,
        backend=Backend.TORCH,
        tagger=tagger,
    )


PANN_ARTIFACT_SLUG = "pann_cnn14"
ENV_ONNX_PANN_OVERRIDE = "SPLITSMITH_ONNX_PANN"


def _resolve_onnx_pann_path() -> Path:
    """Locate the PANN ONNX artifact.

    Order:
    1. ``SPLITSMITH_ONNX_PANN`` env var -- dev / test override pointing
       at a freshly exported file under ``build/onnx-spike/``.
    2. The slim model registry -- ``ensemble_calibration.json`` must have
       a ``model_artifacts.pann_cnn14`` block; the registry downloads
       from R2 and SHA256-verifies. Raises clear errors if the block
       isn't there yet (still in slim v1 rollout).
    """
    override = os.environ.get(ENV_ONNX_PANN_OVERRIDE)
    if override:
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(f"{ENV_ONNX_PANN_OVERRIDE} points at {path}, which does not exist")
        return path

    from ..models import get_default_registry

    registry = get_default_registry()
    if registry is None:
        raise RuntimeError(
            "ONNX PANN backend selected but ensemble_calibration.json has no "
            "model_artifacts block. Run `uv run python "
            "scripts/export_pann_onnx.py` and paste the printed snippet into "
            "src/splitsmith/data/ensemble_calibration.json, or point "
            f"{ENV_ONNX_PANN_OVERRIDE} at a local export."
        )
    return registry.resolve(PANN_ARTIFACT_SLUG)


def _build_pann_runtime_onnx() -> PannRuntime:
    """Construct an onnxruntime-backed :class:`PannRuntime`.

    Loads the consolidated single-file artifact produced by
    ``scripts/export_pann_onnx.py``. Inference contract matches the
    torch branch: ``(N, T)`` float32 audio at ``PANN_SR`` -> ``(N,)``
    gunshot probability in numpy.
    """
    import onnxruntime as ort

    onnx_path = _resolve_onnx_pann_path()
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name

    def predict_gunshot_prob(batch: np.ndarray) -> np.ndarray:
        audio = np.ascontiguousarray(batch.astype(np.float32, copy=False))
        clipwise = session.run(None, {input_name: audio})[0]
        return clipwise[:, PANN_GUNSHOT_CLASS_INDEX].astype(np.float32)

    return PannRuntime(
        predict_gunshot_prob=predict_gunshot_prob,
        backend=Backend.ONNX,
        tagger=None,
    )


def load_pann_runtime() -> PannRuntime:
    """Load PANN through the selected backend.

    First call on the torch path downloads ~80 MB to ``~/panns_data/``.
    On the ONNX path, the consolidated ``pann_cnn14.onnx`` is resolved
    via :func:`_resolve_onnx_pann_path` (env override or slim model
    registry). Reuse the returned runtime across detections.
    """
    backend = select_backend()
    if backend is Backend.TORCH:
        return _build_pann_runtime_torch()
    if backend is Backend.ONNX:
        return _build_pann_runtime_onnx()
    raise SplitsmithBackendError(f"Unknown backend {backend!r}")


def compute_pann_gunshot_probs(
    audio: np.ndarray,
    sample_rate: int,
    candidate_times: np.ndarray,
    runtime: PannRuntime,
) -> np.ndarray:
    """Per-candidate ``Gunshot, gunfire`` class probability, shape ``(N,)``.

    Inference goes through ``runtime.predict_gunshot_prob`` so this
    function is backend-agnostic and free of ``import torch``.
    """
    if not len(candidate_times):
        return np.zeros(0, dtype=np.float32)
    if sample_rate != PANN_SR:
        pann_audio = librosa.resample(audio.astype(np.float32), orig_sr=sample_rate, target_sr=PANN_SR)
    else:
        pann_audio = audio.astype(np.float32)
    batch = np.stack(
        [_slice_window(pann_audio, PANN_SR, float(t), PANN_WINDOW_S) for t in candidate_times],
        axis=0,
    )
    return runtime.predict_gunshot_prob(batch)


def voter_c_feature_matrix(
    hand_features: np.ndarray,
    clap_sims: np.ndarray,
    clap_diff: np.ndarray,
    gunshot_prob: np.ndarray,
    camera_classes: list[str] | np.ndarray | str | None = None,
) -> np.ndarray:
    """Stack the GBDT input vector.

    Columns in order: ``hand | clap_sims | clap_diff | gunshot_prob |
    camera_class_onehot``.

    Column order matches the calibration script. Drift here -- adding
    features, reordering CLAP prompts, reordering camera classes -- silently
    desyncs voter C, so any change requires rebuilding the shipped GBDT.

    ``gunshot_prob`` is the PANN ``Gunshot, gunfire`` class probability
    (formerly voter D's signal). Folded into voter C as a feature so the
    GBDT can learn its interaction with the other columns rather than
    casting an independent vote -- on the 30-fixture corpus voter D's
    threshold was a dead axis under ``c_required=True`` (see
    ``docs/ensemble_dashboard/findings/2026-05-11_voter_abd_have_no_leverage.md``).

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
            gunshot_prob.astype(np.float64)[:, None],
            cam_block,
        ],
        axis=1,
    )
