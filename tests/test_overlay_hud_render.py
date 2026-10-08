"""The template HUD path of render_overlay: frames, cache, fallback."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from splitsmith import overlay_render
from splitsmith.config import VideoMetadata
from splitsmith.overlay_hud import HudOptions
from splitsmith.overlay_raster import TemplateFrames, TemplateScriptError
from splitsmith.segment_cache import SegmentCache
from tests.conftest import fake_ffmpeg_probe

W, H = 64, 36


def _meta(duration: float = 2.0) -> VideoMetadata:
    return VideoMetadata(width=W, height=H, duration_seconds=duration, frame_rate_num=10, frame_rate_den=1)


def _audit(tmp_path: Path) -> Path:
    audit = tmp_path / "stage1.json"
    audit.write_text(
        json.dumps(
            {
                "shots": [
                    {"shot_number": i + 1, "candidate_number": i + 1, "ms_after_beep": ms}
                    for i, ms in enumerate([300, 550, 800])
                ]
            }
        ),
        encoding="utf-8",
    )
    return audit


class _FakeRasterizer:
    """Classic's ``png`` plus the HUD's ``render_template_timeline``: real
    RGBA buffers, every call recorded. ``fail_at`` raises a
    TemplateScriptError on that frame index, mid-run."""

    def __init__(self, *, settle: float = 0.2, fail_at: int | None = None) -> None:
        self.settle = settle
        self.fail_at = fail_at
        self.timelines: list[dict[str, Any]] = []
        self.documents: list[str] = []
        self.rendered = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.documents.append(html)
        buffer = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buffer, format="PNG")
        return buffer.getvalue()

    def engine_version(self) -> str:
        return "fake-1"

    def render_template_timeline(self, template, *, context, width, height, plan):  # noqa: ANN001
        times = list(plan(self.settle))
        self.timelines.append(
            {"template": template, "context": context, "times": times, "size": (width, height)}
        )

        def frames():
            for index, _ in enumerate(times):
                if self.fail_at is not None and index == self.fail_at:
                    raise TemplateScriptError("hud-plate.html: boom")
                self.rendered += 1
                yield bytes((index % 256, 0, 0, 255)) * (width * height)

        return TemplateFrames(
            duration=self.settle, frame_count=len(times), width=width, height=height, frames=frames()
        )


class _Encoder:
    """A stub ffmpeg: records argv and every byte piped, writes the output."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[list[str]] = []
        self.piped: list[int] = []
        encoder = self

        class Stdin:
            def write(self, data: bytes) -> int:
                encoder.piped[-1] += len(data)
                return len(data)

            def close(self) -> None:
                return None

        class Stderr:
            def read(self) -> bytes:
                return b""

        class Proc:
            def __init__(self, cmd: list[str]) -> None:
                self.cmd = cmd
                self.stdin = Stdin()
                self.stderr = Stderr()

            def wait(self) -> int:
                Path(self.cmd[-1]).write_bytes(f"mov:{len(encoder.calls)}".encode())
                return 0

            def kill(self) -> None:
                return None

        def popen(cmd: list[str], **_: Any) -> Proc:
            encoder.calls.append(cmd)
            encoder.piped.append(0)
            return Proc(cmd)

        monkeypatch.setattr(overlay_render.subprocess, "Popen", popen)
        # A real file: the segment cache keys the encoder by stat-ing what
        # ``which`` answers, and ``shutil`` is one module for both callers.
        monkeypatch.setattr(overlay_render.shutil, "which", lambda _b: sys.executable)


def _render(tmp_path: Path, rasterizer: _FakeRasterizer, **kwargs: Any) -> Path:
    kwargs.setdefault("probe", _meta())
    kwargs.setdefault("beep_offset_seconds", 1.0)
    return overlay_render.render_overlay(
        audit_path=_audit(tmp_path),
        trimmed_video_path=tmp_path / "trim.mp4",
        output_path=tmp_path / "overlay.mov",
        codec="prores-4444",
        rasterizer=rasterizer,
        probe_runner=fake_ffmpeg_probe(),
        **kwargs,
    )


def test_a_template_variant_renders_the_planned_frames_and_pipes_every_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    fake = _FakeRasterizer(settle=0.2)
    _render(tmp_path, fake, variant="plate")
    (timeline,) = fake.timelines
    assert timeline["template"].name == "hud-plate.html"
    # 10 fps, beep at 1.0, last shot at 1.8, settle 0.2: one held frame at 0,
    # then frames 1.0 .. 2.0 clamped to the clip's last frame (1.9).
    assert timeline["times"] == [0.0, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9]
    assert fake.documents == [], "the Classic path drew nothing"
    assert encoder.piped == [20 * W * H * 4]
    assert "drawtext" not in " ".join(encoder.calls[0]), "the template draws the clock"
    data = timeline["context"].data
    assert data["stage"]["rounds"] == 3 and data["stage"]["beep"] == 1.0
    assert data["options"]["position"] == "bottom-left"


def test_the_options_and_position_reach_the_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Encoder(monkeypatch)
    fake = _FakeRasterizer()
    _render(
        tmp_path,
        fake,
        variant="plate",
        hud_options=HudOptions(speed_colors=False, class_labels=False, landing=False, position="top-right"),
    )
    assert fake.timelines[0]["context"].data["options"] == {
        "speed_colors": False,
        "class_labels": False,
        "landing": False,
        "position": "top-right",
    }


def test_a_tall_output_renders_a_1080_page_and_scales_in_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    fake = _FakeRasterizer()
    tall = VideoMetadata(width=3840, height=2160, duration_seconds=0.5, frame_rate_num=10, frame_rate_den=1)
    _render(tmp_path, fake, variant="plate", probe=tall, beep_offset_seconds=0.0)
    assert fake.timelines[0]["size"] == (1920, 1080)
    cmd = encoder.calls[0]
    assert cmd[cmd.index("-s") + 1] == "1920x1080"
    assert cmd[cmd.index("-vf") + 1] == "scale=3840:2160:flags=lanczos"


def test_no_variant_is_the_classic_path_and_loads_no_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Encoder(monkeypatch)

    def no_template(*_a: Any, **_k: Any) -> None:
        raise AssertionError("Classic must not resolve a template")

    monkeypatch.setattr(overlay_render, "overlay_template_for", no_template)
    fake = _FakeRasterizer()
    for variant in (None, "default"):
        _render(tmp_path, fake, variant=variant)
    assert fake.timelines == [] and fake.documents


def test_a_template_that_throws_mid_run_falls_back_to_classic_with_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    fake = _FakeRasterizer(fail_at=4)
    degraded: list[str] = []
    out = _render(tmp_path, fake, variant="plate", degraded=degraded)
    assert len(encoder.calls) == 2, "the HUD encode was killed, then Classic encoded"
    assert fake.documents, "Classic drew its runs"
    assert out.read_bytes() == b"mov:2"
    assert degraded == ["overlay style 'plate' fell back to Classic: hud-plate.html: boom"]


def test_an_unknown_variant_draws_classic_with_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Encoder(monkeypatch)
    fake = _FakeRasterizer()
    degraded: list[str] = []
    _render(tmp_path, fake, variant="nope", degraded=degraded)
    assert fake.timelines == [] and fake.documents
    assert degraded == ["overlay style 'nope' is not in Look 'splitsmith'; drew Classic"]


def test_a_cache_hit_renders_no_frame_and_leaves_the_mov_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    encoder = _Encoder(monkeypatch)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    first = _FakeRasterizer()
    out = _render(tmp_path, first, variant="plate", segment_cache=cache)
    before = (out.read_bytes(), out.stat().st_mtime_ns)
    os.utime(out, ns=(before[1] - 10**9, before[1] - 10**9))
    pinned = out.stat().st_mtime_ns

    second = _FakeRasterizer()
    _render(tmp_path, second, variant="plate", segment_cache=cache)
    assert second.rendered == 0, "a hit renders no browser frame"
    assert len(encoder.calls) == 1, "and encodes nothing"
    assert out.read_bytes() == before[0] and out.stat().st_mtime_ns == pinned


@pytest.mark.parametrize(
    "change",
    [
        {"hud_options": HudOptions(speed_colors=False)},
        {"hud_options": HudOptions(position="top-left")},
        {"beep_offset_seconds": 0.9},
        {"theme": "clean"},
    ],
)
def test_any_input_change_misses_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: dict
) -> None:
    encoder = _Encoder(monkeypatch)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    _render(tmp_path, _FakeRasterizer(), variant="plate", segment_cache=cache)
    again = _FakeRasterizer()
    _render(tmp_path, again, variant="plate", segment_cache=cache, **change)
    assert again.rendered > 0 and len(encoder.calls) == 2


def test_changed_template_bytes_miss_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _Encoder(monkeypatch)
    cache = SegmentCache(root=tmp_path / "cache", max_bytes=10**9)
    _render(tmp_path, _FakeRasterizer(), variant="plate", segment_cache=cache)
    edited = tmp_path / "hud-plate.html"
    shipped = overlay_render.overlay_template_for(overlay_render.load_look("splitsmith"), "plate")
    edited.write_text(shipped.read_text(encoding="utf-8") + "<!-- edit -->", encoding="utf-8")
    monkeypatch.setattr(overlay_render, "overlay_template_for", lambda _look, _v: edited)
    again = _FakeRasterizer()
    _render(tmp_path, again, variant="plate", segment_cache=cache)
    assert again.rendered > 0


# --- the shipped Plate, through real Chromium and ffmpeg ------------------------

import subprocess as _subprocess  # noqa: E402

from splitsmith.overlay_raster import ChromiumRasterizer  # noqa: E402
from tests.synthetic_media import ffmpeg_available  # noqa: E402


def _frame_at(mov: Path, index: int, out: Path) -> Image.Image:
    _subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(mov),
            "-vf",
            f"select=eq(n\\,{index})",
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgba",
            str(out),
        ],
        check=True,
    )
    return Image.open(out).convert("RGBA")


def _alpha_in(image: Image.Image, box: tuple[int, int, int, int]) -> int:
    return max(image.crop(box).getchannel("A").getdata())


@pytest.mark.integration
@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg")
def test_plate_renders_a_stage_into_a_mov_the_trim_s_length(tmp_path: Path) -> None:
    meta = VideoMetadata(width=640, height=360, duration_seconds=3.0, frame_rate_num=30, frame_rate_den=1)
    out = tmp_path / "overlay.mov"
    with ChromiumRasterizer() as rasterizer:
        overlay_render.render_overlay(
            audit_path=_audit(tmp_path),
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=out,
            beep_offset_seconds=1.0,
            probe=meta,
            codec="prores-4444",
            rasterizer=rasterizer,
            variant="plate",
        )
    frames = _subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert int(frames) == 90
    # Bottom-left plate: drawn before the beep (the counter reads 00/03) and after a shot.
    bottom_left = (0, 220, 320, 360)
    top_right = (400, 0, 640, 140)
    pre_beep = _frame_at(out, 10, tmp_path / "pre.png")
    after_shot = _frame_at(out, 50, tmp_path / "shot.png")
    assert _alpha_in(pre_beep, bottom_left) > 200
    assert _alpha_in(after_shot, bottom_left) > 200
    assert _alpha_in(after_shot, top_right) == 0, "nothing drawn outside the HUD's corner"


@pytest.mark.integration
@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg")
def test_plate_honours_the_position(tmp_path: Path) -> None:
    meta = VideoMetadata(width=640, height=360, duration_seconds=2.0, frame_rate_num=30, frame_rate_den=1)
    out = tmp_path / "overlay.mov"
    with ChromiumRasterizer() as rasterizer:
        overlay_render.render_overlay(
            audit_path=_audit(tmp_path),
            trimmed_video_path=tmp_path / "trim.mp4",
            output_path=out,
            beep_offset_seconds=1.0,
            probe=meta,
            codec="prores-4444",
            rasterizer=rasterizer,
            variant="plate",
            hud_options=HudOptions(position="top-right"),
        )
    frame = _frame_at(out, 50, tmp_path / "f.png")
    assert _alpha_in(frame, (400, 0, 640, 140)) > 200
    assert _alpha_in(frame, (0, 220, 320, 360)) == 0
