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


@pytest.mark.parametrize("name", ["splitsmith", "clean"])
def test_the_shipped_looks_pass_check_without_warnings(
    raster, name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1268: a long stage name shrinks to the card's width, then ellipsizes."""
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    checked = look_tools.check_look(name, prober=raster)
    problems = [(i.subject, i.level, i.message) for i in checked.items if i.level != "ok"]
    assert problems == [], problems


def test_the_probe_sees_long_text_under_a_hidden_body_and_respects_ellipsis_and_opacity(
    raster, tmp_path: Path
) -> None:
    ctx = _context()
    line = '<div style="position: absolute; left: 20px; white-space: nowrap; font-size: 60px">' + "W" * 80
    # Every starter has ``body { overflow: hidden }`` with absolute content: the body is
    # 0 px tall, and clipping against it hid every overrun (the slice-1 review).
    hidden_body = raster.probe_template(
        _page(tmp_path, "hidden.html", f"<style>body{{margin:0;overflow:hidden}}</style>{line}</div>"),
        context=ctx,
        width=640,
        height=360,
    )
    assert hidden_body.overflow and hidden_body.overflow[0][1] > 100
    ellipsized = raster.probe_template(
        _page(
            tmp_path,
            "ellipsis.html",
            '<div style="width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">'
            "<span>" + "W" * 80 + "</span></div>",
        ),
        context=ctx,
        width=640,
        height=360,
    )
    assert ellipsized.overflow == ()
    invisible = raster.probe_template(
        _page(tmp_path, "invisible.html", f'<div style="opacity: 0">{line}</div></div>'),
        context=ctx,
        width=640,
        height=360,
    )
    assert invisible.overflow == ()


@pytest.mark.parametrize(
    "script, expected",
    [
        ("window.duration = () => { throw new Error('bad duration'); };", "bad duration"),
        ("window.duration = () => 1; window.seek = () => { throw new Error('bad seek'); };", "bad seek"),
        ("window.__splitsmithFit = () => { throw new Error('bad fit'); };", "bad fit"),
    ],
)
def test_a_throwing_hook_is_a_finding_not_a_crash(raster, tmp_path: Path, script: str, expected: str) -> None:
    probe = raster.probe_template(
        _page(tmp_path, "hook.html", f"<p>ok</p><script>{script}</script>"),
        context=_context(),
        width=640,
        height=360,
    )
    assert any(expected in e for e in probe.errors), probe.errors


def test_render_template_at_a_time_seeks_there_instead_of_the_poster(raster, tmp_path: Path) -> None:
    """The Look editor's slider (#1264): ``at`` is where the template is
    seeked; without it the poster is."""
    import io

    from PIL import Image

    page = _page(
        tmp_path,
        "fade.html",
        "<div id='b' style='position:absolute;inset:0;background:#fff;opacity:0'></div><script>"
        "window.duration = () => 1; window.poster = () => 1;"
        "window.seek = (t) => { document.getElementById('b').style.opacity = String(t); };</script>",
    )

    def alpha(png: bytes) -> int:
        with Image.open(io.BytesIO(png)) as im:
            return im.convert("RGBA").getpixel((10, 10))[3]

    ctx = _context()
    assert alpha(raster.render_template(page, context=ctx, width=64, height=36)) == 255
    assert alpha(raster.render_template(page, context=ctx, width=64, height=36, at=0.0)) == 0


def test_a_script_error_names_its_line_in_the_template(raster, tmp_path: Path) -> None:
    """The template editor shows where the template broke (#1265)."""
    page = _page(
        tmp_path, "lined.html", "<p>ok</p>\n<script>\nvar a = 1;\nundefined.forEach(x => x);\n</script>"
    )
    probe = raster.probe_template(page, context=_context(), width=640, height=360)
    assert probe.errors and probe.errors[0].startswith("line 4: "), probe.errors
