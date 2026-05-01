"""Detector evaluation harness against audited fixtures.

Treats every shot in each fixture's ``shots[]`` (both ``source: detected`` and
``source: manual``) as ground truth. For each ground-truth shot, finds the
nearest detector candidate within ``--tolerance-ms`` (default 75) using greedy
matching and reports recall, precision, leading-edge drag distribution, and
per-fixture diagnostics.

Use this to:
- Verify a detector change doesn't regress (run before + after, compare).
- Sweep parameters against ground truth to find good defaults.
- Identify true librosa-recall misses (ground-truth shots with NO candidate
  within tolerance) -- those are not solvable by leading-edge tuning.

Run:
    uv run python scripts/eval_detector.py
    uv run python scripts/eval_detector.py --fixture stage-shots-tallmilan-stage7
    uv run python scripts/eval_detector.py --tolerance-ms 50

Ground truth is read from the live fixture JSON. After regenerating fixtures
with ``audit-prep``, the harness will keep working as long as the audited
``shots[]`` is non-empty (each shot's ``time`` is treated as authoritative).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from splitsmith.beep_detect import load_audio
from splitsmith.config import ShotDetectConfig
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


def evaluate_one(
    name: str, *, tolerance_ms: float, config: ShotDetectConfig | None = None
) -> dict:
    truth = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
    audio, sr = load_audio(FIXTURES_DIR / f"{name}.wav")
    config = config or ShotDetectConfig()
    shots = detect_shots(
        audio, sr, truth["beep_time"], truth["stage_time_seconds"], config
    )
    cand_t = sorted(s.time_absolute for s in shots)
    gt_t = sorted(s["time"] for s in truth.get("shots", []))

    used: set[int] = set()
    matches: list[tuple[float, float, float]] = []  # (gt, candidate, drag_ms)
    misses: list[float] = []
    for t in gt_t:
        best_i = best_d = None
        for i, c in enumerate(cand_t):
            if i in used:
                continue
            d = abs(c - t) * 1000.0
            if d <= tolerance_ms and (best_d is None or d < best_d):
                best_i, best_d = i, d
        if best_i is None:
            misses.append(t)
        else:
            used.add(best_i)
            matches.append((t, cand_t[best_i], best_d))
    drags = sorted(d for _, _, d in matches)
    return {
        "fixture": name,
        "gt": len(gt_t),
        "cands": len(cand_t),
        "matched": len(matches),
        "misses": misses,
        "matches": matches,
        "drags": drags,
        "recall": len(matches) / len(gt_t) if gt_t else 0.0,
        "precision": len(matches) / len(cand_t) if cand_t else 0.0,
        "median_drag": drags[len(drags) // 2] if drags else float("nan"),
        "p90_drag": drags[int(len(drags) * 0.9)] if drags else float("nan"),
        "max_drag": max(drags) if drags else float("nan"),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--fixture",
        action="append",
        help="Fixture stem (repeatable). Default: all four audited fixtures.",
    )
    p.add_argument("--tolerance-ms", type=float, default=75.0)
    p.add_argument(
        "--show-pairs",
        action="store_true",
        help="Print every (gt, candidate, drag) match per fixture.",
    )
    p.add_argument("--onset-delta", type=float)
    p.add_argument("--min-gap-ms", type=int)
    p.add_argument("--echo-refractory-ms", type=int)
    p.add_argument("--echo-amplitude-ratio", type=float)
    p.add_argument("--min-confidence", type=float)
    p.add_argument("--recall-fallback", choices=["none", "cwt"])
    args = p.parse_args()

    cfg_kwargs: dict = {}
    if args.onset_delta is not None:
        cfg_kwargs["onset_delta"] = args.onset_delta
    if args.min_gap_ms is not None:
        cfg_kwargs["min_gap_ms"] = args.min_gap_ms
    if args.echo_refractory_ms is not None:
        cfg_kwargs["echo_refractory_ms"] = args.echo_refractory_ms
    if args.echo_amplitude_ratio is not None:
        cfg_kwargs["echo_amplitude_ratio"] = args.echo_amplitude_ratio
    if args.min_confidence is not None:
        cfg_kwargs["min_confidence"] = args.min_confidence
    if args.recall_fallback is not None:
        cfg_kwargs["recall_fallback"] = args.recall_fallback
    config = ShotDetectConfig(**cfg_kwargs) if cfg_kwargs else ShotDetectConfig()
    print(f"config: {config.model_dump()}")
    print(f"tolerance_ms: {args.tolerance_ms}")

    fixtures = args.fixture or DEFAULT_FIXTURES
    print(
        f"\n{'fixture':38s} {'recall':>7s} {'prec':>7s} {'cands':>6s} "
        f"{'mis':>4s} {'med':>7s} {'p90':>7s} {'max':>7s}"
    )
    tot_gt = tot_match = tot_cand = tot_mis = 0
    all_drags: list[float] = []
    for name in fixtures:
        r = evaluate_one(name, tolerance_ms=args.tolerance_ms, config=config)
        print(
            f"{name:38s} {r['recall']*100:6.1f}% {r['precision']*100:6.1f}% "
            f"{r['cands']:6d} {len(r['misses']):4d} "
            f"{r['median_drag']:6.1f}ms {r['p90_drag']:6.1f}ms {r['max_drag']:6.1f}ms"
        )
        for t in r["misses"]:
            print(f"    miss: gt t={t:.4f}s (no candidate within {args.tolerance_ms:.0f} ms)")
        if args.show_pairs:
            for gt, cand, drag in r["matches"]:
                print(f"    pair: gt={gt:.4f}s  cand={cand:.4f}s  drag={drag:5.1f}ms")
        tot_gt += r["gt"]
        tot_match += r["matched"]
        tot_cand += r["cands"]
        tot_mis += len(r["misses"])
        all_drags.extend(r["drags"])
    if all_drags:
        all_drags.sort()
        print(
            f"{'TOTAL':38s} {tot_match/tot_gt*100:6.1f}% "
            f"{tot_match/tot_cand*100:6.1f}% {tot_cand:6d} {tot_mis:4d} "
            f"{all_drags[len(all_drags)//2]:6.1f}ms "
            f"{all_drags[int(len(all_drags)*0.9)]:6.1f}ms "
            f"{max(all_drags):6.1f}ms"
        )


if __name__ == "__main__":
    main()
