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
    StageComposition,
    generate_fcpxml,
    generate_match_fcpxml,
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


def test_generate_fcpxml_overlay_at_source_geometry_reuses_format(tmp_path: Path) -> None:
    """Overlay metadata matching the primary -> no extra ``<format>``
    element. The overlay's ``asset`` reuses the timeline format ID, so
    the XML stays byte-comparable with the pre-cap output."""
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
        overlay_video=_meta_30fps(),  # explicit but identical to primary
    )
    root = ET.fromstring(out.read_bytes())
    formats = root.findall("./resources/format")
    assert len(formats) == 1
    assets = root.findall("./resources/asset")
    overlay_asset = assets[1]
    # Reuses format_id ("r1") since geometry matches.
    assert overlay_asset.attrib["format"] == formats[0].attrib["id"]


def test_generate_fcpxml_overlay_with_smaller_height_emits_dedicated_format(
    tmp_path: Path,
) -> None:
    """Overlay rendered at a capped height -> a second ``<format>``
    element with the overlay's true geometry, and the overlay asset
    references that format. FCP relies on this to scale the smaller
    overlay across the timeline at default ``spatialConform="fit"``."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    overlay = tmp_path / "v_overlay.mov"
    overlay.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    overlay_meta = VideoMetadata(
        width=1280,
        height=720,
        duration_seconds=20.0,
        frame_rate_num=30,
        frame_rate_den=1,
    )
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),  # 1920x1080
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
        overlay_path=overlay,
        overlay_video=overlay_meta,
    )
    root = ET.fromstring(out.read_bytes())
    formats = root.findall("./resources/format")
    assert len(formats) == 2
    primary_fmt, overlay_fmt = formats
    assert primary_fmt.attrib["width"] == "1920"
    assert primary_fmt.attrib["height"] == "1080"
    assert overlay_fmt.attrib["width"] == "1280"
    assert overlay_fmt.attrib["height"] == "720"
    assets = root.findall("./resources/asset")
    overlay_asset = assets[1]
    assert overlay_asset.attrib["format"] == overlay_fmt.attrib["id"]


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


# --- generate_match_fcpxml -------------------------------------------------


def _make_video(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"")
    return p


def test_match_fcpxml_single_stage_no_shrink_matches_single_export(tmp_path: Path) -> None:
    """A 1-stage call with pads >= the actual head/tail should leave the
    timeline structurally identical to ``generate_fcpxml`` for the same
    inputs. We compare attribute-by-attribute rather than literal bytes
    because the asset-clip name maps to ``stage_name`` here vs ``project_name``
    there -- the issue spec calls out "modulo project_name"."""
    video = _make_video(tmp_path, "stage1.mp4")
    out_old = tmp_path / "old.fcpxml"
    out_new = tmp_path / "new.fcpxml"
    shots = [
        _shot(1, time_from_beep=1.42, split=1.42),
        _shot(2, time_from_beep=1.63, split=0.21),
    ]
    generate_fcpxml(
        video_path=video,
        video=_meta_30fps(),
        shots=shots,
        beep_offset_seconds=5.0,
        output_path=out_old,
        project_name="stage1",
        config=OutputConfig(),
    )
    generate_match_fcpxml(
        stages=[
            StageComposition(
                stage_name="stage1",
                video_path=video,
                video=_meta_30fps(),
                shots=shots,
                beep_offset_seconds=5.0,
                head_pad_seconds=10.0,  # > beep_offset -> no head trim
                tail_pad_seconds=20.0,  # > available tail -> no tail trim
            )
        ],
        output_path=out_new,
        project_name="stage1",
        config=OutputConfig(),
    )
    old = ET.fromstring(out_old.read_bytes())
    new = ET.fromstring(out_new.read_bytes())

    assert old.attrib == new.attrib
    # format
    fmt_old = old.find("./resources/format")
    fmt_new = new.find("./resources/format")
    assert fmt_old is not None and fmt_new is not None
    assert fmt_old.attrib == fmt_new.attrib
    # asset (just the primary in this single-stage no-overlay case)
    assets_old = old.findall("./resources/asset")
    assets_new = new.findall("./resources/asset")
    assert len(assets_old) == len(assets_new) == 1
    assert assets_old[0].attrib == assets_new[0].attrib
    # spine asset-clip
    clip_old = old.find("./library/event/project/sequence/spine/asset-clip")
    clip_new = new.find("./library/event/project/sequence/spine/asset-clip")
    assert clip_old is not None and clip_new is not None
    for key in ("ref", "offset", "start", "duration", "format"):
        assert clip_old.attrib[key] == clip_new.attrib[key], key
    # markers byte-for-byte
    markers_old = clip_old.findall("marker")
    markers_new = clip_new.findall("marker")
    assert len(markers_old) == len(markers_new) == 2
    for m_old, m_new in zip(markers_old, markers_new, strict=True):
        assert m_old.attrib == m_new.attrib


def test_match_fcpxml_two_stages_back_to_back(tmp_path: Path) -> None:
    """Two stages with no shrink: spine has two asset-clips, second's offset
    equals first's effective duration. Sequence duration is the sum."""
    v1 = _make_video(tmp_path, "stage1.mp4")
    v2 = _make_video(tmp_path, "stage2.mp4")
    out = tmp_path / "match.fcpxml"
    generate_match_fcpxml(
        stages=[
            StageComposition(
                stage_name="stage1",
                video_path=v1,
                video=_meta_30fps(),
                shots=[_shot(1, time_from_beep=1.0, split=1.0)],
                beep_offset_seconds=5.0,
                head_pad_seconds=10.0,
                tail_pad_seconds=20.0,
            ),
            StageComposition(
                stage_name="stage2",
                video_path=v2,
                video=_meta_30fps(),
                shots=[_shot(1, time_from_beep=2.0, split=2.0)],
                beep_offset_seconds=5.0,
                head_pad_seconds=10.0,
                tail_pad_seconds=20.0,
            ),
        ],
        output_path=out,
        project_name="match",
        config=OutputConfig(),
    )
    root = ET.fromstring(out.read_bytes())
    spine_clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert len(spine_clips) == 2
    assert spine_clips[0].attrib["offset"] == "0s"
    assert spine_clips[0].attrib["start"] == "0s"
    assert spine_clips[0].attrib["duration"] == "600/30s"
    assert spine_clips[0].attrib["name"] == "stage1"
    assert spine_clips[1].attrib["offset"] == "600/30s"
    assert spine_clips[1].attrib["start"] == "0s"
    assert spine_clips[1].attrib["duration"] == "600/30s"
    assert spine_clips[1].attrib["name"] == "stage2"
    seq = root.find("./library/event/project/sequence")
    assert seq is not None
    assert seq.attrib["duration"] == "1200/30s"
    assert root.find("./library/event/project").attrib["name"] == "match"


def test_match_fcpxml_action_cut_padding_trims_each_stage(tmp_path: Path) -> None:
    """head_pad=0.5, tail_pad=1.0 against a 20s synthetic clip with beep at
    5s and last shot at 1.5s after beep:
      - head_trim = beep_offset - head_pad = 5.0 - 0.5 = 4.5s = 135 frames
      - tail_avail = 20 - (5 + 1.5) = 13.5s
      - tail_trim = 13.5 - 1.0 = 12.5s = 375 frames
      - eff_duration = 600 - 135 - 375 = 90 frames = 3.0s
    Stage 1 starts on the spine at 90/30s."""
    v1 = _make_video(tmp_path, "stage1.mp4")
    v2 = _make_video(tmp_path, "stage2.mp4")
    out = tmp_path / "match.fcpxml"
    shots = [
        _shot(1, time_from_beep=1.0, split=1.0),
        _shot(2, time_from_beep=1.5, split=0.5),
    ]
    generate_match_fcpxml(
        stages=[
            StageComposition(
                stage_name=name,
                video_path=path,
                video=_meta_30fps(),
                shots=shots,
                beep_offset_seconds=5.0,
                head_pad_seconds=0.5,
                tail_pad_seconds=1.0,
            )
            for name, path in (("stage1", v1), ("stage2", v2))
        ],
        output_path=out,
        project_name="match",
        config=OutputConfig(),
    )
    root = ET.fromstring(out.read_bytes())
    spine_clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert spine_clips[0].attrib["start"] == "135/30s"
    assert spine_clips[0].attrib["duration"] == "90/30s"
    assert spine_clips[0].attrib["offset"] == "0s"
    assert spine_clips[1].attrib["start"] == "135/30s"
    assert spine_clips[1].attrib["duration"] == "90/30s"
    assert spine_clips[1].attrib["offset"] == "90/30s"
    seq = root.find("./library/event/project/sequence")
    assert seq is not None
    assert seq.attrib["duration"] == "180/30s"


def test_match_fcpxml_drops_markers_outside_trimmed_window(tmp_path: Path) -> None:
    """Shots whose clip-local time falls outside [head_trim, head_trim +
    eff_duration] are dropped. With head_pad=0.5, tail_pad=1.0 and a beep at
    5s the visible window is [4.5s, 7.5s]. The pre-beep shot at -0.6s lands
    at clip-local 4.4s (dropped); 0.4s past beep -> 5.4s (kept); 1.5s past
    beep -> 6.5s (kept; this is the latest shot so tail_avail is computed
    off it)."""
    video = _make_video(tmp_path, "v.mp4")
    out = tmp_path / "v.fcpxml"
    shots = [
        _shot(1, time_from_beep=-0.6, split=0.0),
        _shot(2, time_from_beep=0.4, split=1.0),
        _shot(3, time_from_beep=1.5, split=1.1),
    ]
    generate_match_fcpxml(
        stages=[
            StageComposition(
                stage_name="v",
                video_path=video,
                video=_meta_30fps(),
                shots=shots,
                beep_offset_seconds=5.0,
                head_pad_seconds=0.5,
                tail_pad_seconds=1.0,
            )
        ],
        output_path=out,
        project_name="match",
        config=OutputConfig(),
    )
    root = ET.fromstring(out.read_bytes())
    markers = root.findall(".//spine/asset-clip/marker")
    assert len(markers) == 2
    assert "Shot 2" in markers[0].attrib["value"]
    assert "Shot 3" in markers[1].attrib["value"]


def test_match_fcpxml_secondary_alignment_with_head_trim(tmp_path: Path) -> None:
    """Secondary cam stays beep-aligned even after the primary's head is
    trimmed. With head_pad=0.5 the primary's beep moves to local 0.5s; the
    cam (same beep_offset=5.0) needs to skip 4.5s into its own media so its
    beep also lands at local 0.5s."""
    primary = _make_video(tmp_path, "primary.mp4")
    secondary = _make_video(tmp_path, "secondary.mp4")
    out = tmp_path / "v.fcpxml"
    generate_match_fcpxml(
        stages=[
            StageComposition(
                stage_name="v",
                video_path=primary,
                video=_meta_30fps(),
                shots=[_shot(1, time_from_beep=1.0, split=1.0)],
                beep_offset_seconds=5.0,
                head_pad_seconds=0.5,
                tail_pad_seconds=20.0,  # no tail trim (we want a long visible window for the cam)
                secondaries=(
                    fcpxml_mod.SecondaryClip(
                        video_path=secondary,
                        video=_meta_30fps(),
                        beep_offset_seconds=5.0,
                        label="Cam",
                    ),
                ),
            )
        ],
        output_path=out,
        project_name="match",
        config=OutputConfig(),
    )
    root = ET.fromstring(out.read_bytes())
    cam_clip = root.find(".//spine/asset-clip/asset-clip")
    assert cam_clip is not None
    # Connected-clip offset is in the parent's source-time, so it must be
    # bumped by head_trim (135 frames) to anchor at the parent's visible
    # start. delta = (5.0 - 4.5) - 5.0 = -4.5s -> sec_start = 4.5s = 135.
    assert cam_clip.attrib["offset"] == "135/30s"
    assert cam_clip.attrib["start"] == "135/30s"


def test_match_fcpxml_per_stage_overlay_lane_isolation(tmp_path: Path) -> None:
    """Stage 0 has overlay + secondary, stage 1 has neither. Resource IDs
    are unique across stages and lanes are isolated per stage (stage 1's
    primary clip has no nested clips)."""
    v1 = _make_video(tmp_path, "stage1.mp4")
    v2 = _make_video(tmp_path, "stage2.mp4")
    cam = _make_video(tmp_path, "cam.mp4")
    overlay = _make_video(tmp_path, "stage1_overlay.mov")
    out = tmp_path / "match.fcpxml"
    generate_match_fcpxml(
        stages=[
            StageComposition(
                stage_name="stage1",
                video_path=v1,
                video=_meta_30fps(),
                shots=[_shot(1, time_from_beep=1.0, split=1.0)],
                beep_offset_seconds=5.0,
                head_pad_seconds=10.0,
                tail_pad_seconds=20.0,
                overlay_path=overlay,
                overlay_video=_meta_30fps(),
                secondaries=(
                    fcpxml_mod.SecondaryClip(
                        video_path=cam,
                        video=_meta_30fps(),
                        beep_offset_seconds=5.0,
                        label="Cam",
                    ),
                ),
            ),
            StageComposition(
                stage_name="stage2",
                video_path=v2,
                video=_meta_30fps(),
                shots=[_shot(1, time_from_beep=2.0, split=2.0)],
                beep_offset_seconds=5.0,
                head_pad_seconds=10.0,
                tail_pad_seconds=20.0,
            ),
        ],
        output_path=out,
        project_name="match",
        config=OutputConfig(),
    )
    root = ET.fromstring(out.read_bytes())
    assets = root.findall("./resources/asset")
    # stage1 primary + cam + overlay + stage2 primary = 4
    assert len(assets) == 4
    asset_ids = [a.attrib["id"] for a in assets]
    assert asset_ids == ["r2", "r3", "r4", "r5"]
    spine_clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert len(spine_clips) == 2
    nested_in_stage1 = spine_clips[0].findall("asset-clip")
    nested_in_stage2 = spine_clips[1].findall("asset-clip")
    # stage 1: cam (lane=1) + overlay (lane=2) = 2 nested clips
    assert {c.attrib["lane"] for c in nested_in_stage1} == {"1", "2"}
    # stage 2: nothing nested
    assert nested_in_stage2 == []


def test_match_fcpxml_overlay_skips_into_media_when_head_trimmed(tmp_path: Path) -> None:
    """The overlay was rendered to mirror the primary frame-for-frame, so
    after head_trim it must skip the same amount into its own media to stay
    in sync. Its duration also shrinks to match the primary's effective
    duration."""
    video = _make_video(tmp_path, "v.mp4")
    overlay = _make_video(tmp_path, "v_overlay.mov")
    out = tmp_path / "v.fcpxml"
    generate_match_fcpxml(
        stages=[
            StageComposition(
                stage_name="v",
                video_path=video,
                video=_meta_30fps(),
                shots=[_shot(1, time_from_beep=1.5, split=1.5)],
                beep_offset_seconds=5.0,
                head_pad_seconds=0.5,
                tail_pad_seconds=1.0,
                overlay_path=overlay,
                overlay_video=_meta_30fps(),
            )
        ],
        output_path=out,
        project_name="match",
        config=OutputConfig(),
    )
    root = ET.fromstring(out.read_bytes())
    overlay_clip = root.find(".//spine/asset-clip/asset-clip")
    assert overlay_clip is not None
    # head_trim = 4.5s = 135 frames; eff_duration = 90 frames. ``offset``
    # is in the parent's source-time, so it matches ``start`` (head_trim)
    # to anchor the overlay at the parent's visible spine start.
    assert overlay_clip.attrib["offset"] == "135/30s"
    assert overlay_clip.attrib["start"] == "135/30s"
    assert overlay_clip.attrib["duration"] == "90/30s"
    assert overlay_clip.attrib["lane"] == "1"


def test_match_fcpxml_raises_on_mixed_frame_rates(tmp_path: Path) -> None:
    v1 = _make_video(tmp_path, "stage1.mp4")
    v2 = _make_video(tmp_path, "stage2.mp4")
    out = tmp_path / "match.fcpxml"
    with pytest.raises(ValueError, match="mixed frame rates"):
        generate_match_fcpxml(
            stages=[
                StageComposition(
                    stage_name="stage1",
                    video_path=v1,
                    video=_meta_30fps(),
                    shots=[],
                    beep_offset_seconds=5.0,
                    head_pad_seconds=5.0,
                    tail_pad_seconds=5.0,
                ),
                StageComposition(
                    stage_name="stage2",
                    video_path=v2,
                    video=_meta_2997(),
                    shots=[],
                    beep_offset_seconds=5.0,
                    head_pad_seconds=5.0,
                    tail_pad_seconds=5.0,
                ),
            ],
            output_path=out,
            project_name="match",
            config=OutputConfig(),
        )


def test_match_fcpxml_raises_on_empty_stages(tmp_path: Path) -> None:
    out = tmp_path / "match.fcpxml"
    with pytest.raises(ValueError, match="at least one stage"):
        generate_match_fcpxml(
            stages=[],
            output_path=out,
            project_name="match",
            config=OutputConfig(),
        )


def test_match_fcpxml_raises_on_missing_video(tmp_path: Path) -> None:
    out = tmp_path / "match.fcpxml"
    with pytest.raises(FileNotFoundError):
        generate_match_fcpxml(
            stages=[
                StageComposition(
                    stage_name="stage1",
                    video_path=tmp_path / "missing.mp4",
                    video=_meta_30fps(),
                    shots=[],
                    beep_offset_seconds=5.0,
                    head_pad_seconds=5.0,
                    tail_pad_seconds=5.0,
                )
            ],
            output_path=out,
            project_name="match",
            config=OutputConfig(),
        )


def test_match_fcpxml_raises_when_pads_collapse_duration(tmp_path: Path) -> None:
    """If head_pad + tail_pad math leaves nothing visible, the function
    refuses rather than emitting a zero-duration clip."""
    video = _make_video(tmp_path, "v.mp4")
    out = tmp_path / "match.fcpxml"
    # 20s clip, beep at 0.001s, no shots, head_pad 0, tail_pad 0:
    # head_trim ~= 0, tail_avail = 20 - 0.001 = ~20, tail_trim ~= 20 ->
    # effective ~ 0. Use a stage where the shot pushes tail_avail to 0.
    # Simplest forced collapse: video.duration = 0.001s -> primary_duration
    # rounds to 0 frames -> negative effective duration.
    tiny_meta = VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=0.001,
        frame_rate_num=30,
        frame_rate_den=1,
    )
    with pytest.raises(ValueError, match="non-positive effective duration"):
        generate_match_fcpxml(
            stages=[
                StageComposition(
                    stage_name="v",
                    video_path=video,
                    video=tiny_meta,
                    shots=[],
                    beep_offset_seconds=0.0,
                    head_pad_seconds=0.0,
                    tail_pad_seconds=0.0,
                )
            ],
            output_path=out,
            project_name="match",
            config=OutputConfig(),
        )


# --- probe_video -----------------------------------------------------------


# --- PiP for secondary cams (issue #193) ----------------------------------


def _expected_pip_attrs(
    *,
    seq_w: int,
    seq_h: int,
    scale: float,
    margin_pct: float,
    corner: str,
) -> dict[str, str]:
    half_w = seq_w / 2.0
    half_h = seq_h / 2.0
    clip_half_w = half_w * scale
    clip_half_h = half_h * scale
    margin_x = seq_w * (margin_pct / 100.0)
    margin_y = seq_h * (margin_pct / 100.0)
    if corner in ("top-right", "bottom-right"):
        x = half_w - clip_half_w - margin_x
    else:
        x = -(half_w - clip_half_w - margin_x)
    if corner in ("top-right", "top-left"):
        y = half_h - clip_half_h - margin_y
    else:
        y = -(half_h - clip_half_h - margin_y)
    return {"scale": f"{scale:g} {scale:g}", "position": f"{x:g} {y:g}"}


def test_secondary_without_pip_emits_no_transform(tmp_path: Path) -> None:
    """Default behaviour unchanged: a SecondaryClip with ``pip=None`` lands
    full-frame on its lane, no ``<adjust-transform>``."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    secondary = tmp_path / "cam.mp4"
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
                label="Cam",
            )
        ],
    )
    cam_clip = ET.fromstring(out.read_bytes()).find(".//spine/asset-clip/asset-clip")
    assert cam_clip is not None
    assert cam_clip.find("adjust-transform") is None


@pytest.mark.parametrize(
    "corner",
    ["top-right", "top-left", "bottom-right", "bottom-left"],
)
def test_secondary_with_pip_emits_corner_transform(corner: str, tmp_path: Path) -> None:
    """``pip`` set -> ``<adjust-transform>`` as the cam clip's first child,
    with scale + position computed from the sequence dims and corner."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    secondary = tmp_path / "cam.mp4"
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
                label="Cam",
                pip=fcpxml_mod.PipPlacement(corner=corner),  # type: ignore[arg-type]
            )
        ],
    )
    cam_clip = ET.fromstring(out.read_bytes()).find(".//spine/asset-clip/asset-clip")
    assert cam_clip is not None
    transform = cam_clip.find("adjust-transform")
    assert transform is not None
    assert list(cam_clip)[0] is transform  # transform must precede markers / nested clips
    expected = _expected_pip_attrs(
        seq_w=1920, seq_h=1080, scale=0.25, margin_pct=2.0, corner=corner
    )
    assert transform.attrib == expected


def test_pip_position_scales_with_sequence_dimensions(tmp_path: Path) -> None:
    """At a 4K sequence with ``scale=0.25, margin_pct=2.0`` the absolute
    pixel position is twice the 1080p value -- the clip stays anchored to
    the same fractional corner regardless of resolution."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    secondary = tmp_path / "cam.mp4"
    secondary.write_bytes(b"")
    out = tmp_path / "v.fcpxml"
    generate_fcpxml(
        video_path=video,
        video=_meta_2997(),  # 3840x2160
        shots=[_shot(1, time_from_beep=1.0, split=1.0)],
        beep_offset_seconds=5.0,
        output_path=out,
        project_name="v",
        config=OutputConfig(),
        secondaries=[
            fcpxml_mod.SecondaryClip(
                video_path=secondary,
                video=_meta_2997(),
                beep_offset_seconds=5.0,
                label="Cam",
                pip=fcpxml_mod.PipPlacement(corner="top-right"),
            )
        ],
    )
    transform = ET.fromstring(out.read_bytes()).find(
        ".//spine/asset-clip/asset-clip/adjust-transform"
    )
    assert transform is not None
    expected = _expected_pip_attrs(
        seq_w=3840, seq_h=2160, scale=0.25, margin_pct=2.0, corner="top-right"
    )
    assert transform.attrib == expected


def test_apply_pip_corner_cycle_assigns_rotating_corners() -> None:
    """Multiple cams without explicit pip get TR -> TL -> BR -> BL when
    ``apply_pip_corner_cycle`` is asked to inject a default."""
    secs = tuple(
        fcpxml_mod.SecondaryClip(
            video_path=Path(f"cam{i}.mp4"),
            video=_meta_30fps(),
            beep_offset_seconds=5.0,
            label=f"Cam {i}",
        )
        for i in range(4)
    )
    laid_out = fcpxml_mod.apply_pip_corner_cycle(
        secs, default=fcpxml_mod.PipPlacement(scale=0.3, margin_pct=1.5)
    )
    corners = [s.pip.corner for s in laid_out]  # type: ignore[union-attr]
    assert corners == ["top-right", "top-left", "bottom-right", "bottom-left"]
    # Default scale / margin propagate; corner is the only override.
    for s in laid_out:
        assert s.pip is not None
        assert s.pip.scale == 0.3
        assert s.pip.margin_pct == 1.5


def test_apply_pip_corner_cycle_preserves_explicit() -> None:
    """A cam that already carries an explicit ``pip`` keeps it; cams without
    one rotate through the remaining corners in input order."""
    explicit = fcpxml_mod.SecondaryClip(
        video_path=Path("cam_main.mp4"),
        video=_meta_30fps(),
        beep_offset_seconds=5.0,
        label="Main cam",
        pip=fcpxml_mod.PipPlacement(corner="bottom-left", scale=0.4),
    )
    auto = fcpxml_mod.SecondaryClip(
        video_path=Path("cam_aux.mp4"),
        video=_meta_30fps(),
        beep_offset_seconds=5.0,
        label="Aux cam",
    )
    laid_out = fcpxml_mod.apply_pip_corner_cycle(
        (explicit, auto), default=fcpxml_mod.PipPlacement()
    )
    assert laid_out[0] is explicit  # untouched
    assert laid_out[1].pip is not None
    assert laid_out[1].pip.corner == "top-right"  # cycle starts at TR


def test_apply_pip_corner_cycle_default_none_is_noop() -> None:
    """Without a default, cams that lack ``pip`` stay as-is -- this is the
    "stacked full-frame" path used by today's exports."""
    sec = fcpxml_mod.SecondaryClip(
        video_path=Path("cam.mp4"),
        video=_meta_30fps(),
        beep_offset_seconds=5.0,
        label="Cam",
    )
    laid_out = fcpxml_mod.apply_pip_corner_cycle((sec,), default=None)
    assert laid_out == (sec,)


def test_match_fcpxml_secondary_pip_uses_sequence_dims(tmp_path: Path) -> None:
    """In the stitched composer the sequence format comes from stage 0, so
    every PiP transform must compute against the *base* dims even when a
    later stage's primary has a different intrinsic size."""
    primary = _make_video(tmp_path, "primary.mp4")
    secondary = _make_video(tmp_path, "secondary.mp4")
    out = tmp_path / "match.fcpxml"
    generate_match_fcpxml(
        stages=[
            StageComposition(
                stage_name="s1",
                video_path=primary,
                video=_meta_30fps(),  # 1920x1080
                shots=[_shot(1, time_from_beep=1.0, split=1.0)],
                beep_offset_seconds=5.0,
                head_pad_seconds=5.0,
                tail_pad_seconds=5.0,
                secondaries=(
                    fcpxml_mod.SecondaryClip(
                        video_path=secondary,
                        video=_meta_30fps(),
                        beep_offset_seconds=5.0,
                        label="Cam",
                        pip=fcpxml_mod.PipPlacement(corner="top-right"),
                    ),
                ),
            )
        ],
        output_path=out,
        project_name="match",
        config=OutputConfig(),
    )
    transform = ET.fromstring(out.read_bytes()).find(
        ".//spine/asset-clip/asset-clip/adjust-transform"
    )
    assert transform is not None
    expected = _expected_pip_attrs(
        seq_w=1920, seq_h=1080, scale=0.25, margin_pct=2.0, corner="top-right"
    )
    assert transform.attrib == expected


# --- stage transitions (issue #195) ---------------------------------------


def _two_stage_match(tmp_path: Path) -> tuple[Path, Path, Path]:
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    out = tmp_path / "match.fcpxml"
    return primary_a, primary_b, out


def _basic_match_stages(primary_a: Path, primary_b: Path) -> list[fcpxml_mod.StageComposition]:
    return [
        fcpxml_mod.StageComposition(
            stage_name="A",
            video_path=primary_a,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        ),
        fcpxml_mod.StageComposition(
            stage_name="B",
            video_path=primary_b,
            video=_meta_30fps(),
            shots=[_shot(1, 0.8, 0.8)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        ),
    ]


def test_match_with_transition_emits_effect_resource(tmp_path: Path) -> None:
    primary_a, primary_b, out = _two_stage_match(tmp_path)
    stages = _basic_match_stages(primary_a, primary_b)
    generate_match_fcpxml(
        stages=stages,
        output_path=out,
        project_name="match",
        config=OutputConfig(),
        transitions=[
            fcpxml_mod.StageTransition(
                after_stage_index=0, kind="cross-dissolve", duration_seconds=0.5
            )
        ],
    )
    root = ET.fromstring(out.read_bytes())
    effects = root.findall("./resources/effect")
    assert len(effects) == 1
    assert effects[0].attrib["name"] == "Cross Dissolve"
    assert effects[0].attrib["uid"].endswith("Cross Dissolve.motn")
    transition = root.find(".//spine/transition")
    assert transition is not None
    assert transition.attrib["name"] == "Cross Dissolve"
    filter_video = transition.find("filter-video")
    assert filter_video is not None
    assert filter_video.attrib["ref"] == effects[0].attrib["id"]


def test_transition_offset_centred_on_boundary(tmp_path: Path) -> None:
    """Transition at duration=0.5s @ 30fps -> 15 frames; centred on the
    cut means offset = stage_A.end - 15 // 2 = stage_A.end - 7."""
    primary_a, primary_b, out = _two_stage_match(tmp_path)
    stages = _basic_match_stages(primary_a, primary_b)
    generate_match_fcpxml(
        stages=stages,
        output_path=out,
        project_name="match",
        config=OutputConfig(),
        transitions=[
            fcpxml_mod.StageTransition(
                after_stage_index=0, kind="cross-dissolve", duration_seconds=0.5
            )
        ],
    )
    root = ET.fromstring(out.read_bytes())
    # 20s clip, beep=5, last shot at +1.0s -> tail_avail=14s, tail_pad=5
    # trims 9s. Effective = 20 - 0 - 9 = 11s = 330 frames. Transition
    # duration = 15 frames; half = 7. Offset = 330 - 7 = 323.
    transition = root.find(".//spine/transition")
    assert transition is not None
    assert transition.attrib["offset"] == "323/30s"
    assert transition.attrib["duration"] == "15/30s"


def test_dip_to_color_uses_separate_effect(tmp_path: Path) -> None:
    """A different transition kind allocates its own ``<effect>``;
    same kind on multiple boundaries reuses one."""
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    primary_c = _make_video(tmp_path, "c.mp4")
    out = tmp_path / "match.fcpxml"
    stages = [
        fcpxml_mod.StageComposition(
            stage_name=name,
            video_path=p,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        )
        for name, p in (("A", primary_a), ("B", primary_b), ("C", primary_c))
    ]
    generate_match_fcpxml(
        stages=stages,
        output_path=out,
        project_name="match",
        config=OutputConfig(),
        transitions=[
            fcpxml_mod.StageTransition(after_stage_index=0, kind="cross-dissolve"),
            fcpxml_mod.StageTransition(after_stage_index=1, kind="dip-to-color"),
        ],
    )
    root = ET.fromstring(out.read_bytes())
    effects = {e.attrib["name"]: e for e in root.findall("./resources/effect")}
    assert set(effects) == {"Cross Dissolve", "Dip to Color Dissolve"}
    transitions = root.findall(".//spine/transition")
    assert [t.attrib["name"] for t in transitions] == [
        "Cross Dissolve",
        "Dip to Color Dissolve",
    ]


def test_transition_too_long_for_adjacent_stage_raises(tmp_path: Path) -> None:
    """A transition longer than 2x either stage's effective window is
    rejected so the user sees a clear error rather than malformed
    FCPXML."""
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    out = tmp_path / "match.fcpxml"
    stages = [
        fcpxml_mod.StageComposition(
            stage_name="A",
            video_path=primary_a,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=0.05,
            tail_pad_seconds=0.05,
        ),
        fcpxml_mod.StageComposition(
            stage_name="B",
            video_path=primary_b,
            video=_meta_30fps(),
            shots=[_shot(1, 0.8, 0.8)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        ),
    ]
    with pytest.raises(ValueError, match="exceeds the available material"):
        generate_match_fcpxml(
            stages=stages,
            output_path=out,
            project_name="match",
            config=OutputConfig(),
            transitions=[
                fcpxml_mod.StageTransition(
                    after_stage_index=0,
                    kind="cross-dissolve",
                    duration_seconds=5.0,
                )
            ],
        )


def test_transition_index_out_of_range_raises(tmp_path: Path) -> None:
    primary_a, primary_b, out = _two_stage_match(tmp_path)
    stages = _basic_match_stages(primary_a, primary_b)
    with pytest.raises(ValueError, match="out of range"):
        generate_match_fcpxml(
            stages=stages,
            output_path=out,
            project_name="match",
            config=OutputConfig(),
            transitions=[fcpxml_mod.StageTransition(after_stage_index=1, kind="cross-dissolve")],
        )


def test_duplicate_transitions_raise(tmp_path: Path) -> None:
    primary_a = _make_video(tmp_path, "a.mp4")
    primary_b = _make_video(tmp_path, "b.mp4")
    primary_c = _make_video(tmp_path, "c.mp4")
    out = tmp_path / "match.fcpxml"
    stages = [
        fcpxml_mod.StageComposition(
            stage_name=name,
            video_path=p,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        )
        for name, p in (("A", primary_a), ("B", primary_b), ("C", primary_c))
    ]
    with pytest.raises(ValueError, match="duplicate transition"):
        generate_match_fcpxml(
            stages=stages,
            output_path=out,
            project_name="match",
            config=OutputConfig(),
            transitions=[
                fcpxml_mod.StageTransition(after_stage_index=0),
                fcpxml_mod.StageTransition(after_stage_index=0),
            ],
        )


def test_no_transitions_emits_unchanged_spine(tmp_path: Path) -> None:
    """The default path (no transitions) emits the same spine as
    before -- absence of transitions must not change the output."""
    primary_a, primary_b, out = _two_stage_match(tmp_path)
    stages = _basic_match_stages(primary_a, primary_b)
    generate_match_fcpxml(
        stages=stages, output_path=out, project_name="match", config=OutputConfig()
    )
    root = ET.fromstring(out.read_bytes())
    assert root.find(".//spine/transition") is None
    assert root.find("./resources/effect") is None


# --- probe_video -----------------------------------------------------------


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
