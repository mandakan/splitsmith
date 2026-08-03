"""Unit tests for the stage-list editor engine (#521)."""

import pytest

from splitsmith.config import StageRounds
from splitsmith.match_model import MatchStageDefinition
from splitsmith.ui.stage_edit import (
    StageEditError,
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


class _FakeObject:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeStorage:
    """Minimal stand-in for the S3/R2 storage backend.

    Mirrors the three methods the purge uses: ``list(prefix)`` yielding
    objects with a ``.path``, and ``delete(path)``. ``fail_on`` makes one
    delete raise so the error-collection path can be exercised.
    """

    def __init__(self, paths: list[str], *, fail_on: str | None = None) -> None:
        self.paths = list(paths)
        self.deleted: list[str] = []
        self.fail_on = fail_on

    def list(self, prefix: str):
        return [_FakeObject(p) for p in self.paths if p.startswith(prefix)]

    def delete(self, path: str) -> None:
        if path == self.fail_on:
            raise RuntimeError("boom")
        self.deleted.append(path)
        self.paths.remove(path)


def _project_with_dirs(tmp_path):
    from splitsmith.ui.project import MatchProject

    project = MatchProject(name="M")
    project.init_placeholder_stages(3)
    project.audio_path(tmp_path).mkdir(parents=True, exist_ok=True)
    project.trimmed_path(tmp_path).mkdir(parents=True, exist_ok=True)
    return project


def test_purge_deletes_local_artifacts_for_the_stage(tmp_path) -> None:
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    audio = project.audio_path(tmp_path)
    trimmed = project.trimmed_path(tmp_path)
    (audio / "stage3_cam_abc.wav").write_bytes(b"x")
    (audio / "stage3_cam_abc_audit.wav").write_bytes(b"x")
    (audio / "stage3_primary.wav").write_bytes(b"x")
    (audio / "stage3_audit.peaks-100.json").write_bytes(b"x")
    (trimmed / "stage3_cam_abc_trimmed.mp4").write_bytes(b"x")
    (trimmed / "stage3_trimmed.params.json").write_bytes(b"x")

    counts = purge_stage_artifacts(project, tmp_path, 3)

    assert counts.files_deleted == 6
    assert counts.errors == []
    assert list(audio.glob("stage3_*")) == []
    assert list(trimmed.glob("stage3_*")) == []


def test_purge_leaves_neighbouring_stages_untouched(tmp_path) -> None:
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    audio = project.audio_path(tmp_path)
    (audio / "stage3_cam_abc.wav").write_bytes(b"x")
    keep = audio / "stage4_cam_abc.wav"
    keep.write_bytes(b"keep")

    purge_stage_artifacts(project, tmp_path, 3)

    assert keep.read_bytes() == b"keep"


def test_purging_stage_3_does_not_delete_stage_30(tmp_path) -> None:
    """The trailing underscore in the glob is what makes this pass."""
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    audio = project.audio_path(tmp_path)
    (audio / "stage3_cam_abc.wav").write_bytes(b"x")
    two_digit = audio / "stage30_cam_abc.wav"
    two_digit.write_bytes(b"keep")

    counts = purge_stage_artifacts(project, tmp_path, 3)

    assert counts.files_deleted == 1
    assert two_digit.read_bytes() == b"keep"


def test_purge_deletes_storage_objects_for_the_stage(tmp_path) -> None:
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    scope = "matches/m1/shooters/me"
    storage = _FakeStorage(
        [
            f"{scope}/audio/stage3_cam_abc.wav",
            f"{scope}/audio/stage30_cam_abc.wav",
            f"{scope}/audio/stage4_cam_abc.wav",
            f"{scope}/trimmed/stage3_cam_abc_trimmed.mp4",
            f"{scope}/trimmed/stage3_cam_abc_trimmed.params.json",
        ]
    )
    project.bind_storage(storage, scope=scope)

    counts = purge_stage_artifacts(project, tmp_path, 3)

    assert counts.objects_deleted == 3
    assert sorted(storage.deleted) == sorted(
        [
            f"{scope}/audio/stage3_cam_abc.wav",
            f"{scope}/trimmed/stage3_cam_abc_trimmed.mp4",
            f"{scope}/trimmed/stage3_cam_abc_trimmed.params.json",
        ]
    )
    assert f"{scope}/audio/stage30_cam_abc.wav" in storage.paths


def test_purge_collects_storage_errors_instead_of_raising(tmp_path) -> None:
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    scope = "matches/m1/shooters/me"
    doomed = f"{scope}/audio/stage3_cam_abc.wav"
    storage = _FakeStorage(
        [doomed, f"{scope}/trimmed/stage3_cam_abc_trimmed.mp4"],
        fail_on=doomed,
    )
    project.bind_storage(storage, scope=scope)

    counts = purge_stage_artifacts(project, tmp_path, 3)

    assert counts.objects_deleted == 1
    assert len(counts.errors) == 1
    assert "boom" in counts.errors[0]


def test_purge_with_no_storage_bound_is_local_only(tmp_path) -> None:
    from splitsmith.ui.stage_edit import purge_stage_artifacts

    project = _project_with_dirs(tmp_path)
    (project.audio_path(tmp_path) / "stage3_cam_abc.wav").write_bytes(b"x")

    counts = purge_stage_artifacts(project, tmp_path, 3)

    assert counts.files_deleted == 1
    assert counts.objects_deleted == 0
