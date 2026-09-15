"""``POST /api/shooters/{slug}/export-preview`` (spec 2026-09-15 s3).

Rasterization is stubbed through the module's ``rasterizer_factory``;
the seeded project has audits but no real trims (a one-byte source), so
every still composes on the surface, which is the hosted path too.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import runtime as runtime_module
from splitsmith.overlay_raster import RasterizerUnavailableError
from splitsmith.ui import export_preview_api

from .test_ui_server import _seed_match_export_project

ROUTE = "/api/shooters/me/export-preview"


class _StubRasterizer:
    launches = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()


@contextmanager
def _stub_factory():
    _StubRasterizer.launches += 1
    yield _StubRasterizer()


@contextmanager
def _no_browser():
    raise RasterizerUnavailableError("no browser", "install one")
    yield  # pragma: no cover


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _stub_factory)
    monkeypatch.setenv(runtime_module.ENV_CACHE_DIR, str(tmp_path / "cache"))
    runtime_module._clear_runtime_cache()
    _StubRasterizer.launches = 0
    client, _root = _seed_match_export_project(tmp_path, stage_count=2)
    yield client
    runtime_module._clear_runtime_cache()


@pytest.mark.parametrize("card", ["frame", "title", "slate", "lower-third", "summary", "closing", "overlay"])
def test_each_card_answers_a_png_of_the_requested_size(client, card: str) -> None:
    r = client.post(ROUTE, json={"card": card, "stage_number": 1, "width": 480, "title_info": "L3"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (480, 270)


def test_the_same_request_hits_the_cache(client) -> None:
    body = {"card": "title", "stage_number": 1, "width": 480}
    first = client.post(ROUTE, json=body)
    launches = _StubRasterizer.launches
    assert launches == 1
    second = client.post(ROUTE, json=body)
    assert second.status_code == 200 and second.content == first.content
    assert _StubRasterizer.launches == launches
    client.post(ROUTE, json={**body, "title_info": "changed"})
    assert _StubRasterizer.launches == launches + 1


def test_unknown_fields_are_ignored_like_the_export_body(client) -> None:
    body = {"card": "slate", "stage_number": 1, "output_format": "mp4", "title_kind": "x"}
    r = client.post(ROUTE, json=body)
    assert r.status_code == 200


def test_unknown_stage_is_404(client) -> None:
    assert client.post(ROUTE, json={"card": "frame", "stage_number": 9}).status_code == 404


def test_overlay_without_shots_is_409(client, tmp_path: Path) -> None:
    audit = tmp_path / "match" / "shooters" / "me" / "audit" / "stage2.json"
    audit.write_text(
        '{"stage_number": 2, "stage_name": "Stage 2", "beep_time": 5.0, "shots": []}', encoding="utf-8"
    )
    r = client.post(ROUTE, json={"card": "overlay", "stage_number": 2})
    assert r.status_code == 409
    assert "audited shots" in r.json()["detail"]


def test_no_browser_is_503(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _no_browser)
    r = client.post(ROUTE, json={"card": "title", "stage_number": 1})
    assert r.status_code == 503
    assert "browser" in r.json()["detail"].lower()


def test_the_frame_needs_no_browser(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _no_browser)
    assert client.post(ROUTE, json={"card": "frame", "stage_number": 1}).status_code == 200


@pytest.mark.parametrize(
    "bad",
    [
        {"card": "poster", "stage_number": 1},
        {"card": "title"},
        {"card": "title", "stage_number": 1, "width": 4000},
    ],
)
def test_bad_bodies_are_422(client, bad: dict) -> None:
    assert client.post(ROUTE, json=bad).status_code == 422
