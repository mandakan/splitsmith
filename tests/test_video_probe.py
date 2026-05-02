"""Unit tests for ``splitsmith.video_probe``.

ffprobe is mocked in these unit tests so they run without depending on
ffmpeg being installed and without paying its startup cost. An end-to-end
integration test that hits the real binary lives separately and is gated
behind ``@pytest.mark.integration``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from splitsmith import video_probe


def _fake_ffprobe_output(duration: float, width: int = 1920, height: int = 1080) -> str:
    return json.dumps(
        {
            "format": {"duration": str(duration)},
            "streams": [
                {
                    "codec_name": "h264",
                    "width": width,
                    "height": height,
                    "duration": str(duration),
                }
            ],
        }
    )


def _fake_completed(stdout: str) -> MagicMock:
    completed = MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = stdout
    completed.stderr = ""
    completed.returncode = 0
    return completed


def test_probe_caches_result(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    cache_dir = tmp_path / "probes"

    with (
        patch("splitsmith.video_probe.shutil.which", return_value="/usr/bin/ffprobe"),
        patch(
            "splitsmith.video_probe.subprocess.run",
            return_value=_fake_completed(_fake_ffprobe_output(12.5)),
        ) as run,
    ):
        first = video_probe.probe(source, cache_dir=cache_dir)
        # Second call should hit the cache -- subprocess.run not invoked again.
        second = video_probe.probe(source, cache_dir=cache_dir)

    assert first.duration == pytest.approx(12.5)
    assert first.width == 1920
    assert first.codec == "h264"
    assert second == first
    assert run.call_count == 1
    assert (cache_dir / f"{video_probe.source_cache_key(source)}.json").exists()


def test_cached_returns_none_on_miss(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    cache_dir = tmp_path / "probes"
    assert video_probe.cached(source, cache_dir) is None


def test_probe_invalidates_on_mtime_change(tmp_path: Path) -> None:
    """Mutating the source flips the cache key; we re-run ffprobe."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original")
    cache_dir = tmp_path / "probes"

    with (
        patch("splitsmith.video_probe.shutil.which", return_value="/usr/bin/ffprobe"),
        patch(
            "splitsmith.video_probe.subprocess.run",
            side_effect=[
                _fake_completed(_fake_ffprobe_output(10.0)),
                _fake_completed(_fake_ffprobe_output(20.0)),
            ],
        ) as run,
    ):
        first = video_probe.probe(source, cache_dir=cache_dir)
        # Re-write with a different size to flip mtime + size in the key.
        source.write_bytes(b"changed-significantly")
        second = video_probe.probe(source, cache_dir=cache_dir)

    assert first.duration == pytest.approx(10.0)
    assert second.duration == pytest.approx(20.0)
    assert run.call_count == 2


def test_probe_raises_on_timeout(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    with (
        patch("splitsmith.video_probe.shutil.which", return_value="/usr/bin/ffprobe"),
        patch(
            "splitsmith.video_probe.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=4.0),
        ),
    ):
        with pytest.raises(video_probe.ProbeError, match="timed out"):
            video_probe.probe(source, cache_dir=tmp_path / "p")


def test_probe_raises_on_nonzero_exit(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    with (
        patch("splitsmith.video_probe.shutil.which", return_value="/usr/bin/ffprobe"),
        patch(
            "splitsmith.video_probe.subprocess.run",
            side_effect=subprocess.CalledProcessError(returncode=1, cmd="ffprobe", stderr="bad"),
        ),
    ):
        with pytest.raises(video_probe.ProbeError, match="exit 1"):
            video_probe.probe(source, cache_dir=tmp_path / "p")


def test_probe_raises_when_binary_missing(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    with patch("splitsmith.video_probe.shutil.which", return_value=None):
        with pytest.raises(video_probe.ProbeError, match="not found"):
            video_probe.probe(source, cache_dir=tmp_path / "p")


def test_source_cache_key_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert video_probe.source_cache_key(tmp_path / "no-such.mp4") == ""
