"""Sweep Voter E negative-pool training rules (#187).

The shipped Voter E probe trains binary on shots vs ``cross_bay`` only.
That ignores all the other audit-JSON subclass labels (movement,
handling, echo, wind, speech, steel_ring, ...) and the much larger pool
of unlabeled non-shot candidates the universe detector emits.

This script reuses the cached single-frame CLIP embeddings (stored under
``tests/fixtures/.cache/{fix}_visual_offsets_+0.npz`` from the #184 sweep)
and trains four LR variants per fixture under leave-one-fixture-out CV:

* ``v0_cross_bay``           -- shots vs cross_bay (current shipped rule)
* ``all_labeled_balanced``   -- shots vs every subclass-labeled negative
* ``all_universe_balanced``  -- shots vs ALL non-shot candidates (labeled
  or not). Treats every detector universe candidate that didn't match a
  truth shot as a negative.
* ``all_universe_no_weight`` -- same as above but without ``class_weight=balanced``,
  to disentangle the wider-pool effect from the reweighting effect.

For each held-out fixture we compute ROC AUC on the full universe (the
score Voter E will produce in production) and precision-at-recall=1.0
on the cross_bay-only subset (the v0 metric the issue tracks). Aggregate
+ per-fixture deltas vs ``v0_cross_bay`` are printed.

Run after the #184 sweep has populated the single-frame cache:

    uv run python scripts/sweep_multiframe_voter_e.py        # if cache absent
    uv run python scripts/sweep_negative_pool_voter_e.py
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
SINGLE_OFFSETS_KEY = "+0"
EMBED_DIM = 512


VARIANTS = [
    "v0_cross_bay",
    "all_labeled_balanced",
    "all_universe_balanced",
    "all_universe_no_weight",
]


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


def _load_cache(fixture: str) -> dict | None:
    path = CACHE_DIR / f"{fixture}_visual_offsets_{SINGLE_OFFSETS_KEY}.npz"
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


def _train_mask_for_variant(variant: str, labels: np.ndarray, subclasses: list[str | None]) -> np.ndarray:
    """Boolean mask over a fixture's rows selecting which join the training pool.

    ``labels`` is the binary shot/non-shot vector; ``subclasses`` carry
    the audit-JSON subclass label (or None for unlabeled negatives).
    """
    n = len(labels)
    if variant == "v0_cross_bay":
        return np.array(
            [(labels[i] == 1) or (subclasses[i] == "cross_bay") for i in range(n)],
            dtype=bool,
        )
    if variant == "all_labeled_balanced":
        return np.array(
            [(labels[i] == 1) or (subclasses[i] is not None) for i in range(n)],
            dtype=bool,
        )
    # The two universe variants train on every row (positives + every
    # non-shot candidate, including unlabeled ones).
    return np.ones(n, dtype=bool)


def _class_weight_for_variant(variant: str):
    if variant == "all_universe_no_weight":
        return None
    return "balanced"


def _train_lofo(
    embeddings: dict[str, np.ndarray],
    labels: dict[str, np.ndarray],
    subclasses: dict[str, list[str | None]],
    variant: str,
) -> dict[str, np.ndarray]:
    """Score every fixture's full universe under the given training rule."""
    fixtures = sorted(embeddings)
    out: dict[str, np.ndarray] = {}
    for held in fixtures:
        x_train_chunks: list[np.ndarray] = []
        y_train_chunks: list[np.ndarray] = []
        for f in fixtures:
            if f == held:
                continue
            mask = _train_mask_for_variant(variant, labels[f], subclasses[f])
            x_train_chunks.append(embeddings[f][mask])
            y_train_chunks.append(labels[f][mask])
        x_train = np.concatenate(x_train_chunks, axis=0)
        y_train = np.concatenate(y_train_chunks, axis=0)
        if y_train.sum() == 0 or (y_train == 0).sum() == 0 or len(y_train) < 10:
            out[held] = np.zeros(embeddings[held].shape[0])
            continue
        clf = LogisticRegression(
            C=1.0,
            max_iter=2000,
            class_weight=_class_weight_for_variant(variant),
            solver="lbfgs",
        )
        clf.fit(x_train, y_train)
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


def _summary_full_universe(scores: np.ndarray, labels: np.ndarray) -> dict[str, float]:
    if labels.sum() == 0 or (labels == 0).sum() == 0 or scores.size == 0:
        return {"auc": float("nan"), "p_at_r_1.0": float("nan"), "p_at_r_0.95": float("nan")}
    return {
        "auc": float(roc_auc_score(labels, scores)),
        "p_at_r_1.0": _precision_at_recall(scores, labels, 1.0),
        "p_at_r_0.95": _precision_at_recall(scores, labels, 0.95),
    }


def _summary_cross_bay_only(
    scores: np.ndarray, labels: np.ndarray, subclasses: list[str | None]
) -> dict[str, float]:
    """v0 metric: AUC + P@R=1.0 restricted to shots + cross_bay rows.

    Lets us check whether the broader-negative-pool variants give up
    anything on the canonical v0 fold-by-fold report.
    """
    mask = np.array(
        [(labels[i] == 1) or (subclasses[i] == "cross_bay") for i in range(len(labels))],
        dtype=bool,
    )
    if mask.sum() == 0:
        return {"auc": float("nan"), "p_at_r_1.0": float("nan")}
    s = scores[mask]
    y = labels[mask]
    if y.sum() == 0 or (y == 0).sum() == 0:
        return {"auc": float("nan"), "p_at_r_1.0": float("nan")}
    return {
        "auc": float(roc_auc_score(y, s)),
        "p_at_r_1.0": _precision_at_recall(s, y, 1.0),
    }


def main() -> int:
    audited = sorted(
        p.stem
        for p in FIXTURES_DIR.glob("stage-shots-*.json")
        if not any(skip in p.name for skip in ("peaks", "promotion-report", "iphone"))
    )

    embeddings: dict[str, np.ndarray] = {}
    labels: dict[str, np.ndarray] = {}
    subclasses: dict[str, list[str | None]] = {}
    for fixture in audited:
        if not _is_head_go3s(fixture):
            continue
        cached = _load_cache(fixture)
        if cached is None:
            continue
        embeddings[fixture] = cached["embeddings"]
        labels[fixture] = cached["labels"]
        subclasses[fixture] = cached["subclasses"]

    if not embeddings:
        print("No single-frame embedding cache; run sweep_multiframe_voter_e.py first.")
        return 1

    pool_summary = []
    for fixture in sorted(embeddings):
        n = embeddings[fixture].shape[0]
        n_pos = int(labels[fixture].sum())
        n_cross = sum(1 for s in subclasses[fixture] if s == "cross_bay")
        n_labeled_neg = sum(1 for s in subclasses[fixture] if s is not None)
        pool_summary.append((fixture, n, n_pos, n_cross, n_labeled_neg))
    print(f"{'fixture':48s}  {'N':>4s}  {'pos':>4s}  {'cross_bay':>10s}  {'labeled_neg':>11s}")
    for f, n, p, c, lneg in pool_summary:
        print(f"{f:48s}  {n:4d}  {p:4d}  {c:10d}  {lneg:11d}")
    print()

    per_fixture_full: dict[str, dict[str, dict]] = {v: {} for v in VARIANTS}
    per_fixture_cb: dict[str, dict[str, dict]] = {v: {} for v in VARIANTS}
    aggregates_full: dict[str, dict[str, float]] = {}
    aggregates_cb: dict[str, dict[str, float]] = {}

    for variant in VARIANTS:
        scores_by_fixture = _train_lofo(embeddings, labels, subclasses, variant)
        all_scores: list[np.ndarray] = []
        all_labels: list[np.ndarray] = []
        all_subclasses: list[str | None] = []
        for fixture, s in scores_by_fixture.items():
            per_fixture_full[variant][fixture] = _summary_full_universe(s, labels[fixture])
            per_fixture_cb[variant][fixture] = _summary_cross_bay_only(
                s, labels[fixture], subclasses[fixture]
            )
            all_scores.append(s)
            all_labels.append(labels[fixture])
            all_subclasses.extend(subclasses[fixture])
        agg_scores = np.concatenate(all_scores)
        agg_labels = np.concatenate(all_labels)
        aggregates_full[variant] = _summary_full_universe(agg_scores, agg_labels)
        aggregates_cb[variant] = _summary_cross_bay_only(agg_scores, agg_labels, all_subclasses)

    print("=== aggregate (full universe ranking) ===")
    print(f"{'variant':28s}  {'AUC':>6s}  {'P@R=1.0':>8s}  {'P@R=0.95':>8s}")
    for v in VARIANTS:
        a = aggregates_full[v]
        print(f"{v:28s}  {a['auc']:6.3f}  {a['p_at_r_1.0']:8.3f}  {a['p_at_r_0.95']:8.3f}")

    print("\n=== aggregate (shots-vs-cross_bay subset; v0 metric) ===")
    print(f"{'variant':28s}  {'AUC':>6s}  {'P@R=1.0':>8s}")
    for v in VARIANTS:
        a = aggregates_cb[v]
        print(f"{v:28s}  {a['auc']:6.3f}  {a['p_at_r_1.0']:8.3f}")

    print("\n=== per-fixture AUC vs v0_cross_bay (full universe) ===")
    fixtures = sorted(embeddings)
    others = [v for v in VARIANTS if v != "v0_cross_bay"]
    line = f"{'fixture':48s}  {'v0':>8s}"
    for v in others:
        line += f"  {v:>22s}"
    print(line)
    deltas: dict[str, list[float]] = {v: [] for v in others}
    for f in fixtures:
        v0 = per_fixture_full["v0_cross_bay"][f]["auc"]
        line = f"{f:48s}  {v0:8.3f}"
        for v in others:
            other = per_fixture_full[v][f]["auc"]
            d = other - v0
            deltas[v].append(d)
            line += f"  {other:8.3f} ({d:+6.3f})"
        print(line)

    print()
    print(f"{'variant':28s}  {'mean delta':>10s}  {'regressions':>11s}")
    for v in others:
        ds = deltas[v]
        mean = sum(ds) / len(ds)
        regs = sum(1 for d in ds if d < 0)
        print(f"{v:28s}  {mean:+10.3f}  {regs:>3d}/{len(ds):d}")

    print("\n=== per-fixture P@R=1.0 (cross_bay subset, v0 metric) vs v0_cross_bay ===")
    line = f"{'fixture':48s}  {'v0':>8s}"
    for v in others:
        line += f"  {v:>22s}"
    print(line)
    for f in fixtures:
        v0 = per_fixture_cb["v0_cross_bay"][f]["p_at_r_1.0"]
        line = f"{f:48s}  {v0:8.3f}"
        for v in others:
            other = per_fixture_cb[v][f]["p_at_r_1.0"]
            d = (other if not math.isnan(other) else 0.0) - (v0 if not math.isnan(v0) else 0.0)
            line += f"  {other:8.3f} ({d:+6.3f})"
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
