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
from pathlib import Path

import pytest
from PIL import Image

from splitsmith.compare import overlay_summary as summ
from splitsmith.compare.mp4_grid import GridStagePlan, GridTile
from splitsmith.compare.overlay_data import TileShot, TileStageData
from splitsmith.compare.overlay_sprites import SpriteGeometry, TilePlacement
from splitsmith.overlay_layout import Anchor, ColorToken, Emphasis, Role
from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError
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


def test_the_name_and_the_placing_share_the_top_center():
    """Issue #683 Task 7b: the top rail is centred, not corner-anchored --
    a corner label was a leftover habit from the live overlay staying out
    of the picture's way, and the hold's picture is background texture,
    not content to stay clear of."""
    tile = _full_stat_tile("Ann")
    groups = summ._cell_groups(tile, summ.StagePlacing(rank=2, total_ranked=5), "Ann")
    top = _groups_by_anchor(groups)[Anchor.TOP_CENTER][0]
    assert [e.text for e in top.elements] == ["Ann", "#2"]
    assert top.elements[0].role is Role.IDENTITY
    assert top.elements[1].role is Role.VERDICT
    assert top.elements[1].emphasis is Emphasis.PLATE


def test_the_hero_pct_and_its_working_hf_and_time_are_declared():
    """Issue #683 Task 7b: units attach to their own value now (no more
    separate TIME/HF/STAGE captions), and the three are not drawn as
    equal-weight peers -- stage percentage is the hero (the only figure
    comparable across the whole grid), hit factor and time are its
    smaller "working"."""
    scorecard = StageScorecard(hit_factor=12.0, stage_pct=100.0)
    tile = TileStageData(label="Ann", stage_number=1, stage_time_seconds=4.5, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    middle = _groups_by_anchor(groups)[Anchor.MIDDLE_CENTER]

    hero_group = next(g for g in middle if any(e.role is Role.HERO for e in g.elements))
    working_group = next(g for g in middle if any(e.role is Role.HEADLINE for e in g.elements))

    assert [e.text for e in hero_group.elements] == ["100.0%"]
    assert [e.text for e in working_group.elements] == ["4.50s", "12.00 HF"]
    # No captions anywhere -- the unit is embedded in the text itself.
    assert all(e.caption is None for e in (*hero_group.elements, *working_group.elements))


def test_a_clean_run_states_its_zeros_without_a_plate():
    """Presence is a fact; emphasis is a judgement. Drawing an accent
    plate on every clean cell in the grid would make the plate mean
    nothing when a real penalty turns up. The counts still read as
    "worth -10" -- coloured accent -- even at zero; only an *actual*
    nonzero fault additionally plates (see ``test_a_penalised_run_...``).
    """
    scorecard = StageScorecard(alphas=10, misses=0, no_shoots=0, procedurals=0)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    faults = [e for g in groups for e in g.elements if e.text in ("M0", "NS0", "P0")]
    assert [e.text for e in faults] == ["M0", "NS0", "P0"]
    assert all(e.emphasis is Emphasis.PLAIN for e in faults)
    assert all(e.color is ColorToken.ACCENT for e in faults)


def test_a_penalised_run_lights_the_plate():
    """Only the counts that actually happened plate. ``NS0`` stays plain
    (still red -- a no-shoot is worth -10 whether or not this shooter had
    one) while ``M1``/``P2`` -- both genuinely nonzero -- plate."""
    scorecard = StageScorecard(alphas=10, misses=1, no_shoots=0, procedurals=2)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    by_text = {e.text: e for g in groups for e in g.elements if e.text in ("M1", "NS0", "P2")}
    assert set(by_text) == {"M1", "NS0", "P2"}
    assert by_text["M1"].emphasis is Emphasis.PLATE
    assert by_text["P2"].emphasis is Emphasis.PLATE
    assert by_text["NS0"].emphasis is Emphasis.PLAIN
    assert all(e.color is ColorToken.ACCENT for e in by_text.values())


def test_the_six_counts_are_one_equal_weight_colour_coded_row():
    """Issue #683 Task 7: A/C/D used to draw at Role.DETAIL and M/NS/P at
    the bigger Role.VERDICT, so the faults visually outweighed the hits
    even though all six are inputs to the same hit-factor number. They
    must now share one role (equal size) and read as a single row, with
    colour -- not size -- carrying what each count is worth."""
    scorecard = StageScorecard(alphas=10, charlies=1, deltas=1, misses=1, no_shoots=0, procedurals=2)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    (counts_group,) = [g for g in _groups_by_anchor(groups)[Anchor.MIDDLE_CENTER] if len(g.elements) == 6]
    texts_and_colors = [(e.text, e.color) for e in counts_group.elements]
    assert texts_and_colors == [
        ("A10", ColorToken.SPLIT_GOOD),
        ("C1", ColorToken.INK),
        ("D1", ColorToken.SPLIT),
        ("M1", ColorToken.ACCENT),
        ("NS0", ColorToken.ACCENT),
        ("P2", ColorToken.ACCENT),
    ]
    # Equal weight: every one of the six shares a role, so none can
    # outrank another by size the way the old accuracy/faults split did.
    assert len({e.role for e in counts_group.elements}) == 1


def test_split_statistics_sit_quiet_and_small_on_the_bottom_rail():
    """Issue #683 Task 7b moved this group from the running clock's old
    top-right corner to the bottom rail: the three-rail redesign uses the
    whole cell (top identity, middle scored result, bottom split
    detail), and split statistics are explicitly the quiet, small figures
    -- last in the reading order, not competing with the hero for the
    top of the cell."""
    tile = _full_stat_tile("Ann")
    groups = summ._cell_groups(tile, None, "Ann")
    bottom = _groups_by_anchor(groups)[Anchor.BOTTOM_CENTER][0]
    texts = [e.text for e in bottom.elements]
    assert any("Best" in t for t in texts)
    assert any(t.startswith("Draw") for t in texts)
    assert all(e.emphasis is Emphasis.MUTED for e in bottom.elements)
    assert all(e.role is Role.DETAIL for e in bottom.elements)


def test_a_dq_takes_the_placings_slot_and_suppresses_the_scoring():
    scorecard = StageScorecard(hit_factor=5.12, stage_pct=80.0, alphas=7, dq=True)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    texts = [e.text for g in groups for e in g.elements]
    assert "DQ" in texts
    assert not any("5.12" in t for t in texts)
    assert not any("80.0" in t for t in texts)


def test_a_dq_with_a_stage_time_promotes_it_to_hero_with_no_divider():
    """A DQ suppresses the counts entirely (see the test above), so there
    is nothing above a rule to separate from -- and nothing left to show
    in the middle rail except the collapsed time, promoted to the hero's
    own visual weight so the cell's biggest slot is not simply empty."""
    scorecard = StageScorecard(hit_factor=5.12, stage_pct=80.0, alphas=7, dq=True)
    tile = TileStageData(label="Ann", stage_number=1, stage_time_seconds=9.87, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    middle = _groups_by_anchor(groups).get(Anchor.MIDDLE_CENTER, [])

    assert not any(g.divider for g in middle)
    (hero_group,) = middle
    assert [e.text for e in hero_group.elements] == ["9.87s"]
    assert [e.role for e in hero_group.elements] == [Role.HERO]


def test_no_divider_when_nothing_is_computed_below_the_counts():
    """A rule with nothing below it to divide from would be a stray line
    for no reason -- it must only appear when both sides of it are real."""
    scorecard = StageScorecard(alphas=5)  # no hit factor, no stage_pct
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)  # no stage_time either
    groups = summ._cell_groups(tile, None, "Ann")
    middle = _groups_by_anchor(groups)[Anchor.MIDDLE_CENTER]

    assert not any(g.divider for g in middle)
    assert len(middle) == 1
    assert [e.text for e in middle[0].elements] == ["A5"]


def test_a_tile_with_nothing_declares_only_its_label():
    """The control cell. A tile with no audit and no scorecard renders
    its name and nothing else -- which is what the pixel checks measure
    "is the hold blurred" against."""
    groups = summ._cell_groups(None, None, "Ann")
    assert [e.text for g in groups for e in g.elements] == ["Ann"]


def test_the_middle_rail_declares_counts_then_a_divider_then_working_then_the_hero():
    """Groups sharing MIDDLE_CENTER stack top to bottom in declaration
    order (it is not a bottom anchor, so no reversal), and that order is
    the equation itself: what the shooter did, a rule, what it produced,
    what it's worth."""
    tile = _full_stat_tile("Ann")
    groups = summ._cell_groups(tile, None, "Ann")
    middle = _groups_by_anchor(groups)[Anchor.MIDDLE_CENTER]

    assert len(middle) == 4
    counts, divider, working, hero = middle
    # ``_full_stat_tile`` carries alphas/charlies/deltas/misses/no_shoots
    # but not procedurals (unread, not zero) -- five counts, not six.
    assert len(counts.elements) == 5
    assert divider.divider is True
    assert divider.elements == ()
    assert {e.role for e in working.elements} == {Role.HEADLINE}
    assert [e.role for e in hero.elements] == [Role.HERO]


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
#
# ``_cell_groups`` is unchanged by this task (see the module docstring), so
# these ask it directly for the same facts the old tests round-tripped
# through PIL drawing to observe.


def _texts(groups) -> list[str]:
    return [e.text for g in groups for e in g.elements]


def test_missing_scorecard_collapses_to_the_time_as_the_hero():
    """No scorecard means no counts, no hit factor and no stage
    percentage -- but the tile still has a stage time, and issue #683
    Task 7b's collapse rule promotes it into the hero's own visual
    weight rather than leaving that slot empty."""
    shots = (TileShot(time_from_beep=1.0, split=1.0), TileShot(time_from_beep=1.3, split=0.3))
    tile = TileStageData(label="Ann", stage_number=1, shots=shots, stage_time_seconds=12.34, scorecard=None)
    groups = summ._cell_groups(tile, None, "Ann")
    texts = _texts(groups)

    assert any("shots" in t for t in texts)
    # The unit is embedded in the value now -- no separate caption.
    assert "12.34s" in texts
    assert not any(e.caption is not None for g in groups for e in g.elements)
    assert not any("%" in t for t in texts)
    assert not any(" HF" in t for t in texts)
    assert not any(t.startswith("A") and t[1:2].isdigit() for t in texts)
    assert "DQ" not in texts

    hero_group = next(g for g in groups if any(e.role is Role.HERO for e in g.elements))
    assert [e.text for e in hero_group.elements] == ["12.34s"]


def test_stage_points_never_appears_and_stage_pct_does():
    scorecard = StageScorecard(stage_points=143.2, stage_pct=87.4)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    texts = _texts(summ._cell_groups(tile, None, "Ann"))

    assert any("87.4" in t for t in texts)
    assert not any("143.2" in t for t in texts)


def test_none_hit_counts_are_omitted_not_zeroed():
    # charlies and misses are genuinely unread (None); no_shoots is a real
    # zero. Each count is now its own element -- the row must draw A7 and
    # D1 and skip charlies/misses entirely, not print a fabricated 0 for a
    # count nobody read.
    scorecard = StageScorecard(alphas=7, charlies=None, deltas=1, misses=None, no_shoots=0)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    texts = _texts(summ._cell_groups(tile, None, "Ann"))

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
    texts = _texts(summ._cell_groups(tile, None, "Ann"))

    for count in ("A10", "C1", "D1", "M1", "NS0", "P2"):
        assert count in texts


def test_manual_time_is_marked_as_manual():
    tile = TileStageData(label="Ann", stage_number=1, stage_time_seconds=12.34, stage_time_is_manual=True)
    texts = _texts(summ._cell_groups(tile, None, "Ann"))

    assert any("12.34" in t and "manual" in t for t in texts)


def test_dq_replaces_the_scoring_lines():
    scorecard = StageScorecard(hit_factor=5.12, stage_pct=80.0, alphas=7, dq=True)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    texts = _texts(groups)
    captions = [e.caption for g in groups for e in g.elements if e.caption is not None]

    assert "DQ" in texts
    assert "HF" not in captions
    assert "STAGE" not in captions
    assert not any("A7" in t for t in texts)


def test_splits_exclude_the_draw_from_best_average_worst():
    shots = (
        TileShot(time_from_beep=1.5, split=1.5),  # the draw
        TileShot(time_from_beep=1.7, split=0.2),
        TileShot(time_from_beep=2.0, split=0.3),
        TileShot(time_from_beep=2.4, split=0.4),
    )
    tile = TileStageData(label="Ann", stage_number=1, shots=shots)
    texts = _texts(summ._cell_groups(tile, None, "Ann"))

    stats_lines = [t for t in texts if t.startswith("Best")]
    assert len(stats_lines) == 1
    assert "0.20" in stats_lines[0]
    assert "0.30" in stats_lines[0]
    assert "0.40" in stats_lines[0]
    assert "1.50" not in stats_lines[0]
    assert any(t == "Draw 1.50" for t in texts)


def test_a_tile_with_no_audit_shows_only_its_label():
    tile = TileStageData(label="Ann", stage_number=1)
    assert _texts(summ._cell_groups(tile, None, "Ann")) == ["Ann"]


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


def test_placing_drawn_for_ranked_tiles():
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


def test_the_ranking_reaches_the_rendered_html():
    """The wiring test: ``build_hold_still`` must actually thread
    ``_rank_placings``'s output into each placement's own declared groups
    (via ``_summary_cells``) and on into the rasterized HTML -- the three
    tests above only prove the ranking function itself is correct."""
    placements = [_placement("Ann", 0, 0), _placement("Bo", 0, 1)]
    data = {
        "Ann": TileStageData(label="Ann", stage_number=1, scorecard=StageScorecard(stage_pct=95.0)),
        "Bo": TileStageData(label="Bo", stage_number=1, scorecard=StageScorecard(stage_pct=60.0)),
    }
    html = _rendered_html(placements, data)

    assert "#1" in html
    assert "#2" in html


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
    placings = summ._rank_placings(placements, data)

    cells = summ._summary_cells(placements, data, placings)
    by_label = {placement.label: groups for placement, groups in cells}

    ann_texts = {e.text for g in by_label["Ann"] for e in g.elements}
    assert "12.34s" in ann_texts
    assert "87.4%" in ann_texts
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
    assert "87.4" in ann_cell
    for figure in ("12.34", "87.4", "5.12"):
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


@pytest.mark.integration
def test_a_long_names_ink_never_crosses_its_own_cell_in_a_real_render():
    """The pixel-level proof the whole pivot was justified by.

    Reproduces the shape that broke the old PIL fitter -- a 23-character
    competitor name with a full stat block (identity + placing chip,
    shot detail, TIME/HF/STAGE band, accuracy/faults row) -- in a cell
    small enough that content genuinely wants to overflow: 160x90, a 4x4
    grid on a 640x360 canvas. Every other placement in the grid is a
    filler (``present=False``), which ``overlay_html.summary_html``
    forces empty regardless of what it is handed (see
    ``tests/test_overlay_html.py::test_a_filler_tile_cell_is_empty_of_text``),
    so the target is the *only* placement with any content at all --
    which means any non-transparent pixel anywhere outside its own
    rectangle can only be its own content escaping, not another cell's
    legitimate ink. That is what makes a single whole-image alpha bounding
    box a sufficient check here, rather than needing to attribute pixels
    to a shooter.
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
