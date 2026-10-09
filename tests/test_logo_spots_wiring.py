"""Logo spots end to end (spec 2026-10-09): the grid's summaries, the
defaults every place a user starts from, the threading from each request
and CLI to the renderer, the preview, and the shipped sting's brand."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from splitsmith.compare.mp4_grid import GridStagePlan, GridTile
from splitsmith.compare.overlay_sprites import SpriteGeometry, TilePlacement
from splitsmith.compare.overlay_summary import build_hold_still, build_match_summary_grid_still
from splitsmith.export_presets import ExportPresetBody
from splitsmith.overlay_theme import load_theme

GREEN = (0, 255, 0)


def _logo(tmp_path: Path, name: str = "logo.png", colour: tuple[int, int, int] = GREEN) -> Path:
    path = tmp_path / name
    Image.new("RGBA", (64, 64), (*colour, 255)).save(path)
    return path


def _top_right(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    x0, y0, width, height = box
    side = round(height * 0.09)
    margin = round(height * 0.04)
    return image.getpixel((x0 + width - margin - side // 2, y0 + margin + side // 2))


def _plan() -> GridStagePlan:
    tiles = tuple(
        GridTile(
            label=label,
            trim_path=None,
            beep_offset_in_clip=0.0,
            seek_seconds=0.0,
            lead_pad_seconds=0.0,
            source_duration_seconds=0.0,
            row=0,
            col=col,
        )
        for col, label in enumerate(("Anna", "Bo"))
    )
    return GridStagePlan(
        stage_number=1, stage_name="S", tiles=tiles, duration_seconds=5.0, audio_label="Anna", rows=1, cols=2
    )


# --- the grid ---------------------------------------------------------------


def test_the_grid_hold_puts_each_logo_in_its_own_tile(tmp_path: Path) -> None:
    geometry = SpriteGeometry(canvas_width=640, canvas_height=180, rows=1, cols=2)
    placements = (
        TilePlacement(label="Anna", row=0, col=0, present=True),
        TilePlacement(label="Bo", row=0, col=1, present=True),
    )
    still = build_hold_still(
        placements, {}, {}, geometry, theme=load_theme("clean"), logos={"Bo": _logo(tmp_path)}
    )
    assert _top_right(still, (320, 0, 320, 180)) == GREEN
    assert _top_right(still, (0, 0, 320, 180)) != GREEN, "Anna has no logo"
    plain = build_hold_still(placements, {}, {}, geometry, theme=load_theme("clean"))
    assert _top_right(plain, (320, 0, 320, 180)) != GREEN


def test_a_filler_tile_never_carries_a_logo(tmp_path: Path) -> None:
    geometry = SpriteGeometry(canvas_width=640, canvas_height=180, rows=1, cols=2)
    placements = (TilePlacement(label="Bo", row=0, col=1, present=False),)
    still = build_hold_still(
        placements, {}, {}, geometry, theme=load_theme("clean"), logos={"Bo": _logo(tmp_path)}
    )
    assert _top_right(still, (320, 0, 320, 180)) != GREEN


def test_the_grid_match_summary_puts_the_logo_below_the_title_strip(tmp_path: Path) -> None:
    from splitsmith.compare.overlay_summary import match_summary_strip_height

    still = build_match_summary_grid_still(
        _plan(),
        {},
        {},
        width=640,
        height=360,
        title="M",
        theme=load_theme("clean"),
        rasterizer=None,
        logos={"Anna": _logo(tmp_path)},
    )
    strip = match_summary_strip_height(360, 1)
    assert _top_right(still, (0, strip, 320, 360 - strip)) == GREEN


# --- defaults ---------------------------------------------------------------


def test_polished_is_the_default_everywhere_a_user_starts() -> None:
    from splitsmith.ui.export_preview_api import ExportPreviewRequest
    from splitsmith.ui.exports_api import CompareGridRequest, MatchExportRequest
    from splitsmith.ui.match_exports import MatchExportRequestData

    assert MatchExportRequest(stage_numbers=[1]).logo_spots == ["summaries", "wipe"]
    assert CompareGridRequest(stage_numbers=[1], audio_from="a").logo_spots == ["summaries", "wipe"]
    assert ExportPresetBody().logo_spots == ["summaries", "wipe"]
    assert ExportPreviewRequest(card="sting", stage_number=1).logo_spots == ["summaries", "wipe"]
    # The renderers' own default is none: a caller that says nothing draws what it drew.
    assert MatchExportRequestData.__dataclass_fields__["logo_spots"].default == frozenset()


def test_a_preset_drops_a_spot_it_does_not_know_and_keeps_the_rest() -> None:
    body = ExportPresetBody.model_validate({"logo_spots": ["wipe", "hologram"]})
    assert body.logo_spots == ["wipe"]


def test_a_request_refuses_an_unknown_spot() -> None:
    from pydantic import ValidationError

    from splitsmith.ui.exports_api import MatchExportRequest

    with pytest.raises(ValidationError):
        MatchExportRequest(stage_numbers=[1], logo_spots=["hologram"])


# --- threading --------------------------------------------------------------


def test_the_match_export_threads_the_spots_and_the_brand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith.ui import match_exports as match_exports_mod

    from .test_ui_server import _seed_match_export_project, _stub_match_export_probe, _wait_for_job

    http, _root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)
    seen: list = []
    real = match_exports_mod.export_match

    def capture(*args: object, **kwargs: object) -> object:
        seen.append(kwargs["request"])
        return real(*args, **kwargs)

    monkeypatch.setattr(match_exports_mod, "export_match", capture)
    body = {"stage_numbers": [1], "include_overlay": False}
    for extra in ({}, {"logo_spots": []}):
        r = http.post("/api/shooters/me/export/match", json={**body, **extra})
        assert r.status_code == 200, r.text
        assert _wait_for_job(http, r.json()["id"])["status"] == "succeeded"
    assert [req.logo_spots for req in seen] == [frozenset({"wipe", "summaries"}), frozenset()]


def test_the_composition_carries_the_spots_and_the_account_brand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith.cli import app

    from .test_match_cli_export import _capture_mp4, _seed, runner

    root = _seed(tmp_path)
    captured = _capture_mp4(monkeypatch)
    out = tmp_path / "out" / "m.mp4"
    result = runner.invoke(app, ["match", "export", str(root), "--format", "mp4", "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert captured["comp"].logo_spots == {"wipe", "summaries"}
    result = runner.invoke(
        app, ["match", "export", str(root), "--format", "mp4", "--output", str(out), "--logos", "cards"]
    )
    assert result.exit_code == 0, result.output
    assert captured["comp"].logo_spots == frozenset()
    result = runner.invoke(
        app, ["match", "export", str(root), "--format", "mp4", "--output", str(out), "--logos", "banner"]
    )
    assert result.exit_code == 2


def test_the_grid_job_threads_the_spots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_compare_grid_endpoint import (
        _fake_probe,
        _fake_render_grid_mp4,
        _match_create_app,
        _MatchClient,
        _seed_match,
        _wait_for_job,
        _write_trims,
        mp4_grid_mod,
        pl_mod,
    )

    captured: list[dict[str, Any]] = []

    def fake_render(shooters: Any, *, audio_label: str, output_path: Path, **kwargs: Any) -> Any:
        captured.append(kwargs)
        return _fake_render_grid_mp4(shooters, audio_label=audio_label, output_path=output_path)

    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", fake_render)
    match_root = _seed_match(tmp_path, shooters=["mathias"], stage_numbers=[1])
    _write_trims(match_root, slug="mathias", stage_numbers=[1])
    client = _MatchClient(_match_create_app(project_root=match_root, project_name="Compare Match"))
    body = {"stage_numbers": [1], "audio_from": "mathias"}
    for extra in ({}, {"logo_spots": ["summaries"]}):
        response = client.post("/api/match/compare-export", json={**body, **extra})
        assert response.status_code == 200
        assert _wait_for_job(client, response.json()["id"])["status"] == "succeeded"
    assert [c["logo_spots"] for c in captured[-2:]] == [{"wipe", "summaries"}, {"summaries"}]


def test_the_compare_cli_threads_the_spots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_compare_cli_mp4 import _capture_render, _invoke_mp4, _patch_probe, _seed_match_with_stages

    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    _patch_probe(monkeypatch)
    captured = _capture_render(monkeypatch)
    result = _invoke_mp4(match_root, tmp_path / "out.mp4")
    assert result.exit_code == 0, result.output
    assert captured["logo_spots"] == {"wipe", "summaries"}
    result = _invoke_mp4(match_root, tmp_path / "out.mp4", "--logos", "wipe")
    assert result.exit_code == 0, result.output
    assert captured["logo_spots"] == {"wipe"}


# --- the preview ------------------------------------------------------------


def test_the_key_moves_only_on_a_card_the_spots_touch() -> None:
    from splitsmith.export_preview import PreviewSpec, preview_key

    def key(card: str, spots: frozenset[str]) -> str:
        spec = PreviewSpec(card=card, stage_number=1, logo_spots=spots)  # type: ignore[arg-type]
        return preview_key(spec, slug="s", project_updated_at="t", audit="a")

    assert key("title", frozenset({"wipe", "summaries"})) == key("title", frozenset())
    assert key("sting", frozenset({"summaries"})) == key("sting", frozenset())
    assert key("sting", frozenset({"wipe"})) != key("sting", frozenset())
    assert key("summary", frozenset({"summaries"})) != key("summary", frozenset())


def test_the_sting_preview_draws_your_brand_with_the_wipe_spot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith import runtime as runtime_module
    from splitsmith.composition import BrandMark
    from splitsmith.ui import export_preview_api

    from .test_look_editor_api import _factory, _Recorder
    from .test_ui_server import _seed_match_export_project

    logo = _logo(tmp_path)
    monkeypatch.setattr(export_preview_api, "rasterizer_factory", _factory)
    monkeypatch.setattr(export_preview_api, "load_brand", lambda _store: BrandMark(logo_path=logo, line=None))
    monkeypatch.setenv(runtime_module.ENV_CACHE_DIR, str(tmp_path / "cache"))
    runtime_module._clear_runtime_cache()
    _Recorder.calls = []
    http, _root = _seed_match_export_project(tmp_path, stage_count=1)
    body = {"card": "sting", "stage_number": 1, "width": 480, "variant": "wipe"}
    assert http.post("/api/shooters/me/export-preview", json=body).status_code == 200
    assert _Recorder.calls[-1]["data"]["brand"]["logo"] == logo.resolve().as_uri()
    off = {**body, "logo_spots": ["summaries"]}
    assert http.post("/api/shooters/me/export-preview", json=off).status_code == 200
    assert "brand" not in _Recorder.calls[-1]["data"]
    runtime_module._clear_runtime_cache()


# --- the shipped sting ------------------------------------------------------


@pytest.mark.integration
def test_the_shipped_sting_carries_your_brand_over_the_shooters_logo(tmp_path: Path) -> None:
    import io

    from splitsmith import looks
    from splitsmith.identity import ResolvedIdentity
    from splitsmith.look_sting import sting_context
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError
    from splitsmith.overlay_theme import theme_for

    shooter_logo = _logo(tmp_path, "shooter.png", GREEN)
    brand_logo = _logo(tmp_path, "brand.png", (0, 0, 255))
    look = looks.load_look("splitsmith")
    template = looks.sting_template_for(look, "wipe")
    assert template is not None

    def counts(brand: dict[str, Any] | None) -> tuple[int, int]:
        ctx = sting_context(
            kind="sting:wipe",
            seconds=1.0,
            from_label="Stage 01",
            to_label="Stage 02",
            width=640,
            height=360,
            fps=30,
            theme=theme_for(look),
            shooters=[ResolvedIdentity("A", "#ff2d2d", shooter_logo, None)],
            brand=brand,
        )
        png = rasterizer.render_template(template, context=ctx, width=640, height=360)
        with Image.open(io.BytesIO(png)) as im:
            pixels = list(im.convert("RGBA").getdata())
        green = sum(1 for r, g, b, a in pixels if a > 200 and g > 200 and r < 80 and b < 80)
        blue = sum(1 for r, g, b, a in pixels if a > 200 and b > 200 and r < 80 and g < 80)
        return green, blue

    try:
        with ChromiumRasterizer() as rasterizer:
            with_brand = counts({"logo": brand_logo.resolve().as_uri(), "line": None})
            without = counts(None)
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    assert with_brand[1] > 100 and with_brand[0] == 0, with_brand
    assert without[0] > 100 and without[1] == 0, without
