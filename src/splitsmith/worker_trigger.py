"""Launch a one-shot worker run when a job is enqueued (the wake channel).

The hosted worker runs ``splitsmith worker --one-shot`` with restart
policy NEVER: it drains the queue and exits, so no idle process holds a
connection that keeps the Neon compute awake. Something must therefore
start it when work arrives. The API process is the only enqueuer, so
after a successful defer it fires the configured :class:`WorkerLauncher`.

The queue of record stays in Postgres (Procrastinate); a launcher is
only the wake signal and must be safe to lose - the serve-boot pending
re-check and the 6-hourly safety cron on the worker service are the
nets. The only implementation today is Railway (GraphQL
``serviceInstanceRedeploy``); the seam exists so non-Railway workers
(webhook/ntfy to a home machine, GitHub Actions) can be added without
touching the enqueue path.

Configuration comes entirely from env vars (:func:`build_worker_launcher`);
when they are absent the launcher is disabled and enqueue behaves exactly
as before, so local / docker-compose deployments never talk to Railway.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"

# Selects the launcher implementation in build_worker_launcher();
# "railway" is the only one today.
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
    """Read the Railway launcher config from env vars; ``None`` disables it."""
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

_REDEPLOY_MUTATION = """\
mutation TriggerWorkerRun($serviceId: String!, $environmentId: String!) {
  serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
}
"""


class RailwayWorkerLauncher:
    """Fires ``serviceInstanceRedeploy`` for the worker service, at most
    once per cooldown window, and only when no live worker heartbeat exists.

    Railway's deployment status cannot gate "is a drain running": a
    one-shot deployment keeps status SUCCESS after its container exits
    (observed live on staging, 2026-07-03). The authoritative signal is
    Procrastinate's own worker registry - ``worker_active`` (built by
    :func:`make_worker_active_checker`) reads it. With no checker
    configured the launcher redeploys unconditionally, subject to the
    cooldown.
    """

    def __init__(
        self,
        config: RailwayLauncherConfig,
        worker_active: WorkerActiveCheck | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._worker_active = worker_active
        self._client = httpx.AsyncClient(
            headers={"Project-Access-Token": config.token},
            timeout=10.0,
            transport=transport,
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
        try:
            task = asyncio.get_running_loop().create_task(self.trigger())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception:
            logger.warning("worker launcher: schedule failed; nets will recover", exc_info=True)

    async def trigger(self) -> bool:
        """Redeploy the worker unless one is draining or we are in cooldown.

        Returns True when a redeploy was fired. Never raises: a Railway or
        DB hiccup must not break enqueue - a missed signal is repaired by
        the boot re-check or the safety cron. A failing heartbeat check
        counts as "worker may be active": redeploying on bad information
        could kill a live drain, and the nets cover the missed launch.
        """
        async with self._lock:
            now = time.monotonic()
            if self._last_attempt is not None and now - self._last_attempt < self._config.cooldown_seconds:
                return False
            # Stamp before the checks so a slow/failing backend is not
            # hammered by every enqueue in a burst.
            self._last_attempt = now
            try:
                if self._worker_active is not None and await self._worker_active():
                    logger.info("worker launcher: skipped, a live worker heartbeat exists")
                    return False
                await self._redeploy()
                logger.info("worker launcher: redeploy fired")
                return True
            except Exception:
                logger.warning(
                    "worker launcher: failed; boot re-check or safety cron will drain the queue",
                    exc_info=True,
                )
                return False

    async def _redeploy(self) -> None:
        variables = {
            "serviceId": self._config.service_id,
            "environmentId": self._config.environment_id,
        }
        response = await self._client.post(
            self._config.api_url, json={"query": _REDEPLOY_MUTATION, "variables": variables}
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
    while letting a crashed worker's stale row unblock the launcher within
    seconds. Runs on the API's session factory - the launcher only fires
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
    the launcher's accepted races (missed signal, enqueue during worker exit)
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

    async def _retrigger_pending_jobs() -> None:
        from sqlalchemy import text

        now = time.monotonic()
        if _last_run and now - _last_run[0] < min_interval_seconds:
            return
        _last_run[:] = [now]

        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("SELECT count(*) FROM procrastinate_jobs WHERE status = 'todo'")
                )
                pending = result.scalar_one()
        except Exception:
            logger.warning("boot re-trigger: pending-job check failed", exc_info=True)
            return
        if pending:
            logger.info("boot re-trigger: %s pending job(s) - firing worker launcher", pending)
            launcher.schedule()

    return _retrigger_pending_jobs


def build_worker_launcher(
    worker_active: WorkerActiveCheck | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WorkerLauncher | None:
    """Select and build the launcher from ``SPLITSMITH_WORKER_LAUNCHER``
    (default ``railway``). ``None`` (launcher disabled) when the selected
    implementation's env config is absent; unknown names raise so a
    misconfigured hosted boot fails loudly instead of stranding jobs."""
    kind = os.environ.get(ENV_WORKER_LAUNCHER, "").strip().lower() or "railway"
    if kind == "railway":
        config = load_railway_config()
        return (
            RailwayWorkerLauncher(config, worker_active=worker_active, transport=transport)
            if config
            else None
        )
    raise ValueError(f"{ENV_WORKER_LAUNCHER}={kind!r}: unknown worker launcher")
