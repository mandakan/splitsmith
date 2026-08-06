"""Structure tests for ``overlay_html``: the pure declaration-to-HTML half
of the box-engine stage summary (issue #683 amendment, Task 6R-1).

These assert structure, not pixels -- no browser runs in this file. Every
test is written to fail against a plausible wrong implementation: a
version that used inline styles instead of a real box model, that forgot
``overflow: hidden``, that let a role's size drift from ``CellScale``, or
that trusted a filler placement's caller to pass an empty group sequence
rather than enforcing it structurally.
"""

import re

import pytest

from splitsmith.compare.overlay_data import TileShot, TileStageData
from splitsmith.compare.overlay_sprites import SpriteGeometry, TilePlacement
from splitsmith.compare.overlay_summary import StagePlacing, _cell_groups
from splitsmith.overlay_html import cell_html, summary_html
from splitsmith.overlay_layout import Anchor, CellScale, Element, Flow, Group, Role
from splitsmith.overlay_theme import load_theme
from splitsmith.ui.project import StageScorecard

THEME = load_theme("clean")
SCALE = CellScale(
    identity=31,
    headline=41,
    verdict=51,
    detail=61,
    caption=13,
    live_primary=71,
    pad=9,
)


def _rule(html: str, selector: str) -> str:
    """The body of the CSS rule whose selector line is exactly
    ``selector`` (a literal class name, e.g. ``".cell"``), or raise if
    it isn't declared at all.

    Anchored to the start of a line so a lookup for ``".emphasis-muted"``
    matches its own standalone rule rather than the shared
    ``.emphasis-plain, .emphasis-muted { ... }`` line, which also ends in
    that literal text.
    """
    match = re.search(r"^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", html, re.MULTILINE)
    assert match is not None, f"no standalone CSS rule for {selector!r} in:\n{html}"
    return match.group(1)


# --- overflow: hidden is structural, not trusted -------------------------


def test_every_cell_carries_overflow_hidden():
    groups = (Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW, elements=(Element(Role.IDENTITY, "Anders"),)),)
    html = cell_html(groups, scale=SCALE, theme=THEME)
    rule = _rule(html, ".cell")
    assert "overflow: hidden" in rule or "overflow:hidden" in rule.replace(" ", "")


def test_a_filler_tile_cell_is_empty_of_text():
    """A filler placement (``present=False``) must render an empty cell
    regardless of what groups a caller hands it -- the invariant is
    enforced in ``summary_html`` itself, not trusted to the caller. A
    wrong implementation that just calls ``cell_html`` unconditionally
    would leak the shooter's name onto a tile they never occupied."""
    groups = (Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW, elements=(Element(Role.IDENTITY, "Ghost"),)),)
    placement = TilePlacement(label="Ghost", row=0, col=0, present=False)
    geometry = SpriteGeometry(canvas_width=320, canvas_height=180, rows=1, cols=1)
    doc = summary_html([(placement, groups)], geometry=geometry, scale=SCALE, theme=THEME)
    assert "Ghost" not in doc
    assert '<div class="cell"></div>' in doc


# --- role sizes come from CellScale, not from Python arithmetic ---------


@pytest.mark.parametrize(
    "role,expected",
    [
        (Role.IDENTITY, SCALE.identity),
        (Role.HEADLINE, SCALE.headline),
        (Role.VERDICT, SCALE.verdict),
        (Role.DETAIL, SCALE.detail),
        (Role.LIVE_PRIMARY, SCALE.live_primary),
    ],
)
def test_each_role_class_carries_its_cellscale_size(role, expected):
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, f".role-{role.value}")
    assert f"font-size: {expected}px" in rule


def test_caption_size_is_read_off_the_scale_not_the_role():
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".caption")
    assert f"font-size: {SCALE.caption}px" in rule


def test_a_different_scale_moves_the_same_role_class():
    """If a role's font-size were hardcoded rather than read from the
    ``scale`` argument, this would fail: a second, distinct CellScale
    must produce a distinct font-size for the same role."""
    other = CellScale(identity=99, headline=98, verdict=97, detail=96, caption=95, live_primary=94, pad=93)
    html_a = cell_html((), scale=SCALE, theme=THEME)
    html_b = cell_html((), scale=other, theme=THEME)
    assert f"font-size: {SCALE.identity}px" in _rule(html_a, ".role-identity")
    assert f"font-size: {other.identity}px" in _rule(html_b, ".role-identity")
    assert f"font-size: {SCALE.identity}px" not in _rule(html_b, ".role-identity")


# --- emphasis ------------------------------------------------------------


def test_plate_renders_a_filled_background():
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".emphasis-plate")
    r, g, b = THEME.accent
    assert f"background: rgb({r},{g},{b})" in rule


def test_muted_is_reduced_opacity_and_plain_is_not():
    html = cell_html((), scale=SCALE, theme=THEME)
    plain_and_muted = _rule(html, ".emphasis-plain, .emphasis-muted")
    muted_only = _rule(html, ".emphasis-muted")
    # Shared stroke/shadow/color live on the combined selector...
    assert "-webkit-text-stroke" in plain_and_muted
    # ...but only .emphasis-muted dims itself.
    assert "opacity" in muted_only
    assert "opacity" not in plain_and_muted


def test_plate_carries_no_stroke_or_shadow():
    """A plate brings its own contrast (a filled rectangle); a stroke
    around thin glyphs on top of it is a halo that eats the glyph -- see
    the module docstring's measured pixel numbers. A wrong implementation
    that applied the shared stroke/shadow rule to plates too would fail
    this."""
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".emphasis-plate")
    assert "text-stroke" not in rule
    assert "text-shadow" not in rule


# --- identity: ellipsis, but never to zero characters --------------------


def test_identity_gets_ellipsis():
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".role-identity")
    assert "text-overflow: ellipsis" in rule


def test_identity_never_shrinks_below_its_floor():
    """#617 shipped a bug where a widget ellipsized a note away to zero
    visible characters and the assertion never looked at what a viewer
    would actually see. The structural answer is a non-zero min-width
    floor on the identity element that CSS cannot shrink past."""
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".el-identity")
    match = re.search(r"min-width:\s*(\d+)ch", rule)
    assert match is not None, f"no min-width floor on .el-identity: {rule}"
    assert int(match.group(1)) > 0


# --- a tile with no audit and no scorecard: label only -------------------


def test_a_tile_with_no_audit_and_no_scorecard_emits_only_its_label():
    groups = _cell_groups(None, None, "Zoe")
    html = cell_html(groups, scale=SCALE, theme=THEME)
    values = re.findall(r'<span class="value[^"]*">([^<]*)</span>', html)
    assert values == ["Zoe"]


# --- escaping --------------------------------------------------------------


def test_a_name_containing_a_script_tag_is_escaped():
    hostile = "<script>alert(1)</script>"
    groups = (Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW, elements=(Element(Role.IDENTITY, hostile),)),)
    html = cell_html(groups, scale=SCALE, theme=THEME)
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_a_caption_containing_an_ampersand_is_escaped():
    groups = (
        Group(
            anchor=Anchor.BOTTOM_LEFT,
            flow=Flow.ROW,
            elements=(Element(Role.HEADLINE, "12.34", caption="A & B"),),
        ),
    )
    html = cell_html(groups, scale=SCALE, theme=THEME)
    assert "A & B" not in html
    assert "A &amp; B" in html


# --- fonts: bundled, no system face reachable -----------------------------


def test_both_bundled_fonts_are_declared_via_font_face():
    html = cell_html((), scale=SCALE, theme=THEME)
    assert html.count("@font-face") == 2
    assert "JetBrainsMono-Bold.ttf" in html
    assert "Antonio-VariableFont.ttf" in html
    assert "file://" in html


def test_the_cell_font_family_is_the_bundled_face():
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".cell")
    assert '"Splitsmith Mono"' in rule


# --- geometry: cell pixel size matches floor division, like xstack -------


def test_grid_lands_on_the_same_floor_divided_boundaries_as_xstack():
    # 1000 // 3 == 333, deliberately not evenly divisible, so a wrong
    # implementation using true division or canvas_width / cols directly
    # would produce a different (wrong) track size here.
    geometry = SpriteGeometry(canvas_width=1000, canvas_height=541, rows=1, cols=3)
    assert geometry.cell_width == 333
    assert geometry.cell_height == 541
    placement = TilePlacement(label="A", row=0, col=0, present=True)
    doc = summary_html([(placement, ())], geometry=geometry, scale=SCALE, theme=THEME)
    grid_rule = _rule(doc, ".grid")
    assert "repeat(3, 333px)" in grid_rule
    assert "repeat(1, 541px)" in grid_rule


def test_document_body_is_sized_to_the_canvas_and_background_transparent():
    geometry = SpriteGeometry(canvas_width=640, canvas_height=360, rows=1, cols=1)
    placement = TilePlacement(label="A", row=0, col=0, present=True)
    doc = summary_html([(placement, ())], geometry=geometry, scale=SCALE, theme=THEME)
    body_rule = _rule(doc, "html, body")
    assert "width: 640px" in body_rule
    assert "height: 360px" in body_rule
    assert "background: transparent" in body_rule


def test_a_tile_lands_in_its_declared_grid_row_and_column():
    placement = TilePlacement(label="A", row=2, col=1, present=True)
    geometry = SpriteGeometry(canvas_width=900, canvas_height=900, rows=3, cols=3)
    doc = summary_html([(placement, ())], geometry=geometry, scale=SCALE, theme=THEME)
    # CSS grid lines are 1-indexed; a 0-indexed TilePlacement.row/col of
    # (2, 1) must land on grid-row 3 / grid-column 2.
    assert "grid-row:3;grid-column:2;" in doc


# --- anchors and flow: driven by is_bottom / is_right / is_center --------


def test_two_groups_sharing_an_anchor_land_in_one_stacking_wrapper():
    """The identity/placing row and the counts row both anchor at
    BOTTOM_LEFT in real usage (see ``_cell_groups``). They must share one
    ``.anchor`` wrapper -- not two independent absolutely-positioned
    wrappers -- because only a shared flex container lets CSS compute
    their stacking offset instead of Python doing it by hand, which is
    the exact class of bug this module exists to retire."""
    band = Group(anchor=Anchor.BOTTOM_LEFT, flow=Flow.ROW, elements=(Element(Role.HEADLINE, "9.42"),))
    counts = Group(anchor=Anchor.BOTTOM_LEFT, flow=Flow.ROW, elements=(Element(Role.DETAIL, "A7 C2"),))
    html = cell_html((band, counts), scale=SCALE, theme=THEME)
    assert html.count('class="anchor anchor-bottom-left') == 1
    # Declaration order preserved inside the shared wrapper.
    band_pos = html.index("9.42")
    counts_pos = html.index("A7 C2")
    assert band_pos < counts_pos


def test_bottom_anchor_stacks_in_reverse_so_the_first_group_hugs_the_edge():
    band = Group(anchor=Anchor.BOTTOM_LEFT, flow=Flow.ROW, elements=(Element(Role.HEADLINE, "9.42"),))
    html = cell_html((band,), scale=SCALE, theme=THEME)
    assert "stack-reverse" in html
    rule = _rule(html, ".stack-reverse")
    assert "column-reverse" in rule


def test_top_anchor_stacks_normally():
    identity = Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW, elements=(Element(Role.IDENTITY, "A"),))
    html = cell_html((identity,), scale=SCALE, theme=THEME)
    assert "stack-normal" in html
    rule = _rule(html, ".stack-normal")
    assert rule.strip() == "flex-direction: column;"


@pytest.mark.parametrize(
    "anchor,expected_align",
    [
        (Anchor.TOP_LEFT, "align-left"),
        (Anchor.TOP_CENTER, "align-center"),
        (Anchor.TOP_RIGHT, "align-right"),
        (Anchor.BOTTOM_LEFT, "align-left"),
        (Anchor.BOTTOM_CENTER, "align-center"),
        (Anchor.BOTTOM_RIGHT, "align-right"),
    ],
)
def test_anchor_alignment_class_follows_is_right_and_is_center(anchor, expected_align):
    group = Group(anchor=anchor, flow=Flow.ROW, elements=(Element(Role.DETAIL, "x"),))
    html = cell_html((group,), scale=SCALE, theme=THEME)
    anchor_div = re.search(rf'<div class="anchor anchor-{anchor.value} \S+ (\S+)">', html)
    assert anchor_div is not None
    assert anchor_div.group(1) == expected_align


def test_flow_maps_to_flex_direction():
    row = Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW, elements=(Element(Role.DETAIL, "x"),))
    column = Group(anchor=Anchor.TOP_RIGHT, flow=Flow.COLUMN, elements=(Element(Role.DETAIL, "y"),))
    html = cell_html((row, column), scale=SCALE, theme=THEME)
    assert "flex-direction: row" in _rule(html, ".group.flow-row")
    assert "flex-direction: column" in _rule(html, ".group.flow-column")


# --- a small self-built roster, rendered structurally (no browser) -------


def _fixture_tile(**overrides):
    defaults = {
        "label": "Anders",
        "stage_number": 1,
        "shots": (TileShot(time_from_beep=0.4, split=0.4), TileShot(time_from_beep=0.9, split=0.5)),
        "stage_time_seconds": 9.42,
        "scorecard": StageScorecard(
            hit_factor=6.1,
            stage_pct=91.2,
            alphas=8,
            charlies=1,
            deltas=0,
            misses=0,
            no_shoots=0,
            procedurals=0,
            dq=False,
        ),
    }
    defaults.update(overrides)
    return TileStageData(**defaults)


def test_golden_roster_structure():
    """A small three-tile roster -- a clean run, a DQ, and a filler --
    covering the shapes ``_cell_groups`` actually produces. Rendered
    without a browser: this asserts DOM/CSS structure the same way a
    pixel test would assert on rendered bytes, so a regression in how
    groups compose into cells fails here before Task 7 ever launches
    Chromium."""
    clean = _fixture_tile(label="Anders")
    dq_scorecard = StageScorecard(dq=True)
    dq_tile = TileStageData(label="Bea", stage_number=1, scorecard=dq_scorecard)

    placements = (
        TilePlacement(label="Anders", row=0, col=0, present=True),
        TilePlacement(label="Bea", row=0, col=1, present=True),
        TilePlacement(label="Ghost", row=0, col=2, present=False),
    )
    cells = (
        (placements[0], _cell_groups(clean, StagePlacing(rank=1, total_ranked=2), "Anders")),
        (placements[1], _cell_groups(dq_tile, None, "Bea")),
        (placements[2], ()),
    )
    geometry = SpriteGeometry(canvas_width=960, canvas_height=540, rows=1, cols=3)
    doc = summary_html(cells, geometry=geometry, scale=SCALE, theme=THEME)

    # Every present tile's own numbers stay attached to its own name --
    # this is the whole point of the amendment. Cheap structural proxy:
    # each cell's own <div class="cell"> block contains only its own
    # figures, never the other shooter's.
    cell_blocks = re.findall(r'grid-column:(\d);">(.*?)</div></div>', doc)
    assert len(cell_blocks) >= 2

    assert "Anders" in doc
    assert "#1" in doc
    assert "91.2%" in doc
    assert "Bea" in doc
    assert "DQ" in doc
    # The DQ tile must not carry Anders's scoring, and vice versa.
    bea_section = doc[doc.index('grid-column:2;">') : doc.index('grid-column:3;">')]
    assert "91.2%" not in bea_section
    assert "Anders" not in bea_section

    # The filler tile (col 3) is empty.
    ghost_section = doc[doc.index('grid-column:3;">') :]
    assert '<div class="cell"></div>' in ghost_section
    assert "Ghost" not in doc
