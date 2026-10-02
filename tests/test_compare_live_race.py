"""The free square's live race (2026-10-02): its rows over the run, what
they say, and how a render with only the race reaches ffmpeg."""

from __future__ import annotations

from splitsmith.compare import mp4_grid
from splitsmith.compare.free_cell import race_groups
from splitsmith.compare.overlay_sprites import (
    RaceRow,
    TilePlacement,
    build_overlay_states,
    race_rows_at,
)
from splitsmith.config import StageRounds
from splitsmith.overlay_layout import ColorToken
from splitsmith.stage_summary_data import TileShot, TileStageData
from tests.conftest import fake_ffmpeg_probe
from tests.test_compare_mp4_grid_overlay import CANVAS, _recorder, _shooters, _StubRasterizer

PLACEMENTS = (
    TilePlacement(label="Anna", row=0, col=0, present=True),
    TilePlacement(label="Bo", row=0, col=1, present=True),
    TilePlacement(label="Cy", row=1, col=0, present=False),
)


def _tile(label: str, *times: float, expected: int | None = 3) -> TileStageData:
    shots, previous = [], 0.0
    for t in times:
        shots.append(TileShot(time_from_beep=t, split=round(t - previous, 3)))
        previous = t
    rounds = StageRounds(expected=expected) if expected else None
    return TileStageData(label=label, stage_number=2, shots=tuple(shots), stage_rounds=rounds)


DATA = {"Anna": _tile("Anna", 1.0, 1.5, 2.0), "Bo": _tile("Bo", 1.2, 1.8, 2.6)}


def test_rows_count_shots_then_hold_the_finish_and_mark_the_first_finisher() -> None:
    before = race_rows_at(PLACEMENTS, DATA, 0.0)
    assert [(r.shots_fired, r.finish_seconds) for r in before] == [(0, None), (0, None), (0, None)]

    mid = race_rows_at(PLACEMENTS, DATA, 1.6)
    assert [r.shots_fired for r in mid[:2]] == [2, 1]

    anna_done = race_rows_at(PLACEMENTS, DATA, 2.0)
    assert anna_done[0].finish_seconds == 2.0 and anna_done[0].first
    assert anna_done[1].finish_seconds is None and not anna_done[1].first

    end = race_rows_at(PLACEMENTS, DATA, 9.0)
    assert [(r.label, r.finish_seconds, r.first) for r in end] == [
        ("Anna", 2.0, True),
        ("Bo", 2.6, False),
        ("Cy", None, False),
    ]


def test_a_race_of_one_marks_no_winner() -> None:
    rows = race_rows_at(PLACEMENTS, {"Anna": DATA["Anna"]}, 9.0)
    assert rows[0].finish_seconds == 2.0
    assert not any(r.first for r in rows)


def test_rows_say_count_against_rounds_then_the_held_time() -> None:
    rows = (
        RaceRow("Anna Berg", True, 3, 3, 2.0, first=True),
        RaceRow("Bo Ek", True, 2, 16, None),
        RaceRow("Cy Lund", True, 4, None, None),
        RaceRow("Dee Nyström", True, 0, 16, None),
        RaceRow("Eve Holm", False, 0, None, None),
    )
    groups = race_groups(rows, stage_number=2)
    assert [e.text for e in groups[0].elements] == ["Stage 02 · Live"]
    body = [[e.text for e in g.elements] for g in groups[1:]]
    assert body == [["Anna", "2.00 s"], ["Bo", "2 / 16"], ["Cy", "4"], ["Dee", "-"], ["Eve", "-"]]
    assert groups[1].elements[1].color == ColorToken.SPLIT_GOOD
    assert groups[2].elements[1].color == ColorToken.INK


def test_states_without_tiles_carry_only_the_race_and_step_on_every_shot() -> None:
    states = build_overlay_states(
        PLACEMENTS,
        DATA,
        head_pad_seconds=1.0,
        duration_seconds=5.0,
        tiles=False,
        race_at=(1, 1),
        stage_number=2,
    )
    assert all(s.panels == () for s in states)
    assert {(s.race.row, s.race.col) for s in states} == {(1, 1)}
    # The opening state plus one per shot.
    assert len(states) == 1 + 6
    assert states[-1].race.rows[1].finish_seconds == 2.6


def test_a_race_only_render_draws_the_race_without_tile_overlays_or_clocks(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mp4_grid, "ChromiumRasterizer", _StubRasterizer)
    raster = _StubRasterizer()
    calls, runner = _recorder()
    work = tmp_path / "work"
    shooters = _shooters(tmp_path, shots={"Anders": [0.9, 1.4], "Bea": [1.0, 1.7], "Mathias": [1.1, 2.4]})
    mp4_grid.render_grid_mp4(
        shooters,
        audio_label="Anders",
        output_path=tmp_path / "grid.mp4",
        canvas=CANVAS,
        runner=runner,
        work_dir=work,
        ffmpeg_binary="ffmpeg",
        free_cell="race",
        rasterizer=raster,
        probe_runner=fake_ffmpeg_probe(),
    )
    stage = " ".join(calls[0])
    # The sprites ride in as the concat input and the surface still sits
    # in the free cell, but no tile gets a clock.
    assert "sprites-stage1.txt" in stage
    assert "free-stage1.png" in stage
    assert "drawtext" not in stage
    pages = [html for html, _w, _h in raster.calls]
    assert any("Stage 01 · Live" in html for html in pages)
    assert any("1.40 s" in html for html in pages)
    # With the overlay off no page carries a tile's shot counter: the
    # race's own "2" sits in a grid row, never alone at a corner anchor.
    assert not any("anchor anchor-top-left" in html for html in pages)
