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
def test_the_draw_is_the_timer_draw_whatever_the_draw_region_says(with_movement: bool) -> None:
    """#1528: the Draw is what a shot timer reports, the first shot's time
    from the beep, even with a confirmed draw region whose start was moved;
    the figures and the engine card's Draw element say the same."""
    tile = _draw_tile(with_movement=with_movement)
    assert stage_summary_figures(tile).draw == 2.31
    draws = [e for g in _groups(tile) for e in g.elements if e.caption == "Draw"]
    assert [e.text for e in draws] == ["2.31"]


def test_the_upright_card_shows_the_timer_draw_too() -> None:
    from splitsmith.overlay_summary_cell import summary_groups, upright_summary_scale

    tile = _draw_tile(with_movement=False)
    groups = summary_groups(
        tile,
        "Me",
        scale=upright_summary_scale(1080, 1920, dense=False),
        cell_width=1080,
        cell_height=1920,
        upright=True,
    )
    assert [e.text for g in groups for e in g.elements if e.caption == "Draw"] == ["2.31"]


def test_without_the_breakdown_the_loaders_read_no_regions(tmp_path: Path) -> None:
    """The export's 'Use Breakdown' switch off: the stage reads as one
    nobody broke down -- no regions, no reloads, no shot fired moving."""
    from splitsmith.stage_summary_data import load_stage_events, load_stage_reloads, load_stage_shots

    audit = _audit(tmp_path, [_MOVE_1, _MOVE_2, _RELOAD])
    assert load_stage_events(audit) and load_stage_reloads(audit)
    assert any(s.moving for s in load_stage_shots(audit))
    assert load_stage_events(audit, breakdown=False) == ()
    assert load_stage_reloads(audit, breakdown=False) == ()
    assert not any(s.moving for s in load_stage_shots(audit, breakdown=False))
    assert [s.split for s in load_stage_shots(audit, breakdown=False)] == [
        s.split for s in load_stage_shots(audit)
    ]


def test_every_tile_builder_carries_the_stages_regions() -> None:
    """The Draw reads ``TileStageData.events``: a production builder with
    shots that forgets them shows the first shot as the draw, silently.
    Filler tiles (no shots) are exempt."""
    import ast

    root = Path(__file__).parent.parent / "src" / "splitsmith"
    missing = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "TileStageData"):
                continue
            keywords = {k.arg for k in node.keywords}
            if "shots" in keywords and "events" not in keywords:
                missing.append(f"{path.relative_to(root)}:{node.lineno}")
    assert missing == []


def test_the_match_summary_shows_the_timer_draw_too() -> None:
    from splitsmith.match_summary import build_match_summary

    summary = build_match_summary([("Skolhuset", _draw_tile(with_movement=True))], title="M", label="Me")
    assert summary.rows[0].draw == 2.31 and summary.best_draw == 2.31


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
