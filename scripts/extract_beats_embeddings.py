"""Extract BEATs gunshot probabilities for each detector candidate.

Sibling of ``extract_audio_embeddings.py`` (the PANN extractor), used by
issue #179 to evaluate whether replacing Voter D's PANN backbone with
Microsoft BEATs improves consensus precision/recall on the audited
fixture set.

For each audited fixture, runs detect_shots at max recall
(recall_fallback=cwt, min_confidence=0.0). For each candidate, slices a
1 s window centred on the candidate, resamples to 16 kHz mono, and
runs BEATs to produce per-class AudioSet probabilities. The
``Gunshot, gunfire`` class (index 427) is what Voter D keys on; the
full clipwise vector is also cached so downstream analysis can compare
class-level separation between PANN and BEATs.

Cache layout matches the PANN extractor on purpose -- the calibration
script (``build_ensemble_artifacts.py``) routes between caches via a
single ``_voter_d_cache_path`` helper that keys on the backend name.

BEATs is dev-only -- not part of the runtime pipeline. It also isn't on
PyPI; install the official Microsoft codebase locally
(``pip install -e <microsoft/unilm>/beats``) and either point
``--checkpoint`` at the .pt file or set the
``SPLITSMITH_BEATS_CHECKPOINT`` environment variable.

Run:
    uv run python scripts/extract_beats_embeddings.py --checkpoint /path/to/BEATs_iter3_plus.pt
    uv run python scripts/extract_beats_embeddings.py --force  # re-extract all
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import librosa
import numpy as np

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.ensemble.features import (
    BEATS_CHECKPOINT_ENV,
    BEATS_GUNSHOT_CLASS_INDEX,
    BEATS_SR,
    BEATS_WINDOW_S,
    load_beats_runtime,
)
from splitsmith.shot_detect import detect_shots

# Match the PANN extractor's fixture list so calibration sees the same
# stems regardless of which backend was extracted last. Drift between
# the two lists silently shrinks the calibration set when switching.
DEFAULT_FIXTURES = [
    "stage-shots-tallmilan-2026-stage3",
    "stage-shots-blacksmith-2026-stage7",
    "stage-shots-blacksmith-2026-stage1",
    "stage-shots-blacksmith-2026-stage2",
    "stage-shots-blacksmith-2026-stage3",
    "stage-shots-blacksmith-2026-stage5",
    "stage-shots-blacksmith-2026-stage6",
    "stage-shots-blacksmith-2026-stage8",
    "stage-shots-tallmilan-2026-stage2",
    "stage-shots-tallmilan-2026-stage7",
    "stage-shots-tallmilan-2026-stage5",
    "stage-shots-tallmilan-2026-stage6",
    "stage-shots-tallmilan-2026-stage2-apple-iphone17pro",
    "stage-shots-tallmilan-2026-stage4-apple-iphone17pro",
    "stage-shots-tallmilan-2026-stage5-apple-iphone17pro",
    "stage-shots-tallmilan-2026-stage6-apple-iphone17pro",
    "stage-shots-tallmilan-2026-stage7-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage1-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage2-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage3-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage5-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage6-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage7-apple-iphone17pro",
    "stage-shots-blacksmith-2026-stage8-apple-iphone17pro",
]
FIXTURES_DIR = Path("tests/fixtures")
FULL_DIR = FIXTURES_DIR / "full"
CACHE_DIR = FIXTURES_DIR / ".cache"


def _slice_window(audio: np.ndarray, sr: int, t: float, win_s: float) -> np.ndarray:
    half = int(round(sr * win_s / 2.0))
    centre = int(round(t * sr))
    lo = max(0, centre - half)
    hi = min(audio.size, centre + half)
    chunk = audio[lo:hi].astype(np.float32)
    target_len = int(round(sr * win_s))
    if chunk.size < target_len:
        pad = target_len - chunk.size
        left = pad // 2
        right = pad - left
        chunk = np.pad(chunk, (left, right), mode="constant")
    return chunk[:target_len]


def _resolve_audio_paths(name: str, *, full: bool) -> tuple[Path, Path, float, float]:
    """Return ``(wav_path, cache_path, beep_time, stage_time)``.

    Mirrors the PANN extractor's resolver -- ``--full`` reads the
    wide-window WAV and writes a parallel ``*_beats_full.npz`` so the
    short-fixture cache is never overwritten.
    """
    if full:
        wav_path = FULL_DIR / f"{name}_full.wav"
        cache_path = CACHE_DIR / f"{name}_beats_full.npz"
        from wave import open as wave_open

        with wave_open(str(wav_path), "rb") as w:
            duration = w.getnframes() / w.getframerate()
        return wav_path, cache_path, 0.0, duration
    truth = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
    return (
        FIXTURES_DIR / f"{name}.wav",
        CACHE_DIR / f"{name}_beats.npz",
        float(truth["beep_time"]),
        float(truth["stage_time_seconds"]),
    )


def extract_for_fixture(name: str, *, force: bool, beats_runtime, full: bool = False) -> None:
    wav_path, cache_path, beep_time, stage_time = _resolve_audio_paths(name, full=full)
    if cache_path.exists() and not force:
        print(f"  {name}: cached -> {cache_path}")
        return
    if not wav_path.exists():
        print(f"  {name}: WAV missing at {wav_path}, skipping")
        return

    audio, sr = load_audio(wav_path)
    config = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
    shots = detect_shots(audio, sr, beep_time, stage_time, config)
    if not shots:
        print(f"  {name}: no candidates, skipping")
        return

    times = np.array([s.time_absolute for s in shots], dtype=np.float64)

    if sr != BEATS_SR:
        audio_beats = librosa.resample(audio.astype(np.float32), orig_sr=sr, target_sr=BEATS_SR)
    else:
        audio_beats = audio.astype(np.float32)
    batch = np.stack(
        [_slice_window(audio_beats, BEATS_SR, float(t), BEATS_WINDOW_S) for t in times], axis=0
    )

    import torch

    batch_t = torch.from_numpy(batch).to(beats_runtime.device)
    probs_t = beats_runtime.model.predict_probs(batch_t)
    clipwise = probs_t.detach().cpu().numpy().astype(np.float32)
    gunshot_prob = clipwise[:, BEATS_GUNSHOT_CLASS_INDEX]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(
        cache_path,
        times=times,
        clipwise=clipwise,
        gunshot_prob=gunshot_prob,
    )
    print(
        f"  {name}: {len(times)} cands  clipwise {clipwise.shape}  "
        f"gunshot_prob mean={gunshot_prob.mean():.3f} max={gunshot_prob.max():.3f}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append")
    p.add_argument("--force", action="store_true", help="Re-extract even if cache exists")
    p.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help=(
            "Path to a BEATs .pt checkpoint. Falls back to the "
            f"{BEATS_CHECKPOINT_ENV} environment variable when omitted."
        ),
    )
    p.add_argument(
        "--full",
        action="store_true",
        help=(
            "Read tests/fixtures/full/{name}_full.wav (wide-window audio "
            "from scripts/extract_full_fixture_audio.py) and write a "
            "parallel *_beats_full.npz cache."
        ),
    )
    args = p.parse_args()

    checkpoint = args.checkpoint or (
        Path(os.environ[BEATS_CHECKPOINT_ENV]) if BEATS_CHECKPOINT_ENV in os.environ else None
    )
    if checkpoint is None:
        raise SystemExit(
            "BEATs checkpoint not provided. Pass --checkpoint or set "
            f"{BEATS_CHECKPOINT_ENV}=<path-to-BEATs.pt>."
        )

    print(f"Loading BEATs from {checkpoint}...")
    runtime = load_beats_runtime(checkpoint_path=checkpoint)

    fixtures = args.fixture or DEFAULT_FIXTURES
    print("Extracting BEATs gunshot probabilities:")
    for f in fixtures:
        extract_for_fixture(f, force=args.force, beats_runtime=runtime, full=args.full)
    suffix = "_beats_full" if args.full else "_beats"
    print(f"\nDone. Cached to tests/fixtures/.cache/*{suffix}.npz")


if __name__ == "__main__":
    main()
