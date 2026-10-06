"""The identity routes (#1243): PATCH accent and club, upload and remove the logo."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from splitsmith.identity import LOGO_DIR
from splitsmith.match_project import MatchProject

from .test_ui_server import _seed_match_export_project

IDENTITY = "/api/shooters/me/identity"
LOGO = "/api/shooters/me/identity/logo"


@pytest.fixture
def seeded(tmp_path: Path):
    client, root = _seed_match_export_project(tmp_path, stage_count=1)
    return client, root / "shooters" / "me"


def _png(size: tuple[int, int] = (16, 16), colour=(255, 0, 0, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def test_patch_sets_accent_and_club_and_the_project_remembers_them(seeded) -> None:
    client, shooter_root = seeded
    r = client.patch(IDENTITY, json={"accent": "#FF2D2D", "club": " Bromma PK "})
    assert r.status_code == 200, r.text
    assert r.json()["identity"] == {"accent": "#ff2d2d", "logo": None, "club": "Bromma PK"}
    assert MatchProject.load(shooter_root).identity.accent == "#ff2d2d"
    cleared = client.patch(IDENTITY, json={"accent": None})
    assert cleared.status_code == 200
    assert cleared.json()["identity"] == {"accent": None, "logo": None, "club": "Bromma PK"}


@pytest.mark.parametrize("body", [{"accent": "red"}, {"accent": "#fff"}, {"club": "x" * 61}])
def test_patch_refuses_a_bad_accent_or_club_and_writes_nothing(seeded, body) -> None:
    client, shooter_root = seeded
    before = MatchProject.load(shooter_root).identity
    r = client.patch(IDENTITY, json=body)
    assert r.status_code == 422, r.text
    assert MatchProject.load(shooter_root).identity == before


def test_logo_upload_stores_a_content_named_file_and_points_the_project_at_it(seeded) -> None:
    client, shooter_root = seeded
    r = client.post(LOGO, files={"file": ("club.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    name = r.json()["identity"]["logo"]
    assert name is not None and name.startswith("logo-") and name.endswith(".png")
    assert (shooter_root / LOGO_DIR / name).read_bytes() == _png()
    assert MatchProject.load(shooter_root).identity.logo == name


def test_replacing_the_logo_removes_the_previous_file(seeded) -> None:
    client, shooter_root = seeded
    first = client.post(LOGO, files={"file": ("a.png", _png(), "image/png")}).json()["identity"]["logo"]
    second = client.post(LOGO, files={"file": ("b.png", _png(colour=(0, 0, 255, 255)), "image/png")})
    assert second.status_code == 200
    new = second.json()["identity"]["logo"]
    assert new != first
    assert not (shooter_root / LOGO_DIR / first).exists()
    assert (shooter_root / LOGO_DIR / new).exists()


def test_logo_upload_refuses_svg_and_non_images_and_leaves_no_file(seeded) -> None:
    client, shooter_root = seeded
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    for payload, mime in ((svg, "image/svg+xml"), (b"not an image", "image/png")):
        r = client.post(LOGO, files={"file": ("logo.svg", payload, mime)})
        assert r.status_code == 422, r.text
    assert not (shooter_root / LOGO_DIR).exists() or not any((shooter_root / LOGO_DIR).iterdir())
    assert MatchProject.load(shooter_root).identity.logo is None


def test_logo_upload_refuses_an_oversized_file(seeded) -> None:
    client, shooter_root = seeded
    big = _png() + b"\\0" * (2 * 1024 * 1024)
    r = client.post(LOGO, files={"file": ("big.png", big, "image/png")})
    assert r.status_code == 413, r.text
    assert MatchProject.load(shooter_root).identity.logo is None


def test_deleting_the_logo_clears_the_project_and_removes_the_file(seeded) -> None:
    client, shooter_root = seeded
    name = client.post(LOGO, files={"file": ("a.png", _png(), "image/png")}).json()["identity"]["logo"]
    r = client.delete(LOGO)
    assert r.status_code == 200, r.text
    assert r.json()["identity"]["logo"] is None
    assert not (shooter_root / LOGO_DIR / name).exists()


def test_the_shooter_list_carries_each_identity(seeded) -> None:
    client, _root = seeded
    client.patch(IDENTITY, json={"accent": "#123456", "club": "PK"})
    rows = client.get("/api/match/shooters").json()["shooters"]
    me = next(row for row in rows if row["slug"] == "me")
    assert me["identity"] == {"accent": "#123456", "logo": None, "club": "PK"}
