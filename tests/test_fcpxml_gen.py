"""Tests for fcpxml_gen.

Unit tests don't shell out to ffprobe; they construct a synthetic VideoMetadata
and a small list of Shot records, then parse the generated FCPXML to verify
shape and frame-aligned marker placement. probe_video has a separate
integration test against the real stage_sample.mp4 fixture.
"""

from __future__ import annotations

import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from splitsmith.config import (
    OutputConfig,
    Shot,
    SplitColorThresholds,
    VideoMetadata,
)
from splitsmith.fcpxml_gen import (
    FFprobeError,
    generate_fcpxml,
    probe_video,
    split_color_band,
)


def _shot(
    n: int, time_from_beep: float, split: float, peak: float = 0.5, conf: float = 0.8
) -> Shot:
    return Shot(
        shot_number=n,
        time_absolute=10.0 + time_from_beep,
        time_from_beep=time_from_beep,
        split=split,
        peak_amplitude=peak,
        confidence=conf,
    )


def _meta_30fps() -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=20.0,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _meta_2997() -> VideoMetadata:
    return VideoMetadata(
        width=3840,
        height=2160,
        duration_seconds=20.0,
        frame_rate_num=30000,
        frame_rate_den=1001,
    )


# --- split_color_band -----------------------------------------------------


def test_split_color_band_thresholds() -> None:
    t = SplitColorThresholds()
    # shot 1 is always BLUE (draw)
    assert split_color_band(1, 1.5, t) == "BLUE"
    # transitions: split > transition_min -> BLUE regardless of value
    assert split_color_band(5, 1.2, t) == "BLUE"
    # green/yellow/red bands
    assert split_color_band(2, 0.20, t) == "GREEN"
    assert split_color_band(2, 0.25, t) == "GREEN"  # boundary inclusive
    assert split_color_band(2, 0.30, t) == "YELLOW"
    assert split_color_band(2, 0.35, t) == "YELLOW"  # boundary inclusive
    assert split_color_band(2, 0.40, t) == "RED"


# --- generate_fcpxml: structure and markers --------------------------------


def test_generate_fcpxml_minimal_structure(tmp_path: Path) -> None:
    video = tmp_path / "stage3.mp4"
    video.write_bytes(b"")
    out = tmp_path / "stage3.fcpxml"

    shots = [
        _shot(1, time_from_beep=1.42, split=1.42),
        _shot(2, time_from_beep=1.63, split=0.21),
    ]
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=shots,
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="stage3",
        config=OutputConfig(),
    )
    assert out.exists()
    content = out.read_bytes()
    assert content.startswith(b"<?xml")
    assert b"<!DOCTYPE fcpxml>" in content

    root = ET.fromstring(content)
    assert root.tag == "fcpxml"
    assert root.attrib["version"] == "1.10"
    # Resources: format + asset
    fmt = root.find("./resources/format")
    assert fmt is not None and fmt.attrib["frameDuration"] == "1/30s"
    assert fmt.attrib["width"] == "1920"
    asset = root.find("./resources/asset")
    assert asset is not None
    # Asset src URI matches the resolved video path
    media = asset.find("media-rep")
    assert media is not None and media.attrib["src"].startswith("file://")
    assert media.attrib["src"].endswith("stage3.mp4")
    # Sequence + spine + asset-clip + 2 markers
    asset_clip = root.find("./library/event/project/sequence/spine/asset-clip")
    assert asset_clip is not None
    markers = asset_clip.findall("marker")
    assert len(markers) == 2
    # Marker names embed shot number, split and band
    assert "Shot 1" in markers[0].attrib["value"]
    assert "[BLUE]" in markers[0].attrib["value"]  # draw
    assert "Shot 2" in markers[1].attrib["value"]
    assert "[GREEN]" in markers[1].attrib["value"]  # 0.21s split


def test_marker_start_is_frame_aligned(tmp_path: Path) -> None:
    """At 30 fps (1/30s frames), a shot at clip-local 6.5167s should snap to
    the nearest frame and produce a rational ``X/30s`` start."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    # beep_offset 5.0s, shot at 1.5167s after beep -> 6.5167s clip-local
    # 6.5167s / (1/30s) = 195.5 frames, rounds to 196 -> 196/30s
    shots = [_shot(1, time_from_beep=1.5167, split=1.5167)]
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=shots,
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
    )
    root = ET.fromstring(out.read_bytes())
    marker = root.find(".//marker")
    assert marker is not None
    assert marker.attrib["start"] == "196/30s"
    assert marker.attrib["duration"] == "1/30s"


def test_marker_drops_shots_beyond_clip_duration(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    # Clip is 20s; this shot lands at clip-local 25s, must be dropped.
    shots = [
        _shot(1, time_from_beep=1.0, split=1.0),
        _shot(2, time_from_beep=20.0, split=19.0),  # clip-local 25s, out of range
    ]
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=shots,
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
    )
    root = ET.fromstring(out.read_bytes())
    markers = root.findall(".//marker")
    assert len(markers) == 1


def test_2997_frame_alignment_uses_rational_duration(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    shots = [_shot(1, time_from_beep=1.0, split=1.0)]
    generate_fcpxml(
        video_path=video,
        video=_meta_2997(),
        shots=shots,
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
    )
    root = ET.fromstring(out.read_bytes())
    fmt = root.find("./resources/format")
    assert fmt is not None
    assert fmt.attrib["frameDuration"] == "1001/30000s"
    # frame_duration = 1001/30000s; clip-local 6.0s = 6.0 / (1001/30000) =
    # 179.82 frames -> 180; marker start = 180 * 1001/30000 = 180180/30000s.
    # We deliberately keep the frame-duration denominator unreduced (FCP convention).
    marker = root.find(".//marker")
    assert marker is not None
    assert marker.attrib["start"] == "180180/30000s"
    # Sanity: the unreduced fraction equals the mathematical 6.006s exactly.
    assert Fraction(180180, 30000) == Fraction(180, 1) * Fraction(1001, 30000)


def test_generate_fcpxml_raises_on_missing_video(tmp_path: Path) -> None:
    out = tmp_path / "v.fcpxml"
    with pytest.raises(FileNotFoundError):
        generate_fcpxml(
            video_path=tmp_path / "missing.mp4",
            video=_meta_30fps(),
            shots=[],
            beep_offset_seconds=5.0,
            output_path=out,
            project_name="v",
            config=OutputConfig(),
        )


# --- probe_video -----------------------------------------------------------


def test_probe_video_parses_runner_output(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")

    def fake_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        payload = (
            '{"streams":[{"width":1920,"height":1080,"r_frame_rate":"30000/1001"}],'
            '"format":{"duration":"42.5"}}'
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=payload, stderr="")

    meta = probe_video(video, runner=fake_runner)
    assert meta.width == 1920
    assert meta.height == 1080
    assert meta.frame_rate_num == 30000
    assert meta.frame_rate_den == 1001
    assert meta.duration_seconds == pytest.approx(42.5)


def test_probe_video_wraps_missing_binary(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")

    def missing(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError("nope")

    with pytest.raises(FFprobeError, match="ffprobe binary not found"):
        probe_video(video, ffprobe_binary="ffprobe-nope", runner=missing)


def test_probe_video_wraps_failure(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")

    def failing(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr="bad")

    with pytest.raises(FFprobeError, match="bad"):
        probe_video(video, runner=failing)


def test_probe_video_raises_on_unparseable(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")

    def garbage(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="not json", stderr="")

    with pytest.raises(FFprobeError, match="unparseable"):
        probe_video(video, runner=garbage)


@pytest.mark.integration
def test_probe_video_against_real_fixture(fixtures_dir: Path) -> None:
    src = fixtures_dir / "stage_sample.mp4"
    if not src.exists():
        pytest.skip(f"sample video not available at {src}")
    meta = probe_video(src)
    assert meta.width == 3840
    assert meta.height == 2160
    assert meta.frame_rate_num == 30000
    assert meta.frame_rate_den == 1001
    assert meta.duration_seconds == pytest.approx(46.11, abs=0.5)
