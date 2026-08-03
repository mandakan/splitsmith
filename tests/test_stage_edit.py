"""Unit tests for the stage-list editor engine (#521)."""

import asyncio
from pathlib import Path

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


def _match_with_stages(*numbers: int):
    from splitsmith.match_model import Match

    match = Match(name="M")
    match.stages = _existing(*numbers)
    return match


def _harness(tmp_path, slugs, stage_numbers, *, order: list[str] | None = None):
    """Build in-memory projects plus the injected callables.

    ``hooks["save_match"]`` defaults to a no-op so most tests don't need to
    supply one; the ordering test overrides that key directly rather than
    also passing ``save_match=`` alongside ``**hooks`` (which would collide).

    ``order`` is an optional shared list: when given, ``cancel_jobs``
    appends ``"cancel"`` to it, so a caller that also wires ``save_project``
    / ``save_match`` to append their own markers gets one combined
    timeline proving cancellation happens before the per-shooter fan-out.
    """
    from splitsmith.ui.project import MatchProject

    projects = {}
    for slug in slugs:
        project = MatchProject(name="M")
        project.init_placeholder_stages(max(stage_numbers))
        project.audio_path(tmp_path).mkdir(parents=True, exist_ok=True)
        project.trimmed_path(tmp_path).mkdir(parents=True, exist_ok=True)
        projects[slug] = project

    saved: list[str] = []
    audits_deleted: list[tuple[str, int]] = []
    cancelled: list[set[int]] = []

    async def cancel_jobs(stage_numbers_arg):
        cancelled.append(set(stage_numbers_arg))
        if order is not None:
            order.append("cancel")
        return len(stage_numbers_arg)

    hooks = {
        "load_project": lambda slug: projects[slug],
        "save_project": lambda slug, project: saved.append(slug),
        "delete_audit": lambda slug, n: (audits_deleted.append((slug, n)) or True),
        "cancel_jobs": cancel_jobs,
        "save_match": lambda: None,
    }
    return projects, saved, audits_deleted, cancelled, hooks


def test_apply_removes_the_stage_from_every_shooter(tmp_path) -> None:
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    projects, saved, audits_deleted, cancelled, hooks = _harness(
        tmp_path, ["anna", "erik", "mathias"], [1, 2, 3]
    )

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["anna", "erik", "mathias"],
            **hooks,
        )
    )

    assert summary.removed == [3]
    assert [s.stage_number for s in match.stages] == [1, 2]
    for project in projects.values():
        assert [s.stage_number for s in project.stages] == [1, 2]
    assert sorted(saved) == ["anna", "erik", "mathias"]
    assert sorted(audits_deleted) == [("anna", 3), ("erik", 3), ("mathias", 3)]


def test_apply_preserves_untouched_stages_artifacts(tmp_path) -> None:
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    projects, _saved, _audits, _cancelled, hooks = _harness(tmp_path, ["me"], [1, 2, 3])
    audio = projects["me"].audio_path(tmp_path)
    (audio / "stage3_cam_a.wav").write_bytes(b"gone")
    survivor = audio / "stage2_cam_a.wav"
    survivor.write_bytes(b"keep")

    asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["me"],
            **hooks,
        )
    )

    assert survivor.read_bytes() == b"keep"
    assert not (audio / "stage3_cam_a.wav").exists()


def test_apply_cancels_jobs_for_removed_stages_only(tmp_path) -> None:
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    _p, _saved, _audits, cancelled, hooks = _harness(tmp_path, ["me"], [1, 2, 3])

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["me"],
            **hooks,
        )
    )

    assert cancelled == [{3}]
    assert summary.jobs_cancelled == 1


def test_apply_with_no_removals_cancels_nothing(tmp_path) -> None:
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2)
    projects, _saved, audits_deleted, cancelled, hooks = _harness(tmp_path, ["me"], [1, 2])

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Renamed"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["me"],
            **hooks,
        )
    )

    assert cancelled == []
    assert audits_deleted == []
    assert summary.jobs_cancelled == 0
    assert summary.renamed == [1]
    assert match.stages[0].stage_name == "Renamed"
    # Rename fan-out: the shooter's own project.stages must be updated too,
    # not just the match doc -- deleting this line leaves the suite green
    # even if the per-shooter rename loop is removed entirely.
    assert projects["me"].stages[0].stage_name == "Renamed"


def test_apply_adds_a_stage_to_match_and_every_shooter(tmp_path) -> None:
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2)
    projects, _saved, _audits, _cancelled, hooks = _harness(tmp_path, ["a", "b"], [1, 2])

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
                SubmittedStage(stage_number=None, stage_name="Standards"),
            ],
            shooter_slugs=["a", "b"],
            **hooks,
        )
    )

    assert summary.added == [3]
    assert [s.stage_number for s in match.stages] == [1, 2, 3]
    for project in projects.values():
        assert [s.stage_number for s in project.stages] == [1, 2, 3]
        added = project.stages[-1]
        assert added.stage_name == "Standards"
        assert added.time_seconds == 0.0
        assert added.placeholder is True


def test_apply_reports_per_shooter_counts(tmp_path) -> None:
    from splitsmith.ui.project import StageVideo
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    projects, _saved, _audits, _cancelled, hooks = _harness(tmp_path, ["haves", "havenots"], [1, 2, 3])
    projects["haves"].stages[2].videos = [StageVideo(path=Path("a.mp4"), role="primary")]

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["haves", "havenots"],
            **hooks,
        )
    )

    by_slug = {s.slug: s for s in summary.shooters}
    assert by_slug["haves"].videos_unassigned == 1
    assert by_slug["havenots"].videos_unassigned == 0
    assert projects["haves"].unassigned_videos[0].role == "secondary"


def test_apply_saves_the_match_doc_after_every_shooter(tmp_path) -> None:
    """Match doc last: a crash mid-fan-out must leave the canonical list
    describing the pre-edit world, never a match promising stages the
    shooters no longer have."""
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    order: list[str] = []
    projects, _saved, _audits, _cancelled, hooks = _harness(tmp_path, ["a", "b"], [1, 2, 3], order=order)
    hooks["save_project"] = lambda slug, project: order.append(f"project:{slug}")

    def save_match() -> None:
        order.append("match")

    hooks["save_match"] = save_match

    asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["a", "b"],
            **hooks,
        )
    )

    # Cancellation (step 1) must land before any per-shooter save (step 2),
    # which in turn must land before the match doc save (step 3).
    assert order == ["cancel", "project:a", "project:b", "match"]


def test_apply_collects_a_failing_shooter_and_still_commits(tmp_path) -> None:
    """A shooter whose project doc genuinely fails to save is collected as
    an error but does not strand the other shooter or block the match
    commit.

    The failing slug goes FIRST (``["bad", "ok"]``): every assertion here
    is about what happens *after* "bad" fails, so an abort-on-first-error
    implementation (a stray ``break``/``return`` in the shooter loop)
    cannot pass by accident the way it could if "bad" were last and
    nothing after it were observed.

    The failure lives in ``save_project``, not ``delete_audit``: a
    per-stage cleanup failure is recoverable (see
    ``test_apply_recovers_a_shooter_when_one_stage_cleanup_step_fails``
    below) and no longer strands the shooter, so exercising "one shooter
    fails outright" now needs a failure outside any single stage's
    control.
    """
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    projects, saved, _audits, _cancelled, hooks = _harness(tmp_path, ["bad", "ok"], [1, 2, 3])

    def save_project(slug: str, project) -> None:
        if slug == "bad":
            raise RuntimeError("state store down")
        saved.append(slug)

    hooks["save_project"] = save_project

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["bad", "ok"],
            **hooks,
        )
    )

    assert len(summary.errors) == 1
    assert "state store down" in summary.errors[0]
    assert [s.stage_number for s in match.stages] == [1, 2]
    # The loop kept going past "bad" instead of aborting: an
    # abort-on-first-error implementation would leave "ok" unsaved and
    # would never reach the point of appending its result.
    assert saved == ["ok"]
    assert [s.slug for s in summary.shooters] == ["bad", "ok"]


def test_apply_resets_videos_unassigned_and_records_error_when_save_fails(tmp_path) -> None:
    """When a shooter's project never gets saved, any in-memory
    video-unassignment counted along the way must not be reported as if it
    happened -- the video is still bound to its old stage on disk. Counts
    for cleanup that genuinely already ran (the audit-doc delete here)
    survive, because those are independent, already-committed side
    effects, not part of the unsaved project doc.
    """
    from splitsmith.ui.project import StageVideo
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    projects, _saved, _audits, _cancelled, hooks = _harness(tmp_path, ["me"], [1, 2, 3])
    projects["me"].stages[2].videos = [StageVideo(path=Path("a.mp4"), role="primary")]

    def save_project(slug: str, project) -> None:
        raise RuntimeError("disk full")

    hooks["save_project"] = save_project

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["me"],
            **hooks,
        )
    )

    result = summary.shooters[0]
    assert result.error is not None
    assert "disk full" in result.error
    assert result.videos_unassigned == 0
    assert result.audit_docs_deleted == 1


def test_apply_recovers_a_shooter_when_one_stage_cleanup_step_fails(tmp_path) -> None:
    """A per-stage cleanup failure (here, ``delete_audit`` raising) must
    not strand the shooter: their stage-list update and save must still go
    through, because ``diff_stage_list`` runs against ``match.stages``,
    which will have already forgotten the removed stage by the next edit --
    there would be no way to retry a stranded shooter's cleanup later.
    """
    from splitsmith.ui.stage_edit import SubmittedStage, apply_stage_edit

    match = _match_with_stages(1, 2, 3)
    projects, saved, _audits, _cancelled, hooks = _harness(tmp_path, ["me"], [1, 2, 3])

    def delete_audit(slug: str, n: int) -> bool:
        raise RuntimeError("state store down")

    hooks["delete_audit"] = delete_audit

    summary = asyncio.run(
        apply_stage_edit(
            match=match,
            root=tmp_path,
            submitted=[
                SubmittedStage(stage_number=1, stage_name="Stage 1"),
                SubmittedStage(stage_number=2, stage_name="Stage 2"),
            ],
            shooter_slugs=["me"],
            **hooks,
        )
    )

    assert [s.stage_number for s in projects["me"].stages] == [1, 2]
    assert saved == ["me"]
    assert len(summary.errors) == 1
    assert "state store down" in summary.errors[0]
    result = summary.shooters[0]
    assert result.error is not None
    assert "state store down" in result.error
