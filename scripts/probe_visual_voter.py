"""Visual-voter v0 probe: zero-shot CLIP scoring of candidate-time frames.

For each audited go3s/head fixture with a reachable ``source_video``,
extract one frame at every candidate timestamp and score it against a
zero-shot CLIP prompt bank (shot-positive vs distractor prompts).

Outputs per-fixture JSON sidecars under ``build/visual-probe/`` for
``eval_visual_voter.py`` to consume. No ensemble integration; this is
purely a feasibility probe per issue #10.

Run:
    uv run python scripts/probe_visual_voter.py
    uv run python scripts/probe_visual_voter.py --fixture stage-shots-blacksmith-2026-stage3-s97dcec94
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path

import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
OUT_DIR = REPO_ROOT / "build" / "visual-probe"
FRAME_CACHE_DIR = OUT_DIR / "frames"

CLIP_MODEL_ID = "openai/clip-vit-base-patch32"

POSITIVE_PROMPTS = [
    "a person aiming a pistol at a steel target on a shooting range",
    "a shooter firing a handgun on a shooting range",
    "a first-person view of a hand holding a pistol pointed downrange",
    "muzzle flash from a pistol being fired",
]

NEGATIVE_PROMPTS = [
    "a person walking on a shooting range",
    "a person reloading a pistol on a shooting range",
    "a shooting range with no one shooting",
    "a person standing still on a shooting range",
    "a holstered pistol on a shooter's belt",
    "an empty shooting bay",
]


def iter_target_fixtures(only: str | None) -> Iterable[Path]:
    for path in sorted(FIXTURES_DIR.glob("stage-shots-*.json")):
        name = path.stem
        if any(skip in name for skip in ("peaks", "promotion-report", "iphone")):
            continue
        if only and only not in name:
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        camera = data.get("camera") or {}
        if camera.get("id") != "go3s" or camera.get("mount") != "head":
            continue
        src = data.get("source_video")
        if not src:
            continue
        if not Path(src).exists():
            print(f"  SKIP (video missing): {path.name}", file=sys.stderr)
            continue
        yield path


def extract_frame(video: Path, source_time: float, dest: Path) -> bool:
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    pre_seek = max(0.0, source_time - 2.0)
    fine_seek = source_time - pre_seek
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{pre_seek:.3f}",
        "-i",
        str(video),
        "-ss",
        f"{fine_seek:.3f}",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-y",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not dest.exists():
        print(
            f"    ffmpeg failed at t={source_time:.3f} for {video.name}: " f"{result.stderr.strip()[:200]}",
            file=sys.stderr,
        )
        return False
    return True


def load_clip(device: str) -> tuple[CLIPModel, CLIPProcessor, torch.Tensor, torch.Tensor]:
    print(f"Loading CLIP ({CLIP_MODEL_ID}) on {device}...", file=sys.stderr)
    model = CLIPModel.from_pretrained(CLIP_MODEL_ID).to(device).eval()
    processor = CLIPProcessor.from_pretrained(CLIP_MODEL_ID)
    with torch.no_grad():
        text_inputs = processor(
            text=POSITIVE_PROMPTS + NEGATIVE_PROMPTS,
            return_tensors="pt",
            padding=True,
        ).to(device)
        text_feats = model.get_text_features(**text_inputs).pooler_output
        text_feats = text_feats / text_feats.norm(dim=-1, keepdim=True)
    pos_feats = text_feats[: len(POSITIVE_PROMPTS)]
    neg_feats = text_feats[len(POSITIVE_PROMPTS) :]
    return model, processor, pos_feats, neg_feats


def score_frames(
    model: CLIPModel,
    processor: CLIPProcessor,
    pos_feats: torch.Tensor,
    neg_feats: torch.Tensor,
    images: list[Image.Image],
    device: str,
    batch: int = 32,
) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(images), batch):
        chunk = images[i : i + batch]
        inputs = processor(images=chunk, return_tensors="pt").to(device)
        with torch.no_grad():
            feats = model.get_image_features(**inputs).pooler_output
            feats = feats / feats.norm(dim=-1, keepdim=True)
            pos_sims = feats @ pos_feats.T
            neg_sims = feats @ neg_feats.T
        pos_mean = pos_sims.mean(dim=-1).cpu().tolist()
        pos_max = pos_sims.max(dim=-1).values.cpu().tolist()
        neg_mean = neg_sims.mean(dim=-1).cpu().tolist()
        for pm, pmax, nm in zip(pos_mean, pos_max, neg_mean, strict=True):
            out.append(
                {
                    "clip_pos_mean": pm,
                    "clip_pos_max": pmax,
                    "clip_neg_mean": nm,
                    "clip_diff": pm - nm,
                }
            )
    return out


def probe_fixture(
    fixture_path: Path,
    model: CLIPModel,
    processor: CLIPProcessor,
    pos_feats: torch.Tensor,
    neg_feats: torch.Tensor,
    device: str,
) -> dict:
    data = json.loads(fixture_path.read_text())
    candidates = ((data.get("_candidates_pending_audit") or {}).get("candidates")) or []
    window = data.get("fixture_window_in_source") or [0.0, 0.0]
    src_offset = float(window[0])
    video = Path(data["source_video"])
    shots_by_cand = {s.get("candidate_number"): s for s in (data.get("shots") or [])}
    labels_by_time = ((data.get("_candidates_pending_audit") or {}).get("labels_by_time")) or {}

    print(f"\n{fixture_path.stem}: {len(candidates)} candidates", file=sys.stderr)

    extracted: list[tuple[dict, Path]] = []
    extract_t0 = time.time()
    for idx, cand in enumerate(candidates):
        fixture_time = float(cand["time"])
        source_time = src_offset + fixture_time
        frame_path = (
            FRAME_CACHE_DIR
            / fixture_path.stem
            / f"cand_{cand['candidate_number']:04d}_t{int(round(source_time*1000)):08d}.jpg"
        )
        if extract_frame(video, source_time, frame_path):
            extracted.append((cand, frame_path))
        if (idx + 1) % 25 == 0:
            print(
                f"  extracted {idx+1}/{len(candidates)} " f"(elapsed {time.time()-extract_t0:.1f}s)",
                file=sys.stderr,
            )
    print(
        f"  frame extraction: {len(extracted)}/{len(candidates)} ok " f"in {time.time()-extract_t0:.1f}s",
        file=sys.stderr,
    )

    images = [Image.open(p).convert("RGB") for _, p in extracted]
    score_t0 = time.time()
    scores = score_frames(model, processor, pos_feats, neg_feats, images, device)
    print(f"  CLIP scoring: {len(scores)} frames in {time.time()-score_t0:.1f}s", file=sys.stderr)

    rows: list[dict] = []
    for (cand, frame_path), score in zip(extracted, scores, strict=True):
        cn = cand["candidate_number"]
        is_shot = cn in shots_by_cand
        time_key = f"{float(cand['time']):.3f}"
        rows.append(
            {
                "candidate_number": cn,
                "fixture_time": float(cand["time"]),
                "source_time": src_offset + float(cand["time"]),
                "audio_confidence": float(cand.get("confidence", 0.0)),
                "audio_peak_amplitude": float(cand.get("peak_amplitude", 0.0)),
                "is_shot": bool(is_shot),
                "subclass": (shots_by_cand.get(cn) or {}).get("subclass")
                or labels_by_time.get(time_key)
                or None,
                "frame": str(frame_path.relative_to(REPO_ROOT)),
                **score,
            }
        )

    return {
        "fixture": fixture_path.name,
        "stage_number": data.get("stage_number"),
        "source_video": str(video),
        "fixture_window_in_source": window,
        "clip_model": CLIP_MODEL_ID,
        "positive_prompts": POSITIVE_PROMPTS,
        "negative_prompts": NEGATIVE_PROMPTS,
        "n_candidates": len(candidates),
        "n_scored": len(rows),
        "candidates": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        help="substring filter on fixture stem (e.g. blacksmith-2026-stage3)",
    )
    parser.add_argument(
        "--device",
        default="mps" if torch.backends.mps.is_available() else "cpu",
        choices=("cpu", "mps", "cuda"),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="limit number of candidates per fixture (for smoke tests)",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FRAME_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    fixtures = list(iter_target_fixtures(args.fixture))
    if not fixtures:
        print("No matching fixtures found.", file=sys.stderr)
        return 1
    print(f"Probing {len(fixtures)} fixtures", file=sys.stderr)

    model, processor, pos_feats, neg_feats = load_clip(args.device)

    for fixture_path in fixtures:
        if args.limit:
            data = json.loads(fixture_path.read_text())
            cands = ((data.get("_candidates_pending_audit") or {}).get("candidates")) or []
            data["_candidates_pending_audit"]["candidates"] = cands[: args.limit]
            tmp = OUT_DIR / f"_tmp_{fixture_path.name}"
            tmp.write_text(json.dumps(data))
            try:
                report = probe_fixture(tmp, model, processor, pos_feats, neg_feats, args.device)
                report["fixture"] = fixture_path.name
            finally:
                tmp.unlink(missing_ok=True)
        else:
            report = probe_fixture(fixture_path, model, processor, pos_feats, neg_feats, args.device)
        out_path = OUT_DIR / f"{fixture_path.stem}.json"
        out_path.write_text(json.dumps(report, indent=2))
        print(f"  wrote {out_path.relative_to(REPO_ROOT)}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
