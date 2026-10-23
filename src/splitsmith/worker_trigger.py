"""Wake a worker when a job is enqueued (the wake channel).

The hosted worker runs ``splitsmith worker --one-shot``: it drains the
queue and exits, so no idle process holds a connection that keeps the
Neon compute awake. Something must therefore start it when work arrives.
The API process is the only enqueuer, so after a successful defer it
fires the :class:`WorkerDispatcher`.

The queue of record stays in Postgres (Procrastinate); a wake is only a
signal and must be safe to lose - the serve-boot pending re-check and
the 6-hourly safety-net workflow (it wakes serve) are the nets. The
dispatcher walks the registered workers (``workers`` table) in priority
tiers, descending (higher priority preferred, matching the
register-worker copy - so a registered home worker at the default 1000
outranks the platform fallback at 100):

- **self-hosted** workers are woken by pushing "wake" onto their live
  SSE channel (:class:`~splitsmith.worker_channel.WakeChannelRegistry`),
  where the push doubles as the availability check (a disconnected box
  simply is not there to wake), or, with ``SPLITSMITH_WAKE_CHANNEL=r2``, by
  one write of the bucket marker every box polls (:class:`WakeMarker`).
- **cloud_run** (the single ``kind='cloud_run'`` row) is woken by starting
  an execution of the Cloud Run Job named by ``SPLITSMITH_WORKER_JOB``.

The first tier with at least one successful wake wins; lower tiers stay
asleep. Because a self-hosted wake is best-effort (the box may be
connected but wedged), a successful self-hosted wake arms a grace timer:
if jobs are still pending and no worker heartbeat has appeared
``grace_seconds`` after the FIRST unanswered self-hosted wake, the
dispatcher escalates to the tiers below the one it woke - bypassing the
cooldown, since the escalation is part of the same logical trigger. A
later trigger that re-wakes the same dead tier does not move that
deadline; anchoring it to the latest attempt starved the queue for as
long as submits kept arriving faster than the grace window.

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

CLOUD_RUN_API_URL = "https://run.googleapis.com/v2"
# The metadata server hands a Cloud Run instance its service account's
# access token; no key file, no google-auth dependency.
GCP_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)

# Selects the dispatcher mode in build_worker_dispatcher(): unset builds a
# dispatcher whose platform leg is the Cloud Run Job when
# SPLITSMITH_WORKER_JOB names one (self-hosted workers only otherwise);
# "cloud_run" requires the job; "none" disables the wake machinery.
ENV_WORKER_LAUNCHER = "SPLITSMITH_WORKER_LAUNCHER"
# Full resource name of the worker job:
# ``projects/<project>/locations/<region>/jobs/<job>``.
ENV_WORKER_JOB = "SPLITSMITH_WORKER_JOB"
# How self-hosted workers are woken: "sse" (default; the box holds an SSE
# channel to serve) or "r2" (serve writes a marker object to the storage
# bucket and the box polls it, so nothing holds a connection to serve and
# a scale-to-zero host can sleep; see WakeMarker).
ENV_WAKE_CHANNEL = "SPLITSMITH_WAKE_CHANNEL"
#: Bucket key of the self-hosted wake marker. Outside ``users/`` so no
#: tenant key can collide with it.
WAKE_MARKER_KEY = "_splitsmith/wake/self-hosted"
#: How often a box polls the marker in "r2" mode.
WAKE_POLL_SECONDS = 30.0


class CloudRunJobConfig(BaseModel):
    """The Cloud Run Job that runs ``splitsmith worker --one-shot``."""

    job: str
    api_url: str = CLOUD_RUN_API_URL
    token_url: str = GCP_METADATA_TOKEN_URL


def load_cloud_run_config() -> CloudRunJobConfig | None:
    """Read the Cloud Run leg's config; ``None`` when no job is named."""
    job = os.environ.get(ENV_WORKER_JOB, "").strip()
    if not job:
        return None
    if not job.startswith("projects/") or "/jobs/" not in job:
        raise ValueError(f"{ENV_WORKER_JOB}={job!r}: expected projects/<p>/locations/<r>/jobs/<j>")
    return CloudRunJobConfig(job=job)


def platform_worker_kind() -> str | None:
    """The ``workers.kind`` of this deployment's platform worker, if any.

    serve seeds that row at boot so the dispatcher has a fallback below
    the self-hosted boxes.
    """
    if load_cloud_run_config() is not None:
        return "cloud_run"
    return None


def wake_channel() -> str:
    """``"sse"`` or ``"r2"`` (``SPLITSMITH_WAKE_CHANNEL``)."""
    value = os.environ.get(ENV_WAKE_CHANNEL, "").strip().lower() or "sse"
    if value not in ("sse", "r2"):
        raise ValueError(f"{ENV_WAKE_CHANNEL}={value!r}: expected 'sse' or 'r2'")
    return value


class WakeMarker:
    """The "r2" wake channel's server half: one object in the storage bucket.

    Waking the self-hosted boxes is a PUT of a fresh body, which changes the
    object's ETag; each box HEADs the key every ``WAKE_POLL_SECONDS`` and
    drains when the ETag moves (``agent._marker_poll_loop``). No connection
    to serve and no database read, so serve and Neon stay asleep between
    jobs. The cost is pickup latency up to the poll interval, and a box
    cannot be checked for liveness: the dispatcher counts the marker as a
    wake of every enabled self-hosted worker and relies on the grace
    escalation if none drains.
    """

    def __init__(self, client: Any, bucket: str, key: str = WAKE_MARKER_KEY) -> None:
        self._client = client
        self._bucket = bucket
        self._key = key

    async def touch(self) -> None:
        body = f"{time.time():.6f}".encode()
        await asyncio.to_thread(
            self._client.put_object, Bucket=self._bucket, Key=self._key, Body=body, ContentType="text/plain"
        )


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


class WorkerDispatcher:
    """Wakes the highest-priority available worker(s), at most once per
    cooldown window, and only when no live worker heartbeat exists.

    The platform's own run status cannot gate "is a drain running" (a
    finished one-shot run still reads as a success, and a box has no status
    at all). The authoritative signal is Procrastinate's own worker registry - ``worker_active`` (built by
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
        worker_active: WorkerActiveCheck | None,
        pending_jobs: PendingJobsCount,
        cooldown_seconds: float = 30.0,
        grace_seconds: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
        cloud_run: CloudRunJobConfig | None = None,
        wake_marker: WakeMarker | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._cloud_run = cloud_run
        self._wake_marker = wake_marker
        # The dispatcher owns an HTTP client only when it has a platform leg.
        self._gcp_client: httpx.AsyncClient | None = (
            httpx.AsyncClient(timeout=10.0, transport=transport) if cloud_run is not None else None
        )
        self._gcp_token: str | None = None
        self._gcp_token_expires = 0.0
        self._worker_active = worker_active
        self._pending_jobs = pending_jobs
        self._cooldown_seconds = cooldown_seconds
        self._grace_seconds = grace_seconds
        self._lock = asyncio.Lock()
        self._last_attempt: float | None = None
        # When the oldest self-hosted wake that no drain has answered yet
        # went out; the grace deadline counts from here. Reset when a drain
        # is seen, the queue is empty, or the dispatcher escalates.
        self._unanswered_since: float | None = None
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

        Returns True when a wake was fired. Never raises: a platform or
        DB hiccup must not break enqueue - a missed signal is repaired by
        the boot re-check or the safety cron. A failing heartbeat check
        counts as "worker may be active": a start on bad information
        would run a second drain beside a live one, and the nets cover the
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
                    self._unanswered_since = None
                    return False
                return await self._wake_from(max_priority_exclusive=None)
            except Exception:
                logger.warning(
                    "worker dispatcher: failed; boot re-check or safety cron will drain the queue",
                    exc_info=True,
                )
                return False

    async def _wake_from(self, max_priority_exclusive: int | None) -> bool:
        """Walk the enabled workers in descending priority tiers and wake the
        first tier with at least one reachable target. Higher priority is
        preferred (matches the register-worker copy).

        ``max_priority_exclusive`` skips tiers at or above that priority
        (used by the grace escalation to wake only the tiers below the one
        it already woke). A tier where every wake fails falls through to
        the next tier. Returns True when any wake was fired.
        """
        workers = await self._store.list_enabled()
        if max_priority_exclusive is not None:
            workers = [w for w in workers if w.priority < max_priority_exclusive]
        for priority, tier in itertools.groupby(workers, key=lambda w: w.priority):
            woken_ids: list[str] = []
            woke_self_hosted = False
            marker: bool | None = None  # touched once per tier, on first need
            for worker in tier:
                if worker.kind == "cloud_run":
                    if self._cloud_run is None:
                        continue
                    try:
                        await self._run_job()
                    except Exception:
                        logger.warning("worker dispatcher: cloud run job start failed", exc_info=True)
                        continue
                    logger.info("worker dispatcher: cloud run job started")
                    woken_ids.append(worker.id)
                elif self._wake_marker is not None:
                    # "r2" channel: one marker write wakes every box polling
                    # it. Unlike an SSE push it cannot tell whether a box is
                    # there, so the grace escalation below is the net.
                    if marker is None:
                        try:
                            await self._wake_marker.touch()
                            marker = True
                            logger.info("worker dispatcher: wake marker written")
                        except Exception:
                            marker = False
                            logger.warning("worker dispatcher: wake marker write failed", exc_info=True)
                    if marker:
                        woken_ids.append(worker.id)
                        woke_self_hosted = True
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
                    # be wedged, a marker reader may be gone); a platform-only
                    # wake needs no faster net than the existing ones.
                    now = time.monotonic()
                    if self._unanswered_since is None:
                        self._unanswered_since = now
                    anchor = self._unanswered_since
                    delay = max(0.0, self._grace_seconds - (now - anchor))
                    self._spawn(self._grace_escalate(priority, anchor=anchor, delay=delay))
                return True
        return False

    async def _grace_escalate(self, woken_priority: int, *, anchor: float, delay: float) -> None:
        """After ``delay`` (the rest of the grace window counted from
        ``anchor``, the first unanswered wake): if jobs are still pending and
        no worker heartbeat appeared, wake the tiers below the one woken.

        Every re-wake of a dead tier spawns one of these with the same
        ``anchor``; the first to get here escalates and clears
        ``_unanswered_since`` under ``self._lock``, so the others (and any
        whose anchor a seen drain or an empty queue cleared) stand down.
        That keeps overlapping windows from starting the platform worker
        twice, while the escalation still bypasses the cooldown.

        Never raises: it runs as a detached task and a failure is
        covered by the nets. A failing heartbeat check aborts the
        escalation (same fail-safe as :meth:`trigger`).
        """
        try:
            await asyncio.sleep(delay)
            answered = await self._pending_jobs() <= 0 or (
                self._worker_active is not None and await self._worker_active()
            )
            async with self._lock:
                if self._unanswered_since != anchor:
                    return
                self._unanswered_since = None
                if answered:
                    return
                self._last_attempt = time.monotonic()
            logger.info(
                "worker dispatcher: no drain %.0fs after wake - escalating past priority %s",
                self._grace_seconds,
                woken_priority,
            )
            await self._wake_from(max_priority_exclusive=woken_priority)
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

    async def _gcp_access_token(self) -> str:
        """The service account's access token, cached until a minute before expiry."""
        assert self._cloud_run is not None and self._gcp_client is not None
        if self._gcp_token is not None and time.monotonic() < self._gcp_token_expires:
            return self._gcp_token
        response = await self._gcp_client.get(
            self._cloud_run.token_url, headers={"Metadata-Flavor": "Google"}
        )
        response.raise_for_status()
        payload = response.json()
        self._gcp_token = str(payload["access_token"])
        self._gcp_token_expires = time.monotonic() + max(0.0, float(payload.get("expires_in", 0)) - 60.0)
        return self._gcp_token

    async def _run_job(self) -> None:
        """Start one execution of the worker job (``jobs.run``).

        A second execution while one runs is harmless: both drain the same
        Postgres queue and Procrastinate hands each job to one of them. The
        heartbeat gate in :meth:`trigger` keeps that rare.
        """
        assert self._cloud_run is not None and self._gcp_client is not None
        token = await self._gcp_access_token()
        response = await self._gcp_client.post(
            f"{self._cloud_run.api_url}/{self._cloud_run.job}:run",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        response.raise_for_status()


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

    serve scales to zero and cold-starts on a visit, so this runs on
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
    wake_marker: WakeMarker | None = None,
) -> WorkerDispatcher | None:
    """Build the dispatcher from ``SPLITSMITH_WORKER_LAUNCHER``.

    Unset builds a dispatcher whose platform leg is the Cloud Run Job named
    by ``SPLITSMITH_WORKER_JOB`` when that is set, and wakes self-hosted
    workers only otherwise. ``cloud_run`` is the same but requires the job.
    ``none`` disables the wake machinery (returns None) for deployments with
    an always-on worker. Unknown names (``railway`` among them, since its
    leg is gone) raise, so a misconfigured hosted boot fails loudly instead
    of stranding jobs.

    ``wake_marker`` switches self-hosted wakes from SSE pushes to the
    storage-bucket marker (``SPLITSMITH_WAKE_CHANNEL=r2``).
    """
    kind = os.environ.get(ENV_WORKER_LAUNCHER, "").strip().lower()
    if kind == "none":
        return None
    if kind not in ("", "cloud_run"):
        raise ValueError(f"{ENV_WORKER_LAUNCHER}={kind!r}: unknown worker launcher")
    cloud_run = load_cloud_run_config()
    if kind == "cloud_run" and cloud_run is None:
        raise ValueError(f"{ENV_WORKER_LAUNCHER}=cloud_run needs {ENV_WORKER_JOB}")
    return WorkerDispatcher(
        store,
        registry,
        cloud_run=cloud_run,
        wake_marker=wake_marker,
        worker_active=worker_active,
        pending_jobs=pending_jobs,
        transport=transport,
    )
