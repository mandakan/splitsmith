"""Export MCP tools (issue #211 layer 3e).

Wraps the per-stage and match-level export pipelines as
synchronous MCP tools. The agent flow:

1. ``list_templates`` -> pick a template, read its preset values.
2. (loop) ``export_stage`` per stage to write trim + CSV + FCPXML +
   report + optional overlay to ``<project>/exports/``.
3. ``export_match`` to stitch the per-stage exports into one
   match-level FCPXML / FCP7XML / MP4. Optional YouTube sidecar +
   per-shot SRT.

Tools do not call detection or trim helpers transparently. The
agent runs ``detect_beep`` / ``detect_shots`` / ``trim_audit_clip``
explicitly first; ``export_stage`` errors clearly when audit JSON
is missing, and ``export_match`` errors when any per-stage export
artefact is missing. Composing a stitched export from incomplete
ingest is rarely what the agent wants -- a clear error beats a
silent half-export.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .. import templates as templates_module
from ..config import Config
from ..config import StageData as EngineStageData
from ..ui import exports as export_helpers
from ..ui import match_exports as match_export_helpers
from ..ui.project import MatchProject, StageEntry
from .sandbox import resolve_project_root

# ``StageData`` requires a non-None ``scorecard_updated_at`` but
# ``StageEntry`` allows None on placeholder stages (no scoreboard
# yet). Use this sentinel so the engine still gets a valid datetime
# without us inventing one that looks real.
_PLACEHOLDER_SCORECARD_TIME = datetime(2000, 1, 1, tzinfo=UTC)


def _engine_stage_data(stage: StageEntry) -> EngineStageData:
    """Translate a ``StageEntry`` to the engine's ``StageData``.

    Stages that never went through scoreboard import have
    ``scorecard_updated_at=None``; the engine treats it as a
    diagnostic-only field, so substituting a sentinel keeps the
    export path runnable on placeholder projects.
    """
    return EngineStageData(
        stage_number=stage.stage_number,
        stage_name=stage.stage_name,
        time_seconds=stage.time_seconds,
        scorecard_updated_at=stage.scorecard_updated_at or _PLACEHOLDER_SCORECARD_TIME,
    )


def list_templates_tool(
    *,
    user_dir: str | None = None,
) -> list[dict[str, Any]]:
    """List the export-template catalogue.

    Returns the merged builtin + user templates (user wins on id
    collision). ``user_dir`` defaults to ``~/.splitsmith/templates``;
    pass an explicit path to scan a different directory.

    Each row: ``{id, source, name, description, settings}`` where
    ``settings`` is the template's resolved preset values (the
    fields the agent forwards to ``export_stage`` /
    ``export_match``). Fields the template didn't set come back as
    ``None`` so the agent can fall back to the tool's defaults.
    """
    user_path = Path(user_dir).expanduser() if user_dir else None
    entries = templates_module.list_templates(user_dir=user_path)
    return [
        {
            "id": entry.id,
            "source": entry.source,
            "name": entry.template.name,
            "description": entry.template.description,
            "settings": entry.template.model_dump(mode="json", exclude={"schema_version"}),
        }
        for entry in entries
    ]


def export_stage_tool(
    project_root: str,
    *,
    stage_number: int,
    write_trim: bool = True,
    write_csv: bool = True,
    write_fcpxml: bool = True,
    write_report: bool = True,
    write_overlay: bool = False,
    overlay_codec: Literal["auto", "hevc-alpha", "prores-4444"] = "auto",
    overlay_max_height: int | None = None,
    overlay_max_fps: float | None = None,
    overlay_theme: Literal["splitsmith", "clean"] = "splitsmith",
) -> dict[str, Any]:
    """Run the per-stage export -- writes the lossless trim + CSV +
    FCPXML + report (and optional overlay) into ``<project>/exports/``.

    Mirror of ``POST /api/stages/{n}/export``. The lossless trim is
    distinct from the audit-mode short-GOP scrub copy under
    ``<project>/trimmed/`` (which ``trim_audit_clip`` writes).

    Preconditions: stage has primary, primary has ``beep_time``,
    ``audit/stage<N>.json`` exists with at least one shot
    (``detect_shots`` runs first; or the audit JSON was hand-edited).

    Returns paths to the artefacts written + per-secondary trim map +
    any anomalies surfaced by the engine.
    """
    root = resolve_project_root(project_root)
    project = MatchProject.load(root)
    try:
        stage = project.stage(stage_number)
    except KeyError as exc:
        raise ValueError(f"stage {stage_number} not found") from exc
    primary = next((v for v in stage.videos if v.role == "primary"), None)
    if primary is None:
        raise ValueError(f"stage {stage_number} has no primary video")
    if primary.beep_time is None:
        raise ValueError(
            f"stage {stage_number} primary has no beep_time yet; " "run detect_beep or set_beep_manual first"
        )
    source = project.resolve_video_path(root, primary.path)
    if not source.exists():
        raise FileNotFoundError(f"primary source missing for stage {stage_number}: {source}")

    audit_path = project.audit_path(root) / f"stage{stage_number}.json"
    if not audit_path.exists():
        raise FileNotFoundError(
            f"audit JSON missing for stage {stage_number}: {audit_path}; "
            "run detect_shots or write the audit file first"
        )

    exports_dir = project.exports_path(root)
    exports_dir.mkdir(parents=True, exist_ok=True)

    secondaries_in: list[export_helpers.SecondaryExport] = []
    for sv in stage.videos:
        if sv.role != "secondary" or sv.beep_time is None:
            continue
        sec_source = project.resolve_video_path(root, sv.path)
        secondaries_in.append(
            export_helpers.SecondaryExport(
                video_id=sv.video_id,
                source_path=sec_source,
                beep_time_in_source=sv.beep_time,
                label=f"Cam {sv.video_id}",
            )
        )

    engine_stage = _engine_stage_data(stage)

    request = export_helpers.StageExportRequest(
        stage_number=stage_number,
        write_trim=write_trim,
        write_csv=write_csv,
        write_fcpxml=write_fcpxml,
        write_report=write_report,
        write_overlay=write_overlay,
        overlay_codec=overlay_codec,
        overlay_max_height=overlay_max_height,
        overlay_max_fps=overlay_max_fps,
        overlay_theme=overlay_theme,
    )
    result = export_helpers.export_stage(
        request=request,
        audit_path=audit_path,
        exports_dir=exports_dir,
        source_video_path=source if source.exists() else None,
        stage_data=engine_stage,
        beep_time_in_source=primary.beep_time,
        pre_buffer_seconds=project.trim_pre_buffer_seconds,
        post_buffer_seconds=project.trim_post_buffer_seconds,
        config=Config(),
        secondaries=secondaries_in,
    )

    return {
        "stage_number": result.stage_number,
        "trimmed_video_path": _path_or_none(result.trimmed_video_path),
        "csv_path": _path_or_none(result.csv_path),
        "fcpxml_path": _path_or_none(result.fcpxml_path),
        "report_path": _path_or_none(result.report_path),
        "overlay_path": _path_or_none(result.overlay_path),
        "shots_written": result.shots_written,
        "anomalies": list(result.anomalies),
        "secondary_trimmed_paths": {vid: str(p) for vid, p in result.secondary_trimmed_paths.items()},
    }


def export_match_tool(
    project_root: str,
    *,
    stage_numbers: list[int],
    head_pad_seconds: float = 5.0,
    tail_pad_seconds: float = 5.0,
    include_secondaries: bool = True,
    include_overlay: bool = True,
    project_name: str | None = None,
    pip_layout: Literal["stacked", "pip-corners"] = "stacked",
    output_format: Literal["fcpxml", "fcp7xml", "mp4"] = "fcpxml",
    transition_kind: Literal["none", "zoom", "static"] = "none",
    transition_duration_seconds: float = 0.5,
    title_kind: Literal["none", "slate", "lower-third"] = "none",
    title_duration_seconds: float = 1.5,
    intro_path: str | None = None,
    outro_path: str | None = None,
    youtube_sidecar: bool = False,
    youtube_preset: bool = False,
) -> dict[str, Any]:
    """Stitch N stages into one match-level export.

    Mirror of ``POST /api/match/export``. Composes from each stage's
    already-written per-stage export (lossless trim + audit JSON +
    optional overlay). Run ``export_stage`` for every stage in
    ``stage_numbers`` first; this tool errors with ``MatchExportError``
    when any required artefact is missing.

    The ``head_pad_seconds`` / ``tail_pad_seconds`` are the visible
    padding around the beep / final shot per stage and must be in
    ``[0.0, project.trim_pre_buffer_seconds]`` /
    ``[0.0, project.trim_post_buffer_seconds]`` -- exceeding the cap
    raises ``ValueError``.

    Output format: ``fcpxml`` (Final Cut Pro 1.10, default), ``fcp7xml``
    (Premiere Pro / DaVinci Resolve), or ``mp4`` (single rendered
    file, no NLE needed). The ``youtube_sidecar`` flag writes a
    YouTube-shaped JSON + per-shot SRT alongside; ``youtube_preset``
    is only meaningful for ``output_format="mp4"``.

    Returns ``{output_path, stage_count, duration_seconds, anomalies}``.
    Anomalies are non-fatal warnings the engine surfaced (e.g. a
    stage exported without shots, an intro file missing).
    """
    if not stage_numbers:
        raise ValueError("stage_numbers cannot be empty")
    root = resolve_project_root(project_root)
    project = MatchProject.load(root)

    max_head = project.trim_pre_buffer_seconds
    max_tail = project.trim_post_buffer_seconds
    if not 0.0 <= head_pad_seconds <= max_head:
        raise ValueError(
            f"head_pad_seconds={head_pad_seconds} out of range; "
            f"must be in [0.0, {max_head}] (project trim_pre_buffer)"
        )
    if not 0.0 <= tail_pad_seconds <= max_tail:
        raise ValueError(
            f"tail_pad_seconds={tail_pad_seconds} out of range; "
            f"must be in [0.0, {max_tail}] (project trim_post_buffer)"
        )

    exports_dir = project.exports_path(root)
    audit_dir = project.audit_path(root)
    stages_input: list[match_export_helpers.MatchStageInput] = []
    for stage_number in stage_numbers:
        try:
            stage = project.stage(stage_number)
        except KeyError as exc:
            raise ValueError(f"stage {stage_number} not found in project") from exc
        primary = next((v for v in stage.videos if v.role == "primary"), None)
        if primary is None or primary.beep_time is None:
            raise ValueError(
                f"stage {stage_number} has no primary or no beep yet; "
                "finish ingest + audit before match export"
            )
        base = f"stage{stage_number}_{export_helpers._slugify(stage.stage_name)}"
        trimmed_path = exports_dir / f"{base}_trimmed.mp4"
        if not trimmed_path.exists():
            raise FileNotFoundError(
                f"stage {stage_number}: lossless trim missing at "
                f"{trimmed_path} -- call export_stage first"
            )
        audit_path = audit_dir / f"stage{stage_number}.json"
        if not audit_path.exists():
            raise FileNotFoundError(f"stage {stage_number}: audit JSON missing at {audit_path}")
        primary_clip_beep = min(primary.beep_time, project.trim_pre_buffer_seconds)
        secondaries: list[match_export_helpers.MatchSecondaryInput] = []
        if include_secondaries:
            for sv in stage.videos:
                if sv.role != "secondary" or sv.beep_time is None:
                    continue
                sec_trimmed = exports_dir / f"{base}_cam_{sv.video_id}_trimmed.mp4"
                if not sec_trimmed.exists():
                    raise FileNotFoundError(
                        f"stage {stage_number}: secondary trim missing at "
                        f"{sec_trimmed} -- run export_stage with the "
                        "secondary registered + beep_time set"
                    )
                sec_clip_beep = min(sv.beep_time, project.trim_pre_buffer_seconds)
                secondaries.append(
                    match_export_helpers.MatchSecondaryInput(
                        video_id=sv.video_id,
                        trimmed_path=sec_trimmed,
                        beep_offset_seconds=sec_clip_beep,
                        label=f"Cam {sv.video_id}",
                    )
                )
        overlay_path: Path | None = None
        if include_overlay:
            candidate = exports_dir / f"{base}_overlay.mov"
            if candidate.exists():
                overlay_path = candidate
        stages_input.append(
            match_export_helpers.MatchStageInput(
                stage_number=stage_number,
                stage_name=stage.stage_name,
                audit_path=audit_path,
                trimmed_path=trimmed_path,
                beep_offset_seconds=primary_clip_beep,
                secondaries=tuple(secondaries),
                overlay_path=overlay_path,
            )
        )

    request_data = match_export_helpers.MatchExportRequestData(
        stage_numbers=tuple(stage_numbers),
        head_pad_seconds=head_pad_seconds,
        tail_pad_seconds=tail_pad_seconds,
        include_secondaries=include_secondaries,
        include_overlay=include_overlay,
        project_name=project_name or project.name or "match",
        pip_layout=pip_layout,
        output_format=output_format,
        transition_kind=transition_kind,
        transition_duration_seconds=transition_duration_seconds,
        title_kind=title_kind,
        title_duration_seconds=title_duration_seconds,
        intro_path=Path(intro_path).expanduser() if intro_path else None,
        outro_path=Path(outro_path).expanduser() if outro_path else None,
        youtube_sidecar=youtube_sidecar,
        youtube_preset=youtube_preset,
    )
    result = match_export_helpers.export_match(
        stages=stages_input,
        request=request_data,
        exports_dir=exports_dir,
        config=Config().output,
    )
    return {
        "output_path": str(result.fcpxml_path),
        "stage_count": result.stage_count,
        "duration_seconds": result.duration_seconds,
        "anomalies": list(result.anomalies),
    }


def _path_or_none(p: Path | None) -> str | None:
    return str(p) if p is not None else None
