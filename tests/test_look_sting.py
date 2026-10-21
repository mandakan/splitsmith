"""The sting context and motion (issue #1245): what a ``transition`` slot
template receives and how its frames are asked for."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from splitsmith.identity import ResolvedIdentity
from splitsmith.look import sting as look_sting
from splitsmith.look.motion import MotionClipError
from splitsmith.look.template import template_digest
from splitsmith.looks import load_look
from splitsmith.overlay.raster import TemplateFrames
from splitsmith.overlay.theme import theme_for
from tests.look_fixtures import install_frames_look


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


class _RecordingRasterizer(_FakeRasterizer):
    def __init__(self) -> None:
        super().__init__(motion_seconds=1.0)
        self.contexts: list = []
        self.frames_dirs: list = []

    def render_template_frames(self, template, *, context, width, height, fps, max_seconds, frames_dir=None):
        self.contexts.append(context)
        self.frames_dirs.append(frames_dir)
        return super().render_template_frames(
            template, context=context, width=width, height=height, fps=fps, max_seconds=max_seconds
        )


def _plan_args(**over):  # type: ignore[no-untyped-def]
    args = {"seconds": 1.0, "from_label": "Stage 1", "to_label": "Stage 2", "width": 64, "height": 36}
    args["fps"] = 30.0
    args.update(over)
    return args


def test_a_tagless_sting_plans_as_before() -> None:
    raster = _RecordingRasterizer()
    plan = look_sting.sting_plan(load_look("splitsmith"), "sting:wipe", rasterizer=raster, **_plan_args())
    assert plan is not None and not isinstance(plan, look_sting.FramesSting)
    assert "frames" not in raster.contexts[0].data["transition"]


def test_a_frames_sting_is_loaded_once_without_frames_for_its_sounds(monkeypatch, tmp_path: Path) -> None:
    look = install_frames_look(tmp_path, monkeypatch)
    raster = _RecordingRasterizer()
    plan = look_sting.sting_plan(look, "sting:push", rasterizer=raster, **_plan_args())
    assert isinstance(plan, look_sting.FramesSting)
    assert len(raster.contexts) == 1 and raster.frames_dirs == [None]
    assert "frames" not in plan.context.data["transition"]
    assert plan.digest == template_digest(plan.template, plan.context, fps=30.0, engine_version="fake")


def test_render_frames_sting_extracts_both_edges_then_draws_with_the_mount(
    monkeypatch, tmp_path: Path
) -> None:
    look = install_frames_look(tmp_path, monkeypatch)
    raster = _RecordingRasterizer()
    plan = look_sting.sting_plan(look, "sting:push", rasterizer=raster, **_plan_args(width=4, height=2))
    ran: list[tuple[str, ...]] = []

    def run(cmd: tuple[str, ...]) -> None:
        ran.append(cmd)
        out = Path(cmd[-1]).parent
        out.mkdir(parents=True, exist_ok=True)
        for i in range(30):
            (out / f"{i:04d}.jpg").write_bytes(b"\xff\xd8\xff")

    written: list[Path] = []
    monkeypatch.setattr(
        look_sting, "write_motion_clip", lambda frames, *, out, fps, ffmpeg_binary: written.append(out)
    )
    look_sting.render_frames_sting(
        plan, tail_edge=tmp_path / "t.mp4", head_edge=tmp_path / "h.mp4", tail_pad_seconds=0.0,
        head_pad_seconds=0.0, rate="30", key="k", frames_root=tmp_path / "fr",
        clip_path=tmp_path / "clip.mov", rasterizer=raster, ffmpeg_binary="ff", run=run,
    )  # fmt: skip
    assert [cmd[6] for cmd in ran] == [str(tmp_path / "t.mp4"), str(tmp_path / "h.mp4")]
    assert raster.frames_dirs[-1] == tmp_path / "fr"
    frames = raster.contexts[-1].data["transition"]["frames"]
    assert frames["count"] == 30 and frames["from"][0].endswith("/frames/k/from/0000.jpg")
    assert written == [tmp_path / "clip.mov"]


def test_an_edge_with_no_frames_is_a_motion_clip_error(monkeypatch, tmp_path: Path) -> None:
    look = install_frames_look(tmp_path, monkeypatch)
    raster = _RecordingRasterizer()
    plan = look_sting.sting_plan(look, "sting:push", rasterizer=raster, **_plan_args(width=4, height=2))
    with pytest.raises(MotionClipError, match="no frames"):
        look_sting.render_frames_sting(
            plan, tail_edge=tmp_path / "t.mp4", head_edge=tmp_path / "h.mp4", tail_pad_seconds=0.0,
            head_pad_seconds=0.0, rate="30", key="k", frames_root=tmp_path / "fr",
            clip_path=tmp_path / "clip.mov", rasterizer=raster, ffmpeg_binary="ff", run=lambda cmd: None,
        )  # fmt: skip


def test_a_frames_sting_that_fails_to_load_is_none(monkeypatch, tmp_path: Path) -> None:
    look = install_frames_look(tmp_path, monkeypatch)

    class _Throws(_RecordingRasterizer):
        def render_template_frames(self, *a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("frames is undefined")

    assert look_sting.sting_plan(look, "sting:push", rasterizer=_Throws(), **_plan_args()) is None


def test_the_replacing_graph_is_a_black_canvas_under_the_clip() -> None:
    parts, out = look_sting.frames_sting_filters(2, rate="30", seconds=1.0, width=640, height=360)
    assert parts[0] == "color=c=black:s=640x360:r=30:d=1,format=rgba[canvas]"
    assert parts[-1].startswith("[canvas][motion]overlay=0:0") and out == "stung"
