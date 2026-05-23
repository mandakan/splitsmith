"""Render plots + a markdown report from ``runs.parquet``.

Reads the parquet emitted by ``run_sweep.py`` (filter by ``--run-id``
when multiple sweeps are stored in the same file). Auto-detects the
sweep shape:

* 0 swept keys  -> single-config snapshot: per-fixture bar chart only.
* 1 swept key   -> line plot of precision / recall / F1 vs the swept
  parameter, plus a per-fixture line-plot facet.
* 2 swept keys  -> three heatmaps (precision / recall / F1) over the
  Cartesian product, plus the per-fixture line plots reduced over the
  second axis (best value picked per row).
* 3+ swept keys -> small-multiples grid of 2D heatmaps faceted on the
  third key.

Outputs into ``build/sweeps/<run_id>/``:

* ``overview.png``         -- single composite figure suitable for README.
* ``report.md``            -- detailed markdown report with embedded
  plots, parameter table, best-config callout, and per-fixture stats.
* ``precision_*.png``, ``recall_*.png``, ``f1_*.png``, ``per_fixture_*.png``
  -- the individual plots the report references.

Also writes ``build/sweeps/latest_overview.png`` + ``build/sweeps/latest_report.md``
as symlinks-by-copy so the README can point at a stable path.

Run:
    uv run python scripts/plot_sweep.py                  # latest run_id
    uv run python scripts/plot_sweep.py --run-id <id>
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "build" / "sweeps"
DEFAULT_RUNS = OUT_DIR / "runs.parquet"


def _short_fixture(name: str) -> str:
    """Compact fixture label for plot axes.

    ``stage-shots-tallmilan-2026-stage3-s97dcec94-apple-iphone17pro``
    -> ``tallmilan-2026/stage3/s97dcec94/apple-iphone17pro``. Keeps the
    shooter token + camera distinction visible without ballooning the
    label width.
    """
    s = name.removeprefix("stage-shots-")
    # Insert / separators around the stageN token + shooter token.
    parts = s.split("-")
    out: list[str] = []
    for p in parts:
        if p.startswith("stage") and p[5:].isdigit():
            out.append("/" + p)
        elif len(p) == 9 and p.startswith("s") and all(c.isalnum() for c in p[1:]):
            out.append("/" + p)
        else:
            out.append(p)
    return "-".join(out).replace("-/", "/")


def _load_runs(path: Path, run_id: str | None) -> tuple[list[dict], str]:
    table = pq.read_table(path)
    rows = table.to_pylist()
    if not rows:
        raise SystemExit(f"{path} is empty.")
    if run_id is None:
        run_id = sorted({r["run_id"] for r in rows})[-1]
    rows = [r for r in rows if r["run_id"] == run_id]
    if not rows:
        raise SystemExit(
            f"No rows for run_id={run_id!r}. Available: "
            f"{sorted({r['run_id'] for r in table.to_pylist()})}"
        )
    return rows, run_id


def _swept_keys(rows: list[dict]) -> list[str]:
    """Return only the keys whose values actually vary across this run.

    A grid might pin a key (e.g. ``voter_c_mode: [adaptive]``); that
    column has one unique value across all combos and shouldn't drive
    plot dimensionality. Filtering it here keeps the plot layout aligned
    with the user's intent without forcing them to remove pinned keys.
    """
    declared = [k for k in (rows[0].get("swept_keys") or "").split(",") if k]
    out: list[str] = []
    for k in declared:
        seen = {r[f"param_{k}"] for r in rows if r["fixture"] == "__all__"}
        if len(seen) > 1:
            out.append(k)
    return out


def _agg_rows(rows: list[dict], fixture: str = "__all__") -> list[dict]:
    return [r for r in rows if r["fixture"] == fixture]


def _fixture_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["fixture"] != "__all__" and not r["fixture"].startswith("__class_")]


def _unique_sorted(values: list[Any]) -> list[Any]:
    # ``None`` is not orderable with numbers in py3 -- map to a sentinel
    # for sorting, then restore.
    def _key(v: Any) -> tuple[int, Any]:
        if v is None:
            return (0, 0)
        if isinstance(v, bool):
            return (1, int(v))
        if isinstance(v, (int, float)):
            return (2, float(v))
        return (3, str(v))

    return sorted(set(values), key=_key)


def _plot_1d(rows: list[dict], key: str, out_dir: Path) -> list[tuple[str, Path]]:
    """Three line plots (P/R/F1) over the swept key + per-fixture facet."""
    agg = _agg_rows(rows, "__all__")
    xs = [r[f"param_{key}"] for r in agg]
    order = np.argsort([float(v) if isinstance(v, (int, float, bool)) and v is not None else 0 for v in xs])
    xs_sorted = [xs[i] for i in order]
    series = {
        "precision": [agg[i]["precision"] for i in order],
        "recall": [agg[i]["recall"] for i in order],
        "f1": [agg[i]["f1"] for i in order],
    }
    artifacts: list[tuple[str, Path]] = []
    for metric, ys in series.items():
        fig, ax = plt.subplots(figsize=(6.5, 4))
        ax.plot(
            xs_sorted,
            ys,
            marker="o",
            linewidth=1.5,
            color={"precision": "tab:blue", "recall": "tab:orange", "f1": "tab:green"}[metric],
        )
        ax.set_xlabel(key)
        ax.set_ylabel(metric)
        ax.set_title(f"{metric} vs {key} (overall)")
        ax.set_ylim(0.0, 1.02)
        ax.grid(alpha=0.3)
        path = out_dir / f"{metric}_vs_{key}.png"
        fig.tight_layout()
        fig.savefig(path, dpi=130)
        plt.close(fig)
        artifacts.append((metric, path))

    # Per-fixture F1 facet
    fix_rows = _fixture_rows(rows)
    fixtures = sorted({r["fixture"] for r in fix_rows})
    fig, ax = plt.subplots(figsize=(8, max(4, 0.25 * len(fixtures) + 3)))
    for fix in fixtures:
        these = [r for r in fix_rows if r["fixture"] == fix]
        x = [r[f"param_{key}"] for r in these]
        y = [r["f1"] for r in these]
        idx = np.argsort([float(v) if isinstance(v, (int, float, bool)) and v is not None else 0 for v in x])
        ax.plot([x[i] for i in idx], [y[i] for i in idx], marker=".", alpha=0.7, label=_short_fixture(fix))
    ax.set_xlabel(key)
    ax.set_ylabel("F1")
    ax.set_title(f"per-fixture F1 vs {key}")
    ax.set_ylim(0.0, 1.02)
    ax.grid(alpha=0.3)
    ax.legend(loc="lower right", fontsize=7, ncol=2)
    path = out_dir / f"per_fixture_f1_vs_{key}.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    artifacts.append(("per_fixture_f1", path))
    return artifacts


def _plot_2d(rows: list[dict], keys: list[str], out_dir: Path) -> list[tuple[str, Path]]:
    agg = _agg_rows(rows, "__all__")
    k1, k2 = keys
    v1 = _unique_sorted([r[f"param_{k1}"] for r in agg])
    v2 = _unique_sorted([r[f"param_{k2}"] for r in agg])
    idx1 = {v: i for i, v in enumerate(v1)}
    idx2 = {v: i for i, v in enumerate(v2)}
    artifacts: list[tuple[str, Path]] = []
    for metric, cmap in (("precision", "Blues"), ("recall", "Oranges"), ("f1", "Greens")):
        grid = np.full((len(v1), len(v2)), np.nan)
        for r in agg:
            i = idx1[r[f"param_{k1}"]]
            j = idx2[r[f"param_{k2}"]]
            grid[i, j] = r[metric]
        fig, ax = plt.subplots(figsize=(1.4 * len(v2) + 2.5, 1.0 * len(v1) + 2))
        im = ax.imshow(grid, origin="lower", aspect="auto", cmap=cmap, vmin=0, vmax=1)
        ax.set_xticks(range(len(v2)))
        ax.set_xticklabels([str(v) for v in v2], rotation=45, ha="right")
        ax.set_yticks(range(len(v1)))
        ax.set_yticklabels([str(v) for v in v1])
        ax.set_xlabel(k2)
        ax.set_ylabel(k1)
        ax.set_title(f"{metric}  ({k1} x {k2})")
        for i in range(grid.shape[0]):
            for j in range(grid.shape[1]):
                if not np.isnan(grid[i, j]):
                    ax.text(
                        j,
                        i,
                        f"{grid[i,j]:.2f}",
                        ha="center",
                        va="center",
                        fontsize=8,
                        color="black" if grid[i, j] < 0.55 else "white",
                    )
        fig.colorbar(im, ax=ax)
        path = out_dir / f"{metric}_heatmap.png"
        fig.tight_layout()
        fig.savefig(path, dpi=130)
        plt.close(fig)
        artifacts.append((metric, path))
    return artifacts


def _plot_per_fixture_bars(rows: list[dict], out_dir: Path) -> Path:
    """Per-fixture P/R/F1 bars at the best aggregate F1 combo."""
    agg = _agg_rows(rows, "__all__")
    best = max(agg, key=lambda r: r["f1"]) if agg else None
    combo_idx = best["combo_idx"] if best else None
    fix_rows = [r for r in _fixture_rows(rows) if combo_idx is None or r["combo_idx"] == combo_idx]
    fixtures = sorted({r["fixture"] for r in fix_rows})
    metrics = ("precision", "recall", "f1")
    width = 0.27
    x = np.arange(len(fixtures))
    fig, ax = plt.subplots(figsize=(max(8, 0.5 * len(fixtures)), 5))
    for i, m in enumerate(metrics):
        vals = []
        for fix in fixtures:
            r = next((r for r in fix_rows if r["fixture"] == fix), None)
            vals.append(r[m] if r else 0.0)
        ax.bar(x + (i - 1) * width, vals, width, label=m)
    ax.set_xticks(x)
    ax.set_xticklabels([_short_fixture(f) for f in fixtures], rotation=45, ha="right", fontsize=8)
    ax.set_ylim(0.0, 1.02)
    ax.set_ylabel("score")
    title = "Per-fixture P/R/F1"
    if best is not None:
        title += f"  (at best aggregate F1 = {best['f1']:.3f})"
    ax.set_title(title)
    ax.legend()
    ax.grid(alpha=0.3, axis="y")
    path = out_dir / "per_fixture_bars.png"
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _compose_overview(
    artifacts: list[tuple[str, Path]],
    per_fixture: Path,
    out: Path,
    swept: list[str],
) -> None:
    """Stitch a 2x2 (or 1x2) collage of the most useful plots into one PNG."""
    panels: list[Path] = []
    by_name = dict(artifacts)
    for n in ("f1", "precision", "recall"):
        if n in by_name:
            panels.append(by_name[n])
    panels.append(per_fixture)
    panels = panels[:4]

    if not panels:
        return
    cols = 2 if len(panels) > 1 else 1
    rows = (len(panels) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(7.5 * cols, 5.5 * rows))
    axes = np.atleast_1d(axes).flatten()
    for ax in axes:
        ax.axis("off")
    for ax, p in zip(axes, panels, strict=False):
        img = plt.imread(p)
        ax.imshow(img)
    title = "Splitsmith ensemble sweep -- " + ("x".join(swept) if swept else "defaults snapshot")
    fig.suptitle(title, fontsize=13)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def _write_report(
    out_dir: Path,
    run_id: str,
    rows: list[dict],
    swept: list[str],
    artifacts: list[tuple[str, Path]],
    per_fixture: Path,
    overview: Path,
) -> Path:
    agg = _agg_rows(rows, "__all__")
    best = max(agg, key=lambda r: r["f1"]) if agg else None
    fixtures = sorted({r["fixture"] for r in _fixture_rows(rows)})
    class_rows = [
        r
        for r in rows
        if r["fixture"].startswith("__class_") and (best is None or r["combo_idx"] == best["combo_idx"])
    ]

    lines: list[str] = []
    lines.append(f"# Ensemble sweep report -- `{run_id}`")
    lines.append("")
    lines.append(f"![overview]({overview.name})")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    if swept:
        lines.append(f"- Swept parameters: `{', '.join(swept)}`")
    else:
        lines.append("- No parameters swept; single defaults snapshot.")
    lines.append(f"- Combos evaluated: {len({r['combo_idx'] for r in rows})}")
    lines.append(f"- Fixtures in corpus: {len(fixtures)}")
    if best is not None:
        lines.append("")
        lines.append("### Best aggregate F1")
        lines.append("")
        lines.append(
            f"- F1 = **{best['f1']:.4f}**, "
            f"precision = {best['precision']:.4f}, recall = {best['recall']:.4f}"
        )
        lines.append(
            f"- True positives: {best['true_pos']} / {best['n_positives']}; "
            f"false positives: {best['false_pos']}; "
            f"false negatives: {best['false_neg']}"
        )
        lines.append("")
        lines.append("Parameters at the best point:")
        lines.append("")
        lines.append("| key | value |")
        lines.append("|---|---|")
        for k in sorted(best.keys()):
            if k.startswith("param_"):
                lines.append(f"| `{k.removeprefix('param_')}` | `{best[k]}` |")

    if class_rows:
        lines.append("")
        lines.append("### Per camera class (at best aggregate F1)")
        lines.append("")
        lines.append("| class | kept | TP | FP | FN | precision | recall | F1 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in class_rows:
            cls = r["fixture"].removeprefix("__class_")
            lines.append(
                f"| {cls} | {r['n_kept']} | {r['true_pos']} | {r['false_pos']} | "
                f"{r['false_neg']} | {r['precision']:.3f} | {r['recall']:.3f} | {r['f1']:.3f} |"
            )

    lines.append("")
    lines.append("## Plots")
    lines.append("")
    for name, path in artifacts:
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"![{name}]({path.name})")
        lines.append("")
    lines.append("### Per-fixture")
    lines.append("")
    lines.append(f"![per-fixture]({per_fixture.name})")
    lines.append("")

    fix_rows = [r for r in _fixture_rows(rows) if best is None or r["combo_idx"] == best["combo_idx"]]
    if fix_rows:
        lines.append("## Per-fixture table (at best aggregate F1)")
        lines.append("")
        lines.append("| fixture | camera | kept | TP | FP | FN | P | R | F1 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in sorted(fix_rows, key=lambda r: -r["f1"]):
            lines.append(
                f"| {r['fixture']} | {r['camera_class']} | {r['n_kept']} | "
                f"{r['true_pos']} | {r['false_pos']} | {r['false_neg']} | "
                f"{r['precision']:.3f} | {r['recall']:.3f} | {r['f1']:.3f} |"
            )

    report_path = out_dir / "report.md"
    report_path.write_text("\n".join(lines) + "\n")
    return report_path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    p.add_argument("--run-id", type=str, default=None)
    args = p.parse_args()

    if not args.runs.exists():
        raise SystemExit(f"{args.runs} does not exist -- run scripts/run_sweep.py first.")
    rows, run_id = _load_runs(args.runs, args.run_id)
    out_dir = OUT_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    swept = _swept_keys(rows)

    artifacts: list[tuple[str, Path]] = []
    if len(swept) == 1:
        artifacts = _plot_1d(rows, swept[0], out_dir)
    elif len(swept) >= 2:
        artifacts = _plot_2d(rows, swept[:2], out_dir)
    per_fixture = _plot_per_fixture_bars(rows, out_dir)

    overview = out_dir / "overview.png"
    _compose_overview(artifacts, per_fixture, overview, swept)
    report = _write_report(out_dir, run_id, rows, swept, artifacts, per_fixture, overview)

    latest_png = OUT_DIR / "latest_overview.png"
    latest_md = OUT_DIR / "latest_report.md"
    shutil.copyfile(overview, latest_png)
    # Rewrite image paths inside the copied report so the "latest" pointer resolves
    # against the run-id directory rather than the cwd.
    rewritten = report.read_text().replace("(", f"({run_id}/").replace(f"({run_id}/http", "(http")
    latest_md.write_text(rewritten)

    # Also drop a tracked copy under docs/ so the README image works for
    # anyone cloning the repo without first running the pipeline. build/
    # is gitignored, so the latest_* pointers there are only useful
    # locally.
    docs_dir = REPO_ROOT / "docs" / "ensemble_dashboard"
    docs_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(overview, docs_dir / "latest_overview.png")
    for _, path in artifacts:
        shutil.copyfile(path, docs_dir / path.name)
    shutil.copyfile(per_fixture, docs_dir / per_fixture.name)
    # Rewrite the report's overview reference to the docs/ filename
    # (latest_overview.png) since we deliberately don't copy the
    # composite as ``overview.png`` -- it would shadow the run-specific
    # naming on subsequent rebuilds.
    docs_report = report.read_text().replace("![overview](overview.png)", "![overview](latest_overview.png)")
    (docs_dir / "latest_report.md").write_text(docs_report)

    print(f"run_id: {run_id}")
    print(f"plots: {out_dir}")
    print(f"report: {report}")
    print(f"latest pointers: {latest_png}, {latest_md}")
    print(json.dumps({"run_id": run_id, "plots_dir": str(out_dir), "report": str(report)}))


if __name__ == "__main__":
    main()
