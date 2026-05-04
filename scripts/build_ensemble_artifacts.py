"""Build the shipped ensemble calibration + GBDT artifacts.

Reads the audited fixtures listed in ``DEFAULT_FIXTURES`` and writes:

* ``src/splitsmith/data/ensemble_calibration.json`` -- per-voter
  thresholds, the CLAP prompt bank, calibration provenance.
* ``src/splitsmith/data/voter_c_gbdt.joblib`` -- the trained
  ``GradientBoostingClassifier`` (fit on ALL calibration data, threshold
  picked from 5-fold CV predictions on the same set).

The production server loads both via ``splitsmith.ensemble.calibration``
and reuses them across detections.

Re-run this script after adding new audited fixtures or changing the
hand-feature / CLAP-prompt set. The fixture-builder script
``scripts/build_ensemble_fixture.py`` continues to produce the
review-time variants under ``build/ensemble-review/``; this script is
the production-time equivalent that ships its outputs in the wheel.

CLAP and PANN feature caches under ``tests/fixtures/.cache/`` are
expected to exist; build them first via
``scripts/extract_clap_features.py`` and
``scripts/extract_audio_embeddings.py``.

Run:
    uv run python scripts/build_ensemble_artifacts.py
    uv run python scripts/build_ensemble_artifacts.py --target-recall 0.95
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import StratifiedKFold

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
from splitsmith.ensemble import features as feat
from splitsmith.shot_detect import detect_shots

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
FULL_DIR = FIXTURES_DIR / "full"
CACHE_DIR = FIXTURES_DIR / ".cache"
MINED_NEGATIVES_PATH = CACHE_DIR / "_mined_negatives.npz"
DATA_DIR = Path("src/splitsmith/data")

# Cap mined negatives per fixture relative to that fixture's positive count,
# sampled by descending Voter A confidence so the hardest survivors win.
# Keeps the GBDT's class balance bounded and gives more signal per training row.
DEFAULT_NEG_CAP_RATIO: float = 5.0
_TIME_MATCH_TOL_S: float = 1e-3  # full-cache rows are produced from the same WAV


def _label(cand_t: list[float], truth_shots: list[dict], tol_ms: float) -> list[int]:
    """Greedy nearest-time label: 1 if a truth shot is within ``tol_ms`` of the candidate."""
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


def _load_audit_reason_map(audit: dict) -> list[tuple[float, str]]:
    """Extract ``(time, reason)`` pairs from the fixture's labels_by_time.

    Mirrors ``splitsmith.lab.core._load_labels_from_audit`` -- reasons
    are stored keyed by candidate time at 1 ms resolution. Returned as
    a list (rather than dict) so callers can do nearest-time matching
    when the candidate's onset doesn't land exactly on the stored key.
    """
    pending = audit.get("_candidates_pending_audit") or {}
    raw = pending.get("labels_by_time") if isinstance(pending, dict) else None
    if not isinstance(raw, dict):
        return []
    out: list[tuple[float, str]] = []
    for k, v in raw.items():
        if not isinstance(v, str) or not v:
            continue
        try:
            out.append((float(k), v))
        except (TypeError, ValueError):
            continue
    return out


def _reason_at_time(
    candidate_time: float,
    entries: list[tuple[float, str]],
    *,
    tolerance_ms: float = 75.0,
) -> str | None:
    """Nearest-time reason lookup within tolerance. Same convention as
    the lab's subclass lookup -- audit times can drift up to 75 ms from
    the candidate's detected time."""
    best: tuple[float, str] | None = None
    for t, r in entries:
        d_ms = abs(t - candidate_time) * 1000.0
        if d_ms <= tolerance_ms and (best is None or d_ms < best[0]):
            best = (d_ms, r)
    return best[1] if best is not None else None


def _build_universe(fixtures: list[str], tolerance_ms: float):
    """Per-fixture: detect at max recall, label, slot CLAP+PANN signals."""
    universe = []
    for fix in fixtures:
        truth_path = FIXTURES_DIR / f"{fix}.json"
        wav_path = FIXTURES_DIR / f"{fix}.wav"
        clap_path = CACHE_DIR / f"{fix}_clap.npz"
        pann_path = CACHE_DIR / f"{fix}_pann.npz"
        if not truth_path.exists() or not wav_path.exists():
            print(f"  skip {fix}: missing fixture files")
            continue
        if not clap_path.exists() or not pann_path.exists():
            raise SystemExit(
                f"{fix}: CLAP/PANN cache missing. Run "
                "scripts/extract_clap_features.py and "
                "scripts/extract_audio_embeddings.py first."
            )

        truth = json.loads(truth_path.read_text())
        audio, sr = load_audio(wav_path)
        cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
        shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], cfg)
        if not shots:
            continue
        cand_t = [s.time_absolute for s in shots]
        labels = _label(cand_t, truth.get("shots", []), tolerance_ms)
        # Hand-labeled FP reasons (issue #103 follow-up): a row with
        # ``reason`` set is a confirmed-by-human negative we want voter C
        # to learn from preferentially via sample_weight.
        reason_entries = _load_audit_reason_map(truth)

        clap = np.load(clap_path, allow_pickle=True)
        if clap["audio_emb"].shape[0] != len(shots):
            raise SystemExit(f"{fix}: CLAP cache stale; re-run extract_clap_features.py --force")
        prompts_in_cache = [str(p) for p in clap["prompts"].tolist()]
        if tuple(prompts_in_cache) != feat.CLAP_PROMPTS:
            raise SystemExit(
                f"{fix}: CLAP cache prompt order mismatch with package CLAP_PROMPTS. "
                "Update extract_clap_features.py to import the prompt bank from "
                "splitsmith.ensemble.features and re-run with --force."
            )
        sims = clap["text_sims"]
        clap_diff = feat.clap_diff_from_similarities(sims)

        pann = np.load(pann_path)
        if pann["gunshot_prob"].shape[0] != len(shots):
            raise SystemExit(f"{fix}: PANN cache stale; re-run extract_audio_embeddings.py --force")
        gunshot_prob = pann["gunshot_prob"]

        times = np.array(cand_t, dtype=np.float64)
        confidences = np.array([s.confidence for s in shots], dtype=np.float64)
        peak_amps = np.array([s.peak_amplitude for s in shots], dtype=np.float64)
        hand = feat.compute_hand_features(
            audio, sr, times, truth["beep_time"], confidences, peak_amps
        )

        for i, shot in enumerate(shots):
            row_reason = (
                _reason_at_time(shot.time_absolute, reason_entries) if labels[i] == 0 else None
            )
            universe.append(
                {
                    "fixture": fix,
                    "label": labels[i],
                    "confidence": float(shot.confidence),
                    "clap_diff": float(clap_diff[i]),
                    "gunshot_prob": float(gunshot_prob[i]),
                    "hand_feats": hand[i].tolist(),
                    "clap_sims": [float(x) for x in sims[i]],
                    "reason": row_reason,
                }
            )
    return universe


def _voter_a_floor(universe: list[dict]) -> float:
    """Lowest positive confidence; preserves voter A recall by construction."""
    pos = [c["confidence"] for c in universe if c["label"] == 1]
    return max(0.0, min(pos) - 1e-6) if pos else 0.03


def _voter_b_threshold(universe: list[dict]) -> float:
    pos = [c["clap_diff"] for c in universe if c["label"] == 1]
    return float(min(pos)) if pos else 0.0


def _voter_d_threshold(universe: list[dict]) -> float:
    pos = [c["gunshot_prob"] for c in universe if c["label"] == 1]
    return float(min(pos)) if pos else 0.0


def _x_from(universe: list[dict]) -> np.ndarray:
    return np.array(
        [c["hand_feats"] + c["clap_sims"] + [c["clap_diff"]] for c in universe],
        dtype=np.float64,
    )


def _load_mined_negatives(
    n_pos_by_fixture: dict[str, int],
    *,
    cap_ratio: float,
    log: Callable[[str], None],
) -> tuple[list[dict], dict]:
    """Materialise mined-negative training rows aligned to full-mode caches.

    Returns ``(rows, provenance)``. ``rows`` use the same shape as
    ``_build_universe`` items so they can be appended to the Voter C training
    set. Each fixture's contribution is capped at ``cap_ratio * n_positives``
    by descending Voter A confidence (the hardest survivors). Voter A/B/D
    thresholds are NOT recomputed -- those stay calibrated on positives only.

    Quietly returns no rows when the mined-negatives file or the full-mode
    feature caches are missing, so the script remains a drop-in replacement
    for installs that haven't run the issue #87 mining pipeline yet.
    """
    if not MINED_NEGATIVES_PATH.exists():
        log("No mined-negatives cache; Voter C trains on stage-window negatives only.")
        return [], {"n_mined_negatives_used": 0, "mining_source_fixtures": []}

    mined = np.load(MINED_NEGATIVES_PATH, allow_pickle=True)
    fixtures = mined["fixture"]
    times = mined["time_in_full"].astype(np.float64)
    confidences = mined["confidence"].astype(np.float64)
    peaks = mined["peak_amplitude"].astype(np.float64)
    region_tags = mined["region_tag"]

    by_fixture: dict[str, list[int]] = {}
    for i, fix in enumerate(fixtures):
        by_fixture.setdefault(str(fix), []).append(i)

    rows: list[dict] = []
    used_fixtures: list[str] = []
    skipped: list[str] = []
    for fix, indices in by_fixture.items():
        sidecar_path = FULL_DIR / f"{fix}_full.json"
        wav_path = FULL_DIR / f"{fix}_full.wav"
        clap_path = CACHE_DIR / f"{fix}_clap_full.npz"
        pann_path = CACHE_DIR / f"{fix}_pann_full.npz"
        if not all(p.exists() for p in (sidecar_path, wav_path, clap_path, pann_path)):
            skipped.append(fix)
            continue

        n_pos = n_pos_by_fixture.get(fix, 0)
        if n_pos == 0:
            skipped.append(fix)
            continue
        cap = max(1, int(round(cap_ratio * n_pos)))
        # Hardest survivors first: descending Voter A confidence.
        ordered = sorted(indices, key=lambda i: -confidences[i])[:cap]

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
        if clap_times.shape != pann["gunshot_prob"].shape:
            raise SystemExit(
                f"{fix}: full CLAP and PANN caches disagree on candidate count "
                f"({clap_times.shape[0]} vs {pann['gunshot_prob'].shape[0]}); "
                "rebuild both with --full --force."
            )
        prompts_in_cache = [str(p) for p in clap["prompts"].tolist()]
        if tuple(prompts_in_cache) != feat.CLAP_PROMPTS:
            raise SystemExit(
                f"{fix}: full CLAP cache prompt order mismatch with package "
                "CLAP_PROMPTS; rebuild with extract_clap_features.py --full --force."
            )
        sims = clap["text_sims"]
        clap_diffs = feat.clap_diff_from_similarities(sims)
        gunshot_probs = pann["gunshot_prob"]

        # Map mined times -> full-cache row indices. Same WAV + same config in
        # mine_negatives and the --full extractors, so an exact (or sub-ms)
        # match always exists; absence is a cache-staleness bug worth raising.
        kept_cache_idx: list[int] = []
        kept_mined_idx: list[int] = []
        for mi in ordered:
            t = times[mi]
            j = int(np.argmin(np.abs(clap_times - t)))
            if abs(clap_times[j] - t) > _TIME_MATCH_TOL_S:
                raise SystemExit(
                    f"{fix}: mined-negative time {t:.4f}s has no match in "
                    f"full CLAP cache (closest {clap_times[j]:.4f}s, "
                    f"delta {abs(clap_times[j] - t)*1e3:.2f}ms). Rebuild full "
                    "caches with --full --force after re-running mine_negatives.py."
                )
            kept_cache_idx.append(j)
            kept_mined_idx.append(mi)

        if not kept_cache_idx:
            continue

        audio, sr = load_audio(wav_path)
        cand_t = np.array(times[kept_mined_idx], dtype=np.float64)
        cand_conf = np.array(confidences[kept_mined_idx], dtype=np.float64)
        cand_peak = np.array(peaks[kept_mined_idx], dtype=np.float64)
        hand = feat.compute_hand_features(audio, sr, cand_t, beep_in_full, cand_conf, cand_peak)

        for k, ci in enumerate(kept_cache_idx):
            rows.append(
                {
                    "fixture": fix,
                    "label": 0,
                    "confidence": float(cand_conf[k]),
                    "clap_diff": float(clap_diffs[ci]),
                    "gunshot_prob": float(gunshot_probs[ci]),
                    "hand_feats": hand[k].tolist(),
                    "clap_sims": [float(x) for x in sims[ci]],
                    "region_tag": str(region_tags[kept_mined_idx[k]]),
                    "mined": True,
                }
            )
        used_fixtures.append(fix)
        log(
            f"  mined {fix}: kept {len(kept_cache_idx)} of {len(indices)} "
            f"(cap {cap} = {cap_ratio:g}x {n_pos} positives)"
        )

    if skipped:
        log(
            "  skipped mined fixtures (missing full cache or no positives): "
            + ", ".join(sorted(skipped))
        )

    provenance = {
        "n_mined_negatives_used": len(rows),
        "mining_source_fixtures": sorted(used_fixtures),
        "mining_cap_ratio": cap_ratio,
    }
    return rows, provenance


def _sample_weights(universe: list[dict], labeled_weight: float) -> np.ndarray:
    """Per-row training weight.

    Rows the user has hand-labeled with a ``reason`` (i.e. confirmed-FPs:
    cross_bay / echo / movement / wind / handling / barrel_echo / ...)
    get the ``labeled_weight`` multiplier. Everything else stays at 1.0.

    Equivalent to row duplication for tree-based GBDTs but cheaper.
    """
    weights = np.ones(len(universe), dtype=np.float64)
    for i, c in enumerate(universe):
        if c.get("reason"):
            weights[i] = labeled_weight
    return weights


def _train_voter_c(universe: list[dict], target_recall: float, labeled_weight: float):
    """Fit GBDT; pick threshold from 5-fold CV predictions on the same set.

    ``labeled_weight`` upweights rows whose ``reason`` field is set --
    i.e., user-confirmed false positives on the labeled fixtures. With
    1.0 the training is uniform; values >1 push the GBDT to learn the
    hand-labeled FP patterns more aggressively without changing class
    balance globally.
    """
    X = _x_from(universe)
    y = np.array([c["label"] for c in universe], dtype=np.int64)
    w = _sample_weights(universe, labeled_weight)
    if y.sum() < 5:
        raise SystemExit(
            f"need at least 5 positives for 5-fold CV; got {int(y.sum())}. "
            "Add more audited fixtures."
        )

    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_probs = np.zeros_like(y, dtype=np.float64)
    for tr, te in skf.split(X, y):
        f = GradientBoostingClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
        )
        f.fit(X[tr], y[tr], sample_weight=w[tr])
        cv_probs[te] = f.predict_proba(X[te])[:, 1]

    n_pos = int(y.sum())
    pairs = sorted(zip(cv_probs, y, strict=True), key=lambda x: -x[0])
    cum, threshold = 0, 0.0
    for prob, lbl in pairs:
        if lbl == 1:
            cum += 1
        if cum / n_pos >= target_recall:
            threshold = float(prob)
            break

    clf = GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42
    )
    clf.fit(X, y, sample_weight=w)
    return clf, threshold


def build_artifacts(
    fixtures: list[str] | None = None,
    *,
    target_recall: float = 0.95,
    tolerance_ms: float = 75.0,
    mining_cap_ratio: float = DEFAULT_NEG_CAP_RATIO,
    use_mined_negatives: bool = True,
    labeled_weight: float = 2.0,
    log: Callable[[str], None] = print,
) -> dict:
    """Run the calibration build and write artifacts under ``DATA_DIR``.

    Importable so the production UI's "Rebuild calibration" button can
    drive the same code path as the CLI. Logs progress through ``log``;
    returns the calibration dict that was written.
    """
    fixtures = list(fixtures) if fixtures else list(DEFAULT_FIXTURES)
    log(f"Calibrating ensemble over {len(fixtures)} fixture(s)...")
    universe = _build_universe(fixtures, tolerance_ms)
    n_total = len(universe)
    n_pos = sum(c["label"] for c in universe)
    log(
        f"Universe: {n_total} candidates, {n_pos} positives "
        f"(across {len({c['fixture'] for c in universe})} fixtures)"
    )

    n_pos_by_fixture = Counter(c["fixture"] for c in universe if c["label"] == 1)
    if use_mined_negatives:
        mined_rows, mining_provenance = _load_mined_negatives(
            n_pos_by_fixture, cap_ratio=mining_cap_ratio, log=log
        )
    else:
        mined_rows, mining_provenance = [], {
            "n_mined_negatives_used": 0,
            "mining_source_fixtures": [],
        }
    voter_c_universe = universe + mined_rows
    if mined_rows:
        log(
            f"Voter C training set: {len(universe)} stage-window + "
            f"{len(mined_rows)} mined negatives = {len(voter_c_universe)} rows."
        )

    voter_a = _voter_a_floor(universe)
    voter_b = _voter_b_threshold(universe)
    voter_d = _voter_d_threshold(universe)
    n_labeled = sum(1 for c in voter_c_universe if c.get("reason"))
    if n_labeled and labeled_weight != 1.0:
        log(
            f"Class-aware GBDT: {n_labeled} hand-labeled FP rows "
            f"upweighted by {labeled_weight:.1f}x"
        )
    clf, voter_c = _train_voter_c(
        voter_c_universe, target_recall=target_recall, labeled_weight=labeled_weight
    )
    log(f"Voter A floor (lowest positive confidence): {voter_a:.4f}")
    log(f"Voter B threshold (lowest positive CLAP diff): {voter_b:.4f}")
    log(f"Voter C threshold (GBDT, target recall {target_recall*100:.0f} %): {voter_c:.4f}")
    log(f"Voter D threshold (lowest positive PANN gunshot_prob): {voter_d:.4f}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cal_path = DATA_DIR / "ensemble_calibration.json"
    model_path = DATA_DIR / "voter_c_gbdt.joblib"

    cal = {
        "voter_a_floor": voter_a,
        "voter_b_threshold": voter_b,
        "voter_c_threshold": voter_c,
        "voter_d_threshold": voter_d,
        "voter_c_target_recall": target_recall,
        "tolerance_ms": tolerance_ms,
        "clap_prompts_shot": list(feat.CLAP_PROMPTS_SHOT),
        "clap_prompts": list(feat.CLAP_PROMPTS),
        "calibration_fixtures": [f for f in fixtures if any(c["fixture"] == f for c in universe)],
        "n_calibration_candidates": n_total,
        "n_calibration_positives": int(n_pos),
        "n_hand_labeled_fps": int(n_labeled),
        "labeled_fp_weight": labeled_weight,
        "voter_c_feature_dim": feat.VOTER_C_FEATURE_DIM,
        "built_at": dt.datetime.now(dt.UTC).isoformat(),
        **mining_provenance,
    }
    cal_path.write_text(json.dumps(cal, indent=2) + "\n")
    joblib.dump(clf, model_path)
    log(f"Wrote {cal_path}")
    log(f"Wrote {model_path}")
    return cal


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append", help="Calibration fixture stem (repeatable).")
    p.add_argument("--target-recall", type=float, default=0.95)
    p.add_argument("--tolerance-ms", type=float, default=75.0)
    p.add_argument(
        "--mining-cap-ratio",
        type=float,
        default=DEFAULT_NEG_CAP_RATIO,
        help="Per-fixture cap on mined negatives, as a multiple of that fixture's "
        "positive count. Hardest survivors (highest Voter A confidence) win.",
    )
    p.add_argument(
        "--no-mining",
        action="store_true",
        help="Ignore tests/fixtures/.cache/_mined_negatives.npz even if present.",
    )
    p.add_argument(
        "--labeled-weight",
        type=float,
        default=2.0,
        help="Sample-weight multiplier for hand-labeled FP rows (rows whose audit "
        "JSON carries a ``reason`` for the candidate's time). 1.0 = uniform; >1 "
        "trains voter C harder against the user's known-painful FP classes.",
    )
    args = p.parse_args()
    build_artifacts(
        fixtures=args.fixture or None,
        target_recall=args.target_recall,
        tolerance_ms=args.tolerance_ms,
        mining_cap_ratio=args.mining_cap_ratio,
        use_mined_negatives=not args.no_mining,
        labeled_weight=args.labeled_weight,
    )


if __name__ == "__main__":
    main()
