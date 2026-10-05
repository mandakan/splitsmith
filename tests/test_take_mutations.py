"""Mutations on one stage's clip of a multi-stage single take (#1212).

A single take registers one source path on several stages, each with its
own ``StageVideo`` (beep, ``video_id``, trim). Move, role change and
remove used to look the video up by path alone and act on the FIRST
stage's registration -- moving stage 1's video into stage 2, unassigning
stage 1 when the user meant stage 3, and deleting ``raw/`` while other
stages still used it. The request now names the clip's stage.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui.server import create_app

TAKE = Path("raw/take.mp4")


def _take_stages() -> list[StageEntry]:
    return [
        StageEntry(
            stage_number=n,
            stage_name=f"S{n}",
            time_seconds=20.0,
            videos=[StageVideo(path=TAKE, role="primary", beep_time=float(n * 100), stage_number=n)],
        )
        for n in (1, 2, 3)
    ]


def _beeps(project: MatchProject) -> dict[int, list[tuple[str, float | None]]]:
    return {s.stage_number: [(v.role, v.beep_time) for v in s.videos] for s in project.stages}


# --- model ---------------------------------------------------------------------


def test_role_change_acts_on_the_named_stage() -> None:
    project = MatchProject(name="Take", stages=_take_stages())
    project.assign_video(TAKE, from_stage_number=2, to_stage_number=2, role="secondary")
    assert _beeps(project) == {
        1: [("primary", 100.0)],
        2: [("primary", 200.0)],  # auto-upgraded: the stage has no other primary
        3: [("primary", 300.0)],
    }
    project.assign_video(TAKE, from_stage_number=2, to_stage_number=2, role="ignored")
    assert _beeps(project)[2] == [("ignored", 200.0)]
    assert _beeps(project)[1] == [("primary", 100.0)]


def test_unassign_takes_the_named_stage() -> None:
    project = MatchProject(name="Take", stages=_take_stages())
    moved = project.assign_video(TAKE, from_stage_number=3, to_stage_number=None)
    assert moved.beep_time == 300.0
    assert _beeps(project) == {1: [("primary", 100.0)], 2: [("primary", 200.0)], 3: []}
    assert [v.beep_time for v in project.unassigned_videos] == [300.0]


def test_moving_onto_a_stage_that_already_holds_the_file_is_refused() -> None:
    project = MatchProject(name="Take", stages=_take_stages())
    with pytest.raises(ValueError, match="already"):
        project.assign_video(TAKE, from_stage_number=3, to_stage_number=2, role="secondary")
    assert _beeps(project) == {1: [("primary", 100.0)], 2: [("primary", 200.0)], 3: [("primary", 300.0)]}


def test_a_stage_that_does_not_hold_the_file_is_a_key_error() -> None:
    project = MatchProject(name="Take", stages=_take_stages())
    project.stages.append(StageEntry(stage_number=4, stage_name="S4", time_seconds=20.0))
    with pytest.raises(KeyError):
        project.assign_video(TAKE, from_stage_number=4, to_stage_number=None)


def test_remove_takes_the_named_stage_and_keeps_the_shared_raw(tmp_path: Path) -> None:
    project = MatchProject(name="Take", stages=_take_stages())
    plan = project.remove_video(TAKE, tmp_path, stage_number=3)
    assert plan.stage_number == 3
    assert plan.raw_link_path is None  # stages 1 and 2 still stream it
    assert plan.trimmed_cache_path is not None and "stage3_" in plan.trimmed_cache_path.name
    assert _beeps(project) == {1: [("primary", 100.0)], 2: [("primary", 200.0)], 3: []}


def test_removing_the_last_registration_releases_the_raw(tmp_path: Path) -> None:
    project = MatchProject(name="Take", stages=_take_stages())
    for n in (3, 1):
        assert project.remove_video(TAKE, tmp_path, stage_number=n).raw_link_path is None
    plan = project.remove_video(TAKE, tmp_path, stage_number=2)
    assert plan.raw_link_path is not None


def test_without_a_stage_the_first_registration_still_answers(tmp_path: Path) -> None:
    """Old clients send no stage; a single-stage video behaves as before."""
    project = MatchProject(name="Take", stages=_take_stages())
    project.assign_video(TAKE, to_stage_number=None)
    assert [v.beep_time for v in project.unassigned_videos] == [100.0]
    assert _beeps(project)[2] == [("primary", 200.0)]


# --- routes --------------------------------------------------------------------


def _client(tmp_path: Path) -> tuple[TestClient, str, Path]:
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Take")
    (shooter_root / "raw").mkdir(parents=True, exist_ok=True)
    (shooter_root / "raw" / "take.mp4").write_bytes(b"source")
    project = MatchProject.load(shooter_root)
    project.stages = _take_stages()
    project.save(shooter_root)
    app = create_app(project_root=root, project_name="Take")
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    return TestClient(app), f"/api/matches/{match_id}/shooters/me", shooter_root


def _route_beeps(project_json: dict) -> dict[int, list[tuple[str, float | None]]]:
    return {
        s["stage_number"]: [(v["role"], v["beep_time"]) for v in s["videos"]] for s in project_json["stages"]
    }


def test_route_role_change_names_the_stage(tmp_path: Path) -> None:
    client, base, _root = _client(tmp_path)
    resp = client.post(
        f"{base}/assignments/move",
        json={"video_path": "raw/take.mp4", "from_stage_number": 2, "to_stage_number": 2, "role": "ignored"},
    )
    assert resp.status_code == 200, resp.text
    assert _route_beeps(resp.json()) == {
        1: [("primary", 100.0)],
        2: [("ignored", 200.0)],
        3: [("primary", 300.0)],
    }


def test_route_move_onto_a_stage_holding_the_file_is_a_409(tmp_path: Path) -> None:
    client, base, _root = _client(tmp_path)
    resp = client.post(
        f"{base}/assignments/move",
        json={
            "video_path": "raw/take.mp4",
            "from_stage_number": 3,
            "to_stage_number": 2,
            "role": "secondary",
        },
    )
    assert resp.status_code == 409, resp.text


def test_route_remove_names_the_stage_and_keeps_the_raw(tmp_path: Path) -> None:
    client, base, shooter_root = _client(tmp_path)
    resp = client.post(f"{base}/videos/remove", json={"video_path": "raw/take.mp4", "stage_number": 3})
    assert resp.status_code == 200, resp.text
    assert _route_beeps(resp.json()["project"]) == {
        1: [("primary", 100.0)],
        2: [("primary", 200.0)],
        3: [],
    }
    assert (shooter_root / "raw" / "take.mp4").exists()


def test_promote_takes_the_registration_on_the_target_stage(tmp_path: Path) -> None:
    """Stage 2 has another primary and the take as a secondary: promoting the
    take there promotes stage 2's registration, not stage 1's."""
    stages = _take_stages()
    stages[1].videos[0].role = "secondary"
    stages[1].videos.insert(0, StageVideo(path=Path("raw/other.mp4"), role="primary", stage_number=2))
    project = MatchProject(name="Take", stages=stages)
    promoted = project.swap_primary(TAKE, root=tmp_path, stage_number=2, backup_audit=False)
    assert promoted.stage_number == 2
    assert [(str(v.path), v.role) for v in project.stage(2).videos] == [
        ("raw/other.mp4", "secondary"),
        ("raw/take.mp4", "primary"),
    ]
    assert _beeps(project)[1] == [("primary", 100.0)]
    assert _beeps(project)[3] == [("primary", 300.0)]


def test_a_stage_damaged_by_the_old_bug_loses_one_entry_at_a_time(tmp_path: Path) -> None:
    """Before #1212 a role change could leave a stage holding two entries of
    one file. Removing one must not take the other with it."""
    stages = _take_stages()
    stages[1].videos.append(StageVideo(path=TAKE, role="secondary", beep_time=100.0, stage_number=2))
    project = MatchProject(name="Take", stages=stages)
    project.remove_video(TAKE, tmp_path, stage_number=2)
    assert _beeps(project)[2] == [("secondary", 100.0)]
