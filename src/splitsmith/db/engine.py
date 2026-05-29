"""Async engine + session factory."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.pool import NullPool


def create_engine(url: str, *, echo: bool = False, pool_disabled: bool = False) -> AsyncEngine:
    """Build an async SQLAlchemy engine.

    ``url`` shapes the backend:

    - ``sqlite+aiosqlite:///:memory:`` -- tests (microsecond setup).
    - ``sqlite+aiosqlite:///path/to/db.sqlite`` -- local-mode
      persistence if the desktop ever needs job survival.
    - ``postgresql+asyncpg://user:pass@host:5432/dbname`` --
      hosted-mode production. Neon free tier or self-hosted.

    ``echo=True`` dumps every SQL statement to stdout -- useful
    for debugging; never set this in production.

    ``pool_disabled=True`` uses :class:`NullPool` so every
    ``session()`` opens a fresh DB connection and closes it on
    release. Required when the engine is shared across multiple
    short-lived event loops (each ``asyncio.run`` call), as is the
    case for the hosted-mode boot path + the
    :class:`PostgresJobBackend` worker thread pool. asyncpg
    connections are event-loop-bound; a pooled connection created
    in loop A and reused in loop B crashes with "attached to a
    different loop". NullPool sidesteps the issue at the cost of a
    per-call TCP handshake -- acceptable for the call rates here
    (handler I/O + worker callbacks, not OLTP-style hot loops).
    Local-mode SQLite/aiosqlite is forgiving and doesn't need this.
    """
    kwargs: dict = {"echo": echo}
    if pool_disabled:
        kwargs["poolclass"] = NullPool
    return create_async_engine(url, **kwargs)


def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Wrapper around SQLAlchemy's ``async_sessionmaker`` with
    sensible defaults: ``expire_on_commit=False`` so refreshed
    objects survive past their session's commit (the common
    pattern in FastAPI handlers that return Pydantic models
    serialised from ORM objects).
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _tenant_guc_after_begin(user_id: str) -> Callable[[Session, SessionTransaction, Connection], None]:
    """Build an ``after_begin`` listener that pins ``app.user_id`` for the
    transaction that just began.

    Uses ``set_config(..., true)`` -- the function form of ``SET LOCAL``,
    so the value is scoped to the current transaction and cleared at its
    end. This is set *per transaction* rather than once per session on
    purpose: under :class:`NullPool` (the hosted engine), a
    :class:`~sqlalchemy.orm.Session` releases its connection on every
    commit/rollback and acquires a *fresh* one for the next transaction,
    so a session-level ``SET`` would be lost the moment a store method
    runs a second query after a commit (e.g. ``PostgresMatchStore.upsert``'s
    IntegrityError retry). Re-setting on each ``after_begin`` guarantees
    every connection a query lands on carries the GUC.

    No-op on non-PostgreSQL backends (SQLite has no ``set_config`` / RLS),
    so the unit-test engine is untouched; RLS itself is proven by the
    ``pytest -m docker`` isolation test.
    """

    def _after_begin(
        session: Session,
        transaction: SessionTransaction,
        connection: Connection,
    ) -> None:
        if connection.dialect.name != "postgresql":
            return
        connection.execute(
            text("SELECT set_config('app.user_id', :uid, true)"),
            {"uid": user_id},
        )

    return _after_begin


def tenant_session_factory(
    base_factory: async_sessionmaker[AsyncSession],
    user_id: str,
) -> Callable[[], AsyncSession]:
    """Wrap ``base_factory`` so every session it opens sets the
    ``app.user_id`` GUC the Row-Level Security policies key on, on each
    transaction.

    Returns a callable with the same ``async with factory() as session``
    shape as :func:`sessionmaker`'s result, so the per-user store classes
    consume it unchanged -- the GUC is set transparently (via an
    ``after_begin`` listener, see :func:`_tenant_guc_after_begin`) before
    they run a single statement, and re-set on every subsequent
    transaction within the session.
    """
    listener = _tenant_guc_after_begin(user_id)

    def _open() -> AsyncSession:
        session = base_factory()
        # Attach to this session instance only; the listener is collected
        # with the session, so distinct per-user factories never share or
        # accumulate listeners.
        event.listen(session.sync_session, "after_begin", listener)
        return session

    return _open
