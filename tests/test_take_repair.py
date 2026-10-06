"""Repair a single take damaged by the old #1212 bug (#1214).

Before #1212 a role change, unassign or move on one stage's clip of a
multi-stage single take acted on the first stage's registration. That left
three shapes of damage: a covered stage listing the file twice (with one
shared ``video_id``), a covered stage with no entry for the file, and a
tray copy of the file while the take still covers stages. The repair drops
the file's entries on every damaged stage and the tray, and lets coverage
recreate exactly one registration per damaged stage; undamaged stages are
not touched.
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


def test_a_role_change_damage_flags_both_stages() -> None:
    project = _take()
    _role_change_damage(project)
    assert project.take_damage(str(TAKE)) == [1, 2]


def test_an_unassigned_registration_flags_its_stage() -> None:
    project = _take()
    stray = project.stage(3).videos.pop()
    stray.stage_number = None
    project.unassigned_videos.append(stray)
    assert project.take_damage(str(TAKE)) == [3]


def test_a_path_without_a_take_has_no_damage() -> None:
    project = _take()
    project.raw_videos.clear()
    assert project.take_damage(str(TAKE)) == []


# --- repair ---------------------------------------------------------------------


def test_repair_clears_only_the_damaged_stages() -> None:
    project = _take()
    _role_change_damage(project)
    removed = project.repair_take(str(TAKE))
    assert sorted(n for n, _ in removed) == [2, 2]
    # Stages 1 and 2 are empty and wait for coverage to recreate them;
    # stage 3 keeps its own entry and beep.
    assert _beeps(project) == {1: [], 2: [], 3: [300.0]}


def test_repair_drops_a_tray_copy() -> None:
    project = _take()
    stray = project.stage(3).videos.pop()
    stray.stage_number = None
    project.unassigned_videos.append(stray)
    project.repair_take(str(TAKE))
    assert project.unassigned_videos == []
    assert _beeps(project) == {1: [100.0], 2: [200.0], 3: []}


def test_repair_of_a_healthy_take_changes_nothing() -> None:
    project = _take()
    assert project.repair_take(str(TAKE)) == []
    assert _beeps(project) == {1: [100.0], 2: [200.0], 3: [300.0]}


# --- route ----------------------------------------------------------------------


def _client(tmp_path: Path, project: MatchProject) -> tuple[TestClient, str]:
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
    return TestClient(app), f"/api/matches/{match_id}/shooters/me"


def test_route_repairs_to_one_registration_per_stage(tmp_path: Path) -> None:
    project = _take()
    _role_change_damage(project)
    client, base = _client(tmp_path, project)

    resp = client.post(f"{base}/raw-videos/repair", json={"filename": "take.mp4"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["repaired_stages"] == [1, 2]
    stages = {s["stage_number"]: s["videos"] for s in body["project"]["stages"]}
    assert [len(stages[n]) for n in (1, 2, 3)] == [1, 1, 1]
    # Repaired stages wait for a fresh beep; the untouched stage keeps its own.
    assert [stages[n][0]["beep_time"] for n in (1, 2, 3)] == [None, None, 300.0]
    assert len({stages[n][0]["video_id"] for n in (1, 2, 3)}) == 3


def test_route_on_a_healthy_take_is_a_no_op(tmp_path: Path) -> None:
    client, base = _client(tmp_path, _take())
    resp = client.post(f"{base}/raw-videos/repair", json={"filename": "take.mp4"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["repaired_stages"] == []


def test_route_404s_an_unknown_file(tmp_path: Path) -> None:
    client, base = _client(tmp_path, _take())
    resp = client.post(f"{base}/raw-videos/repair", json={"filename": "other.mp4"})
    assert resp.status_code == 404, resp.text
