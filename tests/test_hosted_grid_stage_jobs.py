"""A hosted grid renders as one job per stage, then a stitch (#1691).

The request queues a ``compare-grid-stage`` job per planned stage (so serve's
wake fans them out) and the grid job, claimed first by priority. The grid
job plans from storage, publishes its plan, renders any stage job still
queued itself, waits for the rest, then stitches; each stage job renders its
stage into the stored segment cache.
The renderer is faked here (its split is pinned in
``test_compare_mp4_grid_tiles``); these pin the jobs around it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from splitsmith.compare import mp4_grid as mp4_grid_mod
from splitsmith.compare import project_loader as pl_mod
from splitsmith.config import VideoMetadata
from splitsmith.stored_segment_cache import StoredSegmentCache
from splitsmith.ui import server as server_mod
from splitsmith.ui.grid_tiles import HostedGridTiles
from splitsmith.ui.jobs import JobCancelled, JobStatus
from tests.test_hosted_compare_grid import (
    TRIM_BYTES,
    _give_the_shooter_a_beeped_primary,
    _wait_for_job,
    hosted_grid,
    inline_deferrer,
)
from tests.test_hosted_raw_upload import hosted_client, hosted_client_with_match, hosted_db

__all__ = ["hosted_client", "hosted_client_with_match", "hosted_db", "hosted_grid", "inline_deferrer"]


def _probe(_target: object) -> VideoMetadata:
    return VideoMetadata(width=1920, height=1080, duration_seconds=30.0, frame_rate_num=30, frame_rate_den=1)


@pytest.fixture
def two_stages(hosted_grid, hosted_db, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """The hosted match with stages 1 and 2 beeped and exported, and a
    renderer that records each call and answers as the real one would."""
    client, storage, match_id, slug, _rendered = hosted_grid
    _give_the_shooter_a_beeped_primary(hosted_db, match_id, slug, stages=(1, 2))
    storage.write_bytes(f"matches/{match_id}/shooters/{slug}/exports/stage2_two_trimmed.mp4", TRIM_BYTES)
    calls: list[dict[str, Any]] = []
    failing: set[int] = set()
    raising: set[int] = set()
    # Inline, a stage job runs before its grid job is even queued: it
    # would wait out the plan; plan alone at once instead.
    monkeypatch.setattr(server_mod, "GRID_PLAN_WAIT_S", 0.0)

    def render(shooters: Any, *, audio_label: str, output_path: Path, **kwargs: Any) -> Any:
        calls.append({"audio_label": audio_label, **kwargs})
        numbers = sorted({n for s in shooters for n in s.stages_by_number})
        only = kwargs.get("only_stage")
        if only is not None and only in raising:
            raise RuntimeError("render blew up")
        if only is not None:
            ok = only not in failing
            outcome = mp4_grid_mod.StageOutcome(
                stage_number=only, stage_name=f"Stage {only}", ok=ok, error=None if ok else "boom"
            )
            return mp4_grid_mod.GridRenderResult(output_path=output_path, stages=(outcome,))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"grid")
        stages = tuple(
            mp4_grid_mod.StageOutcome(stage_number=n, stage_name=f"Stage {n}", ok=True) for n in numbers
        )
        return mp4_grid_mod.GridRenderResult(output_path=output_path, stages=stages)

    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", render)
    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _probe)
    return client, storage, match_id, slug, calls, failing, raising


def _grid(client, match_id: str, slug: str, stages: list[int]) -> dict:  # type: ignore[no-untyped-def]
    resp = client.post(
        f"/api/matches/{match_id}/match/compare-export", json={"stage_numbers": stages, "audio_from": slug}
    )
    assert resp.status_code == 200, resp.text
    return _wait_for_job(client, match_id, resp.json()["id"], timeout=30.0)


def _stage_jobs(client, match_id: str) -> list[dict]:  # type: ignore[no-untyped-def]
    jobs = client.get(f"/api/matches/{match_id}/me/jobs").json()
    return [j for j in jobs if j["kind"] == "compare-grid-stage"]


def test_the_parent_queues_one_stage_job_per_planned_stage(two_stages) -> None:
    client, _storage, match_id, slug, calls, _failing, _raising = two_stages
    job = _grid(client, match_id, slug, [1, 2])
    assert job["status"] == "succeeded", job
    assert [c.get("only_stage") for c in calls] == [1, 2, None]
    stage_jobs = _stage_jobs(client, match_id)
    assert sorted(j["stage_number"] for j in stage_jobs) == [1, 2]
    assert all(j["status"] == "succeeded" for j in stage_jobs)


def test_the_parent_stitches_from_the_stage_jobs_cache(two_stages) -> None:
    """Every job reads and writes the same stored cache under the match and
    keys the tiles the same way, so the stitch (and a retried grid, after a
    killed one) finds what the stage jobs stored."""
    client, _storage, match_id, slug, calls, _failing, _raising = two_stages
    assert _grid(client, match_id, slug, [1, 2])["status"] == "succeeded"
    caches = [c["segment_cache"] for c in calls]
    assert all(isinstance(c, StoredSegmentCache) for c in caches)
    assert {c.prefix for c in caches} == {f"matches/{match_id}/render-segments"}
    identities = [c["tile_identities"] for c in calls]
    assert identities[0] == identities[1] == identities[2] and identities[0]
    assert all(isinstance(c["tile_access"], HostedGridTiles) for c in calls)
    # Every job built the same render options: a stage segment is reused
    # only when the commands match.
    options = [{k: v for k, v in c.items() if k not in _PER_JOB} for c in calls]
    assert options[0] == options[1] == options[2]


_PER_JOB = {
    "segment_cache",
    "tile_access",
    "only_stage",
    "work_dir",
    "progress",
    "on_notice",
    "tile_identities",
}


def test_a_failed_stage_job_does_not_fail_the_grid(two_stages) -> None:
    """A stage the stage job could not render is left to the grid job, whose
    render encodes what is missing; the stage job is no red job in the
    drawer for a grid that succeeded."""
    client, _storage, match_id, slug, calls, failing, raising = two_stages
    failing.add(1)
    raising.add(2)
    job = _grid(client, match_id, slug, [1, 2])
    assert job["status"] == "succeeded", job
    stage_jobs = {j["stage_number"]: j for j in _stage_jobs(client, match_id)}
    assert {n: j["status"] for n, j in stage_jobs.items()} == {1: "succeeded", 2: "succeeded"}
    results = {
        n: client.get(f"/api/matches/{match_id}/me/jobs/{j['id']}").json()["result"]
        for n, j in stage_jobs.items()
    }
    assert results[1]["ok"] is False and results[1]["error"] == "boom"
    assert results[2]["ok"] is False and "render blew up" in results[2]["error"]
    assert [c.get("only_stage") for c in calls] == [1, 2, None]


def test_the_grid_job_renders_queued_stages_itself(two_stages, inline_deferrer) -> None:
    """No worker took the stage jobs (none woke, or one worker serves the
    queue): the grid job cancels each and renders it, from the back, then
    stitches. The grid finishes either way."""
    client, _storage, match_id, slug, calls, _failing, _raising = two_stages
    inline_deferrer["hold_kinds"] = {"compare-grid-stage"}
    job = _grid(client, match_id, slug, [1, 2])
    assert job["status"] == "succeeded", job
    assert [c.get("only_stage") for c in calls] == [2, 1, None]
    assert sorted(j["status"] for j in _stage_jobs(client, match_id)) == ["cancelled", "cancelled"]


def test_the_request_queues_the_stage_jobs_behind_the_grid_job(two_stages) -> None:
    """Queued by the request, the stage jobs go through serve's wake, which
    fans them out to workers; the grid job is claimed before them."""
    from splitsmith import queue as queue_mod

    client, _storage, match_id, slug, _calls, _failing, _raising = two_stages
    _grid(client, match_id, slug, [1, 2])
    jobs = client.get(f"/api/matches/{match_id}/me/jobs").json()
    grid = next(j for j in jobs if j["kind"] == "compare-grid")
    assert all(j["created_at"] <= grid["created_at"] for j in _stage_jobs(client, match_id))
    assert queue_mod.job_priority("compare-grid-stage") < queue_mod.job_priority("compare-grid")


def test_the_request_wakes_workers_once_for_the_whole_batch(two_stages, inline_deferrer) -> None:
    """Every grid job is queued with the wakes held, so serve wakes once,
    after the last, and the wake counts every stage job (run G2)."""
    client, _storage, match_id, slug, _calls, _failing, _raising = two_stages
    inline_deferrer["wakes_held"] = []
    _grid(client, match_id, slug, [1, 2])
    queued = [(kind, held) for kind, held in inline_deferrer["wakes_held"] if kind.startswith("compare-grid")]
    assert [kind for kind, _ in queued] == ["compare-grid-stage", "compare-grid-stage", "compare-grid"]
    assert all(held for _, held in queued)


def test_a_stage_job_plans_from_the_grid_jobs_published_plan(tmp_path: Path, monkeypatch) -> None:
    from splitsmith.storage import FilesystemStorage

    storage = FilesystemStorage(tmp_path / "store")
    key = "matches/m1/render-plans/run1.json"
    storage.write_bytes(key, b'{"tile_meta": {"/t.mp4": {"duration_seconds": 3.0}}}')
    monkeypatch.setattr(server_mod, "_grid_plan_key", lambda run: key)
    state = SimpleNamespace(storage=storage)
    assert server_mod._await_grid_plan(_Handle(None), state, "run1") == {"/t.mp4": {"duration_seconds": 3.0}}  # type: ignore[arg-type]


def test_a_stage_job_plans_alone_when_no_plan_comes(tmp_path: Path, monkeypatch) -> None:
    from splitsmith.storage import FilesystemStorage

    monkeypatch.setattr(server_mod, "GRID_PLAN_WAIT_S", 0.0)
    monkeypatch.setattr(server_mod, "GRID_STAGE_POLL_S", 0.0)
    monkeypatch.setattr(server_mod, "_grid_plan_key", lambda run: "matches/m1/render-plans/none.json")
    state = SimpleNamespace(storage=FilesystemStorage(tmp_path / "store"))
    assert server_mod._await_grid_plan(_Handle(None), state, "none") is None  # type: ignore[arg-type]


def test_a_one_stage_grid_queues_no_stage_jobs(two_stages) -> None:
    client, _storage, match_id, slug, calls, _failing, _raising = two_stages
    assert _grid(client, match_id, slug, [1])["status"] == "succeeded"
    assert [c.get("only_stage") for c in calls] == [None]
    assert _stage_jobs(client, match_id) == []


class _Handle:
    def __init__(self, cancel_after: int | None) -> None:
        self.polls = 0
        self.cancel_after = cancel_after

    def check_cancel(self) -> None:
        if self.cancel_after is not None and self.polls >= self.cancel_after:
            raise JobCancelled()
        self.polls += 1

    def update(self, **_kwargs: Any) -> None:
        pass


def _jobs(status: JobStatus):  # type: ignore[no-untyped-def]
    cancelled: list[str] = []

    async def get(job_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(id=job_id, status=status)

    async def cancel(job_id: str):  # type: ignore[no-untyped-def]
        cancelled.append(job_id)

    return SimpleNamespace(jobs=SimpleNamespace(get=get, cancel=cancel)), cancelled


def _work(state, handle, stage_jobs):  # type: ignore[no-untyped-def]
    server_mod._work_grid_stages(
        handle, None, None, {}, stage_jobs, state=state, scratch=Path("/nowhere")  # type: ignore[arg-type]
    )


def test_cancelling_the_parent_cancels_its_stage_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_mod, "GRID_STAGE_POLL_S", 0.0)
    state, cancelled = _jobs(JobStatus.RUNNING)
    with pytest.raises(JobCancelled):
        _work(state, _Handle(cancel_after=2), {1: "a", 2: "b"})
    assert cancelled == ["a", "b"]


def test_the_wait_ends_at_its_deadline_without_cancelling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_mod, "GRID_STAGE_POLL_S", 0.0)
    monkeypatch.setattr(server_mod, "GRID_STAGE_WAIT_S", 0.0)
    state, cancelled = _jobs(JobStatus.RUNNING)
    _work(state, _Handle(cancel_after=None), {1: "a"})
    assert cancelled == []
