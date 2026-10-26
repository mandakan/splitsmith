"""Derive the pipeline steps a match is missing (spec 2026-09-27 s3).

Pure: reads projects and audit docs the caller loaded, returns steps,
submits nothing. Driven by the ``processed`` flags the merge already
maintains (a pulled beep_time change clears trim and, on a primary,
shot_detect), so a phone-side confirm and a desktop-side confirm leave
the same state behind. ``_after_beep_reviewed`` evaluates the same
``video_step`` with ``explicit=True``.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .. import automation, raw_link
from ..match_model import load_match_or_legacy
from ..match_project import MatchProject, StageEntry, StageVideo, is_stub_audit
from .plan import AUDIT_FILENAME_RE

StepKind = Literal["trim", "shot_detect"]


class ReconcileStep(BaseModel):
    kind: StepKind
    slug: str
    stage_number: int
    video_id: str
    #: The inputs the step runs with; a recorded failure is skipped only
    #: while these are unchanged.
    input_key: str

    @property
    def key(self) -> str:
        return f"{self.kind}/{self.slug}/{self.stage_number}/{self.video_id}"


def video_step(
    stage: StageEntry,
    video: StageVideo,
    audit_doc: dict | None,
    *,
    detect_enabled: bool = True,
    explicit: bool = False,
    trim_valid: bool | None = None,
) -> StepKind | None:
    """The next step for one video, or None.

    ``explicit`` is a confirm the user just made on this machine: a
    trimmed primary re-runs detection even over existing results, as the
    beep-review endpoint always has. Without it (a reconcile pass) the
    step never detects over an audit doc with real content, and honours
    the project's ``shot_detect_on_beep_verified`` gate.

    ``trim_valid`` is whether the current-keyed trim is on disk and passes
    the structural check (``valid_trims_for``); ``None`` when the caller
    did not look, and then the flag alone decides.
    """
    if not video.beep_reviewed or video.beep_time is None:
        return None
    # The file decides when the caller looked: a flag set by a trim that is
    # gone (deleted, never synced down, a migration that could not rename
    # it) must not keep the stage untrimmed forever (spec 2026-10-12).
    trimmed = video.processed.get("trim") and trim_valid is not False
    if not trimmed:
        return "trim" if stage.time_seconds > 0 else None
    if video.role != "primary":
        return None
    if explicit:
        return "shot_detect"
    if not detect_enabled or video.processed.get("shot_detect"):
        return None
    if audit_doc is not None and not is_stub_audit(audit_doc):
        return None
    return "shot_detect"


def step_input_key(
    kind: StepKind,
    project: MatchProject,
    stage: StageEntry,
    video: StageVideo,
    *,
    source_present: bool | None = None,
) -> str:
    """Everything a step's outcome depends on (#1069). A failure is
    retried as soon as any of it changes: a moved beep, a stage time
    fixed on the phone, a buffer setting, a relinked source, or a source
    that was missing (volume unmounted) and is back. ``source_present``
    is ``None`` when the caller did not look, and then stays out of the
    key."""
    parts = [f"beep={video.beep_time:.4f}", f"stage={stage.time_seconds:.4f}"]
    if kind == "trim":
        parts += [
            f"pre={project.trim_pre_buffer_seconds:g}",
            f"post={project.trim_post_buffer_seconds:g}",
            f"src={video.path.as_posix()}",
        ]
        if source_present is not None:
            parts.append(f"present={int(source_present)}")
    return " ".join(parts)


def plan_reconcile(
    projects: Mapping[str, MatchProject],
    audits: Mapping[str, Mapping[int, dict]],
    failures: Mapping[str, str],
    *,
    present_sources: Collection[tuple[str, str]] | None = None,
    valid_trims: Collection[tuple[str, str]] | None = None,
) -> list[ReconcileStep]:
    """``present_sources`` holds ``(slug, video_id)`` for every video whose
    source file is on disk (``present_sources_for``); ``None`` leaves
    reachability out of the trim input key. ``valid_trims`` holds the same
    pairs for every video whose trim is on disk and valid
    (``valid_trims_for``); ``None`` trusts the ``processed`` flags."""
    steps: list[ReconcileStep] = []
    for slug in sorted(projects):
        project = projects[slug]
        detect_enabled = automation.resolve_automation(
            project_override=project.automation
        ).settings.shot_detect_on_beep_verified
        for stage in project.stages:
            audit_doc = audits.get(slug, {}).get(stage.stage_number)
            for video in stage.videos:
                kind = video_step(
                    stage,
                    video,
                    audit_doc,
                    detect_enabled=detect_enabled,
                    trim_valid=None if valid_trims is None else (slug, video.video_id) in valid_trims,
                )
                if kind is None:
                    continue
                step = ReconcileStep(
                    kind=kind,
                    slug=slug,
                    stage_number=stage.stage_number,
                    video_id=video.video_id,
                    input_key=step_input_key(
                        kind,
                        project,
                        stage,
                        video,
                        source_present=(
                            None if present_sources is None else (slug, video.video_id) in present_sources
                        ),
                    ),
                )
                if failures.get(step.key) == step.input_key:
                    continue
                steps.append(step)
    return steps


def load_reconcile_inputs(
    match_root: Path,
) -> tuple[dict[str, MatchProject], dict[str, dict[int, dict]]]:
    """Read every shooter's project and audit docs. An unreadable audit
    file is left out and reads as "no audit"."""
    match, shooter_roots = load_match_or_legacy(match_root)
    projects: dict[str, MatchProject] = {}
    audits: dict[str, dict[int, dict]] = {}
    for slug in match.shooters:
        root = shooter_roots[slug]
        projects[slug] = MatchProject.load(root)
        docs: dict[int, dict] = {}
        audit_dir = root / "audit"
        if audit_dir.is_dir():
            for path in audit_dir.iterdir():
                m = AUDIT_FILENAME_RE.match(path.name)
                if not m:
                    continue
                try:
                    docs[int(m.group(1))] = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
        audits[slug] = docs
    return projects, audits


def present_sources_for(match_root: Path, projects: Mapping[str, MatchProject]) -> set[tuple[str, str]]:
    """``(slug, video_id)`` of every video whose source file exists now.

    A plain stat, never ``resolve_video_path``: that can mirror a source
    from hosted storage, which a planning pass must not start."""
    _, shooter_roots = load_match_or_legacy(match_root)
    present: set[tuple[str, str]] = set()
    for slug, project in projects.items():
        root = shooter_roots.get(slug)
        if root is None:
            continue
        for stage in project.stages:
            for video in stage.videos:
                path = raw_link.resolve(video.path if video.path.is_absolute() else root / video.path)
                try:
                    if path.exists():
                        present.add((slug, video.video_id))
                except OSError:
                    continue
    return present


def valid_trims_for(match_root: Path, projects: Mapping[str, MatchProject]) -> set[tuple[str, str]]:
    """``(slug, video_id)`` of every video whose current-keyed trim is on
    disk and passes the structural check. A plain stat and parse, cached
    per file version; never a pull from storage."""
    from ..media_check import valid_mp4_cached
    from ..ui.audio import trimmed_video_path

    _, shooter_roots = load_match_or_legacy(match_root)
    out: set[tuple[str, str]] = set()
    for slug, project in projects.items():
        root = shooter_roots.get(slug)
        if root is None:
            continue
        for stage in project.stages:
            for video in stage.videos:
                trim = trimmed_video_path(root, stage.stage_number, video, project=project)
                if valid_mp4_cached(trim).ok:
                    out.add((slug, video.video_id))
    return out
