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
    DEFAULT_JOBS_PER_EXECUTION,
    ENV_WAKE_CHANNEL,
    ENV_WORKER_JOB,
    ENV_WORKER_JOBS_PER_EXECUTION,
    ENV_WORKER_LAUNCHER,
    ENV_WORKER_MAX_EXECUTIONS,
    GCP_METADATA_TOKEN_URL,
    CloudRunJobConfig,
    WorkerDispatcher,
    build_worker_dispatcher,
    hold_wakes,
    make_boot_retrigger,
    make_pending_jobs_counter,
    make_worker_active_checker,
    wrap_deferrer,
)

_ALL_ENV_VARS = (
    ENV_WORKER_LAUNCHER,
    ENV_WORKER_JOB,
    ENV_WAKE_CHANNEL,
    ENV_WORKER_MAX_EXECUTIONS,
    ENV_WORKER_JOBS_PER_EXECUTION,
)


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
    # Off unless a test is about it: with jobs pending and no live worker
    # the after-wake watch keeps waking, so ``_drain_tasks`` would not end.
    watch_seconds: float | None = None,
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
        watch_seconds=watch_seconds,
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
            watch_seconds=None,
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
            watch_seconds=None,
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


# ---------------------------------------------------------------------------
# After-wake watch (#1677): a drain that ends with work left is woken again
# ---------------------------------------------------------------------------


def _sequence(*values: Any) -> Any:
    """An async callable returning ``values`` in turn, then the last forever."""
    remaining = list(values)

    async def _next() -> Any:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return _next


def _watch_scenario(*, pending: Any, worker_active: Any) -> list[dict[str, Any]]:
    """Wake the platform worker once and let the watch run to its end; return the job starts."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> None:
        await _platform_row_id(store)
        dispatcher = _dispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=_config(),
            requests=requests,
            worker_active=worker_active,
            pending_jobs=pending,
            watch_seconds=0.01,
        )
        dispatcher._cooldown_seconds = 0.0
        assert await dispatcher.trigger() is True
        await asyncio.wait_for(_drain_tasks(dispatcher), 5)

    asyncio.run(scenario())
    return requests


def test_watch_wakes_again_while_jobs_are_left_and_no_worker_lives() -> None:
    """The execution exits (its budget spent, or killed) with jobs still
    outstanding and no heartbeat: the watch starts another, until the queue
    is empty."""
    requests = _watch_scenario(pending=_sequence(3, 2, 0), worker_active=_inactive)
    assert len(requests) == 3  # the trigger, then one re-wake per non-empty check


def test_watch_waits_while_a_worker_is_live() -> None:
    # No worker at the trigger; the woken one is live at every watch check.
    requests = _watch_scenario(pending=_sequence(5, 5, 0), worker_active=_sequence(False, True))
    assert len(requests) == 1


def test_watch_gives_up_when_re_wakes_make_no_progress() -> None:
    """A queue no drain can shorten must not start executions forever."""
    from splitsmith.worker_trigger import WATCH_MAX_FRUITLESS_WAKES

    requests = _watch_scenario(pending=_pending(4), worker_active=_inactive)
    assert len(requests) == 1 + WATCH_MAX_FRUITLESS_WAKES


# ---------------------------------------------------------------------------
# Fan-out (#1671): a backlog runs on several executions, up to the cap
# ---------------------------------------------------------------------------


def _count(n: int) -> Any:
    async def _n() -> int:
        return n

    return _n


def _fan_out_starts(
    *,
    pending: int,
    live: int,
    max_executions: int,
    active: Any = _active,
    triggers: int = 1,
    instances: int = 1,
) -> list[int]:
    """Trigger ``triggers`` times on a platform-only fleet, round-robin over
    ``instances`` serve processes sharing one database; return the job
    starts after each."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []
    starts: list[int] = []

    async def scenario() -> None:
        await _platform_row_id(store)
        dispatchers = [
            WorkerDispatcher(
                store,
                WakeChannelRegistry(),
                cloud_run=_config(),
                worker_active=active,
                pending_jobs=_pending(pending),
                cooldown_seconds=0.0,
                transport=_transport(requests),
                watch_seconds=None,
                live_workers=_count(live),
                max_executions=max_executions,
                jobs_per_execution=4,
            )
            for _ in range(instances)
        ]
        for n in range(triggers):
            await dispatchers[n % instances].trigger()
            starts.append(len(requests))
        for dispatcher in dispatchers:
            await _drain_tasks(dispatcher)

    asyncio.run(scenario())
    return starts


def test_fan_out_adds_executions_beside_a_live_drain_for_a_backlog() -> None:
    """Twelve queued jobs at four per execution want three; one is live."""
    assert _fan_out_starts(pending=12, live=1, max_executions=3) == [2]


def test_fan_out_is_capped() -> None:
    assert _fan_out_starts(pending=100, live=1, max_executions=2) == [1]


def test_fan_out_leaves_a_small_queue_to_the_live_drain() -> None:
    assert _fan_out_starts(pending=3, live=1, max_executions=3) == [0]


def test_fan_out_is_off_at_a_cap_of_one() -> None:
    assert _fan_out_starts(pending=100, live=1, max_executions=1) == [0]


def test_a_cold_wake_starts_what_the_backlog_wants_and_counts_them_while_they_boot() -> None:
    """No drain live: the wake starts three for twelve jobs. A trigger right
    after, while those three boot without a heartbeat, adds none."""
    assert _fan_out_starts(
        pending=12, live=0, max_executions=3, active=_sequence(False, True), triggers=2
    ) == [
        3,
        3,
    ]


def test_a_second_cold_wake_while_the_first_boots_starts_only_one() -> None:
    """No heartbeat yet at either trigger: the second counts the three still
    booting and starts one (a booting one may have died), not three more."""
    assert _fan_out_starts(pending=12, live=0, max_executions=3, active=_inactive, triggers=2) == [3, 4]


def test_the_cap_holds_across_serve_instances() -> None:
    """Serve runs several instances. One cold-wakes four executions for
    sixteen jobs; the others, triggered while those four boot without a
    heartbeat, see them through the database and add none."""
    assert _fan_out_starts(
        pending=16, live=0, max_executions=4, active=_sequence(False, True), triggers=3, instances=3
    ) == [4, 4, 4]


def test_a_refused_start_releases_its_claim() -> None:
    """Claims the platform refused are not counted as booting: the next
    trigger may start them. The starts go at once, so one refusal does not
    stop the others: three of four start and one claim is released."""
    store = _fresh_store()
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == GCP_METADATA_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        attempts.append(str(request.url))
        if len(attempts) == 2:
            return httpx.Response(429, json={"error": "quota"})
        return httpx.Response(200, json={"name": "operations/op"})

    async def scenario() -> int:
        row_id = await _platform_row_id(store)
        dispatcher = WorkerDispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=_config(),
            worker_active=_inactive,
            pending_jobs=_pending(16),
            transport=httpx.MockTransport(handler),
            watch_seconds=None,
            max_executions=4,
        )
        assert await dispatcher.trigger() is True  # three started, the second refused
        await _drain_tasks(dispatcher)
        return await store.claim_starts(row_id, wanted=4, live=0, grace_seconds=120, at_least_one=False)

    assert asyncio.run(scenario()) == 1
    assert len(attempts) == 4


def test_a_watch_re_wake_inside_the_cooldown_starts_nothing() -> None:
    """A re-wake queued behind a trigger's in-flight job start finds the
    cooldown stamped and stands down instead of starting a second one."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == GCP_METADATA_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        requests.append({"url": str(request.url)})
        await release.wait()
        return httpx.Response(200, json={"name": "operations/op"})

    async def scenario() -> None:
        await _platform_row_id(store)
        dispatcher = WorkerDispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=_config(),
            worker_active=_inactive,
            pending_jobs=_pending(1),
            cooldown_seconds=30.0,
            transport=httpx.MockTransport(handler),
            watch_seconds=None,
        )
        trigger = asyncio.create_task(dispatcher.trigger())
        await asyncio.sleep(0.05)  # the trigger holds the lock, its start in flight
        rewake = asyncio.create_task(dispatcher._rewake())
        await asyncio.sleep(0.05)
        release.set()
        await asyncio.wait_for(asyncio.gather(trigger, rewake), 5)

    asyncio.run(scenario())
    assert len(requests) == 1


def test_build_dispatcher_reads_the_fan_out_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(ENV_WORKER_MAX_EXECUTIONS, "5")
    monkeypatch.setenv(ENV_WORKER_JOBS_PER_EXECUTION, "6")
    dispatcher = build_worker_dispatcher(
        _fresh_store(), WakeChannelRegistry(), worker_active=None, pending_jobs=_pending(0)
    )
    assert dispatcher is not None
    assert (dispatcher._max_executions, dispatcher._jobs_per_execution) == (5, 6)
    monkeypatch.setenv(ENV_WORKER_MAX_EXECUTIONS, "0")
    with pytest.raises(ValueError):
        build_worker_dispatcher(
            _fresh_store(), WakeChannelRegistry(), worker_active=None, pending_jobs=_pending(0)
        )


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


def test_make_pending_jobs_counter_counts_a_dead_workers_doing_jobs() -> None:
    """An orphan needs a wake as much as a queued job: only a drain's stalled
    sweep ends it (#1677). Run against the two tables on SQLite: queued jobs
    of any priority (proxies are deferred low) and task count once each, as
    do jobs of a stale or deleted worker; a live worker's job does not."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import DateTime, bindparam, text

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = sessionmaker(engine)
    now = datetime.now(UTC)
    stamp = text("INSERT INTO procrastinate_workers (id, last_heartbeat) VALUES (:id, :at)").bindparams(
        bindparam("at", type_=DateTime(timezone=True))
    )

    async def scenario() -> int:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE procrastinate_jobs (id INTEGER PRIMARY KEY, status TEXT, "
                    "worker_id INTEGER, priority INTEGER, task_name TEXT)"
                )
            )
            await conn.execute(
                text("CREATE TABLE procrastinate_workers (id INTEGER PRIMARY KEY, last_heartbeat)")
            )
            await conn.execute(stamp, {"id": 1, "at": now - timedelta(seconds=5)})  # live
            await conn.execute(stamp, {"id": 2, "at": now - timedelta(seconds=300)})  # killed
            rows = [
                ("todo", None, 0, "run_compute_job"),  # counted
                ("todo", None, -100, "run_compute_job"),  # a proxy, counted once
                ("todo", None, 0, "ping"),  # counted
                ("doing", 1, 0, "run_compute_job"),  # live worker: not counted
                ("doing", 2, 0, "run_compute_job"),  # stale worker: counted
                ("doing", 2, -100, "run_compute_job"),  # stale worker: counted
                ("doing", None, 0, "run_compute_job"),  # pruned worker: counted
                ("succeeded", None, 0, "run_compute_job"),
                ("failed", 2, 0, "run_compute_job"),
            ]
            for status, worker_id, priority, task in rows:
                await conn.execute(
                    text(
                        "INSERT INTO procrastinate_jobs (status, worker_id, priority, task_name) "
                        "VALUES (:s, :w, :p, :t)"
                    ),
                    {"s": status, "w": worker_id, "p": priority, "t": task},
                )
        return await make_pending_jobs_counter(factory)()

    assert asyncio.run(scenario()) == 6


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


def test_held_wakes_fire_once_when_the_batch_ends() -> None:
    """A batch of submits (a grid's stage jobs) wakes once, after its last
    defer, so the wake counts every job rather than the first."""
    calls: list[str] = []

    async def deferrer(**kwargs: Any) -> None:
        calls.append(kwargs["job_id"])

    class _Counting(_StubLauncher):
        def __init__(self) -> None:
            super().__init__()
            self.triggered: list[int] = []

        async def trigger(self) -> bool:
            self.triggered.append(len(calls))
            return True

    stub = _Counting()
    wrapped = wrap_deferrer(deferrer, stub)

    async def scenario() -> None:
        async with hold_wakes():
            for n in range(3):
                await wrapped(job_id=f"j{n}", user_id="u1", kind="trim", args={}, match_id=None)
            assert stub.scheduled == 0 and stub.triggered == []
        await wrapped(job_id="after", user_id="u1", kind="trim", args={}, match_id=None)

    asyncio.run(scenario())
    assert stub.triggered == [3]
    assert stub.scheduled == 1  # the submit after the batch wakes as before


def test_a_batch_that_raises_still_wakes_for_what_it_queued() -> None:
    async def deferrer(**kwargs: Any) -> None:
        return None

    stub = _StubLauncher()
    wrapped = wrap_deferrer(deferrer, stub)

    async def scenario() -> None:
        async with hold_wakes():
            await wrapped(job_id="j1", user_id="u1", kind="trim", args={}, match_id=None)
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(scenario())
    assert stub.scheduled == 1  # _StubLauncher.trigger counts through schedule


def test_a_batched_grid_burst_starts_every_execution_at_once() -> None:
    """Run G2: twelve stage jobs and the grid job, queued 0.7 s apart. Woken
    per submit, the first wake saw one job and started one execution; the
    rest waited on the cooldown's settle check. Held to the batch's end, one
    wake counts all thirteen and starts thirteen."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []
    queued = {"n": 0}

    async def pending() -> int:
        return queued["n"]

    async def solo() -> int:
        return min(queued["n"], 12)

    async def deferrer(**kwargs: Any) -> None:
        queued["n"] += 1

    async def scenario() -> int:
        await _platform_row_id(store)
        dispatcher = WorkerDispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=_config(),
            worker_active=_inactive,
            pending_jobs=pending,
            cooldown_seconds=30.0,
            transport=_transport(requests),
            watch_seconds=None,
            live_workers=_count(0),
            max_executions=13,
            jobs_per_execution=2,
            solo_jobs=solo,
        )
        wrapped = wrap_deferrer(deferrer, dispatcher)
        async with hold_wakes():
            for n in range(13):
                await wrapped(job_id=f"j{n}", user_id="u1", kind="compare-grid-stage", args={}, match_id=None)
        started = len(requests)
        await asyncio.wait_for(_drain_tasks(dispatcher), 5)
        return started

    assert asyncio.run(scenario()) == 13


def test_executions_start_concurrently() -> None:
    """Thirteen ``jobs.run`` calls one after another took ten seconds on
    staging; the batch's wake is awaited inside the request, so they go at once."""
    store = _fresh_store()
    in_flight = {"now": 0, "peak": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == GCP_METADATA_TOKEN_URL:
            return httpx.Response(200, json={"access_token": "sa-token", "expires_in": 3600})
        in_flight["now"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["now"])
        await asyncio.sleep(0.02)
        in_flight["now"] -= 1
        return httpx.Response(200, json={"name": "operations/op"})

    async def scenario() -> int:
        worker_id = await _platform_row_id(store)
        dispatcher = WorkerDispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=_config(),
            worker_active=_inactive,
            pending_jobs=_pending(0),
            transport=httpx.MockTransport(handler),
            watch_seconds=None,
        )
        return await dispatcher._start_executions(worker_id, 5)

    assert asyncio.run(scenario()) == 5
    assert in_flight["peak"] == 5


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


# ---------------------------------------------------------------------------
# Fan-out for backlogs that never reach trigger() (#1709)
# ---------------------------------------------------------------------------


def _backlog_starts(
    *,
    pending: Any,
    active: Any,
    triggers: int = 1,
    cooldown: float = 0.0,
    watch_seconds: float | None = 0.01,
    live: int = 1,
    jobs_per_execution: int = 4,
    max_executions: int = 4,
    solo: Any = None,
) -> int:
    """Trigger ``triggers`` times on a platform-only fleet with ``live``
    heartbeats, let the watch and the settle check run out; return the job starts."""
    store = _fresh_store()
    requests: list[dict[str, Any]] = []

    async def scenario() -> None:
        await _platform_row_id(store)
        dispatcher = WorkerDispatcher(
            store,
            WakeChannelRegistry(),
            cloud_run=_config(),
            worker_active=active,
            pending_jobs=pending,
            cooldown_seconds=cooldown,
            transport=_transport(requests),
            watch_seconds=watch_seconds,
            live_workers=_count(live),
            max_executions=max_executions,
            jobs_per_execution=jobs_per_execution,
            **({"solo_jobs": solo} if solo is not None else {}),
        )
        for _ in range(triggers):
            await dispatcher.trigger()
        await asyncio.wait_for(_drain_tasks(dispatcher), 5)

    asyncio.run(scenario())
    return len(requests)


def test_watch_fans_out_for_jobs_a_live_drain_chains() -> None:
    """The submit finds a drain live with one job queued; that drain then
    chains four more (beep to trim) straight into the queue, with no submit.
    The watch adds the second execution five jobs want."""
    assert _backlog_starts(pending=_sequence(1, 5, 5, 0), active=_active) == 1


def test_the_bench_shape_gets_a_second_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run A3: one cold-woken execution, its five beep jobs chain five trims.
    Once its start has aged out of the boot grace, the watch adds one."""
    monkeypatch.setattr("splitsmith.worker_trigger.STARTUP_GRACE_SECONDS", 0.0)
    assert _backlog_starts(pending=_sequence(1, 5, 5, 0), active=_sequence(False, True)) == 2


def test_the_job_a_live_drain_runs_counts_toward_the_backlog() -> None:
    """Run A4: the drain runs one trim and four wait (``pending_jobs`` counts
    only the four). Five jobs at four per execution want two, so one is added;
    counting the queue alone wanted one and added none."""
    assert _backlog_starts(pending=_sequence(1, 4, 4, 0), active=_active) == 1


def test_a_stages_trims_fan_out_at_the_default_jobs_per_execution() -> None:
    """The same stage at the default two jobs per execution: five jobs want
    three executions, so two join the live drain."""
    starts = _backlog_starts(
        pending=_sequence(1, 4, 4, 0), active=_active, jobs_per_execution=DEFAULT_JOBS_PER_EXECUTION
    )
    assert starts == 2


def test_a_grid_s_stage_jobs_each_get_a_worker() -> None:
    """#1691: twelve queued grid stage jobs beside the live grid job want
    a worker each, not one per two jobs: one round of stages, not two."""
    starts = _backlog_starts(
        pending=_sequence(12, 12, 12, 0),
        active=_active,
        jobs_per_execution=DEFAULT_JOBS_PER_EXECUTION,
        max_executions=13,
        solo=_sequence(12, 12, 12, 0),
    )
    assert starts == 12


def test_solo_jobs_share_the_cap() -> None:
    starts = _backlog_starts(
        pending=_sequence(12, 12, 12, 0),
        active=_active,
        jobs_per_execution=DEFAULT_JOBS_PER_EXECUTION,
        max_executions=8,
        solo=_sequence(12, 12, 12, 0),
    )
    assert starts == 7


def test_a_burst_dropped_by_the_cooldown_is_counted_when_it_ends() -> None:
    """Five submits beside a live drain: the first sees one job, the other
    four land in the cooldown. One settle check at its end counts all five."""
    starts = _backlog_starts(
        pending=_sequence(1, 5), active=_active, triggers=5, cooldown=0.05, watch_seconds=None
    )
    assert starts == 1


def test_make_solo_jobs_counter_counts_queued_grid_stages() -> None:
    """#1691: only queued jobs of a solo kind count, read from the task's
    args (JSON text on SQLite, a dict on Postgres)."""
    import json as _json

    from sqlalchemy import text

    from splitsmith.worker_trigger import make_solo_jobs_counter

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = sessionmaker(engine)

    async def scenario() -> int:
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE procrastinate_jobs (id INTEGER PRIMARY KEY, status TEXT, args TEXT)")
            )
            for status, kind in (
                ("todo", "compare-grid-stage"),
                ("todo", "compare-grid-stage"),
                ("doing", "compare-grid-stage"),
                ("todo", "trim"),
                ("todo", "compare-grid"),
            ):
                await conn.execute(
                    text("INSERT INTO procrastinate_jobs (status, args) VALUES (:s, :a)"),
                    {"s": status, "a": _json.dumps({"kind": kind, "job_id": "x"})},
                )
        return await make_solo_jobs_counter(factory)()

    assert asyncio.run(scenario()) == 2


def test_a_backlog_queued_behind_a_cold_wake_fans_out_while_it_boots() -> None:
    """A burst (a grid's stage jobs, a match's uploads) wakes one execution;
    the rest of the burst lands in the cooldown. The settle check at its end
    fans out beside that execution while it still boots (no heartbeat yet):
    the platform row's recent start shows the tier walk chose Cloud Run.
    Before, it waited for a heartbeat and the watch two minutes later."""
    starts = _backlog_starts(
        pending=_sequence(1, 13),
        active=_inactive,
        triggers=2,
        cooldown=0.05,
        watch_seconds=None,
        live=0,
        jobs_per_execution=2,
        max_executions=13,
    )
    assert starts == 7


def test_with_no_execution_booting_the_settle_check_still_waits_for_a_drain() -> None:
    """No recent Cloud Run start (a self-hosted box was woken, or nothing
    was): the settle check starts nothing, so it never jumps a box that is
    still booting."""
    starts = _backlog_starts(
        pending=_sequence(0, 13),
        active=_inactive,
        triggers=1,
        cooldown=0.05,
        watch_seconds=None,
        live=0,
        jobs_per_execution=2,
        max_executions=13,
    )
    assert starts <= 1
