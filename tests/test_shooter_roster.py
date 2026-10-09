"""The Shooters page's roster (spec 2026-10-09): everyone you have filmed,
one row per SSI id, the look the videos draw, you first."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from splitsmith.identity import ShooterIdentity
from splitsmith.match_project import MatchProject
from splitsmith.shooter_book import BookSnapshot
from splitsmith.ui import shooter_roster
from splitsmith.ui.shooter_roster import RosterSeen, build_roster

from .test_ui_server import _seed_match_export_project


def _seen(
    shooter_id: int | None,
    name: str,
    match: str,
    day: int,
    identity: ShooterIdentity | None = None,
    slug: str = "me",
) -> RosterSeen:
    return RosterSeen(
        shooter_id=shooter_id,
        name=name,
        match_id=match,
        match_name=f"Match {match}",
        slug=slug,
        updated_at=datetime(2026, 10, day, tzinfo=UTC),
        identity=identity or ShooterIdentity(),
    )


def test_one_row_per_shooter_with_the_newest_name_and_every_match_counted() -> None:
    rows = build_roster(
        [_seen(7, "Anna J", "a", 1), _seen(7, "Anna Jonsson", "b", 5), _seen(9, "Bo", "a", 2)],
        BookSnapshot(),
        you_id=None,
    )
    assert [(r.shooter_id, r.name, r.match_count) for r in rows] == [(7, "Anna Jonsson", 2), (9, "Bo", 1)]
    assert rows[0].last_match_name == "Match b"


def test_you_first_then_the_newest_match_then_the_name() -> None:
    rows = build_roster(
        [
            _seen(1, "Old", "a", 1),
            _seen(2, "Me", "a", 1),
            _seen(3, "New", "b", 9),
            _seen(4, "Also new", "b", 9),
        ],
        BookSnapshot(),
        you_id=2,
    )
    assert [r.name for r in rows] == ["Me", "Also new", "New", "Old"]
    assert rows[0].you and not any(r.you for r in rows[1:])


def test_the_book_wins_and_a_match_look_fills_in_without_one() -> None:
    book = BookSnapshot(
        entries={7: ShooterIdentity(accent="#aa0000", club="Bromma", logo="logo-0123456789ab.png")}
    )
    rows = build_roster(
        [
            _seen(7, "Anna", "a", 3, ShooterIdentity(accent="#00ff00")),
            _seen(9, "Bo", "a", 2, ShooterIdentity(club="PK", logo="logo-ba9876543210.png"), slug="bo"),
            _seen(9, "Bo", "b", 1),
            _seen(11, "Cy", "a", 1),
        ],
        book,
        you_id=None,
    )
    anna, bo, cy = rows
    assert (anna.source, anna.accent, anna.club) == ("book", "#aa0000", "Bromma")
    assert anna.logo_url == "/api/me/shooter-book/7/logo?v=logo-0123456789ab.png"
    assert (bo.source, bo.club) == ("match", "PK")
    assert bo.logo_url == "/api/matches/a/shooters/bo/identity/logo?v=logo-ba9876543210.png"
    assert (cy.source, cy.logo_url) == ("none", None)


def test_a_shooter_without_an_ssi_id_is_a_row_per_match() -> None:
    rows = build_roster(
        [_seen(None, "Guest", "a", 2, slug="guest"), _seen(None, "Guest", "b", 1, slug="guest")],
        BookSnapshot(),
        you_id=None,
    )
    assert [(r.shooter_id, r.match_id, r.slug, r.match_count) for r in rows] == [
        (None, "a", "guest", 1),
        (None, "b", "guest", 1),
    ]


def test_the_route_lists_this_machines_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter_root = root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    project.selected_shooter_id = 40821
    project.competitor_name = "Mathias Axell"
    project.save(shooter_root)
    monkeypatch.setattr(shooter_roster, "local_roots", lambda: [root])
    rows = client.get("/api/me/shooters").json()["rows"]
    assert [(r["shooter_id"], r["name"], r["match_count"]) for r in rows] == [(40821, "Mathias Axell", 1)]
    assert MatchProject.load(shooter_root).identity == ShooterIdentity(), "listing writes nothing"


def test_the_hosted_source_reads_the_accounts_matches_in_one_batch() -> None:
    import asyncio
    from types import SimpleNamespace

    project = MatchProject(name="m", competitor_name="Anna", selected_shooter_id=7)

    class _Matches:
        async def list(self):  # type: ignore[no-untyped-def]
            return [SimpleNamespace(match_id="m1", name="Höstfinalen XI")]

    class _State:
        calls = 0

        async def load_docs_for_matches(self, ids):  # type: ignore[no-untyped-def]
            type(self).calls += 1
            assert ids == ["m1"]
            return {"m1": SimpleNamespace(projects={"anna": project.model_dump(mode="json"), "bad": {"x": 1}})}

    seen = asyncio.run(shooter_roster.hosted_seen(_Matches(), _State()))
    assert [(s.shooter_id, s.name, s.match_id, s.match_name, s.slug) for s in seen] == [
        (7, "Anna", "m1", "Höstfinalen XI", "anna")
    ]
    assert _State.calls == 1
    assert asyncio.run(shooter_roster.hosted_seen(None, None)) == []
