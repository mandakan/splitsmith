"""Stage details (spec 2026-10-22): scoring rules, derived values and how a
scoreboard sync treats what the user typed."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from splitsmith import stage_details as sd
from splitsmith.match_project import MatchProject, StageEntry, StageScorecard


def _card(**kw: object) -> StageScorecard:
    return StageScorecard(**kw)  # type: ignore[arg-type]


def test_power_factor_from_division_and_override() -> None:
    assert sd.power_factor_for("Production Optics", None) == "minor"
    assert sd.power_factor_for("Classic Major", None) == "major"
    assert sd.power_factor_for("Standard MAJOR", None) == "major"
    assert sd.power_factor_for(None, None) == "minor"
    assert sd.power_factor_for("Production Optics", "major") == "major"
    assert sd.power_factor_for("Classic Major", "minor") == "minor"


@pytest.mark.parametrize(
    ("hits", "pf", "points"),
    [
        ({"alphas": 5, "charlies": 4}, "minor", 37.0),
        ({"alphas": 2, "charlies": 7, "misses": 1}, "minor", 21.0),
        ({"alphas": 2, "charlies": 7, "deltas": 1}, "minor", 32.0),
        ({"alphas": 2, "charlies": 7, "deltas": 1}, "major", 40.0),
        ({"alphas": 1, "misses": 2, "no_shoots": 1}, "minor", 0.0),
        ({"alphas": 10, "procedurals": 1}, "minor", 40.0),
    ],
)
def test_points_from_hits(hits: dict[str, int], pf: str, points: float) -> None:
    full = dict.fromkeys(sd.HIT_FIELDS, 0) | hits
    assert sd.points_from_hits(_card(**full), pf) == points  # type: ignore[arg-type]


def test_points_need_every_hit_field() -> None:
    assert sd.points_from_hits(_card(alphas=5, charlies=4), "minor") is None


def _hits(**kw: int) -> dict[str, int]:
    return dict.fromkeys(sd.HIT_FIELDS, 0) | kw


def test_derive_fills_points_and_hit_factor() -> None:
    card = sd.derive(_card(**_hits(alphas=5, charlies=4)), 12.68, "minor")
    assert card.stage_points == 37.0
    assert card.hit_factor == 2.918
    assert sorted(card.derived) == ["hit_factor", "stage_points"]


def test_derive_never_overwrites_a_typed_or_scoreboard_value() -> None:
    typed = sd.derive(
        _card(**_hits(alphas=2, charlies=7, misses=1), stage_points=23.0, manual=["stage_points"]),
        14.32,
        "minor",
    )
    assert typed.stage_points == 23.0
    assert typed.hit_factor == round(23.0 / 14.32, 4)
    board = sd.derive(_card(**_hits(alphas=5, charlies=4), stage_points=40.0, hit_factor=3.0), 12.68, "minor")
    assert (board.stage_points, board.hit_factor, board.derived) == (40.0, 3.0, [])


def test_derive_recomputes_and_clears_its_own_values() -> None:
    first = sd.derive(_card(**_hits(alphas=5, charlies=4)), 12.68, "minor")
    changed = sd.derive(first.model_copy(update={"alphas": 6}), 12.68, "minor")
    assert changed.stage_points == 42.0
    removed = sd.derive(changed.model_copy(update={"charlies": None}), 12.68, "minor")
    assert removed.stage_points is None and removed.hit_factor is None
    assert removed.derived == []


def test_derive_without_a_time_leaves_hit_factor_empty() -> None:
    card = sd.derive(_card(**_hits(alphas=5, charlies=4)), 0.0, "minor")
    assert card.stage_points == 37.0 and card.hit_factor is None


def _project(tmp_path: Path) -> MatchProject:
    project = MatchProject.init(tmp_path / "janne", name="BH")
    project.competitor_division = "Production Optics"
    project.stages = [
        StageEntry(stage_number=1, stage_name="Now You See Me", time_seconds=0.0),
        StageEntry(stage_number=2, stage_name="Weak Sandman", time_seconds=0.0),
    ]
    return project


def test_a_sync_keeps_typed_values_and_fills_blanks(tmp_path: Path) -> None:
    project = _project(tmp_path)
    stage = project.stage(1)
    stage.time_seconds, stage.time_seconds_manual = 12.65, True
    stage.scorecard = _card(alphas=6, manual=["alphas"])

    project.merge_stage_times(
        {
            "competitorId": 1,
            "stages": [
                {
                    "stage_number": 1,
                    "time_seconds": 12.68,
                    "scorecard_updated_at": "2023-09-09T08:00:00+00:00",
                    "alphas": 5,
                    "charlies": 4,
                    "deltas": 0,
                    "misses": 0,
                    "no_shoots": 0,
                    "procedurals": 0,
                }
            ],
        }
    )

    card = project.stage(1).scorecard
    assert project.stage(1).time_seconds == 12.65
    assert card is not None
    assert (card.alphas, card.charlies, card.manual) == (6, 4, ["alphas"])
    assert card.stage_points == 42.0 and "stage_points" in card.derived


def test_a_sync_without_typed_values_replaces_the_card(tmp_path: Path) -> None:
    project = _project(tmp_path)
    project.stage(2).scorecard = _card(alphas=1, hit_factor=1.0)
    project.merge_stage_times(
        {"competitorId": 1, "stages": [{"stage_number": 2, "alphas": 9, "hit_factor": 2.2912}]}
    )
    card = project.stage(2).scorecard
    assert card is not None and (card.alphas, card.hit_factor) == (9, 2.2912)


def test_scorecard_order(tmp_path: Path) -> None:
    project = _project(tmp_path)
    project.stage(1).scorecard_updated_at = datetime(2023, 9, 9, 11, tzinfo=UTC)
    project.stage(2).scorecard_updated_at = datetime(2023, 9, 9, 9, tzinfo=UTC)
    assert sd.scorecard_order(project) == [2, 1]


def test_project_round_trips_the_new_fields(tmp_path: Path) -> None:
    project = _project(tmp_path)
    project.power_factor = "major"
    project.stage(1).shot_order = 3
    project.stage(1).scorecard = _card(alphas=1, manual=["alphas"])
    project.save(tmp_path / "janne")
    loaded = MatchProject.load(tmp_path / "janne")
    assert loaded.power_factor == "major"
    assert loaded.stage(1).shot_order == 3
    assert loaded.stage(1).scorecard.manual == ["alphas"]  # type: ignore[union-attr]


def test_linking_the_scoreboard_keeps_what_was_typed(tmp_path: Path) -> None:
    """A hand-made match filled in on Stage details, then linked: the
    rebuild keeps the typed time, the order and the typed scores."""
    project = _project(tmp_path)
    for entry in project.stages:
        entry.placeholder = True
    stage = project.stage(1)
    stage.time_seconds, stage.time_seconds_manual, stage.shot_order = 12.5, True, 2
    stage.scorecard = _card(alphas=5, manual=["alphas"])
    raw = {
        "match": {"name": "BH"},
        "competitors": [
            {
                "name": "Janne",
                "stages": [
                    {
                        "stage_number": 1,
                        "stage_name": "Now You See Me",
                        "time_seconds": 0.0,
                        "scorecard_updated_at": "2023-09-09T08:00:00+00:00",
                    },
                    {
                        "stage_number": 2,
                        "stage_name": "Weak Sandman",
                        "time_seconds": 20.95,
                        "scorecard_updated_at": "2023-09-09T09:00:00+00:00",
                    },
                ],
            }
        ],
    }

    project.import_scoreboard(raw)

    kept = project.stage(1)
    assert (kept.time_seconds, kept.time_seconds_manual, kept.shot_order) == (12.5, True, 2)
    assert kept.scorecard is not None and kept.scorecard.alphas == 5
    assert project.stage(2).time_seconds == 20.95


def test_an_emptied_typed_field_is_not_kept_over_the_scoreboard(tmp_path: Path) -> None:
    project = _project(tmp_path)
    project.stage(1).scorecard = _card(stage_points=None, manual=["stage_points"])

    project.merge_stage_times({"competitorId": 1, "stages": [{"stage_number": 1, "stage_points": 37.0}]})

    card = project.stage(1).scorecard
    assert card is not None and card.stage_points == 37.0 and card.manual == []
