# Template HUD Overlay, Slice 1 (Engine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `render_overlay` can draw the whole live HUD (clock, counter, split) through a Look `overlay` template variant, frame by frame through `seek(t)`, with a content-keyed cache and a fallback to Classic; `plate` ships as the first variant, reachable from the `splitsmith overlay` CLI and the overlay frame script.

**Architecture:** A pure module `overlay_hud` turns the audit's shots into the template's data (splits, coach classes, speed tiers) and decides which frames need a render (`hud_frame_plan`). `looks` gains an `overlay` slot whose `default` is Classic. The rasterizer gets one new method, `render_template_timeline`, which loads a template, asks it `settle()`, and renders exactly the seek times the plan hands back. A new `overlay_hud_render` module encodes those frames through the same ffmpeg argv the Classic path uses and caches the MOV in the render segment cache. `render_overlay` tries the HUD first when a template variant is asked for and falls back to its untouched Classic path on any template failure.

**Tech Stack:** Python 3.11, Pydantic, Playwright (Chromium headless shell), ffmpeg (ProRes 4444 / HEVC alpha), pytest; HTML/JS for the template.

**Spec:** `docs/superpowers/specs/2026-10-08-template-hud-overlay-design.md` (slice 1 of its "Delivery"). Slices 2 to 4 (four more templates, request fields and the gallery, `looks check`) get their own plans.

## Global Constraints

- Classic is byte for byte unchanged: with `variant=None` (or `"default"`), `render_overlay` makes the same argv, pipes the same frames, and never loads a Look template or touches a cache.
- No new dependencies.
- Python: type hints everywhere, `pathlib.Path`, f-strings, Black at 110, Ruff clean, imports stdlib / third-party / local, no `..` relative imports. `uv` only, never `pip`.
- Prose (docstrings, comments, messages, docs): ASCII only, no dash used as punctuation (no em or en dash, no `--` as a dash), none of the clean-prose banned words.
- Tests: `uv run pytest <path> -n0` for a focused run. Integration tests carry `@pytest.mark.integration`; they need the project ffmpeg first on PATH (`export PATH="$PWD/desktop/build/bin:$PATH"` or `SPLITSMITH_FFMPEG`), since Homebrew's has no `drawtext`.
- Git in this worktree: use `/usr/bin/git` (the rtk hook refuses plain `git` in worktree sessions). Never `git add src` wholesale (the `node_modules` symlink).
- Speed tier cutoffs: `GOOD_BELOW = 0.93`, `SLOW_ABOVE = 1.12`, `MIN_CLASS_SHOTS = 3`; tiered classes are `split`, `transition`, `movement`.
- HUD page height cap: 1080 lines (`HUD_MAX_PAGE_HEIGHT`); larger output is scaled up by ffmpeg.
- Positions: `top-left`, `top-right`, `bottom-left`, `bottom-right`.

## Review Focus

1. **A beep at clip time 0, or past the clip's end** (a trim with no pre-buffer; a bad offset): the plan's frame counts must still sum to the trim's frame count, never index past it. Pinned in Task 1.
2. **A last shot plus `settle()` past the clip's end, or a shot before the beep** (negative `ms_after_beep`): the live span is clamped to the clip and starts no earlier than the beep. Pinned in Task 1.
3. **A template that throws on frame 40 of 300:** the encoder is killed, no partial MOV is left at the output path, and the stage still gets a Classic overlay plus a note saying why. Pinned in Task 4.
4. **A re-export with nothing changed:** no browser frame is rendered, and the output MOV's bytes and mtime are left alone (the MP4 segment cache keys on that mtime). Any option change misses. Pinned in Task 4.
5. **A Look that lacks the variant asked for** (the `clean` Look has no `overlay` slot; a mistyped name): it borrows the shipped default's template, and a name no Look has draws Classic with a note. Pinned in Tasks 2 and 4.

---

### Task 1: `overlay_hud`, the template's data and the frame plan

**Files:**
- Create: `src/splitsmith/overlay_hud.py`
- Test: `tests/test_overlay_hud.py`

**Interfaces:**
- Consumes: `stage_summary_data.TileShot` (`time_from_beep: float`, `split: float`, `interval_class: IntervalClass | None`).
- Produces:
  - `HudPosition = Literal["top-left", "top-right", "bottom-left", "bottom-right"]`, `HUD_POSITIONS: tuple[HudPosition, ...]`
  - `HUD_KEY_VERSION: int`, `HUD_MAX_PAGE_HEIGHT: int`
  - `class HudOptions(BaseModel)` (frozen): `speed_colors: bool = True`, `class_labels: bool = True`, `landing: bool = True`, `position: HudPosition | None = None`
  - `speed_tiers(shots: Sequence[TileShot]) -> list[SpeedTier | None]`
  - `hud_stage_data(shots: Sequence[TileShot], *, beep_in_clip: float) -> dict[str, Any]`
  - `declared_positions(template: Path) -> tuple[HudPosition, ...]`
  - `resolve_position(requested: HudPosition | None, declared: Sequence[HudPosition]) -> HudPosition | None`
  - `hud_options_data(options: HudOptions, position: HudPosition | None) -> dict[str, Any]`
  - `@dataclass(frozen=True) class HudFrame: seek: float; count: int`
  - `hud_frame_plan(*, frame_count: int, fps: float, beep: float, last_shot: float, settle: float) -> tuple[HudFrame, ...]`
  - `hud_page_size(width: int, height: int) -> tuple[int, int]`

- [ ] **Step 1: Write the failing tests**

```python
"""overlay_hud: what a template HUD is told, and which frames it draws."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.overlay_hud import (
    HudFrame,
    HudOptions,
    declared_positions,
    hud_frame_plan,
    hud_options_data,
    hud_page_size,
    hud_stage_data,
    resolve_position,
    speed_tiers,
)
from splitsmith.stage_summary_data import TileShot


def _shot(t: float, split: float, cls: str | None) -> TileShot:
    return TileShot(time_from_beep=t, split=split, interval_class=cls)  # type: ignore[arg-type]


STAGE = [
    _shot(1.10, 1.10, "first_shot"),
    _shot(1.35, 0.25, "split"),
    _shot(1.60, 0.25, "split"),
    _shot(2.40, 0.80, "split"),
    _shot(2.65, 0.25, "split"),
    _shot(4.30, 1.65, "reload"),
    _shot(4.52, 0.22, "split"),
]


def test_tiers_compare_a_split_with_its_own_class_median() -> None:
    # split median over [0.25, 0.25, 0.80, 0.25, 0.22] is 0.25: 0.80 is slow,
    # 0.22 is 0.88x (good), the rest normal. The draw and the reload carry none.
    assert speed_tiers(STAGE) == [None, "normal", "normal", "slow", "normal", None, "good"]


def test_a_class_with_fewer_than_three_shots_has_no_tiers() -> None:
    shots = [_shot(1.0, 1.0, "first_shot"), _shot(2.0, 0.7, "transition"), _shot(2.9, 0.9, "transition")]
    assert speed_tiers(shots) == [None, None, None]


def test_an_unclassified_shot_has_no_tier() -> None:
    shots = [_shot(0.3 * i, 0.3, None) for i in range(1, 6)]
    assert speed_tiers(shots) == [None] * 5


def test_stage_data_is_in_clip_time_with_labels_and_rounded_numbers() -> None:
    data = hud_stage_data(STAGE, beep_in_clip=5.0)
    assert data["beep"] == 5.0
    assert data["rounds"] == 7
    assert data["stage_time"] == pytest.approx(4.52)
    first, slow = data["shots"][0], data["shots"][3]
    assert first == {"t": 6.1, "split": 1.1, "cls": "first_shot", "label": "Draw", "tier": None}
    assert slow["t"] == pytest.approx(7.4) and slow["label"] == "Split" and slow["tier"] == "slow"
    assert data["shots"][5]["label"] == "Reload"
    # Rounded so float noise never moves a cache key.
    assert all(len(repr(s["t"])) <= 12 for s in data["shots"])


def test_stage_data_without_shots_is_an_empty_stage() -> None:
    assert hud_stage_data([], beep_in_clip=1.0) == {"beep": 1.0, "shots": [], "stage_time": 0.0, "rounds": 0}


def test_declared_positions_reads_the_meta_tag_in_order(tmp_path: Path) -> None:
    template = tmp_path / "hud.html"
    template.write_text(
        '<meta name="splitsmith-positions" content="bottom-left, top-left,nowhere,top-right">',
        encoding="utf-8",
    )
    assert declared_positions(template) == ("bottom-left", "top-left", "top-right")


def test_a_template_without_the_tag_declares_none(tmp_path: Path) -> None:
    template = tmp_path / "hud.html"
    template.write_text("<!doctype html><body></body>", encoding="utf-8")
    assert declared_positions(template) == ()


def test_resolve_position_takes_a_declared_request_else_the_first() -> None:
    declared = ("bottom-left", "top-right")
    assert resolve_position("top-right", declared) == "top-right"
    assert resolve_position(None, declared) == "bottom-left"
    assert resolve_position("top-left", declared) == "bottom-left"
    assert resolve_position("top-left", ()) is None


def test_options_data_carries_the_resolved_position() -> None:
    options = HudOptions(speed_colors=False, position="top-left")
    assert hud_options_data(options, "bottom-left") == {
        "speed_colors": False,
        "class_labels": True,
        "landing": True,
        "position": "bottom-left",
    }


def _total(plan: tuple[HudFrame, ...]) -> int:
    return sum(frame.count for frame in plan)


def test_plan_holds_before_the_beep_renders_the_live_span_and_holds_the_tail() -> None:
    # 10 fps, 3 s clip = 30 frames; beep at 1.0 s, last shot 1.5 s, settle 0.2 s.
    plan = hud_frame_plan(frame_count=30, fps=10.0, beep=1.0, last_shot=1.5, settle=0.2)
    assert plan[0] == HudFrame(seek=0.0, count=10)
    assert [f.seek for f in plan[1:]] == [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7]
    assert [f.count for f in plan[1:-1]] == [1] * 7
    assert plan[-1] == HudFrame(seek=1.7, count=13)
    assert _total(plan) == 30


def test_plan_with_the_beep_on_frame_zero_has_no_hold() -> None:
    plan = hud_frame_plan(frame_count=20, fps=10.0, beep=0.0, last_shot=0.5, settle=0.0)
    assert plan[0] == HudFrame(seek=0.0, count=1)
    assert _total(plan) == 20


def test_plan_with_the_beep_past_the_clip_is_one_hold() -> None:
    assert hud_frame_plan(frame_count=20, fps=10.0, beep=5.0, last_shot=6.0, settle=0.5) == (
        HudFrame(seek=0.0, count=20),
    )


def test_plan_clamps_a_live_span_that_runs_past_the_clip() -> None:
    plan = hud_frame_plan(frame_count=20, fps=10.0, beep=1.0, last_shot=1.8, settle=5.0)
    assert plan[-1] == HudFrame(seek=1.9, count=1)
    assert _total(plan) == 20


def test_plan_starts_at_the_beep_even_when_a_shot_precedes_it() -> None:
    plan = hud_frame_plan(frame_count=30, fps=10.0, beep=1.0, last_shot=0.6, settle=0.2)
    assert plan[0] == HudFrame(seek=0.0, count=10)
    assert [f.seek for f in plan[1:]] == [1.0, 1.1, 1.2]
    assert _total(plan) == 30


def test_plan_does_not_cap_a_long_field_course() -> None:
    plan = hud_frame_plan(frame_count=3000, fps=30.0, beep=5.0, last_shot=95.0, settle=0.6)
    assert len(plan) == 1 + 2719 and _total(plan) == 3000


def test_plan_of_an_empty_clip_is_empty() -> None:
    assert hud_frame_plan(frame_count=0, fps=30.0, beep=0.0, last_shot=0.0, settle=0.0) == ()


def test_page_size_caps_at_1080_lines_and_keeps_even_sides() -> None:
    assert hud_page_size(1920, 1080) == (1920, 1080)
    assert hud_page_size(1280, 720) == (1280, 720)
    assert hud_page_size(3840, 2160) == (1920, 1080)
    assert hud_page_size(2704, 1520) == (1920, 1080)
```

Check the long-course count before relying on it: live frames are indices `ceil(5.0*30)=150` through `ceil(95.6*30)=2868`, which is 2719 frames; plus the hold. The last live frame takes the tail (`3000 - 1 - 2868 = 131` frames).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_overlay_hud.py -n0 -q`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'splitsmith.overlay_hud'`.

- [ ] **Step 3: Write the module**

```python
"""What a template HUD is told, and which frames it draws (spec
``2026-10-08-template-hud-overlay-design``).

Pure: no browser, no ffmpeg, no file but a template read as text. A
template never computes a split, a class or a speed tier; it reads them
from ``data.stage``, so every HUD variant shows the same numbers and the
numbers are pinned here, in Python.

The frame plan is the other half. A HUD is static before the beep and
after ``last shot + settle()``, so only the span between needs a render
per frame; the stretches either side are one frame held. That is the
whole cost model of the template path: about one browser frame per output
frame of live stage, and nothing for the pads.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict

from .stage_summary_data import TileShot

HudPosition = Literal["top-left", "top-right", "bottom-left", "bottom-right"]
HUD_POSITIONS: tuple[HudPosition, ...] = get_args(HudPosition)

SpeedTier = Literal["good", "normal", "slow"]

#: A split below this fraction of its class's median on the stage is good.
GOOD_BELOW = 0.93
#: Above this fraction it is slow.
SLOW_ABOVE = 1.12
#: A median of fewer shots says nothing; such a class carries no tiers.
MIN_CLASS_SHOTS = 3
#: The classes a speed reads for. The draw, a reload and an activation are
#: their own events, never "slow" beside a split.
TIERED_CLASSES = frozenset({"split", "transition", "movement"})

CLASS_LABELS: dict[str, str] = {
    "first_shot": "Draw",
    "split": "Split",
    "transition": "Transition",
    "movement": "Movement",
    "reload": "Reload",
    "activation": "Activation",
}

#: Bump when anything about how a HUD MOV is made changes and the
#: template digest would not show it (the frame plan, the encode).
HUD_KEY_VERSION = 1

#: The page renders at most this many lines; a larger output is scaled up
#: by ffmpeg. Measured on the spike (#1305): a 4K page costs twice a 1080p
#: one per frame, and the HUD is type and flat plates.
HUD_MAX_PAGE_HEIGHT = 1080

_POSITIONS_META = re.compile(
    r"""<meta\s+name=["']splitsmith-positions["']\s+content=["']([^"']*)["']""", re.IGNORECASE
)


class HudOptions(BaseModel):
    """The shared toggles every template HUD honours."""

    model_config = ConfigDict(frozen=True)

    speed_colors: bool = True
    class_labels: bool = True
    landing: bool = True
    #: ``None`` is the variant's own default (the first position it declares).
    position: HudPosition | None = None


def speed_tiers(shots: Sequence[TileShot]) -> list[SpeedTier | None]:
    """Each shot's tier against this stage's median for its class."""
    by_class: dict[str, list[float]] = {}
    for shot in shots:
        if shot.interval_class in TIERED_CLASSES:
            by_class.setdefault(shot.interval_class, []).append(shot.split)
    medians = {cls: median(values) for cls, values in by_class.items() if len(values) >= MIN_CLASS_SHOTS}
    tiers: list[SpeedTier | None] = []
    for shot in shots:
        middle = medians.get(shot.interval_class) if shot.interval_class else None
        if middle is None or middle <= 0:
            tiers.append(None)
            continue
        ratio = shot.split / middle
        tiers.append("good" if ratio < GOOD_BELOW else "slow" if ratio > SLOW_ABOVE else "normal")
    return tiers


def hud_stage_data(shots: Sequence[TileShot], *, beep_in_clip: float) -> dict[str, Any]:
    """``data.stage``: the beep and every shot in clip seconds, each with
    its split, class, label and tier. Numbers are rounded to the
    microsecond so float noise never moves a cache key."""
    tiers = speed_tiers(shots)
    return {
        "beep": round(beep_in_clip, 6),
        "shots": [
            {
                "t": round(beep_in_clip + shot.time_from_beep, 6),
                "split": round(shot.split, 6),
                "cls": shot.interval_class,
                "label": CLASS_LABELS.get(shot.interval_class) if shot.interval_class else None,
                "tier": tier,
            }
            for shot, tier in zip(shots, tiers, strict=True)
        ],
        "stage_time": round(shots[-1].time_from_beep, 6) if shots else 0.0,
        "rounds": len(shots),
    }


def declared_positions(template: Path) -> tuple[HudPosition, ...]:
    """The positions a template supports, from its
    ``<meta name="splitsmith-positions">`` tag, in its order (the first is
    its default). Read as text: no browser."""
    match = _POSITIONS_META.search(template.read_text(encoding="utf-8"))
    if match is None:
        return ()
    names = [name.strip() for name in match.group(1).split(",")]
    return tuple(name for name in names if name in HUD_POSITIONS)  # type: ignore[misc]


def resolve_position(requested: HudPosition | None, declared: Sequence[HudPosition]) -> HudPosition | None:
    """The position a render uses: the request when the template declares
    it, else the template's default; ``None`` for a template that declares
    none (it places itself)."""
    if not declared:
        return None
    if requested in declared:
        return requested
    return declared[0]


def hud_options_data(options: HudOptions, position: HudPosition | None) -> dict[str, Any]:
    """``data.options``, with the position already resolved."""
    return {
        "speed_colors": options.speed_colors,
        "class_labels": options.class_labels,
        "landing": options.landing,
        "position": position,
    }


@dataclass(frozen=True)
class HudFrame:
    """One rendered frame: the clip time it seeks to and how many output
    frames it fills."""

    seek: float
    count: int


def hud_frame_plan(
    *, frame_count: int, fps: float, beep: float, last_shot: float, settle: float
) -> tuple[HudFrame, ...]:
    """Which frames a template HUD renders. Frame ``i`` sits at ``i / fps``.
    Frames before the beep are one frame at ``seek(0)`` held; every frame
    from the first at or after the beep through the first at or after
    ``max(beep, last_shot) + settle`` is rendered; the last rendered frame
    is held to the end. Counts always sum to ``frame_count``."""
    if frame_count <= 0:
        return ()
    first_live = max(0, math.ceil(beep * fps - 1e-9))
    if first_live >= frame_count:
        return (HudFrame(seek=0.0, count=frame_count),)
    end = max(beep, last_shot) + max(0.0, settle)
    last_live = min(frame_count - 1, max(first_live, math.ceil(end * fps - 1e-9)))
    plan: list[HudFrame] = []
    if first_live > 0:
        plan.append(HudFrame(seek=0.0, count=first_live))
    plan.extend(HudFrame(seek=round(i / fps, 6), count=1) for i in range(first_live, last_live + 1))
    tail = frame_count - 1 - last_live
    if tail > 0:
        plan[-1] = HudFrame(seek=plan[-1].seek, count=1 + tail)
    return tuple(plan)


def hud_page_size(width: int, height: int) -> tuple[int, int]:
    """The page a HUD renders at: the output size, capped at
    :data:`HUD_MAX_PAGE_HEIGHT` lines with the aspect kept and both sides
    even (yuv formats need them even)."""
    if height <= HUD_MAX_PAGE_HEIGHT:
        return width, height
    scaled = round(width * HUD_MAX_PAGE_HEIGHT / height)
    return scaled - scaled % 2, HUD_MAX_PAGE_HEIGHT


__all__ = [
    "CLASS_LABELS",
    "GOOD_BELOW",
    "HUD_KEY_VERSION",
    "HUD_MAX_PAGE_HEIGHT",
    "HUD_POSITIONS",
    "HudFrame",
    "HudOptions",
    "HudPosition",
    "MIN_CLASS_SHOTS",
    "SLOW_ABOVE",
    "SpeedTier",
    "TIERED_CLASSES",
    "declared_positions",
    "hud_frame_plan",
    "hud_options_data",
    "hud_page_size",
    "hud_stage_data",
    "resolve_position",
    "speed_tiers",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_overlay_hud.py -n0 -q`
Expected: all pass. If `test_plan_does_not_cap_a_long_field_course` disagrees on the count, recompute by hand from the docstring rule before touching the code: the test, not the implementation, may be off by one.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/splitsmith/overlay_hud.py tests/test_overlay_hud.py
uv run black --check src/splitsmith/overlay_hud.py tests/test_overlay_hud.py
/usr/bin/git add src/splitsmith/overlay_hud.py tests/test_overlay_hud.py
/usr/bin/git commit -m "feat(overlay): the template HUD's data and frame plan"
```

---

### Task 2: the Look `overlay` slot

**Files:**
- Modify: `src/splitsmith/looks.py` (`SLOT_NAMES` near line 36, `_slot_shape` near line 205, new `overlay_template_for` after `sting_template_for` near line 448, `__all__`)
- Test: `tests/test_looks.py`

**Interfaces:**
- Produces: `OVERLAY_SLOT = "overlay"`; `overlay_template_for(look: Look, variant: str) -> Path | None` (the Look's own template, else the shipped default Look's, else `None`; `None` always for `"default"`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_looks.py`; it already has a `user_dir` fixture and imports `looks`; reuse its helper for writing a user Look if one exists, otherwise write `look.json` directly as below)

```python
# --- the overlay slot (template HUD, spec 2026-10-08) ------------------------


def _manifest(name: str, slots: dict) -> dict:
    shipped = looks.load_look("splitsmith").manifest
    return {"name": name, "colors": {k: list(v) for k, v in shipped.colors.items()}, "slots": slots}


def test_overlay_is_a_slot_and_default_means_classic() -> None:
    assert "overlay" in looks.SLOT_NAMES and looks.OVERLAY_SLOT == "overlay"
    splitsmith = looks.load_look("splitsmith")
    assert looks.overlay_template_for(splitsmith, "default") is None


def test_a_manifest_cannot_name_a_template_for_the_classic_overlay() -> None:
    with pytest.raises(ValueError, match="Classic"):
        looks.LookManifest.model_validate(_manifest("mine", {"overlay": {"default": "hud.html"}}))
    with pytest.raises(ValueError, match="Classic"):
        looks.LookManifest.model_validate(_manifest("mine", {"overlay": "hud.html"}))


def test_a_look_without_the_slot_borrows_the_shipped_overlay_template() -> None:
    clean = looks.load_look("clean")
    template = looks.overlay_template_for(clean, "plate")
    assert template is not None and template.name == "hud-plate.html"
    assert template.parent == looks.shipped_looks_dir() / "splitsmith"


def test_an_overlay_variant_no_look_has_is_none() -> None:
    assert looks.overlay_template_for(looks.load_look("splitsmith"), "nope") is None


def test_the_catalog_lists_the_overlay_variants() -> None:
    splitsmith = next(c for c in looks.look_catalog() if c.name == "splitsmith")
    assert [v.name for v in splitsmith.slots["overlay"]] == ["plate"]
```

`test_a_look_without_the_slot_borrows_...` and `test_the_catalog_lists_...` stay red until Task 5 ships `hud-plate.html`. Mark both `@pytest.mark.xfail(reason="hud-plate.html lands in Task 5", strict=True)` now and remove the marks in Task 5.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_looks.py -n0 -q -k overlay`
Expected: FAIL with `AttributeError: module 'splitsmith.looks' has no attribute 'OVERLAY_SLOT'` (the two xfail tests report XFAIL).

- [ ] **Step 3: Implement**

In `looks.py`, extend the slot tuple and its docstring:

```python
SLOT_NAMES: tuple[str, ...] = (
    "title_page",
    "slate",
    "lower_third",
    "summary",
    "closing",
    "transition",
    "overlay",
)
"""Every slot a manifest may name. ``summary`` is reserved: no renderer
reads it in this slice, the stage summary still composes through
``overlay_summary_cell``. ``transition`` holds the Look's stings (issue
#1245): each variant is a ``sting:<variant>`` transition kind.
``overlay`` holds the template HUDs (spec 2026-10-08): its ``default`` is
the engine's Classic overlay and can never name a file."""

STING_SLOT = "transition"
OVERLAY_SLOT = "overlay"
```

In `_slot_shape`, before the per-variant loop's checks:

```python
            if slot == OVERLAY_SLOT and DEFAULT_VARIANT in variants:
                raise ValueError(
                    "slot 'overlay': 'default' is the engine's Classic overlay and cannot name a template; "
                    "give the template a variant name"
                )
```

After `sting_template_for`:

```python
def overlay_template_for(look: Look, variant: str) -> Path | None:
    """The template that draws the live HUD in ``variant`` for ``look``:
    the Look's own, else the shipped default Look's. ``None`` for
    ``default`` (Classic, drawn by the engine) and for a variant neither
    has; the renderer draws Classic then and notes why."""
    if variant == DEFAULT_VARIANT:
        return None
    own = look.own_template(OVERLAY_SLOT, variant)
    if own is not None:
        return own
    return _shipped_default().own_template(OVERLAY_SLOT, variant)
```

Add `"OVERLAY_SLOT"` and `"overlay_template_for"` to `__all__`. `look_tools.check_look` iterates `CARD_SLOTS` and the sting only, so it does not probe overlay templates with card data; slice 4 teaches it the HUD contract. Do not change that here.

- [ ] **Step 4: Run the Looks tests**

Run: `uv run pytest tests/test_looks.py tests/test_looks_api.py tests/test_looks_cli.py -n0 -q`
Expected: pass, with the two marked XFAIL. `test_the_catalog_lists_every_slot_with_default_first_and_previews` still passes: it compares against `SLOT_NAMES` itself.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/looks.py tests/test_looks.py
/usr/bin/git commit -m "feat(looks): an overlay slot whose default is the Classic overlay"
```

---

### Task 3: the rasterizer renders a template along a timeline

**Files:**
- Modify: `src/splitsmith/look_sandbox.py` (budgets near line 60, its `__all__` near line 209)
- Modify: `src/splitsmith/overlay_raster.py` (a `_HUD_JS` beside `_POSTER_JS` near line 333; a `hud` hook in `_GUARD_JS`; `render_template_timeline` on the `Rasterizer` protocol after `render_template_frames` near line 173 and on `ChromiumRasterizer` after its `render_template_frames` near line 860)
- Test: `tests/test_overlay_raster.py`

**Interfaces:**
- Produces:
  - `look_sandbox.HUD_LOAD_SECONDS = 30.0`, `look_sandbox.HUD_FRAME_SECONDS = 1.0`
  - `Rasterizer.render_template_timeline(self, template: Path, *, context: TemplateContext, width: int, height: int, plan: Callable[[float], Sequence[float]]) -> TemplateFrames`. It loads the template, asks the `hud` hook for `{seek: bool, settle: number | null}`, refuses a template without both, calls `plan(settle)` for the seek times, and yields one RGBA buffer per time. `TemplateFrames.duration` is the settle; `frame_count` is `len(times)`. The run's budget is `HUD_LOAD_SECONDS + len(times) * HUD_FRAME_SECONDS`.

- [ ] **Step 1: Write the failing tests** (in `tests/test_overlay_raster.py`, beside the `render_template_frames` tests; first add the hook's text to `_RecordingPage._HOOK_TEXT`)

```python
    _HOOK_TEXT = {
        "duration": "typeof window.duration === 'function' ? Number(window.duration()) || 0 : 0",
        "poster": "typeof window.poster === 'function' ? Number(window.poster()) || 0 : 0",
        "fit": "window.__splitsmithFit && window.__splitsmithFit()",
        "fonts": "document.fonts.ready",
        "probe": "probe",
        "hud": "typeof window.settle",
    }
```

```python
# --- render_template_timeline(): the template HUD ------------------------------


class _HudPage(_RecordingPage):
    """A HUD template: ``seek`` and a 0.2 s ``settle``."""

    def __init__(self) -> None:
        super().__init__()
        self.answers = {"typeof window.settle": {"seek": True, "settle": 0.2}}


class _NoSettlePage(_RecordingPage):
    def __init__(self) -> None:
        super().__init__()
        self.answers = {"typeof window.settle": {"seek": True, "settle": None}}


class _NoSeekPage(_RecordingPage):
    def __init__(self) -> None:
        super().__init__()
        self.answers = {"typeof window.settle": {"seek": False, "settle": 0.0}}


def test_render_template_timeline_hands_settle_to_the_plan_and_seeks_its_times(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_HudPage)
    asked: list[float] = []

    def plan(settle: float) -> list[float]:
        asked.append(settle)
        return [0.0, 1.0, 1.1]

    out = rasterizer.render_template_timeline(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, plan=plan
    )
    assert asked == [0.2]
    assert (out.duration, out.frame_count) == (0.2, 3)
    frames = list(out.frames)
    assert len(frames) == 3 and all(len(f) == 2 * 2 * 4 for f in frames)
    page = rasterizer._browser.contexts[0].pages[0]
    seeks = [c[1] for c in page.calls if "window.seek(" in c[1]]
    # One seek at 0 to lay out and fit, then one per planned time.
    assert [s[s.index("window.seek(") :] for s in seeks] == [
        f"window.seek({t}) : undefined" for t in (0.0, 0.0, 1.0, 1.1)
    ]
    assert page.screenshots == 3
    assert rasterizer._browser.contexts[0].closed


def test_render_template_timeline_refuses_a_template_without_settle(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_NoSettlePage)
    with pytest.raises(overlay_raster.TemplateScriptError, match="settle"):
        rasterizer.render_template_timeline(
            _template(tmp_path), context=_context_fixture(), width=2, height=2, plan=lambda s: [0.0]
        )
    assert rasterizer._browser.contexts[0].closed


def test_render_template_timeline_refuses_a_template_without_seek(tmp_path: Path) -> None:
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_NoSeekPage)
    with pytest.raises(overlay_raster.TemplateScriptError, match="seek"):
        rasterizer.render_template_timeline(
            _template(tmp_path), context=_context_fixture(), width=2, height=2, plan=lambda s: [0.0]
        )


def test_render_template_timeline_budget_grows_with_the_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The card's fixed budget would refuse a long course; the HUD's grows
    with the frames, and is still a hard stop."""
    rasterizer = ChromiumRasterizer()
    rasterizer._browser = _RecordingBrowser(page_factory=_HudPage)
    clock = iter([0.0] + [0.5 * i for i in range(1, 400)])
    monkeypatch.setattr(overlay_raster.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(look_sandbox, "HUD_LOAD_SECONDS", 0.0)
    monkeypatch.setattr(look_sandbox, "HUD_FRAME_SECONDS", 0.4)
    out = rasterizer.render_template_timeline(
        _template(tmp_path), context=_context_fixture(), width=2, height=2, plan=lambda s: [0.1 * i for i in range(10)]
    )
    with pytest.raises(overlay_raster.TemplateTimeoutError, match="10 frames"):
        list(out.frames)
```

If `look_sandbox` is not yet imported in the test module, add `from splitsmith import look_sandbox`. The fake clock answers each `time.monotonic()` call 0.5 s later; with 0.4 s per frame for 10 frames the budget is 4 s, overrun before the last frame. If the renderer calls `monotonic` a different number of times, keep the assertion (it times out, and the message names the frame count) and adjust only the clock's length.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_overlay_raster.py -n0 -q -k timeline`
Expected: FAIL with `AttributeError: 'ChromiumRasterizer' object has no attribute 'render_template_timeline'`.

- [ ] **Step 3: Implement**

`look_sandbox.py`, below `MAX_ANIMATION_SECONDS`:

```python
#: A template HUD (spec 2026-10-08) loads within this, then gets
#: HUD_FRAME_SECONDS per frame it renders: its total grows with the live
#: span, so a long field course is never refused the way a card's fixed
#: MAX_ANIMATION_SECONDS would refuse it. Measured: about 0.05 s a frame at
#: 1080p (#1305); a template slower than 1 s a frame is broken, not slow.
HUD_LOAD_SECONDS = 30.0
HUD_FRAME_SECONDS = 1.0
```

and add both names to its `__all__`.

`overlay_raster.py`, beside `_POSTER_JS`:

```python
_HUD_JS = (
    "({seek: typeof window.seek === 'function', "
    "settle: typeof window.settle === 'function' ? Number(window.settle()) : null})"
)
```

In `_GUARD_JS`'s hook table, after `poster`:

```python
      hud: () => ("""
    + _HUD_JS
    + """),
```

On the `Rasterizer` protocol, after `render_template_frames`:

```python
    def render_template_timeline(
        self,
        template: Path,
        *,
        context: TemplateContext,
        width: int,
        height: int,
        plan: Callable[[float], Sequence[float]],
    ) -> TemplateFrames:
        """A template HUD's frames: ``plan`` gets the template's ``settle()``
        and answers the clip times to render; one RGBA buffer per time,
        rendered lazily."""
        ...
```

On `ChromiumRasterizer`, after `render_template_frames`:

```python
    def render_template_timeline(
        self,
        template: Path,
        *,
        context: TemplateContext,
        width: int,
        height: int,
        plan: Callable[[float], Sequence[float]],
    ) -> TemplateFrames:
        """Load a template HUD once and render it at the times ``plan``
        answers. The template must define ``seek(t)`` and ``settle()``
        (seconds it keeps moving after the last shot); ``plan`` gets that
        settle. Fonts, a seek to 0 and the fit policy run once before any
        frame, as for a card. The budget is
        :data:`look_sandbox.HUD_LOAD_SECONDS` plus
        :data:`look_sandbox.HUD_FRAME_SECONDS` per frame, so a long stage
        gets the time it needs and a stuck one still stops. A hook that
        throws or overruns raises :class:`TemplateScriptError` and closes
        the context; :meth:`TemplateFrames.close` releases it either way."""
        if self._browser is None:
            raise RuntimeError(
                "ChromiumRasterizer.render_template_timeline() called outside its own 'with' block -- the "
                "browser is only live between __enter__ and __exit__"
            )
        started = time.monotonic()
        view = self._open_template(template, context=context, width=width, height=height)
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                view.close()

        try:
            hooks = view.call("hud") or {}
            self._check(view.errors, template)
            if not hooks.get("seek"):
                raise TemplateScriptError(f"{template.name}: an overlay template must define seek(t)")
            settle = hooks.get("settle")
            if settle is None or not math.isfinite(float(settle)) or float(settle) < 0:
                raise TemplateScriptError(
                    f"{template.name}: an overlay template must define settle() returning seconds >= 0"
                )
            view.call("fonts")
            view.call("seek", 0.0)
            view.call("fonts")
            view.call("fit")
            self._check(view.errors, template)
            times = [float(t) for t in plan(float(settle))]
        except _HookError as exc:
            release()
            raise TemplateScriptError(f"{template.name}: {exc}") from exc
        except BaseException:
            release()
            raise
        budget = look_sandbox.HUD_LOAD_SECONDS + len(times) * look_sandbox.HUD_FRAME_SECONDS

        def generate() -> Iterator[bytes]:
            try:
                for seconds in times:
                    if time.monotonic() - started > budget:
                        raise TemplateTimeoutError(
                            f"{template.name}: {len(times)} frames took longer than {budget:g} s"
                        )
                    try:
                        view.call("seek", seconds)
                    except _HookError as exc:
                        raise TemplateScriptError(f"{template.name}: {exc}") from exc
                    self._check(view.errors, template)
                    png = view.screenshot()
                    with Image.open(io.BytesIO(png)) as image:
                        yield image.convert("RGBA").tobytes()
            finally:
                release()

        return TemplateFrames(
            duration=float(settle),
            frame_count=len(times),
            width=width,
            height=height,
            frames=generate(),
            release=release,
        )
```

Add `Callable` and `Sequence` to the `collections.abc` import if missing. The two `RuntimeError` messages' existing `--` is copied from the sibling method's wording; keep the new one in step with them rather than restyling.

- [ ] **Step 4: Run the raster tests**

Run: `uv run pytest tests/test_overlay_raster.py -n0 -q`
Expected: pass (the integration ones skip without `-m integration`).

- [ ] **Step 5: Prove the tests bite, then commit**

Temporarily change `if not hooks.get("seek"):` to `if False:`, run `-k without_seek`, see it fail, and restore. Then:

```bash
/usr/bin/git add src/splitsmith/look_sandbox.py src/splitsmith/overlay_raster.py tests/test_overlay_raster.py
/usr/bin/git commit -m "feat(raster): render a template along a planned timeline with a settle hook"
```

---

### Task 4: the HUD render path, its cache and the fallback

**Files:**
- Create: `src/splitsmith/overlay_hud_render.py`
- Modify: `src/splitsmith/overlay_render.py` (`render_overlay` signature and body, near line 483)
- Test: `tests/test_overlay_hud_render.py`

**Interfaces:**
- Consumes: Task 1 (`HudOptions`, `hud_stage_data`, `declared_positions`, `resolve_position`, `hud_options_data`, `hud_frame_plan`, `hud_page_size`, `HUD_KEY_VERSION`), Task 2 (`overlay_template_for`, `load_look`), Task 3 (`render_template_timeline`), `segment_cache.SegmentCache` (`key`, `lookup`, `partial_path`, `commit`, `evict`), `stage_summary_data.load_stage_shots`, `look_template.TemplateContext` / `theme_tokens` / `engine_block` / `shared_url` / `template_digest`, `overlay_html.single_css`, `overlay_layout.CellScale`.
- Produces:
  - `overlay_hud_render.HudFallback(Exception)`
  - `overlay_hud_render.render_hud_overlay(*, template: Path, audit_path: Path, output_path: Path, beep_offset_seconds: float, duration_seconds: float, width: int, height: int, rate_num: int, rate_den: int, codec: Literal["hevc-alpha", "prores-4444"], ffmpeg_binary: str, theme: OverlayTheme, options: HudOptions, rasterizer: Rasterizer, segment_cache: SegmentCache | None) -> Path`
  - `render_overlay(..., variant: str | None = None, hud_options: HudOptions | None = None, segment_cache: SegmentCache | None = None, degraded: list[str] | None = None)`. `degraded` collects one line per fallback, worded for the export report.

- [ ] **Step 1: Write the failing tests**

```python
"""The template HUD path of render_overlay: frames, cache, fallback."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from splitsmith import overlay_render
from splitsmith.config import VideoMetadata
from splitsmith.overlay_hud import HudOptions
from splitsmith.overlay_raster import TemplateFrames, TemplateScriptError
from splitsmith.segment_cache import SegmentCache
from tests.conftest import fake_ffmpeg_probe

W, H = 64, 36


def _meta(duration: float = 2.0) -> VideoMetadata:
    return VideoMetadata(width=W, height=H, duration_seconds=duration, frame_rate_num=10, frame_rate_den=1)


def _audit(tmp_path: Path) -> Path:
    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps(
            {
                "shots": [
                    {"shot_number": i + 1, "candidate_number": i + 1, "ms_after_beep": ms}
                    for i, ms in enumerate([300, 550, 800])
                ]
            }
        ),
        encoding="utf-8",
    )
    return audit


class _FakeRasterizer:
    """Classic's ``png`` plus the HUD's ``render_template_timeline``: real
    RGBA buffers, every call recorded. ``fail_at`` raises a
    TemplateScriptError on that frame index, mid-run."""

    def __init__(self, *, settle: float = 0.2, fail_at: int | None = None) -> None:
        self.settle = settle
        self.fail_at = fail_at
        self.timelines: list[dict[str, Any]] = []
        self.documents: list[str] = []
        self.rendered = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.documents.append(html)
        buffer = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buffer, format="PNG")
        return buffer.getvalue()

    def engine_version(self) -> str:
        return "fake-1"

    def render_template_timeline(self, template, *, context, width, height, plan):  # noqa: ANN001
        times = list(plan(self.settle))
        self.timelines.append({"template": template, "context": context, "times": times, "size": (width, height)})

        def frames():
            for index, _ in enumerate(times):
                if self.fail_at is not None and index == self.fail_at:
                    raise TemplateScriptError("hud-plate.html: boom")
                self.rendered += 1
                yield bytes((index % 256, 0, 0, 255)) * (width * height)

        return TemplateFrames(duration=self.settle, frame_count=len(times), width=width, height=height, frames=frames())


class _Encoder:
    """A stub ffmpeg: records argv and every byte piped, writes the output."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[list[str]] = []
        self.piped: list[int] = []
        encoder = self

        class Stdin:
            def write(self, data: bytes) -> int:
                encoder.piped[-1] += len(data)
                return len(data)

            def close(self) -> None:
                return None

        class Stderr:
            def read(self) -> bytes:
                return b""

        class Proc:
            def __init__(self, cmd: list[str]) -> None:
                self.cmd = cmd
                self.stdin = Stdin()
                self.stderr = Stderr()

            def wait(self) -> int:
                Path(self.cmd[-1]).write_bytes(f"mov:{len(encoder.calls)}".encode())
                return 0

            def kill(self) -> None:
                return None

        def popen(cmd: list[str], **_: Any) -> Proc:
            encoder.calls.append(cmd)
            encoder.piped.append(0)
            return Proc(cmd)

        monkeypatch.setattr(overlay_render.subprocess, "Popen", popen)
        # A real file: the segment cache keys the encoder by stat-ing what
        # ``which`` answers, and ``shutil`` is one module for both callers.
        monkeypatch.setattr(overlay_render.shutil, "which", lambda _b: sys.executable)


def _render(tmp_path: Path, rasterizer: _FakeRasterizer, **kwargs: Any) -> Path:
    kwargs.setdefault("probe", _meta())
    return overlay_render.render_overlay(
        audit_path=_audit(tmp_path),
        trimmed_video_path=tmp_path / "trim.mp4",
        output_path=tmp_path / "overlay.mov",
        beep_offset_seconds=1.0,
        codec="prores-4444",
        rasterizer=rasterizer,
        probe_runner=fake_ffmpeg_probe(),
        **kwargs,
    )


def test_a_template_variant_renders_the_planned_frames_and_pipes_every_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    fake = _FakeRasterizer(settle=0.2)
    _render(tmp_path, fake, variant="plate")
    (timeline,) = fake.timelines
    assert timeline["template"].name == "hud-plate.html"
    # 10 fps, beep at 1.0, last shot at 1.8, settle 0.2: one held frame at 0,
    # then frames 1.0 .. 2.0 clamped to the clip's last frame (1.9).
    assert timeline["times"] == [0.0, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9]
    assert fake.documents == [], "the Classic path drew nothing"
    assert encoder.piped == [20 * W * H * 4]
    assert "drawtext" not in " ".join(encoder.calls[0]), "the template draws the clock"
    data = timeline["context"].data
    assert data["stage"]["rounds"] == 3 and data["stage"]["beep"] == 1.0
    assert data["options"]["position"] == "bottom-left"


def test_the_options_and_position_reach_the_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Encoder(monkeypatch)
    fake = _FakeRasterizer()
    _render(
        tmp_path,
        fake,
        variant="plate",
        hud_options=HudOptions(speed_colors=False, class_labels=False, landing=False, position="top-right"),
    )
    assert fake.timelines[0]["context"].data["options"] == {
        "speed_colors": False,
        "class_labels": False,
        "landing": False,
        "position": "top-right",
    }


def test_a_tall_output_renders_a_1080_page_and_scales_in_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    fake = _FakeRasterizer()
    tall = VideoMetadata(width=3840, height=2160, duration_seconds=0.5, frame_rate_num=10, frame_rate_den=1)
    _render(tmp_path, fake, variant="plate", probe=tall, beep_offset_seconds=0.0)
    assert fake.timelines[0]["size"] == (1920, 1080)
    cmd = encoder.calls[0]
    assert cmd[cmd.index("-s") + 1] == "1920x1080"
    assert cmd[cmd.index("-vf") + 1] == "scale=3840:2160:flags=lanczos"


def test_no_variant_is_the_classic_path_and_loads_no_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Encoder(monkeypatch)

    def no_template(*_a: Any, **_k: Any) -> None:
        raise AssertionError("Classic must not resolve a template")

    monkeypatch.setattr(overlay_render, "overlay_template_for", no_template)
    fake = _FakeRasterizer()
    for variant in (None, "default"):
        _render(tmp_path, fake, variant=variant)
    assert fake.timelines == [] and fake.documents


def test_a_template_that_throws_mid_run_falls_back_to_classic_with_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    fake = _FakeRasterizer(fail_at=4)
    degraded: list[str] = []
    out = _render(tmp_path, fake, variant="plate", degraded=degraded)
    assert len(encoder.calls) == 2, "the HUD encode was killed, then Classic encoded"
    assert fake.documents, "Classic drew its runs"
    assert out.read_bytes() == b"mov:2"
    assert degraded == ["overlay style 'plate' fell back to Classic: hud-plate.html: boom"]


def test_an_unknown_variant_draws_classic_with_a_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Encoder(monkeypatch)
    fake = _FakeRasterizer()
    degraded: list[str] = []
    _render(tmp_path, fake, variant="nope", degraded=degraded)
    assert fake.timelines == [] and fake.documents
    assert degraded == ["overlay style 'nope' is not in Look 'splitsmith'; drew Classic"]


def test_a_cache_hit_renders_no_frame_and_leaves_the_mov_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    first = _FakeRasterizer()
    out = _render(tmp_path, first, variant="plate", segment_cache=cache)
    before = (out.read_bytes(), out.stat().st_mtime_ns)
    os.utime(out, ns=(before[1] - 10**9, before[1] - 10**9))
    pinned = out.stat().st_mtime_ns

    second = _FakeRasterizer()
    _render(tmp_path, second, variant="plate", segment_cache=cache)
    assert second.rendered == 0, "a hit renders no browser frame"
    assert len(encoder.calls) == 1, "and encodes nothing"
    assert out.read_bytes() == before[0] and out.stat().st_mtime_ns == pinned


@pytest.mark.parametrize(
    "change",
    [
        {"hud_options": HudOptions(speed_colors=False)},
        {"hud_options": HudOptions(position="top-left")},
        {"beep_offset_seconds": 0.9},
        {"theme": "clean"},
    ],
)
def test_any_input_change_misses_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict) -> None:
    encoder = _Encoder(monkeypatch)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    _render(tmp_path, _FakeRasterizer(), variant="plate", segment_cache=cache)
    again = _FakeRasterizer()
    _render(tmp_path, again, variant="plate", segment_cache=cache, **change)
    assert again.rendered > 0 and len(encoder.calls) == 2


def test_changed_template_bytes_miss_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Encoder(monkeypatch)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    _render(tmp_path, _FakeRasterizer(), variant="plate", segment_cache=cache)
    edited = tmp_path / "hud-plate.html"
    shipped = overlay_render.overlay_template_for(overlay_render.load_look("splitsmith"), "plate")
    edited.write_text(shipped.read_text(encoding="utf-8") + "<!-- edit -->", encoding="utf-8")
    monkeypatch.setattr(overlay_render, "overlay_template_for", lambda _look, _v: edited)
    again = _FakeRasterizer()
    _render(tmp_path, again, variant="plate", segment_cache=cache)
    assert again.rendered > 0
```

These need `hud-plate.html` to exist (Task 5). Until then, every test that asks for `variant="plate"` would resolve no template: create a placeholder now so Task 4 is testable on its own, and Task 5 replaces its content:

```bash
printf '<!doctype html>\n<meta name="splitsmith-positions" content="bottom-left,top-left,top-right,bottom-right">\n<body></body>\n' > src/splitsmith/data/looks/splitsmith/hud-plate.html
```

and add `"overlay": {"plate": "hud-plate.html"}` to `src/splitsmith/data/looks/splitsmith/look.json`'s `slots` (keep its key order alphabetical, as the file has it). Remove the two `xfail` marks from Task 2's tests now; they pass.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_overlay_hud_render.py -n0 -q`
Expected: FAIL with `TypeError: render_overlay() got an unexpected keyword argument 'variant'`.

- [ ] **Step 3: Write `overlay_hud_render.py`**

```python
"""The template HUD path of the overlay MOV (spec 2026-10-08).

``overlay_render.render_overlay`` calls :func:`render_hud_overlay` when a
template variant is asked for. The MOV is what Classic writes (same size,
rate, codec and alpha) so the MP4 compositing and the FCPXML never know
which path drew it. The difference is who draws: here the template draws
everything, clock included, one browser frame per live output frame
(``overlay_hud.hud_frame_plan``).

Any failure of the template's own (a script error, a timeout, a crashed
page) is a :class:`HudFallback`: the caller draws Classic and says why. A
failure that would sink Classic too (ffmpeg missing or failing) stays an
``OverlayRenderError``.

The cache is the render segment cache. The key is the encode argv with
its stdin standing for the template digest, so a hit is found before the
browser renders a frame, and a hit leaves an identical output file alone:
the MP4 segment cache keys on the overlay file's mtime.
"""

from __future__ import annotations

import filecmp
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from .look_template import TemplateContext, engine_block, shared_url, template_digest, theme_tokens
from .overlay_html import single_css
from .overlay_hud import (
    HUD_KEY_VERSION,
    HudFrame,
    HudOptions,
    declared_positions,
    hud_frame_plan,
    hud_options_data,
    hud_page_size,
    hud_stage_data,
    resolve_position,
)
from .overlay_layout import CellScale
from .overlay_raster import Rasterizer, TemplateScriptError
from .overlay_text import OverlayRenderError
from .overlay_theme import OverlayTheme
from .segment_cache import SegmentCache
from .stage_summary_data import load_stage_shots

logger = logging.getLogger(__name__)

_CACHEABLE_SUFFIXES = (".mov", ".mp4")


class HudFallback(Exception):
    """The template could not draw this stage; draw Classic instead."""


def hud_context(
    *, stage: dict, options: dict, theme: OverlayTheme, width: int, height: int, fps: float
) -> TemplateContext:
    """``window.splitsmith`` for a HUD: the stage and the options as data,
    the theme's tokens, and the engine stylesheet for its font faces."""
    return TemplateContext(
        theme=theme_tokens(theme),
        data={"stage": stage, "options": options},
        size={"width": width, "height": height},
        fps=fps,
        engine=engine_block(
            css=single_css(width=width, height=height, scale=CellScale.for_cell(height), theme=theme)
        ),
        assets={"shared": shared_url()},
    )


def _install(source: Path, output_path: Path) -> None:
    """Put a cached MOV at ``output_path`` unless it is already there byte
    for byte (an untouched file keeps its mtime, and with it the MP4
    segment key)."""
    if output_path.exists() and filecmp.cmp(source, output_path, shallow=False):
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(f".{output_path.name}.part")
    shutil.copyfile(source, partial)
    partial.replace(output_path)


def render_hud_overlay(
    *,
    template: Path,
    audit_path: Path,
    output_path: Path,
    beep_offset_seconds: float,
    duration_seconds: float,
    width: int,
    height: int,
    rate_num: int,
    rate_den: int,
    codec: Literal["hevc-alpha", "prores-4444"],
    ffmpeg_binary: str,
    theme: OverlayTheme,
    options: HudOptions,
    rasterizer: Rasterizer,
    segment_cache: SegmentCache | None,
) -> Path:
    """Render the stage's HUD through ``template`` into ``output_path``."""
    # Deferred: overlay_render imports this module, and the encoder argv
    # and the partial-output rule are its own, shared by both paths.
    from .overlay_render import _build_ffmpeg_cmd, _discard_partial_output

    shots = load_stage_shots(audit_path)
    if not shots:
        raise HudFallback("no readable shots in the audit")
    fps = rate_num / rate_den
    frame_count = max(0, int(round(duration_seconds * fps)))
    page_width, page_height = hud_page_size(width, height)
    stage = hud_stage_data(shots, beep_in_clip=beep_offset_seconds)
    position = resolve_position(options.position, declared_positions(template))
    context = hud_context(
        stage=stage,
        options=hud_options_data(options, position),
        theme=theme,
        width=page_width,
        height=page_height,
        fps=fps,
    )
    scale = None if (page_width, page_height) == (width, height) else f"scale={width}:{height}:flags=lanczos"
    rate = f"{rate_num}/{rate_den}"
    use_cache = segment_cache is not None and output_path.suffix in _CACHEABLE_SUFFIXES

    with tempfile.TemporaryDirectory(prefix="splitsmith-hud-") as work:
        key: str | None = None
        target = output_path
        if use_cache:
            assert segment_cache is not None
            probe_argv = _build_ffmpeg_cmd(
                ffmpeg_binary=ffmpeg_binary,
                codec=codec,
                width=page_width,
                height=page_height,
                rate=rate,
                output_path=output_path,
                clock_filter=scale,
            )
            digest = template_digest(template, context, fps=fps, engine_version=rasterizer.engine_version())
            key = segment_cache.key(
                tuple(probe_argv),
                output_path=output_path,
                work_dir=Path(work),
                virtual_inputs={"-": f"hud-v{HUD_KEY_VERSION}:{digest}:frames={frame_count}"},
            )
            hit = segment_cache.lookup(key, suffix=output_path.suffix)
            if hit is not None:
                _install(hit, output_path)
                return output_path
            target = segment_cache.partial_path(key, suffix=output_path.suffix)

        cmd = _build_ffmpeg_cmd(
            ffmpeg_binary=ffmpeg_binary,
            codec=codec,
            width=page_width,
            height=page_height,
            rate=rate,
            output_path=target,
            clock_filter=scale,
        )
        plans: list[tuple[HudFrame, ...]] = []

        def plan(settle: float) -> list[float]:
            frames = hud_frame_plan(
                frame_count=frame_count,
                fps=fps,
                beep=beep_offset_seconds,
                last_shot=stage["shots"][-1]["t"],
                settle=settle,
            )
            plans.append(frames)
            return [frame.seek for frame in frames]

        try:
            rendered = rasterizer.render_template_timeline(
                template, context=context, width=page_width, height=page_height, plan=plan
            )
        except TemplateScriptError as exc:
            raise HudFallback(str(exc)) from exc

        target.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdin is not None
        piped = False
        try:
            for buffer, frame in zip(rendered.frames, plans[0], strict=True):
                for _ in range(frame.count):
                    proc.stdin.write(buffer)
                    piped = True
            proc.stdin.close()
        except BaseException as exc:
            # Kill and reap before reading stderr (see overlay_render's
            # Classic loop for why the order matters), and throw away what
            # was written: a truncated MOV must never reach the timeline.
            proc.kill()
            proc.wait()
            _discard_partial_output(target, piped_frames=piped)
            if isinstance(exc, TemplateScriptError):
                raise HudFallback(str(exc)) from exc
            if not isinstance(exc, Exception):
                raise
            stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            raise OverlayRenderError(f"ffmpeg failed during render: {stderr or exc}") from exc
        finally:
            rendered.close()
        rc = proc.wait()
        stderr_text = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if rc != 0:
            _discard_partial_output(target, piped_frames=piped)
            raise OverlayRenderError(f"ffmpeg exited with {rc}: {stderr_text}")

        if use_cache:
            assert segment_cache is not None and key is not None
            final = segment_cache.commit(target, key, suffix=output_path.suffix)
            segment_cache.evict(keep={key})
            _install(final, output_path)
    return output_path


__all__ = ["HudFallback", "hud_context", "render_hud_overlay"]
```

Check `_discard_partial_output`'s contract before relying on it on a cache partial: it unlinks when `piped_frames` is true. A cache partial is never a previous good render, so deleting it is always right; if the function keeps a file when nothing was piped, that keeps an empty partial in the cache dir, which `SegmentCache.evict` removes after a day. Acceptable; note it in the docstring only if review asks.

- [ ] **Step 4: Wire `render_overlay`**

Add imports at the top of `overlay_render.py`:

```python
from .looks import DEFAULT_VARIANT, load_look, overlay_template_for
from .overlay_hud import HudOptions
from .segment_cache import SegmentCache
```

Extend the signature after `probe_runner`:

```python
    variant: str | None = None,
    hud_options: HudOptions | None = None,
    segment_cache: SegmentCache | None = None,
    degraded: list[str] | None = None,
```

Document the four parameters in the docstring:

```
    ``variant``: the Look's ``overlay`` variant. ``None`` or ``"default"``
        is Classic, this function's own path, unchanged. A template
        variant draws the whole HUD through the template
        (``overlay_hud_render``); a template failure, or a variant the
        Look cannot resolve, draws Classic and appends a line to
        ``degraded``.
    ``hud_options``: the template HUD's toggles and position; ignored by
        Classic.
    ``segment_cache``: where a template HUD's MOV is cached; ignored by
        Classic, which has no cache.
    ``degraded``: collects one export-report line per fallback.
```

Then, directly after `fps = rate_num / rate_den` and `duration_seconds = probe.duration_seconds`, and before `scale = CellScale.for_cell(height)`:

```python
    if variant is not None and variant != DEFAULT_VARIANT:
        from .overlay_hud_render import HudFallback, render_hud_overlay

        template = overlay_template_for(load_look(theme), variant)
        if template is None:
            note = f"overlay style {variant!r} is not in Look {theme!r}; drew Classic"
            logger.warning(note)
            if degraded is not None:
                degraded.append(note)
        else:
            try:
                with _rasterizer_for(rasterizer) as active:
                    return render_hud_overlay(
                        template=template,
                        audit_path=audit_path,
                        output_path=output_path,
                        beep_offset_seconds=beep_offset_seconds,
                        duration_seconds=duration_seconds,
                        width=width,
                        height=height,
                        rate_num=rate_num,
                        rate_den=rate_den,
                        codec=resolved_codec,
                        ffmpeg_binary=ffmpeg_binary,
                        theme=load_theme(theme),
                        options=hud_options or HudOptions(),
                        rasterizer=active,
                        segment_cache=segment_cache,
                    )
            except HudFallback as exc:
                note = f"overlay style {variant!r} fell back to Classic: {exc}"
                logger.warning(note)
                if degraded is not None:
                    degraded.append(note)
```

The import is deferred because `overlay_hud_render` imports this module's encoder helpers. Everything below this block is the Classic path, untouched.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_overlay_hud_render.py tests/test_overlay_render.py tests/test_looks.py -n0 -q`
Expected: pass. The Classic suite (`test_overlay_render.py`) must pass with no edits; an edit there means Classic moved.

- [ ] **Step 6: Prove three tests bite**

One at a time, restore after each:
- In `_install`, drop the `filecmp.cmp` early return: `test_a_cache_hit_renders_no_frame_and_leaves_the_mov_alone` must fail on the mtime.
- In `render_overlay`, remove the `except HudFallback` block's body and re-raise: `test_a_template_that_throws_mid_run_falls_back_to_classic_with_a_note` must fail.
- In `hud_context`, drop `"options"` from `data`: the `speed_colors` case of `test_any_input_change_misses_the_cache` must fail.

- [ ] **Step 7: Commit**

```bash
/usr/bin/git add src/splitsmith/overlay_hud_render.py src/splitsmith/overlay_render.py \
  src/splitsmith/data/looks/splitsmith/look.json src/splitsmith/data/looks/splitsmith/hud-plate.html \
  tests/test_overlay_hud_render.py tests/test_looks.py
/usr/bin/git commit -m "feat(overlay): draw the HUD through a Look template, cached, with a Classic fallback"
```

---

### Task 5: the Plate template

**Files:**
- Modify: `src/splitsmith/data/looks/splitsmith/hud-plate.html` (replace the placeholder)
- Test: `tests/test_overlay_hud_render.py` (integration tests at the end)

**Interfaces:**
- Consumes: `window.splitsmith.data.stage` / `.options` (Task 1's shapes), `window.splitsmith.theme` tokens (`ink`, `ink_2`, `split`, `split_good`, `accent`, `surface`), the engine stylesheet's `"Splitsmith Mono"` / `"Splitsmith Display"` faces.
- Produces: a template defining `seek(t)` and `settle()`, declaring `bottom-left,top-left,top-right,bottom-right`.

- [ ] **Step 1: Write the failing integration tests** (append)

```python
# --- the shipped Plate, through real Chromium and ffmpeg ------------------------

import subprocess as _subprocess  # noqa: E402

from splitsmith.overlay_raster import ChromiumRasterizer  # noqa: E402
from tests.synthetic_media import ffmpeg_available  # noqa: E402


def _frame_at(mov: Path, index: int, out: Path) -> Image.Image:
    _subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mov), "-vf", f"select=eq(n\\,{index})",
         "-frames:v", "1", "-pix_fmt", "rgba", str(out)],
        check=True,
    )
    return Image.open(out).convert("RGBA")


def _alpha_in(image: Image.Image, box: tuple[int, int, int, int]) -> int:
    return max(image.crop(box).getchannel("A").getdata())


@pytest.mark.integration
@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg")
def test_plate_renders_a_stage_into_a_mov_the_trim_s_length(tmp_path: Path) -> None:
    meta = VideoMetadata(width=640, height=360, duration_seconds=3.0, frame_rate_num=30, frame_rate_den=1)
    out = tmp_path / "overlay.mov"
    with ChromiumRasterizer() as rasterizer:
        overlay_render.render_overlay(
            audit_path=_audit(tmp_path),
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=out,
            beep_offset_seconds=1.0,
            probe=meta,
            codec="prores-4444",
            rasterizer=rasterizer,
            variant="plate",
        )
    frames = _subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
         "stream=nb_read_frames", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert int(frames) == 90
    # Bottom-left plate: drawn before the beep (the counter reads 00/03) and after a shot.
    bottom_left = (0, 220, 320, 360)
    top_right = (400, 0, 640, 140)
    pre_beep = _frame_at(out, 10, tmp_path / "pre.png")
    after_shot = _frame_at(out, 50, tmp_path / "shot.png")
    assert _alpha_in(pre_beep, bottom_left) > 200
    assert _alpha_in(after_shot, bottom_left) > 200
    assert _alpha_in(after_shot, top_right) == 0, "nothing drawn outside the HUD's corner"


@pytest.mark.integration
@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg")
def test_plate_honours_the_position(tmp_path: Path) -> None:
    meta = VideoMetadata(width=640, height=360, duration_seconds=2.0, frame_rate_num=30, frame_rate_den=1)
    out = tmp_path / "overlay.mov"
    with ChromiumRasterizer() as rasterizer:
        overlay_render.render_overlay(
            audit_path=_audit(tmp_path),
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=out,
            beep_offset_seconds=1.0,
            probe=meta,
            codec="prores-4444",
            rasterizer=rasterizer,
            variant="plate",
            hud_options=HudOptions(position="top-right"),
        )
    frame = _frame_at(out, 50, tmp_path / "f.png")
    assert _alpha_in(frame, (400, 0, 640, 140)) > 200
    assert _alpha_in(frame, (0, 220, 320, 360)) == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `PATH="$PWD/desktop/build/bin:$PATH" uv run pytest tests/test_overlay_hud_render.py -n0 -q -m integration`
Expected: FAIL. The placeholder defines no `seek`, so the render falls back to Classic, whose counter sits top-left and whose clock sits top-right: `_alpha_in(after_shot, top_right) == 0` fails.

- [ ] **Step 3: Write the template**

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="splitsmith-positions" content="bottom-left,top-left,top-right,bottom-right">
<title>hud plate</title>
<script>
  // The shipped Plate HUD (spec 2026-10-08): the clock on a dark plate with
  // a progress line, the shot count, and the last split as a chip with its
  // class and speed. seek(t) draws any instant from the stage data alone;
  // nothing reads the wall clock, so every frame is reproducible. The engine
  // stylesheet is loaded for its font faces only.
  document.write('<style>' + window.splitsmith.engine.css + '</style>');
</script>
<style>
  html, body { margin: 0; padding: 0; background: transparent; overflow: hidden; }
  #hud { position: absolute; display: flex; align-items: stretch; gap: 0.9vh; }
  #hud.bottom-left { left: 3.5vh; bottom: 4vh; }
  #hud.top-left { left: 3.5vh; top: 3.5vh; }
  #hud.top-right { right: 3.5vh; top: 3.5vh; flex-direction: row-reverse; }
  #hud.bottom-right { right: 3.5vh; bottom: 4vh; flex-direction: row-reverse; }
  .plate {
    position: relative; overflow: hidden; border-radius: 1.2vh;
    padding: 0.9vh 2.6vh; display: flex; flex-direction: column; justify-content: center;
  }
  .label {
    font-family: "Splitsmith Display", sans-serif; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.14em; font-size: 1.9vh; line-height: 2.6vh; height: 2.6vh; white-space: nowrap;
  }
  .num {
    font-family: "Splitsmith Mono", monospace; font-weight: 700;
    font-variant-numeric: tabular-nums; line-height: 1; white-space: nowrap;
  }
  #clock { font-size: 9.6vh; }
  #count { font-size: 5.8vh; transform-origin: 50% 70%; }
  #count small { font-size: 0.6em; }
  #bar { position: absolute; left: 0; bottom: 0; height: 0.6vh; width: 0; }
  #chip { align-self: flex-end; border-left: 0.8vh solid transparent; }
  #split { font-size: 5.2vh; }
</style>
</head>
<body>
<div id="hud">
  <div class="plate" id="clockPlate">
    <div class="label" id="clockLabel">Time</div>
    <div class="num" id="clock">0.00</div>
    <div id="bar"></div>
  </div>
  <div class="plate" id="countPlate">
    <div class="label">Shots</div>
    <div class="num" id="count"></div>
  </div>
  <div class="plate" id="chip">
    <div class="label" id="chipLabel"></div>
    <div class="num" id="split"></div>
  </div>
</div>
<script>
(function () {
  var s = window.splitsmith;
  var theme = s.theme, stage = s.data.stage, opts = s.data.options;
  var shots = stage.shots, beep = stage.beep, rounds = stage.rounds;
  var PUNCH = 0.22, CHIP_IN = 0.18, BEEP_GLOW = 0.4, LANDING = 0.6;
  var PLATE_ALPHA = 0.72;

  function el(id) { return document.getElementById(id); }
  function clamp(x) { return Math.max(0, Math.min(1, x)); }
  function easeOut(x) { x = clamp(x); return 1 - Math.pow(1 - x, 3); }
  function rgb(hex) {
    var n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function plate(k) {
    // The surface tone, lit towards the accent by k (0..1).
    var a = rgb(theme.surface), b = rgb(theme.accent_fill || theme.accent);
    var c = a.map(function (v, i) { return Math.round(v + (b[i] - v) * k); });
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + PLATE_ALPHA + ')';
  }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function tierColour(tier) {
    if (!opts.speed_colors || !tier) return theme.split;
    return tier === 'good' ? theme.split_good : tier === 'slow' ? theme.accent : theme.split;
  }

  var hud = el('hud');
  hud.className = opts.position || 'bottom-left';
  hud.style.color = theme.ink;
  document.querySelectorAll('.label').forEach(function (l) { l.style.color = theme.ink_2; });
  ['clockPlate', 'countPlate', 'chip'].forEach(function (id) { el(id).style.background = plate(0); });
  el('bar').style.background = theme.accent;
  if (!opts.class_labels) el('chipLabel').style.display = 'none';

  window.settle = function () {
    return opts.landing ? LANDING : Math.max(PUNCH, CHIP_IN);
  };

  window.seek = function (t) {
    var n = 0;
    while (n < shots.length && shots[n].t <= t) n++;
    var last = n ? shots[n - 1] : null;
    var landed = n === shots.length && n > 0 ? t - last.t : -1;
    var running = landed >= 0 ? stage.stage_time : Math.max(0, t - beep);
    var since = last ? t - last.t : Infinity;

    el('clock').textContent = running.toFixed(2);
    el('bar').style.width = (rounds ? 100 * n / rounds : 0) + '%';
    var landing = opts.landing && landed >= 0;
    el('clockLabel').textContent = landing ? 'Stage time' : 'Time';
    var flash = landing ? Math.max(0, 1 - landed / LANDING) : 0;
    el('clockPlate').style.background = plate(flash);
    var glow = t >= beep && t - beep < BEEP_GLOW ? 1 - (t - beep) / BEEP_GLOW : 0;
    el('clockPlate').style.boxShadow = glow > 0 ? 'inset 0 0 0 ' + (0.4 * glow) + 'vh ' + theme.accent : 'none';

    var punch = since < PUNCH ? 1 + 0.3 * (1 - easeOut(since / PUNCH)) : 1;
    el('count').style.transform = 'scale(' + punch + ')';
    el('count').innerHTML = pad2(n) + '<small>/' + rounds + '</small>';

    var chip = el('chip');
    if (!last) { chip.style.visibility = 'hidden'; return; }
    chip.style.visibility = 'visible';
    var k = easeOut(since / CHIP_IN);
    chip.style.opacity = k;
    chip.style.transform = 'translateX(' + (-3 * (1 - k)) + 'vh)';
    chip.style.borderLeftColor = tierColour(last.tier);
    el('chipLabel').textContent = last.label || '';
    el('split').textContent = last.split.toFixed(2);
    el('split').style.color = tierColour(last.tier);
  };
})();
</script>
</body>
</html>
```

The chip keeps its box while hidden (`visibility`, not `display`), so the HUD's width never jumps when the first shot lands.

- [ ] **Step 4: Run the tests, then look at the frames**

Run: `PATH="$PWD/desktop/build/bin:$PATH" uv run pytest tests/test_overlay_hud_render.py tests/test_looks.py -n0 -q -m "integration or not integration"`
Expected: pass. Then render real frames with the script from Task 6 (or, before it exists, a one-off `render_overlay` call composited over a synthetic clip) and open the PNGs: the pre-beep frame, a frame right after a shot (punch and chip mid-slide), a frame after the reload, and the landing. A green pixel test proves the corner has paint, not that it reads well.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/data/looks/splitsmith/hud-plate.html tests/test_overlay_hud_render.py
/usr/bin/git commit -m "feat(looks): the Plate HUD, the first template overlay"
```

---

### Task 6: the CLI, the frame script and the docs

**Files:**
- Modify: `src/splitsmith/cli.py` (the `overlay` command near line 1167)
- Modify: `scripts/render_overlay_frames.py` (arguments near line 138, the `render_overlay` call near line 164)
- Modify: `CLAUDE.md` (a paragraph under "Rendered cards and stage summaries")
- Test: `tests/test_overlay_render.py` (CLI cases beside the existing `CliRunner` ones)

**Interfaces:**
- Consumes: `render_overlay(variant=, hud_options=, segment_cache=, degraded=)`, `HUD_POSITIONS`, `HudOptions`, `looks.variants_for`, `looks.OVERLAY_SLOT`, `match_exports.render_segment_cache`, `Config.load`.
- Produces: `splitsmith overlay --overlay-variant NAME --overlay-position POS --no-speed-colors --no-class-labels --no-landing`.

- [ ] **Step 1: Write the failing CLI tests**

```python
def test_overlay_cli_passes_the_hud_options_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_render(**kwargs: Any) -> Path:
        seen.update(kwargs)
        kwargs["degraded"].append("overlay style 'plate' fell back to Classic: boom")
        return kwargs["output_path"]

    monkeypatch.setattr(overlay_render, "render_overlay", fake_render)
    result = CliRunner().invoke(
        app,
        [
            "overlay", "--audit", str(_write_audit(tmp_path)), "--video", str(tmp_path / "t.mp4"),
            "--output", str(tmp_path / "o.mov"), "--overlay-variant", "plate",
            "--overlay-position", "top-right", "--no-speed-colors", "--no-landing",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["variant"] == "plate"
    assert seen["hud_options"].model_dump() == {
        "speed_colors": False, "class_labels": True, "landing": False, "position": "top-right",
    }
    assert "fell back to Classic" in result.output


def test_overlay_cli_refuses_an_unknown_variant_and_position(tmp_path: Path) -> None:
    base = ["overlay", "--audit", str(_write_audit(tmp_path)), "--video", str(tmp_path / "t.mp4"),
            "--output", str(tmp_path / "o.mov")]
    bad_variant = CliRunner().invoke(app, [*base, "--overlay-variant", "nope"])
    assert bad_variant.exit_code != 0 and "plate" in bad_variant.output
    bad_position = CliRunner().invoke(app, [*base, "--overlay-position", "middle"])
    assert bad_position.exit_code != 0 and "bottom-left" in bad_position.output
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_overlay_render.py -n0 -q -k overlay_cli`
Expected: FAIL: `No such option: --overlay-variant`.

- [ ] **Step 3: Implement the flags**

In `cli.py`'s `overlay` command, after `theme`:

```python
    overlay_variant: str = typer.Option(
        "default",
        "--overlay-variant",
        help=(
            "Overlay style: 'default' is Classic (fast); a template style such as 'plate' draws the "
            "whole HUD with motion and renders slower."
        ),
    ),
    overlay_position: str | None = typer.Option(
        None, "--overlay-position", help="Template styles: top-left, top-right, bottom-left or bottom-right."
    ),
    speed_colors: bool = typer.Option(True, "--speed-colors/--no-speed-colors", help="Colour splits by speed."),
    class_labels: bool = typer.Option(
        True, "--class-labels/--no-class-labels", help="Show draw, split, transition and reload labels."
    ),
    landing: bool = typer.Option(True, "--landing/--no-landing", help="The landing moment on the last shot."),
```

In the body, after `_validate_theme(theme)`:

```python
    from .config import Config
    from .looks import DEFAULT_VARIANT, OVERLAY_SLOT, load_look, variants_for
    from .overlay_hud import HUD_POSITIONS, HudOptions
    from .ui.match_exports import render_segment_cache

    styles = (DEFAULT_VARIANT, *variants_for(load_look(theme), OVERLAY_SLOT))
    if overlay_variant not in styles:
        raise typer.BadParameter(f"--overlay-variant must be one of {', '.join(styles)}, got {overlay_variant!r}")
    if overlay_position is not None and overlay_position not in HUD_POSITIONS:
        raise typer.BadParameter(
            f"--overlay-position must be one of {', '.join(HUD_POSITIONS)}, got {overlay_position!r}"
        )
    degraded: list[str] = []
```

and pass to `render_overlay`:

```python
        variant=overlay_variant,
        hud_options=HudOptions(
            speed_colors=speed_colors,
            class_labels=class_labels,
            landing=landing,
            position=overlay_position,  # type: ignore[arg-type]
        ),
        segment_cache=render_segment_cache(Config.load(None).output),
        degraded=degraded,
```

then after the call: `for note in degraded: console.print(f"[yellow]{note}[/]")`. Check how other commands load the default config (`Config.load(None)` or a `--config` option on this app) and follow theirs if it differs. If importing `ui.match_exports` from `cli.py` pulls server-only modules, move `render_segment_cache` (and `RENDER_CACHE_ENV`) into `segment_cache.py` and re-export it from `match_exports`; `scripts/ci/assert_slim_import_surface.py` is the check.

- [ ] **Step 4: The frame script**

In `scripts/render_overlay_frames.py`, add:

```python
    parser.add_argument("--overlay-variant", default="default", help="overlay style: default (Classic) or a template")
    parser.add_argument("--overlay-position", default=None, choices=(None, *HUD_POSITIONS))
    parser.add_argument("--no-speed-colors", action="store_true")
    parser.add_argument("--no-class-labels", action="store_true")
    parser.add_argument("--no-landing", action="store_true")
```

(import `HUD_POSITIONS, HudOptions` from `splitsmith.overlay_hud` with the other `noqa: E402` imports), and pass `variant=args.overlay_variant` and `hud_options=HudOptions(speed_colors=not args.no_speed_colors, class_labels=not args.no_class_labels, landing=not args.no_landing, position=args.overlay_position)` to its `render_overlay` call. Run it:

```bash
PATH="$PWD/desktop/build/bin:$PATH" uv run python scripts/render_overlay_frames.py --overlay-variant plate --out build/overlay-frames-plate
PATH="$PWD/desktop/build/bin:$PATH" uv run python scripts/render_overlay_frames.py --out build/overlay-frames-classic
```

Compare `build/overlay-frames-classic` against the same command on `main` (`/usr/bin/git stash` is off limits: check out `main` in a scratch worktree under `~/.claude-tmp/` and run it there): Classic's frames must be pixel-identical. Open the Plate frames and look at every moment.

- [ ] **Step 5: CLAUDE.md**

Add after the paragraph about the stage card's round count, in "Rendered cards and stage summaries":

```markdown
The live overlay has **template styles** (spec ``2026-10-08-template-hud-overlay-design``):
a Look's ``overlay`` slot names HUD templates (``plate`` ships first);
``default`` is always Classic, the engine path (run-length PNGs and the
``drawtext`` clock), and a manifest cannot name a file for it.
``render_overlay(variant=...)`` sends a template variant through
``overlay_hud_render``: the template draws clock, counter and split per
frame through ``seek(t)`` and declares ``settle()``, the seconds it moves
after the last shot; ``overlay_hud.hud_frame_plan`` renders only the beep
to ``last shot + settle`` and holds a frame either side. The data
(splits, coach classes, speed tiers) is computed in ``overlay_hud``, never
in a template. The MOV is cached in the render segment cache by the
template digest and left untouched on a hit (the MP4 keys on its mtime). A
template failure or an unknown variant draws Classic and lands in
``degraded``; no browser is still ``OverlayRenderError``. Classic's argv
and pixels never change for any of this; check with
``scripts/render_overlay_frames.py`` against main.
```

- [ ] **Step 6: Full check and commit**

```bash
uv run pytest tests/test_overlay_hud.py tests/test_overlay_hud_render.py tests/test_overlay_render.py \
  tests/test_overlay_raster.py tests/test_looks.py tests/test_looks_api.py tests/test_looks_cli.py -n0 -q
uv run ruff check src tests scripts
uv run black --check src tests scripts
uv run python scripts/ci/assert_slim_import_surface.py
/usr/bin/git add src/splitsmith/cli.py scripts/render_overlay_frames.py CLAUDE.md tests/test_overlay_render.py
/usr/bin/git commit -m "feat(cli): overlay styles on the overlay verb and the frame script"
```

Then run the whole suite once (`uv run pytest -q`) and the integration tests with the project ffmpeg on PATH. Put the Plate frames (every moment, both positions, landing on and off) on an Urdr page for review before opening the PR.
