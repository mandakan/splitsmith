"""Load per-stage trim metadata from a single shooter's MatchProject."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import fcpxml_gen
from ..fcpxml_gen import VideoMetadata
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
    """A shooter's project + the per-stage bundles ready for export."""

    label: str
    project_root: Path
    project: MatchProject
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
