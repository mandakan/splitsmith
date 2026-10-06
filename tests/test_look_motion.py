"""The alpha clip a motion template becomes, and the filters that lay it on a card."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from splitsmith import look_motion
from splitsmith.overlay_raster import TemplateFrames

FFMPEG = shutil.which("ffmpeg")


def _frames(count: int, *, width: int = 8, height: int = 4, alpha: int = 255) -> TemplateFrames:
    def gen():
        for i in range(count):
            yield bytes([i * 10 % 256, 0, 0, alpha]) * (width * height)

    return TemplateFrames(duration=count / 10, frame_count=count, width=width, height=height, frames=gen())


def test_motion_clip_command_reads_raw_rgba_from_stdin_and_writes_lossless_png_frames(tmp_path: Path) -> None:
    cmd = look_motion.motion_clip_command(
        out=tmp_path / "c.mov", width=8, height=4, fps=25, ffmpeg_binary="ffmpeg"
    )
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-f") + 1] == "rawvideo"
    assert cmd[cmd.index("-s") + 1] == "8x4"
    assert cmd[cmd.index("-i") + 1] == "-"
    assert cmd[cmd.index("-c:v") + 1] == "png"
    assert cmd[-1] == str(tmp_path / "c.mov")
    pix = [cmd[i + 1] for i, t in enumerate(cmd) if t == "-pix_fmt"]
    assert pix == ["rgba", "rgba"]


def test_motion_overlay_filters_hold_the_last_frame_for_the_card() -> None:
    parts, label = look_motion.motion_overlay_filters(1, rate="30000/1001", seconds=3.0, source_label="0:v")
    assert label == "withmotion"
    assert parts[0].startswith("[1:v]format=rgba,fps=30000/1001,setpts=PTS-STARTPTS,")
    assert "tpad=stop_mode=clone:stop_duration=3" in parts[0]
    assert parts[0].endswith("trim=0:3[motion]")
    assert parts[1] == "[0:v][motion]overlay=0:0:format=auto[withmotion]"


@pytest.mark.integration
@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_write_motion_clip_writes_every_frame_with_alpha(tmp_path: Path) -> None:
    out = tmp_path / "clip.mov"
    clip = look_motion.write_motion_clip(_frames(7, alpha=128), out=out, fps=10, ffmpeg_binary=FFMPEG)
    assert clip == look_motion.MotionClip(path=out, seconds=0.7, frame_count=7)
    ffprobe = str(Path(FFMPEG).with_name("ffprobe"))
    probe = (
        subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=codec_name,pix_fmt,nb_read_frames",
                "-of",
                "csv=p=0",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .split(",")
    )
    assert probe == ["png", "rgba", "7"]


@pytest.mark.integration
@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_write_motion_clip_removes_the_partial_file_when_frames_raise(tmp_path: Path) -> None:
    def gen():
        yield bytes(8 * 4 * 4)
        raise RuntimeError("frame 2 boom")

    frames = TemplateFrames(duration=0.2, frame_count=2, width=8, height=4, frames=gen())
    out = tmp_path / "clip.mov"
    with pytest.raises(look_motion.MotionClipError, match="frame 2 boom"):
        look_motion.write_motion_clip(frames, out=out, fps=10, ffmpeg_binary=FFMPEG)
    assert not out.exists()


def test_write_motion_clip_refuses_a_frame_of_the_wrong_size(tmp_path: Path) -> None:
    frames = TemplateFrames(duration=0.1, frame_count=1, width=8, height=4, frames=iter([bytes(3)]))
    with pytest.raises(look_motion.MotionClipError, match="bytes"):
        look_motion.write_motion_clip(
            frames, out=tmp_path / "c.mov", fps=10, ffmpeg_binary="ffmpeg-that-is-not-run"
        )


def test_motion_overlay_filters_can_delay_the_clip_with_transparent_padding() -> None:
    """Issue #1244: a boundary's head edge of an animated card shows the
    backdrop alone for d/2, then the clip starts; the padding is
    transparent so the backdrop shows through. Unchanged without a delay."""
    from splitsmith.look_motion import motion_overlay_filters

    plain, label = motion_overlay_filters(1, rate="30", seconds=2.0, source_label="0:v")
    assert plain[0] == (
        "[1:v]format=rgba,fps=30,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=2,trim=0:2[motion]"
    )
    delayed, _ = motion_overlay_filters(1, rate="30", seconds=2.0, source_label="0:v", delay_seconds=0.5)
    assert delayed[0] == (
        "[1:v]format=rgba,fps=30,setpts=PTS-STARTPTS,tpad=start_duration=0.5:start_mode=add:color=black@0.0,"
        "tpad=stop_mode=clone:stop_duration=2,trim=0:2[motion]"
    )
    assert delayed[1] == plain[1] and label == "withmotion"
