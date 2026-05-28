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

import re

import procrastinate


def _to_psycopg_dsn(sqlalchemy_url: str) -> str:
    """Translate a SQLAlchemy async URL to a psycopg3 DSN.

    Procrastinate's :class:`PsycopgConnector` talks to psycopg3
    directly, not through SQLAlchemy, so it wants a bare libpq DSN
    (``postgresql://user:pass@host:5432/db``) -- no ``+asyncpg``
    dialect suffix. We strip the suffix so callers can keep passing
    the same ``SPLITSMITH_DATABASE_URL`` they already pass to
    SQLAlchemy.

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
    return re.sub(r"^postgresql\+\w+://", "postgresql://", sqlalchemy_url)


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
    singleton, on the first detection task in PR-gamma) and drains
    many jobs. For PR-beta the only registered task is ``ping``, so
    the worker stays light.

    Installs SIGINT/SIGTERM handlers (Procrastinate default) for
    graceful drain; must therefore run on the main thread, which it
    does via ``asyncio.run`` from the ``splitsmith worker`` command.
    """
    app = build_app(database_url)
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
