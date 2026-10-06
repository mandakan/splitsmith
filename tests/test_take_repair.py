"""Repair a single take damaged by the old #1212 bug (#1214).

Before #1212 a role change, unassign or move on one stage's clip of a
multi-stage single take acted on the first stage's registration. That left
a stage listing the file twice (with one shared ``video_id``): the old code
appended the first stage's entry to the clicked stage. That is the only
unambiguous shape -- an empty covered stage or a tray copy looks exactly
like a legitimate per-clip remove or unassign, which do not update
``covers_stages``. The repair keeps each damaged stage's first entry (its
own registration) and drops the appended extras.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from splitsmith.match_project import MatchProject, RawVideo, StageEntry, StageVideo
from splitsmith.ui.server import create_app

TAKE = Path("raw/take.mp4")


def _take(covers: tuple[int, ...] = (1, 2, 3)) -> MatchProject:
    project = MatchProject(
        name="Take",
        stages=[
            StageEntry(
                stage_number=n,
                stage_name=f"S{n}",
                time_seconds=20.0,
                videos=[StageVideo(path=TAKE, role="primary", beep_time=float(n * 100), stage_number=n)],
            )
            for n in (1, 2, 3)
        ],
    )
    project.raw_videos.append(
        RawVideo(
            original_filename="take.mp4",
            size_bytes=1,
            uploaded_at=datetime.now(UTC),
            storage_path=str(TAKE),
            covers_stages=list(covers),
        )
    )
    return project


def _role_change_damage(project: MatchProject) -> None:
    """What the old role change on stage 2's clip did: stage 1's entry moved
    into stage 2, keeping stage 1's beep."""
    intruder = project.stage(1).videos.pop()
    intruder.role = "secondary"
    intruder.stage_number = 2
    project.stage(2).videos.append(intruder)


def _beeps(project: MatchProject) -> dict[int, list[float | None]]:
    return {s.stage_number: [v.beep_time for v in s.videos if v.path == TAKE] for s in project.stages}


# --- detection ------------------------------------------------------------------


def test_a_healthy_take_has_no_damage() -> None:
    assert _take().take_damage(str(TAKE)) == []


def test_a_stage_listing_the_file_twice_is_damaged() -> None:
    project = _take()
    _role_change_damage(project)
    assert project.take_damage(str(TAKE)) == [2]


def test_a_legitimate_remove_is_not_damage(tmp_path: Path) -> None:
    """covers_stages is not updated by a per-clip remove; an empty covered
    stage must not read as damage, or Repair would undo the user's edit."""
    project = _take()
    project.remove_video(TAKE, tmp_path, stage_number=3)
    assert project.take_damage(str(TAKE)) == []


def test_a_legitimate_unassign_is_not_damage() -> None:
    project = _take()
    project.assign_video(TAKE, from_stage_number=3, to_stage_number=None)
    assert project.take_damage(str(TAKE)) == []


def test_a_move_to_an_uncovered_stage_is_not_damage() -> None:
    project = _take(covers=(1, 2))
    project.stage(3).videos.clear()  # stage 3 is not part of the take
    project.assign_video(TAKE, from_stage_number=2, to_stage_number=3, role="secondary")
    assert project.take_damage(str(TAKE)) == []


# --- repair ---------------------------------------------------------------------


def test_repair_keeps_the_stage_s_own_entry() -> None:
    """The old bug appended the intruder, so the first entry is the stage's
    own registration, reviewed beep and all."""
    project = _take()
    project.stage(2).videos[0].beep_reviewed = True
    _role_change_damage(project)
    assert project.repair_take(str(TAKE)) == [2]
    kept = [v for v in project.stage(2).videos if v.path == TAKE]
    assert [(v.beep_time, v.beep_reviewed) for v in kept] == [(200.0, True)]
    # The stage the intruder was taken from stays empty: the repair cannot
    # tell that apart from a legitimate remove.
    assert _beeps(project) == {1: [], 2: [200.0], 3: [300.0]}


def test_repair_of_a_healthy_take_changes_nothing() -> None:
    project = _take()
    assert project.repair_take(str(TAKE)) == []
    assert _beeps(project) == {1: [100.0], 2: [200.0], 3: [300.0]}


# --- route ----------------------------------------------------------------------


def _client(tmp_path: Path, project: MatchProject) -> tuple[TestClient, str, Path]:
    from tests.conftest import scaffold_match

    root, shooter_root = scaffold_match(tmp_path, name="Take")
    (shooter_root / "raw").mkdir(parents=True, exist_ok=True)
    (shooter_root / "raw" / "take.mp4").write_bytes(b"source")
    base = MatchProject.load(shooter_root)
    base.stages = project.stages
    base.raw_videos = project.raw_videos
    base.unassigned_videos = project.unassigned_videos
    base.save(shooter_root)
    app = create_app(project_root=root, project_name="Take")
    match_id = app.state.splitsmith_state.matches.known_ids()[0]
    return TestClient(app), f"/api/matches/{match_id}/shooters/me", shooter_root


def test_route_repairs_a_stage_listing_the_file_twice(tmp_path: Path) -> None:
    project = _take()
    _role_change_damage(project)
    client, base, shooter_root = _client(tmp_path, project)
    trim = shooter_root / "trimmed" / f"stage2_cam_{project.stage(2).videos[0].video_id}_trimmed.mp4"
    trim.parent.mkdir(parents=True, exist_ok=True)
    trim.write_bytes(b"stage 2's good trim")

    resp = client.post(f"{base}/raw-videos/repair", json={"filename": "take.mp4"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["repaired_stages"] == [2]
    stages = {s["stage_number"]: s["videos"] for s in body["project"]["stages"]}
    assert [[v["beep_time"] for v in stages[n]] for n in (1, 2, 3)] == [[], [200.0], [300.0]]
    # The kept entry shares its id with the dropped one; its trim survives.
    assert trim.read_bytes() == b"stage 2's good trim"
    assert MatchProject.load(shooter_root).take_damage(str(TAKE)) == []


def test_route_on_a_healthy_take_is_a_no_op(tmp_path: Path) -> None:
    client, base, _root = _client(tmp_path, _take())
    resp = client.post(f"{base}/raw-videos/repair", json={"filename": "take.mp4"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["repaired_stages"] == []


def test_route_404s_an_unknown_file(tmp_path: Path) -> None:
    client, base, _root = _client(tmp_path, _take())
    resp = client.post(f"{base}/raw-videos/repair", json={"filename": "other.mp4"})
    assert resp.status_code == 404, resp.text
    assert "not registered" in resp.text
