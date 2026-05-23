"""Leave-one-fixture-out comparison: Voter C with vs. without mined negatives.

For each calibration fixture in turn:
1. Train two GBDT classifiers on the other 11 fixtures' in-stage universe:
   * NO_MINING: positives + in-stage negatives only.
   * WITH_MINING: NO_MINING + mined-negatives from the other 11 (capped at
     5x positives per fixture, descending Voter A confidence).
2. Pick a threshold by re-running 5-fold CV on each training set targeting
   95% recall (matches build_ensemble_artifacts.py's logic).
3. Evaluate on the HELD-OUT fixture's:
   * in-stage universe -- precision/recall at the threshold.
   * mined negatives -- false-positive rate (the metric mining is supposed
     to improve).

Reports per-fold and aggregate. No leakage: mined negatives from the held-out
fixture never appear in either model's training set.

Run:
    uv run python scripts/lofo_mining_experiment.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.ensemble import features as feat
from splitsmith.ensemble.fixtures import fixture_stems
from splitsmith.shot_detect import detect_shots

FIXTURES_DIR = Path("tests/fixtures")
FULL_DIR = FIXTURES_DIR / "full"
CACHE_DIR = FIXTURES_DIR / ".cache"
MINED_PATH = CACHE_DIR / "_mined_negatives.npz"
TARGET_RECALL = 0.95
TOLERANCE_MS = 75.0
NEG_CAP_RATIO = 5.0
TIME_TOL_S = 1e-3

# Production ensemble defaults (src/splitsmith/ensemble/api.py).
CONSENSUS = 3
APRIORI_BOOST = 1.0  # only applied when expected_rounds is set; not for these fixtures

DEFAULT_FIXTURES = fixture_stems(mount="head", shooter_id="s97dcec94")


def _label(cand_t: list[float], truth_shots: list[dict], tol_ms: float) -> list[int]:
    labels = [0] * len(cand_t)
    used: set[int] = set()
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
    return labels


def build_in_stage_rows(fix: str) -> list[dict]:
    truth_path = FIXTURES_DIR / f"{fix}.json"
    wav_path = FIXTURES_DIR / f"{fix}.wav"
    clap_path = CACHE_DIR / f"{fix}_clap.npz"
    pann_path = CACHE_DIR / f"{fix}_pann.npz"
    truth = json.loads(truth_path.read_text())
    audio, sr = load_audio(wav_path)
    cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
    shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg)
    if not shots:
        return []
    cand_t = [s.time_absolute for s in shots]
    labels = _label(cand_t, truth.get("shots", []), TOLERANCE_MS)

    clap = np.load(clap_path, allow_pickle=True)
    pann = np.load(pann_path)
    sims = clap["text_sims"]
    clap_diff = feat.clap_diff_from_similarities(sims)
    gunshot_prob = pann["gunshot_prob"]

    times = np.array(cand_t, dtype=np.float64)
    confidences = np.array([s.confidence for s in shots], dtype=np.float64)
    peak_amps = np.array([s.peak_amplitude for s in shots], dtype=np.float64)
    from splitsmith.ensemble.tta import compute_tta_agreement

    tta_arr = compute_tta_agreement(audio, sr, truth["beep_time"], truth["stage_time_seconds"], times)
    hand = feat.compute_hand_features(audio, sr, times, truth["beep_time"], confidences, peak_amps, tta_arr)

    rows: list[dict] = []
    for i, shot in enumerate(shots):
        rows.append(
            {
                "fixture": fix,
                "label": int(labels[i]),
                "hand_feats": hand[i].tolist(),
                "clap_sims": [float(x) for x in sims[i]],
                "clap_diff": float(clap_diff[i]),
                "gunshot_prob": float(gunshot_prob[i]),
                "confidence": float(shot.confidence),
                "source": "in_stage",
            }
        )
    return rows


def build_mined_rows_for_fixture(fix: str, mined_npz: np.lib.npyio.NpzFile) -> list[dict]:
    sidecar_path = FULL_DIR / f"{fix}_full.json"
    wav_path = FULL_DIR / f"{fix}_full.wav"
    clap_path = CACHE_DIR / f"{fix}_clap_full.npz"
    pann_path = CACHE_DIR / f"{fix}_pann_full.npz"
    if not all(p.exists() for p in (sidecar_path, wav_path, clap_path, pann_path)):
        return []

    fixtures = mined_npz["fixture"]
    times = mined_npz["time_in_full"].astype(np.float64)
    confidences = mined_npz["confidence"].astype(np.float64)
    peaks = mined_npz["peak_amplitude"].astype(np.float64)
    region_tags = mined_npz["region_tag"]
    indices = [i for i, f in enumerate(fixtures) if str(f) == fix]
    if not indices:
        return []

    sidecar = json.loads(sidecar_path.read_text())
    audit = json.loads((FIXTURES_DIR / f"{fix}.json").read_text())
    beep_in_full = (
        float(audit["fixture_window_in_source"][0])
        + float(audit["beep_time"])
        - float(sidecar["full_window_in_source"][0])
    )

    clap = np.load(clap_path, allow_pickle=True)
    pann = np.load(pann_path)
    clap_times = clap["times"].astype(np.float64)
    sims = clap["text_sims"]
    clap_diffs = feat.clap_diff_from_similarities(sims)
    gunshot_probs = pann["gunshot_prob"]

    audio, sr = load_audio(wav_path)
    cand_t = np.array(times[indices], dtype=np.float64)
    cand_conf = np.array(confidences[indices], dtype=np.float64)
    cand_peak = np.array(peaks[indices], dtype=np.float64)
    # Mined negatives sit outside any stage window; TTA agreement isn't
    # meaningful here, so use the 1.0 floor (matches build_ensemble_artifacts).
    tta_arr = np.ones(len(cand_t), dtype=np.float64)
    hand = feat.compute_hand_features(audio, sr, cand_t, beep_in_full, cand_conf, cand_peak, tta_arr)

    rows: list[dict] = []
    for k, mi in enumerate(indices):
        t = times[mi]
        j = int(np.argmin(np.abs(clap_times - t)))
        if abs(clap_times[j] - t) > TIME_TOL_S:
            raise SystemExit(
                f"{fix}: mined time {t:.4f}s has no full-cache match (delta "
                f"{abs(clap_times[j] - t)*1e3:.2f}ms)."
            )
        rows.append(
            {
                "fixture": fix,
                "label": 0,
                "hand_feats": hand[k].tolist(),
                "clap_sims": [float(x) for x in sims[j]],
                "clap_diff": float(clap_diffs[j]),
                "gunshot_prob": float(gunshot_probs[j]),
                "confidence": float(cand_conf[k]),
                "region_tag": str(region_tags[mi]),
                "source": "mined",
            }
        )
    return rows


def cap_mined(rows: list[dict], n_pos_by_fixture: dict[str, int]) -> list[dict]:
    """Per-fixture cap at NEG_CAP_RATIO * n_positives, descending confidence."""
    by_fix: dict[str, list[dict]] = {}
    for r in rows:
        by_fix.setdefault(r["fixture"], []).append(r)
    kept: list[dict] = []
    for fix, fix_rows in by_fix.items():
        n_pos = n_pos_by_fixture.get(fix, 0)
        if n_pos == 0:
            continue
        cap = max(1, int(round(NEG_CAP_RATIO * n_pos)))
        ordered = sorted(fix_rows, key=lambda r: -r["confidence"])[:cap]
        kept.extend(ordered)
    return kept


def x_from(rows: list[dict]) -> np.ndarray:
    if not rows:
        return np.zeros((0, feat.VOTER_C_FEATURE_DIM), dtype=np.float64)
    return np.array(
        [r["hand_feats"] + r["clap_sims"] + [r["clap_diff"]] for r in rows],
        dtype=np.float64,
    )


def train_with_threshold(rows: list[dict]) -> tuple[GradientBoostingClassifier, float]:
    X = x_from(rows)
    y = np.array([r["label"] for r in rows], dtype=np.int64)
    n_pos = int(y.sum())
    if n_pos < 5:
        raise SystemExit("not enough positives for 5-fold CV")
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_probs = np.zeros_like(y, dtype=np.float64)
    for tr, te in skf.split(X, y):
        f = GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)
        f.fit(X[tr], y[tr])
        cv_probs[te] = f.predict_proba(X[te])[:, 1]
    pairs = sorted(zip(cv_probs, y, strict=True), key=lambda x: -x[0])
    cum, threshold = 0, 0.0
    for prob, lbl in pairs:
        if lbl == 1:
            cum += 1
        if cum / n_pos >= TARGET_RECALL:
            threshold = float(prob)
            break
    final = GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)
    final.fit(X, y)
    return final, threshold


def calibrate_voters_abd(rows: list[dict]) -> tuple[float, float, float]:
    """Mirror build_ensemble_artifacts: Voter A/B/D thresholds = min over positives.

    Operates on the in-stage training universe (mined negatives are intentionally
    excluded; they're label-0 so they couldn't lower a "min over positives"
    threshold anyway, and we want exact parity with the no-mining build).
    """
    pos_conf = [r["confidence"] for r in rows if r["label"] == 1]
    pos_diff = [r["clap_diff"] for r in rows if r["label"] == 1]
    pos_pann = [r["gunshot_prob"] for r in rows if r["label"] == 1]
    voter_a = max(0.0, min(pos_conf) - 1e-6) if pos_conf else 0.03
    voter_b = float(min(pos_diff)) if pos_diff else 0.0
    voter_d = float(min(pos_pann)) if pos_pann else 0.0
    return voter_a, voter_b, voter_d


def evaluate_ensemble(
    rows: list[dict],
    clf: GradientBoostingClassifier,
    voter_a_floor: float,
    voter_b_threshold: float,
    voter_c_threshold: float,
    voter_d_threshold: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (kept_mask, vote_total) for ``rows`` under 3-of-4 consensus.

    No apriori boost applied: the calibration fixtures don't carry
    stage_rounds.expected, so production would skip the boost anyway.
    """
    if not rows:
        return np.zeros(0, dtype=bool), np.zeros(0, dtype=np.int64)
    X = x_from(rows)
    confidences = np.array([r["confidence"] for r in rows], dtype=np.float64)
    clap_diffs = np.array([r["clap_diff"] for r in rows], dtype=np.float64)
    gunshot_probs = np.array([r["gunshot_prob"] for r in rows], dtype=np.float64)
    gbdt_probs = clf.predict_proba(X)[:, 1]

    va = (confidences >= voter_a_floor).astype(np.int64)
    vb = (clap_diffs >= voter_b_threshold).astype(np.int64)
    vc = (gbdt_probs >= voter_c_threshold).astype(np.int64)
    vd = (gunshot_probs >= voter_d_threshold).astype(np.int64)
    vote_total = va + vb + vc + vd
    kept = vote_total >= CONSENSUS
    return kept, vote_total


def run() -> None:
    fixtures = list(DEFAULT_FIXTURES)
    print("Loading per-fixture data...")
    in_stage_by_fixture: dict[str, list[dict]] = {}
    for fix in fixtures:
        in_stage_by_fixture[fix] = build_in_stage_rows(fix)

    if MINED_PATH.exists():
        mined_npz = np.load(MINED_PATH, allow_pickle=True)
        mined_by_fixture = {fix: build_mined_rows_for_fixture(fix, mined_npz) for fix in fixtures}
    else:
        raise SystemExit("missing _mined_negatives.npz; run scripts/mine_negatives.py first")

    n_pos_by_fixture = {fix: sum(r["label"] for r in rows) for fix, rows in in_stage_by_fixture.items()}

    print(
        f"In-stage universe: {sum(len(v) for v in in_stage_by_fixture.values())} candidates, "
        f"{sum(n_pos_by_fixture.values())} positives"
    )
    print(
        f"Mined negatives (raw): {sum(len(v) for v in mined_by_fixture.values())} "
        "across fixtures (will be capped per fold)\n"
    )

    fmt = (
        "{fix:48s}  {n_pos:3d} pos  thr_no={thr_no:.3f} thr_y={thr_y:.3f}  "
        "in-stage P_no={p_no:5.1f}% P_y={p_y:5.1f}% (R_no={r_no:5.1f}% R_y={r_y:5.1f}%)  "
        "mined-FP_no={fp_no:5.1f}% FP_y={fp_y:5.1f}%"
    )
    print(
        "Per-fixture LOFO results -- VOTER C ALONE (NO_MINING vs WITH_MINING):\n"
        "  P = precision, R = recall on held-out in-stage; FP = false-positive "
        "rate on held-out mined negatives.\n"
    )

    agg_no = {"in_kept": 0, "in_pos": 0, "mined_fired": 0, "mined_total": 0}
    agg_y = {"in_kept": 0, "in_pos": 0, "mined_fired": 0, "mined_total": 0}
    ens_no = {"in_kept": 0, "in_pos": 0, "mined_fired": 0, "mined_total": 0}
    ens_y = {"in_kept": 0, "in_pos": 0, "mined_fired": 0, "mined_total": 0}
    ens_recall_no = []
    ens_recall_y = []
    agg_recall_no = []
    agg_recall_y = []
    n_pos_total = 0
    per_fixture_ens: list[dict] = []

    for held in fixtures:
        # Train on the other 11.
        train_in_stage: list[dict] = []
        train_mined_raw: list[dict] = []
        for f in fixtures:
            if f == held:
                continue
            train_in_stage.extend(in_stage_by_fixture[f])
            train_mined_raw.extend(mined_by_fixture[f])

        train_pos_by_fix = {f: sum(r["label"] for r in in_stage_by_fixture[f]) for f in fixtures if f != held}
        train_mined = cap_mined(train_mined_raw, train_pos_by_fix)

        clf_no, thr_no = train_with_threshold(train_in_stage)
        clf_y, thr_y = train_with_threshold(train_in_stage + train_mined)
        a_floor, b_thr, d_thr = calibrate_voters_abd(train_in_stage)

        # Evaluate on held-out.
        held_in = in_stage_by_fixture[held]
        held_mined = mined_by_fixture[held]
        n_pos = n_pos_by_fixture[held]
        if not held_in or n_pos == 0:
            print(f"{held}: skip (no positives)")
            continue
        n_pos_total += n_pos
        X_in = x_from(held_in)
        y_in = np.array([r["label"] for r in held_in], dtype=np.int64)
        probs_no = clf_no.predict_proba(X_in)[:, 1]
        probs_y = clf_y.predict_proba(X_in)[:, 1]
        kept_no = probs_no >= thr_no
        kept_y = probs_y >= thr_y
        in_kept_no = int(kept_no.sum())
        in_kept_y = int(kept_y.sum())
        in_pos_no = int(((kept_no) & (y_in == 1)).sum())
        in_pos_y = int(((kept_y) & (y_in == 1)).sum())
        p_no = (in_pos_no / in_kept_no * 100.0) if in_kept_no else 0.0
        p_y = (in_pos_y / in_kept_y * 100.0) if in_kept_y else 0.0
        r_no = in_pos_no / n_pos * 100.0
        r_y = in_pos_y / n_pos * 100.0

        if held_mined:
            X_m = x_from(held_mined)
            mined_no = int((clf_no.predict_proba(X_m)[:, 1] >= thr_no).sum())
            mined_y = int((clf_y.predict_proba(X_m)[:, 1] >= thr_y).sum())
            fp_no = mined_no / len(held_mined) * 100.0
            fp_y = mined_y / len(held_mined) * 100.0
        else:
            mined_no = mined_y = 0
            fp_no = fp_y = 0.0

        print(
            fmt.format(
                fix=held,
                n_pos=n_pos,
                thr_no=thr_no,
                thr_y=thr_y,
                p_no=p_no,
                p_y=p_y,
                r_no=r_no,
                r_y=r_y,
                fp_no=fp_no,
                fp_y=fp_y,
            )
        )

        agg_no["in_kept"] += in_kept_no
        agg_no["in_pos"] += in_pos_no
        agg_no["mined_fired"] += mined_no
        agg_no["mined_total"] += len(held_mined)
        agg_y["in_kept"] += in_kept_y
        agg_y["in_pos"] += in_pos_y
        agg_y["mined_fired"] += mined_y
        agg_y["mined_total"] += len(held_mined)
        agg_recall_no.append(in_pos_no / n_pos)
        agg_recall_y.append(in_pos_y / n_pos)

        # Full 4-voter ensemble (production: consensus=3, no apriori).
        ens_kept_no_in, _ = evaluate_ensemble(held_in, clf_no, a_floor, b_thr, thr_no, d_thr)
        ens_kept_y_in, _ = evaluate_ensemble(held_in, clf_y, a_floor, b_thr, thr_y, d_thr)
        ens_in_pos_no = int((ens_kept_no_in & (y_in == 1)).sum())
        ens_in_pos_y = int((ens_kept_y_in & (y_in == 1)).sum())
        ens_in_kept_no = int(ens_kept_no_in.sum())
        ens_in_kept_y = int(ens_kept_y_in.sum())
        if held_mined:
            ens_kept_no_m, _ = evaluate_ensemble(held_mined, clf_no, a_floor, b_thr, thr_no, d_thr)
            ens_kept_y_m, _ = evaluate_ensemble(held_mined, clf_y, a_floor, b_thr, thr_y, d_thr)
            ens_mined_no = int(ens_kept_no_m.sum())
            ens_mined_y = int(ens_kept_y_m.sum())
        else:
            ens_mined_no = ens_mined_y = 0
        ens_no["in_kept"] += ens_in_kept_no
        ens_no["in_pos"] += ens_in_pos_no
        ens_no["mined_fired"] += ens_mined_no
        ens_no["mined_total"] += len(held_mined)
        ens_y["in_kept"] += ens_in_kept_y
        ens_y["in_pos"] += ens_in_pos_y
        ens_y["mined_fired"] += ens_mined_y
        ens_y["mined_total"] += len(held_mined)
        ens_recall_no.append(ens_in_pos_no / n_pos)
        ens_recall_y.append(ens_in_pos_y / n_pos)
        per_fixture_ens.append(
            {
                "fix": held,
                "n_pos": n_pos,
                "in_kept_no": ens_in_kept_no,
                "in_pos_no": ens_in_pos_no,
                "in_kept_y": ens_in_kept_y,
                "in_pos_y": ens_in_pos_y,
                "mined_no": ens_mined_no,
                "mined_y": ens_mined_y,
                "n_mined": len(held_mined),
            }
        )

    print()
    print("=" * 100)
    print("Pooled (sum across folds, micro-averaged):")
    p_no = agg_no["in_pos"] / agg_no["in_kept"] * 100.0 if agg_no["in_kept"] else 0.0
    p_y = agg_y["in_pos"] / agg_y["in_kept"] * 100.0 if agg_y["in_kept"] else 0.0
    r_no = agg_no["in_pos"] / n_pos_total * 100.0
    r_y = agg_y["in_pos"] / n_pos_total * 100.0
    fp_no = agg_no["mined_fired"] / agg_no["mined_total"] * 100.0 if agg_no["mined_total"] else 0.0
    fp_y = agg_y["mined_fired"] / agg_y["mined_total"] * 100.0 if agg_y["mined_total"] else 0.0
    print(
        f"  NO_MINING   in-stage precision={p_no:5.1f}%  recall={r_no:5.1f}%  "
        f"mined-FP rate={fp_no:5.1f}% ({agg_no['mined_fired']}/{agg_no['mined_total']})"
    )
    print(
        f"  WITH_MINING in-stage precision={p_y:5.1f}%  recall={r_y:5.1f}%  "
        f"mined-FP rate={fp_y:5.1f}% ({agg_y['mined_fired']}/{agg_y['mined_total']})"
    )
    print()
    print(
        f"Macro-avg recall (Voter C alone): NO={np.mean(agg_recall_no)*100:5.1f}%  "
        f"WITH={np.mean(agg_recall_y)*100:5.1f}%"
    )

    # Full 4-voter ensemble at production defaults (consensus=3, no apriori).
    print()
    print("=" * 100)
    print("FULL 4-VOTER ENSEMBLE (consensus=3, production default), per-fixture:")
    print()
    fmt_ens = (
        "{fix:48s}  {n_pos:3d} pos  "
        "in-stage P_no={p_no:5.1f}% P_y={p_y:5.1f}% (R_no={r_no:5.1f}% R_y={r_y:5.1f}%)  "
        "mined-FP_no={fp_no:5.1f}% FP_y={fp_y:5.1f}% "
        "({fp_n_no}/{nm} -> {fp_n_y}/{nm})"
    )
    for d in per_fixture_ens:
        n_pos = d["n_pos"]
        nm = d["n_mined"]
        p_no = d["in_pos_no"] / d["in_kept_no"] * 100.0 if d["in_kept_no"] else 0.0
        p_y = d["in_pos_y"] / d["in_kept_y"] * 100.0 if d["in_kept_y"] else 0.0
        r_no = d["in_pos_no"] / n_pos * 100.0
        r_y = d["in_pos_y"] / n_pos * 100.0
        fp_no = d["mined_no"] / nm * 100.0 if nm else 0.0
        fp_y = d["mined_y"] / nm * 100.0 if nm else 0.0
        print(
            fmt_ens.format(
                fix=d["fix"],
                n_pos=n_pos,
                p_no=p_no,
                p_y=p_y,
                r_no=r_no,
                r_y=r_y,
                fp_no=fp_no,
                fp_y=fp_y,
                fp_n_no=d["mined_no"],
                fp_n_y=d["mined_y"],
                nm=nm,
            )
        )

    print()
    print("Pooled ensemble (sum across folds, micro-averaged):")
    p_no = ens_no["in_pos"] / ens_no["in_kept"] * 100.0 if ens_no["in_kept"] else 0.0
    p_y = ens_y["in_pos"] / ens_y["in_kept"] * 100.0 if ens_y["in_kept"] else 0.0
    r_no = ens_no["in_pos"] / n_pos_total * 100.0
    r_y = ens_y["in_pos"] / n_pos_total * 100.0
    fp_rate_no = ens_no["mined_fired"] / ens_no["mined_total"] * 100.0 if ens_no["mined_total"] else 0.0
    fp_rate_y = ens_y["mined_fired"] / ens_y["mined_total"] * 100.0 if ens_y["mined_total"] else 0.0
    print(
        f"  NO_MINING   in-stage precision={p_no:5.1f}%  recall={r_no:5.1f}%  "
        f"mined-FP rate={fp_rate_no:5.2f}% ({ens_no['mined_fired']}/{ens_no['mined_total']})"
    )
    print(
        f"  WITH_MINING in-stage precision={p_y:5.1f}%  recall={r_y:5.1f}%  "
        f"mined-FP rate={fp_rate_y:5.2f}% ({ens_y['mined_fired']}/{ens_y['mined_total']})"
    )

    # Combined "production-realistic" precision: kept = in-stage kept +
    # mined-FP; true positives = in-stage positives kept. Approximates the
    # precision the user actually sees when the candidate stream includes
    # the long pre/post-stage tails.
    combined_kept_no = ens_no["in_kept"] + ens_no["mined_fired"]
    combined_kept_y = ens_y["in_kept"] + ens_y["mined_fired"]
    cp_no = ens_no["in_pos"] / combined_kept_no * 100.0 if combined_kept_no else 0.0
    cp_y = ens_y["in_pos"] / combined_kept_y * 100.0 if combined_kept_y else 0.0
    print()
    print("Combined production-realistic precision (in-stage kept + mined-FP as denominator):")
    print(f"  NO_MINING   {ens_no['in_pos']}/{combined_kept_no} = {cp_no:5.2f}%")
    print(f"  WITH_MINING {ens_y['in_pos']}/{combined_kept_y} = {cp_y:5.2f}%")

    print()
    print(
        f"Macro-avg ensemble recall: NO={np.mean(ens_recall_no)*100:5.1f}%  "
        f"WITH={np.mean(ens_recall_y)*100:5.1f}%"
    )


if __name__ == "__main__":
    run()
