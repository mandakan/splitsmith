"""Smoke-test the shipped ensemble runtime on audited fixtures.

Loads the artifacts under ``src/splitsmith/data/`` (calibration JSON +
GBDT joblib) and runs ``detect_shots_ensemble`` on every selected
fixture, scoring kept candidates against the fixture's audited
``shots[]`` with a 75 ms tolerance. Prints per-fixture and aggregate
precision / recall.

Unlike ``eval_ensemble.py`` (which re-trains the GBDT and re-calibrates
thresholds inline) and unlike ``build_ensemble_artifacts.py`` (which
reports cross-validation metrics), this script exercises the **shipped**
artifacts on the same code path the FastAPI ``/api/stages/{n}/shot-
detect`` endpoint uses. Run it before and after rebuilding artifacts to
verify the rebuild actually improves production-runtime behaviour, and
diff the two outputs.

Examples:

    # Smoke-test every audited fixture
    uv run python scripts/smoke_runtime.py

    # Just the new bofors batch
    uv run python scripts/smoke_runtime.py --match bofors-bombardment-2026

    # Headcam fixtures only, saving a CSV for diffing
    uv run python scripts/smoke_runtime.py --mount head --csv before.csv

Filter args mirror ``splitsmith.ensemble.fixtures.audited``.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

from splitsmith.beep_detect import load_audio
from splitsmith.ensemble import (
    EnsembleConfig,
    detect_shots_ensemble,
    load_ensemble_runtime,
)
from splitsmith.ensemble.fixtures import FIXTURES_DIR, Fixture, audited

DEFAULT_TOL_MS = 75.0


def _match_truth(cand_t: np.ndarray, truth_t: list[float], tol_ms: float) -> tuple[int, int, int]:
    """Greedy nearest-time matching. Returns (tp, fp, fn)."""
    used: set[int] = set()
    tp = 0
    for t in sorted(truth_t):
        best_i: int | None = None
        best_d: float | None = None
        for i, c in enumerate(cand_t):
            if i in used:
                continue
            d = abs(float(c) - t) * 1000.0
            if d <= tol_ms and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is not None:
            used.add(best_i)
            tp += 1
    fp = int(cand_t.size) - tp
    fn = len(truth_t) - tp
    return tp, fp, fn


def _run_fixture(
    fix: Fixture,
    runtime,
    cfg: EnsembleConfig,
    tol_ms: float,
) -> dict[str, object]:
    truth = json.loads((FIXTURES_DIR / f"{fix.stem}.json").read_text())
    audio, sr = load_audio(FIXTURES_DIR / f"{fix.stem}.wav")
    truth_times = [float(s.get("time", 0.0)) for s in truth.get("shots", [])]
    expected_rounds = (truth.get("stage_rounds") or {}).get("expected")

    result = detect_shots_ensemble(
        audio,
        sr,
        beep_time=float(truth["beep_time"]),
        stage_time=float(truth["stage_time_seconds"]),
        runtime=runtime,
        expected_rounds=expected_rounds,
        ensemble_config=cfg,
        camera_class=fix.camera_class,
        camera_make=fix.camera_make,
        camera_model=fix.camera_model,
    )
    kept = [c for c in result.candidates if c.kept]
    cand_t = np.array([c.time for c in kept], dtype=np.float64)
    tp, fp, fn = _match_truth(cand_t, truth_times, tol_ms)
    gt = len(truth_times)
    return {
        "fixture": fix.stem,
        "camera_class": fix.camera_class,
        "kept": len(kept),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "gt": gt,
        "precision": (tp / len(kept)) if kept else 0.0,
        "recall": (tp / gt) if gt else 0.0,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fixture", action="append", help="Restrict to specific fixture stems (repeatable).")
    p.add_argument("--match", help="Filter by match slug (e.g. bofors-bombardment-2026).")
    p.add_argument("--mount", choices=("head", "hand"), help="Filter by camera mount.")
    p.add_argument("--shooter", help="Filter by shooter id.")
    p.add_argument(
        "--tolerance-ms",
        type=float,
        default=DEFAULT_TOL_MS,
        help=f"Time-match tolerance (default {DEFAULT_TOL_MS} ms).",
    )
    p.add_argument(
        "--no-voter-e",
        dest="voter_e",
        action="store_false",
        default=True,
        help="Skip Voter E (CLIP visual probe). Voter E is headcam-only and "
        "requires source videos to be mounted; turn off for handheld-only "
        "or offline runs.",
    )
    p.add_argument("--csv", type=Path, help="Write per-fixture rows to this CSV path.")
    args = p.parse_args()

    fixtures: list[Fixture] = audited(mount=args.mount, shooter_id=args.shooter, match=args.match)
    if args.fixture:
        wanted = set(args.fixture)
        fixtures = [f for f in fixtures if f.stem in wanted]
    if not fixtures:
        print("No fixtures matched the filters.", file=sys.stderr)
        sys.exit(1)
    fixtures.sort(key=lambda f: f.stem)

    runtime = load_ensemble_runtime(with_voter_e=args.voter_e)
    cfg = EnsembleConfig()
    print(f"Calibration built at: {runtime.calibration.built_at}")
    print(f"Default class: {runtime.calibration.default_camera_class}  consensus: {cfg.consensus}")
    print(f"{'fixture':62s} {'kept':>5s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'gt':>4s} {'prec':>7s} {'rec':>7s}")
    print("-" * 110)

    rows: list[dict[str, object]] = []
    tot_kept = tot_tp = tot_fp = tot_fn = tot_gt = 0
    for fix in fixtures:
        row = _run_fixture(fix, runtime, cfg, args.tolerance_ms)
        rows.append(row)
        print(
            f"{row['fixture']:62s} {row['kept']:5d} {row['tp']:4d} {row['fp']:4d} {row['fn']:4d} "
            f"{row['gt']:4d} {row['precision']*100:6.1f}% {row['recall']*100:6.1f}%"
        )
        tot_kept += int(row["kept"])
        tot_tp += int(row["tp"])
        tot_fp += int(row["fp"])
        tot_fn += int(row["fn"])
        tot_gt += int(row["gt"])

    print("-" * 110)
    overall_prec = (tot_tp / tot_kept) if tot_kept else 0.0
    overall_rec = (tot_tp / tot_gt) if tot_gt else 0.0
    print(
        f"{'TOTAL':62s} {tot_kept:5d} {tot_tp:4d} {tot_fp:4d} {tot_fn:4d} {tot_gt:4d} "
        f"{overall_prec*100:6.1f}% {overall_rec*100:6.1f}%"
    )

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWrote {args.csv}")


if __name__ == "__main__":
    main()
