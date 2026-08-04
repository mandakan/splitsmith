"""Integration tests that actually run the ffmpeg commands mp4_grid builds.

Marked ``integration`` and skipped without an ffmpeg on PATH, per
CLAUDE.md: the unit tests in ``test_compare_mp4_grid_commands.py`` never
shell out.

These exist because asserting on the argument tuple cannot see whether
the graph does what it says. Two defects found during this task -- the
head pad being swallowed when ``setpts`` ran before ``tpad``, and every
segment's video ending a tail pad short of its audio -- both passed a
green string-matching suite and were only visible in the rendered file.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from splitsmith.compare import mp4_grid

FFMPEG = shutil.which("ffmpeg")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(FFMPEG is None, reason="needs a real ffmpeg on PATH"),
]

CANVAS = mp4_grid.GridCanvas(width=640, height=360, frame_rate_num=30, frame_rate_den=1)
FRAME_SECONDS = 1 / 30
STAGE_SECONDS = 4.0


def _source(path: Path, *, seconds: float, color: str) -> Path:
    """A solid-colour clip with a tone, so both streams are measurable."""
    cmd = [
        FFMPEG, "-hide_banner", "-y",
        "-f", "lavfi", "-t", str(seconds), "-i", f"color=c={color}:s=320x240:r=30",
        "-f", "lavfi", "-t", str(seconds), "-i", "sine=frequency=440:sample_rate=48000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ]  # fmt: skip
    done = subprocess.run(cmd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    return path


def _stream_seconds(path: Path, spec: str) -> float:
    """Decode one stream to nowhere and read the timestamp it ends on."""
    done = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path), "-map", spec, "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    stamps = re.findall(r"time=(\d+):(\d+):(\d+\.\d+)", done.stderr)
    assert stamps, done.stderr[-2000:]
    hours, minutes, seconds = stamps[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _patch_colour(path: Path, *, at: float, x: int, y: int) -> tuple[int, int, int]:
    """Mean RGB of an 8x8 patch, for telling black pad from real footage."""
    done = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-v", "error", "-ss", str(at), "-i", str(path),
            "-vf", f"crop=8:8:{x}:{y}", "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],  # fmt: skip
        capture_output=True,
    )
    raw = done.stdout
    assert len(raw) == 8 * 8 * 3, done.stderr[-2000:]
    return tuple(round(sum(raw[i::3]) / len(raw[i::3])) for i in range(3))  # type: ignore[return-value]


def _tile(label: str, trim: Path | None, *, col: int, lead_pad: float = 0.0) -> mp4_grid.GridTile:
    return mp4_grid.GridTile(
        label=label,
        trim_path=trim,
        beep_offset_in_clip=0.0,
        seek_seconds=0.0,
        lead_pad_seconds=lead_pad,
        row=0,
        col=col,
    )


def _render(tmp_path: Path, tiles: tuple[mp4_grid.GridTile, ...], name: str) -> Path:
    plan = mp4_grid.GridStagePlan(
        stage_number=1,
        stage_name="Stage 1",
        tiles=tiles,
        duration_seconds=STAGE_SECONDS,
        audio_label=tiles[-1].label,
        rows=1,
        cols=len(tiles),
    )
    out = tmp_path / name
    cmd = mp4_grid.build_stage_command(plan, canvas=CANVAS, output_path=out, ffmpeg_binary=FFMPEG)
    done = subprocess.run(list(cmd), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-3000:]
    return out


def test_a_stage_whose_footage_runs_out_still_fills_the_whole_duration(tmp_path: Path):
    # Every tile's content is head_pad + its post-beep span, while the
    # stage runs head_pad + the longest post-beep span + tail_pad -- so
    # the longest tile is always exactly a tail pad short. Without a tail
    # pad on the video the segment's video ends before its audio, and
    # concat -c copy carries that gap into every later stage.
    tiles = (
        _tile("Short", _source(tmp_path / "a.mp4", seconds=2.0, color="red"), col=0),
        _tile("Alsoshort", _source(tmp_path / "b.mp4", seconds=2.5, color="blue"), col=1),
    )
    out = _render(tmp_path, tiles, "short_footage.mp4")

    video = _stream_seconds(out, "0:v:0")
    audio = [_stream_seconds(out, f"0:a:{slot}") for slot in range(len(tiles))]

    assert video == pytest.approx(STAGE_SECONDS, abs=2 * FRAME_SECONDS)
    for track in audio:
        assert track == pytest.approx(STAGE_SECONDS, abs=2 * FRAME_SECONDS)
    for track in audio:
        assert abs(video - track) <= 2 * FRAME_SECONDS


def test_the_tail_pad_is_black_and_does_not_disturb_the_head_pad(tmp_path: Path):
    # A start tpad and a stop tpad in one chain is exactly the kind of
    # interaction that looks fine and is not: the head pad is what keeps
    # a clamped tile's beep on the grid timeline.
    tiles = (
        _tile("Padded", _source(tmp_path / "a.mp4", seconds=2.0, color="red"), col=0, lead_pad=0.5),
        _tile("Plain", _source(tmp_path / "b.mp4", seconds=2.0, color="blue"), col=1),
    )
    out = _render(tmp_path, tiles, "both_pads.mp4")

    cell_w, cell_h = CANVAS.width // 2, CANVAS.height // 2
    centre_x, centre_y = cell_w // 2, cell_h // 2

    # Head pad: black for the first 0.5s of the padded tile only.
    assert _patch_colour(out, at=0.1, x=centre_x, y=centre_y) == (0, 0, 0)
    assert _patch_colour(out, at=0.4, x=centre_x, y=centre_y) == (0, 0, 0)
    assert _patch_colour(out, at=0.1, x=cell_w + centre_x, y=centre_y)[2] > 200  # blue, unpadded
    # Its footage is showing once the pad is spent.
    assert _patch_colour(out, at=1.0, x=centre_x, y=centre_y)[0] > 200  # red

    # Tail pad: both clips are spent well before the stage ends.
    assert _patch_colour(out, at=3.5, x=centre_x, y=centre_y) == (0, 0, 0)
    assert _patch_colour(out, at=3.5, x=cell_w + centre_x, y=centre_y) == (0, 0, 0)

    assert _stream_seconds(out, "0:v:0") == pytest.approx(STAGE_SECONDS, abs=2 * FRAME_SECONDS)
