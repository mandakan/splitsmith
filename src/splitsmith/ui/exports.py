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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import csv_gen, fcpxml_gen, report
from ..config import Config, ReportFiles, Shot, StageAnalysis, StageData


@dataclass(frozen=True)
class StageExportRequest:
    """One stage's export job: which artefacts to write.

    The trimmed video is not regenerated here -- it lives at
    ``<project>/trimmed/stage<N>_<slug>.mp4`` from the audit-mode trim and
    is referenced as-is. The FCPXML is only written when that file exists,
    matching the CLI's ``_process_one`` behaviour.
    """

    stage_number: int
    write_csv: bool = True
    write_fcpxml: bool = True
    write_report: bool = True


@dataclass(frozen=True)
class StageExportResult:
    """Paths produced (or skipped) by :func:`export_stage`."""

    stage_number: int
    csv_path: Path | None
    fcpxml_path: Path | None
    report_path: Path | None
    trimmed_video_path: Path | None
    shots_written: int
    anomalies: list[str]


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
    trimmed_video_path: Path | None,
    stage_data: StageData,
    beep_time_in_source: float,
    config: Config,
) -> StageExportResult:
    """Run the export for one stage. Pure orchestration over the engine
    modules; never re-detects.

    ``audit_path`` must exist and contain at least one shot. ``exports_dir``
    is created if missing. The trimmed video is required for FCPXML (the
    timeline references it) -- when missing the FCPXML step is skipped
    rather than failing the whole export.
    """
    if not audit_path.exists():
        raise StageExportError(
            f"no audit JSON at {audit_path}; finish auditing this stage first"
        )
    try:
        audit_data = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageExportError(f"failed to read audit JSON {audit_path}: {exc}") from exc

    shots = audit_shots_to_engine_shots(
        audit_data, beep_time_in_source=beep_time_in_source
    )
    if not shots:
        raise StageExportError(
            f"audit JSON {audit_path} has no shots in shots[]; nothing to export"
        )

    exports_dir.mkdir(parents=True, exist_ok=True)
    base = f"stage{stage_data.stage_number}_{_slugify(stage_data.stage_name)}"

    csv_path: Path | None = None
    if request.write_csv:
        csv_path = exports_dir / f"{base}_splits.csv"
        csv_gen.write_splits_csv(shots, csv_path)

    fcpxml_path: Path | None = None
    fcpxml_skipped_reason: str | None = None
    if request.write_fcpxml:
        if trimmed_video_path is None or not trimmed_video_path.exists():
            fcpxml_skipped_reason = (
                "no trimmed clip on disk; trim the stage from the audit screen first"
            )
        else:
            fcpxml_path = exports_dir / f"{base}.fcpxml"
            meta = fcpxml_gen.probe_video(trimmed_video_path)
            # Beep offset *within the trimmed clip*: comes from the audit
            # JSON (``beep_time``), which the audit pipeline writes as
            # ``beep_in_clip`` -- not the source beep. Fall back to the
            # configured trim buffer if missing (matches the CLI default).
            audit_beep_in_clip = audit_data.get("beep_time")
            beep_offset_in_clip = (
                float(audit_beep_in_clip)
                if isinstance(audit_beep_in_clip, (int, float))
                else config.output.trim_buffer_seconds
            )
            fcpxml_gen.generate_fcpxml(
                video_path=trimmed_video_path,
                video=meta,
                shots=shots,
                beep_offset_seconds=beep_offset_in_clip,
                output_path=fcpxml_path,
                project_name=base,
                config=config.output,
            )

    anomalies = report.detect_anomalies(shots, beep_time_in_source, stage_data.time_seconds)
    files = ReportFiles(
        video=trimmed_video_path if trimmed_video_path and trimmed_video_path.exists() else None,
        csv=csv_path,
        fcpxml=fcpxml_path,
    )
    report_path: Path | None = None
    if request.write_report:
        analysis = StageAnalysis(
            stage=stage_data,
            video_path=trimmed_video_path or Path(),
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

    if fcpxml_skipped_reason and request.write_fcpxml:
        # Surface as an anomaly so the report lists it without failing the
        # whole export. The SPA also reads the StageExportResult and shows
        # the user; this keeps the file-side artefact aligned.
        anomalies = [*anomalies, f"FCPXML not written: {fcpxml_skipped_reason}"]

    return StageExportResult(
        stage_number=stage_data.stage_number,
        csv_path=csv_path,
        fcpxml_path=fcpxml_path,
        report_path=report_path,
        trimmed_video_path=trimmed_video_path
        if trimmed_video_path and trimmed_video_path.exists()
        else None,
        shots_written=len(shots),
        anomalies=anomalies,
    )


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Filesystem-friendly slug. Mirrors the CLI's ``_slugify`` exactly so
    exports have identical names whether produced via the CLI or the
    production UI -- byte-comparable filenames are part of the AC."""
    return _SLUG_RE.sub("-", name.lower()).strip("-") or "stage"
