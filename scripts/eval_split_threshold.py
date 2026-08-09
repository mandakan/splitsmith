#!/usr/bin/env python3
"""Evaluate a single split/not-split gap threshold against the corpus (#773).

The share card, results page and stage summary fall back, on stages with
no ``interval_class`` yet, to "a gap <= threshold is a split". This
script measures how well any single threshold can do that job:

1. Pools every inter-shot gap from the audited fixtures (and, with
   ``--matches``, from on-disk match audits) and prints the distribution
   around the candidate region. A clean valley between the split and
   transition modes would justify a threshold; overlap bounds its error.
2. Scores candidate thresholds against a structural proxy: on a stage
   with T scored targets, a clean run has T-1 non-split intervals
   (target-to-target transitions, some absorbed into movement/reloads),
   so per stage the T-1 largest gaps are labelled non-split and the rest
   split. Stages with fewer detected shots than expected rounds are
   dropped; makeup shots add splits, not transitions, so extras stay.

The proxy is approximate - steel-to-steel gaps can be genuinely short
and revisits add transitions - so read the error floor as "roughly a
tenth of intervals defy any single threshold", not as an exact figure.
When manually classified stages exist (``interval_class_source ==
"manual"``), prefer them over the proxy: pass ``--manual-only`` to score
against real labels instead.

Usage:
    uv run python scripts/eval_split_threshold.py
    uv run python scripts/eval_split_threshold.py --matches /Volumes/X9/matches
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

FIXDIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
SKIP = re.compile(r"\.bak|before-promote|peaks|candidates|promotion-report")
NAME = re.compile(
    r"stage-shots-(?P<event>.+)-stage(?P<stage>\d+)-(?P<shooter>s[0-9a-f]+)(?P<device>-.+)?\.json$"
)
CANDIDATES = [round(0.35 + 0.025 * i, 3) for i in range(45)]


def load_stage(path: Path) -> dict:
    d = json.loads(path.read_text())
    shots = d.get("shots") or []
    with_ms = [s for s in shots if s.get("ms_after_beep") is not None]
    with_ms.sort(key=lambda s: float(s["ms_after_beep"]))
    times = [float(s["ms_after_beep"]) / 1000.0 for s in with_ms]
    rounds = d.get("stage_rounds") or {}
    return {
        "n_shots": len(times),
        "gaps": [round(b - a, 3) for a, b in zip(times, times[1:], strict=False)],
        # class of the interval ending at shot i+1 (None when unclassified)
        "classes": [s.get("interval_class") for s in with_ms[1:]],
        "sources": [s.get("interval_class_source") for s in with_ms[1:]],
        "expected": rounds.get("expected"),
        "targets": (rounds.get("paper_targets") or 0) + (rounds.get("steel_targets") or 0),
        "has_targets": rounds.get("paper_targets") is not None and rounds.get("steel_targets") is not None,
    }


def collect(match_roots: list[Path]) -> list[dict]:
    stages = []
    by_key: dict[tuple, tuple[Path, bool]] = {}
    for p in sorted(FIXDIR.glob("stage-shots-*.json")):
        if SKIP.search(p.name):
            continue
        m = NAME.search(p.name)
        if not m:
            continue
        key = (m["event"], int(m["stage"]), m["shooter"])
        is_device = m["device"] is not None
        if key not in by_key or (by_key[key][1] and not is_device):
            by_key[key] = (p, is_device)
    for (event, stage, shooter), (p, _) in sorted(by_key.items()):
        stages.append(dict(event=event, stage=stage, shooter=shooter, **load_stage(p)))
    for root in match_roots:
        for audit in sorted(root.glob("*/shooters/*/audit")):
            for p in sorted(audit.glob("stage*.json")):
                if p.name.endswith(".bak"):
                    continue
                stages.append(
                    dict(
                        event=audit.parent.parent.parent.name,
                        stage=int(re.search(r"stage(\d+)", p.name).group(1)),
                        shooter=audit.parent.name,
                        **load_stage(p),
                    )
                )
    return stages


def print_hist(gaps: list[float], hi: float = 1.6, width: float = 0.05) -> None:
    buckets: dict[int, int] = defaultdict(int)
    over = 0
    for g in gaps:
        if g >= hi:
            over += 1
        else:
            buckets[int(g // width)] += 1
    peak = max(buckets.values()) if buckets else 1
    print(f"gap distribution (n={len(gaps)}, >= {hi}s: {over})")
    for i in range(int(hi / width)):
        c = buckets.get(i, 0)
        print(f"  {i * width:5.2f}-{(i + 1) * width:4.2f} {c:5d} {'#' * round(c / peak * 60)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--matches",
        type=Path,
        action="append",
        default=[],
        help="match-store root (e.g. /Volumes/X9/matches) to include audit JSONs from",
    )
    ap.add_argument(
        "--manual-only",
        action="store_true",
        help="score only intervals with interval_class_source == 'manual' (real labels)",
    )
    args = ap.parse_args()

    stages = collect(args.matches)
    all_gaps = [g for s in stages for g in s["gaps"]]
    print(f"stages: {len(stages)}, intervals: {len(all_gaps)}\n")
    print_hist(all_gaps)

    if args.manual_only:
        labelled = [
            (g, cls)
            for s in stages
            for g, cls, src in zip(s["gaps"], s["classes"], s["sources"], strict=True)
            if src == "manual"
        ]
        print(f"\nmanually classified intervals: {len(labelled)}")
        if not labelled:
            print("none yet - audit some stages in the Coach view first")
            return
        print(f"{'t':>6s} {'wrong':>7s} {'pct':>7s}")
        for t in CANDIDATES:
            wrong = sum(1 for g, cls in labelled if (g <= t) != (cls == "split"))
            print(f"{t:6.3f} {wrong:4d}/{len(labelled)} {wrong / len(labelled) * 100:6.2f}%")
        return

    usable = [
        s
        for s in stages
        if s["expected"] and s["has_targets"] and s["targets"] >= 2 and s["n_shots"] >= s["expected"]
    ]
    print(f"\nstructural proxy over {len(usable)}/{len(stages)} stages")
    print(f"{'t':>6s} {'wrong':>9s} {'pct':>7s}")
    for t in CANDIDATES:
        wrong = total = 0
        for s in usable:
            k = s["targets"] - 1
            order = sorted(range(len(s["gaps"])), key=lambda i: -s["gaps"][i])
            nonsplit = set(order[:k])
            for i, g in enumerate(s["gaps"]):
                if (g > t) != (i in nonsplit):
                    wrong += 1
                total += 1
        print(f"{t:6.3f} {wrong:4d}/{total} {wrong / total * 100:6.2f}%")


if __name__ == "__main__":
    main()
