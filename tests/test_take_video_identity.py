"""Identity + coverage semantics for one source file shared by N stages."""

from pathlib import Path

from splitsmith.ui.project import MatchProject, StageEntry, StageVideo


def _project_with_two_stages() -> MatchProject:
    return MatchProject(
        name="take-test",
        stages=[
            StageEntry(stage_number=1, stage_name="One", time_seconds=20.0),
            StageEntry(stage_number=2, stage_name="Two", time_seconds=25.0),
        ],
    )


def test_video_ids_differ_across_stages_for_shared_path() -> None:
    proj = _project_with_two_stages()
    shared = Path("raw/take.mp4")
    proj.stages[0].videos.append(StageVideo(path=shared, role="primary", stage_number=1))
    proj.stages[1].videos.append(StageVideo(path=shared, role="primary", stage_number=2))
    ids = {proj.stages[0].videos[0].video_id, proj.stages[1].videos[0].video_id}
    assert len(ids) == 2


def test_unassigned_video_id_is_path_only_hash() -> None:
    import hashlib

    v = StageVideo(path=Path("raw/take.mp4"))
    assert v.stage_number is None
    expected = hashlib.blake2s(b"raw/take.mp4", digest_size=6).hexdigest()
    assert v.video_id == expected


def test_load_stamps_stage_numbers() -> None:
    proj = _project_with_two_stages()
    shared = Path("raw/take.mp4")
    proj.stages[0].videos.append(StageVideo(path=shared, role="primary"))
    proj.unassigned_videos.append(StageVideo(path=Path("raw/other.mp4")))
    # Round-trip through dump/validate simulates a project load from disk.
    reloaded = MatchProject.model_validate(proj.model_dump(mode="json"))
    assert reloaded.stages[0].videos[0].stage_number == 1
    assert reloaded.unassigned_videos[0].stage_number is None


def test_assign_video_restamps_stage_number() -> None:
    proj = _project_with_two_stages()
    proj.unassigned_videos.append(StageVideo(path=Path("raw/take.mp4")))
    v = proj.assign_video(Path("raw/take.mp4"), to_stage_number=2)
    assert v.stage_number == 2
    back = proj.assign_video(Path("raw/take.mp4"), to_stage_number=None)
    assert back.stage_number is None
