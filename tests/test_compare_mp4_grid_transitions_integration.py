"""A real ffmpeg render of a two-shooter grid with a 1 s fade (#1244): the
boundary segment is d long and carries the grid's N+1 tracks, the stitched
file keeps the cut's length, and every track survives with its name."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from splitsmith import composition
from splitsmith.compare import mp4_grid
from splitsmith.compare.project_loader import CompareShooterBundle, CompareStageBundle
from splitsmith.fcpxml_gen import probe_video
from tests.compare_fixture import cut_clip, probe_seconds, write_audit
from tests.synthetic_media import (
    SYNTHETIC_FPS_DEN,
    SYNTHETIC_FPS_NUM,
    build_synthetic_video,
    ffmpeg_available,
)

pytestmark = pytest.mark.integration

FRAME = SYNTHETIC_FPS_DEN / SYNTHETIC_FPS_NUM


def _streams(path: Path, *, ffprobe: str) -> list[tuple[str, str]]:
    """``(codec_type, handler name)`` per stream, in file order: an MP4 keeps a
    track's name in its handler, which is where the stitch restates it."""
    done = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    streams = json.loads(done.stdout)["streams"]
    return [(stream["codec_type"], stream.get("tags", {}).get("handler_name", "")) for stream in streams]


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_two_shooter_fade_keeps_the_length_and_every_track(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    source = tmp_path / "source.mp4"
    build_synthetic_video(source)
    shooters: list[CompareShooterBundle] = []
    for label in ("Anders", "Mathias"):
        stages: dict[int, CompareStageBundle] = {}
        for number in (1, 2):
            trim = tmp_path / f"{label}-{number}.mp4"
            cut_clip(source, trim, 120, ffmpeg=ffmpeg)  # 4 s, beep at 1 s
            audit = tmp_path / f"{label}-{number}.json"
            write_audit(audit, (1100, 1320))
            meta = probe_video(trim)
            stages[number] = CompareStageBundle(
                stage_number=number,
                stage_name=f"Stage {number}",
                trim_path=trim,
                audit_path=audit,
                beep_offset_in_clip=1.0,
                duration_seconds=meta.duration_seconds,
                width=meta.width,
                height=meta.height,
                frame_rate_num=meta.frame_rate_num,
                frame_rate_den=meta.frame_rate_den,
            )
        shooters.append(
            CompareShooterBundle(label=label, project_root=tmp_path / label, stages_by_number=stages)
        )

    def render(name: str, transitions: tuple[composition.Transition, ...]) -> mp4_grid.GridRenderResult:
        return mp4_grid.render_grid_mp4(
            shooters,
            audio_label="Anders",
            output_path=tmp_path / f"{name}.mp4",
            canvas=mp4_grid.GridCanvas(640, 360, SYNTHETIC_FPS_NUM, SYNTHETIC_FPS_DEN),
            head_pad_seconds=0.5,
            tail_pad_seconds=1.0,
            ffmpeg_binary=ffmpeg,
            work_dir=tmp_path / name,
            transitions=transitions,
        )

    cut = render("cut", ())
    fade = render(
        "fade",
        (composition.Transition(from_stage_index=0, to_stage_index=1, kind="fade", duration_seconds=1.0),),
    )
    assert fade.degradations == () and all(stage.ok for stage in fade.stages)
    boundary = tmp_path / "fade" / "boundary-000.mov"
    assert probe_seconds(boundary, ffprobe=ffprobe) == pytest.approx(1.0, abs=FRAME)
    assert [kind for kind, _ in _streams(boundary, ffprobe=ffprobe)] == ["video", "audio", "audio", "audio"]
    assert probe_seconds(fade.output_path, ffprobe=ffprobe) == pytest.approx(
        probe_seconds(cut.output_path, ffprobe=ffprobe), abs=2 * FRAME
    )
    assert _streams(fade.output_path, ffprobe=ffprobe) == [
        ("video", "VideoHandler"),
        ("audio", "Mix"),
        ("audio", "Anders"),
        ("audio", "Mathias"),
    ]
    assert [c.start_seconds for c in fade.chapters] == [c.start_seconds for c in cut.chapters]


# --- stings (#1245): the clip reaches the boundary's pixels -------------------------


class _RedStingRasterizer:
    """Yields opaque red frames for any template, so the sting's presence
    is a colour the boundary frame either has or has not."""

    def __init__(self) -> None:
        self.frames_rendered = 0

    def png(self, html: str, *, width: int, height: int) -> bytes:
        raise AssertionError("not used")

    def render_template(self, template, *, context, width: int, height: int) -> bytes:
        raise AssertionError("not used")

    def engine_version(self) -> str:
        return "red"

    def render_template_frames(self, template, *, context, width, height, fps, max_seconds):
        import math

        from splitsmith.overlay_raster import TemplateFrames

        count = max(1, math.ceil(max_seconds * fps - 1e-9))
        red = bytes((255, 0, 0, 255)) * (width * height)

        def frames():
            for _ in range(count):
                self.frames_rendered += 1
                yield red

        return TemplateFrames(
            duration=max_seconds, frame_count=count, width=width, height=height, frames=frames()
        )


def _mean_rgb(path: Path, *, at: float, ffmpeg: str) -> tuple[float, float, float]:
    """The mean colour of the frame ``at`` seconds into ``path``."""
    import io
    import subprocess

    from PIL import Image, ImageStat

    done = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-v",
            "error",
            "-ss",
            f"{at:g}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        capture_output=True,
    )
    assert done.returncode == 0, done.stderr[-2000:].decode("utf-8", "replace")
    with Image.open(io.BytesIO(done.stdout)) as image:
        r, g, b = ImageStat.Stat(image.convert("RGB")).mean
    return r, g, b


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_grid_sting_is_visible_on_the_boundary_and_keeps_every_track(tmp_path: Path) -> None:
    """Issue #1245 on the grid: the red sting covers the boundary, the N+1
    tracks survive the extra video input, and the length is the cut's."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    source = tmp_path / "source.mp4"
    build_synthetic_video(source)
    shooters: list[CompareShooterBundle] = []
    for label in ("Anders", "Mathias"):
        stages: dict[int, CompareStageBundle] = {}
        for number in (1, 2):
            trim = tmp_path / f"{label}-{number}.mp4"
            cut_clip(source, trim, 120, ffmpeg=ffmpeg)
            audit = tmp_path / f"{label}-{number}.json"
            write_audit(audit, (1100, 1320))
            meta = probe_video(trim)
            stages[number] = CompareStageBundle(
                stage_number=number,
                stage_name=f"Stage {number}",
                trim_path=trim,
                audit_path=audit,
                beep_offset_in_clip=1.0,
                duration_seconds=meta.duration_seconds,
                width=meta.width,
                height=meta.height,
                frame_rate_num=meta.frame_rate_num,
                frame_rate_den=meta.frame_rate_den,
            )
        shooters.append(
            CompareShooterBundle(label=label, project_root=tmp_path / label, stages_by_number=stages)
        )

    def render(name: str, transitions: tuple[composition.Transition, ...]) -> mp4_grid.GridRenderResult:
        return mp4_grid.render_grid_mp4(
            shooters,
            audio_label="Anders",
            output_path=tmp_path / f"{name}.mp4",
            canvas=mp4_grid.GridCanvas(640, 360, SYNTHETIC_FPS_NUM, SYNTHETIC_FPS_DEN),
            head_pad_seconds=0.5,
            tail_pad_seconds=1.0,
            ffmpeg_binary=ffmpeg,
            work_dir=tmp_path / name,
            rasterizer=_RedStingRasterizer(),
            transitions=transitions,
        )

    cut = render("cut", ())
    sting = composition.Transition(
        from_stage_index=0, to_stage_index=1, kind="sting:wipe", duration_seconds=1.0
    )
    stung = render("sting", (sting,))
    assert stung.degradations == () and all(stage.ok for stage in stung.stages)
    boundary = tmp_path / "sting" / "boundary-000.mov"
    assert probe_seconds(boundary, ffprobe=ffprobe) == pytest.approx(1.0, abs=FRAME)
    assert [kind for kind, _ in _streams(boundary, ffprobe=ffprobe)] == ["video", "audio", "audio", "audio"]
    for at in (0.05, 0.5, 0.9):
        r, g, b = _mean_rgb(boundary, at=at, ffmpeg=ffmpeg)
        assert r > 200 and g < 60 and b < 60, (at, r, g, b)
    r, g, b = _mean_rgb(tmp_path / "sting" / "stage1.mov", at=1.0, ffmpeg=ffmpeg)
    assert not (r > 200 and g < 60 and b < 60), ("the stage is not red", r, g, b)
    assert probe_seconds(stung.output_path, ffprobe=ffprobe) == pytest.approx(
        probe_seconds(cut.output_path, ffprobe=ffprobe), abs=2 * FRAME
    )
    assert _streams(stung.output_path, ffprobe=ffprobe) == [
        ("video", "VideoHandler"),
        ("audio", "Mix"),
        ("audio", "Anders"),
        ("audio", "Mathias"),
    ]
