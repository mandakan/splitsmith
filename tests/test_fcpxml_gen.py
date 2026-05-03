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

from splitsmith import fcpxml_gen as fcpxml_mod
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
    # colorSpace is required so FCP doesn't warn on import (issue #41).
    assert fmt.attrib["colorSpace"] == "1-1-1 (Rec. 709)"
    sequence = root.find("./library/event/project/sequence")
    assert sequence is not None
    # audioRate is a DTD-enumerated shorthand ("48k"), NOT integer Hz --
    # FCP rejects "48000" with a DTD validation error (issue #41).
    assert sequence.attrib["audioRate"] == "48k"
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


def test_tag_source_application_writes_bplist_via_xattr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The post-write xattr call sends a valid binary plist of the string
    "Splitsmith" so FCP's import dialog shows the source app instead of
    "(null)" (issue #41). Best-effort: we verify the payload shape, not
    that the xattr actually lands on disk (CI doesn't have ``xattr``)."""
    import plistlib

    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fcpxml_mod.subprocess, "run", fake_run)

    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
    )

    assert "cmd" in captured, "xattr was never invoked"
    cmd = captured["cmd"]
    assert cmd[0] == "xattr"
    assert cmd[1:3] == ["-wx", "com.apple.metadata:kMDItemCreator"]
    payload = bytes.fromhex(cmd[3])
    assert plistlib.loads(payload) == "Splitsmith"


def test_tag_source_application_tolerates_missing_xattr_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Linux / CI / sandboxes where ``xattr`` isn't installed, the
    write must still succeed -- the tag is best-effort."""

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError("xattr not installed")

    monkeypatch.setattr(fcpxml_mod.subprocess, "run", fake_run)

    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
    )
    assert out.exists()


def test_generate_fcpxml_omits_overlay_clip_when_path_is_none(tmp_path: Path) -> None:
    """No ``overlay_path`` -> the FCPXML must be byte-identical to the
    no-overlay v1 output: one asset, no lane-1 connected clip."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
    )
    root = ET.fromstring(out.read_bytes())
    assets = root.findall("./resources/asset")
    assert len(assets) == 1
    nested = root.findall(".//spine/asset-clip/asset-clip")
    assert nested == []


def test_generate_fcpxml_omits_overlay_clip_when_file_missing(tmp_path: Path) -> None:
    """Pointed at a non-existent overlay path -> still no V2 connected clip.
    Exercise: the FCPXML can be re-generated unconditionally and only sprouts
    the overlay when the .mov is actually on disk."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
        overlay_path=tmp_path / "missing_overlay.mov",
    )
    root = ET.fromstring(out.read_bytes())
    assets = root.findall("./resources/asset")
    assert len(assets) == 1
    nested = root.findall(".//spine/asset-clip/asset-clip")
    assert nested == []


def test_generate_fcpxml_inserts_overlay_as_lane_1_connected_clip(tmp_path: Path) -> None:
    """Overlay file present -> a second asset is registered and a lane=1
    connected ``asset-clip`` lives inside the V1 spine clip on V2."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "v_overlay.mov"
    overlay.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
        overlay_path=overlay,
    )
    root = ET.fromstring(out.read_bytes())
    assets = root.findall("./resources/asset")
    assert len(assets) == 2
    overlay_asset = assets[1]
    assert overlay_asset.attrib["hasAudio"] == "0"
    media = overlay_asset.find("media-rep")
    assert media is not None and media.attrib["src"].endswith("v_overlay.mov")
    nested = root.findall(".//spine/asset-clip/asset-clip")
    assert len(nested) == 1
    overlay_clip = nested[0]
    assert overlay_clip.attrib["lane"] == "1"
    assert overlay_clip.attrib["offset"] == "0s"
    # Same duration as the primary so FCP doesn't truncate the overlay.
    assert overlay_clip.attrib["duration"] == "600/30s"  # 20s @ 30fps


def test_generate_fcpxml_attaches_secondary_cam_on_lane_1(tmp_path: Path) -> None:
    """Single secondary cam with the same beep offset as the primary -> a
    connected ``asset-clip`` on lane=1 with offset=0 (cams sync at the beep
    by default since both trims share the same pre-buffer)."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    secondary = tmp_path / "v_cam_abc.mp4"
    secondary.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
        secondaries=[
            fcpxml_mod.SecondaryClip(
                video_path=secondary,
                video=_meta_30fps(),
                beep_offset_seconds=5.0,
                label="Cam abc",
            )
        ],
    )
    root = ET.fromstring(out.read_bytes())
    assets = root.findall("./resources/asset")
    assert len(assets) == 2
    secondary_asset = assets[1]
    media = secondary_asset.find("media-rep")
    assert media is not None and media.attrib["src"].endswith("v_cam_abc.mp4")
    nested = root.findall(".//spine/asset-clip/asset-clip")
    assert len(nested) == 1
    cam_clip = nested[0]
    assert cam_clip.attrib["lane"] == "1"
    assert cam_clip.attrib["offset"] == "0s"
    assert cam_clip.attrib["start"] == "0s"
    assert cam_clip.attrib["name"] == "Cam abc"


def test_generate_fcpxml_secondary_with_short_pre_uses_offset(tmp_path: Path) -> None:
    """A cam whose beep landed earlier than the primary's beep_offset (short
    head: secondary trim has less pre-roll) gets a positive offset on the
    parent timeline so its beep still aligns with the primary's beep."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    secondary = tmp_path / "cam_short.mp4"
    secondary.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
        secondaries=[
            fcpxml_mod.SecondaryClip(
                video_path=secondary,
                video=_meta_30fps(),
                beep_offset_seconds=2.0,  # cam's beep at clip-local 2s, primary at 5s
                label="Cam short",
            )
        ],
    )
    root = ET.fromstring(out.read_bytes())
    cam_clip = root.find(".//spine/asset-clip/asset-clip")
    assert cam_clip is not None
    # Difference is 3s @ 30fps = 90 frames -> 90/30s
    assert cam_clip.attrib["offset"] == "90/30s"
    assert cam_clip.attrib["start"] == "0s"


def test_generate_fcpxml_secondary_with_long_pre_uses_start(tmp_path: Path) -> None:
    """A cam whose clip-local beep sits later than the primary's beep gets
    offset=0 and a positive ``start``: we skip into the cam's media so its
    beep frame coincides with the primary's beep frame at parent t=pb."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    secondary = tmp_path / "cam_long.mp4"
    secondary.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
        secondaries=[
            fcpxml_mod.SecondaryClip(
                video_path=secondary,
                video=_meta_30fps(),
                beep_offset_seconds=7.0,  # cam beep is 2s later in its own media
                label="Cam long",
            )
        ],
    )
    root = ET.fromstring(out.read_bytes())
    cam_clip = root.find(".//spine/asset-clip/asset-clip")
    assert cam_clip is not None
    assert cam_clip.attrib["offset"] == "0s"
    # Skip 2s = 60 frames @ 30fps
    assert cam_clip.attrib["start"] == "60/30s"


def test_generate_fcpxml_overlay_lane_above_all_secondaries(tmp_path: Path) -> None:
    """When N cams attach AND the overlay is present, the overlay rides on
    lane=N+1 so it stays on top of every cam regardless of count."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    cam_a = tmp_path / "cam_a.mp4"
    cam_a.write_bytes(b"")
    cam_b = tmp_path / "cam_b.mp4"
    cam_b.write_bytes(b"")
    overlay = tmp_path / "v_overlay.mov"
    overlay.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
        overlay_path=overlay,
        secondaries=[
            fcpxml_mod.SecondaryClip(
                video_path=cam_a, video=_meta_30fps(), beep_offset_seconds=5.0, label="A"
            ),
            fcpxml_mod.SecondaryClip(
                video_path=cam_b, video=_meta_30fps(), beep_offset_seconds=5.0, label="B"
            ),
        ],
    )
    root = ET.fromstring(out.read_bytes())
    assets = root.findall("./resources/asset")
    # primary + 2 cams + overlay = 4 assets
    assert len(assets) == 4
    nested = root.findall(".//spine/asset-clip/asset-clip")
    assert len(nested) == 3
    lanes = [int(c.attrib["lane"]) for c in nested]
    # Cams lane=1, lane=2 in input order; overlay lane=3 (above both cams).
    assert sorted(lanes) == [1, 2, 3]
    # The clip on lane=3 must reference the overlay asset, not a cam.
    overlay_clip = next(c for c in nested if c.attrib["lane"] == "3")
    assert overlay_clip.attrib["name"] == "Splitsmith overlay"


def test_generate_fcpxml_skips_missing_secondary(tmp_path: Path) -> None:
    """A secondary whose file vanished between submit and write is silently
    skipped (mirrors the overlay's missing-file behaviour) -- the rest of
    the FCPXML is unchanged so other cams still ship."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
        secondaries=[
            fcpxml_mod.SecondaryClip(
                video_path=tmp_path / "missing_cam.mp4",
                video=_meta_30fps(),
                beep_offset_seconds=5.0,
                label="Missing",
            )
        ],
    )
    root = ET.fromstring(out.read_bytes())
    assets = root.findall("./resources/asset")
    assert len(assets) == 1  # only the primary
    nested = root.findall(".//spine/asset-clip/asset-clip")
    assert nested == []


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
