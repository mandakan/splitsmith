"""The live per-tile overlay as declared content (issue #693).

``compare/overlay_live.py`` is the live half of what
``compare/overlay_summary.py`` is for the freeze-frame summary: it turns
a :class:`~splitsmith.compare.overlay_sprites.TilePanel` into declared
``Group``/``Element`` objects and lets ``overlay_html.grid_html`` plus an
injected rasterizer do every piece of measurement. These tests are about
the *declaration* -- what a tile says -- and about the cache and
rasterizer seam. Nothing here launches a browser; the pixel assertions
live in ``test_compare_overlay_live_render.py``.
"""

import pytest

from splitsmith.compare import overlay_live as live
from splitsmith.compare import overlay_sprites as sp
from splitsmith.overlay_layout import Anchor, CellScale, ColorToken, Role
from splitsmith.overlay_theme import load_theme

GEOMETRY = sp.SpriteGeometry(canvas_width=1280, canvas_height=720, rows=2, cols=2)
THEME = load_theme("splitsmith")


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
    return sp.TilePanel(**base)


def _elements(groups):
    return [element for group in groups for element in group.elements]


def _texts(groups):
    return [element.text for element in _elements(groups)]


# --- the counter ------------------------------------------------------


def test_the_shot_counter_is_declared_top_left_at_live_primary():
    groups = live.panel_groups(_panel(shots_fired=3, expected_shots=12))
    counter = next(g for g in groups if g.anchor is Anchor.TOP_LEFT)
    assert [e.text for e in counter.elements] == ["3/12"]
    assert counter.elements[0].role is Role.LIVE_PRIMARY


def test_the_counter_omits_the_denominator_when_no_shot_count_is_expected():
    groups = live.panel_groups(_panel(shots_fired=3, expected_shots=None))
    assert _texts(groups) == ["3"]


def test_a_tile_that_has_not_fired_declares_no_counter():
    assert live.panel_groups(_panel(shots_fired=0, expected_shots=12)) == ()


# --- the last split ---------------------------------------------------


def test_the_last_split_is_declared_bottom_center_in_the_split_colour():
    groups = live.panel_groups(_panel(shots_fired=2, last_split=0.28))
    split = next(g for g in groups if g.anchor is Anchor.BOTTOM_CENTER)
    assert [e.text for e in split.elements] == ["0.28s"]
    assert split.elements[0].color is ColorToken.SPLIT
    assert split.elements[0].role is Role.LIVE_PRIMARY


def test_a_split_is_rounded_to_two_decimals_the_way_it_always_was():
    groups = live.panel_groups(_panel(shots_fired=2, last_split=0.30666))
    assert "0.31s" in _texts(groups)


def test_a_tile_with_no_split_yet_declares_only_its_counter():
    groups = live.panel_groups(_panel(shots_fired=1, last_split=None))
    assert [g.anchor for g in groups] == [Anchor.TOP_LEFT]


def test_a_first_shot_declares_a_counter_but_no_split():
    # build_overlay_states emits shots_fired=1 with last_split=None: there
    # is no previous shot to measure against. A split of 0.00s here would
    # invent a number the run never produced.
    assert _texts(live.panel_groups(_panel(shots_fired=1))) == ["1"]


# --- absence is first class -------------------------------------------


def test_a_filler_tile_declares_nothing_even_with_shots_and_a_split():
    # A filler tile out of build_overlay_states always carries
    # shots_fired=0 and last_split=None, so a fixture that only tries that
    # combination cannot tell "we skip filler tiles" from "we skip tiles
    # with nothing to show". Forcing both on makes a dropped ``present``
    # guard visible.
    panel = _panel(present=False, shots_fired=5, expected_shots=12, last_split=0.5)
    assert live.panel_groups(panel) == ()


# --- the whole state as one document ----------------------------------


def test_state_html_places_each_tile_in_its_own_grid_cell():
    state = sp.OverlayState(
        start_seconds=0.0,
        duration_seconds=1.0,
        panels=(
            _panel("ann", 0, 0, shots_fired=3, expected_shots=12),
            _panel("bo", 1, 1, shots_fired=7, expected_shots=12, last_split=0.22),
        ),
    )
    html = live.state_html(state, GEOMETRY, theme=THEME)
    assert "grid-row:1;grid-column:1;" in html
    assert "grid-row:2;grid-column:2;" in html
    assert "3/12" in html and "7/12" in html and "0.22s" in html


def test_state_html_escapes_a_label_that_carries_markup():
    # A competitor's name is untrusted input. The live overlay does not
    # draw the label today, but the document is built from the same
    # escaping path either way and a future caller must not be the first
    # to discover it isn't escaped.
    state = sp.OverlayState(
        start_seconds=0.0,
        duration_seconds=1.0,
        panels=(_panel("<script>x</script>", 0, 0, shots_fired=1),),
    )
    assert "<script>x</script>" not in live.state_html(state, GEOMETRY, theme=THEME)


def test_state_html_sizes_the_document_to_the_canvas():
    state = sp.OverlayState(start_seconds=0.0, duration_seconds=1.0, panels=(_panel(shots_fired=1),))
    html = live.state_html(state, GEOMETRY, theme=THEME)
    assert f"width: {GEOMETRY.canvas_width}px; height: {GEOMETRY.canvas_height}px" in html


def test_type_size_comes_from_the_cell_not_the_canvas():
    # 3x3 and 4x4 are first-class grid kinds. A live_primary size read off
    # canvas height would be identical at every grid kind on one canvas;
    # read off the cell it shrinks as the grid gets denser. Asserted at
    # 4K, where the difference is genuinely expressible: ``live_primary``
    # is ``max(48, cell_h // 14)``, and at 1920x1080 that 48px absolute
    # floor binds at 2x2 (540//14 = 38) just as hard as at 4x4 (270//14 =
    # 19), so both grid kinds draw the counter at exactly 48px and the
    # test would pass against a canvas-driven size too. That floor doing
    # double duty as a type scale is issue #692, not something this port
    # changes -- the PIL renderer read the same field.
    state = sp.OverlayState(start_seconds=0.0, duration_seconds=1.0, panels=(_panel(shots_fired=1),))
    two = sp.SpriteGeometry(canvas_width=3840, canvas_height=2160, rows=2, cols=2)
    four = sp.SpriteGeometry(canvas_width=3840, canvas_height=2160, rows=4, cols=4)
    big = CellScale.for_cell(two.cell_height).live_primary
    small = CellScale.for_cell(four.cell_height).live_primary
    assert big > small, "fixture no longer expresses a cell-driven size difference"
    assert f"font-size: {big}px" in live.state_html(state, two, theme=THEME)
    assert f"font-size: {small}px" in live.state_html(state, four, theme=THEME)


# --- the clock and the sprites must agree on a typeface ---------------


@pytest.mark.parametrize("theme_name", ["splitsmith", "clean"])
def test_the_clock_and_the_sprites_resolve_the_same_bundled_face(theme_name):
    # The two halves of the overlay sit in the same cell: the counter is a
    # rasterized sprite and the running clock is an ffmpeg ``drawtext``
    # filter with its own font loader. Before #693 both went through
    # ``theme_font_face``, so they matched by construction. The sprites now
    # take their face from ``overlay_html``'s ``@font-face`` rules, which
    # declare the bundled faces unconditionally regardless of theme -- so
    # ``theme_font_face`` has to do the same or the ``clean`` theme draws a
    # counter and a clock in two different typefaces, with the clock's own
    # face varying by host (measured: DejaVu Sans Mono Bold on the dev box,
    # whatever a CI runner happens to carry elsewhere).
    face = sp.theme_font_face(load_theme(theme_name))
    assert face == "splitsmith-mono"


# --- the sprite cache and the rasterizer seam -------------------------


class _FakeRasterizer:
    """Records the documents it is handed and returns a distinct, valid
    PNG per distinct document, so a test can tell two sprites apart
    without launching a browser."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append((html, width, height))
        return b"\x89PNG\r\n\x1a\n" + str(len(self.calls)).encode()


def _state(panels, start=0.0, duration=1.0):
    return sp.OverlayState(start_seconds=start, duration_seconds=duration, panels=tuple(panels))


def test_the_sequence_writes_what_the_rasterizer_returned(tmp_path):
    fake = _FakeRasterizer()
    sequence = live.write_sprite_sequence(
        [_state([_panel(shots_fired=1)])], GEOMETRY, theme=THEME, cache_dir=tmp_path, rasterizer=fake
    )
    assert sequence[0][0].read_bytes() == b"\x89PNG\r\n\x1a\n1"


def test_the_rasterizer_is_asked_for_the_canvas_size(tmp_path):
    fake = _FakeRasterizer()
    live.write_sprite_sequence(
        [_state([_panel(shots_fired=1)])], GEOMETRY, theme=THEME, cache_dir=tmp_path, rasterizer=fake
    )
    _, width, height = fake.calls[0]
    assert (width, height) == (GEOMETRY.canvas_width, GEOMETRY.canvas_height)


def test_identical_panels_rasterize_once_and_share_one_file(tmp_path):
    fake = _FakeRasterizer()
    panels = [_panel(shots_fired=1, last_split=1.0)]
    sequence = live.write_sprite_sequence(
        [_state(panels, 0.0, 1.0), _state(panels, 1.0, 2.0)],
        GEOMETRY,
        theme=THEME,
        cache_dir=tmp_path,
        rasterizer=fake,
    )
    assert sequence[0][0] == sequence[1][0]
    assert len(fake.calls) == 1, "a repeated state must not pay for a second rasterization"
    assert len(list(tmp_path.glob("*.png"))) == 1


def test_durations_are_carried_through(tmp_path):
    fake = _FakeRasterizer()
    sequence = live.write_sprite_sequence(
        [_state([_panel()], 0.0, 1.5), _state([_panel(shots_fired=1)], 1.5, 2.5)],
        GEOMETRY,
        theme=THEME,
        cache_dir=tmp_path,
        rasterizer=fake,
    )
    assert [d for _, d in sequence] == [1.5, 2.5]


def test_different_content_gets_a_different_file(tmp_path):
    fake = _FakeRasterizer()
    sequence = live.write_sprite_sequence(
        [_state([_panel(shots_fired=1)]), _state([_panel(shots_fired=2)])],
        GEOMETRY,
        theme=THEME,
        cache_dir=tmp_path,
        rasterizer=fake,
    )
    assert sequence[0][0] != sequence[1][0]


def test_geometry_is_part_of_the_cache_key(tmp_path):
    fake = _FakeRasterizer()
    wide = sp.SpriteGeometry(canvas_width=1920, canvas_height=1080, rows=2, cols=2)
    first = live.write_sprite_sequence(
        [_state([_panel(shots_fired=1)])], GEOMETRY, theme=THEME, cache_dir=tmp_path, rasterizer=fake
    )
    second = live.write_sprite_sequence(
        [_state([_panel(shots_fired=1)])], wide, theme=THEME, cache_dir=tmp_path, rasterizer=fake
    )
    assert first[0][0] != second[0][0]


def test_theme_is_part_of_the_cache_key(tmp_path):
    fake = _FakeRasterizer()
    clean = live.write_sprite_sequence(
        [_state([_panel(shots_fired=1)])],
        GEOMETRY,
        theme=load_theme("clean"),
        cache_dir=tmp_path,
        rasterizer=fake,
    )
    splitsmith = live.write_sprite_sequence(
        [_state([_panel(shots_fired=1)])],
        GEOMETRY,
        theme=load_theme("splitsmith"),
        cache_dir=tmp_path,
        rasterizer=fake,
    )
    assert clean[0][0] != splitsmith[0][0]


# --- degradation: the overlay without a browser -----------------------


def test_the_absent_sequence_covers_every_state_for_its_own_duration(tmp_path):
    # No browser means no sprite content, but the running clock is an
    # ffmpeg drawtext filter that owes Chromium nothing. Keeping a real
    # (empty) sprite stream is what lets the clock still draw: the filter
    # graph reads the sprite concat list as an input, so removing it would
    # move stream indices and take the clock down with it.
    states = [_state([_panel(shots_fired=1)], 0.0, 1.5), _state([_panel(shots_fired=2)], 1.5, 2.5)]
    sequence = live.write_absent_sprite_sequence(states, GEOMETRY, cache_dir=tmp_path)
    assert [d for _, d in sequence] == [1.5, 2.5]


def test_the_absent_sequence_draws_nothing_at_all(tmp_path):
    from PIL import Image

    sequence = live.write_absent_sprite_sequence(
        [_state([_panel(shots_fired=3, expected_shots=12, last_split=0.2)])], GEOMETRY, cache_dir=tmp_path
    )
    with Image.open(sequence[0][0]) as image:
        assert image.mode == "RGBA"
        assert image.size == (GEOMETRY.canvas_width, GEOMETRY.canvas_height)
        # Fully transparent: max alpha across the whole canvas is zero.
        assert image.getextrema()[3][1] == 0


def test_the_absent_sequence_writes_one_file_for_the_whole_stage(tmp_path):
    # Every state is the same nothing, so a 30-shot stage must not write
    # 30 identical transparent PNGs.
    states = [_state([_panel(shots_fired=i)], float(i), 1.0) for i in range(30)]
    sequence = live.write_absent_sprite_sequence(states, GEOMETRY, cache_dir=tmp_path)
    assert len({path for path, _ in sequence}) == 1
    assert len(list(tmp_path.glob("*.png"))) == 1


def test_the_absent_sequence_does_not_collide_with_a_rendered_one(tmp_path):
    # Both write into the same content-addressed cache directory for the
    # whole run. A blank canvas sharing a name with a real sprite would
    # blank out a stage that rendered fine.
    fake = _FakeRasterizer()
    rendered = live.write_sprite_sequence(
        [_state([_panel(shots_fired=1)])], GEOMETRY, theme=THEME, cache_dir=tmp_path, rasterizer=fake
    )
    absent = live.write_absent_sprite_sequence(
        [_state([_panel(shots_fired=1)])], GEOMETRY, cache_dir=tmp_path
    )
    assert rendered[0][0] != absent[0][0]


def test_the_absent_sequence_is_geometry_specific(tmp_path):
    wide = sp.SpriteGeometry(canvas_width=1920, canvas_height=1080, rows=2, cols=2)
    first = live.write_absent_sprite_sequence([_state([_panel()])], GEOMETRY, cache_dir=tmp_path)
    second = live.write_absent_sprite_sequence([_state([_panel()])], wide, cache_dir=tmp_path)
    assert first[0][0] != second[0][0]


def test_a_rasterizer_failure_is_not_swallowed(tmp_path):
    # The whole overlay depends on this now, not just the summary's text.
    # ``render_grid_mp4`` degrades on a *missing browser* up front; a
    # browser that dies mid-render is a different thing and must surface.
    class _Boom:
        def png(self, html: str, *, width: int, height: int) -> bytes:
            raise RuntimeError("browser died")

    with pytest.raises(RuntimeError, match="browser died"):
        live.write_sprite_sequence(
            [_state([_panel(shots_fired=1)])],
            GEOMETRY,
            theme=THEME,
            cache_dir=tmp_path,
            rasterizer=_Boom(),
        )
