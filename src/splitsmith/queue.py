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
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import procrastinate

from .observability import init_sentry

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


def build_app(database_url: str) -> procrastinate.App:
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
    connector = procrastinate.PsycopgConnector(conninfo=dsn)
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


def make_deferrer(database_url: str) -> Callable[..., Awaitable[None]]:
    """Build the enqueue coroutine injected into :class:`PostgresJobBackend`.

    The returned ``deferrer(job_id, user_id, kind, args, match_id)``
    enqueues a :data:`RUN_COMPUTE_JOB_TASK` job onto the user's queue.
    ``configure_task`` defers a task by name without registering its
    body locally -- the API process only enqueues; the worker owns the
    implementation. The :class:`procrastinate.App` is built lazily and
    cached on first defer so merely wiring hosted mode (e.g. against
    SQLite, where the queue is unused) never touches Postgres; a SQLite
    URL raises only if a job is actually submitted.
    """
    cache: dict[str, procrastinate.App] = {}

    async def _defer(
        *,
        job_id: str,
        user_id: str,
        kind: str,
        args: dict[str, Any],
        match_id: str | None,
    ) -> None:
        app = cache.get("app")
        if app is None:
            app = build_app(database_url)
            cache["app"] = app
        queue = queue_name_for_user(user_id)
        async with app.open_async():
            await app.configure_task(name=RUN_COMPUTE_JOB_TASK, queue=queue).defer_async(
                job_id=job_id,
                user_id=user_id,
                kind=kind,
                args=args,
                match_id=match_id,
            )

    return _defer


def register_compute_task(app: procrastinate.App, state: Any) -> None:
    """Register the worker-side :data:`RUN_COMPUTE_JOB_TASK` on ``app``.

    Bound to a worker ``state`` (built by
    ``splitsmith.ui.server.build_worker_state``). The task re-sets the
    ``current_match_*`` ContextVars from the queued ``match_id``,
    rehydrates any Pydantic ``req`` in ``args``, then drives the shared
    body via :meth:`PostgresJobBackend.run_job` -- the same ``kind`` ->
    body mapping the local registry uses. Must be registered before
    :meth:`procrastinate.App.run_worker_async`.
    """
    from .match_registry import MatchNotRegisteredError
    from .ui.server import current_match_id, current_match_root, current_tenant

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

        try:
            await state.jobs.run_job(job_id=job_id, kind=kind, args=call_args, before_body=_bind_match)
        finally:
            current_tenant.reset(tenant_token)


def _rehydrate_args(kind: str, args: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the typed ``req`` Pydantic model dropped to a dict by
    :func:`splitsmith.db.job_backend._to_wire_args` for the queue.

    Only ``export`` / ``match_export`` carry a ``req``; every other kind
    passes through. Importing the request models lazily keeps this module
    free of a server import at load time (it stays lazy-importable behind
    ``_hosted_mode_active()``)."""
    if kind not in ("export", "match_export") or "req" not in args:
        return args
    from .ui.server import ExportStageRequest, MatchExportRequest

    model = ExportStageRequest if kind == "export" else MatchExportRequest
    out = dict(args)
    out["req"] = model.model_validate(args["req"])
    return out


async def run_worker(
    database_url: str,
    *,
    concurrency: int = 1,
    queues: list[str] | None = None,
) -> None:
    """Run a long-lived worker that drains the job queue until killed.

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

    from .ui.server import (
        _configure_app_logging,
        build_worker_state,
        warm_ensemble_runtime,
    )

    state = await asyncio.to_thread(build_worker_state)
    _configure_app_logging()
    try:
        await asyncio.to_thread(warm_ensemble_runtime)
    except Exception:  # noqa: BLE001 - warmup is best-effort; cold load is re-timed at job time
        logger.warning("ensemble warmup failed on worker boot; first shot_detect will cold-load")
    app = build_app(database_url)
    register_compute_task(app, state)
    async with app.open_async():
        await app.run_worker_async(queues=queues, concurrency=concurrency)


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
