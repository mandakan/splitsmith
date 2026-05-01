"""Evaluate shot_refine across the calibration fixtures.

For every audited shot, look up the candidate the user kept (via
candidate_number link OR nearest-time match), compute the refined timestamp
from that candidate, and compare both the original and the refined times to
the audited time. Reports per-fixture median/p90 drift improvement.

Run:
    uv run python scripts/eval_refinement.py
    uv run python scripts/eval_refinement.py --method aic
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig, ShotRefineConfig
from splitsmith.shot_detect import detect_shots
from splitsmith.shot_refine import refine_shot_time

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


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--method", choices=("envelope", "aic"), default="envelope")
    p.add_argument("--search-half-window-ms", type=float, default=200.0)
    p.add_argument("--min-confidence", type=float, default=0.5)
    args = p.parse_args()

    refine_cfg = ShotRefineConfig(
        method=args.method,
        search_half_window_ms=args.search_half_window_ms,
        min_confidence=args.min_confidence,
    )

    print(f"method={args.method}  search=+/-{args.search_half_window_ms:.0f}ms  "
          f"min_conf={args.min_confidence}\n")
    all_orig: list[float] = []
    all_refined: list[float] = []
    print(f"{'fixture':38s} {'n_pos':>5s} {'orig_med':>9s} {'orig_p90':>9s} "
          f"{'ref_med':>8s} {'ref_p90':>8s} {'big_fix':>7s} {'rejected':>8s}")
    for fix in DEFAULT_FIXTURES:
        truth_path = FIXTURES_DIR / f"{fix}.json"
        if not truth_path.exists():
            continue
        truth = json.loads(truth_path.read_text())
        audio, sr = load_audio(FIXTURES_DIR / f"{fix}.wav")
        det_cfg = ShotDetectConfig(recall_fallback="cwt", min_confidence=0.0)
        shots = detect_shots(audio, sr, truth["beep_time"], truth["stage_time_seconds"], det_cfg)
        cand_t = [s.time_absolute for s in shots]

        orig_drifts: list[float] = []
        refined_drifts: list[float] = []
        big_fixes = 0  # refinement moved by > 50 ms
        rejected = 0
        for s in truth.get("shots", []):
            audit_t = s["time"]
            # Find the candidate this audit kept: time-nearest within 200 ms
            best_i, best_d = None, None
            for i, c in enumerate(cand_t):
                d = abs(c - audit_t)
                if best_d is None or d < best_d:
                    best_i, best_d = i, d
            if best_i is None or best_d * 1000 > 200:
                continue  # generator miss -- not a refinement opportunity
            orig_t = cand_t[best_i]
            r = refine_shot_time(audio, sr, orig_t, refine_cfg)
            orig_drift_ms = abs(orig_t - audit_t) * 1000
            ref_drift_ms = abs(r.time - audit_t) * 1000
            orig_drifts.append(orig_drift_ms)
            refined_drifts.append(ref_drift_ms)
            if abs(r.drift_ms) > 50:
                big_fixes += 1
            if not r.accepted:
                rejected += 1

        if not orig_drifts:
            continue
        all_orig.extend(orig_drifts)
        all_refined.extend(refined_drifts)
        o = np.array(orig_drifts)
        r_arr = np.array(refined_drifts)
        print(f"{fix:38s} {len(orig_drifts):5d} "
              f"{np.median(o):8.1f}  {np.quantile(o, 0.9):8.1f}  "
              f"{np.median(r_arr):7.1f}  {np.quantile(r_arr, 0.9):7.1f}  "
              f"{big_fixes:7d}  {rejected:8d}")

    o = np.array(all_orig)
    r = np.array(all_refined)
    print(f"\n{'TOTAL':38s} {len(all_orig):5d} "
          f"{np.median(o):8.1f}  {np.quantile(o, 0.9):8.1f}  "
          f"{np.median(r):7.1f}  {np.quantile(r, 0.9):7.1f}")
    delta_med = np.median(o) - np.median(r)
    delta_p90 = np.quantile(o, 0.9) - np.quantile(r, 0.9)
    print(f"  median improvement: {delta_med:+.1f} ms")
    print(f"  p90 improvement:    {delta_p90:+.1f} ms")


if __name__ == "__main__":
    main()
