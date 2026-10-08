"""The roster and the title page draw the shooter book's look (spec 2026-10-08).

A match whose shooter sets nothing reads the book in the renders; the
shooters list, Compare's payload, the logo route and the title page's club
line read it the same way, so the ring a user sees is the look the video
draws. A share request never reads the owner's book.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from splitsmith.identity import ShooterIdentity
from splitsmith.match_model import Match, MatchStageDefinition
from splitsmith.match_project import MatchProject
from splitsmith.shooter_book import BookSnapshot, JsonShooterBookStore, ShooterBookEntry
from splitsmith.ui.match_exports import title_info_lines

from .test_identity_api import _png
from .test_ui_server import _seed_match_export_project

SID = 40821


def _seed(tmp_path: Path, *, own: ShooterIdentity | None = None):
    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    match = Match.load(root)
    match.stages = [MatchStageDefinition(stage_number=1, stage_name="Stage 1")]
    match.save(root)
    shooter_root = root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    project.selected_shooter_id = SID
    project.competitor_name = "Mathias Axell"
    if own is not None:
        project.identity = own
    project.save(shooter_root)
    store = JsonShooterBookStore()
    logo = asyncio.run(store.put_logo(_png()))
    entry = ShooterIdentity(accent="#ff2d2d", club="Bromma PK", logo=logo)
    asyncio.run(store.put(ShooterBookEntry(shooter_id=SID, identity=entry, label="Mathias Axell")))
    return client, entry


def _roster_identity(client) -> dict:
    r = client.get("/api/match/shooters")
    assert r.status_code == 200, r.text
    (me,) = [s for s in r.json()["shooters"] if s["slug"] == "me"]
    return me["identity"]


def _compare_identity(client) -> dict:
    (match_id,) = client.app.state.splitsmith_state.matches.known_ids()
    r = client.get(f"/api/matches/{match_id}/match/stage/1/compare")
    assert r.status_code == 200, r.text
    (me,) = [s for s in r.json()["shooters"] if s["slug"] == "me"]
    return me["identity"]


def test_the_roster_shows_the_book_look_when_the_match_sets_nothing(tmp_path: Path) -> None:
    client, entry = _seed(tmp_path)
    assert _roster_identity(client) == entry.model_dump(mode="json")
    assert _compare_identity(client) == entry.model_dump(mode="json")
    r = client.get("/api/shooters/me/identity/logo")
    assert r.status_code == 200 and r.content == _png()


def test_the_match_own_look_wins_as_a_whole(tmp_path: Path) -> None:
    own = ShooterIdentity(accent="#00ff00")
    client, _ = _seed(tmp_path, own=own)
    assert _roster_identity(client) == own.model_dump(mode="json")
    assert _compare_identity(client) == own.model_dump(mode="json")
    assert client.get("/api/shooters/me/identity/logo").status_code == 404


def test_a_share_request_never_reads_the_owner_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import splitsmith.ui.server as server

    client, _ = _seed(tmp_path)

    class _Share:
        @staticmethod
        def get() -> bool:
            return True

    monkeypatch.setattr(server, "current_share_request", _Share)
    empty = ShooterIdentity().model_dump(mode="json")
    assert _roster_identity(client) == empty
    assert _compare_identity(client) == empty
    assert client.get("/api/shooters/me/identity/logo").status_code == 404


def test_the_title_page_club_line_reads_the_book() -> None:
    project = MatchProject(name="m", competitor_name="Mathias Axell", selected_shooter_id=SID)
    book = BookSnapshot(entries={SID: ShooterIdentity(club="Bromma PK")})
    assert "Bromma PK" in title_info_lines(project, book=book)
    # The match's own record wins as a whole, club or not.
    project.identity = ShooterIdentity(accent="#00ff00")
    assert "Bromma PK" not in title_info_lines(project, book=book)
