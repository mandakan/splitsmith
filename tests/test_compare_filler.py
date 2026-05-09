"""Tests for the black-filler renderer in compare/filler.py."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from splitsmith.compare.filler import (
    FillerRenderError,
    ensure_filler,
    filler_filename,
)


def _stub_runner_factory() -> tuple[list[list[str]], Any]:
    """Returns (calls list, stub callable) -- the stub creates its output file."""
    calls: list[list[str]] = []

    def stub(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(list(cmd))
        # ffmpeg writes its output to the last positional arg
        Path(cmd[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    return calls, stub


def test_filler_filename_is_deterministic() -> None:
    name = filler_filename(
        width=1920,
        height=1080,
        frame_rate_num=30000,
        frame_rate_den=1001,
        duration_seconds=12.345,
    )
    again = filler_filename(
        width=1920,
        height=1080,
        frame_rate_num=30000,
        frame_rate_den=1001,
        duration_seconds=12.345,
    )
    assert name == again
    # Geometry / fps / duration are visible in the name -- so different
    # callers see different names for different inputs.
    assert "1920x1080" in name
    assert "30000-1001" in name
    assert name.endswith(".mp4")


def test_ensure_filler_invokes_ffmpeg_with_lavfi(tmp_path: Path) -> None:
    calls, stub = _stub_runner_factory()
    out = ensure_filler(
        width=1920,
        height=1080,
        frame_rate_num=30,
        frame_rate_den=1,
        duration_seconds=10.0,
        output_dir=tmp_path,
        runner=stub,
    )
    assert out.parent == tmp_path
    assert out.exists()
    assert len(calls) == 1
    cmd = calls[0]
    # Argument order matters for ffmpeg: -f lavfi must precede -i.
    assert cmd[0] == "ffmpeg"
    f_idx = cmd.index("-f")
    i_idx = cmd.index("-i")
    assert f_idx < i_idx
    assert cmd[f_idx + 1] == "lavfi"
    assert cmd[i_idx + 1].startswith("color=c=black:s=1920x1080:r=")
    assert "-an" in cmd
    assert "-c:v" in cmd
    cv = cmd[cmd.index("-c:v") + 1]
    assert cv == "libx264"
    assert cmd[-1] == str(out)


def test_ensure_filler_idempotent_skips_ffmpeg(tmp_path: Path) -> None:
    calls, stub = _stub_runner_factory()
    args = {
        "width": 1280,
        "height": 720,
        "frame_rate_num": 60,
        "frame_rate_den": 1,
        "duration_seconds": 5.0,
        "output_dir": tmp_path,
        "runner": stub,
    }
    first = ensure_filler(**args)  # type: ignore[arg-type]
    second = ensure_filler(**args)  # type: ignore[arg-type]
    assert first == second
    assert len(calls) == 1  # second call returns the cached file


def test_ensure_filler_creates_output_dir(tmp_path: Path) -> None:
    _calls, stub = _stub_runner_factory()
    nested = tmp_path / "a" / "b" / "fillers"
    out = ensure_filler(
        width=640,
        height=480,
        frame_rate_num=30,
        frame_rate_den=1,
        duration_seconds=1.0,
        output_dir=nested,
        runner=stub,
    )
    assert nested.is_dir()
    assert out.parent == nested


def test_ensure_filler_rejects_invalid_geometry(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="width/height"):
        ensure_filler(
            width=0,
            height=1080,
            frame_rate_num=30,
            frame_rate_den=1,
            duration_seconds=1.0,
            output_dir=tmp_path,
            runner=lambda *a, **k: None,  # type: ignore[arg-type]
        )


def test_ensure_filler_rejects_invalid_duration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duration"):
        ensure_filler(
            width=1920,
            height=1080,
            frame_rate_num=30,
            frame_rate_den=1,
            duration_seconds=0.0,
            output_dir=tmp_path,
            runner=lambda *a, **k: None,  # type: ignore[arg-type]
        )


def test_ffmpeg_missing_raises(tmp_path: Path) -> None:
    def stub(_cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError("ffmpeg")

    with pytest.raises(FillerRenderError, match="not found"):
        ensure_filler(
            width=1920,
            height=1080,
            frame_rate_num=30,
            frame_rate_den=1,
            duration_seconds=1.0,
            output_dir=tmp_path,
            runner=stub,
        )


def test_ffmpeg_failure_propagates(tmp_path: Path) -> None:
    def stub(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr="boom")

    with pytest.raises(FillerRenderError, match="boom"):
        ensure_filler(
            width=1920,
            height=1080,
            frame_rate_num=30,
            frame_rate_den=1,
            duration_seconds=1.0,
            output_dir=tmp_path,
            runner=stub,
        )
