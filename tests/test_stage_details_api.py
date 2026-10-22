"""Stage details routes (``ui/stage_details_api.py``, spec 2026-10-22)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from splitsmith import match_model
from splitsmith.match_project import MatchProject, StageEntry
from splitsmith.ui.stage_details_api import ssi_url


@pytest.fixture(autouse=True)
def _no_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


def _app(tmp_path: Path):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from splitsmith.ui.server import create_app

    root = tmp_path / "match"
    match = match_model.Match.init(root, name="BH")
    match.stages = [match_model.MatchStageDefinition(stage_number=n, stage_name=f"S{n}") for n in (1, 2, 3)]
    match.save(root)
    for slug, name in (("janne", "Janne Hauta"), ("mathias", "Mathias Axell")):
        match.add_shooter(root, match_model.Shooter(slug=slug, name=name))
        sroot = match_model.Match.shooter_root(root, slug)
        project = MatchProject.init(sroot, name="BH")
        project.competitor_name = name
        project.competitor_division = "Production Optics"
        project.stages = [StageEntry(stage_number=n, stage_name=f"S{n}", time_seconds=0.0) for n in (1, 2, 3)]
        if slug == "mathias":
            for n, hour in ((1, 11), (2, 9), (3, 10)):
                stage = project.stage(n)
                stage.time_seconds = 20.0
                stage.scorecard_updated_at = datetime(2023, 9, 9, hour, tzinfo=UTC)
        project.save(sroot)
    client = TestClient(create_app())
    assert client.post("/api/me/recent-projects/bind", json={"path": str(root.resolve())}).status_code == 200
    (mid,) = client.app.state.splitsmith_state.matches.known_ids()  # type: ignore[attr-defined]
    return client, root, f"/api/matches/{mid}/shooters"


def _row(n: int, **kw: object) -> dict[str, object]:
    return {"stage_number": n, **kw}


def test_put_round_trips_typed_values(tmp_path: Path) -> None:
    client, root, base = _app(tmp_path)
    body = {
        "power_factor": None,
        "rows": [
            _row(
                1,
                time_seconds=12.68,
                shot_order=3,
                alphas=5,
                charlies=4,
                deltas=0,
                misses=0,
                no_shoots=0,
                procedurals=0,
                manual=["alphas", "charlies", "deltas", "misses", "no_shoots", "procedurals"],
            ),
            _row(2, shot_order=1),
            _row(3),
        ],
    }

    resp = client.put(f"{base}/janne/stage-details", json=body)

    assert resp.status_code == 200, resp.text
    stage = resp.json()["stages"][0]
    assert (stage["time_seconds"], stage["time_manual"], stage["shot_order"]) == (12.68, True, 3)
    assert stage["scorecard"]["stage_points"] == 37.0
    assert stage["scorecard"]["hit_factor"] == 2.918
    assert sorted(stage["scorecard"]["derived"]) == ["hit_factor", "stage_points"]
    saved = MatchProject.load(match_model.Match.shooter_root(root, "janne"))
    assert saved.stage(1).time_seconds_manual and saved.stage(2).shot_order == 1
    assert saved.stage(1).scorecard.manual[0] == "alphas"  # type: ignore[union-attr]


def test_duplicate_order_and_unknown_stage_are_refused(tmp_path: Path) -> None:
    client, _, base = _app(tmp_path)
    dup = client.put(
        f"{base}/janne/stage-details", json={"rows": [_row(1, shot_order=1), _row(2, shot_order=1)]}
    )
    assert dup.status_code == 400 and "same place" in dup.json()["detail"]
    unknown = client.put(f"{base}/janne/stage-details", json={"rows": [_row(9)]})
    assert unknown.status_code == 400


def test_clearing_a_time_and_a_typed_field(tmp_path: Path) -> None:
    client, _, base = _app(tmp_path)
    url = f"{base}/janne/stage-details"
    client.put(url, json={"rows": [_row(1, time_seconds=12.0, alphas=4, manual=["alphas"])]})

    resp = client.put(url, json={"rows": [_row(1, time_seconds=None)]})

    stage = resp.json()["stages"][0]
    assert stage["time_seconds"] is None and stage["time_manual"] is False
    assert stage["scorecard"]["alphas"] is None and stage["scorecard"]["manual"] == []


def test_an_untouched_scoreboard_time_stays_the_scoreboards(tmp_path: Path) -> None:
    client, _, base = _app(tmp_path)

    resp = client.put(
        f"{base}/mathias/stage-details", json={"rows": [_row(1, time_seconds=20.0, shot_order=2)]}
    )

    assert resp.json()["stages"][0]["time_manual"] is False


def test_get_offers_another_shooters_order(tmp_path: Path) -> None:
    client, _, base = _app(tmp_path)

    view = client.get(f"{base}/janne/stage-details").json()

    assert view["power_factor"] == "minor" and view["power_factor_set"] is False
    assert view["others"] == [
        {"slug": "mathias", "name": "Mathias Axell", "order": [2, 3, 1], "from_scorecards": True}
    ]
    client.put(f"{base}/mathias/stage-details", json={"rows": [_row(1, shot_order=1), _row(3, shot_order=2)]})
    again = client.get(f"{base}/janne/stage-details").json()
    assert again["others"][0]["order"] == [1, 3] and again["others"][0]["from_scorecards"] is False


def test_ssi_links(tmp_path: Path) -> None:
    project = MatchProject.init(tmp_path / "p", name="BH")
    assert ssi_url(project) is None
    project.scoreboard_content_type, project.scoreboard_match_id = 22, "17169"
    assert ssi_url(project) == "https://shootnscoreit.com/event/22/17169/"
    project.selected_competitor_id = 512019
    assert ssi_url(project) == "https://shootnscoreit.com/event/participant/23/512019/"
    project.scoreboard_content_type = 24
    assert ssi_url(project) == "https://shootnscoreit.com/event/24/17169/"


def test_the_shooter_list_counts_stages_without_a_time(tmp_path: Path) -> None:
    client, _, base = _app(tmp_path)
    match_base = base.removesuffix("/shooters")

    rows = {r["slug"]: r for r in client.get(f"{match_base}/match/shooters").json()["shooters"]}

    assert rows["janne"]["stages_without_time"] == 3
    assert rows["mathias"]["stages_without_time"] == 0


def test_an_emptied_field_goes_back_to_being_worked_out(tmp_path: Path) -> None:
    client, _, base = _app(tmp_path)
    url = f"{base}/janne/stage-details"
    hits = {"alphas": 5, "charlies": 4, "deltas": 0, "misses": 0, "no_shoots": 0, "procedurals": 0}
    typed = ["alphas", "charlies", "deltas", "misses", "no_shoots", "procedurals"]
    client.put(
        url,
        json={
            "rows": [_row(1, time_seconds=12.68, stage_points=40, manual=[*typed, "stage_points"], **hits)]
        },
    )

    resp = client.put(
        url,
        json={
            "rows": [_row(1, time_seconds=12.68, stage_points=None, manual=[*typed, "stage_points"], **hits)]
        },
    )

    card = resp.json()["stages"][0]["scorecard"]
    assert card["stage_points"] == 37.0 and "stage_points" in card["derived"]
    assert "stage_points" not in card["manual"]


def test_a_new_time_from_audit_updates_a_worked_out_hit_factor(tmp_path: Path) -> None:
    client, _, base = _app(tmp_path)
    hits = {"alphas": 5, "charlies": 4, "deltas": 0, "misses": 0, "no_shoots": 0, "procedurals": 0}
    client.put(
        f"{base}/janne/stage-details",
        json={"rows": [_row(1, time_seconds=12.68, manual=list(hits), **hits)]},
    )

    project = client.post(f"{base}/janne/stages/1/time", json={"time_seconds": 18.5}).json()

    card = next(s for s in project["stages"] if s["stage_number"] == 1)["scorecard"]
    assert card["hit_factor"] == round(37 / 18.5, 4)
