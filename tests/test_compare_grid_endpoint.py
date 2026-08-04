"""``POST /api/match/compare-export`` -- match-scoped compare-grid MP4 export.

Local-mode only (Task 5, phase 0). The endpoint validates up front (empty
selection, unknown audio shooter, no trims anywhere) so the SPA never
queues a render that would just produce a grid of black tiles, then
queues through the existing job registry -- a full-match 4K re-encode
runs for minutes, so the response is a ``Job`` snapshot the SPA polls.

No real ffmpeg here: ``project_loader.load_shooter_from_match`` is
exercised for real against seeded projects on disk (that's the part
worth testing without a stub), but ``fcpxml_gen.probe_video`` is
stubbed (no real trim files exist) and ``mp4_grid.render_grid_mp4`` is
stubbed for the "trims exist" fixtures so the worker thread never
shells out to ffmpeg.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from splitsmith.compare import mp4_grid as mp4_grid_mod
from splitsmith.compare import project_loader as pl_mod
from splitsmith.fcpxml_gen import VideoMetadata
from splitsmith.match_model import Match, MatchStageDefinition, Shooter
from splitsmith.ui.match_exports import _slugify
from splitsmith.ui.project import MatchProject, StageEntry, StageVideo
from tests.test_ui_server import _match_create_app, _MatchClient


def _fake_probe(_path: Path) -> VideoMetadata:
    return VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=30.0,
        frame_rate_num=30,
        frame_rate_den=1,
    )


def _seed_shooter(match_root: Path, *, slug: str, name: str, stage_numbers: list[int]) -> None:
    """Register a shooter with a beeped primary on every stage in
    ``stage_numbers``. Mirrors ``tests/test_trims_to_compare_e2e.py``'s
    ``_seed_shooter`` -- no trim files are written here; callers that
    want them on disk call ``_write_trims`` separately.
    """
    match = Match.load(match_root)
    match.add_shooter(match_root, Shooter(slug=slug, name=name))
    shooter_root = Match.shooter_root(match_root, slug)
    project = MatchProject.init(shooter_root, name=match.name)
    for number in stage_numbers:
        stage_name = next(s.stage_name for s in match.stages if s.stage_number == number)
        project.stages.append(
            StageEntry(
                stage_number=number,
                stage_name=stage_name,
                time_seconds=10.0 + number,
                videos=[StageVideo(path=Path(f"raw/{slug}{number}.mov"), role="primary", beep_time=5.0)],
            )
        )
    project.save(shooter_root)


def _write_trims(match_root: Path, *, slug: str, stage_numbers: list[int]) -> None:
    """Drop a stub lossless trim where the loader expects one, for every
    stage in ``stage_numbers``. Mirrors ``test_compare_merged_match.py``'s
    ``_seed_legacy_project`` trim-stubbing.
    """
    shooter_root = Match.shooter_root(match_root, slug)
    project = MatchProject.load(shooter_root)
    exports = project.exports_path(shooter_root)
    exports.mkdir(parents=True, exist_ok=True)
    for number in stage_numbers:
        stage = next(s for s in project.stages if s.stage_number == number)
        (exports / f"stage{number}_{_slugify(stage.stage_name)}_trimmed.mp4").write_bytes(b"")


def _seed_match(tmp_path: Path, *, shooters: list[str], stage_numbers: list[int]) -> Path:
    match_root = tmp_path / "match"
    match = Match.init(match_root, name="Compare Match")
    match.stages = [MatchStageDefinition(stage_number=n, stage_name=f"Stage {n}") for n in stage_numbers]
    match.save(match_root)
    for slug in shooters:
        _seed_shooter(match_root, slug=slug, name=slug.capitalize(), stage_numbers=stage_numbers)
    return match_root


def _fake_render_grid_mp4(shooters: Any, *, audio_label: str, output_path: Path, **kwargs: Any) -> Any:
    """Stand-in for ``mp4_grid.render_grid_mp4``: writes a stub file and
    reports every stage that made it into ``shooters`` as succeeded, so
    the worker's result payload can be asserted without real ffmpeg.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"stub")
    stage_numbers = sorted({n for s in shooters for n in s.stages_by_number})
    stages = tuple(
        mp4_grid_mod.StageOutcome(stage_number=n, stage_name=f"Stage {n}", ok=True) for n in stage_numbers
    )
    return mp4_grid_mod.GridRenderResult(output_path=output_path, stages=stages)


@pytest.fixture
def match_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _MatchClient:
    """A match with one shooter ('mathias') who has a beep on stage 1
    but no exported trim -- the "nothing to render" state.
    """
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    match_root = _seed_match(tmp_path, shooters=["mathias"], stage_numbers=[1])
    app = _match_create_app(project_root=match_root, project_name="Compare Match")
    return _MatchClient(app)


@pytest.fixture
def match_client_with_trims(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _MatchClient:
    """Two shooters, two stages, both trimmed on every stage. ffmpeg is
    stubbed via ``mp4_grid.render_grid_mp4`` so the queued job finishes
    fast and deterministically without shelling out.
    """
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _fake_render_grid_mp4)
    match_root = _seed_match(tmp_path, shooters=["mathias", "anna"], stage_numbers=[1, 2])
    for slug in ("mathias", "anna"):
        _write_trims(match_root, slug=slug, stage_numbers=[1, 2])
    app = _match_create_app(project_root=match_root, project_name="Compare Match")
    return _MatchClient(app)


def _wait_for_job(client: _MatchClient, job_id: str, *, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/me/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


# --- validation --------------------------------------------------------------


def test_rejects_empty_stage_selection(match_client: _MatchClient) -> None:
    response = match_client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [], "audio_from": "mathias"},
    )
    assert response.status_code == 400
    assert "stage_numbers" in response.json()["detail"]


def test_rejects_unknown_audio_shooter(match_client: _MatchClient) -> None:
    response = match_client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "nobody"},
    )
    assert response.status_code == 400
    assert "nobody" in response.json()["detail"]


def test_reports_missing_trims_rather_than_rendering_filler_for_everyone(
    match_client: _MatchClient,
) -> None:
    # No shooter has an exported trim in this fixture match.
    response = match_client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "trim" in detail.lower()


# --- queueing -----------------------------------------------------------------


def test_queues_a_job_when_trims_exist(match_client_with_trims: _MatchClient) -> None:
    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "compare-grid"
    assert body["status"] in ("pending", "running")


def test_job_result_reports_output_path_and_stage_counts_on_success(
    match_client_with_trims: _MatchClient,
) -> None:
    """The result payload names the output file and the per-stage counts
    the SPA needs to render a "N of M stages" summary -- not just a bare
    success flag.
    """
    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1, 2], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    job = _wait_for_job(match_client_with_trims, response.json()["id"])
    assert job["status"] == "succeeded", job
    result = job["result"]
    assert Path(result["output_path"]).exists()
    assert result["stages_total"] == 2
    assert result["stages_rendered"] == 2
    assert result["failed"] == []


def test_only_selected_stages_are_rendered(match_client_with_trims: _MatchClient) -> None:
    """Both fixture stages are trimmed, but the request selects only
    stage 1 -- the render must not pick up stage 2 just because a trim
    exists for it on disk.
    """
    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    job = _wait_for_job(match_client_with_trims, response.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["stages_total"] == 1


def test_partial_stage_failure_is_a_reported_success_not_a_job_failure(
    match_client_with_trims: _MatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``render_grid_mp4`` isolates per-stage ffmpeg failures rather than
    failing the whole run; the job must carry that partial result
    through rather than collapsing it into a bare job failure.
    """

    def _one_stage_fails(shooters: Any, *, audio_label: str, output_path: Path, **kwargs: Any) -> Any:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"stub")
        return mp4_grid_mod.GridRenderResult(
            output_path=output_path,
            stages=(
                mp4_grid_mod.StageOutcome(stage_number=1, stage_name="Stage 1", ok=True),
                mp4_grid_mod.StageOutcome(
                    stage_number=2, stage_name="Stage 2", ok=False, error="ffmpeg exit 1"
                ),
            ),
        )

    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _one_stage_fails)

    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1, 2], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    job = _wait_for_job(match_client_with_trims, response.json()["id"])
    assert job["status"] == "succeeded", job
    result = job["result"]
    assert result["stages_total"] == 2
    assert result["stages_rendered"] == 1
    assert result["failed"] == [{"stage_number": 2, "stage_name": "Stage 2", "error": "ffmpeg exit 1"}]
