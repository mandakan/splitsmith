"""How long an export has left (spec 2026-10-25, plan Task 5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from splitsmith import export_estimates as ee
from splitsmith.ui.jobs import Job, JobPart, JobStatus

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _job(
    job_id: str,
    kind: str,
    parts: list[JobPart] | None,
    *,
    status: JobStatus = JobStatus.RUNNING,
    created: int = 0,
    started: int | None = None,
) -> Job:
    at = NOW - timedelta(seconds=600) + timedelta(seconds=created)
    return Job(
        id=job_id,
        kind=kind,
        status=status,
        export_id="e1",
        parts=parts,
        created_at=at,
        updated_at=at,
        started_at=NOW - timedelta(seconds=started) if started is not None else None,
    )


def _part(
    key: str, kind: str, status: str = "waiting", *, profile: str | None = None, **kw: object
) -> JobPart:
    return JobPart(key=key, label=key, kind=kind, profile=profile, status=status, **kw)  # type: ignore[arg-type]


def _running(key: str, kind: str, elapsed: float, progress: float | None = None, **kw: object) -> JobPart:
    return _part(key, kind, "running", started_at=NOW - timedelta(seconds=elapsed), progress=progress, **kw)


def _grid(stage_parts: list[JobPart], *, stitch: str = "waiting") -> list[Job]:
    stages = [_job(f"s{i}", "compare-grid-stage", [part], created=i) for i, part in enumerate(stage_parts)]
    parent = _job(
        "g",
        "compare-grid",
        [
            _part("plan", "load", "done"),
            _part("stages", "wait", "running"),
            _part("stitch", "stitch", stitch, profile="1080-basic"),
        ],
        created=100,
    )
    return [*stages, parent]


def test_grid_time_left_is_the_longest_stage_plus_the_stitch() -> None:
    jobs = _grid(
        [
            _part("render", "grid_stage", "done", profile="1080-basic"),
            _running("render", "grid_stage", 100.0, profile="1080-basic"),
            _part("render", "grid_stage", profile="1080-basic"),
        ]
    )
    estimate = ee.estimate_export(jobs, {}, now=NOW, hosted=False)
    builtin_stage = ee.BUILTIN_PART_SECONDS[("grid_stage", "1080-basic")]
    builtin_stitch = ee.BUILTIN_PART_SECONDS[("stitch", "1080-basic")]
    # The waiting stage (a whole stage) outlasts the running one (200 s left).
    assert estimate.time_left_s == pytest.approx(builtin_stage + builtin_stitch)
    assert estimate.finish_at == NOW + timedelta(seconds=estimate.time_left_s)


def test_no_history_is_approximate_and_uses_builtins() -> None:
    jobs = [_job("m", "match_export", [_part("load", "load"), _part("compose", "compose")])]
    estimate = ee.estimate_export(jobs, {}, now=NOW, hosted=False)
    assert estimate.approximate is True
    assert estimate.time_left_s == pytest.approx(
        ee.BUILTIN_PART_SECONDS[("load", None)] + ee.BUILTIN_PART_SECONDS[("compose", None)]
    )


def test_history_scales_by_footage() -> None:
    done = _part(
        "render",
        "grid_stage",
        "done",
        profile="1080-basic",
        footage_s=100.0,
        started_at=NOW - timedelta(seconds=400),
        finished_at=NOW - timedelta(seconds=200),
    )
    history = ee.history_rates([_job("old", "compare-grid-stage", [done], status=JobStatus.SUCCEEDED)])
    assert history[("grid_stage", "1080-basic")] == pytest.approx(2.0)
    waiting = _part("render", "grid_stage", profile="1080-basic", footage_s=150.0)
    estimate = ee.estimate_export(
        [_job("s", "compare-grid-stage", [waiting])], history, now=NOW, hosted=False
    )
    assert estimate.time_left_s == pytest.approx(300.0)
    assert estimate.approximate is False


def test_running_part_uses_its_rate() -> None:
    part = _running("compose", "compose", 60.0, progress=0.25)
    estimate = ee.estimate_export([_job("m", "match_export", [part])], {}, now=NOW, hosted=False)
    assert estimate.time_left_s == pytest.approx(180.0)


def test_a_part_past_its_estimate_is_overrun_not_negative() -> None:
    part = _running("compose", "compose", 10_000.0)
    estimate = ee.estimate_export([_job("m", "match_export", [part])], {}, now=NOW, hosted=False)
    assert estimate.time_left_s == 0.0
    assert [p.overrun for p in estimate.parts] == [True]


def test_pending_hosted_job_adds_worker_boot() -> None:
    job = _job("x", "export", [_part("export", "stage_export")], status=JobStatus.PENDING)
    local = ee.estimate_export([job], {}, now=NOW, hosted=False)
    hosted = ee.estimate_export([job], {}, now=NOW, hosted=True)
    assert hosted.time_left_s == pytest.approx(local.time_left_s + ee.WORKER_BOOT_S)


def test_a_job_without_parts_counts_as_one() -> None:
    job = _job("x", "export", None, status=JobStatus.RUNNING, started=30)
    estimate = ee.estimate_export([job], {}, now=NOW, hosted=False)
    assert [(p.job_id, p.key) for p in estimate.parts] == [("x", "job")]
    assert estimate.time_left_s == pytest.approx(ee.DEFAULT_PART_SECONDS - 30)


def test_an_upload_after_the_lead_adds_its_time() -> None:
    lead = _job("m", "match_export", [_part("compose", "compose", "done")], status=JobStatus.SUCCEEDED)
    upload = _job("u", "youtube_upload", [_part("upload", "youtube")], status=JobStatus.PENDING, created=50)
    estimate = ee.estimate_export([lead, upload], {}, now=NOW, hosted=False)
    assert estimate.time_left_s == pytest.approx(ee.BUILTIN_PART_SECONDS[("youtube", None)])
