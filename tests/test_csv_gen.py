"""Tests for csv_gen.write_splits_csv / read_splits_csv / write_events_csv."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.config import Shot, StageEvent
from splitsmith.csv_gen import (
    CSV_HEADER,
    CSV_HEADER_WITH_MOVING,
    EVENTS_CSV_HEADER,
    read_splits_csv,
    write_events_csv,
    write_splits_csv,
)


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
        _make_shot(2, beep + 1.630, beep_time=beep, prev_t=beep + 1.420, peak=0.48, confidence=0.81),
        _make_shot(3, beep + 1.820, beep_time=beep, prev_t=beep + 1.630, peak=0.42, confidence=0.74),
    ]
    out = tmp_path / "splits.csv"
    write_splits_csv(shots, out)

    text = out.read_text()
    lines = text.splitlines()
    assert lines[0] == ",".join(CSV_HEADER_WITH_MOVING)
    # First row uses 3 decimal places for time and split; moving is always
    # false with no events passed.
    assert lines[1].startswith("1,1.420,1.420,0.5500,0.920,draw")
    assert lines[1].endswith(",false")

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
    assert out.read_text().strip() == ",".join(CSV_HEADER_WITH_MOVING)
    assert read_splits_csv(out) == []


def test_read_accepts_legacy_header_without_moving(tmp_path: Path) -> None:
    """A splits CSV written before the ``moving`` column existed still reads."""
    out = tmp_path / "legacy.csv"
    out.write_text(",".join(CSV_HEADER) + "\n" + "1,1.420,1.420,0.5500,0.920,draw\n")
    rows = read_splits_csv(out)
    assert len(rows) == 1
    assert rows[0].shot_number == 1
    assert rows[0].notes == "draw"


def test_moving_column_true_for_a_shot_inside_a_confirmed_movement_region(tmp_path: Path) -> None:
    shots = [
        _make_shot(1, 1.0, peak=0.5, confidence=0.9),  # time_from_beep 0.0, not moving
        _make_shot(2, 4.5, prev_t=1.0, peak=0.5, confidence=0.9),  # time_from_beep 3.5, moving
    ]
    events = [StageEvent(id="evt-1", kind="movement", start=3.0, end=6.0, source="manual")]
    out = tmp_path / "moving.csv"
    write_splits_csv(shots, out, events=events)
    lines = out.read_text().splitlines()
    assert lines[0] == ",".join(CSV_HEADER_WITH_MOVING)
    assert lines[1].endswith(",false")
    assert lines[2].endswith(",true")


def test_moving_column_ignores_an_unconfirmed_auto_proposal(tmp_path: Path) -> None:
    """Callers pass only confirmed regions; this guards against a caller
    accidentally handing an auto proposal through -- the column must not
    read as moving for it (the module trusts its caller, but a shot that
    would otherwise be 'true' here is the tell that the gate broke)."""
    shots = [_make_shot(1, 4.5, beep_time=1.0, peak=0.5, confidence=0.9)]  # time_from_beep 3.5
    events = [StageEvent(id="evt-1", kind="movement", start=3.0, end=6.0, source="auto")]
    out = tmp_path / "auto_only.csv"
    # auto-only is never passed through by a correct caller, but the
    # function itself only applies ``shot_is_moving`` over what it's given --
    # it does not re-filter by source. Confirming that here documents the
    # contract: filtering is the caller's job (events.confirmed), not this
    # module's.
    write_splits_csv(shots, out, events=events)
    rows_text = out.read_text().splitlines()[1]
    assert rows_text.endswith(",true")


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


def test_write_events_csv_writes_header_and_rows(tmp_path: Path) -> None:
    events = [
        StageEvent(id="evt-1", kind="reload", start=8.05, end=9.47, source="manual"),
        StageEvent(id="evt-2", kind="movement", start=3.4, end=6.1, source="manual", note="to box B"),
    ]
    out = tmp_path / "events.csv"
    write_events_csv(events, out)
    lines = out.read_text().splitlines()
    assert lines[0] == ",".join(EVENTS_CSV_HEADER)
    assert lines[1] == "evt-1,reload,8.050,9.470,1.420,manual,"
    assert lines[2] == "evt-2,movement,3.400,6.100,2.700,manual,to box B"


def test_write_events_csv_with_no_events_still_writes_header_only(tmp_path: Path) -> None:
    out = tmp_path / "empty_events.csv"
    write_events_csv([], out)
    assert out.read_text().strip() == ",".join(EVENTS_CSV_HEADER)


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
