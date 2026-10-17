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


def test_the_video_tracks_frame_rate_comes_from_its_mdhd(tmp_path: Path) -> None:
    check = media_check.valid_mp4(_write(tmp_path / "fifty.mp4", tiny_mp4(duration_s=2.0, video_samples=100)))
    assert check.ok, check.reason
    assert check.frame_rate == pytest.approx(50.0)


def test_no_mdhd_means_no_frame_rate_and_the_file_still_passes(tmp_path: Path) -> None:
    check = media_check.valid_mp4(_write(tmp_path / "bare.mp4", tiny_mp4(with_mdhd=False)))
    assert check.ok
    assert check.frame_rate is None


def test_a_broken_file_has_no_frame_rate(tmp_path: Path) -> None:
    assert media_check.valid_mp4(_write(tmp_path / "bad.mp4", corrupt_moov_mp4())).frame_rate is None


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
    from tests.synthetic_media import SYNTHETIC_FPS_DEN, SYNTHETIC_FPS_NUM

    assert media_check.valid_mp4(src).frame_rate == pytest.approx(
        SYNTHETIC_FPS_NUM / SYNTHETIC_FPS_DEN, abs=0.01
    )
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


def test_the_check_reads_the_moov_not_the_whole_file(tmp_path: Path) -> None:
    """A 4K trim is a gigabyte; the check runs on every trim at each
    reconcile. It must read box headers and the moov only (review I3)."""
    import struct
    import tracemalloc

    from tests.media_bytes import tiny_mp4

    head = tiny_mp4()
    mdat_payload = 256 * 1024 * 1024
    clip = tmp_path / "big_trimmed.mp4"
    with clip.open("wb") as f:
        f.write(head)
        f.write(struct.pack(">I4s", 8 + mdat_payload, b"mdat"))
        f.truncate(len(head) + 8 + mdat_payload)  # sparse: no disk, full size
    tracemalloc.start()
    try:
        check = media_check.valid_mp4(clip)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert check.ok, check.reason
    assert peak < 8 * 1024 * 1024


def test_a_truncated_mdat_is_invalid(tmp_path: Path) -> None:
    import struct

    from tests.media_bytes import tiny_mp4

    clip = tmp_path / "cut_trimmed.mp4"
    clip.write_bytes(tiny_mp4() + struct.pack(">I4s", 8 + 1000, b"mdat") + b"\x00" * 10)
    assert not media_check.valid_mp4(clip).ok


def test_deeply_nested_boxes_are_invalid_not_a_crash(tmp_path: Path) -> None:
    import struct

    payload = b""
    for _ in range(5000):
        payload = struct.pack(">I4s", 8 + len(payload), b"trak") + payload
    moov = struct.pack(">I4s", 8 + len(payload), b"moov") + payload
    clip = tmp_path / "nested.mp4"
    clip.write_bytes(moov)
    assert not media_check.valid_mp4(clip).ok
