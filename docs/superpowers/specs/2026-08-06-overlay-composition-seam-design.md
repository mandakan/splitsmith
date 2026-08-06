# Design: a composition seam for overlay elements (#683)

Status: approved 2026-08-06. Supersedes the framing in
`docs/superpowers/plans/2026-08-06-overlay-composition-seam-kickoff.md`,
which stays accurate on constraints and tooling but predates the findings
below.

## Why

`overlay_summary._cell_lines` builds an ordered `list` of
`(text, size, colour)` tuples and `_draw_cell` walks it top-left. Every
figure the summary shows is a position in that list, so adding one means
inserting into the sequence and every layout assumption around it shifts.
The summary's content is about to change, which is what makes this worth
doing now.

Two things found while looking at rendered frames turned this from a
refactor into a refactor plus two fixes. Both are recorded in the review
artifact published 2026-08-06.

### Correcting the issue's premise

Issue #683 gives two motivating symptoms. Only one is real.

**Not real.** The issue says the shot counter and the running clock "sit
in the same corner of the same cell at visibly different weights". They
do not. `mp4_grid._stage_overlay_plan` sets `font_size=max(48, cell_h //
14)` and `overlay_sprites.render_state` computes the same value for its
`big`; measured on a rendered frame at 640x360 cells, both draw 35px of
ink. The 45px in the issue is the slash in `2/12`, which ascends and
descends past any digit. Do not design against this symptom.

What *is* true is that the formula is written out twice in two modules,
plus a third time for the pad (`_clock_pad` mirroring `render_state`'s
`pad`). Nothing owns it. That is worth fixing for the next element's
sake, not because the two disagree today.

**Real.** The line sequence is hardcoded, as described above.

### Defect: procedurals never reach the screen

`StageScorecard` carries `procedurals` and it survives into
`TileStageData.scorecard`, but `_hit_count_line` reads alphas, charlies,
deltas, misses and no-shoots, and never reads it:

```
>>> sc = StageScorecard(alphas=10, charlies=1, deltas=1,
...                     misses=0, no_shoots=0, procedurals=2)
>>> _hit_count_line(sc)
'A10 C1 D1 M0 NS0'
```

Two procedurals is 20 points off a stage. The shooter sees a hit factor
that does not follow from the hits above it and no explanation anywhere.

**The fixture cannot catch this.** Every entry in `tests/compare_fixture.py`
sets `procedurals` to `0` or `None`, so drawing them would change no
pixel and no assertion. This is the trap #682 was filed for, in a field
#682 did not cover.

### Defect: the accent facts are the least legible things in the cell

The placing and the `DQ` are the only cross-shooter facts the summary
carries and both are drawn at `stat_size`, the smallest size in the cell,
in `theme.accent` over arbitrary footage. Sampling the `#1` glyph box on
a rendered stage-2 frame:

| | |
|---|---|
| pixels near accent red | 7.1% |
| pixels near stroke black | 33.9% |
| reddest pixel found | `(201, 8, 10)` |

The theme accent is `(255, 45, 45)`. It never survives antialiasing at
that size -- `stroke_width = max(2, fitted_size // 16)` puts a 2px halo
on both sides of a glyph whose strokes are about 3px wide. A stroke
around thin glyphs is not contrast.

It also happens to land on a red bar in this fixture. That is not a
fixture quirk: the footage underneath is arbitrary, so an accent with no
guaranteed figure/ground relationship will sometimes disappear on real
video.

## Target composition

Chosen from five rendered candidates (A-E in the artifact). **Candidate
E**: everything the summary shows today, plus procedurals, arranged by
anchor rather than by list order.

Per present tile:

| Anchor | Flow | Contents |
|---|---|---|
| top-left | row | shooter label, then the placing `#N` or `DQ` on a plate |
| top-right | column | shot count, `Best/Avg/Worst`, `Draw` -- quiet, right-aligned |
| bottom-left | row | accuracy line, then the faults line beside it |
| bottom-left | row | the headline band: `TIME`, `HF`, `STAGE` |

The headline band is three captioned values -- display-size figures with
small muted captions above them.

Bottom-left carries **two groups**, which is why groups may share an
anchor: they stack away from the anchored edge in declaration order, so
the band sits on the cell's bottom edge and the accuracy/faults row sits
directly above it. Groups do not nest.

Top-right is deliberately the running clock's old corner. Nothing jumps
across the action-to-hold cut that does not have to.

### Content decisions

**Accuracy and faults split by kind.** `A / C / D` says how well the
shooter shot; `M / NS / P` says what went wrong. Today's single line
mixes the two and drops `P`. After this change:

- accuracy line: `A10 C1 D1` -- omits any field that is `None`
- faults line: `M0 NS0 P0` -- omits any field that is `None`

**Zeros are drawn; absences are not.** A scorecard that recorded zero
misses draws `M0`; a scoreboard row that carried no penalty column at all
draws nothing for it. The two must stay distinguishable, which is the
same rule the rest of this module already follows.

**Emphasis is separate from presence.** The faults line takes the accent
plate only when at least one penalty is nonzero. A clean run states
itself in muted ink rather than lighting an accent plate on every cell in
the grid. Presence is a fact; emphasis is a judgement.

**The plate, not a stroke.** Every accent element (placing, `DQ`,
nonzero faults) draws as ink on a filled accent plate. This is the fix
for the 7.1% measurement above: a plate holds the same figure/ground
relationship over any footage, and a stroke does not.

**Nothing on screen today is lost.** Shot count, `Best/Avg/Worst` and
`Draw` all survive, moved rather than cut.

**Still deferred:** whether a time delta joins the placing. That call
waits for real footage (#686) and nothing here settles it.

## Architecture

### New module: `src/splitsmith/overlay_layout.py`

Sits beside `overlay_text.py` and `overlay_theme.py` -- top-level, not
under `compare/`, because the single-shooter renderer is the next
consumer (#684) and must not import from `compare/`.

```python
class Anchor(Enum):
    TOP_LEFT = "top-left"
    TOP_CENTER = "top-center"
    TOP_RIGHT = "top-right"
    BOTTOM_LEFT = "bottom-left"
    BOTTOM_CENTER = "bottom-center"
    BOTTOM_RIGHT = "bottom-right"


class Flow(Enum):
    COLUMN = "column"   # stacked, away from the anchored edge
    ROW = "row"         # side by side, along the anchored edge


class Role(Enum):
    """What an element is. Not how big it is -- CellScale decides that."""
    IDENTITY = "identity"       # the shooter's name
    HEADLINE = "headline"       # a figure the viewer reads first
    VERDICT = "verdict"         # placing, DQ, faults -- accent-eligible
    DETAIL = "detail"           # supporting figures
    LIVE_PRIMARY = "live"       # the live counter and clock


class Emphasis(Enum):
    PLAIN = "plain"
    MUTED = "muted"
    PLATE = "plate"


@dataclass(frozen=True)
class Element:
    role: Role
    text: str
    emphasis: Emphasis = Emphasis.PLAIN
    # The small muted label drawn above a headline value ("TIME", "HF").
    # A field rather than its own Role: it is never an element on its own,
    # it always belongs to the value it labels, and its size comes from
    # CellScale.caption.
    caption: str | None = None


@dataclass(frozen=True)
class Group:
    anchor: Anchor
    flow: Flow
    elements: tuple[Element, ...]
```

`Group` is what makes the band expressible: three captioned headlines in
one `ROW` at `BOTTOM_LEFT`, laid out together rather than as three
independently anchored things that have to agree by coincidence.

Several groups may share an anchor. They stack away from that anchor's
edge in declaration order -- the first group declared sits closest to the
edge. Groups do not nest; that is the whole reason sharing an anchor is
allowed instead.

**`CellScale` is the single owner of type size:**

```python
@dataclass(frozen=True)
class CellScale:
    identity: int
    headline: int
    verdict: int
    detail: int
    caption: int
    live_primary: int
    pad: int

    @classmethod
    def for_cell(cls, cell_height: int) -> "CellScale": ...

    def size_for(self, role: Role) -> int: ...
```

`caption` is a size without a matching `Role`, so `size_for` covers the
five roles and callers read `scale.caption` directly. That asymmetry is
deliberate: a caption is never an element on its own.

`live_primary` is `max(48, cell_height // 14)` and `pad` is
`max(24, cell_height // 36)` -- **exactly** today's values, because the
live sprite and the `drawtext` clock adopt this resolver without changing
a pixel. It is a separate field from `headline` rather than shared with
it: the live overlay and the summary genuinely want different sizes, and
the win is that both formulas now live in one file instead of being
written out in `mp4_grid`, `overlay_sprites` and `overlay_summary`
independently.

### Two backends, one declaration

`Anchor` resolves to pixels for PIL and to an ffmpeg expression for
`drawtext`:

```python
def anchor_pixels(anchor, *, cell_x, cell_y, cell_w, cell_h, pad, size) -> tuple[int, int]
def anchor_ffmpeg_expr(anchor, *, col, row, cell_w, cell_h, pad) -> tuple[str, str]
```

`anchor_ffmpeg_expr` must reproduce `_clock_filters`' current
`x={col*cell_w}+{cell_w}-tw-{pad}` / `y={row*cell_h}+{pad}` string for
`TOP_RIGHT` character for character. That is what keeps the fingerprint
tests green.

### `overlay_summary` composes groups

`_cell_lines` is replaced by:

```python
def _cell_groups(tile, placing, label) -> tuple[Group, ...]
```

Still one function that knows what a cell says, but returning anchored,
roled groups instead of an ordered list. `_draw_cell` walks the groups
and resolves each anchor rather than stacking from a fixed origin.

`_lay_out_block` stays -- its both-axes bounding is load-bearing and the
bug it fixed (one shooter's figures spilling into another's cell) is
worse than losing a line. It gains a caller per group instead of one per
cell, so a long `Best/Avg/Worst` line at top-right can shrink without
touching the name.

### What is deliberately not built

No plugin registry, no per-element classes, no configuration file, no
theme-driven element sets. Five roles, six anchors, one product.

## Invariants

Each of these has a test behind it today. None may regress.

1. **Default-off stays byte-identical.** Two fingerprint tests pin it:
   `test_the_default_off_argv_is_unchanged_since_the_preflight_landed`
   (42 commands) and `test_zero_hold_produces_the_command_main_produces_today`
   (18 commands). If either moves, `concat -c copy` can refuse a segment
   hours into a match render.
2. **The live overlay stays a step function** over shot events, ~30
   sprite PNGs per stage, content-addressed. Nothing here adds a
   per-frame element.
3. **The clock stays `drawtext`** and stays degradable: an ffmpeg without
   libfreetype loses the clock and keeps everything else.
4. **Four absences stay distinguishable:** a DQ, a missing scorecard, a
   filler tile (`present=False`), and a missing audit. A filler tile
   still draws nothing at all.
5. **No value is invented.** A missing figure renders less -- never a
   zero, never a guess. The new zero-versus-absent rule for penalties is
   an instance of this, not an exception to it.

## Testing

**Unit.** `CellScale.for_cell` pins every formula at several cell
heights, including an explicit assertion that `live_primary` and `pad`
equal what `mp4_grid` and `overlay_sprites` compute today. `_cell_groups`
is asserted for each of the four absences plus the clean, penalised and
DQ cases.

**Regression.** Both fingerprint tests run unchanged. `anchor_ffmpeg_expr`
is asserted against the literal string `_clock_filters` builds today.

**Fixture.** The roster gains a nonzero-penalty case. Constraints on
where it can go: the tile must have a scorecard and must not be DQ'd, and
its points changing must not disturb stage 2's tie at 100% or the
points-versus-percentage divergence between the Open/major and PO/minor
shooters. Stage 2's `Mathias` (#3, 78.5%) is the candidate -- `_card`
already subtracts penalties from points and recomputes hit factor and
percentage self-consistently, so the tie at #1 is untouched. Verify the
divergence survives before settling on it.

**Mutation drill, required.** For each of the two defects: re-introduce
the bug, confirm the new test goes red, revert with an edit. Per
`compare-grid-review-lessons`: purge `__pycache__` on both sides (CPython
invalidates on mtime-in-seconds plus size, so a same-length edit reverted
within one second is silently never applied), never `git checkout` the
file, and use a fresh work dir so the content-addressed sprite cache
cannot serve pre-mutation PNGs.

**Pixels.** `scripts/render_grid_frames.py --overlay --summary-hold 2`
before and after. Confirm by reading the frames, not by reading the code:
the plate exists, `M0 NS0 P0` appears on the clean cell, nothing appears
on Bea's, and Bea's cell stays label-only.

## Out of scope

- Rewriting the live overlay's composition. It adopts `CellScale` and
  nothing else.
- #684, the single-shooter port. This exists to make that cheaper.
- #691, the canvas-divisibility bug in `--summary-hold`.
- #689, the two weak summary-hold tests.
- The time delta beside the placing.

## Baselines

On `main` at `1856704`: 2712 passed / 20 skipped, ~2m14s; integration 28
ran, 0 skipped.

```bash
uv run pytest -q --ignore=tests/test_hosted_docker_smoke.py
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest -m integration --ignore=tests/test_hosted_docker_smoke.py -q
uv run ruff check src tests scripts && uv run black --check src tests scripts
```

Use `-n 4` if more than one agent is running; `-n auto` takes 12 workers
each and concurrent sessions produce contention failures in
`test_shot_detect` / `test_tta_agreement` that are not defects.
