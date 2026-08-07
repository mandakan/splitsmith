"""Where the live overlay's ink actually lands, through a real browser.

Issue #693 moved the per-tile sprites off PIL and its hand-rolled width
fitter onto ``overlay_html`` plus headless Chromium, so every assertion
in this file needs a real render and is ``integration``-marked. That is
the point rather than an inconvenience: the argument for the swap is that
a browser's box model keeps content inside its own cell *structurally*,
and a fake rasterizer cannot testify to that. CI installs the browser and
runs this suite with ``SPLITSMITH_REQUIRE_INTEGRATION=1``.

The boundary harness at the bottom is carried over from the PIL renderer
unchanged in what it asserts -- which is itself the test of whether #683's
composition seam held. It is the harness that caught the review defect the
issue quotes: a ``ROW`` group with no cumulative width bound drawing one
shooter's content inside the next shooter's cell.
"""

import pytest
from PIL import Image

from splitsmith.compare import overlay_live, overlay_sprites
from splitsmith.overlay_raster import ChromiumRasterizer
from splitsmith.overlay_theme import load_theme

pytestmark = pytest.mark.integration

GEOMETRY = overlay_sprites.SpriteGeometry(canvas_width=1280, canvas_height=720, rows=2, cols=2)
# The bundled face, not whatever mono happens to be on the host: the
# layout stress tests need metrics that do not vary between a dev machine
# and a CI runner.
THEME = load_theme("splitsmith")


@pytest.fixture(scope="module")
def rasterizer():
    """One browser for the whole module.

    Chromium's process startup is ~0.8s on the dev host and every test
    here would otherwise pay it. This is also how production works -- see
    ``ChromiumRasterizer``'s docstring and ``render_grid_mp4``'s preflight,
    which launch once per render, not once per stage.
    """
    with ChromiumRasterizer() as raster:
        yield raster


def _panel(label="ann", row=0, col=0, **kwargs):
    base = {
        "label": label,
        "row": row,
        "col": col,
        "present": True,
        "shots_fired": 0,
        "expected_shots": None,
        "last_split": None,
    }
    base.update(kwargs)
    return overlay_sprites.TilePanel(**base)


def _state(panels):
    return overlay_sprites.OverlayState(start_seconds=0.0, duration_seconds=1.0, panels=tuple(panels))


def _render(rasterizer, panels, geometry=GEOMETRY, theme=THEME) -> Image.Image:
    png = rasterizer.png(
        overlay_live.state_html(_state(panels), geometry, theme=theme),
        width=geometry.canvas_width,
        height=geometry.canvas_height,
    )
    import io

    return Image.open(io.BytesIO(png)).convert("RGBA")


def _cell(image, geometry, row, col):
    """The tile's own cell. Every pixel of it belongs to that tile: the
    sprite draws nothing outside the cells at all."""
    x0 = col * geometry.cell_width
    y0 = row * geometry.cell_height
    return image.crop((x0, y0, x0 + geometry.cell_width, y0 + geometry.cell_height))


def _has_ink(image) -> bool:
    return image.getextrema()[3][1] > 0


# --- the sprite itself ------------------------------------------------


def test_sprite_is_canvas_sized_rgba(rasterizer):
    image = _render(rasterizer, [_panel(shots_fired=1)])
    assert image.mode == "RGBA"
    assert image.size == (GEOMETRY.canvas_width, GEOMETRY.canvas_height)


def test_ink_lands_in_the_firing_tiles_own_cell(rasterizer):
    image = _render(
        rasterizer,
        [
            _panel("ann", 0, 0, shots_fired=3, last_split=0.25),
            _panel("bo", 0, 1),
            _panel("cy", 1, 0),
            _panel("dee", 1, 1),
        ],
    )
    assert _has_ink(_cell(image, GEOMETRY, 0, 0))
    assert not _has_ink(_cell(image, GEOMETRY, 0, 1))
    assert not _has_ink(_cell(image, GEOMETRY, 1, 0))
    assert not _has_ink(_cell(image, GEOMETRY, 1, 1))


def test_the_split_label_is_drawn_inside_the_bottom_row_cell(rasterizer):
    # A full-width delta strip used to reserve a band across the bottom of
    # the canvas and bottom-anchored cell content was pushed up to clear
    # it. Nothing reserves that band now, so a bottom-row tile's split
    # label lands in the lower part of its own cell.
    image = _render(rasterizer, [_panel("cy", 1, 0, shots_fired=4, last_split=0.31)])
    lower_half = image.crop(
        (
            0,
            GEOMETRY.cell_height + GEOMETRY.cell_height // 2,
            GEOMETRY.cell_width,
            GEOMETRY.canvas_height,
        )
    )
    assert _has_ink(lower_half)


# --- absence reaches the pixels ---------------------------------------


def test_a_filler_tile_draws_nothing_in_its_cell(rasterizer):
    image = _render(rasterizer, [_panel("ann", 0, 0, shots_fired=2), _panel("bo", 0, 1, present=False)])
    assert not _has_ink(_cell(image, GEOMETRY, 0, 1))


def test_present_false_draws_nothing_even_with_shots_fired_and_split(rasterizer):
    # A filler tile from build_overlay_states always has shots_fired=0 and
    # last_split=None, so a fixture that only tries that combination cannot
    # tell "we skip filler tiles" from "we skip tiles with nothing to
    # show" -- both look identical. Force both onto a present=False panel
    # so a removed ``present`` guard actually shows up.
    image = _render(
        rasterizer,
        [_panel("dee", 0, 0, present=False, shots_fired=5, expected_shots=12, last_split=0.5)],
    )
    assert not _has_ink(_cell(image, GEOMETRY, 0, 0))


def test_a_tile_that_has_not_fired_draws_no_counter(rasterizer):
    fired = _render(rasterizer, [_panel(shots_fired=1)])
    unfired = _render(rasterizer, [_panel(shots_fired=0)])
    assert _has_ink(_cell(fired, GEOMETRY, 0, 0))
    assert not _has_ink(_cell(unfired, GEOMETRY, 0, 0))


def test_the_whole_canvas_is_blank_before_anyone_fires(rasterizer):
    # With the delta strip gone there is no canvas-level furniture left: a
    # state where nobody has fired draws nothing anywhere, so the sprite is
    # a fully transparent PNG. That is intended, and it is why every "the
    # sprite reached the pixels" assertion above samples a state where a
    # counter or a split genuinely exists.
    image = _render(rasterizer, [_panel("ann", 0, 0), _panel("bo", 0, 1)])
    assert not _has_ink(image)


# --- layout stress: nothing escapes its own cell ----------------------
#
# 3x3 and 4x4 are first-class grid kinds (compare/layout.py routes 5-16
# shooters to them), not extremes. A fixture has to actually pack a grid
# that size with present, firing tiles or it cannot express the collision
# the #683 review found.

FULL_GRID_LABELS = [
    "ANN",
    "BO",
    "CY",
    "DEE",
    "EVE",
    "FIN",
    "GUS",
    "HAL",
    "IVY",
    "JAY",
    "KIM",
    "LEO",
    "MAE",
    "NAT",
    "OLA",
    "PIA",
]

GRID_SIZES = [(2, 2), (3, 3), (4, 4)]
CANVAS_SIZES = [(1920, 1080), (3840, 2160)]


def _full_grid_panels(rows: int, cols: int):
    """Every cell present and fired, so every cell has a counter and a
    split label to fit -- the shape a narrow cell has to survive."""
    panels = []
    for i in range(rows * cols):
        row, col = divmod(i, cols)
        panels.append(
            _panel(
                FULL_GRID_LABELS[i],
                row,
                col,
                shots_fired=i + 1,
                expected_shots=32,
                last_split=round(0.15 + 0.01 * i, 2),
            )
        )
    return panels


def _ink_extent(crop):
    """Leftmost/rightmost columns with any non-transparent pixel in
    ``crop`` (exclusive right, matching ``Image.getbbox``), or ``None`` if
    the crop has no ink at all."""
    bbox = crop.getchannel("A").getbbox()
    if bbox is None:
        return None
    return bbox[0], bbox[2]


def test_a_counter_too_wide_for_its_cell_is_clipped_not_spilled(rasterizer):
    """The clipping guarantee, driven with input that can actually reach it.

    Deliberately adversarial, and labelled as such. With the live
    overlay's real vocabulary -- a counter like ``"16/32"`` and a split
    like ``"0.28s"`` at ``CellScale.live_primary`` -- nothing overflows a
    cell at any grid kind or canvas in #692, so no realistic fixture puts
    the box model under pressure at all. This one uses a shot count no
    match would ever produce, and measures the two things that matter:
    the over-wide counter is **cut off at its own cell's edge** (ink
    bbox measured at ``(24, 29, 480, 87)`` in a 480px cell -- flush to
    the boundary, not past it) and the neighbouring shooter's cell
    contains only the neighbour's own content.

    **What this test is *not*: a mutation-proven fixture.** Every attempt
    to make it fail by weakening the stylesheet failed -- removing
    ``overflow: hidden`` from ``.cell``, from ``.value``, both together,
    and dropping ``.anchor``'s ``max-width`` bound each left the render
    byte-for-byte where it was. The invariant is enforced redundantly by
    several independent rules, which is the *point* of moving to a real
    box model but also means this assertion is documentation of measured
    behaviour rather than a fixture proven able to catch its own
    regression. The test in this file that is proven to fail is
    :func:`test_cell_text_stays_inside_its_own_cell_on_a_full_grid`:
    dropping ``.anchor-top-left``'s pad inset breaks 5 of its cases.

    Note what changed and is deliberately not asserted either way: the
    old PIL fitter *shrank* text (steps of 2, floored at 12px) until it
    fit, where CSS *clips* it. Under every input a real match can produce
    neither happens -- see ``compare/overlay_live.py``'s docstring.
    """
    geometry = overlay_sprites.SpriteGeometry(canvas_width=1920, canvas_height=1080, rows=4, cols=4)
    panels = [
        _panel("wide", 0, 0, shots_fired=99999999, expected_shots=99999999),
        _panel("next", 0, 1, shots_fired=1, expected_shots=32),
    ]
    image = _render(rasterizer, panels, geometry=geometry)

    # The clipped cell's own ink runs right up to its boundary -- that is
    # what clipping looks like, and asserting it stops *short* of the edge
    # would be asserting the text fit after all. What must hold is that it
    # goes no further.
    left, right = _ink_extent(_cell(image, geometry, 0, 0))
    assert left > 0, "the over-wide counter escaped its own cell to the left"
    assert right <= geometry.cell_width

    # The invariant that matters: the neighbouring shooter's cell contains
    # only the neighbour's own content, still inset from its left edge by
    # the same pad every tile gets. Spilled ink from the cell to its left
    # lands at x=0 here, which is the #683 review defect exactly.
    neighbour_left, _ = _ink_extent(_cell(image, geometry, 0, 1))
    assert neighbour_left > 0, (
        "the over-wide counter's ink reached into the next shooter's cell -- nothing is "
        "clipping content at the cell boundary"
    )


@pytest.mark.parametrize("rows,cols", GRID_SIZES)
@pytest.mark.parametrize("canvas_width,canvas_height", CANVAS_SIZES)
def test_cell_text_stays_inside_its_own_cell_on_a_full_grid(
    rasterizer, canvas_width, canvas_height, rows, cols
):
    geometry = overlay_sprites.SpriteGeometry(
        canvas_width=canvas_width, canvas_height=canvas_height, rows=rows, cols=cols
    )
    panels = _full_grid_panels(rows, cols)
    image = _render(rasterizer, panels, geometry=geometry)
    for panel in panels:
        cell = _cell(image, geometry, panel.row, panel.col)
        extent = _ink_extent(cell)
        assert extent is not None, (
            f"cell ({panel.row},{panel.col}) has no ink at all "
            f"({rows}x{cols} grid, {canvas_width}x{canvas_height})"
        )
        left, right = extent
        assert left > 0, (
            f"cell ({panel.row},{panel.col}) text touches its left edge "
            f"({rows}x{cols} grid, {canvas_width}x{canvas_height})"
        )
        assert right < cell.width, (
            f"cell ({panel.row},{panel.col}) text touches its right edge -- spills into "
            f"the next cell ({rows}x{cols} grid, {canvas_width}x{canvas_height})"
        )
