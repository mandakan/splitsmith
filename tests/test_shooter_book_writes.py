"""Setting a shooter's look saves it to the shooter book (spec 2026-10-08)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from splitsmith.identity import ShooterIdentity
from splitsmith.match_project import MatchProject
from splitsmith.shooter_book import JsonShooterBookStore

from .test_identity_api import _png
from .test_ui_server import _seed_match_export_project

IDENTITY = "/api/shooters/me/identity"
LOGO = "/api/shooters/me/identity/logo"
SID = 40821


def _book(sid: int = SID):
    return asyncio.run(JsonShooterBookStore().get(sid))


def _seed(tmp_path: Path, *, sid: int | None = SID):
    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    shooter_root = root / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    project.selected_shooter_id = sid
    project.competitor_name = "Mathias Axell"
    project.save(shooter_root)
    return client, shooter_root


def test_an_edit_writes_the_book_entry(tmp_path: Path) -> None:
    client, _ = _seed(tmp_path)
    assert client.patch(IDENTITY, json={"accent": "#ff2d2d", "club": "Bromma PK"}).status_code == 200
    entry = _book()
    assert entry is not None and entry.identity == ShooterIdentity(accent="#ff2d2d", club="Bromma PK")
    assert entry.label == "Mathias Axell"


def test_only_this_match_leaves_the_book_alone(tmp_path: Path) -> None:
    client, shooter_root = _seed(tmp_path)
    client.patch(IDENTITY, json={"accent": "#ff2d2d"})
    assert client.patch(IDENTITY, json={"accent": "#00ff00", "scope": "match"}).status_code == 200
    assert MatchProject.load(shooter_root).identity.accent == "#00ff00"
    assert _book().identity.accent == "#ff2d2d"


def test_a_logo_upload_copies_into_the_book(tmp_path: Path) -> None:
    client, _ = _seed(tmp_path)
    r = client.post(LOGO, files={"file": ("club.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    name = r.json()["identity"]["logo"]
    entry = _book()
    assert entry.identity.logo == name
    path = asyncio.run(JsonShooterBookStore().logo_file(name))
    assert path is not None and path.read_bytes() == _png()
    # Removing it updates the book too; an upload kept to this match does not.
    assert client.delete(LOGO).status_code == 200
    assert _book() is None, "a look that sets nothing leaves no entry"
    r = client.post(LOGO, files={"file": ("c.png", _png(), "image/png")}, data={"scope": "match"})
    assert r.status_code == 200 and _book() is None


def test_no_ssi_id_writes_no_entry(tmp_path: Path) -> None:
    client, _ = _seed(tmp_path, sid=None)
    assert client.patch(IDENTITY, json={"accent": "#ff2d2d"}).status_code == 200
    assert asyncio.run(JsonShooterBookStore().list()) == []


def test_use_book_clears_the_match_record_only(tmp_path: Path) -> None:
    client, shooter_root = _seed(tmp_path)
    client.patch(IDENTITY, json={"accent": "#ff2d2d"})
    client.patch(IDENTITY, json={"accent": "#00ff00", "scope": "match"})
    # The book wins over the match's own record (spec 2026-10-09): an older
    # client's "only this match" edit is stored and drawn under the book.
    assert MatchProject.load(shooter_root).identity.accent == "#00ff00"
    view = client.get(IDENTITY).json()
    assert view == {
        "source": "book",
        "identity": {"accent": "#ff2d2d", "logo": None, "club": None},
        "shooter_id": SID,
        "book_entry": True,
        "book_available": True,
    }
    r = client.post(f"{IDENTITY}/use-book")
    assert r.status_code == 200
    assert r.json()["source"] == "book" and r.json()["identity"]["accent"] == "#ff2d2d"
    assert MatchProject.load(shooter_root).identity == ShooterIdentity()
    assert _book().identity.accent == "#ff2d2d"


def test_two_matches_edited_in_turn_leave_the_latest_in_the_book() -> None:
    """Two matches with the same shooter, each saving its edit in turn (the
    route's own call; one test process cannot open two match apps). The
    book holds the latest; it writes no match, so neither is touched."""
    from splitsmith.shooter_book import save_identity

    store = JsonShooterBookStore()
    asyncio.run(
        save_identity(
            store, shooter_id=SID, identity=ShooterIdentity(club="Bromma PK"), label="A", logo_bytes=None
        )
    )
    asyncio.run(
        save_identity(
            store, shooter_id=SID, identity=ShooterIdentity(club="Stockholm"), label="A", logo_bytes=None
        )
    )
    assert _book().identity.club == "Stockholm"
    asyncio.run(save_identity(store, shooter_id=SID, identity=ShooterIdentity(), label="A", logo_bytes=None))
    assert _book() is None


@pytest.mark.parametrize("scope", ["book", "match"])
def test_a_bad_value_writes_neither(tmp_path: Path, scope: str) -> None:
    client, _ = _seed(tmp_path)
    assert client.patch(IDENTITY, json={"accent": "red", "scope": scope}).status_code == 422
    assert _book() is None


# --- review fixes -------------------------------------------------------------------


def test_use_book_is_refused_when_the_book_has_nothing_for_this_shooter(tmp_path: Path) -> None:
    client, shooter_root = _seed(tmp_path)
    client.patch(IDENTITY, json={"accent": "#ff2d2d", "scope": "match"})
    assert client.get(IDENTITY).json()["book_entry"] is False
    r = client.post(f"{IDENTITY}/use-book")
    assert r.status_code == 409
    assert MatchProject.load(shooter_root).identity.accent == "#ff2d2d", "nothing was wiped"


def test_only_this_match_on_a_book_look_keeps_the_book_logo(tmp_path: Path) -> None:
    client, shooter_root = _seed(tmp_path)
    name = client.post(LOGO, files={"file": ("club.png", _png(), "image/png")}).json()["identity"]["logo"]
    client.patch(IDENTITY, json={"club": "Bromma PK"})
    assert client.post(f"{IDENTITY}/use-book").status_code == 200
    assert client.get(IDENTITY).json()["source"] == "book"
    # An accent change kept to this match starts from what the sheet showed.
    assert client.patch(IDENTITY, json={"accent": "#00ff00", "scope": "match"}).status_code == 200
    own = MatchProject.load(shooter_root).identity
    assert (own.accent, own.club, own.logo) == ("#00ff00", "Bromma PK", name)
    assert (shooter_root / "identity" / name).read_bytes() == _png()
    assert _book().identity.accent is None, "the book was not touched"


def test_an_empty_book_entry_is_removed_and_never_the_source(tmp_path: Path) -> None:
    client, _ = _seed(tmp_path)
    assert client.put(f"/api/me/shooter-book/{SID}", json={"club": "X"}).status_code == 200
    assert client.get(IDENTITY).json()["source"] == "book"
    assert client.put(f"/api/me/shooter-book/{SID}", json={"club": None}).status_code == 200
    assert _book() is None, "an entry that sets nothing is removed"
    assert client.get(IDENTITY).json()["source"] == "none"


def test_a_book_entry_that_sets_nothing_never_counts_as_the_source() -> None:
    from splitsmith.shooter_book import BookSnapshot
    from splitsmith.ui.identity_media import identity_source

    project = MatchProject(name="m", selected_shooter_id=SID)
    assert identity_source(project, BookSnapshot(entries={SID: ShooterIdentity()})) == "none"


def test_a_logo_copy_that_fails_saves_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.shooter_book import save_identity

    store = JsonShooterBookStore()

    async def boom(data: bytes) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(store, "put_logo", boom)
    identity = ShooterIdentity(club="PK", logo="logo-0123456789ab.png")
    asyncio.run(save_identity(store, shooter_id=SID, identity=identity, label="A", logo_bytes=b"x"))
    assert _book().identity == ShooterIdentity(club="PK")


def test_a_missing_match_logo_file_keeps_the_books_logo() -> None:
    from splitsmith.shooter_book import ShooterBookEntry, save_identity

    store = JsonShooterBookStore()
    name = asyncio.run(store.put_logo(_png()))
    asyncio.run(store.put(ShooterBookEntry(shooter_id=SID, identity=ShooterIdentity(logo=name))))
    identity = ShooterIdentity(logo=name, club="PK")
    asyncio.run(save_identity(store, shooter_id=SID, identity=identity, label="A", logo_bytes=None))
    assert _book().identity == ShooterIdentity(logo=name, club="PK")


def test_removing_the_logo_with_scope_match_leaves_the_book(tmp_path: Path) -> None:
    client, _ = _seed(tmp_path)
    client.post(LOGO, files={"file": ("club.png", _png(), "image/png")})
    name = _book().identity.logo
    assert client.delete(LOGO, params={"scope": "match"}).status_code == 200
    assert _book().identity.logo == name


def test_an_edit_through_the_old_route_starts_from_the_book_it_draws(tmp_path: Path) -> None:
    """The book wins (spec 2026-10-09), so an older client's edit must start
    from the book's look, not from a stale match record under it: the book's
    accent survives a club-only edit."""
    client, _ = _seed(tmp_path)
    client.patch(IDENTITY, json={"accent": "#aa0000"})
    client.patch(IDENTITY, json={"accent": "#00ff00", "scope": "match"})
    client.patch(IDENTITY, json={"club": "Edited"})
    entry = _book()
    assert (entry.identity.accent, entry.identity.club) == ("#aa0000", "Edited")


def test_removing_a_book_logo_that_is_not_there_is_not_an_error(tmp_path: Path) -> None:
    """The Shooters sheet clears a look that came from a match record: the
    book has no entry to remove a logo from, which is already the result."""
    client, _ = _seed(tmp_path)
    r = client.delete(f"/api/me/shooter-book/{SID + 1}/logo")
    assert r.status_code == 200, r.text
