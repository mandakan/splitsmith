"""Hard-negative mining + label-quality audit for voter C.

Premise (user-confirmed 2026-05-01): every audited fixture is FULLY labelled.
Anything in the candidate universe that does not match an audited shot within
the labelling tolerance is a CONFIRMED true negative -- not just an
"unlabelled" one. This lets us:

1. Surface the model's hardest mistakes:
   * Confirmed negatives the GBDT ranks high (false positives we should fix).
   * Confirmed positives the GBDT ranks low (borderline real shots to study).
2. Re-train voter C with sample_weight upweighting the top-fraction of hard
   negatives, then compare precision / recall to the unweighted baseline.

Run:
    uv run python scripts/analyze_negatives.py
    uv run python scripts/analyze_negatives.py --hard-frac 0.20 --hard-weight 3.0
    uv run python scripts/analyze_negatives.py --show 20
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
    "stage-shots-blacksmith-2026-stage2",
    "stage-shots-tallmilan-stage2",
    "stage-shots-tallmilan-stage7",
    "stage-shots-tallmilan-2026-stage5",
    "stage-shots-tallmilan-2026-stage6",
]
FIXTURES_DIR = Path("tests/fixtures")
CACHE_DIR = FIXTURES_DIR / ".cache"

_CLAP_PROMPTS_SHOT = {
    "a single gunshot at close range",
    "a loud handgun shot recorded with a body-worn microphone",
    "a sharp pistol shot in an outdoor competition",
    "a rapid sequence of pistol shots",
}


def _label(cand_t, gt_t, tol_ms):
    used = set()
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


def _hand_features(audio, sr, t, all_times, beep_time, confidence, peak_amp):
    idx = int(round(t * sr))
    n = audio.size
    win = int(0.050 * sr)
    pre_lo, pre_hi = max(0, idx - win), idx
    post_lo, post_hi = idx, min(n, idx + win)
    rms_pre = float(np.sqrt(np.mean(audio[pre_lo:pre_hi].astype(np.float64) ** 2))) if pre_hi > pre_lo else 0.0
    rms_post = float(np.sqrt(np.mean(audio[post_lo:post_hi].astype(np.float64) ** 2))) if post_hi > post_lo else 0.0
    pre10 = int(0.010 * sr)
    a_lo = max(0, idx - pre10)
    pre_amp = float(np.max(np.abs(audio[a_lo:idx]))) if idx > a_lo else 0.0
    attack = (peak_amp - pre_amp) / 0.010
    sorted_t = sorted(all_times)
    j = sorted_t.index(t)
    gap_prev = (sorted_t[j] - sorted_t[j - 1]) if j > 0 else 5.0

    # NOTE: tried adding direct-to-reverb ratio (5ms direct / 45ms tail) and
    # attack_2ms here on 2026-05-01 to fix cross-bay confusion. attack_2ms is
    # individually discriminative (pos median 163 vs neg median 31) but
    # redundant with attack_10ms and the model held-out precision REGRESSED
    # (199 -> 206 kept, 55.3 % -> 53.4 % at 95 % recall). DRR mean is
    # essentially identical for pos and neg (0.098 vs 0.094) -- the rise-foot
    # leading-edge timestamp puts the "direct" window inside the rise itself,
    # not the impulse, so the metric measures rise/decay ratio instead of
    # direct/reverb ratio. Both reverted; next step is more labelled fixtures
    # or per-fixture context features (peak / local-ambient ratio).

    return [
        peak_amp, confidence, rms_pre, rms_post,
        rms_post / (rms_pre + 1e-6), attack, gap_prev,
        (t - beep_time) * 1000.0,
    ]


def _build_universe(fixtures, tol_ms):
    universe = []
    for fix in fixtures:
        truth = json.loads((FIXTURES_DIR / f"{fix}.json").read_text())
        audio, sr = load_audio(FIXTURES_DIR / f"{fix}.wav")
        cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
        shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg)
        gt_t = [s["time"] for s in truth.get("shots", [])]
        cand_t = [s.time_absolute for s in shots]
        labels = _label(cand_t, gt_t, tol_ms)

        clap = np.load(CACHE_DIR / f"{fix}_clap.npz", allow_pickle=True)
        prompts = [str(p) for p in clap["prompts"].tolist()]
        sims = clap["text_sims"]
        shot_idx = [i for i, p in enumerate(prompts) if p in _CLAP_PROMPTS_SHOT]
        not_idx = [i for i, p in enumerate(prompts) if p not in _CLAP_PROMPTS_SHOT]
        diff = sims[:, shot_idx].mean(axis=1) - sims[:, not_idx].mean(axis=1)

        for i, sh in enumerate(shots):
            feats = _hand_features(
                audio, sr, sh.time_absolute, cand_t,
                truth["beep_time"], sh.confidence, sh.peak_amplitude,
            )
            universe.append({
                "fixture": fix,
                "t": sh.time_absolute,
                "label": labels[i],
                "clap_diff": float(diff[i]),
                "hand_feats": feats,
                "clap_sims": list(sims[i]),
                "peak": float(sh.peak_amplitude),
                "conf_detector": float(sh.confidence),
            })
    return universe


def _to_xy(uni):
    X = np.array([c["hand_feats"] + c["clap_sims"] + [c["clap_diff"]] for c in uni], dtype=np.float64)
    y = np.array([c["label"] for c in uni], dtype=np.int64)
    return X, y


def _holdout_probs(X, y, sample_weight=None):
    """5-fold stratified held-out probabilities."""
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import StratifiedKFold
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    probs = np.zeros_like(y, dtype=np.float64)
    for tr, te in skf.split(X, y):
        clf = GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
        )
        sw = sample_weight[tr] if sample_weight is not None else None
        clf.fit(X[tr], y[tr], sample_weight=sw)
        probs[te] = clf.predict_proba(X[te])[:, 1]
    return probs


def _eval_at_target_recall(probs, y, target_recall):
    n_pos = int(y.sum())
    pairs = sorted(zip(probs, y, strict=True), key=lambda x: -x[0])
    cum = 0
    threshold = 0.0
    for prob, lbl in pairs:
        if lbl == 1:
            cum += 1
        if cum / n_pos >= target_recall:
            threshold = prob
            break
    kept = int((probs >= threshold).sum())
    pos = int(((probs >= threshold) & (y == 1)).sum())
    return threshold, kept, pos


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--tolerance-ms", type=float, default=75.0)
    p.add_argument("--target-recall", type=float, default=0.95)
    p.add_argument("--hard-frac", type=float, default=0.20,
                   help="Top fraction of negatives (by predicted prob) to upweight.")
    p.add_argument("--hard-weight", type=float, default=3.0,
                   help="Sample weight multiplier for hard negatives (1.0 = off).")
    p.add_argument("--show", type=int, default=15,
                   help="How many top mistakes to print per category.")
    args = p.parse_args()

    universe = _build_universe(DEFAULT_FIXTURES, args.tolerance_ms)
    X, y = _to_xy(universe)
    n_pos = int(y.sum())
    n_neg = int((1 - y).sum())
    print(f"universe: {len(universe)} cands, {n_pos} pos, {n_neg} neg\n")

    # Pass 1: vanilla GBDT.
    probs = _holdout_probs(X, y)
    thr, kept, pos = _eval_at_target_recall(probs, y, args.target_recall)
    print(f"Pass 1 (unweighted, target recall {args.target_recall*100:.0f} %):")
    print(f"  threshold {thr:.4f}  kept {kept}  recall {pos}/{n_pos} = {pos/n_pos*100:.1f}%  precision {pos/kept*100:.1f}%")

    # Show top "hardest negatives" -- model thinks shot, you said no.
    print(f"\n=== Top {args.show} hardest NEGATIVES (model says shot, audit says no) ===")
    print(f"{'fixture':38s} {'t':>9s} {'prob':>6s} {'detect_conf':>11s} {'peak':>7s} {'clap_diff':>10s}")
    neg_with_probs = [(probs[i], universe[i]) for i in range(len(universe)) if universe[i]["label"] == 0]
    neg_with_probs.sort(key=lambda x: -x[0])
    for prob, c in neg_with_probs[: args.show]:
        print(f"{c['fixture']:38s} {c['t']:9.4f} {prob:6.3f} {c['conf_detector']:11.3f} "
              f"{c['peak']:7.3f} {c['clap_diff']:10.4f}")

    # Show borderline positives -- model uncertain, you said real.
    print(f"\n=== Bottom {args.show} POSITIVES (model uncertain, audit says shot) ===")
    print(f"{'fixture':38s} {'t':>9s} {'prob':>6s} {'detect_conf':>11s} {'peak':>7s} {'clap_diff':>10s}")
    pos_with_probs = [(probs[i], universe[i]) for i in range(len(universe)) if universe[i]["label"] == 1]
    pos_with_probs.sort(key=lambda x: x[0])
    for prob, c in pos_with_probs[: args.show]:
        print(f"{c['fixture']:38s} {c['t']:9.4f} {prob:6.3f} {c['conf_detector']:11.3f} "
              f"{c['peak']:7.3f} {c['clap_diff']:10.4f}")

    # Pass 2: hard-negative mining.
    if args.hard_weight != 1.0:
        weights = np.ones_like(y, dtype=np.float64)
        # Top hard-frac of negatives by Pass-1 probability get heavier weight.
        n_hard = int(round(n_neg * args.hard_frac))
        if n_hard > 0:
            neg_indices = np.where(y == 0)[0]
            neg_probs = probs[neg_indices]
            # Indices into the full universe of the hardest negatives.
            hardest_within_neg = np.argsort(-neg_probs)[:n_hard]
            hardest = neg_indices[hardest_within_neg]
            weights[hardest] = args.hard_weight
        probs2 = _holdout_probs(X, y, sample_weight=weights)
        thr2, kept2, pos2 = _eval_at_target_recall(probs2, y, args.target_recall)
        print(
            f"\nPass 2 (top {args.hard_frac*100:.0f}% of negatives weighted {args.hard_weight}x):"
        )
        print(
            f"  threshold {thr2:.4f}  kept {kept2}  recall {pos2}/{n_pos} = {pos2/n_pos*100:.1f}%  precision {pos2/kept2*100:.1f}%"
        )
        delta_kept = kept - kept2
        delta_prec = (pos2 / kept2 - pos / kept) * 100
        print(f"  delta vs Pass 1: kept {-delta_kept:+d}, precision {delta_prec:+.1f} pp")


if __name__ == "__main__":
    main()
