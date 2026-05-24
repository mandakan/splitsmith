"""Unit tests for the CLI's audio-path / extraction helpers."""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from splitsmith import cli
from splitsmith.cli import _extract_or_load_audio, _video_to_audio_path


def test_video_to_audio_path_for_mp4() -> None:
    """The default case: an mp4 input gets a sibling .wav for the extraction."""
    assert _video_to_audio_path(Path("/tmp/x.mp4")) == Path("/tmp/x.wav")


def _make_wav(path: Path, sr: int = 48000, seconds: float = 0.5) -> None:
    samples = (np.sin(2 * np.pi * 440.0 * np.arange(int(sr * seconds)) / sr) * 0.1).astype(np.float32)
    sf.write(path, samples, sr)


def test_extract_or_load_audio_skips_ffmpeg_for_wav_input(tmp_path: Path) -> None:
    """Regression for the slim-smoke failure on 2026-05-24.

    When the input is already a ``.wav``, the ffmpeg pass is skipped and
    the file is read directly via soundfile. This avoids two failure
    modes:

    1. Linux ffmpeg with input==output -> exit 254 (the original CI bug).
    2. macOS ffmpeg with ``-y`` -> silently overwrites the source.

    The test guarantees the helper doesn't shell out to ffmpeg at all
    on a ``.wav`` input by deleting ``ffmpeg`` from PATH for the call.
    """
    wav_path = tmp_path / "stage_sample.wav"
    _make_wav(wav_path)

    # Belt-and-braces: ensure the function doesn't fall through to ffmpeg.
    saved = shutil.which
    shutil.which = lambda _name: None  # type: ignore[assignment]
    try:
        audio, sr = _extract_or_load_audio(wav_path, _video_to_audio_path(wav_path))
    finally:
        shutil.which = saved  # type: ignore[assignment]

    assert sr == 48000
    assert audio.dtype == np.float32
    assert audio.size == int(48000 * 0.5)


def test_extract_or_load_audio_collision_guard(tmp_path: Path) -> None:
    """If a caller hand-crafts ``audio_path == video``, load directly."""
    wav_path = tmp_path / "x.wav"
    _make_wav(wav_path)

    audio, sr = _extract_or_load_audio(wav_path, wav_path)
    assert sr == 48000
    assert audio.size > 0


def test_extract_or_load_audio_ffmpeg_required_for_non_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-wav input still triggers the ffmpeg path."""
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"\x00" * 32)
    audio_path = _video_to_audio_path(fake_video)
    assert audio_path != fake_video

    # Stub ffmpeg out of PATH so we can assert the ffmpeg branch was reached
    # without depending on a real binary.
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    import typer

    with pytest.raises(typer.BadParameter, match="ffmpeg binary not found"):
        _extract_or_load_audio(fake_video, audio_path)


def test_detect_csv_open_pins_utf8() -> None:
    """Regression guard for the candidates CSV write.

    On Linux with ``LANG=C`` / ``LANG=POSIX`` Python's text-mode ``open``
    picks ``ascii`` from the locale and crashes on non-ASCII shooter or
    club names. Pinning ``encoding="utf-8"`` at the open site sidesteps
    that. This test fails if a future refactor drops the encoding kwarg.
    """
    src = inspect.getsource(cli.audit_prep)
    open_lines = [line for line in src.splitlines() if "csv_path.open" in line]
    assert open_lines, "csv_path.open call not found in cli.audit_prep -- did it get renamed?"
    assert all(
        'encoding="utf-8"' in line for line in open_lines
    ), f'csv_path.open must pin encoding="utf-8"; saw: {open_lines}'
