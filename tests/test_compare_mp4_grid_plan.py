from pathlib import Path

import pytest

from splitsmith.compare import mp4_grid
from splitsmith.compare.layout import grid_shape
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle


def _stage(n: int, *, trim: Path, beep: float, duration: float) -> CompareStageBundle:
    return CompareStageBundle(
        stage_number=n,
        stage_name=f"Stage {n}",
        trim_path=trim,
        audit_path=Path("/nonexistent.json"),
        beep_offset_in_clip=beep,
        duration_seconds=duration,
        width=1920,
        height=1080,
        frame_rate_num=30000,
        frame_rate_den=1001,
    )


def _bundle(label: str, stages: dict[int, CompareStageBundle]) -> CompareShooterBundle:
    return CompareShooterBundle(label=label, project_root=Path(f"/p/{label}"), stages_by_number=stages)


def test_grid_shape_returns_rows_cols():
    assert grid_shape("2x2") == (2, 2)
    assert grid_shape("2up-h") == (1, 2)
    assert grid_shape("2up-v") == (2, 1)
    assert grid_shape("1up") == (1, 1)


def test_tiles_are_alphabetical_and_slot_stable_across_stages():
    a = _bundle("Anders", {1: _stage(1, trim=Path("/a1.mp4"), beep=2.0, duration=12.0)})
    m = _bundle(
        "Mathias",
        {
            1: _stage(1, trim=Path("/m1.mp4"), beep=3.0, duration=14.0),
            2: _stage(2, trim=Path("/m2.mp4"), beep=1.0, duration=9.0),
        },
    )

    plans = mp4_grid.build_stage_plans(
        [m, a], audio_label="Mathias", head_pad_seconds=1.0, tail_pad_seconds=0.0
    )

    assert [p.stage_number for p in plans] == [1, 2]
    # Alphabetical, and the same label holds the same (row, col) in both stages.
    assert [t.label for t in plans[0].tiles] == ["Anders", "Mathias"]
    assert [t.label for t in plans[1].tiles] == ["Anders", "Mathias"]
    assert plans[0].tiles[0].row == plans[1].tiles[0].row
    assert plans[0].tiles[0].col == plans[1].tiles[0].col


def test_missing_trim_becomes_a_filler_tile_not_a_dropped_slot():
    a = _bundle("Anders", {1: _stage(1, trim=Path("/a1.mp4"), beep=2.0, duration=12.0)})
    m = _bundle(
        "Mathias",
        {
            1: _stage(1, trim=Path("/m1.mp4"), beep=3.0, duration=14.0),
            2: _stage(2, trim=Path("/m2.mp4"), beep=1.0, duration=9.0),
        },
    )

    plans = mp4_grid.build_stage_plans(
        [m, a], audio_label="Mathias", head_pad_seconds=1.0, tail_pad_seconds=0.0
    )

    stage2 = plans[1]
    anders = next(t for t in stage2.tiles if t.label == "Anders")
    assert anders.trim_path is None
    assert len(stage2.tiles) == 2  # slot kept, not dropped


def test_seek_and_duration_are_beep_aligned():
    # head_pad 1.0: a beep at 3.0 seeks to 2.0; a beep at 0.5 clamps to 0.0.
    early = _bundle("Early", {1: _stage(1, trim=Path("/e.mp4"), beep=0.5, duration=10.0)})
    late = _bundle("Late", {1: _stage(1, trim=Path("/l.mp4"), beep=3.0, duration=14.0)})

    plans = mp4_grid.build_stage_plans(
        [early, late], audio_label="Early", head_pad_seconds=1.0, tail_pad_seconds=0.5
    )

    tiles = {t.label: t for t in plans[0].tiles}
    assert tiles["Late"].seek_seconds == pytest.approx(2.0)
    assert tiles["Early"].seek_seconds == pytest.approx(0.0)
    # Duration spans head pad + the longest post-beep run + tail pad.
    # Late runs 14.0 - 3.0 = 11.0 after its beep; Early runs 9.5.
    assert plans[0].duration_seconds == pytest.approx(1.0 + 11.0 + 0.5)


def test_unknown_audio_label_is_rejected():
    a = _bundle("Anders", {1: _stage(1, trim=Path("/a1.mp4"), beep=2.0, duration=12.0)})
    with pytest.raises(ValueError, match="Nobody"):
        mp4_grid.build_stage_plans([a], audio_label="Nobody", head_pad_seconds=0.0, tail_pad_seconds=0.0)
