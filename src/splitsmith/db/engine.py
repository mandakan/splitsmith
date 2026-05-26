"""Async engine + session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
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
