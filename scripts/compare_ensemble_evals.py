"""Compare two eval_ensemble_artifacts.py outputs.

Totals per camera class and mode, and every fixture that changed.

Usage: uv run python scripts/compare_ensemble_evals.py <old.json> <new.json>
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    return p, r, (2 * p * r / (p + r) if p + r else float("nan"))


def main(old_path: Path, new_path: Path) -> None:
    old, new = json.loads(old_path.read_text()), json.loads(new_path.read_text())
    common = [fx for fx in old if fx in new]
    for mode in ("rounds", "blind"):
        print(f"\n== mode: {mode}")
        tot: dict[str, list[list[int]]] = collections.defaultdict(lambda: [[0, 0, 0], [0, 0, 0]])
        for fx in common:
            for i, src in enumerate((old, new)):
                for j in range(3):
                    tot[old[fx]["camera_class"]][i][j] += src[fx][mode][j]
        for key in sorted(tot):
            o, n = tot[key]
            print(
                f"  {key:10s} old P/R/F1 {'/'.join(f'{v:.3f}' for v in prf(*o))} (FP {o[1]} FN {o[2]})   "
                f"new {'/'.join(f'{v:.3f}' for v in prf(*n))} (FP {n[1]} FN {n[2]})"
            )
        worse = [fx for fx in common if sum(new[fx][mode][1:]) > sum(old[fx][mode][1:])]
        better = [fx for fx in common if sum(new[fx][mode][1:]) < sum(old[fx][mode][1:])]
        same = len(common) - len(worse) - len(better)
        print(f"  worse {len(worse)}, better {len(better)}, unchanged {same}")
        for fx in worse:
            o, n = old[fx][mode], new[fx][mode]
            print(f"    WORSE {fx}: FP {o[1]}->{n[1]} FN {o[2]}->{n[2]}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
