"""Unit tests for the stage-list editor engine (#521)."""

import pytest

from splitsmith.config import StageRounds
from splitsmith.match_model import MatchStageDefinition
from splitsmith.ui.stage_edit import (
    StageEditError,
    StageListDiff,
    SubmittedStage,
    diff_stage_list,
)


def _existing(*numbers: int) -> list[MatchStageDefinition]:
    return [MatchStageDefinition(stage_number=n, stage_name=f"Stage {n}") for n in numbers]


def test_rename_only_is_not_a_removal() -> None:
    diff = diff_stage_list(
        _existing(1, 2, 3),
        [
            SubmittedStage(stage_number=1, stage_name="El Presidente"),
            SubmittedStage(stage_number=2, stage_name="Stage 2"),
            SubmittedStage(stage_number=3, stage_name="Stage 3"),
        ],
    )
    assert diff.removed == []
    assert diff.added == []
    assert [s.stage_number for s in diff.renamed] == [1]
    assert diff.renamed[0].stage_name == "El Presidente"
    assert diff.unchanged == [2, 3]


def test_removal_is_detected_by_absence() -> None:
    diff = diff_stage_list(
        _existing(1, 2, 3, 4, 5),
        [SubmittedStage(stage_number=n, stage_name=f"Stage {n}") for n in (1, 2, 4, 5)],
    )
    assert diff.removed == [3]
    assert diff.added == []


def test_added_rows_allocate_above_the_current_max_not_the_gap() -> None:
    """The freed number 3 must never be handed out again."""
    diff = diff_stage_list(
        _existing(1, 2, 4, 5),
        [
            SubmittedStage(stage_number=1, stage_name="Stage 1"),
            SubmittedStage(stage_number=2, stage_name="Stage 2"),
            SubmittedStage(stage_number=4, stage_name="Stage 4"),
            SubmittedStage(stage_number=5, stage_name="Stage 5"),
            SubmittedStage(stage_number=None, stage_name="Standards"),
        ],
    )
    assert [s.stage_number for s in diff.added] == [6]
    assert diff.added[0].stage_name == "Standards"


def test_two_added_rows_get_consecutive_numbers() -> None:
    diff = diff_stage_list(
        _existing(1, 2),
        [
            SubmittedStage(stage_number=1, stage_name="Stage 1"),
            SubmittedStage(stage_number=2, stage_name="Stage 2"),
            SubmittedStage(stage_number=None, stage_name="A"),
            SubmittedStage(stage_number=None, stage_name="B"),
        ],
    )
    assert [s.stage_number for s in diff.added] == [3, 4]


def test_add_to_an_empty_existing_list_starts_at_one() -> None:
    diff = diff_stage_list([], [SubmittedStage(stage_number=None, stage_name="Only")])
    assert [s.stage_number for s in diff.added] == [1]


def test_unknown_stage_number_is_rejected_not_implicitly_added() -> None:
    with pytest.raises(StageEditError, match="99"):
        diff_stage_list(
            _existing(1, 2),
            [
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
                SubmittedStage(stage_number=99, stage_name="Ghost"),
            ],
        )


def test_empty_submission_is_rejected() -> None:
    with pytest.raises(StageEditError, match="at least one stage"):
        diff_stage_list(_existing(1, 2), [])


def test_duplicate_stage_number_is_rejected() -> None:
    with pytest.raises(StageEditError, match="duplicate"):
        diff_stage_list(
            _existing(1, 2),
            [
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=1, stage_name="Stage 1 again"),
            ],
        )


def test_blank_stage_name_is_rejected() -> None:
    with pytest.raises(StageEditError, match="stage_name"):
        diff_stage_list(
            _existing(1),
            [SubmittedStage(stage_number=1, stage_name="   ")],
        )


def test_stage_name_is_trimmed() -> None:
    diff = diff_stage_list(
        _existing(1),
        [SubmittedStage(stage_number=1, stage_name="  Padded  ")],
    )
    assert diff.renamed[0].stage_name == "Padded"


def test_changed_stage_rounds_counts_as_a_rename() -> None:
    diff = diff_stage_list(
        _existing(1),
        [
            SubmittedStage(
                stage_number=1,
                stage_name="Stage 1",
                stage_rounds=StageRounds(expected=12),
            )
        ],
    )
    assert [s.stage_number for s in diff.renamed] == [1]
    assert diff.renamed[0].stage_rounds is not None
    assert diff.renamed[0].stage_rounds.expected == 12
