"""Per-stage export pipeline for the production UI (issue #17).

The Audit screen produces a per-stage audit JSON at
``<project>/audit/stage<N>.json`` whose ``shots[]`` is the user's source of
truth. This module is a thin orchestrator that converts that JSON into the
existing engine's :class:`Shot` records and calls the unchanged
:mod:`csv_gen`, :mod:`fcpxml_gen`, and :mod:`report` writers so the
production UI's exports are byte-comparable with ``splitsmith single``
output for the same audit data.

Pure of detection: never re-runs beep / shot detection. The whole point of
the production UI is that the user-audited shots are the truth.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import csv_gen, fcpxml_gen, overlay_render, report, trim
from ..config import Config, ReportFiles, Shot, StageAnalysis, StageData


@dataclass(frozen=True)
class StageExportRequest:
    """One stage's export job: which artefacts to write.

    The lossless trim is part of the export -- it's the archival deliverable
    that ships to FCP, distinct from the audit-mode short-GOP scrub copy
    that lives in ``<project>/trimmed/``. The FCPXML references the lossless
    trim, mirroring ``splitsmith single``: the SPA's exports are
    byte-comparable with the CLI's for the same audit data.
    """

    stage_number: int
    write_trim: bool = True
    write_csv: bool = True
    write_fcpxml: bool = True
    write_report: bool = True
    write_overlay: bool = False


@dataclass(frozen=True)
class SecondaryExport:
    """One secondary cam to ship alongside the primary (issue #54).

    The export pipeline trims each secondary into ``exports/`` with a
    ``cam_<video_id>`` suffix and references it from the multi-cam FCPXML
    as a connected clip. ``video_id`` is the project's stable per-video
    handle (:attr:`StageVideo.video_id`); secondaries without a beep can't
    sync, so the caller filters them out before passing them in.
    """

    video_id: str
    source_path: Path
    beep_time_in_source: float
    label: str = "Secondary cam"


@dataclass(frozen=True)
class StageExportResult:
    """Paths produced (or skipped) by :func:`export_stage`."""

    stage_number: int
    trimmed_video_path: Path | None
    csv_path: Path | None
    fcpxml_path: Path | None
    report_path: Path | None
    overlay_path: Path | None
    shots_written: int
    anomalies: list[str]
    # Per-cam lossless trims keyed by ``StageVideo.video_id`` (issue #54).
    # Empty when the stage is single-cam or all secondaries failed to trim.
    # The FCPXML references each present file as a connected clip.
    secondary_trimmed_paths: dict[str, Path] = field(default_factory=dict)


class StageExportError(RuntimeError):
    """Raised when the audit JSON is missing / malformed / lacks the data
    needed to produce an export. Endpoints surface this as a 400."""


def audit_shots_to_engine_shots(
    audit_data: dict[str, Any],
    *,
    beep_time_in_source: float,
) -> list[Shot]:
    """Convert the audit JSON's ``shots[]`` to engine :class:`Shot` records.

    ``beep_time_in_source`` is the beep position in the source video's
    timeline (seconds from start). Audit ``shots[].time`` is clip-local;
    we never use it directly here -- the engine wants ``time_absolute`` in
    the source, which is ``beep_time_in_source + time_from_beep``.

    ``peak_amplitude`` and ``confidence`` are looked up from the candidate
    pool (``_candidates_pending_audit.candidates``) by ``candidate_number``
    when present; otherwise default to 0.0 (manually-added shots that
    weren't tied to a detector candidate).

    Splits: shot 1's split is the draw (= ``time_from_beep``); shot N>1 is
    the difference between successive ``time_from_beep`` values. This
    mirrors :func:`csv_gen.write_splits_csv`'s expectations from the CLI.
    """
    raw_shots = audit_data.get("shots") or []
    if not isinstance(raw_shots, list) or not raw_shots:
        return []

    candidates_block = audit_data.get("_candidates_pending_audit") or {}
    candidates = candidates_block.get("candidates") if isinstance(candidates_block, dict) else None
    by_cand: dict[int, dict[str, Any]] = {}
    if isinstance(candidates, list):
        for c in candidates:
            num = c.get("candidate_number") if isinstance(c, dict) else None
            if isinstance(num, int):
                by_cand[num] = c

    # Sort by shot_number so the output is deterministic regardless of the
    # JSON's row order. Audits saved by the SPA preserve order, but external
    # tools (audit-apply) write append-style, so don't trust order.
    ordered = sorted(raw_shots, key=lambda s: s.get("shot_number", 0))

    out: list[Shot] = []
    prev_time_from_beep: float | None = None
    for raw in ordered:
        if not isinstance(raw, dict):
            continue
        ms = raw.get("ms_after_beep")
        if ms is None:
            continue
        time_from_beep = float(ms) / 1000.0
        time_absolute = beep_time_in_source + time_from_beep
        cand_num = raw.get("candidate_number")
        cand = by_cand.get(cand_num) if isinstance(cand_num, int) else None
        peak = float(cand.get("peak_amplitude", 0.0)) if isinstance(cand, dict) else 0.0
        conf = (
            float(cand.get("confidence", 0.0))
            if isinstance(cand, dict) and cand.get("confidence") is not None
            else 0.0
        )
        # Clamp confidence to the model's [0, 1] domain in case the
        # candidate carries a raw classifier score that escaped the band.
        conf = max(0.0, min(1.0, conf))
        notes_raw = raw.get("notes")
        notes = str(notes_raw) if isinstance(notes_raw, str) else ""
        shot_number = int(raw.get("shot_number", len(out) + 1))
        if prev_time_from_beep is None:
            split = time_from_beep  # draw
        else:
            split = time_from_beep - prev_time_from_beep
        prev_time_from_beep = time_from_beep
        out.append(
            Shot(
                shot_number=shot_number,
                time_absolute=time_absolute,
                time_from_beep=time_from_beep,
                split=split,
                peak_amplitude=peak,
                confidence=conf,
                notes=notes,
            )
        )
    return out


def export_stage(
    *,
    request: StageExportRequest,
    audit_path: Path,
    exports_dir: Path,
    source_video_path: Path | None,
    stage_data: StageData,
    beep_time_in_source: float,
    pre_buffer_seconds: float,
    post_buffer_seconds: float,
    config: Config,
    secondaries: list[SecondaryExport] | None = None,
) -> StageExportResult:
    """Run the export for one stage. Pure orchestration over the engine
    modules; never re-detects.

    Produces (subject to the request flags):
      - ``stage<N>_<slug>_trimmed.mp4`` -- lossless stream-copy trim of the
        source, matching ``splitsmith single``. This is the FCP-bound
        deliverable, distinct from the audit-mode short-GOP scrub copy in
        ``<project>/trimmed/``.
      - ``stage<N>_<slug>_splits.csv``
      - ``stage<N>_<slug>.fcpxml`` (references the lossless trim above)
      - ``stage<N>_<slug>_report.txt``

    ``audit_path`` must exist and contain at least one shot. ``exports_dir``
    is created if missing. ``source_video_path`` is the primary's source
    file (resolved through any symlink); it is required when ``write_trim``
    or ``write_fcpxml`` is set.
    """
    if not audit_path.exists():
        raise StageExportError(f"no audit JSON at {audit_path}; finish auditing this stage first")
    try:
        audit_data = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageExportError(f"failed to read audit JSON {audit_path}: {exc}") from exc

    shots = audit_shots_to_engine_shots(audit_data, beep_time_in_source=beep_time_in_source)
    if not shots:
        raise StageExportError(
            f"audit JSON {audit_path} has no shots in shots[]; nothing to export"
        )

    exports_dir.mkdir(parents=True, exist_ok=True)
    base = f"stage{stage_data.stage_number}_{_slugify(stage_data.stage_name)}"

    skip_reasons: list[str] = []

    # Source-reachability is the most common reason an export degrades:
    # the project stores symlinks to a USB drive that's not plugged in.
    # Build one shared, specific message so the user gets the same hint
    # regardless of which artefact tripped over the missing source.
    source_missing = source_video_path is None or not source_video_path.exists()
    missing_msg: str | None = None
    if source_missing and (request.write_trim or request.write_fcpxml):
        if source_video_path is None:
            missing_msg = (
                "source video is not registered for this stage; assign a "
                "primary on the Ingest screen first."
            )
        else:
            missing_msg = (
                f"source video not reachable: {source_video_path}. If it lives "
                "on external storage (USB drive, SD card), reconnect and "
                "re-run Generate. CSV and report still wrote -- they only "
                "need the audit JSON."
            )

    # Lossless trim into exports/. Always reference *this* file from FCPXML
    # so SPA-produced output lines up with ``splitsmith single``. The
    # audit-mode short-GOP file in <project>/trimmed/ is a scrub cache,
    # not an export.
    trimmed_path: Path | None = None
    if request.write_trim:
        if source_missing:
            assert missing_msg is not None  # populated above
            skip_reasons.append(f"trim not written: {missing_msg}")
            # If a prior run left a lossless trim, surface it -- the user
            # has *something* to ship while the source is unreachable.
            stale = exports_dir / f"{base}_trimmed.mp4"
            if stale.exists():
                trimmed_path = stale
        else:
            assert source_video_path is not None  # narrowed by source_missing
            trimmed_path = exports_dir / f"{base}_trimmed.mp4"
            try:
                trim.trim_video(
                    source_video_path,
                    trimmed_path,
                    beep_time=beep_time_in_source,
                    stage_time=stage_data.time_seconds,
                    pre_buffer_seconds=pre_buffer_seconds,
                    post_buffer_seconds=post_buffer_seconds,
                    mode="lossless",
                    overwrite=True,
                )
            except (trim.FFmpegError, FileNotFoundError, RuntimeError) as exc:
                # Don't fail the whole export -- CSV / report are still
                # useful even if ffmpeg blew up. Surface as anomaly.
                skip_reasons.append(f"trim not written: {exc}")
                trimmed_path = None
                if (exports_dir / f"{base}_trimmed.mp4").exists():
                    # Stale artefact from a prior run -- still reference it
                    # from FCPXML so the user gets *something* usable.
                    trimmed_path = exports_dir / f"{base}_trimmed.mp4"

    # Per-cam lossless trims (issue #54). Each secondary's trim lands at
    # ``stage<N>_<slug>_cam_<video_id>_trimmed.mp4`` so its name mirrors the
    # audit-mode cache slot in <project>/trimmed/. Skipped silently when
    # ``write_trim`` is off (CSV-only re-run); per-cam ffmpeg failures are
    # surfaced as anomalies so the FCPXML can still reference the cams that
    # did make it. Stale prior-run trims are kept so an aborted re-run still
    # ships a multi-cam timeline.
    secondary_trimmed: dict[str, Path] = {}
    secondary_inputs = list(secondaries or [])
    if request.write_trim:
        for sec in secondary_inputs:
            sec_target = exports_dir / f"{base}_cam_{sec.video_id}_trimmed.mp4"
            if not sec.source_path.exists():
                skip_reasons.append(
                    f"secondary cam {sec.video_id} trim not written: source not "
                    f"reachable: {sec.source_path}"
                )
                if sec_target.exists():
                    secondary_trimmed[sec.video_id] = sec_target
                continue
            try:
                trim.trim_video(
                    sec.source_path,
                    sec_target,
                    beep_time=sec.beep_time_in_source,
                    stage_time=stage_data.time_seconds,
                    pre_buffer_seconds=pre_buffer_seconds,
                    post_buffer_seconds=post_buffer_seconds,
                    mode="lossless",
                    overwrite=True,
                )
                secondary_trimmed[sec.video_id] = sec_target
            except (trim.FFmpegError, FileNotFoundError, RuntimeError) as exc:
                skip_reasons.append(f"secondary cam {sec.video_id} trim not written: {exc}")
                if sec_target.exists():
                    secondary_trimmed[sec.video_id] = sec_target
    else:
        # When trim is off, surface stale per-cam trims so the FCPXML can
        # still wire them up (mirrors the primary's stale-trim handling).
        for sec in secondary_inputs:
            sec_target = exports_dir / f"{base}_cam_{sec.video_id}_trimmed.mp4"
            if sec_target.exists():
                secondary_trimmed[sec.video_id] = sec_target

    csv_path: Path | None = None
    if request.write_csv:
        csv_path = exports_dir / f"{base}_splits.csv"
        csv_gen.write_splits_csv(shots, csv_path)

    # Overlay render (issue #45). Gated on having a trimmed clip to mirror;
    # the overlay must match the trim frame-for-frame or it will drift on
    # the FCP timeline. ``write_overlay`` defaults False so existing flows
    # (and CSV-only re-runs without a source) don't pay the cost.
    overlay_path: Path | None = None
    fcp_overlay_path: Path | None = None
    overlay_target = exports_dir / f"{base}_overlay.mov"
    if request.write_overlay:
        # Resolve the trim we'll mirror: prefer the one we just wrote, then
        # a stale lossless trim from a prior run. If none exists -- e.g.
        # source unreachable AND no prior trim -- skip with a clear reason.
        mirror_target: Path | None = None
        if trimmed_path is not None and trimmed_path.exists():
            mirror_target = trimmed_path
        elif (exports_dir / f"{base}_trimmed.mp4").exists():
            mirror_target = exports_dir / f"{base}_trimmed.mp4"

        if mirror_target is None:
            if missing_msg:
                skip_reasons.append(f"overlay not written: {missing_msg}")
            else:
                skip_reasons.append(
                    "overlay not written: no lossless trim in exports/. "
                    "Re-run Generate with the Trim toggle enabled."
                )
        else:
            try:
                overlay_render.render_overlay(
                    audit_path=audit_path,
                    trimmed_video_path=mirror_target,
                    output_path=overlay_target,
                    beep_offset_seconds=pre_buffer_seconds,
                )
                overlay_path = overlay_target
            except (overlay_render.OverlayRenderError, OSError) as exc:
                skip_reasons.append(f"overlay not written: {exc}")
                overlay_path = None
                if overlay_target.exists():
                    # Stale render from a prior run; still surface so the
                    # FCPXML can reference it.
                    overlay_path = overlay_target

    # Whether or not the overlay was just rendered, an existing
    # ``<base>_overlay.mov`` should be referenced from the FCPXML so the
    # same XML works regardless of which render produced it.
    if overlay_target.exists():
        fcp_overlay_path = overlay_target

    fcpxml_path: Path | None = None
    if request.write_fcpxml:
        # FCPXML needs a video to reference. Prefer the lossless trim we
        # just produced; fall back to a stale lossless trim from a prior
        # export if it exists; only as a last resort look for a lossless
        # trim independent of this run.
        fcp_video: Path | None = None
        candidate = exports_dir / f"{base}_trimmed.mp4"
        if trimmed_path is not None and trimmed_path.exists():
            fcp_video = trimmed_path
        elif candidate.exists():
            fcp_video = candidate

        if fcp_video is None:
            if missing_msg:
                # The trim couldn't run because the source is unreachable.
                # Surface that as the FCPXML reason too -- avoids the user
                # chasing two separate "no lossless trim" messages.
                skip_reasons.append(f"fcpxml not written: {missing_msg}")
            else:
                skip_reasons.append(
                    "fcpxml not written: no lossless trim in exports/. "
                    "Re-run Generate with the Trim toggle enabled."
                )
        else:
            fcpxml_path = exports_dir / f"{base}.fcpxml"
            try:
                meta = fcpxml_gen.probe_video(fcp_video)
                # Multi-cam wiring (issue #54). Probe each surviving secondary
                # trim and pass it as a connected clip; ffprobe failures only
                # drop that cam from the timeline (other cams still ship).
                fcp_secondaries: list[fcpxml_gen.SecondaryClip] = []
                # Preserve the input order so cam lane assignments are stable
                # across re-runs (the dict was built in input-order earlier).
                for sec in secondary_inputs:
                    sec_path = secondary_trimmed.get(sec.video_id)
                    if sec_path is None or not sec_path.exists():
                        continue
                    try:
                        sec_meta = fcpxml_gen.probe_video(sec_path)
                    except fcpxml_gen.FFprobeError as exc:
                        skip_reasons.append(
                            f"secondary cam {sec.video_id} dropped from FCPXML: {exc}"
                        )
                        continue
                    # Each cam was trimmed with the same pre-buffer as the
                    # primary, so its clip-local beep is at
                    # ``min(pre_buffer, beep_time_in_source)`` -- short heads
                    # truncate the pre-roll, in which case the cam's beep
                    # sits earlier in the file.
                    sec_beep_offset = min(pre_buffer_seconds, sec.beep_time_in_source)
                    fcp_secondaries.append(
                        fcpxml_gen.SecondaryClip(
                            video_path=sec_path,
                            video=sec_meta,
                            beep_offset_seconds=sec_beep_offset,
                            label=sec.label,
                        )
                    )
                # Beep offset within the lossless trim: the trim cut at
                # ``beep_time - pre_buffer`` from source, so the beep lives
                # ``pre_buffer`` seconds into the clip.
                fcpxml_gen.generate_fcpxml(
                    video_path=fcp_video,
                    video=meta,
                    shots=shots,
                    beep_offset_seconds=pre_buffer_seconds,
                    output_path=fcpxml_path,
                    project_name=base,
                    config=config.output,
                    overlay_path=fcp_overlay_path,
                    secondaries=fcp_secondaries or None,
                )
            except (fcpxml_gen.FFprobeError, OSError) as exc:
                skip_reasons.append(f"fcpxml not written: {exc}")
                fcpxml_path = None

    anomalies = report.detect_anomalies(shots, beep_time_in_source, stage_data.time_seconds)
    if skip_reasons:
        anomalies = [*anomalies, *skip_reasons]

    report_path: Path | None = None
    if request.write_report:
        files = ReportFiles(
            video=trimmed_path if trimmed_path and trimmed_path.exists() else None,
            csv=csv_path,
            fcpxml=fcpxml_path,
        )
        analysis = StageAnalysis(
            stage=stage_data,
            video_path=trimmed_path or source_video_path or Path(),
            beep_time=beep_time_in_source,
            shots=shots,
            anomalies=anomalies,
        )
        report_path = exports_dir / f"{base}_report.txt"
        report.write_report(
            analysis,
            files,
            report_path,
            color_thresholds=config.output.split_color_thresholds,
        )

    secondary_paths_present = {vid: p for vid, p in secondary_trimmed.items() if p.exists()}
    return StageExportResult(
        stage_number=stage_data.stage_number,
        trimmed_video_path=(
            trimmed_path if trimmed_path is not None and trimmed_path.exists() else None
        ),
        csv_path=csv_path,
        fcpxml_path=fcpxml_path,
        report_path=report_path,
        overlay_path=overlay_path if overlay_path is not None and overlay_path.exists() else None,
        shots_written=len(shots),
        anomalies=anomalies,
        secondary_trimmed_paths=secondary_paths_present,
    )


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Filesystem-friendly slug. Mirrors the CLI's ``_slugify`` exactly so
    exports have identical names whether produced via the CLI or the
    production UI -- byte-comparable filenames are part of the AC."""
    return _SLUG_RE.sub("-", name.lower()).strip("-") or "stage"
