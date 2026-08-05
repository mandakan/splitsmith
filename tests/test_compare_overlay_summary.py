"""The frozen post-stage summary still: freeze extraction, blur-once, compose.

Freeze extraction goes through a fake runner -- no ffmpeg is ever shelled
out to here, per CLAUDE.md. Text presence/absence is asserted by
monkeypatching ``_draw_text_with_shadow`` to record what was drawn, rather
than by pixel-diffing rendered glyphs: it is exactly what
``overlay_summary`` calls to put ink on the canvas, so this is the same
seam the module already uses, not a new one invented for the test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from splitsmith.compare import overlay_summary as summ
from splitsmith.compare.mp4_grid import GridCanvas, GridStagePlan, GridTile
from splitsmith.compare.overlay_data import TileShot, TileStageData
from splitsmith.compare.overlay_sprites import SpriteGeometry, TilePlacement
from splitsmith.overlay_theme import load_theme
from splitsmith.ui.project import StageScorecard

THEME = load_theme("clean")
GEOMETRY = SpriteGeometry(canvas_width=640, canvas_height=360, rows=2, cols=2)
CANVAS = GridCanvas(width=640, height=360, frame_rate_num=30, frame_rate_den=1)


def _tile(label: str, row: int, col: int, *, trim: Path | None, seek: float = 0.0, lead_pad: float = 0.0):
    return GridTile(
        label=label,
        trim_path=trim,
        beep_offset_in_clip=0.0,
        seek_seconds=seek,
        lead_pad_seconds=lead_pad,
        row=row,
        col=col,
    )


def _plan(tiles, *, duration=5.0, stage_number=1):
    return GridStagePlan(
        stage_number=stage_number,
        stage_name="Stage",
        tiles=tuple(tiles),
        duration_seconds=duration,
        audio_label=tiles[0].label,
        rows=2,
        cols=2,
    )


def _placement(label, row, col, *, present=True):
    return TilePlacement(label=label, row=row, col=col, present=present)


class _Recorder:
    """A fake runner: records every argv, returns a scripted result."""

    def __init__(self, returncode: int = 0):
        self.calls: list[list[str]] = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.returncode, b"", b"")


def _solid_png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


# --- freeze extraction --------------------------------------------------


def test_freeze_extraction_asks_for_one_frame_at_the_last_action_moment(tmp_path):
    trim = tmp_path / "ann.mp4"
    tile = _tile("Ann", 0, 0, trim=trim, seek=2.0, lead_pad=0.0)
    plan = _plan([tile], duration=5.0)
    runner = _Recorder()

    freezes = summ.extract_freeze_frames(
        plan, canvas=CANVAS, work_dir=tmp_path / "work", ffmpeg_binary="ffmpeg", runner=runner
    )

    assert "Ann" in freezes
    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd
    seek_value = float(cmd[cmd.index("-ss") + 1])
    expected = 2.0 + 5.0 - 0.0 - (1.0 / CANVAS.fps)
    # ``:g`` formats to 6 significant digits, which can round the last
    # digit of a value like 6.9666... by a few micro-seconds -- nowhere
    # near a frame, so a generous absolute tolerance is still meaningful.
    assert seek_value == pytest.approx(expected, abs=1e-4)
    assert "-i" in cmd
    assert cmd[cmd.index("-i") + 1] == str(trim)
    assert "-frames:v" in cmd
    assert cmd[cmd.index("-frames:v") + 1] == "1"


def test_freeze_extraction_skips_filler_tiles(tmp_path):
    real = _tile("Ann", 0, 0, trim=tmp_path / "ann.mp4")
    filler = _tile("Bo", 0, 1, trim=None)
    plan = _plan([real, filler])
    runner = _Recorder()

    freezes = summ.extract_freeze_frames(
        plan, canvas=CANVAS, work_dir=tmp_path / "work", ffmpeg_binary="ffmpeg", runner=runner
    )

    assert set(freezes) == {"Ann"}
    assert len(runner.calls) == 1


def test_a_failed_extraction_degrades_to_a_black_cell(tmp_path):
    tile = _tile("Ann", 0, 0, trim=tmp_path / "ann.mp4")
    plan = _plan([tile])
    runner = _Recorder(returncode=1)

    freezes = summ.extract_freeze_frames(
        plan, canvas=CANVAS, work_dir=tmp_path / "work", ffmpeg_binary="ffmpeg", runner=runner
    )
    assert freezes == {}

    data = {"Ann": TileStageData(label="Ann", stage_number=1)}
    placements = [_placement("Ann", 0, 0)]
    image = summ.build_hold_still(placements, data, freezes, GEOMETRY, theme=THEME)

    assert image.mode == "RGB"
    corner = image.getpixel((GEOMETRY.cell_width - 2, GEOMETRY.cell_height - 2))
    assert corner == (0, 0, 0)


# --- blur-once ------------------------------------------------------------


def test_blur_is_applied_once_per_tile_not_per_frame(tmp_path, monkeypatch):
    calls = []
    original = summ._apply_blur

    def counting_blur(image, radius):
        calls.append(radius)
        return original(image, radius)

    monkeypatch.setattr(summ, "_apply_blur", counting_blur)

    ann_png = _solid_png(tmp_path / "ann.png", (GEOMETRY.cell_width, GEOMETRY.cell_height), (200, 10, 10))
    bo_png = _solid_png(tmp_path / "bo.png", (GEOMETRY.cell_width, GEOMETRY.cell_height), (10, 10, 200))
    placements = [_placement("Ann", 0, 0), _placement("Bo", 0, 1)]
    data = {
        "Ann": TileStageData(label="Ann", stage_number=1),
        "Bo": TileStageData(label="Bo", stage_number=1),
    }
    freezes = {"Ann": ann_png, "Bo": bo_png}

    summ.build_hold_still(placements, data, freezes, GEOMETRY, theme=THEME)

    assert len(calls) == 2


# --- composition ------------------------------------------------------------


def test_hold_still_is_canvas_sized_rgb():
    placements = [_placement("Ann", 0, 0)]
    data = {"Ann": TileStageData(label="Ann", stage_number=1)}
    image = summ.build_hold_still(placements, data, {}, GEOMETRY, theme=THEME)
    assert image.mode == "RGB"
    assert image.size == (GEOMETRY.canvas_width, GEOMETRY.canvas_height)


def test_each_cell_draws_over_its_own_freeze_frame(tmp_path):
    ann_png = _solid_png(tmp_path / "ann.png", (GEOMETRY.cell_width, GEOMETRY.cell_height), (200, 10, 10))
    bo_png = _solid_png(tmp_path / "bo.png", (GEOMETRY.cell_width, GEOMETRY.cell_height), (10, 10, 200))
    placements = [_placement("Ann", 0, 0), _placement("Bo", 0, 1)]
    data = {
        "Ann": TileStageData(label="Ann", stage_number=1),
        "Bo": TileStageData(label="Bo", stage_number=1),
    }
    freezes = {"Ann": ann_png, "Bo": bo_png}

    image = summ.build_hold_still(placements, data, freezes, GEOMETRY, theme=THEME, blur_radius=1, dim=0.45)

    # Sample far from any drawn text (bottom edge of each cell).
    ann_pixel = image.getpixel((10, GEOMETRY.cell_height - 5))
    bo_pixel = image.getpixel((GEOMETRY.cell_width + 10, GEOMETRY.cell_height - 5))
    assert ann_pixel[0] > ann_pixel[2]  # red-ish, not blue-ish
    assert bo_pixel[2] > bo_pixel[0]  # blue-ish, not red-ish


# --- text content: capture what _draw_text_with_shadow was asked to draw --


def _capture(monkeypatch):
    drawn: list[str] = []
    original = summ._draw_text_with_shadow

    def recorder(draw, canvas, xy, text, font, fill, **kwargs):
        drawn.append(text)
        return original(draw, canvas, xy, text, font, fill, **kwargs)

    monkeypatch.setattr(summ, "_draw_text_with_shadow", recorder)
    return drawn


def test_missing_scorecard_omits_the_scoring_lines(monkeypatch):
    drawn = _capture(monkeypatch)
    shots = (TileShot(time_from_beep=1.0, split=1.0), TileShot(time_from_beep=1.3, split=0.3))
    tile = TileStageData(label="Ann", stage_number=1, shots=shots, stage_time_seconds=12.34, scorecard=None)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    assert any("shots" in t for t in drawn)
    assert any(t.startswith("Time") for t in drawn)
    assert not any(t.startswith("HF") for t in drawn)
    assert not any(t.startswith("Stage") for t in drawn)
    assert not any(t.startswith("A") and t[1:2].isdigit() for t in drawn)
    assert "DQ" not in drawn


def test_stage_points_never_appears_and_stage_pct_does(monkeypatch):
    drawn = _capture(monkeypatch)
    scorecard = StageScorecard(stage_points=143.2, stage_pct=87.4)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    assert any("87.4" in t for t in drawn)
    assert not any("143.2" in t for t in drawn)


def test_none_hit_counts_are_omitted_not_zeroed(monkeypatch):
    drawn = _capture(monkeypatch)
    # charlies and misses are genuinely unread (None); no_shoots is a real
    # zero. The line must show the real zero and skip the unread fields --
    # not print a fabricated 0 for a count nobody read.
    scorecard = StageScorecard(alphas=7, charlies=None, deltas=1, misses=None, no_shoots=0)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    hit_lines = [t for t in drawn if t.startswith("A") and "of" not in t and t != "Ann"]
    assert hit_lines == ["A7 D1 NS0"]


def test_manual_time_is_marked_as_manual(monkeypatch):
    drawn = _capture(monkeypatch)
    tile = TileStageData(label="Ann", stage_number=1, stage_time_seconds=12.34, stage_time_is_manual=True)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    assert any("12.34" in t and "manual" in t for t in drawn)


def test_dq_replaces_the_scoring_lines(monkeypatch):
    drawn = _capture(monkeypatch)
    scorecard = StageScorecard(hit_factor=5.12, stage_pct=80.0, alphas=7, dq=True)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    assert "DQ" in drawn
    assert not any(t.startswith("HF") for t in drawn)
    assert not any(t.startswith("Stage") for t in drawn)
    assert not any("A7" in t for t in drawn)


def test_splits_exclude_the_draw_from_best_average_worst(monkeypatch):
    drawn = _capture(monkeypatch)
    shots = (
        TileShot(time_from_beep=1.5, split=1.5),  # the draw
        TileShot(time_from_beep=1.7, split=0.2),
        TileShot(time_from_beep=2.0, split=0.3),
        TileShot(time_from_beep=2.4, split=0.4),
    )
    tile = TileStageData(label="Ann", stage_number=1, shots=shots)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    stats_lines = [t for t in drawn if t.startswith("Best")]
    assert len(stats_lines) == 1
    assert "0.20" in stats_lines[0]
    assert "0.30" in stats_lines[0]
    assert "0.40" in stats_lines[0]
    assert "1.50" not in stats_lines[0]
    assert any(t == "Draw 1.50" for t in drawn)


def test_a_tile_with_no_audit_shows_only_its_label(monkeypatch):
    drawn = _capture(monkeypatch)
    tile = TileStageData(label="Ann", stage_number=1)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    assert drawn == ["Ann"]


def test_a_filler_tile_is_black_with_no_text(monkeypatch):
    drawn = _capture(monkeypatch)
    placements = [_placement("Bo", 0, 1, present=False)]
    image = summ.build_hold_still(placements, {}, {}, GEOMETRY, theme=THEME)

    assert drawn == []
    extrema = image.getextrema()
    assert extrema == ((0, 0), (0, 0), (0, 0))


# --- ranking (Task 8's addition -- not in the stale brief) ----------------


def test_placing_drawn_for_ranked_tiles(monkeypatch):
    drawn = _capture(monkeypatch)
    placements = [_placement("Ann", 0, 0), _placement("Bo", 0, 1)]
    data = {
        "Ann": TileStageData(label="Ann", stage_number=1, scorecard=StageScorecard(stage_pct=95.0)),
        "Bo": TileStageData(label="Bo", stage_number=1, scorecard=StageScorecard(stage_pct=60.0)),
    }
    summ.build_hold_still(placements, data, {}, GEOMETRY, theme=THEME)

    assert "#1 of 2" in drawn
    assert "#2 of 2" in drawn


def test_ranking_follows_stage_pct_even_when_stage_points_disagree(monkeypatch):
    drawn = _capture(monkeypatch)
    placements = [_placement("Ann", 0, 0), _placement("Bo", 0, 1)]
    # Ann has more raw points but the lower stage_pct; Bo has fewer points
    # but the higher stage_pct. If ranking ever sorted by stage_points, Ann
    # would come out #1 -- it must not. Both scorecards carry a real
    # (non-None) stage_points so a mutated sort key can actually compare
    # them instead of merely crashing on ``-None``.
    data = {
        "Ann": TileStageData(
            label="Ann", stage_number=1, scorecard=StageScorecard(stage_pct=60.0, stage_points=200.0)
        ),
        "Bo": TileStageData(
            label="Bo", stage_number=1, scorecard=StageScorecard(stage_pct=90.0, stage_points=100.0)
        ),
    }
    summ.build_hold_still(placements, data, {}, GEOMETRY, theme=THEME)

    assert "#1 of 2" in drawn
    ann_index = drawn.index("Ann")
    bo_index = drawn.index("Bo")
    ann_placing = drawn[ann_index + 1]
    bo_placing = drawn[bo_index + 1]
    assert bo_placing == "#1 of 2", f"Bo has the higher stage_pct and must rank #1, got {bo_placing!r}"
    assert ann_placing == "#2 of 2", f"Ann has the lower stage_pct and must rank #2, got {ann_placing!r}"


def test_dq_missing_scorecard_and_filler_get_no_placing(monkeypatch):
    drawn = _capture(monkeypatch)
    placements = [
        _placement("Ann", 0, 0),
        _placement("Bo", 0, 1),
        _placement("Cy", 1, 0),
        _placement("Dee", 1, 1, present=False),
    ]
    data = {
        "Ann": TileStageData(label="Ann", stage_number=1, scorecard=StageScorecard(stage_pct=99.0, dq=True)),
        "Bo": TileStageData(label="Bo", stage_number=1, scorecard=None),
        "Cy": TileStageData(label="Cy", stage_number=1, scorecard=StageScorecard(stage_pct=70.0)),
    }
    summ.build_hold_still(placements, data, {}, GEOMETRY, theme=THEME)

    placing_lines = [t for t in drawn if t.startswith("#")]
    # The only rankable tile is Cy (DQ'd Ann and scorecard-less Bo are
    # excluded, filler Dee draws nothing at all) -- alone in the pool.
    assert placing_lines == ["#1 of 1"]


def test_build_hold_still_rejects_whole_match_keyed_data():
    placements = [_placement("Ann", 0, 0)]
    data = {("Ann", 1): TileStageData(label="Ann", stage_number=1)}
    with pytest.raises(ValueError, match="keyed by tile label"):
        summ.build_hold_still(placements, data, {}, GEOMETRY, theme=THEME)  # type: ignore[arg-type]


# --- write_hold_still: the extraction + composition + save wrapper --------


def test_write_hold_still_saves_a_png(tmp_path):
    tile = _tile("Ann", 0, 0, trim=tmp_path / "ann.mp4")
    plan = _plan([tile])
    runner = _Recorder(returncode=1)  # extraction fails; still must not crash
    data = {"Ann": TileStageData(label="Ann", stage_number=1)}

    out_path = summ.write_hold_still(
        plan,
        data,
        GEOMETRY,
        theme=THEME,
        canvas=CANVAS,
        work_dir=tmp_path / "work",
        ffmpeg_binary="ffmpeg",
        runner=runner,
    )

    assert out_path.exists()
    with Image.open(out_path) as image:
        assert image.size == (GEOMETRY.canvas_width, GEOMETRY.canvas_height)
