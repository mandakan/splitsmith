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
    "stage-shots",
    "stage-shots-blacksmith-h5",
    "stage-shots-blacksmith-2026-stage2",
    "stage-shots-tallmilan-stage2",
    "stage-shots-tallmilan-stage7",
    "stage-shots-tallmilan-2026-stage6",
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


def extract_for_fixture(name: str, *, force: bool, audio_tagger) -> None:
    cache_path = CACHE_DIR / f"{name}_pann.npz"
    if cache_path.exists() and not force:
        print(f"  {name}: cached -> {cache_path}")
        return

    truth = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
    audio, sr = load_audio(FIXTURES_DIR / f"{name}.wav")
    config = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
    shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], config)
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
    args = p.parse_args()

    # Lazy import: torch + panns-inference are heavy; keep import out of the
    # train_classifier path where the user may opt out of NN features.
    from panns_inference import AudioTagging

    print("Loading PANNs CNN14 (first run downloads ~80 MB checkpoint to ~/panns_data/)...")
    at = AudioTagging(checkpoint_path=None, device="cpu")

    fixtures = args.fixture or DEFAULT_FIXTURES
    print("Extracting embeddings:")
    for f in fixtures:
        extract_for_fixture(f, force=args.force, audio_tagger=at)
    print("\nDone. Cached to tests/fixtures/.cache/*_pann.npz")


if __name__ == "__main__":
    main()
