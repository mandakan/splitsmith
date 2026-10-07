"""A real ffmpeg render of a 1 s fade between two synthetic stages (#1244):
the boundary file is d long and the stitched file keeps the cut's length.
The unit tests compare declared durations; this one probes the files."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from splitsmith import composition, mp4_render
from splitsmith.audit_data import audit_shots_to_engine_shots, read_audit_data
from splitsmith.fcpxml_gen import StageComposition, probe_video
from tests.compare_fixture import cut_clip, probe_seconds, write_audit
from tests.synthetic_media import (
    SYNTHETIC_FPS_DEN,
    SYNTHETIC_FPS_NUM,
    build_synthetic_video,
    ffmpeg_available,
)

pytestmark = pytest.mark.integration

FRAME = SYNTHETIC_FPS_DEN / SYNTHETIC_FPS_NUM


@pytest.mark.skipif(not ffmpeg_available(), reason="needs ffmpeg and ffprobe on PATH")
def test_a_fade_keeps_the_timeline_length_in_the_real_file(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    source = tmp_path / "source.mp4"
    build_synthetic_video(source)
    stages: list[StageComposition] = []
    for number in (1, 2):
        trim = tmp_path / f"stage{number}.mp4"
        cut_clip(source, trim, 120, ffmpeg=ffmpeg)  # 4 s, beep at 1 s
        audit = tmp_path / f"stage{number}.json"
        write_audit(audit, (1100, 1320))
        stages.append(
            StageComposition(
                stage_name=f"Stage {number}",
                video_path=trim,
                video=probe_video(trim),
                shots=audit_shots_to_engine_shots(read_audit_data(audit), beep_time_in_source=0.0),
                beep_offset_seconds=1.0,
                head_pad_seconds=0.5,
                tail_pad_seconds=1.0,
            )
        )

    def render(name: str, transitions: tuple[composition.Transition, ...]) -> mp4_render.Mp4RenderResult:
        comp = composition.from_stage_compositions(stages, project_name="m", transitions=transitions)
        return mp4_render.render_mp4(
            comp, output_path=tmp_path / f"{name}.mp4", work_dir=tmp_path / name, ffmpeg_binary=ffmpeg
        )

    cut = render("cut", ())
    fade = render(
        "fade",
        (composition.Transition(from_stage_index=0, to_stage_index=1, kind="fade", duration_seconds=1.0),),
    )
    assert fade.degradations == ()
    assert probe_seconds(tmp_path / "fade" / "boundary_000.mp4", ffprobe=ffprobe) == pytest.approx(
        1.0, abs=FRAME
    )
    assert probe_seconds(fade.output_path, ffprobe=ffprobe) == pytest.approx(
        probe_seconds(cut.output_path, ffprobe=ffprobe), abs=2 * FRAME
    )
    assert fade.duration_seconds == pytest.approx(cut.duration_seconds)


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
def test_a_sting_is_visible_on_the_boundary_and_nowhere_else(tmp_path: Path) -> None:
    """Issue #1245: a sting whose every frame is opaque red paints the
    boundary red for its whole length; the stages keep their own picture
    and the stitched file keeps the cut's length."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    assert ffmpeg and ffprobe
    source = tmp_path / "source.mp4"
    build_synthetic_video(source)
    stages: list[StageComposition] = []
    for number in (1, 2):
        trim = tmp_path / f"stage{number}.mp4"
        cut_clip(source, trim, 120, ffmpeg=ffmpeg)
        audit = tmp_path / f"stage{number}.json"
        write_audit(audit, (1100, 1320))
        stages.append(
            StageComposition(
                stage_name=f"Stage {number}",
                video_path=trim,
                video=probe_video(trim),
                shots=audit_shots_to_engine_shots(read_audit_data(audit), beep_time_in_source=0.0),
                beep_offset_seconds=1.0,
                head_pad_seconds=0.5,
                tail_pad_seconds=1.0,
            )
        )

    def render(name: str, transitions: tuple[composition.Transition, ...]) -> mp4_render.Mp4RenderResult:
        comp = composition.from_stage_compositions(stages, project_name="m", transitions=transitions)
        return mp4_render.render_mp4(
            comp,
            output_path=tmp_path / f"{name}.mp4",
            work_dir=tmp_path / name,
            ffmpeg_binary=ffmpeg,
            rasterizer=_RedStingRasterizer(),
        )

    cut = render("cut", ())
    sting = composition.Transition(
        from_stage_index=0, to_stage_index=1, kind="sting:wipe", duration_seconds=1.0
    )
    stung = render("sting", (sting,))
    assert stung.degradations == ()
    boundary = tmp_path / "sting" / "boundary_000.mp4"
    assert probe_seconds(boundary, ffprobe=ffprobe) == pytest.approx(1.0, abs=FRAME)
    for at in (0.05, 0.5, 0.9):
        r, g, b = _mean_rgb(boundary, at=at, ffmpeg=ffmpeg)
        assert r > 200 and g < 60 and b < 60, (at, r, g, b)
    r, g, b = _mean_rgb(tmp_path / "sting" / "stage_000.mp4", at=1.0, ffmpeg=ffmpeg)
    assert not (r > 200 and g < 60 and b < 60), ("the stage is not red", r, g, b)
    assert probe_seconds(stung.output_path, ffprobe=ffprobe) == pytest.approx(
        probe_seconds(cut.output_path, ffprobe=ffprobe), abs=2 * FRAME
    )
