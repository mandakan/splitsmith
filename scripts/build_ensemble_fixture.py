"""Build review-ready fixture variants for visual comparison in the UI.

For each input fixture, produces TWO sibling fixtures under
``build/ensemble-review/``:

* ``{stem}-baseline.json``         -- shots[] = baseline (cwt + min_confidence=0.03)
* ``{stem}-ensemble-{N}of4.json``  -- shots[] = N-of-4 ensemble consensus

Voters
------
A. Baseline detector + min_confidence=0.03  (calibrated, 100 % recall)
B. CLAP zero-shot, threshold = min(clap_diff over positives)  (100 % recall)
C. GBDT classifier, threshold for --gbdt-target-recall (default 95 %)
D. PANNs gunshot_prob, threshold = min(over positives)  (100 % recall)

Both written variants share the same ``_candidates_pending_audit`` (the full
max-recall candidate set), so opening either in the review SPA shows every
detector candidate and highlights only the ones the chosen filter kept.

The WAV file required by the review SPA is symlinked alongside each JSON, so
``uv run splitsmith review --fixture <path>`` Just Works -- no copy of the
audio, no modification of the originals.

Apriori prior is OPTIONAL
-------------------------
The SSI Scoreboard apriori (--expected-rounds N) is an optional soft prior;
omit the flag and the script runs with no apriori data. No external API call
or fetch is required at any point in this script -- you supply expected
rounds yourself if/when you have them.

Run:
    uv run python scripts/build_ensemble_fixture.py
    uv run python scripts/build_ensemble_fixture.py --consensus 4
    uv run python scripts/build_ensemble_fixture.py \\
        --include-fixture stage-shots-tallmilan-2026-stage6 \\
        --expected-rounds 12
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.shot_detect import detect_shots

DEFAULT_FIXTURES = [
    "stage-shots",
    "stage-shots-blacksmith-h5",
    "stage-shots-blacksmith-2026-stage1",
    "stage-shots-blacksmith-2026-stage2",
    "stage-shots-blacksmith-2026-stage3",
    "stage-shots-tallmilan-stage2",
    "stage-shots-tallmilan-stage7",
    "stage-shots-tallmilan-2026-stage5",
    "stage-shots-tallmilan-2026-stage6",
]
FIXTURES_DIR = Path("tests/fixtures")
CACHE_DIR = FIXTURES_DIR / ".cache"
OUTPUT_DIR = Path("build/ensemble-review")

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
    audio, sr, t, all_times, beep_time, confidence, peak_amp
) -> list[float]:
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
    # Reverb-tail amplitude (user observation 2026-05-01): movement /
    # handling false positives lack the gunshot's sustained 50-200 ms
    # decay. Find local peak in the 10 ms after rise-foot timestamp,
    # measure mean |audio| in [peak + 50 ms, peak + 200 ms]. Used
    # absolute (not normalised by peak) because cross-bay shots have
    # low peak + medium tail; the ratio confounds them with shots.
    peak_search_n = int(0.010 * sr)
    psearch_hi = min(n, idx + peak_search_n)
    if psearch_hi > idx:
        peak_local_idx = idx + int(np.argmax(np.abs(audio[idx:psearch_hi])))
    else:
        peak_local_idx = idx
    tail_lo = min(n, peak_local_idx + int(0.050 * sr))
    tail_hi = min(n, peak_local_idx + int(0.200 * sr))
    tail_amp = float(np.mean(np.abs(audio[tail_lo:tail_hi]))) if tail_hi > tail_lo else 0.0

    # Multi-resolution envelope ratios (added 2026-05-01). See
    # eval_ensemble._hand_features for the rationale; LOFO validation showed
    # +2.9 pp precision at the same recall on the 8-fixture calibration set.
    mr_lo = max(0, peak_local_idx - int(0.025 * sr))
    mr_hi = min(n, peak_local_idx + int(0.025 * sr))
    seg = np.abs(audio[mr_lo:mr_hi].astype(np.float64))
    p_1 = _smoothed_peak(seg, 1.0, sr)
    p_5 = _smoothed_peak(seg, 5.0, sr)
    p_20 = _smoothed_peak(seg, 20.0, sr)
    ratio_1_20 = p_1 / (p_20 + 1e-9)
    ratio_5_20 = p_5 / (p_20 + 1e-9)

    return [
        peak_amp,
        confidence,
        rms_pre,
        rms_post,
        rms_post / (rms_pre + 1e-6),
        attack,
        gap_prev,
        (t - beep_time) * 1000.0,
        tail_amp,
        ratio_1_20,
        ratio_5_20,
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


def _voter_a_floor(fixtures: list[str], tolerance_ms: float) -> float:
    """Lowest detector confidence across audited positives in the calibration
    set. Used as voter A's min_confidence so it preserves 100 % recall by
    construction (the hardcoded 0.03 was tuned on an earlier dataset and
    later AGC-ducked shots fall below it)."""
    min_conf = float("inf")
    for fix in fixtures:
        p = FIXTURES_DIR / f"{fix}.json"
        if not p.exists():
            continue
        truth = json.loads(p.read_text())
        if not truth.get("shots"):
            continue
        audio, sr = load_audio(FIXTURES_DIR / f"{fix}.wav")
        cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
        shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg)
        gt_t = [s["time"] for s in truth["shots"]]
        cand_t = [s.time_absolute for s in shots]
        labels = _label(cand_t, gt_t, tolerance_ms)
        for sh, lbl in zip(shots, labels, strict=True):
            if lbl == 1 and sh.confidence < min_conf:
                min_conf = float(sh.confidence)
    return max(0.0, min_conf - 1e-6) if min_conf != float("inf") else 0.03


def _compute_universe(fixtures: list[str], tolerance_ms: float, voter_a_floor: float):
    """Return list[dict] one per candidate across all fixtures, with voter signals."""
    universe = []
    per_fixture = {}
    for fix in fixtures:
        truth = json.loads((FIXTURES_DIR / f"{fix}.json").read_text())
        audio, sr = load_audio(FIXTURES_DIR / f"{fix}.wav")
        cfg_recall = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
        cfg_safe = ShotDetectConfig(recall_fallback="cwt", min_confidence=voter_a_floor)
        all_shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg_recall)
        safe_shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg_safe)
        safe_times = {round(s.time_absolute, 6) for s in safe_shots}

        gt_t = [s["time"] for s in truth.get("shots", [])]
        cand_t = [s.time_absolute for s in all_shots]
        labels = _label(cand_t, gt_t, tolerance_ms)

        clap = np.load(CACHE_DIR / f"{fix}_clap.npz", allow_pickle=True)
        if clap["audio_emb"].shape[0] != len(all_shots):
            raise SystemExit(
                f"{fix}: CLAP cache stale; re-run extract_clap_features.py --force"
            )
        prompts = [str(p) for p in clap["prompts"].tolist()]
        sims = clap["text_sims"]
        shot_idx = [i for i, p in enumerate(prompts) if p in _CLAP_PROMPTS_SHOT]
        not_idx = [i for i, p in enumerate(prompts) if p not in _CLAP_PROMPTS_SHOT]
        shot_mean = sims[:, shot_idx].mean(axis=1)
        not_mean = sims[:, not_idx].mean(axis=1)
        diff = shot_mean - not_mean

        pann_path = CACHE_DIR / f"{fix}_pann.npz"
        if not pann_path.exists():
            raise SystemExit(
                f"{fix}: PANN cache missing; run scripts/extract_audio_embeddings.py first."
            )
        pann = np.load(pann_path)
        if pann["gunshot_prob"].shape[0] != len(all_shots):
            raise SystemExit(
                f"{fix}: PANN cache stale; re-run extract_audio_embeddings.py --force"
            )
        gunshot_prob = pann["gunshot_prob"]

        per_fixture[fix] = {
            "truth": truth,
            "shots": all_shots,
            "indices_in_universe": [],
        }

        for i, shot in enumerate(all_shots):
            feats = _hand_features(
                audio, sr, shot.time_absolute, cand_t, truth["beep_time"],
                shot.confidence, shot.peak_amplitude,
            )
            per_fixture[fix]["indices_in_universe"].append(len(universe))
            universe.append({
                "fixture": fix,
                "t": shot.time_absolute,
                "label": labels[i],
                "vote_a": int(round(shot.time_absolute, 6) in safe_times),
                "clap_diff": float(diff[i]),
                "gunshot_prob": float(gunshot_prob[i]),
                "hand_feats": feats,
                "clap_sims": list(sims[i]),
                "peak_amplitude": float(shot.peak_amplitude),
                "confidence": float(shot.confidence),
                "time_from_beep": float(shot.time_from_beep),
            })
    return universe, per_fixture


def _vote_b_threshold(calib_universe) -> float:
    pos = [c["clap_diff"] for c in calib_universe if c["label"] == 1]
    return min(pos) if pos else 0.0


def _vote_d_threshold(calib_universe) -> float:
    pos = [c["gunshot_prob"] for c in calib_universe if c["label"] == 1]
    return min(pos) if pos else 0.0


def _x_from(cands) -> np.ndarray:
    return np.array(
        [c["hand_feats"] + c["clap_sims"] + [c["clap_diff"]] for c in cands], dtype=np.float64
    )


def _train_voter_c(calib_universe, target_recall: float):
    """Fit GBDT on calibration set (no CV); pick threshold to hit target recall on
    held-out 5-fold predictions of the SAME calibration set. Returns (clf, threshold)."""
    X = _x_from(calib_universe)
    y = np.array([c["label"] for c in calib_universe], dtype=np.int64)

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_probs = np.zeros_like(y, dtype=np.float64)
    for tr, te in skf.split(X, y):
        f = GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
        )
        f.fit(X[tr], y[tr])
        cv_probs[te] = f.predict_proba(X[te])[:, 1]

    n_pos = int(y.sum())
    pairs = sorted(zip(cv_probs, y, strict=True), key=lambda x: -x[0])
    cum, threshold = 0, 0.0
    for prob, lbl in pairs:
        if lbl == 1:
            cum += 1
        if cum / n_pos >= target_recall:
            threshold = prob
            break

    # Final model trained on ALL calibration data (no held-out -- this is what
    # we apply to the new fixtures).
    clf = GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
    )
    clf.fit(X, y)
    return clf, threshold


def _build_fixture_json(
    source_truth: dict,
    shots_kept: list[dict],
    all_candidates: list[dict],
    label: str,
) -> dict:
    out = dict(source_truth)
    out["shots"] = shots_kept
    out["_candidates_pending_audit"] = {
        "_note": (
            f"Auto-generated for ensemble UI comparison ({label}). "
            "shots[] reflects the chosen filter; candidates[] is the full "
            "max-recall detector output."
        ),
        "candidates": all_candidates,
    }
    # Strip any keys that don't make sense in the synthetic fixture.
    out.pop("tolerance_ms", None)
    return out


def _materialize(json_path: Path, wav_src: Path, fixture_dict: dict) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(fixture_dict, indent=2) + "\n")
    wav_link = json_path.with_suffix(".wav")
    if wav_link.exists() or wav_link.is_symlink():
        wav_link.unlink()
    # Use absolute path so symlink resolves regardless of cwd at review time.
    wav_link.symlink_to(wav_src.resolve())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append", help="Calibration fixture stem (repeatable). Default: all four audited.")
    p.add_argument(
        "--include-fixture",
        action="append",
        default=[],
        help="Apply the trained ensemble to this fixture too (no labels needed). Repeatable.",
    )
    p.add_argument("--consensus", type=int, default=3, choices=[1, 2, 3, 4],
                   help="Consensus threshold for the *-ensemble-{N}of4.json variant. "
                   "Default 3 (= 3-of-4 strict majority, preserves 100 %% recall). "
                   "Keep if (vote_total + apriori_boost) >= consensus.")
    p.add_argument(
        "--gbdt-target-recall",
        type=float,
        default=0.95,
        help="Voter C target recall (default 95 %%). 100 %% can drag the GBDT "
        "threshold close to 0 at small data scales and crater precision.",
    )
    p.add_argument(
        "--expected-rounds",
        type=int,
        default=None,
        help="OPTIONAL soft apriori prior (no external API call required; "
        "you supply this value yourself if/when you have it from SSI Scoreboard "
        "or any other source). Candidates ranked top-N by detector confidence "
        "(within the fixture) get +apriori-boost added to their consensus score. "
        "Applies only to fixtures listed in --include-fixture (not calibration set).",
    )
    p.add_argument(
        "--apriori-boost",
        type=float,
        default=1.0,
        help="Magnitude of the apriori boost (default 1.0 = equivalent to +1 vote). "
        "Pick smaller (e.g. 0.5) for a gentler nudge that only breaks ties.",
    )
    p.add_argument("--tolerance-ms", type=float, default=75.0)
    args = p.parse_args()

    calibration_fixtures = args.fixture or DEFAULT_FIXTURES
    apply_fixtures = list(dict.fromkeys(calibration_fixtures + args.include_fixture))
    new_fixtures = [f for f in apply_fixtures if f not in calibration_fixtures]
    print(f"Calibrating voters on {len(calibration_fixtures)} audited fixture(s)...")
    if new_fixtures:
        print(f"Applying ensemble to {len(new_fixtures)} new fixture(s) (no labels): {new_fixtures}")

    voter_a_floor = _voter_a_floor(calibration_fixtures, args.tolerance_ms)
    print(f"Voter A floor (auto-calibrated to lowest positive confidence): {voter_a_floor:.4f}")
    universe, per_fixture = _compute_universe(apply_fixtures, args.tolerance_ms, voter_a_floor)
    calib_universe = [c for c in universe if c["fixture"] in calibration_fixtures]
    print(
        f"Calibration set: {len(calib_universe)} candidates, "
        f"{sum(c['label'] for c in calib_universe)} positives"
    )

    clap_thr = _vote_b_threshold(calib_universe)
    pann_thr = _vote_d_threshold(calib_universe)
    clf_c, c_thr = _train_voter_c(calib_universe, target_recall=args.gbdt_target_recall)
    print(f"Voter B threshold (CLAP shot-notshot, calibrated): {clap_thr:.4f}")
    print(f"Voter C threshold (GBDT, target recall {args.gbdt_target_recall*100:.0f} %): {c_thr:.4f}")
    print(f"Voter D threshold (PANN gunshot_prob, calibrated): {pann_thr:.4f}")

    # Apply voter C to the FULL universe using the model trained on calibration.
    X_all = _x_from(universe)
    probs = clf_c.predict_proba(X_all)[:, 1]

    for i, c in enumerate(universe):
        c["vote_b"] = int(c["clap_diff"] >= clap_thr)
        c["vote_c"] = int(probs[i] >= c_thr)
        c["vote_d"] = int(c["gunshot_prob"] >= pann_thr)
        c["vote_total"] = c["vote_a"] + c["vote_b"] + c["vote_c"] + c["vote_d"]
        c["score_c"] = float(probs[i])

    # Apply per-fixture soft apriori boost (only for --include-fixture targets;
    # calibration fixtures are unmodified so their existing eval numbers stand).
    boost_set = set(args.include_fixture)
    for fix in apply_fixtures:
        rows = [c for c in universe if c["fixture"] == fix]
        if args.expected_rounds is not None and fix in boost_set:
            ranked = sorted(rows, key=lambda c: -c["confidence"])
            top_set = {id(r) for r in ranked[: args.expected_rounds]}
            for c in rows:
                c["apriori_boost"] = args.apriori_boost if id(c) in top_set else 0.0
        else:
            for c in rows:
                c["apriori_boost"] = 0.0
        for c in rows:
            c["score"] = c["vote_total"] + c["apriori_boost"]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nWriting fixture variants to {OUTPUT_DIR}/")

    for fix in apply_fixtures:
        info = per_fixture[fix]
        truth = info["truth"]
        all_shots = info["shots"]
        idxs = info["indices_in_universe"]

        # Build the full candidates[] payload for both variants. Candidate numbers
        # are 1-based and stable within a fixture.
        all_candidates = []
        for n, shot in enumerate(all_shots, start=1):
            all_candidates.append({
                "candidate_number": n,
                "time": round(shot.time_absolute, 4),
                "ms_after_beep": round(shot.time_from_beep * 1000, 0),
                "peak_amplitude": round(shot.peak_amplitude, 4),
                "confidence": round(shot.confidence, 3),
            })

        # Baseline shots[]: candidates voted by A (cwt + min_confidence=0.03).
        baseline_shots = []
        for n, ui in enumerate(idxs, start=1):
            if universe[ui]["vote_a"] == 1:
                baseline_shots.append({
                    "shot_number": len(baseline_shots) + 1,
                    "candidate_number": n,
                    "time": round(universe[ui]["t"], 4),
                    "ms_after_beep": round(universe[ui]["time_from_beep"] * 1000, 0),
                    "source": "detected",
                })

        # Ensemble shots[]: keep if (vote_total + apriori_boost) >= consensus.
        # Each entry surfaces its votes / boost / score so the JSON documents
        # the decision (and the UI / audit trail can show it).
        ensemble_shots = []
        for n, ui in enumerate(idxs, start=1):
            u = universe[ui]
            if u["score"] >= args.consensus:
                ensemble_shots.append({
                    "shot_number": len(ensemble_shots) + 1,
                    "candidate_number": n,
                    "time": round(u["t"], 4),
                    "ms_after_beep": round(u["time_from_beep"] * 1000, 0),
                    "source": "detected",
                    "ensemble_votes": u["vote_total"],
                    "apriori_boost": u["apriori_boost"],
                    "ensemble_score": round(u["score"], 2),
                })

        # Materialize both.
        wav_src = FIXTURES_DIR / f"{fix}.wav"
        baseline_json = OUTPUT_DIR / f"{fix}-baseline.json"
        ensemble_json = OUTPUT_DIR / f"{fix}-ensemble-{args.consensus}of4.json"

        _materialize(baseline_json, wav_src, _build_fixture_json(
            truth, baseline_shots, all_candidates, label="baseline (cwt + min_confidence=0.03)"
        ))
        _materialize(ensemble_json, wav_src, _build_fixture_json(
            truth, ensemble_shots, all_candidates,
            label=f"ensemble {args.consensus}-of-4 consensus (A=baseline+0.03, B=CLAP, C=GBDT, D=PANN)",
        ))

        n_total = len(all_candidates)
        n_base = len(baseline_shots)
        n_ens = len(ensemble_shots)
        n_audited = sum(1 for s in truth.get("shots", []) if s.get("source") != "manual")
        n_manual = sum(1 for s in truth.get("shots", []) if s.get("source") == "manual")
        boost_tag = ""
        if fix in boost_set and args.expected_rounds is not None:
            n_boosted = sum(
                1 for ui in idxs if universe[ui]["apriori_boost"] > 0
            )
            n_lift = sum(
                1
                for ui in idxs
                if universe[ui]["apriori_boost"] > 0
                and universe[ui]["vote_total"] < args.consensus
                and universe[ui]["score"] >= args.consensus
            )
            boost_tag = (
                f" [apriori top-{args.expected_rounds}: {n_boosted} cands boosted, "
                f"{n_lift} lifted into kept set]"
            )
        print(
            f"  {fix}: cands {n_total}, baseline {n_base}, "
            f"ensemble({args.consensus}/4) {n_ens}, "
            f"audited {n_audited} (+{n_manual} manual){boost_tag}"
        )

    print(f"\nOpen in the review UI -- one fixture per browser tab/window:")
    for fix in apply_fixtures:
        b = OUTPUT_DIR / f"{fix}-baseline.json"
        e = OUTPUT_DIR / f"{fix}-ensemble-{args.consensus}of4.json"
        print(f"\n  uv run splitsmith review --fixture {b}")
        print(f"  uv run splitsmith review --fixture {e} --port 5174")


if __name__ == "__main__":
    main()
