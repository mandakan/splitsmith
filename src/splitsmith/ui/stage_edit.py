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

from pydantic import BaseModel, Field

from splitsmith.config import StageRounds
from splitsmith.match_model import MatchStageDefinition


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
