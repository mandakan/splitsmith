"""Procrastinate-backed job queue (hosted mode).

The Postgres-native dispatch layer that backs the worker fleet
(see ``docs/saas-readiness/04-compute-backends.md``). Today's
``PostgresJobBackend`` persists ``compute_jobs`` rows but drives
them on an in-process :class:`~concurrent.futures.ThreadPoolExecutor`
inside ``splitsmith serve``; this module is the queue layer that
lets the dispatch step move out-of-process so a separate
``splitsmith worker`` fleet can pop jobs.

PR-alpha landed the schema + the configured :class:`procrastinate.App`.
PR-beta (this change) adds the long-lived worker entrypoint
(:func:`run_worker`, exposed as ``splitsmith worker``) plus a single
state-free ``ping`` task whose only job is to prove the round-trip:
enqueue from the API process, dispatch + execute in a separate worker
process. The real job-kind tasks (``shot_detect``, ``beep_detect``,
``trim``, ...) land in PR-gamma, where each job kind gets split into a
pure ``*_body`` function and a thin task wrapper that rebuilds per-user
state from ``user_id`` + project_id alone.

## Local-mode safety

Importing this module pulls in ``procrastinate`` + ``psycopg``,
which are ``[hosted]``-extra dependencies. Local desktop
(``splitsmith ui``) must therefore never import this module --
that's why the import lives behind ``_hosted_mode_active()`` in
``splitsmith.ui.server._apply_hosted_mode_wiring``, alongside the
existing lazy imports of ``splitsmith.db.*``.

## Per-tenant queues

Each user's tasks land on a queue named ``user-<id>``. The
convention exists so future worker pools can be pinned to specific
tenants (premium-tier worker fleet vs. shared free-tier pool), and
so per-tenant rate-limiting becomes a queue-level concern instead
of a per-task one. For now, a single worker can subscribe to all
``user-*`` queues with one glob -- the cost of the convention
today is zero.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import re
import threading
import time
import weakref
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import procrastinate
import psycopg
import psycopg_pool
from procrastinate.jobs import Status as ProcrastinateStatus

from .observability import init_sentry
from .worker_trigger import STALLED_HEARTBEAT_SECONDS

logger = logging.getLogger(__name__)

# asyncpg-only SQLAlchemy URL query params that libpq / psycopg3 does not
# understand and rejects with "invalid URI query parameter". They're
# meaningful only to the SQLAlchemy asyncpg dialect, so we drop them when
# building the psycopg DSN for Procrastinate.
_ASYNCPG_ONLY_QUERY_KEYS = frozenset(
    {
        "prepared_statement_cache_size",
        "statement_cache_size",
        "prepared_statement_name_func",
    }
)

# Procrastinate task name for the unified compute-job dispatcher. The
# API enqueues it via :func:`make_deferrer` (``configure_task`` -- no
# local registration needed); the worker registers the real
# implementation via :func:`register_compute_task`.
RUN_COMPUTE_JOB_TASK = "run_compute_job"

#: Job kinds nobody is waiting on, deferred below every other kind (#1670).
#: ``generate_proxy`` runs on every raw upload and only serves Ingest's
#: scrubber; queued in arrival order it held beep detection, trims and shot
#: detection behind about 90 s of transcode per clip. Every worker fetches by
#: ``priority DESC, id ASC`` across all the queues it serves, so these still
#: drain, in arrival order, once nothing else is waiting.
BACKGROUND_JOB_KINDS = frozenset({"generate_proxy"})
BACKGROUND_JOB_PRIORITY = -100


def job_priority(kind: str) -> int:
    """The Procrastinate priority a job of ``kind`` is deferred with."""
    return BACKGROUND_JOB_PRIORITY if kind in BACKGROUND_JOB_KINDS else 0


#: The worker job's task timeout as deployed (``--task-timeout``), in
#: seconds. Cloud Run kills a task at it, mid-job or not, and exposes it
#: nowhere a process can read, so the deploy states it beside the flag
#: (like the size, ``observability.WORKER_VCPU_ENV``). Unset: no deadline.
WORKER_TASK_TIMEOUT_ENV = "SPLITSMITH_WORKER_TASK_TIMEOUT_S"
#: How long before that timeout a one-shot drain stops claiming jobs: room
#: for the longest job kind to finish after the last claim.
WORKER_DRAIN_MARGIN_ENV = "SPLITSMITH_WORKER_DRAIN_MARGIN_S"
DEFAULT_DRAIN_MARGIN_SECONDS = 900.0


def drain_budget_seconds() -> float | None:
    """Seconds a one-shot drain may keep claiming jobs, counted from its start.

    The deployed task timeout less the margin; ``None`` without a stated
    timeout (the agent, a local run). A bad value never stops the drain,
    since an execution that exits on a config error drains and sweeps
    nothing: an unreadable or non-positive timeout logs a warning and drains
    with no deadline, and an unreadable margin, or one that leaves no budget,
    logs a warning and falls back to the default margin (no deadline if that
    leaves none either).
    """
    raw = os.environ.get(WORKER_TASK_TIMEOUT_ENV, "").strip()
    if not raw:
        return None
    timeout = _finite_float(raw)
    if timeout is None:
        # A zero or negative one falls through: no margin leaves a budget.
        logger.warning(
            "%s=%r is not a number of seconds; draining with no deadline",
            WORKER_TASK_TIMEOUT_ENV,
            raw,
        )
        return None
    raw_margin = os.environ.get(WORKER_DRAIN_MARGIN_ENV, "").strip()
    margin = _finite_float(raw_margin) if raw_margin else DEFAULT_DRAIN_MARGIN_SECONDS
    if margin is None or not 0 <= margin < timeout:
        logger.warning(
            "%s=%r does not leave a budget below %s=%g; using the default %gs",
            WORKER_DRAIN_MARGIN_ENV,
            raw_margin,
            WORKER_TASK_TIMEOUT_ENV,
            timeout,
            DEFAULT_DRAIN_MARGIN_SECONDS,
        )
        margin = DEFAULT_DRAIN_MARGIN_SECONDS
        if margin >= timeout:
            return None
    return timeout - margin


def _finite_float(raw: str) -> float | None:
    """``raw`` as a finite float, or None (garbage, nan, inf)."""
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _to_psycopg_dsn(sqlalchemy_url: str) -> str:
    """Translate a SQLAlchemy async URL to a psycopg3 DSN.

    Procrastinate's :class:`PsycopgConnector` talks to psycopg3
    directly, not through SQLAlchemy, so it wants a bare libpq DSN
    (``postgresql://user:pass@host:5432/db``) -- no ``+asyncpg``
    dialect suffix. We strip the suffix so callers can keep passing
    the same ``SPLITSMITH_DATABASE_URL`` they already pass to
    SQLAlchemy.

    The query string also has to be reconciled: the SQLAlchemy asyncpg
    URL carries asyncpg-flavoured params that libpq rejects. We map
    ``ssl=<mode>`` -> ``sslmode=<mode>`` (asyncpg's SSL knob vs libpq's)
    and drop asyncpg-only keys like ``prepared_statement_cache_size``.
    Without this, a Neon URL (``?ssl=require&prepared_statement_cache_size=0``)
    makes psycopg raise ``invalid URI query parameter: "ssl"`` and the
    worker can't connect.

    SQLite URLs raise: Procrastinate is Postgres-only, and the
    alembic migration that lands its schema is a no-op on SQLite.
    Callers must not construct the App against a SQLite URL.
    """
    if sqlalchemy_url.startswith("sqlite"):
        raise ValueError(
            "Procrastinate requires Postgres; got a SQLite URL. "
            "SQLite-backed dev/smoke runs don't exercise the queue."
        )
    # ``postgresql+asyncpg://...`` -> ``postgresql://...``
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", sqlalchemy_url)
    parts = urlsplit(url)
    if not parts.query:
        return url
    translated: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "ssl":
            translated.append(("sslmode", value))
        elif key in _ASYNCPG_ONLY_QUERY_KEYS:
            continue
        else:
            translated.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(translated)))


def build_app(database_url: str, *, application_name: str | None = None) -> procrastinate.App:
    """Build the configured :class:`procrastinate.App`.

    ``database_url`` is the same ``SPLITSMITH_DATABASE_URL`` value
    consumed by SQLAlchemy elsewhere. The async/asyncpg dialect
    suffix is stripped here so psycopg3 can parse it.

    The returned App has the ``ping`` smoke task registered (see
    :func:`_register_smoke_tasks`). The real job-kind tasks
    (``shot_detect`` / ``beep_detect`` / ``trim``) are still PR-gamma:
    each gets a thin task wrapper around a pure ``*_body`` function.
    """
    dsn = _to_psycopg_dsn(database_url)
    # PsycopgConnector forwards every extra kwarg to its pool factory
    # (psycopg_pool.AsyncConnectionPool), whose first arg is ``conninfo``.
    # Passing it directly is correct; wrapping it as ``kwargs={"conninfo":
    # dsn}`` instead lands in the pool's *per-connection* ``kwargs`` and
    # collides with the pool's own ``conninfo`` ("got multiple values for
    # argument 'conninfo'") -- a runtime-only failure the unit tests miss.
    #
    # ``min_size=1``: Procrastinate opens the pool with ``open(wait=True)``,
    # which blocks until ``min_size`` connections are established or its 30s
    # default elapses. psycopg_pool defaults ``min_size`` to 4. When the
    # worker runs as a short-lived cron drain (``worker --one-shot``) it
    # cold-connects to a scale-to-zero Neon compute -- its OWN first query is
    # this pool open (the worker-state wiring issues none). Filling four SSL
    # connections through Neon's pooler while the compute resumes overruns the
    # 30s budget and the run dies with ``PoolTimeout: pool initialization
    # incomplete after 30.0 sec``. Requiring a single connection at open lets
    # one land inside the window while the compute wakes; ``max_size`` keeps
    # the same runtime capacity for the drain. The always-on worker never hit
    # this because it kept the compute warm.
    #
    # ``application_name`` (#1198) names the pool's connections in
    # ``pg_stat_activity``; ``kwargs`` is the per-connection keyword set
    # psycopg merges into the conninfo.
    extra: dict[str, Any] = {"kwargs": {"application_name": application_name}} if application_name else {}
    connector = procrastinate.PsycopgConnector(conninfo=dsn, min_size=1, max_size=4, **extra)
    app = procrastinate.App(connector=connector)
    _register_smoke_tasks(app)
    return app


def _register_smoke_tasks(app: procrastinate.App) -> None:
    """Register the worker-fleet smoke task(s) on ``app``.

    ``ping`` carries no per-user state and touches no project files.
    Its sole purpose is the PR-beta round-trip proof: the API process
    defers ``ping`` onto a queue, and a separate ``splitsmith worker``
    process pops + runs it. Real job kinds arrive in PR-gamma.

    Tasks must be registered on the App instance before
    :meth:`procrastinate.App.run_worker_async`, so this runs inside
    :func:`build_app` rather than at import time (the module must stay
    lazy-importable behind ``_hosted_mode_active()``).
    """

    @app.task(name="ping", queue="default")
    async def _ping(*, payload: str = "ping") -> str:
        return payload


class LoopRegistry(Protocol):
    """What the deferrer needs from :class:`splitsmith.db.engine.LoopEngines`:
    which loops are long-lived, and a hook into their shutdown."""

    def is_current_loop_adopted(self) -> bool: ...

    def on_loop_close(self, closer: Callable[[], Awaitable[None]]) -> None: ...


class _LoopApp:
    """One long-lived loop's Procrastinate App: opened once, under ``lock``."""

    __slots__ = ("app", "closed", "lock")

    def __init__(self) -> None:
        self.app: procrastinate.App | None = None
        self.closed = False
        self.lock = asyncio.Lock()


class LoopDeferrers:
    """Procrastinate Apps for one database URL, one kept open per long-lived loop (#1199).

    A psycopg :class:`~psycopg_pool.AsyncConnectionPool` is bound to the loop
    that opened it, exactly like an asyncpg connection (#423), so an open App
    may only serve its own loop. On a loop the :class:`LoopRegistry` reports
    adopted (the app loop, the worker loop, the ``DbRunner`` loop), the first
    defer opens an App and every later defer on that loop reuses it; the
    registry's shutdown hook closes it. Any other loop (a throwaway
    ``asyncio.run``, the re-entrant ``run_sync`` fallback, or every loop when
    there is no registry) opens a fresh App for the one defer and closes it
    again, which is what every defer did before.

    The open pool makes no traffic of its own while idle: ``min_size=1`` is
    the floor, it has no keepalive, and Procrastinate passes psycopg_pool's
    ``check_connection``, so a connection the server closed while idle
    (Neon after five minutes) is found and replaced at the next checkout.
    The pool's only timer is the ``max_idle`` shrink, which closes a
    connection above ``min_size`` that went unused and never opens one.

    Process-scoped per URL (:func:`loop_deferrers`): the self-hosted agent
    re-wires hosted mode once per drain, and a fresh object per wiring would
    leave the previous one's App open on the shared ``DbRunner`` loop.
    """

    def __init__(self, database_url: str, *, application_name: str | None = None) -> None:
        self._database_url = database_url
        self._application_name = application_name
        self._by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopApp] = (
            weakref.WeakKeyDictionary()
        )
        # The map is shared by the threads the loops run on.
        self._map_lock = threading.Lock()

    @property
    def open_count(self) -> int:
        return sum(1 for entry in list(self._by_loop.values()) if entry.app is not None)

    async def defer(self, registry: LoopRegistry | None, **kwargs: Any) -> None:
        """Defer :data:`RUN_COMPUTE_JOB_TASK` with ``kwargs`` from the running loop."""
        if registry is not None and registry.is_current_loop_adopted():
            app = await self._open_for_current_loop(registry)
            if app is not None:
                await _defer_on(app, **kwargs)
                return
        app = build_app(self._database_url, application_name=self._application_name)
        await app.connector.open_async()
        try:
            await _defer_on(app, **kwargs)
        finally:
            await app.connector.close_async()

    async def _open_for_current_loop(self, registry: LoopRegistry) -> procrastinate.App | None:
        """The running loop's open App, opened on first use; None once the
        loop's resources closed under a defer that was waiting for the open
        (the caller then defers through a one-off App)."""
        loop = asyncio.get_running_loop()
        with self._map_lock:
            entry = self._by_loop.get(loop)
            created = entry is None
            if entry is None:
                entry = _LoopApp()
                self._by_loop[loop] = entry
        if created:
            registry.on_loop_close(lambda: self._close_loop(loop, entry))
        if entry.app is not None:
            return entry.app
        async with entry.lock:
            if entry.closed:
                return None
            if entry.app is None:
                # A failed open leaves the pool closed for good (see
                # ``_open_app_with_retry``), so each attempt builds a fresh App
                # and nothing is cached until one opens.
                app = build_app(self._database_url, application_name=self._application_name)
                try:
                    await app.connector.open_async()
                except BaseException:
                    with contextlib.suppress(Exception):
                        await app.connector.close_async()
                    raise
                entry.app = app
            return entry.app

    async def _close_loop(self, loop: asyncio.AbstractEventLoop, entry: _LoopApp) -> None:
        with self._map_lock:
            if self._by_loop.get(loop) is entry:
                del self._by_loop[loop]
        entry.closed = True
        app, entry.app = entry.app, None
        if app is None:
            return
        try:
            await app.connector.close_async()
        except asyncio.CancelledError:
            # A pool whose worker tasks were cancelled re-raises their
            # cancellation from ``close``. Only swallow it when this task is
            # not itself being cancelled.
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            logger.warning("queue deferrer pool close was interrupted by cancelled pool tasks")


_process_deferrers: dict[str, LoopDeferrers] = {}
_process_deferrers_lock = threading.Lock()


def loop_deferrers(database_url: str, *, application_name: str | None = None) -> LoopDeferrers:
    """The process's :class:`LoopDeferrers` for ``database_url``, created on
    first use; ``application_name`` applies when it is created."""
    with _process_deferrers_lock:
        deferrers = _process_deferrers.get(database_url)
        if deferrers is None:
            deferrers = LoopDeferrers(database_url, application_name=application_name)
            _process_deferrers[database_url] = deferrers
        return deferrers


async def _defer_on(
    app: procrastinate.App,
    *,
    job_id: str,
    user_id: str,
    kind: str,
    args: dict[str, Any],
    match_id: str | None,
) -> None:
    await app.configure_task(
        name=RUN_COMPUTE_JOB_TASK, queue=queue_name_for_user(user_id), priority=job_priority(kind)
    ).defer_async(
        job_id=job_id,
        user_id=user_id,
        kind=kind,
        args=args,
        match_id=match_id,
    )


def make_deferrer(
    database_url: str, *, application_name: str | None = None, loops: LoopRegistry | None = None
) -> Callable[..., Awaitable[None]]:
    """Build the enqueue coroutine injected into :class:`PostgresJobBackend`.

    The returned ``deferrer(job_id, user_id, kind, args, match_id)``
    enqueues a :data:`RUN_COMPUTE_JOB_TASK` job onto the user's queue.
    ``configure_task`` defers a task by name without registering its
    body locally -- the API process only enqueues; the worker owns the
    implementation. No App is built until a job is submitted, so merely
    wiring hosted mode (e.g. against SQLite, where the queue is unused)
    never touches Postgres; a SQLite URL raises only if a job is actually
    submitted.

    ``loops`` is the process's :class:`~splitsmith.db.engine.LoopEngines`:
    a defer on a loop it adopted reuses that loop's open App (#1199); see
    :class:`LoopDeferrers`. Without it every defer opens and closes its own.
    """
    deferrers = loop_deferrers(database_url, application_name=application_name)

    async def _defer(
        *,
        job_id: str,
        user_id: str,
        kind: str,
        args: dict[str, Any],
        match_id: str | None,
    ) -> None:
        # Validate before any connection is opened.
        queue_name_for_user(user_id)
        await deferrers.defer(loops, job_id=job_id, user_id=user_id, kind=kind, args=args, match_id=match_id)

    return _defer


def register_compute_task(
    app: procrastinate.App,
    state: Any,
    *,
    after_job: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Register the worker-side :data:`RUN_COMPUTE_JOB_TASK` on ``app``.

    Bound to a worker ``state`` (built by
    ``splitsmith.ui.server.build_worker_state``). The task re-sets the
    ``current_match_*`` ContextVars from the queued ``match_id``,
    rehydrates any Pydantic ``req`` in ``args``, then drives the shared
    body via :meth:`PostgresJobBackend.run_job` -- the same ``kind`` ->
    body mapping the local registry uses. Must be registered before
    :meth:`procrastinate.App.run_worker_async`.

    ``after_job`` runs once each job ends, however it ended (the Cloud Run
    worker's cache sweep, see :func:`_job_sweep`); it must not raise.
    """
    from . import looks
    from .match_registry import MatchNotRegisteredError
    from .ui.context import current_match_origin
    from .ui.server import current_match_id, current_match_root, current_tenant, tenant_looks_provider

    @app.task(name=RUN_COMPUTE_JOB_TASK, queue="default")
    async def _run_compute_job(
        *,
        job_id: str,
        user_id: str,
        kind: str,
        args: dict[str, Any] | None = None,
        match_id: str | None = None,
    ) -> None:
        call_args = _rehydrate_args(kind, args or {})

        # Build this job's tenant context from the queued ``user_id`` and
        # pin it for the lifetime of the run. ``state.jobs`` / ``state.storage``
        # / ``state.matches_store`` are tenant-resolving properties, so they
        # must see ``current_tenant`` before any of them is touched -- the
        # very next line reads ``state.jobs``. ``run_job`` offloads the body
        # via ``asyncio.to_thread``, which copies this context, so the GUC the
        # tenant session factory sets (and the per-user S3 prefix) follow the
        # body onto the worker thread. ``user_id`` is required: there is no
        # fallback to the ``user-<id>`` queue name (a queued job always
        # carries its owner; a missing one is a wire-format bug, not a
        # recoverable state).
        tenant = state.build_tenant(user_id)
        tenant_token = current_tenant.set(tenant)
        looks_token = looks.set_user_looks_provider(tenant_looks_provider(tenant))

        # The match's origin, read from its row the way the alias middleware
        # reads it for a request. A body that asks ``_is_mirror()`` (the
        # audit audio job's web-rendition fallback) gets the same answer on
        # the worker as it would on the API; without this every match looked
        # hosted-native here and a mirror's audio job went looking for a raw
        # source no mirror has.
        match_origin: str | None = None
        if match_id is not None:
            row = await state.matches_store.get(match_id)
            match_origin = getattr(row, "origin", None)

        def _bind_match() -> None:
            """Re-set the match ContextVars from the queued ``match_id``.

            Runs on the worker thread inside ``run_job``'s failure
            capture, so an unresolvable match fails the job cleanly
            instead of stranding its row at PENDING. The worker resolves
            a match through its *local* recent-projects list; a separate
            worker process has none of the API user's matches, so every
            match-carrying kind fails here today. Only ``model_download``
            (no ``match_id``) is proven end-to-end on the worker -- making
            cross-process match metadata reachable is a later chunk.
            """
            if match_id is None:
                return
            try:
                root = state.matches.resolve(match_id)
            except MatchNotRegisteredError as exc:
                raise RuntimeError(
                    f"hosted worker cannot resolve match {match_id!r}: it is not in "
                    f"this user's matches table (or its metadata is unreachable). "
                    "Match-less kinds (e.g. model_download) need no resolution."
                ) from exc
            current_match_root.set(root)
            current_match_id.set(match_id)
            current_match_origin.set(match_origin)

        try:
            await state.jobs.run_job(job_id=job_id, kind=kind, args=call_args, before_body=_bind_match)
        finally:
            looks.reset_user_looks_provider(looks_token)
            current_tenant.reset(tenant_token)
            if after_job is not None:
                await after_job()


def _rehydrate_args(kind: str, args: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the typed ``req`` Pydantic model dropped to a dict by
    :func:`splitsmith.ui.job_journal.to_wire_args` for the queue.

    Delegates to the shared :func:`~splitsmith.ui.job_journal.rehydrate_args`
    (also used by the local crash-recovery resume, issue #665) so the
    wire-args projection has one source of truth. Imported lazily to keep
    this module's import graph unchanged."""
    from .ui.job_journal import rehydrate_args

    return rehydrate_args(kind, args)


def _attach_procrastinate_logging() -> None:
    """Share splitsmith's stdout handler with the ``procrastinate`` logger.

    ``_configure_app_logging`` wires only the ``splitsmith`` package logger;
    procrastinate's records propagate to a bare root logger left at WARNING,
    so a deployed worker's drain lifecycle ("Starting worker", "No job found.
    Stopping worker because wait=False") is invisible - a silent clean exit
    is indistinguishable from a hang in the platform's logs (observed live,
    2026-07-03). Reuses the handler objects marked ``_splitsmith_stdout`` so
    hosted-mode JSON formatting stays consistent. Idempotent.
    """
    pkg_logger = logging.getLogger("splitsmith")
    proc_logger = logging.getLogger("procrastinate")
    for handler in pkg_logger.handlers:
        if getattr(handler, "_splitsmith_stdout", False) and handler not in proc_logger.handlers:
            proc_logger.addHandler(handler)
    if proc_logger.level == logging.NOTSET or proc_logger.level > logging.INFO:
        proc_logger.setLevel(logging.INFO)


async def run_worker(
    database_url: str,
    *,
    concurrency: int = 1,
    queues: list[str] | None = None,
    wait: bool = True,
    stop_event: asyncio.Event | None = None,
    process_name: str = "splitsmith-worker",
    deadline: float | None = None,
) -> None:
    """Run a worker that drains the job queue.

    ``wait=True`` (the default) is the long-lived fleet worker: it blocks
    on LISTEN/NOTIFY and keeps draining new jobs until killed. ``wait=False``
    is the one-shot/cron drain: it processes the jobs already queued and then
    returns. The one-shot mode is what lets the Postgres compute scale to
    zero between runs -- an always-connected worker polls the queue often
    enough that Neon's idle-suspend window never opens, so a periodic
    ``wait=False`` drain (the Cloud Run Job) trades a little pickup latency for an
    idle DB that actually suspends. See ``splitsmith worker --one-shot``.

    ``queues=None`` (the default) subscribes to **every** queue --
    Procrastinate has no queue-glob, so one worker covers all
    ``user-*`` queues this way. Per-tenant pinning means passing an
    explicit ``queues=[...]`` list; that's a future concern, not
    wired here.

    A persistent worker is the whole point of the fleet: it loads the
    ensemble models once (the process-wide ``_ENSEMBLE_RUNTIME``
    singleton, on the first detection) and drains many jobs. Building
    the worker ``state`` wires the per-user ``PostgresJobBackend``
    (execute-only, no boot sweep) + storage + the shared job bodies.

    Installs SIGINT/SIGTERM handlers (Procrastinate default) for
    graceful drain; must therefore run on the main thread, which it
    does via ``asyncio.run`` from the ``splitsmith worker`` command.

    ``stop_event`` is for a caller that owns the signals itself (the
    self-hosted agent, #1033): Procrastinate then installs none, and
    setting the event is the graceful stop instead -- no new job is
    taken, the one in flight finishes, and this returns. Procrastinate's
    own handler cannot serve that caller: it restores the previous
    handler but never chains to it, so a SIGTERM taken mid-drain would
    never reach the agent's loop.

    ``process_name`` prefixes the ``application_name`` of every database
    connection the worker opens (#1198); the self-hosted agent passes
    ``splitsmith-agent``.

    ``deadline`` (a ``time.monotonic()`` instant) is when the drain stops
    claiming jobs: the job in flight finishes, and this returns with any
    others left queued for the next wake (#1684). The Cloud Run worker sets
    it from :func:`drain_budget_seconds` so the task timeout never kills a
    job part-way.

    ``build_worker_state`` runs its own ``asyncio.run`` calls (the auth
    bootstrap upserts the user row synchronously), so it can't run inside
    this coroutine's event loop -- it's offloaded to a worker thread,
    which has no running loop of its own.

    Sentry is initialised first (no-op unless ``SENTRY_DSN`` is set). After
    the state is built, ``_configure_app_logging`` attaches the same stdout
    handler the web process uses -- in hosted mode that is the structured
    JSON formatter, so worker ``job.completed`` / ``job.failed`` events are
    captured by the platform the same way. Finally the ensemble singleton is
    warmed on a worker thread so the first ``shot_detect`` does not pay a
    hidden cold model load; a warmup failure is logged and swallowed so the
    worker still drains non-shot_detect jobs (the cold cost is then timed as
    the ``cold_model_load`` phase on the first detection).
    """
    init_sentry(component="worker")

    if wait:
        # Long-lived fleet worker boot. One-shot drains (wait=False, the
        # self-hosted agent's per-wake path) skip this to avoid per-drain spam.
        from . import __version__

        logger.info("splitsmith worker starting, version %s", __version__)

    from .ui.server import (
        _configure_app_logging,
        build_worker_state,
        warm_ensemble_runtime,
    )

    state = await asyncio.to_thread(build_worker_state, process_name=process_name)
    engines = state.db_engines
    app: Any = None
    try:
        if engines is not None:
            # The worker's main loop is long-lived: pooled engine (#1178). Job
            # bodies on ``asyncio.to_thread`` threads reach the database through
            # ``run_sync`` -> the process DbRunner, which adopted its own loop.
            # Adopted inside the ``try`` so the ``finally`` disposes it however
            # boot ends, a failed ``_open_app_with_retry`` included.
            engine = engines.adopt_current_loop()
            # A superuser / BYPASSRLS role would skip every tenant policy.
            from .db.roles import assert_runtime_role

            await assert_runtime_role(engine)
        _configure_app_logging()
        try:
            await asyncio.to_thread(warm_ensemble_runtime)
        except Exception:  # noqa: BLE001 - warmup is best-effort; cold load is re-timed at job time
            logger.warning("ensemble warmup failed on worker boot; first shot_detect will cold-load")
        _attach_procrastinate_logging()
        app = await _open_app_with_retry(
            database_url, state, application_name=f"{process_name}-queue", after_job=_job_sweep()
        )
        options: dict[str, Any] = {
            "queues": queues,
            "concurrency": concurrency,
            "wait": wait,
            # A one-shot drain exits when the queue is empty; LISTEN/NOTIFY
            # exists to wake a long-lived worker for jobs that arrive later.
            # Beyond being useless mid-drain, cancelling the listener at
            # shutdown intermittently hangs inside psycopg's notifies()
            # generator (~1 in 5 one-shot runs locally), leaving a zombie
            # container that never exits - the opposite of scale-to-zero.
            "listen_notify": wait,
            # Each worker start prunes the workers silent this long, which
            # nulls their jobs' worker_id and makes them orphans to the sweep
            # at once; Procrastinate's 30 s would end a job whose worker is
            # merely behind, inside the sweep's own threshold.
            "stalled_worker_timeout": STALLED_HEARTBEAT_SECONDS,
        }
        # A job whose worker was killed stays ``doing`` and its row
        # ``running`` until some worker ends it (#1677): every drain does at
        # its start, and every STALLED_SWEEP_SECONDS while it runs.
        end_orphan = _orphan_ender(state)
        await _sweep_stalled(app, end_orphan)
        await _run_worker_until(app, stop_event, options, deadline=deadline, end_orphan=end_orphan)
        logger.info("worker: drain complete (wait=%s); shutting down", wait)
    finally:
        try:
            if app is not None:
                await app.connector.close_async()
        finally:
            if engines is not None:
                await engines.dispose_current_loop()


async def _run_worker_until(
    app: Any,
    stop_event: asyncio.Event | None,
    options: dict[str, Any],
    *,
    deadline: float | None = None,
    end_orphan: OrphanEnder | None = None,
    sweep_seconds: float = 0.0,
    stalled_after_seconds: float = STALLED_HEARTBEAT_SECONDS,
) -> None:
    """``app.run_worker_async`` with a graceful stop on ``stop_event`` or at ``deadline``.

    The same two steps ``run_worker_async`` takes (import the task paths,
    build a ``Worker`` over the app's defaults), plus a watcher that calls
    the worker's graceful ``stop``: no new job is claimed and the one in
    flight finishes. The watchers are created before ``worker.run`` so they
    first run after ``run`` has cleared its own stop flag; a stop set any
    earlier would be wiped by that clear.

    A caller with a ``stop_event`` owns the signals, so Procrastinate
    installs none; otherwise its SIGTERM / SIGINT handlers stay.

    Two tasks of our own run until ``worker.run`` returns:

    - a heartbeat for this worker. Procrastinate's graceful stop (the
      deadline, SIGTERM, the agent's stop, or a failed heartbeat write)
      cancels its own heartbeat *before* it waits for the job in flight, so
      without this a stopping drain's job looked orphaned for up to the
      whole drain margin and any sweep, or serve's wake count, ended it
      while it ran.
    - with ``end_orphan``, the periodic stalled sweep (every
      ``sweep_seconds``, default :data:`STALLED_SWEEP_SECONDS`), which
      never ends this worker's own jobs.
    """
    from procrastinate.worker import Worker

    if stop_event is not None and stop_event.is_set():
        return
    app.perform_import_paths()
    worker = Worker(
        app=app, **{**app.worker_defaults, **options, "install_signal_handlers": stop_event is None}
    )
    beat_seconds = float(options.get("update_heartbeat_interval", 10.0))

    async def _keep_beating() -> None:
        while True:
            await asyncio.sleep(beat_seconds)
            worker_id = worker.worker_id
            if worker_id is None:
                continue
            try:
                await app.job_manager.update_heartbeat(worker_id)
            except Exception:
                logger.warning("worker: heartbeat write failed; retrying", exc_info=True)

    async def _sweep_every(interval: float) -> None:
        assert end_orphan is not None
        while True:
            await asyncio.sleep(interval)
            await _sweep_stalled(
                app,
                end_orphan,
                exclude_worker_id=worker.worker_id,
                seconds_since_heartbeat=stalled_after_seconds,
            )

    async def _watch_event(event: asyncio.Event) -> None:
        await event.wait()
        worker.stop()

    async def _watch_deadline(at: float) -> None:
        await asyncio.sleep(max(0.0, at - time.monotonic()))
        logger.info("worker: drain budget spent; finishing the job in flight and claiming no more")
        worker.stop()

    watchers: list[asyncio.Task[None]] = [asyncio.create_task(_keep_beating())]
    if stop_event is not None:
        watchers.append(asyncio.create_task(_watch_event(stop_event)))
    if deadline is not None:
        watchers.append(asyncio.create_task(_watch_deadline(deadline)))
    if end_orphan is not None:
        watchers.append(asyncio.create_task(_sweep_every(sweep_seconds or STALLED_SWEEP_SECONDS)))
    try:
        await worker.run()
    finally:
        for watcher in watchers:
            watcher.cancel()
        for watcher in watchers:
            with contextlib.suppress(asyncio.CancelledError):
                await watcher


# Connection failures worth retrying when the worker opens its pool against a
# serverless Postgres (Neon). ``PoolTimeout`` is the observed crash: the
# short-lived ``--one-shot`` cron drain reconnects every tick and
# ``psycopg_pool.open(wait=True)`` intermittently can't fill within its 30s
# window -- seen even against an already-awake compute, so it's a transient
# pooler stall, not just cold start. ``OperationalError`` / ``OSError`` cover
# transient network or pooler refusals in the same window.
_RETRYABLE_OPEN_ERRORS = (psycopg_pool.PoolTimeout, psycopg.OperationalError, OSError)


def _job_sweep() -> Callable[[], Awaitable[None]] | None:
    """The per-job source-cache sweep for a worker on an in-memory filesystem.

    ``None`` off Cloud Run (the agent sweeps once per drain on its own disk).
    On Cloud Run every mirrored source and written trim counts against the
    container's memory, so a drain that only grows its cache is killed
    part-way through a match. The sweep is skipped while another job runs in
    this process, so it never evicts a file a running job holds open.
    """
    from .agent import _resolve_cache_root, _run_cache_sweep
    from .observability import jobs_running
    from .source_cache import per_job_cache_max_bytes

    cap = per_job_cache_max_bytes()
    if cap is None:
        return None
    cache_root = _resolve_cache_root()

    async def sweep() -> None:
        if jobs_running():
            return
        await _run_cache_sweep(cache_root, cap)

    return sweep


#: The error an orphaned job's row ends with. It says what happened and
#: what to do, since the user sees it in the failures sheet.
ORPHANED_JOB_ERROR = (
    "the worker running this job stopped before it finished (it was killed, ran out of memory "
    "or hit its time limit); retry it"
)
#: How often a running drain looks for other workers' orphans.
STALLED_SWEEP_SECONDS = 120.0

#: Ends one orphaned job's ``compute_jobs`` row: ``(user_id, job_id)``.
OrphanEnder = Callable[[str, str], Awaitable[None]]


async def recover_stalled_jobs(
    app: procrastinate.App,
    end_orphan: OrphanEnder,
    *,
    seconds_since_heartbeat: float = STALLED_HEARTBEAT_SECONDS,
    exclude_worker_id: int | None = None,
) -> int:
    """End every job whose worker died mid-run (#1677); return the count.

    Procrastinate's stalled-job query finds the ``doing`` jobs whose worker
    has no heartbeat newer than ``seconds_since_heartbeat`` (or no row at
    all), of every task, so the sweep ends exactly what serve's wake count
    (``worker_trigger.make_pending_jobs_counter``) counts as orphaned.
    ``exclude_worker_id`` is the sweeping drain's own worker: its jobs are
    alive by definition. A compute job's ``compute_jobs`` row is ended first through
    ``end_orphan`` (failed, or cancelled when a cancel was requested: its
    worker can no longer honour one), then the Procrastinate job is marked
    failed. In that order, so a row write that fails leaves the job
    ``doing`` for the next sweep instead of a row stuck ``running`` with
    nothing left to find it.

    Failed, not re-queued: a worker killed mid-job is now a crash or an
    out-of-memory kill (the drain budget keeps the task timeout from
    killing one), which a re-run repeats, and a loop of re-runs bills an
    execution each time. A failed row shows in the failures sheet with a
    Retry, and stops blocking its stage's dedupe.

    Never raises past one job: a job another sweep ended first is skipped.
    """
    stalled = await app.job_manager.get_stalled_jobs(seconds_since_heartbeat=seconds_since_heartbeat)
    ended = 0
    for job in stalled:
        if exclude_worker_id is not None and job.worker_id == exclude_worker_id:
            continue
        kwargs = job.task_kwargs
        user_id, job_id = kwargs.get("user_id"), kwargs.get("job_id")
        try:
            if job.task_name == RUN_COMPUTE_JOB_TASK and isinstance(user_id, str) and isinstance(job_id, str):
                await end_orphan(user_id, job_id)
            assert job.id is not None
            await app.job_manager.finish_job_by_id_async(
                job_id=job.id, status=ProcrastinateStatus.FAILED, delete_job=False
            )
        except Exception:
            logger.warning(
                "worker: could not end orphaned job %s; the next sweep retries", job.id, exc_info=True
            )
            continue
        ended += 1
        logger.warning(
            "worker: ended orphaned job %s (compute job %s, %s): its worker stopped mid-run",
            job.id,
            job_id,
            kwargs.get("kind"),
        )
    return ended


async def _sweep_stalled(app: procrastinate.App, end_orphan: OrphanEnder, **kwargs: Any) -> None:
    """:func:`recover_stalled_jobs`, logged and swallowed: a failed sweep must
    not stop the drain, and the next one retries."""
    try:
        await recover_stalled_jobs(app, end_orphan, **kwargs)
    except Exception:
        logger.warning("worker: stalled-job sweep failed", exc_info=True)


def _orphan_ender(state: Any) -> OrphanEnder:
    """End an orphan's row through its owner's job backend (the tenant's
    RLS-scoped session, like the job itself)."""

    async def end(user_id: str, job_id: str) -> None:
        await state.build_tenant(user_id).jobs.end_orphaned(job_id, error=ORPHANED_JOB_ERROR)

    return end


async def _open_app_with_retry(
    database_url: str,
    state: Any,
    *,
    application_name: str | None = None,
    after_job: Callable[[], Awaitable[None]] | None = None,
    attempts: int = 5,
    base_delay: float = 3.0,
) -> procrastinate.App:
    """Build and open a Procrastinate :class:`~procrastinate.App`, retrying the
    connection on transient failures.

    Procrastinate hard-codes its pool open at ``open(wait=True)`` with a 30s
    timeout we can't widen, and a timed-out pool is left closed (it will not
    re-open), so each attempt builds a **fresh** App. ``build_app`` issues no
    query, so rebuilding per attempt is cheap. Linear backoff between attempts:
    the first attempt tends to warm the path (waking the compute / priming
    Neon's pooler) and a later one connects. The caller owns
    ``connector.close_async`` once this returns an opened App.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        app = build_app(database_url, application_name=application_name)
        register_compute_task(app, state, after_job=after_job)
        try:
            await app.connector.open_async()
            return app
        except _RETRYABLE_OPEN_ERRORS as exc:
            last_exc = exc
            with contextlib.suppress(Exception):
                await app.connector.close_async()
            logger.warning("worker DB connect failed (attempt %d/%d): %s", attempt, attempts, exc)
            if attempt < attempts:
                await asyncio.sleep(base_delay * attempt)
    assert last_exc is not None  # loop runs >=1 time; only reached after a failure
    raise last_exc


def queue_name_for_user(user_id: str) -> str:
    """Return the per-tenant queue name for ``user_id``.

    Convention: ``user-<id>``. See module docstring for the
    rationale. ``user_id`` is the ULID from the ``users`` table;
    callers should never pass an unsanitised email or any value
    they didn't read out of the DB.
    """
    if not isinstance(user_id, str) or not user_id:
        raise ValueError(f"queue_name_for_user requires a non-empty user_id; got {user_id!r}")
    return f"user-{user_id}"
