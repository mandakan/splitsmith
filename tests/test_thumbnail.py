"""Unit tests for ``splitsmith.thumbnail`` (ffmpeg mocked)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from splitsmith import thumbnail


def _fake_completed() -> MagicMock:
    completed = MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = ""
    completed.stderr = ""
    completed.returncode = 0
    return completed


def _ffmpeg_writes_thumb(dest: Path) -> MagicMock:
    """Return a side-effect that simulates ffmpeg writing the thumbnail file."""

    def _run(cmd, *args, **kwargs):  # noqa: ANN001 ANN002 ANN003
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
        return _fake_completed()

    return MagicMock(side_effect=_run)


def test_ensure_extracts_and_caches(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    cache_dir = tmp_path / "thumbs"

    with (
        patch("splitsmith.thumbnail.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("splitsmith.thumbnail.subprocess.run", _ffmpeg_writes_thumb(tmp_path)) as run,
    ):
        first = thumbnail.ensure(source, cache_dir=cache_dir, duration=12.0)
        # Cache hit: ffmpeg should not run again.
        second = thumbnail.ensure(source, cache_dir=cache_dir, duration=12.0)

    assert first.exists()
    assert first.suffix == ".jpg"
    assert first == second
    assert run.call_count == 1


def test_ensure_picks_short_t_for_short_clips(tmp_path: Path) -> None:
    source = tmp_path / "short.mp4"
    source.write_bytes(b"fake")
    cache_dir = tmp_path / "thumbs"

    captured: list[list[str]] = []

    def _capture_cmd(cmd, *args, **kwargs):  # noqa: ANN001 ANN002 ANN003
        captured.append(list(cmd))
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"x")
        return _fake_completed()

    with (
        patch("splitsmith.thumbnail.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("splitsmith.thumbnail.subprocess.run", side_effect=_capture_cmd),
    ):
        thumbnail.ensure(source, cache_dir=cache_dir, duration=2.0)

    # duration=2.0 -> t = min(1.0, 2.0 * 0.1) = 0.2
    assert captured, "ffmpeg should have been invoked"
    args = captured[0]
    ss_idx = args.index("-ss")
    assert float(args[ss_idx + 1]) == pytest.approx(0.2)


def test_ensure_raises_on_timeout(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    with (
        patch("splitsmith.thumbnail.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch(
            "splitsmith.thumbnail.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=6.0),
        ),
    ):
        with pytest.raises(thumbnail.ThumbnailError, match="timed out"):
            thumbnail.ensure(source, cache_dir=tmp_path / "t", duration=10.0)


def test_ensure_raises_when_ffmpeg_produces_no_output(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    with (
        patch("splitsmith.thumbnail.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("splitsmith.thumbnail.subprocess.run", return_value=_fake_completed()),
    ):
        with pytest.raises(thumbnail.ThumbnailError, match="no output"):
            thumbnail.ensure(source, cache_dir=tmp_path / "t", duration=10.0)


def test_cached_returns_none_on_miss(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    assert thumbnail.cached(source, tmp_path / "thumbs") is None


def _ffmpeg_writes_clip() -> MagicMock:
    """Stand-in for ffmpeg producing the requested MP4 output."""

    def _run(cmd, *args, **kwargs):  # noqa: ANN001 ANN002 ANN003
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-mp4")
        return _fake_completed()

    return MagicMock(side_effect=_run)


def test_ensure_clip_extracts_and_caches(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    cache_dir = tmp_path / "thumbs"

    with (
        patch("splitsmith.thumbnail.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("splitsmith.thumbnail.subprocess.run", _ffmpeg_writes_clip()) as run,
    ):
        first = thumbnail.ensure_clip(source, cache_dir=cache_dir, center_time=12.5, duration_s=1.0)
        # Same key -> cache hit, ffmpeg not invoked again.
        second = thumbnail.ensure_clip(
            source, cache_dir=cache_dir, center_time=12.5, duration_s=1.0
        )

    assert first.exists()
    assert first.suffix == ".mp4"
    assert first == second
    assert run.call_count == 1


def test_ensure_clip_keys_on_center_time(tmp_path: Path) -> None:
    """A re-detected beep at a new time must produce a different cached
    file -- otherwise the SPA would serve a stale preview around the old
    (wrong) beep position."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    cache_dir = tmp_path / "thumbs"

    with (
        patch("splitsmith.thumbnail.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("splitsmith.thumbnail.subprocess.run", _ffmpeg_writes_clip()) as run,
    ):
        a = thumbnail.ensure_clip(source, cache_dir=cache_dir, center_time=12.5)
        b = thumbnail.ensure_clip(source, cache_dir=cache_dir, center_time=14.7)

    assert a != b
    assert run.call_count == 2


def test_ensure_clip_clamps_start_to_zero(tmp_path: Path) -> None:
    """A beep close to the start of the recording still gets a preview --
    we just clamp the start time at 0 instead of seeking to a negative."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    cache_dir = tmp_path / "thumbs"

    captured: list[list[str]] = []

    def _capture(cmd, *args, **kwargs):  # noqa: ANN001 ANN002 ANN003
        captured.append(list(cmd))
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"x")
        return _fake_completed()

    with (
        patch("splitsmith.thumbnail.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("splitsmith.thumbnail.subprocess.run", side_effect=_capture),
    ):
        thumbnail.ensure_clip(source, cache_dir=cache_dir, center_time=0.2, duration_s=1.0)

    args = captured[0]
    ss_idx = args.index("-ss")
    assert float(args[ss_idx + 1]) == pytest.approx(0.0)


def test_ensure_clip_raises_on_timeout(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    with (
        patch("splitsmith.thumbnail.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch(
            "splitsmith.thumbnail.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=12.0),
        ),
    ):
        with pytest.raises(thumbnail.ThumbnailError, match="timed out"):
            thumbnail.ensure_clip(source, cache_dir=tmp_path / "t", center_time=5.0)


def test_cached_clip_returns_none_on_miss(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    assert (
        thumbnail.cached_clip(source, tmp_path / "thumbs", center_time=5.0, duration_s=1.0) is None
    )
