"""Stage-list editing for an existing match (#521).

Adds, removes, and renames stages on a match that already has audit
progress. ``stage_number`` is stable for a stage's lifetime: nothing here
renumbers an existing stage, so every per-stage artifact key
(``audit/stage<N>.json``, ``stage<N>_cam_*.wav``,
``stage<N>_cam_*_trimmed.mp4``) stays valid for the stages the user did
not touch. That is what lets this ship without an artifact-migration
engine; see the design doc for the renumber/reorder work it defers.

Removing a stage leaves a gap, and the freed number is not handed out
again. That second property does *not* follow from never renumbering --
it comes from the persisted counter ``Match.next_stage_number``, which
this module reads, allocates above, and writes back. Allocating
``max(stages) + 1`` instead would return the number of a removed
*highest*-numbered stage straight back to circulation.
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


def _state_conflict_excs() -> tuple[type[BaseException], ...]:
    """Resolve ``StateConflictError`` lazily, mirroring
    ``splitsmith.ui.server._state_conflict_excs``.

    This module must stay importable on a slim local install without the
    ``[hosted]`` extra (``splitsmith.ui.server`` imports it at module scope,
    and ``test_local_mode_no_hosted_imports`` pins that ``splitsmith.db``
    never leaks into a local-mode entrypoint's import chain), so the import
    is deferred to call time and guarded rather than hoisted to the top of
    the file. Empty tuple on a slim install -- ``except ():`` then matches
    nothing, which is correct: local file saves never raise a conflict.
    """
    try:
        from ..db import StateConflictError

        return (StateConflictError,)
    except Exception:  # pragma: no cover - slim local install without db extras
        return ()


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
    #: The allocation counter after this diff: the mark it was given,
    #: advanced past every number in :attr:`added`. Required rather than
    #: defaulted -- a diff that allocated numbers without reporting where
    #: the counter ended is exactly the state that lets one be reissued.
    #: The caller persists it (see :func:`apply_stage_edit`).
    next_stage_number: int


def diff_stage_list(
    existing: list[MatchStageDefinition],
    submitted: list[SubmittedStage],
    *,
    next_stage_number: int,
) -> StageListDiff:
    """Diff ``submitted`` against ``existing``. Raises :class:`StageEditError`.

    ``next_stage_number`` is the match's allocation counter
    (``Match.resolve_next_stage_number()``): new rows are numbered from it
    upward, and :attr:`StageListDiff.next_stage_number` reports where it
    ended so the caller can write it back. It is an *input* precisely
    because the list cannot supply it. Deriving the next number from
    ``max(existing) + 1`` -- what this function used to do -- reuses the
    number of a removed highest-numbered stage: with stages 1..6, removing
    6 and then adding hands 6 back out.

    That reuse is not cosmetic. A worker still running on the removed
    stage can land ``stage<freed>_cam_*.wav`` after the purge (nothing
    cancels it, see #645); those bytes are inert only while the number can
    never name a live stage again, and a new stage 6 would silently
    inherit the old one's audio and trim.

    A mark that does not clear ``max(existing)`` is refused outright. The
    collision floor in ``Match.resolve_next_stage_number`` keeps the one
    caller honest, but that is guidance, not enforcement, and nothing
    downstream would catch the result: the allocated number would land on
    a live stage, and ``apply_stage_edit`` would extend ``match.stages``
    with a second definition sharing its artifact key. Failing loudly here
    beats clamping, which would hide a caller that is computing the mark
    wrongly and will get it wrong again.
    """
    if not submitted:
        raise StageEditError("a match must keep at least one stage")

    by_number = {s.stage_number: s for s in existing}
    if by_number and next_stage_number <= max(by_number):
        raise StageEditError(
            f"next_stage_number {next_stage_number} does not clear stage {max(by_number)}; "
            "the allocation counter must be above every live stage"
        )
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

    diff = StageListDiff(next_stage_number=next_stage_number)
    diff.removed = sorted(set(by_number) - seen)

    next_number = next_stage_number
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
    diff.next_stage_number = next_number
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
    """What the edit did to one shooter.

    ``error`` is set when something failed for this shooter specifically,
    but it covers two outcomes that are not equally bad, and the strings
    differ only in formatting -- so read :attr:`saved`, never the message:

    * ``saved=True`` with an ``error``: one removed stage's cleanup step
      failed (video release, audit delete, or artifact purge). The
      shooter's stage list still updated and their project was written;
      what is left behind is orphaned derived state.
    * ``saved=False``: the project doc was never written. This shooter's
      stage list is unchanged on disk and still describes the pre-edit
      world, while the match doc has moved on.

    Consumers should check these rather than string-matching the shooter's
    slug inside :attr:`StageEditSummary.errors`.
    """

    slug: str
    videos_unassigned: int = 0
    audit_docs_deleted: int = 0
    files_deleted: int = 0
    objects_deleted: int = 0
    error: str | None = None
    #: True once this shooter's project doc was written. Defaults to
    #: False so any path that does not reach the save reports the
    #: conservative answer.
    saved: bool = False


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
    submitted: list[SubmittedStage],
    shooter_slugs: list[str],
    load_project: Any,
    save_project: Any,
    save_match: Any,
    delete_audit: Any,
    shooter_root: Any,
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

    ``shooter_root(slug)`` resolves the directory a shooter's derived
    caches live under, and is called per shooter inside the fan-out rather
    than taken once as a single ``root``. Those caches are at
    ``<match_root>/shooters/<slug>/{audio,trimmed}/``, so one root cannot
    stand in for the whole match: passing the match root made
    ``project.audio_path(root)`` resolve to ``<match_root>/audio``, which
    does not exist, and ``purge_stage_artifacts``' directory-missing
    ``continue`` then reported a clean ``files_deleted: 0`` while every
    file survived.

    The advanced allocation counter rides along on that final save: it is
    assigned to ``match.next_stage_number`` immediately before
    ``save_match()``, in the same write as the stages it numbered. A crash
    before that point discards the mark and the stage list together, so
    the two can never disagree about which numbers have been spent.

    Cancellation is not instantaneous, so a worker can still land a write
    after step 2's purge. Because the counter never reissues a freed
    number, that write is inert garbage that can never be read as a live
    stage. Nothing cancels those jobs today (#645), which makes the
    counter the only thing standing between a late write and a new stage
    silently inheriting a removed one's audio.

    A single removed stage's cleanup (video release, audit delete, artifact
    purge) failing must not stop the rest of that shooter's update: this
    diff is computed against ``match.stages`` (see above ``diff_stage_list``
    call), which will no longer contain the removed stage once the match
    doc is saved, so a shooter who did not get to drop that stage on this
    pass would have no way to retry it later -- the diff would already read
    as "nothing to remove." Per-stage failures are therefore caught inline
    and recorded, while the shooter's stage-list update and save still go
    ahead. Only a failure loading or saving the project itself (outside any
    single stage's control) leaves that shooter unsaved -- except a lost
    optimistic-lock race on that save (hosted mode), which is not recorded
    as an ordinary per-shooter error but re-raised so the caller's 409
    handling applies, the same as a match-doc save conflict.
    """
    diff = diff_stage_list(
        list(match.stages),
        submitted,
        next_stage_number=match.resolve_next_stage_number(),
    )
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
    conflict_excs = _state_conflict_excs()

    for slug in shooter_slugs:
        result = ShooterStageEditResult(slug=slug)
        try:
            project = load_project(slug)

            for stage_number in diff.removed:
                try:
                    result.videos_unassigned += project.unassign_stage_videos(stage_number)
                    if delete_audit(slug, stage_number):
                        result.audit_docs_deleted += 1
                    counts = purge_stage_artifacts(project, shooter_root(slug), stage_number)
                    result.files_deleted += counts.files_deleted
                    result.objects_deleted += counts.objects_deleted
                    summary.errors.extend(f"{slug}: {e}" for e in counts.errors)
                except Exception as exc:  # noqa: BLE001 -- one stage's
                    # cleanup failing must not block this shooter's
                    # stage-list update, renames, adds, or save (see the
                    # docstring: the alternative permanently strands the
                    # shooter on this stage). Execution falls through to
                    # the save below, so this shooter still ends up with
                    # ``saved=True`` alongside the error -- that pairing
                    # is what tells a consumer "orphaned artifacts", not
                    # "unsaved shooter".
                    message = f"{slug} stage {stage_number}: {exc}"
                    summary.errors.append(message)
                    result.error = message

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
            result.saved = True
        except conflict_excs:
            # A lost optimistic-lock race on THIS shooter's project save
            # must not be swallowed into ``summary.errors`` as an ordinary
            # per-shooter failure -- that would return 200 with the
            # stage-list edit silently unsaved for this shooter. Re-raise
            # so it propagates past ``apply_stage_edit`` the same way a
            # match-doc save conflict already does, reaching the caller's
            # global ``StateConflictError`` handler (-> 409). This aborts
            # the fan-out; any earlier shooters in ``shooter_slugs`` this
            # pass already saved keep their changes.
            raise
        except Exception as exc:  # noqa: BLE001 -- one shooter must not
            # strand the others or the match doc. Reaching here means the
            # project was never saved, so any in-memory video-unassignment
            # this shooter accumulated is discarded along with it; only
            # already-performed audit/artifact deletions are real.
            # ``result.saved`` stays at its False default, which is the
            # difference between this error and the per-stage one above.
            summary.errors.append(f"{slug}: {exc}")
            result.error = str(exc)
            result.videos_unassigned = 0
        summary.shooters.append(result)

    match.stages = [s for s in match.stages if s.stage_number not in removed]
    for definition in match.stages:
        update = renamed_by_number.get(definition.stage_number)
        if update is not None:
            definition.stage_name = update.stage_name
            definition.stage_rounds = update.stage_rounds
    match.stages.extend(diff.added)
    match.stages.sort(key=lambda s: s.stage_number)
    # Persist the counter in the same save as the stages it numbered.
    # Assigned unconditionally, not only when something was added: on a
    # match whose document predates the field this is what turns the
    # backfilled value into a stored one.
    match.next_stage_number = diff.next_stage_number
    save_match()

    return summary
