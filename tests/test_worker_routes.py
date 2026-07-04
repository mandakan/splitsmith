"""HTTP-surface tests for the worker endpoints (self-hosted workers, Task 6).

POST /api/workers/register - single-use registration-token exchange.
GET  /api/workers/channel  - long-lived SSE wake channel (Bearer worker token).

Both are token-authenticated (no session) and answer every failure with the
uniform ``404 {"detail": "not found"}`` - same precedent as the share surface.

The SSE body never completes, and both TestClient and httpx.ASGITransport
buffer the full response body, so the streaming tests drive one ASGI request
by hand (`_open_channel`) and the frame-level behavior (keepalive, finally
cleanup) is unit-tested against `_worker_channel_events` directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
from fastapi.testclient import TestClient

from splitsmith.db import create_engine, sessionmaker
from splitsmith.db.workers import WorkersStore
from tests.hosted_helpers import PUBLIC_URL, _CapturingSender

NOT_FOUND = {"detail": "not found"}

T = TypeVar("T")


def _with_store(db_url: str, fn: Callable[[WorkersStore], Awaitable[T]]) -> T:
    """Run one WorkersStore operation on a throwaway engine, then dispose it.

    Disposal matters: an undisposed aiosqlite engine leaves its connection
    worker thread bound to the closed asyncio.run loop, and the thread's
    death later surfaces as PytestUnhandledThreadExceptionWarning attributed
    to whatever unrelated test happens to be running."""

    async def _run() -> T:
        engine = create_engine(db_url)
        try:
            return await fn(WorkersStore(sessionmaker(engine)))
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def seed_pending_worker(db_url: str, name: str = "box") -> tuple[str, str]:
    """Create a pending self-hosted worker row.

    Returns (worker_id, plaintext_registration_token).
    """
    record, token = _with_store(db_url, lambda s: s.create_self_hosted(name))
    return record.id, token


def register_worker(client: TestClient, db_url: str, name: str = "box") -> tuple[str, str]:
    """Seed + register a worker via the HTTP endpoint.

    Returns (worker_id, worker_token).
    """
    worker_id, reg_token = seed_pending_worker(db_url, name)
    resp = client.post("/api/workers/register", json={"token": reg_token})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["worker_id"] == worker_id
    return worker_id, body["worker_token"]


# ---------------------------------------------------------------------------
# Local mode: no worker surface at all
# ---------------------------------------------------------------------------


def test_local_mode_register_is_uniform_404() -> None:
    from splitsmith.ui.server import create_app

    with TestClient(create_app()) as client:
        resp = client.post("/api/workers/register", json={"token": "anything"})
        assert resp.status_code == 404
        assert resp.json() == NOT_FOUND


def test_local_mode_channel_is_uniform_404() -> None:
    from splitsmith.ui.server import create_app

    with TestClient(create_app()) as client:
        resp = client.get("/api/workers/channel", headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 404
        assert resp.json() == NOT_FOUND


# ---------------------------------------------------------------------------
# POST /api/workers/register - malformed body
# ---------------------------------------------------------------------------


def test_register_malformed_body_is_uniform_404() -> None:
    """Missing body, missing required field, and truncated JSON all return 404.

    FastAPI's default RequestValidationError -> 422 must be intercepted for
    this path so the endpoint existence and its expected input shape are
    not revealed to unauthenticated callers.
    """
    from splitsmith.ui.server import create_app

    with TestClient(create_app()) as client:
        # No body at all
        resp = client.post("/api/workers/register")
        assert resp.status_code == 404, f"no body: {resp.text}"
        assert resp.json() == NOT_FOUND

        # Valid JSON but missing required `token` field
        resp = client.post("/api/workers/register", json={})
        assert resp.status_code == 404, f"empty json: {resp.text}"
        assert resp.json() == NOT_FOUND

        # Truncated / invalid JSON
        resp = client.post(
            "/api/workers/register",
            content=b"{",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 404, f"invalid json: {resp.text}"
        assert resp.json() == NOT_FOUND


# ---------------------------------------------------------------------------
# POST /api/workers/register
# ---------------------------------------------------------------------------


def test_register_unknown_token_is_uniform_404(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    resp = client.post("/api/workers/register", json={"token": "no-such-token"})
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_register_happy_path_returns_token_and_credentials(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    worker_id, reg_token = seed_pending_worker(hosted_env)

    resp = client.post("/api/workers/register", json={"token": reg_token, "info": {"hostname": "nas"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["worker_id"] == worker_id
    assert isinstance(body["worker_token"], str) and body["worker_token"]
    # Credentials echo the hosted env: DB URL + public URL; no S3 configured.
    assert body["credentials"] == {
        "database_url": hosted_env,
        "public_url": PUBLIC_URL,
        "s3": None,
    }
    # The info dict landed on the row.
    record = _with_store(hosted_env, lambda s: s.get(worker_id))
    assert record is not None and record.info == {"hostname": "nas"}


def test_register_replay_is_uniform_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    _, reg_token = seed_pending_worker(hosted_env)
    assert client.post("/api/workers/register", json={"token": reg_token}).status_code == 200

    replay = client.post("/api/workers/register", json={"token": reg_token})
    assert replay.status_code == 404
    assert replay.json() == NOT_FOUND


def test_register_credentials_include_s3_when_bucket_set(
    monkeypatch: pytest.MonkeyPatch,
    hosted_env: str,
) -> None:
    monkeypatch.setenv("SPLITSMITH_S3_BUCKET", "splits")
    monkeypatch.setenv("SPLITSMITH_S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("SPLITSMITH_S3_REGION", "auto")
    monkeypatch.setenv("SPLITSMITH_S3_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("SPLITSMITH_S3_SECRET_ACCESS_KEY", "sk")

    from splitsmith.ui.server import create_app

    _, reg_token = seed_pending_worker(hosted_env)
    with TestClient(create_app()) as client:
        resp = client.post("/api/workers/register", json={"token": reg_token})
    assert resp.status_code == 200
    assert resp.json()["credentials"]["s3"] == {
        "bucket": "splits",
        "endpoint_url": "http://minio:9000",
        "region": "auto",
        "access_key_id": "ak",
        "secret_access_key": "sk",
    }


# ---------------------------------------------------------------------------
# GET /api/workers/channel - auth failures (no streaming needed)
# ---------------------------------------------------------------------------


def test_channel_missing_header_is_uniform_404(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    resp = client.get("/api/workers/channel")
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_channel_wrong_scheme_is_uniform_404(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    _, worker_token = register_worker(client, hosted_env)
    resp = client.get("/api/workers/channel", headers={"Authorization": f"Token {worker_token}"})
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_channel_unknown_bearer_is_uniform_404(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    resp = client.get("/api/workers/channel", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


# ---------------------------------------------------------------------------
# GET /api/workers/channel - streaming (hand-driven ASGI request)
# ---------------------------------------------------------------------------


async def _open_channel(app: Any, worker_token: str) -> tuple[asyncio.Task[Any], asyncio.Queue[dict]]:
    """Start one GET /api/workers/channel ASGI request; returns (task, sent-messages)."""
    messages: asyncio.Queue[dict] = asyncio.Queue()

    async def receive() -> dict:
        await asyncio.Event().wait()  # hold the connection open forever
        raise AssertionError("unreachable")

    async def send(message: dict) -> None:
        await messages.put(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/workers/channel",
        "raw_path": b"/api/workers/channel",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {worker_token}".encode()),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    start = await asyncio.wait_for(messages.get(), timeout=5)
    assert start["type"] == "http.response.start", start
    assert start["status"] == 200, start
    headers = dict(start["headers"])
    assert headers[b"content-type"].startswith(b"text/event-stream")
    assert headers[b"cache-control"] == b"no-cache"
    return task, messages


async def _next_frame(messages: asyncio.Queue[dict]) -> bytes:
    """Next non-empty body chunk."""
    while True:
        message = await asyncio.wait_for(messages.get(), timeout=5)
        assert message["type"] == "http.response.body", message
        if message.get("body"):
            return message["body"]


async def _close_channel(task: asyncio.Task[Any]) -> None:
    task.cancel()
    try:
        await task
    except BaseException:
        pass


def test_channel_streams_wake_event_and_cleans_up(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    worker_id, worker_token = register_worker(client, hosted_env)
    app = client.app
    state = app.state.splitsmith_state

    async def scenario() -> bytes:
        task, messages = await _open_channel(app, worker_token)
        # The generator connects the channel on its first step; poll until
        # the push lands (push False = not connected yet).
        for _ in range(500):
            if state.wake_channels.push(worker_id, "wake"):
                break
            await asyncio.sleep(0.005)
        else:
            pytest.fail("worker channel never connected")
        frame = await _next_frame(messages)
        await _close_channel(task)
        # finally ran: the channel is disconnected from the registry.
        assert worker_id not in state.wake_channels.connected_ids()
        return frame

    frame = asyncio.run(scenario())
    assert b"event: wake\ndata: {}\n\n" in frame
    # Connecting stamped last_seen_at.
    record = _with_store(hosted_env, lambda s: s.get(worker_id))
    assert record is not None and record.last_seen_at is not None


def test_channel_disabled_row_gets_disabled_event_first(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, _ = hosted_app
    worker_id, worker_token = register_worker(client, hosted_env)
    _with_store(hosted_env, lambda s: s.update(worker_id, enabled=False))
    app = client.app

    async def scenario() -> bytes:
        task, messages = await _open_channel(app, worker_token)
        frame = await _next_frame(messages)
        await _close_channel(task)
        return frame

    frame = asyncio.run(scenario())
    assert frame.startswith(b"event: disabled\ndata: {}\n\n")


# ---------------------------------------------------------------------------
# _worker_channel_events generator (unit level: keepalive, retrigger, finally)
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, fail_from_call: int | None = None) -> None:
        self.touches: list[str] = []
        self._fail_from_call = fail_from_call

    async def touch_seen(self, worker_id: str) -> None:
        self.touches.append(worker_id)
        if self._fail_from_call is not None and len(self.touches) >= self._fail_from_call:
            raise RuntimeError("db gone")


def test_generator_emits_keepalive_comment_on_idle() -> None:
    from splitsmith.ui.server import _worker_channel_events
    from splitsmith.worker_channel import WakeChannelRegistry

    async def scenario() -> str:
        gen = _worker_channel_events(
            _FakeStore(),
            WakeChannelRegistry(),
            "w1",
            disabled=False,
            boot_retrigger=None,
            keepalive_seconds=0.01,
        )
        try:
            return await asyncio.wait_for(gen.__anext__(), timeout=5)
        finally:
            await gen.aclose()

    assert asyncio.run(scenario()) == ": ka\n\n"


def test_generator_fires_boot_retrigger_on_connect() -> None:
    from splitsmith.ui.server import _worker_channel_events
    from splitsmith.worker_channel import WakeChannelRegistry

    calls: list[str] = []

    async def retrigger() -> None:
        calls.append("ran")

    async def scenario() -> None:
        gen = _worker_channel_events(
            _FakeStore(),
            WakeChannelRegistry(),
            "w1",
            disabled=False,
            boot_retrigger=retrigger,
            keepalive_seconds=0.01,
        )
        try:
            await asyncio.wait_for(gen.__anext__(), timeout=5)
        finally:
            await gen.aclose()

    asyncio.run(scenario())
    assert calls == ["ran"]


def test_generator_finally_swallows_touch_seen_failure() -> None:
    """Closing the stream must disconnect the channel and never raise, even
    when the goodbye touch_seen hits a dead DB (the client is gone anyway)."""
    from splitsmith.ui.server import _worker_channel_events
    from splitsmith.worker_channel import WakeChannelRegistry

    store = _FakeStore(fail_from_call=2)  # connect touch succeeds, goodbye touch raises
    registry = WakeChannelRegistry()

    async def scenario() -> None:
        gen = _worker_channel_events(
            store,
            registry,
            "w1",
            disabled=True,
            boot_retrigger=None,
        )
        first = await asyncio.wait_for(gen.__anext__(), timeout=5)
        assert first == "event: disabled\ndata: {}\n\n"
        assert registry.connected_ids() == frozenset({"w1"})
        await gen.aclose()  # must not raise
        assert registry.connected_ids() == frozenset()

    asyncio.run(scenario())
    assert store.touches == ["w1", "w1"]


# ---------------------------------------------------------------------------
# Hosted boot lifespan: railway-row seeding
# ---------------------------------------------------------------------------


def test_hosted_boot_seeds_railway_row_when_env_configured(
    monkeypatch: pytest.MonkeyPatch,
    hosted_env: str,
) -> None:
    monkeypatch.setenv("SPLITSMITH_WORKER_TRIGGER_TOKEN", "t")
    monkeypatch.setenv("SPLITSMITH_WORKER_SERVICE_ID", "s")
    monkeypatch.setenv("SPLITSMITH_WORKER_ENVIRONMENT_ID", "e")

    from splitsmith.ui.server import create_app

    with TestClient(create_app()):
        pass  # lifespan runs the seeding

    rows = _with_store(hosted_env, lambda s: s.list())
    assert [w.kind for w in rows] == ["railway"]
    assert rows[0].enabled is True


def test_hosted_boot_does_not_seed_railway_row_without_env(
    hosted_env: str,
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    assert _with_store(hosted_env, lambda s: s.list()) == []
