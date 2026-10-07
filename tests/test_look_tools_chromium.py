"""The real prober, the starters and the shipped Looks through Chromium (issue #1262)."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import look_tools
from splitsmith.look_template import TemplateContext, engine_block, shared_url, theme_tokens
from splitsmith.overlay_html import single_css
from splitsmith.overlay_layout import CellScale
from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError
from splitsmith.overlay_theme import load_theme

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def raster():
    try:
        with ChromiumRasterizer() as r:
            yield r
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")


def _context() -> TemplateContext:
    theme = load_theme("splitsmith")
    return TemplateContext(
        theme=theme_tokens(theme),
        data={"card": {"text": "x"}, "groups": [], "shooters": []},
        size={"width": 640, "height": 360},
        fps=30,
        engine=engine_block(
            css=single_css(width=640, height=360, scale=CellScale.for_cell(360), theme=theme)
        ),
        assets={"shared": shared_url()},
    )


def _page(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(
        f"<!doctype html><html><head><meta charset='utf-8'></head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return path


def test_the_probe_sees_script_errors_fonts_overflow_and_the_animation_hooks(raster, tmp_path: Path) -> None:
    ctx = _context()
    broken = raster.probe_template(
        _page(tmp_path, "broken.html", "<script>undefined.forEach(x => x)</script>"),
        context=ctx,
        width=640,
        height=360,
    )
    assert broken.errors and "forEach" in broken.errors[0]
    wide = raster.probe_template(
        _page(
            tmp_path,
            "wide.html",
            '<div style="font-family: Inter, sans-serif; font-size: 80px; white-space: nowrap; '
            'position: absolute; left: 20px">'
            "Stage 7 - The Very Long Corridor Of Doom</div>",
        ),
        context=ctx,
        width=640,
        height=360,
    )
    assert "Inter" in wide.families
    assert wide.overflow and wide.overflow[0][1] > 100
    animated = raster.probe_template(
        _page(
            tmp_path,
            "anim.html",
            "<p>ok</p><script>window.duration = () => 0.8; window.poster = () => 1.4;</script>",
        ),
        context=ctx,
        width=640,
        height=360,
    )
    assert (animated.duration, animated.poster, animated.has_seek) == (0.8, 1.4, False)
    still = raster.probe_template(
        _page(tmp_path, "still.html", "<p style='font-family: \"Splitsmith Display\"'>ok</p>"),
        context=ctx,
        width=640,
        height=360,
    )
    assert still.errors == () and still.overflow == () and still.duration == 0


@pytest.mark.parametrize("starter", sorted(look_tools.STARTERS))
def test_every_starter_passes_check(
    raster, starter: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    look_tools.new_look("mine", starter=starter)
    report = look_tools.check_look("mine", prober=raster)
    problems = [(i.subject, i.level, i.message) for i in report.items if i.level != "ok"]
    assert problems == [], problems


@pytest.mark.parametrize("name", ["splitsmith", "clean"])
def test_the_shipped_looks_have_no_errors(
    raster, name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    checked = look_tools.check_look(name, prober=raster)
    errors = [(i.subject, i.message) for i in checked.items if i.level == "error"]
    assert errors == [], errors


@pytest.mark.xfail(
    strict=True, reason="#1268: the fit policy fits height only; a long stage name runs off the card"
)
def test_the_shipped_looks_pass_check_without_warnings(
    raster, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    checked = look_tools.check_look("splitsmith", prober=raster)
    problems = [(i.subject, i.level, i.message) for i in checked.items if i.level != "ok"]
    assert problems == [], problems
