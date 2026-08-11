"""Warm librosa/numba's on-disk JIT cache before the parallel test run.

Why this exists (#742): ``test_shot_detect``, ``test_tta_agreement`` and
``test_mine_negatives`` compile librosa's numba kernels the first time they
run. numba's ``.nbi``/``.nbc`` cache lives inside the installed package and
its writes are *not* atomic across processes. Under ``pytest -n auto`` the
CI venv is restored from cache with stale ``.nbi`` files that get invalidated
and recompiled, so several xdist workers race to rewrite the same cache file
at once and one segfaults ("worker 'gwN' crashed"). It does not reproduce
locally; it is a GitHub-runner-specific race.

Running this once, serially, *after* the venv is restored and *before*
pytest fans out populates the shared cache in a single process. Workers then
only read ``.nbi`` (concurrent reads are safe). Warm the real splitsmith
detection entrypoints -- not raw librosa -- so the exact numba signatures the
tests trigger are the ones compiled here.

Best-effort: this is a CI hygiene step, not a correctness gate. A failure to
warm a given path is reported and the script still exits 0 -- the worst case
is that the original race can recur, which the test run itself surfaces.
Genuine import breakage still exits non-zero so it is not mistaken for green.
"""

import sys

import numpy as np

from splitsmith.config import ShotDetectConfig


def _synthetic_stage(sample_rate: int, *, seconds: float = 2.0) -> np.ndarray:
    """A short mono signal with a few impulses -- enough to drive every
    onset/STFT/resample kernel down its real code path without depending on
    a fixture. Values are deterministic (no RNG) so the warm run is stable."""
    n = int(sample_rate * seconds)
    t = np.arange(n, dtype=np.float32) / sample_rate
    # Low-level broadband bed so onset strength has something to normalise
    # against, plus sharp impulses that read as shots.
    audio = 0.01 * np.sin(2.0 * np.pi * 220.0 * t).astype(np.float32)
    for shot_t in (0.30, 0.55, 0.95, 1.40):
        idx = int(shot_t * sample_rate)
        if idx < n:
            audio[idx : idx + 8] += 0.9
    return audio


def _warm(label: str, fn) -> bool:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 -- best-effort, report and continue
        print(f"  warn: {label} did not warm ({type(exc).__name__}: {exc})")
        return False
    print(f"  ok: {label}")
    return True


def main() -> int:
    sample_rate = 48000
    audio = _synthetic_stage(sample_rate)
    beep_time = 0.10
    stage_time = 1.50

    print("Warming librosa/numba JIT cache (#742) ...")

    # shot_detect.detect_shots -> librosa.onset.onset_strength /
    # onset_detect / frames_to_time
    from splitsmith import shot_detect

    cfg = ShotDetectConfig()
    _warm(
        "shot_detect.detect_shots",
        lambda: shot_detect.detect_shots(audio, sample_rate, beep_time, stage_time, cfg),
    )

    # ensemble.clap_mel.log_mel_input_features -> librosa.stft /
    # librosa.filters.mel
    from splitsmith.ensemble import clap_mel

    _warm(
        "ensemble.clap_mel.log_mel_input_features",
        lambda: clap_mel.log_mel_input_features(audio),
    )

    # ensemble.tta.compute_tta_agreement -> the perturbed detector passes
    from splitsmith.ensemble import tta

    base_candidates = np.array([0.30, 0.55, 0.95, 1.40], dtype=np.float64)
    _warm(
        "ensemble.tta.compute_tta_agreement",
        lambda: tta.compute_tta_agreement(audio, sample_rate, beep_time, stage_time, base_candidates),
    )

    # librosa.resample at the two rates features.py uses (CLAP_SR / PANN_SR)
    import librosa

    from splitsmith.ensemble.features import CLAP_SR, PANN_SR

    _warm(
        "librosa.resample -> CLAP_SR",
        lambda: librosa.resample(audio, orig_sr=sample_rate, target_sr=CLAP_SR),
    )
    _warm(
        "librosa.resample -> PANN_SR",
        lambda: librosa.resample(audio, orig_sr=sample_rate, target_sr=PANN_SR),
    )

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
