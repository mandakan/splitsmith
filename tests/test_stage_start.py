"""A stage's start (#1528): standard (holstered and loaded) or not. Set once
per match through the stage editor, copied to every shooter, kept across a
re-import, and only standard starts are compared as Draws across stages."""

from __future__ import annotations

from pathlib import Path

from splitsmith.coach import auto_classify_config
from splitsmith.coach_distributions import match_distributions
from splitsmith.match_model import is_standard_start
from splitsmith.match_summary import build_match_summary
from splitsmith.stage_summary_data import TileShot, TileStageData

from .test_ui_server_stage_edit import _seeded_match


def test_only_standard_reads_as_standard() -> None:
    assert is_standard_start("standard") and is_standard_start(None)
    assert not is_standard_start("non_standard")
    # A finer kind a newer version wrote is not a standard start.
    assert not is_standard_start("table_unloaded")


def test_the_stage_editor_sets_the_start_for_the_match_and_every_shooter(tmp_path: Path) -> None:
    client, _state = _seeded_match(tmp_path, stages=2, shooters=["me", "you"])
    rows = client.get("/api/match/stages").json()["stages"]
    assert [r["start"] for r in rows] == ["standard", "standard"]
    payload = [
        {"stage_number": r["stage_number"], "stage_name": r["stage_name"], "stage_rounds": r["stage_rounds"]}
        for r in rows
    ]
    payload[1]["start"] = "non_standard"
    resp = client.put("/api/match/stages", json={"stages": payload})
    assert resp.status_code == 200, resp.text
    assert resp.json()["renamed"] == [2]
    assert [r["start"] for r in client.get("/api/match/stages").json()["stages"]] == [
        "standard",
        "non_standard",
    ]
    for slug in ("me", "you"):
        stages = client.get(f"/api/shooters/{slug}/project").json()["stages"]
        assert [s["start"] for s in stages] == ["standard", "non_standard"]

    # A submission that leaves the start out (an older client) keeps it.
    for row in payload:
        row.pop("start", None)
    payload[0]["stage_name"] = "Renamed"
    assert client.put("/api/match/stages", json={"stages": payload}).status_code == 200
    assert [r["start"] for r in client.get("/api/match/stages").json()["stages"]] == [
        "standard",
        "non_standard",
    ]


def _tile(n: int, first: float, *, standard: bool) -> tuple[str, TileStageData]:
    shots = (TileShot(first, first), TileShot(first + 0.25, 0.25))
    return f"S{n}", TileStageData(label="Me", stage_number=n, shots=shots, standard_start=standard)


def test_best_draw_compares_standard_starts_only() -> None:
    summary = build_match_summary(
        [_tile(1, 1.4, standard=True), _tile(2, 0.9, standard=False), _tile(3, 1.2, standard=True)],
        title="M",
        label="Me",
    )
    # The table start's 0.90 is still its row's Draw, never the Best draw.
    assert [r.draw for r in summary.rows] == [1.4, 0.9, 1.2]
    assert summary.best_draw == 1.2


def test_the_draw_baseline_leaves_non_standard_starts_out() -> None:
    def shots(first_ms: int) -> list[dict]:
        return [
            {"shot_number": 1, "ms_after_beep": first_ms},
            {"shot_number": 2, "ms_after_beep": first_ms + 250},
        ]

    stages = [(1, "S1", shots(1400)), (2, "S2", shots(3900)), (3, "S3", shots(1200))]
    every = match_distributions(stages=stages, config=auto_classify_config())
    assert sorted(every.first_shot_seconds) == [1.2, 1.4, 3.9]
    standard = match_distributions(
        stages=stages, config=auto_classify_config(), non_standard_starts=frozenset({2})
    )
    assert sorted(standard.first_shot_seconds) == [1.2, 1.4]
