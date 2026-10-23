"""The Cloud Run worker sweeps its source cache after every job (#1676).

Its filesystem is in memory, so mirrored sources and written trims count
against the container's memory; a drain that only grows its cache is
killed part-way through a match.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from splitsmith import observability as obs
from splitsmith import queue as queue_mod
from splitsmith.source_cache import per_job_cache_max_bytes

GIB = 1024**3


def test_no_per_job_sweep_off_cloud_run() -> None:
    assert per_job_cache_max_bytes({"SPLITSMITH_WORKER_MEMORY_MB": "8192"}) is None


def test_cloud_run_caps_the_cache_at_a_quarter_of_its_memory() -> None:
    env = {"CLOUD_RUN_EXECUTION": "x", "SPLITSMITH_WORKER_MEMORY_MB": "8192"}
    assert per_job_cache_max_bytes(env) == 2 * GIB


def test_an_explicit_cache_cap_wins_on_cloud_run() -> None:
    env = {
        "CLOUD_RUN_EXECUTION": "x",
        "SPLITSMITH_WORKER_MEMORY_MB": "8192",
        "SPLITSMITH_SOURCE_CACHE_MAX_GB": "0.5",
    }
    assert per_job_cache_max_bytes(env) == GIB // 2


def test_cloud_run_without_a_stated_memory_keeps_one_gib() -> None:
    assert per_job_cache_max_bytes({"CLOUD_RUN_EXECUTION": "x"}) == GIB


class _App:
    def __init__(self) -> None:
        self.tasks: dict[str, object] = {}

    def task(self, *, name: str, queue: str):
        def register(fn):
            self.tasks[name] = fn
            return fn

        return register


def _state(run_job) -> SimpleNamespace:
    async def get_row(match_id: str) -> object:
        return SimpleNamespace(match_id=match_id, origin="hosted")

    return SimpleNamespace(
        build_tenant=lambda _uid: SimpleNamespace(looks=None),
        jobs=SimpleNamespace(run_job=run_job),
        matches=SimpleNamespace(resolve=lambda _mid: Path("/nowhere")),
        matches_store=SimpleNamespace(get=get_row),
    )


@pytest.mark.parametrize("fails", [False, True])
def test_after_job_runs_once_each_job_ends(monkeypatch, fails: bool) -> None:
    from splitsmith.ui import server

    monkeypatch.setattr(server, "tenant_looks_provider", lambda _tenant: None)
    calls: list[str] = []

    async def run_job(*, job_id, kind, args, before_body) -> None:
        calls.append("job")
        if fails:
            raise RuntimeError("body crashed")

    async def after_job() -> None:
        calls.append("sweep")

    app = _App()
    queue_mod.register_compute_task(app, _state(run_job), after_job=after_job)
    task = app.tasks[queue_mod.RUN_COMPUTE_JOB_TASK]
    if fails:
        with pytest.raises(RuntimeError):
            asyncio.run(task(job_id="j", user_id="u", kind="trim", match_id=None))
    else:
        asyncio.run(task(job_id="j", user_id="u", kind="trim", match_id=None))
    assert calls == ["job", "sweep"]


def _cache(root: Path) -> tuple[Path, Path]:
    old = root / "users" / "u" / "matches" / "m" / "raw" / "old.mp4"
    new = root / "users" / "u" / "matches" / "m" / "trimmed" / "new_trimmed.mp4"
    for path in (old, new):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\0" * 600_000)
    past = time.time() - 3600
    os.utime(old, (past, past))
    return old, new


def _cloud_run_env(monkeypatch, root: Path) -> None:
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "splitsmith-worker-staging-x")
    monkeypatch.setenv("SPLITSMITH_PROJECTS_DIR", str(root))
    monkeypatch.setenv("SPLITSMITH_SOURCE_CACHE_MAX_GB", str(1_000_000 / GIB))  # a 1 MB budget


def test_the_cloud_run_sweep_evicts_the_least_recent_files(monkeypatch, tmp_path: Path) -> None:
    _cloud_run_env(monkeypatch, tmp_path)
    old, new = _cache(tmp_path)
    sweep = queue_mod._job_sweep()
    assert sweep is not None
    asyncio.run(sweep())
    assert not old.exists() and new.exists()


def test_the_sweep_waits_while_another_job_runs(monkeypatch, tmp_path: Path) -> None:
    _cloud_run_env(monkeypatch, tmp_path)
    old, new = _cache(tmp_path)
    monkeypatch.setattr(obs, "_RUNNING", obs._RunningJobs())
    obs._RUNNING.begin()  # another job holds files open
    sweep = queue_mod._job_sweep()
    assert sweep is not None
    asyncio.run(sweep())
    assert old.exists() and new.exists()


def test_no_sweep_off_cloud_run(monkeypatch) -> None:
    monkeypatch.delenv("CLOUD_RUN_EXECUTION", raising=False)
    assert queue_mod._job_sweep() is None
