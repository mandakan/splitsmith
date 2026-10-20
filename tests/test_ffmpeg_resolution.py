"""Every producer runs the runtime's ffmpeg, never whatever PATH holds.

The 0.81.0 DMG could not export a trim: the desktop names the bundled
ffmpeg through ``SPLITSMITH_FFMPEG``, the stage export called the trimmer
and ffprobe with their bare default names, and a Mac started from the
Finder has no Homebrew on its PATH. Dev machines and CI always do, which is
why no test saw it. These tests run with a made-up bundle and no PATH.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from splitsmith import fcpxml_gen, runtime, trim


@pytest.fixture
def bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A fake bundle: executable ffmpeg and ffprobe files the runtime is
    pointed at by env, with a PATH that has no ffmpeg anywhere."""
    folder = tmp_path / "Resources" / "bin"
    folder.mkdir(parents=True)
    for name in ("ffmpeg", "ffprobe"):
        tool = folder / name
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
    monkeypatch.setenv("SPLITSMITH_FFMPEG", str(folder / "ffmpeg"))
    monkeypatch.setenv("SPLITSMITH_FFPROBE", str(folder / "ffprobe"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    runtime._clear_runtime_cache()
    yield folder
    runtime._clear_runtime_cache()


class _Runner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout='{"streams": [], "format": {}}', stderr="")


def test_a_trim_asked_for_without_a_binary_runs_the_bundled_ffmpeg(bundle: Path, tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    runner = _Runner()
    trim.trim_video(src, tmp_path / "out.mp4", beep_time=5.0, stage_time=10.0, runner=runner)
    assert runner.calls[0][0] == str(bundle / "ffmpeg")


def test_a_named_binary_still_wins(bundle: Path, tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    runner = _Runner()
    trim.trim_video(
        src, tmp_path / "out.mp4", beep_time=5.0, stage_time=10.0, runner=runner, ffmpeg_binary="/opt/ffmpeg"
    )
    assert runner.calls[0][0] == "/opt/ffmpeg"


def test_the_web_rendition_and_the_encoder_probe_use_it_too(
    bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed: list[str] = []
    monkeypatch.setattr(
        trim, "_probe_available_encoders", lambda binary: probed.append(binary) or frozenset()
    )
    trim.select_audit_encoder("auto", runner=_Runner())
    assert probed == [str(bundle / "ffmpeg")]
    src = tmp_path / "stage1_cam_a_trimmed.mp4"
    src.write_bytes(b"x")
    runner = _Runner()
    with pytest.raises(trim.FFmpegError):
        # The fake runner writes no file, so the structural check refuses the
        # rendition; the command it ran is what this test is about.
        trim.transcode_web_trim(src, tmp_path / "web.mp4", trim.WebTrimConfig(), runner=runner)
    assert runner.calls[0][0] == str(bundle / "ffmpeg")


def test_ffprobe_resolves_the_same_way(bundle: Path, tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    runner = _Runner()
    with pytest.raises(fcpxml_gen.FFprobeError):
        # Empty probe output is unparseable; the binary is what matters here.
        fcpxml_gen.probe_video(clip, runner=runner)
    assert runner.calls[0][0] == str(bundle / "ffprobe")


def test_the_bundle_folder_leads_path_once_the_runtime_resolved(bundle: Path) -> None:
    """The safety net for producers reached with a bare name through code
    this module does not know about: the OS finds the bundle first."""
    runtime.runtime()
    assert os.environ["PATH"].split(os.pathsep)[0] == str(bundle)
    runtime._clear_runtime_cache()
    runtime.runtime()
    assert os.environ["PATH"].split(os.pathsep).count(str(bundle)) == 1, "added once, not per resolution"


def test_a_bare_name_resolution_leaves_path_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPLITSMITH_FFMPEG", raising=False)
    monkeypatch.delenv("SPLITSMITH_FFPROBE", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    runtime._clear_runtime_cache()
    try:
        runtime.runtime()
        assert os.environ["PATH"] == "/usr/bin:/bin"
    finally:
        runtime._clear_runtime_cache()
