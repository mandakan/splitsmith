# Stage Events and Lane Editor Implementation Plan (part 1: model, API, Coach editor)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Movement, reload and activation become regions (`events`) on the stage audit doc, seeded from the reload hint and the division's magazine capacity, edited in lanes under the Coach page's video, and surfaced as per-shot `moving` flags and per-stage reload figures.

**Architecture:** A `StageEvent` Pydantic model and a pure `splitsmith/events.py` (validation, figures, seeder) feed the existing coach GET and one new PUT, both under `_audit_rmw()` with the audit revision's optimistic check. The SPA mirrors the pure module in `lib/events.ts` (case for case over one shared fixture file), renders a `LaneEditor` built on `MarkerLayer`'s pointer conventions, and adopts `useScrubSource` for the Coach player. Rendering, the summary card, CSV/FCPXML and share figures are **part 2**, a separate plan written after this one ships, because the overlay slots need real event data on disk to look at frames.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, pytest (`-n auto`); React 19 + TypeScript, vitest + testing-library, Tailwind v4.

**Spec:** `docs/superpowers/specs/2026-10-08-stage-events-lane-editor-design.md`

## Global Constraints

- Python: type hints everywhere, `pathlib.Path`, f-strings, Black 110, Ruff (`E F I N W UP B C4 PTH`), imports stdlib / third-party / local separated by blank lines, `from __future__ import annotations`. `uv run` for everything; never `pip`.
- Pure functions in `events.py`: no file I/O, no `state` access. Pydantic models for anything crossing a module boundary.
- Every audit-doc read-modify-write on the server runs inside `with _audit_rmw():` (spec; pinned by `tests/test_audit_lock_wiring.py`).
- Routes are reached as `/api/matches/{id}/shooters/{slug}/stages/{n}/...`; tests use the `_bootstrap` helper's `base` prefix (`tests/test_coach_api.py`).
- No new `doc_kind`, no new dependency, no new preference. `events` and `events_seeded` are keys on the existing audit doc.
- Seconds from beep everywhere in events (`start`, `end`); the SPA converts to clip time with `coach.beep_time`.
- SPA: build with `components/ui` primitives; `numeral` utility for every number; no arbitrary `text-[...]`/`tracking-[...]`/`font-display` outside `components/ui` (ESLint `no-restricted-syntax`); red (`--color-led`) only for the playhead (current position); `Label` 1-3 words; one `primary` button per view (the Coach page already has none, keep it that way).
- Event hues are the budget chip ticks: movement `bg-beep`, reload `bg-live`, activation `bg-ink-2` (`BUDGET_TICK` / `Chip` `TICK` map).
- Tests: Python `uv run pytest -n0 <file>` while iterating, full `uv run pytest` before a commit that touches `src/`. SPA from `src/splitsmith/ui_static`: `pnpm test -- <file>`, `pnpm typecheck`, `pnpm lint` before each SPA commit.
- Mutation drill on every new test: delete or invert the behaviour, watch the test fail, restore it. Note the result in the commit body when a test needed a rewrite to fail.
- Commit messages: conventional commits (`feat(coach): ...`, `test: ...`), one logical change per commit, Co-Authored-By / Claude-Session trailers as the session's reminder gives them.

## Review Focus

Inputs the spec implies but no obvious test exercises, most likely to bite first. Each line names the owning task, which adds the test.

1. **A reload region that spans a shot** (start before shot k, end after it): `classify_intervals_in_dicts(events=)` must flip only gaps that are longer than `transition_max_s`, never a split that happens to lie inside the region. Task 3.
2. **Events whose `end` exceeds the last shot** (the shooter reloads after the last shot, or a seed on a stage whose last shot is unloaded): figures and the summary must not index past the shot list; `capacity_warning` counts the trailing segment too. Task 1.
3. **A PUT whose events reference a stage with zero shots** (user draws a movement before detection ran): the classifier call gets an empty list and the save still succeeds. Task 5.
4. **Dragging an edge past its own other edge** (start dragged beyond end): the editor must swap or stop, never emit `end <= start`, which the server would 422. Task 7 (`clampToLane` stops at `MIN_EVENT_S`).
5. **A `source`-kind primary video** (no trim yet) on the Coach page: `useScrubSource.choose` must not be consulted, and the stream URL must stay `kind=source`. Task 9.

---

## File map

**Engine (create):**
- `src/splitsmith/events.py` -- pure: `validate_lanes`, `next_event_id`, `events_from_doc`, `shot_times_from_doc`, `shot_is_moving`, `reload_figures`, `stage_event_summary`, `capacity_for`, `capacity_config`, `seed_events`, `seed_doc`.
- `tests/fixtures/events/cases.json` -- the shared Python/TS fixture.
- `tests/test_events.py`

**Engine (modify):**
- `src/splitsmith/config.py` -- `EventKind`, `StageEvent`, `DivisionCapacityConfig`, `Config.division_capacity`.
- `src/splitsmith/coach.py` -- `events=` on `classify_intervals_in_dicts`, `classify_intervals_in_models`, `heal_unclassified`.
- `src/splitsmith/match_cli.py:738-826` -- reclassify passes the doc's events.
- `src/splitsmith/ui/server.py` -- coach GET additions and seeding (`get_stage_coach` 13794, `_build_coach_response` 13710, `_coach_video_entries` 13629), new `put_stage_events`, `_SHARE_PATH_RE` untouched, model `StageEventsPutRequest` next to `CoachShotPatchRequest` (6360).
- `src/splitsmith/ui/capabilities.py:105-125` -- `_REVIEW_ROUTES` gains the events PUT.
- `tests/test_coach_api.py`, `tests/test_coach_classify.py`, `tests/test_audit_lock_wiring.py`, `tests/test_share_write_allowlist.py`, `tests/test_ui_capabilities.py`, `tests/test_match_bundle_queries.py`.

**SPA (create), under `src/splitsmith/ui_static/src/`:**
- `lib/events.ts`, `lib/events.test.ts` -- the TS twin.
- `components/coach/LaneEditor.tsx`, `components/coach/LaneEditor.test.tsx`
- `components/coach/EventCard.tsx` (the selected-region card; tested in `Coach.components.test.tsx`)
- `components/coach/EventList.tsx` (phone read-only rows; tested in `Coach.components.test.tsx`)

**SPA (modify):**
- `lib/api.ts` -- `StageEvent`, `StageEventSummary`, coach payload fields, `api.putStageEvents`.
- `pages/Coach.tsx`, `pages/Coach.test.tsx`
- `components/coach/Coach.components.test.tsx`

**Docs:** `CLAUDE.md` (a "Stage events" section), `SPEC.md` module list.

---

### Task 1: `StageEvent` model and the pure figures

**Files:**
- Modify: `src/splitsmith/config.py` (after `IntervalClassSource`, line 59; after `CoachAutoClassifyConfig`, line 518)
- Create: `src/splitsmith/events.py`
- Create: `tests/fixtures/events/cases.json`
- Create: `tests/test_events.py`

**Interfaces:**
- Produces (config.py):
  ```python
  EventKind = Literal["movement", "reload", "activation"]
  class StageEvent(BaseModel):  # extra="forbid"
      id: str; kind: EventKind; start: float (ge=0); end: float; source: IntervalClassSource; note: str | None = None
      # validator: end > start
  ```
- Produces (events.py):
  ```python
  EVENTS_FIELD: Final = "events"; EVENTS_SEEDED_FIELD: Final = "events_seeded"; MIN_EVENT_S: Final = 0.05
  @dataclass(frozen=True) class ReloadFigure: event_id: str; duration: float; moving: bool; overhang: float | None
  class StageEventSummary(BaseModel): movement_s: float; moving_shots: int; reloads: int; reload_avg_s: float | None; overhang_s: float; capacity_warning: str | None
  def validate_lanes(events: Sequence[StageEvent]) -> None            # ValueError names both ids
  def next_event_id(events: Iterable[StageEvent | dict[str, Any]]) -> str   # "evt-<max+1>", "evt-1" when empty
  def events_from_doc(doc: dict[str, Any]) -> list[StageEvent]        # validates each dict; ValueError on a bad one
  def shot_times_from_doc(doc: dict[str, Any]) -> list[float]         # kept shots' ms_after_beep / 1000, sorted
  def shot_is_moving(time_from_beep: float, events: Sequence[StageEvent]) -> bool
  def reload_figures(events: Sequence[StageEvent]) -> list[ReloadFigure]
  def stage_event_summary(shot_times: Sequence[float], events: Sequence[StageEvent], capacity: int | None) -> StageEventSummary
  ```

- [ ] **Step 1: Write the shared fixture**

`tests/fixtures/events/cases.json`. Times are seconds from beep. Each case is self-describing so the TS test can iterate the same file.

```json
{
  "_note": "Shared by tests/test_events.py and ui_static/src/lib/events.test.ts. Times in seconds from beep. Add a case here, never only on one side.",
  "cases": [
    {
      "name": "po_reload_on_the_move",
      "capacity": 15,
      "shots": [1.21, 1.52, 1.98, 2.27, 2.71, 3.02, 4.35, 4.71, 5.12, 5.48, 6.42, 6.70, 7.08, 7.36,
                10.62, 10.91, 11.40, 11.69, 12.20, 12.51, 13.00, 13.30, 15.90, 16.20],
      "events": [
        {"id": "evt-1", "kind": "movement", "start": 3.40, "end": 6.10, "source": "manual"},
        {"id": "evt-2", "kind": "movement", "start": 7.60, "end": 9.16, "source": "manual"},
        {"id": "evt-3", "kind": "reload",   "start": 8.05, "end": 9.47, "source": "manual"},
        {"id": "evt-4", "kind": "movement", "start": 13.60, "end": 15.60, "source": "manual"}
      ],
      "expect": {
        "moving": [false, false, false, false, false, false, true, true, true, true, false, false, false, false,
                   false, false, false, false, false, false, false, false, false, false],
        "reloads": [{"event_id": "evt-3", "duration": 1.42, "moving": true, "overhang": 0.31}],
        "summary": {"movement_s": 6.26, "moving_shots": 4, "reloads": 1, "reload_avg_s": 1.42, "overhang_s": 0.31, "capacity_warning": null}
      }
    },
    {
      "name": "standing_reload_hidden_none",
      "capacity": 15,
      "shots": [1.0, 1.3, 1.6, 4.9, 5.2],
      "events": [
        {"id": "evt-1", "kind": "reload", "start": 1.9, "end": 3.9, "source": "manual"}
      ],
      "expect": {
        "moving": [false, false, false, false, false],
        "reloads": [{"event_id": "evt-1", "duration": 2.0, "moving": false, "overhang": null}],
        "summary": {"movement_s": 0.0, "moving_shots": 0, "reloads": 1, "reload_avg_s": 2.0, "overhang_s": 0.0, "capacity_warning": null}
      }
    },
    {
      "name": "reload_hidden_inside_movement_negative_overhang",
      "capacity": null,
      "shots": [1.0, 1.3, 6.0, 6.3],
      "events": [
        {"id": "evt-1", "kind": "movement", "start": 1.5, "end": 5.8, "source": "manual"},
        {"id": "evt-2", "kind": "reload",   "start": 2.0, "end": 3.6, "source": "manual"}
      ],
      "expect": {
        "moving": [false, false, false, false],
        "reloads": [{"event_id": "evt-2", "duration": 1.6, "moving": true, "overhang": -2.2}],
        "summary": {"movement_s": 4.3, "moving_shots": 0, "reloads": 1, "reload_avg_s": 1.6, "overhang_s": 0.0, "capacity_warning": null}
      }
    },
    {
      "name": "two_movements_overlap_one_reload_latest_end_wins",
      "capacity": null,
      "shots": [1.0, 7.0],
      "events": [
        {"id": "evt-1", "kind": "movement", "start": 1.2, "end": 3.0, "source": "manual"},
        {"id": "evt-2", "kind": "movement", "start": 3.5, "end": 5.0, "source": "manual"},
        {"id": "evt-3", "kind": "reload",   "start": 2.5, "end": 5.5, "source": "manual"}
      ],
      "expect": {
        "moving": [false, false],
        "reloads": [{"event_id": "evt-3", "duration": 3.0, "moving": true, "overhang": 0.5}],
        "summary": {"movement_s": 3.3, "moving_shots": 0, "reloads": 1, "reload_avg_s": 3.0, "overhang_s": 0.5, "capacity_warning": null}
      }
    },
    {
      "name": "event_past_last_shot_and_inclusive_ends",
      "capacity": null,
      "shots": [1.0, 2.0, 3.0],
      "events": [
        {"id": "evt-1", "kind": "movement", "start": 2.0, "end": 3.0, "source": "auto"},
        {"id": "evt-2", "kind": "reload",   "start": 3.2, "end": 4.9, "source": "auto"}
      ],
      "expect": {
        "moving": [false, true, true],
        "reloads": [{"event_id": "evt-2", "duration": 1.7, "moving": false, "overhang": null}],
        "summary": {"movement_s": 1.0, "moving_shots": 2, "reloads": 1, "reload_avg_s": 1.7, "overhang_s": 0.0, "capacity_warning": null}
      }
    },
    {
      "name": "capacity_warning_no_reload_regions",
      "capacity": 10,
      "shots": [1.0, 1.3, 1.6, 1.9, 2.2, 2.5, 2.8, 3.1, 3.4, 3.7, 4.0, 4.3],
      "events": [],
      "expect": {
        "moving": [false, false, false, false, false, false, false, false, false, false, false, false],
        "reloads": [],
        "summary": {"movement_s": 0.0, "moving_shots": 0, "reloads": 0, "reload_avg_s": null, "overhang_s": 0.0, "capacity_warning": "12 shots without a reload"}
      }
    },
    {
      "name": "capacity_plus_one_is_allowed",
      "capacity": 10,
      "shots": [1.0, 1.3, 1.6, 1.9, 2.2, 2.5, 2.8, 3.1, 3.4, 3.7, 4.0],
      "events": [],
      "expect": {
        "moving": [false, false, false, false, false, false, false, false, false, false, false],
        "reloads": [],
        "summary": {"movement_s": 0.0, "moving_shots": 0, "reloads": 0, "reload_avg_s": null, "overhang_s": 0.0, "capacity_warning": null}
      }
    }
  ],
  "lane_violations": [
    {
      "name": "same_kind_overlap",
      "events": [
        {"id": "evt-1", "kind": "movement", "start": 1.0, "end": 3.0, "source": "manual"},
        {"id": "evt-2", "kind": "movement", "start": 2.5, "end": 4.0, "source": "manual"}
      ],
      "mentions": ["evt-1", "evt-2"]
    }
  ],
  "lane_ok": [
    {
      "name": "touching_edges_and_cross_kind",
      "events": [
        {"id": "evt-1", "kind": "movement", "start": 1.0, "end": 3.0, "source": "manual"},
        {"id": "evt-2", "kind": "movement", "start": 3.0, "end": 4.0, "source": "manual"},
        {"id": "evt-3", "kind": "reload",   "start": 2.0, "end": 3.5, "source": "manual"}
      ]
    }
  ]
}
```

Arithmetic check before committing (do it by hand; the tests use `pytest.approx(abs=1e-9)` after rounding to 2 dp): `po_reload_on_the_move` movement_s = 2.70 + 1.56 + 2.00 = 6.26; shots 4.35, 4.71, 5.12, 5.48 fall in 3.40-6.10 (4), 15.90 and 16.20 are outside 13.60-15.60. `two_movements` movement_s = 1.8 + 1.5 = 3.3, overhang = 5.5 - 5.0 = 0.5.

- [ ] **Step 2: Write the failing tests**

`tests/test_events.py`:

```python
"""Pure stage-event figures (spec 2026-10-08). Mirrored case for case by
``ui_static/src/lib/events.test.ts`` over the same fixture file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from splitsmith.config import StageEvent
from splitsmith.events import (
    events_from_doc,
    next_event_id,
    reload_figures,
    shot_is_moving,
    shot_times_from_doc,
    stage_event_summary,
    validate_lanes,
)

FIXTURE = Path(__file__).parent / "fixtures" / "events" / "cases.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = {c["name"]: c for c in DATA["cases"]}


def _events(raw: list[dict]) -> list[StageEvent]:
    return [StageEvent.model_validate(e) for e in raw]


@pytest.mark.parametrize("name", sorted(CASES))
def test_fixture_case(name: str) -> None:
    case = CASES[name]
    events = _events(case["events"])
    validate_lanes(events)
    expect = case["expect"]

    assert [shot_is_moving(t, events) for t in case["shots"]] == expect["moving"]

    figs = reload_figures(events)
    assert [f.event_id for f in figs] == [r["event_id"] for r in expect["reloads"]]
    for fig, want in zip(figs, expect["reloads"], strict=True):
        assert round(fig.duration, 2) == pytest.approx(want["duration"])
        assert fig.moving is want["moving"]
        if want["overhang"] is None:
            assert fig.overhang is None
        else:
            assert round(fig.overhang, 2) == pytest.approx(want["overhang"])

    summary = stage_event_summary(case["shots"], events, case["capacity"])
    want_s = expect["summary"]
    assert round(summary.movement_s, 2) == pytest.approx(want_s["movement_s"])
    assert summary.moving_shots == want_s["moving_shots"]
    assert summary.reloads == want_s["reloads"]
    if want_s["reload_avg_s"] is None:
        assert summary.reload_avg_s is None
    else:
        assert round(summary.reload_avg_s, 2) == pytest.approx(want_s["reload_avg_s"])
    assert round(summary.overhang_s, 2) == pytest.approx(want_s["overhang_s"])
    assert summary.capacity_warning == want_s["capacity_warning"]


@pytest.mark.parametrize("case", DATA["lane_violations"], ids=lambda c: c["name"])
def test_lane_violation_names_both_events(case: dict) -> None:
    with pytest.raises(ValueError) as exc:
        validate_lanes(_events(case["events"]))
    for ident in case["mentions"]:
        assert ident in str(exc.value)


@pytest.mark.parametrize("case", DATA["lane_ok"], ids=lambda c: c["name"])
def test_lane_ok(case: dict) -> None:
    validate_lanes(_events(case["events"]))  # no raise


def test_stage_event_end_must_follow_start() -> None:
    with pytest.raises(ValidationError):
        StageEvent(id="evt-1", kind="reload", start=2.0, end=2.0, source="manual")
    with pytest.raises(ValidationError):
        StageEvent(id="evt-1", kind="reload", start=-0.1, end=1.0, source="manual")


def test_stage_event_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        StageEvent.model_validate(
            {"id": "evt-1", "kind": "reload", "start": 1.0, "end": 2.0, "source": "manual", "colour": "red"}
        )


def test_next_event_id_only_grows() -> None:
    assert next_event_id([]) == "evt-1"
    events = [
        {"id": "evt-7", "kind": "reload", "start": 1.0, "end": 2.0, "source": "auto"},
        StageEvent(id="evt-3", kind="movement", start=1.0, end=2.0, source="manual"),
    ]
    assert next_event_id(events) == "evt-8"
    # A deleted high id is never reused: the caller passes the deleted one's
    # id through ``events`` (the audit event log keeps it) -- here we only
    # pin that a stray non-matching id is ignored, not treated as zero.
    assert next_event_id([{"id": "manual-abc"}]) == "evt-1"


def test_events_from_doc_and_shot_times() -> None:
    doc = {
        "shots": [
            {"shot_number": 2, "ms_after_beep": 1800},
            {"shot_number": 1, "ms_after_beep": 1500},
            {"shot_number": 3},  # dropped: no ms_after_beep
        ],
        "events": [{"id": "evt-1", "kind": "movement", "start": 1.0, "end": 2.0, "source": "auto"}],
    }
    assert shot_times_from_doc(doc) == [1.5, 1.8]
    assert [e.id for e in events_from_doc(doc)] == ["evt-1"]
    assert events_from_doc({"shots": []}) == []
    with pytest.raises(ValueError):
        events_from_doc({"events": [{"id": "evt-1", "kind": "nap", "start": 1, "end": 2, "source": "auto"}]})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest -n0 tests/test_events.py -q`
Expected: `ImportError: cannot import name 'StageEvent' from 'splitsmith.config'`.

- [ ] **Step 4: Add the model to `config.py`**

After `IntervalClassSource = Literal["auto", "manual"]` (line 59):

```python
# Stage events (spec 2026-10-08): regions on the stage timeline, one lane
# per kind. Independent of shots -- a movement may span several shots,
# which the per-gap ``interval_class`` cannot say.
EventKind = Literal["movement", "reload", "activation"]


class StageEvent(BaseModel):
    """One region on the stage timeline, in seconds from the beep.

    A reload's handles mean hand off the grip -> gun back on target (the
    full manipulation cost), not the mechanical magazine change. Ids are
    ``evt-<n>`` and never reused within a stage (the ``cand-<n>`` rule).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: EventKind
    start: float = Field(ge=0.0)
    end: float
    source: IntervalClassSource
    note: str | None = None

    @model_validator(mode="after")
    def _end_after_start(self) -> StageEvent:
        if self.end <= self.start:
            raise ValueError(f"event {self.id}: end ({self.end}) must be after start ({self.start})")
        return self
```

Check `ConfigDict` is already imported from `pydantic` in config.py (grep `^from pydantic import`); add it to that import if not.

- [ ] **Step 5: Write `events.py`**

```python
"""Stage events: movement, reload and activation as regions on the stage
timeline (spec 2026-10-08).

Pure functions only -- no I/O, no server state. The HTTP layer owns the
audit-doc read/write; this module owns the figures. Mirrored by
``ui_static/src/lib/events.ts`` over ``tests/fixtures/events/cases.json``:
a rule that changes here changes there in the same change.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from pydantic import BaseModel

from .config import StageEvent

EVENTS_FIELD: Final = "events"
EVENTS_SEEDED_FIELD: Final = "events_seeded"
#: Shortest region the editor will produce; a handle dragged past its
#: partner stops here rather than inverting.
MIN_EVENT_S: Final = 0.05

_EVENT_ID_RE = re.compile(r"\Aevt-(\d+)\Z")


@dataclass(frozen=True)
class ReloadFigure:
    event_id: str
    duration: float
    #: True when any movement region overlaps the reload.
    moving: bool
    #: ``reload.end - movement.end`` against the overlapping movement with
    #: the latest end; positive means the reload cost time, zero or
    #: negative means it was hidden. ``None`` for a standing reload.
    overhang: float | None


class StageEventSummary(BaseModel):
    movement_s: float
    moving_shots: int
    reloads: int
    reload_avg_s: float | None
    #: Sum of the positive overhangs only.
    overhang_s: float
    #: "N shots without a reload" when a capacity division fired more
    #: than ``capacity + 1`` rounds between two reload regions.
    capacity_warning: str | None


def validate_lanes(events: Sequence[StageEvent]) -> None:
    """Two events of one kind never overlap (touching edges are fine)."""
    by_kind: dict[str, list[StageEvent]] = {}
    for e in events:
        by_kind.setdefault(e.kind, []).append(e)
    for kind, lane in by_kind.items():
        lane.sort(key=lambda e: (e.start, e.end))
        for prev, cur in zip(lane, lane[1:], strict=False):
            if cur.start < prev.end:
                raise ValueError(f"{kind} events {prev.id} and {cur.id} overlap")


def next_event_id(events: Iterable[StageEvent | dict[str, Any]]) -> str:
    high = 0
    for e in events:
        ident = e.id if isinstance(e, StageEvent) else e.get("id")
        m = _EVENT_ID_RE.match(str(ident)) if ident is not None else None
        if m:
            high = max(high, int(m.group(1)))
    return f"evt-{high + 1}"


def events_from_doc(doc: dict[str, Any]) -> list[StageEvent]:
    raw = doc.get(EVENTS_FIELD) or []
    return [StageEvent.model_validate(e) for e in raw]


def shot_times_from_doc(doc: dict[str, Any]) -> list[float]:
    """Kept shots' times from the beep, ascending."""
    times = [
        float(s["ms_after_beep"]) / 1000.0
        for s in doc.get("shots") or []
        if isinstance(s, dict) and s.get("ms_after_beep") is not None
    ]
    return sorted(times)


def _overlaps(a: StageEvent, b: StageEvent) -> bool:
    return a.start < b.end and b.start < a.end


def shot_is_moving(time_from_beep: float, events: Sequence[StageEvent]) -> bool:
    return any(e.kind == "movement" and e.start <= time_from_beep <= e.end for e in events)


def reload_figures(events: Sequence[StageEvent]) -> list[ReloadFigure]:
    movements = [e for e in events if e.kind == "movement"]
    out: list[ReloadFigure] = []
    for r in sorted((e for e in events if e.kind == "reload"), key=lambda e: e.start):
        overlapping = [m for m in movements if _overlaps(m, r)]
        if overlapping:
            latest_end = max(m.end for m in overlapping)
            out.append(ReloadFigure(r.id, r.end - r.start, True, r.end - latest_end))
        else:
            out.append(ReloadFigure(r.id, r.end - r.start, False, None))
    return out


def _capacity_warning(shot_times: Sequence[float], reloads: Sequence[StageEvent], capacity: int | None) -> str | None:
    if capacity is None or not shot_times:
        return None
    # Shots are split into segments at each reload's start; a shot fired
    # during a reload region counts toward the segment after it.
    cuts = sorted(r.start for r in reloads)
    counts: list[int] = []
    i = 0
    for cut in cuts:
        n = 0
        while i < len(shot_times) and shot_times[i] < cut:
            n += 1
            i += 1
        counts.append(n)
    counts.append(len(shot_times) - i)
    worst = max(counts)
    if worst > capacity + 1:
        return f"{worst} shots without a reload"
    return None


def stage_event_summary(
    shot_times: Sequence[float], events: Sequence[StageEvent], capacity: int | None
) -> StageEventSummary:
    figs = reload_figures(events)
    reloads = [e for e in events if e.kind == "reload"]
    return StageEventSummary(
        movement_s=sum(e.end - e.start for e in events if e.kind == "movement"),
        moving_shots=sum(1 for t in shot_times if shot_is_moving(t, events)),
        reloads=len(figs),
        reload_avg_s=(sum(f.duration for f in figs) / len(figs)) if figs else None,
        overhang_s=sum(f.overhang for f in figs if f.overhang is not None and f.overhang > 0),
        capacity_warning=_capacity_warning(shot_times, reloads, capacity),
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -n0 tests/test_events.py -q`
Expected: all pass. If `two_movements_overlap_one_reload_latest_end_wins` fails on overhang, re-check the hand arithmetic in the fixture before touching the code.

- [ ] **Step 7: Mutation drill**

Change `cur.start < prev.end` to `<=` in `validate_lanes`: `test_lane_ok[touching_edges_and_cross_kind]` must fail. Change `max(m.end ...)` to `min`: the `two_movements` case must fail. Restore both.

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check src/splitsmith/events.py src/splitsmith/config.py tests/test_events.py && uv run black --check src/splitsmith/events.py tests/test_events.py
git add src/splitsmith/events.py src/splitsmith/config.py tests/test_events.py tests/fixtures/events/cases.json
git commit -m "feat(events): StageEvent model and pure reload/movement figures"
```

---

### Task 2: Capacity table and the seeder

**Files:**
- Modify: `src/splitsmith/config.py` (after `CoachAutoClassifyConfig`; `Config` fields at line 544)
- Modify: `src/splitsmith/events.py`
- Modify: `tests/test_events.py`

**Interfaces:**
- Consumes: `StageEvent`, `next_event_id`, `shot_times_from_doc`, `EVENTS_FIELD`, `EVENTS_SEEDED_FIELD` (Task 1); `CoachAutoClassifyConfig.reload_hint_min_s`; `auto_classify_config()` pattern in `coach.py:171` (reads `SPLITSMITH_CONFIG`).
- Produces:
  ```python
  class DivisionCapacityConfig(BaseModel): capacities: dict[str, int]   # config.py
  Config.division_capacity: DivisionCapacityConfig
  def capacity_config() -> DivisionCapacityConfig                       # events.py, env-aware like auto_classify_config
  def capacity_for(division: str | None, config: DivisionCapacityConfig | None = None) -> int | None
  def seed_events(shot_times: Sequence[float], *, hint_min_s: float, capacity: int | None) -> list[StageEvent]
  def seed_doc(doc: dict[str, Any], *, hint_min_s: float, capacity: int | None) -> bool   # mutates; True when it wrote
  ```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_events.py`:

```python
from splitsmith.config import Config, DivisionCapacityConfig  # noqa: E402  (keep with the other imports)
from splitsmith.events import capacity_config, capacity_for, seed_doc, seed_events  # noqa: E402


def _quick(start: float, n: int, split: float = 0.3) -> list[float]:
    return [round(start + i * split, 3) for i in range(n)]


@pytest.mark.parametrize(
    ("division", "want"),
    [
        ("Production Optics", 15),
        ("Production", 15),
        ("Classic Minor", 10),
        ("Classic Major", 8),
        ("Revolver Minor", 8),
        ("Revolver Major", 6),
        ("Open Major", None),
        ("Standard Minor", None),
        ("  production  optics ", 15),
        (None, None),
        ("Something New", None),
    ],
)
def test_capacity_for_ssi_division_strings(division: str | None, want: int | None) -> None:
    assert capacity_for(division) == want


def test_capacity_config_reads_the_env_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("division_capacity:\n  capacities:\n    Production Optics: 16\n", encoding="utf-8")
    monkeypatch.setenv("SPLITSMITH_CONFIG", str(cfg))
    assert capacity_for("Production Optics", capacity_config()) == 16
    assert capacity_for("Classic Minor", capacity_config()) is None  # the override replaces the table
    monkeypatch.delenv("SPLITSMITH_CONFIG")
    assert capacity_for("Classic Minor", capacity_config()) == 10


def test_config_default_has_the_table() -> None:
    assert Config().division_capacity.capacities["Production Optics"] == 15
    assert isinstance(DivisionCapacityConfig().capacities, dict)


def test_seed_po_reload_on_a_movement_hint_wins_inside_window() -> None:
    # 12 quick shots, a 3.2 s gap (the reload on the move), 8 more.
    times = _quick(1.2, 12) + _quick(1.2 + 11 * 0.3 + 3.2, 8)
    seeds = seed_events(times, hint_min_s=2.5, capacity=15)
    assert [(s.kind, s.source) for s in seeds] == [("reload", "auto")]
    assert seeds[0].start == pytest.approx(times[11])
    assert seeds[0].end == pytest.approx(times[12])
    assert seeds[0].id == "evt-1"


def test_seed_capacity_plus_one_edge_no_false_early_seed() -> None:
    # 16 quick shots (15 + 1 chambered), then a 3 s gap, then 4 more.
    times = _quick(1.0, 16) + _quick(1.0 + 15 * 0.3 + 3.0, 4)
    seeds = seed_events(times, hint_min_s=2.5, capacity=15)
    assert len(seeds) == 1
    assert seeds[0].start == pytest.approx(times[15])


def test_seed_no_hint_picks_longest_gap_in_window_when_a_reload_is_required() -> None:
    # 18 shots, no gap over the hint, but shot 10 -> 11 is the longest (1.9 s).
    times = _quick(1.0, 10) + [1.0 + 9 * 0.3 + 1.9] 
    times += _quick(times[-1] + 0.3, 7)
    seeds = seed_events(times, hint_min_s=2.5, capacity=15)
    assert len(seeds) == 1
    assert seeds[0].start == pytest.approx(times[9])
    assert seeds[0].end == pytest.approx(times[10])


def test_seed_fourteen_shots_no_hint_no_seed() -> None:
    assert seed_events(_quick(1.0, 14), hint_min_s=2.5, capacity=15) == []


def test_seed_classic_minor_window_is_eleven() -> None:
    # 11 quick shots then a 0.9 s gap then 3: a reload is required by shot 12,
    # no gap is hinted, so the longest gap in the window (after shot 11) wins.
    times = _quick(1.0, 11) + _quick(1.0 + 10 * 0.3 + 0.9, 3)
    seeds = seed_events(times, hint_min_s=2.5, capacity=10)
    assert len(seeds) == 1
    assert seeds[0].start == pytest.approx(times[10])


def test_seed_open_division_hint_only() -> None:
    times = _quick(1.0, 6) + _quick(1.0 + 5 * 0.3 + 2.8, 6) + _quick(1.0 + 5 * 0.3 + 2.8 + 5 * 0.3 + 2.6, 4)
    seeds = seed_events(times, hint_min_s=2.5, capacity=None)
    assert [round(s.start, 3) for s in seeds] == [round(times[5], 3), round(times[11], 3)]
    assert [s.id for s in seeds] == ["evt-1", "evt-2"]


def test_seed_two_magazines() -> None:
    # PO: reload hinted after shot 14, then 16 more quick shots, then a hinted
    # gap, then 2. Two seeds, windows advancing from the shot after each seed.
    a = _quick(1.0, 14)
    b = _quick(a[-1] + 3.0, 16)
    c = _quick(b[-1] + 2.9, 2)
    seeds = seed_events(a + b + c, hint_min_s=2.5, capacity=15)
    assert [round(s.start, 3) for s in seeds] == [round(a[-1], 3), round(b[-1], 3)]


def test_seed_doc_runs_once_and_only_with_shots() -> None:
    empty = {"shots": []}
    assert seed_doc(empty, hint_min_s=2.5, capacity=15) is False
    assert "events_seeded" not in empty

    times = _quick(1.0, 12) + _quick(1.0 + 11 * 0.3 + 3.2, 4)
    doc = {"shots": [{"shot_number": i + 1, "ms_after_beep": int(round(t * 1000))} for i, t in enumerate(times)]}
    assert seed_doc(doc, hint_min_s=2.5, capacity=15) is True
    assert doc["events_seeded"] is True
    assert [e["kind"] for e in doc["events"]] == ["reload"]
    assert "note" not in doc["events"][0]

    doc["events"] = []  # the user deleted the proposal
    assert seed_doc(doc, hint_min_s=2.5, capacity=15) is False
    assert doc["events"] == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -n0 tests/test_events.py -q`
Expected: `ImportError: cannot import name 'DivisionCapacityConfig'`.

- [ ] **Step 3: Add the config**

In `config.py`, after `CoachAutoClassifyConfig`:

```python
class DivisionCapacityConfig(BaseModel):
    """Magazine capacity per division, keyed on the division string as SSI
    spells it (the power factor is in the name where it matters:
    "Classic Major"). ``capacity + 1`` is the most rounds a shooter can
    fire before a reload (one chambered on a full magazine), and that is a
    bound the seeder uses, never a count. Open and Standard have no entry.
    A YAML override replaces the table.
    """

    capacities: dict[str, int] = Field(
        default_factory=lambda: {
            "Production": 15,
            "Production Optics": 15,
            "Classic Minor": 10,
            "Classic Major": 8,
            "Revolver Minor": 8,
            "Revolver Major": 6,
        }
    )
```

In `class Config`, next to `coach_auto_classify` (line 544):

```python
    division_capacity: DivisionCapacityConfig = Field(default_factory=DivisionCapacityConfig)
```

- [ ] **Step 4: Add the seeder to `events.py`**

Imports to add: `import os`, `from pathlib import Path`, and extend the local import to `from .config import Config, DivisionCapacityConfig, StageEvent`, plus `from .runtime import ENV_CONFIG_FILE`.

```python
def capacity_config() -> DivisionCapacityConfig:
    """The capacity table, from ``SPLITSMITH_CONFIG`` when set (the same
    accessor shape as ``coach.auto_classify_config``)."""
    raw = os.environ.get(ENV_CONFIG_FILE, "").strip()
    if not raw:
        return DivisionCapacityConfig()
    return Config.load(Path(raw).expanduser()).division_capacity


def _norm_division(division: str) -> str:
    return " ".join(division.split()).casefold()


def capacity_for(division: str | None, config: DivisionCapacityConfig | None = None) -> int | None:
    if not division:
        return None
    table = {_norm_division(k): v for k, v in (config or capacity_config()).capacities.items()}
    return table.get(_norm_division(division))


def seed_events(shot_times: Sequence[float], *, hint_min_s: float, capacity: int | None) -> list[StageEvent]:
    """Propose ``reload`` regions, each spanning a whole gap, ``source="auto"``.

    Without a capacity: every gap over the hint. With one: ``capacity + 1``
    shots fit before a reload is forced, so from shot ``first`` the reload
    sits in one of the gaps after shots ``first .. first + capacity``; the
    first hinted gap in that window wins, else the longest; then the window
    advances to the shot after the seed. Movement is never seeded.
    """
    times = sorted(shot_times)
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    seeds: list[StageEvent] = []

    def _seed(g: int) -> None:
        seeds.append(
            StageEvent(id=next_event_id(seeds), kind="reload", start=times[g], end=times[g + 1], source="auto")
        )

    if capacity is None:
        for g, gap in enumerate(gaps):
            if gap > hint_min_s:
                _seed(g)
        return seeds

    first = 0
    while first < len(gaps):
        window = range(first, min(first + capacity, len(gaps) - 1) + 1)
        required = first + capacity <= len(gaps) - 1  # a shot beyond capacity + 1 exists
        hinted = [g for g in window if gaps[g] > hint_min_s]
        if hinted:
            chosen = hinted[0]
        elif required:
            chosen = max(window, key=lambda g: gaps[g])
        else:
            break
        _seed(chosen)
        first = chosen + 1
    return seeds


def seed_doc(doc: dict[str, Any], *, hint_min_s: float, capacity: int | None) -> bool:
    """Seed once per stage. Returns True when the doc changed."""
    if doc.get(EVENTS_SEEDED_FIELD) or doc.get(EVENTS_FIELD):
        return False
    times = shot_times_from_doc(doc)
    if not times:
        return False
    seeds = seed_events(times, hint_min_s=hint_min_s, capacity=capacity)
    doc[EVENTS_FIELD] = [e.model_dump(exclude_none=True) for e in seeds]
    doc[EVENTS_SEEDED_FIELD] = True
    return True
```

Note the `required` test: `len(gaps) - 1` is the last gap index; `first + capacity` is the gap after shot `first + capacity` (0-based), i.e. after the `capacity + 1`-th shot of this magazine. When that gap exists, a further shot exists and a reload is forced inside the window.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest -n0 tests/test_events.py -q`
Expected: all pass. `test_seed_no_hint_picks_longest_gap...`: 18 shots, `len(gaps) = 17`, `first + capacity = 15 <= 16` so required; longest gap in 0..15 is index 9. Walk it by hand if it fails.

- [ ] **Step 6: Mutation drill**

Change `required = first + capacity <= len(gaps) - 1` to `<`: `test_seed_capacity_plus_one_edge...` still passes (hinted) but build a quick check that `test_seed_classic_minor_window_is_eleven` now fails (13 gaps, `0 + 10 < 12` still true -- it won't). So instead change `min(first + capacity, ...)` to `first + capacity - 1`: `test_seed_capacity_plus_one_edge_no_false_early_seed` must fail (the hinted gap 15 leaves the window and the longest quick gap gets seeded). Restore.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/splitsmith/events.py src/splitsmith/config.py tests/test_events.py && uv run black --check src/splitsmith/events.py tests/test_events.py
git add src/splitsmith/events.py src/splitsmith/config.py tests/test_events.py
git commit -m "feat(events): division capacity table and the reload seeder"
```

---

### Task 3: Regions hint the auto-classifier; `match reclassify` passes them

**Files:**
- Modify: `src/splitsmith/coach.py:244-284` (`classify_intervals_in_dicts`), `:286-331` (`heal_unclassified`), `:333-357` (`classify_intervals_in_models`)
- Modify: `src/splitsmith/match_cli.py:738-826` (`reclassify`)
- Modify: `tests/test_coach_classify.py`

**Interfaces:**
- Consumes: `StageEvent`, `events_from_doc` (Task 1).
- Produces:
  ```python
  def classify_intervals_in_dicts(shots, config, *, events: Sequence[StageEvent] = ()) -> list[dict]
  def classify_intervals_in_models(shots, config, *, events: Sequence[StageEvent] = ()) -> list[Shot]
  def heal_unclassified(shots, config=None, *, events: Sequence[StageEvent] = ()) -> bool
  def _classify_gap(gap_s, config, *, reload_overlap: bool = False) -> IntervalClass
  def gap_overlaps_reload(prev_t: float, t: float, events) -> bool
  ```
  Rule: a gap whose auto-class is `movement` (gap > `transition_max_s`) and whose span `(prev_t, t)` overlaps a reload region auto-classes `reload`. Nothing else changes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coach_classify.py` (the file already has `cfg`, `_shot`, `_model_shot`):

```python
from splitsmith.config import StageEvent  # noqa: E402  (join the existing import block)


def _reload(start: float, end: float) -> StageEvent:
    return StageEvent(id="evt-1", kind="reload", start=start, end=end, source="manual")


def test_long_gap_overlapping_a_reload_region_classes_reload(cfg: CoachAutoClassifyConfig) -> None:
    shots = [_shot(1, 1000), _shot(2, 3800)]  # 2.8 s gap -> movement by the thresholds
    classify_intervals_in_dicts(shots, cfg, events=[_reload(1.9, 3.2)])
    assert shots[1]["interval_class"] == "reload"
    assert shots[1]["interval_class_source"] == "auto"


def test_long_gap_without_a_reload_region_stays_movement(cfg: CoachAutoClassifyConfig) -> None:
    shots = [_shot(1, 1000), _shot(2, 3800)]
    classify_intervals_in_dicts(shots, cfg, events=[_reload(4.0, 5.0)])  # elsewhere
    assert shots[1]["interval_class"] == "movement"


def test_split_inside_a_reload_region_stays_split(cfg: CoachAutoClassifyConfig) -> None:
    # Review focus 1: a region spanning a shot must not relabel its splits.
    shots = [_shot(1, 1000), _shot(2, 1300), _shot(3, 1600)]
    classify_intervals_in_dicts(shots, cfg, events=[_reload(0.9, 1.7)])
    assert [s["interval_class"] for s in shots] == ["first_shot", "split", "split"]


def test_transition_inside_a_reload_region_stays_transition(cfg: CoachAutoClassifyConfig) -> None:
    shots = [_shot(1, 1000), _shot(2, 2500)]  # 1.5 s -> transition
    classify_intervals_in_dicts(shots, cfg, events=[_reload(1.2, 2.3)])
    assert shots[1]["interval_class"] == "transition"


def test_manual_class_wins_over_the_region_hint(cfg: CoachAutoClassifyConfig) -> None:
    shots = [_shot(1, 1000), _shot(2, 3800, interval_class="activation", interval_class_source="manual")]
    classify_intervals_in_dicts(shots, cfg, events=[_reload(1.9, 3.2)])
    assert shots[1]["interval_class"] == "activation"


def test_models_path_takes_the_same_hint(cfg: CoachAutoClassifyConfig) -> None:
    out = classify_intervals_in_models([_model_shot(1, 1.0), _model_shot(2, 3.8)], cfg, events=[_reload(1.9, 3.2)])
    assert out[1].interval_class == "reload"
    assert out[1].interval_class_source == "auto"


def test_heal_passes_events_through(cfg: CoachAutoClassifyConfig) -> None:
    shots = [_shot(1, 1000), _shot(2, 3800)]
    assert heal_unclassified(shots, cfg, events=[_reload(1.9, 3.2)]) is True
    assert shots[1]["interval_class"] == "reload"
```

Check `heal_unclassified` and `classify_intervals_in_models` are imported at the top of the test file; add them if not.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -n0 tests/test_coach_classify.py -q`
Expected: `TypeError: classify_intervals_in_dicts() got an unexpected keyword argument 'events'`.

- [ ] **Step 3: Implement**

In `coach.py`, add `from .config import StageEvent` to the existing import and `from collections.abc import Sequence` is already there.

Replace `_classify_gap`:

```python
def _classify_gap(
    gap_s: float | None, config: CoachAutoClassifyConfig, *, reload_overlap: bool = False
) -> IntervalClass:
    """Map a gap to the auto-class. ``None`` means "this is shot 1".

    ``reload_overlap`` is the one input a stage event adds (spec
    2026-10-08): a gap long enough to be movement that overlaps a reload
    region is a reload. Splits and transitions never change -- a region
    spanning a shot says nothing about that shot's split.
    """
    if gap_s is None:
        return "first_shot"
    if gap_s <= config.split_max_s:
        return "split"
    if gap_s <= config.transition_max_s:
        return "transition"
    return "reload" if reload_overlap else "movement"


def gap_overlaps_reload(prev_t: float, t: float, events: Sequence[StageEvent]) -> bool:
    """True when the open interval (prev_t, t) overlaps a reload region."""
    return any(e.kind == "reload" and e.start < t and prev_t < e.end for e in events)
```

In `classify_intervals_in_dicts`, signature `(shots, config, *, events: Sequence[StageEvent] = ())`; replace the `new_class = _classify_gap(gap_s, config)` line with:

```python
        overlap = gap_s is not None and gap_overlaps_reload(prev_ms_before / 1000.0, float(ms) / 1000.0, events)
        new_class = _classify_gap(gap_s, config, reload_overlap=overlap)
```

where `prev_ms_before` is the previous shot's ms captured *before* `prev_ms = float(ms)` runs -- restructure the loop so the previous value is kept in a local before it is overwritten:

```python
        prev_before = prev_ms
        prev_ms = float(ms)
        if shot.get(FIELD_INTERVAL_CLASS_SOURCE) == "manual":
            continue
        overlap = prev_before is not None and gap_overlaps_reload(prev_before / 1000.0, float(ms) / 1000.0, events)
        new_class = _classify_gap(gap_s, config, reload_overlap=overlap)
```

`heal_unclassified(shots, config=None, *, events=())` forwards `events=events`. `classify_intervals_in_models(shots, config, *, events=())` computes `overlap = prev_t is not None and gap_overlaps_reload(prev_t_before, t, events)` the same way (capture `prev_t` before overwriting) and passes `reload_overlap=overlap`.

In `match_cli.py` `reclassify`, after `shots = doc.get("shots")`, add:

```python
            from .events import events_from_doc

            try:
                stage_events = events_from_doc(doc)
            except ValueError as exc:
                typer.echo(f"stage {n}: skipping events hint, {exc}", err=True)
                stage_events = []
```

(put the import at module top instead if `match_cli.py` already imports from sibling modules at the top -- it does, so place `from .events import events_from_doc` with the other local imports) and pass `events=stage_events` to the `classify_intervals_in_dicts` call.

- [ ] **Step 4: Run the suite for the touched areas**

Run: `uv run pytest -n0 tests/test_coach_classify.py tests/test_coach_api.py tests/test_coach_distributions.py -q`
Expected: all pass (existing callers pass no `events`, so nothing moves).

- [ ] **Step 5: Mutation drill**

Delete the `reload_overlap` branch (always return `"movement"`): the three reload tests fail, the split/transition tests still pass. Restore.

- [ ] **Step 6: Commit**

```bash
uv run ruff check src/splitsmith/coach.py src/splitsmith/match_cli.py tests/test_coach_classify.py
git add src/splitsmith/coach.py src/splitsmith/match_cli.py tests/test_coach_classify.py
git commit -m "feat(coach): a reload region hints the auto-classifier; reclassify passes stored events"
```

---

### Task 4: Coach GET -- events, moving flags, summary, seeding, video versions, `_version`

**Files:**
- Modify: `src/splitsmith/ui/server.py` -- `_coach_video_entries` (13629), `_build_coach_response` (13710), `get_stage_coach` (13794)
- Modify: `tests/test_coach_api.py`

**Interfaces:**
- Consumes: `events_from_doc`, `shot_is_moving`, `stage_event_summary`, `seed_doc`, `capacity_for`, `capacity_config`, `EVENTS_FIELD` (Tasks 1-2); `heal_unclassified(events=)` (Task 3); `competitor_division(project, project_dir)` (`division.py:18`); `_trim_version_for`, `_scrub_version_for`, `_hosted_scrub_version_for`, `StoragePresence` (server.py 7881-7960, 286); `audit_revision`, `REVISION_FIELD` (`audit_revision.py`); `_is_mirror()`, `current_share_request`.
- Produces, on the coach payload: `events: list[dict]`, `event_summary: dict`, `_version: str`, each shot `moving: bool`, each video entry `trim_version: str | None`, `scrub_version: str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coach_api.py` (uses the file's `_bootstrap`, `_bootstrap_legacy_trim`):

```python
def _write_shots(audit_file: Path, ms: list[int]) -> None:
    doc = json.loads(audit_file.read_text(encoding="utf-8"))
    doc["shots"] = [{"shot_number": i + 1, "ms_after_beep": m, "source": "detected"} for i, m in enumerate(ms)]
    doc.pop("events", None)
    doc.pop("events_seeded", None)
    audit_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def _po_shots_with_a_reload_gap() -> list[int]:
    quick = [1200 + i * 300 for i in range(12)]
    after = [quick[-1] + 3200 + i * 300 for i in range(4)]
    return quick + after


def test_get_coach_seeds_a_reload_proposal_once(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path, division="Production Optics")
    _write_shots(audit_file, _po_shots_with_a_reload_gap())

    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert [(e["kind"], e["source"]) for e in body["events"]] == [("reload", "auto")]
    assert body["events"][0]["start"] == pytest.approx(12 * 0.3 + 0.9)  # the 12th shot, 1.2 + 11*0.3
    assert body["events"][0]["id"] == "evt-1"
    assert body["event_summary"]["reloads"] == 1
    assert body["event_summary"]["capacity_warning"] is None
    assert all(s["moving"] is False for s in body["shots"])
    assert isinstance(body["_version"], str) and len(body["_version"]) == 16

    stored = json.loads(audit_file.read_text(encoding="utf-8"))
    assert stored["events_seeded"] is True
    assert len(stored["events"]) == 1

    # The user deletes the proposal; the next read does not resurrect it.
    stored["events"] = []
    audit_file.write_text(json.dumps(stored) + "\n", encoding="utf-8")
    body2 = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert body2["events"] == []


def test_get_coach_without_a_division_seeds_from_the_hint_alone(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    _write_shots(audit_file, [1000, 1300, 4000, 4300, 7200])  # two gaps over 2.5 s
    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert [e["kind"] for e in body["events"]] == ["reload", "reload"]


def test_get_coach_marks_moving_shots_and_sums_the_summary(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    _write_shots(audit_file, [1000, 1300, 2500, 2800, 6000])
    doc = json.loads(audit_file.read_text(encoding="utf-8"))
    doc["events"] = [
        {"id": "evt-1", "kind": "movement", "start": 2.0, "end": 3.0, "source": "manual"},
        {"id": "evt-2", "kind": "movement", "start": 3.4, "end": 5.0, "source": "manual"},
        {"id": "evt-3", "kind": "reload", "start": 3.6, "end": 5.4, "source": "manual"},
    ]
    doc["events_seeded"] = True
    audit_file.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    assert [s["moving"] for s in body["shots"]] == [False, False, True, True, False]
    summary = body["event_summary"]
    assert summary["moving_shots"] == 2
    assert summary["movement_s"] == pytest.approx(2.6)
    assert summary["overhang_s"] == pytest.approx(0.4)
    # The 3.2 s gap before shot 5 overlaps the reload region -> auto reload.
    assert body["shots"][4]["interval_class"] == "reload"
    assert body["shots"][4]["interval_class_source"] == "auto"


def test_get_coach_video_entries_carry_trim_and_scrub_versions(tmp_path: Path) -> None:
    client, base = _bootstrap_legacy_trim(tmp_path, stage_numbers=(1,))
    body = client.get(f"{base}/shooters/me/stages/1/coach").json()
    primary = body["videos"][0]
    assert primary["kind"] == "trim"
    assert isinstance(primary["trim_version"], str) and primary["trim_version"]
    assert primary["scrub_version"] is None  # no _web.mp4 beside the trim in this fixture


def test_get_coach_source_video_has_null_versions(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    primary = client.get(f"{base}/shooters/me/stages/1/coach").json()["videos"][0]
    assert primary["kind"] == "source"
    assert primary["trim_version"] is None
    assert primary["scrub_version"] is None
```

Give `_bootstrap` a keyword `division: str | None = None` and set `project.competitor_division = division` before `project.save(shooter_root)` (the project is saved before `create_app`, so the server reads it on every request whether or not it caches). `import pytest` is already at the top.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -n0 tests/test_coach_api.py -q -k "seeds or moving or versions or hint_alone"`
Expected: `KeyError: 'events'`.

- [ ] **Step 3: Implement in `server.py`**

Imports (module top, with the other `splitsmith` imports): `from splitsmith import events as events_module` and `from splitsmith.division import competitor_division` (check it is not already imported under another name).

`_coach_video_entries`: build `presence` once per call exactly as `get_project` does (`StoragePresence(_storage) if _storage is not None and _storage.supports_presigned_get else None`, with `root = state.shooter_root(slug)`) and add to each entry:

```python
            "trim_version": _trim_version_for(root, stg.stage_number, v, project),
            "scrub_version": (
                _hosted_scrub_version_for(presence, root, stg.stage_number, v, project)
                if presence is not None and not v.path.is_absolute()
                else _scrub_version_for(root, stg.stage_number, v, project)
            ),
```

Both helpers return `None` when there is no trim, which is what `kind == "source"` entries must carry.

`_build_coach_response(slug, payload, primary_beep_in_clip, stg, project, cfg, version)`: after the shots list is built, add

```python
    stage_events = events_module.events_from_doc(payload)
    for s_out in shots_out:  # the per-shot dicts already built
        s_out["moving"] = events_module.shot_is_moving(s_out["time_from_beep"], stage_events)
    capacity = events_module.capacity_for(competitor_division(project, state.shooter_root(slug)))
    summary = events_module.stage_event_summary(
        [s["time_from_beep"] for s in shots_out], stage_events, capacity
    )
    out["events"] = [e.model_dump(exclude_none=True) for e in stage_events]
    out["event_summary"] = summary.model_dump()
    out[REVISION_FIELD] = audit_revision(payload)
```

(adapt the local names to the function's own: it builds a list of shot dicts and a result dict; `time_from_beep` is already computed per shot as `ms / 1000`). `events_from_doc` raising `ValueError` on a corrupt doc should become `HTTPException(422, detail=f"stage {stg.stage_number}: invalid events: {exc}")`.

`get_stage_coach`: inside the `with _audit_rmw():` block, after `cfg = ...`:

```python
            capacity = events_module.capacity_for(competitor_division(project, state.shooter_root(slug)))
            seeded = events_module.seed_doc(payload, hint_min_s=cfg.reload_hint_min_s, capacity=capacity)
            healed = coach_module.heal_unclassified(
                payload.get("shots"), cfg, events=events_module.events_from_doc(payload)
            )
            if (healed or seeded) and not current_share_request.get() and not _is_mirror():
                try:
                    version = _coach_save(slug, stage_number, payload, version)
                except _state_conflict_excs():
                    pass
```

Keep the order: seed first so the heal sees the proposals. The previous code persisted a heal on a mirror; keep that behaviour for `healed` alone if `tests/test_mirror_read_only.py` pins it (run it; if a test fails, make the condition `(healed and not current_share_request.get()) or (seeded and not current_share_request.get() and not _is_mirror())`).

- [ ] **Step 4: Run the coach API tests and the mirror/share suites**

Run: `uv run pytest -n0 tests/test_coach_api.py tests/test_mirror_read_only.py tests/test_share_routes.py tests/test_media_presign_serving.py -q`
Expected: all pass. The `_version` key is new on the coach payload; `tests/test_share_routes.py` compares bodies in places -- if one asserts an exact key set, extend it.

- [ ] **Step 5: Query count stays flat (hosted)**

Append to `tests/test_match_bundle_queries.py` a check that the coach GET's `state_docs` SELECT count does not grow with events present. It reuses `hosted_env`, `hosted_app`, `state_docs_selects`, `_create_match`, `_add_shooter`, `_user_id`, `login`, `OWNER`, `_count` from that file:

```python
def test_coach_get_adds_no_state_docs_query_for_events(hosted_env, hosted_app, state_docs_selects) -> None:
    client, sender = hosted_app
    login(client, sender, OWNER)
    uid = _user_id(hosted_env, OWNER)
    match_id = _create_match(client)
    _add_shooter(hosted_env, uid, match_id, "bea")
    path = f"/api/matches/{match_id}/shooters/bea/stages/1/coach"
    first = _count(client, state_docs_selects, path)   # seeds and saves
    second = _count(client, state_docs_selects, path)  # steady state
    assert second <= first
    assert second <= 3, state_docs_selects
```

The exact steady-state number is whatever the route did before this task plus zero; record the pre-change value by running the test against `main` once (`git stash` the server change, run, `git stash pop`) and pin that number instead of `3`.

- [ ] **Step 6: Mutation drill**

Comment out the `seed_doc` call: `test_get_coach_seeds_a_reload_proposal_once` fails. Replace `shot_is_moving(...)` with `False`: `..._marks_moving_shots...` fails. Restore.

- [ ] **Step 7: Commit**

```bash
uv run ruff check src/splitsmith/ui/server.py tests/test_coach_api.py tests/test_match_bundle_queries.py
git add src/splitsmith/ui/server.py tests/test_coach_api.py tests/test_match_bundle_queries.py
git commit -m "feat(coach): coach payload carries stage events, moving flags, summary, seeds once"
```

---

### Task 5: `PUT .../stages/{n}/events`

**Files:**
- Modify: `src/splitsmith/ui/server.py` -- `StageEventsPutRequest` beside `CoachShotPatchRequest` (6360); the route after `get_stage_coach`
- Modify: `src/splitsmith/ui/capabilities.py:116` (`_REVIEW_ROUTES`)
- Modify: `tests/test_coach_api.py`, `tests/test_audit_lock_wiring.py`, `tests/test_share_write_allowlist.py`, `tests/test_ui_capabilities.py`

**Interfaces:**
- Consumes: Tasks 1-4; `_audit_rmw`, `state.load_audit`, `state.save_audit`, `audit_revision`, `AuditRevisionConflictError`, `_new_event_id()`, `_load_audit_for_coach`, `_build_coach_response`.
- Produces: `PUT /api/shooters/{slug}/stages/{n}/events` with body `{"events": [...], "_version": "<hash>" | null}` -> 200 coach payload (same shape as the GET); 422 `{"detail": {"code": "lane_overlap", "message": "..."}}`; 409 `version_conflict` (the audit PUT's body); 404 when the stage has no audit doc.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coach_api.py`:

```python
def _coach(client, base: str) -> dict:
    return client.get(f"{base}/shooters/me/stages/1/coach").json()


def test_put_events_replaces_the_list_and_returns_the_coach_payload(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    before = _coach(client, base)
    events = [
        {"id": "evt-1", "kind": "movement", "start": 1.4, "end": 3.0, "source": "manual"},
        {"id": "evt-2", "kind": "reload", "start": 2.0, "end": 3.4, "source": "manual", "note": "slow"},
    ]
    resp = client.put(
        f"{base}/shooters/me/stages/1/events", json={"events": events, "_version": before["_version"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["events"] == events
    assert body["_version"] != before["_version"]
    assert body["event_summary"]["reloads"] == 1
    assert "shots" in body and "videos" in body

    stored = json.loads(audit_file.read_text(encoding="utf-8"))
    assert stored["events"] == events
    assert stored["events_seeded"] is True
    assert stored["audit_events"][-1]["kind"] == "events_save"
    assert stored["audit_events"][-1]["payload"] == {"count": 2}


def test_put_events_reclassifies_the_overlapped_gap(tmp_path: Path) -> None:
    client, audit_file, base = _bootstrap(tmp_path)
    # 2.3 s gap: movement by the thresholds, but under the 2.5 s reload hint,
    # so the GET's seeder leaves it alone and the classifier sees no region yet.
    _write_shots(audit_file, [1000, 1300, 3600])
    assert _coach(client, base)["shots"][2]["interval_class"] == "movement"
    v = _coach(client, base)["_version"]
    resp = client.put(
        f"{base}/shooters/me/stages/1/events",
        json={"events": [{"id": "evt-1", "kind": "reload", "start": 1.6, "end": 3.0, "source": "manual"}], "_version": v},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["shots"][2]["interval_class"] == "reload"


def test_put_events_rejects_a_lane_overlap_naming_both(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    v = _coach(client, base)["_version"]
    resp = client.put(
        f"{base}/shooters/me/stages/1/events",
        json={
            "events": [
                {"id": "evt-1", "kind": "movement", "start": 1.0, "end": 3.0, "source": "manual"},
                {"id": "evt-2", "kind": "movement", "start": 2.5, "end": 4.0, "source": "manual"},
            ],
            "_version": v,
        },
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "lane_overlap"
    assert "evt-1" in detail["message"] and "evt-2" in detail["message"]


def test_put_events_rejects_end_before_start(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    resp = client.put(
        f"{base}/shooters/me/stages/1/events",
        json={"events": [{"id": "evt-1", "kind": "reload", "start": 2.0, "end": 2.0, "source": "manual"}]},
    )
    assert resp.status_code == 422


def test_put_events_stale_version_is_a_409_version_conflict(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    _coach(client, base)
    resp = client.put(
        f"{base}/shooters/me/stages/1/events", json={"events": [], "_version": "0000000000000000"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "version_conflict"


def test_put_events_with_no_shots_still_saves(tmp_path: Path) -> None:
    # Review focus 3: a movement drawn before detection ran.
    client, audit_file, base = _bootstrap(tmp_path)
    _write_shots(audit_file, [])
    resp = client.put(
        f"{base}/shooters/me/stages/1/events",
        json={"events": [{"id": "evt-1", "kind": "movement", "start": 1.0, "end": 2.0, "source": "manual"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["events"][0]["id"] == "evt-1"
    assert resp.json()["event_summary"]["moving_shots"] == 0


def test_put_events_unknown_stage_is_404(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    assert client.put(f"{base}/shooters/me/stages/9/events", json={"events": []}).status_code == 404
```

Append to `tests/test_audit_lock_wiring.py` (uses its `_probe_saves`, `_seed_match_export_project`):

```python
def test_events_put_and_coach_get_seed_hold_the_lock(tmp_path: Path, monkeypatch) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    _, loads, saves = _probe_saves(client.app.state.splitsmith_state, monkeypatch)
    body = client.get("/api/shooters/me/stages/1/coach")
    assert body.status_code == 200, body.text
    resp = client.put(
        "/api/shooters/me/stages/1/events",
        json={"events": [{"id": "evt-1", "kind": "movement", "start": 0.2, "end": 0.9, "source": "manual"}],
              "_version": body.json()["_version"]},
    )
    assert resp.status_code == 200, resp.text
    assert all(saves), saves
    assert loads[-1] is True
```

(`_seed_match_export_project` returns a `_MatchClient` that rewrites `/api/shooters/...` to the bound match -- the existing test in that file uses bare paths, so these do too. Its one shot at 500 ms seeds nothing, so the GET's save list may be empty; `all([])` is True, which is why the PUT follows.)

In `tests/test_share_write_allowlist.py`, find the test that proves a non-admitted write 404s through a share token (it posts to a route outside `_SHARE_WRITE_ROUTES`) and add a sibling that `PUT`s `.../stages/1/events` through the share token with `{"events": []}` and asserts `404`. Mirror the exact fixture usage of its neighbour.

In `tests/test_ui_capabilities.py`, find the parametrized list that includes `("PATCH", "shooters/me/stages/1/shots/1/coach")` or the reclassify POST and add `("PUT", "shooters/me/stages/1/events")` to the admitted REVIEW routes.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -n0 tests/test_coach_api.py -q -k put_events`
Expected: `405` or `404` assertions failing (route does not exist).

- [ ] **Step 3: Implement**

Model, beside `CoachShotPatchRequest` (server.py ~6360):

```python
class StageEventsPutRequest(BaseModel):
    """The whole event list for one stage (spec 2026-10-08) plus the audit
    revision the client loaded; a stale one is a 409 like the audit PUT."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    events: list[StageEvent]
    revision: str | None = Field(default=None, alias=REVISION_FIELD)
```

(`StageEvent` imported from `splitsmith.config` with the other config imports.)

Route, after `get_stage_coach`:

```python
    @app.put("/api/shooters/{slug}/stages/{stage_number}/events")
    def put_stage_events(slug: str, stage_number: int, req: StageEventsPutRequest) -> JSONResponse:
        project = state.shooter_project(slug)
        try:
            project.stage(stage_number)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            events_module.validate_lanes(req.events)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "lane_overlap", "message": str(exc)}) from exc
        cfg = coach_module.auto_classify_config()
        with _audit_rmw():
            stored, version = state.load_audit(slug, stage_number)
            if stored is None:
                raise HTTPException(status_code=404, detail=f"no audit JSON yet for stage {stage_number}")
            if req.revision is not None and req.revision != audit_revision(stored):
                raise AuditRevisionConflictError(f"stage {stage_number} audit changed since it was loaded")
            stored[events_module.EVENTS_FIELD] = [e.model_dump(exclude_none=True) for e in req.events]
            stored[events_module.EVENTS_SEEDED_FIELD] = True
            shots = [s for s in stored.get("shots") or [] if isinstance(s, dict)]
            coach_module.classify_intervals_in_dicts(shots, cfg, events=req.events)
            stored.setdefault("audit_events", []).append(
                {
                    "id": _new_event_id(),
                    "ts": datetime.now(UTC).isoformat(),
                    "kind": "events_save",
                    "payload": {"count": len(req.events)},
                }
            )
            state.save_audit(slug, stage_number, stored, version=version)
            payload, version, beep_in_clip, stg, project = _load_audit_for_coach(slug, stage_number)
        return JSONResponse(_build_coach_response(slug, payload, beep_in_clip, stg, project, cfg, version))
```

Match the file's existing timestamp idiom for `audit_events` entries (grep `"kind": "coach_reclassify"` in server.py and copy its `ts` expression). The capabilities entry, in `_REVIEW_ROUTES` right after the audit PUT line:

```python
    # Stage events (spec 2026-10-08): the lane editor's whole-list PUT.
    ("PUT", re.compile(r"\Ashooters/[^/]+/stages/\d+/events\Z")),
```

Nothing to add to `_SHARE_WRITE_ROUTES`: the share surface must 404 this.

- [ ] **Step 4: Run**

Run: `uv run pytest -n0 tests/test_coach_api.py tests/test_audit_lock_wiring.py tests/test_share_write_allowlist.py tests/test_ui_capabilities.py tests/test_mirror_read_only.py -q`
Expected: all pass.

- [ ] **Step 5: Mutation drill**

Remove `with _audit_rmw():` (dedent the block): the lock-wiring test fails. Remove the `validate_lanes` call: the overlap test fails (the save would succeed). Restore both.

- [ ] **Step 6: Full suite, then commit**

Run: `uv run pytest -q` (parallel default). Expected: green.

```bash
uv run ruff check src/splitsmith/ui/server.py src/splitsmith/ui/capabilities.py tests/
git add src/splitsmith/ui/server.py src/splitsmith/ui/capabilities.py tests/test_coach_api.py tests/test_audit_lock_wiring.py tests/test_share_write_allowlist.py tests/test_ui_capabilities.py
git commit -m "feat(coach): PUT stage events with lane validation and the audit revision check"
```

---

### Task 6: SPA types, `api.putStageEvents`, and `lib/events.ts` over the shared fixture

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`CoachShot` 1510, `CoachVideoEntry` 1535, `CoachStageResponse` 1555, `getStageCoach` 4200)
- Create: `src/splitsmith/ui_static/src/lib/events.ts`, `src/splitsmith/ui_static/src/lib/events.test.ts`

All SPA commands run from `src/splitsmith/ui_static`.

**Interfaces:**
- Produces (api.ts):
  ```ts
  export type StageEventKind = "movement" | "reload" | "activation";
  export interface StageEvent { id: string; kind: StageEventKind; start: number; end: number; source: CoachIntervalClassSource; note?: string | null; }
  export interface StageEventSummary { movement_s: number; moving_shots: number; reloads: number; reload_avg_s: number | null; overhang_s: number; capacity_warning: string | null; }
  // CoachShot gains: moving?: boolean
  // CoachVideoEntry gains: trim_version?: string | null; scrub_version?: string | null
  // CoachStageResponse gains: events?: StageEvent[]; event_summary?: StageEventSummary; _version?: string
  api.putStageEvents: (slug: string, stageNumber: number, events: StageEvent[], version: string | null | undefined) => Promise<CoachStageResponse>
  ```
- Produces (lib/events.ts):
  ```ts
  export const MIN_EVENT_S = 0.05;
  export interface ReloadFigure { eventId: string; duration: number; moving: boolean; overhang: number | null }
  export function validateLanes(events: StageEvent[]): string | null        // message naming both ids, or null
  export function nextEventId(events: Pick<StageEvent, "id">[]): string
  export function shotIsMoving(t: number, events: StageEvent[]): boolean
  export function reloadFigures(events: StageEvent[]): ReloadFigure[]
  export function enclosingMovement(event: StageEvent, events: StageEvent[]): StageEvent | null  // latest-ending overlapping movement
  export function clampToLane(events: StageEvent[], id: string, start: number, end: number): { start: number; end: number }
  export function snapTime(t: number, targets: number[], toleranceS: number): number
  export function timeFromX(x: number, width: number, duration: number): number   // clamped 0..duration
  export function summarize(shotTimes: number[], events: StageEvent[], capacity: number | null): StageEventSummary
  ```

- [ ] **Step 1: Types and the API call**

In `api.ts`, after `CoachIntervalClassSource` (1502):

```ts
/** Stage events (spec 2026-10-08): regions on the stage timeline in
 *  seconds from the beep, one lane per kind. A reload's handles mean hand
 *  off the grip -> gun back on target. */
export type StageEventKind = "movement" | "reload" | "activation";
export interface StageEvent {
  id: string;
  kind: StageEventKind;
  start: number;
  end: number;
  source: CoachIntervalClassSource;
  note?: string | null;
}
export interface StageEventSummary {
  movement_s: number;
  moving_shots: number;
  reloads: number;
  reload_avg_s: number | null;
  overhang_s: number;
  capacity_warning: string | null;
}
```

`CoachShot`: add `/** Inside a movement region; absent from servers before 0.6x. */ moving?: boolean;`. `CoachVideoEntry`: add `trim_version?: string | null; scrub_version?: string | null;` with the same doc as `StageVideo`'s. `CoachStageResponse`: add `events?: StageEvent[]; event_summary?: StageEventSummary; /** audit_revision of the stored doc; what putStageEvents sends back */ _version?: string;`.

After `reclassifyStageCoach` (4205):

```ts
  putStageEvents: (slug: string, stageNumber: number, events: StageEvent[], version: string | null | undefined) =>
    request<CoachStageResponse>(
      `/api/shooters/${encodeURIComponent(slug)}/stages/${stageNumber}/events`,
      { method: "PUT", json: { events, _version: version ?? null } },
    ),
```

- [ ] **Step 2: Write the failing TS tests**

`src/lib/events.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { StageEvent, StageEventSummary } from "@/lib/api";
import {
  clampToLane,
  enclosingMovement,
  nextEventId,
  reloadFigures,
  shotIsMoving,
  snapTime,
  summarize,
  timeFromX,
  validateLanes,
} from "./events";

interface Case {
  name: string;
  capacity: number | null;
  shots: number[];
  events: StageEvent[];
  expect: {
    moving: boolean[];
    reloads: { event_id: string; duration: number; moving: boolean; overhang: number | null }[];
    summary: StageEventSummary;
  };
}
interface Fixture {
  cases: Case[];
  lane_violations: { name: string; events: StageEvent[]; mentions: string[] }[];
  lane_ok: { name: string; events: StageEvent[] }[];
}

// The same file tests/test_events.py reads: a case lives on both sides or not at all.
const here = dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(
  readFileSync(join(here, "../../../../../tests/fixtures/events/cases.json"), "utf8"),
) as Fixture;

const r2 = (x: number) => Math.round(x * 100) / 100;

describe("events fixture parity", () => {
  it.each(FIXTURE.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(validateLanes(c.events)).toBeNull();
    expect(c.shots.map((t) => shotIsMoving(t, c.events))).toEqual(c.expect.moving);

    const figs = reloadFigures(c.events);
    expect(figs.map((f) => f.eventId)).toEqual(c.expect.reloads.map((r) => r.event_id));
    figs.forEach((f, i) => {
      const want = c.expect.reloads[i];
      expect(r2(f.duration)).toBeCloseTo(want.duration, 9);
      expect(f.moving).toBe(want.moving);
      if (want.overhang === null) expect(f.overhang).toBeNull();
      else expect(r2(f.overhang as number)).toBeCloseTo(want.overhang, 9);
    });

    const s = summarize(c.shots, c.events, c.capacity);
    expect(r2(s.movement_s)).toBeCloseTo(c.expect.summary.movement_s, 9);
    expect(s.moving_shots).toBe(c.expect.summary.moving_shots);
    expect(s.reloads).toBe(c.expect.summary.reloads);
    if (c.expect.summary.reload_avg_s === null) expect(s.reload_avg_s).toBeNull();
    else expect(r2(s.reload_avg_s as number)).toBeCloseTo(c.expect.summary.reload_avg_s, 9);
    expect(r2(s.overhang_s)).toBeCloseTo(c.expect.summary.overhang_s, 9);
    expect(s.capacity_warning).toBe(c.expect.summary.capacity_warning);
  });

  it.each(FIXTURE.lane_violations.map((c) => [c.name, c] as const))("violation: %s", (_n, c) => {
    const msg = validateLanes(c.events);
    expect(msg).not.toBeNull();
    for (const id of c.mentions) expect(msg).toContain(id);
  });

  it.each(FIXTURE.lane_ok.map((c) => [c.name, c] as const))("ok: %s", (_n, c) => {
    expect(validateLanes(c.events)).toBeNull();
  });
});

const ev = (id: string, kind: StageEvent["kind"], start: number, end: number): StageEvent => ({
  id, kind, start, end, source: "manual",
});

describe("editor helpers", () => {
  it("nextEventId only grows and ignores foreign ids", () => {
    expect(nextEventId([])).toBe("evt-1");
    expect(nextEventId([{ id: "evt-7" }, { id: "evt-3" }, { id: "manual-x" }])).toBe("evt-8");
  });

  it("enclosingMovement picks the latest-ending overlapping movement", () => {
    const events = [ev("evt-1", "movement", 1.2, 3.0), ev("evt-2", "movement", 3.5, 5.0), ev("evt-3", "reload", 2.5, 5.5)];
    expect(enclosingMovement(events[2], events)?.id).toBe("evt-2");
    expect(enclosingMovement(ev("evt-9", "reload", 6, 7), events)).toBeNull();
  });

  it("clampToLane stops at same-lane neighbours and never inverts", () => {
    const events = [ev("evt-1", "movement", 1, 3), ev("evt-2", "movement", 4, 6), ev("evt-3", "reload", 0, 10)];
    // Dragging evt-2's start left past evt-1's end clamps at 3.
    expect(clampToLane(events, "evt-2", 2.0, 6)).toEqual({ start: 3, end: 6 });
    // Dragging evt-1's end right past evt-2's start clamps at 4.
    expect(clampToLane(events, "evt-1", 1, 5.5)).toEqual({ start: 1, end: 4 });
    // Review focus 4: start dragged beyond end stops MIN_EVENT_S short.
    expect(clampToLane(events, "evt-1", 3.5, 3)).toEqual({ start: 3 - 0.05, end: 3 });
    expect(clampToLane(events, "evt-1", 1, 0.5)).toEqual({ start: 1, end: 1.05 });
    // Other lanes do not clamp; the floor is 0.
    expect(clampToLane(events, "evt-3", -1, 10)).toEqual({ start: 0, end: 10 });
  });

  it("snapTime snaps within tolerance to the nearest target only", () => {
    expect(snapTime(4.33, [1.21, 4.35, 9.0], 0.05)).toBe(4.35);
    expect(snapTime(4.2, [1.21, 4.35, 9.0], 0.05)).toBe(4.2);
    expect(snapTime(4.34, [4.3, 4.35], 0.05)).toBe(4.35);
  });

  it("timeFromX maps and clamps", () => {
    expect(timeFromX(450, 900, 16.2)).toBeCloseTo(8.1);
    expect(timeFromX(-10, 900, 16.2)).toBe(0);
    expect(timeFromX(2000, 900, 16.2)).toBe(16.2);
    expect(timeFromX(10, 0, 16.2)).toBe(0);
  });
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `pnpm test -- src/lib/events.test.ts`
Expected: fails to resolve `./events`.

- [ ] **Step 4: Write `lib/events.ts`**

```ts
/**
 * Stage events: movement, reload and activation as regions on the stage
 * timeline (spec 2026-10-08). The TS twin of ``splitsmith/events.py``;
 * both run ``tests/fixtures/events/cases.json`` case for case, so a rule
 * that changes here changes there in the same change. Pure, no React.
 */
import type { StageEvent, StageEventSummary } from "@/lib/api";

/** Shortest region the editor produces; a handle dragged past its partner stops here. */
export const MIN_EVENT_S = 0.05;

export interface ReloadFigure {
  eventId: string;
  duration: number;
  /** Any movement region overlaps the reload. */
  moving: boolean;
  /** reload.end - end of the latest-ending overlapping movement; null when standing. */
  overhang: number | null;
}

const overlaps = (a: StageEvent, b: StageEvent) => a.start < b.end && b.start < a.end;

export function validateLanes(events: StageEvent[]): string | null {
  const byKind = new Map<string, StageEvent[]>();
  for (const e of events) byKind.set(e.kind, [...(byKind.get(e.kind) ?? []), e]);
  for (const [kind, lane] of byKind) {
    lane.sort((a, b) => a.start - b.start || a.end - b.end);
    for (let i = 1; i < lane.length; i++) {
      if (lane[i].start < lane[i - 1].end) return `${kind} events ${lane[i - 1].id} and ${lane[i].id} overlap`;
    }
  }
  return null;
}

export function nextEventId(events: Pick<StageEvent, "id">[]): string {
  let high = 0;
  for (const e of events) {
    const m = /^evt-(\d+)$/.exec(e.id);
    if (m) high = Math.max(high, Number(m[1]));
  }
  return `evt-${high + 1}`;
}

export function shotIsMoving(t: number, events: StageEvent[]): boolean {
  return events.some((e) => e.kind === "movement" && e.start <= t && t <= e.end);
}

export function enclosingMovement(event: StageEvent, events: StageEvent[]): StageEvent | null {
  let best: StageEvent | null = null;
  for (const m of events) {
    if (m.kind !== "movement" || !overlaps(m, event)) continue;
    if (!best || m.end > best.end) best = m;
  }
  return best;
}

export function reloadFigures(events: StageEvent[]): ReloadFigure[] {
  return events
    .filter((e) => e.kind === "reload")
    .sort((a, b) => a.start - b.start)
    .map((r) => {
      const m = enclosingMovement(r, events);
      return { eventId: r.id, duration: r.end - r.start, moving: m !== null, overhang: m ? r.end - m.end : null };
    });
}

function capacityWarning(shotTimes: number[], reloads: StageEvent[], capacity: number | null): string | null {
  if (capacity === null || shotTimes.length === 0) return null;
  const sorted = [...shotTimes].sort((a, b) => a - b);
  const cuts = reloads.map((r) => r.start).sort((a, b) => a - b);
  const counts: number[] = [];
  let i = 0;
  for (const cut of cuts) {
    let n = 0;
    while (i < sorted.length && sorted[i] < cut) {
      n++;
      i++;
    }
    counts.push(n);
  }
  counts.push(sorted.length - i);
  const worst = Math.max(...counts);
  return worst > capacity + 1 ? `${worst} shots without a reload` : null;
}

export function summarize(shotTimes: number[], events: StageEvent[], capacity: number | null): StageEventSummary {
  const figs = reloadFigures(events);
  return {
    movement_s: events.filter((e) => e.kind === "movement").reduce((s, e) => s + (e.end - e.start), 0),
    moving_shots: shotTimes.filter((t) => shotIsMoving(t, events)).length,
    reloads: figs.length,
    reload_avg_s: figs.length ? figs.reduce((s, f) => s + f.duration, 0) / figs.length : null,
    overhang_s: figs.reduce((s, f) => s + (f.overhang !== null && f.overhang > 0 ? f.overhang : 0), 0),
    capacity_warning: capacityWarning(shotTimes, events.filter((e) => e.kind === "reload"), capacity),
  };
}

/** Same-lane neighbours clamp a moving edge; the pair never inverts and the floor is 0. */
export function clampToLane(events: StageEvent[], id: string, start: number, end: number): { start: number; end: number } {
  const me = events.find((e) => e.id === id);
  let s = Math.max(0, start);
  let en = end;
  if (me) {
    for (const o of events) {
      if (o.id === id || o.kind !== me.kind) continue;
      if (o.end <= me.start && s < o.end) s = o.end; // neighbour on the left
      if (o.start >= me.end && en > o.start) en = o.start; // neighbour on the right
    }
  }
  if (en - s < MIN_EVENT_S) {
    // Whichever edge moved is the one that stops short of the other.
    if (me && start !== me.start) s = en - MIN_EVENT_S;
    else en = s + MIN_EVENT_S;
  }
  return { start: s, end: en };
}

export function snapTime(t: number, targets: number[], toleranceS: number): number {
  let best = t;
  let bestD = toleranceS;
  for (const x of targets) {
    const d = Math.abs(x - t);
    if (d <= bestD) {
      best = x;
      bestD = d;
    }
  }
  return best;
}

export function timeFromX(x: number, width: number, duration: number): number {
  if (width <= 0 || duration <= 0) return 0;
  return Math.min(Math.max(x / width, 0), 1) * duration;
}
```

The `clampToLane` inversion branch needs care with the test's last two expectations: `clampToLane(events, "evt-1", 3.5, 3)` -- `start` moved (3.5 != 1) so `s = 3 - 0.05`; `clampToLane(events, "evt-1", 1, 0.5)` -- `start` unchanged, so `en = 1 + 0.05`. Floating comparison: write the expectations with `toBeCloseTo` if `toEqual` trips on `2.95`.

- [ ] **Step 5: Run, typecheck, lint**

Run: `pnpm test -- src/lib/events.test.ts && pnpm typecheck && pnpm lint`
Expected: green.

- [ ] **Step 6: Mutation drill**

In `enclosingMovement` change `m.end > best.end` to `<`: the `two_movements...` fixture case and the helper test fail. Restore.

- [ ] **Step 7: Commit**

```bash
git add src/lib/api.ts src/lib/events.ts src/lib/events.test.ts
git commit -m "feat(spa): stage event types, putStageEvents, and lib/events mirroring events.py"
```

---

### Task 7: `LaneEditor` component

**Files:**
- Create: `src/splitsmith/ui_static/src/components/coach/LaneEditor.tsx`
- Create: `src/splitsmith/ui_static/src/components/coach/LaneEditor.test.tsx`

**Interfaces:**
- Consumes: `StageEvent`, `StageEventKind` (api.ts); `clampToLane`, `snapTime`, `timeFromX`, `nextEventId`, `enclosingMovement`, `MIN_EVENT_S` (lib/events.ts); `Label`, `Button`, `Kbd` from `components/ui`; `cn` from `@/lib/utils`; `MoreHorizontal` from lucide.
- Produces:
  ```ts
  export interface LaneEditorProps {
    shots: { shot_number: number; time_from_beep: number }[];
    events: StageEvent[];
    /** Right edge of the strip, seconds from beep (stage time, or last shot + 1 when unknown). */
    stageTime: number;
    /** Frames per second for nudges; 30 when unknown. */
    fps?: number;
    /** Playhead, seconds from beep. */
    currentTime: number;
    selectedId: string | null;
    readOnly?: boolean;
    onSelect: (id: string | null) => void;
    onSeek: (tFromBeep: number) => void;
    /** Live during a drag (commit=false), once on release / per nudge (commit=true). */
    onChange: (events: StageEvent[], commit: boolean) => void;
    /** Optional overflow-menu slot rendered at the strip's top right. */
    menu?: ReactNode;
  }
  export const LANES: readonly StageEventKind[] = ["movement", "reload", "activation"];
  export const SNAP_PX = 8; export const DRAG_THRESHOLD_PX = 6; export const TOUCH_DRAG_THRESHOLD_PX = 10;
  ```
  Test ids: `lane-editor` (root), `lane-ruler`, `lane-<kind>` (each editable lane), `event-<id>` (region body), `handle-<id>-start`, `handle-<id>-end`, `playhead`.

- [ ] **Step 1: Write the failing tests**

`LaneEditor.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StageEvent } from "@/lib/api";

import { LaneEditor } from "./LaneEditor";

const WIDTH = 1000;
const STAGE = 10; // 100 px per second
const SHOTS = [1, 2, 3, 6, 7].map((t, i) => ({ shot_number: i + 1, time_from_beep: t }));
const ev = (id: string, kind: StageEvent["kind"], start: number, end: number, source: StageEvent["source"] = "manual"): StageEvent =>
  ({ id, kind, start, end, source });

function Harness(props: Partial<React.ComponentProps<typeof LaneEditor>> & { initial?: StageEvent[] }) {
  const [events, setEvents] = React.useState<StageEvent[]>(props.initial ?? []);
  const [selected, setSelected] = React.useState<string | null>(props.selectedId ?? null);
  return (
    <LaneEditor
      shots={SHOTS}
      events={events}
      stageTime={STAGE}
      fps={50}
      currentTime={0}
      selectedId={selected}
      onSelect={setSelected}
      onSeek={props.onSeek ?? vi.fn()}
      onChange={(next, commit) => {
        setEvents(next);
        props.onChange?.(next, commit);
      }}
      readOnly={props.readOnly}
    />
  );
}
import React from "react";

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: WIDTH, height: 32, left: 0, top: 0, right: WIDTH, bottom: 32, x: 0, y: 0, toJSON: () => ({}),
  });
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
  HTMLElement.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => vi.restoreAllMocks());

const lastCommit = (onChange: ReturnType<typeof vi.fn>) =>
  [...onChange.mock.calls].reverse().find((c) => c[1] === true)?.[0] as StageEvent[] | undefined;

describe("LaneEditor", () => {
  it("creates a region by dragging empty lane space and seeks the moving edge", () => {
    const onChange = vi.fn();
    const onSeek = vi.fn();
    render(<Harness onChange={onChange} onSeek={onSeek} />);
    const lane = screen.getByTestId("lane-reload");
    fireEvent.pointerDown(lane, { pointerId: 1, clientX: 400, clientY: 10, button: 0 });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 450, clientY: 10 });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 540, clientY: 10 });
    expect(onSeek).toHaveBeenLastCalledWith(expect.closeTo(5.4, 2));
    fireEvent.pointerUp(lane, { pointerId: 1, clientX: 540, clientY: 10 });
    const committed = lastCommit(onChange);
    expect(committed).toHaveLength(1);
    expect(committed![0]).toMatchObject({ id: "evt-1", kind: "reload", source: "manual" });
    expect(committed![0].start).toBeCloseTo(4.0, 2);
    expect(committed![0].end).toBeCloseTo(5.4, 2);
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("aria-selected", "true");
  });

  it("a press under the threshold is a click that seeks and does not create", () => {
    const onChange = vi.fn();
    const onSeek = vi.fn();
    render(<Harness onChange={onChange} onSeek={onSeek} />);
    const lane = screen.getByTestId("lane-movement");
    fireEvent.pointerDown(lane, { pointerId: 1, clientX: 300, clientY: 10, button: 0 });
    fireEvent.pointerMove(lane, { pointerId: 1, clientX: 303, clientY: 10 });
    fireEvent.pointerUp(lane, { pointerId: 1, clientX: 303, clientY: 10 });
    expect(onChange).not.toHaveBeenCalled();
    expect(onSeek).toHaveBeenCalledWith(expect.closeTo(3.0, 2));
  });

  it("dragging the end handle resizes, seeks to the handle and snaps to a shot", () => {
    const onChange = vi.fn();
    const onSeek = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} onSeek={onSeek} />);
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 2, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 2, clientX: 596, clientY: 10 }); // 5.96 s, 4 px from shot at 6.0
    expect(onSeek).toHaveBeenLastCalledWith(expect.closeTo(6.0, 2));
    fireEvent.pointerUp(handle, { pointerId: 2, clientX: 596, clientY: 10 });
    expect(lastCommit(onChange)![0].end).toBeCloseTo(6.0, 3);
  });

  it("Alt skips the snap", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} />);
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 2, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 2, clientX: 596, clientY: 10, altKey: true });
    fireEvent.pointerUp(handle, { pointerId: 2, clientX: 596, clientY: 10, altKey: true });
    expect(lastCommit(onChange)![0].end).toBeCloseTo(5.96, 3);
  });

  it("clamps against a same-lane neighbour and never crosses its own other edge", () => {
    const onChange = vi.fn();
    render(
      <Harness initial={[ev("evt-1", "movement", 1, 3), ev("evt-2", "movement", 4, 6)]} selectedId="evt-1" onChange={onChange} />,
    );
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 3, clientX: 300, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 3, clientX: 550, clientY: 10, altKey: true });
    fireEvent.pointerUp(handle, { pointerId: 3, clientX: 550, clientY: 10, altKey: true });
    expect(lastCommit(onChange)![0].end).toBeCloseTo(4.0, 3);

    const start = screen.getByTestId("handle-evt-1-start");
    fireEvent.pointerDown(start, { pointerId: 4, clientX: 100, clientY: 10, button: 0 });
    fireEvent.pointerMove(start, { pointerId: 4, clientX: 900, clientY: 10, altKey: true });
    fireEvent.pointerUp(start, { pointerId: 4, clientX: 900, clientY: 10, altKey: true });
    const e = lastCommit(onChange)![0];
    expect(e.end - e.start).toBeCloseTo(0.05, 3);
    expect(e.end).toBeCloseTo(4.0, 3);
  });

  it("dragging the body moves the region and seeks the leading edge", () => {
    const onChange = vi.fn();
    const onSeek = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} onSeek={onSeek} />);
    const body = screen.getByTestId("event-evt-1");
    fireEvent.pointerDown(body, { pointerId: 5, clientX: 450, clientY: 10, button: 0 });
    fireEvent.pointerMove(body, { pointerId: 5, clientX: 480, clientY: 10, altKey: true });
    fireEvent.pointerUp(body, { pointerId: 5, clientX: 480, clientY: 10, altKey: true });
    const e = lastCommit(onChange)![0];
    expect(e.start).toBeCloseTo(4.3, 3);
    expect(e.end).toBeCloseTo(5.3, 3);
    expect(onSeek).toHaveBeenLastCalledWith(expect.closeTo(4.3, 2));
  });

  it("Escape restores the pre-drag region and commits nothing new", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} />);
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 6, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 6, clientX: 700, clientY: 10, altKey: true });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChange.mock.calls.filter((c) => c[1] === true)).toHaveLength(0);
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-end", "5");
  });

  it("arrow keys nudge by a frame, Shift moves the end, Alt moves 100 ms, Delete removes", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5)]} selectedId="evt-1" onChange={onChange} />);
    const root = screen.getByTestId("lane-editor");
    root.focus();
    fireEvent.keyDown(root, { key: "ArrowRight" });
    expect(lastCommit(onChange)![0].start).toBeCloseTo(4.02, 3); // 1/50 s
    fireEvent.keyDown(root, { key: "ArrowLeft", shiftKey: true });
    expect(lastCommit(onChange)![0].end).toBeCloseTo(4.98, 3);
    fireEvent.keyDown(root, { key: "ArrowRight", altKey: true });
    expect(lastCommit(onChange)![0].start).toBeCloseTo(4.12, 3);
    fireEvent.keyDown(root, { key: "Delete" });
    expect(lastCommit(onChange)).toEqual([]);
  });

  it("renders auto proposals dashed and marks a touched one manual", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "reload", 4, 5, "auto")]} selectedId="evt-1" onChange={onChange} />);
    expect(screen.getByTestId("event-evt-1")).toHaveAttribute("data-source", "auto");
    const handle = screen.getByTestId("handle-evt-1-end");
    fireEvent.pointerDown(handle, { pointerId: 7, clientX: 500, clientY: 10, button: 0 });
    fireEvent.pointerMove(handle, { pointerId: 7, clientX: 540, clientY: 10, altKey: true });
    fireEvent.pointerUp(handle, { pointerId: 7, clientX: 540, clientY: 10, altKey: true });
    expect(lastCommit(onChange)![0].source).toBe("manual");
  });

  it("shots inside a movement get the moving cap; read-only renders no handles and ignores drags", () => {
    const onChange = vi.fn();
    render(<Harness initial={[ev("evt-1", "movement", 1.5, 3.5)]} readOnly onChange={onChange} />);
    expect(screen.getByTestId("shot-2")).toHaveAttribute("data-moving", "true");
    expect(screen.getByTestId("shot-1")).toHaveAttribute("data-moving", "false");
    expect(screen.queryByTestId("handle-evt-1-end")).toBeNull();
    const lane = screen.getByTestId("lane-reload");
    fireEvent.pointerDown(lane, { pointerId: 8, clientX: 400, clientY: 10, button: 0 });
    fireEvent.pointerMove(lane, { pointerId: 8, clientX: 600, clientY: 10 });
    fireEvent.pointerUp(lane, { pointerId: 8, clientX: 600, clientY: 10 });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("clicking the ruler seeks and draws the playhead at currentTime", () => {
    const onSeek = vi.fn();
    render(<Harness onSeek={onSeek} />);
    fireEvent.click(screen.getByTestId("lane-ruler"), { clientX: 250 });
    expect(onSeek).toHaveBeenCalledWith(expect.closeTo(2.5, 2));
    expect(screen.getByTestId("playhead")).toHaveStyle({ left: "0%" });
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm test -- src/components/coach/LaneEditor.test.tsx`
Expected: module not found.

- [ ] **Step 3: Write the component**

`LaneEditor.tsx`. Structure and the rules that matter; fill the JSX from this skeleton, keeping every `data-testid` the tests name.

```tsx
/**
 * Lanes under the Coach video: shots (read-only) plus movement, reload and
 * activation regions, edited by pointer (spec 2026-10-08). Pointer rules
 * follow MarkerLayer: pointer capture, a travel threshold before a press
 * becomes a drag (wider for touch), Esc restores the pre-drag state. While
 * an edge drags the video seeks to it -- the frame under the cursor is what
 * is being marked. Geometry lives in lib/events.ts; this file owns the DOM.
 */
import type { ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Label } from "@/components/ui/Label";
import type { StageEvent, StageEventKind } from "@/lib/api";
import { clampToLane, enclosingMovement, nextEventId, shotIsMoving, snapTime, timeFromX } from "@/lib/events";
import { cn } from "@/lib/utils";

export const LANES: readonly StageEventKind[] = ["movement", "reload", "activation"];
export const SNAP_PX = 8;
export const DRAG_THRESHOLD_PX = 6;
export const TOUCH_DRAG_THRESHOLD_PX = 10;

const LANE_LABEL: Record<StageEventKind, string> = { movement: "Movement", reload: "Reload", activation: "Activation" };
// Budget hues (Chip TICK): movement beep, reload live, activation ink-2.
const LANE_FILL: Record<StageEventKind, string> = {
  movement: "bg-beep/30 border-beep",
  reload: "bg-live/30 border-live",
  activation: "bg-ink-2/20 border-ink-2",
};
const HANDLE_FILL: Record<StageEventKind, string> = { movement: "bg-beep", reload: "bg-live", activation: "bg-ink-2" };

export interface LaneEditorProps { /* as in Interfaces */ }

type Drag =
  | { mode: "create"; kind: StageEventKind; pointerId: number; startX: number; startY: number; thresholdPx: number; moved: boolean; anchorT: number; id: string | null }
  | { mode: "edge"; edge: "start" | "end"; id: string; pointerId: number; startX: number; startY: number; thresholdPx: number; moved: boolean; before: StageEvent }
  | { mode: "body"; id: string; pointerId: number; startX: number; startY: number; thresholdPx: number; moved: boolean; before: StageEvent; grabOffsetT: number };

export function LaneEditor(props: LaneEditorProps) {
  const { shots, events, stageTime, fps = 30, currentTime, selectedId, readOnly = false, onSelect, onSeek, onChange, menu } = props;
  const stripRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const eventsRef = useRef(events);
  eventsRef.current = events;
  const [, force] = useState(0);

  const duration = Math.max(stageTime, 0.001);
  const pct = (t: number) => `${(Math.min(Math.max(t, 0), duration) / duration) * 100}%`;
  const widthPx = () => stripRef.current?.getBoundingClientRect().width ?? 0;
  const tAt = (clientX: number) => {
    const rect = stripRef.current?.getBoundingClientRect();
    return rect ? timeFromX(clientX - rect.left, rect.width, duration) : 0;
  };
  const snapTargets = () => [0, ...shots.map((s) => s.time_from_beep)];
  const maybeSnap = (t: number, alt: boolean) => (alt ? t : snapTime(t, snapTargets(), (SNAP_PX / Math.max(widthPx(), 1)) * duration));
  const touched = (e: StageEvent): StageEvent => (e.source === "auto" ? { ...e, source: "manual" } : e);

  // ... pointer handlers: beginDrag(e, drag) sets pointer capture (guard: `el.setPointerCapture?.(e.pointerId)`), stores drag, calls onSelect for edge/body.
  // onMove: threshold check as MarkerLayer; then per mode:
  //   create: t = maybeSnap(tAt(x), alt); [s, en] = sorted(anchorT, t); if en - s < MIN_EVENT_S skip; on first move mint id = nextEventId(eventsRef.current) and append {id, kind, start, end, source:"manual"}; else update; onChange(next, false); onSeek(t)
  //   edge:   t = maybeSnap(tAt(x), alt); {start,end} = clampToLane(next, id, edge==="start"? t : before.start, edge==="end"? t : before.end); write touched(event); onChange(next,false); onSeek(edge==="start"? start : end)
  //   body:   s = tAt(x) - grabOffsetT (no snap for the body); len = before.end - before.start; {start,end} = clampToLane(next, id, s, s+len) then if end-start < len (a neighbour clamped) keep the clamped edge and derive the other from len; onChange(next,false); onSeek(start)
  // onUp: release capture; if !moved: mode create -> onSeek(tAt(x)); mode edge/body -> onSelect(id); return. else onChange(eventsRef.current, true); for create onSelect(newId).
  // Esc (window keydown while dragging): restore `before` (edge/body) or remove the created id; onChange(restored, false); drop drag. No commit.
  // Keyboard on root (tabIndex 0, onKeyDown), only with selectedId and !readOnly:
  //   ArrowLeft/Right: step = altKey ? 0.1 : 1/fps; dir = key==="ArrowLeft" ? -1 : 1; shiftKey ? end += dir*step : start += dir*step; clampToLane; touched; onChange(next, true)
  //   Delete/Backspace: onChange(events.filter(e => e.id !== selectedId), true); onSelect(null)
  // Ruler onClick: onSeek(tAt(clientX)).
  // Render:
  //   root <div data-testid="lane-editor" tabIndex={0} className="rounded-[10px] border border-rule bg-surface-2 outline-none focus-visible:ring-2 focus-visible:ring-led">
  //     header row: <Label>Lanes</Label> ... {menu}
  //     <div ref={stripRef} className="relative mx-3 my-2"> (the shared x-axis for every lane)
  //       ruler <div data-testid="lane-ruler" className="relative h-5 border-b border-rule"> with 1 s ticks (numeral text-xs at every 2 s, skipped within 0.9 s of the end), "BEEP" at 0 in text-beep
  //       shots lane: <div className="relative h-8"> each shot <span data-testid={`shot-${n}`} data-moving={shotIsMoving(t, events)} className="absolute top-1.5 bottom-1.5 w-px bg-done" style={{left: pct(t)}}> plus a 5 px hollow circle (border-beep) at the top when moving
  //       LANES.map(kind => <div data-testid={`lane-${kind}`} className="relative h-9 border-t border-rule/60" onPointerDown={readOnly ? undefined : (e) => startCreate(e, kind)} ...>
  //           lane label at left as <Label className="absolute left-0">...</Label> is NOT inside the strip (labels go in a gutter column to the left of stripRef so x maps cleanly) -- lay out as a two-column grid: a 5.5rem gutter of Labels and the strip.
  //           events of this kind: <div role="option" aria-selected={selectedId===e.id} data-testid={`event-${e.id}`} data-source={e.source} data-start={e.start} data-end={e.end}
  //                onClick={() => onSelect(e.id)}   (a plain click selects too -- the page tests click; after a drag the click re-selects the same id, harmless)
  //                className={cn("absolute top-1.5 bottom-1.5 rounded border", LANE_FILL[kind], e.source==="auto" && "border-dashed bg-transparent", selectedId===e.id && "ring-1 ring-ink")}
  //                style={{left: pct(e.start), width: pct(e.end - e.start)}} onPointerDown={readOnly ? undefined : (ev) => startBody(ev, e)} ...>
  //                {!readOnly && <span data-testid={`handle-${e.id}-start`} className={cn("absolute -left-1 top-1/2 h-4 w-2 -translate-y-1/2 rounded-sm", HANDLE_FILL[kind])} onPointerDown={(ev)=>startEdge(ev,e,"start")} .../>}
  //                {!readOnly && <span data-testid={`handle-${e.id}-end`} .../>}
  //                {kind==="reload" && selectedId===e.id && enclosingMovement(e, events) && <OverhangBracket .../>}  (a dashed line from the movement's end to the reload's end with the signed numeral)
  //       playhead: <div data-testid="playhead" className="pointer-events-none absolute inset-y-0 w-px bg-led" style={{left: pct(currentTime)}} />
  //     hint row (hidden on readOnly): "Drag empty lane to add" / "Drag edge to resize, body to move" / <Kbd>Arrows</Kbd> nudge a frame, <Kbd>Shift</Kbd> end, <Kbd>Alt</Kbd> 100 ms / <Kbd>Alt</Kbd>-drag skips snap, <Kbd>Esc</Kbd> cancels, <Kbd>Del</Kbd> removes
}
```

Handle events on the handle elements must `e.stopPropagation()` so the body and lane handlers do not also start. The `pointermove`/`pointerup` listeners are attached to the same element that captured (`e.currentTarget`), as `MarkerLayer` does; jsdom delivers `fireEvent` to the element regardless of capture, which is why the tests fire on the element. The ruler's label placement rule (skip the last numeric label when within 0.9 s of `stageTime`) is what keeps `16` from colliding with `16.20`.

- [ ] **Step 4: Run the tests; iterate until green**

Run: `pnpm test -- src/components/coach/LaneEditor.test.tsx`
Expected: all pass. Then `pnpm typecheck && pnpm lint`. Lint will reject any `text-[Npx]`; use `text-xs`/`text-sm`.

- [ ] **Step 5: Mutation drill**

Remove the `clampToLane` call in the edge handler: the clamp test fails. Remove the threshold check: the click-under-threshold test fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/components/coach/LaneEditor.tsx src/components/coach/LaneEditor.test.tsx
git commit -m "feat(coach): LaneEditor -- regions in lanes with drag, snap, clamp, nudge"
```

---

### Task 8: `EventCard`, `EventList`, and the Coach page wiring

**Files:**
- Create: `src/splitsmith/ui_static/src/components/coach/EventCard.tsx`
- Create: `src/splitsmith/ui_static/src/components/coach/EventList.tsx`
- Modify: `src/splitsmith/ui_static/src/components/coach/Coach.components.test.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/Coach.tsx` (`CoachStageInner`, 729-1058), `src/splitsmith/ui_static/src/pages/Coach.test.tsx`

**Interfaces:**
- Consumes: `LaneEditor` (Task 7); `reloadFigures`, `enclosingMovement`, `validateLanes` (Task 6); `api.putStageEvents`, `CoachStageResponse.events/_version/event_summary` (Task 6); `useIsMobile` (`lib/useIsMobile.ts`); `Segmented`, `Button`, `Chip`, `Label`, `Stat` from `components/ui`.
- Produces:
  ```ts
  export interface EventCardProps {
    event: StageEvent; events: StageEvent[];
    onKind: (kind: StageEventKind) => void; onDelete: () => void; onDone: () => void;
  }
  export interface EventListProps { events: StageEvent[]; shots: { time_from_beep: number }[] }
  ```
  Coach page state: `const [events, setEvents] = useState<StageEvent[]>([])`, `const [selectedEventId, setSelectedEventId] = useState<string | null>(null)`, `const versionRef = useRef<string | undefined>()` (the `_version`), `commitEvents(next)` -> `api.putStageEvents(...)`, on `ApiError` 409 reload via `api.getStageCoach`.

- [ ] **Step 1: Write the failing component tests**

Append to `Coach.components.test.tsx`:

```tsx
import type { StageEvent } from "@/lib/api";
import { EventCard } from "./EventCard";
import { EventList } from "./EventList";

const E = (id: string, kind: StageEvent["kind"], start: number, end: number, source: StageEvent["source"] = "manual"): StageEvent =>
  ({ id, kind, start, end, source });

describe("EventCard", () => {
  const events = [E("evt-1", "movement", 7.6, 9.16), E("evt-2", "reload", 8.05, 9.47)];

  it("shows start, end, duration, the enclosing movement and the overhang for a reload", () => {
    render(<EventCard event={events[1]} events={events} onKind={vi.fn()} onDelete={vi.fn()} onDone={vi.fn()} />);
    const card = screen.getByRole("region", { name: "Region" });
    expect(within(card).getByText("8.05")).toBeInTheDocument();
    expect(within(card).getByText("9.47")).toBeInTheDocument();
    expect(within(card).getByText("1.42")).toBeInTheDocument();
    expect(within(card).getByText(/Movement 7\.60.9\.16/)).toBeInTheDocument();
    expect(within(card).getByText("+0.31")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Reload" })).toHaveAttribute("aria-pressed", "true");
  });

  it("a standing reload shows no overhang row; a movement shows neither", () => {
    const standing = [E("evt-2", "reload", 8.05, 9.47)];
    const { rerender } = render(<EventCard event={standing[0]} events={standing} onKind={vi.fn()} onDelete={vi.fn()} onDone={vi.fn()} />);
    expect(screen.queryByText("Overhang")).toBeNull();
    expect(screen.getByText("Standing")).toBeInTheDocument();
    rerender(<EventCard event={events[0]} events={events} onKind={vi.fn()} onDelete={vi.fn()} onDone={vi.fn()} />);
    expect(screen.queryByText("Overhang")).toBeNull();
    expect(screen.queryByText("During")).toBeNull();
  });

  it("changes kind, deletes and closes", () => {
    const onKind = vi.fn(); const onDelete = vi.fn(); const onDone = vi.fn();
    render(<EventCard event={events[1]} events={events} onKind={onKind} onDelete={onDelete} onDone={onDone} />);
    fireEvent.click(screen.getByRole("button", { name: "Activation" }));
    expect(onKind).toHaveBeenCalledWith("activation");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onDone).toHaveBeenCalled();
  });

  it("names an auto proposal as such", () => {
    const auto = [E("evt-3", "reload", 13.3, 15.9, "auto")];
    render(<EventCard event={auto[0]} events={auto} onKind={vi.fn()} onDelete={vi.fn()} onDone={vi.fn()} />);
    expect(screen.getByText("Proposed")).toBeInTheDocument();
  });
});

describe("EventList", () => {
  it("lists one row per region with range or duration, moving-shot count and overhang", () => {
    const events = [E("evt-1", "movement", 3.4, 6.1), E("evt-2", "movement", 7.6, 9.16), E("evt-3", "reload", 8.05, 9.47)];
    const shots = [4.35, 4.71, 5.12, 5.48, 10.6].map((t) => ({ time_from_beep: t }));
    render(<EventList events={events} shots={shots} />);
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent(/3\.40.6\.10/);
    expect(rows[0]).toHaveTextContent("4 shots");
    expect(rows[2]).toHaveTextContent("1.42");
    expect(rows[2]).toHaveTextContent("+0.31");
  });

  it("renders nothing for an empty list", () => {
    const { container } = render(<EventList events={[]} shots={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: Write the failing page tests**

Append to `pages/Coach.test.tsx` (its mock block needs `putStageEvents: vi.fn()` added to the mocked `api`; `makeCoach` gains `events` and `_version` arguments):

```tsx
function makeCoachWithEvents(shots: CoachShot[], events: StageEvent[], version = "aaaaaaaaaaaaaaaa"): CoachStageResponse {
  return { ...makeCoach(shots), events, _version: version,
    event_summary: { movement_s: 0, moving_shots: 0, reloads: events.filter((e) => e.kind === "reload").length,
      reload_avg_s: null, overhang_s: 0.31, capacity_warning: null } };
}

describe("stage events on the Coach page", () => {
  beforeEach(() => {
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
    HTMLElement.prototype.releasePointerCapture = vi.fn();
  });

  it("renders the lane editor with the payload's events and the stat strip figures", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(
      makeCoachWithEvents([makeShot(1, "c1"), makeShot(2, "c2")], [{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }]),
    );
    render(<MemoryRouter initialEntries={["/match/m1/coach/anna/1"]}><Routes>
      <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} /></Routes></MemoryRouter>);
    expect(await screen.findByTestId("event-evt-1")).toHaveAttribute("data-source", "auto");
    expect(screen.getByText("Overhang")).toBeInTheDocument();
    expect(screen.getByText("+0.31")).toBeInTheDocument();
  });

  it("deleting the selected region PUTs the list with _version and applies the response", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    const first = makeCoachWithEvents([makeShot(1, "c1")], [{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }], "v1v1v1v1v1v1v1v1");
    vi.mocked(api.getStageCoach).mockResolvedValue(first);
    vi.mocked(api.putStageEvents).mockResolvedValue(makeCoachWithEvents([makeShot(1, "c1")], [], "v2v2v2v2v2v2v2v2"));
    render(<MemoryRouter initialEntries={["/match/m1/coach/anna/1"]}><Routes>
      <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} /></Routes></MemoryRouter>);
    fireEvent.click(await screen.findByTestId("event-evt-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [], "v1v1v1v1v1v1v1v1"));
    await waitFor(() => expect(screen.queryByTestId("event-evt-1")).toBeNull());
  });

  it("a 409 on the PUT reloads the coach payload", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ name: "M", competitor_name: "Anna",
      stages: [{ stage_number: 1, stage_name: "Stage One", time_seconds: 16.2 }] } as never);
    const stale = makeCoachWithEvents([makeShot(1, "c1")], [{ id: "evt-1", kind: "reload", start: 8.05, end: 9.47, source: "auto" }], "old");
    const fresh = makeCoachWithEvents([makeShot(1, "c1")], [{ id: "evt-2", kind: "movement", start: 1, end: 2, source: "manual" }], "new");
    vi.mocked(api.getStageCoach).mockResolvedValueOnce(stale).mockResolvedValueOnce(fresh);
    vi.mocked(api.putStageEvents).mockRejectedValue(new ApiError(409, "version_conflict", { code: "version_conflict" }));
    render(<MemoryRouter initialEntries={["/match/m1/coach/anna/1"]}><Routes>
      <Route path="/match/:matchId/coach/:slug/:stage" element={<Coach />} /></Routes></MemoryRouter>);
    fireEvent.click(await screen.findByTestId("event-evt-1"));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(await screen.findByTestId("event-evt-2")).toBeInTheDocument();
    expect(api.getStageCoach).toHaveBeenCalledTimes(2);
  });
});
```

Check `ApiError`'s constructor signature in `api.ts` (`new ApiError(status, detail, rawDetail)`) and adjust the third argument's shape to whatever `request()` passes. `waitFor` comes from `@testing-library/react`.

- [ ] **Step 3: Run to verify they fail**

Run: `pnpm test -- src/components/coach/Coach.components.test.tsx src/pages/Coach.test.tsx`
Expected: `EventCard`/`EventList` unresolved; page tests time out finding `event-evt-1`.

- [ ] **Step 4: Write `EventCard.tsx`**

```tsx
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import { Segmented } from "@/components/ui/Segmented";
import type { StageEvent, StageEventKind } from "@/lib/api";
import { enclosingMovement } from "@/lib/events";

const KIND_OPTIONS = [
  { value: "movement", label: "Movement", tick: "movement" },
  { value: "reload", label: "Reload", tick: "reload" },
  { value: "activation", label: "Activation", tick: "activation" },
] as const;

export interface EventCardProps {
  event: StageEvent;
  events: StageEvent[];
  onKind: (kind: StageEventKind) => void;
  onDelete: () => void;
  onDone: () => void;
}

const f2 = (x: number) => x.toFixed(2);

/** The selected region. Replaces ShotEditor while a region is selected. */
export function EventCard({ event, events, onKind, onDelete, onDone }: EventCardProps) {
  const during = event.kind === "reload" ? enclosingMovement(event, events) : null;
  const overhang = during ? event.end - during.end : null;
  return (
    <section aria-label="Region" className="rounded-[10px] border border-rule bg-surface px-3.5 py-3">
      <div className="flex items-center justify-between gap-3">
        <Label>Region</Label>
        <Segmented value={event.kind} options={KIND_OPTIONS} onChange={onKind} label="Region kind" />
      </div>
      <dl className="mt-3 divide-y divide-rule">
        <Row k="Start"><span className="numeral text-lg">{f2(event.start)}</span></Row>
        <Row k="End"><span className="numeral text-lg">{f2(event.end)}</span></Row>
        <Row k="Duration"><span className="numeral text-lg">{f2(event.end - event.start)}</span></Row>
        {event.kind === "reload" ? (
          <Row k="During">
            {during ? (
              <Chip tick="movement">Movement {f2(during.start)}&ndash;{f2(during.end)}</Chip>
            ) : (
              <span className="text-md text-muted">Standing</span>
            )}
          </Row>
        ) : null}
        {overhang !== null ? (
          <Row k="Overhang">
            <span className="numeral text-lg text-live">{overhang >= 0 ? "+" : ""}{f2(overhang)}</span>
          </Row>
        ) : null}
        <Row k="Source">
          <Chip tick={event.source === "auto" ? "muted" : "transition"}>{event.source === "auto" ? "Proposed" : "Manual"}</Chip>
        </Row>
      </dl>
      <div className="mt-3 flex items-center justify-end gap-2">
        <Button size="sm" variant="destructive" onClick={onDelete}>Delete</Button>
        <Button size="sm" onClick={onDone}>Done</Button>
      </div>
    </section>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2">
      <dt className="text-md text-muted">{k}</dt>
      <dd className="m-0">{children}</dd>
    </div>
  );
}
```

`Segmented` renders each option as a button with `aria-pressed`, which is what the test's `getByRole("button", { name: "Reload" })` reads. If `Segmented`'s `tick` type is `ChipTick`, the `as const` tuple satisfies it.

- [ ] **Step 5: Write `EventList.tsx`**

```tsx
import { Chip } from "@/components/ui/Chip";
import type { StageEvent } from "@/lib/api";
import { reloadFigures } from "@/lib/events";

export interface EventListProps {
  events: StageEvent[];
  shots: { time_from_beep: number }[];
}

const f2 = (x: number) => x.toFixed(2);
const TICK = { movement: "movement", reload: "reload", activation: "activation" } as const;
const NAME = { movement: "Movement", reload: "Reload", activation: "Activation" } as const;

/** Phone read-only companion to the lanes: one hairline row per region. */
export function EventList({ events, shots }: EventListProps) {
  if (events.length === 0) return null;
  const figs = new Map(reloadFigures(events).map((f) => [f.eventId, f]));
  const sorted = [...events].sort((a, b) => a.start - b.start);
  return (
    <ul className="divide-y divide-rule">
      {sorted.map((e) => {
        const fig = figs.get(e.id);
        const inside = e.kind === "movement" ? shots.filter((s) => e.start <= s.time_from_beep && s.time_from_beep <= e.end).length : 0;
        return (
          <li key={e.id} className="flex items-baseline justify-between gap-3 py-2">
            <Chip tick={TICK[e.kind]}>{NAME[e.kind]}</Chip>
            <span className="numeral text-md text-ink-2">
              {fig ? (
                <>
                  {f2(fig.duration)}
                  {fig.overhang !== null ? <> &middot; <span className="text-live">{fig.overhang >= 0 ? "+" : ""}{f2(fig.overhang)}</span></> : null}
                </>
              ) : (
                <>
                  {f2(e.start)}&ndash;{f2(e.end)}
                  {inside > 0 ? <> &middot; {inside} shots</> : null}
                </>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
```

- [ ] **Step 6: Wire the Coach page**

In `CoachStageInner` (`pages/Coach.tsx`):

1. Imports: `LaneEditor`, `EventCard`, `EventList`, `useIsMobile`, `validateLanes`, `type StageEvent, type StageEventKind` from api.
2. State, next to `coach`:
   ```ts
   const [events, setEvents] = useState<StageEvent[]>([]);
   const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
   const revisionRef = useRef<string | undefined>(undefined);
   const pendingCommit = useRef<number | null>(null);
   const isMobile = useIsMobile();
   ```
   Extend `applyCoach` to also do `revisionRef.current = next?._version; setEvents(next?.events ?? []);` and drop a selection whose id is gone.
3. Commit:
   ```ts
   const commitEvents = useCallback(async (next: StageEvent[]) => {
     if (validateLanes(next)) return; // the editor clamps; this is belt and braces
     try {
       applyCoach(await api.putStageEvents(slug, stage, next, revisionRef.current));
     } catch (e) {
       if (e instanceof ApiError && e.status === 409) {
         applyCoach(await api.getStageCoach(slug, stage));
         return;
       }
       setError(e instanceof ApiError ? e.detail : String(e));
     }
   }, [applyCoach, slug, stage]);
   const onEventsChange = useCallback((next: StageEvent[], commit: boolean) => {
     setEvents(next);
     if (!commit) return;
     if (pendingCommit.current) window.clearTimeout(pendingCommit.current);
     pendingCommit.current = window.setTimeout(() => void commitEvents(next), 350); // a held arrow key is one PUT
   }, [commitEvents]);
   ```
   Clear the timeout on unmount. For the card's Delete and kind change call `onEventsChange(next, true)` with the edited list (kind change: `{...e, kind, source: "manual"}`; delete filters and `setSelectedEventId(null)`).
4. Time conversion: `const beep = coach.beep_time; const tFromBeep = currentTime - beep;` `onSeek={(t) => { if (videoRef.current) videoRef.current.currentTime = beep + t; }}`. `stageTime` = `project?.stages.find((s) => s.stage_number === stage)?.time_seconds ?? (last shot's time_from_beep + 1)`. `fps` stays the default 30 (the payload has no fps; a later change can add it).
5. Layout, inside the existing `<div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px] lg:items-start">`: after the video card and before `{activeShot ? <ShotEditor .../> : null}`, render
   ```tsx
   <LaneEditor shots={coach.shots} events={events} stageTime={stageTime} currentTime={tFromBeep}
     selectedId={selectedEventId} readOnly={isMobile} onSelect={setSelectedEventId}
     onSeek={seekFromBeep} onChange={onEventsChange} />
   {isMobile ? <EventList events={events} shots={coach.shots} /> : null}
   ```
   and make the editor slot `selectedEvent ? <EventCard .../> : activeShot ? <ShotEditor .../> : null` (one card level: the region card replaces the shot editor while a region is selected).
6. Stat strip: the page's `PageHeader sub` chips stay; add a `StatStrip` under the header (or extend an existing one if the page has it) with `Stat label="On the move" value={String(summary.moving_shots)} unit="shots"` and, when `summary.reloads > 0`, `Stat label="Overhang" value={signed(summary.overhang_s)} unit="s"` where `summary = coach.event_summary`. Render the `capacity_warning` as `<Chip tone="warn">{capacity_warning}</Chip>` in the `sub` when present. Use `Stat` from `components/ui` (its `value` is a string; render the overhang in `text-live` through `className`).

- [ ] **Step 7: Run everything SPA-side**

Run: `pnpm test -- src/components/coach src/pages/Coach.test.tsx && pnpm typecheck && pnpm lint`
Expected: green. If `useIsMobile` reads `matchMedia` (never matches in tests), `readOnly` is false in tests, which the page tests rely on.

- [ ] **Step 8: Mutation drill**

Remove the 409 branch: the 409 test fails. Remove `revisionRef.current` from the PUT call (pass `undefined`): the delete test's `toHaveBeenCalledWith(..., "v1v1...")` fails. Restore.

- [ ] **Step 9: Commit**

```bash
git add src/components/coach/EventCard.tsx src/components/coach/EventList.tsx src/components/coach/Coach.components.test.tsx src/pages/Coach.tsx src/pages/Coach.test.tsx
git commit -m "feat(coach): lane editor on the Coach page with the region card, phone list and stat figures"
```

---

### Task 9: Coach player on the scrub rendition, full-resolution toggle

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Coach.tsx`, `src/splitsmith/ui_static/src/pages/Coach.test.tsx`
- Modify: `src/splitsmith/ui_static/src/components/coach/LaneEditor.tsx` (the `menu` slot already exists; nothing to add)

**Interfaces:**
- Consumes: `useScrubSource()` -> `{ fullRes, available, setFullRes, choose, markFailed }` (`lib/useScrubSource.ts`); `CoachVideoEntry.trim_version/scrub_version` (Task 6); `Menu`, `menuItemClass` (`components/ui/Menu`); `api.videoStreamUrl(slug, path, kind, version, stage)`.
- Produces: the Coach `<video src>` is `kind=scrub&v=<scrub_version>` when the primary is `kind: "trim"` with a fresh rendition and full-res is off; `kind=trim&v=<trim_version>` when full-res is on or the rendition errored; `kind=source` untouched; `kind=web` untouched (hosted).

- [ ] **Step 1: Write the failing tests**

Append to `pages/Coach.test.tsx`. The mock block gains `getScrubSettings: vi.fn().mockResolvedValue({ full_res_scrub: false })` and `setScrubSettings: vi.fn().mockResolvedValue({ full_res_scrub: true })`. `useDeploymentMode` must report local: find how `Audit.test.tsx` makes it resolve (grep `useDeploymentMode` in `src/pages/Audit.test.tsx` and copy its mock of `@/lib/features`).

```tsx
describe("Coach player source", () => {
  const trimCoach = (overrides: Partial<CoachVideoEntry>): CoachStageResponse => ({
    ...makeCoach([makeShot(1, "c1")]),
    videos: [{ path: "trimmed/stage1.mp4", role: "primary", beep_in_clip: 5, kind: "trim", trim_version: "t1", scrub_version: "s1", ...overrides }],
  });

  it("streams the scrub rendition when the trim has a fresh one", async () => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({}));
    const { container } = renderRoute();
    await screen.findByTestId("lane-editor");
    expect(container.querySelector("video")?.getAttribute("src")).toContain("/scrub/");
  });

  it("falls back to the trim after the rendition errors", async () => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({}));
    const { container } = renderRoute();
    await screen.findByTestId("lane-editor");
    fireEvent.error(container.querySelector("video")!);
    await waitFor(() => expect(container.querySelector("video")?.getAttribute("src")).toContain("/trim/"));
  });

  it("a source-kind primary is left alone", async () => {
    // Review focus 5.
    vi.mocked(api.getProject).mockResolvedValue(PROJECT as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({ kind: "source", trim_version: null, scrub_version: null }));
    const { container } = renderRoute();
    await screen.findByTestId("lane-editor");
    expect(container.querySelector("video")?.getAttribute("src")).toContain("/source/");
  });

  it("the lane editor menu toggles full-resolution video through the scrub settings", async () => {
    vi.mocked(api.getProject).mockResolvedValue(PROJECT as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(trimCoach({}));
    const { container } = renderRoute();
    await screen.findByTestId("lane-editor");
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    fireEvent.click(await screen.findByRole("menuitemcheckbox", { name: /Full-resolution video/ }));
    expect(api.setScrubSettings).toHaveBeenCalledWith(true);
    await waitFor(() => expect(container.querySelector("video")?.getAttribute("src")).toContain("/trim/"));
  });
});
```

Define `PROJECT` and `renderRoute()` once at the top of the describe from the file's existing `renderCoachStage` pieces (the project with `time_seconds: 16.2`, the `MemoryRouter` render). The mocked `videoStreamUrl` in this file already puts the `kind` in the path, which is what `/scrub/` and `/trim/` match.

- [ ] **Step 2: Run to verify they fail**

Run: `pnpm test -- src/pages/Coach.test.tsx -t "Coach player source"`
Expected: `/scrub/` not found (the src still says `/trim/`), no "More" button.

- [ ] **Step 3: Implement**

In `CoachStageInner`:

```ts
const scrub = useScrubSource();
const primary = coach?.videos.find((v) => v.role === "primary") ?? null;
const streamUrl = useMemo(() => {
  if (!primary) return null;
  if (primary.kind !== "trim") return api.videoStreamUrl(slug, primary.path, primary.kind, null, stage);
  const choice = scrub.choose({ path: primary.path, trim_version: primary.trim_version, scrub_version: primary.scrub_version });
  return api.videoStreamUrl(slug, primary.path, choice.kind, choice.version, stage);
}, [primary, scrub, slug, stage]);
```

(`scrub.choose` is a `useCallback` keyed on `fullRes` and `failed`, so the memo recomputes on a toggle or a failure.) On the `<video>` add `onError={() => { if (primary && streamUrl?.includes("kind=scrub")) scrub.markFailed(primary); }}` -- in the test the mocked URL has `/scrub/` not `kind=scrub`, so match on `choice.kind === "scrub"` kept in a ref or recompute: simplest is to compute `const playingScrub = primary?.kind === "trim" && scrub.choose({...}).kind === "scrub";` alongside and use it in `onError`.

The menu, passed to `LaneEditor`'s `menu` prop:

```tsx
menu={
  scrub.available ? (
    <span className="relative shrink-0">
      <Button type="button" size="icon" variant="ghost" aria-label="More" aria-haspopup="menu" aria-expanded={moreOpen} onClick={() => setMoreOpen((v) => !v)}>
        <MoreHorizontal className="size-4" aria-hidden />
      </Button>
      <Menu open={moreOpen} onClose={() => setMoreOpen(false)} align="right">
        <button type="button" role="menuitemcheckbox" aria-checked={scrub.fullRes} className={menuItemClass} onClick={() => scrub.setFullRes(!scrub.fullRes)}>
          Full-resolution video
          <span className="ml-auto text-sm text-muted">{scrub.fullRes ? "on" : "off"}</span>
        </button>
      </Menu>
    </span>
  ) : undefined
}
```

with `const [moreOpen, setMoreOpen] = useState(false);`. This is the same entry Audit's `TransportLine` renders, bound to the same preference, so flipping it in either place flips both.

- [ ] **Step 4: Run, typecheck, lint**

Run: `pnpm test -- src/pages/Coach.test.tsx && pnpm typecheck && pnpm lint`
Expected: green, including the earlier Coach tests (whose `makeCoach` video has no versions -> `choose` returns `trim` with `null` version -> `/trim/` as before, so `test ... src contains /trim/` still holds).

- [ ] **Step 5: Mutation drill**

Make `streamUrl` ignore `choice` and always pass `"trim"`: the scrub test fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/pages/Coach.tsx src/pages/Coach.test.tsx
git commit -m "feat(coach): Coach player streams the scrub rendition; full-resolution toggle on the lane editor"
```

---

### Task 10: Docs, demo seed, and the end-to-end look

**Files:**
- Modify: `CLAUDE.md` (a new `## Stage events (spec 2026-10-08)` section after "Share-link previews" or near the Coach notes), `SPEC.md` (module list: `events.py`)
- Modify: `scripts/seed_demo_match.py` -- the seeded audit docs get a `Production Optics` division on one shooter so the Coach page seeds a proposal (find where `competitor_division` or the project is written and set it)

- [ ] **Step 1: CLAUDE.md section**

Add, in the style of the existing sections (facts a future session needs and cannot derive):

```markdown
## Stage events (spec 2026-10-08)

Movement, reload and activation are **regions** (`events` on the stage
audit doc, `config.StageEvent`, seconds from beep), independent of shots:
a movement may span several shots, which the per-gap `interval_class`
cannot say. The two views coexist: the gap partition is still what the
time budget sums and `statistic_splits` filters on; the regions only
*hint* the auto-classifier (a gap over `transition_max_s` overlapping a
reload region auto-classes `reload`, `coach.gap_overlaps_reload`) and
never own a class. Every figure (per-shot `moving`, `reload_figures` with
the **overhang** = reload end minus the enclosing movement's end,
`stage_event_summary`) is derived, never stored, by `splitsmith/events.py`
and its TS twin `lib/events.ts`, which run `tests/fixtures/events/cases.json`
case for case -- a rule changes on both sides or not at all. A reload's
handles mean hand off the grip -> gun back on target.

Seeding (`events.seed_doc`) runs once per stage (`events_seeded`) on the
coach GET, reload only, never movement: every hinted gap
(`reload_hint_min_s`), or with a division capacity (`DivisionCapacityConfig`,
keyed on the SSI string so the power factor rides in the name) the first
hinted gap in a `capacity + 1`-shot window, else the longest. `capacity + 1`
is a bound, not a count: shooters start with one chambered. The seed is
persisted for owner reads only and never on a mirror -- `events` is a
desktop-owned field and `sync.merge.merge_audit_doc` keeps local's.

`PUT /api/shooters/{slug}/stages/{n}/events` replaces the list under
`_audit_rmw()` with the audit revision check (409 `version_conflict`; 422
`lane_overlap` names both ids), in `_REVIEW_ROUTES` and not on the share
surface. The coach payload carries `events`, `event_summary`, `_version`,
per-shot `moving` and per-video `trim_version` / `scrub_version`; the Coach
player goes through `useScrubSource` like Audit, and the lane editor's
"Full-resolution video" entry is the same `GlobalPrefs.full_res_scrub`.
`components/coach/LaneEditor` owns the DOM only; geometry (clamp, snap,
`MIN_EVENT_S`) is `lib/events.ts`. Arrows nudge (bracket keys sit behind
AltGr on Nordic layouts). Rendering, the summary card and CSV/FCPXML
markers are part 2 of the plan, not yet built.
```

- [ ] **Step 2: Demo seed and the look**

Set `competitor_division = "Production Optics"` on the first seeded shooter in `scripts/seed_demo_match.py`, then:

```bash
uv run python scripts/seed_demo_match.py ~/.claude-tmp/demo-match --media
SPLITSMITH_AUTO_SYNC=0 uv run splitsmith ui --project ~/.claude-tmp/demo-match --skip-system-check --no-browser --port 5174
```

Open the Coach page for stage 1 of the PO shooter in Playwright at 1280x900 and at 390x844, screenshot both, and look: the dashed proposal is on the reload lane, dragging its end moves the video's time readout, the region card shows the figures, the phone shows the read-only lanes and the list. Fix what is wrong before the commit; attach the two screenshots to the PR.

- [ ] **Step 3: Full suites and commit**

```bash
uv run pytest -q
cd src/splitsmith/ui_static && pnpm test && pnpm typecheck && pnpm lint && cd -
git add CLAUDE.md SPEC.md scripts/seed_demo_match.py
git commit -m "docs: stage events and the lane editor; demo match seeds a PO division"
```

---

## Self-review notes

- Spec coverage, part 1: model (T1), capacity + seeder (T2), classifier hint + reclassify (T3), coach GET (T4), PUT + capabilities + share 404 + lock (T5), SPA types/API/lib (T6), LaneEditor (T7), card/list/page/stat strip/409 (T8), scrub + toggle (T9), docs (T10). Deferred to part 2 by design: overlay slots, summary card, CSV/FCPXML, share figures, `lookGallery` tests, render frame scripts.
- Spec's `[ ] { }` nudges replaced by arrows (spec amended in the same commit as this plan).
- Review Focus 1-5 each have a named test: T3 `test_split_inside_a_reload_region_stays_split`; T1 fixture `event_past_last_shot_and_inclusive_ends`; T5 `test_put_events_with_no_shots_still_saves`; T6 `clampToLane ... never inverts` + T7 clamp test; T9 `a source-kind primary is left alone`.
- Names used across tasks: `events_from_doc`, `shot_times_from_doc`, `seed_doc(doc, *, hint_min_s, capacity)`, `capacity_for(division, config=None)`, `gap_overlaps_reload`, `classify_intervals_in_dicts(..., events=)`, `api.putStageEvents(slug, n, events, version)`, `LaneEditor` test ids `lane-editor`, `lane-ruler`, `lane-<kind>`, `event-<id>`, `handle-<id>-start|end`, `shot-<n>`, `playhead`.
