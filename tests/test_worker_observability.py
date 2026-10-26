"""Worker facts on each job's timings: identity, container limits, uptime,
storage bytes and memory high-water marks (:mod:`splitsmith.observability`).

These exist so a benchmark can tell which worker ran a job and how big it
was; the per-job ``meta`` stays the body's own.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from splitsmith import observability as obs
from splitsmith.observability import PhaseTimer


@pytest.fixture(autouse=True)
def _fresh_worker_state(monkeypatch):
    monkeypatch.setattr(obs, "_WORKER_NAME", None)
    monkeypatch.setattr(obs, "_RUNNING", obs._RunningJobs())
    for var in (
        "CLOUD_RUN_JOB",
        "CLOUD_RUN_EXECUTION",
        "CLOUD_RUN_TASK_INDEX",
        obs.WORKER_VCPU_ENV,
        obs.WORKER_MEMORY_MB_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


def test_container_limits_read_cgroup_v2(tmp_path: Path) -> None:
    (tmp_path / "cpu.max").write_text("400000 100000\n", encoding="utf-8")
    (tmp_path / "memory.max").write_text(f"{8 * 1024**3}\n", encoding="utf-8")
    assert obs.container_limits(tmp_path) == {"vcpu": 4.0, "memory_mb": 8192}


def test_container_limits_unlimited_falls_back_to_cpu_count(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "cpu.max").write_text("max 100000\n", encoding="utf-8")
    (tmp_path / "memory.max").write_text("max\n", encoding="utf-8")
    monkeypatch.setattr(obs.os, "cpu_count", lambda: 12)
    assert obs.container_limits(tmp_path) == {"vcpu": 12.0, "memory_mb": None}


def test_container_limits_without_cgroup_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(obs.os, "cpu_count", lambda: 8)
    assert obs.container_limits(tmp_path / "absent") == {"vcpu": 8.0, "memory_mb": None}


def test_container_limits_prefer_the_deployed_size(tmp_path: Path, monkeypatch) -> None:
    """Cloud Run's sandbox shows no cgroup limits and the host's cores; the
    deploy's stated size wins over anything read from the system."""
    (tmp_path / "cpu.max").write_text("800000 100000\n", encoding="utf-8")
    monkeypatch.setenv(obs.WORKER_VCPU_ENV, "4")
    monkeypatch.setenv(obs.WORKER_MEMORY_MB_ENV, "8192")
    assert obs.container_limits(tmp_path) == {"vcpu": 4.0, "memory_mb": 8192}


def test_container_limits_read_cgroup_v1(tmp_path: Path) -> None:
    (tmp_path / "cpu").mkdir()
    (tmp_path / "cpu" / "cpu.cfs_quota_us").write_text("200000\n", encoding="utf-8")
    (tmp_path / "cpu" / "cpu.cfs_period_us").write_text("100000\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "memory.limit_in_bytes").write_text(f"{4 * 1024**3}\n", encoding="utf-8")
    assert obs.container_limits(tmp_path) == {"vcpu": 2.0, "memory_mb": 4096}


def test_container_limits_v1_unlimited(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "cpu").mkdir()
    (tmp_path / "cpu" / "cpu.cfs_quota_us").write_text("-1\n", encoding="utf-8")
    (tmp_path / "cpu" / "cpu.cfs_period_us").write_text("100000\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "memory.limit_in_bytes").write_text("9223372036854771712\n", encoding="utf-8")
    monkeypatch.setattr(obs.os, "cpu_count", lambda: 6)
    assert obs.container_limits(tmp_path) == {"vcpu": 6.0, "memory_mb": None}


def test_worker_name_prefers_an_explicit_name_over_cloud_run(monkeypatch) -> None:
    monkeypatch.setenv("CLOUD_RUN_JOB", "splitsmith-worker-staging")
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "splitsmith-worker-staging-x7k2p")
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "0")
    assert obs.worker_name() == "cloudrun:splitsmith-worker-staging-x7k2p#0"
    obs.set_worker_name("agent:wk_123")
    assert obs.worker_name() == "agent:wk_123"


def test_worker_name_falls_back_to_the_host(monkeypatch) -> None:
    monkeypatch.setattr(obs.socket, "gethostname", lambda: "gaspode")
    assert obs.worker_name() == "host:gaspode"


def test_probe_records_identity_uptime_and_storage_deltas(monkeypatch) -> None:
    monkeypatch.setattr(obs, "container_limits", lambda: {"vcpu": 4.0, "memory_mb": 8192})
    obs.set_worker_name("cloudrun:exec#0")
    obs.STORAGE_IO.add_read(1000)  # before the job: not its bytes
    probe = obs.WorkerProbe.start()
    obs.STORAGE_IO.add_read(250)
    obs.STORAGE_IO.add_written(40)
    facts = probe.finish()

    assert facts["name"] == "cloudrun:exec#0"
    assert facts["vcpu"] == 4.0
    assert facts["memory_mb"] == 8192
    assert facts["uptime_at_start_ms"] >= 0
    assert facts["storage_read_bytes"] == 250
    assert facts["storage_written_bytes"] == 40
    assert facts["ran_alone"] is True
    assert facts["peak_rss_mb"] > 0


def test_overlapping_jobs_are_not_alone() -> None:
    first = obs.WorkerProbe.start()
    second = obs.WorkerProbe.start()
    assert second.finish()["ran_alone"] is False
    # ``first`` started alone but ``second`` started while it ran.
    assert first.finish()["ran_alone"] is False
    assert obs.WorkerProbe.start().finish()["ran_alone"] is True


def test_phasetimer_carries_worker_facts_only_when_set() -> None:
    timer = PhaseTimer()
    assert "worker" not in timer.build()
    timer.set_worker({"name": "agent:wk_1"})
    built = timer.build()
    assert built["worker"] == {"name": "agent:wk_1"}
    assert built["meta"] == {}


def test_s3_storage_counts_bytes_moved(monkeypatch) -> None:
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    from splitsmith.storage import S3Storage

    read0, written0 = obs.STORAGE_IO.snapshot()
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="bench")
        storage = S3Storage(bucket="bench", client=client)
        storage.write_bytes("a.bin", b"x" * 10)
        import io

        storage.upload_stream("b.bin", io.BytesIO(b"y" * 7))
        assert storage.read_bytes("a.bin") == b"x" * 10
        with storage.open_stream("b.bin") as fh:
            assert fh.read() == b"y" * 7

    read, written = obs.STORAGE_IO.snapshot()
    assert (read - read0, written - written0) == (17, 17)


def test_postgres_backend_persists_worker_facts(tmp_path) -> None:
    from splitsmith.ui.jobs import JobStatus
    from tests.test_postgres_job_backend import (
        _build_backend_for_new_user,
        _fetch_row,
        _register,
        _wait_until,
    )

    obs.set_worker_name("agent:wk_bench")
    backend, session_factory, _ = _build_backend_for_new_user(tmp_path)
    done = threading.Event()

    def work(handle):
        obs.STORAGE_IO.add_read(123)
        handle.timer.set_meta(input_bytes=5)
        done.set()

    _register(backend, "test", work)
    job = asyncio.run(backend.submit(kind="test"))
    assert done.wait(timeout=2.0)
    assert _wait_until(lambda: asyncio.run(backend.get(job.id)).status == JobStatus.SUCCEEDED)

    timings = _fetch_row(session_factory, job.id).timings
    assert timings["meta"] == {"input_bytes": 5}
    assert timings["worker"]["name"] == "agent:wk_bench"
    assert timings["worker"]["storage_read_bytes"] == 123


def test_worker_env_states_the_size_deploy_app_sets() -> None:
    """The worker env files repeat deploy-app.yml's --cpu / --memory; a
    resize that changes one and not the other misreports every job."""
    import re

    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "deploy-app.yml").read_text(encoding="utf-8")
    match = re.search(r"--args=worker,--one-shot.*?--cpu (\d+) --memory (\d+)Gi", workflow, re.S)
    assert match, "the worker job's --cpu / --memory flags moved"
    cpu, gib = match.groups()
    for env in ("staging", "production"):
        values = yaml.safe_load(
            (root / "deploy" / "cloudrun" / f"{env}-worker.env.yaml").read_text(encoding="utf-8")
        )
        assert values[obs.WORKER_VCPU_ENV] == cpu
        assert values[obs.WORKER_MEMORY_MB_ENV] == str(int(gib) * 1024)
