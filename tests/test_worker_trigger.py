"""Unit tests for the worker dispatcher - no network, httpx.MockTransport only.

Self-hosted legs run against a real WakeChannelRegistry and an in-memory
SQLite WorkersStore (same harness as test_workers_store.py); the Railway
leg is mocked at the httpx transport.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from splitsmith.db import Base, create_engine, sessionmaker
from splitsmith.db.workers import WorkersStore
from splitsmith.worker_channel import WakeChannelRegistry
from splitsmith.worker_trigger import (
    ENV_RAILWAY_ENVIRONMENT_ID,
    ENV_TRIGGER_TOKEN,
    ENV_WORKER_ENVIRONMENT_ID,
    ENV_WORKER_LAUNCHER,
    ENV_WORKER_SERVICE_ID,
    RailwayLauncherConfig,
    WorkerDispatcher,
    build_worker_dispatcher,
    load_railway_config,
    make_boot_retrigger,
    make_pending_jobs_counter,
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
    """Token without a service id (or vice versa) must not half-enable the leg."""
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


# ---------------------------------------------------------------------------
# Dispatcher harness
# ---------------------------------------------------------------------------


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


def _pending(n: int) -> Any:
    async def _count() -> int:
        return n

    return _count


def _fresh_store() -> WorkersStore:
    """In-memory SQLite engine + WorkersStore (same as test_workers_store.py)."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    return WorkersStore(session_factory)


async def _add_home(
    store: WorkersStore, name: str = "home", *, priority: int = 10, enabled: bool = True
) -> str:
    """Create + register a self-hosted worker; returns its id."""
    record, token = await store.create_self_hosted(name, priority=priority)
    registered = await store.register(token, {"hostname": name})
    assert registered is not None
    if not enabled:
        await store.update(record.id, enabled=False)
    return record.id


async def _railway_row_id(store: WorkersStore) -> str:
    await store.ensure_railway_row()
    rows = [w for w in await store.list() if w.kind == "railway"]
    assert len(rows) == 1
    return rows[0].id


def _dispatcher(
    store: WorkersStore,
    registry: WakeChannelRegistry,
    *,
    railway: RailwayLauncherConfig | None,
    requests: list[dict[str, Any]] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    worker_active: Any = None,
    pending_jobs: Any = None,
    # Short grace so tests that drain the dispatcher's tasks never sit out
    # the production 90s window (pending_jobs defaults to 0, so the drained
    # grace task no-ops).
    grace_seconds: float = 0.01,
) -> WorkerDispatcher:
    if transport is None and requests is not None:
        transport = _transport(requests)
    return WorkerDispatcher(
        store,
        registry,
        railway=railway,
        worker_active=worker_active,
        pending_jobs=pending_jobs if pending_jobs is not None else _pending(0),
        grace_seconds=grace_seconds,
        transport=transport,
    )


async def _drain_tasks(dispatcher: WorkerDispatcher) -> None:
    while dispatcher._tasks:
        await asyncio.gather(*list(dispatcher._tasks))


# ---------------------------------------------------------------------------
# Tier walk
# ---------------------------------------------------------------------------


def test_dispatcher_wakes_connected_home_worker_not_railway() -> None:
    """A connected self-hosted worker at a lower priority absorbs the wake;
    the Railway leg is never touched."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> tuple[bool, int, Any]:
        home_id = await _add_home(store)
        await _railway_row_id(store)
        registry = WakeChannelRegistry()
        queue = registry.connect(home_id)
        dispatcher = _dispatcher(store, registry, railway=_config(), requests=requests)
        fired = await dispatcher.trigger()
        woken = await store.get(home_id)
        assert woken is not None
        return fired, queue.qsize(), woken.last_wake_at

    fired, qsize, last_wake_at = asyncio.run(scenario())
    assert fired is True
    assert qsize == 1
    assert last_wake_at is not None
    assert requests == []


def test_dispatcher_falls_through_to_railway_when_home_disconnected() -> None:
    """A registered-but-disconnected home worker cannot absorb the wake;
    the tier falls through to the Railway tier immediately."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> tuple[bool, Any]:
        await _add_home(store)
        railway_id = await _railway_row_id(store)
        dispatcher = _dispatcher(store, WakeChannelRegistry(), railway=_config(), requests=requests)
        fired = await dispatcher.trigger()
        row = await store.get(railway_id)
        assert row is not None
        return fired, row.last_wake_at

    fired, last_wake_at = asyncio.run(scenario())
    assert fired is True
    assert len(requests) == 1
    assert "serviceInstanceRedeploy" in requests[0]["query"]
    assert requests[0]["variables"] == {"serviceId": "svc", "environmentId": "env"}
    assert last_wake_at is not None


def test_dispatcher_disabled_railway_row_never_called() -> None:
    """The operator disabling the railway row wins over the env config."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> bool:
        railway_id = await _railway_row_id(store)
        await store.update(railway_id, enabled=False)
        dispatcher = _dispatcher(store, WakeChannelRegistry(), railway=_config(), requests=requests)
        return await dispatcher.trigger()

    assert asyncio.run(scenario()) is False
    assert requests == []


def test_dispatcher_returns_false_when_nothing_wakeable() -> None:
    """No railway config + no connected homes: nothing woken, trigger False."""
    store = _fresh_store()

    async def scenario() -> tuple[bool, Any]:
        home_id = await _add_home(store)
        await _railway_row_id(store)  # row exists but railway=None (no env config)
        dispatcher = _dispatcher(store, WakeChannelRegistry(), railway=None)
        fired = await dispatcher.trigger()
        row = await store.get(home_id)
        assert row is not None
        return fired, row.last_wake_at

    fired, last_wake_at = asyncio.run(scenario())
    assert fired is False
    assert last_wake_at is None


def test_dispatcher_swallows_transport_errors() -> None:
    """A Railway outage must never fail (or slow) the enqueue path."""
    store = _fresh_store()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("railway down", request=request)

    async def scenario() -> bool:
        await _railway_row_id(store)
        dispatcher = _dispatcher(
            store, WakeChannelRegistry(), railway=_config(), transport=httpx.MockTransport(handler)
        )
        return await dispatcher.trigger()

    assert asyncio.run(scenario()) is False


def test_dispatcher_treats_graphql_errors_as_failure() -> None:
    store = _fresh_store()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Unauthorized"}]})

    async def scenario() -> bool:
        await _railway_row_id(store)
        dispatcher = _dispatcher(
            store, WakeChannelRegistry(), railway=_config(), transport=httpx.MockTransport(handler)
        )
        return await dispatcher.trigger()

    assert asyncio.run(scenario()) is False


# ---------------------------------------------------------------------------
# Cooldown + heartbeat gate (contracts inherited from the old launcher)
# ---------------------------------------------------------------------------


def test_dispatcher_cooldown_suppresses_burst() -> None:
    store = _fresh_store()

    async def scenario() -> tuple[bool, bool, int]:
        home_id = await _add_home(store)
        registry = WakeChannelRegistry()
        queue = registry.connect(home_id)
        dispatcher = _dispatcher(store, registry, railway=None)
        first = await dispatcher.trigger()
        second = await dispatcher.trigger()
        return first, second, queue.qsize()

    first, second, qsize = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert qsize == 1  # the second call never reached the tier walk


def test_dispatcher_skips_while_worker_active() -> None:
    """A live heartbeat means a drain is in flight; waking anything now is
    pointless (self-hosted) or dangerous (Railway redeploy kills the drain)."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> tuple[bool, int]:
        home_id = await _add_home(store)
        await _railway_row_id(store)
        registry = WakeChannelRegistry()
        queue = registry.connect(home_id)
        dispatcher = _dispatcher(store, registry, railway=_config(), requests=requests, worker_active=_active)
        fired = await dispatcher.trigger()
        return fired, queue.qsize()

    fired, qsize = asyncio.run(scenario())
    assert fired is False
    assert qsize == 0
    assert requests == []


def test_dispatcher_fail_safe_when_checker_raises() -> None:
    """If the heartbeat check itself fails, do NOT wake anything - a possibly
    running drain must never be killed on bad information. The boot
    re-check / safety cron recover a missed launch."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def _broken() -> bool:
        raise RuntimeError("db unavailable")

    async def scenario() -> bool:
        await _railway_row_id(store)
        dispatcher = _dispatcher(
            store, WakeChannelRegistry(), railway=_config(), requests=requests, worker_active=_broken
        )
        return await dispatcher.trigger()

    assert asyncio.run(scenario()) is False
    assert requests == []


# ---------------------------------------------------------------------------
# Grace-window escalation
# ---------------------------------------------------------------------------


def test_grace_escalation_wakes_railway_when_home_stays_idle() -> None:
    """Home worker takes the wake but never starts draining: after
    grace_seconds with jobs still pending, escalate past its tier."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> bool:
        home_id = await _add_home(store)
        await _railway_row_id(store)
        registry = WakeChannelRegistry()
        registry.connect(home_id)
        dispatcher = _dispatcher(
            store,
            registry,
            railway=_config(),
            requests=requests,
            worker_active=_inactive,
            pending_jobs=_pending(1),
            grace_seconds=0.01,
        )
        fired = await dispatcher.trigger()
        assert requests == []  # home absorbed the initial wake
        await _drain_tasks(dispatcher)
        return fired

    assert asyncio.run(scenario()) is True
    assert len(requests) == 1  # escalation fired the Railway leg


def test_grace_escalation_skipped_when_worker_became_active() -> None:
    """The home worker picked the job up within the grace window: no escalation."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []
    calls = {"n": 0}

    async def _becomes_active() -> bool:
        # False for the trigger's heartbeat gate, True by the time the
        # grace task re-checks.
        calls["n"] += 1
        return calls["n"] > 1

    async def scenario() -> bool:
        home_id = await _add_home(store)
        await _railway_row_id(store)
        registry = WakeChannelRegistry()
        registry.connect(home_id)
        dispatcher = _dispatcher(
            store,
            registry,
            railway=_config(),
            requests=requests,
            worker_active=_becomes_active,
            pending_jobs=_pending(1),
            grace_seconds=0.01,
        )
        fired = await dispatcher.trigger()
        await _drain_tasks(dispatcher)
        return fired

    assert asyncio.run(scenario()) is True
    assert requests == []


def test_grace_escalation_skipped_when_queue_drained() -> None:
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> None:
        home_id = await _add_home(store)
        await _railway_row_id(store)
        registry = WakeChannelRegistry()
        registry.connect(home_id)
        dispatcher = _dispatcher(
            store,
            registry,
            railway=_config(),
            requests=requests,
            worker_active=_inactive,
            pending_jobs=_pending(0),
            grace_seconds=0.01,
        )
        await dispatcher.trigger()
        await _drain_tasks(dispatcher)

    asyncio.run(scenario())
    assert requests == []


def test_grace_escalations_racing_fire_exactly_one_redeploy() -> None:
    """Two overlapping grace windows must not double-boot Railway: the first
    escalation moves the attempt stamp under the lock; the second sees the
    stamp changed and stands down."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> None:
        await _railway_row_id(store)
        dispatcher = _dispatcher(
            store,
            WakeChannelRegistry(),
            railway=_config(),
            requests=requests,
            worker_active=_inactive,
            pending_jobs=_pending(1),
        )
        await asyncio.gather(
            dispatcher._grace_escalate(10, armed_stamp=None),
            dispatcher._grace_escalate(10, armed_stamp=None),
        )

    asyncio.run(scenario())
    assert len(requests) == 1


def test_grace_escalation_stands_down_after_newer_wake_attempt() -> None:
    """A grace window armed by an older trigger must not escalate once a
    newer wake attempt has moved the stamp - the newer attempt's own grace
    window (or the nets) covers it."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> None:
        await _railway_row_id(store)
        dispatcher = _dispatcher(
            store,
            WakeChannelRegistry(),
            railway=_config(),
            requests=requests,
            worker_active=_inactive,
            pending_jobs=_pending(1),
        )
        dispatcher._last_attempt = 12345.0  # a newer attempt moved the stamp
        await dispatcher._grace_escalate(10, armed_stamp=1.0)

    asyncio.run(scenario())
    assert requests == []


def test_touch_wake_failure_does_not_cancel_wake_or_grace() -> None:
    """A DB hiccup on the last_wake_at bookkeeping must not flip a delivered
    wake to False or skip arming the grace timer."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> bool:
        home_id = await _add_home(store)
        await _railway_row_id(store)
        registry = WakeChannelRegistry()
        queue = registry.connect(home_id)

        async def _broken_touch_wake(worker_ids: list[str]) -> None:
            raise RuntimeError("db gone")

        store.touch_wake = _broken_touch_wake  # type: ignore[method-assign]
        dispatcher = _dispatcher(
            store,
            registry,
            railway=_config(),
            requests=requests,
            worker_active=_inactive,
            pending_jobs=_pending(1),
            grace_seconds=0.01,
        )
        fired = await dispatcher.trigger()
        assert queue.qsize() == 1  # the wake itself was delivered
        await _drain_tasks(dispatcher)
        return fired

    assert asyncio.run(scenario()) is True
    assert len(requests) == 1  # grace escalation still armed and fired railway


def test_grace_timer_not_armed_for_railway_only_wake() -> None:
    """A railway-only wake has no faster net than the existing ones; nothing
    to escalate to, so no second redeploy after the grace window."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> None:
        await _railway_row_id(store)
        dispatcher = _dispatcher(
            store,
            WakeChannelRegistry(),
            railway=_config(),
            requests=requests,
            worker_active=_inactive,
            pending_jobs=_pending(1),
            grace_seconds=0.01,
        )
        await dispatcher.trigger()
        await _drain_tasks(dispatcher)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert len(requests) == 1  # only the initial wake


# ---------------------------------------------------------------------------
# schedule()
# ---------------------------------------------------------------------------


def test_schedule_fires_trigger_in_background() -> None:
    store = _fresh_store()

    async def scenario() -> int:
        home_id = await _add_home(store)
        registry = WakeChannelRegistry()
        queue = registry.connect(home_id)
        dispatcher = _dispatcher(store, registry, railway=None)
        dispatcher.schedule()
        await _drain_tasks(dispatcher)
        return queue.qsize()

    assert asyncio.run(scenario()) == 1


def test_schedule_never_raises_outside_event_loop() -> None:
    """schedule() must not raise even when called outside a running loop.

    It is called from the enqueue path (a raise would 500 a committed job)
    and from the serve-boot lifespan. Outside a loop asyncio.get_running_loop()
    raises RuntimeError; the try/except guard must absorb it.
    """
    store = _fresh_store()
    dispatcher = _dispatcher(store, WakeChannelRegistry(), railway=None)
    dispatcher.schedule()  # must not raise


# ---------------------------------------------------------------------------
# Stub-session plumbing (worker_active checker + pending-jobs counter)
# ---------------------------------------------------------------------------


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


def test_make_pending_jobs_counter_counts_todo_rows() -> None:
    session = _StubSession(2)
    counter = make_pending_jobs_counter(lambda: _StubSessionCtx(session))
    assert asyncio.run(counter()) == 2
    assert "procrastinate_jobs" in session.queries[0]
    assert "todo" in session.queries[0]


# ---------------------------------------------------------------------------
# build_worker_dispatcher
# ---------------------------------------------------------------------------


def test_build_dispatcher_default_has_railway_leg(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_TRIGGER_TOKEN, "tok")
    monkeypatch.setenv(ENV_WORKER_SERVICE_ID, "svc")
    monkeypatch.setenv(ENV_WORKER_ENVIRONMENT_ID, "env")
    dispatcher = build_worker_dispatcher(
        _fresh_store(), WakeChannelRegistry(), worker_active=None, pending_jobs=_pending(0)
    )
    assert isinstance(dispatcher, WorkerDispatcher)
    assert dispatcher._railway is not None


def test_build_dispatcher_without_railway_env_still_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Railway env no longer disables the wake path: self-hosted
    workers can still be woken; only the railway leg is absent."""
    _clear_env(monkeypatch)
    dispatcher = build_worker_dispatcher(
        _fresh_store(), WakeChannelRegistry(), worker_active=None, pending_jobs=_pending(0)
    )
    assert isinstance(dispatcher, WorkerDispatcher)
    assert dispatcher._railway is None


def test_build_dispatcher_none_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_WORKER_LAUNCHER, "none")
    assert (
        build_worker_dispatcher(
            _fresh_store(), WakeChannelRegistry(), worker_active=None, pending_jobs=_pending(0)
        )
        is None
    )


def test_build_dispatcher_unknown_kind_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in SPLITSMITH_WORKER_LAUNCHER must fail the boot, not silently
    disable job execution."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_WORKER_LAUNCHER, "sqs")
    with pytest.raises(ValueError, match="unknown worker launcher"):
        build_worker_dispatcher(
            _fresh_store(), WakeChannelRegistry(), worker_active=None, pending_jobs=_pending(0)
        )


# ---------------------------------------------------------------------------
# wrap_deferrer + boot re-trigger (unchanged public seams)
# ---------------------------------------------------------------------------


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


def test_boot_retrigger_cooldown_skips_second_immediate_call() -> None:
    """The second call within min_interval_seconds must not hit the DB."""
    stub = _StubLauncher()
    session = _StubSession(1)
    hook = make_boot_retrigger(lambda: _StubSessionCtx(session), stub, min_interval_seconds=300.0)
    asyncio.run(hook())  # first call - runs
    assert stub.scheduled == 1
    asyncio.run(hook())  # second call within interval - skipped
    assert stub.scheduled == 1  # no new schedule
    assert len(session.queries) == 1  # second call did not query the DB


def test_boot_retrigger_cooldown_zero_allows_every_call() -> None:
    """With min_interval_seconds=0.0, consecutive calls both query the DB."""
    stub = _StubLauncher()
    session = _StubSession(1)
    hook = make_boot_retrigger(lambda: _StubSessionCtx(session), stub, min_interval_seconds=0.0)
    asyncio.run(hook())
    asyncio.run(hook())
    assert stub.scheduled == 2
    assert len(session.queries) == 2
