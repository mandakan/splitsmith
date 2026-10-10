"""The live HUD on an upright video keeps out of the platform safe area (issue #1394, part 3).

The area is written out by hand here (13 % of the height along the bottom, a
column 12 % of the width from 40 % down) rather than read back from
``safe_area``, so a change there cannot quietly move what these tests hold
the HUD to.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import look_tools
from splitsmith.looks import load_look, overlay_template_for
from splitsmith.overlay_html import single_css, single_html
from splitsmith.overlay_hud import HudOptions, declared_positions, hud_options_data, hud_stage_data
from splitsmith.overlay_hud_render import hud_context
from splitsmith.overlay_layout import CellScale
from splitsmith.overlay_render import build_frame_states, classic_cell_style
from splitsmith.overlay_single import build_overlay_runs, run_groups
from splitsmith.overlay_theme import load_theme

BEEP = look_tools.HUD_SAMPLE_BEEP
STYLES = ("plate", "pips", "ticker", "timeline", "minimal")


def _stage(sample_index: int = 0) -> dict:
    sample = look_tools.hud_samples()[sample_index]
    return hud_stage_data(sample.shots, beep_in_clip=BEEP, events=sample.events)


def _context(width: int, height: int, position: str | None = None, stage: dict | None = None):
    options = HudOptions(reload_chip=True, stage_bar=True, speed_colors=True)
    return hud_context(
        stage=stage or _stage(),
        options=hud_options_data(options, position),  # type: ignore[arg-type]
        theme=load_theme("splitsmith"),
        width=width,
        height=height,
        fps=30.0,
    )


# --- the context -------------------------------------------------------------------


def test_an_upright_page_hands_the_template_the_safe_area_as_data_and_css() -> None:
    context = _context(608, 1080)
    # 13 % of 1080, 12 % of 608, 40 % of 1080.
    assert context.data["safe_area"] == {"bottom": 140, "right": 73, "right_top": 432}
    css = context.engine["css"]
    assert css.endswith(":root { --safe-bottom: 140px; --safe-right: 73px; --safe-right-top: 432px; }")


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080), (1080, 1080), (1440, 1080)])
def test_a_square_or_wider_page_gets_the_context_it_always_got(size: tuple[int, int]) -> None:
    width, height = size
    context = _context(width, height)
    assert set(context.data) == {"stage", "options"}
    theme = load_theme("splitsmith")
    assert context.engine["css"] == single_css(
        width=width, height=height, scale=CellScale.for_cell(height), theme=theme
    )


def test_classic_pads_only_an_upright_sprite() -> None:
    assert classic_cell_style(1920, 1080) is None
    assert classic_cell_style(1080, 1080) is None
    assert classic_cell_style(1080, 1920) == "padding-bottom:250px"


# --- the pixels (Chromium) ------------------------------------------------------------


@pytest.fixture(scope="module")
def raster():
    from splitsmith.overlay_raster import ChromiumRasterizer, RasterizerUnavailableError

    try:
        with ChromiumRasterizer() as r:
            yield r
    except RasterizerUnavailableError as exc:
        pytest.skip(f"no Chromium: {exc}")


def _moments(stage: dict) -> list[float]:
    shots = [s["t"] for s in stage["shots"]]
    mid = len(shots) // 2
    times = [0.5, shots[0] + 0.05, shots[mid] + 0.05, shots[mid] + 0.12]
    for reload in stage["reloads"]:
        times += [(reload["start"] + reload["end"]) / 2, reload["end"] + 0.1]
    return times + [shots[-1] + 0.3, shots[-1] + 2.0]


def _cases() -> list[tuple[str, str | None, tuple[int, int], int]]:
    look = load_look("splitsmith")
    cases: list[tuple[str, str | None, tuple[int, int], int]] = []
    for style in STYLES:
        template = overlay_template_for(look, style)
        assert template is not None
        positions: tuple[str | None, ...] = declared_positions(template) or (None,)
        # Every position on the page a 1080x1920 (or 720x1280) video renders
        # at, on the twelve-round stage with regions and on 32 rounds; the
        # default position also on a 4:5 page.
        cases += [(style, position, (608, 1080), sample) for position in positions for sample in (0, 1)]
        cases += [(style, positions[0], (864, 1080), 0)]
    return cases


@pytest.mark.integration
@pytest.mark.parametrize(("style", "position", "size", "sample"), _cases())
def test_every_upright_hud_style_keeps_out_of_the_safe_area(
    raster, style: str, position: str | None, size: tuple[int, int], sample: int
) -> None:
    width, height = size
    template = overlay_template_for(load_look("splitsmith"), style)
    assert template is not None
    stage = _stage(sample)
    context = _context(width, height, position, stage)
    found = []
    for at in _moments(stage):
        probe = raster.probe_template(template, context=context, width=width, height=height, at=at)
        assert not probe.errors, probe.errors
        if probe.unsafe:
            found.append((round(at, 2), probe.unsafe[:3]))
    assert found == []


@pytest.mark.integration
@pytest.mark.parametrize(("width", "height"), [(1080, 1920), (720, 1280)])
def test_classic_upright_split_sits_above_the_bottom_band(raster, width: int, height: int) -> None:
    states = build_frame_states(
        shot_times_in_clip=[1.5, 1.75], beep_time_in_clip=0.5, fps=30.0, duration_seconds=3.0
    )
    (run,) = [r for r in build_overlay_runs(states) if r.last_split is not None and r.shots_fired == 2]
    html = single_html(
        run_groups(run),
        width=width,
        height=height,
        scale=CellScale.for_cell(height),
        theme=load_theme("splitsmith"),
        cell_style=classic_cell_style(width, height),
    )
    alpha = Image.open(io.BytesIO(raster.png(html, width=width, height=height))).getchannel("A")
    line = height - round(height * 0.13)
    assert alpha.crop((0, line, width, height)).getbbox() is None, "ink in the bottom band"
    column = alpha.crop((width - round(width * 0.12), round(height * 0.40), width, height))
    assert column.getbbox() is None, "ink in the button column"
    # The split is still drawn, low in the frame, just above the band.
    ink = alpha.crop((0, height // 2, width, line)).getbbox()
    assert ink is not None and ink[3] > line - height // 2 - height * 0.1


@pytest.mark.integration
def test_looks_check_warns_about_a_style_that_draws_into_the_safe_area(
    raster, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    root = look_tools.new_look("mine", starter="hud")
    hud = root / "hud.html"
    # A clock pinned to the foot of the page: fine on a landscape video,
    # under the caption on an upright one.
    hud.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><script>"
        "document.write('<style>' + window.splitsmith.engine.css + '</style>');</script>"
        "<style>html, body { margin: 0; background: transparent; overflow: hidden; }"
        "#clock { position: absolute; left: 4vh; bottom: 2vh; font: 700 6vh 'Splitsmith Mono'; }</style>"
        "</head><body><div id='clock'>0.00</div><script>"
        "window.settle = function () { return 0; };"
        "window.seek = function (t) {};"
        "</script></body></html>",
        encoding="utf-8",
    )
    report = look_tools.check_look("mine", prober=raster)
    warnings = [i.message for i in report.items if i.level == "warn"]
    assert any('"0.00"' in m and "safe area" in m and "upright" in m for m in warnings), warnings


# --- reuse: an upright overlay drawn before the safe area is drawn again ------------------


def _record(*, variant: str = "default", upright: bool = False, revision: str = "rev-1") -> dict:
    from splitsmith.overlay_hud import overlay_settings

    return overlay_settings(
        look="splitsmith",
        variant=variant,
        options=HudOptions(),
        codec="prores-4444",
        max_height=None,
        max_fps=None,
        audit_revision=revision,
        upright=upright,
    )


def _legacy_record(variant: str = "default") -> dict:
    """A record as written before the upright layout existed: the same call,
    which carried no layout key."""
    record = _record(variant=variant)
    assert "layout" not in record
    return record


@pytest.mark.parametrize("variant", ["default", "plate"])
def test_an_upright_record_from_before_the_safe_area_misses(variant: str) -> None:
    from splitsmith.ui.exports import overlay_record_matches

    legacy = _legacy_record(variant)
    # The server's rule: the request says what it wants.
    wanted = _record(variant=variant, upright=True)
    assert not overlay_record_matches(legacy, audit_revision="rev-1", wanted=wanted)
    # The MCP tool's and the CLI's rule: the recorded style, as drawn now on this canvas.
    assert not overlay_record_matches(legacy, audit_revision="rev-1", upright=True)


@pytest.mark.parametrize("variant", ["default", "plate"])
def test_an_upright_record_drawn_with_the_safe_area_matches(variant: str) -> None:
    from splitsmith.ui.exports import overlay_record_matches

    record = _record(variant=variant, upright=True)
    assert record["layout"] == "upright-safe-1"
    wanted = _record(variant=variant, upright=True)
    assert overlay_record_matches(record, audit_revision="rev-1", wanted=wanted)
    assert overlay_record_matches(record, audit_revision="rev-1", upright=True)
    # Still one audit edit from a redraw, like any record.
    assert not overlay_record_matches(record, audit_revision="rev-2", upright=True)


@pytest.mark.parametrize("variant", ["default", "plate"])
def test_a_landscape_record_is_unchanged_and_still_matches(variant: str) -> None:
    from splitsmith.ui.exports import overlay_record_matches

    record = _record(variant=variant)
    keys = {"look", "variant", "options", "codec", "max_height", "max_fps", "audit_revision", "theme"}
    assert set(record) == keys | ({"template"} if variant != "default" else set())
    assert overlay_record_matches(record, audit_revision="rev-1", wanted=_record(variant=variant))
    assert overlay_record_matches(record, audit_revision="rev-1")
    # An upright record is not a landscape one.
    assert not overlay_record_matches(_record(variant=variant, upright=True), audit_revision="rev-1")


def test_the_canvas_is_upright_when_the_video_is_taller_than_wide(monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import fcpxml_gen
    from splitsmith.config import VideoMetadata
    from splitsmith.ui import exports

    sizes = {"up.mp4": (1080, 1920), "wide.mp4": (1920, 1080), "square.mp4": (1080, 1080)}

    def probe(path: Path, **_: object) -> VideoMetadata:
        if path.name not in sizes:
            raise fcpxml_gen.FFprobeError("not a video")
        w, h = sizes[path.name]
        return VideoMetadata(width=w, height=h, duration_seconds=1.0, frame_rate_num=30, frame_rate_den=1)

    monkeypatch.setattr(fcpxml_gen, "probe_video", probe)
    assert exports.overlay_canvas_upright(Path("up.mp4"))
    assert not exports.overlay_canvas_upright(Path("wide.mp4"))
    assert not exports.overlay_canvas_upright(Path("square.mp4"))
    # Unreadable: no upright layout, as before.
    assert not exports.overlay_canvas_upright(Path("broken.mov"))


@pytest.mark.integration
def test_the_canvas_is_read_from_a_real_file(tmp_path: Path) -> None:
    import shutil
    import subprocess

    from splitsmith.ui.exports import overlay_canvas_upright

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("no ffmpeg")
    for name, size in (("up.mp4", "180x320"), ("wide.mp4", "320x180")):
        source = f"color=c=black:s={size}:d=0.2"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", source, str(tmp_path / name)], check=True)
    assert overlay_canvas_upright(tmp_path / "up.mp4")
    assert not overlay_canvas_upright(tmp_path / "wide.mp4")
