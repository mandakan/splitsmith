"""Tests for the hand-audit merge flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith.audit import (
    apply_audit_to_fixture,
    is_kept,
    read_kept_candidate_numbers,
)


def _make_fixture(path: Path, candidates: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "source": "test",
                "beep_time": 0.5,
                "stage_time_seconds": 10.0,
                "shots": [],
                "_candidates_pending_audit": {"candidates": candidates},
            },
            indent=2,
        )
    )


def _make_candidates_csv(path: Path, rows: list[tuple[str, int, float, float]]) -> None:
    """rows: (audit_keep, candidate_number, time_fixture_s, ms_after_beep)."""
    lines = ["audit_keep,candidate_number,time_fixture_s,ms_after_beep"]
    for keep, n, t, ms in rows:
        lines.append(f"{keep},{n},{t},{ms}")
    path.write_text("\n".join(lines) + "\n")


def test_is_kept_recognizes_common_marks() -> None:
    for v in ["Y", "y", "yes", "YES", "1", "x", "X", "true", "TRUE", "keep", "t"]:
        assert is_kept(v), v
    for v in ["", " ", "N", "no", "0", "false", None]:
        assert not is_kept(v), v


def test_read_kept_candidate_numbers_preserves_order(tmp_path: Path) -> None:
    csv_path = tmp_path / "c.csv"
    _make_candidates_csv(
        csv_path,
        [
            ("", 1, 1.0, 100),
            ("Y", 2, 1.5, 200),
            ("", 3, 2.0, 300),
            ("x", 4, 2.5, 400),
            ("yes", 5, 3.0, 500),
        ],
    )
    assert read_kept_candidate_numbers(csv_path) == [2, 4, 5]


def test_read_rejects_csv_without_audit_keep_column(tmp_path: Path) -> None:
    csv_path = tmp_path / "c.csv"
    csv_path.write_text("candidate_number,time_fixture_s\n1,1.0\n")
    with pytest.raises(ValueError, match="audit_keep"):
        read_kept_candidate_numbers(csv_path)


def test_apply_audit_writes_shots_array(tmp_path: Path) -> None:
    csv_path = tmp_path / "c.csv"
    json_path = tmp_path / "f.json"
    _make_candidates_csv(
        csv_path,
        [
            ("Y", 1, 1.0, 500),
            ("", 2, 1.2, 700),
            ("Y", 3, 1.5, 1000),
        ],
    )
    _make_fixture(
        json_path,
        [
            {"candidate_number": 1, "time": 1.0, "ms_after_beep": 500.0},
            {"candidate_number": 2, "time": 1.2, "ms_after_beep": 700.0},
            {"candidate_number": 3, "time": 1.5, "ms_after_beep": 1000.0},
        ],
    )

    n = apply_audit_to_fixture(csv_path, json_path)
    assert n == 2

    data = json.loads(json_path.read_text())
    assert [s["shot_number"] for s in data["shots"]] == [1, 2]
    assert [s["candidate_number"] for s in data["shots"]] == [1, 3]
    assert data["shots"][0]["time"] == pytest.approx(1.0)
    assert data["shots"][1]["time"] == pytest.approx(1.5)
    # The candidates block is preserved so the audit is re-runnable.
    assert len(data["_candidates_pending_audit"]["candidates"]) == 3


def test_apply_audit_rewrite_replaces_previous_shots(tmp_path: Path) -> None:
    csv_path = tmp_path / "c.csv"
    json_path = tmp_path / "f.json"
    _make_candidates_csv(csv_path, [("Y", 2, 1.2, 700)])
    _make_fixture(
        json_path,
        [
            {"candidate_number": 1, "time": 1.0, "ms_after_beep": 500.0},
            {"candidate_number": 2, "time": 1.2, "ms_after_beep": 700.0},
        ],
    )
    # First audit
    apply_audit_to_fixture(csv_path, json_path)
    data1 = json.loads(json_path.read_text())
    assert len(data1["shots"]) == 1
    # Re-audit with a different selection
    _make_candidates_csv(csv_path, [("Y", 1, 1.0, 500), ("Y", 2, 1.2, 700)])
    apply_audit_to_fixture(csv_path, json_path)
    data2 = json.loads(json_path.read_text())
    assert len(data2["shots"]) == 2


def test_apply_audit_raises_on_unknown_candidate_number(tmp_path: Path) -> None:
    csv_path = tmp_path / "c.csv"
    json_path = tmp_path / "f.json"
    _make_candidates_csv(csv_path, [("Y", 999, 99.0, 99999)])
    _make_fixture(json_path, [{"candidate_number": 1, "time": 1.0, "ms_after_beep": 500.0}])
    with pytest.raises(ValueError, match="999"):
        apply_audit_to_fixture(csv_path, json_path)


def test_apply_audit_raises_on_fixture_missing_candidates_block(tmp_path: Path) -> None:
    csv_path = tmp_path / "c.csv"
    json_path = tmp_path / "f.json"
    _make_candidates_csv(csv_path, [("Y", 1, 1.0, 500)])
    json_path.write_text(json.dumps({"beep_time": 0.5, "shots": []}) + "\n")
    with pytest.raises(ValueError, match="candidates block"):
        apply_audit_to_fixture(csv_path, json_path)
