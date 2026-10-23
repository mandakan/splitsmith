"""Self-hosted heartbeats on the "r2" wake channel (#1672).

A box on the bucket-marker channel never opens the SSE channel, so serve
never stamped it seen: the admin view showed every box offline and the
dispatcher woke a months-dead row at priority 1000 ahead of Cloud Run. The
box now rewrites a heartbeat object in the bucket on its marker-poll cycle
(rate-limited), and serve reads it: a fresh one is online, a stale or
missing one is skipped and the next tier is woken.

The heartbeat goes to the bucket, not through a serve route: a request every
few minutes would keep serve and its Neon compute awake around the clock,
which is the reason the "r2" channel exists.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from splitsmith import agent
from splitsmith.agent import _marker_poll_loop, _WakeCoordinator
from splitsmith.worker_channel import WakeChannelRegistry
from splitsmith.worker_trigger import (
    ENV_WAKE_CHANNEL,
    HEARTBEAT_SECONDS,
    HEARTBEAT_STALE_SECONDS,
    CloudRunJobConfig,
    Heartbeat,
    WakeMarker,
    WorkerDispatcher,
    heartbeat_key,
)
from tests.test_worker_cloud_run import JOB, _cloud_run_row, _gcp_transport
from tests.test_worker_trigger import _add_home, _drain_tasks, _fresh_store, _pending

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def _fresh() -> Heartbeat:
    return Heartbeat(at=datetime.now(UTC) - timedelta(seconds=30), version="0.90.0")


def _stale() -> Heartbeat:
    return Heartbeat(at=datetime.now(UTC) - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 60), version="0.10.6")


class _Marker:
    """The dispatcher's view of the bucket: marker writes and heartbeats."""

    def __init__(self, beats: dict[str, Heartbeat | None] | None = None, *, fail_read: bool = False) -> None:
        self.touches = 0
        self.beats = beats or {}
        self.fail_read = fail_read

    async def touch(self) -> None:
        self.touches += 1

    async def read_heartbeats(self, worker_ids: list[str]) -> dict[str, Heartbeat | None]:
        if self.fail_read:
            raise OSError("bucket unreachable")
        return {i: self.beats.get(i) for i in worker_ids}


def _dispatcher(store: Any, marker: _Marker, calls: list[httpx.Request]) -> WorkerDispatcher:
    return WorkerDispatcher(
        store,
        WakeChannelRegistry(),
        worker_active=None,
        pending_jobs=_pending(0),
        grace_seconds=0.01,
        wake_marker=marker,  # type: ignore[arg-type]
        cloud_run=CloudRunJobConfig(job=JOB),
        transport=_gcp_transport(calls),
    )


def _cloud_run_started(calls: list[httpx.Request]) -> bool:
    return any(str(c.url).endswith(":run") for c in calls)


# ---------------------------------------------------------------------------
# Heartbeat freshness
# ---------------------------------------------------------------------------


def test_heartbeat_freshness_threshold() -> None:
    assert Heartbeat(at=NOW - timedelta(seconds=HEARTBEAT_STALE_SECONDS - 1)).fresh(NOW)
    assert not Heartbeat(at=NOW - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 1)).fresh(NOW)
    # A beat is several times more frequent than the stale threshold.
    assert HEARTBEAT_STALE_SECONDS >= 3 * HEARTBEAT_SECONDS


# ---------------------------------------------------------------------------
# The bucket objects
# ---------------------------------------------------------------------------


class _S3:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.fail_keys: set[str] = set()

    def put_object(self, **kwargs: Any) -> None:
        self.objects[kwargs["Key"]] = {"LastModified": NOW, "Metadata": kwargs.get("Metadata", {})}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        if key in self.fail_keys:
            raise ClientError({"Error": {"Code": "500"}}, "HeadObject")
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return self.objects[key]


def test_beat_writes_the_workers_key_and_version_and_serve_reads_it() -> None:
    s3 = _S3()
    marker = WakeMarker(s3, "bucket")
    asyncio.run(marker.beat("w-live", version="0.90.0"))
    s3.fail_keys.add(heartbeat_key("w-flaky"))

    beats = asyncio.run(marker.read_heartbeats(["w-live", "w-never", "w-flaky"]))
    assert heartbeat_key("w-live") in s3.objects
    assert beats["w-live"] == Heartbeat(at=NOW, version="0.90.0")
    assert beats["w-never"] is None  # no object: never beat, so not alive
    assert "w-flaky" not in beats  # read failed: unknown, not dead


# ---------------------------------------------------------------------------
# The agent's poll loop
# ---------------------------------------------------------------------------


def test_marker_poll_loop_beats_on_the_first_poll_then_every_interval() -> None:
    clock = [0.0]
    beats: list[float] = []
    outcomes: list[Exception | None] = [None, OSError("blip"), None, None]

    async def head() -> str | None:
        clock[0] += 30.0  # each poll is 30 s after the last
        return '"a"'

    async def beat() -> None:
        beats.append(clock[0])
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome

    async def scenario() -> None:
        await _marker_poll_loop(
            head,
            _WakeCoordinator(),
            asyncio.Event(),
            interval=0.0,
            max_polls=12,
            beat=beat,
            beat_interval=120.0,
            clock=lambda: clock[0],
        )

    asyncio.run(scenario())
    # Polls run at t=30, 60, ..., 360 (the clock moves in head, after the
    # beat check). First beat on the first poll; the next is due 120 s later;
    # that one fails and is retried on the very next poll.
    assert beats == [0.0, 120.0, 150.0, 270.0]


def test_run_agent_beats_in_r2_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """``run_agent`` hands the poll loop a beat that writes this worker's heartbeat."""
    seen: dict[str, Any] = {}

    async def fake_poll(head: Any, coordinator: Any, stop: Any, **kwargs: Any) -> None:
        seen.update(kwargs)
        await kwargs["beat"]()
        stop.set()
        coordinator.wake_event.set()

    s3 = _S3()
    monkeypatch.setattr(agent, "_marker_poll_loop", fake_poll)
    monkeypatch.setattr(agent, "_bucket_client", lambda creds: s3)
    monkeypatch.setattr(agent, "_agent_version", lambda: "0.90.0")
    agent.AgentState(
        server_url="https://example.test",
        worker_id="w-box",
        worker_token="tok",
        credentials={
            "database_url": "postgresql+asyncpg://u:p@h/db",
            "public_url": "https://example.test",
            "s3": {
                "bucket": "b",
                "endpoint_url": None,
                "region": "auto",
                "access_key_id": "ak",
                "secret_access_key": "sk",
            },
            "wake": {"kind": "r2", "key": "_splitsmith/wake/self-hosted", "interval_seconds": 30.0},
        },
    ).save(tmp_path)
    for name in (
        "SPLITSMITH_MODE",
        "SPLITSMITH_DATABASE_URL",
        "SPLITSMITH_PUBLIC_URL",
        "SPLITSMITH_S3_BUCKET",
        "SPLITSMITH_S3_ENDPOINT_URL",
        "SPLITSMITH_S3_REGION",
        "SPLITSMITH_S3_ACCESS_KEY_ID",
        "SPLITSMITH_S3_SECRET_ACCESS_KEY",
        "SPLITSMITH_PROJECTS_DIR",
    ):
        # setenv first so teardown restores the variable whatever
        # apply_credentials writes into os.environ.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)

    asyncio.run(agent.run_agent("https://example.test", registration_token=None, state_dir=tmp_path))
    assert seen["beat_interval"] == HEARTBEAT_SECONDS
    assert s3.objects[heartbeat_key("w-box")]["Metadata"] == {"version": "0.90.0"}


# ---------------------------------------------------------------------------
# The dispatcher
# ---------------------------------------------------------------------------


def test_stale_self_hosted_row_is_skipped_for_the_next_tier() -> None:
    """The staging incident: a dead box at priority 1000 above Cloud Run at 100."""
    store = _fresh_store()
    calls: list[httpx.Request] = []

    async def scenario() -> _Marker:
        dead = await _add_home(store, "gaspode-old")
        await _cloud_run_row(store)
        marker = _Marker({dead: _stale()})
        assert await _dispatcher(store, marker, calls).trigger() is True
        return marker

    marker = asyncio.run(scenario())
    assert marker.touches == 0
    assert _cloud_run_started(calls)


def test_never_beaten_row_is_skipped() -> None:
    store = _fresh_store()
    calls: list[httpx.Request] = []

    async def scenario() -> _Marker:
        await _add_home(store, "silent")
        await _cloud_run_row(store)
        marker = _Marker({})
        await _dispatcher(store, marker, calls).trigger()
        return marker

    marker = asyncio.run(scenario())
    assert marker.touches == 0
    assert _cloud_run_started(calls)


def test_live_box_takes_the_wake_and_a_stale_row_in_its_tier_is_not_stamped() -> None:
    store = _fresh_store()
    calls: list[httpx.Request] = []

    async def scenario() -> tuple[_Marker, Any, Any]:
        live = await _add_home(store, "gaspode")
        dead = await _add_home(store, "gaspode-old")
        await _cloud_run_row(store)
        marker = _Marker({live: _fresh(), dead: _stale()})
        dispatcher = _dispatcher(store, marker, calls)
        assert await dispatcher.trigger() is True
        await _drain_tasks(dispatcher)
        return marker, (await store.get(live)).last_wake_at, (await store.get(dead)).last_wake_at  # type: ignore[union-attr]

    marker, live_wake, dead_wake = asyncio.run(scenario())
    assert marker.touches == 1
    assert live_wake is not None
    assert dead_wake is None
    assert not _cloud_run_started(calls)


def test_stale_top_tier_falls_to_a_live_box_below_it() -> None:
    store = _fresh_store()
    calls: list[httpx.Request] = []

    async def scenario() -> tuple[_Marker, Any]:
        dead = await _add_home(store, "old", priority=1000)
        live = await _add_home(store, "new", priority=500)
        await _cloud_run_row(store)
        marker = _Marker({dead: _stale(), live: _fresh()})
        dispatcher = _dispatcher(store, marker, calls)
        await dispatcher.trigger()
        await _drain_tasks(dispatcher)
        return marker, (await store.get(live)).last_wake_at  # type: ignore[union-attr]

    marker, live_wake = asyncio.run(scenario())
    assert marker.touches == 1
    assert live_wake is not None
    assert not _cloud_run_started(calls)


def test_unreadable_heartbeats_keep_the_old_behaviour() -> None:
    """A bucket hiccup on the read is not evidence a box is dead: wake it, and
    the grace escalation remains the net."""
    store = _fresh_store()
    calls: list[httpx.Request] = []

    async def scenario() -> _Marker:
        await _add_home(store)
        await _cloud_run_row(store)
        marker = _Marker(fail_read=True)
        dispatcher = _dispatcher(store, marker, calls)
        await dispatcher.trigger()
        await _drain_tasks(dispatcher)
        return marker

    marker = asyncio.run(scenario())
    assert marker.touches == 1
    assert not _cloud_run_started(calls)


# ---------------------------------------------------------------------------
# The admin view, r2 mode, nothing connected over SSE
# ---------------------------------------------------------------------------


ADMIN_EMAIL = "admin@example.com"


@pytest.fixture
def r2_admin(hosted_env: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Any, str]]:
    from splitsmith.ui.server import create_app
    from tests.hosted_helpers import _CapturingSender, login

    monkeypatch.setenv("SPLITSMITH_ADMIN_EMAILS", ADMIN_EMAIL)
    monkeypatch.setenv(ENV_WAKE_CHANNEL, "r2")
    monkeypatch.setenv("SPLITSMITH_S3_BUCKET", "splits")
    monkeypatch.setenv("SPLITSMITH_S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("SPLITSMITH_S3_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("SPLITSMITH_S3_SECRET_ACCESS_KEY", "sk")
    app = create_app()
    sender = _CapturingSender()
    app.state.splitsmith_state.auth.backends[0]._email = sender
    with TestClient(app, follow_redirects=False) as client:
        login(client, sender, ADMIN_EMAIL)
        yield client, app, hosted_env


def test_admin_view_shows_a_beating_box_online_and_a_stale_one_offline(r2_admin: Any) -> None:
    from tests.test_worker_routes import register_worker

    client, app, db_url = r2_admin
    live_id, _ = register_worker(client, db_url, "gaspode")
    dead_id, _ = register_worker(client, db_url, "gaspode-old")
    silent_id, _ = register_worker(client, db_url, "silent")
    beat = _fresh()
    state = app.state.splitsmith_state
    assert state.wake_channels.connected_ids() == frozenset()  # nothing on SSE
    state.wake_marker = _Marker({live_id: beat, dead_id: _stale()})

    workers = {w["id"]: w for w in client.get("/api/admin/workers").json()["workers"]}
    assert workers[live_id]["status"] == "online"
    assert workers[live_id]["version"] == "0.90.0"
    assert datetime.fromisoformat(workers[live_id]["last_seen_at"]).replace(tzinfo=UTC) == beat.at
    assert workers[dead_id]["status"] == "offline"
    assert workers[silent_id]["status"] == "offline"

    patched = client.patch(f"/api/admin/workers/{live_id}", json={"priority": 900}).json()
    assert patched["status"] == "online"


def test_admin_view_survives_an_unreadable_bucket(r2_admin: Any) -> None:
    from tests.test_worker_routes import register_worker

    client, app, db_url = r2_admin
    worker_id, _ = register_worker(client, db_url, "gaspode")
    app.state.splitsmith_state.wake_marker = _Marker(fail_read=True)
    resp = client.get("/api/admin/workers")
    assert resp.status_code == 200
    assert {w["id"]: w for w in resp.json()["workers"]}[worker_id]["status"] == "offline"
