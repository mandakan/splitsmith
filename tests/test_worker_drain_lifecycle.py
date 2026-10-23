"""The hosted worker's drain lifecycle on Cloud Run (#1684, #1677, #1671).

- A one-shot drain stops claiming jobs before the task timeout kills it.
- A job whose worker died leaves ``running`` (failed, or cancelled when a
  cancel was requested) the next time any worker drains.
- A drain that ends with work left gets a new wake from serve.
"""

from __future__ import annotations

import asyncio
import re
import signal
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from splitsmith import queue as queue_mod

_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# #1684: the drain's deadline
# ---------------------------------------------------------------------------


def test_one_shot_drain_stops_claiming_at_its_deadline() -> None:
    """A drain past its deadline finishes the job in flight, claims no
    other and returns; Procrastinate's own SIGTERM handler stays installed
    (Cloud Run stops a task with SIGTERM)."""
    from procrastinate import App, testing

    app = App(connector=testing.InMemoryConnector())
    ran: list[int] = []
    handlers: list[object] = []

    @app.task(name="slow")
    async def slow(n: int) -> None:
        handlers.append(signal.getsignal(signal.SIGTERM))
        ran.append(n)
        await asyncio.sleep(0.3)

    async def scenario() -> None:
        async with app.open_async():
            for n in (1, 2, 3):
                await slow.defer_async(n=n)
            before = signal.getsignal(signal.SIGTERM)
            await asyncio.wait_for(
                queue_mod._run_worker_until(
                    app,
                    None,
                    {"wait": False, "concurrency": 1, "listen_notify": False},
                    deadline=time.monotonic() + 0.1,
                ),
                5,
            )
            assert handlers and handlers[0] is not before
            statuses = sorted(job["status"] for job in app.connector.jobs.values())  # type: ignore[attr-defined]
            assert statuses == ["succeeded", "todo", "todo"]

    asyncio.run(scenario())
    assert ran == [1]


def test_drain_budget_is_the_task_timeout_less_the_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(queue_mod.WORKER_TASK_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(queue_mod.WORKER_DRAIN_MARGIN_ENV, raising=False)
    assert queue_mod.drain_budget_seconds() is None
    monkeypatch.setenv(queue_mod.WORKER_TASK_TIMEOUT_ENV, "3600")
    assert queue_mod.drain_budget_seconds() == 3600 - queue_mod.DEFAULT_DRAIN_MARGIN_SECONDS
    monkeypatch.setenv(queue_mod.WORKER_DRAIN_MARGIN_ENV, "600")
    assert queue_mod.drain_budget_seconds() == 3000
    monkeypatch.setenv(queue_mod.WORKER_DRAIN_MARGIN_ENV, "3600")
    with pytest.raises(ValueError):
        queue_mod.drain_budget_seconds()


def test_worker_command_one_shot_passes_the_task_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """``worker --one-shot`` with the deployed task timeout drains until the
    timeout less the margin, counted from the command's start."""
    from splitsmith.cli import app

    captured: dict[str, object] = {}

    async def _fake_run_worker(_db_url: str, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(queue_mod, "run_worker", _fake_run_worker)
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.setenv("SPLITSMITH_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv(queue_mod.WORKER_TASK_TIMEOUT_ENV, "3600")
    monkeypatch.setenv(queue_mod.WORKER_DRAIN_MARGIN_ENV, "900")

    before = time.monotonic()
    result = CliRunner().invoke(app, ["worker", "--one-shot"])
    after = time.monotonic()

    assert result.exit_code == 0, result.stdout
    deadline = captured["deadline"]
    assert isinstance(deadline, float)
    assert before + 2700 <= deadline <= after + 2700


def test_worker_env_states_the_task_timeout_deploy_app_sets() -> None:
    """The worker env files repeat deploy-app.yml's --task-timeout; a change
    to one and not the other lets the drain overrun into a kill."""
    import yaml

    workflow = (_ROOT / ".github" / "workflows" / "deploy-app.yml").read_text()
    match = re.search(r"--args=worker,--one-shot.*?--task-timeout (\d+)", workflow, re.S)
    assert match, "the worker job's --task-timeout flag moved"
    for env in ("staging", "production"):
        values = yaml.safe_load((_ROOT / "deploy" / "cloudrun" / f"{env}-worker.env.yaml").read_text())
        assert values[queue_mod.WORKER_TASK_TIMEOUT_ENV] == match.group(1)


# ---------------------------------------------------------------------------
# #1677: a killed worker's job leaves ``running``
# ---------------------------------------------------------------------------


def _backend(tmp_path: Path, rows: list[tuple[str, str, bool]]) -> tuple[object, str]:
    """A SQLite-backed job backend whose user owns ``rows`` of (id, status, cancel_requested)."""
    from splitsmith.db import Base, ComputeJobRow, PostgresJobBackend, User, create_engine, sessionmaker

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.sqlite'}", pool_disabled=True)
    factory = sessionmaker(engine)

    async def setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            user = User(email="orphan@thias.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            for job_id, status, cancel in rows:
                s.add(
                    ComputeJobRow(
                        id=job_id,
                        user_id=user.id,
                        kind="shot_detect",
                        status=status,
                        cancel_requested=cancel,
                        acknowledged=False,
                    )
                )
            await s.commit()
            return user.id

    user_id = asyncio.run(setup())
    return PostgresJobBackend(factory, user_id=user_id, sweep_on_boot=False), user_id


def _status(backend: object, job_id: str) -> tuple[str, str | None]:
    job = asyncio.run(backend.get(job_id))  # type: ignore[attr-defined]
    assert job is not None
    return job.status.value, job.error


def test_end_orphaned_fails_a_running_row_and_cancels_a_cancel_requested_one(tmp_path: Path) -> None:
    from splitsmith.ui.jobs import JobStatus

    backend, _ = _backend(
        tmp_path,
        [
            ("killed", JobStatus.RUNNING.value, False),
            ("killed-cancelled", JobStatus.RUNNING.value, True),
            ("done", JobStatus.SUCCEEDED.value, False),
        ],
    )
    for job_id in ("killed", "killed-cancelled", "done"):
        asyncio.run(backend.end_orphaned(job_id, error="gone"))  # type: ignore[attr-defined]
    assert _status(backend, "killed") == ("failed", "gone")
    assert _status(backend, "killed-cancelled") == ("cancelled", None)
    assert _status(backend, "done") == ("succeeded", None)


async def _orphan(app: object, *, job_id: str, user_id: str, heartbeat_age_s: float) -> int:
    """Defer a compute job and claim it for a worker whose last heartbeat is
    ``heartbeat_age_s`` old (a worker Cloud Run killed mid-job); returns the
    Procrastinate job id."""
    import datetime

    manager = app.job_manager  # type: ignore[attr-defined]
    worker_id = await manager.register_worker()
    connector = app.connector  # type: ignore[attr-defined]
    await app.configure_task(name=queue_mod.RUN_COMPUTE_JOB_TASK, queue="user-x").defer_async(  # type: ignore[attr-defined]
        job_id=job_id, user_id=user_id, kind="shot_detect", args={}, match_id=None
    )
    job = await manager.fetch_job(queues=None, worker_id=worker_id)
    assert job is not None
    connector.workers[worker_id] = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        seconds=heartbeat_age_s
    )
    return job.id


def test_stalled_sweep_ends_only_jobs_whose_worker_is_dead(tmp_path: Path) -> None:
    from procrastinate import App, testing

    from splitsmith.ui.jobs import JobStatus

    backend, user_id = _backend(
        tmp_path, [("killed", JobStatus.RUNNING.value, False), ("live", JobStatus.RUNNING.value, False)]
    )
    app = App(connector=testing.InMemoryConnector())

    async def end(uid: str, job_id: str) -> None:
        assert uid == user_id
        await backend.end_orphaned(job_id, error=queue_mod.ORPHANED_JOB_ERROR)  # type: ignore[attr-defined]

    async def scenario() -> tuple[int, int, int]:
        async with app.open_async():
            dead = await _orphan(app, job_id="killed", user_id=user_id, heartbeat_age_s=300)
            live = await _orphan(app, job_id="live", user_id=user_id, heartbeat_age_s=1)
            ended = await queue_mod.recover_stalled_jobs(app, end)
            return ended, dead, live

    ended, dead, live = asyncio.run(scenario())
    assert ended == 1
    jobs = app.connector.jobs  # type: ignore[attr-defined]
    assert jobs[dead]["status"] == "failed"
    assert jobs[live]["status"] == "doing"
    assert _status(backend, "killed") == ("failed", queue_mod.ORPHANED_JOB_ERROR)
    assert _status(backend, "live") == ("running", None)


def test_a_drain_ends_a_killed_workers_job_then_drains_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The issue's check: a worker killed mid-job leaves its row ``running``
    and a successor queued; the next drain ends the orphan at its start (a
    cancel-requested orphan ends cancelled) and runs the successor."""
    import sys
    import types

    from procrastinate import App, testing

    from splitsmith.ui.jobs import JobStatus

    backend, user_id = _backend(
        tmp_path,
        [
            ("killed", JobStatus.RUNNING.value, False),
            ("killed-cancelled", JobStatus.RUNNING.value, True),
            ("next", JobStatus.PENDING.value, False),
        ],
    )
    app = App(connector=testing.InMemoryConnector())
    ran: list[str] = []

    def register(target: App, _state: object, **_kw: object) -> None:
        @target.task(name=queue_mod.RUN_COMPUTE_JOB_TASK, queue="default")
        async def _run(*, job_id: str, **_rest: object) -> None:
            ran.append(job_id)

    server = types.ModuleType("splitsmith.ui.server")
    server.build_worker_state = lambda **_k: types.SimpleNamespace(  # type: ignore[attr-defined]
        db_engines=None, build_tenant=lambda _uid: types.SimpleNamespace(jobs=backend)
    )
    server._configure_app_logging = lambda: None  # type: ignore[attr-defined]
    server.warm_ensemble_runtime = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "splitsmith.ui.server", server)
    monkeypatch.setattr(queue_mod, "init_sentry", lambda **_k: None)
    monkeypatch.setattr(queue_mod, "build_app", lambda _url, **_k: app)
    monkeypatch.setattr(queue_mod, "register_compute_task", register)
    monkeypatch.setattr(queue_mod, "_job_sweep", lambda: None)

    async def seed() -> None:
        async with app.open_async():
            await _orphan(app, job_id="killed", user_id=user_id, heartbeat_age_s=300)
            await _orphan(app, job_id="killed-cancelled", user_id=user_id, heartbeat_age_s=300)
            await app.configure_task(name=queue_mod.RUN_COMPUTE_JOB_TASK, queue="user-x").defer_async(
                job_id="next", user_id=user_id, kind="shot_detect", args={}, match_id=None
            )

    asyncio.run(seed())
    asyncio.run(
        asyncio.wait_for(
            queue_mod.run_worker("postgresql+asyncpg://u:p@h/db", wait=False, deadline=time.monotonic() + 60),
            10,
        )
    )

    assert _status(backend, "killed") == ("failed", queue_mod.ORPHANED_JOB_ERROR)
    assert _status(backend, "killed-cancelled") == ("cancelled", None)
    assert ran == ["next"]
    statuses = sorted(job["status"] for job in app.connector.jobs.values())  # type: ignore[attr-defined]
    assert statuses == ["failed", "failed", "succeeded"]


def test_cancelling_a_running_job_wakes_a_worker(tmp_path: Path) -> None:
    """Cancel only flags a running row; if its worker is dead nothing honours
    the flag, so the cancel wakes a drain whose sweep ends it. A pending row
    is cancelled outright and needs no wake."""
    from splitsmith.ui.jobs import JobStatus

    backend, _ = _backend(
        tmp_path, [("running", JobStatus.RUNNING.value, False), ("pending", JobStatus.PENDING.value, False)]
    )
    kicks: list[str] = []
    backend._on_running_cancel = lambda: kicks.append("wake")  # type: ignore[attr-defined]
    asyncio.run(backend.cancel("pending"))  # type: ignore[attr-defined]
    assert kicks == []
    asyncio.run(backend.cancel("running"))  # type: ignore[attr-defined]
    assert kicks == ["wake"]
