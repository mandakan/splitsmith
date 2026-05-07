"""Tests for the FCP7 XML renderer (issue #197).

These tests pin the structural shape Premiere / DaVinci expect and the
frame-math conversions from the IR's float seconds. Manual import in the
target NLEs is the release gate -- we cover what's mechanically testable
without an actual NLE in the loop.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from splitsmith import composition, fcp7xml_render
from splitsmith.config import Shot, VideoMetadata
from splitsmith.fcpxml_gen import (
    PipPlacement,
    SecondaryClip,
    StageComposition,
)


def _shot(n: int, time_from_beep: float, split: float) -> Shot:
    return Shot(
        shot_number=n,
        time_absolute=10.0 + time_from_beep,
        time_from_beep=time_from_beep,
        split=split,
        peak_amplitude=0.5,
        confidence=0.8,
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


def _make_video(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"")
    return p


def _stage(
    *,
    tmp_path: Path,
    name: str,
    primary_name: str,
    meta: VideoMetadata = None,  # type: ignore[assignment]
    head_pad: float = 5.0,
    tail_pad: float = 5.0,
    secondaries: tuple[SecondaryClip, ...] = (),
    overlay_path: Path | None = None,
) -> StageComposition:
    return StageComposition(
        stage_name=name,
        video_path=_make_video(tmp_path, primary_name),
        video=meta if meta is not None else _meta_30fps(),
        shots=[_shot(1, 1.0, 1.0), _shot(2, 1.3, 0.3)],
        beep_offset_seconds=5.0,
        head_pad_seconds=head_pad,
        tail_pad_seconds=tail_pad,
        secondaries=secondaries,
        overlay_path=overlay_path,
        overlay_video=meta if meta is not None else _meta_30fps(),
    )


def _render(stages: list[StageComposition], tmp_path: Path) -> ET.Element:
    out = tmp_path / "match.xml"
    comp = composition.from_stage_compositions(stages, project_name="match")
    fcp7xml_render.render_fcp7xml(comp, output_path=out)
    raw = out.read_bytes()
    assert raw.startswith(b"<?xml")
    assert b"<!DOCTYPE xmeml>" in raw
    return ET.fromstring(raw)


# --- frame rate conversion ------------------------------------------------


@pytest.mark.parametrize(
    ("num", "den", "expected_timebase", "expected_ntsc"),
    [
        (30, 1, 30, False),
        (24, 1, 24, False),
        (25, 1, 25, False),
        (60, 1, 60, False),
        (30000, 1001, 30, True),
        (60000, 1001, 60, True),
        (24000, 1001, 24, True),
    ],
)
def test_fcp7_rate_conversion(
    num: int, den: int, expected_timebase: int, expected_ntsc: bool
) -> None:
    timebase, ntsc = fcp7xml_render._fcp7_rate(num, den)
    assert timebase == expected_timebase
    assert ntsc is expected_ntsc


# --- xmeml shape ----------------------------------------------------------


def test_render_emits_xmeml_v5_with_doctype(tmp_path: Path) -> None:
    stages = [_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")]
    out = tmp_path / "match.xml"
    comp = composition.from_stage_compositions(stages, project_name="match")
    fcp7xml_render.render_fcp7xml(comp, output_path=out)
    raw = out.read_bytes()
    assert raw.startswith(b"<?xml")
    assert b"<!DOCTYPE xmeml>" in raw
    root = ET.fromstring(raw)
    assert root.tag == "xmeml"
    assert root.attrib["version"] == "5"


def test_sequence_carries_dims_and_timebase(tmp_path: Path) -> None:
    stages = [_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4", meta=_meta_2997())]
    root = _render(stages, tmp_path)
    sample = root.find(".//sequence/media/video/format/samplecharacteristics")
    assert sample is not None
    assert sample.findtext("width") == "3840"
    assert sample.findtext("height") == "2160"
    assert sample.findtext("rate/timebase") == "30"
    assert sample.findtext("rate/ntsc") == "TRUE"


def test_sequence_duration_sums_effective_stage_lengths(tmp_path: Path) -> None:
    """Default pads (5/5) on a 20s clip with shots at +1.0/+1.3:
    head_avail=5, tail_avail=20-(5+1.3)=13.7. head_pad>=head_avail keeps
    everything (head_trim=0); tail_pad=5 < tail_avail=13.7 trims 8.7s
    off the tail. Effective per stage = 600 - 0 - 261 = 339 frames; two
    stages = 678."""
    stages = [
        _stage(tmp_path=tmp_path, name="A", primary_name="a.mp4"),
        _stage(tmp_path=tmp_path, name="B", primary_name="b.mp4"),
    ]
    root = _render(stages, tmp_path)
    assert root.findtext("./project/children/sequence/duration") == "678"


# --- track / clipitem layout ---------------------------------------------


def test_single_stage_lands_one_clipitem_on_v1(tmp_path: Path) -> None:
    stages = [_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")]
    root = _render(stages, tmp_path)
    tracks = root.findall(".//sequence/media/video/track")
    assert len(tracks) == 1
    assert len(tracks[0].findall("clipitem")) == 1


def test_multi_stage_lands_back_to_back_on_v1(tmp_path: Path) -> None:
    stages = [
        _stage(tmp_path=tmp_path, name="A", primary_name="a.mp4"),
        _stage(tmp_path=tmp_path, name="B", primary_name="b.mp4"),
    ]
    root = _render(stages, tmp_path)
    v1 = root.find(".//sequence/media/video/track")
    assert v1 is not None
    clips = v1.findall("clipitem")
    assert len(clips) == 2
    # Each stage's effective window = 339 frames (see duration test); back-
    # to-back placement -> stage A ends where stage B starts.
    assert clips[0].findtext("start") == "0"
    assert clips[0].findtext("end") == "339"
    assert clips[1].findtext("start") == "339"
    assert clips[1].findtext("end") == "678"


def test_secondary_cam_lands_on_v2_with_alignment(tmp_path: Path) -> None:
    """Cam beep at clip-local 2.0s, primary beep at 5.0s -> cam needs to
    sit later on the spine by 3s = 90 frames."""
    primary = _make_video(tmp_path, "a.mp4")
    secondary = _make_video(tmp_path, "cam.mp4")
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
            secondaries=(
                SecondaryClip(
                    video_path=secondary,
                    video=_meta_30fps(),
                    beep_offset_seconds=2.0,
                    label="Cam",
                ),
            ),
        )
    ]
    root = _render(stages, tmp_path)
    tracks = root.findall(".//sequence/media/video/track")
    assert len(tracks) == 2  # V1 + V2
    cam_clip = tracks[1].find("clipitem")
    assert cam_clip is not None
    # head_pad=5 >= beep=5 -> head_trim=0, so visible head matches beep_offset=5.
    # delta = (5 - 0) - 2 = 3s = 90 frames -> cam start = 90, in = 0.
    assert cam_clip.findtext("start") == "90"
    assert cam_clip.findtext("in") == "0"


def test_overlay_lands_on_topmost_track(tmp_path: Path) -> None:
    """When secondaries and overlay are present, overlay is on the track
    above all cams -- highest visual layer."""
    overlay = _make_video(tmp_path, "overlay.mov")
    primary = _make_video(tmp_path, "a.mp4")
    secondary = _make_video(tmp_path, "cam.mp4")
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
            secondaries=(
                SecondaryClip(
                    video_path=secondary,
                    video=_meta_30fps(),
                    beep_offset_seconds=5.0,
                    label="Cam",
                ),
            ),
            overlay_path=overlay,
            overlay_video=_meta_30fps(),
        )
    ]
    root = _render(stages, tmp_path)
    tracks = root.findall(".//sequence/media/video/track")
    assert len(tracks) == 3  # V1 primary + V2 cam + V3 overlay
    top = tracks[-1]
    overlay_clip = top.find("clipitem")
    assert overlay_clip is not None
    assert overlay_clip.findtext("name") == "Splitsmith overlay"


def test_two_secondaries_use_v2_and_v3(tmp_path: Path) -> None:
    primary = _make_video(tmp_path, "a.mp4")
    cam_a = _make_video(tmp_path, "cam_a.mp4")
    cam_b = _make_video(tmp_path, "cam_b.mp4")
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
            secondaries=(
                SecondaryClip(
                    video_path=cam_a,
                    video=_meta_30fps(),
                    beep_offset_seconds=5.0,
                    label="Cam A",
                ),
                SecondaryClip(
                    video_path=cam_b,
                    video=_meta_30fps(),
                    beep_offset_seconds=5.0,
                    label="Cam B",
                ),
            ),
        )
    ]
    root = _render(stages, tmp_path)
    tracks = root.findall(".//sequence/media/video/track")
    assert len(tracks) == 3  # V1, V2 (Cam A), V3 (Cam B)
    assert tracks[1].find("clipitem").findtext("name") == "Cam A"  # type: ignore[union-attr]
    assert tracks[2].find("clipitem").findtext("name") == "Cam B"  # type: ignore[union-attr]


# --- file references ------------------------------------------------------


def test_repeated_asset_use_emits_id_only_reference(tmp_path: Path) -> None:
    """The same primary used across two stages should declare a ``<file>``
    body once and reference it by id thereafter."""
    primary = _make_video(tmp_path, "shared.mp4")
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        ),
        StageComposition(
            stage_name="B",
            video_path=primary,
            video=_meta_30fps(),
            shots=[_shot(1, 0.8, 0.8)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
        ),
    ]
    root = _render(stages, tmp_path)
    file_decls = [f for f in root.findall(".//file") if f.find("pathurl") is not None]
    file_refs = [f for f in root.findall(".//file") if f.find("pathurl") is None]
    assert len(file_decls) == 1
    assert len(file_refs) == 1
    assert file_refs[0].attrib["id"] == file_decls[0].attrib["id"]


# --- markers --------------------------------------------------------------


def test_markers_land_at_clip_local_frames(tmp_path: Path) -> None:
    """Shot 1 at beep+1.0s on a 5s-beep stage -> 6.0s clip-local = 180
    frames at 30fps."""
    stages = [_stage(tmp_path=tmp_path, name="A", primary_name="a.mp4")]
    root = _render(stages, tmp_path)
    markers = root.findall(".//track[1]/clipitem/marker")
    assert len(markers) == 2
    assert markers[0].findtext("in") == "180"
    assert markers[0].findtext("out") == "181"
    assert "Shot 1" in markers[0].findtext("name")  # type: ignore[operator]


def test_markers_outside_visible_window_are_dropped(tmp_path: Path) -> None:
    """Tight tail pad (0.0s) collapses tail; a shot at +5.0s lands beyond
    the visible window and must be skipped."""
    primary = _make_video(tmp_path, "a.mp4")
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary,
            video=_meta_30fps(),
            shots=[
                _shot(1, 1.0, 1.0),  # 6s clip-local, in window
                _shot(2, 14.5, 13.5),  # 19.5s clip-local; pad=0 -> visible end ~6s
            ],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=0.0,
        )
    ]
    root = _render(stages, tmp_path)
    markers = root.findall(".//clipitem/marker")
    assert len(markers) == 1


# --- PiP via Basic Motion -------------------------------------------------


def test_pip_emits_basic_motion_filter(tmp_path: Path) -> None:
    primary = _make_video(tmp_path, "a.mp4")
    secondary = _make_video(tmp_path, "cam.mp4")
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
            secondaries=(
                SecondaryClip(
                    video_path=secondary,
                    video=_meta_30fps(),
                    beep_offset_seconds=5.0,
                    label="Cam",
                    pip=PipPlacement(corner="top-right"),
                ),
            ),
        )
    ]
    root = _render(stages, tmp_path)
    cam_clip = root.findall(".//sequence/media/video/track")[1].find("clipitem")
    assert cam_clip is not None
    effect = cam_clip.find("filter/effect")
    assert effect is not None
    assert effect.findtext("effectid") == "basic"
    params = {p.findtext("parameterid"): p for p in effect.findall("parameter")}
    assert params["scale"].findtext("value") == "25"  # 0.25 * 100
    # 1080p sequence, scale=0.25, margin=2.0%:
    # IR position = (681.6, 383.4) (FCPXML coords, +Y up)
    # FCP7 horiz = 681.6 / 960 = 0.71 (rounded)
    # FCP7 vert = -383.4 / 540 = -0.71 (FCP7 +Y down -> sign flips)
    horiz = float(params["center"].find("value/horiz").text)  # type: ignore[arg-type, union-attr]
    vert = float(params["center"].find("value/vert").text)  # type: ignore[arg-type, union-attr]
    assert horiz == pytest.approx(0.71, abs=0.01)
    assert vert == pytest.approx(-0.71, abs=0.01)


def test_pip_y_axis_flips_for_bottom_corners(tmp_path: Path) -> None:
    """Sanity: a bottom-* corner on the IR (+Y up means py < 0) maps to
    FCP7 vert > 0 (since FCP7 +Y is down)."""
    primary = _make_video(tmp_path, "a.mp4")
    secondary = _make_video(tmp_path, "cam.mp4")
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
            secondaries=(
                SecondaryClip(
                    video_path=secondary,
                    video=_meta_30fps(),
                    beep_offset_seconds=5.0,
                    label="Cam",
                    pip=PipPlacement(corner="bottom-right"),
                ),
            ),
        )
    ]
    root = _render(stages, tmp_path)
    vert = root.find(
        ".//track[2]/clipitem/filter/effect/parameter[parameterid='center']/value/vert"
    )
    assert vert is not None
    assert float(vert.text) > 0  # type: ignore[arg-type]


def test_no_pip_emits_no_filter(tmp_path: Path) -> None:
    primary = _make_video(tmp_path, "a.mp4")
    secondary = _make_video(tmp_path, "cam.mp4")
    stages = [
        StageComposition(
            stage_name="A",
            video_path=primary,
            video=_meta_30fps(),
            shots=[_shot(1, 1.0, 1.0)],
            beep_offset_seconds=5.0,
            head_pad_seconds=5.0,
            tail_pad_seconds=5.0,
            secondaries=(
                SecondaryClip(
                    video_path=secondary,
                    video=_meta_30fps(),
                    beep_offset_seconds=5.0,
                    label="Cam",
                ),
            ),
        )
    ]
    root = _render(stages, tmp_path)
    cam_clip = root.findall(".//sequence/media/video/track")[1].find("clipitem")
    assert cam_clip is not None
    assert cam_clip.find("filter") is None


# --- error paths ----------------------------------------------------------


def test_negative_effective_duration_raises(tmp_path: Path) -> None:
    """Same guard as the FCPXML emitter: trims that collapse the visible
    window to zero raise rather than emitting a malformed timeline."""
    primary = _make_video(tmp_path, "tiny.mp4")
    tiny_meta = VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=0.001,
        frame_rate_num=30,
        frame_rate_den=1,
    )
    stages = [
        StageComposition(
            stage_name="x",
            video_path=primary,
            video=tiny_meta,
            shots=[],
            beep_offset_seconds=0.0,
            head_pad_seconds=0.0,
            tail_pad_seconds=0.0,
        )
    ]
    comp = composition.from_stage_compositions(stages, project_name="m")
    with pytest.raises(ValueError, match="non-positive effective duration"):
        fcp7xml_render.render_fcp7xml(comp, output_path=tmp_path / "out.xml")
