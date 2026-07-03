"""Unit tests for the worker launcher seam - no network, httpx.MockTransport only."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from splitsmith.worker_trigger import (
    ENV_RAILWAY_ENVIRONMENT_ID,
    ENV_TRIGGER_TOKEN,
    ENV_WORKER_ENVIRONMENT_ID,
    ENV_WORKER_LAUNCHER,
    ENV_WORKER_SERVICE_ID,
    RailwayLauncherConfig,
    RailwayWorkerLauncher,
    build_worker_launcher,
    load_railway_config,
    make_boot_retrigger,
    make_worker_active_checker,
    wrap_deferrer,
)

_ALL_ENV_VARS = (
    ENV_WORKER_LAUNCHER,
    ENV_TRIGGER_TOKEN,
    ENV_WORKER_SERVICE_ID,
    ENV_WORKER_ENVIRONMENT_ID,
    ENV_RAILWAY_ENVIRONMENT_ID,
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_load_railway_config_disabled_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    assert load_railway_config() is None


def test_load_railway_config_disabled_when_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Token without a service id (or vice versa) must not half-enable the launcher."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    assert load_railway_config() is None


def test_load_railway_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    monkeypatch.setenv(ENV_WORKER_SERVICE_ID, "svc-id")
    monkeypatch.setenv(ENV_WORKER_ENVIRONMENT_ID, "env-id")
    config = load_railway_config()
    assert config is not None
    assert (config.token, config.service_id, config.environment_id) == ("tok", "svc-id", "env-id")


def test_load_railway_config_falls_back_to_railway_env_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Railway injects RAILWAY_ENVIRONMENT_ID into every container; the
    SPLITSMITH_ override exists only for running outside Railway."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    monkeypatch.setenv(ENV_WORKER_SERVICE_ID, "svc-id")
    monkeypatch.setenv(ENV_RAILWAY_ENVIRONMENT_ID, "railway-env-id")
    config = load_railway_config()
    assert config is not None
    assert config.environment_id == "railway-env-id"


def _config(**overrides: Any) -> RailwayLauncherConfig:
    base: dict[str, Any] = {"token": "tok", "service_id": "svc", "environment_id": "env"}
    base.update(overrides)
    return RailwayLauncherConfig(**base)


def _transport(requests: list[dict[str, Any]]) -> httpx.MockTransport:
    """Ack the redeploy mutation, recording every GraphQL request body."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"data": {"serviceInstanceRedeploy": True}})

    return httpx.MockTransport(handler)


async def _active() -> bool:
    return True


async def _inactive() -> bool:
    return False


def test_launcher_redeploys_when_no_checker_configured() -> None:
    requests: list[dict[str, Any]] = []
    launcher = RailwayWorkerLauncher(_config(), transport=_transport(requests))
    assert asyncio.run(launcher.trigger()) is True
    assert len(requests) == 1
    assert "serviceInstanceRedeploy" in requests[0]["query"]
    assert requests[0]["variables"] == {"serviceId": "svc", "environmentId": "env"}


def test_launcher_redeploys_when_worker_inactive() -> None:
    requests: list[dict[str, Any]] = []
    launcher = RailwayWorkerLauncher(_config(), worker_active=_inactive, transport=_transport(requests))
    assert asyncio.run(launcher.trigger()) is True
    assert len(requests) == 1


def test_launcher_skips_while_worker_active() -> None:
    """A live heartbeat means a drain is in flight; redeploying now would
    kill its job mid-run. The running drain picks up new jobs itself."""
    requests: list[dict[str, Any]] = []
    launcher = RailwayWorkerLauncher(_config(), worker_active=_active, transport=_transport(requests))
    assert asyncio.run(launcher.trigger()) is False
    assert requests == []


def test_launcher_fail_safe_when_checker_raises() -> None:
    """If the heartbeat check itself fails, do NOT redeploy - a possibly
    running drain must never be killed on bad information. The boot
    re-check / safety cron recover a missed launch."""
    requests: list[dict[str, Any]] = []

    async def _broken() -> bool:
        raise RuntimeError("db unavailable")

    launcher = RailwayWorkerLauncher(_config(), worker_active=_broken, transport=_transport(requests))
    assert asyncio.run(launcher.trigger()) is False
    assert requests == []


def test_launcher_cooldown_suppresses_burst() -> None:
    requests: list[dict[str, Any]] = []

    async def scenario() -> tuple[bool, bool]:
        launcher = RailwayWorkerLauncher(_config(), transport=_transport(requests))
        return await launcher.trigger(), await launcher.trigger()

    first, second = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert len(requests) == 1  # the second call never reached the API


def test_launcher_swallows_transport_errors() -> None:
    """A Railway outage must never fail (or slow) the enqueue path."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("railway down", request=request)

    launcher = RailwayWorkerLauncher(_config(), transport=httpx.MockTransport(handler))
    assert asyncio.run(launcher.trigger()) is False


def test_launcher_treats_graphql_errors_as_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Unauthorized"}]})

    launcher = RailwayWorkerLauncher(_config(), transport=httpx.MockTransport(handler))
    assert asyncio.run(launcher.trigger()) is False


def test_schedule_fires_trigger_in_background() -> None:
    requests: list[dict[str, Any]] = []

    async def scenario() -> None:
        launcher = RailwayWorkerLauncher(_config(), transport=_transport(requests))
        launcher.schedule()
        await asyncio.gather(*list(launcher._tasks))

    asyncio.run(scenario())
    assert len(requests) == 1


class _StubResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value


class _StubSession:
    def __init__(self, value: Any) -> None:
        self._value = value
        self.queries: list[str] = []

    async def execute(self, query: Any, params: Any = None) -> _StubResult:
        self.queries.append(str(query))
        return _StubResult(self._value)


class _StubSessionCtx:
    def __init__(self, session: _StubSession) -> None:
        self._session = session

    async def __aenter__(self) -> _StubSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> None:
        return None


def test_worker_active_checker_true_on_fresh_heartbeat() -> None:
    session = _StubSession(True)
    checker = make_worker_active_checker(lambda: _StubSessionCtx(session))
    assert asyncio.run(checker()) is True
    assert "procrastinate_workers" in session.queries[0]


def test_worker_active_checker_false_when_no_live_worker() -> None:
    session = _StubSession(False)
    checker = make_worker_active_checker(lambda: _StubSessionCtx(session))
    assert asyncio.run(checker()) is False


def test_build_worker_launcher_defaults_to_railway(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    monkeypatch.setenv(ENV_WORKER_SERVICE_ID, "svc")
    monkeypatch.setenv(ENV_WORKER_ENVIRONMENT_ID, "env")
    launcher = build_worker_launcher()
    assert isinstance(launcher, RailwayWorkerLauncher)


def test_build_worker_launcher_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    assert build_worker_launcher() is None


def test_build_worker_launcher_unknown_kind_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in SPLITSMITH_WORKER_LAUNCHER must fail the boot, not silently
    disable job execution."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_WORKER_LAUNCHER, "sqs")
    with pytest.raises(ValueError, match="unknown worker launcher"):
        build_worker_launcher()


class _StubLauncher:
    """Minimal WorkerLauncher for wiring tests (the Protocol is structural)."""

    def __init__(self) -> None:
        self.scheduled = 0

    def schedule(self) -> None:
        self.scheduled += 1

    async def trigger(self) -> bool:
        self.schedule()
        return True


def test_wrap_deferrer_defers_then_schedules() -> None:
    calls: list[dict[str, Any]] = []

    async def deferrer(**kwargs: Any) -> None:
        calls.append(kwargs)

    stub = _StubLauncher()
    wrapped = wrap_deferrer(deferrer, stub)
    asyncio.run(wrapped(job_id="j1", user_id="u1", kind="detect_beep", args={}, match_id=None))
    assert calls == [{"job_id": "j1", "user_id": "u1", "kind": "detect_beep", "args": {}, "match_id": None}]
    assert stub.scheduled == 1


def test_wrap_deferrer_failed_defer_does_not_schedule() -> None:
    """If the enqueue itself failed there is no job to run - and the caller
    must see the original exception, not a swallowed one."""

    async def deferrer(**kwargs: Any) -> None:
        raise RuntimeError("boom")

    stub = _StubLauncher()
    wrapped = wrap_deferrer(deferrer, stub)
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(wrapped(job_id="j1", user_id="u1", kind="detect_beep", args={}, match_id=None))
    assert stub.scheduled == 0


def test_boot_retrigger_fires_when_jobs_pending() -> None:
    stub = _StubLauncher()
    session = _StubSession(2)
    hook = make_boot_retrigger(lambda: _StubSessionCtx(session), stub)
    asyncio.run(hook())
    assert stub.scheduled == 1
    assert "procrastinate_jobs" in session.queries[0]


def test_boot_retrigger_quiet_when_queue_empty() -> None:
    stub = _StubLauncher()
    hook = make_boot_retrigger(lambda: _StubSessionCtx(_StubSession(0)), stub)
    asyncio.run(hook())
    assert stub.scheduled == 0


def test_boot_retrigger_swallows_db_errors() -> None:
    """A failed pending-check must not fail the serve boot."""

    class _BrokenCtx:
        async def __aenter__(self) -> Any:
            raise RuntimeError("db down")

        async def __aexit__(self, *exc: Any) -> None:
            return None

    stub = _StubLauncher()
    hook = make_boot_retrigger(lambda: _BrokenCtx(), stub)
    asyncio.run(hook())  # must not raise
    assert stub.scheduled == 0
