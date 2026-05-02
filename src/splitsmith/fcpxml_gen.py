"""Generate Final Cut Pro XML timelines (FCPXML 1.10).

v1 scope (per FIRST_PROMPT.md, fcpxml is intentionally minimal):
- One asset (the trimmed video) + one sequence at the source frame rate.
- V1 = ref-clip of the trimmed video.
- One ``<marker>`` per shot, frame-aligned, with a descriptive name that
  embeds the split, the colour band (per ``SplitColorThresholds``), and a
  ``draw``/``transition`` tag where applicable.

V2 (running timer) and V3 (per-shot colour-coded title clips) are NOT generated
in v1 -- both require Motion-template UIDs that drift across FCP releases. The
marker-name approach gets you keyboard navigation (M / Shift+M) and visible
split labels in the timeline without any template fragility.

Time arithmetic: FCPXML expresses every time as a rational (``N/Ds``) and
requires every time to be a multiple of ``frame_duration``. We rationalize the
shot times against the source frame duration before serializing.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

from .config import OutputConfig, Shot, SplitColorThresholds, VideoMetadata

Runner = Callable[..., subprocess.CompletedProcess]


class FFprobeError(RuntimeError):
    """ffprobe exited non-zero, was missing, or returned unparseable output."""


def probe_video(
    path: Path,
    *,
    ffprobe_binary: str = "ffprobe",
    runner: Runner = subprocess.run,
) -> VideoMetadata:
    """Read width / height / duration / frame rate from ``path`` via ffprobe."""
    if not path.exists():
        raise FileNotFoundError(f"video not found: {path}")
    cmd = [
        ffprobe_binary,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = runner(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FFprobeError(f"ffprobe binary not found: {ffprobe_binary}") from exc
    except subprocess.CalledProcessError as exc:
        raise FFprobeError(
            f"ffprobe failed (exit {exc.returncode}): {exc.stderr or exc.stdout!r}"
        ) from exc

    try:
        data = json.loads(proc.stdout)
        stream = data["streams"][0]
        width = int(stream["width"])
        height = int(stream["height"])
        rfr = stream["r_frame_rate"]
        duration = float(data["format"]["duration"])
    except (json.JSONDecodeError, KeyError, IndexError, ValueError) as exc:
        raise FFprobeError(f"unparseable ffprobe output: {proc.stdout!r}") from exc

    num_str, _, den_str = rfr.partition("/")
    num = int(num_str)
    den = int(den_str) if den_str else 1
    return VideoMetadata(
        width=width,
        height=height,
        duration_seconds=duration,
        frame_rate_num=num,
        frame_rate_den=den,
    )


def split_color_band(shot_index: int, split: float, thresholds: SplitColorThresholds) -> str:
    """Return one of GREEN / YELLOW / RED / BLUE for a shot's split band."""
    if shot_index == 1 or split > thresholds.transition_min:
        return "BLUE"  # draw or post-transition shot -- not a pure split
    if split <= thresholds.green_max:
        return "GREEN"
    if split <= thresholds.yellow_max:
        return "YELLOW"
    return "RED"


def generate_fcpxml(
    *,
    video_path: Path,
    video: VideoMetadata,
    shots: list[Shot],
    beep_offset_seconds: float,
    output_path: Path,
    project_name: str,
    config: OutputConfig,
) -> None:
    """Write a minimal FCPXML 1.10 timeline for the trimmed video.

    ``beep_offset_seconds`` is the time of the beep within ``video_path`` --
    typically equal to ``trim_buffer_seconds`` since the trim was placed
    ``buffer`` seconds before the beep.

    Each entry in ``shots`` must have ``time_from_beep`` set; shot times are
    converted to clip-local time via ``beep_offset + time_from_beep`` and
    rationalized against the source frame duration.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")

    frame_duration = Fraction(video.frame_rate_den, video.frame_rate_num)
    fd_num = video.frame_rate_den
    fd_den = video.frame_rate_num
    duration_frames = int(round(video.duration_seconds / float(frame_duration)))
    duration_str = _frame_aligned_str(duration_frames, fd_num, fd_den)
    frame_duration_str = _frame_aligned_str(1, fd_num, fd_den)

    asset_id = "r2"
    format_id = "r1"

    fcpxml = ET.Element("fcpxml", {"version": config.fcpxml_version})

    # FCPXML format attributes (per DTD 1.10):
    #   - id / name / frameDuration / width / height: required for sequence
    #     formats so FCP can map to a known preset (FFVideoFormat<height>p<fps>).
    #   - colorSpace: required when the format is referenced by a <sequence>;
    #     leaving it out triggers FCP's "Encountered an unexpected value
    #     (format=...)" warning at import (issue #41). "1-1-1 (Rec. 709)" is
    #     the default for SDR Rec. 709 footage, which matches the head-mounted
    #     camera output we target.
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(
        resources,
        "format",
        {
            "id": format_id,
            "name": _format_name(video),
            "frameDuration": frame_duration_str,
            "width": str(video.width),
            "height": str(video.height),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )
    asset = ET.SubElement(
        resources,
        "asset",
        {
            "id": asset_id,
            "name": video_path.stem,
            "start": "0s",
            "duration": duration_str,
            "hasVideo": "1",
            "hasAudio": "1",
            "format": format_id,
            "videoSources": "1",
            "audioSources": "1",
            "audioChannels": "2",
        },
    )
    ET.SubElement(
        asset,
        "media-rep",
        {"kind": "original-media", "src": video_path.resolve().as_uri()},
    )

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": "splitsmith"})
    project = ET.SubElement(event, "project", {"name": project_name})
    # Sequence attributes: ``audioRate`` must be an integer Hz value per the
    # FCPXML 1.10 spec ("48000", not the "48k" shorthand FCP sometimes accepts
    # silently).
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": format_id,
            "duration": duration_str,
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48000",
        },
    )
    spine = ET.SubElement(sequence, "spine")
    asset_clip = ET.SubElement(
        spine,
        "asset-clip",
        {
            "ref": asset_id,
            "offset": "0s",
            "name": project_name,
            "start": "0s",
            "duration": duration_str,
            "format": format_id,
        },
    )

    fd_seconds = float(frame_duration)
    for shot in shots:
        clip_local_seconds = beep_offset_seconds + shot.time_from_beep
        if not 0.0 <= clip_local_seconds < video.duration_seconds:
            continue
        frames = round(clip_local_seconds / fd_seconds)
        ET.SubElement(
            asset_clip,
            "marker",
            {
                "start": _frame_aligned_str(frames, fd_num, fd_den),
                "duration": frame_duration_str,
                "value": _marker_label(shot, config.split_color_thresholds),
            },
        )

    ET.indent(fcpxml, space="    ")
    tree_bytes = ET.tostring(fcpxml, encoding="utf-8", xml_declaration=True)
    # Inject the FCPXML DOCTYPE (ElementTree does not emit it).
    decl_end = tree_bytes.index(b"?>") + 2
    output_path.write_bytes(
        tree_bytes[:decl_end] + b"\n<!DOCTYPE fcpxml>\n" + tree_bytes[decl_end + 1 :]
    )


def _marker_label(shot: Shot, thresholds: SplitColorThresholds) -> str:
    band = split_color_band(shot.shot_number, shot.split, thresholds)
    if shot.shot_number == 1:
        kind = "draw"
    elif shot.split > thresholds.transition_min:
        kind = "transition"
    else:
        kind = "split"
    return f"Shot {shot.shot_number}: {shot.split:.3f}s [{band}] ({kind})"


def _frame_aligned_str(frames: int, fd_num: int, fd_den: int) -> str:
    """Format ``frames * (fd_num/fd_den)`` as an FCPXML rational time string,
    keeping the frame-duration denominator intact (FCP convention -- do not let
    Python's Fraction reduce ``196/30`` to ``98/15``)."""
    if frames == 0:
        return "0s"
    num = frames * fd_num
    if fd_den == 1:
        return f"{num}s"
    return f"{num}/{fd_den}s"


def _format_name(video: VideoMetadata) -> str:
    """Best-effort FCP-style format name. Falls back to a generic label."""
    fps = video.frame_rate_num / video.frame_rate_den
    if 720 <= video.height < 1080:
        prefix = "FFVideoFormat720p"
    elif 1080 <= video.height < 2160:
        prefix = "FFVideoFormat1080p"
    elif video.height >= 2160:
        prefix = "FFVideoFormat2160p"
    else:
        prefix = "FFVideoFormatCustom"
    return f"{prefix}{int(round(fps * 100)) / 100:g}".replace(".", "")
