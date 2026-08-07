"""The frozen post-stage summary still: freeze extraction, blur-once, compose.

Freeze extraction goes through a fake runner -- no ffmpeg is ever shelled
out to here, per CLAUDE.md. As of issue #683's amendment (Task 6R-3),
``overlay_summary`` no longer hand-fits or draws text itself: it declares
a cell's content as ``Group``/``Element`` tuples (``_cell_groups``) and
turns the whole canvas's declared cells into one HTML document composed
through an injected :class:`splitsmith.overlay_raster.Rasterizer`
(``docs/superpowers/plans/2026-08-06-overlay-composition-seam-amendment.md``).
Task 7 (also issue #683) changed what ``_cell_groups`` declares for the
six hit/fault counts -- see :func:`overlay_summary._count_elements` --
but not this split; the checks below still split two ways:

- Tests about *what a cell says* call :func:`overlay_summary._cell_groups`
  (and :func:`overlay_summary._rank_placings`) directly -- both are pure,
  so asserting against their output is more direct than round-tripping
  through rasterization to observe the same facts.
- Tests about *how ``build_hold_still`` wires that declaration through
  the rasterizer* inject :class:`_FakeRasterizer`, a recording double
  standing in for :class:`splitsmith.overlay_raster.Rasterizer` -- unit
  tests never launch a browser, the same seam
  ``compare.mp4_grid.Runner`` gives ``subprocess.run``. It records the
  HTML it was asked to render (inspected for the right substrings) and
  returns a real PNG so the compositing step has something genuine to
  alpha-composite.

The old fitter's own bounding tests (six "lever" tests plus the harness
that drove ``_draw_group`` directly) are gone along with the machinery
they existed to prove did not leak -- see the amendment's "What is
deleted". The invariant they protected (no shooter's figures cross into
a neighbour's cell) is now structural: ``overflow: hidden`` is asserted
once, on the shared stylesheet, in ``tests/test_overlay_html.py``, and
proven against real rendered pixels two ways -- by
``tests/test_compare_grid_overlay_integration.py``'s hold check, which
this task's brief singles out as the boundary assertion that must carry
over unmodified, and by this file's own
``test_a_long_names_ink_never_crosses_its_own_cell_in_a_real_render``
(``@pytest.mark.integration``, real Chromium, measuring the rasterized
PNG's own alpha channel against the target cell's rectangle -- added in
this task's fix round after a review noted the load-bearing claim
otherwise rested on a CSS-text assertion plus an argument about CSS
semantics, with nobody having actually watched Chromium honour it).
What remains this file's own job below the pixel layer is that
``build_hold_still`` attributes each placement's own data to that
placement's own cell and no other's (see
``test_summary_cells_never_attributes_one_placements_content_to_another``).
"""

from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from splitsmith.compare import overlay_summary as summ
from splitsmith.compare.mp4_grid import GridStagePlan, GridTile
from splitsmith.compare.overlay_data import TileShot, TileStageData
from splitsmith.compare.overlay_sprites import SpriteGeometry, TilePlacement
from splitsmith.overlay_html import grid_html
from splitsmith.overlay_layout import Anchor, ColorToken, Emphasis, Flow, Role
from splitsmith.overlay_raster import (
    CHROMIUM_CHANNEL,
    DEVICE_SCALE_FACTOR,
    ChromiumRasterizer,
    RasterizerUnavailableError,
)
from splitsmith.overlay_theme import load_theme
from splitsmith.ui.project import StageScorecard

THEME = load_theme("clean")
GEOMETRY = SpriteGeometry(canvas_width=640, canvas_height=360, rows=2, cols=2)


class _FakeRasterizer:
    """Records every HTML document handed to :meth:`png` and returns a
    real (tiny, fully transparent) PNG, so ``build_hold_still``'s
    alpha-composite step has genuine bytes to work with rather than a
    stub that would mask a wiring bug in the compositing itself.

    Stands in for :class:`splitsmith.overlay_raster.Rasterizer` -- see
    the module docstring.
    """

    def __init__(self, *, fill: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self._fill = fill

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append((html, width, height))
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), self._fill).save(buf, format="PNG")
        return buf.getvalue()


class _BoomRasterizer:
    """A rasterizer that always fails -- the "a live browser exists but
    this particular call went wrong" case, distinct from
    ``RasterizerUnavailableError`` (no browser at all), which is
    ``mp4_grid``'s render-wide preflight's job, not ``build_hold_still``'s.
    """

    def png(self, html: str, *, width: int, height: int) -> bytes:
        raise RuntimeError("rasterize boom")


def _rendered_html(placements, data, *, geometry=GEOMETRY, freezes=None, **kwargs) -> str:
    """Render once through a :class:`_FakeRasterizer` and return the one
    HTML document it was asked to rasterize."""
    fake = _FakeRasterizer()
    summ.build_hold_still(placements, data, freezes or {}, geometry, theme=THEME, rasterizer=fake, **kwargs)
    assert len(fake.calls) == 1, "expected exactly one rasterize call for the whole canvas"
    html, width, height = fake.calls[0]
    assert (width, height) == (geometry.canvas_width, geometry.canvas_height)
    return html


def _cell_markup(html: str, row: int, col: int) -> str:
    """The one placement's own ``<div style="grid-row:...">...</div>``
    fragment, isolated so a test can assert what is and is not present in
    exactly that cell's own markup -- not merely somewhere in the whole
    document (which carries the shared ``<style>`` block, font names and
    all), which would pass even if a shooter's figures landed in a
    neighbour's wrapper instead of their own.
    """
    marker = f"grid-row:{row + 1};grid-column:{col + 1};"
    start = html.index(marker)
    next_marker = html.find('<div style="grid-row:', start + len(marker))
    end = next_marker if next_marker != -1 else html.index("</div></body>")
    return html[start:end]


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


#: Fixed cell geometry for tests that call ``_cell_groups`` directly --
#: only the gaps computed from it change with cell size, never the shape
#: of what is declared, so one size is enough to assert content against.
_CELL_W, _CELL_H = 320, 180
_SCALE = summ._summary_scale(_CELL_H)


def _cg(tile, label="Ann"):
    return summ._cell_groups(tile, label, scale=_SCALE, cell_width=_CELL_W, cell_height=_CELL_H)


def test_the_name_and_the_dq_chip_share_the_top_center():
    """Issue #683 Task 8: the identity row is left-aligned (see
    ``Group.align``), not centred -- the approved design's whole cell
    reads flush-left, not the old three-rail design's centred rails."""
    scorecard = StageScorecard(dq=True)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = _cg(tile)
    top = _groups_by_anchor(groups)[Anchor.TOP_CENTER][0]
    assert [e.text for e in top.elements] == ["Ann", "DQ"]
    assert top.elements[0].role is Role.IDENTITY
    assert top.elements[1].role is Role.VERDICT
    assert top.elements[1].emphasis is Emphasis.PLATE
    assert top.align == "left"


def test_a_clean_tile_has_no_dq_chip():
    tile = _full_stat_tile("Ann")
    groups = _cg(tile)
    top = _groups_by_anchor(groups)[Anchor.TOP_CENTER][0]
    assert [e.text for e in top.elements] == ["Ann"]


def test_scoring_band_declares_a_label_the_counts_then_hf_and_time():
    """Issue #683 Task 8: both bands draw their figures at the same
    role/size (``Role.HEADLINE``) -- neither the hit factor/time row nor
    the split statistics outrank the other, which is the whole point of
    the redesign. Hit factor carries a smaller inline "HF" unit
    (``Element.unit``); time's unit is embedded in the text itself."""
    scorecard = StageScorecard(hit_factor=12.0, stage_pct=100.0, alphas=10)
    tile = TileStageData(label="Ann", stage_number=1, stage_time_seconds=4.5, scorecard=scorecard)
    groups = _cg(tile)
    middle = _groups_by_anchor(groups)[Anchor.MIDDLE_CENTER]

    label_group = next(g for g in middle if any(e.text == "Scoring" for e in g.elements))
    assert label_group.elements[0].role is Role.LABEL

    working_group = next(g for g in middle if any(e.text == "12.00" for e in g.elements))
    assert [e.text for e in working_group.elements] == ["12.00", "4.50s"]
    assert {e.role for e in working_group.elements} == {Role.HEADLINE}
    assert working_group.elements[0].unit == "HF"
    assert working_group.elements[1].unit is None
    # No captions on the scoring figures -- units attach to the value
    # itself, unlike the split statistics' captioned grid below.
    assert all(e.caption is None for e in working_group.elements)


def test_a_clean_run_states_its_zeros_without_a_plate():
    """Presence is a fact; emphasis is a judgement. Drawing an accent
    plate on every clean cell in the grid would make the plate mean
    nothing when a real penalty turns up. The counts still read as
    "worth -10" -- coloured accent_text -- even at zero; only an *actual*
    nonzero fault additionally plates (see ``test_a_penalised_run_...``).
    """
    scorecard = StageScorecard(alphas=10, misses=0, no_shoots=0, procedurals=0)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = _cg(tile)
    faults = [e for g in groups for e in g.elements if e.text in ("M0", "NS0", "P0")]
    assert [e.text for e in faults] == ["M0", "NS0", "P0"]
    assert all(e.emphasis is Emphasis.PLAIN for e in faults)
    assert all(e.color is ColorToken.ACCENT_TEXT for e in faults)


def test_a_penalised_run_lights_the_plate():
    """Only the counts that actually happened plate. ``NS0`` stays plain
    (still red -- a no-shoot is worth -10 whether or not this shooter had
    one) while ``M1``/``P2`` -- both genuinely nonzero -- plate."""
    scorecard = StageScorecard(alphas=10, misses=1, no_shoots=0, procedurals=2)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = _cg(tile)
    by_text = {e.text: e for g in groups for e in g.elements if e.text in ("M1", "NS0", "P2")}
    assert set(by_text) == {"M1", "NS0", "P2"}
    assert by_text["M1"].emphasis is Emphasis.PLATE
    assert by_text["P2"].emphasis is Emphasis.PLATE
    assert by_text["NS0"].emphasis is Emphasis.PLAIN
    assert all(e.color is ColorToken.ACCENT_TEXT for e in by_text.values())


def test_the_six_counts_are_one_equal_weight_colour_coded_row():
    """Issue #683 Task 7: A/C/D used to draw at Role.DETAIL and M/NS/P at
    the bigger Role.VERDICT, so the faults visually outweighed the hits
    even though all six are inputs to the same hit-factor number. They
    must now share one role (equal size) and read as a single row, with
    colour -- not size -- carrying what each count is worth."""
    scorecard = StageScorecard(alphas=10, charlies=1, deltas=1, misses=1, no_shoots=0, procedurals=2)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = _cg(tile)
    (counts_group,) = [g for g in _groups_by_anchor(groups)[Anchor.MIDDLE_CENTER] if len(g.elements) == 6]
    texts_and_colors = [(e.text, e.color) for e in counts_group.elements]
    assert texts_and_colors == [
        ("A10", ColorToken.SPLIT_GOOD),
        ("C1", ColorToken.INK),
        ("D1", ColorToken.SPLIT),
        ("M1", ColorToken.ACCENT_TEXT),
        ("NS0", ColorToken.ACCENT_TEXT),
        ("P2", ColorToken.ACCENT_TEXT),
    ]
    # Equal weight: every one of the six shares a role, so none can
    # outrank another by size the way the old accuracy/faults split did.
    assert len({e.role for e in counts_group.elements}) == 1


def test_split_statistics_are_a_captioned_four_column_grid():
    """Issue #683 Task 8: Best/Avg/Worst/Draw is its own band -- a
    ``Flow.GRID`` group spanning the cell's full width -- equal weight to
    the Scoring band above it, not a quiet trailing line."""
    tile = _full_stat_tile("Ann")
    groups = _cg(tile)
    splits_groups = [
        g
        for g in _groups_by_anchor(groups)[Anchor.MIDDLE_CENTER]
        if any(e.caption in ("Best", "Avg", "Worst", "Draw") for e in g.elements)
    ]
    (grid_group,) = splits_groups
    assert grid_group.flow is Flow.GRID
    assert [e.caption for e in grid_group.elements] == ["Best", "Avg", "Worst", "Draw"]
    assert all(e.role is Role.HEADLINE for e in grid_group.elements)
    assert grid_group.align == "left"


def test_a_dq_chip_suppresses_the_scoring_figures():
    scorecard = StageScorecard(hit_factor=5.12, stage_pct=80.0, alphas=7, dq=True)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = _cg(tile)
    texts = [e.text for g in groups for e in g.elements]
    assert "DQ" in texts
    assert not any("5.12" in t for t in texts)
    assert not any("A7" in t for t in texts)


def test_a_dq_with_a_stage_time_still_shows_the_scoring_band():
    """A DQ suppresses the counts and hit factor (see the test above),
    but the mock's own DQ reference cell still shows the "Scoring" label
    with just the stage time -- the band is not dropped outright, only
    what it can no longer show is."""
    scorecard = StageScorecard(hit_factor=5.12, stage_pct=80.0, alphas=7, dq=True)
    tile = TileStageData(label="Ann", stage_number=1, stage_time_seconds=9.87, scorecard=scorecard)
    groups = _cg(tile)
    middle = _groups_by_anchor(groups).get(Anchor.MIDDLE_CENTER, [])

    label_group = next(g for g in middle if any(e.text == "Scoring" for e in g.elements))
    assert label_group is not None
    working_group = next(g for g in middle if any(e.text == "9.87s" for e in g.elements))
    assert [e.text for e in working_group.elements] == ["9.87s"]
    assert [e.role for e in working_group.elements] == [Role.HEADLINE]


def test_a_tile_with_nothing_declares_only_its_label():
    """The control cell. A tile with no audit and no scorecard renders
    its name and nothing else -- which is what the pixel checks measure
    "is the hold blurred" against."""
    groups = _cg(None)
    assert [e.text for g in groups for e in g.elements] == ["Ann"]


def test_a_tile_with_no_scorecard_but_shots_still_declares_splits():
    """No scorecard at all (not even a DQ) but real shots: the scoring
    band is skipped entirely (nothing to show), but split statistics --
    computed off the shots themselves, not the scorecard -- still draw."""
    shots = (TileShot(time_from_beep=1.0, split=1.0), TileShot(time_from_beep=1.3, split=0.3))
    tile = TileStageData(label="Ann", stage_number=1, shots=shots)
    groups = _cg(tile)
    middle = _groups_by_anchor(groups)[Anchor.MIDDLE_CENTER]
    assert not any(e.text == "Scoring" for g in middle for e in g.elements)
    assert any(e.text == "Splits" for g in middle for e in g.elements)


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


# --- the rasterizer seam: build_hold_still's own wiring --------------------


def test_no_rasterizer_composes_the_freezes_with_no_summary_text():
    """``rasterizer=None`` (the default) is also the degradation path a
    caller with no usable Chromium falls back to (see
    ``mp4_grid.render_grid_mp4``'s preflight) -- so this doubles as "a
    missing browser must not crash a render, just omit the text".

    No freeze frame is supplied either, so the canvas stays pure black
    unless something composited over it -- a `_full_stat_tile`'s worth of
    figures would light plenty of non-black pixels if the summary text
    reached the canvas despite no rasterizer being given.
    """
    placements = [_placement("Ann", 0, 0)]
    data = {"Ann": _full_stat_tile("Ann")}
    image = summ.build_hold_still(placements, data, {}, GEOMETRY, theme=THEME)
    assert image.getextrema() == ((0, 0), (0, 0), (0, 0))


def test_the_rasterizer_is_called_once_for_the_whole_canvas_and_its_result_is_composited():
    placements = [_placement("Ann", 0, 0)]
    data = {"Ann": TileStageData(label="Ann", stage_number=1)}
    fake = _FakeRasterizer(fill=(200, 10, 10, 255))

    image = summ.build_hold_still(placements, data, {}, GEOMETRY, theme=THEME, rasterizer=fake)

    assert len(fake.calls) == 1
    _html, width, height = fake.calls[0]
    assert (width, height) == (GEOMETRY.canvas_width, GEOMETRY.canvas_height)
    # The fake's opaque red PNG covers the whole canvas, so compositing it
    # must have actually happened -- a wiring bug that built the HTML but
    # never called ``alpha_composite`` would leave this pixel black.
    assert image.getpixel((10, 10)) == (200, 10, 10)


def test_a_rasterizer_that_raises_degrades_to_no_text_not_a_crash(caplog):
    """Distinct from ``RasterizerUnavailableError`` (no browser at all,
    caught once per render by ``mp4_grid``'s preflight): this is a live
    rasterizer whose one call for this stage went wrong. One bad
    rasterization must not cost the whole stage the way letting the
    exception propagate out of ``build_hold_still`` would -- mirroring
    ``_prepare_cell``'s and ``extract_freeze_frames``'s own per-tile
    degradation elsewhere in this module.
    """
    placements = [_placement("Ann", 0, 0)]
    data = {"Ann": _full_stat_tile("Ann")}

    with caplog.at_level("WARNING"):
        image = summ.build_hold_still(
            placements, data, {}, GEOMETRY, theme=THEME, rasterizer=_BoomRasterizer()
        )

    assert image.mode == "RGB"
    assert image.size == (GEOMETRY.canvas_width, GEOMETRY.canvas_height)
    assert image.getextrema() == ((0, 0), (0, 0), (0, 0))
    assert "rasterize boom" in caplog.text


def test_write_hold_still_forwards_the_rasterizer(tmp_path):
    tile = _tile("Ann", 0, 0, trim=tmp_path / "ann.mp4")
    plan = _plan([tile])
    fake = _FakeRasterizer()
    data = {"Ann": TileStageData(label="Ann", stage_number=1)}

    summ.write_hold_still(
        plan,
        data,
        GEOMETRY,
        theme=THEME,
        work_dir=tmp_path / "work",
        ffmpeg_binary="ffmpeg",
        runner=_Recorder(returncode=1),
        rasterizer=fake,
    )

    assert len(fake.calls) == 1


# --- text content: what a cell's declared groups say -----------------------


def _texts(groups) -> list[str]:
    return [e.text for g in groups for e in g.elements]


def test_missing_scorecard_still_shows_stage_time_and_shot_stats():
    """No scorecard means no counts, no hit factor -- but the tile still
    has a stage time, and it still draws (in the Scoring band, at the same
    weight it always draws at) rather than being dropped for want of a
    scorecard. Splits still draw too, computed off the shots themselves."""
    shots = (TileShot(time_from_beep=1.0, split=1.0), TileShot(time_from_beep=1.3, split=0.3))
    tile = TileStageData(label="Ann", stage_number=1, shots=shots, stage_time_seconds=12.34, scorecard=None)
    groups = _cg(tile)
    texts = _texts(groups)

    # The unit is embedded in the value now -- no separate caption for it.
    assert "12.34s" in texts
    assert not any("%" in t for t in texts)
    assert not any(" HF" in t for t in texts)
    assert not any(t.startswith("A") and t[1:2].isdigit() for t in texts)
    assert "DQ" not in texts
    assert "Scoring" in texts
    assert "Splits" in texts
    assert "0.30" in texts  # the one non-draw split, as Best/Avg/Worst

    working_group = next(g for g in groups if any(e.text == "12.34s" for e in g.elements))
    assert [e.role for e in working_group.elements] == [Role.HEADLINE]


def test_stage_pct_and_stage_points_never_appear():
    """Issue #683 Task 8: the stage percentage is gone entirely, not
    merely resized or moved -- along with stage_points, which never drew
    to begin with."""
    scorecard = StageScorecard(stage_points=143.2, stage_pct=87.4, alphas=10)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    texts = _texts(_cg(tile))

    assert not any("87.4" in t for t in texts)
    assert not any("143.2" in t for t in texts)


def test_none_hit_counts_are_omitted_not_zeroed():
    # charlies and misses are genuinely unread (None); no_shoots is a real
    # zero. Each count is now its own element -- the row must draw A7 and
    # D1 and skip charlies/misses entirely, not print a fabricated 0 for a
    # count nobody read.
    scorecard = StageScorecard(alphas=7, charlies=None, deltas=1, misses=None, no_shoots=0)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    texts = _texts(_cg(tile))

    assert "A7" in texts
    assert "D1" in texts
    assert "NS0" in texts
    assert not any(t.startswith("C") for t in texts)
    assert not any(t.startswith("M") for t in texts)


def _count_texts(scorecard) -> list[str]:
    return [e.text for e in summ._count_elements(scorecard)]


def test_procedurals_reach_the_screen():
    """The defect this task exists for.

    ``StageScorecard`` carries ``procedurals`` and it survives into
    ``TileStageData``, but the old hit-count line read alphas, charlies,
    deltas, misses and no-shoots and never read it. Two procedurals is 20
    points off a stage; the shooter saw a hit factor that did not follow
    from the hits above it and no explanation anywhere on screen.
    """
    scorecard = StageScorecard(alphas=10, charlies=1, deltas=1, misses=0, no_shoots=0, procedurals=2)
    assert _count_texts(scorecard)[-3:] == ["M0", "NS0", "P2"]


def test_accuracy_and_faults_are_both_in_the_one_row():
    """A/C/D says how well the shooter shot; M/NS/P says what went wrong.
    Both are now one equal-weight row (issue #683 Task 7) -- colour, not a
    separate line/role, is what gives the faults their own reading."""
    scorecard = StageScorecard(alphas=10, charlies=1, deltas=1, misses=1, no_shoots=0, procedurals=2)
    assert _count_texts(scorecard) == ["A10", "C1", "D1", "M1", "NS0", "P2"]


def test_a_recorded_zero_is_drawn_and_an_unread_field_is_not():
    """Zero and absent are different facts and must stay distinguishable.

    A scoreboard row that recorded zero misses draws ``M0``. A row that
    carried no penalty column at all draws nothing for it -- and a row
    with no penalty columns whatsoever draws no faults counts at all.
    """
    recorded = StageScorecard(misses=0, no_shoots=0, procedurals=0)
    assert _count_texts(recorded) == ["M0", "NS0", "P0"]

    partial = StageScorecard(misses=0, no_shoots=None, procedurals=None)
    assert _count_texts(partial) == ["M0"]

    unread = StageScorecard(misses=None, no_shoots=None, procedurals=None)
    assert _count_texts(unread) == []


def test_an_all_none_accuracy_draws_nothing():
    assert _count_texts(StageScorecard(alphas=None, charlies=None, deltas=None)) == []


def test_both_accuracy_and_faults_are_drawn_for_a_penalised_tile():
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
    texts = _texts(_cg(tile))

    for count in ("A10", "C1", "D1", "M1", "NS0", "P2"):
        assert count in texts


def test_manual_time_is_marked_as_manual():
    tile = TileStageData(label="Ann", stage_number=1, stage_time_seconds=12.34, stage_time_is_manual=True)
    texts = _texts(_cg(tile))

    assert any("12.34" in t and "manual" in t for t in texts)


def test_dq_replaces_the_scoring_lines():
    scorecard = StageScorecard(hit_factor=5.12, stage_pct=80.0, alphas=7, dq=True)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = _cg(tile)
    texts = _texts(groups)

    assert "DQ" in texts
    assert not any("5.12" in t for t in texts)
    assert not any("A7" in t for t in texts)


def test_splits_exclude_the_draw_from_best_average_worst():
    shots = (
        TileShot(time_from_beep=1.5, split=1.5),  # the draw
        TileShot(time_from_beep=1.7, split=0.2),
        TileShot(time_from_beep=2.0, split=0.3),
        TileShot(time_from_beep=2.4, split=0.4),
    )
    tile = TileStageData(label="Ann", stage_number=1, shots=shots)
    groups = _cg(tile)
    by_caption = {e.caption: e.text for g in groups for e in g.elements if e.caption is not None}

    assert by_caption == {"Best": "0.20", "Avg": "0.30", "Worst": "0.40", "Draw": "1.50"}


def test_a_tile_with_no_audit_shows_only_its_label():
    tile = TileStageData(label="Ann", stage_number=1)
    assert _texts(_cg(tile)) == ["Ann"]


def test_a_filler_tile_is_black_with_no_text():
    """The control at the ``build_hold_still`` layer: a filler placement
    must reach neither a freeze frame nor any rasterized content, matching
    the live sprite's own treatment of an empty slot."""
    placements = [_placement("Bo", 0, 1, present=False)]
    html = _rendered_html(placements, {})
    image = summ.build_hold_still(placements, {}, {}, GEOMETRY, theme=THEME)

    assert "Bo" not in _cell_markup(html, 0, 1)
    extrema = image.getextrema()
    assert extrema == ((0, 0), (0, 0), (0, 0))


# --- ranking (Task 8's addition -- not in the stale brief) ----------------
#
# ``_rank_placings`` is unchanged by this task, so the ranking arithmetic
# itself is asked for directly rather than round-tripped through
# rasterization to read the same facts back off drawn strings. A separate
# wiring test below confirms the ranking this function returns actually
# reaches the rendered HTML through ``build_hold_still``/``_summary_cells``.


def test_placing_computed_for_ranked_tiles():
    placements = [_placement("Ann", 0, 0), _placement("Bo", 0, 1)]
    data = {
        "Ann": TileStageData(label="Ann", stage_number=1, scorecard=StageScorecard(stage_pct=95.0)),
        "Bo": TileStageData(label="Bo", stage_number=1, scorecard=StageScorecard(stage_pct=60.0)),
    }
    placings = summ._rank_placings(placements, data)

    assert placings["Ann"].rank == 1
    assert placings["Bo"].rank == 2


def test_ranking_follows_stage_pct_even_when_stage_points_disagree():
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
    placings = summ._rank_placings(placements, data)

    assert placings["Bo"].rank == 1, f"Bo has the higher stage_pct and must rank #1, got {placings['Bo']!r}"
    assert placings["Ann"].rank == 2, f"Ann has the lower stage_pct and must rank #2, got {placings['Ann']!r}"


def test_dq_missing_scorecard_and_filler_get_no_placing():
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
    placings = summ._rank_placings(placements, data)

    # The only rankable tile is Cy (DQ'd Ann and scorecard-less Bo are
    # excluded, filler Dee never enters the pool at all) -- alone in it.
    assert set(placings) == {"Cy"}
    assert placings["Cy"].rank == 1


def test_ranking_no_longer_reaches_the_rendered_html():
    """Issue #683 Task 8: the placing is gone from the rendered summary
    entirely, along with the stage percentage it was ranked by.
    ``_rank_placings``/``StagePlacing`` stay in the module (kept for a
    possible future caller -- see the task report) but ``build_hold_still``
    no longer threads their output into ``_cell_groups``, so no
    ``"#1"``/``"#2"`` chip reaches the HTML even though the ranking itself
    is still directly computable (see the tests above)."""
    placements = [_placement("Ann", 0, 0), _placement("Bo", 0, 1)]
    data = {
        "Ann": TileStageData(label="Ann", stage_number=1, scorecard=StageScorecard(stage_pct=95.0)),
        "Bo": TileStageData(label="Bo", stage_number=1, scorecard=StageScorecard(stage_pct=60.0)),
    }
    html = _rendered_html(placements, data)

    assert "#1" not in html
    assert "#2" not in html


# --- content attribution: one placement's data never reaches another's ----
#
# The old fitter-era boundary tests here (``test_a_short_cell_keeps_its_
# summary_inside_its_own_cell``, ``test_a_long_name_keeps_its_summary_
# inside_its_own_cell_horizontally``) proved a *pixel* never crossed a
# cell edge by recording PIL draw calls -- there is no PIL drawing left in
# this module to record. That guarantee is now structural (``overflow:
# hidden``, asserted once in ``tests/test_overlay_html.py`` and proven
# against real rendered pixels by ``tests/test_compare_grid_overlay_
# integration.py``'s hold check -- see this task's brief, which singles
# that check out as the boundary assertion that must carry over
# unmodified; it does, unchanged, per the task report).
#
# ``test_a_tall_cell_lays_the_block_out_unshrunk`` is also gone rather
# than adapted: it asserted PIL font-metric positions were "inert" at a
# large cell size, which is meaningless once there is no shrink-fitter to
# be inert. CSS has no equivalent failure mode to guard here.
#
# What is still this module's own job -- and still worth a direct test --
# is that ``build_hold_still``/``_summary_cells`` attribute each
# placement's own data to that placement's own cell and never to a
# neighbour's, *before* any of it reaches HTML or CSS.


def _full_stat_tile(label: str) -> TileStageData:
    """A tile with every line the summary can draw: label, 6 shots, time,
    HF, hit counts including a genuinely nonzero (lit) procedural, and
    split stats. ``stage_pct`` is set on the scorecard for realism (a
    real audit would carry it) but is never drawn -- issue #683 Task 8
    removed it entirely.

    ``procedurals=1`` (issue #683 F1) is load-bearing, not incidental:
    the whole-branch review found that a fixture with no lit penalty
    plate could not express the defect it was reviewing -- a lit plate
    is, by declaration order, the first thing an unbounded middle band
    clips. Without it here, this fixture would have the same blind spot
    ``tests.compare_fixture.ROSTER`` had before
    ``test_the_roster_carries_a_nonzero_penalty_somewhere`` closed it.
    """
    shots = tuple(TileShot(time_from_beep=1.0 + 0.3 * i, split=0.3) for i in range(6))
    return TileStageData(
        label=label,
        stage_number=1,
        shots=shots,
        stage_time_seconds=12.34,
        scorecard=StageScorecard(
            hit_factor=5.12,
            stage_pct=87.4,
            alphas=7,
            charlies=2,
            deltas=1,
            misses=0,
            no_shoots=0,
            procedurals=1,
        ),
    )


def test_summary_cells_never_attributes_one_placements_content_to_another():
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

    cells = summ._summary_cells(placements, data, scale=_SCALE, cell_width=_CELL_W, cell_height=_CELL_H)
    by_label = {placement.label: groups for placement, groups in cells}

    ann_texts = {e.text for g in by_label["Ann"] for e in g.elements}
    assert "12.34s" in ann_texts
    assert "5.12" in ann_texts
    assert not any("87.4" in t for t in ann_texts)
    # Neighbours have no audit and no scorecard: their own declared
    # groups must carry nothing but their own label -- not a trace of
    # Ann's figures, which a naive "reuse the previous groups" bug could
    # otherwise leak in.
    assert _texts(by_label["Above"]) == ["Above"]
    assert _texts(by_label["Below"]) == ["Below"]


def test_a_full_stat_tiles_figures_never_reach_a_neighboring_cells_markup():
    """The vertical case: Ann sits in the *middle* row of three, so a
    figure landing in either neighbour (not just the one below) would be
    caught."""
    geometry = SpriteGeometry(canvas_width=360, canvas_height=225, rows=3, cols=1)
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
    html = _rendered_html(placements, data, geometry=geometry)

    ann_cell = _cell_markup(html, 1, 0)
    above_cell = _cell_markup(html, 0, 0)
    below_cell = _cell_markup(html, 2, 0)

    assert "12.34" in ann_cell
    assert "5.12" in ann_cell
    for figure in ("12.34", "5.12"):
        assert figure not in above_cell, f"{figure!r} reached the cell above Ann's"
        assert figure not in below_cell, f"{figure!r} reached the cell below Ann's"


def test_a_long_names_figures_never_reach_a_neighboring_cells_markup():
    """The horizontal case, mirroring the vertical one above: a long
    identity label sits in the *middle* column of three."""
    label = "Mathias Axell-Lindstrom"
    geometry = SpriteGeometry(canvas_width=960, canvas_height=180, rows=1, cols=3)
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
    html = _rendered_html(placements, data, geometry=geometry)

    mid_cell = _cell_markup(html, 0, 1)
    left_cell = _cell_markup(html, 0, 0)
    right_cell = _cell_markup(html, 0, 2)

    assert label in mid_cell
    assert "12.34" in mid_cell
    for figure in (label, "12.34", "87.4"):
        assert figure not in left_cell, f"{figure!r} reached the cell to the left"
        assert figure not in right_cell, f"{figure!r} reached the cell to the right"


# --- integration: real Chromium, measured on pixels, not markup ------------
#
# Everything above either asks the pure declaration layer
# (``_cell_groups``/``_rank_placings``) what a cell *should* say, or asks a
# fake rasterizer what HTML ``build_hold_still`` handed it -- neither
# observes whether a real browser actually honours ``overflow: hidden``.
# ``tests/test_overlay_html.py`` asserts the CSS text is present; this test
# is the one that renders it through real Chromium and measures the result.


class _CapturingRealRasterizer:
    """Wraps a real :class:`~splitsmith.overlay_raster.ChromiumRasterizer`
    and remembers the exact PNG bytes it returned.

    ``build_hold_still`` immediately alpha-composites the rasterizer's
    result over the canvas and only returns the composited RGB image, so
    a test that wants the rasterizer's own raw (alpha-carrying) output --
    to measure exactly what Chromium painted, not what it looks like
    sitting on a black background -- needs to intercept it here rather
    than inspect ``build_hold_still``'s return value.
    """

    def __init__(self, real: ChromiumRasterizer) -> None:
        self._real = real
        self.last_png: bytes | None = None

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.last_png = self._real.png(html, width=width, height=height)
        return self.last_png


def _declared_content_survived_the_fit_policy(html: str, *, width: int, height: int) -> dict:
    """DOM-level proof for issue #683 F1: not "did ink stay inside the
    cell" (guaranteed by ``overflow: hidden`` alone, even when almost
    everything a cell was declared to say has been clipped away) but "is
    what ``_cell_groups`` declared actually there, unclipped".

    Navigates a real Chromium to ``html`` (mirroring
    :meth:`~splitsmith.overlay_raster.ChromiumRasterizer.png`'s own
    ``page.goto(file://...)`` + ``document.fonts.ready`` sequence, since
    a shrink/drop decision made before the bundled face loads and reflows
    everything under it would be measuring the wrong metrics) and calls
    ``window.__splitsmithFit`` the same way that method does, then reads
    back, for the *first* (only, in every caller of this helper) cell in
    the document:

    - each Splits caption (``Best``/``Avg``/``Worst``/``Draw``) AND its
      own value (the number below it): whether each was declared at all,
      whether the fit policy left it visible (not ``display: none``), and
      whether its rendered rectangle is fully inside the cell's own
      rectangle -- a dropped-but-still-laid-out element could be "in the
      DOM" while sitting well outside the visible, clipped area, which is
      exactly what pre-fix HEAD did. The caption and value are checked
      separately and both matter: a caption is the top half of its ``.el``
      and can clear the cell boundary while the value below it is cut --
      an emptied ``.group`` still consuming a flex gap did exactly that
      (F1's leftover-gutter defect), and a check that only ever looked at
      ``.caption`` rects passed straight through it.
    - whether ``P1`` (the fixture's one genuinely nonzero -- lit --
      fault) and ``M0``/``NS0`` (zero-valued faults) are each visible,
      so a caller can check F1's rule 3: a lit plate must never be
      dropped while a zero-valued count survives.

    Raises :class:`playwright.sync_api.Error` (never catches it) if no
    usable Chromium can be launched -- every caller is responsible for
    its own ``pytest.skip``, matching how this suite's other real-browser
    tests treat :class:`~splitsmith.overlay_raster.RasterizerUnavailableError`.
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=CHROMIUM_CHANNEL, headless=True)
        try:
            with tempfile.TemporaryDirectory(prefix="splitsmith-f1-test-") as tmp:
                html_path = Path(tmp) / "summary.html"
                html_path.write_text(html, encoding="utf-8")
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=DEVICE_SCALE_FACTOR,
                )
                try:
                    page = context.new_page()
                    page.goto(html_path.resolve().as_uri(), wait_until="load")
                    page.evaluate("document.fonts.ready")
                    page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
                    return page.evaluate("""
                        () => {
                          const cell = document.querySelector('.cell');
                          const cellRect = cell.getBoundingClientRect();
                          function within(rect) {
                            return rect.top >= cellRect.top - 0.5 && rect.bottom <= cellRect.bottom + 0.5
                              && rect.left >= cellRect.left - 0.5 && rect.right <= cellRect.right + 0.5
                              && rect.width > 0 && rect.height > 0;
                          }
                          function elFor(text) {
                            return Array.from(cell.querySelectorAll('.value'))
                              .find(el => el.textContent.trim() === text);
                          }
                          function visible(el) {
                            if (!el) { return false; }
                            return getComputedStyle(el.closest('.el')).display !== 'none';
                          }
                          const captions = ['Best', 'Avg', 'Worst', 'Draw'].map(function (cap) {
                            const el = Array.from(cell.querySelectorAll('.caption'))
                              .find(c => c.textContent.trim() === cap);
                            const shown = visible(el);
                            const rect = shown ? el.getBoundingClientRect() : null;
                            // The value is the OTHER half of the same ``.el`` --
                            // see ``overlay_html._element_div``. Checked
                            // separately from its own caption: a caption can
                            // clear the cell boundary while the value below it
                            // is cut, which is exactly what an emptied
                            // ``.group`` still consuming a flex gap does.
                            const valueEl = el ? el.closest('.el').querySelector('.value') : null;
                            const valueShown = visible(valueEl);
                            const valueRect = valueShown ? valueEl.getBoundingClientRect() : null;
                            return {
                              caption: cap, present: !!el, visible: shown,
                              within: shown ? within(rect) : null,
                              valuePresent: !!valueEl, valueVisible: valueShown,
                              valueWithin: valueShown ? within(valueRect) : null,
                            };
                          });
                          const p1 = elFor('P1');
                          const m0 = elFor('M0');
                          const ns0 = elFor('NS0');
                          return {
                            captions: captions,
                            p1Visible: visible(p1),
                            m0Visible: visible(m0),
                            ns0Visible: visible(ns0),
                          };
                        }
                        """)
                finally:
                    context.close()
        finally:
            browser.close()


def _assert_splits_and_penalty_priority_held(result: dict) -> None:
    """Shared assertions over :func:`_declared_content_survived_the_fit_policy`'s
    result -- issue #683 F1's rules 2 and 3."""
    for entry in result["captions"]:
        assert entry["present"], f"{entry['caption']!r} was never declared at all"
        assert entry["visible"], f"{entry['caption']!r} was dropped by the fit policy"
        assert entry["within"], f"{entry['caption']!r} rendered outside the cell's own rectangle"
        # The value below the caption -- e.g. the number under "Best" --
        # is checked separately, not assumed to follow its caption: a
        # caption is the top half of its ``.el`` and can clear the cell
        # boundary while the value beneath it is clipped (an emptied
        # ``.group`` still consuming a flex gap pushes exactly the value,
        # never the caption above it, out of the cell -- see the F1
        # follow-up fix report).
        assert entry["valuePresent"], f"{entry['caption']!r}'s value was never declared at all"
        assert entry["valueVisible"], f"{entry['caption']!r}'s value was dropped by the fit policy"
        assert entry["valueWithin"], f"{entry['caption']!r}'s value rendered outside the cell's own rectangle"
    if result["m0Visible"]:
        assert result["p1Visible"], "M0 (a zero count) survived while P1 (a lit penalty) was dropped"
    if result["ns0Visible"]:
        assert result["p1Visible"], "NS0 (a zero count) survived while P1 (a lit penalty) was dropped"


@pytest.mark.integration
def test_a_long_names_ink_never_crosses_its_own_cell_in_a_real_render():
    """The pixel-level proof the whole pivot was justified by.

    Reproduces the shape that broke the old PIL fitter -- a 23-character
    competitor name with a full stat block (identity, the six colour-coded
    hit/fault counts including a lit procedural, hit factor, stage time,
    and split statistics -- see :func:`_full_stat_tile`) -- in a cell
    small enough that content genuinely wants to overflow: 160x90, a 4x4
    grid on a 640x360 canvas. Every other placement in the grid is a
    filler (``present=False``), which ``overlay_html.grid_html``
    forces empty regardless of what it is handed (see
    ``tests/test_overlay_html.py::test_a_filler_tile_cell_is_empty_of_text``),
    so the target is the *only* placement with any content at all --
    which means any non-transparent pixel anywhere outside its own
    rectangle can only be its own content escaping, not another cell's
    legitimate ink. That is what makes a single whole-image alpha bounding
    box a sufficient check here, rather than needing to attribute pixels
    to a shooter.

    **This alone is not proof the cell says anything.** An empty cell
    also never crosses its own boundary, and issue #683's whole-branch
    review found that this check passed *most comfortably* exactly when
    the fit-less middle band had clipped away the most content: measured
    against the pre-fix code, this test's own bounding box was 70px tall
    out of the cell's 90 (78%) -- comfortably "inside the cell" -- while
    every one of the Splits band's declared captions was laid out *below*
    the visible, clipped area. A whole-image alpha bounding box cannot
    tell "ink from Scoring, clipped" apart from "ink from Splits, intact"
    -- both paint pixels somewhere inside the same 160x90 rectangle. The
    companion assertion below can, because it asks the DOM directly which
    declared element is which, rather than reading pixels back and
    guessing.
    """
    label = "Mathias Axell-Lindstrom"
    geometry = SpriteGeometry(canvas_width=640, canvas_height=360, rows=4, cols=4)
    assert (geometry.cell_width, geometry.cell_height) == (160, 90)
    target_row, target_col = 1, 2
    placements = [
        _placement(
            label if (row, col) == (target_row, target_col) else f"filler-{row}-{col}",
            row,
            col,
            present=(row, col) == (target_row, target_col),
        )
        for row in range(geometry.rows)
        for col in range(geometry.cols)
    ]
    data = {label: _full_stat_tile(label)}

    try:
        with ChromiumRasterizer() as real:
            wrapper = _CapturingRealRasterizer(real)
            summ.build_hold_still(placements, data, {}, geometry, theme=THEME, rasterizer=wrapper)
    except RasterizerUnavailableError as exc:
        pytest.skip(str(exc))

    assert wrapper.last_png is not None
    image = Image.open(io.BytesIO(wrapper.last_png)).convert("RGBA")
    alpha = image.split()[-1]
    bbox = alpha.getbbox()
    assert bbox is not None, "expected the target cell to paint something -- the render is blank"

    cell_left = target_col * geometry.cell_width
    cell_top = target_row * geometry.cell_height
    cell_right = cell_left + geometry.cell_width
    cell_bottom = cell_top + geometry.cell_height

    assert bbox[0] >= cell_left, f"ink starts at x={bbox[0]}, left of the cell's own left edge {cell_left}"
    assert bbox[1] >= cell_top, f"ink starts at y={bbox[1]}, above the cell's own top edge {cell_top}"
    assert (
        bbox[2] <= cell_right
    ), f"ink extends to x={bbox[2]}, right of the cell's own right edge {cell_right}"
    assert (
        bbox[3] <= cell_bottom
    ), f"ink extends to y={bbox[3]}, below the cell's own bottom edge {cell_bottom}"

    # Companion assertion (issue #683 F1): the same fixture, the same
    # 160x90 stress geometry -- but a real DOM read for the *specific*
    # declared content (Splits, and the lit-vs-zero fault ordering)
    # rather than the whole cell's alpha bounding box. Rebuilds the exact
    # single-cell document ``build_hold_still`` composed above (a filler
    # tile renders an empty ``.cell`` with nothing to query, so
    # reproducing just the one real cell is equivalent and lets
    # ``_declared_content_survived_the_fit_policy`` find it with a bare
    # ``.cell`` selector).
    scale = summ._summary_scale(geometry.cell_height)
    cells = summ._summary_cells(
        [_placement(label, 0, 0)],
        data,
        scale=scale,
        cell_width=geometry.cell_width,
        cell_height=geometry.cell_height,
    )
    solo_geometry = SpriteGeometry(
        canvas_width=geometry.cell_width, canvas_height=geometry.cell_height, rows=1, cols=1
    )
    solo_html = grid_html(cells, geometry=solo_geometry, scale=scale, theme=THEME)
    try:
        result = _declared_content_survived_the_fit_policy(
            solo_html, width=solo_geometry.canvas_width, height=solo_geometry.canvas_height
        )
    except PlaywrightError as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"no usable Chromium: {exc}")
    _assert_splits_and_penalty_priority_held(result)


@pytest.mark.integration
def test_a_full_stat_block_survives_the_fit_policy_in_a_real_render():
    """THE regression test for issue #683 F1: "the summary has no fit
    policy."

    Renders a single ``_full_stat_tile`` (6 shots, a genuinely nonzero --
    lit -- procedural) at 160x90, the exact geometry
    ``test_a_long_names_ink_never_crosses_its_own_cell_in_a_real_render``
    (this branch's own flagship pixel proof) already runs at, and reached
    into the DOM after the fit-policy script runs (see
    ``overlay_html._fit_script``, invoked here the same way
    :meth:`~splitsmith.overlay_raster.ChromiumRasterizer.png` invokes it
    in production) to check something a pixel bounding box cannot: not
    just "did ink stay inside the cell" but "is the *declared* content
    actually there, unclipped".

    At HEAD before F1's fix, ``.cell`` was a bare ``grid-template-rows:
    auto 1fr auto`` with no fit policy at all: the 1fr track grows to fit
    whatever content wants (CSS's automatic minimum size for a bare
    ``1fr`` track is its content's own size), and only THEN does
    ``overflow: hidden`` clip the excess -- always from the bottom, which
    by ``_cell_groups``' declaration order is always the Splits band and
    a lit penalty plate, while the zero-valued counts above them survive.
    So at 160x90 with this fixture, Best/Avg/Worst/Draw were entirely
    missing and, worse for an app whose product IS splits, silently so:
    every check that only measured "did ink cross the cell boundary" kept
    passing. This test fails against that behaviour (see the fix-round
    report for the transcript) and passes once the ``minmax(0, 1fr)``
    track fix plus the shrink/drop fit policy land.
    """
    label = "Ann"
    geometry = SpriteGeometry(canvas_width=640, canvas_height=360, rows=4, cols=4)
    assert (geometry.cell_width, geometry.cell_height) == (160, 90)
    scale = summ._summary_scale(geometry.cell_height)
    data = {label: _full_stat_tile(label)}
    cells = summ._summary_cells(
        [_placement(label, 0, 0)],
        data,
        scale=scale,
        cell_width=geometry.cell_width,
        cell_height=geometry.cell_height,
    )
    # A single-cell canvas exactly the size of the stress cell: the whole
    # document's ``.cell`` IS the target, so
    # ``_declared_content_survived_the_fit_policy`` needs no placement
    # bookkeeping to find the right one.
    solo_geometry = SpriteGeometry(
        canvas_width=geometry.cell_width, canvas_height=geometry.cell_height, rows=1, cols=1
    )
    html = grid_html(cells, geometry=solo_geometry, scale=scale, theme=THEME)

    try:
        result = _declared_content_survived_the_fit_policy(
            html, width=solo_geometry.canvas_width, height=solo_geometry.canvas_height
        )
    except PlaywrightError as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"no usable Chromium: {exc}")

    _assert_splits_and_penalty_priority_held(result)


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
