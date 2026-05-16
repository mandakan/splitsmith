"""FCP7 XML (xmeml) renderer for the Composition IR (issue #197).

Walks ``composition.Composition`` and emits a Final Cut Pro 7-style
``.xml`` file. Premiere Pro and DaVinci Resolve both import this format
via ``File > Import``; one renderer covers most of the non-FCP NLE world
without reverse-engineering binary project files.

Coverage in this PR:

- Primary clip per stage on V1, in/out trimmed to the head/tail pads.
- Secondary cams as connected clips on V2..VN, time-aligned to the
  primary's beep frame using the same head-slip math as
  ``fcpxml_gen.generate_match_fcpxml``.
- PiP via the FCP7 "Basic Motion" filter (Scale + Center). The IR's
  ``Transform`` is in pixel-space with +Y up; FCP7 uses normalized units
  with +Y down, so we convert.
- Alpha overlay on the topmost track, full-frame.
- Per-shot markers under each primary clipitem.

Out of scope (sibling issues): transitions (#195), title cards (#196),
intro/outro segments (#173), audio mix tweaks. ``Composition.transitions``
/ ``intro`` / ``outro`` are silently ignored by this renderer for now;
when those features land they'll add to the emit path here.

Frame rate convention: FCP7 carries an integer ``timebase`` plus a
boolean ``ntsc`` flag. Fractional NTSC rates (29.97 / 59.94 / 23.976)
map to ``timebase`` rounded up plus ``ntsc=TRUE``; whole-number rates
map to ``timebase`` plus ``ntsc=FALSE``. Frame counts in the XML are
non-drop-frame integer frames at the actual rate.

Compatibility caveat: importing into Premiere / DaVinci is the release
gate -- the unit tests cover structural correctness (timebase, lane
allocation, marker frame math) but a manual import check is required
before the format is considered done.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from .composition import (
    Composition,
    ConnectedClip,
    Stage,
    Transform,
)
from .config import VideoMetadata


def render_fcp7xml(
    composition: Composition,
    *,
    output_path: Path,
) -> None:
    """Render ``composition`` as FCP7 XML at ``output_path``.

    The renderer is self-contained: it doesn't shell out, doesn't probe
    media, and produces a single ``.xml`` file the user can drop into
    Premiere or DaVinci. ``output_path`` is overwritten in place.
    """
    seq = composition.sequence
    timebase, ntsc = _fcp7_rate(seq.frame_rate_num, seq.frame_rate_den)

    plans = [_plan_stage(stage, seq.frame_rate_num, seq.frame_rate_den) for stage in composition.stages]
    total_frames = sum(p.effective_duration_frames for p in plans)
    max_secondaries = max((len(stage.secondaries) for stage in composition.stages), default=0)
    has_overlay = any(stage.overlay is not None for stage in composition.stages)

    xmeml = ET.Element("xmeml", {"version": "5"})
    project = ET.SubElement(xmeml, "project")
    _text(project, "name", composition.project_name)
    children = ET.SubElement(project, "children")

    sequence = ET.SubElement(children, "sequence", {"id": "sequence-1"})
    _text(sequence, "name", composition.project_name)
    _text(sequence, "duration", str(total_frames))
    _emit_rate(sequence, timebase, ntsc)

    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    _emit_video_format(video, seq.width, seq.height, timebase, ntsc)

    file_ids: dict[Path, str] = {}

    # V1: primaries.
    track_v1 = ET.SubElement(video, "track")
    _emit_track_flags(track_v1)
    cumulative = 0
    for plan in plans:
        _emit_primary_clipitem(
            track_v1,
            plan=plan,
            spine_offset_frames=cumulative,
            file_ids=file_ids,
            timebase=timebase,
            ntsc=ntsc,
            sequence_width=seq.width,
            sequence_height=seq.height,
        )
        cumulative += plan.effective_duration_frames

    # V2..VN: secondary cams. One track per cam-index across stages so a
    # stage with N cams uses tracks V2..V(N+1). Stages with fewer cams
    # leave gaps on the higher tracks; FCP7 readers handle gaps natively.
    for cam_index in range(max_secondaries):
        track = ET.SubElement(video, "track")
        _emit_track_flags(track)
        cumulative = 0
        for plan in plans:
            stage = plan.stage
            if cam_index < len(stage.secondaries):
                sec = stage.secondaries[cam_index]
                _emit_secondary_clipitem(
                    track,
                    stage=stage,
                    secondary=sec,
                    plan=plan,
                    spine_offset_frames=cumulative,
                    file_ids=file_ids,
                    timebase=timebase,
                    ntsc=ntsc,
                    sequence_width=seq.width,
                    sequence_height=seq.height,
                )
            cumulative += plan.effective_duration_frames

    # Overlay on the topmost track, full-frame, head-aligned to each
    # stage's visible head (no transform). Skipped entirely when no stage
    # carries an overlay.
    if has_overlay:
        track = ET.SubElement(video, "track")
        _emit_track_flags(track)
        cumulative = 0
        for plan in plans:
            if plan.stage.overlay is not None:
                _emit_overlay_clipitem(
                    track,
                    overlay=plan.stage.overlay,
                    plan=plan,
                    spine_offset_frames=cumulative,
                    file_ids=file_ids,
                    timebase=timebase,
                    ntsc=ntsc,
                )
            cumulative += plan.effective_duration_frames

    ET.indent(xmeml, space="    ")
    tree_bytes = ET.tostring(xmeml, encoding="utf-8", xml_declaration=True)
    decl_end = tree_bytes.index(b"?>") + 2
    output_path.write_bytes(tree_bytes[:decl_end] + b"\n<!DOCTYPE xmeml>\n" + tree_bytes[decl_end + 1 :])


# --- planning -------------------------------------------------------------


class _StagePlan:
    """Per-stage timing plan in frames (mirrors ``fcpxml_gen``'s math).

    ``primary_duration_frames`` is the source clip length in frames,
    ``head_trim_frames`` is how many frames to skip from the head, and
    ``effective_duration_frames`` is what lands on the spine. We
    duplicate the math here rather than importing the FCPXML emitter's
    private helpers; the IR is the layer they're meant to share, not
    each other's internals.
    """

    __slots__ = (
        "stage",
        "primary_duration_frames",
        "head_trim_frames",
        "head_trim_seconds",
        "effective_duration_frames",
        "fps",
    )

    def __init__(
        self,
        *,
        stage: Stage,
        primary_duration_frames: int,
        head_trim_frames: int,
        head_trim_seconds: float,
        effective_duration_frames: int,
        fps: float,
    ) -> None:
        self.stage = stage
        self.primary_duration_frames = primary_duration_frames
        self.head_trim_frames = head_trim_frames
        self.head_trim_seconds = head_trim_seconds
        self.effective_duration_frames = effective_duration_frames
        self.fps = fps


def _plan_stage(stage: Stage, frame_rate_num: int, frame_rate_den: int) -> _StagePlan:
    fps = frame_rate_num / frame_rate_den
    fd_seconds = frame_rate_den / frame_rate_num
    primary_duration_frames = round(stage.primary.metadata.duration_seconds / fd_seconds)

    head_avail = max(0.0, stage.beep_offset_seconds)
    if stage.markers:
        # IR's marker.time_seconds is clip-local source time; the last
        # shot's local time = beep + max(time_from_beep). Since markers
        # carry the raw Shot we just use marker.time_seconds.
        last_local = max(m.time_seconds for m in stage.markers)
    else:
        last_local = stage.beep_offset_seconds
    tail_avail = max(0.0, stage.primary.metadata.duration_seconds - last_local)

    head_trim_seconds = max(0.0, head_avail - stage.head_pad_seconds)
    tail_trim_seconds = max(0.0, tail_avail - stage.tail_pad_seconds)
    head_trim_frames = round(head_trim_seconds / fd_seconds)
    tail_trim_frames = round(tail_trim_seconds / fd_seconds)
    effective = primary_duration_frames - head_trim_frames - tail_trim_frames
    if effective <= 0:
        raise ValueError(
            f"stage {stage.name!r} would have non-positive effective duration "
            f"after trim ({effective} frames); reduce head/tail pad or check "
            "the trimmed clip length"
        )
    return _StagePlan(
        stage=stage,
        primary_duration_frames=primary_duration_frames,
        head_trim_frames=head_trim_frames,
        head_trim_seconds=head_trim_seconds,
        effective_duration_frames=effective,
        fps=fps,
    )


# --- emit helpers ---------------------------------------------------------


def _text(parent: ET.Element, tag: str, value: str) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = value
    return el


def _bool(parent: ET.Element, tag: str, value: bool) -> ET.Element:
    return _text(parent, tag, "TRUE" if value else "FALSE")


def _emit_rate(parent: ET.Element, timebase: int, ntsc: bool) -> None:
    rate = ET.SubElement(parent, "rate")
    _text(rate, "timebase", str(timebase))
    _bool(rate, "ntsc", ntsc)


def _emit_track_flags(track: ET.Element) -> None:
    """Default per-track flags Premiere expects; absent flags surface as
    "track disabled" warnings on import."""
    _bool(track, "enabled", True)
    _bool(track, "locked", False)


def _emit_video_format(
    video: ET.Element,
    width: int,
    height: int,
    timebase: int,
    ntsc: bool,
) -> None:
    fmt = ET.SubElement(video, "format")
    sample = ET.SubElement(fmt, "samplecharacteristics")
    _text(sample, "width", str(width))
    _text(sample, "height", str(height))
    _text(sample, "pixelaspectratio", "square")
    _text(sample, "fielddominance", "none")
    _emit_rate(sample, timebase, ntsc)


def _file_id_for(path: Path, file_ids: dict[Path, str]) -> tuple[str, bool]:
    """Allocate (or look up) a stable file id for ``path``.

    Returns ``(id, is_first_use)``. First use emits the full ``<file>``
    body; subsequent uses emit just ``<file id="..."/>`` so the file's
    metadata isn't repeated.
    """
    resolved = path.resolve()
    if resolved in file_ids:
        return file_ids[resolved], False
    file_id = f"file-{len(file_ids) + 1}"
    file_ids[resolved] = file_id
    return file_id, True


def _emit_file(
    parent: ET.Element,
    *,
    path: Path,
    meta: VideoMetadata,
    file_ids: dict[Path, str],
    has_audio: bool,
) -> None:
    """Emit a ``<file>`` referencing the source media.

    The ``<rate>`` and ``<duration>`` come from the source's own
    metadata (#233): for mixed-rate timelines the underlying file's
    frame rate must match its actual encoding so xmeml-aware readers
    (Premiere, Resolve) conform correctly. The clipitem above this
    file keeps the sequence rate for spine-relative timing.
    """
    file_id, first_use = _file_id_for(path, file_ids)
    if not first_use:
        ET.SubElement(parent, "file", {"id": file_id})
        return
    file_el = ET.SubElement(parent, "file", {"id": file_id})
    _text(file_el, "name", path.name)
    _text(file_el, "pathurl", path.resolve().as_uri())
    file_timebase, file_ntsc = _fcp7_rate(meta.frame_rate_num, meta.frame_rate_den)
    _emit_rate(file_el, file_timebase, file_ntsc)
    file_fd = meta.frame_rate_den / meta.frame_rate_num
    _text(file_el, "duration", str(round(meta.duration_seconds / file_fd)))
    media = ET.SubElement(file_el, "media")
    video = ET.SubElement(media, "video")
    sample = ET.SubElement(video, "samplecharacteristics")
    _text(sample, "width", str(meta.width))
    _text(sample, "height", str(meta.height))
    if has_audio:
        audio = ET.SubElement(media, "audio")
        _text(audio, "channelcount", "2")


def _emit_primary_clipitem(
    track: ET.Element,
    *,
    plan: _StagePlan,
    spine_offset_frames: int,
    file_ids: dict[Path, str],
    timebase: int,
    ntsc: bool,
    sequence_width: int,
    sequence_height: int,
) -> None:
    stage = plan.stage
    clip = ET.SubElement(
        track,
        "clipitem",
        {"id": f"clipitem-primary-{_safe_id(stage.name)}-{spine_offset_frames}"},
    )
    _text(clip, "name", stage.name)
    _text(clip, "duration", str(plan.primary_duration_frames))
    _emit_rate(clip, timebase, ntsc)
    _text(clip, "start", str(spine_offset_frames))
    _text(clip, "end", str(spine_offset_frames + plan.effective_duration_frames))
    _text(clip, "in", str(plan.head_trim_frames))
    _text(clip, "out", str(plan.head_trim_frames + plan.effective_duration_frames))
    _emit_file(
        clip,
        path=stage.primary.path,
        meta=stage.primary.metadata,
        file_ids=file_ids,
        has_audio=True,
    )
    fd_seconds = stage.primary.metadata.frame_rate_den / stage.primary.metadata.frame_rate_num
    for marker in stage.markers:
        # The IR stores marker.time_seconds in clip-local source time;
        # FCP7 markers ride inside the clipitem and use source-frame
        # coordinates (same as our ``in`` value), so no spine math here.
        frame = round(marker.time_seconds / fd_seconds)
        if frame < plan.head_trim_frames or frame >= plan.head_trim_frames + plan.effective_duration_frames:
            continue
        _emit_marker(clip, frame=frame, label=_marker_label(marker))


def _emit_secondary_clipitem(
    track: ET.Element,
    *,
    stage: Stage,
    secondary: ConnectedClip,
    plan: _StagePlan,
    spine_offset_frames: int,
    file_ids: dict[Path, str],
    timebase: int,
    ntsc: bool,
    sequence_width: int,
    sequence_height: int,
) -> None:
    """Emit one cam clipitem aligned so its beep matches the primary's
    visible-head beep frame. Mirrors the head-slip math from the FCPXML
    emitter (``fcpxml_gen.generate_match_fcpxml``)."""
    fd_seconds = stage.primary.metadata.frame_rate_den / stage.primary.metadata.frame_rate_num
    sec_meta = secondary.asset.metadata
    sec_duration_frames = round(sec_meta.duration_seconds / fd_seconds)
    assert secondary.beep_offset_seconds is not None  # cam role guarantees this
    delta_frames = round(
        ((stage.beep_offset_seconds - plan.head_trim_seconds) - secondary.beep_offset_seconds) / fd_seconds
    )
    if delta_frames >= 0:
        cam_spine_offset = spine_offset_frames + delta_frames
        cam_in = 0
    else:
        cam_spine_offset = spine_offset_frames
        cam_in = -delta_frames
    cam_visible_frames = sec_duration_frames - cam_in
    cam_end = cam_spine_offset + cam_visible_frames

    clip = ET.SubElement(
        track,
        "clipitem",
        {"id": f"clipitem-cam-{_safe_id(secondary.label)}-{cam_spine_offset}"},
    )
    _text(clip, "name", secondary.label)
    _text(clip, "duration", str(sec_duration_frames))
    _emit_rate(clip, timebase, ntsc)
    _text(clip, "start", str(cam_spine_offset))
    _text(clip, "end", str(cam_end))
    _text(clip, "in", str(cam_in))
    _text(clip, "out", str(sec_duration_frames))
    _emit_file(
        clip,
        path=secondary.asset.path,
        meta=sec_meta,
        file_ids=file_ids,
        has_audio=True,
    )
    if secondary.transform is not None:
        _emit_basic_motion_filter(
            clip,
            transform=secondary.transform,
            sequence_width=sequence_width,
            sequence_height=sequence_height,
        )


def _emit_overlay_clipitem(
    track: ET.Element,
    *,
    overlay: ConnectedClip,
    plan: _StagePlan,
    spine_offset_frames: int,
    file_ids: dict[Path, str],
    timebase: int,
    ntsc: bool,
) -> None:
    """Emit the alpha overlay clipitem. The overlay was rendered to mirror
    the primary frame-for-frame so we trim it the same way: ``in`` skips
    by the primary's head_trim, ``out`` runs for ``effective_duration``."""
    fd_seconds = overlay.asset.metadata.frame_rate_den / overlay.asset.metadata.frame_rate_num
    overlay_duration_frames = round(overlay.asset.metadata.duration_seconds / fd_seconds)
    clip = ET.SubElement(
        track,
        "clipitem",
        {"id": f"clipitem-overlay-{spine_offset_frames}"},
    )
    _text(clip, "name", overlay.label)
    _text(clip, "duration", str(overlay_duration_frames))
    _emit_rate(clip, timebase, ntsc)
    _text(clip, "start", str(spine_offset_frames))
    _text(clip, "end", str(spine_offset_frames + plan.effective_duration_frames))
    _text(clip, "in", str(plan.head_trim_frames))
    _text(clip, "out", str(plan.head_trim_frames + plan.effective_duration_frames))
    _emit_file(
        clip,
        path=overlay.asset.path,
        meta=overlay.asset.metadata,
        file_ids=file_ids,
        has_audio=False,
    )


def _emit_marker(parent: ET.Element, *, frame: int, label: str) -> None:
    marker = ET.SubElement(parent, "marker")
    _text(marker, "name", label)
    _text(marker, "in", str(frame))
    # FCP itself emits 1-frame ranges for point markers; Premiere reads
    # both ``out=in+1`` and ``out=-1``, but the +1 form survives a
    # round-trip through DaVinci more reliably.
    _text(marker, "out", str(frame + 1))


def _emit_basic_motion_filter(
    parent: ET.Element,
    *,
    transform: Transform,
    sequence_width: int,
    sequence_height: int,
) -> None:
    """Emit a Basic Motion filter that scales + positions the clip.

    FCP7 Basic Motion uses normalised coordinates: (0, 0) is sequence
    centre, +1.0 horiz means the clip centre sits at the right edge,
    +1.0 vert means the BOTTOM edge (FCP7 Y axis flips relative to
    FCPXML's). Scale is in percent (25 = 25% of native).
    """
    horiz = transform.position[0] / (sequence_width / 2.0)
    vert = -transform.position[1] / (sequence_height / 2.0)

    filt = ET.SubElement(parent, "filter")
    effect = ET.SubElement(filt, "effect")
    _text(effect, "name", "Basic Motion")
    _text(effect, "effectid", "basic")
    _text(effect, "effectcategory", "motion")
    _text(effect, "effecttype", "motion")
    _text(effect, "mediatype", "video")
    # Scale parameter (percent).
    scale_param = ET.SubElement(effect, "parameter")
    _text(scale_param, "name", "Scale")
    _text(scale_param, "parameterid", "scale")
    _text(scale_param, "value", f"{transform.scale * 100:g}")
    _text(scale_param, "valuemin", "0")
    _text(scale_param, "valuemax", "1000")
    # Center parameter (normalised horiz/vert).
    center_param = ET.SubElement(effect, "parameter")
    _text(center_param, "name", "Center")
    _text(center_param, "parameterid", "center")
    value = ET.SubElement(center_param, "value")
    _text(value, "horiz", f"{horiz:g}")
    _text(value, "vert", f"{vert:g}")


def _marker_label(marker: object) -> str:
    """Best-effort marker label.

    The IR's ``Marker.shot`` carries the underlying ``Shot``; we don't
    re-import the FCPXML emitter's split-colour banding here -- a
    minimal "Shot N / split" label is enough for FCP7 import. The
    FCPXML renderer does the richer banding because FCP-the-editor
    reads it; Premiere/DaVinci show plain text.
    """
    shot = getattr(marker, "shot", None)
    if shot is None:
        return "Marker"
    n = getattr(shot, "shot_number", "?")
    split = getattr(shot, "split", None)
    if split is None:
        return f"Shot {n}"
    return f"Shot {n} / {split:.2f}s"


def _safe_id(label: str) -> str:
    """xmeml ``id`` attributes are CDATA but most importers expect them
    to be word-shaped. Strip whitespace / punctuation to keep ids stable
    and importer-friendly."""
    return "".join(c if c.isalnum() else "-" for c in label).strip("-") or "x"


# --- frame rate -----------------------------------------------------------


def _fcp7_rate(frame_rate_num: int, frame_rate_den: int) -> tuple[int, bool]:
    """Convert ``num/den`` to FCP7's ``(timebase, ntsc)`` pair.

    NTSC fractional rates (29.97 / 59.94 / 23.976) carry a rounded
    timebase plus ``ntsc=TRUE``. Whole-number rates carry an exact
    timebase plus ``ntsc=FALSE``.
    """
    fps = frame_rate_num / frame_rate_den
    rounded = round(fps)
    # An exact integer frame rate (30 / 25 / 24) is non-NTSC.
    if abs(fps - rounded) < 1e-6:
        return rounded, False
    # Otherwise it's an NTSC fractional rate (29.97 = 30000/1001 etc.).
    return rounded, True


__all__ = ["render_fcp7xml"]
