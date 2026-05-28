"""Procrastinate-backed job queue (hosted mode).

The Postgres-native dispatch layer that backs the worker fleet
(see ``docs/saas-readiness/04-compute-backends.md``). Today's
``PostgresJobBackend`` persists ``compute_jobs`` rows but drives
them on an in-process :class:`~concurrent.futures.ThreadPoolExecutor`
inside ``splitsmith serve``; this module is the queue layer that
lets the dispatch step move out-of-process so a separate
``splitsmith worker`` fleet can pop jobs.

PR-alpha (this PR) lands the schema + the configured
:class:`procrastinate.App` only. No tasks are registered yet -- task
registration is the PR-gamma migration step where each job kind
(``shot_detect``, ``beep_detect``, ``trim``, ...) gets split into a
pure ``*_body`` function and a thin task wrapper that rebuilds per-
user state from ``user_id`` + project_id alone.

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

    The returned App has **no tasks registered**. Task registration
    happens in PR-gamma when each job kind gets its task wrapper.
    Until then, callers can still use this App for the dispatch
    plumbing (open/close connector, run the worker loop with a no-op
    handler) so PR-beta's ``splitsmith worker`` smoke test is wirable
    against a real queue.
    """
    dsn = _to_psycopg_dsn(database_url)
    connector = procrastinate.PsycopgConnector(kwargs={"conninfo": dsn})
    return procrastinate.App(connector=connector)


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
