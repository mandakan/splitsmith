"""Sprite rendering: where the ink lands, and the cache key."""

from splitsmith import overlay_text
from splitsmith.compare import overlay_sprites
from splitsmith.overlay_theme import load_theme

GEOMETRY = overlay_sprites.SpriteGeometry(canvas_width=1280, canvas_height=720, rows=2, cols=2)
THEME = load_theme("clean")


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
