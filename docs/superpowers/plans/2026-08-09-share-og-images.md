# Share-link Open Graph Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a pasted share link preview as a branded 1200x630 card carrying real figures, on both the match view and the per-stage view.

**Architecture:** A pure core (`share_card.py`) derives the figures and builds Pydantic card models; a pure HTML layer (`share_card_html.py`) turns a model into a 1200x630 document; the existing `overlay_raster.ChromiumRasterizer` turns that into a PNG. A hosted-only router injects `og:*` tags into the SPA shell for share routes and serves the PNGs from a content-addressed cache in object storage.

**Tech Stack:** Python 3.11+, Pydantic, FastAPI, Playwright/Chromium (already core deps), pytest.

Spec: `docs/superpowers/specs/2026-08-09-share-og-images-design.md`.

## Global Constraints

- Python 3.11+, type hints everywhere. `uv` for dependencies -- never `pip`. No new dependencies in this plan.
- Black formatting, line length 110. Ruff for linting.
- `pathlib.Path` for paths, never strings. f-strings for formatting.
- Imports grouped stdlib / third-party / local, separated by blank lines. No relative imports beyond a single dot.
- Pydantic models for all data crossing module boundaries. No dicts of unknown shape.
- Detection and derivation logic stays out of `cli.py` and out of route handlers.
- Card dimensions are exactly 1200 x 630.
- `SplitColorThresholds.transition_min` default is `1.0` (`src/splitsmith/config.py:357`). It hangs off `OutputConfig` as `split_color_thresholds` (`config.py:361`), so the configured value is `OutputConfig().split_color_thresholds.transition_min`. The fallback path reads it from config; never hardcode it.
- `CoachIntervalClass` values are exactly: `first_shot`, `split`, `transition`, `movement`, `reload`, `activation`.
- **The split rule is owned by `splitsmith.coach.statistic_splits`** (landed on
  main in #774, closing #772), mirrored in TS as `statisticSplits`. Nothing in
  this plan may reimplement it. Main's rule uses the classified path as soon as
  ANY interval carries a class; this plan originally specified all-or-nothing,
  and that disagreement is filed as #775 rather than settled here.
- After Task 4b, `share_card.Interval` and `share_card.intervals_from_audit_shots`
  **do not exist**. Shot records come from `audit_data.audit_shots_to_engine_shots`,
  whose `config.Shot` carries `.split` and `.interval_class` and so satisfies
  `coach.SplitStatInterval`. Tests use a local frozen-dataclass double with those
  two attributes rather than constructing a full engine `Shot`.
- Share links are hosted-only. Every new route returns 404 when `_hosted_mode_active()` is False.
- Run the full suite with `uv run pytest` before the final commit. Use `-n0` when debugging one test.
- Tests must not depend on execution order or share mutable state outside `tmp_path`.

---

### Task 1: Split-figure derivation (the shared definition)

> **Superseded in part by Task 4b.** #774 landed the canonical rule on main as
> `coach.statistic_splits` while this branch was in flight. Task 4b deletes this
> task's reimplementation, its `Interval` type and its `intervals_from_audit_shots`,
> keeping only `StageFigures`. The text below is left as the record of what was
> built and why; do not implement it fresh.

This is the module #772 will also consume. It is pure: no I/O, no browser, no FastAPI.

**Files:**
- Create: `src/splitsmith/share_card.py`
- Test: `tests/test_share_card_figures.py`

**Interfaces:**
- Consumes: `splitsmith.config.SplitColorThresholds` (existing).
- Produces:
  - `Interval` frozen dataclass: `index: int`, `seconds: float`, `interval_class: str | None`
  - `StageFigures` frozen dataclass: `draw: float | None`, `avg_split: float | None`, `split_count: int`, `interval_count: int`, `source: Literal["coach", "threshold", "empty"]`
  - `intervals_from_audit_shots(shots: Sequence[Mapping[str, Any]]) -> tuple[Interval, ...]`
  - `stage_figures(intervals: Sequence[Interval], *, transition_min: float) -> StageFigures`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_share_card_figures.py`:

```python
"""Split-figure derivation for the share card (spec 2026-08-09).

The coach path and the threshold fallback must disagree on a run that
contains transitions -- a test that cannot pass if classification is
being ignored.
"""

from __future__ import annotations

import pytest

from splitsmith.share_card import Interval, intervals_from_audit_shots, stage_figures

# One real-shaped run: draw 1.28, nine splits (mean 0.182), two
# transitions, one movement, one reload. Intervals sum to 14.74 s.
_SECONDS = [1.28, 0.19, 0.17, 0.22, 1.85, 0.16, 0.18, 2.42, 0.21, 0.15, 5.45, 0.20, 0.16, 2.10]
_CLASSES = [
    "first_shot",
    "split",
    "split",
    "split",
    "transition",
    "split",
    "split",
    "reload",
    "split",
    "split",
    "movement",
    "split",
    "split",
    "transition",
]


def _classified() -> tuple[Interval, ...]:
    return tuple(
        Interval(index=i + 1, seconds=s, interval_class=c)
        for i, (s, c) in enumerate(zip(_SECONDS, _CLASSES, strict=True))
    )


def _unclassified() -> tuple[Interval, ...]:
    return tuple(
        Interval(index=i + 1, seconds=s, interval_class=None) for i, s in enumerate(_SECONDS)
    )


def test_coach_path_averages_only_split_intervals() -> None:
    figs = stage_figures(_classified(), transition_min=1.0)
    assert figs.source == "coach"
    assert figs.draw == pytest.approx(1.28)
    assert figs.avg_split == pytest.approx(0.182, abs=5e-4)
    assert figs.split_count == 9
    assert figs.interval_count == 14


def test_threshold_fallback_used_when_no_interval_is_classified() -> None:
    figs = stage_figures(_unclassified(), transition_min=1.0)
    assert figs.source == "threshold"
    assert figs.draw == pytest.approx(1.28)
    # Same nine sub-second intervals survive the 1.0 s cut on this run.
    assert figs.split_count == 9
    assert figs.avg_split == pytest.approx(0.182, abs=5e-4)


def test_threshold_fallback_diverges_from_coach_when_a_transition_is_short() -> None:
    """A 0.80 s transition is below transition_min, so the fallback counts
    it as a split and the coach path does not. This is the assertion that
    fails if classification is silently ignored."""
    seconds = [1.28, 0.19, 0.80, 0.17]
    classes = ["first_shot", "split", "transition", "split"]
    classified = tuple(
        Interval(index=i + 1, seconds=s, interval_class=c)
        for i, (s, c) in enumerate(zip(seconds, classes, strict=True))
    )
    unclassified = tuple(
        Interval(index=i + 1, seconds=s, interval_class=None) for i, s in enumerate(seconds)
    )
    coach = stage_figures(classified, transition_min=1.0)
    threshold = stage_figures(unclassified, transition_min=1.0)
    assert coach.split_count == 2
    assert threshold.split_count == 3
    assert coach.avg_split != pytest.approx(threshold.avg_split)


def test_partial_classification_falls_back_to_threshold_for_the_whole_stage() -> None:
    """All-or-nothing: one unset interval demotes the entire run."""
    intervals = (
        Interval(index=1, seconds=1.28, interval_class="first_shot"),
        Interval(index=2, seconds=0.19, interval_class="split"),
        Interval(index=3, seconds=0.80, interval_class=None),
    )
    assert stage_figures(intervals, transition_min=1.0).source == "threshold"


def test_all_intervals_are_transitions_yields_draw_but_no_average() -> None:
    intervals = (
        Interval(index=1, seconds=1.28, interval_class="first_shot"),
        Interval(index=2, seconds=2.40, interval_class="reload"),
    )
    figs = stage_figures(intervals, transition_min=1.0)
    assert figs.draw == pytest.approx(1.28)
    assert figs.avg_split is None
    assert figs.split_count == 0


def test_no_intervals_yields_empty_source_and_no_figures() -> None:
    figs = stage_figures((), transition_min=1.0)
    assert figs.source == "empty"
    assert figs.draw is None
    assert figs.avg_split is None
    assert figs.interval_count == 0


def test_intervals_from_audit_shots_orders_by_time_and_derives_gaps() -> None:
    shots = [
        {"shot_number": 2, "ms_after_beep": 1470, "interval_class": "split"},
        {"shot_number": 1, "ms_after_beep": 1280, "interval_class": "first_shot"},
        {"shot_number": 3, "ms_after_beep": 1640, "interval_class": "split"},
    ]
    intervals = intervals_from_audit_shots(shots)
    assert [i.index for i in intervals] == [1, 2, 3]
    assert intervals[0].seconds == pytest.approx(1.28)
    assert intervals[1].seconds == pytest.approx(0.19)
    assert intervals[2].seconds == pytest.approx(0.17)


def test_intervals_from_audit_shots_skips_entries_without_a_time() -> None:
    shots = [
        {"shot_number": 1, "ms_after_beep": 1280},
        {"shot_number": 2, "interval_class": "split"},
    ]
    assert len(intervals_from_audit_shots(shots)) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_share_card_figures.py -n0 -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.share_card'`

- [ ] **Step 3: Write the implementation**

Create `src/splitsmith/share_card.py`:

```python
"""Share-card figures and models (spec 2026-08-09).

Pure: no file I/O, no browser, no FastAPI. Rasterizing a card is
``share_card_html`` plus ``overlay_raster``'s job; serving one is
``ui/share_og.py``'s.

**One definition of a split.** A split statistic is computed over
intervals classed ``split`` -- transitions, movement, reloads and
activations are excluded by construction rather than by a threshold.
The draw is the ``first_shot`` interval. When a stage carries no
classification at all (detected and audited, never coached), the
fallback is the rule ``fcpxml_gen.split_color_band`` already encodes:
index 1 is the draw, and any interval above
``SplitColorThresholds.transition_min`` is not a split.

Classification is all-or-nothing per stage. Mixing the two rules within
one run would produce an average whose definition varies by which
intervals happened to be reviewed, so a single unset interval demotes
the whole stage to the threshold path. :attr:`StageFigures.source`
records which path ran.

Issue #772 brings the video stage summary and the results page onto
this same definition; both consume :func:`stage_figures`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

#: The interval class naming the draw. Mirrors ``CoachIntervalClass`` in
#: ``ui_static/src/lib/api.ts``; ``coach.py`` owns the Python side.
DRAW_CLASS = "first_shot"

#: The only class a split statistic is computed over.
SPLIT_CLASS = "split"

FigureSource = Literal["coach", "threshold", "empty"]


@dataclass(frozen=True)
class Interval:
    """One inter-shot gap. ``index`` is 1-based; index 1 is the draw, so
    its ``seconds`` is time from the beep rather than from a prior shot."""

    index: int
    seconds: float
    interval_class: str | None


@dataclass(frozen=True)
class StageFigures:
    """What a stage card puts on screen, and how it was derived."""

    draw: float | None
    avg_split: float | None
    split_count: int
    interval_count: int
    source: FigureSource


def intervals_from_audit_shots(shots: Sequence[Mapping[str, Any]]) -> tuple[Interval, ...]:
    """Derive ordered intervals from an audit doc's ``shots`` list.

    Mirrors the ordering and gap arithmetic ``ui/server._build_coach_response``
    applies: sort by ``ms_after_beep``, treat shot 1's time from the beep as
    its interval, and take each later shot's gap from its predecessor.
    Entries without ``ms_after_beep`` are not shots on a timeline and are
    dropped rather than defaulted to zero.
    """
    ordered = sorted(
        (s for s in shots if isinstance(s, Mapping) and s.get("ms_after_beep") is not None),
        key=lambda s: float(s["ms_after_beep"]),
    )
    out: list[Interval] = []
    prev_ms: float | None = None
    for i, shot in enumerate(ordered):
        ms = float(shot["ms_after_beep"])
        seconds = ms / 1000.0 if prev_ms is None else (ms - prev_ms) / 1000.0
        prev_ms = ms
        cls = shot.get("interval_class")
        out.append(
            Interval(
                index=i + 1,
                seconds=seconds,
                interval_class=cls if isinstance(cls, str) else None,
            )
        )
    return tuple(out)


def stage_figures(intervals: Sequence[Interval], *, transition_min: float) -> StageFigures:
    """Draw and average split for one run.

    ``transition_min`` comes from ``SplitColorThresholds`` and is only
    consulted on the fallback path. Passing it explicitly keeps this
    function pure and lets a caller A/B a candidate value (#773) without
    touching config.
    """
    if not intervals:
        return StageFigures(
            draw=None, avg_split=None, split_count=0, interval_count=0, source="empty"
        )

    classified = all(i.interval_class is not None for i in intervals)
    if classified:
        source: FigureSource = "coach"
        draw = next((i.seconds for i in intervals if i.interval_class == DRAW_CLASS), None)
        splits = [i.seconds for i in intervals if i.interval_class == SPLIT_CLASS]
    else:
        source = "threshold"
        draw = next((i.seconds for i in intervals if i.index == 1), None)
        splits = [i.seconds for i in intervals if i.index != 1 and i.seconds <= transition_min]

    return StageFigures(
        draw=draw,
        avg_split=(sum(splits) / len(splits)) if splits else None,
        split_count=len(splits),
        interval_count=len(intervals),
        source=source,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_share_card_figures.py -n0 -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Prove the divergence test can fail**

Temporarily change `classified = all(...)` to `classified = False` in `stage_figures`.
Run: `uv run pytest tests/test_share_card_figures.py -n0 -q`
Expected: FAIL on `test_coach_path_averages_only_split_intervals` and
`test_threshold_fallback_diverges_from_coach_when_a_transition_is_short`.
Revert the change and re-run to confirm PASS.

This is the mutation check CLAUDE.md's review practice asks for: a test that
passes against the bug it claims to cover is not coverage.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/share_card.py tests/test_share_card_figures.py
git commit -m "feat(share): one definition of draw and non-anomaly average split

Refs #772"
```

---

### Task 2: Card models and content hash

**Files:**
- Modify: `src/splitsmith/share_card.py`
- Test: `tests/test_share_card_models.py`

**Interfaces:**
- Consumes: Task 1's `StageFigures`, `Interval`, `stage_figures`.
- Produces:
  - `RosterEntry(BaseModel)`: `name: str`, `division: str | None`
  - `MatchCard(BaseModel)`: `match_name: str`, `match_date: str | None`, `stage_count: int`, `roster: list[RosterEntry]`
  - `StageCard(BaseModel)`: `stage_number: int`, `stage_name: str`, `shooter_name: str`, `match_name: str`, `shot_count: int`, `stage_time: float | None`, `figures: StageFigures`
  - `card_hash(card: MatchCard | StageCard) -> str` -- 16-char hex
  - `MatchCard.roster` is sorted alphabetically by `name` at construction.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_share_card_models.py`:

```python
"""Card models: roster ordering and content hashing (spec 2026-08-09)."""

from __future__ import annotations

from splitsmith.share_card import (
    Interval,
    MatchCard,
    RosterEntry,
    StageCard,
    card_hash,
    stage_figures,
)


def _figs() -> object:
    return stage_figures(
        (
            Interval(index=1, seconds=1.28, interval_class="first_shot"),
            Interval(index=2, seconds=0.19, interval_class="split"),
        ),
        transition_min=1.0,
    )


def _stage_card(stage_name: str = "Per told me to do it!") -> StageCard:
    return StageCard(
        stage_number=3,
        stage_name=stage_name,
        shooter_name="Mathias Axell",
        match_name="Tallmilan 2026",
        shot_count=2,
        stage_time=14.74,
        figures=_figs(),
    )


def test_roster_is_sorted_alphabetically_by_name() -> None:
    card = MatchCard(
        match_name="Tallmilan 2026",
        match_date="2026-04-26",
        stage_count=7,
        roster=[
            RosterEntry(name="Petra Lind", division="Standard"),
            RosterEntry(name="Anders Berg", division="Production Optics"),
            RosterEntry(name="Mathias Axell", division="Production Optics"),
        ],
    )
    assert [r.name for r in card.roster] == ["Anders Berg", "Mathias Axell", "Petra Lind"]


def test_hash_is_stable_across_equal_cards() -> None:
    assert card_hash(_stage_card()) == card_hash(_stage_card())


def test_hash_changes_when_any_displayed_figure_changes() -> None:
    before = card_hash(_stage_card())
    after = card_hash(_stage_card(stage_name="Short and Sweet"))
    assert before != after


def test_hash_changes_when_the_average_split_changes() -> None:
    base = _stage_card()
    moved = base.model_copy(
        update={
            "figures": stage_figures(
                (
                    Interval(index=1, seconds=1.28, interval_class="first_shot"),
                    Interval(index=2, seconds=0.31, interval_class="split"),
                ),
                transition_min=1.0,
            )
        }
    )
    assert card_hash(base) != card_hash(moved)


def test_hash_is_sixteen_hex_characters() -> None:
    h = card_hash(_stage_card())
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_share_card_models.py -n0 -q`
Expected: FAIL with `ImportError: cannot import name 'MatchCard'`

- [ ] **Step 3: Write the implementation**

Append to `src/splitsmith/share_card.py` (add `hashlib`, `json`, and the
`pydantic` imports to the existing import block):

```python
class RosterEntry(BaseModel):
    """One shooter on a match card."""

    name: str
    division: str | None = None


class MatchCard(BaseModel):
    """The top-level share card: identity plus who is in the match.

    No aggregate time figure by design -- IPSC ranks by hit factor and
    match percentage, so summed stage time is not a number the sport
    produces. See the spec's "Match card -- roster" section.
    """

    match_name: str
    match_date: str | None = None
    stage_count: int
    roster: list[RosterEntry] = Field(default_factory=list)

    @field_validator("roster")
    @classmethod
    def _sorted_roster(cls, value: list[RosterEntry]) -> list[RosterEntry]:
        """Alphabetical by name, matching the slot-order convention
        ``compare/`` already uses so a roster never reshuffles."""
        return sorted(value, key=lambda r: r.name)


class StageCard(BaseModel):
    """One shooter's run on one stage."""

    stage_number: int
    stage_name: str
    shooter_name: str
    match_name: str
    shot_count: int
    stage_time: float | None = None
    figures: StageFigures


def card_hash(card: MatchCard | StageCard) -> str:
    """Content hash over everything the card displays.

    The ``og:image`` URL carries this, so a re-audit that moves any
    displayed figure moves the URL and crawlers refetch instead of
    serving a stale preview. Sixteen hex characters: collision risk is
    negligible for a per-token keyspace and the URL stays readable.
    """
    payload = json.dumps(card.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
```

Add to the imports at the top of the module:

```python
import hashlib
import json

from pydantic import BaseModel, Field, field_validator
```

`StageFigures` is a frozen dataclass, which Pydantic accepts as a field
type and serializes through `model_dump(mode="json")` without extra
configuration.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_share_card_models.py -n0 -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/share_card.py tests/test_share_card_models.py
git commit -m "feat(share): card models with content-addressed hashing"
```

---

### Task 3: Two more design-system tokens

The card needs a surface fill and a dimmer label grey that `OverlayTheme`
does not yet carry. Adding them to the build script rather than
hardcoding hexes preserves the property `overlay_theme.py` documents: the
design system cannot silently drift from the renderers.

**Files:**
- Modify: `scripts/build_overlay_theme.py` (the `TOKEN_MAP` dict)
- Modify: `src/splitsmith/overlay_theme.py` (the `OverlayTheme` dataclass)
- Modify: `src/splitsmith/data/overlay_theme.json` (regenerated, not hand-edited)
- Test: `tests/test_overlay_theme.py`

**Interfaces:**
- Produces: `OverlayTheme.surface: RGB` and `OverlayTheme.subtle: RGB`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_overlay_theme.py`:

```python
def test_splitsmith_theme_carries_surface_and_subtle() -> None:
    """The share card (spec 2026-08-09) paints a surface fill and a
    dimmer label grey. Both come from index.css via the build script,
    never hardcoded in a renderer."""
    theme = load_theme("splitsmith")
    assert theme.surface == (0x14, 0x17, 0x1C)  # --color-surface
    assert theme.subtle == (0x6B, 0x70, 0x79)  # --color-subtle
```

If `load_theme` is not already imported in that file, add
`from splitsmith.overlay_theme import load_theme` to its imports.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_overlay_theme.py::test_splitsmith_theme_carries_surface_and_subtle -n0 -q`
Expected: FAIL with `AttributeError: 'OverlayTheme' object has no attribute 'surface'`

- [ ] **Step 3: Add the tokens to the build map**

In `scripts/build_overlay_theme.py`, add two entries to `TOKEN_MAP`:

```python
    # Share card (spec 2026-08-09): the plate fill behind a stat cell and
    # the dimmer grey its caption uses. Both already exist in index.css;
    # the card must not invent its own hexes.
    "surface": "--color-surface",
    "subtle": "--color-subtle",
```

- [ ] **Step 4: Add the fields to the dataclass**

In `src/splitsmith/overlay_theme.py`, add to `OverlayTheme` after `muted`:

```python
    #: Plate fill behind a share-card stat cell (``--color-surface``).
    surface: RGB
    #: Dimmer label grey than :attr:`muted` (``--color-subtle``), for
    #: captions that must sit below a value without competing with it.
    subtle: RGB
```

Add the same two keys to the `_CLEAN` preset literal in that module. The
clean theme carries no brand colours, so use its existing neutral
discipline: `surface=(0, 0, 0)` and `subtle=(128, 128, 128)`.

- [ ] **Step 5: Regenerate the JSON**

Run: `uv run python scripts/build_overlay_theme.py`
Expected: `src/splitsmith/data/overlay_theme.json` now contains `surface`
and `subtle`. Verify with `git diff --stat src/splitsmith/data/overlay_theme.json`.

- [ ] **Step 6: Run the theme tests AND everything downstream of the dataclass**

`OverlayTheme` gains two required fields and the shipped
`overlay_theme.json` is regenerated. Every overlay renderer reads that
dataclass, so a focused run cannot see the blast radius -- adding a
required field to a frozen dataclass is exactly the change that breaks a
constructor call in another module.

Run: `uv run pytest tests/test_overlay_theme.py tests/test_overlay_html.py \
  tests/test_overlay_raster.py tests/test_overlay_render.py \
  tests/test_overlay_single.py tests/test_overlay_layout.py \
  tests/test_overlay_clock.py tests/test_overlay_text.py \
  tests/test_compare_overlay_summary.py tests/test_compare_overlay_live.py \
  tests/test_compare_overlay_sprites.py -q`
Expected: PASS. Any failure here is this task's to fix, not a later task's.

- [ ] **Step 7: Commit**

```bash
git add scripts/build_overlay_theme.py src/splitsmith/overlay_theme.py \
        src/splitsmith/data/overlay_theme.json tests/test_overlay_theme.py
git commit -m "feat(theme): carry surface and subtle tokens for the share card"
```

---

### Task 4: Card HTML

**Files:**
- Create: `src/splitsmith/share_card_html.py`
- Test: `tests/test_share_card_html.py`

**Interfaces:**
- Consumes: Task 2's `MatchCard` / `StageCard`; Task 3's `OverlayTheme.surface` / `.subtle`; `overlay_html._font_face_url` (existing).
- Produces:
  - `CARD_WIDTH = 1200`, `CARD_HEIGHT = 630`
  - `match_card_html(card: MatchCard, *, theme: OverlayTheme) -> str`
  - `stage_card_html(card: StageCard, *, theme: OverlayTheme) -> str`

Read `src/splitsmith/overlay_html.py` before writing this. Two constraints
carry over verbatim:

1. `@font-face` `src` URLs come from `overlay_html._font_face_url`, which
   resolves the bundled TTFs under `src/splitsmith/data/fonts/`. The
   rasterizer navigates to a real file; it must never use
   `page.set_content()` (that module's docstring records the measurement
   showing the bundled face silently fails to load under `set_content`).
2. Every text box gets `overflow: hidden`, and long names clamp in CSS.
   No Python measures text.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_share_card_html.py`:

```python
"""Card HTML is pure string building -- no browser in these tests."""

from __future__ import annotations

import pytest

from splitsmith.overlay_theme import load_theme
from splitsmith.share_card import (
    Interval,
    MatchCard,
    RosterEntry,
    StageCard,
    stage_figures,
)
from splitsmith.share_card_html import (
    CARD_HEIGHT,
    CARD_WIDTH,
    match_card_html,
    stage_card_html,
)


@pytest.fixture
def theme():
    return load_theme("splitsmith")


def _stage_card(**overrides) -> StageCard:
    figures = overrides.pop(
        "figures",
        stage_figures(
            (
                Interval(index=1, seconds=1.28, interval_class="first_shot"),
                Interval(index=2, seconds=0.19, interval_class="split"),
                Interval(index=3, seconds=0.17, interval_class="split"),
            ),
            transition_min=1.0,
        ),
    )
    base = {
        "stage_number": 3,
        "stage_name": "Per told me to do it!",
        "shooter_name": "Mathias Axell",
        "match_name": "Tallmilan 2026",
        "shot_count": 3,
        "stage_time": 14.74,
        "figures": figures,
    }
    base.update(overrides)
    return StageCard(**base)


def test_card_dimensions_are_declared(theme) -> None:
    assert (CARD_WIDTH, CARD_HEIGHT) == (1200, 630)
    html = stage_card_html(_stage_card(), theme=theme)
    assert "1200px" in html
    assert "630px" in html


def test_stage_card_shows_draw_and_average_split(theme) -> None:
    """The fractional part is wrapped in a dimmed span, so assert on the
    parts rather than a contiguous "1.28"."""
    html = stage_card_html(_stage_card(), theme=theme)
    assert ">1<span" in html and ".28</span>" in html
    assert ">0<span" in html and ".180</span>" in html  # (0.19 + 0.17) / 2
    assert "Draw" in html
    assert "Avg split" in html


def test_stage_card_omits_the_average_when_there_are_no_splits(theme) -> None:
    figures = stage_figures(
        (
            Interval(index=1, seconds=1.28, interval_class="first_shot"),
            Interval(index=2, seconds=2.40, interval_class="reload"),
        ),
        transition_min=1.0,
    )
    html = stage_card_html(_stage_card(figures=figures), theme=theme)
    assert "Draw" in html
    assert "Avg split" not in html
    # Never a placeholder: no zero, no dash standing in for a real figure.
    assert ".000</span>" not in html


def test_names_are_escaped(theme) -> None:
    """A competitor's name is untrusted input, same rule overlay_html holds."""
    html = stage_card_html(_stage_card(shooter_name='Ann "quote" <b>Berg</b>'), theme=theme)
    assert "<b>Berg</b>" not in html
    assert "&lt;b&gt;Berg&lt;/b&gt;" in html


def test_match_card_lists_every_roster_entry(theme) -> None:
    card = MatchCard(
        match_name="Tallmilan 2026",
        match_date="2026-04-26",
        stage_count=7,
        roster=[
            RosterEntry(name="Petra Lind", division="Standard"),
            RosterEntry(name="Anders Berg", division="Production Optics"),
        ],
    )
    html = match_card_html(card, theme=theme)
    assert "Anders Berg" in html
    assert "Petra Lind" in html
    assert "Tallmilan 2026" in html
    # No invented aggregate: the match card carries no summed stage time.
    assert "Total time" not in html


def test_match_card_survives_an_empty_roster(theme) -> None:
    card = MatchCard(match_name="Tallmilan 2026", match_date=None, stage_count=7, roster=[])
    html = match_card_html(card, theme=theme)
    assert "Tallmilan 2026" in html


def test_every_text_box_hides_overflow(theme) -> None:
    """The categorical fix overlay_html.py exists for: nothing a
    descendant does can paint outside its own box."""
    html = stage_card_html(_stage_card(), theme=theme)
    assert html.count("overflow: hidden") >= 2


def test_bundled_font_faces_are_declared(theme) -> None:
    html = stage_card_html(_stage_card(), theme=theme)
    assert "@font-face" in html
    assert "Antonio" in html
    assert "JetBrains Mono" in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_share_card_html.py -n0 -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.share_card_html'`

- [ ] **Step 3: Write the implementation**

Create `src/splitsmith/share_card_html.py`:

```python
"""Card model to a 1200x630 HTML document (spec 2026-08-09).

**This module is pure.** No browser, no Playwright import, no file I/O
beyond resolving a path string for ``@font-face``. Rasterizing what it
returns is ``overlay_raster.py``'s job.

Two constraints carry over verbatim from ``overlay_html.py``, both
load-bearing:

- The ``@font-face`` ``src`` is a ``file://`` URL naming a bundled TTF,
  and the rasterizer must NAVIGATE to a written document rather than
  calling ``page.set_content()``. That module's docstring records the
  measurement: under ``set_content`` the custom face silently fails to
  load and Chromium substitutes a host font, with no error and no
  exception.
- Every box sets ``overflow: hidden`` and long strings clamp in CSS.
  Nothing here measures text or decides a size in Python -- that
  arithmetic is exactly what kept reappearing as a defect in the
  pre-browser fitter.

A competitor's name is untrusted input, so every interpolated string
goes through :func:`html.escape`.
"""

from __future__ import annotations

from html import escape

from .overlay_html import _font_face_url
from .overlay_theme import RGB, OverlayTheme
from .share_card import MatchCard, StageCard

CARD_WIDTH = 1200
CARD_HEIGHT = 630


def _rgb(value: RGB) -> str:
    r, g, b = value
    return f"#{r:02x}{g:02x}{b:02x}"


_BRAND_MARK = (
    '<svg viewBox="0 0 36 36" width="36" height="36" fill="none" aria-hidden="true">'
    '<rect x="1.5" y="1.5" width="33" height="33" rx="7" fill="{surface}" stroke="{rule}" stroke-width="1"/>'
    '<rect x="10" y="8" width="3" height="20" rx="1.2" fill="{ink}"/>'
    '<rect x="23" y="8" width="3" height="20" rx="1.2" fill="{ink}"/>'
    '<circle cx="18" cy="18" r="2.4" fill="{accent}"/>'
    "</svg>"
)


def _style(theme: OverlayTheme) -> str:
    return f"""<style>
@font-face {{ font-family: "Antonio"; src: url({_font_face_url("Antonio-VariableFont.ttf")});
             font-weight: 400 700; font-display: block; }}
@font-face {{ font-family: "JetBrains Mono"; src: url({_font_face_url("JetBrainsMono-Bold.ttf")});
             font-weight: 700; font-display: block; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
.card {{ width: {CARD_WIDTH}px; height: {CARD_HEIGHT}px; overflow: hidden;
         display: flex; flex-direction: column; padding: 56px 72px;
         background: linear-gradient(to bottom, {_rgb(theme.surface)}, {_rgb(theme.stroke)});
         color: {_rgb(theme.ink)}; font-family: sans-serif; }}
.top {{ display: flex; align-items: center; justify-content: space-between; overflow: hidden; }}
.brand {{ display: flex; align-items: center; gap: 14px; overflow: hidden; }}
.wordmark {{ font-family: "Antonio"; font-weight: 700; font-size: 28px; line-height: 0.9;
             text-transform: uppercase; letter-spacing: -0.02em; }}
.kick {{ font-family: "JetBrains Mono"; font-weight: 700; font-size: 15px; letter-spacing: 0.2em;
         text-transform: uppercase; color: {_rgb(theme.subtle)}; overflow: hidden;
         white-space: nowrap; text-overflow: ellipsis; }}
.hot {{ color: {_rgb(theme.accent)}; }}
.body {{ flex: 1; display: flex; align-items: center; gap: 56px; overflow: hidden; }}
.display {{ font-family: "Antonio"; font-weight: 700; text-transform: uppercase;
            line-height: 0.92; letter-spacing: -0.01em; overflow: hidden;
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
.num {{ font-family: "JetBrains Mono"; font-weight: 700; font-variant-numeric: tabular-nums;
        letter-spacing: -0.03em; line-height: 0.86; }}
.dim {{ color: {_rgb(theme.muted)}; }}
.vrule {{ width: 1px; align-self: stretch; margin: 14px 0; background: {_rgb(theme.rule)}; }}
.hrule {{ height: 1px; background: {_rgb(theme.rule)}; margin-bottom: 22px; }}
.figs {{ display: flex; gap: 48px; overflow: hidden; }}
.fig {{ display: flex; flex-direction: column; gap: 8px; overflow: hidden; }}
.fig .v {{ font-size: 128px; }}
.col {{ display: flex; flex-direction: column; gap: 14px; flex: 1; overflow: hidden; }}
.roster {{ display: flex; flex-direction: column; gap: 14px; width: 430px; overflow: hidden; }}
.rrow {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px;
         overflow: hidden; }}
</style>"""


def _document(theme: OverlayTheme, body: str) -> str:
    mark = _BRAND_MARK.format(
        surface=_rgb(theme.surface),
        rule=_rgb(theme.rule),
        ink=_rgb(theme.ink),
        accent=_rgb(theme.accent),
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"{_style(theme)}</head><body>"
        f'<div class="card">{body.replace("{MARK}", mark)}</div>'
        "</body></html>"
    )


#: Colours come from the stylesheet, so this is a constant, not a function.
_FOOTER = (
    '<div class="hrule"></div>'
    '<div class="kick">Per-shot splits from stage video '
    '<span class="hot">&middot;</span> splitsmith.app</div>'
)


def match_card_html(card: MatchCard, *, theme: OverlayTheme) -> str:
    """Identity plus roster. Carries no aggregate time by design."""
    meta = [f"{card.stage_count} stages"]
    if card.match_date:
        meta.append(escape(card.match_date))
    rows = "".join(
        f'<div class="rrow"><div class="display" style="font-size:34px">{escape(r.name)}</div>'
        f'<div class="kick">{escape(r.division or "")}</div></div>'
        for r in card.roster
    )
    label = f"{len(card.roster)} shooters" if len(card.roster) != 1 else "Shooter"
    body = (
        '<div class="top"><div class="brand">{MARK}'
        '<div class="wordmark">Splitsmith</div></div>'
        f'<div class="kick">{" &middot; ".join(meta)}</div></div>'
        '<div class="body">'
        f'<div class="col"><div class="display" style="font-size:96px">'
        f"{escape(card.match_name)}</div></div>"
        '<div class="vrule"></div>'
        f'<div class="roster"><div class="kick">{escape(label)}</div>{rows}</div>'
        "</div>" + _FOOTER
    )
    return _document(theme, body)


def stage_card_html(card: StageCard, *, theme: OverlayTheme) -> str:
    """Draw and average split, the two numbers splitsmith computes.

    The average numeral is dropped entirely when there is nothing to
    average -- the figures sit in a flex row, so removing one closes the
    layout up. Never a zero or a dash standing in for a real figure.
    """
    figs = []
    if card.figures.draw is not None:
        figs.append(_figure(f"{card.figures.draw:.2f}", "Draw"))
    if card.figures.avg_split is not None:
        caption = f"Avg split &middot; {card.figures.split_count} of {card.figures.interval_count}"
        figs.append(_figure(f"{card.figures.avg_split:.3f}", caption))

    meta = [f"Stage {card.stage_number}", f"{card.shot_count} shots"]
    if card.stage_time is not None:
        meta.append(f"{card.stage_time:.2f}s")

    body = (
        '<div class="top"><div class="brand">{MARK}'
        '<div class="wordmark">Splitsmith</div></div>'
        f'<div class="kick">{" &middot; ".join(escape(m) for m in meta)}</div></div>'
        f'<div class="body"><div class="figs">{"".join(figs)}</div>'
        '<div class="vrule"></div>'
        f'<div class="col"><div class="display" style="font-size:44px">'
        f"{escape(card.stage_name)}</div>"
        f'<div class="kick">{escape(card.shooter_name)}</div>'
        f'<div class="kick">{escape(card.match_name)}</div></div>'
        "</div>" + _FOOTER
    )
    return _document(theme, body)


def _figure(value: str, caption: str) -> str:
    """One numeral block. ``caption`` is already-escaped markup."""
    whole, _, frac = value.partition(".")
    return (
        f'<div class="fig"><div class="num v">{escape(whole)}'
        f'<span class="dim">.{escape(frac)}</span></div>'
        f'<div class="kick">{caption}</div></div>'
    )
```

`_font_face_url` is private to `overlay_html`. Check its exact signature
before writing -- if it takes something other than a bare filename,
adjust the two calls. If importing a private name across modules reads
badly to the reviewer, promote it to `font_face_url` in `overlay_html`
and update that module's two call sites in the same commit.

Note `_figure` takes `caption` as markup rather than text so the
`&middot;` survives; the only values passed are built here, never from
user input.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_share_card_html.py -n0 -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/share_card_html.py tests/test_share_card_html.py
git commit -m "feat(share): 1200x630 card HTML, pure and browser-free"
```

---

### Task 4b: Consume main's canonical split rule

Reconciliation, not new behaviour. #774 landed on main while this branch was in
flight and closed issue #772, putting the split rule in
`splitsmith.coach.statistic_splits` (mirrored in TS as `statisticSplits`). Task 1
had implemented the same rule independently. Two definitions of one rule is the
exact outcome both the spec and #772 exist to prevent, so this branch drops its
copy.

The duplication is wider than the rule itself: main also taught
`audit_data.audit_shots_to_engine_shots` to carry `interval_class` onto the engine
`Shot`, which already has `.split` and therefore satisfies `coach.SplitStatInterval`.
That makes Task 1's `Interval` and `intervals_from_audit_shots` redundant too.

**One behavioural change comes with this.** Main's rule takes the classified path
as soon as ANY interval carries a class; Task 1 implemented all-or-nothing. On a
partially classified stage the two disagree. Main's is shipped and canonical, so
the card adopts it. The disagreement is filed as **#775** and is not settled here
— do not "fix" main's semantics in this task.

**Files:**
- Modify: `src/splitsmith/share_card.py`
- Modify: `tests/test_share_card_figures.py`

**Interfaces:**
- Consumes: `splitsmith.coach.statistic_splits`, `coach.SplitStatInterval`,
  `coach.SPLIT_STAT_TRANSITION_MIN`.
- Produces: `stage_figures(shots: Sequence[SplitStatInterval], *, transition_min: float = SPLIT_STAT_TRANSITION_MIN) -> StageFigures`
- Deletes: `Interval`, `intervals_from_audit_shots`, `DRAW_CLASS`, `SPLIT_CLASS`,
  and the inline rule inside `stage_figures`.
- `StageFigures` keeps its shape exactly: `draw`, `avg_split`, `split_count`,
  `interval_count`, `source`. Tasks 2, 4, 5, 6 and 9 all read those fields.

- [ ] **Step 1: Rewrite the tests first**

Replace the rule-testing half of `tests/test_share_card_figures.py`. The tests that
must go are the ones asserting a rule this module no longer owns: the all-or-nothing
test contradicts main outright, and the two `intervals_from_audit_shots` tests cover
a deleted function. What stays is what `StageFigures` still decides: the draw, the
mean, the counts, and `source`.

```python
"""Share-card figures. The RULE lives in ``coach.statistic_splits`` (#774);
this module only shapes its output into a card's two headline numbers, so
these tests assert the shaping, not the rule."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from splitsmith.share_card import StageFigures, stage_figures


@dataclass(frozen=True)
class _Shot:
    """The two attributes ``coach.SplitStatInterval`` reads. A real engine
    ``Shot`` needs six more required fields that no card looks at."""

    split: float
    interval_class: str | None


# Draw 1.28, nine splits (mean 0.182), two transitions, one movement, one
# reload. The intervals sum to 14.74 s.
_SECONDS = [1.28, 0.19, 0.17, 0.22, 1.85, 0.16, 0.18, 2.42, 0.21, 0.15, 5.45, 0.20, 0.16, 2.10]
_CLASSES = [
    "first_shot", "split", "split", "split", "transition", "split", "split",
    "reload", "split", "split", "movement", "split", "split", "transition",
]


def _classified() -> tuple[_Shot, ...]:
    return tuple(_Shot(split=s, interval_class=c) for s, c in zip(_SECONDS, _CLASSES, strict=True))


def _unclassified() -> tuple[_Shot, ...]:
    return tuple(_Shot(split=s, interval_class=None) for s in _SECONDS)


def test_classified_stage_reports_the_draw_and_the_split_mean() -> None:
    figs = stage_figures(_classified())
    assert figs.source == "coach"
    assert figs.draw == pytest.approx(1.28)
    assert figs.avg_split == pytest.approx(0.182, abs=5e-4)
    assert figs.split_count == 9
    assert figs.interval_count == 14


def test_unclassified_stage_falls_back_through_the_shared_helper() -> None:
    figs = stage_figures(_unclassified())
    assert figs.source == "threshold"
    assert figs.draw == pytest.approx(1.28)
    assert figs.split_count == 9
    assert figs.avg_split == pytest.approx(0.182, abs=5e-4)


def test_the_fallback_excludes_a_draw_faster_than_the_threshold() -> None:
    """A Production Optics draw can land under transition_min, so duration
    alone cannot exclude it -- index 0 must. Guards the shared helper's
    fallback branch through this module's own surface."""
    shots = (
        _Shot(split=0.90, interval_class=None),
        _Shot(split=0.20, interval_class=None),
        _Shot(split=0.20, interval_class=None),
        _Shot(split=0.20, interval_class=None),
    )
    figs = stage_figures(shots)
    assert figs.draw == pytest.approx(0.90)
    assert figs.split_count == 3
    assert figs.avg_split == pytest.approx(0.20)


def test_partial_classification_follows_mains_any_rule_see_issue_775() -> None:
    """This branch's spec argued for all-or-nothing; main counts the
    classified intervals as soon as ANY interval carries a class. Main is
    canonical, so the card follows it. The disagreement is issue #775 --
    if that issue changes the rule, this test changes with it."""
    shots = (
        _Shot(split=1.28, interval_class="first_shot"),
        _Shot(split=0.19, interval_class="split"),
        _Shot(split=0.80, interval_class=None),
    )
    figs = stage_figures(shots)
    assert figs.source == "coach"
    assert figs.split_count == 1
    assert figs.avg_split == pytest.approx(0.19)


def test_a_stage_of_pure_dead_time_reports_a_draw_but_no_average() -> None:
    shots = (
        _Shot(split=1.28, interval_class="first_shot"),
        _Shot(split=2.40, interval_class="reload"),
    )
    figs = stage_figures(shots)
    assert figs.draw == pytest.approx(1.28)
    assert figs.avg_split is None
    assert figs.split_count == 0


def test_no_shots_yields_empty_source_and_no_figures() -> None:
    figs = stage_figures(())
    assert figs == StageFigures(
        draw=None, avg_split=None, split_count=0, interval_count=0, source="empty"
    )
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_share_card_figures.py -n0 -q`
Expected: failures — `stage_figures` still takes `Interval` objects with `.seconds`
and still applies all-or-nothing.

- [ ] **Step 3: Rewrite `stage_figures` as a wrapper**

In `src/splitsmith/share_card.py`, delete `Interval`, `intervals_from_audit_shots`,
`DRAW_CLASS` and `SPLIT_CLASS`, and replace the rule with delegation:

```python
def stage_figures(
    shots: Sequence[SplitStatInterval],
    *,
    transition_min: float = SPLIT_STAT_TRANSITION_MIN,
) -> StageFigures:
    """The two headline figures a stage card shows, plus their provenance.

    **This function does not own the split rule.** ``coach.statistic_splits``
    does (issue #772, landed in #774), mirrored in TS by ``statisticSplits``.
    All this adds is the card's shape: the draw, the mean of whatever the
    shared helper returned, and how it was derived.

    ``shots`` is one stage's full time-ordered sequence, draw first --
    ``config.Shot`` from ``audit_data.audit_shots_to_engine_shots`` satisfies
    the protocol. The draw is ``shots[0].split``, matching the helper's own
    "index 0 is the draw" convention.

    ``avg_split`` is None rather than zero when the helper returns nothing:
    a stage of transitions and reloads has no splits to average, and the
    card renders no average rather than inventing one.
    """
    if not shots:
        return StageFigures(
            draw=None, avg_split=None, split_count=0, interval_count=0, source="empty"
        )
    splits = statistic_splits(shots, transition_min=transition_min)
    classified = any(s.interval_class is not None for s in shots)
    return StageFigures(
        draw=shots[0].split,
        avg_split=(sum(splits) / len(splits)) if splits else None,
        split_count=len(splits),
        interval_count=len(shots),
        # Mirrors the helper's own branch condition. If #775 changes that
        # condition, this line changes with it -- they must not drift.
        source="coach" if classified else "threshold",
    )
```

Update the module docstring: it currently claims to be the one definition. It is
not any more, and saying so is the point of this task. Point it at
`coach.statistic_splits` and at #775 for the partial-classification question.

Imports to add (local group): `from .coach import SPLIT_STAT_TRANSITION_MIN, SplitStatInterval, statistic_splits`.
Drop `Mapping` from the typing imports if nothing else uses it.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_share_card_figures.py tests/test_share_card_models.py tests/test_share_card_html.py -n0 -q`
Expected: PASS.

- [ ] **Step 5: Prove the delegation is real**

Break `coach.statistic_splits` — make it return `[]` unconditionally — and re-run
`tests/test_share_card_figures.py`. Tests must fail. If they pass, `share_card`
still has its own copy of the rule somewhere and the task is not done. Revert and
confirm green.

This is the whole point of the task, so it is not optional.

- [ ] **Step 6: Confirm nothing else imports the deleted names**

Run: `rg -n 'intervals_from_audit_shots|share_card import.*Interval|DRAW_CLASS|SPLIT_CLASS' src tests`
Expected: no hits.

- [ ] **Step 7: Run the coach and audit suites too**

`share_card` now depends on `coach`. Run:
`uv run pytest tests/test_coach_classify.py tests/test_audit_data.py tests/test_share_card_figures.py tests/test_share_card_models.py tests/test_share_card_html.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/share_card.py tests/test_share_card_figures.py
git commit -m "refactor(share): consume the canonical split rule instead of repeating it

coach.statistic_splits (#774) landed on main while this branch was in
flight, implementing the same rule share_card had built independently.
Two definitions of one rule is what issue #772 exists to prevent, so
this deletes the copy, along with Interval and intervals_from_audit_shots
-- audit_shots_to_engine_shots now carries interval_class onto the engine
Shot, which already satisfies SplitStatInterval.

Adopts main's partial-classification semantics as a consequence; the
disagreement with this branch's spec is filed as #775.

Refs #772, #775"
```

---

### Task 5: Render-and-cache seam

**Files:**
- Create: `src/splitsmith/share_card_render.py`
- Create: `src/splitsmith/data/share_card_fallback.png`
- Test: `tests/test_share_card_render.py`

**Interfaces:**
- Consumes: Task 2's models + `card_hash`; Task 4's HTML functions; `overlay_raster.Rasterizer` (Protocol) and `overlay_raster.RasterizerUnavailableError`; `storage.Storage` (Protocol).
- Produces:
  - `storage_key(token: str, card: MatchCard | StageCard, *, slug: str | None = None) -> str`
  - `render_card(card, *, theme, rasterizer) -> bytes`
  - `cached_card_png(card, *, token, storage, theme, rasterizer_factory, slug=None) -> bytes` -- reads storage, renders and writes on a miss, returns the bundled fallback plate on `RasterizerUnavailableError`
  - `FALLBACK_PNG_PATH: Path`

**`rasterizer_factory`, not a rasterizer.** It is a zero-argument callable
returning a context manager that yields a `Rasterizer`. Launching
Chromium costs about a second; taking an already-live instance would pay
that on every cache hit and defeat the cache. The factory is only called
on a miss. Tests assert this directly.

- [ ] **Step 1: Build the fallback plate**

The error path must not need a browser, so the plate ships as a checked-in
PNG built once from the existing brand artboard.

```bash
uv run python scripts/capture_hero_og.py
cp site/og.png src/splitsmith/data/share_card_fallback.png
```

Verify it is 1200x630:

```bash
uv run python -c "
import struct, pathlib
d = pathlib.Path('src/splitsmith/data/share_card_fallback.png').read_bytes()
print(struct.unpack('>II', d[16:24]))
"
```
Expected: `(1200, 630)`

If `capture_hero_og.py` cannot run (it needs `playwright install chromium`
without `--only-shell`), `site/og.png` is already checked in at those
dimensions -- copy it directly.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_share_card_render.py`:

```python
"""Render-and-cache seam. A fake Rasterizer keeps these browser-free."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from splitsmith.overlay_raster import RasterizerUnavailableError
from splitsmith.overlay_theme import load_theme
from splitsmith.share_card import MatchCard, RosterEntry, StageCard, stage_figures
from splitsmith.share_card_render import (
    FALLBACK_PNG_PATH,
    cached_card_png,
    storage_key,
)
from splitsmith.storage import FilesystemStorage

TOKEN = "tok_abc123"


@dataclass(frozen=True)
class _Shot:
    """Minimal stand-in for ``config.Shot``: the two attributes
    ``coach.SplitStatInterval`` reads. Building a real engine ``Shot``
    here would mean six irrelevant required fields."""

    split: float
    interval_class: str | None


class _FakeRasterizer:
    """Counts calls so a cache hit is provable, not assumed."""

    def __init__(self, payload: bytes = b"\x89PNG-fake") -> None:
        self.payload = payload
        self.calls = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls += 1
        assert (width, height) == (1200, 630)
        return self.payload


class _BrokenRasterizer:
    def __init__(self) -> None:
        self.calls = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls += 1
        raise RasterizerUnavailableError("no chromium", "no chromium, run the install hint")


class _Factory:
    """Zero-arg callable returning a context manager, standing in for
    ``ChromiumRasterizer``. ``launches`` counts how many times a browser
    would actually have been started -- a cache hit must not start one."""

    def __init__(self, rasterizer: object) -> None:
        self.rasterizer = rasterizer
        self.launches = 0

    def __call__(self) -> "_Factory":
        self.launches += 1
        return self

    def __enter__(self) -> object:
        return self.rasterizer

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def theme():
    return load_theme("splitsmith")


@pytest.fixture
def store(tmp_path):
    return FilesystemStorage(tmp_path)


def _match_card() -> MatchCard:
    return MatchCard(
        match_name="Tallmilan 2026",
        match_date="2026-04-26",
        stage_count=7,
        roster=[RosterEntry(name="Mathias Axell", division="Production Optics")],
    )


def _stage_card() -> StageCard:
    return StageCard(
        stage_number=3,
        stage_name="Per told me to do it!",
        shooter_name="Mathias Axell",
        match_name="Tallmilan 2026",
        shot_count=3,
        stage_time=14.74,
        figures=stage_figures(
            (
                _Shot(split=1.28, interval_class="first_shot"),
                _Shot(split=0.19, interval_class="split"),
            )
        ),
    )


def test_storage_key_is_scoped_by_token_and_carries_the_hash() -> None:
    key = storage_key(TOKEN, _match_card())
    assert key.startswith(f"share-cards/{TOKEN}/match-")
    assert key.endswith(".png")


def test_stage_key_carries_slug_and_stage_number() -> None:
    key = storage_key(TOKEN, _stage_card(), slug="mathias")
    assert key.startswith(f"share-cards/{TOKEN}/stage-mathias-3-")


def test_first_call_renders_and_writes(store, theme) -> None:
    ras = _FakeRasterizer()
    factory = _Factory(ras)
    data = cached_card_png(
        _match_card(), token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory
    )
    assert data == ras.payload
    assert ras.calls == 1
    assert store.exists(storage_key(TOKEN, _match_card()))


def test_second_call_serves_the_cache_without_rendering(store, theme) -> None:
    ras = _FakeRasterizer()
    factory = _Factory(ras)
    card = _match_card()
    first = cached_card_png(
        card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory
    )
    second = cached_card_png(
        card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory
    )
    assert first == second
    assert ras.calls == 1


def test_a_cache_hit_never_launches_a_browser(store, theme) -> None:
    """Launching Chromium costs about a second. Paying that on a hit
    would defeat the cache, so the factory must stay uncalled."""
    factory = _Factory(_FakeRasterizer())
    card = _match_card()
    cached_card_png(card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory)
    assert factory.launches == 1
    cached_card_png(card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory)
    assert factory.launches == 1


def test_changed_figures_miss_the_cache_and_re_render(store, theme) -> None:
    ras = _FakeRasterizer()
    factory = _Factory(ras)
    card = _stage_card()
    cached_card_png(
        card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory, slug="m"
    )
    moved = card.model_copy(update={"stage_name": "Short and Sweet"})
    cached_card_png(
        moved, token=TOKEN, storage=store, theme=theme, rasterizer_factory=factory, slug="m"
    )
    assert ras.calls == 2


def test_rasterizer_failure_serves_the_bundled_plate(store, theme) -> None:
    ras = _BrokenRasterizer()
    data = cached_card_png(
        _match_card(), token=TOKEN, storage=store, theme=theme, rasterizer_factory=_Factory(ras)
    )
    assert data == FALLBACK_PNG_PATH.read_bytes()
    assert ras.calls == 1


def test_rasterizer_failure_does_not_poison_the_cache(store, theme) -> None:
    """A later working render must still be able to fill the key."""
    card = _match_card()
    cached_card_png(
        card,
        token=TOKEN,
        storage=store,
        theme=theme,
        rasterizer_factory=_Factory(_BrokenRasterizer()),
    )
    assert not store.exists(storage_key(TOKEN, card))
    good = _FakeRasterizer()
    data = cached_card_png(
        card, token=TOKEN, storage=store, theme=theme, rasterizer_factory=_Factory(good)
    )
    assert data == good.payload


def test_the_bundled_plate_is_a_1200x630_png() -> None:
    import struct

    raw = FALLBACK_PNG_PATH.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", raw[16:24]) == (1200, 630)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_share_card_render.py -n0 -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.share_card_render'`

- [ ] **Step 4: Write the implementation**

Create `src/splitsmith/share_card_render.py`:

```python
"""Render a share card and cache it, content-addressed (spec 2026-08-09).

The impure seam between the pure card model / HTML and the outside
world. The rasterizer arrives through ``overlay_raster.Rasterizer`` (a
Protocol, not a concrete import) so unit tests inject a fake and never
launch Chromium -- the same seam ``compare.mp4_grid.Runner`` uses.

**Content addressing is what makes the freshness problem disappear.**
The storage key carries a hash of everything the card displays, and the
``og:image`` URL is built from live data at request time. A re-audit
moves the figures, which moves the hash, which moves the URL -- so
Slack and X refetch rather than serving a preview of numbers nobody has
any more. Nothing needs invalidating.

A render failure never reaches the crawler as a 500: it serves the
bundled plate and leaves the cache key empty, so the next request with a
working browser still fills it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from importlib.resources import files
from pathlib import Path

from .overlay_raster import Rasterizer, RasterizerUnavailableError
from .overlay_theme import OverlayTheme
from .share_card import MatchCard, StageCard, card_hash
from .share_card_html import CARD_HEIGHT, CARD_WIDTH, match_card_html, stage_card_html
from .storage import Storage

logger = logging.getLogger(__name__)

#: Static plate served when Chromium cannot run. Built once from
#: ``scripts/og/og.html`` and checked in, so the error path needs no
#: browser of its own.
FALLBACK_PNG_PATH: Path = Path(str(files("splitsmith.data") / "share_card_fallback.png"))


def storage_key(token: str, card: MatchCard | StageCard, *, slug: str | None = None) -> str:
    """Content-addressed object key, scoped to the share token."""
    digest = card_hash(card)
    if isinstance(card, StageCard):
        return f"share-cards/{token}/stage-{slug}-{card.stage_number}-{digest}.png"
    return f"share-cards/{token}/match-{digest}.png"


def render_card(
    card: MatchCard | StageCard, *, theme: OverlayTheme, rasterizer: Rasterizer
) -> bytes:
    """Card model to PNG bytes. Raises ``RasterizerUnavailableError``."""
    html = (
        stage_card_html(card, theme=theme)
        if isinstance(card, StageCard)
        else match_card_html(card, theme=theme)
    )
    return rasterizer.png(html, width=CARD_WIDTH, height=CARD_HEIGHT)


def cached_card_png(
    card: MatchCard | StageCard,
    *,
    token: str,
    storage: Storage,
    theme: OverlayTheme,
    rasterizer_factory: Callable[[], AbstractContextManager[Rasterizer]],
    slug: str | None = None,
) -> bytes:
    """Serve the cached PNG, rendering and writing it on a miss.

    ``rasterizer_factory`` is called ONLY on a miss. Launching Chromium
    costs about a second; taking a live rasterizer instead would pay that
    on every cache hit and leave the cache saving nothing but CPU.
    """
    key = storage_key(token, card, slug=slug)
    if storage.exists(key):
        return storage.read_bytes(key)
    try:
        with rasterizer_factory() as rasterizer:
            data = render_card(card, theme=theme, rasterizer=rasterizer)
    except RasterizerUnavailableError as exc:
        # Deliberately not cached: a browser-less host must not pin the
        # fallback plate onto this key forever.
        logger.warning("share card render unavailable, serving fallback plate: %s", exc.detail)
        return FALLBACK_PNG_PATH.read_bytes()
    storage.write_bytes(key, data)
    return data
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_share_card_render.py -n0 -q`
Expected: PASS, 10 tests.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/share_card_render.py \
        src/splitsmith/data/share_card_fallback.png \
        tests/test_share_card_render.py
git commit -m "feat(share): content-addressed card render cache with a browser-free fallback"
```

---

### Task 6: PNG routes

**Files:**
- Create: `src/splitsmith/ui/share_og.py`
- Modify: `src/splitsmith/ui/server.py:936` (`_SHARE_PATH_RE`)
- Modify: `src/splitsmith/ui/server.py` (register the router beside `sync_router` / `device_router`, around line 14528)
- Test: `tests/test_share_og_routes.py`

**Interfaces:**
- Consumes: Task 5's `cached_card_png`, `storage_key`; Task 1/2's builders.
- Produces:
  - `router: APIRouter` exporting
    `GET /api/share/{token}/og.png` and
    `GET /api/share/{token}/og/{slug}/{stage}.png`
  - `build_match_card(state) -> MatchCard` and
    `build_stage_card(state, slug, stage) -> StageCard | None`

`_SHARE_PATH_RE` is the containment boundary for the whole anonymous
surface -- the share middleware impersonates the owner's tenant, so only
read-only, match-scoped routes that never let the client supply a match
id belong in it. Both new routes qualify.

- [ ] **Step 1: Extend the allowlist**

In `src/splitsmith/ui/server.py`, add two alternatives to `_SHARE_PATH_RE`:

```python
_SHARE_PATH_RE = re.compile(
    r"^(?:match/shooters"
    r"|shooters/[^/]+/project"
    r"|shooters/[^/]+/stages/\d+/coach"
    r"|shooters/[^/]+/coach/distributions"
    r"|shooters/[^/]+/videos/stream"
    r"|og\.png"
    r"|og/[^/]+/\d+\.png)$"
)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_share_og_routes.py`, modelled on `tests/test_share_routes.py`:

```python
"""OG PNG routes on the anonymous share surface (spec 2026-08-09)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login, seed_match

MID = "test-match-og001"
SLUG = "anna"


def _create_share(client: TestClient) -> str:
    resp = client.post(f"/api/matches/{MID}/match/shares")
    assert resp.status_code == 201
    return resp.json()["url"].rsplit("/", 1)[-1]


def test_match_png_is_reachable_anonymously(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    token = _create_share(client)
    client.cookies.clear()

    resp = client.get(f"/api/share/{token}/og.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_unknown_token_png_is_404(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, _ = hosted_app
    assert client.get("/api/share/not-a-real-token/og.png").status_code == 404


def test_revoked_token_png_is_404(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    resp = client.post(f"/api/matches/{MID}/match/shares")
    token = resp.json()["url"].rsplit("/", 1)[-1]
    client.delete(f"/api/matches/{MID}/match/shares/{resp.json()['id']}")
    client.cookies.clear()

    assert client.get(f"/api/share/{token}/og.png").status_code == 404


def test_a_path_outside_the_allowlist_is_still_404(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    """The allowlist widened by exactly two shapes, not by a prefix."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    token = _create_share(client)
    client.cookies.clear()

    # A non-numeric stage does not match the allowlist shape, so the
    # middleware 404s before routing. (A "../.." path is not used here:
    # httpx normalises it client-side, so that assertion could not fail.)
    assert client.get(f"/api/share/{token}/og/{SLUG}/abc.png").status_code == 404
    assert client.get(f"/api/share/{token}/ogx.png").status_code == 404


def test_stage_png_for_an_unknown_stage_falls_back_to_the_match_card(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    token = _create_share(client)
    client.cookies.clear()

    match_png = client.get(f"/api/share/{token}/og.png").content
    stage_png = client.get(f"/api/share/{token}/og/{SLUG}/99.png").content
    assert stage_png == match_png


def test_png_routes_404_outside_hosted_mode(tmp_path) -> None:
    """There is no shared local-mode client fixture in conftest.py, so
    build one the way tests/test_ui_server.py does."""
    from splitsmith.ui.server import create_app

    app = create_app(project_root=tmp_path / "match", project_name="Test Match")
    local = TestClient(app)
    assert local.get("/api/share/anything/og.png").status_code == 404
```

`hosted_env` and `hosted_app` come from `tests/hosted_helpers.py`, not
`conftest.py` -- import them from there, as `tests/test_share_routes.py`
already does. There is no local-mode `client` fixture; the last test
builds its own app.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_share_og_routes.py -n0 -q`
Expected: FAIL with 404s on the routes that should succeed.

- [ ] **Step 4: Write the router**

Create `src/splitsmith/ui/share_og.py`. Follow the lazy-import,
always-registered idiom of `device_auth_api.py`: a module-level
`router = APIRouter()`, a `_hosted_gate()` that imports
`_hosted_mode_active` from `.server` inside the function body, and db
imports kept inside function bodies so a local-slim install still imports
the module.

The two handlers:

- Resolve `state` from `request.app.state.splitsmith_state`.
- 503 when `state.storage is None`, matching the idiom at
  `ui/server.py:6819`.
- Build the card via `build_match_card` / `build_stage_card`, which read
  through `state.shooter_project`, `state.load_audit` and
  `state.shooters()` -- the same accessors the coach route uses. The
  share middleware has already pinned the tenant and match, so neither
  handler takes a match id from the client.
- `build_stage_card` returns `None` for an unknown stage, an unknown
  slug, or a stage whose audit yields no intervals; the handler then
  serves the match card instead.
Write it as:

```python
"""Hosted-only share-card routes: og:* meta on the share shells and the
card PNGs themselves (spec 2026-08-09).

Same lazy-import, always-registered idiom as ``sync_api`` and
``device_auth_api``: db and rendering imports stay inside function
bodies so a local-slim install still imports this module, and every
route 404s outside hosted mode.

The two PNG paths are on the anonymous share surface, so they are also
listed in ``server._SHARE_PATH_RE`` -- that regex is the containment
boundary, and both routes qualify: read-only, match-scoped, and the
client never supplies a match id.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ..overlay_theme import load_theme
from ..audit_data import audit_shots_to_engine_shots
from ..share_card import (
    MatchCard,
    RosterEntry,
    StageCard,
    stage_figures,
)
from ..share_card_render import cached_card_png

logger = logging.getLogger(__name__)

router = APIRouter()


def _hosted_gate() -> None:
    """Raise 404 outside hosted mode. Lazy import, same as sync_api."""
    from .server import _hosted_mode_active

    if not _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")


def _state(request: Request) -> Any:
    return request.app.state.splitsmith_state


def build_match_card(state: Any) -> MatchCard:
    """Identity plus roster. No aggregate time figure by design."""
    project_meta = state.match_meta()
    roster = [
        RosterEntry(name=s.get("name") or s["slug"], division=s.get("division"))
        for s in state.shooters()
    ]
    return MatchCard(
        match_name=project_meta.get("name") or "Splitsmith match",
        match_date=project_meta.get("date"),
        stage_count=len(project_meta.get("stages") or []),
        roster=roster,
    )


def build_stage_card(state: Any, slug: str, stage_number: int) -> StageCard | None:
    """``None`` for an unknown slug or stage, or a stage with no shots --
    the caller then serves the match card instead."""
    try:
        project = state.shooter_project(slug)
        stg = project.stage(stage_number)
    except (KeyError, ValueError):
        return None
    payload, _version = state.load_audit(slug, stage_number)
    if not payload:
        return None
    # main's canonical converter (#774) -- it carries interval_class onto
    # each engine Shot, which is what makes the rule reachable here. The
    # beep offset only affects ``time_absolute``, which no card reads, so
    # 0.0 is correct rather than merely harmless.
    shots = audit_shots_to_engine_shots(payload, beep_time_in_source=0.0)
    if not shots:
        return None
    figures = stage_figures(shots)
    shooter = next((s for s in state.shooters() if s["slug"] == slug), {})
    return StageCard(
        stage_number=stage_number,
        stage_name=getattr(stg, "name", "") or f"Stage {stage_number}",
        shooter_name=shooter.get("name") or slug,
        match_name=state.match_meta().get("name") or "Splitsmith match",
        shot_count=len(intervals),
        stage_time=getattr(stg, "time_seconds", None),
        figures=figures,
    )


_PNG_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def _png_response(state: Any, token: str, card: Any, slug: str | None) -> Response:
    if state.storage is None:
        raise HTTPException(status_code=503, detail="storage unavailable")
    data = cached_card_png(
        card,
        token=token,
        storage=state.storage,
        theme=load_theme("splitsmith"),
        # Passed as a factory, not an instance: a cache hit must not pay
        # Chromium's ~1 s launch. Lazy import keeps a local-slim install
        # from importing playwright at module load.
        rasterizer_factory=_chromium_factory,
        slug=slug,
    )
    return Response(content=data, media_type="image/png", headers=_PNG_HEADERS)


def _chromium_factory():
    from ..overlay_raster import ChromiumRasterizer

    return ChromiumRasterizer()


@router.get("/api/share/{token}/og.png", include_in_schema=False)
def share_match_png(token: str, request: Request) -> Response:
    _hosted_gate()
    state = _state(request)
    return _png_response(state, token, build_match_card(state), None)


@router.get("/api/share/{token}/og/{slug}/{stage}.png", include_in_schema=False)
def share_stage_png(token: str, slug: str, stage: int, request: Request) -> Response:
    _hosted_gate()
    state = _state(request)
    card = build_stage_card(state, slug, stage)
    if card is None:
        return _png_response(state, token, build_match_card(state), None)
    return _png_response(state, token, card, slug)


def warm_match_card(state: Any, token: str) -> None:
    """Render the match card at share-creation time so the link the owner
    pastes previews without a cold render. Warms the first hash rather
    than pinning it: later data changes miss the cache and re-render."""
    if state.storage is None:
        return
    cached_card_png(
        build_match_card(state),
        token=token,
        storage=state.storage,
        theme=load_theme("splitsmith"),
        rasterizer_factory=_chromium_factory,
    )
```

Confirm `state.match_meta()`, `state.shooters()` and the `stage` object's
`name` / `time_seconds` attributes against `ui/server.py` before writing
-- adjust the accessor names to whatever is actually there. The shape
above is the contract; the accessor spellings are the thing to verify.

- [ ] **Step 5: Register the router**

In `src/splitsmith/ui/server.py`, beside the existing `device_router`
registration:

```python
    # Share-link OG card PNGs (spec 2026-08-09). Same lazy-import,
    # always-registered idiom as sync_router and device_router: every
    # route 404s outside hosted mode.
    from .share_og import router as share_og_router

    app.include_router(share_og_router)
```

- [ ] **Step 6: Run the tests, plus everything downstream of the allowlist**

`_SHARE_PATH_RE` is the containment boundary the whole anonymous share
surface routes through, so a focused run on the new file is not enough.

Run: `uv run pytest tests/test_share_og_routes.py tests/test_share_routes.py \
  tests/test_share_tokens_store.py tests/test_hosted_mode_boot.py \
  tests/test_hosted_status.py -q`
Expected: PASS, including the 6 new tests.

- [ ] **Step 7: Prove the allowlist test can fail**

Temporarily revert `_SHARE_PATH_RE` to its original value.
Run: `uv run pytest tests/test_share_og_routes.py -n0 -q`
Expected: FAIL on `test_match_png_is_reachable_anonymously`.
Restore the change and re-run to confirm PASS.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/ui/share_og.py src/splitsmith/ui/server.py \
        tests/test_share_og_routes.py
git commit -m "feat(share): serve OG card PNGs on the anonymous share surface"
```

---

### Task 7: Meta-tag injection on the share shells

**Files:**
- Modify: `src/splitsmith/ui/share_og.py`
- Test: `tests/test_share_og_meta.py`

**Interfaces:**
- Consumes: Task 6's card builders; `state.public_base_url`; `server.STATIC_DIR`.
- Produces: `GET /share/{token}` and `GET /share/{token}/results/{slug}/{stage}` on the same `router`.

These must be registered before the SPA catch-all at
`ui/server.py:14558`. `app.include_router` in Task 6 already runs before
that block, so ordering holds -- but the test below is what proves it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_share_og_meta.py`:

```python
"""Meta tags on the share shells. Crawlers do not run JavaScript, so
these must be in the served HTML, not rendered by React."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from tests.hosted_helpers import _CapturingSender, login, seed_match

MID = "test-match-meta01"
SLUG = "anna"


def _meta(html: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"', html
    )
    return m.group(1) if m else None


def _share_token(client: TestClient) -> str:
    resp = client.post(f"/api/matches/{MID}/match/shares")
    assert resp.status_code == 201
    return resp.json()["url"].rsplit("/", 1)[-1]


def test_match_shell_carries_og_tags(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    token = _share_token(client)
    client.cookies.clear()

    html = client.get(f"/share/{token}").text
    assert _meta(html, "og:title")
    assert _meta(html, "og:image", ).startswith("http")
    assert _meta(html, "og:image").endswith("/og.png")
    assert _meta(html, "og:image:width") == "1200"
    assert _meta(html, "og:image:height") == "630"
    assert _meta(html, "twitter:card") == "summary_large_image"


def test_share_shells_are_noindex(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    """A share link is unlisted, not public."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    token = _share_token(client)
    client.cookies.clear()

    assert _meta(client.get(f"/share/{token}").text, "robots") == "noindex"


def test_stage_shell_names_the_stage_and_points_at_the_stage_png(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    token = _share_token(client)
    client.cookies.clear()

    html = client.get(f"/share/{token}/results/{SLUG}/1").text
    assert "Stage 1" in (_meta(html, "og:title") or "")
    assert _meta(html, "og:image").endswith(f"/og/{SLUG}/1.png")


def test_revoked_and_unknown_tokens_serve_identical_shells(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    """The meta must not reveal that a token once existed."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    resp = client.post(f"/api/matches/{MID}/match/shares")
    token = resp.json()["url"].rsplit("/", 1)[-1]
    client.delete(f"/api/matches/{MID}/match/shares/{resp.json()['id']}")
    client.cookies.clear()

    revoked = client.get(f"/share/{token}").text
    unknown = client.get("/share/definitely-not-a-token").text
    assert revoked == unknown


def test_the_shell_still_serves_the_spa_bundle(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    """Meta injection must not break the app for a real browser."""
    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)
    token = _share_token(client)
    client.cookies.clear()

    resp = client.get(f"/share/{token}")
    assert resp.status_code == 200
    assert '<div id="root">' in resp.text
    assert resp.headers["cache-control"] == "no-cache"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_share_og_meta.py -n0 -q`
Expected: FAIL -- the SPA fallback serves `index.html` with no meta tags.

- [ ] **Step 3: Implement the shells**

Add to `src/splitsmith/ui/share_og.py` (add `from html import escape` and
`from fastapi.responses import HTMLResponse` to the imports):

```python
def _meta_tags(tags: dict[str, str]) -> str:
    """Render meta elements. ``og:*`` uses ``property``, everything else
    uses ``name`` -- the Open Graph spec and the HTML spec disagree, and
    Facebook's crawler only honours ``property``."""
    out = []
    for key, value in tags.items():
        attr = "property" if key.startswith("og:") else "name"
        out.append(f'<meta {attr}="{escape(key, quote=True)}" content="{escape(value, quote=True)}">')
    return "".join(out)


def _shell(tags: dict[str, str]) -> HTMLResponse:
    """The SPA shell with meta injected before ``</head>``.

    ``no-cache`` is preserved from the SPA fallback it shadows: the shell
    still points at a content-hashed bundle, and a cached shell would
    pin an old one.
    """
    from .server import STATIC_DIR

    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                "SPA bundle not built. Run `npm run build` in "
                "src/splitsmith/ui_static/ or use `npm run dev`."
            ),
        )
    html = index.read_text(encoding="utf-8")
    html = html.replace("</head>", f"{_meta_tags(tags)}</head>", 1)
    return HTMLResponse(content=html, headers={"Cache-Control": "no-cache"})


def _base_tags(image_url: str, title: str, description: str, page_url: str, alt: str) -> dict[str, str]:
    return {
        "og:title": title,
        "og:description": description,
        "og:type": "website",
        "og:url": page_url,
        "og:image": image_url,
        "og:image:width": "1200",
        "og:image:height": "630",
        "og:image:alt": alt,
        "twitter:card": "summary_large_image",
        "twitter:title": title,
        "twitter:description": description,
        "twitter:image": image_url,
        # A share link is unlisted, not public.
        "robots": "noindex",
    }


def _generic_tags() -> dict[str, str]:
    """Unknown or revoked token. Carries nothing token-derived, so the two
    cases are byte-identical and the meta never reveals that a token
    once existed."""
    return {
        "og:title": "Splitsmith",
        "og:description": "Per-shot split detection from stage video.",
        "og:type": "website",
        "robots": "noindex",
    }


def _resolved_or_none(state: Any, token: str) -> bool:
    """True iff the token resolves to a live share. Mirrors what the
    share middleware already does for the API surface."""
    from ..async_bridge import run_sync

    if state.resolve_share_token is None:
        return False
    return run_sync(state.resolve_share_token(token)) is not None


@router.get("/share/{token}", include_in_schema=False)
def share_match_shell(token: str, request: Request) -> HTMLResponse:
    _hosted_gate()
    state = _state(request)
    if not _resolved_or_none(state, token):
        return _shell(_generic_tags())
    base = state.public_base_url or ""
    card = build_match_card(state)
    shooters = ", ".join(r.name for r in card.roster) or "No shooters yet"
    return _shell(
        _base_tags(
            image_url=f"{base}/api/share/{token}/og.png",
            title=card.match_name,
            description=f"{shooters} - {card.stage_count} stages",
            page_url=f"{base}/share/{token}",
            alt=f"Splitsmith results card for {card.match_name}",
        )
    )


@router.get("/share/{token}/results/{slug}/{stage}", include_in_schema=False)
def share_stage_shell(token: str, slug: str, stage: int, request: Request) -> HTMLResponse:
    _hosted_gate()
    state = _state(request)
    if not _resolved_or_none(state, token):
        return _shell(_generic_tags())
    base = state.public_base_url or ""
    card = build_stage_card(state, slug, stage)
    if card is None:
        return share_match_shell(token, request)
    parts = [card.shooter_name]
    if card.figures.draw is not None:
        parts.append(f"draw {card.figures.draw:.2f}s")
    if card.figures.avg_split is not None:
        parts.append(f"avg split {card.figures.avg_split:.3f}s")
    parts.append(f"{card.shot_count} shots")
    return _shell(
        _base_tags(
            image_url=f"{base}/api/share/{token}/og/{slug}/{stage}.png",
            title=f"Stage {stage} - {card.stage_name}",
            description=" - ".join(parts),
            page_url=f"{base}/share/{token}/results/{slug}/{stage}",
            alt=f"Splitsmith stage card: stage {stage}, {card.shooter_name}",
        )
    )
```

Two things to verify against `ui/server.py` before writing: that
`state.resolve_share_token` is reachable from a plain request (it is set
on `AppState` at `server.py:5419`), and that the share middleware does
not also need to run for these HTML routes. It does not -- these
handlers resolve the token themselves and read only match-scoped
accessors.

`run_sync` comes from `splitsmith.async_bridge`; confirm the exact
helper name there, since these handlers are sync `def`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_share_og_meta.py -n0 -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run the existing share and SPA tests**

Run: `uv run pytest tests/test_share_routes.py tests/test_hosted_mode_boot.py -n0 -q`
Expected: PASS. The new routes shadow two paths that previously fell
through to the SPA catch-all; this confirms nothing else did.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui/share_og.py tests/test_share_og_meta.py
git commit -m "feat(share): inject og tags into the share shells, noindex both"
```

---

### Task 8: Warm the match card at share creation

**Files:**
- Modify: `src/splitsmith/ui/server.py:6034` (`_create_match_share`)
- Test: `tests/test_share_og_routes.py`

**Interfaces:**
- Consumes: Task 5's `cached_card_png`; Task 6's `build_match_card`.

This warms the first hash rather than pinning it. If the match data
changes afterwards, the new hash simply misses the cache and renders on
first fetch, exactly like a stage card.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_share_og_routes.py`:

```python
def test_creating_a_share_warms_the_match_card(
    hosted_env: str, hosted_app: tuple[TestClient, _CapturingSender]
) -> None:
    """The link the owner pastes previews without a cold render."""
    from splitsmith.share_card_render import storage_key
    from splitsmith.ui.share_og import build_match_card

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)

    token = _create_share(client)
    state = client.app.state.splitsmith_state
    assert state.storage.exists(storage_key(token, build_match_card(state)))


def test_share_creation_still_succeeds_when_rendering_fails(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warming is best-effort: a browser-less host must still hand the
    owner a working link."""
    import splitsmith.ui.share_og as share_og

    def _boom(*args: object, **kwargs: object) -> bytes:
        raise RuntimeError("no browser here")

    monkeypatch.setattr(share_og, "cached_card_png", _boom)

    client, sender = hosted_app
    login(client, sender, "owner@example.com")
    seed_match(hosted_env, "owner@example.com", MID)

    assert client.post(f"/api/matches/{MID}/match/shares").status_code == 201
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_share_og_routes.py -n0 -q -k warm`
Expected: FAIL -- nothing writes the key at share creation.

- [ ] **Step 3: Implement the warm-up**

In `_create_match_share`, after `s = await store.create(mid)` and before
building the `ShareInfo` response, add a best-effort warm call. Wrap it
in `try/except Exception` with a `logger.warning` -- a failed warm must
never cost the owner their share link, and the PNG route will render on
first fetch anyway.

Import `warm_match_card` from `.share_og` inside the function body,
matching the lazy-import idiom the surrounding hosted code uses. Add a
`warm_match_card(state, token: str) -> None` function to `share_og.py`
that calls `cached_card_png` through the module-level name, so the
monkeypatch in the second test intercepts it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_share_og_routes.py -n0 -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui/server.py src/splitsmith/ui/share_og.py \
        tests/test_share_og_routes.py
git commit -m "feat(share): warm the match card when a share link is created"
```

---

### Task 9: Real-browser integration test and documentation

**Files:**
- Create: `tests/test_share_card_integration.py`
- Modify: `SPEC.md` (the "Module responsibilities" section)
- Modify: `CLAUDE.md` (a short paragraph under a new "Share links" heading)

This test needs no media, so CI's `SPLITSMITH_REQUIRE_INTEGRATION=1` has
nothing to skip over.

- [ ] **Step 1: Write the integration test**

Create `tests/test_share_card_integration.py`:

```python
"""Real Chromium, real PNG. Everything else in the card suite injects a
fake Rasterizer; this is the one test that proves the HTML actually
rasterizes at the declared size."""

from __future__ import annotations

import struct
from dataclasses import dataclass

import pytest

from splitsmith.overlay_raster import ChromiumRasterizer
from splitsmith.overlay_theme import load_theme
from splitsmith.share_card import MatchCard, RosterEntry, StageCard, stage_figures
from splitsmith.share_card_render import render_card


@dataclass(frozen=True)
class _Shot:
    split: float
    interval_class: str | None


@pytest.mark.integration
def test_stage_card_rasterizes_to_a_1200x630_png() -> None:
    card = StageCard(
        stage_number=3,
        stage_name="Per told me to do it!",
        shooter_name="Mathias Axell",
        match_name="Tallmilan 2026",
        shot_count=14,
        stage_time=14.74,
        figures=stage_figures(
            (
                _Shot(split=1.28, interval_class="first_shot"),
                _Shot(split=0.19, interval_class="split"),
                _Shot(split=1.85, interval_class="transition"),
            )
        ),
    )
    with ChromiumRasterizer() as rasterizer:
        png = render_card(card, theme=load_theme("splitsmith"), rasterizer=rasterizer)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (1200, 630)


@pytest.mark.integration
def test_a_long_stage_name_does_not_change_the_canvas_size() -> None:
    """The box model clamps; nothing in Python measures text."""
    card = MatchCard(
        match_name="Unload, and then show clear -- an unreasonably long match name",
        match_date="2026-04-26",
        stage_count=7,
        roster=[RosterEntry(name=f"Competitor Number {i}", division="Production Optics") for i in range(8)],
    )
    from splitsmith.share_card_render import render_card as _render

    with ChromiumRasterizer() as rasterizer:
        png = _render(card, theme=load_theme("splitsmith"), rasterizer=rasterizer)
    assert struct.unpack(">II", png[16:24]) == (1200, 630)
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_share_card_integration.py -n0 -q -m integration`
Expected: PASS, 2 tests. If Chromium is missing, run
`uv run playwright install chromium --only-shell` first.

- [ ] **Step 3: Look at the output**

A green assertion on dimensions is not evidence the card reads correctly
-- CLAUDE.md's review practice records a fix that reached the table cell
and was ellipsized away while the test still passed. Write one card to
disk and open it:

```bash
uv run python -c "
from splitsmith.overlay_raster import ChromiumRasterizer
from splitsmith.overlay_theme import load_theme
from splitsmith.share_card import StageCard, stage_figures
from splitsmith.share_card_render import render_card
from collections import namedtuple
import pathlib
S = namedtuple('S', 'split interval_class')
card = StageCard(
    stage_number=3, stage_name='Per told me to do it!',
    shooter_name='Mathias Axell', match_name='Tallmilan 2026',
    shot_count=14, stage_time=14.74,
    figures=stage_figures((
        S(1.28, 'first_shot'), S(0.19, 'split'),
    )),
)
with ChromiumRasterizer() as r:
    pathlib.Path('/tmp/stage-card.png').write_bytes(
        render_card(card, theme=load_theme('splitsmith'), rasterizer=r))
print('wrote /tmp/stage-card.png')
"
```

Check: both fonts loaded (the numerals are JetBrains Mono, the names are
condensed Antonio -- a fallback face is obvious), the draw and average
are both present and correctly formatted, nothing is clipped.

- [ ] **Step 4: Document the module boundaries**

Add to `SPEC.md`'s "Module responsibilities" section:

```markdown
- `share_card.py` -- pure. Derives draw and average non-anomaly split
  from a stage's intervals, and builds the `MatchCard` / `StageCard`
  models plus their content hash. The one definition of a split
  statistic: intervals classed `split`, with `split_color_band`'s
  `transition_min` rule as the fallback for uncoached stages.
- `share_card_html.py` -- pure. Card model to a 1200x630 HTML document.
- `share_card_render.py` -- rasterizes a card through
  `overlay_raster.Rasterizer` and caches the PNG under a content-
  addressed storage key. Serves a bundled plate if Chromium is absent.
- `ui/share_og.py` -- hosted-only. Injects `og:*` tags into the share
  shells and serves the card PNGs on the anonymous share surface.
```

- [ ] **Step 5: Document the surface in CLAUDE.md**

Add a short section after "Multi-shooter comparison":

```markdown
## Share-link previews

A share link previews as a 1200x630 card. The match card is a roster --
deliberately no summed stage time, since IPSC ranks by hit factor and
accumulated raw time is not a figure the sport produces. The stage card
leads with draw and average non-anomaly split, the numbers splitsmith
itself computes, defined by the `CoachIntervalClass` taxonomy with
`split_color_band`'s `transition_min` as the fallback for uncoached
stages (`share_card.stage_figures` is the one definition; #772 brings
the video summary and results page onto it).

`og:image` URLs are content-addressed: a re-audit moves the figures,
which moves the hash, which moves the URL, so crawlers refetch. Nothing
invalidates a cache. Meta tags are injected server-side in
`ui/share_og.py` for every client -- crawlers do not run JavaScript, so
a client-side helmet would reach none of them.
```

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. Also run `uv run ruff check src tests` and
`uv run black --check src tests`.

- [ ] **Step 7: Commit**

```bash
git add tests/test_share_card_integration.py SPEC.md CLAUDE.md
git commit -m "test(share): rasterize a real card, and document the surface"
```

---

## Verification before opening the PR

- [ ] `uv run pytest` is green, including `-m integration`.
- [ ] `uv run ruff check src tests` and `uv run black --check src tests` are clean.
- [ ] A rendered card has been opened and looked at, not just asserted on.
- [ ] Every new test has been checked against the pre-change code: delete
      the fix, watch it fail. Tasks 1 and 6 have explicit mutation steps;
      apply the same check to the rest.
- [ ] The PR body is a single squashed summary, not a many-commit list --
      a long squash body breaks the release-please parser and the change
      vanishes from the changelog while CI stays green.
