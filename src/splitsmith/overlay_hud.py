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

from pydantic import BaseModel, ConfigDict, field_validator

from .config import StageEvent
from .events import confirmed, reload_figures, shot_is_moving
from .looks import DEFAULT_VARIANT, LOOK_NAME_RE
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

    speed_colors: bool = False
    class_labels: bool = True
    landing: bool = True
    #: Draw a chip counting a confirmed reload's time (spec 2026-10-08,
    #: part 2). Off by default; a stage without confirmed regions draws
    #: exactly as before whatever these say.
    reload_chip: bool = False
    #: Draw a thin stage bar with the movement and reload bands.
    stage_bar: bool = False
    #: ``None`` is the variant's own default (the first position it declares).
    position: HudPosition | None = None


class OverlayStyleFields(BaseModel):
    """The overlay style as every body that draws an overlay carries it:
    the stage and match export requests, and the export preset. Validated
    by shape only, as a preset's Look is, so a body loads on a machine
    without that style; a style the Look lacks draws Classic with a note."""

    #: The Look's ``overlay`` variant; ``default`` is Classic.
    overlay_variant: str = DEFAULT_VARIANT
    overlay_speed_colors: bool = False
    overlay_class_labels: bool = True
    overlay_landing: bool = True
    overlay_reload_chip: bool = False
    overlay_stage_bar: bool = False
    overlay_position: HudPosition | None = None

    @field_validator("overlay_variant")
    @classmethod
    def _variant_shape(cls, value: str) -> str:
        if not LOOK_NAME_RE.match(value):
            raise ValueError(f"{value!r} is not an overlay style name ({LOOK_NAME_RE.pattern})")
        return value

    def hud_options(self) -> HudOptions:
        return HudOptions(
            speed_colors=self.overlay_speed_colors,
            class_labels=self.overlay_class_labels,
            landing=self.overlay_landing,
            reload_chip=self.overlay_reload_chip,
            stage_bar=self.overlay_stage_bar,
            position=self.overlay_position,
        )


def overlay_settings(
    *,
    look: str,
    variant: str,
    options: HudOptions,
    codec: str,
    max_height: int | None,
    max_fps: float | None,
    audit_revision: str | None,
) -> dict[str, Any]:
    """What an overlay MOV was drawn with, as recorded beside it and
    compared before a match export reuses it. Classic draws none of the
    template options, so they are left out of a Classic record: toggling
    them with Classic chosen never forces a re-render.

    ``audit_revision`` is the revision of the audit doc the overlay was drawn
    from (``ui.exports.overlay_audit_revision``), for every style: a shot
    edit or a confirmed region moves what any style draws, so an overlay is
    never reused across an audit change."""
    return {
        "look": look,
        "variant": variant,
        "options": options.model_dump() if variant != DEFAULT_VARIANT else {},
        "codec": codec,
        "max_height": max_height,
        "max_fps": max_fps,
        "audit_revision": audit_revision,
    }


#: What an overlay rendered before the record existed is taken to be: the
#: defaults with no audit revision. It cannot say which audit it was drawn
#: from, so it matches no request and is drawn again once.
LEGACY_OVERLAY_SETTINGS: dict[str, Any] = overlay_settings(
    look="splitsmith",
    variant=DEFAULT_VARIANT,
    options=HudOptions(),
    codec="auto",
    max_height=None,
    max_fps=None,
    audit_revision=None,
)


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


def _clip(beep_in_clip: float, seconds_from_beep: float) -> float:
    return round(beep_in_clip + max(0.0, seconds_from_beep), 6)


def hud_stage_data(
    shots: Sequence[TileShot], *, beep_in_clip: float, events: Sequence[StageEvent] = ()
) -> dict[str, Any]:
    """``data.stage``: the beep and every shot in clip seconds, each with
    its split, class, label, tier and whether it was fired moving. A shot
    the audit places before the beep is drawn at the beep: the HUD is
    static before it (the frame plan holds one frame there), as Classic
    clamps the same case. Numbers are rounded to the microsecond so float
    noise never moves a cache key.

    ``events`` and ``reloads`` come from confirmed regions only
    (``events.confirmed``; a proposal is dropped here as well, so no caller
    can leak one), in clip seconds. A reload's duration and overhang are
    ``events.reload_figures``', so a template never re-derives one. Both
    keys are always present, empty without confirmed regions."""
    regions = confirmed(events)
    by_id = {e.id: e for e in regions}
    tiers = speed_tiers(shots)
    return {
        "beep": round(beep_in_clip, 6),
        "shots": [
            {
                "t": _clip(beep_in_clip, shot.time_from_beep),
                "split": round(shot.split, 6),
                "cls": shot.interval_class,
                "label": CLASS_LABELS.get(shot.interval_class) if shot.interval_class else None,
                "tier": tier,
                "moving": shot_is_moving(shot.time_from_beep, regions),
            }
            for shot, tier in zip(shots, tiers, strict=True)
        ],
        "stage_time": round(shots[-1].time_from_beep, 6) if shots else 0.0,
        "rounds": len(shots),
        "events": [
            {"kind": e.kind, "start": _clip(beep_in_clip, e.start), "end": _clip(beep_in_clip, e.end)}
            for e in regions
        ],
        "reloads": [
            {
                "start": _clip(beep_in_clip, by_id[fig.event_id].start),
                "end": _clip(beep_in_clip, by_id[fig.event_id].end),
                "duration": round(fig.duration, 6),
                "overhang": None if fig.overhang is None else round(fig.overhang, 6),
            }
            for fig in reload_figures(regions)
        ],
    }


def declared_positions(template: Path) -> tuple[HudPosition, ...]:
    """The positions a template supports, from its
    ``<meta name="splitsmith-positions">`` tag, in its order (the first is
    its default). Read as text: no browser."""
    match = _POSITIONS_META.search(template.read_text(encoding="utf-8"))
    if match is None:
        return ()
    names = [name.strip() for name in match.group(1).split(",")]
    return tuple(name for name in names if name in HUD_POSITIONS)


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
        "reload_chip": options.reload_chip,
        "stage_bar": options.stage_bar,
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
    "LEGACY_OVERLAY_SETTINGS",
    "OverlayStyleFields",
    "MIN_CLASS_SHOTS",
    "SLOW_ABOVE",
    "SpeedTier",
    "TIERED_CLASSES",
    "declared_positions",
    "hud_frame_plan",
    "hud_options_data",
    "hud_page_size",
    "hud_stage_data",
    "overlay_settings",
    "resolve_position",
    "speed_tiers",
]
