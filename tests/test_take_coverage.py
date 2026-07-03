"""Tests for PATCH /api/shooters/{slug}/raw-videos/coverage and the
sequential-take chain-advance on manual beep (controller addendum).

Local-mode TestClient throughout; hosted attach job-enqueue is an
orthogonal concern covered by test_hosted_raw_upload.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from splitsmith.ui.jobs import Job, JobBodyRegistry, JobStatus
from splitsmith.ui.project import MatchProject, StageEntry

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_auto_beep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


class _FakeJobBackend:
    """Records submitted jobs without running them."""

    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []
        self.bodies = JobBodyRegistry()

    @property
    def is_shutting_down(self) -> bool:
        return False

    def active_count(self) -> int:
        return 0

    def begin_shutdown(self) -> None:
        pass

    def wait_for_drain(self, _timeout: float) -> bool:
        return True

    async def submit(
        self,
        *,
        kind: str,
        args: dict[str, Any] | None = None,
        stage_number: int | None = None,
        video_id: str | None = None,
    ) -> Job:
        import secrets

        self.submitted.append(
            {"kind": kind, "args": args, "stage_number": stage_number, "video_id": video_id}
        )
        now = datetime.now(UTC)
        return Job(
            id=secrets.token_hex(8),
            kind=kind,
            status=JobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

    async def find_active(
        self,
        *,
        kind: str | None = None,
        stage_number: int | None = None,
        video_id: str | None = None,
    ) -> None:
        return None

    async def get(self, _id: str) -> None:
        return None

    async def list(self) -> list:
        return []

    async def cancel(self, _id: str) -> None:
        return None

    async def cancel_active_for_user(self) -> int:
        return 0

    async def acknowledge(self, _id: str) -> None:
        return None

    async def acknowledge_all_failures(self) -> list:
        return []


_T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)


def _build_local_app(tmp_path: Path, *, stage_count: int = 3, with_scorecard: bool = False):
    """Create a local-mode app with stages, a registered take video, and a fake job backend.

    Stages are written directly to the project model so tests control
    ``scorecard_updated_at`` precisely (sequential mode = None,
    scoreboard mode = a real datetime). Returns (client, app, fake_jobs).
    """
    from splitsmith.match_model import Match
    from tests.test_ui_server import _match_create_app, _MatchClient

    project_root = tmp_path / "match"
    app = _match_create_app(project_root=project_root, project_name="Coverage Test")
    client = _MatchClient(app)

    # Add stages directly so we can set scorecard_updated_at to None
    # (the scoreboard import endpoint requires it via StageData schema).
    # Use MatchProject.load on the on-disk path; avoid state.shooter_root()
    # which requires the current_match_root ContextVar set by request middleware.
    shooter_root = Match.shooter_root(project_root, "me")
    project = MatchProject.load(shooter_root)
    for sn in range(1, stage_count + 1):
        scat = _T0 if with_scorecard else None
        project.stages.append(
            StageEntry(
                stage_number=sn,
                stage_name=f"Stage {sn}",
                time_seconds=10.0,
                scorecard_updated_at=scat,
            )
        )
    project.save(shooter_root)

    # Place take.mp4 on disk and register via scan.
    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    (src_dir / "take.mp4").write_bytes(b"\x00")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )

    state = app.state.splitsmith_state
    fake_jobs = _FakeJobBackend()
    state.jobs = fake_jobs

    return client, app, fake_jobs


# ---------------------------------------------------------------------------
# PATCH /api/shooters/{slug}/raw-videos/coverage
# ---------------------------------------------------------------------------


def test_patch_coverage_creates_stagevideos_and_clears_unassigned(tmp_path: Path) -> None:
    """PATCH coverage=[2,1] creates two StageVideos, removes the unassigned entry,
    and persists the RawVideo with declared order preserved."""
    client, _, _ = _build_local_app(tmp_path)

    resp = client.patch(
        "/api/shooters/me/raw-videos/coverage",
        json={"filename": "take.mp4", "covers_stages": [2, 1]},
    )
    assert resp.status_code == 200, resp.text

    proj = client.get("/api/shooters/me/project").json()
    stages = {s["stage_number"]: s for s in proj["stages"]}
    assert len(stages[1]["videos"]) == 1
    assert len(stages[2]["videos"]) == 1
    assert str(stages[1]["videos"][0]["path"]) == "raw/take.mp4"
    assert str(stages[2]["videos"][0]["path"]) == "raw/take.mp4"

    # Unassigned entry removed when coverage is applied.
    assert all(str(v["path"]) != "raw/take.mp4" for v in proj["unassigned_videos"])

    # RawVideo carries declared order (not sorted).
    raw = next((r for r in proj["raw_videos"] if r["storage_path"] == "raw/take.mp4"), None)
    assert raw is not None
    assert raw["covers_stages"] == [2, 1]


def test_patch_coverage_removes_dropped_stage_and_invalidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATCH coverage=[2] after [2,1] removes stage 1's StageVideo and invalidates its trim cache."""
    from splitsmith.ui import audio as audio_helpers

    invalidated: list[tuple[int, str]] = []

    def _fake_invalidate(root: Path, stage_number: int, video: Any, *, project: Any = None) -> None:
        invalidated.append((stage_number, str(video.path)))

    monkeypatch.setattr(audio_helpers, "invalidate_video_audit_trim", _fake_invalidate)

    client, _, _ = _build_local_app(tmp_path)
    client.patch(
        "/api/shooters/me/raw-videos/coverage",
        json={"filename": "take.mp4", "covers_stages": [2, 1]},
    )
    invalidated.clear()

    resp = client.patch(
        "/api/shooters/me/raw-videos/coverage",
        json={"filename": "take.mp4", "covers_stages": [2]},
    )
    assert resp.status_code == 200, resp.text

    proj = client.get("/api/shooters/me/project").json()
    stages = {s["stage_number"]: s for s in proj["stages"]}
    assert len(stages[1]["videos"]) == 0
    assert len(stages[2]["videos"]) == 1

    # Stage 1's trim cache was invalidated.
    assert any(sn == 1 for sn, _ in invalidated)


def test_patch_coverage_422_on_unknown_stage(tmp_path: Path) -> None:
    """PATCH with a stage number not in the project returns 422."""
    client, _, _ = _build_local_app(tmp_path, stage_count=2)

    resp = client.patch(
        "/api/shooters/me/raw-videos/coverage",
        json={"filename": "take.mp4", "covers_stages": [1, 99]},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "unknown_stage_numbers"
    assert 99 in detail["stage_numbers"]


def test_patch_coverage_404_on_unknown_filename(tmp_path: Path) -> None:
    """PATCH with a filename not registered in the project returns 404."""
    client, _, _ = _build_local_app(tmp_path)

    resp = client.patch(
        "/api/shooters/me/raw-videos/coverage",
        json={"filename": "never-registered.mp4", "covers_stages": [1]},
    )
    assert resp.status_code == 404


def test_patch_coverage_enqueues_sequential_detect(tmp_path: Path) -> None:
    """PATCH coverage=[2,1] on a sequential take enqueues detect_beep for stage 2
    (first stage in declared order) only - the job chains the rest."""
    client, _, fake_jobs = _build_local_app(tmp_path)

    client.patch(
        "/api/shooters/me/raw-videos/coverage",
        json={"filename": "take.mp4", "covers_stages": [2, 1]},
    )

    detect_jobs = [j for j in fake_jobs.submitted if j["kind"] == "detect_beep"]
    assert len(detect_jobs) == 1, f"expected 1 detect_beep job in sequential mode, got {detect_jobs}"
    assert detect_jobs[0]["stage_number"] == 2


def test_patch_coverage_enqueues_one_per_stage_in_scoreboard_mode(tmp_path: Path) -> None:
    """PATCH coverage=[1,2,3] in scoreboard mode enqueues one detect_beep per covered stage."""
    client, _, fake_jobs = _build_local_app(tmp_path, stage_count=3, with_scorecard=True)

    client.patch(
        "/api/shooters/me/raw-videos/coverage",
        json={"filename": "take.mp4", "covers_stages": [1, 2, 3]},
    )

    detect_jobs = [j for j in fake_jobs.submitted if j["kind"] == "detect_beep"]
    submitted_stages = {j["stage_number"] for j in detect_jobs}
    assert submitted_stages == {1, 2, 3}, f"expected jobs for all 3 stages, got {detect_jobs}"


# ---------------------------------------------------------------------------
# Controller addendum: sequential chain advance on manual beep
# ---------------------------------------------------------------------------


def _build_sequential_take(tmp_path: Path) -> tuple[Any, _FakeJobBackend, dict[int, str]]:
    """Three-stage sequential take; returns (client, fake_jobs, video_ids_by_stage)."""
    client, app, fake_jobs = _build_local_app(tmp_path, stage_count=3)

    # Apply coverage [1, 2, 3] to register all stage videos.
    client.patch(
        "/api/shooters/me/raw-videos/coverage",
        json={"filename": "take.mp4", "covers_stages": [1, 2, 3]},
    )
    # Reset submitted list - we only care about what happens after the beep call.
    fake_jobs.submitted.clear()

    proj = client.get("/api/shooters/me/project").json()
    stages = {s["stage_number"]: s for s in proj["stages"]}
    video_ids = {sn: stages[sn]["videos"][0]["video_id"] for sn in [1, 2, 3]}

    return client, fake_jobs, video_ids


def test_manual_beep_on_sequential_take_enqueues_next_stage(tmp_path: Path) -> None:
    """Manual beep on stage 1 of a [1,2,3] sequential take submits detect_beep for stage 2."""
    client, fake_jobs, video_ids = _build_sequential_take(tmp_path)

    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{video_ids[1]}/beep",
        json={"beep_time": 5.0},
    )
    assert resp.status_code == 200, resp.text

    detect_jobs = [j for j in fake_jobs.submitted if j["kind"] == "detect_beep"]
    assert len(detect_jobs) == 1, f"expected 1 detect_beep job for next stage, got {detect_jobs}"
    assert detect_jobs[0]["stage_number"] == 2
    assert detect_jobs[0]["video_id"] == video_ids[2]


def test_manual_beep_on_non_take_video_enqueues_nothing(tmp_path: Path) -> None:
    """Manual beep on a single-stage (non-take) video submits no detect_beep jobs."""
    from tests.test_ui_server import _match_create_app, _MatchClient

    project_root = tmp_path / "match"
    app = _match_create_app(project_root=project_root, project_name="No Take")
    client = _MatchClient(app)

    # Add one stage directly - avoid state.shooter_root() which needs the ContextVar.
    from splitsmith.match_model import Match

    state = app.state.splitsmith_state
    shooter_root = Match.shooter_root(project_root, "me")
    project = MatchProject.load(shooter_root)
    project.stages.append(
        StageEntry(stage_number=1, stage_name="S1", time_seconds=10.0, scorecard_updated_at=None)
    )
    project.save(shooter_root)

    src_dir = tmp_path / "videos"
    src_dir.mkdir()
    (src_dir / "single.mp4").write_bytes(b"\x00")
    client.post(
        "/api/shooters/me/videos/scan",
        json={"source_dir": str(src_dir), "auto_assign_primary": False},
    )
    client.post(
        "/api/shooters/me/assignments/move",
        json={"video_path": "raw/single.mp4", "to_stage_number": 1, "role": "primary"},
    )

    fake_jobs = _FakeJobBackend()
    state.jobs = fake_jobs

    proj = client.get("/api/shooters/me/project").json()
    video_id = proj["stages"][0]["videos"][0]["video_id"]

    resp = client.post(
        f"/api/shooters/me/stages/1/videos/{video_id}/beep",
        json={"beep_time": 5.0},
    )
    assert resp.status_code == 200, resp.text

    detect_jobs = [j for j in fake_jobs.submitted if j["kind"] == "detect_beep"]
    assert len(detect_jobs) == 0, f"expected no detect_beep jobs for non-take video, got {detect_jobs}"
