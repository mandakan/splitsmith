"""The highlight reel as a spine (spec 2026-10-18-highlight-reel-design).

Pure: picks plus each stage's footage in, clips and cards out, every time
clip-local. A moment whose stage has no footage, or whose window clamps to
under ``MIN_CLIP_S``, is left out rather than padded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .highlights import Moment, MomentKind, ReelPicks

SfxKind = Literal["beep", "impact", "riser", "whoosh"]
CardKind = Literal["title", "slam", "callout", "end"]

MIN_CLIP_S = 0.3
SLOW = 0.5
COLD_OPEN_LEAD_S = 1.2
REPLAY_LEAD_S = 1.0
REPLAY_TAIL_S = 0.3
MONTAGE_LEAD_S = 0.35
MONTAGE_TAIL_S = 0.05
BEEP_CARD_S = 0.6
SLAM_S = 0.7
CALLOUT_S = 1.2
TITLE_S = 1.6
END_S = 2.5

MOMENT_LABELS: dict[MomentKind, str] = {
    "fastest_draw": "FASTEST DRAW",
    "fastest_split": "FASTEST SPLIT",
    "longest_streak": "HOT STREAK",
    "fastest_reload": "FASTEST RELOAD",
    "fastest_transition": "FASTEST TRANSITION",
    "best_stage": "BEST STAGE",
}


@dataclass(frozen=True)
class FootageClip:
    """A clip on disk and where its beep sits in it, in clip seconds."""

    path: Path
    beep_offset: float
    duration: float


@dataclass(frozen=True)
class StageFootage:
    stage_number: int
    stage_name: str
    primary: FootageClip
    secondary: FootageClip | None = None


@dataclass(frozen=True)
class FrameRef:
    source: Path
    at: float


@dataclass(frozen=True)
class ReelClip:
    """``seconds`` of ``source`` from clip-local ``start``, played at
    ``speed`` and held ``freeze_seconds`` on its last frame. The source's
    own sound plays only at real speed with no ``sfx``."""

    name: str
    source: Path
    start: float
    seconds: float
    speed: float = 1.0
    freeze_seconds: float = 0.0
    sfx: SfxKind | None = None

    @property
    def out_seconds(self) -> float:
        return self.seconds / self.speed + self.freeze_seconds


@dataclass(frozen=True)
class ReelCard:
    name: str
    kind: CardKind
    seconds: float
    text: str
    figure: str | None = None
    unit: str | None = None
    lines: tuple[str, ...] = ()
    backdrop: FrameRef | None = None
    sfx: SfxKind | None = None


ReelItem = ReelClip | ReelCard


def figure_text(moment: Moment) -> tuple[str, str]:
    """The callout's figure and unit."""
    if moment.kind == "longest_streak":
        return f"{int(moment.figure)}", "SHOTS"
    if moment.kind == "best_stage":
        return f"{moment.figure:.1f}", "%"
    return f"{moment.figure:.2f}", "SEC"


def _window(clip: FootageClip, start: float, end: float) -> tuple[float, float] | None:
    """Beep-relative ``start``..``end`` as clip-local (start, seconds), clamped."""
    lo = max(0.0, clip.beep_offset + start)
    hi = min(clip.duration, clip.beep_offset + end)
    if hi - lo < MIN_CLIP_S:
        return None
    return lo, hi - lo


def plan_reel(
    picks: ReelPicks,
    footage: Mapping[int, StageFootage],
    *,
    title: str,
    shooter: str,
    end_lines: Sequence[str],
) -> tuple[ReelItem, ...]:
    """Cold open (the beep on black, the top moment slowed, its callout),
    the title, one slam, clip and callout per beat with a slowed replay from
    the stage's other camera when it has one, the montage, the end card."""
    items: list[ReelItem] = []
    count = 0

    def name(prefix: str) -> str:
        nonlocal count
        count += 1
        return f"{count:03d}_{prefix}"

    def callout(moment: Moment, stage: StageFootage) -> ReelCard:
        figure, unit = figure_text(moment)
        return ReelCard(
            name("callout"),
            "callout",
            CALLOUT_S,
            MOMENT_LABELS[moment.kind],
            figure=figure,
            unit=unit,
            lines=(stage.stage_name,),
            backdrop=FrameRef(stage.primary.path, stage.primary.beep_offset + moment.focus),
            sfx="whoosh",
        )

    cold = picks.cold_open
    cold_stage = footage.get(cold.stage_number) if cold is not None else None
    if cold is not None and cold_stage is not None:
        window = _window(cold_stage.primary, cold.focus - COLD_OPEN_LEAD_S, cold.focus)
        if window is not None:
            items.append(ReelCard(name("beep"), "slam", BEEP_CARD_S, "", sfx="beep"))
            items.append(ReelClip(name("cold"), cold_stage.primary.path, *window, speed=SLOW, sfx="riser"))
            items.append(callout(cold, cold_stage))
    items.append(ReelCard(name("title"), "title", TITLE_S, title, lines=(shooter,), sfx="impact"))

    for moment in picks.beats:
        stage = footage.get(moment.stage_number)
        if stage is None:
            continue
        window = _window(stage.primary, moment.start, moment.end)
        if window is None:
            continue
        items.append(ReelCard(name("slam"), "slam", SLAM_S, MOMENT_LABELS[moment.kind], sfx="impact"))
        items.append(ReelClip(name("beat"), stage.primary.path, *window))
        items.append(callout(moment, stage))
        if stage.secondary is not None:
            replay = _window(stage.secondary, moment.focus - REPLAY_LEAD_S, moment.focus + REPLAY_TAIL_S)
            if replay is not None:
                items.append(ReelClip(name("replay"), stage.secondary.path, *replay, speed=SLOW, sfx="riser"))

    for stage_number, t in picks.montage:
        stage = footage.get(stage_number)
        if stage is None:
            continue
        window = _window(stage.primary, t - MONTAGE_LEAD_S, t + MONTAGE_TAIL_S)
        if window is not None:
            items.append(ReelClip(name("cut"), stage.primary.path, *window))

    items.append(ReelCard(name("end"), "end", END_S, title, lines=tuple(end_lines), sfx="impact"))
    return tuple(items)
