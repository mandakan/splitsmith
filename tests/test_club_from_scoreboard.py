"""A shooter's club line from the scoreboard: linking a shooter to their
competitor fills an empty ``identity.club`` with the competitor's club, on
every way a link is made, and never overwrites one the user typed."""

from __future__ import annotations

import json
from pathlib import Path

from splitsmith.identity import ShooterIdentity
from splitsmith.match_project import MatchProject
from splitsmith.ui.server import create_app

from .test_ui_server import (
    _load_v1_match_fixture,
    _match_create_app,
    _MatchClient,
)


def _with_club() -> dict:
    fixture = _load_v1_match_fixture()
    return next(c for c in fixture["competitors"] if (c.get("club") or "").strip())


def test_adopt_fills_only_an_empty_club_line(tmp_path: Path) -> None:
    project = MatchProject.init(tmp_path / "p", name="x")
    assert project.adopt_scoreboard_club(" Team ONYX ") is True
    assert project.identity.club == "Team ONYX"
    assert project.adopt_scoreboard_club("Other") is False
    assert project.identity.club == "Team ONYX"
    assert project.adopt_scoreboard_club("") is False
    assert project.adopt_scoreboard_club(None) is False


def test_select_shooter_fills_the_club(tmp_path: Path) -> None:
    competitor = _with_club()
    root = tmp_path / "match"
    client = _MatchClient(_match_create_app(project_root=root, project_name="x"))
    client.post("/api/shooters/me/scoreboard/upload", json={"data": _load_v1_match_fixture()})
    # Offline, the stage-time merge answers 400 after the pin is saved.
    client.post(
        "/api/shooters/me/scoreboard/select-shooter",
        json={"shooter_id": competitor["shooterId"], "competitor_id": competitor["id"]},
    )
    assert MatchProject.load(root / "shooters" / "me").identity.club == competitor["club"].strip()


def test_select_shooter_keeps_a_club_the_user_typed(tmp_path: Path) -> None:
    competitor = _with_club()
    root = tmp_path / "match"
    client = _MatchClient(_match_create_app(project_root=root, project_name="x"))
    project = MatchProject.load(root / "shooters" / "me")
    project.identity = ShooterIdentity(club="Bromma PK")
    project.save(root / "shooters" / "me")
    client.post("/api/shooters/me/scoreboard/upload", json={"data": _load_v1_match_fixture()})
    client.post(
        "/api/shooters/me/scoreboard/select-shooter",
        json={"shooter_id": competitor["shooterId"], "competitor_id": competitor["id"]},
    )
    assert MatchProject.load(root / "shooters" / "me").identity.club == "Bromma PK"


def test_create_from_scoreboard_fills_each_shooters_club(tmp_path: Path) -> None:
    fixture = _load_v1_match_fixture()
    competitor = _with_club()
    target = tmp_path / "new-match"
    (target / "scoreboard").mkdir(parents=True)
    (target / "scoreboard" / "match.json").write_text(json.dumps(fixture), encoding="utf-8")
    client = _MatchClient(create_app())
    r = client.post(
        "/api/match/create-from-scoreboard",
        json={
            "project_folder": str(target),
            "name": fixture["name"],
            "match_id": 27190,
            "content_type": 22,
            "competitors": [
                {
                    "name": competitor["name"],
                    "division": competitor.get("division"),
                    "selected_shooter_id": competitor["shooterId"],
                    "selected_competitor_id": competitor["id"],
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    (shooter_dir,) = sorted((target / "shooters").iterdir())
    assert MatchProject.load(shooter_dir).identity.club == competitor["club"].strip()


def test_reconcile_fills_the_club(tmp_path: Path) -> None:
    """The connect-then-confirm flow: a shooter added by hand, linked to the
    competitor with a club."""
    fixture = _load_v1_match_fixture()
    competitor = _with_club()
    match_root = tmp_path / "match"
    client = _MatchClient(_match_create_app(project_root=match_root, project_name="Manual"))
    add = client.post("/api/match/shooters", json={"name": competitor["name"]})
    slug = next(s["slug"] for s in add.json()["shooters"] if s["name"] == competitor["name"])
    (match_root / "scoreboard").mkdir(parents=True, exist_ok=True)
    (match_root / "scoreboard" / "match.json").write_text(json.dumps(fixture), encoding="utf-8")
    connect = client.post("/api/match/scoreboard/connect", json={"match_id": 27190, "content_type": 22})
    assert connect.status_code == 200, connect.text
    link = {"slug": slug, "shooter_id": competitor["shooterId"], "competitor_id": competitor["id"]}
    r = client.post("/api/match/scoreboard/reconcile", json={"links": [link]})
    assert r.status_code == 200, r.text
    assert MatchProject.load(match_root / "shooters" / slug).identity.club == competitor["club"].strip()
