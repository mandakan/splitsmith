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

A drain can also end with work left: it spends its time budget (the task
timeout less a margin, ``queue.drain_budget_seconds``) or is killed, which
leaves its job ``doing`` until the next drain's stalled sweep fails it. So
every wake arms a watch that wakes again while work is outstanding and no
heartbeat is live (:meth:`WorkerDispatcher._watch`), and a backlog that
outgrows the live drains gets more Cloud Run executions, up to a cap
(:meth:`WorkerDispatcher._fan_out`).

Configuration comes from env vars (:func:`build_worker_dispatcher`);
``SPLITSMITH_WORKER_LAUNCHER=none`` disables the dispatcher entirely so
local / docker-compose deployments (always-on worker) skip the wake
machinery.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import math
import os
import time
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
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
#: Bucket key prefix of each box's heartbeat in "r2" mode; the worker id
#: follows. A box polling the marker never talks to serve, so this object is
#: its only sign of life. It goes to the bucket rather than through a serve
#: route because a request every few minutes would keep serve and its Neon
#: compute awake around the clock, which is what "r2" mode exists to avoid.
HEARTBEAT_KEY_PREFIX = "_splitsmith/heartbeat/"
#: How often a box rewrites its heartbeat (checked on each marker poll).
HEARTBEAT_SECONDS = 120.0
#: A heartbeat older than this, or none at all, means the box is gone: the
#: dispatcher skips its row and the admin view shows it offline. Five beats,
#: so a failed bucket write or two never hands a live box's jobs to the platform.
HEARTBEAT_STALE_SECONDS = 600.0


def heartbeat_key(worker_id: str) -> str:
    """The bucket key of ``worker_id``'s heartbeat."""
    return f"{HEARTBEAT_KEY_PREFIX}{worker_id}"


class Heartbeat(BaseModel):
    """A box's last heartbeat: the bucket's write time and the agent's version."""

    at: datetime
    version: str | None = None

    def fresh(self, now: datetime | None = None) -> bool:
        current = now if now is not None else datetime.now(UTC)
        return (current - self.at).total_seconds() <= HEARTBEAT_STALE_SECONDS


#: A ``doing`` job whose worker's heartbeat is older than this (or whose
#: worker row is gone) is orphaned: its worker was killed. Procrastinate
#: beats every 10 s and prunes a worker silent for 30 s at a worker's start.
STALLED_HEARTBEAT_SECONDS = 60.0
#: How often serve re-checks, after a wake, whether work is outstanding with
#: no live worker (an execution that exited on its budget, or was killed).
WATCH_SECONDS = 120.0
#: Re-wakes in a row that left the outstanding count no lower before the
#: watch gives up and leaves the rest to the nets.
WATCH_MAX_FRUITLESS_WAKES = 3
# Fan-out (#1671): the most Cloud Run executions a backlog may run at once
# (``SPLITSMITH_WORKER_MAX_EXECUTIONS``, default 4) and the jobs one
# execution is expected to clear in a few minutes
# (``SPLITSMITH_WORKER_JOBS_PER_EXECUTION``, default 2: a startup is 20 to
# 30 s, and a trim or detection job 10 to 100 s on 4 vCPU, so two jobs
# already pay for one). N executions bill about what one does for N times as
# long, plus a startup each.
ENV_WORKER_MAX_EXECUTIONS = "SPLITSMITH_WORKER_MAX_EXECUTIONS"
ENV_WORKER_JOBS_PER_EXECUTION = "SPLITSMITH_WORKER_JOBS_PER_EXECUTION"
DEFAULT_MAX_EXECUTIONS = 4
DEFAULT_JOBS_PER_EXECUTION = 2
#: How long a started execution counts as live before its heartbeat shows
#: (measured: 28 s from start to the first job).
STARTUP_GRACE_SECONDS = 120.0


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
    jobs. The cost is pickup latency up to the poll interval.

    Liveness comes from each box's heartbeat object (:meth:`beat`, written
    by the agent on its poll cycle; :meth:`read_heartbeats`, read by the
    dispatcher and the admin view). The marker still wakes every box that
    polls it; the dispatcher only decides whether a tier has a live box to
    count, and relies on the grace escalation if none drains.
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

    async def beat(self, worker_id: str, *, version: str) -> None:
        """Write ``worker_id``'s heartbeat; the bucket's write time is its time."""
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=heartbeat_key(worker_id),
            Body=version.encode(),
            ContentType="text/plain",
            Metadata={"version": version},
        )

    async def read_heartbeats(self, worker_ids: list[str]) -> dict[str, Heartbeat | None]:
        """Each worker's last heartbeat, ``None`` when it has none.

        A worker whose read failed is left out: not knowing is not evidence
        the box is dead.
        """

        def _head(worker_id: str) -> Heartbeat | None:
            from botocore.exceptions import ClientError

            try:
                head = self._client.head_object(Bucket=self._bucket, Key=heartbeat_key(worker_id))
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                    return None
                raise
            at = head["LastModified"]
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            return Heartbeat(at=at, version=(head.get("Metadata") or {}).get("version"))

        results = await asyncio.gather(
            *(asyncio.to_thread(_head, worker_id) for worker_id in worker_ids), return_exceptions=True
        )
        beats: dict[str, Heartbeat | None] = {}
        for worker_id, result in zip(worker_ids, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("heartbeat read for worker %s failed: %s", worker_id, result)
                continue
            beats[worker_id] = result
        return beats


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
LiveWorkersCount = Callable[[], Awaitable[int]]


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
        watch_seconds: float | None = WATCH_SECONDS,
        live_workers: LiveWorkersCount | None = None,
        max_executions: int = 1,
        jobs_per_execution: int = DEFAULT_JOBS_PER_EXECUTION,
        solo_jobs: PendingJobsCount | None = None,
    ) -> None:
        if max_executions < 1 or jobs_per_execution < 1:
            raise ValueError("max_executions and jobs_per_execution must be at least 1")
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
        # The after-wake watch (see :meth:`_watch`); None disables it.
        self._watch_seconds = watch_seconds
        self._watching = False
        # One check at the end of a cooldown that dropped a trigger (#1709).
        self._settling = False
        # Fan-out (#1671): how many Cloud Run executions a backlog may run at
        # once, and how many queued jobs one execution is expected to clear
        # in a few minutes. ``live_workers`` counts heartbeats; without it the
        # dispatcher never adds an execution beside a live drain.
        self._live_workers = live_workers
        self._max_executions = max_executions
        self._jobs_per_execution = jobs_per_execution
        # The queued jobs of a kind that wants a worker each
        # (``queue.SOLO_JOB_KINDS``): one stage of a compare grid (#1691).
        self._solo_jobs = solo_jobs
        # Executions still booting have no heartbeat yet; they are counted
        # through the platform row (``WorkersStore.claim_starts``), so every
        # serve instance sees every instance's starts.

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
                # The rest of a burst: counted once the cooldown ends (#1709).
                self._start_settle(self._last_attempt + self._cooldown_seconds - now)
                return False
            # Stamp before the checks so a slow/failing backend is not
            # hammered by every enqueue in a burst.
            self._last_attempt = now
            try:
                if self._worker_active is not None and await self._worker_active():
                    self._unanswered_since = None
                    # The live drain may chain jobs of its own (beep to trim,
                    # trim to shots) that never reach this dispatcher; the
                    # watch fans out for those (#1709).
                    self._start_watch()
                    if await self._fan_out():
                        return True
                    logger.info("worker dispatcher: skipped, a live worker heartbeat exists")
                    return False
                woke = await self._wake_from(max_priority_exclusive=None)
                if woke:
                    self._start_watch()
                return woke
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
        if self._wake_marker is not None:
            workers = await self._without_dead_boxes(workers)
        for priority, tier in itertools.groupby(workers, key=lambda w: w.priority):
            woken_ids: list[str] = []
            woke_self_hosted = False
            marker: bool | None = None  # touched once per tier, on first need
            for worker in tier:
                if worker.kind == "cloud_run":
                    if self._cloud_run is None:
                        continue
                    # No drain is live on this path: start as many as the
                    # backlog wants, up to the cap (#1671), less those still
                    # booting, and always one (a booting one may have died).
                    count = await self._claim(worker.id, live=0, at_least_one=True)
                    if await self._start_executions(worker.id, count) == 0:
                        continue
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

    async def _without_dead_boxes(self, workers: list[Any]) -> list[Any]:
        """Drop the self-hosted rows whose heartbeat is stale or missing ("r2" mode).

        A marker write wakes whatever box is polling, so a dead row cannot be
        told from a live one by the wake itself; without this a long-gone row
        at the top priority took every wake and the platform tier only ran
        after the grace window. An unreadable heartbeat keeps its row.
        """
        assert self._wake_marker is not None
        boxes = [w.id for w in workers if w.kind != "cloud_run"]
        if not boxes:
            return workers
        try:
            beats = await self._wake_marker.read_heartbeats(boxes)
        except Exception:
            logger.warning("worker dispatcher: heartbeat read failed; waking every box", exc_info=True)
            return workers
        now = datetime.now(UTC)
        live: list[Any] = []
        for worker in workers:
            if worker.id in beats:
                beat = beats[worker.id]
                if beat is None or not beat.fresh(now):
                    logger.info("worker dispatcher: skipping %s, no recent heartbeat", worker.name)
                    continue
            live.append(worker)
        return live

    async def _executions_wanted(self, live: int) -> int:
        """Cloud Run executions the backlog wants: one per
        ``jobs_per_execution`` jobs, at least one, at most ``max_executions``.
        One when fan-out is off or the count fails.

        The jobs are the queued ones plus one in flight per ``live`` drain
        (#1709): ``pending_jobs`` leaves out what a live drain is running, so
        a stage's chained trims (one running, four queued) read as a backlog
        the running drain already covers. A queued job of a solo kind (a
        compare grid's stage, minutes of encode) wants an execution of its
        own, so a grid's stages render in one round rather than two."""
        if self._max_executions == 1:
            return 1
        try:
            pending = await self._pending_jobs()
        except Exception:
            logger.warning("worker dispatcher: pending count failed; starting one execution", exc_info=True)
            return 1
        solo = 0
        if self._solo_jobs is not None:
            try:
                solo = max(0, min(pending, await self._solo_jobs()))
            except Exception:
                logger.warning(
                    "worker dispatcher: solo-job count failed; counting them as any job", exc_info=True
                )
        jobs = pending - solo + live
        return min(self._max_executions, max(1, solo + math.ceil(jobs / self._jobs_per_execution)))

    async def _claim(self, worker_id: str, *, live: int, at_least_one: bool) -> int:
        """How many executions to start: what the backlog wants, less the
        ``live`` drains and the ones booting on any serve instance
        (``WorkersStore.claim_starts``). A failed claim starts one on a cold
        wake (the wake must not be lost) and none beside a live drain."""
        wanted = await self._executions_wanted(live)
        try:
            return await self._store.claim_starts(
                worker_id,
                wanted=wanted,
                live=live,
                grace_seconds=STARTUP_GRACE_SECONDS,
                at_least_one=at_least_one,
            )
        except Exception:
            logger.warning("worker dispatcher: start claim failed", exc_info=True)
            return 1 if at_least_one else 0

    async def _start_executions(self, worker_id: str, count: int) -> int:
        """Start ``count`` claimed executions of the worker job; return how many started.

        Separate executions rather than one with several tasks: each drains
        and exits on its own, and Procrastinate's locking hands every job to
        one of them. Stops at the first refused start and releases the
        claims it did not use.
        """
        started = 0
        for _ in range(count):
            try:
                await self._run_job()
            except Exception:
                logger.warning("worker dispatcher: cloud run job start failed", exc_info=True)
                break
            started += 1
        if started < count:
            try:
                await self._store.release_starts(worker_id, count - started)
            except Exception:
                logger.warning("worker dispatcher: releasing unused start claims failed", exc_info=True)
        if started:
            logger.info("worker dispatcher: cloud run job started (%s execution(s))", started)
        return started

    async def _fan_out(self) -> bool:
        """Add Cloud Run executions beside a live drain when the backlog outgrows it (#1671).

        The live heartbeats plus executions started within
        :data:`STARTUP_GRACE_SECONDS` by any serve instance (booting, no
        heartbeat yet) are the capacity; a backlog that wants more than
        that, up to the cap, gets the difference. An execution counted both
        ways (started recently and already beating) only makes this start
        fewer. Off unless a cap above one, a live-worker count and the
        platform leg are all configured, and the platform row is enabled.
        Runs under the lock.
        """
        if self._max_executions == 1 or self._live_workers is None or self._cloud_run is None:
            return False
        platform = [w for w in await self._store.list_enabled() if w.kind == "cloud_run"]
        if not platform:
            return False
        live = await self._live_workers()
        extra = await self._claim(platform[0].id, live=live, at_least_one=False)
        if extra <= 0:
            return False
        logger.info("worker dispatcher: backlog outgrows %s live drain(s); adding %s", live, extra)
        if await self._start_executions(platform[0].id, extra) == 0:
            return False
        try:
            await self._store.touch_wake([w.id for w in platform])
        except Exception:
            logger.warning("worker dispatcher: touch_wake bookkeeping failed", exc_info=True)
        return True

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
                # Under the lock, like every wake: a watch re-wake or a
                # trigger running beside it would count the same booting
                # executions as missing and start them twice.
                await self._wake_from(max_priority_exclusive=woken_priority)
        except Exception:
            logger.warning("worker dispatcher: grace escalation failed; nets will recover", exc_info=True)

    async def _top_up(self) -> bool:
        """:meth:`_fan_out` beside a live drain, under the lock and the cooldown (#1709).

        A backlog a live worker builds itself (the jobs it chains) and the
        submits a cooldown dropped never reach :meth:`trigger`; the watch
        and the settle check call this for them. Only beside a live drain:
        with none, the cold wake's own tier walk decides who drains, and
        Cloud Run executions started here would jump a self-hosted box still
        booting. Stamps the cooldown only when it started an execution, so a
        no-op check never swallows a submit's trigger. Never raises.
        """
        async with self._lock:
            now = time.monotonic()
            if self._last_attempt is not None and now - self._last_attempt < self._cooldown_seconds:
                return False
            try:
                if self._worker_active is None or not await self._worker_active():
                    return False
                if not await self._fan_out():
                    return False
            except Exception:
                logger.warning("worker dispatcher: fan-out check failed; nets will recover", exc_info=True)
                return False
            self._last_attempt = now
            return True

    def _start_settle(self, delay: float) -> None:
        """Arm one :meth:`_top_up` for when the cooldown ends, unless one is armed."""
        if self._settling:
            return
        self._settling = self._spawn(self._settle(delay))

    async def _settle(self, delay: float) -> None:
        """The settle check: a burst's first submit counted one job; this counts the rest."""
        try:
            await asyncio.sleep(max(0.0, delay))
            self._settling = False
            await self._top_up()
        finally:
            self._settling = False

    def _start_watch(self) -> None:
        """Arm the after-wake watch unless one is already running."""
        if self._watch_seconds is None or self._watching:
            return
        self._watching = self._spawn(self._watch(self._watch_seconds))

    async def _watch(self, interval: float) -> None:
        """Wake again while work is outstanding and no worker is live (#1677),
        and fan out beside a live one when the backlog outgrows it (#1709).

        A drain ends with work left when it spends its time budget (#1684),
        and a killed one leaves its job ``doing`` with nothing to clear it;
        until now both waited for the next submit's wake. Every ``interval``
        this counts the outstanding jobs (``pending_jobs``: queued, plus
        ``doing`` ones whose worker is dead, which the next drain fails at
        its start) and, with no live heartbeat, triggers again. It ends
        when nothing is outstanding, or after
        :data:`WATCH_MAX_FRUITLESS_WAKES` re-wakes in a row left the count
        no lower (a queue no worker can clear must not start executions
        forever); the boot re-check and the safety cron stay the nets.

        It lives in serve's process like the grace escalation, so a serve
        that scales to zero mid-drain drops it; the next boot re-check
        counts the same outstanding work. Never raises.
        """
        fruitless = 0
        woke_at: int | None = None
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    outstanding = await self._pending_jobs()
                    if outstanding <= 0:
                        return
                    live = self._worker_active is not None and await self._worker_active()
                except Exception:
                    logger.warning("worker dispatcher: watch check failed; nets will recover", exc_info=True)
                    return
                if live:
                    # A busy drain with work queued behind it: add executions
                    # if the backlog outgrows it (#1709).
                    await self._top_up()
                    continue
                if woke_at is not None and outstanding >= woke_at:
                    fruitless += 1
                    if fruitless >= WATCH_MAX_FRUITLESS_WAKES:
                        logger.warning(
                            "worker dispatcher: %s job(s) outstanding after %s re-wakes made no progress; "
                            "leaving them to the boot re-check and the safety cron",
                            outstanding,
                            fruitless,
                        )
                        return
                else:
                    fruitless = 0
                logger.info(
                    "worker dispatcher: %s job(s) outstanding and no live worker - waking again", outstanding
                )
                woke_at = outstanding
                await self._rewake()
        finally:
            self._watching = False

    async def _rewake(self) -> None:
        """The watch's wake: :meth:`trigger` without re-arming the watch."""
        async with self._lock:
            now = time.monotonic()
            if self._last_attempt is not None and now - self._last_attempt < self._cooldown_seconds:
                # A trigger or an escalation just woke: it is answering this.
                return
            self._last_attempt = now
            try:
                await self._wake_from(max_priority_exclusive=None)
            except Exception:
                logger.warning("worker dispatcher: watch re-wake failed; nets will recover", exc_info=True)

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> bool:
        """Run ``coro`` as a GC-protected background task; never raises.

        Returns whether the task was created."""
        try:
            task = asyncio.get_running_loop().create_task(coro)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return True
        except Exception:
            coro.close()  # silence the "never awaited" warning
            logger.warning("worker dispatcher: schedule failed; nets will recover", exc_info=True)
            return False

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


def make_live_workers_counter(
    session_factory: Callable[[], Any], max_age_seconds: float = 20.0
) -> LiveWorkersCount:
    """Count Procrastinate workers whose heartbeat is fresher than
    ``max_age_seconds``: the same liveness as :func:`make_worker_active_checker`,
    counted for the dispatcher's fan-out."""

    async def _live_workers() -> int:
        from sqlalchemy import text

        async with session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT count(*) FROM procrastinate_workers "
                    "WHERE last_heartbeat > now() - make_interval(secs => :age)"
                ),
                {"age": max_age_seconds},
            )
            return int(result.scalar_one())

    return _live_workers


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name}={raw!r}: expected a whole number of at least 1")
    return value


def make_pending_jobs_counter(
    session_factory: Callable[[], Any], stalled_after_seconds: float = STALLED_HEARTBEAT_SECONDS
) -> PendingJobsCount:
    """Count Procrastinate jobs that need a worker to start.

    Queued (``todo``) jobs, plus ``doing`` jobs orphaned by a dead worker:
    no worker row (Procrastinate nulls ``worker_id`` when it prunes one) or
    a heartbeat older than ``stalled_after_seconds``. Procrastinate's own
    stalled-job query, counted. An orphan needs a wake too, since only a
    worker's start ends it (``queue.recover_stalled_jobs``), and until then
    its row reads ``running`` and blocks its stage (#1677).

    Shared by the serve-boot re-check (:func:`make_boot_retrigger`), the
    dispatcher's grace escalation and its after-wake watch - one definition
    of "pending" for all three.
    """

    async def _pending_jobs() -> int:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import DateTime, bindparam, text

        # The cutoff is computed here rather than as ``now() - interval`` so
        # the same statement runs on SQLite, where the test pins its
        # semantics; serve's and Neon's clocks agree far inside 60 s.
        cutoff = datetime.now(UTC) - timedelta(seconds=stalled_after_seconds)
        async with session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT count(*) FROM procrastinate_jobs job WHERE job.status = 'todo' "
                    "OR (job.status = 'doing' AND (job.worker_id IS NULL OR job.worker_id IN ("
                    "SELECT id FROM procrastinate_workers WHERE last_heartbeat < :cutoff)))"
                ).bindparams(bindparam("cutoff", type_=DateTime(timezone=True))),
                {"cutoff": cutoff},
            )
            return int(result.scalar_one())

    return _pending_jobs


def make_solo_jobs_counter(session_factory: Callable[[], Any]) -> PendingJobsCount:
    """Count queued jobs of a kind that wants an execution each
    (``queue.SOLO_JOB_KINDS``). The kind is in the task's args; they are
    read and matched here, not in SQL, so the statement runs on SQLite too,
    and the queue is short."""

    async def _solo_jobs() -> int:
        from sqlalchemy import text

        from .queue import SOLO_JOB_KINDS

        async with session_factory() as session:
            rows = await session.execute(text("SELECT args FROM procrastinate_jobs WHERE status = 'todo'"))
            count = 0
            for (args,) in rows:
                if isinstance(args, str):
                    args = json.loads(args)
                if isinstance(args, dict) and args.get("kind") in SOLO_JOB_KINDS:
                    count += 1
            return count

    return _solo_jobs


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
    live_workers: LiveWorkersCount | None = None,
    solo_jobs: PendingJobsCount | None = None,
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

    The fan-out cap and the jobs one execution is expected to clear come
    from ``SPLITSMITH_WORKER_MAX_EXECUTIONS`` and
    ``SPLITSMITH_WORKER_JOBS_PER_EXECUTION`` (#1671); fan-out needs
    ``live_workers``.
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
        live_workers=live_workers,
        max_executions=_positive_int_env(ENV_WORKER_MAX_EXECUTIONS, DEFAULT_MAX_EXECUTIONS),
        jobs_per_execution=_positive_int_env(ENV_WORKER_JOBS_PER_EXECUTION, DEFAULT_JOBS_PER_EXECUTION),
        solo_jobs=solo_jobs,
    )
