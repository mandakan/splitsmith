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
import plistlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

from .config import OutputConfig, Shot, SplitColorThresholds, VideoMetadata


@dataclass(frozen=True)
class SecondaryClip:
    """One secondary cam attached to the primary as a connected clip (issue #54).

    Each secondary is its own lossless trim placed on V2/V3/... and slipped on
    the parent timeline so the cam's beep lands at the same second as the
    primary's. ``beep_offset_seconds`` is the clip-local time of the beep in
    this cam's trim -- typically equal to the project's ``pre_buffer_seconds``,
    but may be less if the cam's beep landed within the pre-buffer of the
    file (a short head wipes some of the pre-roll).
    """

    video_path: Path
    video: VideoMetadata
    beep_offset_seconds: float
    label: str = "Secondary cam"


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
    overlay_path: Path | None = None,
    overlay_video: VideoMetadata | None = None,
    secondaries: list[SecondaryClip] | None = None,
) -> None:
    """Write a minimal FCPXML 1.10 timeline for the trimmed video.

    ``beep_offset_seconds`` is the time of the beep within ``video_path`` --
    typically equal to ``trim_buffer_seconds`` since the trim was placed
    ``buffer`` seconds before the beep.

    Each entry in ``shots`` must have ``time_from_beep`` set; shot times are
    converted to clip-local time via ``beep_offset + time_from_beep`` and
    rationalized against the source frame duration.

    ``overlay_path``: optional pre-rendered alpha MOV (issue #45) to place
    on V2 as a connected clip. When the file exists the timeline gets a
    second asset + a lane=N asset-clip nested in the V1 clip; when it's
    None the FCPXML is unchanged. ``overlay_video`` is the probed metadata
    for the overlay -- when omitted, the trimmed video's metadata is used
    (the renderer mirrors the source frame-for-frame so this is correct).

    ``secondaries``: optional list of secondary cams (issue #54). Each is
    placed as a connected clip on lane=1, lane=2, ... below the overlay,
    with its head slipped so the cam's beep lines up frame-aligned with the
    primary's beep. Missing files are silently skipped (mirrors the overlay
    behaviour) so the same XML works whether all cams shipped or only the
    primary did.
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
    # Resource IDs after the primary asset are allocated in this order:
    # secondary cams first, then (optionally) a dedicated overlay format,
    # then the overlay asset last so the overlay always lives on the highest
    # lane (V2/V3/... below it) regardless of cam count.
    usable_secondaries = [s for s in (secondaries or []) if s.video_path.exists()]
    use_overlay = overlay_path is not None and overlay_path.exists()
    overlay_meta_for_format = overlay_video if overlay_video is not None else video
    overlay_uses_own_format = use_overlay and _overlay_format_differs(
        video, overlay_meta_for_format
    )
    next_id = 3 + len(usable_secondaries)
    overlay_format_id: str | None = None
    if overlay_uses_own_format:
        overlay_format_id = f"r{next_id}"
        next_id += 1
    overlay_asset_id = f"r{next_id}"

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

    # Secondary cam assets (issue #54). Each gets its own ``asset`` resource so
    # FCP can ingest the cam's video + audio independently of the primary.
    # IDs run r3..r(3+N-1); overlay (when present) takes the next ID after.
    secondary_asset_entries: list[tuple[SecondaryClip, str, str]] = []
    for idx, sec in enumerate(usable_secondaries):
        sec_id = f"r{3 + idx}"
        sec_frame_duration = Fraction(sec.video.frame_rate_den, sec.video.frame_rate_num)
        sec_duration_frames = int(round(sec.video.duration_seconds / float(sec_frame_duration)))
        # Mix-and-match cam frame rates would make the offset / duration math
        # parent-vs-child rational; v1 punts on that and uses the primary's
        # frame rate to express secondary durations. Cams shot on the same
        # match day are typically the same fps, and the trim is stream-copy
        # so the source rate is preserved -- if they differ, FCP will round
        # on import (acceptable for a connected cam) but the spec for
        # multi-rate timelines is out of scope here.
        sec_duration_in_parent_frames = int(
            round(sec.video.duration_seconds / float(frame_duration))
        )
        sec_duration_parent_str = _frame_aligned_str(sec_duration_in_parent_frames, fd_num, fd_den)
        sec_asset = ET.SubElement(
            resources,
            "asset",
            {
                "id": sec_id,
                "name": sec.video_path.stem,
                "start": "0s",
                "duration": _frame_aligned_str(
                    sec_duration_frames,
                    sec.video.frame_rate_den,
                    sec.video.frame_rate_num,
                ),
                "hasVideo": "1",
                "hasAudio": "1",
                "format": format_id,
                "videoSources": "1",
                "audioSources": "1",
                "audioChannels": "2",
            },
        )
        ET.SubElement(
            sec_asset,
            "media-rep",
            {"kind": "original-media", "src": sec.video_path.resolve().as_uri()},
        )
        secondary_asset_entries.append((sec, sec_id, sec_duration_parent_str))

    overlay_meta = overlay_video if overlay_video is not None else video
    overlay_duration_str: str | None = None
    if use_overlay:
        assert overlay_path is not None  # narrowed by use_overlay
        overlay_duration_frames = int(round(overlay_meta.duration_seconds / float(frame_duration)))
        overlay_duration_str = _frame_aligned_str(overlay_duration_frames, fd_num, fd_den)
        # Dedicated format when geometry / fps differs so FCP scales the
        # overlay over the timeline at default ``spatialConform="fit"``.
        # When dims & fps match the primary, reusing format_id keeps the
        # XML byte-comparable with the pre-#45 output.
        if overlay_uses_own_format:
            assert overlay_format_id is not None
            overlay_fd_num = overlay_meta.frame_rate_den
            overlay_fd_den = overlay_meta.frame_rate_num
            ET.SubElement(
                resources,
                "format",
                {
                    "id": overlay_format_id,
                    "name": _format_name(overlay_meta),
                    "frameDuration": _frame_aligned_str(1, overlay_fd_num, overlay_fd_den),
                    "width": str(overlay_meta.width),
                    "height": str(overlay_meta.height),
                    "colorSpace": "1-1-1 (Rec. 709)",
                },
            )
            asset_format_ref = overlay_format_id
        else:
            asset_format_ref = format_id
        overlay_asset = ET.SubElement(
            resources,
            "asset",
            {
                "id": overlay_asset_id,
                "name": overlay_path.stem,
                "start": "0s",
                "duration": overlay_duration_str,
                "hasVideo": "1",
                "hasAudio": "0",
                "format": asset_format_ref,
                "videoSources": "1",
            },
        )
        ET.SubElement(
            overlay_asset,
            "media-rep",
            {"kind": "original-media", "src": overlay_path.resolve().as_uri()},
        )

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": "splitsmith"})
    project = ET.SubElement(event, "project", {"name": project_name})
    # Sequence attributes: ``audioRate`` is a DTD-enumerated shorthand
    # ("32k", "44.1k", "48k", "88.2k", "96k", ...) -- NOT integer Hz. FCP
    # rejects "48000" with "DTD validation failed (Value '48000' for
    # attribute audioRate of sequence is not among the enumerated set)".
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": format_id,
            "duration": duration_str,
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
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

    # Secondary cam connected clips (issue #54). Lanes 1..N. Each cam's head
    # is slipped on the parent timeline so its beep aligns frame-for-frame
    # with the primary's beep. Two cases:
    #   - sb <= pb: place head later on the timeline by ``pb - sb`` frames,
    #     start the cam from frame 0.
    #   - sb >  pb: head sits at parent t=0; skip ``sb - pb`` frames of the
    #     cam so the beep still lines up.
    fd_seconds_for_align = float(frame_duration)
    for lane_idx, (sec, sec_id, sec_duration_parent_str) in enumerate(
        secondary_asset_entries, start=1
    ):
        delta_frames = round((beep_offset_seconds - sec.beep_offset_seconds) / fd_seconds_for_align)
        if delta_frames >= 0:
            sec_offset_str = _frame_aligned_str(delta_frames, fd_num, fd_den)
            sec_start_str = "0s"
        else:
            sec_offset_str = "0s"
            sec_start_str = _frame_aligned_str(-delta_frames, fd_num, fd_den)
        ET.SubElement(
            asset_clip,
            "asset-clip",
            {
                "ref": sec_id,
                "lane": str(lane_idx),
                "offset": sec_offset_str,
                "name": sec.label,
                "start": sec_start_str,
                "duration": sec_duration_parent_str,
                "format": format_id,
            },
        )

    if use_overlay and overlay_duration_str is not None:
        # Connected clip on the lane above all secondary cams (lane=N+1 when
        # N cams attach; lane=1 in the single-cam default). FCPXML stacks
        # higher lanes over lower ones, so this keeps the overlay's typography
        # on top of every cam regardless of count. ``offset="0s"`` aligns its
        # head to the primary's head; the renderer matches the trim duration
        # so we don't need a nested clip for trims.
        overlay_lane = len(secondary_asset_entries) + 1
        ET.SubElement(
            asset_clip,
            "asset-clip",
            {
                "ref": overlay_asset_id,
                "lane": str(overlay_lane),
                "offset": "0s",
                "name": "Splitsmith overlay",
                "start": "0s",
                "duration": overlay_duration_str,
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
    _tag_source_application(output_path)


@dataclass(frozen=True)
class StageComposition:
    """One stage's contribution to a stitched match-export FCPXML (issue #170).

    Each stage is a self-contained piece: trimmed primary + optional secondaries
    + optional overlay + per-stage shots and beep offset. ``head_pad_seconds`` /
    ``tail_pad_seconds`` are how much footage to keep before the beep / after the
    final shot; the composer trims the rest at FCPXML level (no re-encoding).
    Both pads are clamped to what's actually present in the trimmed clip:
    head_trim = max(0, beep_offset - head_pad), tail_trim = max(0, available -
    tail_pad), so the function is safe to call with pads larger than the trim
    actually contains (the result is "use everything that's there").
    """

    stage_name: str
    video_path: Path
    video: VideoMetadata
    shots: list[Shot]
    beep_offset_seconds: float
    head_pad_seconds: float
    tail_pad_seconds: float
    overlay_path: Path | None = None
    overlay_video: VideoMetadata | None = None
    secondaries: tuple[SecondaryClip, ...] = ()


def generate_match_fcpxml(
    *,
    stages: list[StageComposition],
    output_path: Path,
    project_name: str,
    config: OutputConfig,
) -> None:
    """Write a stitched FCPXML with N stages back-to-back on the spine.

    Each stage carries its own primary + secondaries + overlay + markers. The
    composer trims at FCPXML level by adjusting the primary's ``start`` and
    ``duration`` -- no ffmpeg invocation. Cumulative spine offsets keep the
    stages contiguous; secondary head-slip math is generalised to handle
    head-trimmed primaries (every cam's beep stays frame-aligned with the
    primary's beep regardless of trim).

    Frame-rate mixing across stages is out of scope for v1: all stages must
    share a frame rate (raises ValueError otherwise).
    """
    if not stages:
        raise ValueError("generate_match_fcpxml requires at least one stage")
    for stage in stages:
        if not stage.video_path.exists():
            raise FileNotFoundError(f"video not found: {stage.video_path}")

    base = stages[0].video
    for stage in stages[1:]:
        if (
            stage.video.frame_rate_num != base.frame_rate_num
            or stage.video.frame_rate_den != base.frame_rate_den
        ):
            raise ValueError(
                "mixed frame rates across stages are not supported "
                f"(stage 0: {base.frame_rate_num}/{base.frame_rate_den}, "
                f"stage with {stage.video_path.name}: "
                f"{stage.video.frame_rate_num}/{stage.video.frame_rate_den})"
            )

    frame_duration = Fraction(base.frame_rate_den, base.frame_rate_num)
    fd_num = base.frame_rate_den
    fd_den = base.frame_rate_num
    fd_seconds = float(frame_duration)
    frame_duration_str = _frame_aligned_str(1, fd_num, fd_den)

    fcpxml = ET.Element("fcpxml", {"version": config.fcpxml_version})
    resources = ET.SubElement(fcpxml, "resources")
    format_id = "r1"
    ET.SubElement(
        resources,
        "format",
        {
            "id": format_id,
            "name": _format_name(base),
            "frameDuration": frame_duration_str,
            "width": str(base.width),
            "height": str(base.height),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )

    # Per-stage resource + spine plan. Resource IDs are allocated globally in
    # declaration order: r2 = stage 0 primary, then its secondaries, then its
    # overlay, then stage 1's primary, etc. Connected-clip lanes reset per
    # stage (each stage's overlay sits above that stage's secondaries only).
    resource_counter = 1  # r1 already used by format

    @dataclass
    class _StagePlan:
        stage: StageComposition
        primary_id: str
        primary_duration_frames: int
        head_trim_frames: int
        effective_duration_frames: int
        # cam, asset_id, sec_dur_in_parent_frames
        usable_secondaries: list[tuple[SecondaryClip, str, int]]
        overlay_asset_id: str | None
        overlay_format_id: str | None  # None when overlay reuses the timeline format
        overlay_duration_frames: int  # in parent frame units; 0 when no overlay

    plans: list[_StagePlan] = []

    for stage in stages:
        stage_frame_duration = Fraction(stage.video.frame_rate_den, stage.video.frame_rate_num)
        primary_duration_frames = int(
            round(stage.video.duration_seconds / float(stage_frame_duration))
        )

        # Determine actual head and tail available in the trimmed file.
        # head_avail = beep_offset (the trim's pre-buffer; may be < trim_buffer
        # when the cam beep landed within the pre-buffer of the source).
        # tail_avail = source_duration - last_shot_clip_local. With no shots,
        # treat the beep as the last event (degenerate case for action cuts).
        head_avail = max(0.0, stage.beep_offset_seconds)
        if stage.shots:
            last_shot_local = stage.beep_offset_seconds + max(s.time_from_beep for s in stage.shots)
        else:
            last_shot_local = stage.beep_offset_seconds
        tail_avail = max(0.0, stage.video.duration_seconds - last_shot_local)

        head_trim_seconds = max(0.0, head_avail - stage.head_pad_seconds)
        tail_trim_seconds = max(0.0, tail_avail - stage.tail_pad_seconds)
        head_trim_frames = int(round(head_trim_seconds / fd_seconds))
        tail_trim_frames = int(round(tail_trim_seconds / fd_seconds))
        effective_duration_frames = primary_duration_frames - head_trim_frames - tail_trim_frames
        if effective_duration_frames <= 0:
            raise ValueError(
                f"stage {stage.stage_name!r} would have non-positive effective duration "
                f"after trim ({effective_duration_frames} frames); reduce head/tail pad "
                "or check the trimmed clip length"
            )

        resource_counter += 1
        primary_id = f"r{resource_counter}"

        usable_secondaries: list[tuple[SecondaryClip, str, int]] = []
        for sec in stage.secondaries:
            if not sec.video_path.exists():
                continue
            resource_counter += 1
            sec_id = f"r{resource_counter}"
            sec_dur_in_parent_frames = int(round(sec.video.duration_seconds / fd_seconds))
            usable_secondaries.append((sec, sec_id, sec_dur_in_parent_frames))

        overlay_asset_id: str | None = None
        overlay_format_id: str | None = None
        overlay_duration_frames = 0
        if stage.overlay_path is not None and stage.overlay_path.exists():
            overlay_meta = stage.overlay_video if stage.overlay_video is not None else stage.video
            if _overlay_format_differs(stage.video, overlay_meta):
                resource_counter += 1
                overlay_format_id = f"r{resource_counter}"
            resource_counter += 1
            overlay_asset_id = f"r{resource_counter}"
            overlay_duration_frames = int(round(overlay_meta.duration_seconds / fd_seconds))

        plans.append(
            _StagePlan(
                stage=stage,
                primary_id=primary_id,
                primary_duration_frames=primary_duration_frames,
                head_trim_frames=head_trim_frames,
                effective_duration_frames=effective_duration_frames,
                usable_secondaries=usable_secondaries,
                overlay_asset_id=overlay_asset_id,
                overlay_format_id=overlay_format_id,
                overlay_duration_frames=overlay_duration_frames,
            )
        )

    # Emit assets in the same order resource IDs were assigned so the XML
    # reads top-to-bottom in stage order.
    for plan in plans:
        primary_asset = ET.SubElement(
            resources,
            "asset",
            {
                "id": plan.primary_id,
                "name": plan.stage.video_path.stem,
                "start": "0s",
                "duration": _frame_aligned_str(plan.primary_duration_frames, fd_num, fd_den),
                "hasVideo": "1",
                "hasAudio": "1",
                "format": format_id,
                "videoSources": "1",
                "audioSources": "1",
                "audioChannels": "2",
            },
        )
        ET.SubElement(
            primary_asset,
            "media-rep",
            {"kind": "original-media", "src": plan.stage.video_path.resolve().as_uri()},
        )
        for sec, sec_id, _sec_dur_in_parent_frames in plan.usable_secondaries:
            sec_asset = ET.SubElement(
                resources,
                "asset",
                {
                    "id": sec_id,
                    "name": sec.video_path.stem,
                    "start": "0s",
                    "duration": _frame_aligned_str(
                        int(
                            round(
                                sec.video.duration_seconds
                                / float(
                                    Fraction(sec.video.frame_rate_den, sec.video.frame_rate_num)
                                )
                            )
                        ),
                        sec.video.frame_rate_den,
                        sec.video.frame_rate_num,
                    ),
                    "hasVideo": "1",
                    "hasAudio": "1",
                    "format": format_id,
                    "videoSources": "1",
                    "audioSources": "1",
                    "audioChannels": "2",
                },
            )
            ET.SubElement(
                sec_asset,
                "media-rep",
                {"kind": "original-media", "src": sec.video_path.resolve().as_uri()},
            )
        if plan.overlay_asset_id is not None and plan.stage.overlay_path is not None:
            if plan.overlay_format_id is not None:
                overlay_meta = (
                    plan.stage.overlay_video
                    if plan.stage.overlay_video is not None
                    else plan.stage.video
                )
                overlay_fd_num = overlay_meta.frame_rate_den
                overlay_fd_den = overlay_meta.frame_rate_num
                ET.SubElement(
                    resources,
                    "format",
                    {
                        "id": plan.overlay_format_id,
                        "name": _format_name(overlay_meta),
                        "frameDuration": _frame_aligned_str(1, overlay_fd_num, overlay_fd_den),
                        "width": str(overlay_meta.width),
                        "height": str(overlay_meta.height),
                        "colorSpace": "1-1-1 (Rec. 709)",
                    },
                )
                overlay_asset_format = plan.overlay_format_id
            else:
                overlay_asset_format = format_id
            overlay_asset = ET.SubElement(
                resources,
                "asset",
                {
                    "id": plan.overlay_asset_id,
                    "name": plan.stage.overlay_path.stem,
                    "start": "0s",
                    "duration": _frame_aligned_str(plan.overlay_duration_frames, fd_num, fd_den),
                    "hasVideo": "1",
                    "hasAudio": "0",
                    "format": overlay_asset_format,
                    "videoSources": "1",
                },
            )
            ET.SubElement(
                overlay_asset,
                "media-rep",
                {"kind": "original-media", "src": plan.stage.overlay_path.resolve().as_uri()},
            )

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": "splitsmith"})
    project = ET.SubElement(event, "project", {"name": project_name})
    total_duration_frames = sum(plan.effective_duration_frames for plan in plans)
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": format_id,
            "duration": _frame_aligned_str(total_duration_frames, fd_num, fd_den),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")

    cumulative_offset_frames = 0
    for plan in plans:
        stage = plan.stage
        head_trim_seconds = plan.head_trim_frames * fd_seconds
        eff_duration_str = _frame_aligned_str(plan.effective_duration_frames, fd_num, fd_den)
        primary_clip = ET.SubElement(
            spine,
            "asset-clip",
            {
                "ref": plan.primary_id,
                "offset": _frame_aligned_str(cumulative_offset_frames, fd_num, fd_den),
                "name": stage.stage_name,
                "start": _frame_aligned_str(plan.head_trim_frames, fd_num, fd_den),
                "duration": eff_duration_str,
                "format": format_id,
            },
        )

        # Secondary cam connected clips. Lane=1..N (per stage). The parent's
        # ``offset`` on a connected clip is in the parent's source-time
        # coordinates -- offset=0s would land at parent_source=0, which is
        # ``head_trim`` seconds *before* the parent's visible spine start.
        # So every connected-clip offset is biased by ``head_trim_frames``
        # to anchor at the parent's visible start. Beep alignment then
        # follows from delta = (beep_offset - head_trim) - sec_beep in
        # frames; a non-negative delta places the cam later in the parent's
        # local time, a negative delta skips into the cam's own media.
        for lane_idx, (sec, sec_id, sec_dur_in_parent_frames) in enumerate(
            plan.usable_secondaries, start=1
        ):
            delta_frames = round(
                ((stage.beep_offset_seconds - head_trim_seconds) - sec.beep_offset_seconds)
                / fd_seconds
            )
            if delta_frames >= 0:
                sec_offset_frames = plan.head_trim_frames + delta_frames
                sec_start_frames = 0
            else:
                sec_offset_frames = plan.head_trim_frames
                sec_start_frames = -delta_frames
            ET.SubElement(
                primary_clip,
                "asset-clip",
                {
                    "ref": sec_id,
                    "lane": str(lane_idx),
                    "offset": _frame_aligned_str(sec_offset_frames, fd_num, fd_den),
                    "name": sec.label,
                    "start": _frame_aligned_str(sec_start_frames, fd_num, fd_den),
                    "duration": _frame_aligned_str(sec_dur_in_parent_frames, fd_num, fd_den),
                    "format": format_id,
                },
            )

        if plan.overlay_asset_id is not None:
            overlay_lane = len(plan.usable_secondaries) + 1
            # Overlay was rendered to mirror the primary frame-for-frame, so
            # ``start`` skips into the overlay's own media by head_trim to
            # stay in sync. ``offset`` is also head_trim (parent-source-time
            # of the parent's visible start) so the overlay anchors at the
            # primary's visible start instead of head_trim seconds earlier.
            head_trim_str = _frame_aligned_str(plan.head_trim_frames, fd_num, fd_den)
            ET.SubElement(
                primary_clip,
                "asset-clip",
                {
                    "ref": plan.overlay_asset_id,
                    "lane": str(overlay_lane),
                    "offset": head_trim_str,
                    "name": "Splitsmith overlay",
                    "start": head_trim_str,
                    "duration": eff_duration_str,
                    "format": format_id,
                },
            )

        # Markers. Each shot's clip-local source-media time stays the marker
        # ``start``; FCP only renders markers within [primary.start,
        # primary.start + duration], so we drop shots outside that window
        # here for a clean XML.
        head_trim_seconds_for_window = head_trim_seconds
        eff_end_seconds = head_trim_seconds_for_window + plan.effective_duration_frames * fd_seconds
        for shot in stage.shots:
            clip_local_seconds = stage.beep_offset_seconds + shot.time_from_beep
            if not head_trim_seconds_for_window <= clip_local_seconds < eff_end_seconds:
                continue
            frames = round(clip_local_seconds / fd_seconds)
            ET.SubElement(
                primary_clip,
                "marker",
                {
                    "start": _frame_aligned_str(frames, fd_num, fd_den),
                    "duration": frame_duration_str,
                    "value": _marker_label(shot, config.split_color_thresholds),
                },
            )

        cumulative_offset_frames += plan.effective_duration_frames

    ET.indent(fcpxml, space="    ")
    tree_bytes = ET.tostring(fcpxml, encoding="utf-8", xml_declaration=True)
    decl_end = tree_bytes.index(b"?>") + 2
    output_path.write_bytes(
        tree_bytes[:decl_end] + b"\n<!DOCTYPE fcpxml>\n" + tree_bytes[decl_end + 1 :]
    )
    _tag_source_application(output_path)


def _tag_source_application(path: Path) -> None:
    """Tag the FCPXML file with ``kMDItemCreator`` so FCP's import dialog
    shows ``Splitsmith`` instead of ``application "(null)"`` (issue #41).

    FCPXML has no in-document attribute for source app -- FCP reads the
    name from the file's macOS extended attribute, the same channel
    Resolve / Premiere use to identify themselves in the same dialog.
    Best-effort: silently skip on non-macOS platforms or if the ``xattr``
    binary isn't available (CI / Linux).

    ``kMDItemCreator`` is a Spotlight-indexed key whose value must be a
    binary plist; ``plistlib`` builds it for us so we don't hand-roll
    bplist00 byte layouts.
    """
    payload = plistlib.dumps("Splitsmith", fmt=plistlib.FMT_BINARY)
    try:
        subprocess.run(
            [
                "xattr",
                "-wx",
                "com.apple.metadata:kMDItemCreator",
                payload.hex(),
                str(path),
            ],
            check=False,
            capture_output=True,
        )
    except (FileNotFoundError, OSError):
        # No xattr binary (Linux/CI) or filesystem doesn't support
        # extended attributes -- the FCPXML file is still valid; FCP
        # just falls back to "(null)" in the dialog title.
        return


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


def _overlay_format_differs(primary: VideoMetadata, overlay: VideoMetadata) -> bool:
    """``True`` when the overlay needs its own ``<format>`` element.

    Reusing the primary's format ID is fine when dimensions and frame rate
    match; when either differs (smaller overlay for size savings, capped
    fps, etc.), FCP needs the overlay's true geometry to scale it across
    the timeline instead of pinning it at native size. ``spatialConform``
    defaults to ``"fit"`` which preserves aspect ratio -- exactly what we
    want when downscaling a same-aspect overlay.
    """
    return (
        primary.width != overlay.width
        or primary.height != overlay.height
        or primary.frame_rate_num != overlay.frame_rate_num
        or primary.frame_rate_den != overlay.frame_rate_den
    )


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
