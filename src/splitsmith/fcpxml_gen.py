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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET

from .config import OutputConfig, Shot, SplitColorThresholds, VideoMetadata

PipCorner = Literal["top-right", "top-left", "bottom-right", "bottom-left"]

# Corner cycle order when PiP is requested without an explicit per-cam corner.
# Top-right first because that's where the user typically wants the headcam-
# vs-handheld layout to land for an IPSC stage view.
_PIP_CORNER_CYCLE: tuple[PipCorner, ...] = (
    "top-right",
    "top-left",
    "bottom-right",
    "bottom-left",
)


@dataclass(frozen=True)
class PipPlacement:
    """Picture-in-picture placement for a secondary cam (#193).

    ``scale`` is a uniform multiplier applied to the cam's native size
    (assumes the cam's resolution roughly matches the sequence -- if not,
    the user can nudge the resulting transform in FCP). ``margin_pct`` is
    the inset from the sequence edge expressed as a percentage of the
    sequence width / height (so it scales sensibly across resolutions).
    """

    corner: PipCorner = "top-right"
    scale: float = 0.25
    margin_pct: float = 2.0

    def resolve(
        self,
        *,
        sequence_width: int,
        sequence_height: int,
    ) -> tuple[float, tuple[float, float]]:
        """Resolve to ``(scale, (x, y))`` in sequence-centre pixel space.

        Position is in pixels relative to the sequence centre with +Y up
        -- the same coordinate system FCPXML's ``<adjust-transform>``
        uses. ``composition.Transform`` (#194) and the FCPXML emitter
        both consume this resolved form so the corner / margin_pct
        abstraction stays in one place.
        """
        half_w = sequence_width / 2.0
        half_h = sequence_height / 2.0
        clip_half_w = half_w * self.scale
        clip_half_h = half_h * self.scale
        margin_x = sequence_width * (self.margin_pct / 100.0)
        margin_y = sequence_height * (self.margin_pct / 100.0)
        if self.corner in ("top-right", "bottom-right"):
            x = half_w - clip_half_w - margin_x
        else:
            x = -(half_w - clip_half_w - margin_x)
        if self.corner in ("top-right", "top-left"):
            y = half_h - clip_half_h - margin_y
        else:
            y = -(half_h - clip_half_h - margin_y)
        return self.scale, (x, y)


@dataclass(frozen=True)
class SecondaryClip:
    """One secondary cam attached to the primary as a connected clip (issue #54).

    Each secondary is its own lossless trim placed on V2/V3/... and slipped on
    the parent timeline so the cam's beep lands at the same second as the
    primary's. ``beep_offset_seconds`` is the clip-local time of the beep in
    this cam's trim -- typically equal to the project's ``pre_buffer_seconds``,
    but may be less if the cam's beep landed within the pre-buffer of the
    file (a short head wipes some of the pre-roll).

    ``pip`` (issue #193): when set, the renderer adds an ``<adjust-transform>``
    that scales and positions the cam as a corner inset over the primary.
    ``None`` keeps today's full-frame stacked layout.
    """

    video_path: Path
    video: VideoMetadata
    beep_offset_seconds: float
    label: str = "Secondary cam"
    pip: PipPlacement | None = None


def apply_pip_corner_cycle(
    secondaries: Iterable[SecondaryClip],
    *,
    default: PipPlacement | None = None,
) -> tuple[SecondaryClip, ...]:
    """Return ``secondaries`` with auto-assigned PiP corners (issue #193).

    Cams that already carry an explicit ``pip`` keep theirs unchanged. Cams
    without one get ``default`` (or are left alone if ``default`` is None),
    with corners rotated through ``_PIP_CORNER_CYCLE`` in input order so a
    multi-cam stage lands one cam per visible corner. ``scale`` /
    ``margin_pct`` are inherited from ``default``.
    """
    out: list[SecondaryClip] = []
    cycle_idx = 0
    for sec in secondaries:
        if sec.pip is not None or default is None:
            out.append(sec)
            continue
        corner = _PIP_CORNER_CYCLE[cycle_idx % len(_PIP_CORNER_CYCLE)]
        cycle_idx += 1
        out.append(replace(sec, pip=replace(default, corner=corner)))
    return tuple(out)


def _pip_transform_attrs(
    pip: PipPlacement,
    *,
    sequence_width: int,
    sequence_height: int,
) -> dict[str, str]:
    """Format ``<adjust-transform>`` attributes for ``pip`` in this sequence."""
    scale, (x, y) = pip.resolve(sequence_width=sequence_width, sequence_height=sequence_height)
    return {
        "scale": f"{scale:g} {scale:g}",
        "position": f"{x:g} {y:g}",
    }


def _emit_title_clip(
    parent: ET.Element,
    *,
    ref: str,
    offset_str: str,
    start_str: str,
    duration_str: str,
    title: StageTitle,
    text_style_id: str,
    lane: int | None = None,
) -> ET.Element:
    """Emit a ``<title>`` element with text + style children (issue #196).

    Per FCPXML 1.10 the order inside ``<title>`` is ``param*, text*,
    text-style-def*``; we emit a single ``<text>`` whose child
    ``<text-style ref=...>`` references a sibling ``<text-style-def>``
    that carries the actual font / size / colour. ``text_style_id``
    must be unique across the document.
    """
    attrs = {
        "ref": ref,
        "offset": offset_str,
        "start": start_str,
        "duration": duration_str,
        "name": title.text,
    }
    if lane is not None:
        attrs["lane"] = str(lane)
    title_el = ET.SubElement(parent, "title", attrs)
    text_el = ET.SubElement(title_el, "text")
    style_use = ET.SubElement(text_el, "text-style", {"ref": text_style_id})
    style_use.text = title.text
    style_def = ET.SubElement(title_el, "text-style-def", {"id": text_style_id})
    ET.SubElement(
        style_def,
        "text-style",
        {
            "font": title.font,
            "fontSize": str(title.font_size),
            "fontColor": title.color,
            "alignment": "center",
        },
    )
    return title_el


def _attach_pip_transform(
    asset_clip: ET.Element,
    pip: PipPlacement | None,
    *,
    sequence_width: int,
    sequence_height: int,
) -> None:
    """Insert ``<adjust-transform>`` on ``asset_clip`` when ``pip`` is set.

    The transform must precede any ``<marker>`` / nested clip children per
    FCPXML element ordering. Secondary connected clips are leaves today, so
    appending is fine; the explicit ``insert(0, ...)`` keeps the contract
    correct if a future change adds children to a secondary clip.
    """
    if pip is None:
        return
    attrs = _pip_transform_attrs(
        pip, sequence_width=sequence_width, sequence_height=sequence_height
    )
    transform = ET.Element("adjust-transform", attrs)
    asset_clip.insert(0, transform)


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
        sec_clip = ET.SubElement(
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
        _attach_pip_transform(
            sec_clip,
            sec.pip,
            sequence_width=video.width,
            sequence_height=video.height,
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


TransitionKind = Literal["cross-dissolve", "dip-to-color"]

# FCP's built-in transitions live under .motn templates with stable
# names; emitting an ``<effect>`` resource that points at one of these
# uids lets FCP resolve the transition without splitsmith bundling any
# Motion files. Both have shipped unchanged for years -- if Apple ever
# renames either, the FCPXML import will surface a missing-effect
# warning and the user re-picks the transition manually.
_TRANSITION_EFFECT_UIDS: dict[TransitionKind, str] = {
    "cross-dissolve": (
        ".../Transitions.localized/Dissolves.localized/"
        "Cross Dissolve.localized/Cross Dissolve.motn"
    ),
    "dip-to-color": (
        ".../Transitions.localized/Dissolves.localized/"
        "Dip to Color Dissolve.localized/Dip to Color Dissolve.motn"
    ),
}

_TRANSITION_NAMES: dict[TransitionKind, str] = {
    "cross-dissolve": "Cross Dissolve",
    "dip-to-color": "Dip to Color Dissolve",
}


TitleStyle = Literal["slate", "lower-third"]

# FCP's "Basic Title" generator. Stable across FCP versions and the
# safest cross-installation pick; templates that require non-default
# Motion library availability are intentionally avoided.
_BASIC_TITLE_EFFECT_UID = (
    ".../Generators.localized/Titles.localized/" "Basic Title.localized/Basic Title.motn"
)


@dataclass(frozen=True)
class StageTitle:
    """A title card associated with a stage (issue #196).

    ``slate`` -- the title sits on the spine BEFORE the stage's
    primary, extending the timeline by ``duration_seconds``. Useful
    for "Stage 3 -- Skipper" cards that the user wants to read before
    the action starts.

    ``lower-third`` -- the title is a connected clip overlaid above
    the primary, anchored to its visible head and showing for
    ``duration_seconds``. Useful for an unobtrusive label that doesn't
    interrupt the timeline.

    ``font_size`` is in FCPXML points (1080p sequence default scale);
    larger sequences may want a larger value but FCP will scale a
    Basic Title across the timeline format -- the default works for
    most home / matchcam exports.
    """

    stage_index: int
    text: str
    style: TitleStyle = "slate"
    duration_seconds: float = 1.5
    font_size: int = 144
    font: str = "Helvetica"
    color: str = "1 1 1 1"  # opaque white


@dataclass(frozen=True)
class StageTransition:
    """A transition between two consecutive stages on the spine (issue #195).

    ``after_stage_index`` is the index of the stage the transition
    sits *after* (so a transition with ``after_stage_index=0``
    crossfades from ``stages[0]`` into ``stages[1]``). Each stage may
    have at most one transition following it; multiple ``StageTransition``s
    targeting the same index raises.

    ``duration_seconds`` is the transition's total length; FCPXML
    centres the transition on the cut point so each adjacent stage's
    visible window must contain at least ``duration_seconds / 2`` of
    material -- the exporter validates this and raises a clear error
    otherwise.

    ``color`` only applies to ``"dip-to-color"`` and accepts any FCP
    colour string (e.g. ``"0 0 0 1"`` for opaque black). When omitted
    FCP uses its default (black) -- explicit colour is a follow-up
    when templating lands.
    """

    after_stage_index: int
    kind: TransitionKind = "cross-dissolve"
    duration_seconds: float = 0.5
    color: str | None = None


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
    transitions: list[StageTransition] | None = None,
    titles: list[StageTitle] | None = None,
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

    ``transitions`` (issue #195): optional list of ``StageTransition``s
    that crossfade between consecutive stages. Each transition is
    centred on its stage boundary (offset = end_of_prev - duration/2,
    duration = transition.duration); each adjacent stage must have at
    least ``duration / 2`` of effective material to overlap or the
    exporter raises ``ValueError``. ``None`` keeps today's hard-cut
    layout.

    ``titles`` (issue #196): optional list of ``StageTitle``s that
    add a ``slate`` (pre-stage card on the spine) or ``lower-third``
    (connected text clip overlaid on the primary) per stage. Slates
    extend the spine; lower-thirds anchor to the primary's visible
    head. Slates combined with transitions raise ``ValueError`` --
    a slate between two transition-joined primaries has no clean
    visual semantic.
    """
    if not stages:
        raise ValueError("generate_match_fcpxml requires at least one stage")
    for stage in stages:
        if not stage.video_path.exists():
            raise FileNotFoundError(f"video not found: {stage.video_path}")
    transitions = list(transitions or ())
    titles = list(titles or ())
    seen_indices: set[int] = set()
    for t in transitions:
        if t.after_stage_index in seen_indices:
            raise ValueError(f"duplicate transition after stage index {t.after_stage_index}")
        if not 0 <= t.after_stage_index < len(stages) - 1:
            raise ValueError(
                f"transition.after_stage_index={t.after_stage_index} is out of "
                f"range for {len(stages)} stages (must be in 0..{len(stages) - 2})"
            )
        if t.duration_seconds <= 0:
            raise ValueError(
                f"transition after stage {t.after_stage_index} has non-positive "
                f"duration ({t.duration_seconds:g}s)"
            )
        seen_indices.add(t.after_stage_index)
    seen_title_indices: set[int] = set()
    for ti in titles:
        if ti.stage_index in seen_title_indices:
            raise ValueError(f"duplicate title for stage index {ti.stage_index}")
        if not 0 <= ti.stage_index < len(stages):
            raise ValueError(
                f"title.stage_index={ti.stage_index} is out of range for "
                f"{len(stages)} stages (must be in 0..{len(stages) - 1})"
            )
        if ti.duration_seconds <= 0:
            raise ValueError(
                f"title for stage {ti.stage_index} has non-positive "
                f"duration ({ti.duration_seconds:g}s)"
            )
        seen_title_indices.add(ti.stage_index)
    has_slate = any(ti.style == "slate" for ti in titles)
    if has_slate and transitions:
        raise ValueError(
            "slate titles and transitions cannot be combined -- a slate "
            "between two transition-joined primaries has no clean visual "
            "semantic. Use lower-third titles, or remove transitions."
        )

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

    # Transition effect resources (issue #195). One ``<effect>`` per
    # unique transition kind used; transitions on the spine reference
    # these via ``<filter-video ref="...">``. Validate the per-stage
    # half-duration constraint before emitting so a bad combination
    # surfaces as a clear error rather than as broken FCPXML.
    transition_effect_ids: dict[TransitionKind, str] = {}
    for trans in transitions:
        trans_frames = round(trans.duration_seconds / fd_seconds)
        half_frames = trans_frames // 2
        prev_plan = plans[trans.after_stage_index]
        next_plan = plans[trans.after_stage_index + 1]
        if half_frames > prev_plan.effective_duration_frames:
            raise ValueError(
                f"transition after stage {prev_plan.stage.stage_name!r} "
                f"({trans.duration_seconds:g}s) exceeds the available material "
                f"({prev_plan.effective_duration_frames * fd_seconds:.3f}s); "
                "increase the stage's tail pad or shorten the transition"
            )
        if half_frames > next_plan.effective_duration_frames:
            raise ValueError(
                f"transition before stage {next_plan.stage.stage_name!r} "
                f"({trans.duration_seconds:g}s) exceeds the available material "
                f"({next_plan.effective_duration_frames * fd_seconds:.3f}s); "
                "increase the stage's head pad or shorten the transition"
            )
        if trans.kind not in transition_effect_ids:
            resource_counter += 1
            effect_id = f"r{resource_counter}"
            ET.SubElement(
                resources,
                "effect",
                {
                    "id": effect_id,
                    "name": _TRANSITION_NAMES[trans.kind],
                    "uid": _TRANSITION_EFFECT_UIDS[trans.kind],
                },
            )
            transition_effect_ids[trans.kind] = effect_id

    # Title effect resource (issue #196). One ``<effect>`` referencing
    # FCP's Basic Title generator suffices for both slate and lower-
    # third styles -- they're just different placements of the same
    # generator, distinguished by where the ``<title>`` element lands
    # (spine vs connected) and by its lane.
    title_effect_id: str | None = None
    if titles:
        resource_counter += 1
        title_effect_id = f"r{resource_counter}"
        ET.SubElement(
            resources,
            "effect",
            {
                "id": title_effect_id,
                "name": "Basic Title",
                "uid": _BASIC_TITLE_EFFECT_UID,
            },
        )

    titles_by_index: dict[int, StageTitle] = {ti.stage_index: ti for ti in titles}
    slate_frames_by_index: dict[int, int] = {
        idx: round(ti.duration_seconds / fd_seconds)
        for idx, ti in titles_by_index.items()
        if ti.style == "slate"
    }
    total_slate_frames = sum(slate_frames_by_index.values())

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": "splitsmith"})
    project = ET.SubElement(event, "project", {"name": project_name})
    total_duration_frames = (
        sum(plan.effective_duration_frames for plan in plans) + total_slate_frames
    )
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

    transitions_by_index: dict[int, StageTransition] = {t.after_stage_index: t for t in transitions}

    cumulative_offset_frames = 0
    for stage_idx, plan in enumerate(plans):
        stage = plan.stage
        head_trim_seconds = plan.head_trim_frames * fd_seconds
        eff_duration_str = _frame_aligned_str(plan.effective_duration_frames, fd_num, fd_den)

        # Slate title (issue #196). Sits on the spine BEFORE the
        # primary; bumps the cumulative offset so the primary lands
        # after the slate. Only one slate per stage; lower-third
        # variants are emitted as connected clips below.
        stage_title = titles_by_index.get(stage_idx)
        if stage_title is not None and stage_title.style == "slate":
            assert title_effect_id is not None
            slate_dur_frames = slate_frames_by_index[stage_idx]
            _emit_title_clip(
                spine,
                ref=title_effect_id,
                offset_str=_frame_aligned_str(cumulative_offset_frames, fd_num, fd_den),
                start_str="0s",
                duration_str=_frame_aligned_str(slate_dur_frames, fd_num, fd_den),
                title=stage_title,
                text_style_id=f"ts-slate-{stage_idx}",
            )
            cumulative_offset_frames += slate_dur_frames

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
            sec_clip = ET.SubElement(
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
            _attach_pip_transform(
                sec_clip,
                sec.pip,
                sequence_width=base.width,
                sequence_height=base.height,
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

        # Lower-third title (issue #196). Connected clip on the lane
        # above secondaries + overlay so the text always sits on top.
        # ``offset`` anchors at the primary's visible head; the user
        # can drag the title later in FCP if they want it elsewhere.
        if stage_title is not None and stage_title.style == "lower-third":
            assert title_effect_id is not None
            lt_lane = len(plan.usable_secondaries) + 2
            lt_dur_frames = round(stage_title.duration_seconds / fd_seconds)
            head_trim_str = _frame_aligned_str(plan.head_trim_frames, fd_num, fd_den)
            _emit_title_clip(
                primary_clip,
                ref=title_effect_id,
                offset_str=head_trim_str,
                start_str="0s",
                duration_str=_frame_aligned_str(lt_dur_frames, fd_num, fd_den),
                title=stage_title,
                text_style_id=f"ts-lt-{stage_idx}",
                lane=lt_lane,
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

        # Stage-boundary transition (issue #195). Centred on the cut
        # point: transition offset = end_of_prev - duration/2,
        # duration = transition.duration. Both adjacent stages keep
        # their full effective_duration on the spine; FCPXML's
        # transition straddles the boundary by half its duration on
        # each side and FCP cross-fades using each side's overlap
        # material.
        next_transition = transitions_by_index.get(stage_idx)
        if next_transition is not None:
            tdur_frames = round(next_transition.duration_seconds / fd_seconds)
            stage_end = cumulative_offset_frames + plan.effective_duration_frames
            t_offset = stage_end - tdur_frames // 2
            transition_el = ET.SubElement(
                spine,
                "transition",
                {
                    "name": _TRANSITION_NAMES[next_transition.kind],
                    "offset": _frame_aligned_str(t_offset, fd_num, fd_den),
                    "duration": _frame_aligned_str(tdur_frames, fd_num, fd_den),
                },
            )
            ET.SubElement(
                transition_el,
                "filter-video",
                {"ref": transition_effect_ids[next_transition.kind]},
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
