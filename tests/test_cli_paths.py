"""Unit tests for the CLI's audio-path derivation helpers."""

from __future__ import annotations

from pathlib import Path

from splitsmith.cli import _video_to_audio_path


def test_video_to_audio_path_for_mp4() -> None:
    """The default case: an mp4 input gets a sibling .wav for the extraction."""
    assert _video_to_audio_path(Path("/tmp/x.mp4")) == Path("/tmp/x.wav")


def test_video_to_audio_path_for_wav_does_not_collide_with_input() -> None:
    """Regression for the smoke-test failure on 2026-05-24.

    When the input is already a ``.wav``, the naive
    ``Path.with_suffix(".wav")`` returns the same path. ffmpeg then either
    rejects the call (Linux: exit 254 "Output file is the same as input")
    or silently overwrites the source (macOS with ``-y``). The helper
    has to give a distinct path.
    """
    inp = Path("/tmp/sample.wav")
    out = _video_to_audio_path(inp)
    assert out != inp
    assert out.suffix == ".wav"


def test_video_to_audio_path_handles_uppercase_wav() -> None:
    """Path suffix check is case-insensitive."""
    inp = Path("/tmp/SAMPLE.WAV")
    out = _video_to_audio_path(inp)
    assert out != inp
