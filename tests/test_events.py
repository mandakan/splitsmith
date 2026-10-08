"""Pure stage-event figures (spec 2026-10-08). Mirrored case for case by
``ui_static/src/lib/events.test.ts`` over the same fixture file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from splitsmith.config import Config, DivisionCapacityConfig, StageEvent
from splitsmith.events import (
    capacity_config,
    capacity_for,
    events_from_doc,
    next_event_id,
    reload_figures,
    seed_doc,
    seed_events,
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


def test_stage_event_ignores_unknown_fields() -> None:
    """A doc a newer version wrote loads on an older one (the desktop app and
    the CLI share ``~/.splitsmith``); the events PUT's request model is what
    refuses an unknown key (``tests/test_coach_api.py``)."""
    event = StageEvent.model_validate(
        {"id": "evt-1", "kind": "reload", "start": 1.0, "end": 2.0, "source": "manual", "colour": "red"}
    )
    assert event.model_dump(exclude_none=True) == {
        "id": "evt-1",
        "kind": "reload",
        "start": 1.0,
        "end": 2.0,
        "source": "manual",
    }


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


def test_stage_event_summary_sorts_its_shot_times() -> None:
    """Parity of contract with ``lib/events.ts``: the capacity segmentation
    walks shots in time order, whatever order the caller passes them in."""
    reload = _events([{"id": "evt-1", "kind": "reload", "start": 1.0, "end": 2.0, "source": "manual"}])
    ordered = [0.1, 0.2, 0.3, 0.4, 5.0]
    shuffled = [5.0, 0.1, 0.2, 0.3, 0.4]
    assert stage_event_summary(ordered, reload, 2).capacity_warning == "4 shots without a reload"
    assert stage_event_summary(shuffled, reload, 2) == stage_event_summary(ordered, reload, 2)


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
    doc = {
        "shots": [{"shot_number": i + 1, "ms_after_beep": int(round(t * 1000))} for i, t in enumerate(times)]
    }
    assert seed_doc(doc, hint_min_s=2.5, capacity=15) is True
    assert doc["events_seeded"] is True
    assert [e["kind"] for e in doc["events"]] == ["reload"]
    assert "note" not in doc["events"][0]

    doc["events"] = []  # the user deleted the proposal
    assert seed_doc(doc, hint_min_s=2.5, capacity=15) is False
    assert doc["events"] == []
