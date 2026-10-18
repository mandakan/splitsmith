"""The highlight reel's moments (spec 2026-10-18-highlight-reel-design).

Pure: reads ``TileStageData`` and audit docs handed in, never a file. A
stage is ready for the reel when its shots are audited and its breakdown
is done: opened in Breakdown (``events_seeded``) with no auto proposal
left.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, get_args

from .coach import statistic_split_shots
from .config import DEFAULT_MOMENTS, HighlightConfig, MomentKind
from .match_project import StageStatus
from .stage_summary_data import TileStageData

ReadinessState = Literal[
    "ready", "not_audited", "breakdown_not_opened", "proposals_left", "trim_missing", "no_video", "skipped"
]


@dataclass(frozen=True)
class StageReadiness:
    stage_number: int
    state: ReadinessState
    proposals: int = 0


def _auto_proposals(doc: dict[str, Any]) -> int:
    events = doc.get("events")
    if not isinstance(events, list):
        return 0
    return sum(1 for e in events if isinstance(e, dict) and e.get("source") == "auto")


def stage_readiness(
    stage_number: int,
    status: StageStatus,
    audit_doc: dict[str, Any] | None,
    *,
    has_footage: bool,
    has_video: bool = True,
) -> StageReadiness:
    """Where one stage stands for the reel. The order is the order a user
    fixes things in: audit, then breakdown. Only a stage with no video at
    all is out of play; one not trimmed yet is not audited yet, and a done
    stage whose trim is gone or was cut for another beep blocks as
    ``trim_missing`` rather than being left out without a word."""
    if status == StageStatus.skipped:
        return StageReadiness(stage_number, "skipped")
    if not has_video:
        return StageReadiness(stage_number, "no_video")
    if status != StageStatus.audited or audit_doc is None:
        return StageReadiness(stage_number, "not_audited")
    if not audit_doc.get("events_seeded"):
        return StageReadiness(stage_number, "breakdown_not_opened")
    proposals = _auto_proposals(audit_doc)
    if proposals:
        return StageReadiness(stage_number, "proposals_left", proposals=proposals)
    if not has_footage:
        return StageReadiness(stage_number, "trim_missing")
    return StageReadiness(stage_number, "ready")


#: States that leave a stage out of the reel without blocking it.
OUT_OF_PLAY: tuple[ReadinessState, ...] = ("skipped", "no_video")


def reel_unlocked(rows: Sequence[StageReadiness]) -> bool:
    """Every stage ready or out of play (skipped, no video), and at least
    one ready."""
    if not any(r.state == "ready" for r in rows):
        return False
    return all(r.state == "ready" or r.state in OUT_OF_PLAY for r in rows)


MOMENT_KINDS: tuple[MomentKind, ...] = get_args(MomentKind)
MomentGroup = Literal["default", "stat", "lolz"]
#: Where each moment sits on the menu: the defaults, the stats to switch on,
#: and the lolz (fun among friends, less so on a public link; never on by
#: default).
MOMENT_GROUPS: dict[MomentKind, MomentGroup] = {
    **dict.fromkeys(DEFAULT_MOMENTS, "default"),
    "longest_burst": "stat",
    "fastest_string": "stat",
    "most_reloads": "stat",
    "moving_reload": "stat",
    "most_moving_shots": "stat",
    "fastest_stage": "stat",
    "top_hit_factor": "stat",
    "fastest_transition": "stat",
    "thinker": "lolz",
    "slowest_draw": "lolz",
    "slowest_reload": "lolz",
    "longest_stage": "lolz",
    "most_shots": "lolz",
}
#: A stage-level moment's clip: its last stretch, ending on its last shot.
BEST_STAGE_LEAD_S = 2.5
#: The longest stretch of moving shots a moment shows, ending on the last.
MOVING_SHOTS_MAX_S = 4.0


@dataclass(frozen=True)
class Moment:
    """One candidate, in seconds from the beep. ``focus`` is the instant the
    freeze and the callout land on; ``figure`` the headline number;
    ``label`` a qualifier the callout prints under it ("IN 1 S")."""

    kind: MomentKind
    stage_number: int
    start: float
    end: float
    focus: float
    figure: float
    label: str = ""


def _ranked(moments: list[Moment], *, high_first: bool, limit: int) -> list[Moment]:
    sign = -1.0 if high_first else 1.0
    return sorted(moments, key=lambda m: (sign * m.figure, m.stage_number, m.focus))[:limit]


def _draws(stages: Sequence[TileStageData], cfg: HighlightConfig) -> list[Moment]:
    out = []
    for s in stages:
        draw = s.draw
        if s.standard_start and draw is not None and draw >= cfg.min_draw_s:
            out.append(Moment("fastest_draw", s.stage_number, -cfg.lead_s, draw + cfg.tail_s, draw, draw))
    return out


def _splits(stages: Sequence[TileStageData], cfg: HighlightConfig) -> list[Moment]:
    out = []
    for s in stages:
        for shot in statistic_split_shots(s.shots):
            if shot.split < cfg.min_split_s:
                continue
            t = shot.time_from_beep
            out.append(
                Moment(
                    "fastest_split",
                    s.stage_number,
                    t - shot.split - cfg.lead_s,
                    t + cfg.tail_s,
                    t,
                    shot.split,
                )
            )
    return out


def _streaks(stages: Sequence[TileStageData], cfg: HighlightConfig) -> list[Moment]:
    out = []
    for s in stages:
        shots = s.shots
        run_start = 0  # index of the first shot of the current run
        for i in range(1, len(shots) + 1):
            fast = (
                i < len(shots)
                and shots[i].interval_class in (None, "split")
                and cfg.min_split_s <= shots[i].split <= cfg.streak_split_max_s
            )
            if fast:
                continue
            count = i - run_start
            if count >= cfg.min_streak_shots:
                first, last = shots[run_start].time_from_beep, shots[i - 1].time_from_beep
                out.append(
                    Moment(
                        "longest_streak", s.stage_number, first - cfg.lead_s, last + cfg.lead_s, last, count
                    )
                )
            run_start = i
    return out


def _regions(
    stages: Sequence[TileStageData], cfg: HighlightConfig, *, kind: Literal["reload", "movement"]
) -> list[Moment]:
    moment_kind: MomentKind = "fastest_reload" if kind == "reload" else "fastest_transition"
    floor = cfg.min_reload_s if kind == "reload" else cfg.min_transition_s
    out = []
    for s in stages:
        first_shot = s.shots[0].time_from_beep if s.shots else None
        for e in s.events:
            if e.kind != kind or e.end - e.start < floor:
                continue
            if kind == "movement" and (first_shot is None or e.start < first_shot):
                continue  # a draw on the move is the draw's, not a transition
            out.append(
                Moment(
                    moment_kind,
                    s.stage_number,
                    e.start - cfg.lead_s,
                    e.end + cfg.lead_s,
                    e.end,
                    e.end - e.start,
                )
            )
    return out


def _stage_figure(stages: Sequence[TileStageData], kind: MomentKind) -> list[Moment]:
    """A stage-level figure, shown as the stage's last stretch."""
    out = []
    for s in stages:
        card = s.scorecard
        figure: float | None = {
            "best_stage": card.stage_pct if card is not None else None,
            "top_hit_factor": card.hit_factor if card is not None else None,
            "fastest_stage": s.stage_time_seconds,
            "longest_stage": s.stage_time_seconds,
            "most_shots": float(s.shot_count) if s.shot_count else None,
        }[kind]
        last = s.last_shot_time
        if figure is None or last is None:
            continue
        out.append(Moment(kind, s.stage_number, last - BEST_STAGE_LEAD_S, last + 0.5, last, figure))
    return out


def _bursts(stages: Sequence[TileStageData], cfg: HighlightConfig) -> list[Moment]:
    """Each stage's most shots inside any ``burst_window_s``, at least three."""
    out = []
    for s in stages:
        times = [shot.time_from_beep for shot in s.shots]
        best: tuple[int, int] | None = None  # (count, first index)
        j = 0
        for i in range(len(times)):
            while times[i] - times[j] > cfg.burst_window_s + 1e-9:
                j += 1
            count = i - j + 1
            if best is None or count > best[0]:
                best = (count, j)
        if best is None or best[0] < 3:
            continue
        count, first = best
        last = times[first + count - 1]
        out.append(
            Moment(
                "longest_burst",
                s.stage_number,
                times[first] - cfg.lead_s,
                last + cfg.lead_s,
                last,
                count,
                label=f"IN {cfg.burst_window_s:g} S",
            )
        )
    return out


#: Interval classes a string or a pause may not span.
_DEAD_TIME = ("movement", "reload", "activation")


def _strings(stages: Sequence[TileStageData], cfg: HighlightConfig) -> list[Moment]:
    """The fastest ``string_shots`` shots in a row with no reload, movement
    or activation between them, one per stage."""
    n = cfg.string_shots
    out = []
    for s in stages:
        shots = s.shots
        best: tuple[float, int] | None = None
        for i in range(len(shots) - n + 1):
            inner = shots[i + 1 : i + n]
            if any(shot.interval_class in _DEAD_TIME for shot in inner):
                continue
            seconds = shots[i + n - 1].time_from_beep - shots[i].time_from_beep
            if best is None or seconds < best[0] - 1e-9:
                best = (seconds, i)
        if best is None:
            continue
        seconds, i = best
        first, last = shots[i].time_from_beep, shots[i + n - 1].time_from_beep
        out.append(
            Moment(
                "fastest_string",
                s.stage_number,
                first - cfg.lead_s,
                last + cfg.lead_s,
                last,
                round(seconds, 6),
                label=f"{n} SHOTS",
            )
        )
    return out


def _mag_changes(stages: Sequence[TileStageData], cfg: HighlightConfig) -> list[Moment]:
    """Stages with two or more confirmed reloads, shown on the quickest."""
    out = []
    for s in stages:
        reloads = [e for e in s.events if e.kind == "reload"]
        if len(reloads) < 2:
            continue
        quickest = min(reloads, key=lambda e: (e.end - e.start, e.start))
        out.append(
            Moment(
                "most_reloads",
                s.stage_number,
                quickest.start - cfg.lead_s,
                quickest.end + cfg.lead_s,
                quickest.end,
                len(reloads),
            )
        )
    return out


def _moving_reloads(stages: Sequence[TileStageData], cfg: HighlightConfig) -> list[Moment]:
    """Reloads done on the move, by their exposed time (``reload_figures``)."""
    out = []
    for s in stages:
        by_id = {e.id: e for e in s.events}
        for figure in s.reloads:
            event = by_id.get(figure.event_id)
            if not figure.moving or event is None:
                continue
            out.append(
                Moment(
                    "moving_reload",
                    s.stage_number,
                    event.start - cfg.lead_s,
                    event.end + cfg.lead_s,
                    event.end,
                    round(figure.exposed, 6),
                )
            )
    return out


def _moving_shots(stages: Sequence[TileStageData], cfg: HighlightConfig) -> list[Moment]:
    """Each stage's count of shots fired on the move (two or more), shown up
    to its last moving shot."""
    out = []
    for s in stages:
        moving = [shot.time_from_beep for shot in s.shots if shot.moving]
        if len(moving) < 2:
            continue
        last = moving[-1]
        start = max(moving[0], last - MOVING_SHOTS_MAX_S) - cfg.lead_s
        out.append(Moment("most_moving_shots", s.stage_number, start, last + cfg.lead_s, last, len(moving)))
    return out


def _pauses(stages: Sequence[TileStageData], cfg: HighlightConfig) -> list[Moment]:
    """Gaps between shots with nothing to show for them: not a movement,
    reload or activation by class, and no confirmed movement or reload
    region over the gap."""
    out = []
    for s in stages:
        regions = [e for e in s.events if e.kind in ("movement", "reload")]
        for prev, shot in zip(s.shots, s.shots[1:], strict=False):
            gap = shot.split
            if shot.interval_class in _DEAD_TIME or gap < cfg.min_pause_s:
                continue
            a, b = prev.time_from_beep, shot.time_from_beep
            if any(e.start < b and e.end > a for e in regions):
                continue
            out.append(Moment("thinker", s.stage_number, a - 0.3, b + 0.5, b, gap))
    return out


def _as(kind: MomentKind, moments: list[Moment]) -> list[Moment]:
    return [replace(m, kind=kind) for m in moments]


#: The order the trailer's beats play in: the draw, the stage's mechanics,
#: then speed, then the stage figures and the result. A lolz moment sits
#: beside the serious one it answers (the slowest draw after the fastest).
BEAT_ORDER: tuple[MomentKind, ...] = (
    "fastest_draw",
    "slowest_draw",
    "fastest_transition",
    "fastest_reload",
    "moving_reload",
    "most_reloads",
    "slowest_reload",
    "most_moving_shots",
    "thinker",
    "longest_streak",
    "longest_burst",
    "fastest_string",
    "fastest_split",
    "most_shots",
    "fastest_stage",
    "longest_stage",
    "top_hit_factor",
    "best_stage",
)


@dataclass(frozen=True)
class ReelPicks:
    cold_open: Moment | None
    beats: tuple[Moment, ...]
    #: (stage number, shot time from the beep), in match order.
    montage: tuple[tuple[int, float], ...]


def pick_reel(
    found: dict[MomentKind, list[Moment]],
    stages: Sequence[TileStageData],
    *,
    kinds: Sequence[MomentKind] | None = None,
    max_beats: int | None = None,
    montage_cuts: int = 8,
    config: HighlightConfig | None = None,
) -> ReelPicks:
    """One beat per chosen kind (``kinds``, else the config's ``moments``),
    in ``BEAT_ORDER``, always the kind's best: a label says "fastest", so a
    slower one from an unused stage would be false, and two beats may share
    a stage. The cold open is the fastest split when it is chosen (else the
    first beat) and is not played again as a beat; the montage is the
    fastest splits of the match, played in match order."""
    cfg = config or HighlightConfig()
    chosen = set(kinds if kinds is not None else cfg.moments)
    limit = max_beats if max_beats is not None else cfg.max_beats
    tops = [found[kind][0] for kind in BEAT_ORDER if kind in chosen and found.get(kind)]
    splits = found.get("fastest_split", []) if "fastest_split" in chosen else []
    cold_open = splits[0] if splits else (tops[0] if tops else None)
    beats = [m for m in tops if m != cold_open][:limit]
    shots: list[tuple[float, int, float]] = []
    for s in stages:
        for shot in statistic_split_shots(s.shots):
            if shot.split >= cfg.min_split_s:
                shots.append((shot.split, s.stage_number, shot.time_from_beep))
    fastest = sorted(shots)[:montage_cuts]
    montage = tuple(sorted((stage, t) for _, stage, t in fastest))
    return ReelPicks(cold_open=cold_open, beats=tuple(beats), montage=montage)


def find_moments(stages: Sequence[TileStageData], config: HighlightConfig) -> dict[MomentKind, list[Moment]]:
    """Every kind's candidates, best first, ties broken by stage then time.
    Every figure is an existing rule's: the Draw is ``TileStageData.draw``,
    splits are ``coach.statistic_split_shots``', regions are confirmed."""
    n = config.max_candidates
    draws = _draws(stages, config)
    reloads = _regions(stages, config, kind="reload")
    found: dict[MomentKind, list[Moment]] = {
        "fastest_draw": _ranked(draws, high_first=False, limit=n),
        "fastest_split": _ranked(_splits(stages, config), high_first=False, limit=n),
        "longest_streak": _ranked(_streaks(stages, config), high_first=True, limit=n),
        "fastest_reload": _ranked(reloads, high_first=False, limit=n),
        "best_stage": _ranked(_stage_figure(stages, "best_stage"), high_first=True, limit=n),
        "longest_burst": _ranked(_bursts(stages, config), high_first=True, limit=n),
        "fastest_string": _ranked(_strings(stages, config), high_first=False, limit=n),
        "most_reloads": _ranked(_mag_changes(stages, config), high_first=True, limit=n),
        "moving_reload": _ranked(_moving_reloads(stages, config), high_first=False, limit=n),
        "most_moving_shots": _ranked(_moving_shots(stages, config), high_first=True, limit=n),
        "fastest_stage": _ranked(_stage_figure(stages, "fastest_stage"), high_first=False, limit=n),
        "top_hit_factor": _ranked(_stage_figure(stages, "top_hit_factor"), high_first=True, limit=n),
        "fastest_transition": _ranked(_regions(stages, config, kind="movement"), high_first=False, limit=n),
        "thinker": _ranked(_pauses(stages, config), high_first=True, limit=n),
        "slowest_draw": _ranked(_as("slowest_draw", draws), high_first=True, limit=n),
        "slowest_reload": _ranked(_as("slowest_reload", reloads), high_first=True, limit=n),
        "longest_stage": _ranked(_stage_figure(stages, "longest_stage"), high_first=True, limit=n),
        "most_shots": _ranked(_stage_figure(stages, "most_shots"), high_first=True, limit=n),
    }
    return found
