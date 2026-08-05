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
    }
    base.update(kwargs)
    return overlay_sprites.TilePanel(**base)


def _state(panels, start=0.0, duration=1.0):
    return overlay_sprites.OverlayState(start_seconds=start, duration_seconds=duration, panels=tuple(panels))


def _quadrant(image, geometry, row, col):
    """The tile's own cell. Every pixel of it belongs to that tile: the
    sprite draws nothing outside the cells at all."""
    x0 = col * geometry.cell_width
    y0 = row * geometry.cell_height
    return image.crop((x0, y0, x0 + geometry.cell_width, y0 + geometry.cell_height))


def _has_ink(image) -> bool:
    return image.getextrema()[3][1] > 0


def test_sprite_is_canvas_sized_rgba():
    image = overlay_sprites.render_state(_state([_panel("ann", 0, 0, shots_fired=1)]), GEOMETRY, theme=THEME)
    assert image.mode == "RGBA"
    assert image.size == (GEOMETRY.canvas_width, GEOMETRY.canvas_height)


def test_ink_lands_in_the_firing_tiles_own_cell():
    panels = [
        _panel("ann", 0, 0, shots_fired=3, last_split=0.25),
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
        _panel("ann", 0, 0, shots_fired=2),
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


def test_the_whole_canvas_is_blank_before_anyone_fires():
    # With the delta strip gone there is no canvas-level furniture left:
    # a state where nobody has fired draws nothing anywhere, so the
    # sprite is a fully transparent PNG. That is the intended behaviour,
    # and it is why every "the sprite reached the pixels" assertion has
    # to sample a state where a counter or a split actually exists.
    panels = [_panel("ann", 0, 0), _panel("bo", 0, 1)]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    assert not _has_ink(image)


def test_the_split_label_is_drawn_inside_the_bottom_row_cell():
    # The strip used to reserve a band across the bottom of the canvas
    # and bottom-anchored cell content was pushed up to clear it. Nothing
    # reserves that band now, so a bottom-row tile's split label must
    # land inside the lower part of its own cell.
    panels = [_panel("cy", 1, 0, shots_fired=4, last_split=0.31)]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    cell_bottom_half = image.crop(
        (0, GEOMETRY.cell_height + GEOMETRY.cell_height // 2, GEOMETRY.cell_width, GEOMETRY.canvas_height)
    )
    assert _has_ink(cell_bottom_half)


def test_identical_panels_reuse_one_file(tmp_path):
    panels = [_panel("ann", 0, 0, shots_fired=1, last_split=1.0)]
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
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "sprites.txt", frame_rate=(30, 1))
    lines = [ln for ln in list_path.read_text().splitlines() if ln.strip()]
    assert lines[0].startswith("file ")
    assert lines[1] == "option framerate 30/1"
    assert lines[2] == "duration 2"
    assert lines[-2] == lines[0]


# --- boundary quantisation ------------------------------------------
#
# The sprite is a stepped image and the clock is a per-frame drawtext
# expression. If a state boundary lands between two output frames the two
# halves of the overlay disagree at every shot -- measured before this
# was fixed, the frame at a shot's own time showed the clock reading
# 0.70 with no counter, and the counter arrived a frame later against
# 0.73. Boundaries therefore have to land on frames that exist.


def _boundaries(durations):
    out, elapsed = [], 0.0
    for duration in durations:
        out.append(elapsed)
        elapsed += duration
    return out


def test_every_boundary_lands_on_a_whole_output_frame():
    # Shot times are millisecond-grained, so raw boundaries sit wherever
    # they land: 1.712 and 2.401 are both mid-frame at 30fps.
    durations = overlay_sprites.quantize_durations([1.712, 0.689, 0.4], frame_rate=(30, 1))
    for boundary in _boundaries(durations):
        frames = boundary * 30
        assert abs(frames - round(frames)) < 1e-6, f"{boundary} is not on a frame"


def test_a_boundary_already_on_a_frame_is_not_pushed_a_frame_late():
    # 8.3 is frame 249 exactly, but ``8.3 * 30`` is 249.00000000000003 in
    # binary floating point, so a bare ceil moves it to frame 250 -- the
    # boundary lands a whole frame late, which is the exact defect the
    # quantiser exists to remove. Of the 20000 millisecond positions in a
    # 20s stage, 8.3, 16.1 and 16.6 are the three that do this at 30fps;
    # 1.7 (the reviewer's example) is exactly 51.0 and would not catch it.
    durations = overlay_sprites.quantize_durations([8.3, 0.3], frame_rate=(30, 1))
    assert _boundaries(durations)[1] == pytest.approx(8.3, abs=1e-9)


def test_a_mid_frame_boundary_rounds_up_never_to_nearest():
    # 1.712s is 51.36 frames. Rounding to nearest would put it at frame
    # 51 (1.700s) -- the counter would step up on a frame shown 12ms
    # before the shot was fired.
    durations = overlay_sprites.quantize_durations([1.712, 0.5], frame_rate=(30, 1))
    assert _boundaries(durations)[1] == pytest.approx(52 / 30, abs=1e-9)


def test_quantising_preserves_the_total_exactly():
    raw = [1.712, 0.689, 0.4, 1.011]
    durations = overlay_sprites.quantize_durations(raw, frame_rate=(30, 1))
    assert sum(durations) == pytest.approx(sum(raw), abs=1e-9)


def test_a_fractional_rate_quantises_to_its_own_frames():
    durations = overlay_sprites.quantize_durations([1.712, 0.5], frame_rate=(30000, 1001))
    boundary = _boundaries(durations)[1]
    frames = boundary * 30000 / 1001
    assert abs(frames - round(frames)) < 1e-6


def test_two_events_inside_one_frame_collapse_the_superseded_state():
    # 0.705s and 0.710s are both inside frame 22 at 30fps (0.7333s). The
    # first state can never be displayed, so it collapses to zero length.
    durations = overlay_sprites.quantize_durations([0.705, 0.005, 0.29], frame_rate=(30, 1))
    assert durations[1] == pytest.approx(0.0, abs=1e-9)
    assert durations[0] > 0.0 and durations[2] > 0.0


def test_the_final_state_never_gets_a_negative_duration():
    # 0.99s falls inside the *last* frame of a 0.991s segment at 30fps,
    # so rounding its boundary up names frame 30 -- a frame the segment
    # does not contain. The final duration came out -0.009, which
    # write_concat_list drops silently: the last state's sprite is never
    # written, the trailing repeat shows the previous one, and that
    # shot's counter increment never reaches the screen.
    durations = overlay_sprites.quantize_durations([0.99, 0.001], frame_rate=(30, 1))
    assert durations[-1] > 0.0, f"final state has a non-positive duration: {durations}"
    assert sum(durations) == pytest.approx(0.991, abs=1e-9)


def test_the_final_state_is_clamped_to_the_last_frame_that_exists():
    # Same shape, a different pair: the boundary is pulled back to frame
    # 29 (the last one a 0.985s segment at 30fps has) rather than pushed
    # to frame 30, which nothing would ever render.
    durations = overlay_sprites.quantize_durations([0.98, 0.005], frame_rate=(30, 1))
    assert durations[-1] > 0.0, f"final state has a non-positive duration: {durations}"
    assert sum(durations) == pytest.approx(0.985, abs=1e-9)
    assert _boundaries(durations)[1] == pytest.approx(29 / 30, abs=1e-9)


def test_a_shot_in_the_last_frame_still_reaches_the_concat_list(tmp_path):
    # The pixels half of the two tests above: the surviving entry has to
    # be the state carrying the *second* shot, not the first.
    two_shots = overlay_sprites.write_sprite_sequence(
        [_state([_panel("ann", 0, 0, shots_fired=2)], 0.0, 1.0)],
        GEOMETRY,
        theme=THEME,
        cache_dir=tmp_path,
    )
    sequence = overlay_sprites.write_sprite_sequence(
        [
            _state([_panel("ann", 0, 0, shots_fired=1)], 0.0, 0.99),
            _state([_panel("ann", 0, 0, shots_fired=2)], 0.99, 0.001),
        ],
        GEOMETRY,
        theme=THEME,
        cache_dir=tmp_path,
    )
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "s.txt", frame_rate=(30, 1))
    files = [ln for ln in list_path.read_text().splitlines() if ln.startswith("file ")]
    assert (
        files[-1] == f"file '{two_shots[0][0].resolve()}'"
    ), "the last shot's sprite is not the one left on screen at the end of the segment"
    assert files[-2] == files[-1]


def test_a_collapsed_state_is_dropped_from_the_list_not_written_as_zero(tmp_path):
    # A ``duration 0`` entry is a state no frame can ever show, and the
    # demuxer's handling of it is not something to rely on.
    sequence = overlay_sprites.write_sprite_sequence(
        [
            _state([_panel("ann", 0, 0)], 0.0, 0.705),
            _state([_panel("ann", 0, 0, shots_fired=1)], 0.705, 0.005),
            _state([_panel("ann", 0, 0, shots_fired=2)], 0.710, 0.29),
        ],
        GEOMETRY,
        theme=THEME,
        cache_dir=tmp_path,
    )
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "s.txt", frame_rate=(30, 1))
    text = list_path.read_text()
    assert "duration 0\n" not in text
    durations = [ln.split()[1] for ln in text.splitlines() if ln.startswith("duration")]
    assert len(durations) == 2


def test_a_collapsed_state_never_loses_a_shot(tmp_path):
    # The state that survives is the *later* one, so both shots are still
    # counted. Losing the surviving state instead would drop a shot off
    # the tile's counter permanently.
    two_shots = overlay_sprites.write_sprite_sequence(
        [_state([_panel("ann", 0, 0, shots_fired=2)], 0.0, 1.0)],
        GEOMETRY,
        theme=THEME,
        cache_dir=tmp_path,
    )
    sequence = overlay_sprites.write_sprite_sequence(
        [
            _state([_panel("ann", 0, 0)], 0.0, 0.705),
            _state([_panel("ann", 0, 0, shots_fired=1)], 0.705, 0.005),
            _state([_panel("ann", 0, 0, shots_fired=2)], 0.710, 0.29),
        ],
        GEOMETRY,
        theme=THEME,
        cache_dir=tmp_path,
    )
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "s.txt", frame_rate=(30, 1))
    files = [ln for ln in list_path.read_text().splitlines() if ln.startswith("file ")]
    # last real entry + the trailing repeat, both the 2-shot sprite
    assert files[-1] == f"file '{two_shots[0][0].resolve()}'"
    assert files[-2] == files[-1]


def test_the_list_pins_the_demuxer_framerate_on_every_entry(tmp_path):
    # Without this the concat demuxer opens each PNG through image2 at its
    # default 25fps, takes its time base from that, and snaps every
    # boundary to 1/25s -- a grid on which 1/30s boundaries do not exist.
    sequence = overlay_sprites.write_sprite_sequence(
        [
            _state([_panel("ann", 0, 0)], 0.0, 1.6),
            _state([_panel("ann", 0, 0, shots_fired=1)], 1.6, 0.4),
        ],
        GEOMETRY,
        theme=THEME,
        cache_dir=tmp_path,
    )
    list_path = overlay_sprites.write_concat_list(sequence, tmp_path / "s.txt", frame_rate=(30000, 1001))
    lines = [ln for ln in list_path.read_text().splitlines() if ln.strip()]
    files = [i for i, ln in enumerate(lines) if ln.startswith("file ")]
    assert files, "no entries written"
    for index in files:
        assert lines[index + 1] == "option framerate 30000/1001"


def test_fonts_are_loaded_once_per_face_and_size():
    # The width-fitting loops ask for a font once per size step per panel
    # per state, and each miss re-reads the bundled TTF off disk. One
    # 3x3 state at 3840x2160 asks 374 times for 17 distinct fonts.
    overlay_sprites._font_at.cache_clear()
    first = overlay_sprites._scaled_font(SPLITSMITH_THEME, 48)
    assert overlay_sprites._scaled_font(SPLITSMITH_THEME, 48) is first
    assert overlay_sprites._scaled_font(SPLITSMITH_THEME, 46) is not first


def test_materialize_font_writes_a_readable_file(tmp_path):
    path = overlay_text.materialize_font("splitsmith-mono", tmp_path)
    assert path.is_file()
    assert path.stat().st_size > 0
    assert path.parent == tmp_path


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
    """Every cell present and fired, so every cell has a counter and a
    split label to fit -- the shape a narrow cell has to survive."""
    n = rows * cols
    panels = []
    for i in range(n):
        row, col = divmod(i, cols)
        panels.append(
            overlay_sprites.TilePanel(
                label=FULL_GRID_LABELS[i],
                row=row,
                col=col,
                present=True,
                shots_fired=i + 1,
                expected_shots=32,
                last_split=round(0.15 + 0.01 * i, 2),
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
def test_cell_text_stays_inside_its_own_cell_on_a_full_grid(canvas_width, canvas_height, rows, cols):
    geometry = overlay_sprites.SpriteGeometry(
        canvas_width=canvas_width, canvas_height=canvas_height, rows=rows, cols=cols
    )
    state, _ = _full_grid_state(rows, cols)
    image = overlay_sprites.render_state(state, geometry, theme=SPLITSMITH_THEME)
    for panel in state.panels:
        x0 = panel.col * geometry.cell_width
        y0 = panel.row * geometry.cell_height
        cell = image.crop((x0, y0, x0 + geometry.cell_width, y0 + geometry.cell_height))
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
        )
    ]
    image = overlay_sprites.render_state(_state(panels), GEOMETRY, theme=THEME)
    assert not _has_ink(_quadrant(image, GEOMETRY, 0, 0))
