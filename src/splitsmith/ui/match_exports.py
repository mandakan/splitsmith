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

from .. import composition, fcp7xml_render, fcpxml_gen, mp4_render, youtube_sidecar
from ..config import OutputConfig
from .exports import audit_shots_to_engine_shots

PipLayout = Literal["stacked", "pip-corners"]
# Issue #197. ``"fcpxml"`` writes a Final Cut Pro 1.10 timeline (current
# default). ``"fcp7xml"`` writes a Final Cut Pro 7-style xmeml file
# importable into Premiere Pro and DaVinci Resolve. Issue #174.
# ``"mp4"`` bakes the composition into a stitched MP4 via ffmpeg --
# overlays / PiP burned in, no NLE round-trip needed.
OutputFormat = Literal["fcpxml", "fcp7xml", "mp4"]
# Issue #195. ``"none"`` keeps today's hard-cut stitching.
# ``"zoom"`` / ``"static"`` map to FCP's built-in .motr transition
# templates (Blurs/Zoom and Lights/Static -- the variants whose UID
# resolves cleanly in FCP 12.x). The user can swap to a related
# variant in FCP after import. Only the FCPXML renderer emits
# transitions today; FCP7 / MP4 ignore the request until they grow
# transition support.
TransitionKind = Literal["none", "zoom", "static"]
# Issue #196. ``"none"`` keeps today's title-less stitching.
# ``"slate"`` adds a pre-stage card on the spine; ``"lower-third"`` is
# a connected text clip overlaid on the start of the primary. Only
# the FCPXML renderer emits titles today; FCP7 / MP4 ignore the
# request until they grow title support.
TitleKind = Literal["none", "slate", "lower-third"]


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
    # corners (BL -> BR -> TR -> TL, counter-clockwise from bottom-left
    # so the 1-cam case stays clear of the overlay's top-corner widgets)
    # at 30% scale with a 2% inset.
    pip_layout: PipLayout = "stacked"
    # Issue #197. Renderer chosen for this export.
    output_format: OutputFormat = "fcpxml"
    # Issue #195. Uniform transition between every consecutive stage
    # (or ``"none"`` for hard cuts). ``transition_duration_seconds`` is
    # ignored when ``transition_kind == "none"``.
    transition_kind: TransitionKind = "none"
    transition_duration_seconds: float = 0.5
    # Issue #196. Per-stage titles. Text defaults to the stage name;
    # ``title_duration_seconds`` is uniform across stages. ``"none"``
    # keeps the timeline title-less.
    title_kind: TitleKind = "none"
    title_duration_seconds: float = 1.5
    # Issue #173. Optional intro / outro video clips placed before
    # stage 0 / after stage N-1. Frame rate must match the timeline;
    # the path must exist on disk. ``None`` keeps the export
    # stage-only (today's behaviour).
    intro_path: Path | None = None
    outro_path: Path | None = None
    # Issue #204 layer 1. Generate a YouTube-shaped JSON sidecar
    # alongside the export, plus a per-shot ``.srt`` and chapter
    # markers in the FCPXML so they survive the NLE round-trip into
    # an MP4 chapter atom. Off by default; enabling on FCP7 / MP4
    # writes the sidecar but no chapter markers (those renderers
    # don't carry chapters yet).
    youtube_sidecar: bool = False
    # Issue #204 layer 2. Encode the MP4 with YouTube's recommended
    # H.264 profile / GOP / colour / audio params. Only meaningful for
    # ``output_format == "mp4"`` -- gets surfaced as an anomaly when
    # set against a non-MP4 renderer.
    youtube_preset: bool = False


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
                f"stage {stage_input.stage_number}: audit JSON missing at " f"{stage_input.audit_path}"
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
        # Empty ``shots[]`` is permissive (#214): the stage still rides
        # the spine as a trim-only segment. No shot markers, no chapter
        # markers, no overlay -- those depend on shots. Surface an
        # anomaly so the user sees which stages are exporting bare.
        if not shots:
            anomalies.append(
                f"stage {stage_input.stage_number}: no shots audited -- "
                f"exported without shot markers / chapters / overlay"
            )

        try:
            primary_meta = probe(stage_input.trimmed_path)  # type: ignore[operator]
        except fcpxml_gen.FFprobeError as exc:
            raise MatchExportError(
                f"stage {stage_input.stage_number}: ffprobe failed on " f"{stage_input.trimmed_path}: {exc}"
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
                    anomalies.append(f"stage {stage_input.stage_number}: cam {sec.video_id} dropped: {exc}")
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
        if request.include_overlay and not shots:
            # Shotless stages can't have an overlay -- it annotates shot
            # times. The shotless anomaly above already signals it; don't
            # double up unless the user explicitly opted in. (#217)
            pass
        elif request.include_overlay and stage_input.overlay_path is None:
            # Overlay was requested but never rendered for this stage.
            # Surface explicitly so the user knows why the timeline is bare.
            anomalies.append(
                f"stage {stage_input.stage_number}: overlay not available -- "
                f"run the per-stage Generate with the Overlay toggle enabled"
            )
        elif request.include_overlay and stage_input.overlay_path is not None:
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
            laid_out = fcpxml_gen.apply_pip_corner_cycle(secondaries, default=fcpxml_gen.PipPlacement())
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
    extension = _OUTPUT_EXTENSIONS[request.output_format]
    output_path = exports_dir / f"{_slugify(request.project_name)}-match{extension}"
    # Match export goes through the composition IR (issue #194). The bridge
    # renderer lowers back to ``generate_match_fcpxml`` for the FCPXML path
    # so output stays byte-identical to the pre-IR emitter when no extra
    # IR features are present; the FCP7 XML path (issue #197) walks the
    # IR directly. The MP4 path (#174) shells out to ffmpeg per stage,
    # then concat-demuxes the temps.
    transitions = _build_uniform_transitions(
        kind=request.transition_kind,
        duration=request.transition_duration_seconds,
        stage_count=len(compositions),
    )
    if transitions and request.output_format != "fcpxml":
        anomalies.append(
            f"transitions ignored: not yet supported by the "
            f"{request.output_format} renderer (issue #195 follow-ups)"
        )
        transitions = ()
    titles = _build_uniform_titles(
        kind=request.title_kind,
        duration=request.title_duration_seconds,
        stage_inputs=stages,
    )
    if titles and request.output_format != "fcpxml":
        anomalies.append(
            f"titles ignored: not yet supported by the "
            f"{request.output_format} renderer (issue #196 follow-ups)"
        )
        titles = {}
    if titles and transitions and any(t.style == "slate" for t in titles.values()):
        # Mirror the emitter's guard at the request layer so the
        # response carries an explicit anomaly instead of a 500-shaped
        # error from generate_match_fcpxml.
        anomalies.append("slate titles dropped: cannot combine with transitions " "(issue #196)")
        titles = {}
    intro_segment = _resolve_segment(
        request.intro_path,
        label="intro",
        probe=probe,
        anomalies=anomalies,
        renderer=request.output_format,
    )
    outro_segment = _resolve_segment(
        request.outro_path,
        label="outro",
        probe=probe,
        anomalies=anomalies,
        renderer=request.output_format,
    )
    # Chapter markers in the FCPXML output: only useful when YouTube
    # sidecar is requested AND the renderer actually carries chapter
    # markers (FCPXML today; FCP7 / MP4 follow-ups). When the user
    # picks a non-FCPXML format with sidecar on, we still write the
    # JSON sidecar but skip embedding chapters.
    embed_chapter_markers = request.youtube_sidecar and request.output_format == "fcpxml"
    comp = composition.from_stage_compositions(
        compositions,
        project_name=request.project_name,
        transitions=transitions,
        titles=titles,
        intro=intro_segment,
        outro=outro_segment,
        chapter_markers=embed_chapter_markers,
    )
    youtube_preset_active = request.youtube_preset and request.output_format == "mp4"
    if request.youtube_preset and request.output_format != "mp4":
        anomalies.append(
            f"youtube encode preset ignored: only the mp4 renderer "
            f"applies it (current renderer: {request.output_format})"
        )
    try:
        if request.output_format == "fcpxml":
            composition.render_fcpxml(comp, output_path=output_path, config=config)
        elif request.output_format == "fcp7xml":
            fcp7xml_render.render_fcp7xml(comp, output_path=output_path)
        else:
            mp4_render.render_mp4(
                comp,
                output_path=output_path,
                youtube_preset=youtube_preset_active,
            )
    except (ValueError, FileNotFoundError, mp4_render.FFmpegError) as exc:
        raise MatchExportError(str(exc)) from exc

    # YouTube sidecar (#204 layer 1). Walks the same IR the renderer
    # consumed; carries chapter timestamps + tags + a captions .srt.
    # Renderer-agnostic so the user can route to FCPXML / FCP7 / MP4
    # and still get the upload-ready text fields.
    if request.youtube_sidecar:
        srt_path = output_path.with_suffix(".srt")
        sidecar_path = output_path.with_name(output_path.stem + "-youtube.json")
        youtube_sidecar.write_srt(comp, srt_path)
        sidecar = youtube_sidecar.build_sidecar(
            comp,
            captions_path=srt_path.relative_to(exports_dir),
            output_video=output_path.relative_to(exports_dir),
        )
        youtube_sidecar.write_sidecar(sidecar, sidecar_path)
        if request.output_format != "fcpxml":
            anomalies.append(
                "youtube chapter markers embedded only on FCPXML "
                "(FCP7 / MP4 chapter atoms are #204 follow-ups)"
            )

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


_OUTPUT_EXTENSIONS: dict[OutputFormat, str] = {
    "fcpxml": ".fcpxml",
    "fcp7xml": ".xml",
    "mp4": ".mp4",
}


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Filesystem-friendly slug. Mirrors ``exports._slugify`` so match-export
    filenames look the same as their per-stage cousins."""
    return _SLUG_RE.sub("-", name.lower()).strip("-") or "match"


def _build_uniform_transitions(
    *,
    kind: TransitionKind,
    duration: float,
    stage_count: int,
) -> tuple[composition.Transition, ...]:
    """Expand a single ``(kind, duration)`` choice into N-1 transitions
    (one between each consecutive stage pair). Returns ``()`` for the
    no-op cases (kind == ``"none"`` or fewer than two stages)."""
    if kind == "none" or stage_count < 2:
        return ()
    return tuple(
        composition.Transition(
            from_stage_index=i,
            to_stage_index=i + 1,
            kind=kind,
            duration_seconds=duration,
        )
        for i in range(stage_count - 1)
    )


def _resolve_segment(
    path: Path | None,
    *,
    label: str,
    probe: object,
    anomalies: list[str],
    renderer: OutputFormat,
) -> composition.Segment | None:
    """Probe an intro/outro path into an IR ``Segment``.

    Missing files surface as anomalies (not hard errors) so the export
    proceeds without the segment -- mirrors the overlay/secondary
    handling. Mismatched renderer / fps issues become anomalies too.
    """
    if path is None:
        return None
    if renderer != "fcpxml":
        anomalies.append(
            f"{label} ignored: not yet supported by the " f"{renderer} renderer (issue #173 follow-ups)"
        )
        return None
    if not path.exists():
        anomalies.append(f"{label} dropped: video missing at {path}")
        return None
    try:
        meta = probe(path)  # type: ignore[operator]
    except fcpxml_gen.FFprobeError as exc:
        anomalies.append(f"{label} dropped: {exc}")
        return None
    return composition.Segment(
        asset=composition.Asset(path=path, metadata=meta),
        name=label.capitalize(),
    )


def _build_uniform_titles(
    *,
    kind: TitleKind,
    duration: float,
    stage_inputs: list[MatchStageInput],
) -> dict[int, composition.TitleCard]:
    """Expand a single ``(kind, duration)`` into one ``TitleCard`` per
    stage. Each title's text defaults to the stage name -- templating
    will let users customise this in #198."""
    if kind == "none":
        return {}
    return {
        idx: composition.TitleCard(
            text=stage_input.stage_name,
            duration_seconds=duration,
            style=kind,
        )
        for idx, stage_input in enumerate(stage_inputs)
    }
