"""Re-aggregate cached multi-frame embeddings under mean / max / concat (#184).

The concat sweep widens the LR input from 512 to 1536 dims on a 419-sample
training pool -- close enough to the over-fit regime that per-fixture
variance dominates. Mean / max aggregations keep dim=512 and fold the
multi-frame information into a single vector. This script reads the
already-extracted embeddings from
``tests/fixtures/.cache/{fix}_visual_offsets_+0_+30_+80.npz``, reshapes
them into ``(N, 3, 512)``, and evaluates each aggregation rule with
leave-one-fixture-out logistic regression.

Run after ``sweep_multiframe_voter_e.py`` has populated the cache:

    uv run python scripts/sweep_multiframe_aggregations.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
CACHE_DIR = FIXTURES_DIR / ".cache"

SINGLE_KEY = "+0"
MULTI_KEY = "+0_+30_+80"
EMBED_DIM = 512


def _load_cache(fixture: str, key: str) -> dict | None:
    path = CACHE_DIR / f"{fixture}_visual_offsets_{key}.npz"
    if not path.exists():
        return None
    d = np.load(path, allow_pickle=True)
    sub_arr = d["subclasses"]
    return {
        "embeddings": d["embeddings"],
        "labels": d["labels"],
        "candidate_times": d["candidate_times"],
        "subclasses": [None if s == "" else str(s) for s in sub_arr.tolist()],
    }


def _is_head_go3s(fixture: str) -> bool:
    truth_path = FIXTURES_DIR / f"{fixture}.json"
    if not truth_path.exists():
        return False
    try:
        truth = json.loads(truth_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    cam = truth.get("camera") or {}
    return cam.get("id") == "go3s" and cam.get("mount") == "head"


def _make_views(multi_emb: np.ndarray) -> dict[str, np.ndarray]:
    """Reshape (N, 1536) -> (N, 3, 512); produce mean / max / concat views."""
    n = multi_emb.shape[0]
    stack = multi_emb.reshape(n, 3, EMBED_DIM)
    return {
        "concat": stack.reshape(n, 3 * EMBED_DIM),
        "mean": stack.mean(axis=1),
        "max": stack.max(axis=1),
        "first_only": stack[:, 0, :],  # sanity: should match the single-frame baseline
    }


def _train_lofo(
    embeddings: dict[str, np.ndarray],
    labels: dict[str, np.ndarray],
    subclasses: dict[str, list[str | None]],
) -> dict[str, np.ndarray]:
    fixtures = sorted(embeddings)
    out: dict[str, np.ndarray] = {}
    for held in fixtures:
        x_train_chunks: list[np.ndarray] = []
        ytr_chunks: list[np.ndarray] = []
        for f in fixtures:
            if f == held:
                continue
            sub = subclasses[f]
            mask = np.array(
                [(labels[f][i] == 1) or (sub[i] == "cross_bay") for i in range(len(sub))],
                dtype=bool,
            )
            x_train_chunks.append(embeddings[f][mask])
            ytr_chunks.append(labels[f][mask])
        x_train = np.concatenate(x_train_chunks, axis=0)
        ytr = np.concatenate(ytr_chunks, axis=0)
        if ytr.sum() == 0 or (ytr == 0).sum() == 0 or len(ytr) < 10:
            out[held] = np.zeros(embeddings[held].shape[0])
            continue
        clf = LogisticRegression(C=1.0, max_iter=2000, class_weight="balanced", solver="lbfgs")
        clf.fit(x_train, ytr)
        out[held] = clf.predict_proba(embeddings[held])[:, 1]
    return out


def _precision_at_recall(scores: np.ndarray, labels: np.ndarray, target: float) -> float:
    n_pos = int(labels.sum())
    if n_pos == 0:
        return float("nan")
    order = np.argsort(-scores)
    needed = math.ceil(target * n_pos)
    tp = 0
    for k, idx in enumerate(order, 1):
        if labels[idx] == 1:
            tp += 1
        if tp >= needed:
            return tp / k
    return float("nan")


def _summary(scores: np.ndarray, labels: np.ndarray) -> dict[str, float]:
    if labels.sum() == 0 or (labels == 0).sum() == 0 or scores.size == 0:
        return {"auc": float("nan"), "p_at_r_1.0": float("nan"), "p_at_r_0.95": float("nan")}
    return {
        "auc": float(roc_auc_score(labels, scores)),
        "p_at_r_1.0": _precision_at_recall(scores, labels, 1.0),
        "p_at_r_0.95": _precision_at_recall(scores, labels, 0.95),
    }


def main() -> int:
    audited = sorted(
        p.stem
        for p in FIXTURES_DIR.glob("stage-shots-*.json")
        if not any(skip in p.name for skip in ("peaks", "promotion-report", "iphone"))
    )

    multi_by_fixture: dict[str, dict] = {}
    for fixture in audited:
        if not _is_head_go3s(fixture):
            continue
        cached = _load_cache(fixture, MULTI_KEY)
        if cached is None:
            continue
        multi_by_fixture[fixture] = cached
    if not multi_by_fixture:
        print("No multi-frame cache found; run sweep_multiframe_voter_e.py first.")
        return 1

    # Build view dicts: aggregation_name -> {fixture -> embedding}
    views: dict[str, dict[str, np.ndarray]] = {
        "concat": {},
        "mean": {},
        "max": {},
        "first_only": {},
    }
    labels_by_fixture: dict[str, np.ndarray] = {}
    subclasses_by_fixture: dict[str, list[str | None]] = {}

    for fixture, data in multi_by_fixture.items():
        v = _make_views(data["embeddings"])
        for name, arr in v.items():
            views[name][fixture] = arr
        labels_by_fixture[fixture] = data["labels"]
        subclasses_by_fixture[fixture] = data["subclasses"]

    # Also load the original single-frame cache (true baseline; should
    # roughly match `first_only` from the multi cache since both are CLIP
    # features at offset 0, but cache provenance differs slightly).
    single_views: dict[str, np.ndarray] = {}
    for fixture in multi_by_fixture:
        cached = _load_cache(fixture, SINGLE_KEY)
        if cached is None:
            continue
        single_views[fixture] = cached["embeddings"]

    aggregations = list(views.keys())
    if single_views:
        views["single_baseline"] = single_views
        aggregations.insert(0, "single_baseline")

    per_fixture: dict[str, dict[str, dict]] = {agg: {} for agg in aggregations}
    aggregate: dict[str, dict[str, float]] = {}

    for agg in aggregations:
        held_out_scores = _train_lofo(views[agg], labels_by_fixture, subclasses_by_fixture)
        for fixture, scores in held_out_scores.items():
            per_fixture[agg][fixture] = _summary(scores, labels_by_fixture[fixture])
        all_scores = np.concatenate(list(held_out_scores.values()))
        all_labels = np.concatenate([labels_by_fixture[f] for f in held_out_scores])
        aggregate[agg] = _summary(all_scores, all_labels)

    print("=== aggregate (held-out across all fixtures pooled) ===")
    print(f"{'agg':18s}  {'AUC':>6s}  {'P@R=1.0':>8s}  {'P@R=0.95':>8s}")
    for agg in aggregations:
        a = aggregate[agg]
        print(f"{agg:18s}  {a['auc']:6.3f}  {a['p_at_r_1.0']:8.3f}  {a['p_at_r_0.95']:8.3f}")

    print("\n=== per-fixture AUC ===")
    fixtures = sorted(multi_by_fixture)
    header = f"{'fixture':48s}"
    for agg in aggregations:
        header += f"  {agg:>14s}"
    print(header)
    for f in fixtures:
        line = f"{f:48s}"
        for agg in aggregations:
            line += f"  {per_fixture[agg][f]['auc']:14.3f}"
        print(line)

    if "single_baseline" in aggregations:
        print("\n=== delta vs single_baseline (per-fixture AUC) ===")
        ranking_aggs = [a for a in aggregations if a != "single_baseline" and a != "first_only"]
        line = f"{'fixture':48s}"
        for agg in ranking_aggs:
            line += f"  {agg:>14s}"
        print(line)
        deltas: dict[str, list[float]] = {agg: [] for agg in ranking_aggs}
        for f in fixtures:
            base = per_fixture["single_baseline"][f]["auc"]
            line = f"{f:48s}"
            for agg in ranking_aggs:
                d = per_fixture[agg][f]["auc"] - base
                deltas[agg].append(d)
                line += f"  {d:+14.3f}"
            print(line)
        print()
        print(f"{'agg':18s}  {'mean delta':>10s}  {'regressions':>12s}")
        for agg in ranking_aggs:
            ds = deltas[agg]
            mean = sum(ds) / len(ds)
            regs = sum(1 for d in ds if d < 0)
            print(f"{agg:18s}  {mean:+10.3f}  {regs:>3d}/{len(ds):d}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
