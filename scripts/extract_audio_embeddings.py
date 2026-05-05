"""Extract PANNs CNN14 embeddings for each detector candidate.

For each audited fixture, runs detect_shots at max recall (recall_fallback=cwt,
min_confidence=0.0). For each candidate, slices a 1 s window centred on the
candidate, resamples to 32 kHz mono, and runs PANNs CNN14 to produce:

* 2048-dim penultimate-layer embedding
* 527-dim AudioSet class probabilities (sigmoid)

Results cached as ``tests/fixtures/.cache/{fixture}_pann.npz`` so re-runs of
the classifier script don't re-do the slow forward pass. Class index 427 is
"Gunshot, gunfire" in the AudioSet ontology.

PANNs is dev-only -- not part of the runtime pipeline. Heavy deps (torch,
panns-inference) live in [dependency-groups.dev].

Run:
    uv run python scripts/extract_audio_embeddings.py
    uv run python scripts/extract_audio_embeddings.py --force  # re-extract all
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import librosa
import numpy as np

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.shot_detect import detect_shots

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
    # Phone-cam fixtures (PR #134 + PR #152).
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
CACHE_DIR = FIXTURES_DIR / ".cache"
PANN_SR = 32000  # PANNs CNN14 native rate
WINDOW_S = 1.0  # 1 s window centred on candidate
GUNSHOT_CLASS_INDEX = 427  # "Gunshot, gunfire" in AudioSet


def _slice_window(audio: np.ndarray, sr: int, t: float, win_s: float) -> np.ndarray:
    half = int(round(sr * win_s / 2.0))
    centre = int(round(t * sr))
    lo = max(0, centre - half)
    hi = min(audio.size, centre + half)
    chunk = audio[lo:hi].astype(np.float32)
    target_len = int(round(sr * win_s))
    if chunk.size < target_len:
        pad = target_len - chunk.size
        # zero-pad symmetrically
        left = pad // 2
        right = pad - left
        chunk = np.pad(chunk, (left, right), mode="constant")
    return chunk[:target_len]


FULL_DIR = FIXTURES_DIR / "full"


def _resolve_audio_paths(name: str, *, full: bool) -> tuple[Path, Path, float, float]:
    """Return ``(wav_path, cache_path, beep_time, stage_time)`` for a candidate run.

    In ``full`` mode the wide-window WAV is scanned end-to-end (beep_time=0,
    stage_time=duration), and the cache is written to a parallel ``*_pann_full.npz``
    so the short-fixture cache is never overwritten. The caller still validates
    that the cache row count matches detect_shots output.
    """
    if full:
        wav_path = FULL_DIR / f"{name}_full.wav"
        cache_path = CACHE_DIR / f"{name}_pann_full.npz"
        from wave import open as wave_open

        with wave_open(str(wav_path), "rb") as w:
            duration = w.getnframes() / w.getframerate()
        return wav_path, cache_path, 0.0, duration
    truth = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
    return (
        FIXTURES_DIR / f"{name}.wav",
        CACHE_DIR / f"{name}_pann.npz",
        float(truth["beep_time"]),
        float(truth["stage_time_seconds"]),
    )


def extract_for_fixture(name: str, *, force: bool, audio_tagger, full: bool = False) -> None:
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

    # Build batch of 1 s windows at 32 kHz.
    if sr != PANN_SR:
        audio_pann = librosa.resample(audio.astype(np.float32), orig_sr=sr, target_sr=PANN_SR)
    else:
        audio_pann = audio.astype(np.float32)
    batch = np.stack(
        [_slice_window(audio_pann, PANN_SR, float(t), WINDOW_S) for t in times], axis=0
    )

    # PANNs.inference returns (clipwise_output, embedding) for batch input.
    clipwise, embedding = audio_tagger.inference(batch)
    clipwise = np.asarray(clipwise, dtype=np.float32)
    embedding = np.asarray(embedding, dtype=np.float32)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(
        cache_path,
        times=times,
        embedding=embedding,
        clipwise=clipwise,
        gunshot_prob=clipwise[:, GUNSHOT_CLASS_INDEX],
    )
    print(
        f"  {name}: {len(times)} cands  embedding {embedding.shape}  "
        f"gunshot_prob mean={clipwise[:, GUNSHOT_CLASS_INDEX].mean():.3f} "
        f"max={clipwise[:, GUNSHOT_CLASS_INDEX].max():.3f}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append")
    p.add_argument("--force", action="store_true", help="Re-extract even if cache exists")
    p.add_argument(
        "--full",
        action="store_true",
        help=(
            "Read tests/fixtures/full/{name}_full.wav (wide-window audio from "
            "scripts/extract_full_fixture_audio.py) and write a parallel "
            "*_pann_full.npz cache. Used by mine_negatives.py + the ensemble "
            "build script for issue #87 hard-negative mining."
        ),
    )
    args = p.parse_args()

    # Lazy import: torch + panns-inference are heavy; keep import out of the
    # train_classifier path where the user may opt out of NN features.
    from panns_inference import AudioTagging

    print("Loading PANNs CNN14 (first run downloads ~80 MB checkpoint to ~/panns_data/)...")
    at = AudioTagging(checkpoint_path=None, device="cpu")

    fixtures = args.fixture or DEFAULT_FIXTURES
    print("Extracting embeddings:")
    for f in fixtures:
        extract_for_fixture(f, force=args.force, audio_tagger=at, full=args.full)
    suffix = "_pann_full" if args.full else "_pann"
    print(f"\nDone. Cached to tests/fixtures/.cache/*{suffix}.npz")


if __name__ == "__main__":
    main()
