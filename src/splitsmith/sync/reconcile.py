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
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .. import automation
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
) -> StepKind | None:
    """The next step for one video, or None.

    ``explicit`` is a confirm the user just made on this machine: a
    trimmed primary re-runs detection even over existing results, as the
    beep-review endpoint always has. Without it (a reconcile pass) the
    step never detects over an audit doc with real content, and honours
    the project's ``shot_detect_on_beep_verified`` gate.
    """
    if not video.beep_reviewed or video.beep_time is None:
        return None
    if not video.processed.get("trim"):
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


def plan_reconcile(
    projects: Mapping[str, MatchProject],
    audits: Mapping[str, Mapping[int, dict]],
    failures: Mapping[str, str],
) -> list[ReconcileStep]:
    steps: list[ReconcileStep] = []
    for slug in sorted(projects):
        project = projects[slug]
        detect_enabled = automation.resolve_automation(
            project_override=project.automation
        ).settings.shot_detect_on_beep_verified
        for stage in project.stages:
            audit_doc = audits.get(slug, {}).get(stage.stage_number)
            for video in stage.videos:
                kind = video_step(stage, video, audit_doc, detect_enabled=detect_enabled)
                if kind is None:
                    continue
                step = ReconcileStep(
                    kind=kind,
                    slug=slug,
                    stage_number=stage.stage_number,
                    video_id=video.video_id,
                    input_key=f"{video.beep_time:.4f}",
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
