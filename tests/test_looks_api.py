"""``GET /api/looks`` and the preview files (spec 2026-10-06 section 4, #1246)."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_ui_server import _seed_match_export_project


@pytest.fixture
def client(tmp_path: Path):
    client, _root = _seed_match_export_project(tmp_path, stage_count=1)
    return client


def test_get_looks_lists_the_shipped_looks(client) -> None:
    body = client.get("/api/looks").json()
    assert [look["name"] for look in body["looks"]][:2] == ["splitsmith", "clean"]
    splitsmith = body["looks"][0]
    assert splitsmith["slots"]["title_page"][1] == {
        "name": "rise",
        "preview": "/api/looks/splitsmith/preview/title_page-rise.png",
    }
    assert splitsmith["preview"] == "/api/looks/splitsmith/preview/look.png"


def test_borrowed_previews_are_served_from_the_shipped_owner(client, tmp_path: Path, monkeypatch) -> None:
    """Review of #1246: ``_shipped`` is the owner of every borrowed
    preview, so a user Look shadowing ``splitsmith`` cannot hide them."""
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path / "home"))
    ok = client.get("/api/looks/_shipped/preview/look.png")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png"
    assert client.get("/api/looks/_shipped/preview/nope.png").status_code == 404


def test_preview_files_are_served_with_a_cache_header(client) -> None:
    ok = client.get("/api/looks/splitsmith/preview/slate-rise.png")
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/png"
    assert ok.headers["cache-control"] == "public, max-age=3600"
    assert ok.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize(
    "path",
    [
        "/api/looks/nope/preview/slate-rise.png",
        "/api/looks/splitsmith/preview/look.json",
        "/api/looks/splitsmith/preview/..%2Flook.json",
        "/api/looks/splitsmith/preview/missing.png",
        "/api/looks/splitsmith/preview/Slate-Rise.png",
    ],
)
def test_everything_else_is_404(client, path: str) -> None:
    assert client.get(path).status_code == 404, path


def test_the_looks_routes_are_registered_once(client) -> None:
    routes = [
        (method, r.path)
        for r in client.app.routes
        if getattr(r, "path", "").startswith("/api/looks")
        for method in sorted(getattr(r, "methods", ()))
    ]
    assert sorted(routes) == [
        ("DELETE", "/api/looks/{name}"),
        ("GET", "/api/looks"),
        ("GET", "/api/looks/fonts/{font_id}"),
        ("GET", "/api/looks/{name}"),
        ("GET", "/api/looks/{name}/fonts"),
        ("GET", "/api/looks/{name}/fonts/{file}"),
        ("GET", "/api/looks/{name}/preview/{file}"),
        ("GET", "/api/looks/{name}/samples"),
        ("GET", "/api/looks/{name}/templates"),
        ("POST", "/api/looks/{name}/check"),
        ("POST", "/api/looks/{name}/duplicate"),
        ("POST", "/api/looks/{name}/fonts"),
        ("POST", "/api/looks/{name}/reveal"),
        ("PUT", "/api/looks/{name}"),
        ("PUT", "/api/looks/{name}/templates"),
    ]


def test_get_looks_lists_the_transition_families(client) -> None:
    """Issue #1259: the families come from the one list, each with its
    directions (the first is what the tile selects) and a looping preview
    the preview route serves."""
    from splitsmith import composition

    transitions = client.get("/api/looks").json()["transitions"]
    assert [t["id"] for t in transitions] == [f.id for f in composition.XFADE_FAMILIES]
    wind = next(t for t in transitions if t["id"] == "wind")
    assert wind["label"] == "Wind"
    assert [d["kind"] for d in wind["directions"]] == ["hlwind", "hrwind", "vuwind", "vdwind"]
    for family in transitions:
        assert family["preview"] == f"/api/looks/_transitions/preview/{family['id']}.webp", family["id"]
        served = client.get(family["preview"])
        assert served.status_code == 200 and served.headers["content-type"] == "image/webp", family["id"]
