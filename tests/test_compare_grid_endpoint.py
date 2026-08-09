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

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from splitsmith.compare import mp4_grid as mp4_grid_mod
from splitsmith.compare import project_loader as pl_mod
from splitsmith.export_naming import stage_file_base
from splitsmith.fcpxml_gen import VideoMetadata
from splitsmith.match_model import Match, MatchStageDefinition, Shooter
from splitsmith.match_project import MatchProject, StageEntry, StageVideo
from splitsmith.ui import server as server_mod
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
        (exports / f"{stage_file_base(number, stage.stage_name)}_trimmed.mp4").write_bytes(b"")


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


def test_rejects_an_unresolvable_camera_selector(match_client: _MatchClient) -> None:
    """A ``cameras`` override that matches no mount or role anywhere in
    the shooter's project is a typo -- it must 400 with an actionable
    message, not bubble up as an unhandled 500.
    """
    response = match_client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias", "cameras": {"mathias": "drone"}},
    )
    assert response.status_code == 400
    assert "drone" in response.json()["detail"]


def test_rejects_when_audio_source_has_no_trim_on_any_selected_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """anna has a trim on stage 1; mathias -- the chosen audio source --
    only has one on stage 2. Selecting just stage 1 must 400: rendering
    would produce a grid with anna's picture and no audio at all, a
    many-minute render away from a doomed request the SPA should have
    caught at submit time.
    """
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    match_root = _seed_match(tmp_path, shooters=["mathias", "anna"], stage_numbers=[1, 2])
    _write_trims(match_root, slug="anna", stage_numbers=[1])
    _write_trims(match_root, slug="mathias", stage_numbers=[2])
    app = _match_create_app(project_root=match_root, project_name="Compare Match")
    client = _MatchClient(app)

    response = client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "mathias" in detail.lower()
    assert "trim" in detail.lower()


def test_audio_source_missing_only_some_selected_stages_is_still_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mathias (the audio source) has a trim on stage 1 but not stage 2;
    anna has trims on both. Selecting both stages must still queue --
    stage 2 simply renders mathias as filler, which is normal. Only a
    *total* absence of trims on the selection is fatal for the audio
    source, not a partial one.
    """
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _fake_render_grid_mp4)
    match_root = _seed_match(tmp_path, shooters=["mathias", "anna"], stage_numbers=[1, 2])
    _write_trims(match_root, slug="mathias", stage_numbers=[1])
    _write_trims(match_root, slug="anna", stage_numbers=[1, 2])
    app = _match_create_app(project_root=match_root, project_name="Compare Match")
    client = _MatchClient(app)

    response = client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1, 2], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "compare-grid"
    assert body["status"] in ("pending", "running")


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


def test_a_selected_stage_nobody_can_render_is_reported_not_silently_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both shooters have trims on stages 1 and 2 but not 3, and the
    request asks for all three. ``build_stage_plans`` derives its stage
    list from the shooters' trims, so stage 3 produces no plan at all --
    and the result used to count itself against the plans, reporting
    "2 of 2" for a three-stage request. The user asked for three, got
    two, and nothing said so.
    """
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _fake_render_grid_mp4)
    match_root = _seed_match(tmp_path, shooters=["mathias", "anna"], stage_numbers=[1, 2, 3])
    for slug in ("mathias", "anna"):
        _write_trims(match_root, slug=slug, stage_numbers=[1, 2])
    app = _match_create_app(project_root=match_root, project_name="Compare Match")
    client = _MatchClient(app)

    response = client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1, 2, 3], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    job = _wait_for_job(client, response.json()["id"])
    assert job["status"] == "succeeded", job
    result = job["result"]

    # Counted against what was asked for, not against what got planned.
    assert result["stages_total"] == 3
    assert result["stages_rendered"] == 2
    assert result["skipped_stages"] == [3]
    # And the shooter/stage pairs that caused it are named, with the path
    # the loader looked for -- the same data ``compare export`` prints.
    missing = result["missing_trims"]
    assert {(m["shooter"], m["stage_number"]) for m in missing} == {("Mathias", 3), ("Anna", 3)}
    assert all(m["expected_path"].endswith(".mp4") for m in missing)


def test_missing_trims_are_reported_for_stages_that_still_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """anna has no trim on stage 2, so stage 2 renders with her cell
    black. That is legitimate output, but the user has to be told which
    cell went black and why -- otherwise a mis-selected camera looks
    exactly like a shooter who skipped the stage (#618).
    """
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _fake_render_grid_mp4)
    match_root = _seed_match(tmp_path, shooters=["mathias", "anna"], stage_numbers=[1, 2])
    _write_trims(match_root, slug="mathias", stage_numbers=[1, 2])
    _write_trims(match_root, slug="anna", stage_numbers=[1])
    app = _match_create_app(project_root=match_root, project_name="Compare Match")
    client = _MatchClient(app)

    response = client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1, 2], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    job = _wait_for_job(client, response.json()["id"])
    assert job["status"] == "succeeded", job
    result = job["result"]

    assert result["stages_total"] == 2
    assert result["stages_rendered"] == 2
    assert result["skipped_stages"] == []
    assert [(m["shooter"], m["stage_number"]) for m in result["missing_trims"]] == [("Anna", 2)]


def test_missing_trims_outside_the_selection_are_not_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``missing_trims`` on the bundle covers the whole project, not the
    selection. Reporting stage 2's gap when the user only asked for
    stage 1 is noise about footage this render never touched.
    """
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _fake_render_grid_mp4)
    match_root = _seed_match(tmp_path, shooters=["mathias", "anna"], stage_numbers=[1, 2])
    _write_trims(match_root, slug="mathias", stage_numbers=[1])
    _write_trims(match_root, slug="anna", stage_numbers=[1])
    app = _match_create_app(project_root=match_root, project_name="Compare Match")
    client = _MatchClient(app)

    response = client.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    job = _wait_for_job(client, response.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["result"]["missing_trims"] == []
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


# --- what actually reaches the renderer ---------------------------------------
#
# ``render_grid_mp4`` is stubbed in every test above, so nothing here saw
# its keyword arguments. Both of these mutations survived the whole file
# green: dropping ``canvas=`` (the page's "1080p -- faster" option then
# silently renders 4K), and passing the slug instead of the display label
# (which ``build_stage_plans`` would reject at render time, minutes in).


def _capturing_render(captured: dict[str, Any]):
    def _render(shooters: Any, **kwargs: Any) -> Any:
        captured["shooters"] = shooters
        captured.update(kwargs)
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"stub")
        stage_numbers = sorted({n for s in shooters for n in s.stages_by_number})
        return mp4_grid_mod.GridRenderResult(
            output_path=output_path,
            stages=tuple(
                mp4_grid_mod.StageOutcome(stage_number=n, stage_name=f"Stage {n}", ok=True)
                for n in stage_numbers
            ),
        )

    return _render


def test_the_requested_canvas_reaches_the_renderer(
    match_client_with_trims: _MatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _capturing_render(captured))

    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={
            "stage_numbers": [1],
            "audio_from": "mathias",
            "canvas_width": 1920,
            "canvas_height": 1080,
        },
    )
    assert response.status_code == 200
    job = _wait_for_job(match_client_with_trims, response.json()["id"])
    assert job["status"] == "succeeded", job

    assert captured["canvas"] == mp4_grid_mod.GridCanvas(width=1920, height=1080)
    # The rate is never taken from the request -- it derives from the
    # footage inside the engine, so the canvas must arrive unpinned.
    assert not captured["canvas"].is_frame_rate_pinned


def test_the_default_canvas_is_4k(
    match_client_with_trims: _MatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _capturing_render(captured))

    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    _wait_for_job(match_client_with_trims, response.json()["id"])

    assert captured["canvas"].width == mp4_grid_mod.DEFAULT_CANVAS_WIDTH
    assert captured["canvas"].height == mp4_grid_mod.DEFAULT_CANVAS_HEIGHT


def test_the_renderer_gets_the_display_label_not_the_slug(
    match_client_with_trims: _MatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The request names a shooter by slug; the bundles are labelled by
    # display name. Handing the slug straight through means no tile
    # matches the audio label and the render dies -- but only after the
    # request has been accepted and the job has started.
    captured: dict[str, Any] = {}
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _capturing_render(captured))

    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    _wait_for_job(match_client_with_trims, response.json()["id"])

    assert captured["audio_label"] == "Mathias"
    assert {s.label for s in captured["shooters"]} == {"Mathias", "Anna"}


def test_the_renderer_gets_a_scratch_dir_the_worker_owns(
    match_client_with_trims: _MatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without an owned work_dir the engine keeps its per-stage segments
    # forever, by design -- gigabytes of 4K temps per match.
    captured: dict[str, Any] = {}
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _capturing_render(captured))

    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    _wait_for_job(match_client_with_trims, response.json()["id"])

    work_dir = captured["work_dir"]
    assert work_dir.name.startswith(".compare-grid-work-")
    assert not work_dir.exists()  # removed once the render returned
    assert callable(captured["runner"])


# --- progress -----------------------------------------------------------------


class _RecordingHandle:
    """Just enough :class:`JobHandle` for the progress runner."""

    def __init__(self) -> None:
        self.updates: list[tuple[float | None, str | None]] = []

    def update(self, *, progress: float | None = None, message: str | None = None) -> None:
        self.updates.append((progress, message))


def _plan(number: int) -> mp4_grid_mod.GridStagePlan:
    return mp4_grid_mod.GridStagePlan(
        stage_number=number,
        stage_name=f"Stage {number}",
        tiles=(),
        duration_seconds=10.0,
        audio_label="Mathias",
        rows=1,
        cols=1,
    )


def test_the_progress_runner_reports_each_stage_then_the_stitch() -> None:
    handle = _RecordingHandle()
    plans = tuple(_plan(n) for n in (1, 2, 3, 4))
    calls: list[list[str]] = []

    def _base(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    runner = server_mod._compare_grid_progress_runner(handle, plans, base_runner=_base)
    for _ in range(len(plans) + 1):  # one per stage, then the stitch
        assert runner(["ffmpeg"], capture_output=True).returncode == 0

    assert [round(p or 0, 3) for p, _ in handle.updates] == [0.05, 0.275, 0.5, 0.725, 0.95]
    assert handle.updates[2][1] == "Rendering stage 3 (Stage 3) -- 3 of 4..."
    assert handle.updates[-1][1] == "Stitching 4 stage(s)..."
    assert len(calls) == 5


def test_the_job_snapshot_advances_while_the_render_runs(
    match_client_with_trims: _MatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect this exists for: the worker used to call ``update``
    at 0.02, 0.05 and then 1.0, so a multi-minute encode showed 5% from
    submit to finish and a working render was indistinguishable from a
    hung one.
    """
    reached = threading.Event()
    release = threading.Event()

    def _slow_render(shooters: Any, **kwargs: Any) -> Any:
        runner = kwargs["runner"]
        for _ in range(2):  # two stages' worth of ffmpeg calls
            runner([sys.executable, "-c", ""], capture_output=True)
        reached.set()
        assert release.wait(10)
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"stub")
        return mp4_grid_mod.GridRenderResult(
            output_path=output_path,
            stages=(
                mp4_grid_mod.StageOutcome(stage_number=1, stage_name="Stage 1", ok=True),
                mp4_grid_mod.StageOutcome(stage_number=2, stage_name="Stage 2", ok=True),
            ),
        )

    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _slow_render)

    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1, 2], "audio_from": "mathias"},
    )
    assert response.status_code == 200
    job_id = response.json()["id"]
    assert reached.wait(10), "the render never invoked the injected runner"
    snapshot = match_client_with_trims.get(f"/api/me/jobs/{job_id}").json()
    release.set()

    assert snapshot["status"] == "running"
    assert snapshot["progress"] == pytest.approx(0.5)
    assert "2 of 2" in snapshot["message"]
    assert _wait_for_job(match_client_with_trims, job_id)["status"] == "succeeded"


# --- output name --------------------------------------------------------------


@pytest.mark.parametrize("name", ["../../escape", "sub/dir", "..", ".hidden", ""])
def test_an_output_name_that_is_not_a_plain_stem_is_rejected(
    match_client_with_trims: _MatchClient, name: str
) -> None:
    # exports / f"{name}.mp4" with "../../x" writes outside the match.
    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias", "output_name": name},
    )
    assert response.status_code == 400
    assert "output_name" in response.json()["detail"]


def test_an_ordinary_output_name_still_lands_in_the_match_exports_dir(
    match_client_with_trims: _MatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", _capturing_render(captured))

    response = match_client_with_trims.post(
        "/api/match/compare-export",
        json={"stage_numbers": [1], "audio_from": "mathias", "output_name": "bromma_grid-2026.v2"},
    )
    assert response.status_code == 200
    job = _wait_for_job(match_client_with_trims, response.json()["id"])
    assert job["status"] == "succeeded", job
    assert captured["output_path"].name == "bromma_grid-2026.v2.mp4"
    assert captured["output_path"].parent.name == "exports"
