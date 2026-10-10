# Voter C stage-relative features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give voter C two absolute spectral columns and a 24-column stage-relative block so it holds up when a camera's audio changes, then retrain and ship.

**Architecture:** All new feature math lives in `src/splitsmith/ensemble/features.py`. `compute_hand_features` gains the spectral columns; a new pure function `stage_relative_features` builds the relative block from one stage's columns; `voter_c_feature_matrix` appends it and is the only place the column layout is assembled, called by the runtime (`ensemble/api.py`), the trainer (`scripts/build_ensemble_artifacts.py`) and the sweep table (`scripts/build_sweep_signals.py`). The runtime refuses an artifact whose width does not match.

**Tech Stack:** Python 3.11+, numpy, scikit-learn (build script only), onnxruntime, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-voter-c-stage-relative-features-design.md`

## Global Constraints

- Branch from `chore/retrain-ensemble-hostfinalen` (PR #1355, the Höstfinalen fixtures and retrain); this work retrains on top of it.
- New absolute columns: `spectral_centroid_hz`, `high_band_db`, from the 40 ms starting 2 ms after the candidate time (Hann window, 4096-point FFT, band 200 Hz to 16 kHz, high band above 4 kHz).
- Relative block: log-scale sources `peak_amp, rms_post, tail_amp, peak_floor_ratio, spectral_flatness, spectral_peak_ratio, rms_ratio, attack` (`log(x) - median(log(ref))`), linear sources `ratio_1_20, ratio_5_20, gunshot_prob, clap_diff, spectral_centroid_hz, high_band_db, clap_sim_00..09` (`x - median(ref)`); 24 columns named `rel_<source>`.
- Reference: top `K` candidates of the stage by detector confidence; `K = expected_rounds` when given (clamped to `[1, N]`), else `max(3, round(0.3 * N))` clamped to `N`.
- A NaN source value is excluded from the median and its relative value is 0; no NaN reaches the ONNX graph.
- No running-relative block. No config switch between layouts; the revert is a `git revert` of the PR.
- No new dependencies. `uv` only, Black at 110, type hints, `pathlib.Path`.
- Acceptance: headcam leave-one-match-out F1 >= 0.86, Höstfinalen Vanguard recall >= 0.85, handheld leave-one-match-out F1 not below the #1355 build's by more than 0.005; every newly worse fixture listed in the PR; `docs/cameras.md` records the GO 3S precision trade.

## Review Focus

- A stage with 0, 1 or 2 candidates must score without raising (relative block of zeros or near-zeros).
- `expected_rounds` of 0, negative, or larger than the candidate count must clamp, not index out of range.
- A candidate within 42 ms of the clip end has no spectral window: its spectral columns are NaN inside the pipeline but the matrix handed to ONNX has no NaN.
- An artifact set built before this change, pointed at through `SPLITSMITH_ARTIFACTS_DIR`, must fail at load with a message naming the file and both widths, not at the first `predict_proba`.
- Candidates with tied confidences must give the same reference set every run (stable sort), or two runs of the same stage disagree.

---

### Task 1: Spectral centroid and high-band columns in the hand features

**Files:**
- Modify: `src/splitsmith/ensemble/features.py` (`_HAND_FEATURE_NAMES`, `compute_hand_features`, new `_centroid_and_high_band`)
- Test: `tests/test_features_stage_relative.py` (new)

**Interfaces:**
- Produces: `HAND_FEATURE_DIM == 19`; hand columns 17 and 18 are `spectral_centroid_hz` and `high_band_db` (NaN when the window does not fit); `_centroid_and_high_band(segment: np.ndarray, sample_rate: int) -> tuple[float, float]`.

- [ ] **Step 1: Write the failing tests**

```python
"""Stage-relative voter C features (spec 2026-10-09)."""

import json
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import butter, sosfiltfilt

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.ensemble import features as feat
from splitsmith.ensemble.tta import compute_tta_agreement
from splitsmith.shot_detect import detect_shots

FIXTURES = Path(__file__).parent / "fixtures"
GO3S_FIXTURE = "stage-shots-hostfinalen-xi-2026-stage7-s97dcec94"


def _stage(name: str, gain: float = 1.0, lowpass_hz: float | None = None):
    audio, sr = load_audio(FIXTURES / f"{name}.wav")
    truth = json.loads((FIXTURES / f"{name}.json").read_text())
    shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"],
                         ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0))
    times = np.array([s.time_absolute for s in shots])
    conf = np.array([s.confidence for s in shots])
    peaks = np.array([s.peak_amplitude for s in shots])
    tta = compute_tta_agreement(audio, sr, truth["beep_time"], truth["stage_time_seconds"], times)
    x = audio.astype(np.float64)
    if lowpass_hz is not None:
        x = sosfiltfilt(butter(4, lowpass_hz, btype="low", fs=sr, output="sos"), x)
    x = x * gain
    hand = feat.compute_hand_features(x, sr, times, truth["beep_time"], conf, peaks * gain, tta)
    return hand, truth, times


def test_hand_features_carry_centroid_and_high_band():
    assert feat.HAND_FEATURE_DIM == 19
    assert feat._HAND_FEATURE_NAMES[17:] == ("spectral_centroid_hz", "high_band_db")


def test_lowpass_lowers_centroid_and_high_band():
    hand, _, _ = _stage(GO3S_FIXTURE)
    dull, _, _ = _stage(GO3S_FIXTURE, lowpass_hz=1500.0)
    assert np.nanmedian(dull[:, 17]) < np.nanmedian(hand[:, 17]) - 100.0
    assert np.nanmedian(dull[:, 18]) < np.nanmedian(hand[:, 18]) - 3.0


def test_centroid_and_high_band_ignore_gain():
    hand, _, _ = _stage(GO3S_FIXTURE)
    quiet, _, _ = _stage(GO3S_FIXTURE, gain=0.25)
    np.testing.assert_allclose(quiet[:, 17:19], hand[:, 17:19], rtol=1e-6, atol=1e-6)


def test_window_past_clip_end_is_nan():
    sr = 48000
    audio = np.random.default_rng(0).normal(0, 0.01, sr)
    t = np.array([len(audio) / sr - 0.01])
    hand = feat.compute_hand_features(audio, sr, t, 0.0, np.array([0.5]), np.array([0.1]), np.array([5.0]))
    assert np.isnan(hand[0, 17]) and np.isnan(hand[0, 18])
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -n0 tests/test_features_stage_relative.py -v`
Expected: FAIL (`HAND_FEATURE_DIM` is 17, index 17 out of bounds).

- [ ] **Step 3: Implement**

In `features.py`, append to `_HAND_FEATURE_NAMES` after `"tta_agreement"`:

```python
    # Timbre that drifts with camera and firmware (spec 2026-10-09): the
    # 40 ms starting 2 ms after the candidate. NaN when the window does
    # not fit; voter_c_feature_matrix zeroes them after the relative block.
    "spectral_centroid_hz",
    "high_band_db",
```

Add next to `_spectral_flatness_and_peak_ratio`:

```python
_TIMBRE_OFFSET_S: float = 0.002
_TIMBRE_WINDOW_S: float = 0.040
_TIMBRE_NFFT: int = 4096
_TIMBRE_BAND_HZ: tuple[float, float] = (200.0, 16000.0)
_HIGH_BAND_LO_HZ: float = 4000.0


def _centroid_and_high_band(segment: np.ndarray, sample_rate: int) -> tuple[float, float]:
    """Spectral centroid (Hz) and energy above 4 kHz relative to 200 Hz-16 kHz (dB)."""
    if segment.size < 64:
        return float("nan"), float("nan")
    spec = np.abs(np.fft.rfft(segment * np.hanning(segment.size), _TIMBRE_NFFT)) ** 2
    freqs = np.fft.rfftfreq(_TIMBRE_NFFT, 1.0 / sample_rate)
    band = (freqs >= _TIMBRE_BAND_HZ[0]) & (freqs <= _TIMBRE_BAND_HZ[1])
    total = float(spec[band].sum())
    if total <= 0.0:
        return float("nan"), float("nan")
    centroid = float((freqs[band] * spec[band]).sum() / total)
    high = float(spec[freqs >= _HIGH_BAND_LO_HZ].sum())
    return centroid, float(10.0 * np.log10(high / total + 1e-12))
```

In `compute_hand_features`, after `out[k, 16] = float(tta_agreement[k])`:

```python
        t_lo = idx + int(round(_TIMBRE_OFFSET_S * sample_rate))
        t_hi = t_lo + int(round(_TIMBRE_WINDOW_S * sample_rate))
        segment = audio[t_lo:t_hi].astype(np.float64) if t_hi <= n else np.zeros(0)
        out[k, 17], out[k, 18] = _centroid_and_high_band(segment, sample_rate)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest -n0 tests/test_features_stage_relative.py -v`
Expected: 4 passed. (`test_ensemble.py` and the parity tests now fail on the width; Task 3 fixes them. Do not commit those failures on their own: commit this task's files only.)

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ensemble/features.py tests/test_features_stage_relative.py
git commit -m "feat(ensemble): spectral centroid and high-band columns in the hand features"
```

### Task 2: `stage_relative_features`

**Files:**
- Modify: `src/splitsmith/ensemble/features.py` (new constants and function after `compute_hand_features`)
- Test: `tests/test_features_stage_relative.py`

**Interfaces:**
- Consumes: hand columns from Task 1 (`HAND_FEATURE_DIM == 19`, index map from `_HAND_FEATURE_NAMES`).
- Produces: `REL_FEATURE_NAMES: tuple[str, ...]` (24 names, `rel_<source>`), `REL_FEATURE_DIM == 24`, `reference_indices(confidences: np.ndarray, expected_rounds: int | None) -> np.ndarray`, `stage_relative_features(hand: np.ndarray, clap_sims: np.ndarray, clap_diff: np.ndarray, gunshot_prob: np.ndarray, expected_rounds: int | None) -> np.ndarray` of shape `(N, 24)`, float64, no NaN.

- [ ] **Step 1: Write the failing tests** (append to the test file)

```python
def test_reference_uses_round_count_then_thirty_percent():
    conf = np.array([0.1, 0.9, 0.5, 0.7, 0.3, 0.8, 0.2, 0.6, 0.4, 0.05])
    assert sorted(feat.reference_indices(conf, 3).tolist()) == [1, 3, 5]
    assert sorted(feat.reference_indices(conf, None).tolist()) == [1, 3, 5]  # max(3, round(3.0))
    assert len(feat.reference_indices(conf, 50)) == 10
    assert len(feat.reference_indices(conf, 0)) == 1
    assert len(feat.reference_indices(conf, -4)) == 1
    assert len(feat.reference_indices(np.array([]), None)) == 0


def test_reference_is_stable_under_ties():
    conf = np.full(9, 0.5)
    assert feat.reference_indices(conf, 4).tolist() == feat.reference_indices(conf, 4).tolist() == [0, 1, 2, 3]


def _rel(hand, n_clap=10, expected=None):
    n = hand.shape[0]
    sims = np.zeros((n, n_clap))
    return feat.stage_relative_features(hand, sims, np.zeros(n), np.zeros(n), expected)


def test_tiny_stages_do_not_raise():
    for n in (0, 1, 2):
        hand = np.abs(np.random.default_rng(n).normal(0.5, 0.1, (n, feat.HAND_FEATURE_DIM)))
        rel = _rel(hand)
        assert rel.shape == (n, feat.REL_FEATURE_DIM)
        assert not np.isnan(rel).any()


def test_nan_source_is_zero_and_excluded_from_median():
    hand = np.ones((4, feat.HAND_FEATURE_DIM))
    hand[:, 1] = [0.9, 0.8, 0.7, 0.1]           # confidence: first three are the reference
    hand[:, 17] = [1000.0, 1200.0, np.nan, 500.0]
    rel = _rel(hand, expected=3)
    col = feat.REL_FEATURE_NAMES.index("rel_spectral_centroid_hz")
    assert rel[2, col] == 0.0
    assert rel[3, col] == pytest.approx(500.0 - 1100.0)


def test_relative_level_ignores_gain_on_real_audio():
    hand, truth, _ = _stage(GO3S_FIXTURE)
    quiet, _, _ = _stage(GO3S_FIXTURE, gain=0.25)
    exp = (truth.get("stage_rounds") or {}).get("expected")
    a, b = _rel(hand, expected=exp), _rel(quiet, expected=exp)
    for name in ("rel_peak_amp", "rel_rms_post", "rel_tail_amp"):
        c = feat.REL_FEATURE_NAMES.index(name)
        np.testing.assert_allclose(b[:, c], a[:, c], atol=1e-6)


def test_relative_centroid_absorbs_a_whole_stage_timbre_shift():
    hand, truth, times = _stage(GO3S_FIXTURE)
    dull, _, _ = _stage(GO3S_FIXTURE, lowpass_hz=1500.0)
    exp = (truth.get("stage_rounds") or {}).get("expected")
    shot_mask = np.array([np.min(np.abs(float(t) - np.array([s["time"] for s in truth["shots"]]))) < 0.075
                          for t in times])
    c = feat.REL_FEATURE_NAMES.index("rel_spectral_centroid_hz")
    abs_shift = abs(np.nanmedian(dull[shot_mask, 17]) - np.nanmedian(hand[shot_mask, 17]))
    rel_shift = abs(np.median(_rel(dull, expected=exp)[shot_mask, c]) - np.median(_rel(hand, expected=exp)[shot_mask, c]))
    assert abs_shift > 100.0
    assert rel_shift < 0.3 * abs_shift
```
- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -n0 tests/test_features_stage_relative.py -v`
Expected: the new tests FAIL with `AttributeError: ... has no attribute 'reference_indices'`.

- [ ] **Step 3: Implement** (in `features.py`, after `compute_hand_features`)

```python
# Stage-relative block (spec 2026-10-09): each source column minus the
# median over the stage's likely shots, so a camera or firmware that
# shifts every shot's level or timbre moves the reference with it.
_REL_LOG_SOURCES: tuple[str, ...] = (
    "peak_amp", "rms_post", "tail_amp", "peak_floor_ratio",
    "spectral_flatness", "spectral_peak_ratio", "rms_ratio", "attack",
)
_REL_LIN_HAND_SOURCES: tuple[str, ...] = ("ratio_1_20", "ratio_5_20")
_REL_LIN_TIMBRE_SOURCES: tuple[str, ...] = ("spectral_centroid_hz", "high_band_db")
REL_FEATURE_NAMES: tuple[str, ...] = tuple(
    f"rel_{s}"
    for s in (
        *_REL_LOG_SOURCES, *_REL_LIN_HAND_SOURCES, "gunshot_prob", "clap_diff",
        *_REL_LIN_TIMBRE_SOURCES, *(f"clap_sim_{i:02d}" for i in range(len(CLAP_PROMPTS))),
    )
)
REL_FEATURE_DIM: int = len(REL_FEATURE_NAMES)
_REF_FALLBACK_FRACTION: float = 0.3
_REF_FALLBACK_MIN: int = 3
_HAND_INDEX: dict[str, int] = {name: i for i, name in enumerate(_HAND_FEATURE_NAMES)}


def reference_indices(confidences: np.ndarray, expected_rounds: int | None) -> np.ndarray:
    """Indices of the stage's likely shots: the top-K candidates by detector confidence.

    ``K`` is ``expected_rounds`` when known, else ``max(3, round(0.3 * N))``,
    always clamped to ``[1, N]``. A stable sort keeps tied confidences in
    candidate order so two runs of the same stage agree.
    """
    n = int(confidences.size)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    if expected_rounds is not None:
        k = int(expected_rounds)
    else:
        k = max(_REF_FALLBACK_MIN, int(round(_REF_FALLBACK_FRACTION * n)))
    k = min(max(k, 1), n)
    return np.argsort(-confidences, kind="stable")[:k]


def stage_relative_features(
    hand: np.ndarray,
    clap_sims: np.ndarray,
    clap_diff: np.ndarray,
    gunshot_prob: np.ndarray,
    expected_rounds: int | None,
) -> np.ndarray:
    """Per-candidate ``(N, REL_FEATURE_DIM)`` block relative to the stage's likely shots.

    All rows must come from one stage (one detector universe). Log-scale
    sources take ``log(x) - median(log(ref))``; linear ones ``x - median(ref)``.
    A NaN source is excluded from the median and its own value is 0.
    """
    n = hand.shape[0]
    out = np.zeros((n, REL_FEATURE_DIM), dtype=np.float64)
    if n == 0:
        return out
    ref = reference_indices(hand[:, _HAND_INDEX["confidence"]], expected_rounds)
    sources: list[np.ndarray] = [np.log(np.maximum(hand[:, _HAND_INDEX[s]], 1e-9)) for s in _REL_LOG_SOURCES]
    sources += [hand[:, _HAND_INDEX[s]] for s in _REL_LIN_HAND_SOURCES]
    sources += [np.asarray(gunshot_prob, dtype=np.float64), np.asarray(clap_diff, dtype=np.float64)]
    sources += [hand[:, _HAND_INDEX[s]] for s in _REL_LIN_TIMBRE_SOURCES]
    sources += [np.asarray(clap_sims[:, i], dtype=np.float64) for i in range(clap_sims.shape[1])]
    for j, col in enumerate(sources):
        ref_vals = col[ref]
        ref_vals = ref_vals[~np.isnan(ref_vals)]
        if ref_vals.size == 0:
            continue
        rel = col - float(np.median(ref_vals))
        out[:, j] = np.where(np.isnan(rel), 0.0, rel)
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest -n0 tests/test_features_stage_relative.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ensemble/features.py tests/test_features_stage_relative.py
git commit -m "feat(ensemble): stage-relative feature block for voter C"
```

### Task 3: One layout for runtime, trainer and sweep, and the width guard

**Files:**
- Modify: `src/splitsmith/ensemble/features.py` (`VOTER_C_FEATURE_DIM`, `voter_c_feature_matrix`)
- Modify: `src/splitsmith/ensemble/api.py` (`load_ensemble_runtime`, `detect_shots_ensemble`)
- Modify: `scripts/build_ensemble_artifacts.py` (`_x_from`)
- Modify: `scripts/build_sweep_signals.py:269`
- Test: `tests/test_features_stage_relative.py`

**Interfaces:**
- Consumes: `stage_relative_features`, `REL_FEATURE_DIM` (Task 2).
- Produces: `voter_c_feature_matrix(hand, clap_sims, clap_diff, gunshot_prob, camera_classes=None, *, expected_rounds: int | None = None) -> np.ndarray` of width `VOTER_C_FEATURE_DIM = HAND_FEATURE_DIM + len(CLAP_PROMPTS) + 1 + 1 + CAMERA_CLASS_FEATURE_DIM + REL_FEATURE_DIM` (= 19 + 10 + 2 + 2 + 24 = 57), no NaN; `load_ensemble_runtime` raises `RuntimeError` when a voter C graph's `n_features != VOTER_C_FEATURE_DIM`.

- [ ] **Step 1: Write the failing tests** (append)

```python
import importlib.util
import sys

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _build_script():
    spec = importlib.util.spec_from_file_location("build_ensemble_artifacts", SCRIPTS / "build_ensemble_artifacts.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_ensemble_artifacts"] = mod
    spec.loader.exec_module(mod)
    return mod


def _rows(fixture: str, n: int, seed: int, expected: int | None):
    rng = np.random.default_rng(seed)
    hand = np.abs(rng.normal(0.5, 0.2, (n, feat.HAND_FEATURE_DIM)))
    sims = rng.normal(0, 0.1, (n, len(feat.CLAP_PROMPTS)))
    return [
        {"fixture": fixture, "camera_class": "headcam", "expected_rounds": expected,
         "hand_feats": hand[i].tolist(), "clap_sims": sims[i].tolist(),
         "clap_diff": float(sims[i, 0]), "gunshot_prob": float(abs(sims[i, 1]))}
        for i in range(n)
    ]


def test_matrix_has_the_relative_block_and_no_nan():
    rows = _rows("a", 6, 1, 3)
    hand = np.array([r["hand_feats"] for r in rows]); hand[0, 17] = np.nan
    sims = np.array([r["clap_sims"] for r in rows])
    x = feat.voter_c_feature_matrix(hand, sims, sims[:, 0], np.abs(sims[:, 1]), "headcam", expected_rounds=3)
    assert x.shape == (6, feat.VOTER_C_FEATURE_DIM) and feat.VOTER_C_FEATURE_DIM == 57
    assert not np.isnan(x).any()


def test_trainer_and_runtime_build_identical_matrices():
    build = _build_script()
    rows = _rows("stage-a", 7, 2, 4) + _rows("stage-b", 5, 3, None)
    trainer = build._x_from(rows)
    runtime_parts = []
    for fx, exp in (("stage-a", 4), ("stage-b", None)):
        rs = [r for r in rows if r["fixture"] == fx]
        sims = np.array([r["clap_sims"] for r in rs])
        runtime_parts.append(feat.voter_c_feature_matrix(
            np.array([r["hand_feats"] for r in rs]), sims, np.array([r["clap_diff"] for r in rs]),
            np.array([r["gunshot_prob"] for r in rs]), "headcam", expected_rounds=exp))
    np.testing.assert_array_equal(trainer, np.concatenate(runtime_parts))


def test_old_width_artifact_fails_at_load(monkeypatch):
    from splitsmith.ensemble import api

    class Narrow:
        n_features = 31
        path = Path("voter_c_gbdt_headcam.onnx")

    monkeypatch.setattr(api, "load_voter_c_model", lambda *_a, **_k: {"headcam": Narrow()})
    monkeypatch.setattr(api.feat, "load_clap_runtime", lambda: None)
    monkeypatch.setattr(api.feat, "load_pann_runtime", lambda: None)
    with pytest.raises(RuntimeError, match=r"voter_c_gbdt_headcam\.onnx.*31.*57"):
        api.load_ensemble_runtime(with_voter_e=False)
```

Check before running that `load_ensemble_runtime` calls `load_voter_c_model` through the module name `api.load_voter_c_model` (it is imported into `api`); if it is imported under another name, patch that name.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -n0 tests/test_features_stage_relative.py -v`
Expected: the three new tests FAIL (width 33, `_x_from` has no relative block, no load-time guard).

- [ ] **Step 3: Implement**

`features.py`:

```python
# +1 for clap_diff, +1 for gunshot_prob (folded in from voter D), and the
# stage-relative block (spec 2026-10-09) appended after the camera one-hot.
VOTER_C_FEATURE_DIM: int = (
    HAND_FEATURE_DIM + len(CLAP_PROMPTS) + 1 + 1 + CAMERA_CLASS_FEATURE_DIM + REL_FEATURE_DIM
)
```

Move the `VOTER_C_FEATURE_DIM` definition below `REL_FEATURE_DIM` (it must come after it). In `voter_c_feature_matrix`, add the keyword parameter `expected_rounds: int | None = None`, document that all rows must be one stage, and replace the final `np.concatenate` with:

```python
    rel = stage_relative_features(hand_features, clap_sims, clap_diff, gunshot_prob, expected_rounds)
    x = np.concatenate(
        [
            hand_features,
            clap_sims.astype(np.float64),
            clap_diff.astype(np.float64)[:, None],
            np.asarray(gunshot_prob, dtype=np.float64)[:, None],
            cam_block,
            rel,
        ],
        axis=1,
    )
    # Absolute spectral columns are NaN where the window did not fit;
    # the relative block already handled them.
    return np.nan_to_num(x, nan=0.0)
```

(Keep the existing column expressions for `gunshot_prob` and `cam_block` exactly as they are today; only `rel` and the `nan_to_num` are new.)

`api.py`, in `detect_shots_ensemble`:

```python
    voter_c_x = feat.voter_c_feature_matrix(
        hand, clap_sims, clap_diff, gunshot_prob, camera_classes=camera_class, expected_rounds=expected_rounds
    )
```

`api.py`, in `load_ensemble_runtime`, right after `voter_c_model = load_voter_c_model(...)`:

```python
    for model in voter_c_model.values():
        width = getattr(model, "n_features", feat.VOTER_C_FEATURE_DIM)
        if width != feat.VOTER_C_FEATURE_DIM:
            raise RuntimeError(
                f"{model.path} takes {width} features but this splitsmith builds "
                f"{feat.VOTER_C_FEATURE_DIM}; the artifacts predate the current voter C "
                "layout. Rebuild them with scripts/build_ensemble_artifacts.py, or check "
                "out the commit they were built with."
            )
```

`scripts/build_ensemble_artifacts.py`, replace the body of `_x_from` (keep its signature) so it groups by stage and calls the runtime function:

```python
    """Stack per-row Voter C features through ``feat.voter_c_feature_matrix``.

    Rows are grouped by ``fixture`` (one stage each) so the stage-relative
    block sees exactly the candidates the runtime would; the output keeps
    the input row order.
    """
    if not universe:
        return np.zeros((0, feat.VOTER_C_FEATURE_DIM), dtype=np.float64)
    out = np.zeros((len(universe), feat.VOTER_C_FEATURE_DIM), dtype=np.float64)
    by_fixture: dict[str, list[int]] = {}
    for i, row in enumerate(universe):
        by_fixture.setdefault(row["fixture"], []).append(i)
    for idx in by_fixture.values():
        rows = [universe[i] for i in idx]
        out[idx] = feat.voter_c_feature_matrix(
            np.array([r["hand_feats"] for r in rows], dtype=np.float64),
            np.array([r["clap_sims"] for r in rows], dtype=np.float64),
            np.array([r["clap_diff"] for r in rows], dtype=np.float64),
            np.array([r["gunshot_prob"] for r in rows], dtype=np.float64),
            [r.get("camera_class", DEFAULT_CAMERA_CLASS) for r in rows],
            expected_rounds=rows[0].get("expected_rounds"),
        )
    return out
```

Mined rows (`--with-mining`, off in production) carry no `expected_rounds`; they group by their fixture and use the 30 % fallback. Leave a one-line comment saying so where mined rows are built.

`scripts/build_sweep_signals.py:269`: pass `expected_rounds=` the fixture's `stage_rounds.expected` (the script already reads it for the `expected_rounds` column; use that variable).

- [ ] **Step 4: Run to verify they pass, then the ensemble suites**

Run: `uv run pytest -n0 tests/test_features_stage_relative.py -v`
Expected: all passed.
Run: `uv run pytest -q tests/ -k 'ensemble or voter or parity or features'`
Expected: failures only in tests that load the shipped artifacts (width 33 vs 57) and in `test_onnx_parity.py`'s voter C reference; Task 4 rebuilds both. Any other failure is a bug in this task.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ensemble/features.py src/splitsmith/ensemble/api.py scripts/build_ensemble_artifacts.py scripts/build_sweep_signals.py tests/test_features_stage_relative.py
git commit -m "feat(ensemble): voter C layout gains the stage-relative block, one implementation for runtime and trainer"
```

### Task 4: Retrain, measure, document

**Files:**
- Modify: `src/splitsmith/data/ensemble_calibration.json`, `voter_c_gbdt_headcam.onnx`, `voter_c_gbdt_handheld.onnx`, `tests/data/voter_c_parity_reference.npz` (written by the build)
- Modify: `docs/cameras.md`, `src/splitsmith/data/whats_new.json`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Snapshot the #1355 artifacts for comparison**

```bash
mkdir -p ~/.claude-tmp/artifacts-1355 && cp -Rp src/splitsmith/data/. ~/.claude-tmp/artifacts-1355/
```

The comparison needs the old artifacts run by the old code (their width is 33). Make a throwaway worktree at the #1355 head: `git worktree add ~/.claude-tmp/wt-1355 chore/retrain-ensemble-hostfinalen` and run the evaluation there in Step 3.

- [ ] **Step 2: Rebuild**

Requires `/Volumes/X9` only for voter E, which stays off. CLAP and PANN caches must exist under `tests/fixtures/.cache` (copy from the main checkout if missing).

Run: `uv run python scripts/build_ensemble_artifacts.py --no-voter-e`
Then read `build/ensemble_heldout/report.json`: `by_camera_class.headcam.lomo.at_shipped_threshold.f1` must be >= 0.86 and `by_camera_class.handheld.lomo.at_shipped_threshold.f1` must be >= the #1355 build's value minus 0.005 (0.960 - 0.005). Höstfinalen Vanguard recall comes from the rebuilt sweep table: run `uv run python scripts/build_sweep_signals.py --skip-voter-e`, then count `score_c_lomo >= voter_c_threshold` over Höstfinalen `s0fe3d797` positives; it must be >= 0.85. If any criterion fails, stop and report; do not tune.

- [ ] **Step 3: Per-fixture comparison**

Run the engine over every fixture with each artifact set, in both modes (with and without the round count), and diff. The evaluation script used for #1355 does exactly this; copy it into `scripts/eval_ensemble_artifacts.py` so it lives in the repo:

```python
"""Run the ensemble engine over every fixture with the active artifact set; write per-fixture TP/FP/FN.

Usage: [SPLITSMITH_ARTIFACTS_DIR=<dir>] uv run python scripts/eval_ensemble_artifacts.py <out.json>
Two modes per fixture: with the stage's expected rounds (the app's path when the scorecard knows them) and without.
Compare two outputs with scripts/compare_ensemble_evals.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from splitsmith.beep_detect import load_audio
from splitsmith.ensemble.api import detect_shots_ensemble, load_ensemble_runtime
from splitsmith.ensemble.calibration import camera_class_from_mount

TOLERANCE_S = 0.075


def score(kept: list[float], truth: list[float]) -> tuple[int, int, int]:
    used: set[int] = set()
    tp = 0
    for t in sorted(kept):
        best = None
        for j, s in enumerate(truth):
            if j not in used and abs(s - t) <= TOLERANCE_S and (best is None or abs(s - t) < abs(truth[best] - t)):
                best = j
        if best is not None:
            used.add(best)
            tp += 1
    return tp, len(kept) - tp, len(truth) - tp


def main(out_path: Path) -> None:
    runtime = load_ensemble_runtime(with_voter_e=False)
    out: dict[str, dict] = {}
    for fx in sorted(Path("tests/fixtures").glob("stage-shots-*.json")):
        wav = fx.with_suffix(".wav")
        d = json.loads(fx.read_text())
        if not wav.exists() or not d.get("shots") or d.get("stage_time_seconds") is None:
            continue
        audio, sr = load_audio(wav)
        cam = d.get("camera") or {}
        cls = camera_class_from_mount(cam.get("mount"))
        truth = [float(s["time"]) for s in d["shots"]]
        expected = (d.get("stage_rounds") or {}).get("expected")
        row: dict = {"camera_class": cls}
        for mode, rounds in (("rounds", expected), ("blind", None)):
            res = detect_shots_ensemble(
                audio, sr, float(d["beep_time"]), float(d["stage_time_seconds"]), runtime,
                expected_rounds=rounds, camera_class=cls,
                camera_make=cam.get("make"), camera_model=cam.get("model"),
            )
            row[mode] = score([c.time for c in res.candidates if c.kept], truth)
        out[fx.stem] = row
        print(fx.stem, row, flush=True)
    out_path.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
```

and `scripts/compare_ensemble_evals.py`:

```python
"""Compare two eval_ensemble_artifacts.py outputs: totals per camera class and mode, and every fixture that changed.

Usage: uv run python scripts/compare_ensemble_evals.py <old.json> <new.json>
"""

from __future__ import annotations

import collections
import json
import sys


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    return p, r, (2 * p * r / (p + r) if p + r else float("nan"))


def main(old_path: str, new_path: str) -> None:
    old, new = json.load(open(old_path)), json.load(open(new_path))
    common = [fx for fx in old if fx in new]
    for mode in ("rounds", "blind"):
        print(f"\n== mode: {mode}")
        tot: dict[str, list[list[int]]] = collections.defaultdict(lambda: [[0, 0, 0], [0, 0, 0]])
        for fx in common:
            for i, src in enumerate((old, new)):
                for j in range(3):
                    tot[old[fx]["camera_class"]][i][j] += src[fx][mode][j]
        for key in sorted(tot):
            o, n = tot[key]
            print(f"  {key:10s} old P/R/F1 {'/'.join(f'{v:.3f}' for v in prf(*o))} (FP {o[1]} FN {o[2]})   "
                  f"new {'/'.join(f'{v:.3f}' for v in prf(*n))} (FP {n[1]} FN {n[2]})")
        worse = [fx for fx in common if sum(new[fx][mode][1:]) > sum(old[fx][mode][1:])]
        better = [fx for fx in common if sum(new[fx][mode][1:]) < sum(old[fx][mode][1:])]
        print(f"  worse {len(worse)}, better {len(better)}, unchanged {len(common) - len(worse) - len(better)}")
        for fx in worse:
            o, n = old[fx][mode], new[fx][mode]
            print(f"    WORSE {fx}: FP {o[1]}->{n[1]} FN {o[2]}->{n[2]}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```

Run in the #1355 worktree: `uv run python scripts/eval_ensemble_artifacts.py ~/.claude-tmp/eval-1355.json` (copy the script there first; it is new). Run here: `uv run python scripts/eval_ensemble_artifacts.py ~/.claude-tmp/eval-relative.json`. Then `uv run python scripts/compare_ensemble_evals.py ~/.claude-tmp/eval-1355.json ~/.claude-tmp/eval-relative.json`.
Expected: headcam FP + FN in `rounds` mode not above the #1355 artifacts'. Every WORSE line goes into the PR.

- [ ] **Step 4: Tests**

Run: `uv run pytest -q`
Expected: only the known local failures (seven `drawtext` tests with Homebrew ffmpeg, `test_import_waitlist` under CEST). Prepend the project ffmpeg (`desktop/build/bin`) to `PATH` if it exists to clear the first seven.

- [ ] **Step 5: Document**

In `docs/cameras.md`, under Insta360 GO 3S, add a dated bullet: voter C now judges candidates relative to the stage's likely shots; held-out precision on Blacksmith 2026 GO 3S fell from 0.918 to <measured value> in exchange for surviving camera audio changes (accepted 2026-10-09; revert is a `git revert` of the PR). Under Meta Vanguard, add the measured Höstfinalen held-out recall. Update the `headcam-detection-meta-glasses` entry in `src/splitsmith/data/whats_new.json` only if #1355 has not been released; otherwise add a new entry in the house style (use the `whats-new` skill). Run `uv run pytest -n0 tests/test_whats_new.py`.

- [ ] **Step 6: Commit and open the PR**

```bash
git add src/splitsmith/data/ensemble_calibration.json src/splitsmith/data/voter_c_gbdt_headcam.onnx \
  src/splitsmith/data/voter_c_gbdt_handheld.onnx tests/data/voter_c_parity_reference.npz \
  scripts/eval_ensemble_artifacts.py scripts/compare_ensemble_evals.py docs/cameras.md src/splitsmith/data/whats_new.json
git commit -m "feat(ensemble): retrain voter C with stage-relative features"
```

PR body: the held-out table (headcam and handheld leave-one-match-out before and after), Höstfinalen Vanguard recall, the per-fixture comparison totals and every WORSE fixture, the accepted GO 3S trade, and the revert note. Base: `main` if #1355 has merged, else `chore/retrain-ensemble-hostfinalen`.
