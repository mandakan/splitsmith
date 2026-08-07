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
from splitsmith.compare.overlay_summary import _cell_groups
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
    stroke_width=3,
)


#: Matches a ``/* ... */`` CSS comment, including one that spans several
#: lines (``_style_rules`` writes several such blocks -- the ``.cell``
#: rule alone carries one that is over 20 lines and, not by coincidence,
#: quotes the very declarations the rule below it makes: ``overflow:
#: hidden`` and ``grid-template-rows: auto 1fr auto``). ``[^}]*`` used to
#: swallow those comments whole, so a rule's "body" as ``_rule`` returned
#: it could contain those literal strings even if the real declaration
#: list never did -- proven by setting ``overflow: visible`` on the real
#: declaration and watching ``test_every_cell_carries_overflow_hidden``
#: pass anyway, because the comment above it still said "hidden". Comments
#: are stripped from the whole document before any rule is looked up, so
#: what a test inspects is only ever the declaration list a browser would
#: actually apply.
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _rule(html: str, selector: str) -> str:
    """The body of the CSS rule whose selector line is exactly
    ``selector`` (a literal class name, e.g. ``".cell"``), or raise if
    it isn't declared at all.

    Anchored to the start of a line so a lookup for ``".emphasis-muted"``
    matches its own standalone rule rather than the shared
    ``.emphasis-plain, .emphasis-muted { ... }`` line, which also ends in
    that literal text.
    """
    stripped = _COMMENT_RE.sub("", html)
    match = re.search(r"^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", stripped, re.MULTILINE)
    assert match is not None, f"no standalone CSS rule for {selector!r} in:\n{stripped}"
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

#: Roles the fit-policy script (issue #683 F1) can shrink -- the ones
#: that only ever draw inside ``.anchor-middle-center`` -- read their
#: size through ``calc(var(--fit-scale, 1) * ...))`` rather than a bare
#: pixel value; see ``overlay_html._fit``. ``IDENTITY``/``VERDICT``/
#: ``LIVE_PRIMARY`` live in the identity row or the live overlay's own
#: corner anchors, outside that subtree, and stay literal pixels.
_FIT_SCALED_ROLES = {Role.HEADLINE, Role.DETAIL, Role.LABEL}


def _expected_font_size(px: int, *, fit_scaled: bool) -> str:
    if fit_scaled:
        return f"calc(var(--fit-scale, 1) * {px}px)"
    return f"{px}px"


@pytest.mark.parametrize(
    "role,expected",
    [
        (Role.IDENTITY, SCALE.identity),
        (Role.HEADLINE, SCALE.headline),
        (Role.VERDICT, SCALE.verdict),
        (Role.DETAIL, SCALE.detail),
        (Role.LABEL, SCALE.caption),
        (Role.LIVE_PRIMARY, SCALE.live_primary),
    ],
)
def test_each_role_class_carries_its_cellscale_size(role, expected):
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, f".role-{role.value}")
    size = _expected_font_size(expected, fit_scaled=role in _FIT_SCALED_ROLES)
    assert f"font-size: {size}" in rule


def test_caption_size_is_read_off_the_scale_not_the_role():
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".caption")
    assert f"font-size: {_expected_font_size(SCALE.caption, fit_scaled=True)}" in rule


def test_a_different_scale_moves_the_same_role_class():
    """If a role's font-size were hardcoded rather than read from the
    ``scale`` argument, this would fail: a second, distinct CellScale
    must produce a distinct font-size for the same role."""
    other = CellScale(
        identity=99,
        headline=98,
        verdict=97,
        detail=96,
        caption=95,
        live_primary=94,
        pad=93,
        stroke_width=92,
    )
    html_a = cell_html((), scale=SCALE, theme=THEME)
    html_b = cell_html((), scale=other, theme=THEME)
    assert f"font-size: {SCALE.identity}px" in _rule(html_a, ".role-identity")
    assert f"font-size: {other.identity}px" in _rule(html_b, ".role-identity")
    assert f"font-size: {SCALE.identity}px" not in _rule(html_b, ".role-identity")


# --- emphasis ------------------------------------------------------------


def test_plate_renders_a_filled_background():
    """Issue #683 Task 8: a plate's background reads ``accent_fill`` (the
    darker fill token, AA-large with ink text on top) -- not ``accent``,
    the raw identity red the theme reserves for structural use."""
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".emphasis-plate")
    r, g, b = THEME.accent_fill
    assert f"background: rgb({r},{g},{b})" in rule
    assert THEME.accent_fill != THEME.accent, "fixture theme must distinguish the two tokens"


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
    groups = _cell_groups(None, "Zoe", scale=SCALE, cell_width=640, cell_height=360)
    html = cell_html(groups, scale=SCALE, theme=THEME)
    values = re.findall(r'<span class="value[^"]*">([^<]*)</span>', html)
    assert values == ["Zoe"]


# --- escaping --------------------------------------------------------------


def test_a_name_containing_a_script_tag_is_escaped():
    """A hostile name must never reach the document as a live, executable
    ``<script>`` tag. This can no longer assert "no ``<script>`` tag
    anywhere in the document" wholesale -- issue #683 F1's fit-policy
    script (see ``overlay_html._fit_script``) is now a legitimate,
    always-present ``<script>`` in every document this module emits, and
    it carries no shooter data. The precise thing to check is that the
    hostile *payload* itself never appears unescaped -- which is a
    strictly narrower, not weaker, assertion than "no script tag at
    all": it still fails if ``escape()`` were ever dropped from
    :func:`~splitsmith.overlay_html._element_div`, and it no longer
    false-fails on the module's own, harmless script.
    """
    hostile = "<script>alert(1)</script>"
    groups = (Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW, elements=(Element(Role.IDENTITY, hostile),)),)
    html = cell_html(groups, scale=SCALE, theme=THEME)
    assert hostile not in html
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


def test_identity_draws_in_the_condensed_display_face_not_the_mono_one():
    """Both bundled faces are declared, but until issue #683 Task 7b only
    ``Splitsmith Mono`` was ever assigned to anything -- ``Splitsmith
    Display`` (Antonio, condensed) sat in the stylesheet unused. A
    competitor's name is the one string in a cell that is not a number
    and genuinely wants a condensed face: names run long, cells run
    narrow."""
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".role-identity")
    assert '"Splitsmith Display"' in rule


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
    """Uses ``BOTTOM_LEFT`` as a stand-in anchor to exercise the general
    stacking mechanism -- in real usage (``_cell_groups``) it is
    ``MIDDLE_CENTER`` that shares one anchor across several groups (the
    "Scoring"/"Splits" band-header labels, the counts row, the hit
    factor/time row, and the split-statistics grid, five groups deep).
    Whichever anchor is used, two groups sharing it must land in one
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
        (Anchor.MIDDLE_CENTER, "align-center"),
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


def test_middle_center_is_a_grid_row_not_absolutely_positioned():
    """The one anchor the redesigned stage summary needed and the old
    four-corner model did not have: it fills the flexible middle row of
    the cell's ``auto 1fr auto`` grid rather than pinning to an edge or
    centring on the cell's raw geometry -- see the ``.cell`` rule's own
    comment for why: a ``position: absolute`` centre ignores its
    neighbours and can be outgrown into an overlap, a grid row cannot."""
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".anchor-middle-center")
    assert "grid-row: 2" in rule
    assert "position" not in rule, f"middle-center must not be position:absolute: {rule}"


def test_the_cell_is_a_three_row_grid():
    """``auto minmax(0, 1fr) auto`` is what makes the top/middle/bottom
    rows use the cell's whole height without any of them able to overlap
    another. Not a bare ``1fr``: issue #683 F1 -- a bare ``1fr`` track's
    automatic minimum size is its content's own size, so it grows before
    the ``fr`` unit ever applies and only THEN gets clipped by
    ``overflow: hidden``, which is exactly what let a full stat block
    grow the middle row and lose whatever painted lowest. ``minmax(0,
    1fr)`` pins that automatic minimum to zero, so the track is a fixed
    fraction of the cell regardless of content -- the fit-policy script
    (``overlay_html._fit_script``) depends on that fixed size to tell
    overflowing from fitting at all."""
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".cell")
    assert "grid-template-rows: auto minmax(0, 1fr) auto" in rule


def test_top_and_bottom_center_are_also_grid_rows_not_absolute():
    """The whole TOP_CENTER/MIDDLE_CENTER/BOTTOM_CENTER trio of grid rows
    moves off ``position: absolute`` -- only the live overlay's corner
    anchors (never actually drawn through this module) keep it. Today
    only TOP_CENTER (identity) and MIDDLE_CENTER (the two bands) are
    used by ``_cell_groups``; BOTTOM_CENTER stays live infrastructure."""
    html = cell_html((), scale=SCALE, theme=THEME)
    top_rule = _rule(html, ".anchor-top-center")
    bottom_rule = _rule(html, ".anchor-bottom-center")
    assert "grid-row: 1" in top_rule
    assert "grid-row: 3" in bottom_rule
    assert "position" not in top_rule
    assert "position" not in bottom_rule


@pytest.mark.parametrize(
    "anchor", [Anchor.TOP_LEFT, Anchor.TOP_RIGHT, Anchor.BOTTOM_LEFT, Anchor.BOTTOM_RIGHT]
)
def test_corner_anchors_stay_absolutely_positioned(anchor):
    """The live overlay's own corners are untouched by the three-row
    grid -- they stay ``position: absolute``, out of grid flow
    entirely, so the grid costs them nothing."""
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, f".anchor-{anchor.value}")
    assert "position: absolute" in rule


def test_middle_center_stacks_normally_top_to_bottom():
    """Not a bottom anchor, so declaration order is reading order -- the
    stage summary's two bands (issue #683 Task 8) must not need to be
    declared in reverse the way ``BOTTOM_LEFT`` does. The shared
    stylesheet always declares both ``.stack-normal``/``.stack-reverse``
    rules regardless of what any one cell uses, so this checks which
    class the anchor *div* itself carries, not whether the word appears
    anywhere in the document."""
    group = Group(anchor=Anchor.MIDDLE_CENTER, flow=Flow.ROW, elements=(Element(Role.HEADLINE, "12.00"),))
    html = cell_html((group,), scale=SCALE, theme=THEME)
    anchor_div = re.search(r'<div class="anchor anchor-middle-center (\S+)', html)
    assert anchor_div is not None
    assert anchor_div.group(1) == "stack-normal"


def test_flow_maps_to_flex_direction():
    row = Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW, elements=(Element(Role.DETAIL, "x"),))
    column = Group(anchor=Anchor.TOP_RIGHT, flow=Flow.COLUMN, elements=(Element(Role.DETAIL, "y"),))
    html = cell_html((row, column), scale=SCALE, theme=THEME)
    assert "flex-direction: row" in _rule(html, ".group.flow-row")
    assert "flex-direction: column" in _rule(html, ".group.flow-column")


# --- the hairline scorecard rule (issue #683 Task 7b) ---------------------


def test_a_divider_group_renders_as_a_line_not_text():
    """A divider group carries no elements and must not be mapped through
    the normal elements-to-spans pipeline -- it is a plain rule, not a
    caption-less value."""
    divider = Group(anchor=Anchor.MIDDLE_CENTER, flow=Flow.ROW, elements=(), divider=True)
    html = cell_html((divider,), scale=SCALE, theme=THEME)
    assert '<div class="group divider"></div>' in html
    assert "<span" not in html


def test_the_divider_stretches_to_its_siblings_width_not_its_own():
    """The rule has no content of its own to size itself by -- it must
    override the anchor's own horizontal centring with ``align-self:
    stretch`` so it spans whatever width the counts/hero rows around it
    actually resolve to."""
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".group.divider")
    assert "align-self: stretch" in rule


def test_the_divider_is_a_hairline_not_a_second_heavy_element():
    """Restrained: a thin line, no border box, no stroke/shadow
    treatment -- everything else in the redesign stays quiet around it."""
    html = cell_html((), scale=SCALE, theme=THEME)
    rule = _rule(html, ".group.divider")
    match = re.search(r"height:\s*(\d+)px", rule)
    assert match is not None, f"no explicit height on the divider rule: {rule}"
    assert int(match.group(1)) <= 3, f"the divider is not a hairline: {match.group(1)}px"


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
    dq_tile = TileStageData(label="Bea", stage_number=1, stage_time_seconds=9.87, scorecard=dq_scorecard)

    placements = (
        TilePlacement(label="Anders", row=0, col=0, present=True),
        TilePlacement(label="Bea", row=0, col=1, present=True),
        TilePlacement(label="Ghost", row=0, col=2, present=False),
    )
    geometry = SpriteGeometry(canvas_width=960, canvas_height=540, rows=1, cols=3)
    cells = (
        (
            placements[0],
            _cell_groups(
                clean, "Anders", scale=SCALE, cell_width=geometry.cell_width, cell_height=geometry.cell_height
            ),
        ),
        (
            placements[1],
            _cell_groups(
                dq_tile, "Bea", scale=SCALE, cell_width=geometry.cell_width, cell_height=geometry.cell_height
            ),
        ),
        (placements[2], ()),
    )
    doc = summary_html(cells, geometry=geometry, scale=SCALE, theme=THEME)

    # Every present tile's own numbers stay attached to its own name --
    # this is the whole point of the amendment. The real isolation check
    # is the per-column slicing below; it is what can actually fail.
    assert "Anders" in doc
    assert "91.2" not in doc  # stage_pct is gone entirely (issue #683 Task 8)
    assert "6.10" in doc  # Anders's hit factor
    assert "Bea" in doc
    assert "DQ" in doc
    assert "9.87s" in doc  # Bea's stage time, still drawn even though her scoring is suppressed by the DQ
    # The DQ tile must not carry Anders's scoring, and vice versa.
    bea_section = doc[doc.index('grid-column:2;">') : doc.index('grid-column:3;">')]
    assert "6.10" not in bea_section
    assert "Anders" not in bea_section
    anders_section = doc[doc.index('grid-column:1;">') : doc.index('grid-column:2;">')]
    assert "9.87" not in anders_section

    # The filler tile (col 3) is empty.
    ghost_section = doc[doc.index('grid-column:3;">') :]
    assert '<div class="cell"></div>' in ghost_section
    assert "Ghost" not in doc
