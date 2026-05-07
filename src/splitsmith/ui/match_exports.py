"""Match-level export pipeline: stitch N stages into one FCPXML (issue #171).

Companion to :mod:`splitsmith.ui.exports`. Composes from already-existing
per-stage trims (lossless ``stage<N>_<slug>_trimmed.mp4`` + per-cam variants
+ optional overlay) by walking
:func:`splitsmith.fcpxml_gen.generate_match_fcpxml`. Never re-encodes;
shrinking head/tail pads is done at FCPXML level.

The orchestrator deliberately does not touch ``Project`` state -- the
endpoint adapter assembles the per-stage bundles from project state and
hands them in, mirroring the way :func:`exports.export_stage` is wired.
That keeps the unit tests free of project fixtures.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .. import composition, fcpxml_gen
from ..config import OutputConfig
from .exports import audit_shots_to_engine_shots

PipLayout = Literal["stacked", "pip-corners"]


@dataclass(frozen=True)
class MatchSecondaryInput:
    """One secondary cam ready to ride a stage in the match export."""

    video_id: str
    trimmed_path: Path
    beep_offset_seconds: float
    label: str = "Secondary cam"


@dataclass(frozen=True)
class MatchStageInput:
    """Inputs the orchestrator needs to build one stage's contribution.

    All paths point at *already-trimmed* artefacts under ``exports/``.
    ``beep_offset_seconds`` is the clip-local beep time inside the lossless
    trim (typically equals ``trim_pre_buffer_seconds`` unless the cam beep
    landed within the pre-buffer of the source).
    """

    stage_number: int
    stage_name: str
    audit_path: Path
    trimmed_path: Path
    beep_offset_seconds: float
    secondaries: tuple[MatchSecondaryInput, ...] = ()
    overlay_path: Path | None = None


@dataclass(frozen=True)
class MatchExportRequestData:
    """Container for the validated request body, decoupled from FastAPI.

    The HTTP request model in :mod:`server` populates this; the
    orchestrator below depends only on this dataclass so unit tests don't
    need to construct Pydantic models.
    """

    stage_numbers: tuple[int, ...]
    head_pad_seconds: float
    tail_pad_seconds: float
    include_secondaries: bool
    include_overlay: bool
    project_name: str
    # Issue #193. ``"stacked"`` keeps today's full-frame stacked layout
    # (every secondary covers the one below). ``"pip-corners"`` adds an
    # ``<adjust-transform>`` to each secondary so they land in rotating
    # corners (TR -> TL -> BR -> BL) at 25% scale with a 2% inset.
    pip_layout: PipLayout = "stacked"


@dataclass(frozen=True)
class MatchExportResult:
    fcpxml_path: Path
    stage_count: int
    duration_seconds: float
    anomalies: list[str] = field(default_factory=list)


class MatchExportError(RuntimeError):
    """Raised when one of the selected stages is missing the artefacts the
    composer needs (no trim, no audit, etc). Endpoints surface as 400."""


def export_match(
    *,
    stages: list[MatchStageInput],
    request: MatchExportRequestData,
    exports_dir: Path,
    config: OutputConfig,
    probe: object | None = None,
) -> MatchExportResult:
    """Build a stitched FCPXML from N stages' existing trims.

    Probes each trim (and each enabled secondary / overlay), loads the audit
    JSON for shots, then calls :func:`fcpxml_gen.generate_match_fcpxml`.
    Returns the output path + stage count + total timeline duration so the
    caller (HTTP endpoint or CLI) can confirm what was written.

    ``probe`` defaults to :func:`fcpxml_gen.probe_video` resolved at call
    time so monkeypatching the module attribute (the standard pattern for
    avoiding ffprobe in tests) works. Pass an explicit callable to override.
    """
    if probe is None:
        probe = fcpxml_gen.probe_video
    if not stages:
        raise MatchExportError("at least one stage required")

    compositions: list[fcpxml_gen.StageComposition] = []
    anomalies: list[str] = []
    for stage_input in stages:
        if not stage_input.trimmed_path.exists():
            raise MatchExportError(
                f"stage {stage_input.stage_number}: lossless trim missing at "
                f"{stage_input.trimmed_path} -- run the per-stage export first"
            )
        if not stage_input.audit_path.exists():
            raise MatchExportError(
                f"stage {stage_input.stage_number}: audit JSON missing at "
                f"{stage_input.audit_path}"
            )
        try:
            audit_data = json.loads(stage_input.audit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MatchExportError(
                f"stage {stage_input.stage_number}: failed to read audit JSON: {exc}"
            ) from exc
        # Convert audit -> engine Shot. We need ``time_absolute`` against the
        # source, but the FCPXML composer only reads ``time_from_beep``, so
        # any beep_time_in_source value works here -- we pick 0.0 so the
        # ``time_absolute`` column stays trivially defined.
        shots = audit_shots_to_engine_shots(audit_data, beep_time_in_source=0.0)
        if not shots:
            raise MatchExportError(f"stage {stage_input.stage_number}: audit JSON has no shots")

        try:
            primary_meta = probe(stage_input.trimmed_path)  # type: ignore[operator]
        except fcpxml_gen.FFprobeError as exc:
            raise MatchExportError(
                f"stage {stage_input.stage_number}: ffprobe failed on "
                f"{stage_input.trimmed_path}: {exc}"
            ) from exc

        secondaries: list[fcpxml_gen.SecondaryClip] = []
        if request.include_secondaries:
            for sec in stage_input.secondaries:
                if not sec.trimmed_path.exists():
                    anomalies.append(
                        f"stage {stage_input.stage_number}: cam {sec.video_id} trim "
                        f"missing at {sec.trimmed_path} -- dropped"
                    )
                    continue
                try:
                    sec_meta = probe(sec.trimmed_path)  # type: ignore[operator]
                except fcpxml_gen.FFprobeError as exc:
                    anomalies.append(
                        f"stage {stage_input.stage_number}: cam {sec.video_id} dropped: {exc}"
                    )
                    continue
                secondaries.append(
                    fcpxml_gen.SecondaryClip(
                        video_path=sec.trimmed_path,
                        video=sec_meta,
                        beep_offset_seconds=sec.beep_offset_seconds,
                        label=sec.label,
                    )
                )

        overlay_path: Path | None = None
        overlay_video = None
        if request.include_overlay and stage_input.overlay_path is not None:
            if stage_input.overlay_path.exists():
                try:
                    overlay_video = probe(stage_input.overlay_path)  # type: ignore[operator]
                    overlay_path = stage_input.overlay_path
                except fcpxml_gen.FFprobeError as exc:
                    anomalies.append(f"stage {stage_input.stage_number}: overlay dropped: {exc}")
            else:
                anomalies.append(
                    f"stage {stage_input.stage_number}: overlay missing at "
                    f"{stage_input.overlay_path} -- dropped"
                )

        if request.pip_layout == "pip-corners" and secondaries:
            laid_out = fcpxml_gen.apply_pip_corner_cycle(
                secondaries, default=fcpxml_gen.PipPlacement()
            )
        else:
            laid_out = tuple(secondaries)

        compositions.append(
            fcpxml_gen.StageComposition(
                stage_name=stage_input.stage_name,
                video_path=stage_input.trimmed_path,
                video=primary_meta,
                shots=shots,
                beep_offset_seconds=stage_input.beep_offset_seconds,
                head_pad_seconds=request.head_pad_seconds,
                tail_pad_seconds=request.tail_pad_seconds,
                overlay_path=overlay_path,
                overlay_video=overlay_video,
                secondaries=laid_out,
            )
        )

    exports_dir.mkdir(parents=True, exist_ok=True)
    output_path = exports_dir / f"{_slugify(request.project_name)}-match.fcpxml"
    # Match export goes through the composition IR (issue #194). The bridge
    # renderer lowers back to ``generate_match_fcpxml`` so the output stays
    # byte-identical to the pre-IR path; future renderer work (transitions,
    # titles, FCP7 XML, ffmpeg) replaces the bridge piece by piece.
    comp = composition.from_stage_compositions(compositions, project_name=request.project_name)
    try:
        composition.render_fcpxml(comp, output_path=output_path, config=config)
    except (ValueError, FileNotFoundError) as exc:
        raise MatchExportError(str(exc)) from exc

    # Compute total duration for the response. Mirrors the composer's math
    # (head_avail / tail_avail per stage, frame-aligned). Cheaper than
    # re-parsing the FCPXML and good enough for a status line.
    total_seconds = 0.0
    for comp in compositions:
        head_avail = max(0.0, comp.beep_offset_seconds)
        if comp.shots:
            last_local = comp.beep_offset_seconds + max(s.time_from_beep for s in comp.shots)
        else:
            last_local = comp.beep_offset_seconds
        tail_avail = max(0.0, comp.video.duration_seconds - last_local)
        head_trim = max(0.0, head_avail - comp.head_pad_seconds)
        tail_trim = max(0.0, tail_avail - comp.tail_pad_seconds)
        total_seconds += max(0.0, comp.video.duration_seconds - head_trim - tail_trim)

    return MatchExportResult(
        fcpxml_path=output_path,
        stage_count=len(compositions),
        duration_seconds=total_seconds,
        anomalies=anomalies,
    )


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Filesystem-friendly slug. Mirrors ``exports._slugify`` so match-export
    filenames look the same as their per-stage cousins."""
    return _SLUG_RE.sub("-", name.lower()).strip("-") or "match"
