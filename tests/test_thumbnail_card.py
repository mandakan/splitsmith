"""The thumbnail card (the ``thumbnail`` logo spot): its context, the
composition over the frame, the export's fallback to a plain frame, and
the shipped template drawn in a real browser."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from splitsmith import thumbnail_card
from splitsmith.identity import ResolvedIdentity
from splitsmith.looks import load_look, thumbnail_template_for
from splitsmith.thumbnail_card import ThumbnailData, ThumbnailError, compose_thumbnail, thumbnail_context


def _png(path: Path, size: tuple[int, int], colour: tuple[int, int, int]) -> Path:
    Image.new("RGB", size, colour).save(path)
    return path


class _Layer:
    """A rasterizer that answers a half-transparent red layer and records the context."""

    def __init__(self) -> None:
        self.contexts: list[Any] = []

    def render_template(self, template: Path, *, context: Any, width: int, height: int) -> bytes:
        self.contexts.append(context)
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (255, 0, 0, 128)).save(buf, format="PNG")
        return buf.getvalue()


def test_the_shipped_look_has_a_thumbnail_template() -> None:
    template = thumbnail_template_for(load_look("splitsmith"))
    assert template is not None and template.name == "thumbnail.html"


def test_the_context_names_the_title_lines_and_only_the_logos_there_are(tmp_path: Path) -> None:
    logo = _png(tmp_path / "logo.png", (32, 32), (0, 255, 0))
    look = load_look("splitsmith")
    bare = thumbnail_context(ThumbnailData(title="Höstfinalen XI", lines=("Mathias", " ")), look=look)
    assert bare.data["thumbnail"] == {"title": "Höstfinalen XI", "lines": ["Mathias"]}
    assert "brand" not in bare.data and "event" not in bare.data and bare.data["shooters"] == []
    full = thumbnail_context(
        ThumbnailData(
            title="M",
            shooters=(ResolvedIdentity("A", "#ff2d2d", logo, None),),
            brand={"logo": logo.resolve().as_uri(), "line": None},
            event_logo=logo,
        ),
        look=look,
    )
    assert full.data["brand"]["logo"] == logo.resolve().as_uri()
    assert full.data["event"] == {"logo": logo.resolve().as_uri()}
    assert full.data["shooters"][0]["logo"] == logo.resolve().as_uri()
    assert full.size == {"width": 1280, "height": 720}


def test_a_missing_event_logo_is_left_out(tmp_path: Path) -> None:
    ctx = thumbnail_context(
        ThumbnailData(title="M", event_logo=tmp_path / "gone.png"), look=load_look("splitsmith")
    )
    assert "event" not in ctx.data


def test_compose_fills_the_frame_to_16_9_and_lays_the_card_over_it(tmp_path: Path) -> None:
    frame = _png(tmp_path / "frame.png", (1440, 1080), (0, 0, 255))  # 4:3, cropped to fill
    layer = _Layer()
    image = compose_thumbnail(frame, ThumbnailData(title="M"), look=load_look("splitsmith"), rasterizer=layer)
    assert image.size == (1280, 720) and image.mode == "RGB"
    r, g, b = image.getpixel((640, 360))
    assert r > 100 and b > 100 and g == 0, "half red over blue"
    assert len(layer.contexts) == 1


def test_an_unreadable_frame_or_a_failing_template_is_a_thumbnail_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    look = load_look("splitsmith")
    with pytest.raises(ThumbnailError, match="frame"):
        compose_thumbnail(bad, ThumbnailData(title="M"), look=look, rasterizer=_Layer())

    class _Boom:
        def render_template(self, *args: Any, **kwargs: Any) -> bytes:
            raise RuntimeError("boom")

    frame = _png(tmp_path / "frame.png", (1280, 720), (0, 0, 0))
    with pytest.raises(ThumbnailError, match="boom"):
        compose_thumbnail(frame, ThumbnailData(title="M"), look=look, rasterizer=_Boom())


def test_the_action_frame_is_the_first_stages_first_shot_in_its_own_clip(tmp_path: Path) -> None:
    from splitsmith import youtube_sidecar

    from .test_mp4_render import _carded_composition

    comp = _carded_composition(tmp_path)
    clip, at = youtube_sidecar.action_frame_source(comp)  # type: ignore[misc]
    stage = comp.stages[0]
    assert clip == stage.primary.path
    assert at == min(m.time_seconds for m in stage.markers)


# --- the export ---------------------------------------------------------------


def test_the_export_draws_the_card_and_falls_back_to_the_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the spot on the card is written through the template; with it off
    the plain frame; without a browser the export still succeeds, with the
    plain frame and a note."""
    from splitsmith import mp4_render, overlay_raster
    from splitsmith.ui import match_exports

    from .test_ui_server import _seed_match_export_project, _stub_match_export_probe, _wait_for_job

    http, _root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)

    def fake_render_mp4(comp: Any, *, output_path: Path, **kwargs: Any) -> Any:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")
        return mp4_render.Mp4RenderResult(output_path=output_path, duration_seconds=10.0)

    frames: list[Path] = []

    def fake_frame(video: Path, at: float, out: Path, **kwargs: Any) -> None:
        frames.append(out)
        out.write_bytes(b"frame")

    monkeypatch.setattr(match_exports.mp4_render, "render_mp4", fake_render_mp4)
    monkeypatch.setattr(match_exports.youtube_sidecar, "write_thumbnail", fake_frame)

    def export(spots: list[str]) -> list[str]:
        body = {
            "stage_numbers": [1],
            "include_overlay": False,
            "output_format": "mp4",
            "youtube_sidecar": True,
            "logo_spots": spots,
        }
        r = http.post("/api/shooters/me/export/match", json=body)
        assert r.status_code == 200, r.text
        job = _wait_for_job(http, r.json()["id"])
        assert job["status"] == "succeeded", job
        return list((job.get("result") or {}).get("anomalies") or [])

    real_card = match_exports._write_thumbnail_card
    drawn: list[Path] = []

    def fake_card(comp: Any, request: Any, out: Path) -> str:
        drawn.append(out)
        out.write_bytes(b"card")
        return ""

    monkeypatch.setattr(match_exports, "_write_thumbnail_card", fake_card)
    notes = export(["thumbnail"])
    assert len(drawn) == 1 and frames == [], (drawn, frames)
    assert not any("thumbnail" in n for n in notes)

    export([])
    assert len(drawn) == 1 and len(frames) == 1, "spot off: the plain frame, no card"

    class _NoBrowser:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise overlay_raster.RasterizerUnavailableError("no chromium here", "no chromium here")

    monkeypatch.setattr(match_exports, "_write_thumbnail_card", real_card)
    monkeypatch.setattr(overlay_raster, "ChromiumRasterizer", _NoBrowser)
    monkeypatch.setattr(
        thumbnail_card, "grab_frame", lambda video, at, out, **kw: _png(out, (64, 36), (1, 2, 3))
    )
    notes = export(["thumbnail"])
    assert len(frames) == 2, "no browser: the plain frame"
    assert any("youtube thumbnail is a frame of the video: no browser" in n for n in notes), notes


# --- the shipped template, in a browser ---------------------------------------


@pytest.mark.integration
def test_the_shipped_thumbnail_draws_the_title_and_both_corner_logos(tmp_path: Path) -> None:
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    frame = _png(tmp_path / "frame.png", (1280, 720), (90, 90, 90))
    brand = _png(tmp_path / "brand.png", (64, 64), (0, 0, 255))
    shooter = _png(tmp_path / "shooter.png", (64, 64), (0, 255, 0))
    data = ThumbnailData(
        title="Höstfinalen XI",
        lines=("Mathias Axell",),
        shooters=(ResolvedIdentity("Mathias Axell", None, shooter, None),),
        brand={"logo": brand.resolve().as_uri(), "line": None},
    )
    out = tmp_path / "thumb.jpg"
    try:
        with ChromiumRasterizer() as rasterizer:
            thumbnail_card.render_thumbnail_card(
                frame, out, data, look=load_look("splitsmith"), rasterizer=rasterizer
            )
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    with Image.open(out) as image:
        assert image.size == (1280, 720)
        rgb = image.convert("RGB")
        top_left = rgb.crop((0, 0, 320, 200))
        top_right = rgb.crop((960, 0, 1280, 200))
        bottom_left = rgb.crop((0, 400, 900, 720))

        def count(img: Image.Image, test: Any) -> int:
            return sum(1 for px in img.getdata() if test(*px))

        assert count(top_left, lambda r, g, b: b > 200 and r < 60 and g < 60) > 500
        assert count(top_right, lambda r, g, b: g > 200 and r < 60 and b < 60) > 500
        assert count(bottom_left, lambda r, g, b: r > 220 and g > 220 and b > 220) > 2000, "the title"


def test_the_grid_thumbnail_names_every_shooter_and_needs_the_spot(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from splitsmith.ui.server import _grid_thumbnail_data

    logo = _png(tmp_path / "logo.png", (32, 32), (0, 255, 0))
    match = SimpleNamespace(name="Höstfinalen XI")
    shooters = [ResolvedIdentity("Anna", None, logo, None), ResolvedIdentity("Bo", None, None, None)]
    on = SimpleNamespace(logo_spots=["thumbnail"], overlay_theme="splitsmith")
    data = _grid_thumbnail_data(match, on, identities=shooters, brand=None, event_logo=None)  # type: ignore[arg-type]
    assert data.title == "Höstfinalen XI" and data.lines == ("Anna · Bo",)
    assert list(data.shooters) == shooters and data.brand is None
    off = SimpleNamespace(logo_spots=["wipe"], overlay_theme="splitsmith")
    assert _grid_thumbnail_data(match, off, identities=shooters, brand=None, event_logo=None) is None  # type: ignore[arg-type]
