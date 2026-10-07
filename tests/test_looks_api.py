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
