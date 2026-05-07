"""Visual-voter v0 evaluation: compare zero-shot CLIP signal vs audio.

Reads probe sidecars produced by ``scripts/probe_visual_voter.py``,
computes ROC AUC and precision/recall curves for the visual signal,
the existing audio confidence, and a simple combined score. Writes a
markdown report to ``build/visual-probe/REPORT.md``.

Run:
    uv run python scripts/eval_visual_voter.py
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_DIR = REPO_ROOT / "build" / "visual-probe"


def roc_auc(scores: list[float], labels: list[bool]) -> float | None:
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = ties = 0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def precision_at_recall(
    scores: list[float], labels: list[bool], target_recalls: list[float]
) -> dict[float, dict]:
    paired = sorted(zip(scores, labels), key=lambda x: -x[0])
    total_pos = sum(1 for _, y in paired if y)
    if total_pos == 0:
        return {r: {"precision": None, "k": 0, "threshold": None} for r in target_recalls}
    out: dict[float, dict] = {}
    for target in target_recalls:
        needed = math.ceil(target * total_pos)
        tp = 0
        threshold = None
        for k, (score, y) in enumerate(paired, 1):
            if y:
                tp += 1
            if tp >= needed:
                threshold = score
                out[target] = {
                    "precision": tp / k,
                    "recall": tp / total_pos,
                    "k": k,
                    "threshold": threshold,
                }
                break
        else:
            out[target] = {
                "precision": tp / max(1, len(paired)),
                "recall": tp / total_pos,
                "k": len(paired),
                "threshold": paired[-1][0] if paired else None,
            }
    return out


def zscore(xs: list[float]) -> list[float]:
    if not xs:
        return []
    mu = statistics.fmean(xs)
    sigma = statistics.pstdev(xs) or 1.0
    return [(x - mu) / sigma for x in xs]


def _signals(rows: list[dict]) -> dict[str, list[float]]:
    visual = [float(r["clip_diff"]) for r in rows]
    audio = [float(r["audio_confidence"]) for r in rows]
    out = {
        "audio": audio,
        "visual_zeroshot": visual,
        "combined_zeroshot": [v + a for v, a in zip(zscore(visual), zscore(audio))],
    }
    if all("clip_probe_score" in r and r["clip_probe_score"] is not None for r in rows):
        probe = [float(r["clip_probe_score"]) for r in rows]
        out["visual_probe"] = probe
        out["combined_probe"] = [v + a for v, a in zip(zscore(probe), zscore(audio))]
    return out


def evaluate_one(probe: dict) -> dict:
    rows = probe.get("candidates") or []
    if not rows:
        return {"fixture": probe.get("fixture"), "n_candidates": 0}

    labels = [bool(r["is_shot"]) for r in rows]
    signals = _signals(rows)
    targets = [1.0, 0.95, 0.90]
    return {
        "fixture": probe.get("fixture"),
        "n_candidates": len(rows),
        "n_shots": sum(labels),
        "auc": {name: roc_auc(s, labels) for name, s in signals.items()},
        "p_at_r": {
            name: precision_at_recall(s, labels, targets) for name, s in signals.items()
        },
        "subclass_breakdown": _subclass_stats(rows),
    }


def _subclass_stats(rows: list[dict]) -> dict[str, dict]:
    buckets: dict[str, list[float]] = {}
    for r in rows:
        key = "shot" if r["is_shot"] else (r.get("subclass") or "unlabeled_neg")
        buckets.setdefault(key, []).append(float(r["clip_diff"]))
    return {
        k: {
            "n": len(vs),
            "mean": statistics.fmean(vs),
            "median": statistics.median(vs),
            "stdev": statistics.pstdev(vs),
        }
        for k, vs in buckets.items()
    }


def aggregate(probes: list[dict]) -> dict:
    rows: list[dict] = []
    for p in probes:
        rows.extend(p.get("candidates") or [])
    labels = [bool(r["is_shot"]) for r in rows]
    signals = _signals(rows)
    targets = [1.0, 0.95, 0.90]
    return {
        "n_candidates": len(rows),
        "n_shots": sum(labels),
        "auc": {name: roc_auc(s, labels) for name, s in signals.items()},
        "p_at_r": {
            name: precision_at_recall(s, labels, targets) for name, s in signals.items()
        },
        "subclass_breakdown": _subclass_stats(rows),
    }


def fmt(x: float | None, places: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{places}f}"


def render_report(per_fixture: list[dict], aggregate_stats: dict) -> str:
    lines: list[str] = []
    lines.append("# Visual-voter v0 probe -- evaluation report")
    lines.append("")
    lines.append("Zero-shot + few-shot CLIP scoring of candidate-time frames vs audio-only.")
    lines.append("Issue: https://github.com/mandakan/splitsmith/issues/10")
    lines.append("")
    lines.append("## Aggregate (all fixtures pooled)")
    lines.append("")
    a = aggregate_stats
    lines.append(f"- candidates: {a['n_candidates']}, true shots: {a['n_shots']}")
    lines.append("")
    signals = list(a["auc"].keys())
    lines.append("| signal | ROC AUC | P@R=1.0 | P@R=0.95 | P@R=0.90 |")
    lines.append("|--------|---------|---------|----------|----------|")
    for sig in signals:
        auc = a["auc"][sig]
        par = a["p_at_r"][sig]
        lines.append(
            f"| {sig} | {fmt(auc)} | {fmt(par[1.0]['precision'])} "
            f"| {fmt(par[0.95]['precision'])} | {fmt(par[0.90]['precision'])} |"
        )
    lines.append("")
    lines.append("### Subclass breakdown of zero-shot CLIP diff (aggregate)")
    lines.append("")
    lines.append("| subclass | n | mean | median | stdev |")
    lines.append("|----------|---|------|--------|-------|")
    for sub, s in sorted(a["subclass_breakdown"].items()):
        lines.append(
            f"| {sub} | {s['n']} | {fmt(s['mean'])} | "
            f"{fmt(s['median'])} | {fmt(s['stdev'])} |"
        )
    lines.append("")
    lines.append("## Per-fixture (P@R=1.0)")
    lines.append("")
    header = "| fixture | cands | shots | " + " | ".join(signals) + " |"
    sep = "|---|---|---|" + "|".join(["---"] * len(signals)) + "|"
    lines.append(header)
    lines.append(sep)
    for f in per_fixture:
        if not f.get("auc"):
            continue
        cells = [
            fmt(f["p_at_r"][sig][1.0]["precision"]) if sig in f["p_at_r"] else "n/a"
            for sig in signals
        ]
        lines.append(
            f"| {f['fixture']} | {f['n_candidates']} | {f['n_shots']} | "
            + " | ".join(cells)
            + " |"
        )
    lines.append("")
    lines.append("## Per-fixture (ROC AUC)")
    lines.append("")
    lines.append(header)
    lines.append(sep)
    for f in per_fixture:
        if not f.get("auc"):
            continue
        cells = [fmt(f["auc"].get(sig)) for sig in signals]
        lines.append(
            f"| {f['fixture']} | {f['n_candidates']} | {f['n_shots']} | "
            + " | ".join(cells)
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-dir", default=str(PROBE_DIR))
    args = parser.parse_args()

    probe_dir = Path(args.probe_dir)
    probes: list[dict] = []
    for path in sorted(probe_dir.glob("stage-shots-*.json")):
        if path.name.startswith("_tmp_"):
            continue
        try:
            probes.append(json.loads(path.read_text()))
        except json.JSONDecodeError as exc:
            print(f"  skipping {path.name}: {exc}", file=sys.stderr)

    if not probes:
        print(f"No probe outputs found in {probe_dir}", file=sys.stderr)
        return 1

    per_fixture = [evaluate_one(p) for p in probes]
    agg = aggregate(probes)

    report_path = probe_dir / "REPORT.md"
    report_path.write_text(render_report(per_fixture, agg))
    print(f"Wrote {report_path.relative_to(REPO_ROOT)}", file=sys.stderr)

    print("\n=== aggregate ROC AUC ===")
    for sig, val in agg["auc"].items():
        print(f"  {sig:20s}  {fmt(val)}")
    print("\n=== aggregate precision @ recall=1.0 ===")
    for sig, par_set in agg["p_at_r"].items():
        par = par_set[1.0]
        print(f"  {sig:20s}  P={fmt(par['precision'])}  k={par['k']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
