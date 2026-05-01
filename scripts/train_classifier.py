"""Prototype: candidate classifier for shot vs non-shot.

For each audited fixture, run ``detect_shots`` at max recall (recall_fallback=cwt,
min_confidence=0.0). Each detector candidate is labelled ``1`` if a ground-truth
shot lies within ``--tolerance-ms`` (default 75) and ``0`` otherwise. We extract
a feature vector per candidate, train a gradient-boosted tree, and run
leave-one-fixture-out cross-validation to estimate generalization.

This is a prototype. If it generalizes (precision lifts on held-out fixtures
without losing recall), we discuss productionizing as a post-detector layer.
If it only works in-sample, we throw it away.

Run:
    uv run python scripts/train_classifier.py
    uv run python scripts/train_classifier.py --tolerance-ms 50
    uv run python scripts/train_classifier.py --threshold 0.4

Notes:
- sklearn is a dev-only dep; this script is not part of the runtime pipeline.
- Features are extracted from the same audio the detector saw, so positives
  carry the timing-bias of the detector (rise-foot leading edge). That is the
  intent: we are classifying detector candidates, not raw audio events.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.stats import kurtosis
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import precision_recall_curve
from sklearn.model_selection import StratifiedKFold

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.shot_detect import (
    _cwt_max_envelope,
    _hf_lf_ratio,
    detect_shots,
)

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

FEATURE_NAMES = [
    "peak_amplitude",
    "confidence",
    "hf_lf_ratio",
    "log_hf_lf",
    "rms_pre_50ms",
    "rms_post_50ms",
    "rms_ratio_post_pre",
    "cwt_max_5ms",
    "rel_peak_500ms",
    "spectral_kurtosis",
    "gap_to_prev_s",
    "gap_to_next_s",
    "ms_after_beep",
    "attack_steepness",
]

CACHE_DIR = FIXTURES_DIR / ".cache"

# CLAP prompts must mirror scripts/extract_clap_features.py exactly --
# the shot/not-shot split here drives the differential feature.
_CLAP_PROMPTS_SHOT = {
    "a single gunshot at close range",
    "a loud handgun shot recorded with a body-worn microphone",
    "a sharp pistol shot in an outdoor competition",
    "a rapid sequence of pistol shots",
}


@dataclass
class Candidate:
    fixture: str
    t: float
    label: int
    features: dict[str, float]
    pann_embedding: np.ndarray | None = None
    pann_gunshot_prob: float | None = None
    clap_audio_emb: np.ndarray | None = None
    clap_text_sims: np.ndarray | None = None  # (P,)
    clap_prompts: list[str] | None = None


def _featurize(
    audio: np.ndarray,
    sr: int,
    t: float,
    all_times: list[float],
    beep_time: float,
    cwt_env_full: np.ndarray,
    confidence: float,
    peak_amp: float,
) -> dict[str, float]:
    idx = int(round(t * sr))
    n = audio.size

    # 50 ms RMS pre / post.
    win = int(0.050 * sr)
    pre_lo, pre_hi = max(0, idx - win), idx
    post_lo, post_hi = idx, min(n, idx + win)
    rms_pre = float(np.sqrt(np.mean(audio[pre_lo:pre_hi].astype(np.float64) ** 2))) if pre_hi > pre_lo else 0.0
    rms_post = float(np.sqrt(np.mean(audio[post_lo:post_hi].astype(np.float64) ** 2))) if post_hi > post_lo else 0.0
    rms_ratio = rms_post / (rms_pre + 1e-6)

    # CWT envelope max in ±5 ms window.
    pad = int(0.005 * sr)
    cwt_lo, cwt_hi = max(0, idx - pad), min(cwt_env_full.size, idx + pad)
    cwt_max = float(cwt_env_full[cwt_lo:cwt_hi].max()) if cwt_hi > cwt_lo else 0.0

    # Relative peak in ±250 ms window.
    big = int(0.250 * sr)
    big_lo, big_hi = max(0, idx - big), min(n, idx + big)
    big_max = float(np.max(np.abs(audio[big_lo:big_hi]))) if big_hi > big_lo else 0.0
    rel_peak = peak_amp / (big_max + 1e-9)

    # Spectral kurtosis on a 30 ms window (centred on candidate).
    half = int(0.015 * sr)
    sk_lo, sk_hi = max(0, idx - half), min(n, idx + half)
    if sk_hi - sk_lo >= 8:
        w = audio[sk_lo:sk_hi].astype(np.float64) * np.hanning(sk_hi - sk_lo)
        spec = np.abs(np.fft.rfft(w))
        sk = float(kurtosis(spec, fisher=True, bias=False)) if spec.size > 3 else 0.0
        if not np.isfinite(sk):
            sk = 0.0
    else:
        sk = 0.0

    # HF/LF ratio (re-compute for this candidate).
    hf_lf = _hf_lf_ratio(audio, t, sr)
    log_hf_lf = float(np.log1p(hf_lf))

    # Gaps to neighbouring candidates.
    sorted_t = sorted(all_times)
    j = sorted_t.index(t)
    gap_prev = (sorted_t[j] - sorted_t[j - 1]) if j > 0 else 5.0  # large default for first
    gap_next = (sorted_t[j + 1] - sorted_t[j]) if j < len(sorted_t) - 1 else 5.0

    ms_after_beep = (t - beep_time) * 1000.0

    # Attack steepness: rise from envelope at (idx - 10ms) to envelope at idx, in 1/s units.
    pre10 = int(0.010 * sr)
    a_lo = max(0, idx - pre10)
    pre_amp = float(np.max(np.abs(audio[a_lo:idx]))) if idx > a_lo else 0.0
    attack = (peak_amp - pre_amp) / 0.010  # amplitude / second

    return {
        "peak_amplitude": float(peak_amp),
        "confidence": float(confidence),
        "hf_lf_ratio": float(hf_lf),
        "log_hf_lf": log_hf_lf,
        "rms_pre_50ms": rms_pre,
        "rms_post_50ms": rms_post,
        "rms_ratio_post_pre": rms_ratio,
        "cwt_max_5ms": cwt_max,
        "rel_peak_500ms": float(rel_peak),
        "spectral_kurtosis": sk,
        "gap_to_prev_s": float(gap_prev),
        "gap_to_next_s": float(gap_next),
        "ms_after_beep": float(ms_after_beep),
        "attack_steepness": float(attack),
    }


def _label_candidates(
    cand_times: list[float], gt_times: list[float], tolerance_ms: float
) -> list[int]:
    """Greedy match: each ground-truth claims its closest unused candidate
    within tolerance. Unmatched candidates -> 0; matched -> 1."""
    used: set[int] = set()
    for t in sorted(gt_times):
        best_i, best_d = None, None
        for i, c in enumerate(cand_times):
            if i in used:
                continue
            d = abs(c - t) * 1000.0
            if d <= tolerance_ms and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is not None:
            used.add(best_i)
    labels = [1 if i in used else 0 for i in range(len(cand_times))]
    return labels


def _load_pann_cache(fixture: str) -> dict | None:
    p = CACHE_DIR / f"{fixture}_pann.npz"
    if not p.exists():
        return None
    return dict(np.load(p))


def _load_clap_cache(fixture: str) -> dict | None:
    p = CACHE_DIR / f"{fixture}_clap.npz"
    if not p.exists():
        return None
    return dict(np.load(p, allow_pickle=True))


def collect_candidates(
    fixture: str,
    tolerance_ms: float,
    config: ShotDetectConfig,
    *,
    use_pann: bool = False,
    use_clap: bool = False,
) -> list[Candidate]:
    truth = json.loads((FIXTURES_DIR / f"{fixture}.json").read_text())
    audio, sr = load_audio(FIXTURES_DIR / f"{fixture}.wav")
    shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], config)
    cand_t = [s.time_absolute for s in shots]
    gt_t = [s["time"] for s in truth.get("shots", [])]
    labels = _label_candidates(cand_t, gt_t, tolerance_ms)

    cwt_env = _cwt_max_envelope(audio, sr)

    pann_cache = _load_pann_cache(fixture) if use_pann else None
    if use_pann and pann_cache is None:
        raise SystemExit(
            f"--use-pann set but no cache for {fixture}; "
            f"run scripts/extract_audio_embeddings.py first"
        )
    if pann_cache is not None:
        if pann_cache["embedding"].shape[0] != len(shots):
            raise SystemExit(
                f"{fixture}: PANN cache has {pann_cache['embedding'].shape[0]} rows but "
                f"detector returned {len(shots)} candidates -- "
                f"re-run extract_audio_embeddings.py --force"
            )

    clap_cache = _load_clap_cache(fixture) if use_clap else None
    if use_clap and clap_cache is None:
        raise SystemExit(
            f"--use-clap set but no cache for {fixture}; "
            f"run scripts/extract_clap_features.py first"
        )
    if clap_cache is not None:
        if clap_cache["audio_emb"].shape[0] != len(shots):
            raise SystemExit(
                f"{fixture}: CLAP cache has {clap_cache['audio_emb'].shape[0]} rows but "
                f"detector returned {len(shots)} candidates -- "
                f"re-run extract_clap_features.py --force"
            )

    out = []
    for i, (shot, label) in enumerate(zip(shots, labels, strict=True)):
        feats = _featurize(
            audio=audio,
            sr=sr,
            t=shot.time_absolute,
            all_times=cand_t,
            beep_time=truth["beep_time"],
            cwt_env_full=cwt_env,
            confidence=shot.confidence,
            peak_amp=shot.peak_amplitude,
        )
        emb = pann_cache["embedding"][i] if pann_cache is not None else None
        gprob = float(pann_cache["gunshot_prob"][i]) if pann_cache is not None else None
        clap_aemb = clap_cache["audio_emb"][i] if clap_cache is not None else None
        clap_sims = clap_cache["text_sims"][i] if clap_cache is not None else None
        clap_prompts = (
            [str(p) for p in clap_cache["prompts"].tolist()] if clap_cache is not None else None
        )
        out.append(
            Candidate(
                fixture=fixture,
                t=shot.time_absolute,
                label=label,
                features=feats,
                pann_embedding=emb,
                pann_gunshot_prob=gprob,
                clap_audio_emb=clap_aemb,
                clap_text_sims=clap_sims,
                clap_prompts=clap_prompts,
            )
        )
    n_pos = sum(c.label for c in out)
    n_gt = len(gt_t)
    matched_recall = n_pos / n_gt if n_gt else 0.0
    print(
        f"  {fixture}: {len(out)} cands, {n_pos} positives, {n_gt} gt, "
        f"recall={matched_recall*100:.1f}%"
    )
    return out


def _to_xy(
    cands: list[Candidate],
    *,
    use_pann: bool = False,
    use_clap: bool = False,
    clap_text_only: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """Build (X, y). Hand features always included.

    use_pann: append PANN gunshot_prob + 2048-dim embedding (2049 cols).
    use_clap: append CLAP per-prompt cosine sims + shot_minus_notshot (11 cols)
        -- if clap_text_only is False, also append the 512-dim audio embedding.
    """
    rows = []
    for c in cands:
        row = [c.features[k] for k in FEATURE_NAMES]
        if use_pann:
            if c.pann_embedding is None or c.pann_gunshot_prob is None:
                raise RuntimeError("use_pann=True but candidate is missing PANN features")
            row.append(c.pann_gunshot_prob)
            row.extend(c.pann_embedding.tolist())
        if use_clap:
            if c.clap_text_sims is None or c.clap_prompts is None:
                raise RuntimeError("use_clap=True but candidate is missing CLAP features")
            sims = list(c.clap_text_sims.tolist())
            row.extend(sims)
            # Diff feature: mean(shot prompts) - mean(not-shot prompts).
            shot_idx = [i for i, p in enumerate(c.clap_prompts) if p in _CLAP_PROMPTS_SHOT]
            not_idx = [i for i, p in enumerate(c.clap_prompts) if p not in _CLAP_PROMPTS_SHOT]
            shot_mean = float(np.mean([sims[i] for i in shot_idx])) if shot_idx else 0.0
            not_mean = float(np.mean([sims[i] for i in not_idx])) if not_idx else 0.0
            row.append(shot_mean - not_mean)
            if not clap_text_only and c.clap_audio_emb is not None:
                row.extend(c.clap_audio_emb.tolist())
        rows.append(row)
    X = np.asarray(rows, dtype=np.float64)
    y = np.array([c.label for c in cands], dtype=np.int64)
    return X, y


def _eval_at_threshold(probs: np.ndarray, y: np.ndarray, thr: float) -> tuple[float, float, int, int]:
    pred = (probs >= thr).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    return rec, prec, tp + fp, fn


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append")
    p.add_argument("--tolerance-ms", type=float, default=75.0)
    p.add_argument("--threshold", type=float, default=0.5)
    p.add_argument(
        "--detector-config",
        choices=["max-recall", "default"],
        default="max-recall",
        help="max-recall: cwt + min_confidence=0 (recommended for training input)",
    )
    p.add_argument(
        "--use-pann",
        action="store_true",
        help="Concatenate PANNs CNN14 embedding + gunshot_prob to hand features. "
        "Requires running scripts/extract_audio_embeddings.py first.",
    )
    p.add_argument(
        "--use-clap",
        action="store_true",
        help="Concatenate CLAP audio embedding + text-similarity scores. "
        "Requires running scripts/extract_clap_features.py first.",
    )
    p.add_argument(
        "--clap-text-only",
        action="store_true",
        help="With --use-clap, append only the per-prompt similarities (interpretable), "
        "not the 512-dim audio embedding.",
    )
    args = p.parse_args()

    if args.detector_config == "max-recall":
        config = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
    else:
        config = ShotDetectConfig()
    print(f"detector config: {config.model_dump()}")
    print(f"label tolerance: +/-{args.tolerance_ms:.0f} ms")
    print(f"PANN features: {'on' if args.use_pann else 'off'}")
    if args.use_clap:
        print(f"CLAP features: on ({'text-only' if args.clap_text_only else 'text + audio_emb'})")
    else:
        print("CLAP features: off")
    print()

    fixtures = args.fixture or DEFAULT_FIXTURES
    print("Collecting candidates per fixture:")
    by_fix: dict[str, list[Candidate]] = {}
    for f in fixtures:
        by_fix[f] = collect_candidates(
            f,
            args.tolerance_ms,
            config,
            use_pann=args.use_pann,
            use_clap=args.use_clap,
        )

    all_cands = [c for cs in by_fix.values() for c in cs]
    n_pos = sum(c.label for c in all_cands)
    n_neg = len(all_cands) - n_pos
    print(f"\nTotal: {len(all_cands)} candidates, {n_pos} positive, {n_neg} negative")

    print("\n=== Leave-one-fixture-out CV ===")
    print(
        f"{'held-out':38s} {'recall':>7s} {'prec':>7s} {'kept':>5s} {'miss':>5s} "
        f"{'AP':>5s} {'best_F1@thr':>14s}"
    )
    held_results: list[tuple[float, float, int, int]] = []
    for held in fixtures:
        train = [c for f, cs in by_fix.items() for c in cs if f != held]
        test = by_fix[held]
        if not train or not test:
            continue
        Xtr, ytr = _to_xy(
            train, use_pann=args.use_pann, use_clap=args.use_clap, clap_text_only=args.clap_text_only
        )
        Xte, yte = _to_xy(
            test, use_pann=args.use_pann, use_clap=args.use_clap, clap_text_only=args.clap_text_only
        )
        # If train has no positives or no negatives, can't fit meaningful classifier.
        if len(set(ytr.tolist())) < 2:
            print(f"{held:38s}  (train set has only one class; skipping)")
            continue
        clf = GradientBoostingClassifier(
            n_estimators=200,
            max_depth=3,
            learning_rate=0.05,
            random_state=42,
        )
        clf.fit(Xtr, ytr)
        probs = clf.predict_proba(Xte)[:, 1]
        rec, prec, kept, miss = _eval_at_threshold(probs, yte, args.threshold)

        # Average precision + best F1 (over thresholds).
        if len(set(yte.tolist())) >= 2:
            pr, rc, thrs = precision_recall_curve(yte, probs)
            ap = float(np.mean(pr))
            f1 = (2 * pr * rc) / (pr + rc + 1e-9)
            best_i = int(np.argmax(f1[:-1])) if f1.size > 1 else 0
            best_f1 = float(f1[best_i])
            best_thr = float(thrs[best_i]) if best_i < len(thrs) else 0.5
            f1_str = f"{best_f1:.2f}@{best_thr:.2f}"
        else:
            ap = float("nan")
            f1_str = "n/a"
        print(
            f"{held:38s} {rec*100:6.1f}% {prec*100:6.1f}% {kept:5d} {miss:5d} "
            f"{ap:5.2f} {f1_str:>14s}"
        )
        held_results.append((rec, prec, kept, miss))

    if held_results:
        rec_avg = np.mean([r[0] for r in held_results])
        prec_avg = np.mean([r[1] for r in held_results])
        kept_total = sum(r[2] for r in held_results)
        miss_total = sum(r[3] for r in held_results)
        print(
            f"\n{'AVG (held-out)':38s} {rec_avg*100:6.1f}% {prec_avg*100:6.1f}% "
            f"{kept_total:5d} {miss_total:5d}"
        )

    # === 5-fold random CV (candidate-level, mixed across fixtures) ===
    # Models "more data from the same gun gathered later". Less conservative
    # than LOFO; matches the prototype framing that gun-specific is fine
    # for now and per-gun presets come later.
    print("\n=== 5-fold random CV (mixed across fixtures) ===")
    X, y = _to_xy(
        all_cands,
        use_pann=args.use_pann,
        use_clap=args.use_clap,
        clap_text_only=args.clap_text_only,
    )
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    fold_probs = np.zeros_like(y, dtype=np.float64)
    for tr_idx, te_idx in skf.split(X, y):
        clf = GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
        )
        clf.fit(X[tr_idx], y[tr_idx])
        fold_probs[te_idx] = clf.predict_proba(X[te_idx])[:, 1]
    print(f"{'threshold':>10s} {'recall':>8s} {'prec':>8s} {'kept':>6s} {'miss':>6s}")
    for thr in [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]:
        rec, prec, kept, miss = _eval_at_threshold(fold_probs, y, thr)
        print(f"{thr:10.2f} {rec*100:7.1f}% {prec*100:7.1f}% {kept:6d} {miss:6d}")
    pr, rc, thrs = precision_recall_curve(y, fold_probs)
    ap = float(np.mean(pr))
    print(f"  AP (mean precision over recall) = {ap:.3f}")

    # Lowest threshold that still gives 100% recall:
    perfect_recall_mask = rc == 1.0
    if perfect_recall_mask.any():
        # rc/pr are aligned but thrs is len(pr)-1; map carefully
        best_idx = np.where(perfect_recall_mask)[0]
        if best_idx.size:
            best_i = int(best_idx[np.argmax(pr[best_idx])])
            best_thr = float(thrs[best_i]) if best_i < len(thrs) else 0.0
            kept_at_perfect = int(((fold_probs >= best_thr) & (y == 0)).sum() + y.sum())
            print(
                f"  100%-recall threshold (5-fold): {best_thr:.3f}  "
                f"-> kept {kept_at_perfect} (vs baseline 282 / 71)"
            )

    # === Feature importance on full fit ===
    clf_full = GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
    )
    clf_full.fit(X, y)
    print("\n=== Feature importance (full-dataset fit, top 20) ===")
    importances = clf_full.feature_importances_
    names = list(FEATURE_NAMES)
    if args.use_pann:
        names.append("pann_gunshot_prob")
        names.extend(f"pann_emb_{i:04d}" for i in range(2048))
    if args.use_clap:
        # Pull prompts from any candidate's cache (all fixtures share them).
        any_clap = next((c for c in all_cands if c.clap_prompts is not None), None)
        prompts = any_clap.clap_prompts if any_clap else []
        for i, prompt in enumerate(prompts):
            tag = prompt[:30].replace(" ", "_")
            names.append(f"clap_sim_{i:02d}_{tag}")
        names.append("clap_shot_minus_notshot")
        if not args.clap_text_only:
            names.extend(f"clap_audio_emb_{i:04d}" for i in range(512))
    # Truncate/pad to importances length
    names = names[: len(importances)]
    order = np.argsort(importances)[::-1][:20]
    for i in order:
        print(f"  {names[i]:32s} {importances[i]:.3f}")


if __name__ == "__main__":
    main()
