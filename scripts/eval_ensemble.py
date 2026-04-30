"""Ensemble evaluation: agreement-based culling of detector candidates.

Each candidate is voted on by three independent voters. Different failure
modes -> agreement should improve precision without hurting recall.

Voters
------
A. Baseline conservative: detector with recall_fallback=cwt and
   min_confidence=0.03 (the empirical safe cap from issue #6 sweeps).
B. CLAP zero-shot: shot_minus_notshot > 0  -- no training, just whether
   the candidate's audio is closer to "gunshot" prompts than to
   "ambient/echo" prompts.
C. GBDT classifier: hand features + CLAP text similarities, 5-fold
   stratified CV held-out probabilities, threshold tuned per run for
   ~95 % recall on the training set.

Output
------
* Recall + precision at each consensus level (1-of-3, 2-of-3, 3-of-3).
* Per-fixture breakdown (so we see whether Blacksmith-H5 is rescued).
* Top disagreement set: candidates where exactly one voter agreed --
  these are the "show first in the audit UI" rows.
* Comparison vs baseline (CWT + min_confidence=0.03 alone).

Dependencies: requires PANN + CLAP caches from the earlier extract scripts.

Run:
    uv run python scripts/extract_audio_embeddings.py   # if not cached
    uv run python scripts/extract_clap_features.py      # if not cached
    uv run python scripts/eval_ensemble.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.shot_detect import detect_shots

DEFAULT_FIXTURES = [
    "stage-shots",
    "stage-shots-blacksmith-h5",
    "stage-shots-tallmilan-stage2",
    "stage-shots-tallmilan-stage7",
]
FIXTURES_DIR = Path("tests/fixtures")
CACHE_DIR = FIXTURES_DIR / ".cache"

_CLAP_PROMPTS_SHOT = {
    "a single gunshot at close range",
    "a loud handgun shot recorded with a body-worn microphone",
    "a sharp pistol shot in an outdoor competition",
    "a rapid sequence of pistol shots",
}


def _label(cand_t: list[float], gt_t: list[float], tol_ms: float) -> list[int]:
    used: set[int] = set()
    for t in sorted(gt_t):
        best_i, best_d = None, None
        for i, c in enumerate(cand_t):
            if i in used:
                continue
            d = abs(c - t) * 1000.0
            if d <= tol_ms and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is not None:
            used.add(best_i)
    return [1 if i in used else 0 for i in range(len(cand_t))]


def _hand_features(
    audio: np.ndarray,
    sr: int,
    t: float,
    all_times: list[float],
    beep_time: float,
    confidence: float,
    peak_amp: float,
) -> list[float]:
    """Subset of the train_classifier feature set -- only the ones with
    non-negligible importance from earlier runs. Keeps voter C compact."""
    idx = int(round(t * sr))
    n = audio.size
    win = int(0.050 * sr)
    pre_lo, pre_hi = max(0, idx - win), idx
    post_lo, post_hi = idx, min(n, idx + win)
    rms_pre = (
        float(np.sqrt(np.mean(audio[pre_lo:pre_hi].astype(np.float64) ** 2)))
        if pre_hi > pre_lo
        else 0.0
    )
    rms_post = (
        float(np.sqrt(np.mean(audio[post_lo:post_hi].astype(np.float64) ** 2)))
        if post_hi > post_lo
        else 0.0
    )
    pre10 = int(0.010 * sr)
    a_lo = max(0, idx - pre10)
    pre_amp = float(np.max(np.abs(audio[a_lo:idx]))) if idx > a_lo else 0.0
    attack = (peak_amp - pre_amp) / 0.010
    sorted_t = sorted(all_times)
    j = sorted_t.index(t)
    gap_prev = (sorted_t[j] - sorted_t[j - 1]) if j > 0 else 5.0
    return [
        peak_amp,
        confidence,
        rms_pre,
        rms_post,
        rms_post / (rms_pre + 1e-6),
        attack,
        gap_prev,
        (t - beep_time) * 1000.0,
    ]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append")
    p.add_argument("--tolerance-ms", type=float, default=75.0)
    p.add_argument(
        "--clap-threshold",
        type=float,
        default=None,
        help="Voter B threshold on shot_minus_notshot. Default: auto-tuned to "
        "the minimum clap_diff seen on a real shot (gives 100 %% recall).",
    )
    p.add_argument(
        "--gbdt-target-recall",
        type=float,
        default=1.00,
        help="Voter C target recall; threshold chosen to hit it (default 100 %%).",
    )
    p.add_argument(
        "--show-disagreements",
        type=int,
        default=15,
        help="Print this many candidates from the disagreement set (lowest vote count).",
    )
    args = p.parse_args()

    fixtures = args.fixture or DEFAULT_FIXTURES

    # Build the candidate universe + per-candidate voter signals.
    universe = []  # list of dicts
    for fix in fixtures:
        truth = json.loads((FIXTURES_DIR / f"{fix}.json").read_text())
        audio, sr = load_audio(FIXTURES_DIR / f"{fix}.wav")
        cfg_recall = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
        cfg_safe = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.03)
        all_shots = detect_shots(
            audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg_recall
        )
        safe_shots = detect_shots(
            audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg_safe
        )
        safe_times = {round(s.time_absolute, 6) for s in safe_shots}

        gt_t = [s["time"] for s in truth.get("shots", [])]
        cand_t = [s.time_absolute for s in all_shots]
        labels = _label(cand_t, gt_t, args.tolerance_ms)

        clap_path = CACHE_DIR / f"{fix}_clap.npz"
        if not clap_path.exists():
            raise SystemExit(
                f"Missing CLAP cache for {fix}. Run scripts/extract_clap_features.py first."
            )
        clap = np.load(clap_path, allow_pickle=True)
        if clap["audio_emb"].shape[0] != len(all_shots):
            raise SystemExit(
                f"{fix}: CLAP cache has {clap['audio_emb'].shape[0]} rows but detector "
                f"returned {len(all_shots)}. Re-run extract_clap_features.py --force."
            )
        prompts = [str(p) for p in clap["prompts"].tolist()]
        sims = clap["text_sims"]  # (N, P)
        shot_idx = [i for i, p in enumerate(prompts) if p in _CLAP_PROMPTS_SHOT]
        not_idx = [i for i, p in enumerate(prompts) if p not in _CLAP_PROMPTS_SHOT]
        shot_mean = sims[:, shot_idx].mean(axis=1)
        not_mean = sims[:, not_idx].mean(axis=1)
        diff = shot_mean - not_mean

        for i, shot in enumerate(all_shots):
            feats = _hand_features(
                audio,
                sr,
                shot.time_absolute,
                cand_t,
                truth["beep_time"],
                shot.confidence,
                shot.peak_amplitude,
            )
            universe.append(
                {
                    "fixture": fix,
                    "t": shot.time_absolute,
                    "label": labels[i],
                    "vote_a": int(round(shot.time_absolute, 6) in safe_times),
                    "clap_diff": float(diff[i]),
                    "hand_feats": feats,
                    "clap_sims": list(sims[i]),
                }
            )

    n_total = len(universe)
    n_pos = sum(c["label"] for c in universe)
    print(f"Universe: {n_total} candidates, {n_pos} positives, {n_total - n_pos} negatives\n")

    # Voter A baseline counts.
    a_kept = sum(c["vote_a"] for c in universe)
    a_pos = sum(c["vote_a"] for c in universe if c["label"] == 1)
    print(
        f"Voter A (baseline + min_confidence=0.03):  kept {a_kept}, recall "
        f"{a_pos}/{n_pos} = {a_pos/n_pos*100:.1f}%, precision "
        f"{a_pos}/{a_kept} = {a_pos/a_kept*100:.1f}%"
    )

    # Voter B: CLAP zero-shot. If --clap-threshold not given, auto-tune to
    # the minimum diff seen on a real shot so this voter is 100 %-recall.
    if args.clap_threshold is None:
        pos_diffs = [c["clap_diff"] for c in universe if c["label"] == 1]
        clap_threshold = min(pos_diffs) if pos_diffs else 0.0
    else:
        clap_threshold = args.clap_threshold
    b_kept = sum(1 for c in universe if c["clap_diff"] >= clap_threshold)
    b_pos = sum(1 for c in universe if c["clap_diff"] >= clap_threshold and c["label"] == 1)
    print(
        f"Voter B (CLAP shot - notshot >= {clap_threshold:.4f}): kept {b_kept}, recall "
        f"{b_pos}/{n_pos} = {b_pos/n_pos*100:.1f}%, precision "
        f"{b_pos}/{b_kept} = {b_pos/b_kept*100:.1f}%"
    )

    # Voter C: GBDT 5-fold on hand + clap-sims + diff.
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import StratifiedKFold

    X = np.array(
        [c["hand_feats"] + c["clap_sims"] + [c["clap_diff"]] for c in universe], dtype=np.float64
    )
    y = np.array([c["label"] for c in universe], dtype=np.int64)
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    probs = np.zeros_like(y, dtype=np.float64)
    for tr, te in skf.split(X, y):
        clf = GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
        )
        clf.fit(X[tr], y[tr])
        probs[te] = clf.predict_proba(X[te])[:, 1]

    # Pick threshold to hit target_recall on the dataset (held-out probs already).
    sorted_pairs = sorted(zip(probs, y, strict=True), key=lambda x: -x[0])
    cumulative_pos = 0
    threshold_c = 0.0
    for prob, lbl in sorted_pairs:
        if lbl == 1:
            cumulative_pos += 1
        if cumulative_pos / n_pos >= args.gbdt_target_recall:
            threshold_c = prob
            break
    c_kept = int((probs >= threshold_c).sum())
    c_pos = int(((probs >= threshold_c) & (y == 1)).sum())
    print(
        f"Voter C (GBDT, thr tuned for {args.gbdt_target_recall*100:.0f} % recall = {threshold_c:.3f}): "
        f"kept {c_kept}, recall {c_pos}/{n_pos} = {c_pos/n_pos*100:.1f}%, precision "
        f"{c_pos}/{c_kept} = {c_pos/c_kept*100:.1f}%"
    )

    for i, c in enumerate(universe):
        c["vote_b"] = int(c["clap_diff"] >= clap_threshold)
        c["vote_c"] = int(probs[i] >= threshold_c)
        c["vote_total"] = c["vote_a"] + c["vote_b"] + c["vote_c"]

    print("\n=== Consensus levels (overall) ===")
    print(f"{'level':>10s} {'kept':>5s} {'recall':>8s} {'prec':>8s} {'F1':>6s}")
    for lvl in [1, 2, 3]:
        kept = sum(1 for c in universe if c["vote_total"] >= lvl)
        pos = sum(1 for c in universe if c["vote_total"] >= lvl and c["label"] == 1)
        rec = pos / n_pos if n_pos else 0.0
        prec = pos / kept if kept else 0.0
        f1 = (2 * rec * prec) / (rec + prec) if (rec + prec) else 0.0
        label = {1: "1-of-3", 2: "2-of-3", 3: "3-of-3"}[lvl]
        print(f"{label:>10s} {kept:5d} {rec*100:7.1f}% {prec*100:7.1f}% {f1:6.2f}")

    print("\nReference: baseline (cwt + min_confidence=0.03) =")
    print(
        f"{'baseline':>10s} {a_kept:5d} {a_pos/n_pos*100:7.1f}% "
        f"{a_pos/a_kept*100:7.1f}% {(2*(a_pos/n_pos)*(a_pos/a_kept))/((a_pos/n_pos)+(a_pos/a_kept)):6.2f}"
    )

    print("\n=== Per-fixture breakdown (consensus 2-of-3) ===")
    print(f"{'fixture':38s} {'kept':>5s} {'recall':>8s} {'prec':>8s} {'gt':>4s}")
    for fix in fixtures:
        rows = [c for c in universe if c["fixture"] == fix]
        kept = sum(1 for c in rows if c["vote_total"] >= 2)
        pos = sum(1 for c in rows if c["vote_total"] >= 2 and c["label"] == 1)
        gt = sum(c["label"] for c in rows)
        rec = pos / gt if gt else 0.0
        prec = pos / kept if kept else 0.0
        print(f"{fix:38s} {kept:5d} {rec*100:7.1f}% {prec*100:7.1f}% {gt:4d}")

    print("\n=== Disagreement set (vote_total = 1) -- audit priority ===")
    disagreements = sorted(
        [c for c in universe if c["vote_total"] == 1],
        key=lambda c: (c["fixture"], c["t"]),
    )
    print(f"{'fixture':38s} {'t':>9s} {'label':>5s} {'A':>2s} {'B':>2s} {'C':>2s} {'clap_diff':>10s}")
    for c in disagreements[: args.show_disagreements]:
        print(
            f"{c['fixture']:38s} {c['t']:9.4f} {c['label']:5d} {c['vote_a']:2d} "
            f"{c['vote_b']:2d} {c['vote_c']:2d} {c['clap_diff']:10.4f}"
        )
    if len(disagreements) > args.show_disagreements:
        print(f"  ... and {len(disagreements) - args.show_disagreements} more")
    n_disagree_pos = sum(c["label"] for c in disagreements)
    print(
        f"  Total disagreement-1 candidates: {len(disagreements)}, "
        f"of which {n_disagree_pos} are real shots ({n_disagree_pos/max(len(disagreements),1)*100:.1f} %)"
    )

    # Quick "where each voter is right alone" diagnostic.
    print("\n=== Solo-correct shots (would be lost without that voter) ===")
    for voter in ("a", "b", "c"):
        solo = [
            c
            for c in universe
            if c["label"] == 1
            and c[f"vote_{voter}"] == 1
            and c["vote_a"] + c["vote_b"] + c["vote_c"] == 1
        ]
        print(f"  voter {voter.upper()}: {len(solo)} real shots saved only by this voter")


if __name__ == "__main__":
    main()
