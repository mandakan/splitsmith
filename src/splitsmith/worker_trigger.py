"""Wake a worker when a job is enqueued (the wake channel).

The hosted worker runs ``splitsmith worker --one-shot``: it drains the
queue and exits, so no idle process holds a connection that keeps the
Neon compute awake. Something must therefore start it when work arrives.
The API process is the only enqueuer, so after a successful defer it
fires the :class:`WorkerDispatcher`.

The queue of record stays in Postgres (Procrastinate); a wake is only a
signal and must be safe to lose - the serve-boot pending re-check and
the 6-hourly safety cron on the worker service are the nets. The
dispatcher walks the registered workers (``workers`` table) in priority
tiers, ascending:

- **self-hosted** workers are woken by pushing "wake" onto their live
  SSE channel (:class:`~splitsmith.worker_channel.WakeChannelRegistry`);
  the push doubles as the availability check - a disconnected box simply
  is not there to wake.
- **railway** (the single ``kind='railway'`` row) is woken by firing the
  GraphQL ``serviceInstanceRedeploy`` mutation, available whenever the
  Railway env config is present.

The first tier with at least one successful wake wins; lower tiers stay
asleep. Because a self-hosted wake is best-effort (the box may be
connected but wedged), a successful self-hosted wake arms a grace timer:
if jobs are still pending and no worker heartbeat has appeared after
``grace_seconds``, the dispatcher escalates to the tiers below the one
it woke - bypassing the cooldown, since the escalation is part of the
same logical trigger.

Configuration comes from env vars (:func:`build_worker_dispatcher`);
``SPLITSMITH_WORKER_LAUNCHER=none`` disables the dispatcher entirely so
local / docker-compose deployments (always-on worker) skip the wake
machinery.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from pydantic import BaseModel

if TYPE_CHECKING:
    from .db.workers import WorkersStore
    from .worker_channel import WakeChannelRegistry

logger = logging.getLogger(__name__)

RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"

# Selects the dispatcher mode in build_worker_dispatcher(): unset or
# "railway" builds the dispatcher (its railway leg needs the env vars
# below); "none" disables the wake machinery entirely.
ENV_WORKER_LAUNCHER = "SPLITSMITH_WORKER_LAUNCHER"
ENV_TRIGGER_TOKEN = "SPLITSMITH_WORKER_TRIGGER_TOKEN"
ENV_WORKER_SERVICE_ID = "SPLITSMITH_WORKER_SERVICE_ID"
ENV_WORKER_ENVIRONMENT_ID = "SPLITSMITH_WORKER_ENVIRONMENT_ID"
# Railway injects this into every deployed container; the SPLITSMITH_
# variable above exists only to override it outside Railway.
ENV_RAILWAY_ENVIRONMENT_ID = "RAILWAY_ENVIRONMENT_ID"


class RailwayLauncherConfig(BaseModel):
    """Railway coordinates + auth for starting a one-shot worker run."""

    token: str
    service_id: str
    environment_id: str
    api_url: str = RAILWAY_API_URL
    cooldown_seconds: float = 30.0


def load_railway_config() -> RailwayLauncherConfig | None:
    """Read the Railway leg's config from env vars; ``None`` disables it."""
    token = os.environ.get(ENV_TRIGGER_TOKEN, "").strip()
    service_id = os.environ.get(ENV_WORKER_SERVICE_ID, "").strip()
    environment_id = (
        os.environ.get(ENV_WORKER_ENVIRONMENT_ID, "").strip()
        or os.environ.get(ENV_RAILWAY_ENVIRONMENT_ID, "").strip()
    )
    if not (token and service_id and environment_id):
        return None
    return RailwayLauncherConfig(token=token, service_id=service_id, environment_id=environment_id)


class WorkerLauncher(Protocol):
    """Wake channel for the scale-to-zero worker.

    The queue of record stays in Postgres; a launcher only signals
    "work exists" and must be safe to lose a signal - the serve-boot
    pending re-check and the safety cron are the nets. Implementations
    must make ``trigger`` never raise.
    """

    def schedule(self) -> None: ...

    async def trigger(self) -> bool: ...


WorkerActiveCheck = Callable[[], Awaitable[bool]]
PendingJobsCount = Callable[[], Awaitable[int]]

_REDEPLOY_MUTATION = """\
mutation TriggerWorkerRun($serviceId: String!, $environmentId: String!) {
  serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
}
"""


class WorkerDispatcher:
    """Wakes the highest-priority available worker(s), at most once per
    cooldown window, and only when no live worker heartbeat exists.

    Railway's deployment status cannot gate "is a drain running": a
    one-shot deployment keeps status SUCCESS after its container exits
    (observed live on staging, 2026-07-03). The authoritative signal is
    Procrastinate's own worker registry - ``worker_active`` (built by
    :func:`make_worker_active_checker`) reads it. With no checker
    configured the dispatcher wakes unconditionally, subject to the
    cooldown.

    See the module docstring for the tier walk and the grace-window
    escalation.
    """

    def __init__(
        self,
        store: WorkersStore,
        registry: WakeChannelRegistry,
        *,
        railway: RailwayLauncherConfig | None,
        worker_active: WorkerActiveCheck | None,
        pending_jobs: PendingJobsCount,
        cooldown_seconds: float = 30.0,
        grace_seconds: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._railway = railway
        self._worker_active = worker_active
        self._pending_jobs = pending_jobs
        self._cooldown_seconds = cooldown_seconds
        self._grace_seconds = grace_seconds
        # The dispatcher owns an HTTP client only when it has a railway leg.
        self._client: httpx.AsyncClient | None = (
            httpx.AsyncClient(
                headers={"Project-Access-Token": railway.token},
                timeout=10.0,
                transport=transport,
            )
            if railway is not None
            else None
        )
        self._lock = asyncio.Lock()
        self._last_attempt: float | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def schedule(self) -> None:
        """Run :meth:`trigger` as a background task; never blocks the caller.

        Holding the task in ``self._tasks`` keeps it from being garbage
        collected mid-flight (asyncio only holds weak refs to tasks).

        Never raises. It is called from the enqueue path (a raise would 500 a
        request whose job already committed) and from the serve-boot lifespan
        (a raise would abort the entire boot). A failed schedule is safe to
        lose - the boot re-check and the safety cron are the nets.
        """
        self._spawn(self.trigger())

    async def trigger(self) -> bool:
        """Wake a worker unless one is draining or we are in cooldown.

        Returns True when a wake was fired. Never raises: a Railway or
        DB hiccup must not break enqueue - a missed signal is repaired by
        the boot re-check or the safety cron. A failing heartbeat check
        counts as "worker may be active": a Railway redeploy on bad
        information could kill a live drain, and the nets cover the
        missed launch.
        """
        async with self._lock:
            now = time.monotonic()
            if self._last_attempt is not None and now - self._last_attempt < self._cooldown_seconds:
                return False
            # Stamp before the checks so a slow/failing backend is not
            # hammered by every enqueue in a burst.
            self._last_attempt = now
            try:
                if self._worker_active is not None and await self._worker_active():
                    logger.info("worker dispatcher: skipped, a live worker heartbeat exists")
                    return False
                return await self._wake_from(min_priority_exclusive=None)
            except Exception:
                logger.warning(
                    "worker dispatcher: failed; boot re-check or safety cron will drain the queue",
                    exc_info=True,
                )
                return False

    async def _wake_from(self, min_priority_exclusive: int | None) -> bool:
        """Walk the enabled workers in ascending priority tiers and wake the
        first tier with at least one reachable target.

        ``min_priority_exclusive`` skips tiers at or above that priority
        (used by the grace escalation to wake only the tiers below the one
        it already woke). A tier where every wake fails falls through to
        the next tier. Returns True when any wake was fired.
        """
        workers = await self._store.list_enabled()
        if min_priority_exclusive is not None:
            workers = [w for w in workers if w.priority > min_priority_exclusive]
        for priority, tier in itertools.groupby(workers, key=lambda w: w.priority):
            woken_ids: list[str] = []
            woke_self_hosted = False
            for worker in tier:
                if worker.kind == "railway":
                    if self._railway is None:
                        continue
                    try:
                        await self._redeploy()
                    except Exception:
                        logger.warning("worker dispatcher: railway redeploy failed", exc_info=True)
                        continue
                    logger.info("worker dispatcher: railway redeploy fired")
                    woken_ids.append(worker.id)
                elif self._registry.push(worker.id, "wake"):
                    # The push IS the availability check: a disconnected
                    # box has no channel and simply is not woken.
                    logger.info("worker dispatcher: wake pushed to %s", worker.name)
                    woken_ids.append(worker.id)
                    woke_self_hosted = True
            if woken_ids:
                try:
                    await self._store.touch_wake(woken_ids)
                except Exception:
                    # Bookkeeping only (last_wake_at): a DB hiccup here must
                    # not cancel the grace-timer arming below or flip a
                    # delivered wake to False.
                    logger.warning("worker dispatcher: touch_wake bookkeeping failed", exc_info=True)
                if woke_self_hosted:
                    # Self-hosted wakes are best-effort (a connected box may
                    # be wedged); railway-only wakes need no faster net than
                    # the existing ones.
                    self._spawn(self._grace_escalate(priority, armed_stamp=self._last_attempt))
                return True
        return False

    async def _grace_escalate(self, woken_priority: int, armed_stamp: float | None) -> None:
        """After ``grace_seconds``: if jobs are still pending and no worker
        heartbeat appeared, wake the tiers below the one already woken.

        ``armed_stamp`` is ``self._last_attempt`` as of the wake that armed
        this window. Before escalating, the stamp is re-checked and moved
        under ``self._lock``: if another wake attempt (a trigger or a racing
        escalation) has moved it since, this window stands down - the newer
        attempt's own grace window (or the nets) covers it. That keeps
        overlapping grace windows from double-booting Railway, while the
        escalation still bypasses the cooldown its own trigger stamped.

        Never raises: it runs as a detached task and a failure is
        covered by the nets. A failing heartbeat check aborts the
        escalation (same fail-safe as :meth:`trigger`).
        """
        try:
            await asyncio.sleep(self._grace_seconds)
            if await self._pending_jobs() <= 0:
                return
            if self._worker_active is not None and await self._worker_active():
                return
            async with self._lock:
                if self._last_attempt != armed_stamp:
                    return
                self._last_attempt = time.monotonic()
            logger.info(
                "worker dispatcher: no drain %.0fs after wake - escalating past priority %s",
                self._grace_seconds,
                woken_priority,
            )
            await self._wake_from(min_priority_exclusive=woken_priority)
        except Exception:
            logger.warning("worker dispatcher: grace escalation failed; nets will recover", exc_info=True)

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run ``coro`` as a GC-protected background task; never raises."""
        try:
            task = asyncio.get_running_loop().create_task(coro)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception:
            coro.close()  # silence the "never awaited" warning
            logger.warning("worker dispatcher: schedule failed; nets will recover", exc_info=True)

    async def _redeploy(self) -> None:
        assert self._railway is not None and self._client is not None
        variables = {
            "serviceId": self._railway.service_id,
            "environmentId": self._railway.environment_id,
        }
        response = await self._client.post(
            self._railway.api_url, json={"query": _REDEPLOY_MUTATION, "variables": variables}
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"Railway GraphQL error: {payload['errors']}")


def make_worker_active_checker(
    session_factory: Callable[[], Any], max_age_seconds: float = 20.0
) -> WorkerActiveCheck:
    """True while any Procrastinate worker heartbeat is fresher than
    ``max_age_seconds``.

    A live worker refreshes ``procrastinate_workers.last_heartbeat`` every
    ~10s and removes its row on clean exit; 20s tolerates one missed beat
    while letting a crashed worker's stale row unblock the dispatcher within
    seconds. Runs on the API's session factory - the dispatcher only fires
    during an enqueue, when the DB is already awake, so this adds no wake.
    """

    async def _worker_active() -> bool:
        from sqlalchemy import text

        async with session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM procrastinate_workers "
                    "WHERE last_heartbeat > now() - make_interval(secs => :age))"
                ),
                {"age": max_age_seconds},
            )
            return bool(result.scalar_one())

    return _worker_active


def make_pending_jobs_counter(session_factory: Callable[[], Any]) -> PendingJobsCount:
    """Count Procrastinate jobs still waiting for a worker.

    Shared by the serve-boot re-check (:func:`make_boot_retrigger`) and the
    dispatcher's grace escalation - one definition of "pending" for both.
    """

    async def _pending_jobs() -> int:
        from sqlalchemy import text

        async with session_factory() as session:
            result = await session.execute(
                text("SELECT count(*) FROM procrastinate_jobs WHERE status = 'todo'")
            )
            return int(result.scalar_one())

    return _pending_jobs


def wrap_deferrer(
    deferrer: Callable[..., Awaitable[None]], launcher: WorkerLauncher
) -> Callable[..., Awaitable[None]]:
    """After each successful defer, schedule a worker launch.

    The launch is fire-and-forget so enqueue latency stays flat; the
    defer's own exceptions propagate untouched (a failed enqueue must
    not fire a worker, and the caller needs the original error).
    """

    async def _defer_and_trigger(**kwargs: Any) -> None:
        await deferrer(**kwargs)
        launcher.schedule()

    return _defer_and_trigger


def make_boot_retrigger(
    session_factory: Callable[[], Any],
    launcher: WorkerLauncher,
    *,
    min_interval_seconds: float = 300.0,
) -> Callable[[], Awaitable[None]]:
    """Build the serve-boot pending-jobs re-check.

    Railway app sleeping cold-starts serve on each visit, so this runs on
    every wake - with the DB already awake from boot migrations. It closes
    the dispatcher's accepted races (missed signal, enqueue during worker exit)
    at the next visit instead of waiting for the safety cron, which can
    therefore run 6-hourly. Never raises: a failed check must not fail the
    boot.

    The hook is also called from ``/api/health`` so the 6-hourly wake
    workflow works even when serve never sleeps (an open SSE tab keeps serve
    awake, preventing the lifespan from re-running). The ``min_interval_seconds``
    cooldown prevents health pings from hammering the DB: a call within the
    interval returns immediately. The first call (boot) always runs.

    Note: an external uptime monitor pointed at ``/api/health`` would wake
    Neon up to once per interval - point monitors at the SPA root or a
    static asset instead.
    """
    _last_run: list[float] = []  # single-element list so the closure can mutate it
    _count_pending = make_pending_jobs_counter(session_factory)

    async def _retrigger_pending_jobs() -> None:
        now = time.monotonic()
        if _last_run and now - _last_run[0] < min_interval_seconds:
            return
        _last_run[:] = [now]

        try:
            pending = await _count_pending()
        except Exception:
            logger.warning("boot re-trigger: pending-job check failed", exc_info=True)
            return
        if pending:
            logger.info("boot re-trigger: %s pending job(s) - firing worker dispatcher", pending)
            launcher.schedule()

    return _retrigger_pending_jobs


def build_worker_dispatcher(
    store: WorkersStore,
    registry: WakeChannelRegistry,
    *,
    worker_active: WorkerActiveCheck | None,
    pending_jobs: PendingJobsCount,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WorkerDispatcher | None:
    """Build the dispatcher from ``SPLITSMITH_WORKER_LAUNCHER``.

    Unset or ``railway`` (the historical default) builds a dispatcher whose
    railway leg is :func:`load_railway_config` (absent env vars just drop
    that leg - self-hosted workers can still be woken). ``none`` disables
    the wake machinery (returns None) for deployments with an always-on
    worker. Unknown names raise so a misconfigured hosted boot fails loudly
    instead of stranding jobs.
    """
    kind = os.environ.get(ENV_WORKER_LAUNCHER, "").strip().lower() or "railway"
    if kind == "none":
        return None
    if kind != "railway":
        raise ValueError(f"{ENV_WORKER_LAUNCHER}={kind!r}: unknown worker launcher")
    return WorkerDispatcher(
        store,
        registry,
        railway=load_railway_config(),
        worker_active=worker_active,
        pending_jobs=pending_jobs,
        transport=transport,
    )
