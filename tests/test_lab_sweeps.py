"""Lab sweeps dashboard: parquet -> Pydantic + path-traversal guard.

The sweep endpoints are read-only: the only complexity worth pinning
is (a) the parquet -> Pydantic transform on a synthetic runs.parquet
and (b) the plot_path filename guard that prevents an attacker from
walking out of ``build/sweeps/<run_id>/`` via ``..`` segments.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from splitsmith.lab import sweeps as sweeps_module


def _make_row(
    run_id: str,
    fixture: str,
    combo_idx: int,
    *,
    f1: float,
    precision: float = 0.9,
    recall: float = 0.95,
    swept_keys: str = "consensus",
    consensus: int = 3,
) -> dict:
    return {
        "run_id": run_id,
        "combo_idx": combo_idx,
        "signals_build_id": "2026-05-11T00-00-00Z_abc1234",
        "fixture": fixture,
        "camera_class": "__agg__" if fixture.startswith("__") else "headcam",
        "n_candidates": 100,
        "n_positives": 20,
        "n_kept": 21,
        "true_pos": 19,
        "false_pos": 2,
        "false_neg": 1,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "disagreement_1_count": 0,
        "disagreement_1_pos": 0,
        "solo_a": 0,
        "solo_b": 0,
        "solo_c": 0,
        "solo_d": 0,
        "solo_e": 0,
        "voter_a_floor_eff": 0.018,
        "voter_b_threshold_eff": 0.05,
        "voter_c_threshold_eff": 0.1,
        "voter_d_threshold_eff": 0.05,
        "voter_e_threshold_eff": None,
        "swept_keys": swept_keys,
        "param_consensus": consensus,
        "param_apriori_boost": 1.0,
        "param_c_required": True,
    }


@pytest.fixture
def synthetic_runs_parquet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Plant a small runs.parquet + plot file the sweeps module can read."""
    sweeps_dir = tmp_path / "sweeps"
    sweeps_dir.mkdir()
    monkeypatch.setattr(sweeps_module, "SWEEPS_DIR", sweeps_dir)
    monkeypatch.setattr(sweeps_module, "RUNS_PARQUET", sweeps_dir / "runs.parquet")

    rows = [
        # Run A: 2 combos x (1 fixture + agg)
        _make_row("runA", "fixA", 0, f1=0.8, consensus=2),
        _make_row("runA", "__all__", 0, f1=0.8, consensus=2),
        _make_row("runA", "fixA", 1, f1=0.91, consensus=3),
        _make_row("runA", "__all__", 1, f1=0.91, consensus=3),
        # Run B: 1 combo
        _make_row("runB", "fixA", 0, f1=0.7, swept_keys=""),
        _make_row("runB", "__all__", 0, f1=0.7, swept_keys=""),
    ]
    pq.write_table(pa.Table.from_pylist(rows), sweeps_dir / "runs.parquet")

    plot_dir = sweeps_dir / "runA"
    plot_dir.mkdir()
    (plot_dir / "overview.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return sweeps_dir


def test_list_runs_orders_newest_first_and_picks_best_f1(synthetic_runs_parquet: Path) -> None:
    runs = sweeps_module.list_runs()
    assert [r.run_id for r in runs] == ["runB", "runA"]
    run_a = next(r for r in runs if r.run_id == "runA")
    assert run_a.best_f1 == pytest.approx(0.91)
    assert run_a.best_combo_idx == 1
    assert run_a.swept_keys == ["consensus"]
    assert run_a.n_combos == 2
    assert run_a.n_fixtures == 1


def test_swept_keys_filters_singleton_values(synthetic_runs_parquet: Path) -> None:
    run_b = next(r for r in sweeps_module.list_runs() if r.run_id == "runB")
    # runB declares no swept keys at all
    assert run_b.swept_keys == []


def test_get_run_returns_combos_and_plots(synthetic_runs_parquet: Path) -> None:
    detail = sweeps_module.get_run("runA")
    assert detail is not None
    assert detail.summary.run_id == "runA"
    assert len(detail.combos) == 2
    best = detail.combos[detail.summary.best_combo_idx]
    assert best.aggregate.f1 == pytest.approx(0.91)
    assert "consensus" in best.params
    assert "overview" in detail.available_plots


def test_get_run_missing_returns_none(synthetic_runs_parquet: Path) -> None:
    assert sweeps_module.get_run("never-ran") is None


def test_plot_path_returns_existing_png(synthetic_runs_parquet: Path) -> None:
    p = sweeps_module.plot_path("runA", "overview")
    assert p is not None
    assert p.name == "overview.png"


def test_plot_path_rejects_traversal_segments(synthetic_runs_parquet: Path) -> None:
    """A plot_name containing slashes or dots must not escape SWEEPS_DIR."""
    assert sweeps_module.plot_path("runA", "../runA/overview") is None
    assert sweeps_module.plot_path("runA", "..") is None
    assert sweeps_module.plot_path("runA", "a/b") is None
    # plot_path appends ``.png`` itself; passing it leaves a stray dot
    # that fails the alnum + underscore filter.
    assert sweeps_module.plot_path("runA", "overview.png") is None


def test_plot_path_missing_file_returns_none(synthetic_runs_parquet: Path) -> None:
    assert sweeps_module.plot_path("runA", "no_such_plot") is None


def test_list_runs_empty_parquet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sweeps_module, "SWEEPS_DIR", tmp_path)
    monkeypatch.setattr(sweeps_module, "RUNS_PARQUET", tmp_path / "missing.parquet")
    assert sweeps_module.list_runs() == []
