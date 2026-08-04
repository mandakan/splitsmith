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


def test_a_clamped_tile_carries_the_head_pad_shortfall():
    # Early's beep is 0.5s into its clip but head_pad is 1.0s, so 0.5s
    # of the pad has nowhere to come from and must be synthesised.
    # Without this the tile's beep lands 0.5s early and the grid is
    # desynced -- the one thing the grid exists to prevent.
    early = _bundle("Early", {1: _stage(1, trim=Path("/e.mp4"), beep=0.5, duration=10.0)})
    late = _bundle("Late", {1: _stage(1, trim=Path("/l.mp4"), beep=3.0, duration=14.0)})
    # Zoe shoots stage 2 only, so stage 1 gives her a filler tile. The
    # invariant below is about clips, and a filler has none -- keeping one
    # in the fixture is what keeps the guard honest.
    zoe = _bundle("Zoe", {2: _stage(2, trim=Path("/z2.mp4"), beep=2.0, duration=8.0)})

    plans = mp4_grid.build_stage_plans(
        [early, late, zoe], audio_label="Early", head_pad_seconds=1.0, tail_pad_seconds=0.5
    )

    tiles = {t.label: t for t in plans[0].tiles}
    assert tiles["Early"].lead_pad_seconds == pytest.approx(0.5)
    assert tiles["Late"].lead_pad_seconds == pytest.approx(0.0)
    assert tiles["Zoe"].trim_path is None
    # The invariant the lead pad exists to hold: every tile that has a clip
    # puts its beep at exactly head_pad on the output timeline, clamped or
    # not. A filler tile has no clip to place and sits at 0.0.
    for tile in plans[0].tiles:
        if tile.trim_path is None:
            continue
        landed = tile.lead_pad_seconds + (tile.beep_offset_in_clip - tile.seek_seconds)
        assert landed == pytest.approx(1.0)


def test_filler_tile_has_no_lead_pad():
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

    anders = next(t for t in plans[1].tiles if t.label == "Anders")
    # A filler tile is black for the whole stage; there is no clip to shift.
    assert anders.trim_path is None
    assert anders.lead_pad_seconds == pytest.approx(0.0)


def _roster(labels: list[str], stage_numbers: dict[str, list[int]]) -> list[CompareShooterBundle]:
    """One bundle per label, each holding the stages named for it."""
    return [
        _bundle(
            label,
            {
                n: _stage(n, trim=Path(f"/{label}{n}.mp4"), beep=2.0, duration=12.0)
                for n in stage_numbers[label]
            },
        )
        for label in labels
    ]


def test_four_shooters_fill_a_2x2_grid_row_major():
    # Placement is what Task 2 turns into the xstack layout string. If every
    # tile reports (0, 0) the whole grid renders stacked in the top-left
    # quadrant with three quarters of the canvas black -- so pin the cells,
    # not just the ordering.
    labels = ["Anna", "Bo", "Cilla", "David"]
    shooters = _roster(labels, {label: [1] for label in labels})

    plans = mp4_grid.build_stage_plans(
        shooters, audio_label="Anna", head_pad_seconds=1.0, tail_pad_seconds=0.0
    )

    plan = plans[0]
    assert (plan.rows, plan.cols) == (2, 2)
    assert [t.label for t in plan.tiles] == labels
    assert [(t.row, t.col) for t in plan.tiles] == [(0, 0), (0, 1), (1, 0), (1, 1)]


def test_placement_survives_shooters_missing_a_stage():
    # The load-bearing rule for the concat stitch: a label holds the same
    # cell in every stage, and a missing trim leaves a filler in that cell
    # rather than letting the shooters behind it shuffle forward.
    labels = ["Anna", "Bo", "Cilla", "David"]
    shooters = _roster(labels, {"Anna": [1], "Bo": [1], "Cilla": [1, 2], "David": [1, 2]})

    plans = mp4_grid.build_stage_plans(
        shooters, audio_label="Cilla", head_pad_seconds=1.0, tail_pad_seconds=0.0
    )

    stage1, stage2 = plans
    expected_cells = [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert [(t.row, t.col) for t in stage1.tiles] == expected_cells
    assert [(t.row, t.col) for t in stage2.tiles] == expected_cells
    assert [t.label for t in stage2.tiles] == labels
    # Anna and Bo keep the top row as fillers instead of vanishing.
    assert [t.trim_path is None for t in stage2.tiles] == [True, True, False, False]


def test_two_up_grids_are_not_transposed():
    # rows != cols here, so swapping the divmod divisor moves a tile from
    # (0, 1) to (1, 0) -- invisible on the square rosters above.
    shooters = _roster(["Anna", "Bo"], {"Anna": [1], "Bo": [1]})

    horizontal = mp4_grid.build_stage_plans(
        shooters, audio_label="Anna", head_pad_seconds=0.0, tail_pad_seconds=0.0
    )[0]
    assert (horizontal.rows, horizontal.cols) == (1, 2)
    assert [(t.row, t.col) for t in horizontal.tiles] == [(0, 0), (0, 1)]

    vertical = mp4_grid.build_stage_plans(
        shooters,
        audio_label="Anna",
        head_pad_seconds=0.0,
        tail_pad_seconds=0.0,
        layout_2up="vertical",
    )[0]
    assert (vertical.rows, vertical.cols) == (2, 1)
    assert [(t.row, t.col) for t in vertical.tiles] == [(0, 0), (1, 0)]


def test_unknown_audio_label_is_rejected():
    a = _bundle("Anders", {1: _stage(1, trim=Path("/a1.mp4"), beep=2.0, duration=12.0)})
    with pytest.raises(ValueError, match="Nobody"):
        mp4_grid.build_stage_plans([a], audio_label="Nobody", head_pad_seconds=0.0, tail_pad_seconds=0.0)
