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
  Enforced in :func:`grid_html` regardless of what groups a caller
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
- **``Element.color`` (:class:`~splitsmith.overlay_layout.ColorToken`)
  overrides an element's ink with a named theme colour** -- ``.tok-*``
  classes, one per token, declared between the emphasis rules and
  ``.emphasis-plate`` so a plate always wins its own colour back (see the
  comment above those rules). This is how the stage summary's hit/fault
  counts read by point value (issue #683 Task 7): green for a full-value
  hit, amber for a low one, red for anything worth -10, all at the same
  size -- colour carries the meaning that size used to carry unevenly.
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
  than matching that theme's old font choice. **This only works if the
  rasterizer navigates to the HTML as a real ``file://`` document rather
  than injecting it via ``page.set_content()``** -- the fonts silently
  fall back to a system face with no error under ``set_content()``. See
  :func:`_font_face_url` for the measured numbers and the data-URI
  fallback if a caller cannot navigate to a real document.
- **Every string is escaped.** A competitor's name is untrusted input --
  see :func:`_element_div`.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape
from importlib.resources import files
from pathlib import Path

from .compare.overlay_sprites import SpriteGeometry, TilePlacement
from .overlay_layout import MIN_FONT_SIZE, Anchor, CellScale, ColorToken, Element, Flow, Group, Role
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

    **The rasterizer MUST navigate to this HTML as a real ``file://``
    document -- ``page.goto(f"file://{path}")`` -- not hand it to
    Chromium via ``page.set_content()``.** Measured on the dev host,
    rendering the same document both ways and comparing a text run's
    width against the browser's own fallback monospace:

    - ``file://`` fonts + ``page.goto(file://...)``: custom face loads,
      measured width 552.00px against a 553.89px fallback -- distinct,
      confirming the bundled TTF is what actually rendered.
    - ``file://`` fonts + ``page.set_content()``: **552.00 == 553.89 --
      identical to the fallback.** The ``@font-face`` silently fails to
      load (``set_content()`` gives the document an opaque/``about:blank``
      origin that a local file URL cannot resolve against) and Chromium
      falls back to whatever monospace the host happens to have, with no
      error, no warning and no exception -- just different, host-
      dependent pixels. A 1.89px width difference is not something any
      "did it render some text" check would catch; it can only be caught
      by knowing to check for it, which is why this is written down here
      rather than left to be rediscovered.

    If a caller cannot navigate to a real ``file://`` document (e.g. the
    HTML is generated in memory and never touches disk), the alternative
    is a base64 data URI instead of this function -- confirmed to load
    correctly under ``set_content()`` too -- at the cost of inlining both
    bundled faces (JetBrains Mono Bold + Antonio, roughly 356 KB base64-
    encoded) into every document this module returns, rather than the one
    path string a ``file://`` URL costs. See the module docstring's fonts
    bullet for why ``file://`` was chosen as the default; this docstring
    is the record of the constraint that choice carries.
    """
    resource = files("splitsmith.data").joinpath("fonts").joinpath(filename)
    return Path(str(resource)).resolve().as_uri()


def _rgb(color: RGB) -> str:
    r, g, b = color
    return f"{r},{g},{b}"


def _fit(px: int) -> str:
    """A length that shrinks with the cell's ``--fit-scale`` custom
    property (issue #683 F1's fit policy) instead of a bare pixel value.

    ``--fit-scale`` is unset (falls back to ``1``) everywhere except
    ``.anchor-middle-center``, where the fit-policy script (see
    :func:`_fit_script`) sets it directly once, after the browser lays
    the cell out once at full size and finds it does not fit its
    ``minmax(0, 1fr)`` track. Every font-size and gap inside that one
    subtree that calls this instead of writing ``{px}px`` shrinks
    together, uniformly, when that happens -- one CSS variable rather
    than Python recomputing each of them. Elements outside that subtree
    (the identity row, the live overlay's own corner anchors) never see
    the property set, so ``calc(var(--fit-scale, 1) * ...)`` resolves to
    exactly ``{px}px`` for them, unchanged.
    """
    return f"calc(var(--fit-scale, 1) * {px}px)"


def _style_rules(*, scale: CellScale, theme: OverlayTheme) -> str:
    """The shared stylesheet for one cell's declared content.

    Emitted once per document by :func:`grid_html` (all cells in one
    grid share one ``CellScale``, resolved from one shared cell height)
    and once per call by :func:`cell_html`, so a single cell's fragment
    is independently valid HTML a test can inspect without also holding
    a whole document.
    """
    mono_url = _font_face_url(_FONT_FILES["mono"])
    display_url = _font_face_url(_FONT_FILES["display"])
    ink = _rgb(theme.ink)
    ink_2 = _rgb(theme.ink_2)
    rule_color = _rgb(theme.rule)
    stroke = _rgb(theme.stroke)
    shadow = _rgb(theme.shadow)
    accent = _rgb(theme.accent)
    accent_fill = _rgb(theme.accent_fill)
    accent_text = _rgb(theme.accent_text)
    split = _rgb(theme.split)
    split_good = _rgb(theme.split_good)
    muted = _rgb(theme.muted)
    # ``.group``'s row gutter used to be a flat ``0.4em``, computed against
    # whatever font-size the browser inherits down to the flex container
    # itself -- which no rule here ever sets, so it stayed pinned near the
    # ~16px default regardless of how large ``scale``'s roles actually
    # render. At ``scale.headline`` sizes that is an all but invisible
    # gap: the Scoring band's hit-factor/time row, "5.12HF 12.34s", read
    # as one run-on number rather than two figures with daylight between
    # them. Deriving the gutter from ``scale.pad`` (the one number
    # already scaled off cell height) fixes that at its root instead of
    # hand-tuning an em value per call site.
    row_gutter = max(8, scale.pad // 2)
    column_gutter = max(4, scale.pad // 5)
    # The scorecard rule's own breathing room -- deliberately its own
    # number rather than reusing ``column_gutter``: it separates two
    # whole groups (counts from the hero result), not two elements inside
    # one group, and it must read as a pause, not just another row gap.
    # The divider itself is unused by the current (bands) design -- see
    # ``Group.divider``'s docstring -- but stays live infrastructure for
    # any future caller that wants it.
    rule_margin = max(6, scale.pad // 3)
    # The gap between the cell's top row (identity) and the vertically
    # centred stack below it (issue #683 Task 8's two bands). The mock's
    # own ``.cell`` sets this as a flex ``gap``; a CSS grid needs the
    # equivalent ``row-gap`` on the same three rows the rest of this
    # stylesheet already lays out as ``grid-template-rows``.
    top_row_gap = scale.pad
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
  /* The bands stage summary (issue #683 Task 8) needs the whole cell
     height distributed between an auto-height identity row, a middle
     band that takes whatever is left over, and an auto-height (today,
     empty) bottom row -- ``grid-template-rows: auto minmax(0, 1fr)
     auto``. This is a *default* on every cell, not something only the
     summary opts into: ``TOP_LEFT``/``TOP_RIGHT``/``BOTTOM_LEFT``/
     ``BOTTOM_RIGHT`` (the live overlay's own corner anchors, never
     actually drawn through this module -- see the module docstring)
     stay ``position: absolute`` below, which removes them from grid
     flow entirely, so this costs them nothing. See
     ``.anchor-top-center`` / ``.anchor-middle-center`` /
     ``.anchor-bottom-center`` for why this replaces the middle rail's
     old ``top: 50%; transform: translateY(-50%)``: that centred on the
     cell's own geometry with no regard for its neighbours, so a cell
     with a tall enough middle rail could paint over a row below it. A
     grid row can never overlap another one.

     ``minmax(0, 1fr)`` rather than a bare ``1fr``: a grid track's
     automatic minimum size defaults to ``auto`` -- effectively the
     content's own min-content size -- which means a bare ``1fr`` track
     GROWS to fit whatever its content wants before the ``fr`` unit ever
     applies, and only THEN does this rule's own ``overflow: hidden``
     clip the excess against the cell's fixed 100% height. That grow-
     then-clip sequence is issue #683 F1: nothing ever measured whether
     the middle band's content fit its fair share of the cell, so
     ``overflow: hidden`` always clipped whatever painted lowest --
     which, by ``_cell_groups``'s own declaration order, was always the
     Splits band and a lit penalty plate, while the zero-valued counts
     above them survived. ``minmax(0, 1fr)`` pins the *automatic*
     minimum to ``0`` instead of ``auto``, so the track's resolved height
     is genuinely the leftover space -- fixed, independent of how much
     the middle band wants. ``.anchor-middle-center`` itself needs no
     matching override to honour that: it is ``align-self: center`` with
     ``height: auto``, sized to its own content rather than stretching to
     fill the track, so nothing about the item can reassert the track's
     own auto-minimum one level down (measured directly: adding
     ``min-height: 0; overflow: hidden`` to that rule changed nothing --
     same ``scrollHeight``/``clientHeight`` either way -- which is why
     that rule carries neither). Only with a *fixed* track can
     ``overlay_html``'s fit-policy script (below) tell overflowing from
     fitting by comparing the band's ``scrollHeight`` against that fixed
     height, and shrink or drop content accordingly instead of always
     clipping the bottom of an ever-growing box.
  */
  display: grid;
  grid-template-columns: 1fr;
  grid-template-rows: auto minmax(0, 1fr) auto;
  row-gap: {top_row_gap}px;
}}
.anchor {{
  display: flex;
  gap: {_fit(row_gutter)};
  max-width: calc(100% - 2 * {scale.pad}px);
  min-width: 0;
}}
.anchor-top-left      {{ position: absolute; top: {scale.pad}px; left: {scale.pad}px; }}
.anchor-top-right     {{ position: absolute; top: {scale.pad}px; right: {scale.pad}px; }}
.anchor-bottom-left   {{ position: absolute; bottom: {scale.pad}px; left: {scale.pad}px; }}
.anchor-bottom-right  {{ position: absolute; bottom: {scale.pad}px; right: {scale.pad}px; }}
/* The bands summary's own anchors (issue #683 Task 8): real grid items
   (not ``position: absolute``), one per row, so the browser -- not this
   module's Python -- guarantees they never overlap. Only ``TOP_CENTER``
   and ``MIDDLE_CENTER`` are ever declared by ``_cell_groups`` today;
   ``BOTTOM_CENTER`` stays live infrastructure for a possible future
   caller. */
.anchor-top-center {{
  grid-row: 1;
  grid-column: 1;
  justify-self: center;
  align-self: start;
  padding-top: {scale.pad}px;
}}
.anchor-middle-center {{
  grid-row: 2;
  grid-column: 1;
  justify-self: center;
  align-self: center;
}}
.anchor-bottom-center {{
  grid-row: 3;
  grid-column: 1;
  justify-self: center;
  align-self: end;
  padding-bottom: {scale.pad}px;
}}
/* Issue #683 Task 8's two-band stage summary reuses TOP_CENTER's and
   MIDDLE_CENTER's *rows* -- the grid-row placement that keeps them from
   ever overlapping -- but left-aligned and spanning the cell's full
   width rather than shrink-wrapped and centred (the Splits band's
   four-column grid in particular needs the width). ``justify-self:
   stretch`` plus an explicit ``padding-left``/``padding-right`` replaces
   the base rule's ``justify-self: center`` and its shared, shrink-wrap-
   oriented ``max-width`` -- higher specificity (two classes) wins over
   the single-class base rules above. */
.anchor-top-center.align-left,
.anchor-middle-center.align-left {{
  justify-self: stretch;
  max-width: none;
  box-sizing: border-box;
  padding-left: {scale.pad}px;
  padding-right: {scale.pad}px;
}}
.stack-normal  {{ flex-direction: column; }}
.stack-reverse {{ flex-direction: column-reverse; }}
.anchor.align-left   {{ align-items: flex-start; }}
.anchor.align-center {{ align-items: center; }}
.anchor.align-right  {{ align-items: flex-end; }}
.group {{ display: flex; min-width: 0; }}
.group.flow-row    {{ flex-direction: row; align-items: baseline; gap: {_fit(row_gutter)}; flex-wrap: wrap; }}
.group.flow-column {{ flex-direction: column; gap: {_fit(column_gutter)}; }}
.group.flow-column.align-left   {{ align-items: flex-start; }}
.group.flow-column.align-center {{ align-items: center; }}
.group.flow-column.align-right  {{ align-items: flex-end; }}
/* The split-statistics band (issue #683 Task 8): Best/Avg/Worst/Draw as
   an evenly spaced row across the whole cell width, not shrink-wrapped
   to the left like every other group. ``align-self: stretch`` overrides
   the anchor's own ``align-items: flex-start`` for this one flex item,
   the same trick the divider rule below uses for the same reason.
   ``grid-template-columns`` is set inline per group (``_group_div``) --
   the column count is the number of elements, which this shared rule
   cannot know. */
.group.flow-grid {{
  display: grid;
  align-self: stretch;
  gap: {_fit(column_gutter)};
}}
/* The scorecard hairline (see ``Group.divider``): a plain line, not
   text. ``align-self: stretch`` overrides the anchor's own
   ``align-items: center`` for this one flex item, so it is stretched to
   the width the anchor's column already resolved from its *other*
   (unstretched, text-sized) children -- the widest row above or below it
   -- rather than carrying any width of its own. Not currently drawn by
   ``overlay_summary._cell_groups`` -- the approved bands design (issue
   #683 Task 8) uses band headers instead of a rule -- but kept live for
   a future caller, reading ``theme.rule`` (the design system's own
   dedicated hairline token, ``--color-rule`` -- see
   ``scripts/build_overlay_theme.py``) rather than a semi-transparent
   ink hack now that this module has somewhere to put it. */
.group.divider {{
  display: block;
  align-self: stretch;
  height: 2px;
  background: rgb({rule_color});
  margin: {rule_margin}px 0;
  border: none;
  padding: 0;
}}
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
/* A value's smaller, muted inline suffix (see ``Element.unit`` --
   ``"12.00"`` plus a smaller ``"HF"``). ``em`` here resolves against the
   ``.value`` span it is nested inside, so it scales with whatever role
   that value actually draws at rather than needing its own size input. */
.unit {{
  font-size: 0.45em;
  color: rgb({muted});
  -webkit-text-stroke: 0;
  margin-left: 0.25em;
}}
/* Labels (issue #683 Task 8: "SCORING"/"SPLITS" band headers, and each
   split statistic's own "BEST"/"AVG"/"WORST"/"DRAW" caption) are
   deliberately the one text in the cell with **no stroke** -- a stroke
   around glyphs this thin at this size is a halo that eats them (the
   same lesson ``Emphasis.PLATE`` already learned about accent text at
   small sizes) -- text-shadow only, plus the design's own uppercase /
   letter-spacing treatment and the ``ink_2`` token instead of an opacity
   hack on ``ink``. ``.caption`` (a label drawn *above* a value) never
   carries an ``.emphasis-*`` class at all, so it needs no override; a
   standalone ``Role.LABEL`` element goes through the same value pipeline
   every other role does and so *does* carry ``.emphasis-plain``'s own
   stroke -- ``.role-label`` must therefore be declared after
   ``.emphasis-plain``/``.emphasis-muted`` below to win the property back
   (same source-order mechanism the ``.tok-*`` block's own comment
   documents). */
.caption {{
  display: block;
  font-size: {_fit(scale.caption)};
  color: rgb({ink_2});
  text-transform: uppercase;
  letter-spacing: 0.08em;
  text-shadow: 0 1px 2px rgb({stroke});
}}
.role-identity     {{
  font-size: {scale.identity}px;
  text-overflow: ellipsis;
  /* The one place ``Antonio`` (condensed display) draws instead of the
     mono figure face: a competitor's name is the one string on the
     cell that is not a number, and condensed genuinely matters here --
     names run long and cells run narrow. */
  font-family: "Splitsmith Display", "Splitsmith Mono", sans-serif;
  font-weight: 600;
}}
/* ``.role-headline`` and ``.role-detail`` -- the two roles the middle
   band ever draws a bare value at -- read their size through
   :func:`_fit`, not a literal ``{{scale.headline}}px``: issue #683 F1's
   fit-policy script shrinks them (and ``.caption``/``.role-label``
   above, and every gap in this subtree) together via ``--fit-scale``
   when the band does not fit its ``minmax(0, 1fr)`` track at full size.
   ``.role-identity``/``.role-verdict``/``.role-live-primary`` stay
   literal pixels: the identity row and the live overlay's own corner
   anchors sit outside ``.anchor-middle-center`` and the fit policy never
   touches them. */
.role-headline      {{ font-size: {_fit(scale.headline)}; }}
.role-verdict        {{ font-size: {scale.verdict}px; }}
.role-detail           {{ font-size: {_fit(scale.detail)}; }}
.role-live-primary      {{ font-size: {scale.live_primary}px; }}
.emphasis-plain, .emphasis-muted {{
  color: rgb({ink});
  paint-order: stroke fill;
  -webkit-text-stroke: {scale.stroke_width}px rgb({stroke});
  /* NOT converted to a flat px shadow the way -webkit-text-stroke was
     (161b174, one stroke weight per cell rather than one per font
     size -- see CellScale.stroke_width). The mock's own flat reference
     is ``0 {{cell_h//360}}px {{cell_h//180}}px``, and doing the same
     here honestly is a real API change, not a formula tweak: this
     function only receives ``scale: CellScale``, which does not carry
     the raw cell height (it exists only as a component baked into
     several already-divided fields), and this rule is shared by
     ``cell_html`` -- which never receives a cell height at all -- and
     ``grid_html``, which does (``geometry.cell_height``). Backing a
     height out of e.g. ``scale.pad`` is unreliable: ``_summary_scale``
     and ``CellScale.for_cell`` derive ``pad`` with two DIFFERENT
     formulas depending on the caller, so which one produced a given
     ``scale`` is not recoverable here. Left em-relative rather than
     threading a new required parameter through a public function
     (``cell_html``) other modules and tests already call by this
     signature, for a shadow that is already small relative to the
     stroke fix's own halo-eating problem -- the stroke, not the shadow,
     was what the mock measurement (8.4%/8.9% vs 15-18%, see
     ``CellScale.stroke_width``) was actually about.
  */
  text-shadow: 0.06em 0.06em 0.1em rgb({shadow});
}}
.emphasis-muted {{ opacity: 0.68; }}
/* See the ``.caption`` comment above: this must win back "no stroke,
   ink_2, no shadow-via-emphasis" over ``.emphasis-plain``'s stroke,
   which is why it is declared here rather than beside ``.role-*``
   above. */
.role-label {{
  font-size: {_fit(scale.caption)};
  color: rgb({ink_2});
  text-transform: uppercase;
  letter-spacing: 0.08em;
  -webkit-text-stroke: 0;
  text-shadow: 0 1px 2px rgb({stroke});
}}
/* Colour tokens (see ``ColorToken``) override an element's ink -- but
   never a plate's, which always wants ink-on-accent for contrast. These
   rules must stay between ``.emphasis-plain``/``.emphasis-muted`` (whose
   default colour they are meant to override) and ``.emphasis-plate``
   below (which must win back the colour property for a plated element,
   or an accent-toned count on an accent-coloured plate would vanish).
   Source order is the whole mechanism here: all these selectors have
   equal specificity, so this is not optional ordering. */
.tok-ink        {{ color: rgb({ink}); }}
.tok-split      {{ color: rgb({split}); }}
.tok-split-good {{ color: rgb({split_good}); }}
.tok-accent     {{ color: rgb({accent}); }}
.tok-accent-text {{ color: rgb({accent_text}); }}
.emphasis-plate {{
  /* Issue #683 Task 8: the plate's own background reads ``accent_fill``
     (the darker fill token, AA-large with ink text on top) -- not
     ``accent``, the raw identity red the theme reserves for structural
     use. ``accent_fill``/``accent_text`` existed on the theme since
     c25bb64 but this rule (and ``.tok-accent-text`` above) were never
     wired to either until now. */
  background: rgb({accent_fill});
  color: rgb({ink});
  padding: 0.15em 0.35em;
  border-radius: 0.15em;
  display: inline-block;
}}
""".strip()


def _anchor_classes(anchor: Anchor, align: str | None) -> str:
    """Positioning + stacking classes for one anchor wrapper.

    Driven by ``Anchor.is_bottom`` / ``is_right`` / ``is_center`` per the
    amendment: ``stack-reverse`` on a bottom anchor makes the *first
    declared* group render nearest the bottom edge with later groups
    stacking upward (flex ``column-reverse`` with source order achieves
    exactly that), and the ``align-*`` class centers or right-aligns each
    group's own box within the stack.

    ``align`` (issue #683 Task 8, see ``Group.align``) overrides what the
    anchor alone would imply -- ``None`` falls back to that default, which
    is the only thing every anchor did before this parameter existed.
    """
    classes = ["anchor", f"anchor-{anchor.value}"]
    classes.append("stack-reverse" if anchor.is_bottom else "stack-normal")
    resolved = align
    if resolved is None:
        if anchor.is_right:
            resolved = "right"
        elif anchor.is_center:
            resolved = "center"
        else:
            resolved = "left"
    classes.append(f"align-{resolved}")
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


def _color_class(color: ColorToken | None) -> str:
    """The ``.tok-*`` class for an explicit :class:`ColorToken`, or ``""``
    when an element draws its emphasis's own ink (the default -- see
    :attr:`Element.color`). ``SPLIT_GOOD``'s underscore becomes a hyphen
    because CSS class names conventionally don't carry underscores; the
    token's own ``.value`` (used for theme attribute lookup elsewhere)
    stays untouched."""
    if color is None:
        return ""
    return f"tok-{color.value.replace('_', '-')}"


def _element_div(element: Element) -> str:
    """One :class:`Element` as a ``.el`` block: an optional caption line
    above its value and an optional unit suffix nested inside it, all
    escaped -- a competitor's name is untrusted input and may contain
    ``<``, ``&`` or quotes.

    Carries ``data-drop-priority`` when :attr:`Element.drop_priority` is
    set (issue #683 F1's fit policy) -- the fit-policy script (see
    :func:`_fit_script`) reads this attribute to decide what to hide, in
    ascending order, once shrinking the whole band to the legibility
    floor still does not make it fit. Absent entirely for an element
    whose ``drop_priority`` is ``None`` (every split statistic, and the
    identity row), so that script can never select it no matter what it
    queries for -- the same "the invariant is structural, not trusted"
    posture the rest of this module takes.
    """
    el_classes = "el el-identity" if element.role is Role.IDENTITY else "el"
    role_class = f"role-{element.role.value}"
    emphasis_class = f"emphasis-{element.emphasis.value}"
    color_class = _color_class(element.color)
    caption_html = ""
    if element.caption is not None:
        caption_html = f'<span class="caption">{escape(element.caption)}</span>'
    unit_html = ""
    if element.unit is not None:
        unit_html = f'<span class="unit">{escape(element.unit)}</span>'
    value_classes = " ".join(c for c in ("value", role_class, emphasis_class, color_class) if c)
    value_html = f'<span class="{value_classes}">{escape(element.text)}{unit_html}</span>'
    priority_attr = ""
    if element.drop_priority is not None:
        priority_attr = f' data-drop-priority="{element.drop_priority}"'
    return f'<div class="{el_classes}"{priority_attr}>{caption_html}{value_html}</div>'


def _group_style(group: Group) -> str:
    """The inline ``style`` attribute for one group's overrides (issue
    #683 Task 8: ``Group.gap`` / ``Group.margin_top``, and a ``GRID``
    flow's column count) -- ``""`` when nothing overrides the shared
    stylesheet, so a group with no overrides renders exactly the markup
    it always did.

    ``gap``/``margin_top`` go through :func:`_fit` like every other
    length in the middle band (issue #683 F1): a group-specific gap that
    stayed a literal pixel value here would not shrink when the fit
    policy scales everything else down, which would just move the
    overflow from font-size to whitespace instead of closing it.
    """
    parts: list[str] = []
    if group.flow is Flow.GRID:
        parts.append(f"grid-template-columns: repeat({max(1, len(group.elements))}, 1fr)")
    if group.gap is not None:
        parts.append(f"gap: {_fit(group.gap)}")
    if group.margin_top is not None:
        parts.append(f"margin-top: {_fit(group.margin_top)}")
    if not parts:
        return ""
    return f' style="{"; ".join(parts)}"'


def _group_div(group: Group) -> str:
    """One :class:`Group` as a ``.group`` block -- or, when
    ``group.divider`` is set, the hairline rule instead of an
    elements-mapped block. A divider has no elements to render (see
    :class:`~splitsmith.overlay_layout.Group`'s docstring) so it skips
    :func:`_group_classes`/:func:`_element_div` entirely rather than
    mapping an empty ``elements`` tuple through them."""
    if group.divider:
        return '<div class="group divider"></div>'
    elements_html = "".join(_element_div(element) for element in group.elements)
    return f'<div class="{_group_classes(group)}"{_group_style(group)}>{elements_html}</div>'


def _anchor_div(anchor: Anchor, members: Sequence[Group]) -> str:
    # Every group sharing one anchor must agree on any alignment override
    # -- they render inside the same wrapper div and CSS has one
    # ``align-*`` class to give it. The first override found wins; in
    # practice ``overlay_summary._cell_groups`` sets the same ``align`` on
    # every group it declares for a given anchor, so this is just how the
    # (single, agreeing) value reaches the wrapper.
    align = next((g.align for g in members if g.align is not None), None)
    groups_html = "".join(_group_div(group) for group in members)
    return f'<div class="{_anchor_classes(anchor, align)}">{groups_html}</div>'


def _cell_div(groups: Sequence[Group]) -> str:
    """The ``<div class="cell">...</div>`` markup for one present tile,
    with no wrapping ``<style>`` -- shared by :func:`cell_html` (which
    wraps it with its own stylesheet so a single cell is independently
    testable) and :func:`grid_html` (which emits the stylesheet once
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


def _fit_script() -> str:
    """The one piece of *measurement* this pipeline hands to the browser
    instead of doing in Python (issue #683 F1's fit policy).

    Everything else in this module is arithmetic on :class:`CellScale`:
    read once, in Python, off a cell height that never changes. A fit
    policy cannot work that way -- "does this band's declared content
    fit the space CSS actually gave it" is a question only a real layout
    engine can answer, and the answer depends on what a *shooter's data*
    puts in the band (a full stat block versus a bare name), not just
    the cell's geometry. So this measures, in the one place that can:
    ``.anchor-middle-center``'s ``scrollHeight`` (what the band wants)
    against its ``minmax(0, 1fr)`` track's own fixed height (what it
    gets -- see the ``.cell`` rule's own comment for why that track no
    longer grows to fit content the way a bare ``1fr`` would).

    Two-step policy, run once per cell, in priority order -- the order
    issue #683's F1 finding asked for, because the previous behaviour
    (no policy at all, just ``overflow: hidden`` clipping whatever
    painted lowest) discarded the Splits band and a lit penalty plate
    first, which is backwards for an app whose whole product is splits:

    1. **Shrink.** ``--fit-scale`` (see :func:`_fit`) is binary-searched
       from ``1`` down to the largest factor that fits, floored so no
       font in the band drops below
       :data:`~splitsmith.overlay_layout.MIN_FONT_SIZE` -- the same
       legibility floor the live sprite's own PIL fitter already
       enforces. Losing nothing is always better than losing something,
       and this step touches the whole band uniformly, Scoring and
       Splits alike -- unlike step 2, it does not favour one over the
       other.
    2. **Drop.** Only if the band still does not fit at the floor:
       elements carrying ``data-drop-priority`` (see :func:`_element_div`)
       are hidden one at a time, lowest number first, rechecking after
       each, until it fits or nothing is left to drop.
       ``overlay_summary._cell_groups`` is the only caller that assigns
       a priority, and only to the Scoring band's own elements -- a
       split statistic's ``drop_priority`` is always ``None``, so
       nothing this loop does can ever select one, no matter how the
       band's content changes. Within Scoring, the priorities it assigns
       keep a genuinely nonzero (lit) fault count on screen for as long
       as any zero-valued count is still there to drop in its place
       instead, and empty the whole counts row before this loop ever
       reaches hit factor or time.

    Called by :mod:`splitsmith.overlay_raster` after
    ``document.fonts.ready`` resolves, never before: a shrink measured
    against the browser's fallback system font's metrics would pick the
    wrong factor the instant the bundled face actually loads and
    reflows everything under it.
    """
    return f"""
<script>
window.__splitsmithFit = function () {{
  function fits(stack, available) {{
    return stack.scrollHeight <= available + 0.5;
  }}
  function availableHeight(cell) {{
    var rows = getComputedStyle(cell).gridTemplateRows.split(' ').map(parseFloat);
    return rows.length > 1 ? rows[1] : cell.clientHeight;
  }}
  function floorFactor(stack) {{
    var min = Infinity;
    stack.querySelectorAll('.value, .caption, .unit').forEach(function (el) {{
      var size = parseFloat(getComputedStyle(el).fontSize);
      if (size > 0 && size < min) {{ min = size; }}
    }});
    return min === Infinity ? 1 : Math.min(1, {MIN_FONT_SIZE} / min);
  }}
  function shrinkToFit(stack, available) {{
    var lo = floorFactor(stack);
    var hi = 1;
    stack.style.setProperty('--fit-scale', String(hi));
    if (fits(stack, available)) {{ return; }}
    stack.style.setProperty('--fit-scale', String(lo));
    if (!fits(stack, available)) {{ return; }}
    for (var i = 0; i < 14; i++) {{
      var mid = (lo + hi) / 2;
      stack.style.setProperty('--fit-scale', String(mid));
      if (fits(stack, available)) {{ lo = mid; }} else {{ hi = mid; }}
    }}
    stack.style.setProperty('--fit-scale', String(lo));
  }}
  function normalizeLeadingMargin(stack) {{
    // ``Group.margin_top`` (see ``overlay_summary._cell_groups``' own
    // "Splits" label group) is baked into the HTML at Python time,
    // before this script ever runs, to separate the Splits band from a
    // Scoring band that Python believed would be above it. Collapsing an
    // emptied Scoring group (below) removes its own gap but leaves that
    // margin behind on whatever group is now the flex column's first
    // VISIBLE child -- space meant to separate two bands from each
    // other, now separating one band from nothing. Re-zeroing it on
    // whichever group ends up first-visible, every time the set of
    // hidden groups changes, is what a real box model gives for free
    // when there is nothing above to separate from; the browser cannot
    // do that itself because the margin is this group's own property,
    // not the (already ``display: none``, already zero-height) group
    // before it.
    var seenVisible = false;
    Array.prototype.forEach.call(stack.children, function (child) {{
      if (getComputedStyle(child).display === 'none') {{ return; }}
      if (!seenVisible) {{
        seenVisible = true;
        if (child.style.marginTop) {{ child.style.marginTop = '0px'; }}
      }}
    }});
  }}
  function dropUntilFit(stack, available) {{
    var candidates = Array.prototype.slice.call(stack.querySelectorAll('[data-drop-priority]'));
    candidates.sort(function (a, b) {{
      var ap = parseInt(a.getAttribute('data-drop-priority'), 10);
      var bp = parseInt(b.getAttribute('data-drop-priority'), 10);
      return ap - bp;
    }});
    for (var i = 0; i < candidates.length; i++) {{
      if (fits(stack, available)) {{ return; }}
      var el = candidates[i];
      el.style.display = 'none';
      // A ``.group`` with every child now hidden still sits in the
      // ``.anchor-middle-center`` flex column and still consumes a
      // ``row_gutter`` gap -- an emptied group is not a zero-height one.
      // Left alone, those leftover gaps are exactly what pushed the
      // Splits band's own values out of the cell even after this loop
      // had correctly stopped dropping them: see the fix-round report
      // for the measured 58px residual this closes. Hiding the group
      // itself once nothing inside it is visible removes its gap from
      // the flex column entirely, the same way ``display: none`` already
      // removes each dropped ``.el``'s own space.
      var group = el.closest('.group');
      if (group) {{
        var allHidden = Array.prototype.every.call(group.children, function (child) {{
          return getComputedStyle(child).display === 'none';
        }});
        if (allHidden) {{
          group.style.display = 'none';
          normalizeLeadingMargin(stack);
        }}
      }}
    }}
  }}
  document.querySelectorAll('.cell').forEach(function (cell) {{
    var stack = cell.querySelector('.anchor-middle-center');
    if (!stack) {{ return; }}
    var available = availableHeight(cell);
    if (!(available > 0)) {{ return; }}
    if (fits(stack, available)) {{ return; }}
    shrinkToFit(stack, available);
    if (fits(stack, available)) {{ return; }}
    dropUntilFit(stack, available);
  }});
}};
</script>
""".strip()


def cell_html(groups: Sequence[Group], *, scale: CellScale, theme: OverlayTheme) -> str:
    """One cell's declared content as a self-contained HTML fragment.

    Includes its own ``<style>`` block and the fit-policy ``<script>``
    (see :func:`_fit_script`), so this is valid to drop anywhere (a
    test, a future single-shooter port) without also needing a whole
    document -- :func:`grid_html` calls the same building blocks but
    emits the stylesheet and script once for the whole grid instead of
    once per cell. The script only *defines* ``window.__splitsmithFit``
    here; nothing in this module calls it -- see
    :mod:`splitsmith.overlay_raster` for where and why it is invoked.
    """
    return (
        f"<style>{_style_rules(scale=scale, theme=theme)}</style>\n"
        f"{_fit_script()}\n"
        f"{_cell_div(groups)}"
    )


def single_html(
    groups: Sequence[Group],
    *,
    width: int,
    height: int,
    scale: CellScale,
    theme: OverlayTheme,
) -> str:
    """One canvas-sized cell as a whole HTML document (issue #684).

    The single-shooter overlay's counterpart to :func:`grid_html`. There
    is exactly one cell and it is the whole frame, so this takes plain
    pixel dimensions rather than a :class:`SpriteGeometry` -- nothing
    about a single-shooter export has rows, columns or tile placements,
    and borrowing the grid's vocabulary to express "one of one" would be
    the same information spelled twice.

    :func:`cell_html` is nearly this and its docstring names "a future
    single-shooter port" as its reason to exist, but it returns a
    *fragment*. That matters more than it sounds: ``.cell`` is
    ``width: 100%; height: 100%``, which resolves against its containing
    block, and in a grid document that block is a grid item sized by
    ``.grid``'s pixel tracks. A fragment dropped into an empty page has
    no such ancestor, so the cell collapses to its content and every
    anchor lands in the wrong place. This emits the same ``html, body``
    sizing block :func:`grid_html` does -- minus the grid tracks -- so the
    cell fills the canvas.

    ``html``/``body`` stay ``background: transparent``: the rasterizer
    screenshots with ``omit_background=True`` and the result is piped to
    ffmpeg as an alpha layer. An opaque background here would paint the
    whole frame black over the footage.

    Carries the fit-policy ``<script>`` for the same reason both siblings
    do -- see :func:`_fit_script`.
    """
    style = _style_rules(scale=scale, theme=theme)
    page_style = (
        "html, body {\n"
        "margin: 0; padding: 0;\n"
        f"width: {width}px; height: {height}px;\n"
        "background: transparent; overflow: hidden;\n"
        "}"
    )
    return (
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8"><title>overlay</title>'
        f"<style>{style}\n{page_style}</style>"
        f"{_fit_script()}"
        "</head>"
        f"<body>{_cell_div(groups)}</body></html>"
    )


def grid_html(
    cells: Sequence[tuple[TilePlacement, Sequence[Group]]],
    *,
    geometry: SpriteGeometry,
    scale: CellScale,
    theme: OverlayTheme,
) -> str:
    """A whole canvas-sized grid of declared cells as one HTML document.

    Named for the grid rather than the stage summary because it has two
    callers: ``compare/overlay_summary.py``'s freeze-frame summary and
    ``compare/overlay_live.py``'s per-tile live sprites (issue #693).
    Nothing here knows which one is calling -- a cell is a placement plus
    a tuple of declared groups either way, and the only thing that
    differs between the two is what those groups say.

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

    Carries the fit-policy ``<script>`` (see :func:`_fit_script`) in
    ``<head>``, once for the whole document -- it only defines
    ``window.__splitsmithFit``; :mod:`splitsmith.overlay_raster` is what
    actually calls it, after webfonts are loaded and before the
    screenshot.
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
        f"{_fit_script()}"
        "</head>"
        f'<body><div class="grid">{"".join(body_cells)}</div></body></html>'
    )
