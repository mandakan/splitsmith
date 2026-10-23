"""Cloud Run Job launcher and the "r2" wake channel (``worker_trigger``, ``agent``).

The dispatcher's Cloud Run leg is mocked at the httpx transport (metadata-server
token + ``jobs.run``); the wake marker is a recording fake. Same harness as
``test_worker_trigger.py``: a real WakeChannelRegistry and an in-memory SQLite
WorkersStore.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from splitsmith.agent import _marker_poll_loop, _WakeCoordinator
from splitsmith.worker_channel import WakeChannelRegistry
from splitsmith.worker_trigger import (
    ENV_WAKE_CHANNEL,
    ENV_WORKER_JOB,
    ENV_WORKER_LAUNCHER,
    GCP_METADATA_TOKEN_URL,
    WAKE_MARKER_KEY,
    CloudRunJobConfig,
    WakeMarker,
    WorkerDispatcher,
    build_worker_dispatcher,
    load_cloud_run_config,
    platform_worker_kind,
)
from tests.test_worker_trigger import _add_home, _drain_tasks, _fresh_store, _pending

JOB = "projects/p/locations/europe-west1/jobs/splitsmith-worker-staging"


def _gcp_transport(calls: list[httpx.Request], *, run_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if str(request.url) == GCP_METADATA_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "sa-token", "expires_in": 3600})
        return httpx.Response(run_status, json={"name": "operations/op-1"})

    return httpx.MockTransport(handler)


class _FakeMarker:
    def __init__(self, *, fail: bool = False) -> None:
        self.touches = 0
        self.fail = fail

    async def touch(self) -> None:
        self.touches += 1
        if self.fail:
            raise OSError("bucket unreachable")

    async def read_heartbeats(self, worker_ids: list[str]) -> dict[str, Any]:
        # Every box here is alive; stale and missing heartbeats are
        # ``tests/test_worker_heartbeat.py``.
        from datetime import UTC, datetime

        from splitsmith.worker_trigger import Heartbeat

        return {i: Heartbeat(at=datetime.now(UTC)) for i in worker_ids}


def _dispatcher(store: Any, registry: WakeChannelRegistry, **kwargs: Any) -> WorkerDispatcher:
    kwargs.setdefault("worker_active", None)
    kwargs.setdefault("pending_jobs", _pending(0))
    kwargs.setdefault("grace_seconds", 0.01)
    kwargs.setdefault("watch_seconds", None)  # the after-wake watch has its own tests
    return WorkerDispatcher(store, registry, **kwargs)


async def _cloud_run_row(store: Any) -> str:
    await store.ensure_platform_row("cloud_run")
    rows = [w for w in await store.list() if w.kind == "cloud_run"]
    assert len(rows) == 1
    return rows[0].id


def test_cloud_run_leg_starts_the_job_with_the_metadata_token() -> None:
    store = _fresh_store()
    calls: list[httpx.Request] = []

    async def scenario() -> list[bool]:
        await _cloud_run_row(store)
        dispatcher = _dispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=CloudRunJobConfig(job=JOB),
            transport=_gcp_transport(calls),
            cooldown_seconds=0.0,
        )
        return [await dispatcher.trigger(), await dispatcher.trigger()]

    assert asyncio.run(scenario()) == [True, True]
    token_calls = [c for c in calls if str(c.url) == GCP_METADATA_TOKEN_URL]
    run_calls = [c for c in calls if c not in token_calls]
    # One token fetch serves both starts (cached until near expiry).
    assert len(token_calls) == 1
    assert token_calls[0].headers["Metadata-Flavor"] == "Google"
    assert [str(c.url) for c in run_calls] == [f"https://run.googleapis.com/v2/{JOB}:run"] * 2
    assert all(c.method == "POST" and c.headers["Authorization"] == "Bearer sa-token" for c in run_calls)


def test_cloud_run_start_failure_is_swallowed() -> None:
    store = _fresh_store()
    calls: list[httpx.Request] = []

    async def scenario() -> bool:
        await _cloud_run_row(store)
        dispatcher = _dispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=CloudRunJobConfig(job=JOB),
            transport=_gcp_transport(calls, run_status=403),
        )
        return await dispatcher.trigger()

    assert asyncio.run(scenario()) is False


def test_connected_home_worker_absorbs_the_wake_before_cloud_run() -> None:
    store = _fresh_store()
    calls: list[httpx.Request] = []

    async def scenario() -> tuple[bool, int]:
        home_id = await _add_home(store)
        await _cloud_run_row(store)
        registry = WakeChannelRegistry()
        queue = registry.connect(home_id)
        dispatcher = _dispatcher(
            store, registry, cloud_run=CloudRunJobConfig(job=JOB), transport=_gcp_transport(calls)
        )
        fired = await dispatcher.trigger()
        await _drain_tasks(dispatcher)
        return fired, queue.qsize()

    assert asyncio.run(scenario()) == (True, 1)
    assert calls == []


def test_wake_marker_is_written_once_per_tier_and_wakes_every_box() -> None:
    store = _fresh_store()
    marker = _FakeMarker()

    async def scenario() -> tuple[bool, list[Any]]:
        a = await _add_home(store, "a")
        b = await _add_home(store, "b")
        dispatcher = _dispatcher(store, WakeChannelRegistry(), wake_marker=marker)
        fired = await dispatcher.trigger()
        await _drain_tasks(dispatcher)
        return fired, [(await store.get(i)).last_wake_at for i in (a, b)]  # type: ignore[union-attr]

    fired, stamps = asyncio.run(scenario())
    assert fired is True
    assert marker.touches == 1
    assert all(stamp is not None for stamp in stamps)


def test_marker_wake_escalates_to_cloud_run_when_no_box_drains() -> None:
    """A marker cannot tell whether a box is there; the grace window is the net."""
    store = _fresh_store()
    calls: list[httpx.Request] = []
    marker = _FakeMarker()

    async def scenario() -> None:
        await _add_home(store)
        await _cloud_run_row(store)
        dispatcher = _dispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=CloudRunJobConfig(job=JOB),
            wake_marker=marker,
            transport=_gcp_transport(calls),
            pending_jobs=_pending(1),
            worker_active=_no_heartbeat,
        )
        assert await dispatcher.trigger() is True
        await _drain_tasks(dispatcher)

    asyncio.run(scenario())
    assert marker.touches == 1
    assert any(str(c.url).endswith(":run") for c in calls)


async def _no_heartbeat() -> bool:
    return False


def test_failed_marker_write_falls_through_to_cloud_run() -> None:
    store = _fresh_store()
    calls: list[httpx.Request] = []

    async def scenario() -> bool:
        await _add_home(store)
        await _cloud_run_row(store)
        dispatcher = _dispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=CloudRunJobConfig(job=JOB),
            wake_marker=_FakeMarker(fail=True),
            transport=_gcp_transport(calls),
        )
        return await dispatcher.trigger()

    assert asyncio.run(scenario()) is True
    assert any(str(c.url).endswith(":run") for c in calls)


def test_wake_marker_touch_puts_a_new_body() -> None:
    puts: list[dict[str, Any]] = []

    class _S3:
        def put_object(self, **kwargs: Any) -> None:
            puts.append(kwargs)

    marker = WakeMarker(_S3(), "bucket")
    asyncio.run(marker.touch())
    asyncio.run(marker.touch())
    assert [p["Key"] for p in puts] == [WAKE_MARKER_KEY] * 2
    assert puts[0]["Bucket"] == "bucket"
    assert puts[0]["Body"] != puts[1]["Body"]  # a new ETag each time


def test_platform_row_is_seeded_listed_and_undeletable() -> None:
    store = _fresh_store()

    async def scenario() -> tuple[int, bool, list[str]]:
        row_id = await _cloud_run_row(store)
        await store.ensure_platform_row("cloud_run", version="1.2.3")
        rows = [w for w in await store.list() if w.kind == "cloud_run"]
        deleted = await store.delete(row_id)
        enabled = [w.kind for w in await store.list_enabled()]
        return len(rows), deleted, enabled

    count, deleted, enabled = asyncio.run(scenario())
    assert count == 1
    assert deleted is False
    assert enabled == ["cloud_run"]
    with pytest.raises(ValueError):
        asyncio.run(store.ensure_platform_row("self_hosted"))


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        ENV_WORKER_LAUNCHER,
        ENV_WORKER_JOB,
        ENV_WAKE_CHANNEL,
    ):
        monkeypatch.delenv(var, raising=False)


def test_cloud_run_launcher_requires_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(ENV_WORKER_LAUNCHER, "cloud_run")
    store = _fresh_store()
    with pytest.raises(ValueError, match=ENV_WORKER_JOB):
        build_worker_dispatcher(store, WakeChannelRegistry(), worker_active=None, pending_jobs=_pending(0))
    monkeypatch.setenv(ENV_WORKER_JOB, JOB)
    dispatcher = build_worker_dispatcher(
        store, WakeChannelRegistry(), worker_active=None, pending_jobs=_pending(0)
    )
    assert dispatcher is not None and dispatcher._cloud_run == CloudRunJobConfig(job=JOB)


def test_job_name_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(ENV_WORKER_JOB, "splitsmith-worker")
    with pytest.raises(ValueError):
        load_cloud_run_config()


def test_platform_worker_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    assert platform_worker_kind() is None
    monkeypatch.setenv(ENV_WORKER_JOB, JOB)
    assert platform_worker_kind() == "cloud_run"


def test_marker_poll_loop_wakes_on_first_poll_and_on_every_change() -> None:
    etags: list[Any] = [None, None, '"a"', '"a"', OSError("blip"), '"b"']
    wakes: list[int] = []

    async def head() -> str | None:
        value = etags.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def scenario() -> None:
        coordinator = _WakeCoordinator()
        stop = asyncio.Event()
        original = coordinator.dispatch

        def record(name: str) -> str | None:
            wakes.append(6 - len(etags))
            return original(name)

        coordinator.dispatch = record  # type: ignore[method-assign]
        await _marker_poll_loop(head, coordinator, stop, interval=0.0, max_polls=6)

    asyncio.run(scenario())
    # Poll 1: catch-up drain; poll 3: marker created; poll 6: marker moved
    # (the failed poll 5 kept '"a"' as the last seen ETag).
    assert wakes == [1, 3, 6]


def test_register_bundle_names_the_r2_channel(monkeypatch: pytest.MonkeyPatch, hosted_env: str) -> None:
    from splitsmith.ui.server import create_app
    from tests.test_worker_routes import seed_pending_worker

    monkeypatch.setenv(ENV_WAKE_CHANNEL, "r2")
    monkeypatch.setenv("SPLITSMITH_S3_BUCKET", "splits")
    monkeypatch.setenv("SPLITSMITH_S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("SPLITSMITH_S3_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("SPLITSMITH_S3_SECRET_ACCESS_KEY", "sk")
    _, reg_token = seed_pending_worker(hosted_env)
    with TestClient(create_app()) as client:
        resp = client.post("/api/workers/register", json={"token": reg_token})
    assert resp.status_code == 200
    assert resp.json()["credentials"]["wake"] == {
        "kind": "r2",
        "key": WAKE_MARKER_KEY,
        "interval_seconds": 30.0,
    }


def test_r2_channel_without_a_bucket_fails_the_boot(monkeypatch: pytest.MonkeyPatch, hosted_env: str) -> None:
    from splitsmith.ui.server import create_app

    monkeypatch.setenv(ENV_WAKE_CHANNEL, "r2")
    monkeypatch.delenv("SPLITSMITH_S3_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="r2"):
        create_app()
