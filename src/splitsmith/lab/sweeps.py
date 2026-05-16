"""Read-only access to the ensemble sweep dashboard for the Lab UI.

The CLI in ``scripts/run_sweep.py`` writes ``build/sweeps/runs.parquet``
(every parameter combo x fixture row) and ``scripts/plot_sweep.py``
emits a per-run directory with rendered PNGs + ``report.md``. This
module wraps those artifacts in Pydantic shapes the FastAPI server
can hand to the SPA without re-implementing parquet parsing
client-side.

No write APIs: launching new sweeps stays on the CLI for now. The UI
just consumes what's on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[3]
SWEEPS_DIR = REPO_ROOT / "build" / "sweeps"
RUNS_PARQUET = SWEEPS_DIR / "runs.parquet"

PNG_FILENAMES = {"overview", "per_fixture_bars"}  # filename whitelist for the static endpoint


class SweepRunSummary(BaseModel):
    """One row per ``run_id`` -- header info for the Sweeps list."""

    run_id: str
    signals_build_id: str
    swept_keys: list[str] = Field(description="Names of parameters that actually varied across this run.")
    n_combos: int
    n_fixtures: int
    best_f1: float
    best_precision: float
    best_recall: float
    best_kept: int
    best_true_pos: int
    best_false_pos: int
    best_false_neg: int
    best_combo_idx: int


class SweepFixtureRow(BaseModel):
    """One (combo, fixture) datapoint in a sweep."""

    fixture: str
    camera_class: str
    n_candidates: int
    n_positives: int
    n_kept: int
    true_pos: int
    false_pos: int
    false_neg: int
    precision: float
    recall: float
    f1: float


class SweepComboRow(BaseModel):
    """One parameter combination + its aggregate metrics."""

    combo_idx: int
    params: dict[str, Any]
    aggregate: SweepFixtureRow
    per_class: list[SweepFixtureRow]
    per_fixture: list[SweepFixtureRow]


class SweepRunDetail(BaseModel):
    """Full payload for one run_id: summary + every combo."""

    summary: SweepRunSummary
    combos: list[SweepComboRow]
    available_plots: list[str] = Field(
        description=(
            "Plot stems present in build/sweeps/<run_id>/ that the UI "
            "can request via the static endpoint. Only the composite "
            "overview + per-fixture bars are guaranteed; metric heatmaps "
            "or line plots depend on whether the sweep was 1D / 2D."
        ),
    )


def _row_to_fixture(row: dict[str, Any]) -> SweepFixtureRow:
    return SweepFixtureRow(
        fixture=row["fixture"],
        camera_class=row["camera_class"] or "__agg__",
        n_candidates=int(row["n_candidates"]),
        n_positives=int(row["n_positives"]),
        n_kept=int(row["n_kept"]),
        true_pos=int(row["true_pos"]),
        false_pos=int(row["false_pos"]),
        false_neg=int(row["false_neg"]),
        precision=float(row["precision"]),
        recall=float(row["recall"]),
        f1=float(row["f1"]),
    )


def _load_all_rows() -> list[dict[str, Any]]:
    if not RUNS_PARQUET.exists():
        return []
    return pq.read_table(RUNS_PARQUET).to_pylist()


def _params_for(combo_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract the ``param_*`` columns from any row of the combo."""
    return {k.removeprefix("param_"): v for k, v in combo_rows[0].items() if k.startswith("param_")}


def _swept_keys_for(rows: list[dict[str, Any]]) -> list[str]:
    """Filter the declared sweep keys to those that actually vary."""
    if not rows:
        return []
    declared = [k for k in (rows[0].get("swept_keys") or "").split(",") if k]
    agg = [r for r in rows if r["fixture"] == "__all__"]
    out: list[str] = []
    for k in declared:
        seen = {r[f"param_{k}"] for r in agg}
        if len(seen) > 1:
            out.append(k)
    return out


def list_runs() -> list[SweepRunSummary]:
    """Return one summary per ``run_id``, newest first.

    Newest is approximated by sorting on the run_id prefix (ISO
    timestamp), which is how ``run_sweep.py`` constructs the id.
    """
    rows = _load_all_rows()
    by_run: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_run.setdefault(r["run_id"], []).append(r)

    summaries: list[SweepRunSummary] = []
    for run_id, run_rows in by_run.items():
        agg = [r for r in run_rows if r["fixture"] == "__all__"]
        if not agg:
            continue
        best = max(agg, key=lambda r: r["f1"])
        fixtures = {r["fixture"] for r in run_rows if not r["fixture"].startswith("__")}
        summaries.append(
            SweepRunSummary(
                run_id=run_id,
                signals_build_id=run_rows[0]["signals_build_id"],
                swept_keys=_swept_keys_for(run_rows),
                n_combos=len({r["combo_idx"] for r in run_rows}),
                n_fixtures=len(fixtures),
                best_f1=float(best["f1"]),
                best_precision=float(best["precision"]),
                best_recall=float(best["recall"]),
                best_kept=int(best["n_kept"]),
                best_true_pos=int(best["true_pos"]),
                best_false_pos=int(best["false_pos"]),
                best_false_neg=int(best["false_neg"]),
                best_combo_idx=int(best["combo_idx"]),
            )
        )
    summaries.sort(key=lambda s: s.run_id, reverse=True)
    return summaries


def get_run(run_id: str) -> SweepRunDetail | None:
    """Return the full payload for ``run_id``, or None if absent."""
    rows = [r for r in _load_all_rows() if r["run_id"] == run_id]
    if not rows:
        return None
    summaries = [s for s in list_runs() if s.run_id == run_id]
    if not summaries:
        return None
    summary = summaries[0]

    by_combo: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_combo.setdefault(int(r["combo_idx"]), []).append(r)

    combos: list[SweepComboRow] = []
    for idx in sorted(by_combo.keys()):
        combo_rows = by_combo[idx]
        agg = next(r for r in combo_rows if r["fixture"] == "__all__")
        per_class = [
            _row_to_fixture({**r, "camera_class": r["fixture"].removeprefix("__class_")})
            for r in combo_rows
            if r["fixture"].startswith("__class_")
        ]
        per_fixture = [_row_to_fixture(r) for r in combo_rows if not r["fixture"].startswith("__")]
        combos.append(
            SweepComboRow(
                combo_idx=idx,
                params=_params_for(combo_rows),
                aggregate=_row_to_fixture(agg),
                per_class=per_class,
                per_fixture=per_fixture,
            )
        )

    run_dir = SWEEPS_DIR / run_id
    available_plots: list[str] = []
    if run_dir.is_dir():
        for p in sorted(run_dir.glob("*.png")):
            available_plots.append(p.stem)

    return SweepRunDetail(
        summary=summary,
        combos=combos,
        available_plots=available_plots,
    )


def plot_path(run_id: str, plot_name: str) -> Path | None:
    """Resolve ``build/sweeps/<run_id>/<plot_name>.png`` if it exists.

    Used by the FastAPI route to serve images without exposing the
    rest of ``build/`` to the SPA. The plot_name is constrained to
    alnum / underscore by the caller.
    """
    if not plot_name.replace("_", "").isalnum():
        return None
    candidate = SWEEPS_DIR / run_id / f"{plot_name}.png"
    try:
        candidate.relative_to(SWEEPS_DIR)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate
