"""A shooter's identity comes from the shooter book when the match sets none
(spec 2026-10-08-account-identity-and-shooter-book-design)."""

from __future__ import annotations

from pathlib import Path

from splitsmith.compare.project_loader import CompareShooterBundle
from splitsmith.identity import ShooterIdentity, logo_name
from splitsmith.looks import load_look
from splitsmith.match_project import MatchProject
from splitsmith.shooter_book import EMPTY_BOOK, BookSnapshot
from splitsmith.ui import identity_media

LOOK = load_look("splitsmith")
BOOK_LOGO = logo_name(b"book", "png")


def _book(tmp_path: Path, **identity: str) -> BookSnapshot:
    logo = tmp_path / "files" / BOOK_LOGO
    logo.parent.mkdir(parents=True, exist_ok=True)
    logo.write_bytes(b"png")
    return BookSnapshot(entries={42: ShooterIdentity(**identity)}, logos={BOOK_LOGO: logo})


def _resolve(project: MatchProject, root: Path, book: BookSnapshot):
    return identity_media.resolved_identity_for(project, root, look=LOOK, index=0, label="Anna", book=book)


def test_an_unset_match_identity_reads_the_book(tmp_path: Path) -> None:
    book = _book(tmp_path, accent="#aa0000", club="Bromma", logo=BOOK_LOGO)
    project = MatchProject(name="m", selected_shooter_id=42)
    resolved = _resolve(project, tmp_path, book)
    assert (resolved.accent, resolved.club, resolved.logo_path) == (
        "#aa0000",
        "Bromma",
        tmp_path / "files" / BOOK_LOGO,
    )
    assert identity_media.identity_source(project, book) == "book"


def test_the_match_identity_wins_as_a_whole_record(tmp_path: Path) -> None:
    book = _book(tmp_path, accent="#aa0000", club="Bromma", logo=BOOK_LOGO)
    project = MatchProject(name="m", selected_shooter_id=42, identity=ShooterIdentity(accent="#00ff00"))
    resolved = _resolve(project, tmp_path, book)
    # The match set an accent only: the book's club and logo are not borrowed.
    assert (resolved.accent, resolved.club, resolved.logo_path) == ("#00ff00", None, None)
    assert identity_media.identity_source(project, book) == "match"


def test_no_ssi_id_never_reads_the_book(tmp_path: Path) -> None:
    book = _book(tmp_path, accent="#aa0000")
    project = MatchProject(name="m")
    resolved = _resolve(project, tmp_path, book)
    assert (resolved.accent, resolved.club, resolved.logo_path) == (None, None, None)
    assert identity_media.identity_source(project, book) == "none"


def test_another_shooters_entry_is_never_used(tmp_path: Path) -> None:
    book = _book(tmp_path, accent="#aa0000")
    project = MatchProject(name="m", selected_shooter_id=7)
    assert _resolve(project, tmp_path, book).accent is None


def test_an_empty_book_resolves_exactly_as_today(tmp_path: Path) -> None:
    for project in (
        MatchProject(name="m", selected_shooter_id=42),
        MatchProject(name="m", selected_shooter_id=42, identity=ShooterIdentity(club="PK", accent="#123456")),
    ):
        before = identity_media.resolved_identity_for(project, tmp_path, look=LOOK, index=1, label="A")
        assert _resolve(project, tmp_path, EMPTY_BOOK) == identity_media.resolved_identity_for(
            project, tmp_path, look=LOOK, index=0, label="Anna"
        )
        assert before.label == "A"


def test_a_book_logo_missing_on_disk_is_no_logo(tmp_path: Path) -> None:
    book = BookSnapshot(entries={42: ShooterIdentity(logo=BOOK_LOGO, club="X")}, logos={})
    resolved = _resolve(MatchProject(name="m", selected_shooter_id=42), tmp_path, book)
    assert resolved.logo_path is None and resolved.club == "X"


def test_grid_identities_read_the_book_per_tile(tmp_path: Path) -> None:
    book = _book(tmp_path, accent="#aa0000")
    for label in ("Anna", "Bo"):
        (tmp_path / label).mkdir()
    bundles = [
        CompareShooterBundle(
            label="Anna",
            project_root=tmp_path / "Anna",
            project=MatchProject(name="m", selected_shooter_id=42),
        ),
        CompareShooterBundle(
            label="Bo", project_root=tmp_path / "Bo", project=MatchProject(name="m", selected_shooter_id=9)
        ),
    ]
    identities = identity_media.grid_identities(bundles, look=LOOK, book=book)
    assert identities["Anna"].accent == "#aa0000" and identities["Bo"].accent is None
