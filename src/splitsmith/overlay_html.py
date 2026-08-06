"""Declaration to HTML: the pure half of the box-engine stage summary.

See ``docs/superpowers/plans/2026-08-06-overlay-composition-seam-amendment.md``
for why this exists. In short: the previous stage summary hand-rolled its
own text fitter (``compare/overlay_summary.py``'s ``_fit_group_scale`` /
``_draw_group``) and three review rounds found the same class of defect
three times over -- content escaping the cell it was declared in, up to
one shooter's placing and stage percentage drawing inside the *next*
shooter's cell. A real box model (a browser's CSS engine) solves that
categorically rather than arithmetically: this module turns a cell's
declared :class:`~splitsmith.overlay_layout.Group`/:class:`Element` tuple
into an HTML fragment with ``overflow: hidden`` on every cell, and lets
CSS do all cumulative-width/height bounding. No Python code in this
module measures text or decides a size beyond reading it off
:class:`CellScale` -- that arithmetic is exactly what kept reappearing as
a defect.

**This module is pure.** No browser, no Playwright import, no file I/O
beyond resolving a path string for ``@font-face`` (see
:func:`_font_face_url`) -- nothing here opens a font, measures a glyph or
shells out. It is unit-testable with nothing but the declaration objects
from ``overlay_layout``, ``overlay_theme`` and
``compare/overlay_sprites``. Rasterizing the HTML this module returns is
``overlay_raster.py``'s job (Task 6R-2), a separate module so that only
one of the two needs a browser.

Design rules, each answering one of the old fitter's defects:

- **``overflow: hidden`` on every ``.cell``.** This is the fix for all
  three review-round defects at once: a ``height_budget`` that was read
  but never enforced, a bottom-anchored group spilling upward into the
  cell above, and a ``ROW`` group with no cumulative width bound. None of
  those can happen here -- nothing a descendant does can paint outside
  its own cell, full stop.
- **Cell pixel size comes from ``SpriteGeometry.cell_width`` /
  ``cell_height``** (floor division, matching ``mp4_grid._cell_size``),
  laid out as a single CSS grid at canvas size so the HTML grid lands on
  exactly the integer boundaries the ffmpeg ``xstack`` graph uses.
- **A filler tile (``present=False``) gets an empty cell.** It is not a
  shooter; text over black would imply a competitor who isn't there.
  Enforced in :func:`summary_html` regardless of what groups a caller
  hands it for a filler placement -- the same defensive posture as
  ``overlay_summary._draw_cell``'s own ``if not placement.present:
  return``.
- **A tile with no audit and no scorecard renders only its label.** This
  falls out of ``overlay_summary._cell_groups`` unchanged (it returns
  just the identity group in that case) -- nothing here has to special
  case it.
- **``Role`` maps to a CSS class carrying a ``font-size`` read straight
  off ``CellScale``.** No scale ladder, no per-element width measurement
  loop -- the box the browser lays out is the fit.
- **``Emphasis.PLATE`` is a filled accent background** with padding and
  ink text and *no* stroke (a stroke around thin glyphs on top of a
  filled plate is a halo that eats the glyph -- the plate already brings
  its own contrast). ``PLAIN``/``MUTED`` get ink text with a CSS
  stroke+shadow analogue of ``overlay_text._draw_text_with_shadow``
  (``paint-order: stroke fill`` plus ``-webkit-text-stroke`` and
  ``text-shadow``), and ``MUTED`` additionally drops opacity.
- **``IDENTITY`` gets ``text-overflow: ellipsis``** so a long name still
  identifies rather than spilling -- but never shrinks its box below
  ``min-width: 3ch``, so it can never ellipsize down to nothing. #617
  shipped a bug where a UI element ellipsized a note away to zero visible
  characters and the assertion passed because it never looked at what a
  viewer would actually see; the floor here is the structural answer to
  that.
- **Fonts are ``@font-face``, pointed at a ``file://`` URL naming the
  bundled TTFs** under ``src/splitsmith/data/fonts/`` -- never a data
  URI. The two faces are 74 KB and 274 KB; base64 would inflate that by
  roughly a third in every stage's HTML for no benefit, since the
  rasterizer (Task 6R-2) always runs the browser against this same
  filesystem -- a ``file://`` URL costs one path resolution and no bytes
  read by *this* module, and needs no network stack when Chromium opens
  it locally. No system font is ever reachable: both faces are declared
  unconditionally regardless of theme, which is a deliberate change from
  the old PIL renderer (which let the ``clean`` theme fall through to
  system font discovery) -- determinism across machines matters more here
  than matching that theme's old font choice.
- **Every string is escaped.** A competitor's name is untrusted input --
  see :func:`_element_div`.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape
from importlib.resources import files
from pathlib import Path

from .compare.overlay_sprites import SpriteGeometry, TilePlacement
from .overlay_layout import Anchor, CellScale, Element, Flow, Group, Role
from .overlay_theme import OverlayTheme

RGB = tuple[int, int, int]

#: Bundled TTFs this module's ``@font-face`` rules point at. Both are
#: declared unconditionally -- see the module docstring's fonts bullet.
_FONT_FILES: dict[str, str] = {
    "mono": "JetBrainsMono-Bold.ttf",
    "display": "Antonio-VariableFont.ttf",
}

#: A shortened ``IDENTITY`` still identifies at this floor. Chosen as a
#: handful of characters -- enough that "Mathias" ellipsizing to "Mat..."
#: still reads as a name fragment rather than nothing. See #617 in the
#: module docstring.
_IDENTITY_MIN_WIDTH_CH = 3


def _font_face_url(filename: str) -> str:
    """Resolve a bundled font file to a ``file://`` URL.

    ``importlib.resources.files`` returns the real on-disk path for this
    project's normal (unzipped) install layout -- the same assumption
    ``overlay_text.materialize_font`` and ``overlay_theme._load_splitsmith``
    make elsewhere. Resolving a path is not reading the font's bytes; no
    glyph, table or byte of the TTF is touched here, only its name on
    disk. If the resource is ever missing, this still returns a URL --
    Chromium failing to load it is Task 6R-2/6R-3's degradation path to
    handle, not a concern of a function that never opens a browser.
    """
    resource = files("splitsmith.data").joinpath("fonts").joinpath(filename)
    return Path(str(resource)).resolve().as_uri()


def _rgb(color: RGB) -> str:
    r, g, b = color
    return f"{r},{g},{b}"


def _style_rules(*, scale: CellScale, theme: OverlayTheme) -> str:
    """The shared stylesheet for one cell's declared content.

    Emitted once per document by :func:`summary_html` (all cells in one
    grid share one ``CellScale``, resolved from one shared cell height)
    and once per call by :func:`cell_html`, so a single cell's fragment
    is independently valid HTML a test can inspect without also holding
    a whole document.
    """
    mono_url = _font_face_url(_FONT_FILES["mono"])
    display_url = _font_face_url(_FONT_FILES["display"])
    ink = _rgb(theme.ink)
    stroke = _rgb(theme.stroke)
    shadow = _rgb(theme.shadow)
    accent = _rgb(theme.accent)
    return f"""
@font-face {{
  font-family: "Splitsmith Mono";
  src: url("{mono_url}") format("truetype");
  font-weight: 700;
}}
@font-face {{
  font-family: "Splitsmith Display";
  src: url("{display_url}") format("truetype");
  font-weight: 400 700;
}}
.cell {{
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  box-sizing: border-box;
  font-family: "Splitsmith Mono", monospace;
}}
.anchor {{
  position: absolute;
  display: flex;
  gap: 0.35em;
  max-width: calc(100% - 2 * {scale.pad}px);
  min-width: 0;
}}
.anchor-top-left      {{ top: {scale.pad}px; left: {scale.pad}px; }}
.anchor-top-center    {{ top: {scale.pad}px; left: 50%; transform: translateX(-50%); }}
.anchor-top-right     {{ top: {scale.pad}px; right: {scale.pad}px; }}
.anchor-bottom-left   {{ bottom: {scale.pad}px; left: {scale.pad}px; }}
.anchor-bottom-center {{ bottom: {scale.pad}px; left: 50%; transform: translateX(-50%); }}
.anchor-bottom-right  {{ bottom: {scale.pad}px; right: {scale.pad}px; }}
.stack-normal  {{ flex-direction: column; }}
.stack-reverse {{ flex-direction: column-reverse; }}
.anchor.align-left   {{ align-items: flex-start; }}
.anchor.align-center {{ align-items: center; }}
.anchor.align-right  {{ align-items: flex-end; }}
.group {{ display: flex; gap: 0.4em; min-width: 0; }}
.group.flow-row    {{ flex-direction: row; align-items: baseline; }}
.group.flow-column {{ flex-direction: column; }}
.group.flow-column.align-left   {{ align-items: flex-start; }}
.group.flow-column.align-center {{ align-items: center; }}
.group.flow-column.align-right  {{ align-items: flex-end; }}
.el {{
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
  min-width: 0;
}}
.el-identity {{
  flex: 1 1 auto;
  min-width: {_IDENTITY_MIN_WIDTH_CH}ch;
}}
.value {{
  display: block;
  white-space: nowrap;
  overflow: hidden;
}}
.caption {{
  display: block;
  font-size: {scale.caption}px;
  color: rgb({ink});
  opacity: 0.7;
  paint-order: stroke fill;
  -webkit-text-stroke: 0.08em rgb({stroke});
  text-shadow: 0.05em 0.05em 0.09em rgb({shadow});
}}
.role-identity     {{ font-size: {scale.identity}px; text-overflow: ellipsis; }}
.role-headline      {{ font-size: {scale.headline}px; }}
.role-verdict        {{ font-size: {scale.verdict}px; }}
.role-detail           {{ font-size: {scale.detail}px; }}
.role-live-primary      {{ font-size: {scale.live_primary}px; }}
.emphasis-plain, .emphasis-muted {{
  color: rgb({ink});
  paint-order: stroke fill;
  -webkit-text-stroke: 0.09em rgb({stroke});
  text-shadow: 0.06em 0.06em 0.1em rgb({shadow});
}}
.emphasis-muted {{ opacity: 0.68; }}
.emphasis-plate {{
  background: rgb({accent});
  color: rgb({ink});
  padding: 0.15em 0.35em;
  border-radius: 0.15em;
  display: inline-block;
}}
""".strip()


def _anchor_classes(anchor: Anchor) -> str:
    """Positioning + stacking classes for one anchor wrapper.

    Driven entirely by ``Anchor.is_bottom`` / ``is_right`` / ``is_center``
    per the amendment: ``stack-reverse`` on a bottom anchor makes the
    *first declared* group render nearest the bottom edge with later
    groups stacking upward (flex ``column-reverse`` with source order
    achieves exactly that), and the ``align-*`` class centers or
    right-aligns each group's own box within the stack.
    """
    classes = ["anchor", f"anchor-{anchor.value}"]
    classes.append("stack-reverse" if anchor.is_bottom else "stack-normal")
    if anchor.is_right:
        classes.append("align-right")
    elif anchor.is_center:
        classes.append("align-center")
    else:
        classes.append("align-left")
    return " ".join(classes)


def _group_classes(group: Group) -> str:
    """``flex-direction`` from ``Flow``, plus horizontal alignment for a
    ``COLUMN`` group's own stacked lines (a ``ROW`` group's horizontal
    position is entirely the anchor wrapper's job -- see
    :func:`_anchor_classes` -- so it keeps ``align-items: baseline`` for
    vertical alignment between differently sized elements instead)."""
    classes = ["group", f"flow-{group.flow.value}"]
    if group.flow is Flow.COLUMN:
        if group.anchor.is_right:
            classes.append("align-right")
        elif group.anchor.is_center:
            classes.append("align-center")
        else:
            classes.append("align-left")
    return " ".join(classes)


def _element_div(element: Element) -> str:
    """One :class:`Element` as a ``.el`` block: an optional caption line
    above its value, both escaped -- a competitor's name is untrusted
    input and may contain ``<``, ``&`` or quotes."""
    el_classes = "el el-identity" if element.role is Role.IDENTITY else "el"
    role_class = f"role-{element.role.value}"
    emphasis_class = f"emphasis-{element.emphasis.value}"
    caption_html = ""
    if element.caption is not None:
        caption_html = f'<span class="caption">{escape(element.caption)}</span>'
    value_html = f'<span class="value {role_class} {emphasis_class}">{escape(element.text)}</span>'
    return f'<div class="{el_classes}">{caption_html}{value_html}</div>'


def _group_div(group: Group) -> str:
    elements_html = "".join(_element_div(element) for element in group.elements)
    return f'<div class="{_group_classes(group)}">{elements_html}</div>'


def _anchor_div(anchor: Anchor, members: Sequence[Group]) -> str:
    groups_html = "".join(_group_div(group) for group in members)
    return f'<div class="{_anchor_classes(anchor)}">{groups_html}</div>'


def _cell_div(groups: Sequence[Group]) -> str:
    """The ``<div class="cell">...</div>`` markup for one present tile,
    with no wrapping ``<style>`` -- shared by :func:`cell_html` (which
    wraps it with its own stylesheet so a single cell is independently
    testable) and :func:`summary_html` (which emits the stylesheet once
    for the whole document rather than once per cell).

    Groups are bucketed by anchor, preserving each anchor's first
    occurrence order across ``groups`` -- multiple groups sharing one
    anchor land in a single wrapper div (see :func:`_anchor_div`) so CSS
    stacks them, rather than in independent wrappers that would need
    Python to compute their offsets from one another.
    """
    buckets: dict[Anchor, list[Group]] = {}
    for group in groups:
        buckets.setdefault(group.anchor, []).append(group)
    anchors_html = "".join(_anchor_div(anchor, members) for anchor, members in buckets.items())
    return f'<div class="cell">{anchors_html}</div>'


def cell_html(groups: Sequence[Group], *, scale: CellScale, theme: OverlayTheme) -> str:
    """One cell's declared content as a self-contained HTML fragment.

    Includes its own ``<style>`` block, so this is valid to drop anywhere
    (a test, a future single-shooter port) without also needing a whole
    document -- :func:`summary_html` calls the same building blocks but
    emits the stylesheet once for the whole grid instead of once per
    cell.
    """
    return f"<style>{_style_rules(scale=scale, theme=theme)}</style>\n{_cell_div(groups)}"


def summary_html(
    cells: Sequence[tuple[TilePlacement, Sequence[Group]]],
    *,
    geometry: SpriteGeometry,
    scale: CellScale,
    theme: OverlayTheme,
) -> str:
    """The whole canvas-sized stage summary as one HTML document.

    One CSS grid at canvas size, ``geometry.rows x geometry.cols``, each
    track exactly ``geometry.cell_width`` / ``cell_height`` pixels (floor
    division, matching ``mp4_grid._cell_size``) so the grid lands on the
    same integer boundaries the ffmpeg ``xstack`` filter graph uses -- a
    mismatch here would drift the overlay off its tile.

    A filler tile (``placement.present`` is ``False``) always renders an
    empty cell, regardless of what ``groups`` a caller passed for it --
    it is not a shooter, so text over black would imply a competitor who
    isn't there. This mirrors ``overlay_summary._draw_cell``'s own
    ``if not placement.present: return`` rather than trusting every
    caller to pass an empty sequence for a filler placement.

    ``html``/``body`` are sized to the canvas exactly and left
    background-transparent: the rasterizer (Task 6R-2/6R-3) alpha-
    composites the resulting screenshot over an already-composed still,
    so an opaque background here would paint over that footage.
    """
    style = _style_rules(scale=scale, theme=theme)
    grid_style = (
        ".grid {\n"
        "position: absolute; top: 0; left: 0; display: grid;\n"
        f"grid-template-columns: repeat({geometry.cols}, {geometry.cell_width}px);\n"
        f"grid-template-rows: repeat({geometry.rows}, {geometry.cell_height}px);\n"
        "}\n"
        "html, body {\n"
        "margin: 0; padding: 0;\n"
        f"width: {geometry.canvas_width}px; height: {geometry.canvas_height}px;\n"
        "background: transparent; overflow: hidden;\n"
        "}"
    )
    body_cells: list[str] = []
    for placement, groups in cells:
        inner = _cell_div(groups) if placement.present else '<div class="cell"></div>'
        body_cells.append(
            f'<div style="grid-row:{placement.row + 1};grid-column:{placement.col + 1};">{inner}</div>'
        )
    return (
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8"><title>stage summary</title>'
        f"<style>{style}\n{grid_style}</style>"
        "</head>"
        f'<body><div class="grid">{"".join(body_cells)}</div></body></html>'
    )
