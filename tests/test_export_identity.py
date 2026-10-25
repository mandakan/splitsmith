"""Every job an export queues carries the export's id (spec 2026-10-25).

An export is a query over jobs, not a table: a ContextVar set by the
export route is stamped on every job submitted inside it, and a job
submitted from inside a running export (a chained YouTube upload, a grid
stage the parent re-queues) inherits it, in both backends.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import pytest

from splitsmith.async_bridge import run_sync
from splitsmith.db import ComputeJobRow
from splitsmith.ui.context import export_scope
from splitsmith.ui.jobs import Job, JobRegistry, JobStatus
from tests.test_postgres_job_backend import _build_backend_for_new_user


def _wait(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def _local() -> JobRegistry:
    return JobRegistry(max_concurrent=2)


def _settled(backend: Any, job_id: str) -> Job:
    job: Job | None = None

    def done() -> bool:
        nonlocal job
        job = asyncio.run(backend.get(job_id))
        return job is not None and job.status not in (JobStatus.PENDING, JobStatus.RUNNING)

    _wait(done)
    assert job is not None
    return job


@pytest.fixture(params=["local", "hosted"])
def backend(request: pytest.FixtureRequest, tmp_path: Any) -> Any:
    if request.param == "local":
        registry = _local()
        yield registry
        registry.begin_shutdown()
        registry.wait_for_drain(5)
    else:
        hosted, _factory, _user = _build_backend_for_new_user(tmp_path, inline=False)
        yield hosted


def test_jobs_submitted_in_a_scope_carry_its_id(backend: Any) -> None:
    backend.bodies.register("x", lambda handle, **_: None)

    async def scenario() -> tuple[list[Job], Job]:
        with export_scope("e1"):
            a = await backend.submit(kind="x")
            b = await backend.submit(kind="x")
        outside = await backend.submit(kind="x")
        members = await backend.list_export("e1")
        assert {a.export_id, b.export_id} == {"e1"}
        return members, outside

    members, outside = asyncio.run(scenario())
    assert len(members) == 2
    assert outside.export_id is None


def test_a_chained_submit_inherits_the_export_locally() -> None:
    registry = _local()
    chained: list[Job] = []
    registry.bodies.register("y", lambda handle, **_: None)
    registry.bodies.register("x", lambda handle, **_: chained.append(run_sync(registry.submit(kind="y"))))

    async def scenario() -> Job:
        with export_scope("e1"):
            return await registry.submit(kind="x")

    first = asyncio.run(scenario())
    _settled(registry, first.id)
    assert [job.export_id for job in chained] == ["e1"]


def test_a_chained_submit_inherits_the_export_on_a_worker(tmp_path: Any) -> None:
    """The worker path: the body runs through ``run_job`` with no request
    context, so the export comes from the job's own row."""
    hosted, _factory, _user = _build_backend_for_new_user(tmp_path, inline=False)
    chained: list[Job] = []
    hosted.bodies.register("y", lambda handle, **_: None)
    hosted.bodies.register("x", lambda handle, **_: chained.append(run_sync(hosted.submit(kind="y"))))

    async def queue() -> Job:
        with export_scope("e1"):
            return await hosted.submit(kind="x")

    queued = asyncio.run(queue())
    asyncio.run(hosted.run_job(job_id=queued.id, kind="x"))  # outside the scope, like a worker
    assert [job.export_id for job in chained] == ["e1"]


def test_retry_keeps_the_export(backend: Any) -> None:
    def boom(handle: Any, **_: Any) -> None:
        raise RuntimeError("boom")

    backend.bodies.register("x", boom)

    async def submit() -> Job:
        with export_scope("e1"):
            return await backend.submit(kind="x")

    first = asyncio.run(submit())
    if isinstance(backend, JobRegistry):
        _settled(backend, first.id)
    else:
        asyncio.run(backend.run_job(job_id=first.id, kind="x"))
    retried = asyncio.run(backend.retry(first.id))
    assert retried is not None and retried.export_id == "e1"


def test_active_export_ids_follow_the_parts(tmp_path: Any) -> None:
    hosted, factory, user_id = _build_backend_for_new_user(tmp_path, inline=False)
    from datetime import UTC, datetime

    def row(job_id: str, kind: str, status: str, export_id: str) -> ComputeJobRow:
        now = datetime.now(UTC)
        return ComputeJobRow(
            id=job_id,
            user_id=user_id,
            kind=kind,
            status=status,
            export_id=export_id,
            cancel_requested=False,
            acknowledged=False,
            created_at=now,
            updated_at=now,
        )

    async def seed() -> list[str]:
        async with factory() as session:
            session.add_all(
                [
                    row("a1", "match_export", "running", "e1"),
                    row("b1", "match_export", "succeeded", "e2"),
                    row("c1", "compare-grid", "failed", "e3"),
                    row("c2", "compare-grid-stage", "pending", "e3"),
                    row("d1", "export", "pending", "e4"),
                    row("d2", "export", "succeeded", "e4"),
                ]
            )
            await session.commit()
        return sorted(await hosted.active_export_ids())

    assert asyncio.run(seed()) == ["e1", "e4"]
