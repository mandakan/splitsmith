"""Few-shot CLIP linear probe: shot vs cross_bay binary classifier.

Loads cached frames from ``build/visual-probe/frames/``, computes CLIP
image embeddings, trains a logistic regression with leave-one-fixture-out
cross-validation, and writes ``clip_probe_score`` back into each
fixture's probe sidecar so ``eval_visual_voter.py`` can re-rank.

Negatives = candidates labeled ``cross_bay`` (the dominant FP class
zero-shot CLIP couldn't separate). All other negative subclasses still
get scored at inference time but don't enter training.

Run:
    uv run python scripts/probe_visual_voter.py        # produces sidecars + frames
    uv run python scripts/train_visual_probe.py        # adds clip_probe_score
    uv run python scripts/eval_visual_voter.py         # re-evaluates
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import LeaveOneGroupOut
from transformers import CLIPModel, CLIPProcessor

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_DIR = REPO_ROOT / "build" / "visual-probe"

CLIP_MODEL_ID = "openai/clip-vit-base-patch32"


def load_clip(device: str) -> tuple[CLIPModel, CLIPProcessor]:
    print(f"Loading CLIP ({CLIP_MODEL_ID}) on {device}...", file=sys.stderr)
    model = CLIPModel.from_pretrained(CLIP_MODEL_ID).to(device).eval()
    processor = CLIPProcessor.from_pretrained(CLIP_MODEL_ID)
    return model, processor


def embed_frames(
    model: CLIPModel,
    processor: CLIPProcessor,
    paths: list[Path],
    device: str,
    batch: int = 32,
) -> np.ndarray:
    embeds: list[np.ndarray] = []
    for i in range(0, len(paths), batch):
        chunk = [Image.open(p).convert("RGB") for p in paths[i : i + batch]]
        inputs = processor(images=chunk, return_tensors="pt").to(device)
        with torch.no_grad():
            feats = model.get_image_features(**inputs).pooler_output
            feats = feats / feats.norm(dim=-1, keepdim=True)
        embeds.append(feats.cpu().numpy().astype(np.float32))
    return np.vstack(embeds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device",
        default="mps" if torch.backends.mps.is_available() else "cpu",
        choices=("cpu", "mps", "cuda"),
    )
    parser.add_argument("--probe-dir", default=str(PROBE_DIR))
    parser.add_argument(
        "--C", type=float, default=1.0, help="logistic regression inverse regularization"
    )
    args = parser.parse_args()

    probe_dir = Path(args.probe_dir)
    sidecars: list[tuple[Path, dict]] = []
    for path in sorted(probe_dir.glob("stage-shots-*.json")):
        if path.name.startswith("_tmp_"):
            continue
        sidecars.append((path, json.loads(path.read_text())))
    if not sidecars:
        print(f"No probe sidecars in {probe_dir}", file=sys.stderr)
        return 1

    rows: list[tuple[int, str, int, Path, bool, str | None]] = []
    fixture_index: dict[str, int] = {}
    for path, probe in sidecars:
        fname = probe["fixture"]
        if fname not in fixture_index:
            fixture_index[fname] = len(fixture_index)
        gid = fixture_index[fname]
        for r in probe.get("candidates") or []:
            rows.append(
                (
                    gid,
                    fname,
                    int(r["candidate_number"]),
                    REPO_ROOT / r["frame"],
                    bool(r["is_shot"]),
                    r.get("subclass"),
                )
            )

    print(
        f"Loaded {len(rows)} candidate rows across {len(fixture_index)} fixtures",
        file=sys.stderr,
    )

    model, processor = load_clip(args.device)
    t0 = time.time()
    embeds = embed_frames(
        model, processor, [r[3] for r in rows], args.device, batch=64
    )
    print(f"Embedded {len(rows)} frames in {time.time()-t0:.1f}s", file=sys.stderr)

    is_shot = np.array([r[4] for r in rows], dtype=bool)
    subclass = np.array([r[5] or "" for r in rows])
    groups = np.array([r[0] for r in rows])

    train_mask = is_shot | (subclass == "cross_bay")
    print(
        f"Training pool: {train_mask.sum()} samples "
        f"(shots={is_shot.sum()}, cross_bay={(subclass=='cross_bay').sum()})",
        file=sys.stderr,
    )

    held_out_scores = np.full(len(rows), np.nan, dtype=np.float32)
    fold_aucs: list[tuple[str, float, int, int]] = []

    logo = LeaveOneGroupOut()
    fixture_names = {gid: name for name, gid in fixture_index.items()}

    for train_idx, test_idx in logo.split(embeds, is_shot.astype(int), groups):
        gid = int(groups[test_idx[0]])
        held_name = fixture_names[gid]

        train_pool = train_idx[train_mask[train_idx]]
        if len(train_pool) < 10:
            print(f"  fold {held_name}: skipping (only {len(train_pool)} train samples)")
            continue
        Xtr = embeds[train_pool]
        ytr = is_shot[train_pool].astype(int)
        if ytr.sum() == 0 or ytr.sum() == len(ytr):
            print(f"  fold {held_name}: skipping (degenerate labels)")
            continue

        clf = LogisticRegression(
            C=args.C, max_iter=1000, class_weight="balanced", solver="lbfgs"
        )
        clf.fit(Xtr, ytr)

        Xte = embeds[test_idx]
        proba = clf.predict_proba(Xte)[:, 1]
        held_out_scores[test_idx] = proba

        eval_mask = is_shot[test_idx] | (subclass[test_idx] == "cross_bay")
        if eval_mask.sum() >= 4 and is_shot[test_idx][eval_mask].sum() > 0:
            auc = roc_auc_score(is_shot[test_idx][eval_mask].astype(int), proba[eval_mask])
            fold_aucs.append(
                (
                    held_name,
                    auc,
                    int(eval_mask.sum()),
                    int(is_shot[test_idx][eval_mask].sum()),
                )
            )
            print(
                f"  fold {held_name}: AUC(shot vs cross_bay) = {auc:.3f} "
                f"(n_eval={eval_mask.sum()}, n_shot={is_shot[test_idx][eval_mask].sum()})"
            )

    print()
    if fold_aucs:
        mean_auc = float(np.mean([a for _, a, _, _ in fold_aucs]))
        print(f"Mean leave-one-out AUC (shot vs cross_bay only): {mean_auc:.3f}")

    by_fixture: dict[str, dict[int, float]] = {}
    for (_, fname, cn, _, _, _), score in zip(rows, held_out_scores):
        if not np.isnan(score):
            by_fixture.setdefault(fname, {})[cn] = float(score)

    for path, probe in sidecars:
        scores = by_fixture.get(probe["fixture"], {})
        for r in probe.get("candidates") or []:
            cn = int(r["candidate_number"])
            r["clip_probe_score"] = scores.get(cn)
        path.write_text(json.dumps(probe, indent=2))
    print(f"Wrote clip_probe_score into {len(sidecars)} sidecars", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
