"""The grade gallery's routes: the presets for everyone, your own LUTs and
their upload on the desktop only, and a thumbnail per grade."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import grade
from splitsmith.grade import Lut


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from splitsmith import runtime as runtime_module

    from .ui._helpers import _match_create_app, _MatchClient

    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path / "home"))
    # The thumbnails' cache, never the real one under ~/Library/Caches.
    monkeypatch.setenv(runtime_module.ENV_CACHE_DIR, str(tmp_path / "cache"))
    runtime_module._clear_runtime_cache()
    yield _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="Grades"))
    runtime_module._clear_runtime_cache()


def _cube_bytes() -> bytes:
    return grade.write_cube(Lut(size=2, table=1.0 - grade.identity(2).table)).encode()


def test_the_presets_are_listed_with_a_thumbnail_each(client) -> None:
    r = client.get("/api/grades")
    assert r.status_code == 200, r.text
    presets = r.json()["presets"]
    assert [p["id"] for p in presets][:1] and {p["id"] for p in presets} == {
        f"preset:{n}" for n in grade.preset_names()
    }
    vivid = next(p for p in presets if p["id"] == "preset:vivid")
    assert vivid["label"] == "Vivid"
    png = client.get(vivid["preview"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    none = client.get("/api/grades/preview?grade=none")
    assert none.status_code == 200 and none.content != png.content


def test_an_uploaded_lut_is_yours_to_choose(client) -> None:
    r = client.post(
        "/api/grades", files={"file": ("Film Look.cube", _cube_bytes(), "application/octet-stream")}
    )
    assert r.status_code == 201, r.text
    chosen = r.json()
    assert chosen["id"].startswith("user:film-look-") and chosen["label"] == "film-look"
    listed = client.get("/api/grades/user").json()["user"]
    assert [u["id"] for u in listed] == [chosen["id"]]
    assert client.get(listed[0]["preview"]).status_code == 200


def test_an_upload_that_is_not_a_3d_lut_is_refused(client) -> None:
    r = client.post("/api/grades", files={"file": ("x.cube", b"LUT_1D_SIZE 4\n", "application/octet-stream")})
    assert r.status_code == 422 and "3D" in r.json()["detail"]
    big = b"0 0 0\n" * (grade.MAX_BYTES // 6 + 10)
    assert client.post("/api/grades", files={"file": ("big.cube", big, "text/plain")}).status_code == 413


def test_a_preview_naming_nothing_real_is_a_404(client) -> None:
    assert client.get("/api/grades/preview?grade=preset:nope").status_code == 404
    assert client.get("/api/grades/preview?grade=user:x-0123456789ab.cube").status_code == 404


def test_your_luts_are_desktop_only() -> None:
    from splitsmith.ui import route_scope

    assert ("GET", "/api/grades/user") in route_scope.LOCAL_ONLY_ROUTES
    assert ("POST", "/api/grades") in route_scope.LOCAL_ONLY_ROUTES


def test_thumbnail_strength_is_whole_percent_so_the_cache_stays_bounded(client) -> None:
    from splitsmith.runtime import runtime

    for strength in (0.5, 0.501, 0.4999):
        assert client.get(f"/api/grades/preview?grade=preset:noir&strength={strength}").status_code == 200
    assert len(list((runtime().cache_dir / "grade-previews").glob("*.png"))) == 1
