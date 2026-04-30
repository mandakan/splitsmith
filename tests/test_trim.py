"""Tests for trim.py.

Unit tests mock the ffmpeg subprocess (per CLAUDE.md). An integration test runs
real ffmpeg against the stage_sample.mp4 fixture and is gated by the
``integration`` marker.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from splitsmith.trim import FFmpegError, trim_video


class _RecordingRunner:
    """Stand-in for subprocess.run that records the call and returns success."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def _touch(p: Path) -> Path:
    p.write_bytes(b"")
    return p


def test_trim_computes_window_and_invokes_ffmpeg(tmp_path: Path) -> None:
    src = _touch(tmp_path / "in.mp4")
    dst = tmp_path / "out.mp4"
    runner = _RecordingRunner()

    result = trim_video(
        src,
        dst,
        beep_time=12.453,
        stage_time=14.74,
        buffer_seconds=5.0,
        runner=runner,
    )

    assert result.start_time == pytest.approx(7.453)
    assert result.end_time == pytest.approx(32.193)
    assert result.duration == pytest.approx(24.740)
    assert result.output_path == dst

    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert cmd[0] == "ffmpeg"
    # -ss before -i for fast seeking, per SPEC.
    ss_idx = cmd.index("-ss")
    i_idx = cmd.index("-i")
    assert ss_idx < i_idx
    assert cmd[ss_idx + 1] == "7.453"
    assert cmd[i_idx + 1] == str(src)
    t_idx = cmd.index("-t")
    assert cmd[t_idx + 1] == "24.740"
    # Stream copy (lossless) and don't clobber by default.
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert "-n" in cmd
    assert "-y" not in cmd
    assert cmd[-1] == str(dst)


def test_trim_clamps_start_to_zero(tmp_path: Path) -> None:
    src = _touch(tmp_path / "in.mp4")
    dst = tmp_path / "out.mp4"
    runner = _RecordingRunner()

    result = trim_video(src, dst, beep_time=2.0, stage_time=10.0, buffer_seconds=5.0, runner=runner)

    assert result.start_time == 0.0
    # End is unaffected by the clamp.
    assert result.end_time == pytest.approx(17.0)
    cmd = runner.calls[0]
    assert cmd[cmd.index("-ss") + 1] == "0.000"


def test_trim_overwrite_passes_minus_y(tmp_path: Path) -> None:
    src = _touch(tmp_path / "in.mp4")
    dst = tmp_path / "out.mp4"
    runner = _RecordingRunner()
    trim_video(src, dst, beep_time=1.0, stage_time=1.0, runner=runner, overwrite=True)
    cmd = runner.calls[0]
    assert "-y" in cmd
    assert "-n" not in cmd


def test_trim_rejects_negative_inputs(tmp_path: Path) -> None:
    src = _touch(tmp_path / "in.mp4")
    dst = tmp_path / "out.mp4"
    runner = _RecordingRunner()
    with pytest.raises(ValueError, match="beep_time"):
        trim_video(src, dst, beep_time=-1.0, stage_time=10.0, runner=runner)
    with pytest.raises(ValueError, match="stage_time"):
        trim_video(src, dst, beep_time=1.0, stage_time=-1.0, runner=runner)
    with pytest.raises(ValueError, match="buffer_seconds"):
        trim_video(src, dst, beep_time=1.0, stage_time=1.0, buffer_seconds=-1.0, runner=runner)


def test_trim_raises_when_input_missing(tmp_path: Path) -> None:
    dst = tmp_path / "out.mp4"
    runner = _RecordingRunner()
    with pytest.raises(FileNotFoundError):
        trim_video(tmp_path / "missing.mp4", dst, 1.0, 1.0, runner=runner)
    assert runner.calls == []


def test_trim_wraps_ffmpeg_failure(tmp_path: Path) -> None:
    src = _touch(tmp_path / "in.mp4")
    dst = tmp_path / "out.mp4"

    def failing_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr="boom")

    with pytest.raises(FFmpegError, match="boom"):
        trim_video(src, dst, 1.0, 1.0, runner=failing_runner)


def test_trim_wraps_missing_binary(tmp_path: Path) -> None:
    src = _touch(tmp_path / "in.mp4")
    dst = tmp_path / "out.mp4"

    def missing_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError("no such file: ffmpeg-nope")

    with pytest.raises(FFmpegError, match="ffmpeg binary not found"):
        trim_video(src, dst, 1.0, 1.0, ffmpeg_binary="ffmpeg-nope", runner=missing_runner)


@pytest.mark.integration
def test_trim_integration_real_ffmpeg(tmp_path: Path, fixtures_dir: Path) -> None:
    src = fixtures_dir / "stage_sample.mp4"
    if not src.exists():
        pytest.skip(f"sample video not available at {src}")
    dst = tmp_path / "trimmed.mp4"

    result = trim_video(src, dst, beep_time=4.853, stage_time=14.74, buffer_seconds=2.0)
    assert dst.exists()
    assert dst.stat().st_size > 0

    # Verify output duration via ffprobe is close to the requested duration.
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(dst),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    actual = float(probe.stdout.strip())
    # Stream-copy with -ss before -i snaps to the nearest keyframe; allow generous slack.
    assert actual == pytest.approx(result.duration, abs=2.0)
