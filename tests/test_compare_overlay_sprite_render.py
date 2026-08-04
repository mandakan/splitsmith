"""Sprite rendering: where the ink lands, and the cache key."""

import pytest

from splitsmith import overlay_text
from splitsmith.compare import overlay_sprites
from splitsmith.overlay_theme import load_theme

GEOMETRY = overlay_sprites.SpriteGeometry(canvas_width=1280, canvas_height=720, rows=2, cols=2)
THEME = load_theme("clean")
# The bundled font, not whatever mono face happens to be on the host --
# the full-grid layout stress tests need font metrics that don't vary
# between a dev machine and CI.
SPLITSMITH_THEME = load_theme("splitsmith")


def _panel(label, row, col, **kwargs):
    base = {
        "label": label,
        "row": row,
        "col": col,
        "present": True,
        "shots_fired": 0,
        "expected_shots": None,
        "last_split": None,
        "rank": None,
        "delta_to_leader": None,
    }
    base.update(kwargs)
    return overlay_sprites.TilePanel(**base)


def _state(panels, start=0.0, duration=1.0):
    return overlay_sprites.OverlayState(start_seconds=start, duration_seconds=duration, panels=tuple(panels))


def _quadrant(image, geometry, row, col):
    """The tile's own cell, excluding the bottom delta strip."""
    x0 = col * geometry.cell_width
    y0 = row * geometry.cell_height
    x1 = x0 + geometry.cell_width
    y1 = min(y0 + geometry.cell_height, geometry.canvas_height - geometry.strip_height)
    return image.crop((x0, y0, x1, y1))


def _has_ink(image) -> bool:
    return image.getextrema()[3][1] > 0


def test_sprite_is_canvas_sized_rgba():
    image = overlay_sprites.render_state(_state([_panel("ann", 0, 0, shots_fired=1)]), GEOMETRY, theme=THEME)
    assert image.mode == "RGBA"
    assert image.size == (GEOMETRY.canvas_width, GEOMETRY.canvas_height)


def test_ink_lands_in_the_firing_tiles_own_cell():
    panels = [
        _panel("ann", 0, 0, shots_fired=3, last_split=0.25, rank=1, delta_to_leader=0.0),
        _panel("bo", 0, 1),
        _panel("cy", 1, 0),
        _panel("dee", 1, 1),
    ]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    assert _has_ink(_quadrant(image, GEOMETRY, 0, 0))
    assert not _has_ink(_quadrant(image, GEOMETRY, 0, 1))
    assert not _has_ink(_quadrant(image, GEOMETRY, 1, 0))


def test_a_filler_tile_draws_nothing_in_its_cell():
    panels = [
        _panel("ann", 0, 0, shots_fired=2, rank=1, delta_to_leader=0.0),
        _panel("bo", 0, 1, present=False, shots_fired=0),
    ]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    assert not _has_ink(_quadrant(image, GEOMETRY, 0, 1))


def test_a_tile_that_has_not_fired_draws_no_counter():
    fired = overlay_sprites.render_state(_state([_panel("ann", 0, 0, shots_fired=1)]), GEOMETRY, theme=THEME)
    unfired = overlay_sprites.render_state(
        _state([_panel("ann", 0, 0, shots_fired=0)]), GEOMETRY, theme=THEME
    )
    assert _has_ink(_quadrant(fired, GEOMETRY, 0, 0))
    assert not _has_ink(_quadrant(unfired, GEOMETRY, 0, 0))


def test_the_delta_strip_draws_across_the_bottom_band():
    panels = [
        _panel("ann", 0, 0, shots_fired=2, rank=1, delta_to_leader=0.0),
        _panel("bo", 0, 1, shots_fired=1, rank=2, delta_to_leader=0.31),
    ]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    strip = image.crop(
        (0, GEOMETRY.canvas_height - GEOMETRY.strip_height, GEOMETRY.canvas_width, GEOMETRY.canvas_height)
    )
    assert _has_ink(strip)


def test_no_strip_ink_before_anyone_fires():
    panels = [_panel("ann", 0, 0), _panel("bo", 0, 1)]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    strip = image.crop(
        (0, GEOMETRY.canvas_height - GEOMETRY.strip_height, GEOMETRY.canvas_width, GEOMETRY.canvas_height)
    )
    assert not _has_ink(strip)


def test_identical_panels_reuse_one_file(tmp_path):
    panels = [_panel("ann", 0, 0, shots_fired=1, last_split=1.0, rank=1, delta_to_leader=0.0)]
    states = [_state(panels, 0.0, 1.0), _state(panels, 1.0, 2.0)]
    sequence = overlay_sprites.write_sprite_sequence(states, GEOMETRY, theme=THEME, cache_dir=tmp_path)
    assert len(sequence) == 2
    assert sequence[0][0] == sequence[1][0]
    assert len(list(tmp_path.glob("*.png"))) == 1


def test_durations_are_carried_through(tmp_path):
    panels = [_panel("ann", 0, 0)]
    states = [_state(panels, 0.0, 1.5), _state([_panel("ann", 0, 0, shots_fired=1)], 1.5, 2.5)]
    sequence = overlay_sprites.write_sprite_sequence(states, GEOMETRY, theme=THEME, cache_dir=tmp_path)
    assert [d for _, d in sequence] == [1.5, 2.5]


def test_different_content_gets_a_different_file(tmp_path):
    a = [_panel("ann", 0, 0, shots_fired=1)]
    b = [_panel("ann", 0, 0, shots_fired=2)]
    sequence = overlay_sprites.write_sprite_sequence(
        [_state(a), _state(b)], GEOMETRY, theme=THEME, cache_dir=tmp_path
    )
    assert sequence[0][0] != sequence[1][0]


def test_geometry_is_part_of_the_cache_key(tmp_path):
    panels = [_panel("ann", 0, 0, shots_fired=1)]
    wide = overlay_sprites.SpriteGeometry(canvas_width=1920, canvas_height=1080, rows=2, cols=2)
    first = overlay_sprites.write_sprite_sequence([_state(panels)], GEOMETRY, theme=THEME, cache_dir=tmp_path)
    second = overlay_sprites.write_sprite_sequence([_state(panels)], wide, theme=THEME, cache_dir=tmp_path)
    assert first[0][0] != second[0][0]


def test_theme_is_part_of_the_cache_key(tmp_path):
    # geometry and panels are identical; only the theme differs. Two
    # themes must not share a sprite file even though the two rendered
    # images would look different (different ink colour).
    panels = [_panel("ann", 0, 0, shots_fired=1)]
    clean = overlay_sprites.write_sprite_sequence(
        [_state(panels)], GEOMETRY, theme=load_theme("clean"), cache_dir=tmp_path
    )
    splitsmith = overlay_sprites.write_sprite_sequence(
        [_state(panels)], GEOMETRY, theme=load_theme("splitsmith"), cache_dir=tmp_path
    )
    assert clean[0][0] != splitsmith[0][0]


def test_concat_list_repeats_the_final_entry(tmp_path):
    # The concat demuxer ignores the last entry's duration unless the
    # file is listed once more after it.
    panels = [_panel("ann", 0, 0)]
    sequence = overlay_sprites.write_sprite_sequence(
        [_state(panels, 0.0, 2.0)], GEOMETRY, theme=THEME, cache_dir=tmp_path
    )
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "sprites.txt")
    lines = [ln for ln in list_path.read_text().splitlines() if ln.strip()]
    assert lines[0].startswith("file ")
    assert lines[1] == "duration 2"
    assert lines[-1] == lines[0]


def test_materialize_font_writes_a_readable_file(tmp_path):
    path = overlay_text.materialize_font("splitsmith-mono", tmp_path)
    assert path.is_file()
    assert path.stat().st_size > 0
    assert path.parent == tmp_path


# --- strip entry text: none of the pixel-based tests above distinguish
# "+0.10" from "+-0.10" or "1 ANN" from "1 ANN +0.00", so these exercise
# the formatting helper directly.


def test_strip_entry_text_negative_delta_uses_explicit_sign():
    panel = _panel("ann", 0, 0, rank=2, delta_to_leader=-0.10)
    text = overlay_sprites._strip_entry_text(panel)
    assert text == "2 ANN -0.10"
    assert "+-" not in text


def test_strip_entry_text_positive_delta_uses_explicit_sign():
    panel = _panel("bo", 0, 1, rank=2, delta_to_leader=0.21)
    text = overlay_sprites._strip_entry_text(panel)
    assert text == "2 BO +0.21"


def test_strip_entry_text_leader_has_no_number():
    panel = _panel("ann", 0, 0, rank=1, delta_to_leader=0.0)
    assert overlay_sprites._strip_entry_text(panel) == "1 ANN"


def test_strip_entry_text_unranked_has_no_number():
    panel = _panel("cy", 1, 0, rank=None, delta_to_leader=None)
    assert overlay_sprites._strip_entry_text(panel) == "CY"


def test_negative_delta_through_render_state_does_not_collide_with_the_leader():
    # Finding 2 (review): the string-level tests above exercise
    # _strip_entry_text directly, which would miss a positioning or
    # encoding bug specific to the render path. Drive a negative delta
    # through the real render_state -> pixels path instead.
    panels = [
        _panel("ann", 0, 0, shots_fired=2, rank=1, delta_to_leader=0.0),
        _panel("bo", 0, 1, shots_fired=1, rank=2, delta_to_leader=-0.10),
    ]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    strip = image.crop(
        (0, GEOMETRY.canvas_height - GEOMETRY.strip_height, GEOMETRY.canvas_width, GEOMETRY.canvas_height)
    )
    slot_width = GEOMETRY.canvas_width // 2
    ann_slot = strip.crop((0, 0, slot_width, strip.height))
    bo_slot = strip.crop((slot_width, 0, slot_width * 2, strip.height))
    assert _has_ink(ann_slot)
    assert _has_ink(bo_slot)
    # The two entries are drawn in the same call and could in principle
    # bleed into each other's slot; confirm each stays inside its own,
    # which a stray extra glyph from a "+-0.10" formatting bug would be
    # likely to break given how tight two entries make the budget.
    ann_extent = _ink_extent(ann_slot)
    bo_extent = _ink_extent(bo_slot)
    assert ann_extent[1] < ann_slot.width
    assert bo_extent[0] > 0


# --- layout stress: 3x3 and 4x4 are first-class grid kinds
# (compare/layout.py routes 5-16 shooters to them), not extremes. A
# fixture has to actually pack a grid that size with present, firing
# tiles or it cannot express the collision the review found.

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


def _full_grid_state(rows: int, cols: int):
    """Every cell present and fired, with a mix of delta signs and
    magnitudes -- the shape that broke 3x3/4x4 in review, where a rank
    digit printed on top of the previous entry's delta."""
    n = rows * cols
    panels = []
    for i in range(n):
        row, col = divmod(i, cols)
        delta = 0.0 if i == 0 else round((i - n / 2) * 0.13, 2)
        panels.append(
            overlay_sprites.TilePanel(
                label=FULL_GRID_LABELS[i],
                row=row,
                col=col,
                present=True,
                shots_fired=i + 1,
                expected_shots=32,
                last_split=round(0.15 + 0.01 * i, 2),
                rank=i + 1,
                delta_to_leader=delta,
            )
        )
    return _state(panels), n


def _ink_extent(crop):
    """Leftmost/rightmost columns with any non-transparent pixel in
    ``crop`` (exclusive right, matching ``Image.getbbox``), or ``None``
    if the crop has no ink at all."""
    bbox = crop.getchannel("A").getbbox()
    if bbox is None:
        return None
    return bbox[0], bbox[2]


@pytest.mark.parametrize("rows,cols", GRID_SIZES)
@pytest.mark.parametrize("canvas_width,canvas_height", CANVAS_SIZES)
def test_strip_entries_never_overlap_on_a_full_grid(canvas_width, canvas_height, rows, cols):
    geometry = overlay_sprites.SpriteGeometry(
        canvas_width=canvas_width, canvas_height=canvas_height, rows=rows, cols=cols
    )
    state, n = _full_grid_state(rows, cols)
    image = overlay_sprites.render_state(state, geometry, theme=SPLITSMITH_THEME)
    strip = image.crop((0, canvas_height - geometry.strip_height, canvas_width, canvas_height))
    slot_width = canvas_width / n

    # Entries live in disjoint, contiguous slots. An entry whose ink
    # never reaches its own slot's edges cannot touch its neighbour's
    # ink either -- that containment is what "no two entries overlap"
    # reduces to when slots themselves are checked to tile the canvas
    # with no gaps and no overlap (true here: slot_width * n ==
    # canvas_width by construction).
    for index in range(n):
        slot = strip.crop((int(index * slot_width), 0, int((index + 1) * slot_width), strip.height))
        extent = _ink_extent(slot)
        assert extent is not None, (
            f"strip slot {index} of {n} has no ink at all "
            f"({rows}x{cols} grid, {canvas_width}x{canvas_height})"
        )
        left, right = extent
        assert left > 0, (
            f"strip slot {index} of {n} touches its left edge -- collides with the "
            f"previous entry ({rows}x{cols} grid, {canvas_width}x{canvas_height})"
        )
        assert right < slot.width, (
            f"strip slot {index} of {n} touches its right edge -- collides with the "
            f"next entry ({rows}x{cols} grid, {canvas_width}x{canvas_height})"
        )


@pytest.mark.parametrize("rows,cols", GRID_SIZES)
@pytest.mark.parametrize("canvas_width,canvas_height", CANVAS_SIZES)
def test_cell_text_stays_inside_its_own_cell_on_a_full_grid(canvas_width, canvas_height, rows, cols):
    geometry = overlay_sprites.SpriteGeometry(
        canvas_width=canvas_width, canvas_height=canvas_height, rows=rows, cols=cols
    )
    state, _ = _full_grid_state(rows, cols)
    image = overlay_sprites.render_state(state, geometry, theme=SPLITSMITH_THEME)
    for panel in state.panels:
        x0 = panel.col * geometry.cell_width
        y0 = panel.row * geometry.cell_height
        y1 = min(y0 + geometry.cell_height, canvas_height - geometry.strip_height)
        cell = image.crop((x0, y0, x0 + geometry.cell_width, y1))
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


def test_present_false_draws_nothing_even_with_shots_fired_and_split():
    # A filler tile from build_overlay_states always has shots_fired=0 and
    # last_split=None, so a fixture that only tries that combination can't
    # tell "we skip filler tiles" from "we skip tiles with nothing to
    # show" -- both look identical. Force shots_fired/last_split onto a
    # present=False panel so a removed ``present`` guard actually shows up.
    panels = [
        _panel(
            "dee",
            0,
            0,
            present=False,
            shots_fired=5,
            expected_shots=12,
            last_split=0.5,
            rank=1,
            delta_to_leader=0.0,
        )
    ]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    assert not _has_ink(_quadrant(image, GEOMETRY, 0, 0))
