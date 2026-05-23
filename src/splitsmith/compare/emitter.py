"""FCPXML emission for multi-shooter comparison exports.

Builds one FCPXML where each stage is a compound clip (``<media>``)
arranging the shooters' beep-aligned trims in a grid. The outer
``<sequence>`` stitches the per-stage compound clips back-to-back via
``<ref-clip>`` and drops a marker per stage.

Reuses the helpers from :mod:`splitsmith.fcpxml_gen` so frame
quantization, format-name conventions, and the source-application
xattr stay consistent with the per-stage / per-match exporters.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

from ..config import OutputConfig
from ..runtime import runtime
from ..fcpxml_gen import (
    VideoMetadata,
    _asset_grid_str,
    _format_name,
    _frame_aligned_str,
    _tag_source_application,
)
from .filler import Runner as FillerRunner
from .filler import ensure_filler
from .layout import GridSlot, compute_layout
from .manifest import CompareManifest
from .project_loader import CompareShooterBundle, CompareStageBundle

ProbeFn = Callable[[Path], VideoMetadata]
MUTE_VOLUME = "-96dB"


@dataclass(frozen=True)
class _AssetEntry:
    """One emitted ``<asset>``: id + format id + duration in sequence frames."""

    asset_id: str
    format_id: str
    duration_seq_frames: int
    metadata: VideoMetadata


def _grid_transform_attrs(slot: GridSlot, *, sequence_width: int, sequence_height: int) -> dict[str, str]:
    """``<adjust-transform>`` attrs for a grid slot.

    Mirrors :func:`splitsmith.fcpxml_gen._pip_transform_attrs`'s normalised
    convention -- one position unit equals ``sequence_height / 100`` px so
    the same emit reads correctly on 1080p and 4K timelines (#236).
    """
    unit_per_px = 100.0 / sequence_height
    x = slot.position_px[0] * unit_per_px
    y = slot.position_px[1] * unit_per_px
    return {
        "scale": f"{slot.scale:g} {slot.scale:g}",
        "position": f"{x:g} {y:g}",
    }


def _seq_frames(seconds: float, fd_seconds: float) -> int:
    return int(round(seconds / fd_seconds))


def emit_compare_fcpxml(
    *,
    manifest: CompareManifest,
    shooters: list[CompareShooterBundle],
    output_path: Path,
    config: OutputConfig | None = None,
    probe: ProbeFn | None = None,
    runner: FillerRunner = subprocess.run,
    project_name: str | None = None,
) -> None:
    """Write a multi-shooter comparison FCPXML to ``output_path``.

    ``shooters`` is in manifest order; the alphabetical slot ordering is
    derived internally from ``CompareShooterBundle.label``.

    The audio-source shooter (``manifest.audio_from``) determines the
    sequence's frame rate and pixel size; tiles whose underlying assets
    have a different rate/size get their own ``<format>`` resource and
    rely on FCP's edit-time conform.

    Stages are unioned across shooters by ``stage_number``. A stage with
    no present shooters is omitted from the timeline.
    """
    if config is None:
        config = OutputConfig()
    del probe  # the loaders already probed; emit-time probing is unused

    bundles_by_label = {s.label: s for s in shooters}
    if manifest.audio_from not in bundles_by_label:
        raise ValueError(
            f"audio_from={manifest.audio_from!r} not found among loaded shooters "
            f"({sorted(bundles_by_label)})"
        )
    sorted_labels = sorted(bundles_by_label)
    audio_bundle = bundles_by_label[manifest.audio_from]
    if not audio_bundle.stages_by_number:
        raise ValueError(
            f"audio_from shooter {manifest.audio_from!r} has no stages with trims; "
            "the sequence frame rate / size cannot be derived"
        )

    # Sequence format = first stage of the audio-source shooter.
    seq_seed = next(iter(sorted(audio_bundle.stages_by_number)))
    seq_meta = audio_bundle.stages_by_number[seq_seed].metadata
    fd_num = seq_meta.frame_rate_den
    fd_den = seq_meta.frame_rate_num
    fd_seconds = float(Fraction(fd_num, fd_den))
    seq_width = seq_meta.width
    seq_height = seq_meta.height

    fcpxml = ET.Element("fcpxml", {"version": config.fcpxml_version})
    resources = ET.SubElement(fcpxml, "resources")
    seq_format_id = "r1"
    ET.SubElement(
        resources,
        "format",
        {
            "id": seq_format_id,
            "name": _format_name(seq_meta),
            "frameDuration": _frame_aligned_str(1, fd_num, fd_den),
            "width": str(seq_width),
            "height": str(seq_height),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )

    counter = {"n": 1}

    def _next_id() -> str:
        counter["n"] += 1
        return f"r{counter['n']}"

    formats_by_key: dict[tuple[int, int, int, int], str] = {
        (seq_meta.frame_rate_num, seq_meta.frame_rate_den, seq_width, seq_height): seq_format_id
    }

    def _format_id_for(meta: VideoMetadata) -> str:
        key = (meta.frame_rate_num, meta.frame_rate_den, meta.width, meta.height)
        existing = formats_by_key.get(key)
        if existing is not None:
            return existing
        new_id = _next_id()
        formats_by_key[key] = new_id
        meta_fd_num = meta.frame_rate_den
        meta_fd_den = meta.frame_rate_num
        ET.SubElement(
            resources,
            "format",
            {
                "id": new_id,
                "name": _format_name(meta),
                "frameDuration": _frame_aligned_str(1, meta_fd_num, meta_fd_den),
                "width": str(meta.width),
                "height": str(meta.height),
                "colorSpace": "1-1-1 (Rec. 709)",
            },
        )
        return new_id

    # Emit one <asset> per (label, stage_number) trim that's actually present.
    assets: dict[tuple[str, int], _AssetEntry] = {}
    for label in sorted_labels:
        bundle = bundles_by_label[label]
        for stage_number in sorted(bundle.stages_by_number):
            stage_bundle = bundle.stages_by_number[stage_number]
            meta = stage_bundle.metadata
            asset_id = _next_id()
            format_id = _format_id_for(meta)
            duration_frames = _seq_frames(stage_bundle.duration_seconds, fd_seconds)
            asset = ET.SubElement(
                resources,
                "asset",
                {
                    "id": asset_id,
                    "name": stage_bundle.trim_path.stem,
                    "start": "0s",
                    "duration": _frame_aligned_str(duration_frames, fd_num, fd_den),
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
                {
                    "kind": "original-media",
                    "src": stage_bundle.trim_path.resolve().as_uri(),
                },
            )
            assets[(label, stage_number)] = _AssetEntry(
                asset_id=asset_id,
                format_id=format_id,
                duration_seq_frames=duration_frames,
                metadata=meta,
            )

    # Stage union, in numeric order. Stages with no present shooters at all are skipped.
    all_stage_numbers = sorted({n for b in shooters for n in b.stages_by_number})

    # Pre-render fillers as needed; cache by (W, H, fps, dur_frames).
    filler_cache: dict[tuple[int, int, int, int, int], _AssetEntry] = {}
    filler_dir = output_path.parent / "_compare_fillers"

    def _filler_asset(*, duration_frames: int) -> _AssetEntry:
        key = (
            seq_width,
            seq_height,
            seq_meta.frame_rate_num,
            seq_meta.frame_rate_den,
            duration_frames,
        )
        cached = filler_cache.get(key)
        if cached is not None:
            return cached
        duration_seconds = duration_frames * fd_seconds
        path = ensure_filler(
            width=seq_width,
            height=seq_height,
            frame_rate_num=seq_meta.frame_rate_num,
            frame_rate_den=seq_meta.frame_rate_den,
            duration_seconds=duration_seconds,
            output_dir=filler_dir,
            runner=runner,
            ffmpeg_binary=runtime().ffmpeg_binary,
        )
        format_id = _format_id_for(seq_meta)  # shares the sequence format
        asset_id = _next_id()
        asset_el = ET.SubElement(
            resources,
            "asset",
            {
                "id": asset_id,
                "name": path.stem,
                "start": "0s",
                "duration": _frame_aligned_str(duration_frames, fd_num, fd_den),
                "hasVideo": "1",
                "hasAudio": "0",
                "format": format_id,
                "videoSources": "1",
            },
        )
        ET.SubElement(
            asset_el,
            "media-rep",
            {"kind": "original-media", "src": path.resolve().as_uri()},
        )
        entry = _AssetEntry(
            asset_id=asset_id,
            format_id=format_id,
            duration_seq_frames=duration_frames,
            metadata=seq_meta,
        )
        filler_cache[key] = entry
        return entry

    # Build per-stage <media> compound clips first (resources), then the
    # outer <sequence> referencing them via <ref-clip>.
    stage_compound_ids: list[tuple[int, str, str, int]] = []
    # (stage_number, media_id, stage_name, compound_duration_frames_seq)

    for stage_number in all_stage_numbers:
        present_labels = {label for label in sorted_labels if (label, stage_number) in assets}
        if not present_labels:
            continue

        # Stage name: prefer the audio-source shooter's name if they have
        # this stage; else the alphabetically-first present shooter.
        ordered_present = [lab for lab in sorted_labels if lab in present_labels]
        if stage_number in audio_bundle.stages_by_number:
            stage_name = audio_bundle.stages_by_number[stage_number].stage_name
        else:
            stage_name = bundles_by_label[ordered_present[0]].stages_by_number[stage_number].stage_name

        # Layout uses native pixel dims of the first present shooter for
        # the letterbox scale. All tiles get the same scale per slot --
        # callers can mix non-matching cam sizes; FCP conforms each tile
        # via per-asset format references.
        first_bundle = bundles_by_label[ordered_present[0]].stages_by_number[stage_number]
        layout = compute_layout(
            sorted_labels=sorted_labels,
            present_labels=present_labels,
            sequence_width=seq_width,
            sequence_height=seq_height,
            cam_width=first_bundle.width,
            cam_height=first_bundle.height,
            layout_2up=manifest.layout_2up,
        )

        # Beep alignment: every tile's beep should land at the same parent
        # timeline frame. Use the max beep across present tiles as the
        # zero -- shooters with smaller clip-local beeps get a positive
        # offset; the max-beep tile starts at 0.
        present_stages: dict[str, CompareStageBundle] = {
            lab: bundles_by_label[lab].stages_by_number[stage_number] for lab in present_labels
        }
        max_beep = max(s.beep_offset_in_clip for s in present_stages.values())
        tile_durations_in_parent_frames: dict[str, int] = {}
        tile_offsets_frames: dict[str, int] = {}
        tile_starts_frames: dict[str, int] = {}
        for lab, stage_bundle in present_stages.items():
            delta_frames = round((max_beep - stage_bundle.beep_offset_in_clip) / fd_seconds)
            if delta_frames >= 0:
                offset_frames = delta_frames
                start_frames = 0
            else:
                offset_frames = 0
                start_frames = -delta_frames
            tile_offsets_frames[lab] = offset_frames
            tile_starts_frames[lab] = start_frames
            available_seconds = stage_bundle.duration_seconds - (start_frames * fd_seconds)
            avail_parent_frames = max(0, _seq_frames(available_seconds, fd_seconds))
            tile_durations_in_parent_frames[lab] = avail_parent_frames

        # Compound duration: longest tile-end across present labels.
        compound_frames = max(
            tile_offsets_frames[lab] + tile_durations_in_parent_frames[lab] for lab in present_labels
        )

        media_id = _next_id()
        media_el = ET.SubElement(
            resources,
            "media",
            {"id": media_id, "name": f"stage{stage_number}-grid"},
        )
        compound_seq = ET.SubElement(
            media_el,
            "sequence",
            {
                "format": seq_format_id,
                "duration": _frame_aligned_str(compound_frames, fd_num, fd_den),
                "tcStart": "0s",
                "tcFormat": "NDF",
                "audioLayout": "stereo",
                "audioRate": "48k",
            },
        )
        compound_spine = ET.SubElement(compound_seq, "spine")

        # Slot 0 (alphabetically first present label) is the spine clip;
        # later slots ride as connected clips on lanes 1..N-1. Filler
        # tiles take lanes after that.
        spine_label = ordered_present[0]
        for slot_idx, lab in enumerate(ordered_present):
            asset = assets[(lab, stage_number)]
            attrs = {
                "ref": asset.asset_id,
                "offset": _frame_aligned_str(tile_offsets_frames[lab], fd_num, fd_den),
                "name": lab,
                "start": _asset_grid_str(
                    tile_starts_frames[lab] * fd_seconds,
                    asset.metadata,
                ),
                "duration": _frame_aligned_str(tile_durations_in_parent_frames[lab], fd_num, fd_den),
                "format": asset.format_id,
            }
            if lab != spine_label:
                attrs["lane"] = str(slot_idx)
            clip_el = ET.SubElement(compound_spine, "asset-clip", attrs)
            slot = layout.slots_per_label[lab]
            transform_attrs = _grid_transform_attrs(
                slot, sequence_width=seq_width, sequence_height=seq_height
            )
            ET.SubElement(clip_el, "adjust-transform", transform_attrs)
            if lab != manifest.audio_from:
                ET.SubElement(
                    clip_el,
                    "adjust-volume",
                    {"amount": MUTE_VOLUME},
                )

        # Filler tiles: one asset-clip per empty slot, spanning the full
        # compound duration so the tile is visible for the entire stage.
        next_lane = len(ordered_present)
        for slot in layout.empty_slots:
            asset_entry = _filler_asset(duration_frames=compound_frames)
            attrs = {
                "ref": asset_entry.asset_id,
                "lane": str(next_lane),
                "offset": "0s",
                "name": "filler",
                "start": "0s",
                "duration": _frame_aligned_str(compound_frames, fd_num, fd_den),
                "format": asset_entry.format_id,
            }
            filler_clip = ET.SubElement(compound_spine, "asset-clip", attrs)
            transform_attrs = _grid_transform_attrs(
                slot, sequence_width=seq_width, sequence_height=seq_height
            )
            ET.SubElement(filler_clip, "adjust-transform", transform_attrs)
            next_lane += 1

        stage_compound_ids.append((stage_number, media_id, stage_name, compound_frames))

    # Outer <sequence>: stitch the compound clips together with markers.
    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": "splitsmith"})
    project_name_str = project_name or output_path.stem
    project_el = ET.SubElement(event, "project", {"name": project_name_str})
    total_frames = sum(c[3] for c in stage_compound_ids)
    outer_seq = ET.SubElement(
        project_el,
        "sequence",
        {
            "format": seq_format_id,
            "duration": _frame_aligned_str(total_frames, fd_num, fd_den),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    outer_spine = ET.SubElement(outer_seq, "spine")
    cumulative_frames = 0
    for stage_number, media_id, stage_name, frames in stage_compound_ids:
        ref_clip = ET.SubElement(
            outer_spine,
            "ref-clip",
            {
                "ref": media_id,
                "offset": _frame_aligned_str(cumulative_frames, fd_num, fd_den),
                "name": f"Stage {stage_number} -- {stage_name}",
                "start": "0s",
                "duration": _frame_aligned_str(frames, fd_num, fd_den),
                "srcEnable": "all",
            },
        )
        ET.SubElement(
            ref_clip,
            "marker",
            {
                "start": "0s",
                "duration": _frame_aligned_str(1, fd_num, fd_den),
                "value": f"Stage {stage_number} -- {stage_name}",
            },
        )
        cumulative_frames += frames

    ET.indent(fcpxml, space="    ")
    tree_bytes = ET.tostring(fcpxml, encoding="utf-8", xml_declaration=True)
    decl_end = tree_bytes.index(b"?>") + 2
    output_path.write_bytes(tree_bytes[:decl_end] + b"\n<!DOCTYPE fcpxml>\n" + tree_bytes[decl_end + 1 :])
    _tag_source_application(output_path)
