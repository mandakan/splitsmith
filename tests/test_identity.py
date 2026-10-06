"""Per-shooter identity: the model, its shape rules, and how it resolves against a Look."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from splitsmith import identity, looks


def test_identity_defaults_to_nothing() -> None:
    assert identity.ShooterIdentity() == identity.ShooterIdentity(accent=None, logo=None, club=None)


@pytest.mark.parametrize("bad", ["red", "#fff", "#GGGGGG", "ff2d2d", "#ff2d2d00"])
def test_accent_must_be_six_hex_digits(bad: str) -> None:
    with pytest.raises(ValidationError):
        identity.ShooterIdentity(accent=bad)


def test_accent_is_normalised_to_lower_case() -> None:
    assert identity.ShooterIdentity(accent="#FF2D2D").accent == "#ff2d2d"


@pytest.mark.parametrize(
    "bad", ["logo.png", "logo-abc.png", "logo-0123456789ab.svg", "../logo-0123456789ab.png"]
)
def test_logo_is_a_hash_named_raster_file(bad: str) -> None:
    with pytest.raises(ValidationError):
        identity.ShooterIdentity(logo=bad)


def test_club_is_stripped_bounded_and_blank_is_none() -> None:
    assert identity.ShooterIdentity(club="  Bromma PK  ").club == "Bromma PK"
    assert identity.ShooterIdentity(club="   ").club is None
    with pytest.raises(ValidationError):
        identity.ShooterIdentity(club="x" * 61)


def test_logo_name_is_content_addressed() -> None:
    assert identity.logo_name(b"abc", "png") == identity.logo_name(b"abc", "png")
    assert identity.logo_name(b"abc", "png") != identity.logo_name(b"abd", "png")
    assert identity.logo_name(b"abc", "jpeg").endswith(".jpeg")
    assert identity.ShooterIdentity(logo=identity.logo_name(b"abc", "webp"))


def test_resolve_takes_the_shooters_accent_else_the_looks_series_by_slot(tmp_path: Path) -> None:
    look = looks.load_look("splitsmith")
    own = identity.resolve_identity(
        label="A",
        identity=identity.ShooterIdentity(accent="#123456"),
        index=0,
        look=look,
        shooter_root=tmp_path,
        match_logo=None,
    )
    assert own.accent == "#123456"
    series = look.accent_series
    assert len(series) >= 6
    for index in (0, 1, len(series)):
        resolved = identity.resolve_identity(
            label="B", identity=None, index=index, look=look, shooter_root=tmp_path, match_logo=None
        )
        assert resolved.accent == series[index % len(series)]


def test_resolve_uses_the_shooters_logo_else_the_match_logo(tmp_path: Path) -> None:
    look = looks.load_look("splitsmith")
    name = identity.logo_name(b"x", "png")
    mine = identity.resolve_identity(
        label="A",
        identity=identity.ShooterIdentity(logo=name, club="Bromma PK"),
        index=0,
        look=look,
        shooter_root=tmp_path,
        match_logo=tmp_path / "match.png",
    )
    assert mine.logo_path == tmp_path / identity.LOGO_DIR / name and mine.club == "Bromma PK"
    theirs = identity.resolve_identity(
        label="A", identity=None, index=0, look=look, shooter_root=tmp_path, match_logo=tmp_path / "match.png"
    )
    assert theirs.logo_path == tmp_path / "match.png" and theirs.club is None


def test_a_look_without_a_series_falls_back_to_its_accent_token() -> None:
    from splitsmith.look_template import theme_tokens
    from splitsmith.overlay_theme import load_theme

    look = looks.load_look("clean")
    stripped = look.model_copy(update={"manifest": look.manifest.model_copy(update={"accent_series": []})})
    resolved = identity.resolve_identity(
        label="A", identity=None, index=3, look=stripped, shooter_root=None, match_logo=None
    )
    assert resolved.accent == theme_tokens(load_theme("clean"))["accent"]


def test_the_project_carries_an_identity_that_defaults_to_nothing() -> None:
    from splitsmith.match_project import MatchProject

    project = MatchProject(name="m")
    assert project.identity == identity.ShooterIdentity()
    loaded = MatchProject.model_validate({"name": "m", "identity": {"accent": "#ABCDEF", "club": " PK "}})
    assert loaded.identity.accent == "#abcdef" and loaded.identity.club == "PK"


def test_the_composition_carries_its_shooters() -> None:
    from splitsmith.composition import Composition, CompositionShooter

    shooter = CompositionShooter(label="A", accent="#ff2d2d", logo_path=None, club=None)
    assert Composition.__dataclass_fields__["shooters"].default == ()
    assert shooter.label == "A"
