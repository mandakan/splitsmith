"""Declaration to sprite: the single-shooter overlay (issue #684).

What one shooter's overlay says while a stage is in progress -- the shot
counter and the last split -- as declared ``Group``/``Element`` objects,
and the run-length encoding that decides how often that changes. This
module is to the single-shooter export exactly what
``compare/overlay_live.py`` is to the grid's per-tile sprites, and it is
deliberately a separate module rather than a parameter on that one: the
two disagree about what to draw before the first shot, and a flag
threaded through ``panel_groups`` to express that would make one
function answer to two products.

**Why runs rather than the grid's state machine.** The grid steps on shot
*events* and then has to snap every resulting boundary onto a whole
output frame (``overlay_sprites.quantize_durations``), because a shot
time is millisecond-grained and lands wherever it lands. Here the
boundaries are frame indices from the start: ``build_frame_states``
already evaluated every frame, so collapsing consecutive frames that say
the same thing produces exactly the same ~31 states with no rounding step
at all. The class of bug ``quantize_durations`` exists to prevent is
absent rather than solved.

**Nothing here measures text**, opens a font, or touches PIL. A run
becomes a document via ``overlay_html.single_html`` and pixels via an
injected ``overlay_raster.Rasterizer``; this module only decides what the
strings are and when they change.

**The clock is not here.** It ticks every frame, so it can never be a
run -- it is an ffmpeg ``drawtext`` filter built from
``overlay_clock``. See ``overlay_render`` for where the two halves meet.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .overlay_layout import Anchor, ColorToken, Element, Flow, Group, Role


class _FrameLike(Protocol):
    """The three fields a run is keyed on.

    A structural type rather than an import of
    ``overlay_render.FrameState``: ``overlay_render`` imports *this*
    module, and naming its type here would close the cycle. Everything
    else in the overlay pipeline (``overlay_layout``, ``overlay_html``,
    ``overlay_theme``) sits at the leaf of the import graph for the same
    reason, and this module belongs there with them.
    """

    shot_count: int
    shots_fired: int
    last_split: float | None


@dataclass(frozen=True)
class OverlayRun:
    """One stretch of frames over which the overlay says the same thing.

    ``frame_count`` is how many frames the run spans, so a caller pipes
    one rasterized buffer exactly that many times. ``start_frame`` is not
    needed to render but is what makes a run addressable in a test and a
    log line -- "the counter changed at frame 41" is a reviewable claim;
    "the third run" is not.

    ``last_split`` is ``None`` only before the first shot. From shot 1 it
    carries what ``build_frame_states`` computed: for shot 1 that is the
    time from the beep -- the draw -- and for every shot after it the gap
    from the previous one. Both are numbers the shooter reads off the
    screen, so both are drawn.
    """

    start_frame: int
    frame_count: int
    shots_fired: int
    shot_count: int
    last_split: float | None


def build_overlay_runs(states: Sequence[_FrameLike]) -> tuple[OverlayRun, ...]:
    """Collapse per-frame states into the runs the overlay actually has.

    Consecutive frames agreeing on ``(shots_fired, last_split)`` become
    one run. For a 30-shot stage that is 31 runs against 600 frames --
    and since a run is one browser render, that ratio is the whole
    reason this port is worth doing.

    Note the count is one per distinct ``shots_fired`` value, **not**
    shots plus one: two shots inside the same frame step the counter
    straight past the intermediate value, and there is no frame on which
    to draw it.

    An empty ``states`` yields no runs, which pipes no frames -- correct
    for a zero-length clip, and unreachable in practice because
    ``render_overlay`` rejects an audit with no shots long before here.
    """
    runs: list[OverlayRun] = []
    for index, state in enumerate(states):
        key = (state.shots_fired, state.last_split)
        if runs and (runs[-1].shots_fired, runs[-1].last_split) == key:
            last = runs[-1]
            runs[-1] = OverlayRun(
                start_frame=last.start_frame,
                frame_count=last.frame_count + 1,
                shots_fired=last.shots_fired,
                shot_count=last.shot_count,
                last_split=last.last_split,
            )
            continue
        runs.append(
            OverlayRun(
                start_frame=index,
                frame_count=1,
                shots_fired=state.shots_fired,
                shot_count=state.shot_count,
                last_split=state.last_split,
            )
        )
    return tuple(runs)


def run_groups(run: OverlayRun) -> tuple[Group, ...]:
    """What the overlay says over one run.

    Two things, in the two places they have always been drawn:

    - the **shot counter** at :attr:`~splitsmith.overlay_layout.Anchor.TOP_LEFT`,
      reading ``"7/32"``;
    - the **last split** at
      :attr:`~splitsmith.overlay_layout.Anchor.BOTTOM_CENTER` in the
      theme's split colour, absent only before the first shot. Shot 1's
      figure is the draw (its time from the beep), which is what
      ``build_frame_states`` puts in ``last_split`` and what today's
      renderer already draws.

    **The counter is drawn from frame zero, reading ``0/32``.** The grid
    (``compare/overlay_live.panel_groups``) deliberately draws nothing
    until a shot fires, and that rule is right *there*: four tiles all
    reading ``0/32`` over four people standing at the start position is a
    number, not information. It is wrong here. On a single-shooter frame
    the counter is the only thing on screen before the beep, and the
    denominator tells the viewer how long the stage is. This difference
    is why the two paths declare their own groups instead of sharing one
    function.

    Both elements are :attr:`~splitsmith.overlay_layout.Role.LIVE_PRIMARY`,
    which at 1x1 resolves to ``max(48, height // 14)`` -- byte-identical
    to what the PIL template computed for both, so the type size does not
    move.
    """
    groups: list[Group] = []
    if run.shot_count > 0:
        groups.append(
            Group(
                anchor=Anchor.TOP_LEFT,
                flow=Flow.ROW,
                elements=(Element(text=f"{run.shots_fired}/{run.shot_count}", role=Role.LIVE_PRIMARY),),
            )
        )
    if run.last_split is not None:
        groups.append(
            Group(
                anchor=Anchor.BOTTOM_CENTER,
                flow=Flow.ROW,
                elements=(
                    Element(
                        text=f"{run.last_split:.2f}s",
                        role=Role.LIVE_PRIMARY,
                        color=ColorToken.SPLIT,
                    ),
                ),
            )
        )
    return tuple(groups)
