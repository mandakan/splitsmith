"""Held-out voter C evaluation (#1045).

The shipped threshold comes from a by-candidate split, which puts one
stage's candidates (and a second camera's copy of the same shots) on
both sides of every fold. These pin the grouped replacements the report
and the sweep use: grouping keys, a split that cannot memorise a stage,
and the join onto the sweep refusing to guess.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _script(name: str):  # type: ignore[no-untyped-def]
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        return __import__(name)
    finally:
        sys.path.pop(0)


def test_both_cameras_of_a_stage_share_a_stage_group() -> None:
    mod = _script("build_ensemble_artifacts")
    base = "stage-shots-blacksmith-2026-stage5-s97dcec94"
    twin = f"{base}-apple-iphone17pro"
    assert (Path("tests/fixtures") / f"{twin}.json").exists(), "twin fixture moved; pick another pair"
    assert mod._event_groups(base) == mod._event_groups(twin) == ("blacksmith-2026", "blacksmith-2026:5")


def test_grouped_split_cannot_memorise_a_stage() -> None:
    """Each stage gets a random label and a feature that names the stage.
    A by-candidate split scores held-out rows from stages it trained on and
    looks near perfect; the grouped split never has, and falls to chance.
    That gap is the leak the grouped figures exist to remove."""
    from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold

    mod = _script("build_ensemble_artifacts")
    rng = np.random.default_rng(0)
    n_groups, per_group = 40, 12
    groups = np.repeat(np.arange(n_groups), per_group)
    group_label = rng.integers(0, 2, n_groups)
    y = group_label[groups]
    feats = np.column_stack([groups.astype(np.float64), rng.normal(size=len(groups))])

    stratified = mod._heldout_probs(feats, y, groups, StratifiedKFold(5, shuffle=True, random_state=42))
    grouped = mod._heldout_probs(feats, y, groups, StratifiedGroupKFold(5, shuffle=True, random_state=42))

    def accuracy(p: np.ndarray) -> float:
        return float(((p >= 0.5) == y).mean())

    assert accuracy(stratified) > 0.95
    assert accuracy(grouped) < 0.75


def test_leave_one_match_out_needs_two_matches() -> None:
    from sklearn.model_selection import LeaveOneGroupOut

    mod = _script("build_ensemble_artifacts")
    feats = np.arange(20, dtype=np.float64).reshape(-1, 1)
    y = np.array([0, 1] * 10)
    assert mod._heldout_probs(feats, y, np.zeros(20), LeaveOneGroupOut()) is None


def test_prf_skips_unscored_rows_and_counts_them() -> None:
    mod = _script("build_ensemble_artifacts")
    probs = np.array([0.9, 0.2, np.nan, 0.8])
    labels = np.array([1, 1, 1, 0])
    m = mod._prf_at(probs, labels, 0.5)
    assert (m["tp"], m["fp"], m["fn"], m["n_unscored"]) == (1, 1, 1, 1)


def _sweep_rows() -> list[dict]:
    return [
        {"fixture": "f", "candidate_idx": 0, "t_absolute": 1.0},
        {"fixture": "f", "candidate_idx": 1, "t_absolute": 2.0},
    ]


def _write_oof(path: Path, t_second: float) -> None:
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "fixture": "f",
                    "candidate_idx": i,
                    "t_absolute": t,
                    "score_c_stratified": 0.9,
                    "score_c_grouped": 0.6,
                    "score_c_lomo": 0.4,
                }
                for i, t in enumerate((1.0, t_second))
            ]
        ),
        path,
    )


def test_sweep_join_fills_every_heldout_column(tmp_path: Path) -> None:
    mod = _script("build_sweep_signals")
    _write_oof(tmp_path / "oof.parquet", 2.0)
    rows = _sweep_rows()
    info = mod._attach_heldout_scores(rows, tmp_path / "oof.parquet")
    assert info["n_matched"] == 2
    assert [r["score_c_grouped"] for r in rows] == [0.6, 0.6]


def test_sweep_join_refuses_a_candidate_at_another_time(tmp_path: Path) -> None:
    mod = _script("build_sweep_signals")
    _write_oof(tmp_path / "oof.parquet", 2.5)
    with pytest.raises(SystemExit, match="same detector"):
        mod._attach_heldout_scores(_sweep_rows(), tmp_path / "oof.parquet")


def test_sweep_join_without_a_file_leaves_nan(tmp_path: Path) -> None:
    mod = _script("build_sweep_signals")
    rows = _sweep_rows()
    assert mod._attach_heldout_scores(rows, tmp_path / "absent.parquet")["source"] is None
    assert all(np.isnan(r["score_c_lomo"]) for r in rows)


def test_replaying_a_partly_missing_heldout_column_is_refused() -> None:
    """A NaN would vote "no" and read as lost recall; refuse instead."""
    mod = _script("run_sweep")
    raw = {"score_c": np.array([0.9, 0.1]), "score_c_grouped": np.array([0.8, np.nan])}
    assert mod._score_c_column(raw, "shipped").tolist() == [0.9, 0.1]
    with pytest.raises(SystemExit, match="NaN on 1 of 2"):
        mod._score_c_column(raw, "grouped")
