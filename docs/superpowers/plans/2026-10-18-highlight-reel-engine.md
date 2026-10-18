# Highlight Reel Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `splitsmith match highlights <match> --shooter <slug>` renders a trailer-style highlight reel MP4 of one shooter's match from their audited, broken-down stages.

**Architecture:** Three pure layers and one driver. `highlights.py` finds and picks moments from `TileStageData` and says which stages are ready. `reel_plan.py` turns picks plus each stage's footage into a spine of `ReelClip` / `ReelCard` items in clip-local seconds. `reel_render.py` builds one ffmpeg argv per item (crop to the canvas, speed, freeze, synthesized SFX), encodes through the existing `SegmentCache`, and stitches with the existing concat command. Cards draw through a new shipped Look slot, `highlight`, loaded like a sting (`reel_cards.py`).

**Tech Stack:** Python 3.11, ffmpeg (lavfi for SFX), the Chromium rasterizer the cards already use, pytest.

**Spec:** `docs/superpowers/specs/2026-10-18-highlight-reel-design.md`

This plan is part 1 of 2. Part 2 (the `highlight_reel` job, the Overview row, Export's reel mode, the share link, What's new) is written after the frames from Task 9 have been looked at, since the cut will change once seen.

## Global Constraints

- Python 3.11+, type hints everywhere, `pathlib.Path`, Black at 110, Ruff, `uv run` for everything.
- No new dependencies. SFX are synthesized by ffmpeg's lavfi sources in the argv; no audio files ship.
- Every figure comes from an existing rule: `coach.statistic_split_shots`, `TileStageData.draw` (`events.draw_seconds`), `events.confirmed_from_doc` (through `load_stage_events`), `events.reload_figures`. Nothing re-derived.
- Draws are compared only between standard starts (`TileStageData.standard_start`).
- Conservative: a split under `HighlightConfig.min_split_s` (0.10 s) never qualifies; a moment whose clamped window is under 0.3 s is dropped; no moment is invented when the data is missing.
- Slow motion is `setpts` only, never `minterpolate`, and never slower than 0.5x.
- Every segment is encoded with `mp4_render._encode_args` and audio normalized to 48 kHz stereo, so the stitch stream-copies video.
- Anything that changes a segment must be in its argv or an input file (the segment cache keys on those). Card clips reach the key through `virtual_inputs` keyed by `template_digest`.
- Tests run with `SPLITSMITH_RENDER_CACHE=0` (conftest already sets it). Integration tests are `@pytest.mark.integration` and build media with `tests/synthetic_media.py`.
- Copy rules (clean-prose): ASCII only, no dash punctuation in comments, docstrings or user text.
- Run pytest through the project ffmpeg: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest ...`.

## Review Focus

1. A stage with zero or one shot, or with no classified splits: every finder returns nothing for it and never raises. (Task 2 test `test_finders_tolerate_empty_and_single_shot_stages`.)
2. A moment window that runs off the clip (beep inside the pre-buffer, last shot near the clip's end): clamped to `[0, duration]`, dropped when under 0.3 s. (Task 4 test `test_window_clamps_to_clip_and_drops_slivers`.)
3. Equal figures on two stages: the pick is deterministic (stage number, then time). (Task 2 test `test_ties_break_by_stage_then_time`.)
4. Every stage a non-standard start: no draw moment at all, never a non-standard draw. (Task 2 test `test_non_standard_starts_never_give_a_draw`.)
5. A secondary camera at another frame rate or size than the primary: the clip argv conforms it (`fps=`, crop, scale) so the stitch still stream-copies. (Task 5 integration test `test_clips_at_different_rates_stitch`.)

---

### Task 1: Readiness

**Files:**
- Create: `src/splitsmith/highlights.py`
- Test: `tests/test_highlights.py`

**Interfaces:**
- Produces:
  - `StageReadiness` (frozen dataclass): `stage_number: int`, `state: ReadinessState`, `proposals: int = 0`
  - `ReadinessState = Literal["ready", "not_audited", "breakdown_not_opened", "proposals_left", "no_footage", "skipped"]`
  - `stage_readiness(stage_number: int, status: StageStatus, audit_doc: dict | None, *, has_footage: bool) -> StageReadiness`
  - `reel_unlocked(rows: Sequence[StageReadiness]) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
from splitsmith.highlights import StageReadiness, reel_unlocked, stage_readiness
from splitsmith.match_project import StageStatus


def _doc(*, seeded: bool = True, sources: tuple[str, ...] = ()) -> dict:
    doc: dict = {
        "events": [
            {"id": f"evt-{i}", "kind": "movement", "start": 1.0 + i, "end": 1.5 + i, "source": s}
            for i, s in enumerate(sources)
        ]
    }
    if seeded:
        doc["events_seeded"] = 4
    return doc


def test_audited_seeded_stage_without_proposals_is_ready():
    row = stage_readiness(1, StageStatus.audited, _doc(sources=("manual",)), has_footage=True)
    assert row == StageReadiness(1, "ready")


def test_auto_proposals_block_and_are_counted():
    row = stage_readiness(2, StageStatus.audited, _doc(sources=("auto", "manual", "auto")), has_footage=True)
    assert row == StageReadiness(2, "proposals_left", proposals=2)


def test_never_opened_in_breakdown_blocks():
    row = stage_readiness(3, StageStatus.audited, _doc(seeded=False), has_footage=True)
    assert row.state == "breakdown_not_opened"


def test_not_audited_blocks_before_breakdown_is_looked_at():
    row = stage_readiness(4, StageStatus.ready, None, has_footage=True)
    assert row.state == "not_audited"


def test_skipped_and_footageless_stages_do_not_block():
    rows = [
        stage_readiness(1, StageStatus.audited, _doc(), has_footage=True),
        stage_readiness(2, StageStatus.skipped, None, has_footage=False),
        stage_readiness(3, StageStatus.audited, _doc(), has_footage=False),
    ]
    assert [r.state for r in rows] == ["ready", "skipped", "no_footage"]
    assert reel_unlocked(rows)


def test_nothing_ready_is_not_unlocked():
    assert not reel_unlocked([stage_readiness(1, StageStatus.skipped, None, has_footage=False)])
    assert not reel_unlocked([])


def test_events_that_are_not_a_list_count_as_none():
    doc = {"events_seeded": 4, "events": "garbage"}
    assert stage_readiness(1, StageStatus.audited, doc, has_footage=True).state == "ready"
```

- [ ] **Step 2: Run to verify they fail**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_highlights.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.highlights'`

- [ ] **Step 3: Implement**

```python
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

from .match_project import StageStatus

ReadinessState = Literal["ready", "not_audited", "breakdown_not_opened", "proposals_left", "no_footage", "skipped"]


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
    stage_number: int, status: StageStatus, audit_doc: dict[str, Any] | None, *, has_footage: bool
) -> StageReadiness:
    """Where one stage stands for the reel. The order is the order a user
    fixes things in: audit, then breakdown."""
    if status == StageStatus.skipped:
        return StageReadiness(stage_number, "skipped")
    if not has_footage:
        return StageReadiness(stage_number, "no_footage")
    if status != StageStatus.audited or audit_doc is None:
        return StageReadiness(stage_number, "not_audited")
    if not audit_doc.get("events_seeded"):
        return StageReadiness(stage_number, "breakdown_not_opened")
    proposals = _auto_proposals(audit_doc)
    if proposals:
        return StageReadiness(stage_number, "proposals_left", proposals=proposals)
    return StageReadiness(stage_number, "ready")


def reel_unlocked(rows: Sequence[StageReadiness]) -> bool:
    """Every stage ready or out of play (skipped, no footage), and at
    least one ready."""
    if not any(r.state == "ready" for r in rows):
        return False
    return all(r.state in ("ready", "skipped", "no_footage") for r in rows)
```

- [ ] **Step 4: Run to verify they pass**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_highlights.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/highlights.py tests/test_highlights.py
/usr/bin/git commit -m "feat(highlights): which stages are ready for a highlight reel"
```

---

### Task 2: Moment finders

**Files:**
- Modify: `src/splitsmith/highlights.py`
- Modify: `src/splitsmith/config.py` (add `HighlightConfig`)
- Test: `tests/test_highlights.py`

**Interfaces:**
- Consumes: `TileStageData`, `TileShot` (`stage_summary_data`), `coach.statistic_split_shots`, `StageEvent`.
- Produces:
  - `MomentKind = Literal["fastest_draw", "fastest_split", "longest_streak", "fastest_reload", "fastest_transition", "best_stage"]`
  - `Moment` (frozen dataclass): `kind: MomentKind`, `stage_number: int`, `start: float`, `end: float`, `focus: float`, `figure: float` (all times seconds from the beep)
  - `find_moments(stages: Sequence[TileStageData], config: HighlightConfig) -> dict[MomentKind, list[Moment]]`, each list best first
  - `config.HighlightConfig` (Pydantic): `min_split_s=0.10`, `min_draw_s=0.40`, `min_reload_s=0.50`, `min_transition_s=0.30`, `streak_split_max_s=0.30`, `min_streak_shots=4`, `lead_s=0.4`, `tail_s=0.6`, `max_candidates=20`

- [ ] **Step 1: Add `HighlightConfig` to `config.py`** (next to `CoachAutoClassifyConfig`)

```python
class HighlightConfig(BaseModel):
    """Tunables of the highlight reel's moment finders (spec
    2026-10-18-highlight-reel-design). Floors are sanity, not taste: a
    split under ``min_split_s`` is an echo detected twice, not a split."""

    min_split_s: float = 0.10
    min_draw_s: float = 0.40
    min_reload_s: float = 0.50
    min_transition_s: float = 0.30
    streak_split_max_s: float = 0.30
    min_streak_shots: int = 4
    lead_s: float = 0.4
    tail_s: float = 0.6
    max_candidates: int = 20
```

- [ ] **Step 2: Write the failing tests** (append to `tests/test_highlights.py`)

```python
from splitsmith.config import HighlightConfig, StageEvent
from splitsmith.highlights import Moment, find_moments
from splitsmith.match_project import StageScorecard
from splitsmith.stage_summary_data import TileShot, TileStageData

CFG = HighlightConfig()


def _stage(n: int, times: list[float], *, events=(), pct=None, standard=True, classes=None) -> TileStageData:
    shots = []
    prev = 0.0
    for i, t in enumerate(times):
        cls = classes[i] if classes else ("draw" if i == 0 else "split")
        shots.append(TileShot(time_from_beep=t, split=round(t - prev, 3), interval_class=cls))
        prev = t
    return TileStageData(
        label="A",
        stage_number=n,
        shots=tuple(shots),
        events=tuple(events),
        scorecard=StageScorecard(stage_pct=pct) if pct is not None else None,
        standard_start=standard,
    )


def _ev(i: int, kind: str, start: float, end: float) -> StageEvent:
    return StageEvent(id=f"evt-{i}", kind=kind, start=start, end=end, source="manual")


def test_fastest_draw_is_the_smallest_standard_first_shot():
    found = find_moments([_stage(1, [1.20, 1.50]), _stage(2, [0.95, 1.30])], CFG)
    best = found["fastest_draw"][0]
    assert (best.stage_number, best.figure, best.focus) == (2, 0.95, 0.95)
    assert best.start == -0.4 and best.end == pytest.approx(0.95 + 0.6)


def test_non_standard_starts_never_give_a_draw():
    found = find_moments([_stage(1, [0.5, 0.8], standard=False)], CFG)
    assert found["fastest_draw"] == []


def test_fastest_split_skips_echo_doubles_and_non_split_classes():
    stage = _stage(
        1,
        [1.0, 1.05, 1.30, 3.00, 3.22],
        classes=["draw", "split", "split", "movement", "split"],
    )
    best = find_moments([stage], CFG)["fastest_split"][0]
    # 0.05 is under the floor; 1.70 is a movement; 0.22 wins over 0.25.
    assert best.figure == pytest.approx(0.22)
    assert best.focus == pytest.approx(3.22)
    assert best.start == pytest.approx(3.00 - 0.4)


def test_longest_streak_counts_shots_in_a_run_of_fast_splits():
    stage = _stage(1, [1.0, 1.25, 1.50, 1.74, 1.98, 3.5, 3.7])
    best = find_moments([stage], CFG)["longest_streak"][0]
    assert best.figure == 5  # 1.0 .. 1.98: four splits under 0.30, five shots
    assert best.start == pytest.approx(1.0 - 0.4) and best.end == pytest.approx(1.98 + 0.4)
    assert best.focus == pytest.approx(1.98)


def test_streak_shorter_than_the_minimum_is_not_a_moment():
    assert find_moments([_stage(1, [1.0, 1.2, 1.4])], CFG)["longest_streak"] == []


def test_reload_and_transition_come_from_confirmed_regions():
    events = [
        _ev(1, "draw", 0.0, 1.0),
        _ev(2, "movement", 0.2, 0.9),  # a draw on the move, before shot 1: not a transition
        _ev(3, "movement", 2.0, 3.1),
        _ev(4, "reload", 4.0, 5.3),
        _ev(5, "reload", 6.0, 6.2),  # under the floor
    ]
    found = find_moments([_stage(1, [1.0, 1.3, 3.3, 5.5, 7.0], events=events)], CFG)
    assert [m.figure for m in found["fastest_transition"]] == [pytest.approx(1.1)]
    assert [m.figure for m in found["fastest_reload"]] == [pytest.approx(1.3)]
    assert found["fastest_reload"][0].focus == pytest.approx(5.3)


def test_best_stage_is_the_highest_stage_pct_and_ends_on_the_last_shot():
    found = find_moments([_stage(1, [1.0, 2.0], pct=81.0), _stage(2, [1.0, 4.0], pct=97.5)], CFG)
    best = found["best_stage"][0]
    assert (best.stage_number, best.figure, best.focus) == (2, 97.5, 4.0)
    assert best.start == pytest.approx(4.0 - 2.5)


def test_ties_break_by_stage_then_time():
    found = find_moments([_stage(2, [1.0, 1.2]), _stage(1, [1.0, 1.2])], CFG)
    assert [m.stage_number for m in found["fastest_draw"]] == [1, 2]
    assert [m.stage_number for m in found["fastest_split"]] == [1, 2]


def test_finders_tolerate_empty_and_single_shot_stages():
    found = find_moments([_stage(1, []), _stage(2, [1.1])], CFG)
    assert found["fastest_split"] == [] and found["longest_streak"] == []
    assert [m.stage_number for m in found["fastest_draw"]] == [2]


def test_every_kind_is_present_even_when_empty():
    assert set(find_moments([], CFG)) == {
        "fastest_draw", "fastest_split", "longest_streak",
        "fastest_reload", "fastest_transition", "best_stage",
    }
```

(Add `import pytest` at the top of the test file.)

- [ ] **Step 3: Run to verify they fail**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_highlights.py -v`
Expected: FAIL, `ImportError: cannot import name 'HighlightConfig'` (or `find_moments`)

- [ ] **Step 4: Implement** (append to `highlights.py`; extend imports)

```python
from .coach import statistic_split_shots
from .config import HighlightConfig, StageEvent
from .stage_summary_data import TileStageData

MomentKind = Literal[
    "fastest_draw", "fastest_split", "longest_streak", "fastest_reload", "fastest_transition", "best_stage"
]
MOMENT_KINDS: tuple[MomentKind, ...] = (
    "fastest_draw", "fastest_split", "longest_streak", "fastest_reload", "fastest_transition", "best_stage"
)
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
                Moment("fastest_split", s.stage_number, t - shot.split - cfg.lead_s, t + cfg.tail_s, t, shot.split)
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
                    Moment("longest_streak", s.stage_number, first - cfg.lead_s, last + cfg.lead_s, last, count)
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
                Moment(moment_kind, s.stage_number, e.start - cfg.lead_s, e.end + cfg.lead_s, e.end, e.end - e.start)
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


def find_moments(stages: Sequence[TileStageData], config: HighlightConfig) -> dict[MomentKind, list[Moment]]:
    """Every kind's candidates, best first, ties broken by stage then time."""
    n = config.max_candidates
    streaks = _streaks(stages, config)
    return {
        "fastest_draw": _ranked(_draws(stages, config), high_first=False, limit=n),
        "fastest_split": _ranked(_splits(stages, config), high_first=False, limit=n),
        "longest_streak": _ranked(streaks, high_first=True, limit=n),
        "fastest_reload": _ranked(_regions(stages, config, kind="reload"), high_first=False, limit=n),
        "fastest_transition": _ranked(_regions(stages, config, kind="movement"), high_first=False, limit=n),
        "best_stage": _ranked(_best_stage(stages), high_first=True, limit=n),
    }
```

Note on `_splits`: `statistic_split_shots` already applies the class rule (only `"split"` when any shot is classified), so a movement gap never reaches the floor check. The draw is `TileStageData.draw`, the one Draw rule.

- [ ] **Step 5: Run to verify they pass**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_highlights.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add src/splitsmith/highlights.py src/splitsmith/config.py tests/test_highlights.py
/usr/bin/git commit -m "feat(highlights): find a match's fastest draw, split, streak, reload, transition and best stage"
```

---

### Task 3: Picking the beats and the montage

**Files:**
- Modify: `src/splitsmith/highlights.py`
- Test: `tests/test_highlights.py`

**Interfaces:**
- Consumes: `Moment`, `find_moments` (Task 2).
- Produces:
  - `ReelPicks` (frozen dataclass): `cold_open: Moment | None`, `beats: tuple[Moment, ...]`, `montage: tuple[tuple[int, float], ...]` (stage number, shot time from beep)
  - `BEAT_ORDER: tuple[MomentKind, ...] = ("fastest_draw", "fastest_transition", "fastest_reload", "longest_streak", "fastest_split", "best_stage")`
  - `pick_reel(found: dict[MomentKind, list[Moment]], stages: Sequence[TileStageData], *, max_beats: int = 5, montage_cuts: int = 8) -> ReelPicks`

- [ ] **Step 1: Write the failing tests**

```python
from splitsmith.highlights import BEAT_ORDER, ReelPicks, pick_reel


def _m(kind, stage, figure=1.0, focus=1.0) -> Moment:
    return Moment(kind, stage, focus - 1.0, focus + 0.5, focus, figure)


def _found(**lists) -> dict:
    return {k: list(lists.get(k, [])) for k in BEAT_ORDER}


def test_beats_follow_the_order_and_prefer_unused_stages():
    found = _found(
        fastest_draw=[_m("fastest_draw", 1)],
        fastest_transition=[_m("fastest_transition", 1), _m("fastest_transition", 2)],
        fastest_split=[_m("fastest_split", 3)],
    )
    picks = pick_reel(found, [])
    assert [(m.kind, m.stage_number) for m in picks.beats] == [
        ("fastest_draw", 1),
        ("fastest_transition", 2),
        ("fastest_split", 3),
    ]


def test_a_stage_is_reused_only_when_no_other_candidate_exists():
    found = _found(fastest_draw=[_m("fastest_draw", 1)], fastest_split=[_m("fastest_split", 1)])
    assert [m.stage_number for m in pick_reel(found, []).beats] == [1, 1]


def test_cold_open_is_the_fastest_split_else_the_first_beat():
    split = _m("fastest_split", 3)
    assert pick_reel(_found(fastest_split=[split]), []).cold_open == split
    draw = _m("fastest_draw", 1)
    assert pick_reel(_found(fastest_draw=[draw]), []).cold_open == draw
    assert pick_reel(_found(), []).cold_open is None


def test_max_beats_caps_the_list():
    found = _found(**{k: [_m(k, i + 1)] for i, k in enumerate(BEAT_ORDER)})
    assert len(pick_reel(found, [], max_beats=3).beats) == 3


def test_montage_is_the_fastest_splits_in_match_order():
    stages = [_stage(2, [1.0, 1.18, 1.50]), _stage(1, [1.0, 1.21, 1.60, 1.79])]
    picks = pick_reel(_found(), stages, montage_cuts=3)
    # Fastest three splits: 0.18 (s2 @1.18), 0.19 (s1 @1.79), 0.21 (s1 @1.21); shown in match order.
    assert picks.montage == ((1, 1.21), (1, 1.79), (2, 1.18))
```

- [ ] **Step 2: Run to verify they fail**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_highlights.py -v`
Expected: FAIL, `ImportError: cannot import name 'BEAT_ORDER'`

- [ ] **Step 3: Implement**

```python
BEAT_ORDER: tuple[MomentKind, ...] = (
    "fastest_draw", "fastest_transition", "fastest_reload", "longest_streak", "fastest_split", "best_stage"
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
    used when any candidate allows it, so the reel moves around the match."""
    cfg = config or HighlightConfig()
    beats: list[Moment] = []
    used: set[int] = set()
    for kind in BEAT_ORDER:
        candidates = found.get(kind, [])
        if not candidates or len(beats) >= max_beats:
            continue
        fresh = next((m for m in candidates if m.stage_number not in used), None)
        chosen = fresh or candidates[0]
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_highlights.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/highlights.py tests/test_highlights.py
/usr/bin/git commit -m "feat(highlights): pick a reel's beats, cold open and montage"
```

---

### Task 4: The reel's spine

**Files:**
- Create: `src/splitsmith/reel_plan.py`
- Test: `tests/test_reel_plan.py`

**Interfaces:**
- Consumes: `Moment`, `ReelPicks` (Tasks 2, 3).
- Produces:
  - `FootageClip` (frozen): `path: Path`, `beep_offset: float` (clip-local beep seconds), `duration: float`
  - `StageFootage` (frozen): `stage_number: int`, `stage_name: str`, `primary: FootageClip`, `secondary: FootageClip | None = None`
  - `SfxKind = Literal["beep", "impact", "riser", "whoosh"]`
  - `FrameRef` (frozen): `source: Path`, `at: float` (clip-local)
  - `ReelClip` (frozen): `name: str`, `source: Path`, `start: float` (clip-local), `seconds: float` (source seconds), `speed: float = 1.0`, `freeze_seconds: float = 0.0`, `sfx: SfxKind | None = None`; property `out_seconds = seconds / speed + freeze_seconds`; source audio is used only when `speed == 1.0 and sfx is None`
  - `CardKind = Literal["title", "slam", "callout", "end"]`
  - `ReelCard` (frozen): `name: str`, `kind: CardKind`, `seconds: float`, `text: str`, `figure: str | None = None`, `unit: str | None = None`, `lines: tuple[str, ...] = ()`, `backdrop: FrameRef | None = None`, `sfx: SfxKind | None = None`
  - `ReelItem = ReelClip | ReelCard`
  - `MOMENT_LABELS: dict[MomentKind, str]`
  - `figure_text(moment: Moment) -> tuple[str, str]` (figure, unit)
  - `plan_reel(picks: ReelPicks, footage: Mapping[int, StageFootage], *, title: str, shooter: str, end_lines: Sequence[str]) -> tuple[ReelItem, ...]`

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

import pytest

from splitsmith.highlights import Moment, ReelPicks
from splitsmith.reel_plan import (
    FootageClip,
    ReelCard,
    ReelClip,
    StageFootage,
    figure_text,
    plan_reel,
)

P1 = Path("/m/s1.mp4")
S1 = Path("/m/s1_cam_b.mp4")


def _footage(beep=5.0, duration=30.0, secondary=False) -> dict[int, StageFootage]:
    return {
        1: StageFootage(
            1,
            "Stage 1",
            FootageClip(P1, beep, duration),
            FootageClip(S1, 4.0, duration) if secondary else None,
        )
    }


def _split(stage=1, focus=3.0, start=2.4, end=3.6) -> Moment:
    return Moment("fastest_split", stage, start, end, focus, 0.2)


def _plan(picks, **kw):
    return plan_reel(picks, kw.pop("footage", _footage()), title="Bromma 2026", shooter="Anna", end_lines=("Draw 0.94",))


def test_spine_shape_for_one_beat():
    m = _split()
    items = _plan(ReelPicks(cold_open=m, beats=(m,), montage=((1, 3.0),)))
    kinds = [(type(i).__name__, getattr(i, "kind", None)) for i in items]
    assert kinds == [
        ("ReelCard", "slam"),  # black, the beep
        ("ReelClip", None),  # cold open slow motion
        ("ReelCard", "callout"),
        ("ReelCard", "title"),
        ("ReelCard", "slam"),
        ("ReelClip", None),
        ("ReelCard", "callout"),
        ("ReelClip", None),  # montage cut
        ("ReelCard", "end"),
    ]


def test_clip_times_are_clip_local():
    m = _split()
    items = _plan(ReelPicks(None, (m,), ()))
    clip = next(i for i in items if isinstance(i, ReelClip))
    assert clip.source == P1
    assert clip.start == pytest.approx(5.0 + 2.4)
    assert clip.seconds == pytest.approx(1.2)
    callout = next(i for i in items if isinstance(i, ReelCard) and i.kind == "callout")
    assert callout.backdrop is not None and callout.backdrop.at == pytest.approx(5.0 + 3.0)
    assert (callout.figure, callout.unit) == ("0.20", "SEC")


def test_window_clamps_to_clip_and_drops_slivers():
    early = Moment("fastest_draw", 1, -0.4, 1.5, 0.9, 0.9)
    items = _plan(ReelPicks(None, (early,), ()), footage=_footage(beep=0.2))
    clip = next(i for i in items if isinstance(i, ReelClip))
    assert clip.start == 0.0 and clip.seconds == pytest.approx(1.7)
    sliver = Moment("fastest_split", 1, 29.0, 31.0, 29.6, 0.2)  # clip ends at 30 - 5 = 25 s from the beep
    assert not any(isinstance(i, ReelClip) for i in _plan(ReelPicks(None, (sliver,), ())))


def test_a_stage_without_footage_drops_its_beat_and_cuts():
    m = _split(stage=7)
    items = _plan(ReelPicks(m, (m,), ((7, 3.0),)))
    assert not any(isinstance(i, ReelClip) for i in items)


def test_secondary_angle_replays_in_slow_motion():
    m = _split()
    items = _plan(ReelPicks(None, (m,), ()), footage=_footage(secondary=True))
    replay = [i for i in items if isinstance(i, ReelClip) and i.source == S1]
    assert len(replay) == 1
    assert replay[0].speed == 0.5 and replay[0].sfx == "riser"
    assert replay[0].start == pytest.approx(4.0 + 3.0 - 1.0)


def test_figure_text():
    assert figure_text(Moment("longest_streak", 1, 0, 1, 1, 7)) == ("7", "SHOTS")
    assert figure_text(Moment("best_stage", 1, 0, 1, 1, 97.46)) == ("97.5", "%")
    assert figure_text(Moment("fastest_draw", 1, 0, 1, 1, 0.94)) == ("0.94", "SEC")
```

- [ ] **Step 2: Run to verify they fail**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_reel_plan.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.reel_plan'`

- [ ] **Step 3: Implement**

```python
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
    items: list[ReelItem] = []
    n = 0

    def name(prefix: str) -> str:
        nonlocal n
        n += 1
        return f"{n:03d}_{prefix}"

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
    cold_stage = footage.get(cold.stage_number) if cold else None
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_reel_plan.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/reel_plan.py tests/test_reel_plan.py
/usr/bin/git commit -m "feat(highlights): lay a reel's picks out as clips and cards"
```

---

### Task 5: Clip, card and SFX commands

**Files:**
- Create: `src/splitsmith/reel_render.py`
- Test: `tests/test_reel_render.py`

**Interfaces:**
- Consumes: `ReelClip`, `ReelCard`, `SfxKind` (Task 4); `mp4_render._encode_args`, `mp4_render._rate_string`, `composition.SequenceFormat`; `look_motion.motion_overlay_filters`.
- Produces:
  - `reel_format(primaries: Sequence[VideoMetadata], *, upright: bool) -> SequenceFormat`
  - `sfx_source(kind: SfxKind | None, seconds: float) -> str` (a lavfi graph, stereo 48 kHz)
  - `clip_command(clip: ReelClip, *, sequence: SequenceFormat, output_path: Path, ffmpeg_binary: str = "ffmpeg", source_audio: bool = True) -> tuple[str, ...]`
  - `card_command(backdrop_png: Path, clip: Path | None, *, seconds: float, sfx: SfxKind | None, sequence: SequenceFormat, output_path: Path, ffmpeg_binary: str = "ffmpeg") -> tuple[str, ...]`
  - `frame_command(ref: FrameRef, *, sequence: SequenceFormat, output_path: Path, ffmpeg_binary: str = "ffmpeg") -> tuple[str, ...]` (one cropped, scaled PNG at `ref.at`)

- [ ] **Step 1: Write the failing tests**

```python
import subprocess
from pathlib import Path

import pytest

from splitsmith.composition import SequenceFormat
from splitsmith.config import VideoMetadata
from splitsmith.reel_plan import FrameRef, ReelClip
from splitsmith.reel_render import card_command, clip_command, frame_command, reel_format, sfx_source

UP = SequenceFormat(1080, 1920, 30, 1)


def _meta(w=1920, h=1080, num=30, den=1) -> VideoMetadata:
    return VideoMetadata(width=w, height=h, duration_seconds=60.0, frame_rate_num=num, frame_rate_den=den)


def test_reel_format_upright_and_landscape_and_720p_fallback():
    assert reel_format([_meta()], upright=True) == SequenceFormat(1080, 1920, 30, 1)
    assert reel_format([_meta()], upright=False) == SequenceFormat(1920, 1080, 30, 1)
    assert reel_format([_meta(1280, 720)], upright=True) == SequenceFormat(720, 1280, 30, 1)


def test_reel_format_takes_the_most_common_rate():
    metas = [_meta(num=60), _meta(num=30), _meta(num=60)]
    assert reel_format(metas, upright=True).frame_rate_num == 60


def test_clip_command_crops_scales_conforms_and_slows():
    clip = ReelClip("002_cold", Path("/m/a.mp4"), 7.4, 1.2, speed=0.5, freeze_seconds=0.4, sfx="riser")
    argv = clip_command(clip, sequence=UP, output_path=Path("/w/out.mp4"))
    graph = argv[argv.index("-filter_complex") + 1]
    assert "crop='min(iw,ih*0.5625)':'min(ih,iw/0.5625)'" in graph
    assert "scale=1080:1920" in graph
    assert "setpts=(PTS-STARTPTS)/0.5" in graph
    assert "fps=30/1" in graph
    assert "tpad=stop_mode=clone:stop_duration=0.4" in graph
    assert argv[argv.index("-ss") + 1] == "7.4" and argv[argv.index("-t") + 1] == "1.2"
    assert "aevalsrc" in " ".join(argv)  # the riser, not the source audio


def test_clip_at_real_speed_uses_the_unseeked_source_audio():
    clip = ReelClip("005_beat", Path("/m/a.mp4"), 7.4, 1.2)
    argv = clip_command(clip, sequence=UP, output_path=Path("/w/out.mp4"))
    graph = argv[argv.index("-filter_complex") + 1]
    assert "[1:a]atrim=start=7.4:duration=1.2" in graph
    assert argv.count("/m/a.mp4") == 2


def test_clip_without_source_audio_is_silent():
    clip = ReelClip("005_beat", Path("/m/a.mp4"), 7.4, 1.2)
    argv = clip_command(clip, sequence=UP, output_path=Path("/w/out.mp4"), source_audio=False)
    assert "anullsrc" in " ".join(argv)


@pytest.mark.parametrize("kind", ["beep", "impact", "riser", "whoosh", None])
def test_sfx_source_is_a_lavfi_graph(kind):
    src = sfx_source(kind, 0.7)
    assert "48000" in src
    assert ("anullsrc" in src) == (kind is None)


def test_card_command_with_motion_clip_and_sfx():
    argv = card_command(
        Path("/w/bg.png"), Path("/w/card.mov"), seconds=0.7, sfx="impact", sequence=UP, output_path=Path("/w/o.mp4")
    )
    assert "/w/card.mov" in argv and "aevalsrc" in " ".join(argv)
    assert argv[argv.index("-t") + 1] == "0.7"


def test_frame_command_grabs_one_cropped_frame():
    argv = frame_command(FrameRef(Path("/m/a.mp4"), 8.0), sequence=UP, output_path=Path("/w/f.png"))
    assert argv[argv.index("-ss") + 1] == "8"
    assert "-frames:v" in argv and "scale=1080:1920" in " ".join(argv)
```

- [ ] **Step 2: Run to verify they fail**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_reel_render.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.reel_render'`

- [ ] **Step 3: Implement**

```python
"""Rendering a highlight reel (spec 2026-10-18-highlight-reel-design).

Every item is one segment encoded like a stage (``mp4_render._encode_args``,
48 kHz stereo audio) so the stitch stream-copies the video. A clip is cut
from its source, centre-cropped to the canvas aspect, conformed to the
reel's rate, slowed by ``setpts`` and held on its last frame by ``tpad``.
SFX are lavfi sources in the argv, so the segment cache keys them and no
audio file ships.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .composition import SequenceFormat
from .config import VideoMetadata
from .look_motion import motion_overlay_filters
from .mp4_render import _encode_args, _rate_string
from .reel_plan import FrameRef, ReelClip, SfxKind

AUDIO_NORMALIZE = "aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo"


def reel_format(primaries: Sequence[VideoMetadata], *, upright: bool) -> SequenceFormat:
    """The reel's canvas: 1080 lines on the short side, 720 when every
    primary is 720p or less (the hosted web rendition), at the most common
    primary rate."""
    rates = Counter((m.frame_rate_num, m.frame_rate_den) for m in primaries) or Counter({(30, 1): 1})
    (num, den), _ = rates.most_common(1)[0]
    short = 720 if primaries and all(min(m.width, m.height) <= 720 for m in primaries) else 1080
    long = short * 16 // 9
    width, height = (short, long) if upright else (long, short)
    return SequenceFormat(width, height, num, den)


def _fit(sequence: SequenceFormat) -> str:
    aspect = sequence.width / sequence.height
    return (
        f"crop='min(iw,ih*{aspect:.4g})':'min(ih,iw/{aspect:.4g})',"
        f"scale={sequence.width}:{sequence.height},setsar=1"
    )


def sfx_source(kind: SfxKind | None, seconds: float) -> str:
    d = f"{seconds:g}"
    stereo = "pan=stereo|c0=c0|c1=c0"
    if kind == "beep":
        return f"sine=frequency=3000:sample_rate=48000:duration=0.3,volume=0.4,{stereo},apad=whole_dur={d}"
    if kind == "impact":
        return (
            "aevalsrc=exprs='0.8*sin(2*PI*(45+90*exp(-10*t))*t)*exp(-4*t)'"
            f":sample_rate=48000:channel_layout=stereo:duration={d}"
        )
    if kind == "riser":
        return (
            f"aevalsrc=exprs='0.3*sin(2*PI*(150*t+400*t*t/{d}))*(t/{d})'"
            f":sample_rate=48000:channel_layout=stereo:duration={d}"
        )
    if kind == "whoosh":
        half = f"{seconds / 2:g}"
        return (
            f"anoisesrc=color=pink:amplitude=0.3:sample_rate=48000:duration={d},highpass=f=400,"
            f"afade=t=in:d={half},afade=t=out:st={half}:d={half},{stereo}"
        )
    return "anullsrc=channel_layout=stereo:sample_rate=48000"


def clip_command(
    clip: ReelClip,
    *,
    sequence: SequenceFormat,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
    source_audio: bool = True,
) -> tuple[str, ...]:
    """One clip. The source's own audio only at real speed with no SFX, cut
    from an unseeked second read by ``atrim`` (``_build_stage_command``
    explains why input ``-ss`` is not trusted for audio)."""
    out_seconds = clip.out_seconds
    rate = _rate_string(sequence)
    video = (
        f"[0:v]{_fit(sequence)},setpts=(PTS-STARTPTS)/{clip.speed:g},fps={rate}"
        + (f",tpad=stop_mode=clone:stop_duration={clip.freeze_seconds:g}" if clip.freeze_seconds > 0 else "")
        + ",format=yuv420p[final]"
    )
    args: list[str] = [ffmpeg_binary, "-hide_banner", "-y"]
    args += ["-ss", f"{clip.start:g}", "-t", f"{clip.seconds:g}", "-i", str(clip.source)]
    use_source = source_audio and clip.speed == 1.0 and clip.sfx is None
    if use_source:
        args += ["-i", str(clip.source)]
        audio = (
            f"[1:a]atrim=start={clip.start:g}:duration={clip.seconds:g},asetpts=PTS-STARTPTS,"
            f"{AUDIO_NORMALIZE},apad=whole_dur={out_seconds:g}[aout]"
        )
    else:
        args += ["-f", "lavfi", "-i", sfx_source(clip.sfx, out_seconds)]
        audio = f"[1:a]{AUDIO_NORMALIZE},atrim=duration={out_seconds:g}[aout]"
    args += ["-filter_complex", f"{video};{audio}", "-map", "[final]", "-map", "[aout]"]
    args += ["-t", f"{out_seconds:g}", *_encode_args(sequence, youtube_preset=False), str(output_path)]
    return tuple(args)


def card_command(
    backdrop_png: Path,
    clip: Path | None,
    *,
    seconds: float,
    sfx: SfxKind | None,
    sequence: SequenceFormat,
    output_path: Path,
    ffmpeg_binary: str = "ffmpeg",
) -> tuple[str, ...]:
    """A card held ``seconds`` over its backdrop: a canvas-sized PNG (the
    composed still, or the backdrop under ``clip``, the template's alpha
    MOV, last frame held), with its SFX."""
    rate = _rate_string(sequence)
    args: list[str] = [ffmpeg_binary, "-hide_banner", "-y", "-loop", "1", "-framerate", rate, "-i", str(backdrop_png)]
    if clip is not None:
        args += ["-i", str(clip)]
        parts, label = motion_overlay_filters(1, rate=rate, seconds=seconds, source_label="0:v")
        video = ";".join([*parts, f"[{label}]format=yuv420p,setsar=1[final]"])
        audio_index = 2
    else:
        video = "[0:v]format=yuv420p,setsar=1[final]"
        audio_index = 1
    args += ["-f", "lavfi", "-i", sfx_source(sfx, seconds)]
    audio = f"[{audio_index}:a]{AUDIO_NORMALIZE}[aout]"
    args += ["-t", f"{seconds:g}", "-filter_complex", f"{video};{audio}", "-map", "[final]", "-map", "[aout]"]
    args += [*_encode_args(sequence, youtube_preset=False), str(output_path)]
    return tuple(args)


def frame_command(
    ref: FrameRef, *, sequence: SequenceFormat, output_path: Path, ffmpeg_binary: str = "ffmpeg"
) -> tuple[str, ...]:
    return (
        ffmpeg_binary, "-hide_banner", "-y", "-ss", f"{ref.at:g}", "-i", str(ref.source),
        "-frames:v", "1", "-vf", _fit(sequence), str(output_path),
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_reel_render.py -v`
Expected: all passed

- [ ] **Step 5: Add the integration test that the argvs run and stitch** (append)

```python
from tests.synthetic_media import build_synthetic_video, ffmpeg_available


def _probe_seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


@pytest.mark.integration
def test_clips_at_different_rates_stitch(tmp_path):
    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    src = build_synthetic_video(tmp_path / "src.mp4")
    fast = tmp_path / "src60.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-r", "60", str(fast)], check=True)
    outs = []
    for i, (source, speed, sfx) in enumerate([(src, 1.0, None), (fast, 0.5, "riser")]):
        out = tmp_path / f"seg{i}.mp4"
        clip = ReelClip(f"c{i}", source, 0.5, 1.0, speed=speed, freeze_seconds=0.3, sfx=sfx)
        subprocess.run(clip_command(clip, sequence=UP, output_path=out), check=True, capture_output=True)
        assert _probe_seconds(out) == pytest.approx(clip.out_seconds, abs=0.07)
        outs.append(out)
    listing = tmp_path / "list.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in outs))
    final = tmp_path / "reel.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:v", "copy", "-c:a", "aac", str(final)],
        check=True,
    )
    assert _probe_seconds(final) == pytest.approx(1.3 + 2.3, abs=0.15)
```

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_reel_render.py -v -m integration`
Expected: 1 passed. If the slowed clip measures short, the `-t` output bound is cutting before `tpad`: drop the output `-t` for clips and rely on the input `-t` plus `tpad`.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add src/splitsmith/reel_render.py tests/test_reel_render.py
/usr/bin/git commit -m "feat(highlights): ffmpeg commands for reel clips, cards and synthesized sfx"
```

---

### Task 6: The `highlight` Look slot and the trailer card

**Files:**
- Modify: `src/splitsmith/looks.py` (add `HIGHLIGHT_SLOT`, `highlight_template_for`)
- Create: `src/splitsmith/data/looks/splitsmith/highlight-trailer.html`
- Modify: `src/splitsmith/data/looks/splitsmith/look.json` (slot entry)
- Modify: `src/splitsmith/data/looks/_history.json` (via the script)
- Create: `src/splitsmith/reel_cards.py`
- Test: `tests/test_reel_cards.py`

**Interfaces:**
- Consumes: `ReelCard` (Task 4); `look_sting`'s pattern (`TemplateContext`, `engine_block`, `shared_url`, `theme_tokens`, `template_digest`, `CardMotion`).
- Produces:
  - `looks.HIGHLIGHT_SLOT = "highlight"`, `looks.highlight_template_for(look: Look, variant: str = "trailer") -> Path | None`
  - `reel_cards.reel_card_context(card: ReelCard, *, width: int, height: int, fps: float, theme: OverlayTheme) -> TemplateContext` with `data.highlight = {"kind", "text", "figure", "unit", "lines", "duration_seconds"}`
  - `reel_cards.reel_card_motion(look: Look, card: ReelCard, *, width: int, height: int, fps: float, rasterizer: Rasterizer, variant: str = "trailer") -> CardMotion | None`

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

import pytest

from splitsmith.looks import HIGHLIGHT_SLOT, highlight_template_for, load_look
from splitsmith.overlay_theme import theme_for
from splitsmith.reel_cards import reel_card_context
from splitsmith.reel_plan import ReelCard


def test_shipped_look_has_the_trailer_highlight():
    path = highlight_template_for(load_look("splitsmith"))
    assert path is not None and path.name == "highlight-trailer.html"


def test_unknown_variant_is_none_not_a_fallback():
    assert highlight_template_for(load_look("splitsmith"), "kart") is None


def test_context_carries_the_card_as_data():
    card = ReelCard("004_callout", "callout", 1.2, "FASTEST SPLIT", figure="0.19", unit="SEC", lines=("Stage 3",))
    ctx = reel_card_context(card, width=1080, height=1920, fps=30, theme=theme_for(load_look("splitsmith")))
    assert ctx.data["highlight"] == {
        "kind": "callout",
        "text": "FASTEST SPLIT",
        "figure": "0.19",
        "unit": "SEC",
        "lines": ["Stage 3"],
        "duration_seconds": 1.2,
    }
    assert HIGHLIGHT_SLOT == "highlight"


@pytest.mark.integration
def test_trailer_card_renders_frames_in_chromium(tmp_path):
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError
    from splitsmith.reel_cards import reel_card_motion

    try:
        raster = ChromiumRasterizer().__enter__()
    except RasterizerUnavailableError:
        pytest.skip("no Chromium")
    try:
        for kind in ("title", "slam", "callout", "end"):
            card = ReelCard("x", kind, 1.0, "FASTEST DRAW", figure="0.94", unit="SEC", lines=("Stage 1",))
            motion = reel_card_motion(load_look("splitsmith"), card, width=540, height=960, fps=30, rasterizer=raster)
            assert motion is not None and motion.animated
            frames = list(motion.frames.frames)
            assert len(frames) == 30
            motion.close()
    finally:
        raster.__exit__(None, None, None)
```

- [ ] **Step 2: Run to verify they fail**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_reel_cards.py -v`
Expected: FAIL, `ImportError: cannot import name 'HIGHLIGHT_SLOT'`

- [ ] **Step 3: Add the slot to `looks.py`** (beside `THUMBNAIL_SLOT` and `thumbnail_template_for`)

```python
HIGHLIGHT_SLOT = "highlight"
```

```python
def highlight_template_for(look: Look, variant: str = "trailer") -> Path | None:
    """The highlight reel's card template in ``variant`` (spec
    2026-10-18-highlight-reel-design): the Look's own, else the shipped
    default Look's, else ``None``. No fallback to another variant: a reel
    style is a whole cut, not one card."""
    own = look.own_template(HIGHLIGHT_SLOT, variant)
    if own is not None:
        return own
    return _shipped_default().own_template(HIGHLIGHT_SLOT, variant)
```

- [ ] **Step 4: Register it in `look.json`** (alphabetical among the slots, before `lower_third`)

```json
    "highlight": {
      "trailer": "highlight-trailer.html"
    },
```

- [ ] **Step 5: Write `highlight-trailer.html`**

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>highlight trailer</title>
<script>
  // The highlight reel's trailer card: one big line slammed in, a figure
  // that punches in under it, small lines after. seek() drives the Web
  // Animations frame by frame; nothing reads wall-clock time.
  document.write('<style>' + window.splitsmith.engine.css + '</style>');
</script>
<style>
  html, body { margin: 0; width: 100%; height: 100%; background: transparent; overflow: hidden; }
  .stack {
    position: absolute; inset: 0; display: flex; flex-direction: column;
    align-items: center; justify-content: center; text-align: center; padding: 0 6vw;
  }
  .text {
    font-family: "Splitsmith Display"; text-transform: uppercase; line-height: 0.95;
    font-size: 11vw; letter-spacing: 0.02em;
  }
  .figure { font-family: "Splitsmith Mono"; font-size: 22vw; line-height: 1; margin-top: 2vh; }
  .unit { font-family: "Splitsmith Display"; font-size: 5vw; margin-left: 1.5vw; }
  .line { font-family: "Splitsmith Display"; font-size: 4.5vw; margin-top: 1.5vh; text-transform: uppercase; }
  .bar { width: 22vw; height: 1.2vw; margin: 2.5vh auto 0; }
</style>
</head>
<body>
<div class="stack" id="stack"></div>
<script>
  var S = window.splitsmith;
  var H = S.data.highlight;
  var animations = [];
  var total = Math.min(H.duration_seconds, 0.8);
  function add(el, keyframes, delayMs, durationMs) {
    var a = el.animate(keyframes, {
      duration: durationMs, delay: delayMs, easing: 'cubic-bezier(0.2, 0.9, 0.1, 1)', fill: 'both'
    });
    a.pause();
    animations.push(a);
  }
  document.addEventListener('DOMContentLoaded', function () {
    var stack = document.getElementById('stack');
    document.body.style.background = H.kind === 'callout' ? 'rgba(0, 0, 0, 0.35)' : 'transparent';
    if (H.text) {
      var text = document.createElement('div');
      text.className = 'text';
      text.textContent = H.text;
      text.style.color = H.kind === 'callout' ? S.theme.accent : S.theme.ink;
      stack.appendChild(text);
      add(text, [{ transform: 'scale(1.8)', opacity: 0 }, { transform: 'scale(1)', opacity: 1 }], 0, 260);
    }
    if (H.figure) {
      var figure = document.createElement('div');
      figure.className = 'figure';
      figure.style.color = S.theme.ink;
      figure.textContent = H.figure;
      if (H.unit) {
        var unit = document.createElement('span');
        unit.className = 'unit';
        unit.style.color = S.theme.muted;
        unit.textContent = H.unit;
        figure.appendChild(unit);
      }
      stack.appendChild(figure);
      add(figure, [{ transform: 'translateY(6vh) scale(0.6)', opacity: 0 }, { transform: 'none', opacity: 1 }], 140, 280);
    }
    var bar = document.createElement('div');
    bar.className = 'bar';
    bar.style.background = S.theme.accent;
    stack.appendChild(bar);
    add(bar, [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], 120, 300);
    (H.lines || []).forEach(function (line, i) {
      var el = document.createElement('div');
      el.className = 'line';
      el.style.color = S.theme.ink_2;
      el.textContent = line;
      stack.appendChild(el);
      add(el, [{ opacity: 0, transform: 'translateY(2vh)' }, { opacity: 1, transform: 'none' }], 260 + 80 * i, 260);
    });
    if (!H.text && !H.figure) { bar.style.display = 'none'; }
  });
  window.duration = function () { return H.duration_seconds; };
  window.poster = function () { return total; };
  window.seek = function (seconds) {
    animations.forEach(function (a) { a.currentTime = seconds * 1000; });
  };
</script>
</body>
</html>
```

A `slam` card with empty `text` (the cold open's beep) draws nothing over a black backdrop: the renderer gives it a black PNG (Task 7).

- [ ] **Step 6: Write `reel_cards.py`** (the sting loader's shape)

```python
"""The highlight reel's cards through the Look's ``highlight`` slot.

Loaded like a sting (``look_sting``): the card as ``data.highlight``, the
palette, the canvas, the engine stylesheet. Frames render lazily, so a
cached segment pulls none; ``digest`` is what the segment cache keys the
clip by. ``None`` when the Look has no such variant or it fails to load:
the renderer then draws the card's text with no motion and notes it.
"""

from __future__ import annotations

import logging

from .look_template import TemplateContext, engine_block, shared_url, template_digest, theme_tokens
from .looks import Look, highlight_template_for
from .overlay_card import CardMotion, card_scale
from .overlay_html import single_css
from .overlay_raster import Rasterizer, TemplateFrames
from .overlay_theme import OverlayTheme, theme_for
from .reel_plan import ReelCard

logger = logging.getLogger(__name__)


def reel_card_context(card: ReelCard, *, width: int, height: int, fps: float, theme: OverlayTheme) -> TemplateContext:
    return TemplateContext(
        theme=theme_tokens(theme),
        data={
            "highlight": {
                "kind": card.kind,
                "text": card.text,
                "figure": card.figure,
                "unit": card.unit,
                "lines": list(card.lines),
                "duration_seconds": card.seconds,
            }
        },
        size={"width": width, "height": height},
        fps=fps,
        engine=engine_block(css=single_css(width=width, height=height, scale=card_scale(height), theme=theme)),
        assets={"shared": shared_url()},
    )


def reel_card_motion(
    look: Look,
    card: ReelCard,
    *,
    width: int,
    height: int,
    fps: float,
    rasterizer: Rasterizer,
    variant: str = "trailer",
) -> CardMotion | None:
    template = highlight_template_for(look, variant)
    if template is None:
        logger.warning("Look %s has no highlight %r", look.name, variant)
        return None
    context = reel_card_context(card, width=width, height=height, fps=fps, theme=theme_for(look))
    frames: TemplateFrames | None = None
    try:
        frames = rasterizer.render_template_frames(
            template, context=context, width=width, height=height, fps=fps, max_seconds=card.seconds
        )
        digest = template_digest(template, context, fps=fps, engine_version=rasterizer.engine_version())
    except Exception as exc:  # noqa: BLE001 -- one bad template must not lose the reel
        if frames is not None:
            frames.close()
        logger.warning("could not load the highlight card %s through %s (%s)", card.name, template, exc)
        return None
    return CardMotion(template=template, context=context, frames=frames, digest=digest)
```

- [ ] **Step 7: Record the new template's history**

Run: `uv run python scripts/record_template_history.py`
Expected: `_history.json` gains `highlight-trailer.html`. (`tests/test_look_tools.py` fails until it does.)

- [ ] **Step 8: Run the tests, then the Look suites that pin the shipped Look**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_reel_cards.py tests/test_looks.py tests/test_look_tools.py tests/test_look_template.py -v`
Expected: all passed. If `test_looks.py` enumerates the shipped manifest's slots, or the catalog test (`GET /api/looks`) lists every slot, add `highlight` there. The catalog serving it is fine; the Look editor's `TEMPLATE_SLOTS` stays as it is (user Looks do not author reel cards in v1).

- [ ] **Step 9: Commit**

```bash
/usr/bin/git add src/splitsmith/looks.py src/splitsmith/reel_cards.py src/splitsmith/data/looks tests/test_reel_cards.py
/usr/bin/git commit -m "feat(highlights): the highlight Look slot and the trailer card"
```

---

### Task 7: The render driver

**Files:**
- Modify: `src/splitsmith/reel_render.py`
- Test: `tests/test_reel_render.py`

**Interfaces:**
- Consumes: Tasks 4 to 6; `mp4_render._build_concat_command`, `mp4_render._has_audio_stream`, `mp4_render.FFmpegError`; `overlay_card.card_backdrop`, `overlay_card.compose_card`, `overlay_card.first_frame_image`; `look_motion.write_motion_clip`; `segment_cache.SegmentCache`; `overlay_raster.ChromiumRasterizer`, `RasterizerUnavailableError`.
- Produces:
  - `ReelRenderResult` (frozen): `output_path: Path`, `duration_seconds: float`, `degradations: tuple[str, ...]`
  - `render_reel(items: Sequence[ReelItem], *, sequence: SequenceFormat, output_path: Path, work_dir: Path, look_name: str = "splitsmith", ffmpeg_binary: str = "ffmpeg", runner: Runner = subprocess.run, rasterizer: Rasterizer | None = None, segment_cache: SegmentCache | None = None) -> ReelRenderResult`

Behaviour:
- Opens one `ChromiumRasterizer` when any card exists and none was passed; unavailable means every card is drawn as a plain hold (black or its frame) with a degradation, never a failure.
- A card's backdrop: `frame_command` grab when `backdrop` is set (dimmed through `card_backdrop(..., blur_radius=0, dim=0.35)`), else a black canvas PNG (`card_backdrop(None, ...)` paints the Look's surface; for the reel use a plain black `Image.new("RGB", (w, h), "black")`).
- Animated card: backdrop PNG plus `write_motion_clip` into `<name>_motion.mov`, run in `prepare` only on a cache miss, keyed by `virtual_inputs={str(clip_path): motion.digest}`. Still card (template says `duration() == 0`): `compose_card(first_frame, canvas)` saved as the PNG, `clip=None`.
- Clips: `clip_command(..., source_audio=_has_audio_stream(clip.source))`.
- Stitch: `_build_concat_command(list_path=..., output_path=..., reencode_audio=True)`.

- [ ] **Step 1: Write the failing test** (a fake runner that records argvs and touches outputs)

```python
from splitsmith.reel_plan import ReelCard
from splitsmith.reel_render import render_reel


class _FakeRaster:
    """Stands in for Chromium: every card is a 1-frame still."""

    def engine_version(self) -> str:
        return "fake"

    def render_template_frames(self, template, *, context, width, height, fps, max_seconds):
        class Frames:
            duration = 0.0
            frames = [bytes(width * height * 4)]

            def close(self):
                pass

        f = Frames()
        f.width, f.height = width, height
        return f


def test_render_reel_encodes_every_item_then_stitches(tmp_path, monkeypatch):
    calls: list[tuple[str, ...]] = []

    def runner(cmd, **kw):
        calls.append(tuple(cmd))
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"x")

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr("splitsmith.reel_render._has_audio_stream", lambda p: True)
    items = (
        ReelCard("001_title", "title", 1.6, "Bromma", lines=("Anna",), sfx="impact"),
        ReelClip("002_beat", Path("/m/a.mp4"), 7.4, 1.2),
        ReelCard("003_callout", "callout", 1.2, "FASTEST SPLIT", figure="0.19", unit="SEC",
                 backdrop=FrameRef(Path("/m/a.mp4"), 8.0)),
    )
    result = render_reel(
        items, sequence=UP, output_path=tmp_path / "reel.mp4", work_dir=tmp_path / "w",
        runner=runner, rasterizer=_FakeRaster(),
    )
    assert len(calls) == 5  # title card, clip, frame grab, callout card, stitch
    assert "concat" in calls[-1]
    assert result.duration_seconds == pytest.approx(1.6 + 1.2 + 1.2)
    assert result.degradations == ()
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_reel_render.py::test_render_reel_encodes_every_item_then_stitches -v`
Expected: FAIL, `ImportError: cannot import name 'render_reel'`

- [ ] **Step 3: Implement** (append to `reel_render.py`; extend imports)

```python
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

from .look_motion import write_motion_clip
from .looks import load_look
from .mp4_render import FFmpegError, Runner, _build_concat_command, _has_audio_stream
from .overlay_card import card_backdrop, compose_card, first_frame_image
from .overlay_raster import ChromiumRasterizer, Rasterizer, RasterizerUnavailableError
from .reel_cards import reel_card_motion
from .reel_plan import ReelCard, ReelItem
from .segment_cache import SegmentCache

CALLOUT_DIM = 0.35


@dataclass(frozen=True)
class ReelRenderResult:
    output_path: Path
    duration_seconds: float
    degradations: tuple[str, ...]


def _run(cmd: tuple[str, ...], runner: Runner) -> None:
    proc = runner(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {str(proc.stderr)[-2000:]}")


def render_reel(
    items: Sequence[ReelItem],
    *,
    sequence: SequenceFormat,
    output_path: Path,
    work_dir: Path,
    look_name: str = "splitsmith",
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
    rasterizer: Rasterizer | None = None,
    segment_cache: SegmentCache | None = None,
) -> ReelRenderResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    degradations: list[str] = []
    owned: ChromiumRasterizer | None = None
    active = rasterizer
    if active is None and any(isinstance(i, ReelCard) for i in items):
        owned = ChromiumRasterizer()
        try:
            active = owned.__enter__()
        except RasterizerUnavailableError as exc:
            owned, active = None, None
            degradations.append(f"reel cards drawn without text: {exc.detail}")
    look = load_look(look_name)
    fps = sequence.frame_rate_num / sequence.frame_rate_den
    used: set[str] = set()

    def encode(cmd: tuple[str, ...], out: Path, *, virtual: dict[str, str] | None = None,
               prepare: Callable[[], object] | None = None) -> Path:
        if segment_cache is None:
            if prepare is not None:
                prepare()
            _run(cmd, runner)
            return out
        key = segment_cache.key(cmd, output_path=out, work_dir=work_dir, virtual_inputs=virtual)
        used.add(key)
        hit = segment_cache.lookup(key)
        if hit is not None:
            return hit
        if prepare is not None:
            prepare()
        partial = segment_cache.partial_path(key)
        try:
            _run(tuple(str(partial) if t == str(out) else t for t in cmd), runner)
            return segment_cache.commit(partial, key)
        finally:
            partial.unlink(missing_ok=True)

    def backdrop_for(card: ReelCard) -> Image.Image:
        if card.backdrop is None:
            return Image.new("RGB", (sequence.width, sequence.height), "black")
        frame = work_dir / f"{card.name}_frame.png"
        _run(frame_command(card.backdrop, sequence=sequence, output_path=frame, ffmpeg_binary=ffmpeg_binary), runner)
        return card_backdrop(frame, width=sequence.width, height=sequence.height, look=look,
                             blur_radius=0, dim=CALLOUT_DIM)

    segments: list[tuple[Path, float]] = []
    try:
        for item in items:
            out = work_dir / f"{item.name}.mp4"
            if isinstance(item, ReelClip):
                cmd = clip_command(item, sequence=sequence, output_path=out, ffmpeg_binary=ffmpeg_binary,
                                   source_audio=_has_audio_stream(item.source))
                segments.append((encode(cmd, out), item.out_seconds))
                continue
            canvas = backdrop_for(item)
            png = work_dir / f"{item.name}_backdrop.png"
            motion = None
            if active is not None:
                motion = reel_card_motion(look, item, width=sequence.width, height=sequence.height,
                                          fps=fps, rasterizer=active)
                if motion is None:
                    degradations.append(f"{item.name}: the highlight card failed to load")
            if motion is not None and motion.animated:
                canvas.save(png)
                clip_path = work_dir / f"{item.name}_motion.mov"
                frames = motion.frames

                def write(frames=frames, clip_path=clip_path) -> None:
                    if not clip_path.exists():
                        write_motion_clip(frames, out=clip_path, fps=fps, ffmpeg_binary=ffmpeg_binary)

                cmd = card_command(png, clip_path, seconds=item.seconds, sfx=item.sfx, sequence=sequence,
                                   output_path=out, ffmpeg_binary=ffmpeg_binary)
                try:
                    segments.append((encode(cmd, out, virtual={str(clip_path): motion.digest}, prepare=write),
                                     item.seconds))
                finally:
                    motion.close()
                continue
            if motion is not None:
                text = first_frame_image(motion)
                if text is not None:
                    canvas = compose_card(text, canvas)
            canvas.save(png)
            cmd = card_command(png, None, seconds=item.seconds, sfx=item.sfx, sequence=sequence,
                               output_path=out, ffmpeg_binary=ffmpeg_binary)
            segments.append((encode(cmd, out), item.seconds))
    finally:
        if owned is not None:
            owned.__exit__(None, None, None)

    listing = work_dir / "segments.txt"
    listing.write_text("".join(f"file '{path}'\n" for path, _ in segments))
    _run(_build_concat_command(list_path=listing, output_path=output_path, ffmpeg_binary=ffmpeg_binary,
                               reencode_audio=True), runner)
    if segment_cache is not None:
        segment_cache.evict(keep=used)
    return ReelRenderResult(output_path, sum(s for _, s in segments), tuple(degradations))
```

Check while implementing: `mp4_render` exports `Runner` (it is the type of `render_mp4`'s `runner`); `_run` there may already be importable and preferable to this local one; `compose_card` lives where `template_prep` imports it from (follow that import).
- [ ] **Step 4: Run to verify it passes**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_reel_render.py -v`
Expected: all non-integration passed

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/reel_render.py tests/test_reel_render.py
/usr/bin/git commit -m "feat(highlights): render a reel's items through the segment cache and stitch them"
```

---

### Task 8: `splitsmith match highlights`

**Files:**
- Modify: `src/splitsmith/match_cli.py`
- Create: `src/splitsmith/reel_inputs.py` (loading: project to tiles, footage, readiness; kept out of the CLI per the architecture rules)
- Test: `tests/test_reel_inputs.py`, `tests/test_match_highlights_cli.py`

**Interfaces:**
- Consumes: `match_exports.stage_inputs_for_project`, `MatchStageInput`, `load_stage_shots`, `load_stage_events`, `load_stage_reloads`, `fcpxml_gen.probe_video`, `MatchProject.stage_statuses` (the dict method at `match_project.py:1355`; confirm its name), Tasks 1 to 7.
- Produces:
  - `reel_inputs.ReelInputs` (frozen): `stages: tuple[TileStageData, ...]`, `footage: dict[int, StageFootage]`, `readiness: tuple[StageReadiness, ...]`, `primaries: tuple[VideoMetadata, ...]`, `stage_names: dict[int, str]`
  - `reel_inputs.load_reel_inputs(project: MatchProject, root: Path, *, label: str, probe: Callable[[Path], VideoMetadata] = probe_video) -> ReelInputs`
  - CLI: `splitsmith match highlights MATCH [--shooter S] [--landscape] [--output PATH] [--force]`

Behaviour of `load_reel_inputs`: every non-skipped stage whose primary has a beep goes through `stage_inputs_for_project`; footage exists when `trimmed_path.is_file()`; the audit doc is `json.loads(audit_path.read_text())` when present; a stage's tile is built exactly as `match_exports` builds the match summary's (`TileStageData(label=..., stage_number=..., shots=load_stage_shots(path), stage_time_seconds=..., scorecard=..., stage_rounds=..., reloads=load_stage_reloads(path), events=load_stage_events(path), standard_start=...)`); the secondary is the first `MatchSecondaryInput` whose file exists, probed for its duration. Only ready stages' tiles and footage are returned for picking.

CLI behaviour: locked prints one line per blocking stage ("Stage 3: 2 Breakdown proposals left") and exits 1, unless `--force` (renders from the ready stages only, for trying the cut early). Unlocked: find, pick, plan with `end_lines` = best draw, average split (`statistic_splits` over every ready stage), rounds; render with `render_segment_cache(output_config)`; print the path, the duration and any degradation. Default output: the shooter's exports directory, `<match name>-highlights.mp4`.

- [ ] **Step 1: Write the failing tests for `load_reel_inputs`** using the demo seeder's shape: build a project in `tmp_path` with `scripts/seed_demo_match.py`'s helpers if importable, else construct a `MatchProject` with two stages, write two audit docs (one ready, one with an `auto` region), touch the trimmed files, and inject `probe=lambda p: VideoMetadata(width=1920, height=1080, duration_seconds=30, frame_rate_num=30, frame_rate_den=1)`. Assert: readiness states `["ready", "proposals_left"]`, `footage` keys `{ready stage}`, `stages` has one tile with the doc's shots.

- [ ] **Step 2: Run, watch it fail, implement `reel_inputs.py`, run, watch it pass.**

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_reel_inputs.py -v`

- [ ] **Step 3: Write the CLI tests** with `typer.testing.CliRunner`, monkeypatching `splitsmith.match_cli.render_reel` to record its items and return a `ReelRenderResult`:
  - a locked match exits 1 and names the blocking stage and its proposal count;
  - `--force` on the same match calls `render_reel` once;
  - an unlocked match calls it with `sequence.height > sequence.width`, and with `--landscape` the other way round.

- [ ] **Step 4: Run, fail, implement the command in `match_cli.py`** (next to `export`, following its shooter resolution and `MatchProject.load` lines 715 to 745), run, pass.

Run: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest -n0 tests/test_match_highlights_cli.py -v`

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/reel_inputs.py src/splitsmith/match_cli.py tests/test_reel_inputs.py tests/test_match_highlights_cli.py
/usr/bin/git commit -m "feat(highlights): splitsmith match highlights renders a shooter's reel"
```

---

### Task 9: Real footage, real frames

No new code unless the frames show a defect.

- [ ] **Step 1:** Seed a match with media: `uv run python scripts/seed_demo_match.py ~/.claude-tmp/reel-demo --media`. Audit and break down its stages through the CLI path the seeder leaves (or mark the docs by hand: `events_seeded` set, regions `manual`), then `uv run splitsmith match highlights ~/.claude-tmp/reel-demo --shooter <slug>`.
- [ ] **Step 2:** Run it on a real, fully broken-down match from the corpus (ask the user which one; never run against `~/.splitsmith` with auto-sync on, `SPLITSMITH_AUTO_SYNC=0` regardless).
- [ ] **Step 3:** Pull frames at every item boundary and mid-card: `ffmpeg -i reel.mp4 -vf fps=4 ~/.claude-tmp/reel-frames/%04d.png`, and look at them. Check: the crop keeps the gun in frame; the slam text fits at 1080 wide; the callout figure is legible over the dimmed frame; no black flash between segments; the SFX are audible and not clipping (`ffmpeg -i reel.mp4 -af volumedetect -f null -`, max under 0 dB).
- [ ] **Step 4:** Send the reel and a contact sheet to the user (SendUserFile) before part 2 is planned.
- [ ] **Step 5:** Full suite once: `~/.claude-tmp/with-project-ffmpeg.sh uv run pytest` and `uv run ruff check src tests` and `uv run black --check src tests`.
