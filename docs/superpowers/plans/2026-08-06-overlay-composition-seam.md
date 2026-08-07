# Overlay Composition Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the compare-grid stage summary's hardcoded line sequence with declared, anchored elements, and fix the two defects found while designing it -- procedurals never reaching the screen, and the accent facts being illegible.

**Architecture:** A new top-level `overlay_layout.py` owns the vocabulary (anchors, roles, emphasis) and one `CellScale` that resolves type size from cell height. The live sprite and the `drawtext` clock adopt `CellScale` and change no pixel. `overlay_summary` stops returning an ordered list of lines and starts returning anchored `Group`s, which `_draw_cell` composes.

**Tech Stack:** Python 3.11+, Pillow (PIL), pytest, ffmpeg (`drawtext` for the clock only).

## Global Constraints

- Python 3.11+, type hints everywhere. `uv` for dependencies -- never `pip`.
- Black formatting, line length 110. Ruff for linting.
- `pathlib.Path` for paths, never strings. f-strings for formatting.
- Imports: stdlib, third-party, local -- separated by blank lines. No relative imports beyond a single dot.
- **No new dependencies.** The dep list is small on purpose.
- **Default-off stays byte-identical.** `test_the_default_off_argv_is_unchanged_since_the_preflight_landed` (`tests/test_compare_mp4_grid_commands.py:656`, 42 commands) and `test_zero_hold_produces_the_command_main_produces_today` (`tests/test_compare_mp4_grid_hold.py:457`, 18 commands) must pass unmodified at every commit. If either moves, `concat -c copy` can refuse a segment hours into a match render.
- **Four absences stay distinguishable:** a DQ, a missing scorecard, a filler tile (`present=False`), and a missing audit. A filler tile draws nothing at all.
- **No value is invented.** A missing figure renders less -- never a zero, never a guess.
- The live overlay stays a step function over shot events (~30 sprite PNGs per stage, content-addressed). Nothing here adds a per-frame element.
- Test suite runs in parallel by default. **Use `-n 4` if more than one agent is running** -- `-n auto` takes 12 workers each and concurrent sessions produce contention failures in `test_shot_detect` / `test_tta_agreement` that are not defects. `-n0` for debugging one test.

**Spec:** `docs/superpowers/specs/2026-08-06-overlay-composition-seam-design.md`

## File Structure

| File | Responsibility |
|---|---|
| `src/splitsmith/overlay_layout.py` | **New.** Anchor / Flow / Role / Emphasis / Element / Group / CellScale, plus anchor resolution to pixels and to ffmpeg expressions. Top-level, not under `compare/`, because `overlay_render.py` is the next consumer (#684) and must not import from `compare/`. |
| `src/splitsmith/compare/overlay_sprites.py` | `render_state` takes its `pad` and `big` from `CellScale` instead of computing them. |
| `src/splitsmith/compare/mp4_grid.py` | `_clock_pad` and `_stage_overlay_plan`'s `font_size` come from `CellScale`; `_clock_filters` builds its `x=`/`y=` through `anchor_ffmpeg_expr`. |
| `src/splitsmith/compare/overlay_summary.py` | `_hit_count_line` splits into `_accuracy_line` + `_faults_line`; `_cell_lines` becomes `_cell_groups`; `_draw_cell` composes anchored groups and draws plates. |
| `tests/compare_fixture.py` | The roster gains a nonzero-penalty case. |
| `tests/test_overlay_layout.py` | **New.** Pins every `CellScale` formula and both anchor resolvers. |
| `tests/test_compare_overlay_summary.py` | Extended for accuracy/faults, plates, and anchored composition. |

---

### Task 1: The layout vocabulary and CellScale

**Files:**
- Create: `src/splitsmith/overlay_layout.py`
- Test: `tests/test_overlay_layout.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Anchor`, `Flow`, `Role`, `Emphasis`, `Element`, `Group`, `CellScale`. `CellScale.for_cell(cell_height: int) -> CellScale`, `CellScale.size_for(role: Role) -> int`. Fields: `identity`, `headline`, `verdict`, `detail`, `caption`, `live_primary`, `pad` -- all `int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_overlay_layout.py`:

```python
"""The shared layout vocabulary: anchors, roles, and one type scale."""

import pytest

from splitsmith.overlay_layout import (
    Anchor,
    CellScale,
    Element,
    Emphasis,
    Flow,
    Group,
    Role,
)


@pytest.mark.parametrize("cell_height", [90, 180, 270, 360, 540, 720, 1080])
def test_live_primary_and_pad_match_what_the_grid_computes_today(cell_height):
    """The live sprite and the drawtext clock adopt this resolver without
    changing a pixel, so these two formulas are not free to drift.

    ``overlay_sprites.render_state`` computes ``big`` and ``pad`` inline
    today and ``mp4_grid._stage_overlay_plan`` / ``_clock_pad`` repeat
    them. If either formula changes here, the sprite moves under the
    clock and the two halves of the overlay stop lining up.
    """
    scale = CellScale.for_cell(cell_height)
    assert scale.live_primary == max(48, cell_height // 14)
    assert scale.pad == max(24, cell_height // 36)


@pytest.mark.parametrize("cell_height", [90, 360, 1080])
def test_every_size_is_at_least_the_legibility_floor(cell_height):
    """Below 12px a further shrink reads as noise rather than smaller
    text -- the same floor ``overlay_sprites._MIN_FONT_SIZE`` and
    ``overlay_summary._MIN_FONT_SIZE`` already enforce."""
    scale = CellScale.for_cell(cell_height)
    for role in Role:
        assert scale.size_for(role) >= 12
    assert scale.caption >= 12


def test_sizes_are_ordered_by_prominence():
    """A headline must outrank a detail at every cell size, or the
    hierarchy the composition depends on does not exist."""
    scale = CellScale.for_cell(360)
    assert scale.headline > scale.detail
    assert scale.identity > scale.detail
    assert scale.verdict > scale.detail
    assert scale.caption <= scale.detail


def test_size_for_covers_every_role():
    scale = CellScale.for_cell(360)
    for role in Role:
        assert isinstance(scale.size_for(role), int)


def test_a_group_holds_elements_at_one_anchor():
    group = Group(
        anchor=Anchor.BOTTOM_LEFT,
        flow=Flow.ROW,
        elements=(
            Element(role=Role.HEADLINE, text="4.50", caption="TIME"),
            Element(role=Role.HEADLINE, text="12.00", caption="HF"),
        ),
    )
    assert group.anchor is Anchor.BOTTOM_LEFT
    assert len(group.elements) == 2
    assert group.elements[0].caption == "TIME"
    assert group.elements[0].emphasis is Emphasis.PLAIN


def test_elements_and_groups_are_frozen():
    """Composition is data. A renderer that could mutate a declaration
    would make the declaration untrustworthy."""
    element = Element(role=Role.DETAIL, text="Draw 0.50")
    with pytest.raises(AttributeError):
        element.text = "tampered"
    group = Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW, elements=(element,))
    with pytest.raises(AttributeError):
        group.anchor = Anchor.TOP_RIGHT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_overlay_layout.py -n0 -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'splitsmith.overlay_layout'`

- [ ] **Step 3: Write minimal implementation**

Create `src/splitsmith/overlay_layout.py`:

```python
"""Where overlay elements sit, what they are, and how big they get.

Both overlay renderers draw the same kinds of thing in the same cells:
the compare grid's live sprite (PIL, stepped on shot events), its running
clock (an ffmpeg ``drawtext`` filter, genuinely per frame) and its frozen
stage summary (PIL, once per stage). Until this module existed each of
them computed its own type sizes from cell height, writing the same
formula out in three files, and the summary's composition was a hardcoded
list of lines that every new figure had to be inserted into.

This module owns two things and deliberately nothing else:

- **What an element is** -- an :class:`Anchor`, a :class:`Role`, an
  :class:`Emphasis`. Not a size and not a colour.
- **What a size is** -- :class:`CellScale`, resolved once per cell.

It is not a plugin system. Five roles, six anchors, one product.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: Font sizes never shrink below this. Matches
#: ``overlay_sprites._MIN_FONT_SIZE`` and
#: ``overlay_summary._MIN_FONT_SIZE``: below it a further shrink reads as
#: noise rather than as smaller text.
MIN_FONT_SIZE = 12


class Anchor(Enum):
    """Which corner or edge-centre of a cell an element group sits in.

    Six rather than nine: these are the positions the two renderers
    actually use. The live sprite draws its counter at ``TOP_LEFT`` and
    its last split at ``BOTTOM_CENTER``, the clock draws at ``TOP_RIGHT``,
    and the summary uses ``TOP_LEFT`` / ``TOP_RIGHT`` / ``BOTTOM_LEFT``.
    Adding a middle row would mean inventing a vertical-centring rule
    nothing has asked for.
    """

    TOP_LEFT = "top-left"
    TOP_CENTER = "top-center"
    TOP_RIGHT = "top-right"
    BOTTOM_LEFT = "bottom-left"
    BOTTOM_CENTER = "bottom-center"
    BOTTOM_RIGHT = "bottom-right"

    @property
    def is_bottom(self) -> bool:
        return self in (Anchor.BOTTOM_LEFT, Anchor.BOTTOM_CENTER, Anchor.BOTTOM_RIGHT)

    @property
    def is_right(self) -> bool:
        return self in (Anchor.TOP_RIGHT, Anchor.BOTTOM_RIGHT)

    @property
    def is_center(self) -> bool:
        return self in (Anchor.TOP_CENTER, Anchor.BOTTOM_CENTER)


class Flow(Enum):
    """How the elements of one group run.

    ``COLUMN`` stacks them away from the anchored edge; ``ROW`` runs them
    along it.
    """

    COLUMN = "column"
    ROW = "row"


class Role(Enum):
    """What an element is. Not how big it is -- :class:`CellScale` decides
    that, so a role can be reasoned about without knowing a cell size."""

    #: The shooter's name.
    IDENTITY = "identity"
    #: A figure the viewer should read first -- stage time, hit factor.
    HEADLINE = "headline"
    #: A cross-shooter or disqualifying fact: placing, DQ, penalties.
    #: The only role eligible for :attr:`Emphasis.PLATE`.
    VERDICT = "verdict"
    #: Supporting figures -- split statistics, hit counts, shot count.
    DETAIL = "detail"
    #: The live overlay's shot counter and running clock.
    LIVE_PRIMARY = "live-primary"


class Emphasis(Enum):
    """How hard an element pushes.

    ``PLATE`` draws ink on a filled accent rectangle rather than accent
    ink with a stroke. This is not decoration: measured on a shipped
    frame, the accent placing drew 7.1% accent pixels against 33.9%
    stroke pixels, and the reddest pixel found was ``(201, 8, 10)``
    against a theme accent of ``(255, 45, 45)``. A stroke around thin
    glyphs is a halo that eats the glyph. A plate holds the same
    figure/ground relationship over any footage, and the footage under an
    overlay is always arbitrary.
    """

    PLAIN = "plain"
    MUTED = "muted"
    PLATE = "plate"


@dataclass(frozen=True)
class Element:
    """One drawn string, with what it is rather than how it looks."""

    role: Role
    text: str
    emphasis: Emphasis = Emphasis.PLAIN
    #: The small muted label drawn above a headline value ("TIME", "HF").
    #: A field rather than its own :class:`Role`: a caption is never an
    #: element on its own, it always belongs to the value it labels, and
    #: its size comes from :attr:`CellScale.caption`.
    caption: str | None = None


@dataclass(frozen=True)
class Group:
    """Elements sharing one anchor, laid out together.

    Several groups may share an anchor. They stack away from that
    anchor's edge in declaration order -- the first declared sits closest
    to the edge. Groups do not nest; sharing an anchor is what that would
    otherwise have been for.
    """

    anchor: Anchor
    flow: Flow
    elements: tuple[Element, ...]


@dataclass(frozen=True)
class CellScale:
    """Every type size in one cell, resolved from its height.

    One object rather than a formula per caller. The formulas were
    previously written out in ``overlay_sprites.render_state``,
    ``mp4_grid._clock_pad``, ``mp4_grid._stage_overlay_plan`` and
    ``overlay_summary._draw_cell`` independently, which is what the issue
    meant by "nothing owns what size is a per-tile element".

    Sizes are driven by *cell* height, never canvas height: 3x3 and 4x4
    are first-class grid kinds (``compare/layout.py`` routes 5-16 shooters
    there) and a size picked from the canvas overflows a small cell.
    """

    identity: int
    headline: int
    verdict: int
    detail: int
    caption: int
    live_primary: int
    pad: int

    @classmethod
    def for_cell(cls, cell_height: int) -> CellScale:
        """Resolve the scale for a cell of ``cell_height`` pixels.

        ``live_primary`` and ``pad`` reproduce exactly what the live
        overlay computed before this module existed. They are pinned by
        ``test_live_primary_and_pad_match_what_the_grid_computes_today``
        and are not free to drift: the sprite and the clock have to land
        on the same cell geometry or the two halves of the overlay stop
        lining up.
        """
        floor = MIN_FONT_SIZE
        return cls(
            identity=max(30, cell_height // 17),
            headline=max(30, cell_height // 14),
            verdict=max(24, cell_height // 17),
            detail=max(14, cell_height // 40),
            caption=max(floor, cell_height // 44),
            # Pinned to today's live overlay. Do not "tidy" toward
            # ``headline`` -- see the class docstring.
            live_primary=max(48, cell_height // 14),
            pad=max(24, cell_height // 36),
        )

    def size_for(self, role: Role) -> int:
        """The font size for ``role``.

        ``caption`` has no matching role and is read directly off the
        dataclass -- a caption is never an element on its own.
        """
        return {
            Role.IDENTITY: self.identity,
            Role.HEADLINE: self.headline,
            Role.VERDICT: self.verdict,
            Role.DETAIL: self.detail,
            Role.LIVE_PRIMARY: self.live_primary,
        }[role]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_overlay_layout.py -n0 -v`
Expected: PASS, 6 tests (the parametrised ones count as 7 and 3).

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check src/splitsmith/overlay_layout.py tests/test_overlay_layout.py && uv run black --check src/splitsmith/overlay_layout.py tests/test_overlay_layout.py`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/overlay_layout.py tests/test_overlay_layout.py
git commit -m "feat(overlay): a shared layout vocabulary and one type scale

Anchors, roles and emphasis as data, plus a CellScale that resolves every
type size from cell height. live_primary and pad reproduce exactly what
the live overlay computes today, pinned by test, so adopting this
resolver in the next task changes no pixel."
```

---

### Task 2: Anchor resolution -- pixels and ffmpeg expressions

**Files:**
- Modify: `src/splitsmith/overlay_layout.py`
- Test: `tests/test_overlay_layout.py`

**Interfaces:**
- Consumes: `Anchor` from Task 1.
- Produces: `anchor_origin(anchor, *, cell_x, cell_y, cell_w, cell_h, pad) -> tuple[int, int]` returning the group's origin corner in canvas pixels, and `anchor_ffmpeg_expr(anchor, *, col, row, cell_w, cell_h, pad) -> tuple[str, str]` returning `(x_expr, y_expr)` for `drawtext`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_overlay_layout.py`:

```python
from splitsmith.overlay_layout import anchor_ffmpeg_expr, anchor_origin


def test_top_left_origin_is_inset_by_the_pad():
    x, y = anchor_origin(Anchor.TOP_LEFT, cell_x=640, cell_y=360, cell_w=640, cell_h=360, pad=24)
    assert (x, y) == (664, 384)


def test_bottom_right_origin_is_the_far_corner_inset():
    x, y = anchor_origin(Anchor.BOTTOM_RIGHT, cell_x=0, cell_y=0, cell_w=640, cell_h=360, pad=24)
    assert (x, y) == (616, 336)


def test_center_anchors_land_on_the_cells_horizontal_middle():
    x, _ = anchor_origin(Anchor.BOTTOM_CENTER, cell_x=0, cell_y=0, cell_w=640, cell_h=360, pad=24)
    assert x == 320


def test_the_clock_expression_is_character_for_character_what_it_is_today():
    """``mp4_grid._clock_filters`` builds this string inline today.

    The two argv fingerprint tests hash whole commands, so any drift here
    -- an added space, a reordered term, ``-tw`` moving -- fails them and
    can make ``concat -c copy`` refuse a segment mid-render. This asserts
    the literal rather than recomputing it, so a "harmless tidy" of the
    expression has to change this test deliberately.
    """
    x_expr, y_expr = anchor_ffmpeg_expr(
        Anchor.TOP_RIGHT, col=2, row=1, cell_w=1280, cell_h=720, pad=24
    )
    assert x_expr == "2560+1280-tw-24"
    assert y_expr == "720+24"


def test_a_left_anchor_needs_no_text_width_term():
    x_expr, y_expr = anchor_ffmpeg_expr(
        Anchor.TOP_LEFT, col=0, row=0, cell_w=1280, cell_h=720, pad=24
    )
    assert x_expr == "0+24"
    assert y_expr == "0+24"


def test_bottom_anchors_measure_up_from_the_cells_own_bottom_edge():
    x_expr, y_expr = anchor_ffmpeg_expr(
        Anchor.BOTTOM_LEFT, col=0, row=1, cell_w=1280, cell_h=720, pad=24
    )
    assert x_expr == "0+24"
    assert y_expr == "720+720-th-24"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_overlay_layout.py -n0 -k "origin or expr" -v`
Expected: FAIL -- `ImportError: cannot import name 'anchor_ffmpeg_expr'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/splitsmith/overlay_layout.py`:

```python
def anchor_origin(
    anchor: Anchor,
    *,
    cell_x: int,
    cell_y: int,
    cell_w: int,
    cell_h: int,
    pad: int,
) -> tuple[int, int]:
    """The origin corner of a group at ``anchor``, in canvas pixels.

    ``cell_x`` / ``cell_y`` are the cell's own top-left on the canvas, so
    a caller passes ``col * cell_w`` and ``row * cell_h``. The returned
    point is the corner the group grows *away* from: a bottom anchor
    returns its bottom edge and the caller stacks upward, a right anchor
    returns its right edge and the caller runs leftward. Converting that
    into a text position needs the text's measured box, which only the
    renderer has.

    Centre anchors return the cell's horizontal middle rather than an
    inset edge -- there is nothing to inset from.
    """
    if anchor.is_center:
        x = cell_x + cell_w // 2
    elif anchor.is_right:
        x = cell_x + cell_w - pad
    else:
        x = cell_x + pad
    y = cell_y + cell_h - pad if anchor.is_bottom else cell_y + pad
    return x, y


def anchor_ffmpeg_expr(
    anchor: Anchor,
    *,
    col: int,
    row: int,
    cell_w: int,
    cell_h: int,
    pad: int,
) -> tuple[str, str]:
    """``(x, y)`` expressions for a ``drawtext`` filter at ``anchor``.

    ``drawtext`` positions text by expression, and ``tw`` / ``th`` (text
    width and height) are only known to ffmpeg at draw time -- which is
    exactly why the clock cannot share the PIL path. So a right or bottom
    anchor subtracts ``tw`` / ``th`` inside the expression rather than in
    Python.

    The ``TOP_RIGHT`` form is what ``mp4_grid._clock_filters`` built
    inline before this function existed and it is reproduced character
    for character. Both argv fingerprint tests hash whole commands, so
    any drift here fails them --
    ``test_the_clock_expression_is_character_for_character_what_it_is_today``
    exists so that a drift is a deliberate act rather than a surprise.
    """
    left = col * cell_w
    top = row * cell_h
    if anchor.is_center:
        x_expr = f"{left}+({cell_w}-tw)/2"
    elif anchor.is_right:
        x_expr = f"{left}+{cell_w}-tw-{pad}"
    else:
        x_expr = f"{left}+{pad}"
    y_expr = f"{top}+{cell_h}-th-{pad}" if anchor.is_bottom else f"{top}+{pad}"
    return x_expr, y_expr
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_overlay_layout.py -n0 -v`
Expected: PASS, all tests.

- [ ] **Step 5: Lint, format, commit**

```bash
uv run ruff check src/splitsmith/overlay_layout.py tests/test_overlay_layout.py
uv run black src/splitsmith/overlay_layout.py tests/test_overlay_layout.py
git add src/splitsmith/overlay_layout.py tests/test_overlay_layout.py
git commit -m "feat(overlay): resolve anchors to pixels and to drawtext expressions

One declaration, two backends. The TOP_RIGHT expression reproduces what
_clock_filters builds inline today, character for character, asserted
against the literal so a tidy has to be deliberate."
```

---

### Task 3: The live overlay and clock adopt CellScale

**Files:**
- Modify: `src/splitsmith/compare/overlay_sprites.py` (`render_state`, around lines 297-306)
- Modify: `src/splitsmith/compare/mp4_grid.py` (`_clock_pad` at 776-783, `_clock_filters` at 896-906, `_stage_overlay_plan` at 1553-1563)
- Test: `tests/test_compare_mp4_grid_commands.py`, `tests/test_compare_mp4_grid_hold.py` (run unmodified)

**Interfaces:**
- Consumes: `CellScale.for_cell`, `anchor_ffmpeg_expr`, `Anchor` from Tasks 1-2.
- Produces: no new public names. This task is a substitution, and its deliverable is that nothing changed.

**This task must change no pixel and no argv byte.** That is the whole point of it: the seam becomes load-bearing in Task 6, and this proves the resolver is a faithful replacement before anything depends on it.

- [ ] **Step 1: Capture a before-baseline of the sprite pixels**

Run:

```bash
uv run python - <<'PY'
import hashlib
from splitsmith.compare.overlay_sprites import (
    OverlayState, SpriteGeometry, TilePanel, render_state,
)
from splitsmith.overlay_theme import load_theme

panels = (
    TilePanel(label="Ann", row=0, col=0, present=True, shots_fired=2,
              expected_shots=12, last_split=0.32),
    TilePanel(label="Bo", row=0, col=1, present=True, shots_fired=5,
              expected_shots=None, last_split=None),
)
for size in [(1280, 720, 1, 2), (3840, 2160, 3, 3)]:
    w, h, rows, cols = size
    geo = SpriteGeometry(canvas_width=w, canvas_height=h, rows=rows, cols=cols)
    state = OverlayState(start_seconds=0.0, duration_seconds=1.0, panels=panels)
    img = render_state(state, geo, theme=load_theme("splitsmith"))
    print(size, hashlib.sha256(img.tobytes()).hexdigest()[:16])
PY
```

Record both hashes. They must be identical after Step 3.

- [ ] **Step 2: Run the two fingerprint tests to confirm they pass now**

Run:

```bash
uv run pytest -n0 -v \
  tests/test_compare_mp4_grid_commands.py::test_the_default_off_argv_is_unchanged_since_the_preflight_landed \
  tests/test_compare_mp4_grid_hold.py::test_zero_hold_produces_the_command_main_produces_today
```

Expected: 2 passed.

- [ ] **Step 3: Substitute the formulas**

In `src/splitsmith/compare/overlay_sprites.py`, add to the imports:

```python
from ..overlay_layout import CellScale
```

and in `render_state`, replace:

```python
    pad = max(24, geometry.cell_height // 36)
    big = max(48, geometry.cell_height // 14)
```

with:

```python
    # One resolver for the whole overlay. These two values used to be
    # written out here, in ``mp4_grid._clock_pad`` and in
    # ``_stage_overlay_plan`` independently; ``CellScale`` is the single
    # place that decides them, and it reproduces both exactly.
    scale = CellScale.for_cell(geometry.cell_height)
    pad = scale.pad
    big = scale.live_primary
```

In `src/splitsmith/compare/mp4_grid.py`, add to the imports:

```python
from ..overlay_layout import Anchor, CellScale, anchor_ffmpeg_expr
```

Replace `_clock_pad`'s body (line 783) with:

```python
    return CellScale.for_cell(cell_height).pad
```

In `_clock_filters`, replace the `x=`/`y=` fragment of `common`:

```python
            f"x={clock.col * cell_w}+{cell_w}-tw-{pad}:y={clock.row * cell_h}+{pad}"
```

with:

```python
            f"x={x_expr}:y={y_expr}"
```

and compute them just above the `common` assignment, inside the `for clock in overlay.clocks:` loop:

```python
        x_expr, y_expr = anchor_ffmpeg_expr(
            Anchor.TOP_RIGHT,
            col=clock.col,
            row=clock.row,
            cell_w=cell_w,
            cell_h=cell_h,
            pad=pad,
        )
```

In `_stage_overlay_plan`, replace:

```python
        font_size=max(48, cell_h // 14),
```

with:

```python
        font_size=CellScale.for_cell(cell_h).live_primary,
```

and delete the two comment lines above it that explain the duplication, replacing them with:

```python
        # Same resolver the sprite uses, so the clock and the shot counter
        # beside it cannot pick up different sizes.
```

- [ ] **Step 4: Verify nothing moved**

Re-run the Step 1 script. Expected: **both hashes identical** to Step 1.

Re-run the Step 2 command. Expected: 2 passed.

Then the whole compare suite:

Run: `uv run pytest tests/test_compare_mp4_grid_commands.py tests/test_compare_mp4_grid_hold.py tests/test_compare_overlay_sprites.py -n0 -q`
Expected: all pass.

- [ ] **Step 5: Prove the substitution is load-bearing (mutation drill)**

Change `CellScale.for_cell`'s `live_primary` to `max(47, cell_height // 14)`. Purge caches on both sides:

```bash
find src tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
uv run pytest tests/test_overlay_layout.py -n0 -q
```

Expected: `test_live_primary_and_pad_match_what_the_grid_computes_today` **FAILS**.

Revert with an edit (never `git checkout` -- it would take any uncommitted work with it), purge caches again, re-run. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/compare/overlay_sprites.py src/splitsmith/compare/mp4_grid.py
git commit -m "refactor(overlay): the live sprite and clock resolve sizes through CellScale

Substitution only -- the sprite renders byte-identical pixels at both
1280x720/1x2 and 3840x2160/3x3, and both argv fingerprint tests pass
unmodified. The three copies of max(48, cell_h // 14) and the two of
max(24, cell_h // 36) are now one each."
```

---

### Task 4: The fixture gains a nonzero-penalty case

**Files:**
- Modify: `tests/compare_fixture.py`
- Test: `tests/test_compare_fixture.py` if it exists, otherwise assert inline in `tests/test_compare_overlay_summary.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a roster entry whose `scorecard.procedurals` is nonzero, reachable from `build_roster`.

**Why this comes before the fix:** the defect in Task 5 is invisible to the current fixture -- every roster entry sets `procedurals` to `0` or `None`, so drawing them would change no pixel and no assertion. A fix landed against a fixture that cannot express the failure proves nothing.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_compare_overlay_summary.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compare_overlay_summary.py::test_the_roster_carries_a_nonzero_penalty_somewhere -n0 -v`
Expected: FAIL -- `AssertionError: no roster entry carries a nonzero penalty`

- [ ] **Step 3: Add the penalty to the roster**

In `tests/compare_fixture.py`, find `Mathias`'s stage-2 `StageScoring` and add penalties to its `_card(...)` call:

```python
                    misses=1,
                    procedurals=2,
```

Add a comment above them:

```python
                    # A real penalised run. Nothing else in the roster
                    # carries a nonzero penalty, so without this the
                    # summary could drop procedurals entirely -- as it
                    # did -- and no assertion would move.
                    #
                    # Stage 2 rather than stage 1 because stage 1 has no
                    # non-DQ scorecard-carrying tile to hang it on, and
                    # Mathias rather than Anders or Bea because those two
                    # tie at 100% and this must not disturb the tie.
                    # ``_card`` subtracts penalties from points and
                    # recomputes hit factor and stage_pct together, so
                    # everything stays self-consistent.
```

- [ ] **Step 4: Verify the tie and the divergence survive**

Run:

```bash
uv run python - <<'PY'
from tests.compare_fixture import ROSTER
for spec in ROSTER:
    card = spec.scoring[1].scorecard
    if card is None:
        print(f"{spec.label:10} stage2: no scorecard")
        continue
    print(f"{spec.label:10} stage2: pts={card.stage_points:7.2f} "
          f"hf={card.hit_factor:6.3f} pct={card.stage_pct:6.2f} "
          f"M={card.misses} P={card.procedurals} dq={card.dq}")
PY
```

Expected, and each must be checked by eye:
- two shooters still at `pct=100.00` (the tie is intact),
- Mathias strictly below them (still `#3`),
- the raw-points order still differing from the percentage order (the Open/major versus PO/minor divergence, which is what the fixture exists to express),
- no `ValueError` from `_card`'s above-the-winner guard.

If the tie broke or the divergence collapsed, adjust Mathias's `alphas` upward to compensate for the penalty rather than moving the penalty elsewhere -- and re-run this check.

- [ ] **Step 5: Run the test and the whole compare suite**

Run: `uv run pytest tests/test_compare_overlay_summary.py -n0 -q && uv run pytest tests/ -k compare -n 4 -q`
Expected: all pass. If a test asserted a specific hit-count string for Mathias, update it -- the roster genuinely changed.

- [ ] **Step 6: Commit**

```bash
git add tests/compare_fixture.py tests/test_compare_overlay_summary.py
git commit -m "test(compare): a roster entry that actually carries penalties

Every entry set procedurals to 0 or None, so the summary could drop them
entirely -- as it does -- and no assertion would move. Mathias's stage 2
now carries one miss and two procedurals; the tie at 100% and the
points-versus-percentage divergence are unaffected."
```

---

### Task 5: Procedurals reach the screen, split by kind

**Files:**
- Modify: `src/splitsmith/compare/overlay_summary.py` (`_hit_count_line` at 439-453, `_cell_lines` at 456-516)
- Test: `tests/test_compare_overlay_summary.py`

**Interfaces:**
- Consumes: the penalised roster entry from Task 4.
- Produces: `_accuracy_line(scorecard) -> str | None` returning `"A10 C1 D1"`, and `_faults_line(scorecard) -> str | None` returning `"M0 NS0 P0"`. Both omit any field that is `None` and return `None` when every field is `None`. `_hit_count_line` is removed.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_compare_overlay_summary.py`:

```python
def test_procedurals_reach_the_screen():
    """The defect this task exists for.

    ``StageScorecard`` carries ``procedurals`` and it survives into
    ``TileStageData``, but ``_hit_count_line`` read alphas, charlies,
    deltas, misses and no-shoots and never read it. Two procedurals is 20
    points off a stage; the shooter saw a hit factor that did not follow
    from the hits above it and no explanation anywhere on screen.
    """
    scorecard = StageScorecard(alphas=10, charlies=1, deltas=1, misses=0, no_shoots=0, procedurals=2)
    assert summ._faults_line(scorecard) == "M0 NS0 P2"


def test_accuracy_and_faults_are_separate_lines():
    """A/C/D says how well the shooter shot; M/NS/P says what went wrong.
    One line mixing them cannot give the faults their own emphasis."""
    scorecard = StageScorecard(alphas=10, charlies=1, deltas=1, misses=1, no_shoots=0, procedurals=2)
    assert summ._accuracy_line(scorecard) == "A10 C1 D1"
    assert summ._faults_line(scorecard) == "M1 NS0 P2"


def test_a_recorded_zero_is_drawn_and_an_unread_field_is_not():
    """Zero and absent are different facts and must stay distinguishable.

    A scoreboard row that recorded zero misses draws ``M0``. A row that
    carried no penalty column at all draws nothing for it -- and a row
    with no penalty columns whatsoever draws no faults line.
    """
    recorded = StageScorecard(misses=0, no_shoots=0, procedurals=0)
    assert summ._faults_line(recorded) == "M0 NS0 P0"

    partial = StageScorecard(misses=0, no_shoots=None, procedurals=None)
    assert summ._faults_line(partial) == "M0"

    unread = StageScorecard(misses=None, no_shoots=None, procedurals=None)
    assert summ._faults_line(unread) is None


def test_an_all_none_accuracy_draws_nothing():
    assert summ._accuracy_line(StageScorecard(alphas=None, charlies=None, deltas=None)) is None


def test_both_lines_are_drawn_for_a_penalised_tile(monkeypatch):
    drawn = _capture(monkeypatch)
    scorecard = StageScorecard(
        hit_factor=12.17, stage_pct=78.5, alphas=10, charlies=1, deltas=1,
        misses=1, no_shoots=0, procedurals=2,
    )
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    assert "A10 C1 D1" in drawn
    assert "M1 NS0 P2" in drawn
```

Update the existing `test_none_hit_counts_are_omitted_not_zeroed` (line 367) -- its assertion `hit_lines == ["A7 D1 NS0"]` describes the merged line that no longer exists:

```python
def test_none_hit_counts_are_omitted_not_zeroed(monkeypatch):
    drawn = _capture(monkeypatch)
    # charlies and misses are genuinely unread (None); no_shoots is a real
    # zero. The lines must show the real zero and skip the unread fields --
    # not print a fabricated 0 for a count nobody read. Accuracy and faults
    # are separate lines now, so the zero lands on the faults one.
    scorecard = StageScorecard(alphas=7, charlies=None, deltas=1, misses=None, no_shoots=0)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    placements = [_placement("Ann", 0, 0)]
    summ.build_hold_still(placements, {"Ann": tile}, {}, GEOMETRY, theme=THEME)

    assert "A7 D1" in drawn
    assert "NS0" in drawn
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compare_overlay_summary.py -n0 -k "procedural or accuracy or faults or recorded_zero" -v`
Expected: FAIL -- `AttributeError: module 'splitsmith.compare.overlay_summary' has no attribute '_faults_line'`

- [ ] **Step 3: Write the implementation**

In `src/splitsmith/compare/overlay_summary.py`, replace `_hit_count_line` entirely with:

```python
def _accuracy_line(scorecard) -> str | None:
    """``A7 C2 D1``, omitting any field that is ``None``.

    Accuracy only. Misses and no-shoots used to share this line, which
    made them impossible to emphasise separately from the hits -- and
    procedurals were on neither, so they never reached the screen at all.
    See :func:`_faults_line`.
    """
    return _counts(scorecard, (("alphas", "A"), ("charlies", "C"), ("deltas", "D")))


def _faults_line(scorecard) -> str | None:
    """``M0 NS0 P2``, omitting any field that is ``None``.

    What went wrong, as opposed to how well the shooter shot. A recorded
    zero is drawn: a scoreboard row that says the shooter took no
    procedurals is a fact worth stating, and it is a different fact from
    a row that carried no procedural column at all -- which draws nothing
    here. Those two must stay distinguishable, which is the same rule the
    rest of this module follows.

    ``P`` is the one this function exists for. ``StageScorecard`` has
    carried ``procedurals`` all along and the old merged line never read
    it, so two procedurals -- 20 points -- rendered as nothing.
    """
    return _counts(scorecard, (("misses", "M"), ("no_shoots", "NS"), ("procedurals", "P")))


def _counts(scorecard, fields: tuple[tuple[str, str], ...]) -> str | None:
    """``"<tag><value>"`` per field that is not ``None``, space-joined.

    Returns ``None`` -- draw nothing -- when every field is ``None``.
    """
    parts = [
        f"{tag}{value}"
        for name, tag in fields
        if (value := getattr(scorecard, name)) is not None
    ]
    return " ".join(parts) if parts else None


def has_faults(scorecard) -> bool:
    """Did anything actually go wrong?

    Drives *emphasis*, not presence: a clean run still states ``M0 NS0
    P0``, it just does not light an accent plate to do it. Presence is a
    fact; emphasis is a judgement.
    """
    return any(
        bool(getattr(scorecard, name)) for name in ("misses", "no_shoots", "procedurals")
    )
```

Then in `_cell_lines`, replace:

```python
            hits = _hit_count_line(scorecard)
            if hits is not None:
                lines.append((hits, stat_size, ink))
```

with:

```python
            accuracy = _accuracy_line(scorecard)
            if accuracy is not None:
                lines.append((accuracy, stat_size, ink))
            faults = _faults_line(scorecard)
            if faults is not None:
                lines.append((faults, stat_size, accent if has_faults(scorecard) else ink))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_compare_overlay_summary.py -n0 -q`
Expected: all pass.

- [ ] **Step 5: Mutation drill -- prove the tests catch the defect**

Re-introduce the bug by deleting the `("procedurals", "P")` entry from `_faults_line`'s tuple. Then:

```bash
find src tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
uv run pytest tests/test_compare_overlay_summary.py -n0 -q
```

Expected: `test_procedurals_reach_the_screen`, `test_accuracy_and_faults_are_separate_lines`, `test_a_recorded_zero_is_drawn_and_an_unread_field_is_not` and `test_both_lines_are_drawn_for_a_penalised_tile` all **FAIL**.

Revert with an edit -- **not** `git checkout`, which would take any other uncommitted work with it. Purge `__pycache__` again (CPython invalidates on mtime-in-seconds plus size, so a same-length edit reverted inside one second is silently never applied) and re-run. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/compare/overlay_summary.py tests/test_compare_overlay_summary.py
git commit -m "fix(compare): procedurals reach the screen, split from the hit counts

StageScorecard has carried procedurals all along and _hit_count_line
never read it, so two procedurals -- 20 points -- rendered as nothing and
the shooter saw a hit factor that did not follow from the hits above it.

Accuracy (A/C/D) and faults (M/NS/P) are separate lines now: one line
mixing them cannot give the faults their own emphasis. A recorded zero is
still drawn and an unread field still is not."
```

---

### Task 6: The summary composes declared groups

**Files:**
- Modify: `src/splitsmith/compare/overlay_summary.py` (`_cell_lines` → `_cell_groups`, `_draw_cell`, `_lay_out_block`)
- Test: `tests/test_compare_overlay_summary.py`

**Interfaces:**
- Consumes: `Anchor`, `CellScale`, `Element`, `Emphasis`, `Flow`, `Group`, `Role`, `anchor_origin` from Tasks 1-2; `_accuracy_line`, `_faults_line`, `has_faults` from Task 5.
- Produces: `_cell_groups(tile, placing, label) -> tuple[Group, ...]`. `_cell_lines` is removed.

Target composition, per present tile:

| Anchor | Flow | Contents |
|---|---|---|
| `TOP_LEFT` | `ROW` | label (`IDENTITY`), then `#N` or `DQ` (`VERDICT`, `PLATE`) |
| `TOP_RIGHT` | `COLUMN` | shot count, `Best/Avg/Worst`, `Draw` -- all `DETAIL`, `MUTED` |
| `BOTTOM_LEFT` (2nd) | `ROW` | accuracy (`DETAIL`, `MUTED`), faults (`VERDICT`, `PLATE` iff `has_faults`) |
| `BOTTOM_LEFT` (1st) | `ROW` | `TIME`, `HF`, `STAGE` -- `HEADLINE` with captions |

The band is declared *first* because groups sharing an anchor stack away from its edge in declaration order, and the band sits on the cell's bottom edge.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_compare_overlay_summary.py`:

```python
from splitsmith.overlay_layout import Anchor, Emphasis, Role


def _groups_by_anchor(groups):
    out: dict[Anchor, list] = {}
    for group in groups:
        out.setdefault(group.anchor, []).append(group)
    return out


def test_the_name_and_the_placing_share_the_top_left():
    tile = _full_stat_tile("Ann")
    groups = summ._cell_groups(tile, summ.StagePlacing(rank=2, total_ranked=5), "Ann")
    top_left = _groups_by_anchor(groups)[Anchor.TOP_LEFT][0]
    assert [e.text for e in top_left.elements] == ["Ann", "#2"]
    assert top_left.elements[0].role is Role.IDENTITY
    assert top_left.elements[1].role is Role.VERDICT
    assert top_left.elements[1].emphasis is Emphasis.PLATE


def test_the_band_carries_three_captioned_headlines():
    scorecard = StageScorecard(hit_factor=12.0, stage_pct=100.0)
    tile = TileStageData(label="Ann", stage_number=1, stage_time_seconds=4.5, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    band = _groups_by_anchor(groups)[Anchor.BOTTOM_LEFT][0]
    assert [e.caption for e in band.elements] == ["TIME", "HF", "STAGE"]
    assert [e.text for e in band.elements] == ["4.50", "12.00", "100.0%"]
    assert all(e.role is Role.HEADLINE for e in band.elements)


def test_a_clean_run_states_its_zeros_without_a_plate():
    """Presence is a fact; emphasis is a judgement. Drawing an accent
    plate on every clean cell in the grid would make the plate mean
    nothing when a real penalty turns up."""
    scorecard = StageScorecard(alphas=10, misses=0, no_shoots=0, procedurals=0)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    faults = [e for g in groups for e in g.elements if e.text == "M0 NS0 P0"]
    assert len(faults) == 1
    assert faults[0].emphasis is Emphasis.MUTED


def test_a_penalised_run_lights_the_plate():
    scorecard = StageScorecard(alphas=10, misses=1, no_shoots=0, procedurals=2)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    faults = [e for g in groups for e in g.elements if e.text == "M1 NS0 P2"]
    assert len(faults) == 1
    assert faults[0].emphasis is Emphasis.PLATE


def test_split_statistics_take_the_clocks_old_corner():
    """Nothing jumps across the action-to-hold cut that does not have to.
    The running clock lived at top-right; the figures that replace it
    stay there."""
    tile = _full_stat_tile("Ann")
    groups = summ._cell_groups(tile, None, "Ann")
    top_right = _groups_by_anchor(groups)[Anchor.TOP_RIGHT][0]
    texts = [e.text for e in top_right.elements]
    assert any("Best" in t for t in texts)
    assert any(t.startswith("Draw") for t in texts)
    assert all(e.emphasis is Emphasis.MUTED for e in top_right.elements)


def test_a_dq_takes_the_placings_slot_and_suppresses_the_scoring():
    scorecard = StageScorecard(hit_factor=5.12, stage_pct=80.0, alphas=7, dq=True)
    tile = TileStageData(label="Ann", stage_number=1, scorecard=scorecard)
    groups = summ._cell_groups(tile, None, "Ann")
    texts = [e.text for g in groups for e in g.elements]
    assert "DQ" in texts
    assert not any("5.12" in t for t in texts)
    assert not any("80.0" in t for t in texts)


def test_a_tile_with_nothing_declares_only_its_label():
    """The control cell. A tile with no audit and no scorecard renders
    its name and nothing else -- which is what the pixel checks measure
    "is the hold blurred" against."""
    groups = summ._cell_groups(None, None, "Ann")
    assert [e.text for g in groups for e in g.elements] == ["Ann"]


def test_the_band_is_declared_before_the_faults_row():
    """Groups sharing an anchor stack away from its edge in declaration
    order, and the band sits on the cell's bottom edge."""
    tile = _full_stat_tile("Ann")
    groups = summ._cell_groups(tile, None, "Ann")
    bottom = _groups_by_anchor(groups)[Anchor.BOTTOM_LEFT]
    assert bottom[0].elements[0].role is Role.HEADLINE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compare_overlay_summary.py -n0 -k "top_left or band or clean_run or penalised or clocks_old or dq_takes or nothing_declares" -v`
Expected: FAIL -- `AttributeError: module ... has no attribute '_cell_groups'`

- [ ] **Step 3: Write `_cell_groups`**

In `src/splitsmith/compare/overlay_summary.py`, add to the imports:

```python
from ..overlay_layout import (
    Anchor,
    CellScale,
    Element,
    Emphasis,
    Flow,
    Group,
    Role,
    anchor_origin,
)
```

Replace `_cell_lines` entirely with:

```python
def _cell_groups(
    tile: TileStageData | None,
    placing: StagePlacing | None,
    label: str,
) -> tuple[Group, ...]:
    """What one cell says, as anchored groups rather than an ordered list.

    This used to be a ``list`` of ``(text, size, colour)`` built in a
    fixed sequence, which meant every new figure was an insertion into
    that sequence and every layout assumption around it shifted. Declaring
    position and role instead lets an element be absent without the ones
    around it moving.

    A tile with no audit and no scorecard yields just the label -- that
    cell is the control the hold's pixel checks measure against, so it
    must stay text-free apart from the name.
    """
    scorecard = tile.scorecard if tile is not None else None
    groups: list[Group] = []

    # Top-left: who this is, and how they placed.
    identity: list[Element] = [Element(role=Role.IDENTITY, text=label)]
    if scorecard is not None and scorecard.dq:
        # A DQ takes the placing's slot rather than sitting beside it: a
        # DQ'd run has no rankable finish, so there is no placing to show.
        identity.append(Element(role=Role.VERDICT, text="DQ", emphasis=Emphasis.PLATE))
    elif placing is not None:
        # Bare "#2", not "#2 of 4": only scorecard-carrying tiles enter
        # the ranked pool, so "of 4" on a stage a 7-shooter roster ran
        # would read as a smaller field than actually shot. The grid
        # itself already shows the field size.
        identity.append(
            Element(role=Role.VERDICT, text=f"#{placing.rank}", emphasis=Emphasis.PLATE)
        )
    groups.append(Group(anchor=Anchor.TOP_LEFT, flow=Flow.ROW, elements=tuple(identity)))

    if tile is None:
        return tuple(groups)

    # Top-right: the running clock's old corner. The shooter's own shot
    # detail settles here so nothing jumps across the action-to-hold cut.
    detail: list[Element] = []
    if tile.has_shots:
        detail.append(
            Element(role=Role.DETAIL, text=f"{tile.shot_count} shots", emphasis=Emphasis.MUTED)
        )
        rest = [shot.split for shot in tile.shots[1:]]
        if rest:
            detail.append(
                Element(
                    role=Role.DETAIL,
                    text=(
                        f"Best {min(rest):.2f}  Avg {sum(rest) / len(rest):.2f}  "
                        f"Worst {max(rest):.2f}"
                    ),
                    emphasis=Emphasis.MUTED,
                )
            )
        detail.append(
            Element(
                role=Role.DETAIL,
                text=f"Draw {tile.shots[0].split:.2f}",
                emphasis=Emphasis.MUTED,
            )
        )
    if detail:
        groups.append(
            Group(anchor=Anchor.TOP_RIGHT, flow=Flow.COLUMN, elements=tuple(detail))
        )

    # Bottom-left, declared first so it sits on the cell's bottom edge:
    # the three figures the viewer reads first.
    band: list[Element] = []
    if tile.stage_time_seconds is not None:
        text = f"{tile.stage_time_seconds:.2f}"
        if tile.stage_time_is_manual:
            text += " (manual)"
        band.append(Element(role=Role.HEADLINE, text=text, caption="TIME"))
    if scorecard is not None and not scorecard.dq:
        if scorecard.hit_factor is not None:
            band.append(
                Element(role=Role.HEADLINE, text=f"{scorecard.hit_factor:.2f}", caption="HF")
            )
        if scorecard.stage_pct is not None:
            band.append(
                Element(role=Role.HEADLINE, text=f"{scorecard.stage_pct:.1f}%", caption="STAGE")
            )
    if band:
        groups.append(
            Group(anchor=Anchor.BOTTOM_LEFT, flow=Flow.ROW, elements=tuple(band))
        )

    # Then, stacked above it: how well they shot, and what went wrong.
    if scorecard is not None and not scorecard.dq:
        counts: list[Element] = []
        accuracy = _accuracy_line(scorecard)
        if accuracy is not None:
            counts.append(Element(role=Role.DETAIL, text=accuracy, emphasis=Emphasis.MUTED))
        faults = _faults_line(scorecard)
        if faults is not None:
            counts.append(
                Element(
                    role=Role.VERDICT,
                    text=faults,
                    emphasis=Emphasis.PLATE if has_faults(scorecard) else Emphasis.MUTED,
                )
            )
        if counts:
            groups.append(
                Group(anchor=Anchor.BOTTOM_LEFT, flow=Flow.ROW, elements=tuple(counts))
            )

    return tuple(groups)
```

- [ ] **Step 4: Run the declaration tests**

Run: `uv run pytest tests/test_compare_overlay_summary.py -n0 -k "top_left or band or clean_run or penalised or clocks_old or dq_takes or nothing_declares or declared_before" -v`
Expected: PASS.

Note the drawing tests still fail -- `_draw_cell` has not been rewritten yet. That is Step 5.

- [ ] **Step 5: Rewrite `_draw_cell` to compose groups**

Replace `_draw_cell` with:

```python
def _draw_cell(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    placement: TilePlacement,
    tile: TileStageData | None,
    placing: StagePlacing | None,
    geometry: SpriteGeometry,
    *,
    theme: OverlayTheme,
) -> None:
    """Draw one present tile's declared groups over its own cell.

    A filler tile (``present`` False) draws nothing, matching the live
    sprite's treatment of an empty slot: it is not a shooter, so text
    over black would imply a competitor who isn't there.

    Groups sharing an anchor stack away from that anchor's edge in
    declaration order. Each group is height-bounded on its own rather
    than the cell being bounded once, so a long ``Best/Avg/Worst`` line
    at top-right cannot push the name around.
    """
    if not placement.present:
        return

    scale = CellScale.for_cell(geometry.cell_height)
    cell_x = placement.col * geometry.cell_width
    cell_y = placement.row * geometry.cell_height
    width_budget = max(1, geometry.cell_width - 2 * scale.pad)
    height_budget = max(1, geometry.cell_height - 2 * scale.pad)

    groups = _cell_groups(tile, placing, placement.label)
    consumed: dict[Anchor, int] = {}
    for group in groups:
        origin_x, origin_y = anchor_origin(
            group.anchor,
            cell_x=cell_x,
            cell_y=cell_y,
            cell_w=geometry.cell_width,
            cell_h=geometry.cell_height,
            pad=scale.pad,
        )
        offset = consumed.get(group.anchor, 0)
        # Groups grow away from their own edge, so a bottom anchor's
        # second group moves *up* by what the first consumed.
        origin_y += -offset if group.anchor.is_bottom else offset
        used = _draw_group(
            canvas,
            draw,
            group,
            theme=theme,
            scale=scale,
            origin=(origin_x, origin_y),
            width_budget=width_budget,
            height_budget=max(1, height_budget - offset),
        )
        consumed[group.anchor] = offset + used
```

Add `_draw_group` and the plate helper above it:

```python
def _plate(
    canvas: Image.Image,
    xy: tuple[int, int],
    text: str,
    font,
    *,
    theme: OverlayTheme,
    size: int,
) -> tuple[int, int]:
    """Ink on a filled accent rectangle, returning the plate's size.

    Not decoration. Measured on a shipped frame, the accent placing drew
    7.1% accent pixels against 33.9% stroke pixels and its reddest pixel
    was ``(201, 8, 10)`` against a theme accent of ``(255, 45, 45)``: a
    stroke around thin glyphs is a halo that eats the glyph, and it eats
    most of it at the smallest size in the cell. The footage underneath
    an overlay is always arbitrary, so the only reliable contrast is one
    that brings its own ground.
    """
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = max(8, size // 3), max(5, size // 5)
    plate_w, plate_h = text_w + 2 * pad_x, text_h + 2 * pad_y
    x, y = xy
    canvas.alpha_composite(
        Image.new("RGBA", (plate_w, plate_h), (*theme.accent, 235)), (int(x), int(y))
    )
    draw.text((x + pad_x - bbox[0], y + pad_y - bbox[1]), text, font=font, fill=(*theme.ink, 255))
    return plate_w, plate_h


def _draw_group(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    group: Group,
    *,
    theme: OverlayTheme,
    scale: CellScale,
    origin: tuple[int, int],
    width_budget: float,
    height_budget: float,
) -> int:
    """Draw one group from its anchor origin. Returns the height used.

    The return value is what lets two groups share an anchor without
    overlapping -- the caller offsets the next one by it.
    """
    ink = (*theme.ink, 255)
    muted = (*theme.ink, 170)
    origin_x, origin_y = origin
    cursor_x, cursor_y = origin_x, origin_y
    tallest = 0

    for element in group.elements:
        size = scale.size_for(element.role)
        font, fitted = _fit_font(draw, element.text, theme, base_size=size, budget=width_budget)
        bbox = draw.textbbox((0, 0), element.text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

        caption_h = 0
        if element.caption is not None:
            caption_font, _ = _fit_font(
                draw, element.caption, theme, base_size=scale.caption, budget=width_budget
            )
            caption_bbox = draw.textbbox((0, 0), element.caption, font=caption_font)
            caption_h = (caption_bbox[3] - caption_bbox[1]) + max(4, scale.caption // 3)

        block_h = text_h + caption_h
        x = cursor_x - text_w if group.anchor.is_right else cursor_x
        if group.anchor.is_center:
            x = cursor_x - text_w // 2
        y = cursor_y - block_h if group.anchor.is_bottom else cursor_y

        if element.caption is not None:
            _draw_text_with_shadow(
                draw,
                canvas,
                (x, y - caption_bbox[1]),
                element.caption,
                caption_font,
                muted,
                stroke_width=max(2, scale.caption // 16),
                shadow_offset=max(2, scale.caption // 20),
                shadow_blur=max(3, scale.caption // 10),
                stroke_color=theme.stroke,
                shadow_color=theme.shadow,
            )

        text_y = y + caption_h
        if element.emphasis is Emphasis.PLATE:
            plate_w, plate_h = _plate(
                canvas, (x, text_y), element.text, font, theme=theme, size=fitted
            )
            advance_w, block_h = plate_w, caption_h + plate_h
        else:
            _draw_text_with_shadow(
                draw,
                canvas,
                (x, text_y - bbox[1]),
                element.text,
                font,
                muted if element.emphasis is Emphasis.MUTED else ink,
                stroke_width=max(2, fitted // 16),
                shadow_offset=max(2, fitted // 20),
                shadow_blur=max(3, fitted // 10),
                stroke_color=theme.stroke,
                shadow_color=theme.shadow,
            )
            advance_w = text_w

        gap = max(6, fitted // 6)
        if group.flow is Flow.ROW:
            cursor_x += -(advance_w + gap) if group.anchor.is_right else advance_w + gap
            tallest = max(tallest, block_h)
        else:
            cursor_y += -(block_h + gap) if group.anchor.is_bottom else block_h + gap
            tallest += block_h + gap

    return tallest + max(6, scale.detail // 2)
```

Delete `_lay_out_block` and `_BLOCK_SCALES` only if nothing references them after this change -- check with `grep -n "_lay_out_block\|_BLOCK_SCALES" src tests -r`. If the short-cell test at `tests/test_compare_overlay_summary.py` still needs them, keep them and note why in a comment.

- [ ] **Step 6: Run the full summary suite**

Run: `uv run pytest tests/test_compare_overlay_summary.py -n0 -q`
Expected: all pass. Existing tests that asserted a merged top-left stack (`test_a_short_cell_keeps_its_summary_inside_its_own_cell` in particular) may need their assertions updated to the new anchors -- but **do not weaken the invariant they protect**: no text may cross into a neighbouring cell. If that test now passes trivially, it has stopped testing anything; make it fail against a deliberately over-tall group first.

- [ ] **Step 7: Run every compare test**

Run: `uv run pytest tests/ -k compare -n 4 -q`
Expected: all pass, both fingerprint tests included.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/compare/overlay_summary.py tests/test_compare_overlay_summary.py
git commit -m "feat(compare): the stage summary composes declared groups

_cell_lines built an ordered list of (text, size, colour) in a fixed
sequence, so every new figure was an insertion and every layout
assumption around it shifted. _cell_groups declares anchor, role and
emphasis instead, and _draw_cell resolves them.

The placing, the DQ and a nonzero faults line now draw as ink on a filled
accent plate rather than accent ink with a stroke -- measured on a
shipped frame, the old treatment put 7.1% accent ink against 33.9%
stroke."
```

---

### Task 7: Look at the frames

**Files:**
- None modified. This task is verification.

**Interfaces:**
- Consumes: everything.
- Produces: confirmation, or a defect to go back and fix.

**Why this is its own task:** every defect that has mattered on this feature was found by rendering and measuring, none by reading. A green suite over this change is evidence it broke nothing known -- not evidence it works. On #617 a fix reached the table cell and rich ellipsized it away, so the assertion passed while the user saw nothing.

- [ ] **Step 1: Render**

Run: `uv run python scripts/render_grid_frames.py --overlay --summary-hold 2`

Note `--summary-hold` needs a canvas divisible by the grid (issue #691, unfixed). The default 1280x720 at 3 shooters is 2 columns and divides fine; for 3x3 work use 1440x810 or 3840x2160.

- [ ] **Step 2: Read the frames, do not infer them**

Open `build/grid-frames/stage2-hold-mid.png` and `build/grid-frames/stage1-hold-mid.png` and confirm each of these by looking:

- the name and the placing sit together at top-left, the placing on a filled plate;
- the band runs along the bottom with `TIME` / `HF` / `STAGE` captions above display-size values;
- shot count and split statistics sit quiet at top-right;
- Mathias on stage 2 shows `A10 C1 D1` and a lit `M1 NS0 P2` plate;
- a clean shooter shows their zeros **without** a plate;
- **Bea's stage-1 cell shows her name and nothing else** -- this is the control cell the hold's pixel checks measure blur against, and it must stay text-free apart from the name;
- the filler cell is pure black with nothing drawn on it;
- no text crosses a cell boundary in any cell.

- [ ] **Step 3: Confirm the live overlay is untouched**

Open `build/grid-frames/stage2-mid-action.png`. The counter, clock and last split must look exactly as they did before this branch. If anything moved, Task 3 was not a faithful substitution -- go back to it.

- [ ] **Step 4: Full suite**

Run:

```bash
uv run pytest -q --ignore=tests/test_hosted_docker_smoke.py
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest -m integration --ignore=tests/test_hosted_docker_smoke.py -q
uv run ruff check src tests scripts && uv run black --check src tests scripts
```

Expected: baseline on `main` at `1856704` was 2712 passed / 20 skipped, ~2m14s, integration 28 ran / **0 skipped**. Integration must never skip -- CI fails the build on one. Test count will be higher after this branch; skips must not be.

- [ ] **Step 5: Commit anything outstanding and open the PR**

```bash
git add -A
git commit -m "docs: record the rendered verification for the composition seam"
gh pr create --title "refactor(overlay): a composition seam for overlay elements (#683)" --body "..."
```

The PR body must state: the two defects fixed (procedurals, plate legibility), that the issue's first symptom was stale and why, and that the live overlay is a byte-identical substitution. Link the design spec.

---

## Self-Review

**Spec coverage.**

| Spec requirement | Task |
|---|---|
| `overlay_layout.py` with Anchor / Flow / Role / Emphasis / Element / Group | 1 |
| `CellScale` owning type size, `live_primary` and `pad` exact | 1, pinned by test |
| Groups may share an anchor, stack in declaration order, do not nest | 1 (docstring), 6 (`consumed` offset) |
| `anchor_origin` / `anchor_ffmpeg_expr`, clock expression character-identical | 2 |
| Live sprite and clock adopt `CellScale`, change no pixel | 3, with a hash check and a mutation drill |
| Procedurals reach the screen | 5, with a mutation drill |
| Accuracy and faults split by kind | 5 |
| Zeros drawn, absences not | 5 |
| Emphasis separate from presence (`has_faults`) | 5, 6 |
| Plate rather than stroke for accent elements | 6 |
| Nothing on screen today is lost | 6 (`_cell_groups` carries shot count, splits, draw) |
| Target composition table | 6 |
| Fixture gains a nonzero-penalty case | 4 |
| Four absences stay distinguishable | 6 tests, 7 Step 2 |
| Both fingerprint tests unmodified | 3 Step 4, 6 Step 7, global constraint |
| Mutation drill for each defect | 3 Step 5, 5 Step 5 |
| Render and read the output | 7 |

No spec requirement is unassigned.

**Type consistency.** `_accuracy_line` / `_faults_line` / `has_faults` are defined in Task 5 and consumed in Task 6 under those exact names. `CellScale.for_cell` / `size_for` and the field names `identity, headline, verdict, detail, caption, live_primary, pad` are consistent across Tasks 1, 3 and 6. `anchor_origin` and `anchor_ffmpeg_expr` are defined in Task 2 and used in Tasks 3 and 6 with the same keyword arguments. `Group(anchor, flow, elements)` and `Element(role, text, emphasis, caption)` are constructed in Task 6 exactly as declared in Task 1.

**Known soft spots, flagged rather than hidden.**

- Task 6 Step 5's `_draw_group` is the largest single block in the plan and its row/column advance arithmetic is the part most likely to need adjustment against real output. Task 7 Step 2 is what catches that; do not skip it because the suite is green.
- Whether `_lay_out_block` survives Task 6 depends on what still references it. The plan says to check rather than guessing, and warns against letting the short-cell test pass trivially.
- Task 4 Step 4 may find that adding a penalty to Mathias disturbs the fixture's percentage relationships. The plan gives the compensation (raise his alphas) and requires re-checking rather than accepting a broken tie.
