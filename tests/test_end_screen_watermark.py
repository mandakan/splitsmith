"""The end screen closing card and the ``watermark`` logo spot (spec
2026-10-09 logo spots, the third PR)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from splitsmith import composition, looks, mp4_render
from splitsmith.logo_spots import PRESETS, watermark_image

from .test_mp4_render import _carded_composition, _ok, _transitioned_composition


def _logo(tmp_path: Path, colour: tuple[int, int, int] = (0, 0, 255)) -> Path:
    path = tmp_path / "brand.png"
    Image.new("RGBA", (200, 100), (*colour, 255)).save(path)
    return path


# --- the end screen -------------------------------------------------------------


def test_the_shipped_look_has_an_end_screen_closing_card() -> None:
    look = looks.load_look("splitsmith")
    template = looks.template_for(look, "closing", composition.END_SCREEN_VARIANT)
    assert template.name == "card-end-screen.html"
    assert composition.END_SCREEN_VARIANT not in looks.variants_for(look, "title_page")


def test_an_end_screen_holds_at_least_ten_seconds() -> None:
    assert composition.closing_seconds("end-screen", 3.0) == 10.0
    assert composition.closing_seconds("end-screen", 14.0) == 14.0
    assert composition.closing_seconds("rise", 3.0) == 3.0


def test_the_grid_closing_card_holds_for_the_end_screen() -> None:
    from splitsmith import match_model
    from splitsmith.compare.cards import CardOptions, title_cards

    match = match_model.Match(name="M")
    title, closing = title_cards(
        match, CardOptions(title_page=True, closing_card=True, closing_card_variant="end-screen")
    )
    assert title is not None and title.duration_seconds == 3.0
    assert closing is not None and closing.variant == "end-screen" and closing.duration_seconds == 10.0


def test_the_match_export_holds_the_end_screen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.cli import app

    from .test_match_cli_export import _capture_mp4, _seed, runner

    root = _seed(tmp_path)
    captured = _capture_mp4(monkeypatch)
    out = tmp_path / "out" / "m.mp4"
    base = ["match", "export", str(root), "--format", "mp4", "--output", str(out), "--closing-card"]
    result = runner.invoke(app, base)
    assert result.exit_code == 0, result.output
    assert captured["comp"].closing.duration_seconds == 3.0
    # The CLI's one knob sets the closing card's variant too.
    result = runner.invoke(app, [*base, "--card-variant", "end-screen"])
    assert result.exit_code == 0, result.output
    assert captured["comp"].closing.duration_seconds == 10.0


@pytest.mark.integration
def test_the_end_screen_keeps_the_right_of_the_frame_clear(tmp_path: Path) -> None:
    """Everything sits in the left two fifths: the right, where YouTube puts
    its end screen elements, is the backdrop alone."""
    from splitsmith.overlay_card import build_card_still
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    look = looks.load_look("splitsmith")
    card = composition.MatchTitle(
        text="Höstfinalen XI",
        info=("2026-09-26",),
        variant="end-screen",
        credit=True,
        brand=composition.BrandMark(logo_path=_logo(tmp_path, (0, 255, 0)), line=None),
    )
    backdrop = tmp_path / "frame.png"
    Image.new("RGB", (640, 360), (40, 40, 40)).save(backdrop)
    try:
        with ChromiumRasterizer() as rasterizer:
            image = build_card_still(
                card,
                slot="closing",
                width=640,
                height=360,
                fps=30.0,
                look=look,
                rasterizer=rasterizer,
                backdrop=backdrop,
            )
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")
    assert image is not None
    rgb = image.convert("RGB")
    left = rgb.crop((0, 0, 256, 360))
    right = rgb.crop((300, 0, 640, 360))
    greens = sum(1 for r, g, b in left.getdata() if g > 200 and r < 80 and b < 80)
    assert greens > 500, "the brand, large on the left"
    bright = sum(1 for r, g, b in right.getdata() if max(r, g, b) > 120)
    assert bright == 0, "nothing drawn on the right"


# --- the watermark --------------------------------------------------------------


def test_everything_adds_the_watermark_and_polished_does_not() -> None:
    assert "watermark" in PRESETS["everything"] and "watermark" not in PRESETS["polished"]


def test_watermark_image_scales_and_fades(tmp_path: Path) -> None:
    mark = watermark_image(_logo(tmp_path), height=50)
    assert mark is not None and mark.size == (100, 50)
    assert mark.getpixel((50, 25))[3] == round(255 * 0.8)
    wide = tmp_path / "wide.png"
    Image.new("RGBA", (1000, 10), (0, 0, 255, 255)).save(wide)
    capped = watermark_image(wide, height=50)
    assert capped is not None and capped.width == 150
    assert watermark_image(tmp_path / "gone.png", height=50) is None


def _stage_argv(calls: list[list[str]]) -> list[list[str]]:
    return [argv for argv in calls if Path(argv[-1]).name.startswith("stage_")]


def _render(tmp_path: Path, comp: composition.Composition, name: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> Any:
        calls.append(list(argv))
        Path(argv[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(argv[-1]).write_bytes(b"x")
        return _ok(argv)

    mp4_render.render_mp4(
        comp, output_path=tmp_path / f"{name}.mp4", work_dir=tmp_path / f"w-{name}", runner=runner
    )
    return calls


def test_the_watermark_rides_every_stage_and_nothing_else(tmp_path: Path) -> None:
    logo = _logo(tmp_path)
    base = _carded_composition(tmp_path)
    plain = _stage_argv(_render(tmp_path, base, "plain"))
    marked = dataclasses.replace(
        base, logo_spots=frozenset({"watermark"}), brand=composition.BrandMark(logo_path=logo, line=None)
    )
    calls = _render(tmp_path, marked, "marked")
    stages = _stage_argv(calls)
    assert stages and len(stages) == len(plain)
    height = base.sequence.height
    margin = round(height * 0.04)
    for argv in stages:
        assert str(tmp_path / "w-marked" / "watermark.png") in argv
        graph = argv[argv.index("-filter_complex") + 1]
        assert f"overlay={margin}:{margin}:format=auto[withwm]" in graph
    others = [argv for argv in calls if argv not in stages]
    assert not any("watermark.png" in token for argv in others for token in argv)
    with Image.open(tmp_path / "w-marked" / "watermark.png") as image:
        assert image.height == round(height * 0.07)


def test_without_the_spot_or_a_brand_the_stage_argv_is_unchanged(tmp_path: Path) -> None:
    base = _carded_composition(tmp_path)
    plain = _stage_argv(_render(tmp_path, base, "a"))
    no_brand = dataclasses.replace(base, logo_spots=frozenset({"watermark"}))
    other = _stage_argv(_render(tmp_path, no_brand, "b"))

    def graphs(calls: list[list[str]]) -> list[tuple[int, str]]:
        return [(len(a), a[a.index("-filter_complex") + 1]) for a in calls]

    assert graphs(plain) == graphs(other)
    assert not any("watermark" in token for argv in other for token in argv)


def test_the_watermark_moves_right_when_the_hud_is_top_left(tmp_path: Path) -> None:
    logo = _logo(tmp_path)
    comp = dataclasses.replace(
        _carded_composition(tmp_path),
        logo_spots=frozenset({"watermark"}),
        brand=composition.BrandMark(logo_path=logo, line=None),
        watermark_corner="top-right",
    )
    graph = next(a for a in _stage_argv(_render(tmp_path, comp, "r")) if "-filter_complex" in a)
    graph_text = graph[graph.index("-filter_complex") + 1]
    height = comp.sequence.height
    margin = round(height * 0.04)
    width = round(height * 0.07) * 2  # the 2:1 logo
    assert f"overlay={comp.sequence.width - margin - width}:{margin}:format=auto[withwm]" in graph_text


def test_the_watermark_reaches_the_transition_edges_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    comp = dataclasses.replace(
        _transitioned_composition(tmp_path, kind="fade"),
        logo_spots=frozenset({"watermark"}),
        brand=composition.BrandMark(logo_path=_logo(tmp_path), line=None),
    )
    calls = _render(tmp_path, comp, "t")
    edges = [a for a in calls if Path(a[-1]).name.startswith("edge_")]
    assert edges and all(any("watermark.png" in t for t in a) for a in edges)


def test_the_export_moves_the_watermark_off_a_top_left_hud(
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
    for extra in ({}, {"overlay_position": "top-left"}, {"overlay_position": "top-right"}):
        r = http.post("/api/shooters/me/export/match", json={**body, **extra})
        assert r.status_code == 200, r.text
        assert _wait_for_job(http, r.json()["id"])["status"] == "succeeded"
    assert [req.watermark_corner for req in seen] == ["top-left", "top-right", "top-left"]
