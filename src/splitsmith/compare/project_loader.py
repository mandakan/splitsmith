"""Load per-stage trim metadata from a shooter -- legacy project or merged Match."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .. import camera_select, fcpxml_gen, raw_link
from ..export_naming import stage_file_base
from ..fcpxml_gen import VideoMetadata
from ..match_model import Match
from ..match_project import MatchProject, StageEntry, StageVideo
from ..trim import audit_trim_matches, audit_trim_name, audit_trim_params

ProbeFn = Callable[[Path], VideoMetadata]

#: Where a tile's footage came from (#1681): the shooter's exported trim;
#: the full-resolution audit trim on this disk when no export exists
#: (local, desktop, CLI); or a stage trim the hosted grid job cut from the
#: source itself, the export's own window and stream copy.
TileSource = Literal["export", "audit", "source"]

#: Hosted: the stage trim for this stage and camera, cut from the source
#: (the render job) or only planned (a request validating, which must not
#: download), or ``None`` when it cannot be made: no confirmed beep, no
#: stage time, or no source.
SourceCut = Callable[[StageEntry, StageVideo], Path | None]


@dataclass(frozen=True)
class InsetClip:
    """A second camera small in a corner of the tile (2026-10-02)."""

    trim_path: Path
    beep_offset_in_clip: float
    duration_seconds: float


@dataclass(frozen=True)
class CompareStageBundle:
    """All the per-stage facts the emitter needs from one shooter."""

    stage_number: int
    stage_name: str
    trim_path: Path
    audit_path: Path
    beep_offset_in_clip: float
    duration_seconds: float
    width: int
    height: int
    frame_rate_num: int
    frame_rate_den: int
    #: Mount of the camera that produced this tile, when tagged. Reporting only.
    camera_mount: str | None = None
    #: Another of the shooter's cameras inset in the tile's corner; ``None``
    #: when no inset was asked for or this stage has no such camera.
    inset: InsetClip | None = None
    #: True when the requested camera was unavailable on this stage and the
    #: primary stood in. Surfaced in the run summary and the FCPXML marker.
    substituted: bool = False
    #: Where the tile's footage came from (see :data:`TileSource`). Anything
    #: but ``"export"`` stood in for a missing exported trim (#1681): same
    #: window and beep anchor. Surfaced in the grid's degradations and the
    #: FCPXML marker.
    trim_source: TileSource = "export"

    @property
    def metadata(self) -> VideoMetadata:
        return VideoMetadata(
            width=self.width,
            height=self.height,
            duration_seconds=self.duration_seconds,
            frame_rate_num=self.frame_rate_num,
            frame_rate_den=self.frame_rate_den,
        )


@dataclass(frozen=True)
class MissingTrim:
    """A stage dropped from the grid because it has no trim to cut from:
    no exported trim, and no audit trim current for its beep and stage time.

    The grid renders a black filler tile for it. That is the right output
    -- the footage genuinely isn't there -- but it is indistinguishable
    from "this shooter didn't shoot the stage", so the loader records what
    it looked for and the CLI says so out loud (#618). Reporting only;
    nothing downstream reads it.
    """

    stage_number: int
    stage_name: str
    expected_path: Path
    #: The selector that chose this camera, when the run asked for one.
    #: ``None`` means the primary, where a missing trim just means the
    #: stage was never exported.
    camera: str | None = None


@dataclass(frozen=True)
class CompareShooterBundle:
    """A shooter's project + the per-stage bundles ready for export.

    ``project`` is the shooter's :class:`MatchProject` as the loader read
    it (or was handed it, hosted). ``None`` only on a bundle built by
    hand. The emitter only reads ``label`` and ``stages_by_number``; the
    overlay and the title page's division lines read the project.
    """

    label: str
    project_root: Path
    project: MatchProject | None = None
    stages_by_number: dict[int, CompareStageBundle] = field(default_factory=dict)
    #: Stages omitted because their trim was missing. See :class:`MissingTrim`.
    missing_trims: list[MissingTrim] = field(default_factory=list)


def trim_path_for_video(
    project: MatchProject,
    project_root: Path,
    stage_number: int,
    stage_name: str,
    video: StageVideo | None,
) -> Path:
    """Path the exporter writes for ``video`` on this stage.

    Primaries land at ``stage<N>_<slug>_trimmed.mp4``; every other camera
    at ``stage<N>_<slug>_cam_<video_id>_trimmed.mp4``. Mirrors
    ``exports.export_stage``. ``video=None`` means the primary, same
    convention as :func:`splitsmith.camera_select.resolve_camera`.
    """
    base = stage_file_base(stage_number, stage_name)
    exports = project.exports_path(project_root)
    if video is None or video.role == "primary":
        return exports / f"{base}_trimmed.mp4"
    return exports / f"{base}_cam_{video.video_id}_trimmed.mp4"


def trim_path_for_stage(
    project: MatchProject, project_root: Path, stage_number: int, stage_name: str
) -> Path:
    """Return the lossless-trim path the per-stage exporter would write.

    Mirrors :func:`splitsmith.ui.exports.export_audit_clip`'s naming:
    ``<exports>/stage<N>_<slug>_trimmed.mp4``. Thin wrapper over
    :func:`trim_path_for_video` for callers that only have a stage.
    """
    return trim_path_for_video(project, project_root, stage_number, stage_name, None)


def audit_path_for_stage(project: MatchProject, project_root: Path, stage_number: int) -> Path:
    return project.audit_path(project_root) / f"stage{stage_number}.json"


def _resolve_effective_camera(project: MatchProject, camera: str | None) -> str | None:
    """Pick the selector this run uses and check it against the whole project.

    A per-run value (manifest entry / CLI flag) beats the persisted
    ``compare_camera``. Validation happens once here rather than per stage:
    a selector that resolves nowhere is a typo, while one that merely
    misses a stage is a gap the stage walk substitutes around.
    """
    effective = camera if camera is not None else project.compare_camera
    camera_select.validate_camera([stage.videos for stage in project.stages if not stage.skipped], effective)
    return effective


def _choose_video(
    stage_videos: list[StageVideo], primary: StageVideo | None, camera: str | None
) -> tuple[StageVideo | None, bool]:
    """Return the video that feeds this stage's tile and whether it stood in.

    The requested cam can be absent or unbeeped on a single stage (battery
    died, cam forgotten). The primary then stands in so the grid keeps a
    live tile, and the substitution is recorded rather than silently applied.
    """
    chosen = camera_select.resolve_camera(stage_videos, camera)
    if chosen is None or chosen.beep_time is None:
        return primary, camera is not None
    return chosen, False


def _trim_present(
    trim: Path,
    ensure_trim: Callable[[Path], object] | None,
    trim_exists: Callable[[Path], bool] | None,
) -> bool:
    """Whether ``trim`` is there: ``trim_exists`` answers when given (a
    presence check that reads no bytes), else ``ensure_trim`` may mirror it
    down first and the disk answers."""
    if trim_exists is not None:
        return trim_exists(trim)
    if ensure_trim is not None:
        ensure_trim(trim)
    return trim.exists()


def _current_audit_trim(
    project: MatchProject,
    root: Path,
    stage: StageEntry,
    video: StageVideo,
) -> Path | None:
    """The audit trim on this disk to cut the tile from when the stage has
    no exported trim (#1681), or ``None``. Local, desktop and CLI only: a
    storage-backed project keeps no full-resolution trim (#1683), so the
    hosted grid job cuts from the source instead (:data:`SourceCut`).

    The trim job and the exporter cut the same window around the same
    beep (``trim.trim_video``, ``[max(0, beep - pre), beep + stage_time +
    post]``) and both stream-copy the audio; the grid re-encodes the
    picture either way. So the audit trim stands in, but only while it is
    current by the trim job's own reuse rule (``trim.audit_trim_matches``):
    its sidecar equals ``trim.audit_trim_params`` for the stage's beep,
    stage time and buffers, and the cut is not older than the source when
    the source is on this disk. A tile never uses a trim the trim job would
    re-cut. Never the 720p ``_web.mp4`` rendition.
    """
    if video.beep_time is None:  # the callers skip an unbeeped video; this narrows the type
        return None
    trim = project.trimmed_path(root) / audit_trim_name(stage.stage_number, video.video_id)
    params = audit_trim_params(
        beep_time=video.beep_time,
        stage_time_seconds=stage.time_seconds,
        pre_buffer_seconds=project.trim_pre_buffer_seconds,
        post_buffer_seconds=project.trim_post_buffer_seconds,
    )
    # The source as it sits on this disk, never mirrored down for this: a
    # relative path under the shooter root, or an absolute one (a drive).
    source = video.path if video.path.is_absolute() else raw_link.resolve(root / video.path)
    return trim if audit_trim_matches(trim, params, source=source) else None


def _tile_trim(
    project: MatchProject,
    root: Path,
    stage: StageEntry,
    video: StageVideo,
    *,
    ensure_trim: Callable[[Path], object] | None = None,
    trim_exists: Callable[[Path], bool] | None = None,
    cut_from_source: SourceCut | None = None,
) -> tuple[Path, Path | None, TileSource]:
    """``(expected export path, the file the tile is cut from, its source)``:
    the exported trim when it is there; else, with ``cut_from_source``
    (hosted), a stage trim cut from the source; else a current audit trim
    on this disk; else ``None`` (the caller records the export path as
    missing)."""
    exported = trim_path_for_video(project, root, stage.stage_number, stage.stage_name, video)
    if _trim_present(exported, ensure_trim, trim_exists):
        return exported, exported, "export"
    if cut_from_source is not None:
        return exported, cut_from_source(stage, video), "source"
    return exported, _current_audit_trim(project, root, stage, video), "audit"


def _inset_for(
    stage_videos: list[StageVideo],
    chosen: StageVideo,
    inset: str | None,
    *,
    trim_for: Callable[[StageVideo], Path],
    pre_buffer: float,
    probe: ProbeFn,
    ensure_trim: Callable[[Path], object] | None = None,
    trim_exists: Callable[[Path], bool] | None = None,
) -> InsetClip | None:
    """The inset camera on this stage, chosen among the shooter's *other*
    cameras the way the tile's camera is (``camera_select``: mount, then
    role), so it is never the picture twice. ``None`` when nothing matches,
    the match is ambiguous, unbeeped, or its trim is not on disk -- the
    tile just goes without, which is what the user asked for on a stage
    where that camera was not running."""
    if not inset:
        return None
    others = [v for v in stage_videos if v is not chosen]
    try:
        video = camera_select.resolve_camera(others, inset)
    except camera_select.CameraResolutionError:
        return None
    if video is None or video.beep_time is None:
        return None
    trim = trim_for(video)
    if not _trim_present(trim, ensure_trim, trim_exists):
        return None
    return InsetClip(
        trim_path=trim,
        beep_offset_in_clip=min(pre_buffer, video.beep_time),
        duration_seconds=probe(trim).duration_seconds,
    )


def load_shooter(
    project_root: Path,
    label: str,
    *,
    camera: str | None = None,
    inset: str | None = None,
    probe: ProbeFn | None = None,
) -> CompareShooterBundle:
    """Open ``project_root`` and build per-stage bundles for ``label``.

    Stages are skipped (omitted from ``stages_by_number``) when:
      - the stage is marked ``skipped``;
      - there is no primary video, or the primary has no ``beep_time``;
      - neither the lossless trim nor a current audit trim is on disk
        (an audit trim stands in for a missing export, #1681, and the
        bundle's ``trim_source`` says so).

    ``camera`` selects which of the shooter's cameras feeds the tiles;
    ``None`` falls back to the project's persisted ``compare_camera``,
    then the primary. Raises
    :class:`splitsmith.camera_select.CameraResolutionError` when the
    selector resolves on no stage at all.

    ``probe`` defaults to :func:`splitsmith.fcpxml_gen.probe_video`;
    pass a stub in tests to avoid shelling out to ffprobe.
    """
    if probe is None:
        probe = fcpxml_gen.probe_video
    project = MatchProject.load(project_root)
    pre_buffer = project.trim_pre_buffer_seconds
    effective_camera = _resolve_effective_camera(project, camera)
    bundles: dict[int, CompareStageBundle] = {}
    missing: list[MissingTrim] = []
    for stage in project.stages:
        if stage.skipped:
            continue
        chosen, substituted = _choose_video(stage.videos, stage.primary(), effective_camera)
        if chosen is None or chosen.beep_time is None:
            continue
        expected, trim, trim_source = _tile_trim(project, project_root, stage, chosen)
        if trim is None:
            missing.append(
                MissingTrim(
                    stage_number=stage.stage_number,
                    stage_name=stage.stage_name,
                    expected_path=expected,
                    camera=effective_camera if not substituted else None,
                )
            )
            continue
        meta = probe(trim)
        bundles[stage.stage_number] = CompareStageBundle(
            stage_number=stage.stage_number,
            stage_name=stage.stage_name,
            trim_path=trim,
            trim_source=trim_source,
            audit_path=audit_path_for_stage(project, project_root, stage.stage_number),
            beep_offset_in_clip=min(pre_buffer, chosen.beep_time),
            duration_seconds=meta.duration_seconds,
            width=meta.width,
            height=meta.height,
            frame_rate_num=meta.frame_rate_num,
            frame_rate_den=meta.frame_rate_den,
            camera_mount=chosen.camera_mount,
            substituted=substituted,
            inset=_inset_for(
                stage.videos,
                chosen,
                inset,
                trim_for=lambda v, st=stage: trim_path_for_video(
                    project, project_root, st.stage_number, st.stage_name, v
                ),
                pre_buffer=pre_buffer,
                probe=probe,
            ),
        )
    return CompareShooterBundle(
        label=label,
        project_root=project_root,
        project=project,
        stages_by_number=bundles,
        missing_trims=missing,
    )


def load_shooter_from_match(
    match_root: Path,
    slug: str,
    label: str,
    *,
    camera: str | None = None,
    inset: str | None = None,
    probe: ProbeFn | None = None,
    match: Match | None = None,
    project: MatchProject | None = None,
    ensure_trim: Callable[[Path], object] | None = None,
    trim_exists: Callable[[Path], bool] | None = None,
    cut_from_source: SourceCut | None = None,
) -> CompareShooterBundle:
    """Build a :class:`CompareShooterBundle` from one shooter inside a merged Match.

    Stage definitions come from the match (shared across shooters); per-
    stage data (time + videos) comes from the shooter. Same skip rules
    as :func:`load_shooter`: a stage is omitted when it's marked skipped,
    has no primary video with a beep time, or has neither its lossless
    trim in the shooter's exports dir nor a stand-in (a current audit
    trim, :func:`_current_audit_trim`, or hosted a cut from the source).
    ``camera`` selects the contributing
    camera exactly as in :func:`load_shooter`.

    ``cut_from_source`` replaces the audit-trim fallback for a hosted
    caller, whose storage keeps no full-resolution trim (#1683): the
    render job cuts the stage trim from the source, a request only plans
    it (see :data:`SourceCut`). ``None`` reads the audit trim off the disk.

    ``match`` / ``project`` (#755) let a caller hand over documents it
    already holds instead of reading ``match.json`` / ``project.json``
    off ``match_root`` -- on a hosted worker those live in the state
    store and the disk has neither. ``ensure_trim`` is called with each
    stage's expected trim path *before* its existence is checked, so a
    hosted caller can mirror the trim down from object storage; a local
    caller leaves it ``None`` and the check reads the disk as it always
    has. ``trim_exists`` replaces both for a caller that only needs to
    know which stages have a trim (a request validating before it queues
    the render): it answers without the file, so pass a ``probe`` that does
    not open it either.
    """
    if probe is None:
        probe = fcpxml_gen.probe_video
    if match is None:
        match = Match.load(match_root)
    shooter_root = Match.shooter_root(match_root, slug)
    # Per-stage data comes from project.json: it is authoritative for
    # everything the server writes (beeps, roles, buffers). shooter.json is
    # a merge-time snapshot that nothing keeps in sync, so reading it drops
    # any beep confirmed after the merge.
    #
    # Stage names have two jobs and two different authorities (#615):
    #   - the trim *filename* follows the shooter's own project.json,
    #     because that is what every writer of a trim derives its basename
    #     from. A per-shooter scoreboard import rewrites project.stages and
    #     leaves match.json alone, so looking the file up by the match's
    #     name turns that stage into a black tile.
    #   - the grid *label* follows match.json, which owns the shared stage
    #     definitions: one stage must read the same across every tile,
    #     whatever a single shooter's scorecard called it.
    if project is None:
        project = MatchProject.load(shooter_root)
    stage_names: dict[int, str] = {s.stage_number: s.stage_name for s in match.stages}
    pre_buffer = project.trim_pre_buffer_seconds
    effective_camera = _resolve_effective_camera(project, camera)

    bundles: dict[int, CompareStageBundle] = {}
    missing: list[MissingTrim] = []
    for stage in project.stages:
        if stage.skipped:
            continue
        chosen, substituted = _choose_video(stage.videos, stage.primary(), effective_camera)
        if chosen is None or chosen.beep_time is None:
            continue
        stage_label = stage_names.get(stage.stage_number, stage.stage_name)
        expected, trim, trim_source = _tile_trim(
            project,
            shooter_root,
            stage,
            chosen,
            ensure_trim=ensure_trim,
            trim_exists=trim_exists,
            cut_from_source=cut_from_source,
        )
        if trim is None:
            missing.append(
                MissingTrim(
                    stage_number=stage.stage_number,
                    stage_name=stage_label,
                    expected_path=expected,
                    camera=effective_camera if not substituted else None,
                )
            )
            continue
        meta = probe(trim)
        bundles[stage.stage_number] = CompareStageBundle(
            stage_number=stage.stage_number,
            stage_name=stage_label,
            trim_path=trim,
            trim_source=trim_source,
            audit_path=audit_path_for_stage(project, shooter_root, stage.stage_number),
            beep_offset_in_clip=min(pre_buffer, chosen.beep_time),
            duration_seconds=meta.duration_seconds,
            width=meta.width,
            height=meta.height,
            frame_rate_num=meta.frame_rate_num,
            frame_rate_den=meta.frame_rate_den,
            camera_mount=chosen.camera_mount,
            substituted=substituted,
            inset=_inset_for(
                stage.videos,
                chosen,
                inset,
                trim_for=lambda v, st=stage: trim_path_for_video(
                    project, shooter_root, st.stage_number, st.stage_name, v
                ),
                pre_buffer=pre_buffer,
                probe=probe,
                ensure_trim=ensure_trim,
                trim_exists=trim_exists,
            ),
        )

    return CompareShooterBundle(
        label=label,
        project_root=shooter_root,
        project=project,
        stages_by_number=bundles,
        missing_trims=missing,
    )
