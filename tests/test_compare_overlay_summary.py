"""The frozen post-stage summary still: freeze extraction, blur-once, compose.

Freeze extraction goes through a fake runner -- no ffmpeg is ever shelled
out to here, per CLAUDE.md. Text presence/absence is asserted by
monkeypatching ``_draw_text_with_shadow`` and ``_plate`` to record what
was drawn, rather than by pixel-diffing rendered glyphs: those are
exactly what ``overlay_summary`` calls to put ink on the canvas -- plain
text through the former, ``PLATE``-emphasis text through the latter --
so this is the same pair of seams the module already uses, not a new one
invented for the test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from splitsmith.compare import overlay_summary as summ
from splitsmith.compare.mp4_grid import GridStagePlan, GridTile
from splitsmith.compare.overlay_data import TileShot, TileStageData
from splitsmith.compare.overlay_sprites import SpriteGeometry, TilePlacement
from splitsmith.overlay_layout import Anchor, CellScale, Element, Emphasis, Flow, Group, Role
from splitsmith.overlay_theme import load_theme
from splitsmith.ui.project import StageScorecard

THEME = load_theme("clean")
GEOMETRY = SpriteGeometry(canvas_width=640, canvas_height=360, rows=2, cols=2)


def _tile(
    label: str,
    row: int,
    col: int,
    *,
    trim: Path | None,
    seek: float = 0.0,
    lead_pad: float = 0.0,
    source_duration: float = 8.0,
):
    return GridTile(
        label=label,
        trim_path=trim,
        beep_offset_in_clip=0.0,
        seek_seconds=seek,
        lead_pad_seconds=lead_pad,
        source_duration_seconds=0.0 if trim is None else source_duration,
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
    """A fake runner: records every argv, returns a scripted result.

    ``writes`` says what the fake ffmpeg leaves on disk, because that is
    the thing the returncode does not tell you. Real ffmpeg asked for a
    frame past the end of a clip exits **0** and writes nothing, which is
    the ``"nothing"`` mode; ``"empty"`` is the same lie with a zero-byte
    file created. ``"frame"`` is the honest success.
    """

    def __init__(self, returncode: int = 0, writes: str = "frame"):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.writes = writes

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        out = Path(cmd[-1])
        if self.returncode == 0:
            if self.writes == "frame":
                Image.new("RGB", (16, 9), (7, 9, 11)).save(out)
            elif self.writes == "empty":
                out.write_bytes(b"")
        return subprocess.CompletedProcess(cmd, self.returncode, b"", b"")


def _solid_png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


# --- freeze extraction --------------------------------------------------


def _seek_of(cmd: list[str]) -> float:
    assert "-ss" in cmd, cmd
    return float(cmd[cmd.index("-ss") + 1])


def test_the_freeze_seek_lands_inside_the_tiles_own_footage(tmp_path):
    """The seek must be a time this clip actually has a frame at.

    Production geometry, which is what makes this the test the shipped
    formula could not pass: the stage runs ``head_pad`` + the longest
    tile's post-beep span + ``tail_pad``, so ``plan.duration_seconds``
    (7.5 here) exceeds what this tile's own 6.0s clip can supply from its
    2.0s seek. Any target derived from the action's length is therefore
    past this clip's end -- ffmpeg returns no frame there and the cell
    renders black.

    Asserted against the clip's own bounds rather than against a repeat
    of the implementation's arithmetic. The previous version of this test
    computed ``2.0 + 5.0 - 0.0 - 1/fps`` and compared it to the same
    expression the module evaluated, so it held whatever the module did.
    """
    trim = tmp_path / "ann.mp4"
    tile = _tile("Ann", 0, 0, trim=trim, seek=2.0, lead_pad=0.0, source_duration=6.0)
    plan = _plan([tile], duration=7.5)
    runner = _Recorder()

    freezes = summ.extract_freeze_frames(
        plan, work_dir=tmp_path / "work", ffmpeg_binary="ffmpeg", runner=runner
    )

    assert "Ann" in freezes
    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert cmd[0] == "ffmpeg"
    seek = _seek_of(cmd)
    assert 0.0 <= seek < 6.0, f"seek {seek} is not inside the tile's own 6.0s clip"
    # And near its end, not merely inside it: a target that drifted to the
    # front of the clip would freeze on footage from before the shooter
    # started, which still looks like a picture.
    assert seek >= 6.0 - 1.0, f"seek {seek} is not near the end of the tile's own 6.0s clip"
    # Nothing about the action's length may reach the seek.
    assert seek < plan.duration_seconds - 1.0
    assert cmd[cmd.index("-i") + 1] == str(trim)
    assert cmd[cmd.index("-update") + 1] == "1"


def test_each_tile_freezes_on_its_own_clips_end_not_the_longest_ones(tmp_path):
    """Two tiles of different lengths get two different seeks.

    A stage's tiles rarely run the same length: the action ends when the
    *longest* post-beep span does, and every shorter tile has been black
    for a while by then. So "the end of the footage" is a per-tile fact,
    and a fix that clamped one shared action-derived target into range
    would give both tiles the same seek and freeze the short one on black.
    """
    plan = _plan(
        [
            _tile("Ann", 0, 0, trim=tmp_path / "ann.mp4", seek=2.0, source_duration=6.0),
            _tile("Bo", 0, 1, trim=tmp_path / "bo.mp4", seek=2.0, source_duration=3.25),
        ],
        duration=7.5,
    )
    runner = _Recorder()

    summ.extract_freeze_frames(plan, work_dir=tmp_path / "work", ffmpeg_binary="ffmpeg", runner=runner)

    ann_seek, bo_seek = (_seek_of(cmd) for cmd in runner.calls)
    assert 0.0 <= bo_seek < 3.25, f"the short tile's seek {bo_seek} is past its own 3.25s clip"
    assert bo_seek >= 3.25 - 1.0
    assert ann_seek > bo_seek, (
        f"both tiles were asked for the same moment ({ann_seek} / {bo_seek}); the seek is not "
        "following each tile's own footage"
    )


def test_freeze_extraction_skips_filler_tiles(tmp_path):
    real = _tile("Ann", 0, 0, trim=tmp_path / "ann.mp4")
    filler = _tile("Bo", 0, 1, trim=None)
    plan = _plan([real, filler])
    runner = _Recorder()

    freezes = summ.extract_freeze_frames(
        plan, work_dir=tmp_path / "work", ffmpeg_binary="ffmpeg", runner=runner
    )

    assert set(freezes) == {"Ann"}
    assert len(runner.calls) == 1


def test_an_exit_zero_that_wrote_no_frame_is_not_a_success(tmp_path, caplog):
    """rc=0 with no output file is the shipped failure, exactly.

    ``ffmpeg -ss <past EOF> -i clip -frames:v 1 out.png`` exits 0, reports
    ``frame= 0`` and writes nothing. A returncode-only check returns a
    path to that missing file and calls it a freeze frame, so the fault
    reappears two layers away as an unexplained black cell.
    """
    tile = _tile("Ann", 0, 0, trim=tmp_path / "ann.mp4")
    plan = _plan([tile])
    runner = _Recorder(returncode=0, writes="nothing")

    with caplog.at_level("WARNING"):
        freezes = summ.extract_freeze_frames(
            plan, work_dir=tmp_path / "work", ffmpeg_binary="ffmpeg", runner=runner
        )

    assert freezes == {}
    assert "wrote no freeze frame" in caplog.text
    assert "Ann" in caplog.text


def test_a_zero_byte_freeze_frame_is_not_a_success(tmp_path):
    tile = _tile("Ann", 0, 0, trim=tmp_path / "ann.mp4")
    plan = _plan([tile])
    runner = _Recorder(returncode=0, writes="empty")

    freezes = summ.extract_freeze_frames(
        plan, work_dir=tmp_path / "work", ffmpeg_binary="ffmpeg", runner=runner
    )
    assert freezes == {}


def test_a_stale_png_in_a_reused_work_dir_is_not_mistaken_for_this_runs_frame(tmp_path):
    """The work dir survives between renders when the caller names one.

    Without clearing the target first, an extraction that wrote nothing
    would find the *previous* render's PNG sitting there and report
    success -- the same lie the existence check exists to catch, wearing
    the last render's picture.
    """
    work = tmp_path / "work"
    work.mkdir()
    stale = work / "freeze-stage1-r0c0.png"
    _solid_png(stale, (16, 9), (200, 10, 10))
    tile = _tile("Ann", 0, 0, trim=tmp_path / "ann.mp4")
    plan = _plan([tile])

    freezes = summ.extract_freeze_frames(
        plan,
        work_dir=work,
        ffmpeg_binary="ffmpeg",
        runner=_Recorder(returncode=0, writes="nothing"),
    )

    assert freezes == {}
    assert not stale.exists()


def test_a_failed_extraction_degrades_to_a_black_cell(tmp_path):
    tile = _tile("Ann", 0, 0, trim=tmp_path / "ann.mp4")
    plan = _plan([tile])
    runner = _Recorder(returncode=1)

    freezes = summ.extract_freeze_frames(
        plan, work_dir=tmp_path / "work", ffmpeg_binary="ffmpeg", runner=runner
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


# --- cell composition: declared groups -------------------------------------


def _groups_by_anchor(groups):
    out: dict[Anchor, list] = {}
    for group in groups:
        out.setdefault(group.anchor, []).append(group)
    return out


def test_the_name_and_the_placing_share_the_top_left():
    tile = _full_stat_tile("Ann")
    groups = summ._cell_groups(tile, summ.StagePlacing(rank=2, total_ranked=5), "Ann")
    top_left = _groups_by_anchor(groups)[Anchor.TOP_LEFT][0]
    assert [e.text for e in top_left.elements] == ["Ann", "#2"]
    assert top_left.elements[0].role is Role.IDENTITY
    assert top_left.elements[1].role is Role.VERDICT
    assert top_left.elements[1].emphasis is Emphasis.PLATE


def test_the_band_carries_three_captioned_headlines():
    scorecard = StageScorecard(hit_factor=12.0, stage_pct=100.0)
    tile = TileStageData(label="Ann", stage_number=1, stage_time_seconds=4.5, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    band = _groups_by_anchor(groups)[Anchor.BOTTOM_LEFT][0]
    assert [e.caption for e in band.elements] == ["TIME", "HF", "STAGE"]
    assert [e.text for e in band.elements] == ["4.50", "12.00", "100.0%"]
    assert all(e.role is Role.HEADLINE for e in band.elements)


def test_a_clean_run_states_its_zeros_without_a_plate():
    """Presence is a fact; emphasis is a judgement. Drawing an accent
    plate on every clean cell in the grid would make the plate mean
    nothing when a real penalty turns up."""
    scorecard = StageScorecard(alphas=10, misses=0, no_shoots=0, procedurals=0)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    faults = [e for g in groups for e in g.elements if e.text == "M0 NS0 P0"]
    assert len(faults) == 1
    assert faults[0].emphasis is Emphasis.MUTED


def test_a_penalised_run_lights_the_plate():
    scorecard = StageScorecard(alphas=10, misses=1, no_shoots=0, procedurals=2)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    faults = [e for g in groups for e in g.elements if e.text == "M1 NS0 P2"]
    assert len(faults) == 1
    assert faults[0].emphasis is Emphasis.PLATE


def test_split_statistics_take_the_clocks_old_corner():
    """Nothing jumps across the action-to-hold cut that does not have to.
    The running clock lived at top-right; the figures that replace it
    stay there."""
    tile = _full_stat_tile("Ann")
    groups = summ._cell_groups(tile, None, "Ann")
    top_right = _groups_by_anchor(groups)[Anchor.TOP_RIGHT][0]
    texts = [e.text for e in top_right.elements]
    assert any("Best" in t for t in texts)
    assert any(t.startswith("Draw") for t in texts)
    assert all(e.emphasis is Emphasis.MUTED for e in top_right.elements)


def test_a_dq_takes_the_placings_slot_and_suppresses_the_scoring():
    scorecard = StageScorecard(hit_factor=5.12, stage_pct=80.0, alphas=7, dq=True)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    texts = [e.text for g in groups for e in g.elements]
    assert "DQ" in texts
    assert not any("5.12" in t for t in texts)
    assert not any("80.0" in t for t in texts)


def test_a_tile_with_nothing_declares_only_its_label():
    """The control cell. A tile with no audit and no scorecard renders
    its name and nothing else -- which is what the pixel checks measure
    "is the hold blurred" against."""
    groups = summ._cell_groups(None, None, "Ann")
    assert [e.text for g in groups for e in g.elements] == ["Ann"]


def test_the_band_is_declared_before_the_faults_row():
    """Groups sharing an anchor stack away from its edge in declaration
    order, and the band sits on the cell's bottom edge."""
    tile = _full_stat_tile("Ann")
    groups = summ._cell_groups(tile, None, "Ann")
    bottom = _groups_by_anchor(groups)[Anchor.BOTTOM_LEFT]
    assert bottom[0].elements[0].role is Role.HEADLINE


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


# --- text content: capture what was drawn to the canvas -------------------


def _capture(monkeypatch):
    """Record every string put on the canvas, plain or plated.

    A ``PLATE``-emphasis element (a placing, a DQ, a lit faults line)
    draws through ``_plate`` -- ink on a filled rectangle -- rather than
    ``_draw_text_with_shadow``'s stroke, so both are recorded here. It is
    still the same seam the module uses to put ink down, just two calls
    instead of one now that a second rendering path exists.
    """
    drawn: list[str] = []
    original_shadow = summ._draw_text_with_shadow
    original_plate = summ._plate

    def shadow_recorder(draw, canvas, xy, text, font, fill, **kwargs):
        drawn.append(text)
        return original_shadow(draw, canvas, xy, text, font, fill, **kwargs)

    def plate_recorder(canvas, xy, text, font, *, theme, size):
        drawn.append(text)
        return original_plate(canvas, xy, text, font, theme=theme, size=size)

    monkeypatch.setattr(summ, "_draw_text_with_shadow", shadow_recorder)
    monkeypatch.setattr(summ, "_plate", plate_recorder)
    return drawn


def test_missing_scorecard_omits_the_scoring_lines(monkeypatch):
    drawn = _capture(monkeypatch)
    shots = (TileShot(time_from_beep=1.0, split=1.0), TileShot(time_from_beep=1.3, split=0.3))
    tile = TileStageData(label="Ann", stage_number=1, shots=shots, stage_time_seconds=12.34, scorecard=None)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    assert any("shots" in t for t in drawn)
    # TIME is now a captioned headline: the caption carries the word and
    # the value is bare ("12.34"), not a "Time 12.34" line.
    assert "TIME" in drawn
    assert "12.34" in drawn
    assert "HF" not in drawn
    assert "STAGE" not in drawn
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
    # zero. The lines must show the real zero and skip the unread fields --
    # not print a fabricated 0 for a count nobody read. Accuracy and faults
    # are separate lines now, so the zero lands on the faults one.
    scorecard = StageScorecard(alphas=7, charlies=None, deltas=1, misses=None, no_shoots=0)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    assert "A7 D1" in drawn
    assert "NS0" in drawn


def test_procedurals_reach_the_screen():
    """The defect this task exists for.

    ``StageScorecard`` carries ``procedurals`` and it survives into
    ``TileStageData``, but ``_hit_count_line`` read alphas, charlies,
    deltas, misses and no-shoots and never read it. Two procedurals is 20
    points off a stage; the shooter saw a hit factor that did not follow
    from the hits above it and no explanation anywhere on screen.
    """
    scorecard = StageScorecard(alphas=10, charlies=1, deltas=1, misses=0, no_shoots=0, procedurals=2)
    assert summ._faults_line(scorecard) == "M0 NS0 P2"


def test_accuracy_and_faults_are_separate_lines():
    """A/C/D says how well the shooter shot; M/NS/P says what went wrong.
    One line mixing them cannot give the faults their own emphasis."""
    scorecard = StageScorecard(alphas=10, charlies=1, deltas=1, misses=1, no_shoots=0, procedurals=2)
    assert summ._accuracy_line(scorecard) == "A10 C1 D1"
    assert summ._faults_line(scorecard) == "M1 NS0 P2"


def test_a_recorded_zero_is_drawn_and_an_unread_field_is_not():
    """Zero and absent are different facts and must stay distinguishable.

    A scoreboard row that recorded zero misses draws ``M0``. A row that
    carried no penalty column at all draws nothing for it -- and a row
    with no penalty columns whatsoever draws no faults line.
    """
    recorded = StageScorecard(misses=0, no_shoots=0, procedurals=0)
    assert summ._faults_line(recorded) == "M0 NS0 P0"

    partial = StageScorecard(misses=0, no_shoots=None, procedurals=None)
    assert summ._faults_line(partial) == "M0"

    unread = StageScorecard(misses=None, no_shoots=None, procedurals=None)
    assert summ._faults_line(unread) is None


def test_an_all_none_accuracy_draws_nothing():
    assert summ._accuracy_line(StageScorecard(alphas=None, charlies=None, deltas=None)) is None


def test_both_lines_are_drawn_for_a_penalised_tile(monkeypatch):
    drawn = _capture(monkeypatch)
    scorecard = StageScorecard(
        hit_factor=12.17,
        stage_pct=78.5,
        alphas=10,
        charlies=1,
        deltas=1,
        misses=1,
        no_shoots=0,
        procedurals=2,
    )
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    assert "A10 C1 D1" in drawn
    assert "M1 NS0 P2" in drawn


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

    assert "#1" in drawn
    assert "#2" in drawn


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

    assert "#1" in drawn
    ann_index = drawn.index("Ann")
    bo_index = drawn.index("Bo")
    ann_placing = drawn[ann_index + 1]
    bo_placing = drawn[bo_index + 1]
    assert bo_placing == "#1", f"Bo has the higher stage_pct and must rank #1, got {bo_placing!r}"
    assert ann_placing == "#2", f"Ann has the lower stage_pct and must rank #2, got {ann_placing!r}"


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
    assert placing_lines == ["#1"]


# --- the block is bounded on both axes ------------------------------------


def _full_stat_tile(label: str) -> TileStageData:
    """A tile with every line the summary can draw: label, placing, shot
    count, time, HF, stage pct, hit counts, split stats, draw."""
    shots = tuple(TileShot(time_from_beep=1.0 + 0.3 * i, split=0.3) for i in range(6))
    return TileStageData(
        label=label,
        stage_number=1,
        shots=shots,
        stage_time_seconds=12.34,
        scorecard=StageScorecard(
            hit_factor=5.12, stage_pct=87.4, alphas=7, charlies=2, deltas=1, misses=0, no_shoots=0
        ),
    )


def _drawn_boxes(monkeypatch):
    """Record ``(xy, bbox)`` for every line the summary draws, plain or
    plated -- a plate (a placing, a DQ, a lit faults line) is ink on a
    filled rectangle drawn through ``_plate`` rather than
    ``_draw_text_with_shadow``, and the bound this records against must
    cover both paths or an overflowing plate would go unnoticed."""
    boxes: list[tuple[tuple[int, int], tuple[int, int, int, int]]] = []
    original_shadow = summ._draw_text_with_shadow
    original_plate = summ._plate

    def shadow_recorder(draw, canvas, xy, text, font, fill, **kwargs):
        boxes.append((xy, draw.textbbox(xy, text, font=font)))
        return original_shadow(draw, canvas, xy, text, font, fill, **kwargs)

    def plate_recorder(canvas, xy, text, font, *, theme, size):
        plate_w, plate_h = original_plate(canvas, xy, text, font, theme=theme, size=size)
        x, y = xy
        boxes.append((xy, (x, y, x + plate_w, y + plate_h)))
        return plate_w, plate_h

    monkeypatch.setattr(summ, "_draw_text_with_shadow", shadow_recorder)
    monkeypatch.setattr(summ, "_plate", plate_recorder)
    return boxes


def _drawn_labeled_boxes(monkeypatch):
    """Record ``(text, xy, bbox)`` for every line the summary draws,
    plain or plated.

    Text-tagged, unlike :func:`_drawn_boxes`, so a caller can attribute a
    drawn box to its own shooter by what it *says* rather than by where
    it landed. Position is exactly what
    ``test_a_short_cell_keeps_its_summary_inside_its_own_cell`` is
    checking, so filtering by position would silently discard the very
    overflow that test exists to catch.
    """
    boxes: list[tuple[str, tuple[int, int], tuple[int, int, int, int]]] = []
    original_shadow = summ._draw_text_with_shadow
    original_plate = summ._plate

    def shadow_recorder(draw, canvas, xy, text, font, fill, **kwargs):
        boxes.append((text, xy, draw.textbbox(xy, text, font=font)))
        return original_shadow(draw, canvas, xy, text, font, fill, **kwargs)

    def plate_recorder(canvas, xy, text, font, *, theme, size):
        plate_w, plate_h = original_plate(canvas, xy, text, font, theme=theme, size=size)
        x, y = xy
        boxes.append((text, xy, (x, y, x + plate_w, y + plate_h)))
        return plate_w, plate_h

    monkeypatch.setattr(summ, "_draw_text_with_shadow", shadow_recorder)
    monkeypatch.setattr(summ, "_plate", plate_recorder)
    return boxes


def test_a_short_cell_keeps_its_summary_inside_its_own_cell(monkeypatch):
    """A cell too short for its groups must not spill into a neighbour.

    Nothing bounds a group's height by itself: :func:`_fit_font` budgets
    width only. A short cell -- 75px here -- can overflow *either*
    direction now that content is anchored on more than one edge:
    top-left and top-right grow downward, away from the cell's top edge,
    while the bottom-left band and the counts row stacked above it grow
    upward, away from the bottom edge. Either direction attributes one
    competitor's numbers to another, who has no way to disown them.

    Ann sits in the *middle* row of three, not the top, so both
    directions of crossing are observable in one fixture -- a fixture
    with Ann in row 0 can only ever show the downward defect, because
    there is nothing above row 0 to spill into, which is exactly why an
    earlier version of this test kept passing when the height bound
    behind it was deliberately gutted for a manual check: the fixture
    could only see one of the two ways this can now break.

    Boxes are attributed to Ann by their own drawn text, not by where
    they landed -- position is what is under test, so filtering on it
    would drop precisely the overflowing box the test needs to see.
    """
    geometry = SpriteGeometry(canvas_width=360, canvas_height=225, rows=3, cols=1)
    assert geometry.cell_height == 75
    placements = [
        _placement("Above", 0, 0),
        _placement("Ann", 1, 0),
        _placement("Below", 2, 0),
    ]
    data = {
        "Above": TileStageData(label="Above", stage_number=1),
        "Ann": _full_stat_tile("Ann"),
        "Below": TileStageData(label="Below", stage_number=1),
    }

    boxes = _drawn_labeled_boxes(monkeypatch)
    summ.build_hold_still(placements, data, {}, geometry, theme=THEME)

    ann_boxes = [(xy, box) for text, xy, box in boxes if text not in ("Above", "Below")]
    assert ann_boxes, "Ann's own cell drew nothing"
    cell_top, cell_bottom = geometry.cell_height, 2 * geometry.cell_height
    assert (
        min(box[1] for _xy, box in ann_boxes) >= cell_top
    ), "Ann's summary reaches above her own cell, into the shooter above"
    assert (
        max(box[3] for _xy, box in ann_boxes) <= cell_bottom
    ), "Ann's summary reaches below her own cell, into the shooter below"
    # Bounding it must not empty it: the label at least still lands, and
    # a bound that simply stopped drawing would pass the checks above.
    assert len(ann_boxes) >= 2, f"the bound left only {len(ann_boxes)} line(s) in Ann's cell"


def test_a_tall_cell_lays_the_block_out_unshrunk(monkeypatch):
    """The shipped 3840x2160 default has 540-1080px cells, so the group
    bounding introduced by this task must be inert there -- same sizes,
    same anchored positions."""
    geometry = SpriteGeometry(canvas_width=3840, canvas_height=2160, rows=3, cols=3)
    placements = [_placement("Ann", 0, 0)]
    data = {"Ann": _full_stat_tile("Ann")}

    boxes = _drawn_boxes(monkeypatch)
    summ.build_hold_still(placements, data, {}, geometry, theme=THEME)

    scale = summ.CellScale.for_cell(geometry.cell_height)
    # The identity group's origin: the label lands at the cell's own
    # top-left inset by the shared pad on the x axis. The y axis is not
    # the raw pad -- `_draw_group` offsets it by the font's own ascent
    # (`text_y - bbox[1]`) so the glyph's *ink* starts at the pad, not
    # its nominal baseline box -- so the expected y is derived the same
    # way `_draw_group` derives it, from the same font, rather than
    # dropping the axis (a prior version of this assertion checked only
    # `boxes[0][0][0]`, which is what let that offset go unverified).
    scratch_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    ann_font, _fitted = summ._fit_font(scratch_draw, "Ann", THEME, base_size=scale.identity, budget=10_000)
    ann_bbox = scratch_draw.textbbox((0, 0), "Ann", font=ann_font)
    assert boxes[0][0] == (scale.pad, scale.pad - ann_bbox[1])
    # The first line is the label at its unshrunk size: a group that had
    # been scaled down would draw it smaller than this.
    assert boxes[0][1][3] - boxes[0][1][1] >= scale.identity // 2
    assert max(box[3] for _xy, box in boxes) <= geometry.cell_height


def test_a_long_name_keeps_its_summary_inside_its_own_cell_horizontally(monkeypatch):
    """No box may cross a *vertical* cell edge either -- reachable at any
    canvas size a caller asks for, not just a pathologically small one.

    ``src/splitsmith/ui/server.py`` builds ``GridCanvas`` straight from
    the request's ``canvas_width`` / ``canvas_height``, and #692 is about
    to make 1080p and 2.7K first-class outputs -- 1080p at 5+ shooters
    routes to a 4x4 grid, a 480x270 cell, well inside the range this
    bug reached. An ordinary 23-character IPSC name (``_fit_font``
    shrinks each element on its own, but nothing summed a *row's* several
    elements against the cell width before this fix) crossed at every
    geometry from a 3x3 3840x2160 canvas down, including this file's own
    640x360 ``GEOMETRY``.

    Middle *column* of three, mirroring
    ``test_a_short_cell_keeps_its_summary_inside_its_own_cell``'s middle
    *row*: a crossing can go either left or right once there is a real
    neighbour on both sides, and a name is exactly as likely to push a
    trailing placing chip rightward as a long band value is to push
    itself leftward off a right anchor.
    """
    label = "Mathias Axell-Lindstrom"
    geometry = SpriteGeometry(canvas_width=960, canvas_height=180, rows=1, cols=3)
    assert (geometry.cell_width, geometry.cell_height) == (320, 180)
    placements = [
        _placement("Left", 0, 0),
        _placement(label, 0, 1),
        _placement("Right", 0, 2),
    ]
    data = {
        "Left": TileStageData(label="Left", stage_number=1),
        label: _full_stat_tile(label),
        "Right": TileStageData(label="Right", stage_number=1),
    }

    boxes = _drawn_labeled_boxes(monkeypatch)
    summ.build_hold_still(placements, data, {}, geometry, theme=THEME)

    mid_boxes = [(xy, box) for text, xy, box in boxes if text not in ("Left", "Right")]
    assert mid_boxes, "the middle cell drew nothing"
    cell_left, cell_right = geometry.cell_width, 2 * geometry.cell_width
    assert (
        min(box[0] for _xy, box in mid_boxes) >= cell_left
    ), "the middle shooter's summary reaches left of her own cell, into the shooter to her left"
    assert (
        max(box[2] for _xy, box in mid_boxes) <= cell_right
    ), "the middle shooter's summary reaches right of her own cell, into the shooter to her right"
    assert len(mid_boxes) >= 2, f"the bound left only {len(mid_boxes)} box(es) in the middle cell"


# --- each bounding lever is individually load-bearing (fix round 2) -------
#
# The suite above stays green with any *one* of the levers below removed,
# because the other two on the same axis (plus, on the height axis, the
# second-group skip) compensate for most geometries -- it takes all of
# them gone at once, which is what the short/long-name tests above
# happen to do, for the fixture and cell sizes they use. That is how a
# regression in one lever alone shipped unnoticed: a test that only ever
# breaks in combination is not proof any one piece is load-bearing on its
# own. Each test below calls :func:`summ._draw_group` (or, for the
# second-group skip, :func:`summ.build_hold_still`) directly with a
# ``width_budget`` / ``height_budget`` / cell size picked so that *only*
# the one lever under test can prevent the crossing -- verified by
# breaking each lever in isolation (see task-6-report.md's fix-round-2
# section for the transcripts) and confirming the specific test below,
# and only that one, goes red.


def _run_draw_group(monkeypatch, group, *, scale, width_budget, height_budget, origin=(1000, 1000)):
    """Call ``_draw_group`` directly and capture what it drew, without
    going through a whole cell/tile/geometry. Precise and fast: these
    tests are about one function's own bounding, not the composition
    around it."""
    boxes = _drawn_labeled_boxes(monkeypatch)
    canvas = Image.new("RGBA", (4000, 4000), (0, 0, 0, 255))
    draw = ImageDraw.Draw(canvas)
    used = summ._draw_group(
        canvas, draw, group, theme=THEME, scale=scale, origin=origin,
        width_budget=width_budget, height_budget=height_budget,
    )  # fmt: skip
    return boxes, used, origin


def test_the_height_scale_ladder_shrinks_an_oversized_first_line(monkeypatch):
    """The height-clamp lever (``_fit_group_scale``'s height comparison).

    A COLUMN's first element always draws even if it will not fit (see
    the drop-lever test below) -- so the *only* thing standing between
    an over-tall first line and a crossing is shrinking it to fit in the
    first place. ``height_budget=10`` is far below ``AAA``'s unshrunk
    height at ``CellScale.for_cell(1000).detail`` (25px), but well above
    the font floor's, so a working clamp fits it and a disabled one does
    not.
    """
    scale = CellScale.for_cell(1000)
    group = Group(
        anchor=Anchor.TOP_LEFT,
        flow=Flow.COLUMN,
        elements=(Element(role=Role.DETAIL, text="AAA", emphasis=Emphasis.MUTED),),
    )
    boxes, _used, (ox, oy) = _run_draw_group(
        monkeypatch, group, scale=scale, width_budget=2000, height_budget=10
    )
    assert boxes, "the group drew nothing at all"
    assert all(box[1] >= oy and box[3] - oy <= 10 for _t, _xy, box in boxes), boxes


def test_the_column_height_drop_keeps_a_six_line_group_inside_a_tiny_budget(monkeypatch):
    scale = CellScale.for_cell(1000)
    group = Group(
        anchor=Anchor.TOP_LEFT,
        flow=Flow.COLUMN,
        elements=tuple(Element(role=Role.DETAIL, text=f"L{i}", emphasis=Emphasis.MUTED) for i in range(6)),
    )
    boxes, _used, (ox, oy) = _run_draw_group(
        monkeypatch, group, scale=scale, width_budget=2000, height_budget=46
    )
    assert boxes, "the group drew nothing at all"
    assert all(box[1] >= oy and box[3] - oy <= 46 for _t, _xy, box in boxes), boxes
    # Bounding it must not empty it.
    assert len(boxes) >= 1


def test_the_second_group_skip_keeps_the_faults_row_off_when_the_band_used_the_room(monkeypatch):
    """The second-group-skip lever (``_draw_cell``).

    A cell height (56px) picked so that, with every other lever active,
    the band (declared first, TIME/HF/STAGE) uses close enough to the
    whole height budget that the faults/accuracy row stacked above it --
    a *second* BOTTOM_LEFT group -- has no room left. If the skip is
    disabled the second group is still attempted at whatever budget
    remains and crosses into the cell above (the row above Ann's, in
    this three-row middle fixture).
    """
    geometry = SpriteGeometry(canvas_width=360, canvas_height=56 * 3, rows=3, cols=1)
    assert geometry.cell_height == 56
    placements = [
        _placement("Above", 0, 0),
        _placement("Ann", 1, 0),
        _placement("Below", 2, 0),
    ]
    data = {
        "Above": TileStageData(label="Above", stage_number=1),
        "Ann": _full_stat_tile("Ann"),
        "Below": TileStageData(label="Below", stage_number=1),
    }

    boxes = _drawn_labeled_boxes(monkeypatch)
    summ.build_hold_still(placements, data, {}, geometry, theme=THEME)

    ann_boxes = [(xy, box) for text, xy, box in boxes if text not in ("Above", "Below")]
    assert ann_boxes, "Ann's own cell drew nothing"
    cell_top, cell_bottom = geometry.cell_height, 2 * geometry.cell_height
    assert min(box[1] for _xy, box in ann_boxes) >= cell_top, (
        "Ann's summary reaches above her own cell -- the second BOTTOM_LEFT group was drawn when "
        "there was no room left for it"
    )
    assert max(box[3] for _xy, box in ann_boxes) <= cell_bottom


def test_the_width_scale_ladder_shrinks_a_row_to_keep_both_its_elements(monkeypatch):
    """The width-clamp lever (``_fit_group_scale``'s width comparison, ROW).

    Unlike the height axis, this lever's absence cannot itself produce a
    crossing here -- the trailing-drop lever below already guarantees
    that on its own, for any ROW, by dropping whatever would not fit.
    What the width clamp buys is *content*: at ``width_budget=200``,
    ``CellScale.for_cell(1000)``, both ``"Best 0.30"`` and ``"Avg 0.30"``
    individually fit ``_fit_font``'s own per-element floor, so neither
    forces a shrink on its own, but their combined width at full size
    does not fit -- so a width-blind ladder still picks the unshrunk
    scale, the trailing-drop then has to sacrifice the second element to
    stay inside the budget, and only one of two lines survives where a
    width-aware ladder keeps both, shrunk to fit together. (Verified by
    disabling the width comparison and confirming this specific test
    goes red -- see task-6-report.md's fix-round-2 section.)
    """
    scale = CellScale.for_cell(1000)
    group = Group(
        anchor=Anchor.TOP_LEFT,
        flow=Flow.ROW,
        elements=(
            Element(role=Role.DETAIL, text="Best 0.30", emphasis=Emphasis.MUTED),
            Element(role=Role.DETAIL, text="Avg 0.30", emphasis=Emphasis.MUTED),
        ),
    )
    boxes, _used, (ox, oy) = _run_draw_group(
        monkeypatch, group, scale=scale, width_budget=200, height_budget=1000
    )
    texts = [text for text, _xy, _box in boxes]
    assert texts == ["Best 0.30", "Avg 0.30"], (
        f"expected both elements to survive, shrunk to fit together, got {texts!r} -- a width-blind "
        "scale ladder would draw only the first at full size and let the trailing-drop sacrifice "
        "the second"
    )
    assert all(box[0] >= ox and box[2] - ox <= 200 for _t, _xy, box in boxes), boxes


def test_the_row_width_drop_keeps_an_eight_element_row_inside_a_tiny_budget(monkeypatch):
    """The ROW width-drop lever.

    Eight short elements at ``CellScale.for_cell(1000)``: even shrunk to
    the font floor, eight do not fit ``width_budget=85`` (four do).
    Nothing left to shrink -- only dropping the trailing elements that do
    not fit can still prevent a crossing here.
    """
    scale = CellScale.for_cell(1000)
    group = Group(
        anchor=Anchor.TOP_LEFT,
        flow=Flow.ROW,
        elements=tuple(Element(role=Role.DETAIL, text=f"X{i}", emphasis=Emphasis.MUTED) for i in range(8)),
    )
    boxes, _used, (ox, oy) = _run_draw_group(
        monkeypatch, group, scale=scale, width_budget=85, height_budget=1000
    )
    assert boxes, "the group drew nothing at all"
    assert len(boxes) < 8, "all eight elements survived -- the trailing-drop never engaged"
    assert all(box[0] >= ox and box[2] - ox <= 85 for _t, _xy, box in boxes), boxes


def test_the_column_width_skip_drops_only_the_line_that_does_not_fit(monkeypatch):
    """The COLUMN width-skip lever.

    A long ``Best/Avg/Worst``-shaped line and a short ``Draw`` line, top
    right, ``CellScale.for_cell(1000)``, ``width_budget=120``: the long
    line does not fit even at the font floor (this is the exact shape
    review found running left out of its cell), but the short one does.
    Unlike a height overrun, one column line being too wide says nothing
    about the next one, so the skip is per element -- it must drop only
    the line that does not fit and keep the one that does, not drop
    everything from that point on the way the height/width *drop* levers
    do.
    """
    scale = CellScale.for_cell(1000)
    group = Group(
        anchor=Anchor.TOP_RIGHT,
        flow=Flow.COLUMN,
        elements=(
            Element(role=Role.DETAIL, text="Best 0.30  Avg 0.30  Worst 0.30", emphasis=Emphasis.MUTED),
            Element(role=Role.DETAIL, text="Draw 0.30", emphasis=Emphasis.MUTED),
        ),
    )
    boxes, _used, (ox, oy) = _run_draw_group(
        monkeypatch, group, scale=scale, width_budget=120, height_budget=1000, origin=(3000, 1000)
    )
    texts = [text for text, _xy, _box in boxes]
    assert texts == ["Draw 0.30"], (
        f"expected only the line that fits to survive, got {texts!r} -- a disabled per-element "
        "width skip would draw the long line anyway and cross the cell's left edge"
    )
    assert all(ox - box[0] <= 120 and box[2] <= ox for _t, _xy, box in boxes), boxes


def test_build_hold_still_rejects_whole_match_keyed_data():
    placements = [_placement("Ann", 0, 0)]
    data = {("Ann", 1): TileStageData(label="Ann", stage_number=1)}
    with pytest.raises(ValueError, match="keyed by tile label"):
        summ.build_hold_still(placements, data, {}, GEOMETRY, theme=THEME)  # type: ignore[arg-type]


# --- write_hold_still: the extraction + composition + save wrapper --------


def test_the_roster_carries_a_nonzero_penalty_somewhere():
    """A fixture that cannot express a failure cannot catch it.

    Procedurals reach the summary's tile data and were silently dropped
    on the way to the screen. No assertion could have caught that while
    every roster entry set them to 0 or None, which is the same trap #682
    was filed for in a field #682 did not cover.
    """
    from tests.compare_fixture import ROSTER

    penalised = [
        (spec.label, stage_number, scoring.scorecard)
        for spec in ROSTER
        for stage_number, scoring in enumerate(spec.scoring, start=1)
        if scoring.scorecard is not None
        and not scoring.scorecard.dq
        and any(
            bool(v)
            for v in (
                scoring.scorecard.misses,
                scoring.scorecard.no_shoots,
                scoring.scorecard.procedurals,
            )
        )
    ]
    assert penalised, "no roster entry carries a nonzero penalty"
    assert any(card.procedurals for _, _, card in penalised), "no entry carries a procedural"


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
        work_dir=tmp_path / "work",
        ffmpeg_binary="ffmpeg",
        runner=runner,
    )

    assert out_path.exists()
    with Image.open(out_path) as image:
        assert image.size == (GEOMETRY.canvas_width, GEOMETRY.canvas_height)
