"""Load per-stage trim metadata from a shooter -- legacy project or merged Match."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import camera_select, fcpxml_gen
from ..export_naming import stage_file_base
from ..fcpxml_gen import VideoMetadata
from ..match_model import Match
from ..match_project import MatchProject, StageVideo

ProbeFn = Callable[[Path], VideoMetadata]


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
    #: True when the requested camera was unavailable on this stage and the
    #: primary stood in. Surfaced in the run summary and the FCPXML marker.
    substituted: bool = False

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
    """A stage dropped from the grid because its trim is not on disk.

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

    ``project`` is the legacy :class:`MatchProject` when this bundle came
    from a single-shooter project; ``None`` when it came from a shooter
    inside a merged :class:`splitsmith.match_model.Match`. The emitter
    only reads ``label`` and ``stages_by_number``, so the optional field
    is informational for callers that want to inspect it.
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


def load_shooter(
    project_root: Path,
    label: str,
    *,
    camera: str | None = None,
    probe: ProbeFn | None = None,
) -> CompareShooterBundle:
    """Open ``project_root`` and build per-stage bundles for ``label``.

    Stages are skipped (omitted from ``stages_by_number``) when:
      - the stage is marked ``skipped``;
      - there is no primary video, or the primary has no ``beep_time``;
      - the lossless trim is not on disk.

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
        trim = trim_path_for_video(project, project_root, stage.stage_number, stage.stage_name, chosen)
        if not trim.exists():
            missing.append(
                MissingTrim(
                    stage_number=stage.stage_number,
                    stage_name=stage.stage_name,
                    expected_path=trim,
                    camera=effective_camera if not substituted else None,
                )
            )
            continue
        meta = probe(trim)
        bundles[stage.stage_number] = CompareStageBundle(
            stage_number=stage.stage_number,
            stage_name=stage.stage_name,
            trim_path=trim,
            audit_path=audit_path_for_stage(project, project_root, stage.stage_number),
            beep_offset_in_clip=min(pre_buffer, chosen.beep_time),
            duration_seconds=meta.duration_seconds,
            width=meta.width,
            height=meta.height,
            frame_rate_num=meta.frame_rate_num,
            frame_rate_den=meta.frame_rate_den,
            camera_mount=chosen.camera_mount,
            substituted=substituted,
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
    probe: ProbeFn | None = None,
) -> CompareShooterBundle:
    """Build a :class:`CompareShooterBundle` from one shooter inside a merged Match.

    Stage definitions come from the match (shared across shooters); per-
    stage data (time + videos) comes from the shooter. Same skip rules
    as :func:`load_shooter`: a stage is omitted when it's marked skipped,
    has no primary video with a beep time, or its lossless trim is
    missing from the shooter's exports dir. ``camera`` selects the
    contributing camera exactly as in :func:`load_shooter`.
    """
    if probe is None:
        probe = fcpxml_gen.probe_video
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
        trim = trim_path_for_video(project, shooter_root, stage.stage_number, stage.stage_name, chosen)
        if not trim.exists():
            missing.append(
                MissingTrim(
                    stage_number=stage.stage_number,
                    stage_name=stage_label,
                    expected_path=trim,
                    camera=effective_camera if not substituted else None,
                )
            )
            continue
        meta = probe(trim)
        bundles[stage.stage_number] = CompareStageBundle(
            stage_number=stage.stage_number,
            stage_name=stage_label,
            trim_path=trim,
            audit_path=audit_path_for_stage(project, shooter_root, stage.stage_number),
            beep_offset_in_clip=min(pre_buffer, chosen.beep_time),
            duration_seconds=meta.duration_seconds,
            width=meta.width,
            height=meta.height,
            frame_rate_num=meta.frame_rate_num,
            frame_rate_den=meta.frame_rate_den,
            camera_mount=chosen.camera_mount,
            substituted=substituted,
        )

    return CompareShooterBundle(
        label=label,
        project_root=shooter_root,
        project=None,
        stages_by_number=bundles,
        missing_trims=missing,
    )
