"""The grid's free square (2026-10-02): what it says, and how it reaches
the stage command."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from splitsmith.compare import mp4_grid
from splitsmith.compare.free_cell import FreeCellContext, free_cell_groups
from splitsmith.overlay_layout import ColorToken
from splitsmith.stage_summary_data import TileShot, TileStageData


def _tile(label: str, *times: float) -> TileStageData:
    shots = []
    previous = 0.0
    for t in times:
        shots.append(TileShot(time_from_beep=t, split=round(t - previous, 3)))
        previous = t
    return TileStageData(label=label, stage_number=4, shots=tuple(shots))


CTX = FreeCellContext(
    stage_number=4,
    stage_name="B6 Front",
    match_name="Höstfinalen XI",
    match_date="2026-09-26",
    shooters=("Anton", "Martin", "Mathias"),
    expected_rounds=16,
    paper_targets=8,
    steel_targets=3,
    tiles={
        "Anton": _tile("Anton", 1.6, 1.9, 2.2),
        "Martin": _tile("Martin", 1.4, 1.65, 1.9),
        "Mathias": TileStageData(label="Mathias", stage_number=4),
    },
)


def _texts(groups) -> list[list[str]]:
    return [[e.text for e in g.elements] for g in groups]


def test_stage_card_says_the_stage_its_rounds_and_targets() -> None:
    assert _texts(free_cell_groups("stage", CTX)) == [
        ["Stage 04"],
        ["B6 Front"],
        ["16 rounds"],
        ["8 paper · 3 steel"],
    ]


def test_match_card_says_the_match_the_day_and_the_squad() -> None:
    assert _texts(free_cell_groups("match", CTX)) == [
        ["Höstfinalen XI"],
        ["2026-09-26"],
        ["Anton · Martin · Mathias"],
    ]


def test_splits_mark_the_best_of_each_column_and_dash_what_is_missing() -> None:
    groups = free_cell_groups("splits", CTX)
    assert _texts(groups)[0] == ["Stage 04 · Splits"]
    anton, martin, mathias = groups[1:]
    # Martin has the quicker draw and the quicker splits: marked.
    assert [e.text for e in martin.elements] == ["Martin", "1.40", "0.25", "0.25"]
    assert [e.color for e in martin.elements[1:]] == [ColorToken.SPLIT_GOOD] * 3
    assert [e.color for e in anton.elements[1:]] == [ColorToken.INK] * 3
    # A shooter with no audited shots reads as dashes, never zeros.
    assert [e.text for e in mathias.elements] == ["Mathias", "-", "-", "-"]
    # Captions only on the first row.
    assert [e.caption for e in anton.elements] == ["Shooter", "Draw", "Avg", "Best"]
    assert all(e.caption is None for e in martin.elements)


def test_splits_with_no_audited_shots_say_so() -> None:
    empty = replace(CTX, tiles={})
    assert _texts(free_cell_groups("splits", empty))[1] == ["No audited shots on this stage"]


def test_splits_name_shooters_by_a_short_first_name() -> None:
    from splitsmith.compare.free_cell import short_names

    assert short_names(["Mathias Axell", "Martin Engström", "Anna Jonsson"]) == {
        "Mathias Axell": "Mathias",
        "Martin Engström": "Martin",
        "Anna Jonsson": "Anna",
    }
    assert short_names(["Anna Berg", "Anna Ek", "Bo"]) == {
        "Anna Berg": "Anna B.",
        "Anna Ek": "Anna E.",
        "Bo": "Bo",
    }
    assert short_names(["Christoffer Lund"]) == {"Christoffer Lund": "Christo."}


def test_blank_says_nothing() -> None:
    assert free_cell_groups("blank", CTX) == ()


def _three_up_plan() -> mp4_grid.GridStagePlan:
    tiles = tuple(
        mp4_grid.GridTile(
            label=label,
            trim_path=Path(f"/trims/{label}.mp4"),
            beep_offset_in_clip=2.0,
            seek_seconds=1.0,
            lead_pad_seconds=0.0,
            source_duration_seconds=8.0,
            row=row,
            col=col,
        )
        for label, (row, col) in zip(("Anton", "Martin", "Mathias"), ((0, 0), (0, 1), (1, 0)), strict=True)
    )
    return mp4_grid.GridStagePlan(
        stage_number=4,
        stage_name="B6 Front",
        tiles=tiles,
        duration_seconds=10.0,
        audio_label="Anton",
        rows=2,
        cols=2,
    )


def test_the_free_square_takes_the_black_cells_input_and_nothing_renumbers() -> None:
    plan = _three_up_plan()
    canvas = mp4_grid.GridCanvas()
    black = mp4_grid.build_stage_command(plan, canvas=canvas, output_path=Path("/out/s4.mov"))
    shown = mp4_grid.build_stage_command(
        plan, canvas=canvas, output_path=Path("/out/s4.mov"), free_cell_still=Path("/w/free-stage4.png")
    )
    black_inputs = [black[i + 1] for i, a in enumerate(black) if a == "-i"]
    shown_inputs = [shown[i + 1] for i, a in enumerate(shown) if a == "-i"]
    assert len(black_inputs) == len(shown_inputs)
    position = black_inputs.index(next(i for i in black_inputs if i.startswith("color=c=black")))
    assert shown_inputs[position] == "/w/free-stage4.png"
    assert [x for k, x in enumerate(shown_inputs) if k != position] == [
        x for k, x in enumerate(black_inputs) if k != position
    ]
    # Same filter graph and maps either way: the image runs the black cell's chain.
    assert shown[shown.index("-filter_complex") + 1] == black[black.index("-filter_complex") + 1]
