"""The stage summary's figures as data agree with the engine card's text
(spec 2026-10-16-summary-templates-design): a summary template and the
default summary must never show different numbers for one stage."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.match_project import StageScorecard
from splitsmith.stage_summary_data import TileShot, TileStageData, stage_summary_figures
from tests.test_overlay_summary_cell import (
    _MOVE_1,
    _MOVE_2,
    _RELOAD,
    _audit,
    _events_tile,
    _groups,
    _splits_rows,
)


def _texts(groups) -> list[str]:  # noqa: ANN001
    return [e.text for g in groups for e in g.elements]


def _draw_tile(*, with_movement: bool) -> TileStageData:
    from splitsmith.config import StageEvent

    events = [StageEvent(id="evt-2", kind="draw", start=1.33, end=2.31, source="manual")]
    if with_movement:
        events.insert(0, StageEvent(id="evt-1", kind="movement", start=0.0, end=2.2, source="manual"))
    return TileStageData(
        label="Me",
        stage_number=2,
        shots=(TileShot(2.31, 2.31), TileShot(2.6, 0.29), TileShot(2.9, 0.3)),
        stage_time_seconds=9.0,
        events=tuple(events),
    )


@pytest.mark.parametrize("with_movement", [False, True])
def test_a_confirmed_draw_is_the_stages_draw_everywhere(with_movement: bool) -> None:
    """Spec 2026-10-17: the draw region's length, not the first shot's time;
    the figures and the engine card's Draw element say the same. A movement
    beside it is a region of its own and changes nothing here."""
    tile = _draw_tile(with_movement=with_movement)
    assert stage_summary_figures(tile).draw == 0.98
    draws = [e for g in _groups(tile) for e in g.elements if e.caption == "Draw"]
    assert [e.text for e in draws] == ["0.98"]


def test_the_match_summary_reads_the_draw_region_too() -> None:
    from splitsmith.match_summary import build_match_summary

    summary = build_match_summary([("Skolhuset", _draw_tile(with_movement=True))], title="M", label="Me")
    assert summary.rows[0].draw == 0.98 and summary.best_draw == 0.98


def test_figures_match_the_engine_card_on_a_stage_with_regions(tmp_path: Path) -> None:
    tile = _events_tile(_audit(tmp_path, [_MOVE_1, _MOVE_2, _RELOAD]))
    figures = stage_summary_figures(tile)
    rows = _splits_rows(_groups(tile))
    static_row, moving_row, last_row = rows
    assert [text for _, text in static_row[1:]] == [
        f"{figures.static.best:.2f}",
        f"{figures.static.avg:.2f}",
        f"{figures.static.worst:.2f}",
    ]
    assert [text for _, text in moving_row[1:]] == [
        f"{figures.moving.best:.2f}",
        f"{figures.moving.avg:.2f}",
        f"{figures.moving.worst:.2f}",
    ]
    assert figures.reloads is not None
    assert last_row == [
        ("Draw", f"{figures.draw:.2f}"),
        ("Reloads", str(figures.reloads.count)),
        ("Reload avg", f"{figures.reloads.avg:.2f}"),
        ("Exposed", f"{figures.reloads.exposed:.2f}"),
    ]
    texts = _texts(_groups(tile))
    assert f"{figures.hit_factor:.2f}" in texts and f"{figures.time_seconds:.2f}s" in texts
    assert figures.counts == {"A": 20, "C": 3, "D": 1, "M": 0, "NS": 0, "P": 1}
    assert figures.rounds == len(tile.shots)


def test_figures_match_the_engine_card_without_regions(tmp_path: Path) -> None:
    tile = _events_tile(_audit(tmp_path, None))
    figures = stage_summary_figures(tile)
    (row,) = _splits_rows(_groups(tile))
    assert row == [
        ("Best", f"{figures.splits.best:.2f}"),
        ("Avg", f"{figures.splits.avg:.2f}"),
        ("Worst", f"{figures.splits.worst:.2f}"),
        ("Draw", f"{figures.draw:.2f}"),
    ]
    assert figures.static is None and figures.moving is None and figures.reloads is None


def test_absent_stays_absent() -> None:
    figures = stage_summary_figures(TileStageData(label="Me", stage_number=1))
    assert figures.model_dump() == {
        "time_seconds": None,
        "time_is_manual": False,
        "hit_factor": None,
        "stage_pct": None,
        "points": None,
        "counts": {},
        "draw": None,
        "splits": None,
        "static": None,
        "moving": None,
        "reloads": None,
        "rounds": 0,
        "min_rounds": None,
        "dq": False,
    }


def test_a_dq_keeps_its_time_and_drops_its_scoring() -> None:
    tile = TileStageData(
        label="Me",
        stage_number=1,
        shots=(TileShot(1.2, 1.2), TileShot(1.5, 0.3)),
        stage_time_seconds=9.1,
        scorecard=StageScorecard(dq=True, hit_factor=3.0, alphas=4),
    )
    figures = stage_summary_figures(tile)
    assert figures.dq and figures.time_seconds == 9.1
    assert figures.hit_factor is None and figures.counts == {}
    assert figures.draw == 1.2
