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

_DROP_MR = False  # set by --no-mr CLI flag for ablation runs

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

_CLAP_PROMPTS_SHOT = {
    "a single gunshot at close range",
    "a loud handgun shot recorded with a body-worn microphone",
    "a sharp pistol shot in an outdoor competition",
    "a rapid sequence of pistol shots",
}


def _label(cand_t, truth_shots, tol_ms):
    """Map candidates to GT shots. Returns (labels, generator_misses).

    Time matching first (robust to stale candidate_number links from older
    fixtures whose candidate ordering changed as shot_detect evolved). Linked
    audits where time matching fails are recorded as candidate-generator
    misses, NOT as positives -- their reverb-peak audio features would poison
    voter calibration. See eval_ensemble._label for full rationale.
    """
    labels = [0] * len(cand_t)
    used = set()
    misses = []
    for s in sorted(truth_shots, key=lambda x: x["time"]):
        t = s["time"]
        best_i, best_d = None, None
        for i, c in enumerate(cand_t):
            if i in used:
                continue
            d = abs(c - t) * 1000.0
            if d <= tol_ms and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is not None:
            used.add(best_i)
            labels[best_i] = 1
        else:
            misses.append({"audit_time": t})
    return labels, misses


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

    # Reverb-tail discriminator (user observation 2026-05-01: movement /
    # handling false positives lack the gunshot's sustained 50-200 ms decay).
    # ABSOLUTE tail amplitude beats tail/peak ratio: cross-bay shots have low
    # peak + high tail (high ratio) while real shots have high peak + medium
    # tail (lower ratio), so normalising obscures the discriminator. Absolute
    # tail consistently splits pos > neg across all fixtures and amplitude
    # classes; on the new stage 1 fixture it's a 2.6x ratio.
    peak_search_n = int(0.010 * sr)
    psearch_hi = min(n, idx + peak_search_n)
    if psearch_hi > idx:
        peak_local_idx = idx + int(np.argmax(np.abs(audio[idx:psearch_hi])))
    else:
        peak_local_idx = idx
    tail_lo = min(n, peak_local_idx + int(0.050 * sr))
    tail_hi = min(n, peak_local_idx + int(0.200 * sr))
    if tail_hi > tail_lo:
        tail_amp = float(np.mean(np.abs(audio[tail_lo:tail_hi])))
    else:
        tail_amp = 0.0

    # NOTE 2026-05-01: tried adding direct-to-reverb ratio (5 ms direct / 45 ms
    # tail) and attack_2ms. attack_2ms was individually discriminative but
    # redundant with attack_10ms; DRR was confounded by the rise-foot
    # timestamp falling inside the rise rather than at the impulse. Both
    # regressed held-out precision and were reverted.

    # PROTOTYPE 2026-05-01: multi-resolution envelope ratios. The candidate
    # generator smooths the envelope at 10 ms which discards the sub-ms
    # structure that distinguishes a muzzle blast (sharp 0.5-1 ms pressure
    # spike) from wind / handling (rounded 5-20 ms onset). Smoothing the
    # rectified raw signal at multiple scales and taking ratios exposes the
    # impulsive-vs-sustained character in an amplitude-invariant way.
    # ratio_1_20 ~ "how peaky is the 1 ms peak relative to its 20 ms
    # neighbourhood"; impulsive sources -> high, sustained -> ~1.0.
    mr_lo = max(0, peak_local_idx - int(0.025 * sr))
    mr_hi = min(n, peak_local_idx + int(0.025 * sr))
    seg = np.abs(audio[mr_lo:mr_hi].astype(np.float64))
    p_1 = _smoothed_peak(seg, 1.0, sr)
    p_5 = _smoothed_peak(seg, 5.0, sr)
    p_20 = _smoothed_peak(seg, 20.0, sr)
    ratio_1_20 = p_1 / (p_20 + 1e-9)
    ratio_5_20 = p_5 / (p_20 + 1e-9)

    if _DROP_MR:
        return [
            peak_amp, confidence, rms_pre, rms_post,
            rms_post / (rms_pre + 1e-6), attack, gap_prev,
            (t - beep_time) * 1000.0,
            tail_amp,
        ]
    return [
        peak_amp, confidence, rms_pre, rms_post,
        rms_post / (rms_pre + 1e-6), attack, gap_prev,
        (t - beep_time) * 1000.0,
        tail_amp,
        ratio_1_20, ratio_5_20,
    ]


def _smoothed_peak(seg: np.ndarray, win_ms: float, sr: int) -> float:
    """Peak of ``seg`` after a moving-average smoothing of width ``win_ms``."""
    if seg.size == 0:
        return 0.0
    w = max(1, int(round(win_ms * 1e-3 * sr)))
    if w >= seg.size:
        return float(seg.mean())
    k = np.ones(w, dtype=np.float64) / w
    return float(np.convolve(seg, k, mode="valid").max())


def _build_universe(fixtures, tol_ms):
    universe = []
    for fix in fixtures:
        truth = json.loads((FIXTURES_DIR / f"{fix}.json").read_text())
        audio, sr = load_audio(FIXTURES_DIR / f"{fix}.wav")
        cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
        shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg)
        cand_t = [s.time_absolute for s in shots]
        labels, _ = _label(cand_t, truth.get("shots", []), tol_ms)

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


def _lofo_probs(X, y, fixtures):
    """Leave-one-fixture-out held-out probabilities.

    For each fixture: train on all candidates from the OTHER fixtures, score
    only the held-out fixture's candidates. This stresses cross-fixture
    generalization (mic placement, gain, ambient profile) much harder than
    StratifiedKFold which mixes fixtures across folds.
    """
    from sklearn.ensemble import GradientBoostingClassifier
    fixtures = np.asarray(fixtures)
    probs = np.zeros_like(y, dtype=np.float64)
    for fix in np.unique(fixtures):
        te = fixtures == fix
        tr = ~te
        clf = GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
        )
        clf.fit(X[tr], y[tr])
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
    p.add_argument("--no-mr", action="store_true",
                   help="Drop multi-resolution envelope ratios (ablation).")
    args = p.parse_args()
    global _DROP_MR
    _DROP_MR = args.no_mr

    universe = _build_universe(DEFAULT_FIXTURES, args.tolerance_ms)
    X, y = _to_xy(universe)
    n_pos = int(y.sum())
    n_neg = int((1 - y).sum())
    print(f"universe: {len(universe)} cands, {n_pos} pos, {n_neg} neg\n")

    # Per-feature separation: median(pos) / median(neg) for the hand features
    # only (CLAP sims dominate the X tail and aren't of interest here).
    feat_names = [
        "peak_amp", "confidence", "rms_pre", "rms_post", "rms_post/pre",
        "attack", "gap_prev", "ms_since_beep", "tail_amp",
    ]
    if not _DROP_MR:
        feat_names += ["mr_ratio_1_20", "mr_ratio_5_20"]
    pos_mask = y == 1
    neg_mask = y == 0
    print("=== Per-feature pos vs neg medians (hand features) ===")
    print(f"{'feature':18s} {'med_pos':>10s} {'med_neg':>10s} {'pos/neg':>9s}")
    for i, name in enumerate(feat_names):
        m_pos = float(np.median(X[pos_mask, i]))
        m_neg = float(np.median(X[neg_mask, i]))
        ratio = m_pos / m_neg if abs(m_neg) > 1e-12 else float("inf")
        print(f"{name:18s} {m_pos:10.4f} {m_neg:10.4f} {ratio:9.3f}")
    print()

    # Pass 1: vanilla GBDT.
    probs = _holdout_probs(X, y)
    thr, kept, pos = _eval_at_target_recall(probs, y, args.target_recall)
    print(f"Pass 1 (unweighted, target recall {args.target_recall*100:.0f} %):")
    print(f"  threshold {thr:.4f}  kept {kept}  recall {pos}/{n_pos} = {pos/n_pos*100:.1f}%  precision {pos/kept*100:.1f}%")

    # Leave-one-fixture-out: stress cross-fixture generalization. If the
    # StratifiedKFold gain comes from per-fixture overfitting, LOFO will
    # collapse it; if it comes from physical structure, LOFO will hold.
    fixture_arr = [c["fixture"] for c in universe]
    lofo_probs = _lofo_probs(X, y, fixture_arr)
    lofo_thr, lofo_kept, lofo_pos = _eval_at_target_recall(lofo_probs, y, args.target_recall)
    print(f"\nLOFO (leave-one-fixture-out, target recall {args.target_recall*100:.0f} %):")
    print(f"  threshold {lofo_thr:.4f}  kept {lofo_kept}  recall {lofo_pos}/{n_pos} = {lofo_pos/n_pos*100:.1f}%  precision {lofo_pos/lofo_kept*100:.1f}%")

    # Per-fixture LOFO breakdown so we can see which fixtures generalize and
    # which break -- the average can hide a single fixture collapsing.
    print(f"\n=== Per-fixture LOFO at threshold {lofo_thr:.4f} ===")
    print(f"{'fixture':38s} {'pos':>4s} {'kept':>5s} {'tp':>4s} {'recall':>7s} {'prec':>6s}")
    fixture_arr_np = np.asarray(fixture_arr)
    for fix in sorted(set(fixture_arr)):
        mask = fixture_arr_np == fix
        f_y = y[mask]
        f_probs = lofo_probs[mask]
        f_pos = int(f_y.sum())
        if f_pos == 0:
            continue
        f_kept_mask = f_probs >= lofo_thr
        f_kept = int(f_kept_mask.sum())
        f_tp = int((f_kept_mask & (f_y == 1)).sum())
        f_recall = f_tp / f_pos * 100
        f_prec = (f_tp / f_kept * 100) if f_kept else 0.0
        print(f"{fix:38s} {f_pos:4d} {f_kept:5d} {f_tp:4d} {f_recall:6.1f}% {f_prec:5.1f}%")

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
