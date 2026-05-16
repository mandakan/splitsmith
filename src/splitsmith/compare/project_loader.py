"""Load per-stage trim metadata from a shooter -- legacy project or merged Match."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import fcpxml_gen
from ..fcpxml_gen import VideoMetadata
from ..match_model import Match, Shooter
from ..ui.match_exports import _slugify
from ..ui.project import MatchProject

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


def trim_path_for_stage(
    project: MatchProject, project_root: Path, stage_number: int, stage_name: str
) -> Path:
    """Return the lossless-trim path the per-stage exporter would write.

    Mirrors :func:`splitsmith.ui.exports.export_audit_clip`'s naming:
    ``<exports>/stage<N>_<slug>_trimmed.mp4``.
    """
    base = f"stage{stage_number}_{_slugify(stage_name)}"
    return project.exports_path(project_root) / f"{base}_trimmed.mp4"


def audit_path_for_stage(project: MatchProject, project_root: Path, stage_number: int) -> Path:
    return project.audit_path(project_root) / f"stage{stage_number}.json"


def load_shooter(
    project_root: Path,
    label: str,
    *,
    probe: ProbeFn | None = None,
) -> CompareShooterBundle:
    """Open ``project_root`` and build per-stage bundles for ``label``.

    Stages are skipped (omitted from ``stages_by_number``) when:
      - the stage is marked ``skipped``;
      - there is no primary video, or the primary has no ``beep_time``;
      - the lossless trim is not on disk.

    ``probe`` defaults to :func:`splitsmith.fcpxml_gen.probe_video`;
    pass a stub in tests to avoid shelling out to ffprobe.
    """
    if probe is None:
        probe = fcpxml_gen.probe_video
    project = MatchProject.load(project_root)
    pre_buffer = project.trim_pre_buffer_seconds
    bundles: dict[int, CompareStageBundle] = {}
    for stage in project.stages:
        if stage.skipped:
            continue
        primary = stage.primary()
        if primary is None or primary.beep_time is None:
            continue
        trim = trim_path_for_stage(project, project_root, stage.stage_number, stage.stage_name)
        if not trim.exists():
            continue
        meta = probe(trim)
        bundles[stage.stage_number] = CompareStageBundle(
            stage_number=stage.stage_number,
            stage_name=stage.stage_name,
            trim_path=trim,
            audit_path=audit_path_for_stage(project, project_root, stage.stage_number),
            beep_offset_in_clip=min(pre_buffer, primary.beep_time),
            duration_seconds=meta.duration_seconds,
            width=meta.width,
            height=meta.height,
            frame_rate_num=meta.frame_rate_num,
            frame_rate_den=meta.frame_rate_den,
        )
    return CompareShooterBundle(
        label=label,
        project_root=project_root,
        project=project,
        stages_by_number=bundles,
    )


def _trim_path_for_shooter_stage(
    shooter: Shooter,
    shooter_root: Path,
    stage_number: int,
    stage_name: str,
) -> Path:
    """Same naming as :func:`trim_path_for_stage` but rooted at a shooter dir."""
    base = f"stage{stage_number}_{_slugify(stage_name)}"
    exports = Path(shooter.exports_dir).expanduser() if shooter.exports_dir else shooter_root / "exports"
    if not exports.is_absolute():
        exports = shooter_root / exports
    return exports / f"{base}_trimmed.mp4"


def load_shooter_from_match(
    match_root: Path,
    slug: str,
    label: str,
    *,
    probe: ProbeFn | None = None,
) -> CompareShooterBundle:
    """Build a :class:`CompareShooterBundle` from one shooter inside a merged Match.

    Stage definitions come from the match (shared across shooters); per-
    stage data (time + videos) comes from the shooter. Same skip rules
    as :func:`load_shooter`: a stage is omitted when it's marked skipped,
    has no primary video with a beep time, or its lossless trim is
    missing from the shooter's exports dir.
    """
    if probe is None:
        probe = fcpxml_gen.probe_video
    match = Match.load(match_root)
    shooter = match.load_shooter(match_root, slug)
    shooter_root = Match.shooter_root(match_root, slug)
    # Stage name lookup from the match-level definitions.
    stage_names: dict[int, str] = {s.stage_number: s.stage_name for s in match.stages}

    bundles: dict[int, CompareStageBundle] = {}
    for stage in shooter.stages:
        if stage.skipped:
            continue
        primary = next((v for v in stage.videos if v.role == "primary"), None)
        if primary is None or primary.beep_time is None:
            continue
        stage_name = stage_names.get(stage.stage_number, f"stage{stage.stage_number}")
        trim = _trim_path_for_shooter_stage(shooter, shooter_root, stage.stage_number, stage_name)
        if not trim.exists():
            continue
        meta = probe(trim)
        bundles[stage.stage_number] = CompareStageBundle(
            stage_number=stage.stage_number,
            stage_name=stage_name,
            trim_path=trim,
            audit_path=shooter_root / "audit" / f"stage{stage.stage_number}.json",
            beep_offset_in_clip=min(shooter.trim_pre_buffer_seconds, primary.beep_time),
            duration_seconds=meta.duration_seconds,
            width=meta.width,
            height=meta.height,
            frame_rate_num=meta.frame_rate_num,
            frame_rate_den=meta.frame_rate_den,
        )

    return CompareShooterBundle(
        label=label,
        project_root=shooter_root,
        project=None,
        stages_by_number=bundles,
    )
