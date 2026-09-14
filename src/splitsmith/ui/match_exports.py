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

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .. import composition, fcp7xml_render, fcpxml_gen, mp4_render, youtube_sidecar
from ..audit_data import StageExportError, audit_shots_to_engine_shots, read_audit_data
from ..config import OutputConfig, StageRounds
from ..export_naming import match_file_base, stage_file_base
from ..match_project import MatchProject, StageScorecard
from ..overlay_theme import ThemeName
from ..stage_summary_data import TileStageData, load_stage_shots

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
# a connected text clip overlaid on the start of the primary. FCPXML
# emits them as Basic Title; MP4 draws them itself (issue #973); FCP7
# ignores the request until it grows title support.
TitleKind = Literal["none", "slate", "lower-third"]

#: Renderers with no path for a generated card or a user-supplied clip.
#: The request layer records an anomaly for each ignored feature rather
#: than letting the renderer drop it silently.
_RENDERERS_WITHOUT_TITLES: frozenset[OutputFormat] = frozenset({"fcp7xml"})
_RENDERERS_WITHOUT_SEGMENTS: frozenset[OutputFormat] = frozenset({"fcp7xml"})
_RENDERERS_WITHOUT_MATCH_CARDS: frozenset[OutputFormat] = frozenset({"fcpxml", "fcp7xml"})


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
    # Issue #973. The stage's expected round count when the scoreboard or
    # the audit knows it; a generated stage card prints it as an info
    # line. ``None`` prints nothing -- never a guess.
    expected_rounds: int | None = None
    # Issue #972. What the stage summary says beyond the audit's shots:
    # the scoreboard's scorecard and the stage time, straight off the
    # ``StageEntry``. Absent stays absent -- the summary draws less,
    # never a zero. ``stage_rounds`` is the full model ``expected_rounds``
    # is read from, for the summary's own data shape.
    scorecard: StageScorecard | None = None
    stage_time_seconds: float | None = None
    stage_time_is_manual: bool = False
    stage_rounds: StageRounds | None = None


def stage_inputs_for_project(
    project: MatchProject,
    root: Path,
    stage_numbers: Sequence[int],
) -> list[MatchStageInput]:
    """Assemble the per-stage inputs for ``export_match`` from a project's
    existing artefacts -- the one place the server job and the CLI verb
    share, so the two cannot drift on a file name or a beep offset.

    Reads nothing from disk and re-cuts nothing: the paths are what the
    per-stage exporter *would have* written, and ``export_match`` is what
    checks they exist. A stage whose primary has no beep is a caller
    error and raises ``ValueError``.
    """
    exports_dir = project.exports_path(root)
    audit_dir = project.audit_path(root)
    inputs: list[MatchStageInput] = []
    for stage_number in stage_numbers:
        stage = project.stage(stage_number)
        primary = stage.primary()
        if primary is None or primary.beep_time is None:
            raise ValueError(f"stage {stage_number}: no primary video with a confirmed beep")
        base = stage_file_base(stage_number, stage.stage_name)
        secondaries: list[MatchSecondaryInput] = []
        for video in stage.videos:
            if video.role != "secondary" or video.beep_time is None:
                continue
            secondaries.append(
                MatchSecondaryInput(
                    video_id=video.video_id,
                    trimmed_path=exports_dir / f"{base}_cam_{video.video_id}_trimmed.mp4",
                    # Clip-local beep: the pre-buffer, unless the beep sat
                    # inside it in the source.
                    beep_offset_seconds=min(project.trim_pre_buffer_seconds, video.beep_time),
                    label=f"Cam {video.video_id}",
                )
            )
        rounds = stage.stage_rounds.expected if stage.stage_rounds is not None else None
        inputs.append(
            MatchStageInput(
                stage_number=stage_number,
                stage_name=stage.stage_name,
                audit_path=audit_dir / f"stage{stage_number}.json",
                trimmed_path=exports_dir / f"{base}_trimmed.mp4",
                beep_offset_seconds=min(project.trim_pre_buffer_seconds, primary.beep_time),
                secondaries=tuple(secondaries),
                overlay_path=exports_dir / f"{base}_overlay.mov",
                expected_rounds=rounds,
                scorecard=stage.scorecard,
                # The model treats <=0 as unset: an untouched placeholder
                # stage carries 0.0, and a zero-second stage time is never real.
                stage_time_seconds=stage.time_seconds if stage.time_seconds > 0 else None,
                stage_time_is_manual=stage.time_seconds_manual,
                stage_rounds=stage.stage_rounds,
            )
        )
    return inputs


def title_info_lines(project: MatchProject, *, extra: str | None = None) -> tuple[str, ...]:
    """The info lines under the match name on a generated title page
    (issue #973): the match date, the shooter, then the caller's free
    text. Only what the project actually carries; a blank line is never
    printed."""
    lines: list[str] = []
    if project.match_date is not None:
        lines.append(project.match_date.isoformat())
    if project.competitor_name:
        lines.append(project.competitor_name)
    if extra and extra.strip():
        lines.append(extra.strip())
    return tuple(lines)


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
    # alongside the export, plus a per-shot ``.srt`` and chapters in
    # the output itself: markers in the FCPXML so they survive the NLE
    # round-trip, chapter atoms in the MP4. Off by default; enabling on
    # FCP7 writes the sidecar but embeds nothing (no title track).
    youtube_sidecar: bool = False
    # Free text the sidecar's description opens with, above the chapter
    # list: what the video is (division, camera, the day) in the
    # uploader's own words. The generated text alone is a chapter list.
    description_lead: str | None = None
    # Issue #204 layer 2. Encode the MP4 with YouTube's recommended
    # H.264 profile / GOP / colour / audio params. Only meaningful for
    # ``output_format == "mp4"`` -- gets surfaced as an anomaly when
    # set against a non-MP4 renderer.
    youtube_preset: bool = False
    # Issue #973. Generated cards, MP4 only (an anomaly elsewhere). The
    # title page is the match name over ``title_page_info`` -- assembled
    # by the caller from the project (see :func:`title_info_lines`) so
    # this dataclass stays a request and not a project reader. The
    # closing card repeats the title page: it is the data there is.
    title_page: bool = False
    title_page_info: tuple[str, ...] = ()
    title_page_duration_seconds: float = 3.0
    closing_card: bool = False
    # The overlay theme also styles the cards, so the two read as one.
    overlay_theme: ThemeName = "splitsmith"
    # Issue #972. Seconds to hold each stage's summary after its action
    # in the rendered MP4; 0 (the default) is off, as on the compare
    # grid. ``shooter_label`` is the name on the summary's identity row;
    # ``None`` falls back to the project name.
    summary_hold_seconds: float = 0.0
    shooter_label: str | None = None


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
        # One audit precondition for every surface (#619): absent means the
        # stage never ran shot detection, which is a legitimate state for a
        # trim-only export; unparseable is still a real fault. Re-raised as
        # ``MatchExportError`` so this module's callers keep getting the one
        # exception type they map to a 400.
        try:
            audit_data = read_audit_data(stage_input.audit_path)
        except StageExportError as exc:
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
                f"stage {stage_input.stage_number}: ffprobe failed on {stage_input.trimmed_path}: {exc}"
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
    output_path = exports_dir / f"{match_file_base(request.project_name)}{extension}"
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
    if titles and request.output_format in _RENDERERS_WITHOUT_TITLES:
        anomalies.append(
            f"titles ignored: not yet supported by the "
            f"{request.output_format} renderer (issue #196 follow-ups)"
        )
        titles = {}
    if titles and transitions and any(t.style == "slate" for t in titles.values()):
        # Mirror the emitter's guard at the request layer so the
        # response carries an explicit anomaly instead of a 500-shaped
        # error from generate_match_fcpxml.
        anomalies.append("slate titles dropped: cannot combine with transitions (issue #196)")
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
    # The stage summary hold (issue #972): only the MP4 renderer draws it.
    summaries: dict[int, composition.SummaryHold] = {}
    if request.summary_hold_seconds > 0:
        if request.output_format != "mp4":
            anomalies.append(
                f"summary hold ignored: only the mp4 renderer holds a stage summary "
                f"(current renderer: {request.output_format})"
            )
        else:
            label = request.shooter_label or request.project_name
            for idx, stage_input in enumerate(stages):
                summaries[idx] = composition.SummaryHold(
                    data=TileStageData(
                        label=label,
                        stage_number=stage_input.stage_number,
                        shots=load_stage_shots(stage_input.audit_path),
                        stage_time_seconds=stage_input.stage_time_seconds,
                        stage_time_is_manual=stage_input.stage_time_is_manual,
                        scorecard=stage_input.scorecard,
                        stage_rounds=stage_input.stage_rounds,
                    ),
                    label=label,
                    duration_seconds=request.summary_hold_seconds,
                )
    # Generated match cards (issue #973): only the MP4 renderer draws
    # them. Same text on both; the closing card repeats the title page.
    title_page: composition.MatchTitle | None = None
    closing: composition.MatchTitle | None = None
    if request.title_page or request.closing_card:
        if request.output_format in _RENDERERS_WITHOUT_MATCH_CARDS:
            for wanted, label in ((request.title_page, "title page"), (request.closing_card, "closing card")):
                if wanted:
                    anomalies.append(
                        f"{label} ignored: only the mp4 renderer draws generated cards "
                        f"(current renderer: {request.output_format})"
                    )
        else:
            card = composition.MatchTitle(
                text=request.project_name,
                info=request.title_page_info,
                duration_seconds=request.title_page_duration_seconds,
            )
            title_page = card if request.title_page else None
            closing = card if request.closing_card else None
    # Chapter markers in the output: only when the YouTube sidecar is
    # requested AND the renderer carries chapters -- FCPXML as markers
    # on the timeline, MP4 as chapter atoms in the file (#204 and its
    # follow-up). FCP 7 XML has nowhere to put them; with the sidecar
    # on we still write the JSON but embed nothing.
    embed_chapter_markers = request.youtube_sidecar and request.output_format in ("fcpxml", "mp4")
    comp = composition.from_stage_compositions(
        compositions,
        project_name=request.project_name,
        transitions=transitions,
        titles=titles,
        intro=intro_segment,
        outro=outro_segment,
        chapter_markers=embed_chapter_markers,
        title_page=title_page,
        closing=closing,
        summaries=summaries,
    )
    youtube_preset_active = request.youtube_preset and request.output_format == "mp4"
    if request.youtube_preset and request.output_format != "mp4":
        anomalies.append(
            f"youtube encode preset ignored: only the mp4 renderer "
            f"applies it (current renderer: {request.output_format})"
        )
    # The MP4 renderer reports the timeline it actually wrote -- footage
    # plus every card and clip that made it in -- and what it skipped.
    rendered_seconds: float | None = None
    try:
        if request.output_format == "fcpxml":
            composition.render_fcpxml(comp, output_path=output_path, config=config)
        elif request.output_format == "fcp7xml":
            fcp7xml_render.render_fcp7xml(comp, output_path=output_path)
        else:
            rendered = mp4_render.render_mp4(
                comp,
                output_path=output_path,
                youtube_preset=youtube_preset_active,
                overlay_theme=request.overlay_theme,
                chapters=youtube_sidecar.compute_chapters(comp) if embed_chapter_markers else None,
            )
            rendered_seconds = rendered.duration_seconds
            anomalies.extend(rendered.degradations)
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
            description_lead=(request.description_lead or "").strip() or None,
            captions_path=srt_path.relative_to(exports_dir),
            output_video=output_path.relative_to(exports_dir),
        )
        youtube_sidecar.write_sidecar(sidecar, sidecar_path)
        if not embed_chapter_markers:
            anomalies.append(
                "youtube chapters written to the sidecar description only: "
                f"the {request.output_format} renderer carries no chapter markers"
            )

    # Compute total duration for the response. Mirrors the composer's math
    # (head_avail / tail_avail per stage, frame-aligned). Cheaper than
    # re-parsing the FCPXML and good enough for a status line. The MP4
    # path already knows its timeline (cards and clips included), so it
    # reports that instead.
    if rendered_seconds is not None:
        return MatchExportResult(
            fcpxml_path=output_path,
            stage_count=len(compositions),
            duration_seconds=rendered_seconds,
            anomalies=anomalies,
        )
    total_seconds = 0.0
    for stage_comp in compositions:
        head_avail = max(0.0, stage_comp.beep_offset_seconds)
        if stage_comp.shots:
            last_local = stage_comp.beep_offset_seconds + max(s.time_from_beep for s in stage_comp.shots)
        else:
            last_local = stage_comp.beep_offset_seconds
        tail_avail = max(0.0, stage_comp.video.duration_seconds - last_local)
        head_trim = max(0.0, head_avail - stage_comp.head_pad_seconds)
        tail_trim = max(0.0, tail_avail - stage_comp.tail_pad_seconds)
        total_seconds += max(0.0, stage_comp.video.duration_seconds - head_trim - tail_trim)

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
    if renderer in _RENDERERS_WITHOUT_SEGMENTS:
        anomalies.append(
            f"{label} ignored: not yet supported by the {renderer} renderer (issue #173 follow-ups)"
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
    will let users customise this in #198. The round count, when known,
    rides along as an info line for the renderers that draw one (#973).
    """
    if kind == "none":
        return {}
    return {
        idx: composition.TitleCard(
            text=stage_input.stage_name,
            duration_seconds=duration,
            style=kind,
            info=(f"{stage_input.expected_rounds} rounds",) if stage_input.expected_rounds else (),
        )
        for idx, stage_input in enumerate(stage_inputs)
    }
