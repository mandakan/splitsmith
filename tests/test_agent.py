"""Unit tests for the self-hosted agent runtime (``splitsmith.agent``).

All network is faked with ``httpx.MockTransport``; ``run_worker`` is replaced
by a recording stub. The SSE parser, the wake coordinator, the reader loop, and
the drain loop are each exercised in isolation so no test depends on a live
server or a real Postgres, and nothing hangs (injected no-op sleeps + explicit
connection caps).
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import httpx
import pytest

from splitsmith import agent
from splitsmith.agent import (
    AgentState,
    _drain_loop,
    _reader_loop,
    _WakeCoordinator,
    apply_credentials,
    parse_sse_events,
    register,
)


async def _no_sleep(_seconds: float) -> None:
    return None


def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the test a private copy of ``os.environ`` that monkeypatch restores."""
    monkeypatch.setattr(os, "environ", os.environ.copy())


# ---------------------------------------------------------------------------
# register + AgentState persistence
# ---------------------------------------------------------------------------


def test_register_writes_agent_json_0600_and_roundtrips(tmp_path: Path) -> None:
    creds = {"database_url": "postgresql://db", "public_url": "http://srv", "s3": None}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/workers/register"
        body = __import__("json").loads(request.content)
        assert body["token"] == "reg-token"
        assert set(body["info"]) == {"agent_version", "hostname", "concurrency"}
        assert body["info"]["concurrency"] == 3
        return httpx.Response(
            200,
            json={"worker_id": "w1", "worker_token": "wtok", "credentials": creds},
        )

    transport = httpx.MockTransport(handler)
    state = register("http://srv", "reg-token", tmp_path, concurrency=3, transport=transport)

    assert state.server_url == "http://srv"
    assert state.worker_id == "w1"
    assert state.worker_token == "wtok"
    assert state.credentials == creds

    path = tmp_path / "agent.json"
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    loaded = AgentState.load(tmp_path)
    assert loaded is not None
    assert loaded.model_dump() == state.model_dump()


def test_register_non_2xx_raises(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"detail": "not found"}))
    with pytest.raises(RuntimeError):
        register("http://srv", "bad", tmp_path, transport=transport)
    assert not (tmp_path / "agent.json").exists()


def test_agentstate_load_missing_returns_none(tmp_path: Path) -> None:
    assert AgentState.load(tmp_path) is None


# ---------------------------------------------------------------------------
# apply_credentials
# ---------------------------------------------------------------------------


def test_apply_credentials_no_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_env(monkeypatch)
    state = AgentState(
        server_url="http://srv",
        worker_id="w1",
        worker_token="t",
        credentials={"database_url": "postgresql://db", "public_url": "http://srv", "s3": None},
    )
    apply_credentials(state)

    assert os.environ["SPLITSMITH_MODE"] == "hosted"
    assert os.environ["SPLITSMITH_DATABASE_URL"] == "postgresql://db"
    assert os.environ["SPLITSMITH_PUBLIC_URL"] == "http://srv"
    assert os.environ["SPLITSMITH_EMAIL_BACKEND"] == "console"
    assert "SPLITSMITH_S3_BUCKET" not in os.environ


def test_apply_credentials_respects_existing_email_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_env(monkeypatch)
    os.environ["SPLITSMITH_EMAIL_BACKEND"] = "lettermint"
    state = AgentState(
        server_url="http://srv",
        worker_id="w1",
        worker_token="t",
        credentials={"database_url": "postgresql://db", "public_url": "http://srv", "s3": None},
    )
    apply_credentials(state)
    assert os.environ["SPLITSMITH_EMAIL_BACKEND"] == "lettermint"


def test_apply_credentials_with_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_env(monkeypatch)
    state = AgentState(
        server_url="http://srv",
        worker_id="w1",
        worker_token="t",
        credentials={
            "database_url": "postgresql://db",
            "public_url": "http://srv",
            "s3": {
                "bucket": "splits",
                "endpoint_url": "http://minio:9000",
                "region": "auto",
                "access_key_id": "ak",
                "secret_access_key": "sk",
            },
        },
    )
    apply_credentials(state)
    assert os.environ["SPLITSMITH_S3_BUCKET"] == "splits"
    assert os.environ["SPLITSMITH_S3_ENDPOINT_URL"] == "http://minio:9000"
    assert os.environ["SPLITSMITH_S3_REGION"] == "auto"
    assert os.environ["SPLITSMITH_S3_ACCESS_KEY_ID"] == "ak"
    assert os.environ["SPLITSMITH_S3_SECRET_ACCESS_KEY"] == "sk"


def test_apply_credentials_with_s3_null_endpoint_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_env(monkeypatch)
    state = AgentState(
        server_url="http://srv",
        worker_id="w1",
        worker_token="t",
        credentials={
            "database_url": "postgresql://db",
            "public_url": "http://srv",
            "s3": {
                "bucket": "splits",
                "endpoint_url": None,
                "region": "auto",
                "access_key_id": "ak",
                "secret_access_key": "sk",
            },
        },
    )
    apply_credentials(state)
    assert os.environ["SPLITSMITH_S3_BUCKET"] == "splits"
    assert os.environ["SPLITSMITH_S3_ENDPOINT_URL"] == ""
    assert os.environ["SPLITSMITH_S3_REGION"] == "auto"
    assert os.environ["SPLITSMITH_S3_ACCESS_KEY_ID"] == "ak"
    assert os.environ["SPLITSMITH_S3_SECRET_ACCESS_KEY"] == "sk"


# ---------------------------------------------------------------------------
# parse_sse_events (pure)
# ---------------------------------------------------------------------------


def test_parse_sse_keepalive_then_wake() -> None:
    chunks = [b": ka\n\n", b"event: wake\ndata: {}\n\n"]
    assert list(parse_sse_events(chunks)) == ["wake"]


def test_parse_sse_multiple_events_in_one_buffer() -> None:
    chunks = [b"event: wake\ndata: {}\n\nevent: disabled\ndata: {}\n\n"]
    assert list(parse_sse_events(chunks)) == ["wake", "disabled"]


def test_parse_sse_event_split_across_chunks() -> None:
    chunks = [b"event: wa", b"ke\ndata: {}\n", b"\n"]
    assert list(parse_sse_events(chunks)) == ["wake"]


def test_parse_sse_ignores_comments_and_data_only() -> None:
    chunks = [b": ka\n\n", b": ka\n\n", b"data: {}\n\n"]
    assert list(parse_sse_events(chunks)) == []


# ---------------------------------------------------------------------------
# _WakeCoordinator dispatch semantics
# ---------------------------------------------------------------------------


def test_wake_sets_event() -> None:
    async def scenario() -> bool:
        c = _WakeCoordinator()
        c.dispatch("wake")
        return c.wake_event.is_set()

    assert asyncio.run(scenario()) is True


def test_disabled_suppresses_wake_then_enabled_catches_up() -> None:
    async def scenario() -> tuple[bool, bool]:
        c = _WakeCoordinator()
        c.dispatch("disabled")
        c.dispatch("wake")
        suppressed = not c.wake_event.is_set()
        c.dispatch("enabled")
        caught_up = c.wake_event.is_set()
        return suppressed, caught_up

    suppressed, caught_up = asyncio.run(scenario())
    assert suppressed is True
    assert caught_up is True


def test_replaced_signals_reconnect() -> None:
    async def scenario() -> str | None:
        return _WakeCoordinator().dispatch("replaced")

    assert asyncio.run(scenario()) == "reconnect"


# ---------------------------------------------------------------------------
# _drain_loop: never lost, never concurrent
# ---------------------------------------------------------------------------


def test_drain_loop_wake_during_drain_triggers_exactly_one_followup() -> None:
    async def scenario() -> int:
        wake = asyncio.Event()
        stop = asyncio.Event()
        release = asyncio.Event()
        started = asyncio.Event()
        count = 0

        async def stub(_db: str, _conc: int) -> None:
            nonlocal count
            count += 1
            started.set()
            await release.wait()
            release.clear()

        task = asyncio.create_task(_drain_loop(wake, stop, "db", 1, run=stub))

        wake.set()  # first drain
        await asyncio.wait_for(started.wait(), 1)
        started.clear()
        assert count == 1

        wake.set()  # a wake mid-drain must collapse into exactly one follow-up
        wake.set()  # a second concurrent wake must not add a third drain
        release.set()  # let the first drain finish

        await asyncio.wait_for(started.wait(), 1)  # follow-up drain runs
        started.clear()
        assert count == 2

        release.set()  # let the follow-up finish
        await asyncio.sleep(0.02)  # give the loop a chance to (wrongly) drain again

        stop.set()
        wake.set()
        release.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return count

    assert asyncio.run(scenario()) == 2


# ---------------------------------------------------------------------------
# _reader_loop over MockTransport
# ---------------------------------------------------------------------------


def test_reader_loop_404_raises_system_exit_3() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"detail": "not found"}))

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            await _reader_loop(
                client, "http://srv", "tok", _WakeCoordinator(), asyncio.Event(), sleep=_no_sleep
            )

    with pytest.raises(SystemExit) as excinfo:
        asyncio.run(scenario())
    assert excinfo.value.code == 3


def test_reader_loop_dispatches_wake_from_stream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b": ka\n\nevent: wake\ndata: {}\n\n",
        )

    transport = httpx.MockTransport(handler)

    async def scenario() -> bool:
        coord = _WakeCoordinator()
        async with httpx.AsyncClient(transport=transport) as client:
            await _reader_loop(
                client, "http://srv", "tok", coord, asyncio.Event(), sleep=_no_sleep, max_connections=1
            )
        return coord.wake_event.is_set()

    assert asyncio.run(scenario()) is True


# ---------------------------------------------------------------------------
# run_agent bootstrap wiring
# ---------------------------------------------------------------------------


def test_run_agent_without_state_or_token_raises(tmp_path: Path) -> None:
    async def scenario() -> None:
        await run_agent_call(tmp_path)

    async def run_agent_call(state_dir: Path) -> None:
        await agent.run_agent("http://srv", registration_token=None, state_dir=state_dir)

    with pytest.raises(RuntimeError):
        asyncio.run(scenario())


def test_run_agent_registers_then_exits_on_channel_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_env(monkeypatch)
    creds = {"database_url": "postgresql://db", "public_url": "http://srv", "s3": None}
    drained: list[tuple[str, int]] = []

    async def never_drain(db: str, conc: int) -> None:  # pragma: no cover - must not run
        drained.append((db, conc))

    monkeypatch.setattr(agent, "_run_worker_once", never_drain)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/workers/register":
            return httpx.Response(200, json={"worker_id": "w1", "worker_token": "wtok", "credentials": creds})
        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)

    async def scenario() -> None:
        await agent.run_agent("http://srv", registration_token="reg", state_dir=tmp_path, transport=transport)

    with pytest.raises(SystemExit) as excinfo:
        asyncio.run(scenario())
    assert excinfo.value.code == 3
    # register persisted state and apply_credentials ran before the channel opened.
    assert (tmp_path / "agent.json").exists()
    assert os.environ["SPLITSMITH_MODE"] == "hosted"
    assert drained == []
