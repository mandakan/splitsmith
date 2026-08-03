"""Stage-list editing for an existing match (#521).

Adds, removes, and renames stages on a match that already has audit
progress. ``stage_number`` is stable for a stage's lifetime: removing a
stage leaves a gap and freed numbers are never handed out again, so every
per-stage artifact key (``audit/stage<N>.json``, ``stage<N>_cam_*.wav``,
``stage<N>_cam_*_trimmed.mp4``) stays valid for the stages the user did
not touch. That is what lets this ship without an artifact-migration
engine; see the design doc for the renumber/reorder work it defers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from splitsmith.config import StageRounds
from splitsmith.match_model import MatchStageDefinition
from splitsmith.ui.project import MatchProject, StageEntry


class StageEditError(Exception):
    """A stage-list submission the server refuses. Maps to HTTP 400."""


class SubmittedStage(BaseModel):
    """One row of the SPA's stage editor.

    ``stage_number is None`` marks a row the user added; the server
    allocates its number. A number the match does not have is an error,
    not an implicit add -- a client sending a stale number should learn
    about it rather than silently create a stage.
    """

    stage_number: int | None = None
    stage_name: str
    stage_rounds: StageRounds | None = None


class StageListDiff(BaseModel):
    """What a submission changes, relative to the match's current list."""

    removed: list[int] = Field(default_factory=list)
    added: list[MatchStageDefinition] = Field(default_factory=list)
    renamed: list[MatchStageDefinition] = Field(default_factory=list)
    unchanged: list[int] = Field(default_factory=list)


def diff_stage_list(
    existing: list[MatchStageDefinition],
    submitted: list[SubmittedStage],
) -> StageListDiff:
    """Diff ``submitted`` against ``existing``. Raises :class:`StageEditError`.

    New rows are numbered from ``max(existing) + 1`` upward, deliberately
    skipping numbers freed by an earlier removal: a worker whose
    cancellation lost the race can still write ``stage<freed>_*`` bytes,
    and never reusing the number keeps that write inert instead of letting
    it reattach to a live stage.
    """
    if not submitted:
        raise StageEditError("a match must keep at least one stage")

    by_number = {s.stage_number: s for s in existing}
    seen: set[int] = set()
    for row in submitted:
        if not row.stage_name.strip():
            raise StageEditError("stage_name must not be blank")
        if row.stage_number is None:
            continue
        if row.stage_number in seen:
            raise StageEditError(f"duplicate stage_number {row.stage_number}")
        if row.stage_number not in by_number:
            raise StageEditError(
                f"no stage {row.stage_number} in this match; " "send stage_number=null to add a stage"
            )
        seen.add(row.stage_number)

    diff = StageListDiff()
    diff.removed = sorted(set(by_number) - seen)

    next_number = (max(by_number) if by_number else 0) + 1
    for row in submitted:
        name = row.stage_name.strip()
        if row.stage_number is None:
            diff.added.append(
                MatchStageDefinition(
                    stage_number=next_number,
                    stage_name=name,
                    stage_rounds=row.stage_rounds,
                    placeholder=True,
                )
            )
            next_number += 1
            continue
        current = by_number[row.stage_number]
        if name != current.stage_name or row.stage_rounds != current.stage_rounds:
            diff.renamed.append(
                MatchStageDefinition(
                    stage_number=current.stage_number,
                    stage_name=name,
                    stage_rounds=row.stage_rounds,
                    placeholder=current.placeholder,
                )
            )
        else:
            diff.unchanged.append(current.stage_number)

    diff.unchanged.sort()
    return diff


class PurgeCounts(BaseModel):
    """What a single stage's artifact purge managed to remove."""

    files_deleted: int = 0
    objects_deleted: int = 0
    errors: list[str] = Field(default_factory=list)


def _stage_artifact_glob(stage_number: int) -> str:
    """Glob matching every artifact basename for ``stage_number``.

    Covers both naming tiers in one pattern: per-cam
    (``stage3_cam_<id>.wav``, ``..._audit.wav``, ``..._trimmed.mp4``) and
    legacy (``stage3_primary.wav``, ``stage3_audit.peaks-*.json``,
    ``stage3_trimmed.params.json``).

    The trailing underscore is load-bearing. ``stage3*`` would also match
    ``stage30_cam_x.wav``, so removing stage 3 would silently delete stage
    30's cached audio and trim.
    """
    return f"stage{stage_number}_*"


def purge_stage_artifacts(
    project: MatchProject,
    root: Path,
    stage_number: int,
) -> PurgeCounts:
    """Delete every derived artifact for ``stage_number``, locally and in storage.

    Derived state only -- audio WAVs, peaks, trims and their sidecars. The
    audit doc is purged separately (it lives in ``state_docs`` in hosted
    mode, not in these directories) and the stage's videos are released by
    :meth:`MatchProject.unassign_stage_videos` rather than deleted.

    Best-effort, following ``match_delete``: a failed delete is recorded in
    ``errors`` and the sweep continues. Leaving the stage list unwritten
    because one cache object refused to die would be the worse outcome.
    """
    counts = PurgeCounts()
    pattern = _stage_artifact_glob(stage_number)

    for directory in (project.audio_path(root), project.trimmed_path(root)):
        if not directory.exists():
            continue
        for victim in sorted(directory.glob(pattern)):
            try:
                victim.unlink()
                counts.files_deleted += 1
            except OSError as exc:
                counts.errors.append(f"delete {victim}: {exc}")

    storage = project._storage
    scope = project._storage_scope
    if storage is None or scope is None:
        return counts

    prefix_basename = f"stage{stage_number}_"
    for subdir in ("audio", "trimmed"):
        prefix = f"{scope}/{subdir}/"
        try:
            objects = list(storage.list(prefix))
        except Exception as exc:  # noqa: BLE001 -- best-effort teardown
            counts.errors.append(f"list storage {prefix!r}: {exc}")
            continue
        for obj in objects:
            if not obj.path.rsplit("/", 1)[-1].startswith(prefix_basename):
                continue
            try:
                storage.delete(obj.path)
                counts.objects_deleted += 1
            except Exception as exc:  # noqa: BLE001
                counts.errors.append(f"delete {obj.path!r}: {exc}")

    return counts


class ShooterStageEditResult(BaseModel):
    """What the edit did to one shooter."""

    slug: str
    videos_unassigned: int = 0
    audit_docs_deleted: int = 0
    files_deleted: int = 0
    objects_deleted: int = 0


class StageEditSummary(BaseModel):
    """Outcome of a stage-list edit, modelled on ``DeletionSummary``.

    Failures are collected rather than raised: the stage list committing
    matters more than one uncooperative cache object, and the user needs
    to see what did not get cleaned up.
    """

    removed: list[int] = Field(default_factory=list)
    added: list[int] = Field(default_factory=list)
    renamed: list[int] = Field(default_factory=list)
    jobs_cancelled: int = 0
    shooters: list[ShooterStageEditResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


async def apply_stage_edit(
    *,
    match: Any,
    root: Path,
    submitted: list[SubmittedStage],
    shooter_slugs: list[str],
    load_project: Any,
    save_project: Any,
    save_match: Any,
    delete_audit: Any,
    cancel_jobs: Any,
) -> StageEditSummary:
    """Apply a stage-list edit to the match and every shooter in it.

    Ordering is deliberate and mirrors ``match_delete._delete_hosted``:

    1. Cancel jobs targeting removed stages, so no worker rewrites
       artifacts under the purge. Jobs carry no shooter slug, and removal
       is match-wide, so filtering on ``stage_number`` is exactly right.
    2. Per shooter: release videos, delete the audit doc, purge caches,
       apply adds and renames, save.
    3. Save the match doc **last**. A crash mid-fan-out then leaves the
       canonical list describing the pre-edit world rather than promising
       stages the shooters no longer have.

    Cancellation is not instantaneous, so a worker can still land a write
    after step 2's purge. Because freed numbers are never reused, that
    write is inert garbage that can never be read as a live stage.
    """
    diff = diff_stage_list(list(match.stages), submitted)
    summary = StageEditSummary(
        removed=list(diff.removed),
        added=[s.stage_number for s in diff.added],
        renamed=[s.stage_number for s in diff.renamed],
    )

    if diff.removed:
        try:
            summary.jobs_cancelled = await cancel_jobs(set(diff.removed))
        except Exception as exc:  # noqa: BLE001
            summary.errors.append(f"cancel jobs: {exc}")

    renamed_by_number = {s.stage_number: s for s in diff.renamed}
    removed = set(diff.removed)

    for slug in shooter_slugs:
        result = ShooterStageEditResult(slug=slug)
        try:
            project = load_project(slug)

            for stage_number in diff.removed:
                result.videos_unassigned += project.unassign_stage_videos(stage_number)
                if delete_audit(slug, stage_number):
                    result.audit_docs_deleted += 1
                counts = purge_stage_artifacts(project, root, stage_number)
                result.files_deleted += counts.files_deleted
                result.objects_deleted += counts.objects_deleted
                summary.errors.extend(f"{slug}: {e}" for e in counts.errors)

            project.stages = [s for s in project.stages if s.stage_number not in removed]
            for stage in project.stages:
                update = renamed_by_number.get(stage.stage_number)
                if update is not None:
                    stage.stage_name = update.stage_name
                    stage.stage_rounds = update.stage_rounds
            for definition in diff.added:
                project.stages.append(
                    StageEntry(
                        stage_number=definition.stage_number,
                        stage_name=definition.stage_name,
                        time_seconds=0.0,
                        stage_rounds=definition.stage_rounds,
                        placeholder=True,
                    )
                )
            project.stages.sort(key=lambda s: s.stage_number)
            save_project(slug, project)
        except Exception as exc:  # noqa: BLE001 -- one shooter must not
            # strand the others or the match doc.
            summary.errors.append(f"{slug}: {exc}")
        summary.shooters.append(result)

    match.stages = [s for s in match.stages if s.stage_number not in removed]
    for definition in match.stages:
        update = renamed_by_number.get(definition.stage_number)
        if update is not None:
            definition.stage_name = update.stage_name
            definition.stage_rounds = update.stage_rounds
    match.stages.extend(diff.added)
    match.stages.sort(key=lambda s: s.stage_number)
    save_match()

    return summary
