"""Unit tests for the worker dispatcher - no network, httpx.MockTransport only.

Self-hosted legs run against a real WakeChannelRegistry and an in-memory
SQLite WorkersStore (same harness as test_workers_store.py); the Cloud Run
leg is mocked at the httpx transport.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from splitsmith.db import Base, create_engine, sessionmaker
from splitsmith.db.workers import WorkersStore
from splitsmith.worker_channel import WakeChannelRegistry
from splitsmith.worker_trigger import (
    ENV_WAKE_CHANNEL,
    ENV_WORKER_JOB,
    ENV_WORKER_LAUNCHER,
    GCP_METADATA_TOKEN_URL,
    CloudRunJobConfig,
    WorkerDispatcher,
    build_worker_dispatcher,
    make_boot_retrigger,
    make_pending_jobs_counter,
    make_worker_active_checker,
    wrap_deferrer,
)

_ALL_ENV_VARS = (ENV_WORKER_LAUNCHER, ENV_WORKER_JOB, ENV_WAKE_CHANNEL)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


JOB = "projects/p/locations/europe-west1/jobs/splitsmith-worker-test"


def _config() -> CloudRunJobConfig:
    return CloudRunJobConfig(job=JOB)


def _transport(requests: list[dict[str, Any]]) -> httpx.MockTransport:
    """Hand out a metadata-server token and ack jobs.run, recording each job start."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == GCP_METADATA_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "sa-token", "expires_in": 3600})
        requests.append({"method": request.method, "url": str(request.url)})
        return httpx.Response(200, json={"name": "operations/op"})

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
    store: WorkersStore, name: str = "home", *, priority: int = 1000, enabled: bool = True
) -> str:
    """Create + register a self-hosted worker; returns its id."""
    record, token = await store.create_self_hosted(name, priority=priority)
    registered = await store.register(token, {"hostname": name})
    assert registered is not None
    if not enabled:
        await store.update(record.id, enabled=False)
    return record.id


async def _platform_row_id(store: WorkersStore) -> str:
    await store.ensure_platform_row("cloud_run")
    rows = [w for w in await store.list() if w.kind == "cloud_run"]
    assert len(rows) == 1
    return rows[0].id


def _dispatcher(
    store: WorkersStore,
    registry: WakeChannelRegistry,
    *,
    cloud_run: CloudRunJobConfig | None,
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
        cloud_run=cloud_run,
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


def test_dispatcher_wakes_connected_home_worker_not_cloud_run() -> None:
    """A connected self-hosted worker at a higher priority absorbs the wake;
    the Cloud Run leg is never touched."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> tuple[bool, int, Any]:
        home_id = await _add_home(store)
        await _platform_row_id(store)
        registry = WakeChannelRegistry()
        queue = registry.connect(home_id)
        dispatcher = _dispatcher(store, registry, cloud_run=_config(), requests=requests)
        fired = await dispatcher.trigger()
        woken = await store.get(home_id)
        assert woken is not None
        return fired, queue.qsize(), woken.last_wake_at

    fired, qsize, last_wake_at = asyncio.run(scenario())
    assert fired is True
    assert qsize == 1
    assert last_wake_at is not None
    assert requests == []


def test_dispatcher_prefers_higher_priority_worker() -> None:
    """Higher priority number wins (matches the register-worker copy). A
    connected self-hosted worker at priority 500 outranks the cloud_run row
    at 100, so it absorbs the wake and the Cloud Run leg is never touched."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> tuple[bool, int, Any]:
        home_id = await _add_home(store, priority=500)
        await _platform_row_id(store)  # cloud_run seeded at priority 100
        registry = WakeChannelRegistry()
        queue = registry.connect(home_id)
        dispatcher = _dispatcher(store, registry, cloud_run=_config(), requests=requests)
        fired = await dispatcher.trigger()
        woken = await store.get(home_id)
        assert woken is not None
        return fired, queue.qsize(), woken.last_wake_at

    fired, qsize, last_wake_at = asyncio.run(scenario())
    assert fired is True
    assert qsize == 1  # home (priority 500) absorbed the wake
    assert last_wake_at is not None
    assert requests == []  # cloud_run (priority 100) never started


def test_dispatcher_falls_through_to_cloud_run_when_home_disconnected() -> None:
    """A registered-but-disconnected home worker cannot absorb the wake;
    the tier falls through to the Cloud Run tier immediately."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> tuple[bool, Any]:
        await _add_home(store)
        platform_id = await _platform_row_id(store)
        dispatcher = _dispatcher(store, WakeChannelRegistry(), cloud_run=_config(), requests=requests)
        fired = await dispatcher.trigger()
        row = await store.get(platform_id)
        assert row is not None
        return fired, row.last_wake_at

    fired, last_wake_at = asyncio.run(scenario())
    assert fired is True
    assert requests == [{"method": "POST", "url": f"https://run.googleapis.com/v2/{JOB}:run"}]
    assert last_wake_at is not None


def test_dispatcher_disabled_platform_row_never_called() -> None:
    """The operator disabling the platform row wins over the env config."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> bool:
        platform_id = await _platform_row_id(store)
        await store.update(platform_id, enabled=False)
        dispatcher = _dispatcher(store, WakeChannelRegistry(), cloud_run=_config(), requests=requests)
        return await dispatcher.trigger()

    assert asyncio.run(scenario()) is False
    assert requests == []


def test_dispatcher_returns_false_when_nothing_wakeable() -> None:
    """No job configured + no connected homes: nothing woken, trigger False."""
    store = _fresh_store()

    async def scenario() -> tuple[bool, Any]:
        home_id = await _add_home(store)
        await _platform_row_id(store)  # row exists but cloud_run=None (no env config)
        dispatcher = _dispatcher(store, WakeChannelRegistry(), cloud_run=None)
        fired = await dispatcher.trigger()
        row = await store.get(home_id)
        assert row is not None
        return fired, row.last_wake_at

    fired, last_wake_at = asyncio.run(scenario())
    assert fired is False
    assert last_wake_at is None


def test_dispatcher_swallows_transport_errors() -> None:
    """A Cloud Run outage must never fail (or slow) the enqueue path."""
    store = _fresh_store()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("cloud run down", request=request)

    async def scenario() -> bool:
        await _platform_row_id(store)
        dispatcher = _dispatcher(
            store, WakeChannelRegistry(), cloud_run=_config(), transport=httpx.MockTransport(handler)
        )
        return await dispatcher.trigger()

    assert asyncio.run(scenario()) is False


def test_dispatcher_treats_a_refused_job_start_as_failure() -> None:
    """jobs.run answering 403 (say, the invoker grant is missing) is a failed
    wake, not a fired one; the nets drain the queue later."""
    store = _fresh_store()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == GCP_METADATA_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "sa-token", "expires_in": 3600})
        return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}})

    async def scenario() -> bool:
        await _platform_row_id(store)
        dispatcher = _dispatcher(
            store, WakeChannelRegistry(), cloud_run=_config(), transport=httpx.MockTransport(handler)
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
        dispatcher = _dispatcher(store, registry, cloud_run=None)
        first = await dispatcher.trigger()
        second = await dispatcher.trigger()
        return first, second, queue.qsize()

    first, second, qsize = asyncio.run(scenario())
    assert first is True
    assert second is False
    assert qsize == 1  # the second call never reached the tier walk


def test_dispatcher_skips_while_worker_active() -> None:
    """A live heartbeat means a drain is in flight; waking anything now is
    pointless (self-hosted) or wasteful (a second job drain beside the live one)."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> tuple[bool, int]:
        home_id = await _add_home(store)
        await _platform_row_id(store)
        registry = WakeChannelRegistry()
        queue = registry.connect(home_id)
        dispatcher = _dispatcher(
            store, registry, cloud_run=_config(), requests=requests, worker_active=_active
        )
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
        await _platform_row_id(store)
        dispatcher = _dispatcher(
            store, WakeChannelRegistry(), cloud_run=_config(), requests=requests, worker_active=_broken
        )
        return await dispatcher.trigger()

    assert asyncio.run(scenario()) is False
    assert requests == []


# ---------------------------------------------------------------------------
# Grace-window escalation
# ---------------------------------------------------------------------------


def test_grace_escalation_wakes_cloud_run_when_home_stays_idle() -> None:
    """Home worker takes the wake but never starts draining: after
    grace_seconds with jobs still pending, escalate past its tier."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> bool:
        home_id = await _add_home(store)
        await _platform_row_id(store)
        registry = WakeChannelRegistry()
        registry.connect(home_id)
        dispatcher = _dispatcher(
            store,
            registry,
            cloud_run=_config(),
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
    assert len(requests) == 1  # escalation started the Cloud Run job


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
        await _platform_row_id(store)
        registry = WakeChannelRegistry()
        registry.connect(home_id)
        dispatcher = _dispatcher(
            store,
            registry,
            cloud_run=_config(),
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
        await _platform_row_id(store)
        registry = WakeChannelRegistry()
        registry.connect(home_id)
        dispatcher = _dispatcher(
            store,
            registry,
            cloud_run=_config(),
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
    """Two overlapping grace windows on the same unanswered wake must not
    start the Cloud Run job twice: the first clears the anchor under the
    lock; the second finds it cleared and stands down."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> None:
        await _platform_row_id(store)
        dispatcher = _dispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=_config(),
            requests=requests,
            worker_active=_inactive,
            pending_jobs=_pending(1),
        )
        dispatcher._unanswered_since = 1.0
        await asyncio.gather(
            dispatcher._grace_escalate(1000, anchor=1.0, delay=0.0),
            dispatcher._grace_escalate(1000, anchor=1.0, delay=0.0),
        )

    asyncio.run(scenario())
    assert len(requests) == 1


def test_grace_escalation_stands_down_once_its_wake_was_answered() -> None:
    """A window whose anchor was cleared (a drain was seen, or another
    window already escalated) must not escalate. A newer attempt alone does
    not clear it: see test_grace_escalation_survives_triggers_faster_than_the_window."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> None:
        await _platform_row_id(store)
        dispatcher = _dispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=_config(),
            requests=requests,
            worker_active=_inactive,
            pending_jobs=_pending(1),
        )
        dispatcher._unanswered_since = None  # answered since this window was armed
        await dispatcher._grace_escalate(1000, anchor=1.0, delay=0.0)

    asyncio.run(scenario())
    assert requests == []


def test_grace_escalation_survives_triggers_faster_than_the_window() -> None:
    """A dead home worker under a steady stream of submits: every trigger
    re-wakes the home tier, but the escalation is due ``grace_seconds`` after
    the FIRST unanswered wake, so the platform worker still starts. Anchoring
    the window to the latest attempt instead starved the queue for as long
    as submits kept coming more often than the grace window."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> int:
        home_id = await _add_home(store)
        await _platform_row_id(store)
        registry = WakeChannelRegistry()
        registry.connect(home_id)  # takes every wake, never drains
        dispatcher = WorkerDispatcher(
            store,
            registry,
            cloud_run=_config(),
            worker_active=_inactive,
            pending_jobs=_pending(5),
            cooldown_seconds=0.0,
            grace_seconds=0.2,
            transport=_transport(requests),
        )
        for _ in range(12):  # a trigger every 50 ms for 600 ms
            await dispatcher.trigger()
            await asyncio.sleep(0.05)
        # Checked while submits are still arriving: the escalation was due
        # 200 ms after the first wake, not 200 ms after they stop.
        started_while_busy = len(requests)
        await _drain_tasks(dispatcher)
        return started_while_busy

    assert asyncio.run(scenario()) >= 1


def test_a_seen_drain_gives_the_next_wake_a_fresh_window() -> None:
    """The home worker answered (a heartbeat was seen), then a new job was
    woken for: that wake's window starts over rather than inheriting the
    first wake's deadline."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []
    state = {"active": False}

    async def _worker_active() -> bool:
        return state["active"]

    async def scenario() -> int:
        home_id = await _add_home(store)
        await _platform_row_id(store)
        registry = WakeChannelRegistry()
        registry.connect(home_id)
        dispatcher = WorkerDispatcher(
            store,
            registry,
            cloud_run=_config(),
            worker_active=_worker_active,
            pending_jobs=_pending(5),
            cooldown_seconds=0.0,
            grace_seconds=0.3,
            transport=_transport(requests),
        )
        await dispatcher.trigger()  # t=0: first wake
        await asyncio.sleep(0.1)
        state["active"] = True
        await dispatcher.trigger()  # t=0.1: a drain is seen
        state["active"] = False
        await asyncio.sleep(0.1)
        await dispatcher.trigger()  # t=0.2: a new wake, deadline t=0.5
        await asyncio.sleep(0.2)  # t=0.4: past the first wake's deadline
        started_early = len(requests)
        await _drain_tasks(dispatcher)
        return started_early

    assert asyncio.run(scenario()) == 0
    assert len(requests) == 1  # the fresh window escalates once, on time


def test_touch_wake_failure_does_not_cancel_wake_or_grace() -> None:
    """A DB hiccup on the last_wake_at bookkeeping must not flip a delivered
    wake to False or skip arming the grace timer."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> bool:
        home_id = await _add_home(store)
        await _platform_row_id(store)
        registry = WakeChannelRegistry()
        queue = registry.connect(home_id)

        async def _broken_touch_wake(worker_ids: list[str]) -> None:
            raise RuntimeError("db gone")

        store.touch_wake = _broken_touch_wake  # type: ignore[method-assign]
        dispatcher = _dispatcher(
            store,
            registry,
            cloud_run=_config(),
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
    assert len(requests) == 1  # grace escalation still armed and started the job


def test_grace_timer_not_armed_for_platform_only_wake() -> None:
    """A platform-only wake has no faster net than the existing ones; nothing
    to escalate to, so no second redeploy after the grace window."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> None:
        await _platform_row_id(store)
        dispatcher = _dispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=_config(),
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
        dispatcher = _dispatcher(store, registry, cloud_run=None)
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
    dispatcher = _dispatcher(store, WakeChannelRegistry(), cloud_run=None)
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


def test_build_dispatcher_default_has_the_cloud_run_leg_when_a_job_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_WORKER_JOB, JOB)
    dispatcher = build_worker_dispatcher(
        _fresh_store(), WakeChannelRegistry(), worker_active=None, pending_jobs=_pending(0)
    )
    assert isinstance(dispatcher, WorkerDispatcher)
    assert dispatcher._cloud_run == _config()


def test_build_dispatcher_without_a_job_still_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """No worker job does not disable the wake path: self-hosted workers can
    still be woken; only the platform leg is absent."""
    _clear_env(monkeypatch)
    dispatcher = build_worker_dispatcher(
        _fresh_store(), WakeChannelRegistry(), worker_active=None, pending_jobs=_pending(0)
    )
    assert isinstance(dispatcher, WorkerDispatcher)
    assert dispatcher._cloud_run is None


def test_build_dispatcher_refuses_the_removed_railway_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale SPLITSMITH_WORKER_LAUNCHER=railway must fail the boot loudly:
    the leg is gone, and silently waking nobody would strand every job."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_WORKER_LAUNCHER, "railway")
    with pytest.raises(ValueError, match="unknown worker launcher"):
        build_worker_dispatcher(
            _fresh_store(), WakeChannelRegistry(), worker_active=None, pending_jobs=_pending(0)
        )


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
