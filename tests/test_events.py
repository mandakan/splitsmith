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
