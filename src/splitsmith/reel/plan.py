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

from ..highlights import Moment, MomentKind, ReelPicks

#: ``beep`` is the timer's tone, drawn in its card's own audio; every other
#: kind is a shipped stem (``data/sfx``) the renderer lays over the stitched
#: reel at the item's start, its hit on the cut (``reel_render.SFX_STEMS``).
SfxKind = Literal["beep", "drone", "impact", "braam", "whoosh", "slowmo", "riser", "sting"]
CardKind = Literal["intro", "title", "slam", "callout", "end"]

MIN_CLIP_S = 0.3
SLOW = 0.5
COLD_OPEN_LEAD_S = 1.2
REPLAY_LEAD_S = 1.0
REPLAY_TAIL_S = 0.3
MONTAGE_LEAD_S = 0.35
MONTAGE_TAIL_S = 0.05


@dataclass(frozen=True)
class CutTiming:
    """How long each card holds, and whether beats replay from another angle."""

    intro: float
    title: float
    beep: float
    slam: float
    callout: float
    end: float
    replays: bool


#: The full trailer, and the Shorts / Reels cut (about half as long: a quick
#: intro, tighter cards, no replays; ``make_reel`` also picks fewer beats).
NORMAL_CUT = CutTiming(intro=4.6, title=2.2, beep=0.8, slam=0.7, callout=1.2, end=2.5, replays=True)
SHORT_CUT = CutTiming(intro=2.4, title=1.6, beep=0.6, slam=0.5, callout=0.9, end=1.8, replays=False)
INTRO_S = NORMAL_CUT.intro

MOMENT_LABELS: dict[MomentKind, str] = {
    "fastest_draw": "FASTEST DRAW",
    "fastest_split": "FASTEST SPLIT",
    "longest_streak": "HOT STREAK",
    "fastest_reload": "FASTEST RELOAD",
    "fastest_transition": "FASTEST TRANSITION",
    "best_stage": "BEST STAGE",
    "longest_burst": "BURST",
    "fastest_string": "FASTEST STRING",
    "most_reloads": "MAG CHANGES",
    "moving_reload": "RELOAD ON THE MOVE",
    "most_moving_shots": "SHOTS ON THE MOVE",
    "fastest_stage": "FASTEST STAGE",
    "top_hit_factor": "TOP HIT FACTOR",
    "thinker": "THE THINKER",
    "slowest_draw": "COFFEE BREAK",
    "slowest_reload": "MAG FUMBLE",
    "longest_stage": "MARATHON",
    "most_shots": "AMMO DUMP",
}
#: A count moment's unit; every other figure is seconds.
_COUNT_UNITS: dict[MomentKind, str] = {
    "longest_streak": "SHOTS",
    "longest_burst": "SHOTS",
    "most_moving_shots": "SHOTS",
    "most_reloads": "MAGS",
    "most_shots": "ROUNDS",
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
    own sound plays at real speed only; a slowed clip is silent under its
    ``sfx``."""

    name: str
    source: Path
    start: float
    seconds: float
    speed: float = 1.0
    freeze_seconds: float = 0.0
    sfx: tuple[SfxKind, ...] = ()

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
    sfx: tuple[SfxKind, ...] = ()


ReelItem = ReelClip | ReelCard


def figure_text(moment: Moment) -> tuple[str, str]:
    """The callout's figure and unit."""
    if moment.kind in _COUNT_UNITS:
        return f"{int(moment.figure)}", _COUNT_UNITS[moment.kind]
    if moment.kind == "best_stage":
        return f"{moment.figure:.1f}", "%"
    if moment.kind == "top_hit_factor":
        return f"{moment.figure:.2f}", "HF"
    if moment.kind == "moving_reload":
        return f"{moment.figure:.2f}", "SEC EXPOSED"
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
    short: bool = False,
) -> tuple[ReelItem, ...]:
    """The intro and the title, the cold open (the beep on black, the top
    moment slowed, its callout), one slam, clip and callout per beat with a
    slowed replay from the stage's other camera when it has one, the
    montage, the end card. ``short`` is the Shorts / Reels cut
    (``SHORT_CUT``)."""
    cut = SHORT_CUT if short else NORMAL_CUT
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
            cut.callout,
            MOMENT_LABELS[moment.kind],
            figure=figure,
            unit=unit,
            lines=(stage.stage_name, moment.label) if moment.label else (stage.stage_name,),
            backdrop=FrameRef(stage.primary.path, stage.primary.beep_offset + moment.focus),
            sfx=("whoosh",),
        )

    # The opening builds before anything happens: a drone under the match
    # name revealed on black, a riser into the braam on the shooter's name,
    # its tail fading so the timer's beep lands in quiet.
    items.append(ReelCard(name("intro"), "intro", cut.intro, title, lines=("HIGHLIGHTS",), sfx=("drone",)))
    items.append(ReelCard(name("title"), "title", cut.title, shooter, sfx=("riser", "braam")))

    cold = picks.cold_open
    cold_stage = footage.get(cold.stage_number) if cold is not None else None
    if cold is not None and cold_stage is not None:
        window = _window(cold_stage.primary, cold.focus - COLD_OPEN_LEAD_S, cold.focus)
        if window is not None:
            items.append(ReelCard(name("beep"), "slam", cut.beep, "", sfx=("beep",)))
            items.append(
                ReelClip(name("cold"), cold_stage.primary.path, *window, speed=SLOW, sfx=("slowmo",))
            )
            items.append(callout(cold, cold_stage))

    for moment in picks.beats:
        stage = footage.get(moment.stage_number)
        if stage is None:
            continue
        window = _window(stage.primary, moment.start, moment.end)
        if window is None:
            continue
        items.append(ReelCard(name("slam"), "slam", cut.slam, MOMENT_LABELS[moment.kind], sfx=("impact",)))
        items.append(ReelClip(name("beat"), stage.primary.path, *window))
        items.append(callout(moment, stage))
        if cut.replays and stage.secondary is not None:
            replay = _window(stage.secondary, moment.focus - REPLAY_LEAD_S, moment.focus + REPLAY_TAIL_S)
            if replay is not None:
                items.append(
                    ReelClip(name("replay"), stage.secondary.path, *replay, speed=SLOW, sfx=("slowmo",))
                )

    for stage_number, t in picks.montage:
        stage = footage.get(stage_number)
        if stage is None:
            continue
        window = _window(stage.primary, t - MONTAGE_LEAD_S, t + MONTAGE_TAIL_S)
        if window is not None:
            items.append(ReelClip(name("cut"), stage.primary.path, *window))

    # The sting builds for 6.2 s under the montage and hits on the end card.
    items.append(ReelCard(name("end"), "end", cut.end, title, lines=tuple(end_lines), sfx=("sting",)))
    return tuple(items)
