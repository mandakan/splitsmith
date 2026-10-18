"""The highlight reel's moments (spec 2026-10-18-highlight-reel-design).

Pure: reads ``TileStageData`` and audit docs handed in, never a file. A
stage is ready for the reel when its shots are audited and its breakdown
is done: opened in Breakdown (``events_seeded``) with no auto proposal
left.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .coach import statistic_split_shots
from .config import HighlightConfig
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


MomentKind = Literal[
    "fastest_draw", "fastest_split", "longest_streak", "fastest_reload", "fastest_transition", "best_stage"
]
#: The last stretch of a stage the best-stage moment shows, ending on its last shot.
BEST_STAGE_LEAD_S = 2.5


@dataclass(frozen=True)
class Moment:
    """One candidate, in seconds from the beep. ``focus`` is the instant the
    freeze and the callout land on; ``figure`` the headline number."""

    kind: MomentKind
    stage_number: int
    start: float
    end: float
    focus: float
    figure: float


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


def _best_stage(stages: Sequence[TileStageData]) -> list[Moment]:
    out = []
    for s in stages:
        pct = s.scorecard.stage_pct if s.scorecard is not None else None
        last = s.last_shot_time
        if pct is None or last is None:
            continue
        out.append(Moment("best_stage", s.stage_number, last - BEST_STAGE_LEAD_S, last + 0.5, last, pct))
    return out


#: The order the trailer's beats play in: build up through the stage's
#: mechanics, then speed, then the result.
BEAT_ORDER: tuple[MomentKind, ...] = (
    "fastest_draw",
    "fastest_transition",
    "fastest_reload",
    "longest_streak",
    "fastest_split",
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
    max_beats: int = 5,
    montage_cuts: int = 8,
    config: HighlightConfig | None = None,
) -> ReelPicks:
    """One beat per kind in ``BEAT_ORDER``, each on a stage no earlier beat
    used (at most one beat per stage; a kind whose every candidate sits on
    a used stage is left out), so the reel moves around the match.
    The cold open is the fastest split; the montage the fastest splits of
    the match, played in match order."""
    cfg = config or HighlightConfig()
    beats: list[Moment] = []
    used: set[int] = set()
    for kind in BEAT_ORDER:
        candidates = found.get(kind, [])
        if not candidates or len(beats) >= max_beats:
            continue
        chosen = next((m for m in candidates if m.stage_number not in used), None)
        if chosen is None:
            continue
        beats.append(chosen)
        used.add(chosen.stage_number)
    splits = found.get("fastest_split", [])
    cold_open = splits[0] if splits else (beats[0] if beats else None)
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
    return {
        "fastest_draw": _ranked(_draws(stages, config), high_first=False, limit=n),
        "fastest_split": _ranked(_splits(stages, config), high_first=False, limit=n),
        "longest_streak": _ranked(_streaks(stages, config), high_first=True, limit=n),
        "fastest_reload": _ranked(_regions(stages, config, kind="reload"), high_first=False, limit=n),
        "fastest_transition": _ranked(_regions(stages, config, kind="movement"), high_first=False, limit=n),
        "best_stage": _ranked(_best_stage(stages), high_first=True, limit=n),
    }
