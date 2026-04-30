"""Tests for csv_gen.write_splits_csv / read_splits_csv."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.config import Shot
from splitsmith.csv_gen import CSV_HEADER, read_splits_csv, write_splits_csv


def _make_shot(
    shot_number: int,
    time_absolute: float,
    *,
    beep_time: float = 1.0,
    prev_t: float | None = None,
    peak: float = 0.5,
    confidence: float = 0.7,
    notes: str = "",
) -> Shot:
    if prev_t is None:
        prev_t = beep_time
    return Shot(
        shot_number=shot_number,
        time_absolute=time_absolute,
        time_from_beep=time_absolute - beep_time,
        split=time_absolute - prev_t,
        peak_amplitude=peak,
        confidence=confidence,
        notes=notes,
    )


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    beep = 5.000
    shots = [
        _make_shot(1, beep + 1.420, beep_time=beep, peak=0.55, confidence=0.92, notes="draw"),
        _make_shot(
            2, beep + 1.630, beep_time=beep, prev_t=beep + 1.420, peak=0.48, confidence=0.81
        ),
        _make_shot(
            3, beep + 1.820, beep_time=beep, prev_t=beep + 1.630, peak=0.42, confidence=0.74
        ),
    ]
    out = tmp_path / "splits.csv"
    write_splits_csv(shots, out)

    text = out.read_text()
    lines = text.splitlines()
    assert lines[0] == ",".join(CSV_HEADER)
    # First row uses 3 decimal places for time and split.
    assert lines[1].startswith("1,1.420,1.420,0.5500,0.920,draw")

    rows = read_splits_csv(out)
    assert [r.shot_number for r in rows] == [1, 2, 3]
    assert rows[0].time_from_start == pytest.approx(1.420, abs=1e-3)
    assert rows[0].split == pytest.approx(1.420, abs=1e-3)
    assert rows[0].notes == "draw"
    assert rows[1].time_from_start == pytest.approx(1.630, abs=1e-3)
    assert rows[1].split == pytest.approx(0.210, abs=1e-3)
    assert rows[2].confidence == pytest.approx(0.740, abs=1e-3)


def test_write_empty_list_emits_only_header(tmp_path: Path) -> None:
    out = tmp_path / "empty.csv"
    write_splits_csv([], out)
    assert out.read_text().strip() == ",".join(CSV_HEADER)
    assert read_splits_csv(out) == []


def test_notes_with_commas_and_quotes_round_trip(tmp_path: Path) -> None:
    shots = [
        _make_shot(1, 1.0, peak=0.3, confidence=0.5, notes='draw, "fast" -- 0.8s'),
    ]
    out = tmp_path / "quoted.csv"
    write_splits_csv(shots, out)
    rows = read_splits_csv(out)
    assert rows[0].notes == 'draw, "fast" -- 0.8s'


def test_read_rejects_unexpected_header(tmp_path: Path) -> None:
    out = tmp_path / "bad.csv"
    out.write_text("foo,bar,baz\n1,2,3\n")
    with pytest.raises(ValueError, match="unexpected CSV header"):
        read_splits_csv(out)


def test_user_can_drop_rows_without_breaking_read(tmp_path: Path) -> None:
    """Simulates the hand-cull workflow: delete a row, splits/numbering preserved."""
    shots = [
        _make_shot(1, 1.0, peak=0.5, confidence=0.9),
        _make_shot(2, 1.5, prev_t=1.0, peak=0.5, confidence=0.4),  # the false positive
        _make_shot(3, 2.0, prev_t=1.5, peak=0.5, confidence=0.9),
    ]
    out = tmp_path / "splits.csv"
    write_splits_csv(shots, out)

    # Drop the middle row (the "false positive").
    lines = out.read_text().splitlines()
    out.write_text("\n".join([lines[0], lines[1], lines[3]]) + "\n")

    rows = read_splits_csv(out)
    assert [r.shot_number for r in rows] == [1, 3]
    # The retained row keeps its original split value -- the consumer of the CSV
    # is responsible for recomputing if it cares about gap-to-previous.
    assert rows[1].split == pytest.approx(0.500, abs=1e-3)
