"""The sting context and motion (issue #1245): what a ``transition`` slot
template receives and how its frames are asked for."""

from __future__ import annotations

import math
from pathlib import Path

from splitsmith import look_sting
from splitsmith.identity import ResolvedIdentity
from splitsmith.look_template import template_digest
from splitsmith.looks import load_look
from splitsmith.overlay_raster import TemplateFrames
from splitsmith.overlay_theme import theme_for


class _FakeRasterizer:
    def __init__(self, *, motion_seconds: float) -> None:
        self.motion_seconds = motion_seconds
        self.frame_requests: list[tuple[Path, float, float]] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        raise AssertionError("a sting never renders through png()")

    def render_template(self, template: Path, *, context, width: int, height: int) -> bytes:
        raise AssertionError("a sting never renders a single poster PNG in the renderer")

    def engine_version(self) -> str:
        return "fake"

    def render_template_frames(self, template, *, context, width, height, fps, max_seconds):
        self.frame_requests.append((template, fps, max_seconds))
        duration = self.motion_seconds
        count = 1 if duration <= 0 else max(1, math.ceil(min(duration, max_seconds) * fps - 1e-9))
        blank = bytes(width * height * 4)
        return TemplateFrames(
            duration=duration,
            frame_count=count,
            width=width,
            height=height,
            frames=(blank for _ in range(count)),
        )


def _context(**overrides):  # type: ignore[no-untyped-def]
    args = {
        "kind": "sting:wipe",
        "seconds": 1.0,
        "from_label": "Stage 01",
        "to_label": "Stage 02",
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "theme": theme_for(load_look("splitsmith")),
        "shooters": (),
    }
    args.update(overrides)
    return look_sting.sting_context(**args)


def test_sting_context_names_the_transition_and_the_shooters(tmp_path: Path) -> None:
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(64))
    ctx = _context(shooters=[ResolvedIdentity("Mathias", "#ff2d2d", logo, "SSK")])
    assert ctx.data["transition"] == {
        "kind": "sting:wipe",
        "name": "wipe",
        "duration_seconds": 1.0,
        "from": "Stage 01",
        "to": "Stage 02",
    }
    assert ctx.data["shooters"][0]["label"] == "Mathias"
    assert ctx.data["shooters"][0]["logo"] == logo.resolve().as_uri()
    assert ctx.size == {"width": 1920, "height": 1080} and ctx.fps == 30.0
    assert (
        ctx.theme["accent"] == "#ff2d2d"
        and "css" in ctx.engine
        and ctx.assets["shared"].startswith("file://")
    )


def test_sting_motion_asks_for_the_whole_duration_and_keys_the_digest() -> None:
    fake = _FakeRasterizer(motion_seconds=1.0)
    look = load_look("splitsmith")
    motion = look_sting.sting_motion(
        look,
        "sting:wipe",
        seconds=1.0,
        from_label="A",
        to_label="B",
        width=640,
        height=360,
        fps=30.0,
        rasterizer=fake,
        shooters=(),
    )
    assert motion is not None and motion.animated
    assert motion.template.name == "sting-wipe.html"
    assert fake.frame_requests == [(motion.template, 30.0, 1.0)]
    assert motion.frames.frame_count == 30
    assert motion.digest == template_digest(motion.template, motion.context, fps=30.0, engine_version="fake")
    motion.close()


def test_sting_motion_is_none_for_a_sting_the_look_lacks() -> None:
    fake = _FakeRasterizer(motion_seconds=1.0)
    motion = look_sting.sting_motion(
        load_look("splitsmith"),
        "sting:nope",
        seconds=1.0,
        from_label="A",
        to_label="B",
        width=640,
        height=360,
        fps=30.0,
        rasterizer=fake,
        shooters=(),
    )
    assert motion is None and fake.frame_requests == []


def test_sting_overlay_filters_span_the_boundary_from_its_first_frame() -> None:
    parts, label = look_sting.sting_overlay_filters(2, rate="30", seconds=1.0, source_label="xf")
    assert label == "stung"
    assert parts == [
        "[2:v]format=rgba,fps=30,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=1,trim=0:1[motion]",
        "[xf][motion]overlay=0:0:format=auto[stung]",
    ]
