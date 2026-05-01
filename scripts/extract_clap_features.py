"""Extract CLAP audio-text similarity features per detector candidate.

CLAP (Contrastive Language-Audio Pretraining, LAION) projects audio and text
into a shared embedding space. We:

1. Run ``detect_shots`` at max recall for each fixture.
2. For each candidate, slice a 1 s window centred on its time.
3. Encode the window with the CLAP audio encoder (512-dim).
4. Encode a fixed bank of text prompts with the CLAP text encoder (512-dim).
5. For each candidate, store cosine similarity to every prompt.

These similarities are interpretable signals: "how much does this audio match
the description 'a single gunshot at close range'?" The classifier can then
use the per-prompt similarity (or the differential between gunshot-like and
ambient-like prompts) as features.

Cache: ``tests/fixtures/.cache/{fixture}_clap.npz`` with arrays
- ``times``        (N,)
- ``audio_emb``    (N, 512)
- ``text_sims``    (N, P)  -- one column per prompt, cosine similarity
- ``prompts``      (P,)    -- the prompt strings, in the same order

Run:
    uv run python scripts/extract_clap_features.py
    uv run python scripts/extract_clap_features.py --force
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
    "stage-shots-blacksmith-2026-stage1",
    "stage-shots-blacksmith-2026-stage2",
    "stage-shots-blacksmith-2026-stage3",
    "stage-shots-blacksmith-2026-stage5",
    "stage-shots-blacksmith-2026-stage6",
    "stage-shots-blacksmith-2026-stage8",
    "stage-shots-tallmilan-stage2",
    "stage-shots-tallmilan-stage7",
    "stage-shots-tallmilan-2026-stage5",
    "stage-shots-tallmilan-2026-stage6",
]
FIXTURES_DIR = Path("tests/fixtures")
CACHE_DIR = FIXTURES_DIR / ".cache"
CLAP_SR = 48000  # CLAP-HTSAT native sample rate
WINDOW_S = 1.0
CLAP_MODEL_ID = "laion/clap-htsat-unfused"

# Prompts split into "shot-like" and "not-shot-like" so the classifier can
# also learn a differential. Order matters only for the cached column index.
PROMPTS_SHOT = [
    "a single gunshot at close range",
    "a loud handgun shot recorded with a body-worn microphone",
    "a sharp pistol shot in an outdoor competition",
    "a rapid sequence of pistol shots",
]
PROMPTS_NOT_SHOT = [
    "a distant gunshot echo from another shooting bay",
    "ambient outdoor environment noise",
    "wind blowing into a microphone",
    "footsteps on gravel",
    "a person speaking",
    "a metallic clang from a steel target falling",
]
PROMPTS = PROMPTS_SHOT + PROMPTS_NOT_SHOT


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


def extract_for_fixture(name: str, *, force: bool, model, processor) -> None:
    cache_path = CACHE_DIR / f"{name}_clap.npz"
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

    if sr != CLAP_SR:
        audio_clap = librosa.resample(audio.astype(np.float32), orig_sr=sr, target_sr=CLAP_SR)
    else:
        audio_clap = audio.astype(np.float32)
    windows = [_slice_window(audio_clap, CLAP_SR, float(t), WINDOW_S) for t in times]

    # Encode in modest-size batches to stay well below memory limits.
    import torch

    device = "cpu"
    audio_embeddings = []
    batch_size = 16
    for i in range(0, len(windows), batch_size):
        batch = windows[i : i + batch_size]
        inputs = processor(audio=batch, sampling_rate=CLAP_SR, return_tensors="pt")
        with torch.no_grad():
            out = model.get_audio_features(**{k: v.to(device) for k, v in inputs.items()})
        emb = out.pooler_output if hasattr(out, "pooler_output") else out
        audio_embeddings.append(emb.cpu().numpy())
    audio_emb = np.concatenate(audio_embeddings, axis=0).astype(np.float32)

    # Encode prompts once.
    text_inputs = processor(text=PROMPTS, return_tensors="pt", padding=True)
    with torch.no_grad():
        out = model.get_text_features(**{k: v.to(device) for k, v in text_inputs.items()})
    text_emb_t = out.pooler_output if hasattr(out, "pooler_output") else out
    text_emb = text_emb_t.cpu().numpy().astype(np.float32)

    # Cosine similarity (both normalised first).
    a_norm = audio_emb / (np.linalg.norm(audio_emb, axis=1, keepdims=True) + 1e-9)
    t_norm = text_emb / (np.linalg.norm(text_emb, axis=1, keepdims=True) + 1e-9)
    text_sims = a_norm @ t_norm.T  # (N, P)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(
        cache_path,
        times=times,
        audio_emb=audio_emb,
        text_sims=text_sims.astype(np.float32),
        prompts=np.array(PROMPTS, dtype=object),
    )
    print(
        f"  {name}: {len(times)} cands  audio_emb {audio_emb.shape}  "
        f"text_sims {text_sims.shape}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    # Lazy heavy imports; transformers and torch are dev-only.
    from transformers import ClapModel, ClapProcessor

    print(f"Loading CLAP: {CLAP_MODEL_ID}")
    print("(first run downloads ~600 MB to the HF cache)")
    model = ClapModel.from_pretrained(CLAP_MODEL_ID)
    processor = ClapProcessor.from_pretrained(CLAP_MODEL_ID)
    model.eval()

    fixtures = args.fixture or DEFAULT_FIXTURES
    print("\nExtracting CLAP features:")
    for f in fixtures:
        extract_for_fixture(f, force=args.force, model=model, processor=processor)
    print("\nDone. Cached to tests/fixtures/.cache/*_clap.npz")
    print(f"\nPrompts (in column order):")
    for i, prompt in enumerate(PROMPTS):
        flag = "[shot]" if prompt in PROMPTS_SHOT else "[not] "
        print(f"  {i:2d} {flag} {prompt}")


if __name__ == "__main__":
    main()
