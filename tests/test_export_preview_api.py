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
    motion_seconds = 0.0
    frames_rendered = 0

    def __init__(self) -> None:
        self.frame_requests: list[tuple] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()

    def render_template(self, template, *, context, width: int, height: int) -> bytes:
        import json

        return self.png(json.dumps(context.data, ensure_ascii=False), width=width, height=height)

    def engine_version(self) -> str:
        return "fake"

    def render_template_frames(
        self, template, *, context, width: int, height: int, fps: float, max_seconds: float
    ):
        """A still unless ``motion_seconds`` is set; frames are blank and
        counted in ``frames_rendered`` as they are pulled."""
        import math

        from splitsmith.overlay_raster import TemplateFrames

        pass
        self.frame_requests.append((template, context.model_dump(), width, height, fps, max_seconds))
        duration = self.motion_seconds
        count = 1 if duration <= 0 else max(1, math.ceil(min(duration, max_seconds) * fps - 1e-9))

        def frames():
            # Each frame differs (a moving template's do), so an encoder that
            # merges identical frames still sees the motion.
            for i in range(count):
                self.frames_rendered += 1
                yield bytes([255, 255, 255, min(255, 40 * i)]) * (width * height)

        return TemplateFrames(
            duration=duration, frame_count=count, width=width, height=height, frames=frames()
        )


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


def test_a_re_audit_moves_the_cache_key_locally(client, tmp_path: Path) -> None:
    """Local audit docs always report version 0, so the key must follow the content."""
    body = {"card": "overlay", "stage_number": 1, "width": 480}
    assert client.post(ROUTE, json=body).status_code == 200
    launches = _StubRasterizer.launches
    audit = tmp_path / "match" / "shooters" / "me" / "audit" / "stage1.json"
    before = audit.read_text(encoding="utf-8")
    after = before.replace('"ms_after_beep": 500', '"ms_after_beep": 750')
    assert after != before
    audit.write_text(after, encoding="utf-8")
    assert client.post(ROUTE, json=body).status_code == 200
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


def test_a_saturated_renderer_is_429_and_launches_nothing(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """The preview shares the process-wide render bound with the share cards."""
    import threading

    from splitsmith.ui import render_bound

    slots = threading.BoundedSemaphore(render_bound.RENDER_CONCURRENCY)
    monkeypatch.setattr(render_bound, "_slots", slots)
    for _ in range(render_bound.RENDER_CONCURRENCY):
        assert slots.acquire(timeout=1)
    r = client.post(ROUTE, json={"card": "title", "stage_number": 1, "width": 480})
    assert r.status_code == 429, r.text
    assert int(r.headers["retry-after"]) > 0
    assert _StubRasterizer.launches == 0

    for _ in range(render_bound.RENDER_CONCURRENCY):
        slots.release()
    r = client.post(ROUTE, json={"card": "title", "stage_number": 1, "width": 480})
    assert r.status_code == 200
    assert _StubRasterizer.launches == 1
    for _ in range(render_bound.RENDER_CONCURRENCY):
        assert slots.acquire(timeout=0.01)


def test_the_title_preview_carries_the_division_unless_turned_off(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith.match_project import MatchProject

    shooter_root = tmp_path / "match" / "shooters" / "me"
    project = MatchProject.load(shooter_root)
    project.competitor_division = "Classic Major"
    project.save(shooter_root)
    pages: list[str] = []

    class _Capture(_StubRasterizer):
        def png(self, html: str, *, width: int, height: int) -> bytes:
            pages.append(html)
            return super().png(html, width=width, height=height)

    @contextmanager
    def _capture_factory():
        yield _Capture()

    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _capture_factory)
    body = {"card": "title", "stage_number": 1, "width": 480}
    assert client.post(ROUTE, json=body).status_code == 200
    assert client.post(ROUTE, json={**body, "title_division": False}).status_code == 200
    assert len(pages) == 2, "the option must move the cache key"
    assert "Classic Major" in pages[0]
    assert "Classic Major" not in pages[1]


def test_the_preview_takes_the_look_and_the_variant(client) -> None:
    """Slice 6 (#1246): ``look`` and ``variant`` ride the body and the
    cache key; an unknown Look is a 422 that names the installed ones."""
    default = client.post(ROUTE, json={"card": "title", "stage_number": 1, "width": 480})
    rise = client.post(ROUTE, json={"card": "title", "stage_number": 1, "width": 480, "variant": "rise"})
    clean = client.post(ROUTE, json={"card": "title", "stage_number": 1, "width": 480, "look": "clean"})
    assert default.status_code == rise.status_code == clean.status_code == 200
    assert _StubRasterizer.launches == 3, "three keys, three renders"
    refused = client.post(ROUTE, json={"card": "title", "stage_number": 1, "look": "nope"})
    assert refused.status_code == 422 and "splitsmith" in refused.text


# --- moving previews (#1249) ---------------------------------------------------------------


def test_an_animated_card_previews_as_a_looping_webp(client) -> None:
    _StubRasterizer.motion_seconds = 0.5
    try:
        r = client.post(ROUTE, json={"card": "title", "stage_number": 1, "width": 480, "motion": True})
    finally:
        _StubRasterizer.motion_seconds = 0.0
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (480, 270)
        assert getattr(im, "n_frames", 1) > 1
        assert im.info.get("loop") == 0


def test_a_still_card_stays_a_png_even_when_motion_is_asked(client) -> None:
    r = client.post(ROUTE, json={"card": "slate", "stage_number": 1, "width": 480, "motion": True})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


@pytest.mark.parametrize("card", ["frame", "summary", "overlay"])
def test_cards_without_a_template_never_move(client, card: str) -> None:
    _StubRasterizer.motion_seconds = 0.5
    try:
        r = client.post(ROUTE, json={"card": card, "stage_number": 1, "width": 480, "motion": True})
    finally:
        _StubRasterizer.motion_seconds = 0.0
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_motion_and_still_are_cached_apart(client) -> None:
    _StubRasterizer.motion_seconds = 0.5
    try:
        body = {"card": "closing", "stage_number": 1, "width": 480}
        still = client.post(ROUTE, json=body)
        moving = client.post(ROUTE, json={**body, "motion": True})
        again = client.post(ROUTE, json={**body, "motion": True})
    finally:
        _StubRasterizer.motion_seconds = 0.0
    assert still.headers["content-type"] == "image/png"
    assert moving.headers["content-type"] == "image/webp" and again.content == moving.content
    assert _StubRasterizer.launches == 2
