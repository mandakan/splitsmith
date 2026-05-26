"""Async engine + session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async SQLAlchemy engine.

    ``url`` shapes the backend:

    - ``sqlite+aiosqlite:///:memory:`` -- tests (microsecond setup).
    - ``sqlite+aiosqlite:///path/to/db.sqlite`` -- local-mode
      persistence if the desktop ever needs job survival.
    - ``postgresql+asyncpg://user:pass@host:5432/dbname`` --
      hosted-mode production. Neon free tier or self-hosted.

    ``echo=True`` dumps every SQL statement to stdout -- useful
    for debugging; never set this in production.
    """
    return create_async_engine(url, echo=echo)


def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Wrapper around SQLAlchemy's ``async_sessionmaker`` with
    sensible defaults: ``expire_on_commit=False`` so refreshed
    objects survive past their session's commit (the common
    pattern in FastAPI handlers that return Pydantic models
    serialised from ORM objects).
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
