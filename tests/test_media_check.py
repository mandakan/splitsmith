from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from splitsmith import media_check
from tests.media_bytes import corrupt_moov_mp4, tiny_mp4
from tests.synthetic_media import build_synthetic_video, ffmpeg_available


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_a_structurally_valid_file_passes_with_its_duration(tmp_path: Path) -> None:
    check = media_check.valid_mp4(_write(tmp_path / "ok.mp4", tiny_mp4(duration_s=2.5)))
    assert check.ok, check.reason
    assert check.duration_s == pytest.approx(2.5)
    assert check.video_samples == 25


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (corrupt_moov_mp4(), "moov"),
        (tiny_mp4(with_video=False), "video"),
        (tiny_mp4(video_samples=0), "video"),
        (tiny_mp4()[:40], "moov"),
        (b"", "empty"),
    ],
)
def test_broken_files_fail_and_say_why(tmp_path: Path, data: bytes, reason: str) -> None:
    check = media_check.valid_mp4(_write(tmp_path / "bad.mp4", data))
    assert not check.ok
    assert reason in (check.reason or "")


def test_a_missing_or_unreadable_file_is_invalid_not_an_error(tmp_path: Path) -> None:
    assert not media_check.valid_mp4(tmp_path / "gone.mp4").ok


def test_the_cache_rechecks_a_file_that_changed(tmp_path: Path) -> None:
    p = _write(tmp_path / "x.mp4", tiny_mp4())
    assert media_check.valid_mp4_cached(p).ok
    p.write_bytes(corrupt_moov_mp4())
    os.utime(p, (1_900_000_000, 1_900_000_000))
    assert not media_check.valid_mp4_cached(p).ok


@pytest.mark.integration
def test_a_real_ffmpeg_file_and_a_full_range_one_pass(tmp_path: Path) -> None:
    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    src = build_synthetic_video(tmp_path / "src.mp4")
    assert media_check.valid_mp4(src).ok
    full_range = tmp_path / "full.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-vf",
            "scale=in_range=tv:out_range=pc",
            "-pix_fmt",
            "yuvj420p",
            "-c:v",
            "libx264",
            "-movflags",
            "+faststart",
            str(full_range),
        ],
        check=True,
    )
    assert media_check.valid_mp4(full_range).ok
