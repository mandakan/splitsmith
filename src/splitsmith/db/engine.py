"""Async engine + session factory."""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import Awaitable, Callable
from typing import Any

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

from .share_guard import share_request_is_read_only

logger = logging.getLogger(__name__)


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

    ``pool_disabled=True`` uses :class:`NullPool` so every ``session()``
    opens a fresh DB connection and closes it on release. The hosted
    wiring no longer uses it (see :class:`LoopEngines`); it remains for
    the tests that drive a store through many short-lived event loops on
    purpose, and for callers that want one connection per call.
    """
    kwargs: dict[str, Any] = {"echo": echo}
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


_POOLED_DRIVERS = ("postgresql+asyncpg://",)


class LoopEngines:
    """One pooled :class:`AsyncEngine` per *adopted* event loop.

    asyncpg binds a connection to the loop that created it; a pooled
    connection handed to another loop crashes with "attached to a
    different loop" (#423). So an engine with a real pool may only serve
    one loop, and only a long-lived one is worth a pool. The hosted
    process has exactly two: the main loop (uvicorn's in ``serve``, the
    Procrastinate worker's in ``worker``) and the ``DbRunner`` loop that
    every sync caller submits to. Each adopts itself with
    :meth:`adopt_current_loop` and gets its own engine with
    ``pool_size=5, max_overflow=10, pool_pre_ping=True``.

    Any other loop -- a ``concurrent.futures`` thread's ``asyncio.run``,
    the re-entrant fallback in :func:`splitsmith.async_bridge.run_sync`
    -- gets the shared **fallback** engine, a :class:`NullPool` engine
    that never reuses a connection and is therefore safe from every loop.
    That is exactly what the whole hosted process used before #1178.

    Non-asyncpg URLs (SQLite in tests) are not loop-bound and ``:memory:``
    is one database per connection, so they get the fallback engine from
    every method: one NullPool engine, as the hosted wiring built before.

    Loop entries are weak-keyed, but that only releases a loop whose engine
    holds no connections -- a pooled asyncpg engine with checked-in
    connections keeps its loop key alive through the value, since each
    connection references the loop that opened it. Adopters dispose
    explicitly on the way out (:meth:`dispose_current_loop`) rather than
    relying on garbage collection to reclaim a loop's engine.

    The pool adds no traffic of its own: no warm-up, no periodic ping, no
    ``pool_recycle``. Neon closes an idle connection after five minutes
    and suspends the compute; ``pool_pre_ping`` turns the closed
    connection into a silent reconnect on the next checkout. That is the
    scale-to-zero condition from the 2026-07-03 cost plan.

    ``application_name`` (#1198) names every asyncpg connection in
    ``pg_stat_activity``: the main loop's engine carries it as given, a
    loop adopted with a ``label`` carries ``<name>-<label>`` (the hosted
    wiring labels the ``DbRunner`` loop ``runner``), the fallback carries
    ``<name>-unpooled``. Postgres truncates names past 63 bytes.

    Other loop-bound resources ride the same adoption (#1199): a caller
    asks :meth:`is_current_loop_adopted` to decide whether a resource is
    worth keeping open on this loop, and registers its closer with
    :meth:`on_loop_close`. :meth:`close_loop_resources` runs the running
    loop's closers; :meth:`dispose_current_loop` runs any left before it
    disposes the engine, so every adopter's existing shutdown path closes
    them with no edit. The ``DbRunner`` calls :meth:`close_loop_resources`
    on its own, *before* it cancels the loop's tasks: a psycopg pool runs
    worker tasks on its loop, and a pool whose workers were cancelled
    cannot close (its ``close`` re-raises the ``CancelledError``).
    """

    def __init__(
        self,
        url: str,
        *,
        echo: bool = False,
        pool_size: int = 5,
        max_overflow: int = 10,
        application_name: str | None = None,
    ) -> None:
        self._url = url
        self._application_name = application_name
        self._echo = echo
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._pooled = url.startswith(_POOLED_DRIVERS)
        self._by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncEngine] = (
            weakref.WeakKeyDictionary()
        )
        self._fallback: AsyncEngine | None = None
        self._closers: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, list[Callable[[], Awaitable[None]]]
        ] = weakref.WeakKeyDictionary()

    @property
    def pooled(self) -> bool:
        """Whether adopted loops get a real pool (asyncpg) or the fallback (everything else)."""
        return self._pooled

    @property
    def driver(self) -> str:
        """The URL's scheme (``postgresql+asyncpg``): loggable, unlike the URL,
        which carries the password."""
        return self._url.split("://", 1)[0]

    @property
    def adopted_count(self) -> int:
        return len(self._by_loop)

    def _connect_args(self, label: str | None) -> dict[str, Any]:
        """asyncpg's startup ``application_name``; nothing for other drivers
        (aiosqlite rejects ``server_settings``)."""
        if not self._pooled or not self._application_name:
            return {}
        name = f"{self._application_name}-{label}" if label else self._application_name
        return {"connect_args": {"server_settings": {"application_name": name}}}

    def _fallback_engine(self) -> AsyncEngine:
        if self._fallback is None:
            self._fallback = create_async_engine(
                self._url, echo=self._echo, poolclass=NullPool, **self._connect_args("unpooled")
            )
        return self._fallback

    def adopt_current_loop(self, label: str | None = None) -> AsyncEngine:
        """Mark the running loop long-lived and return its pooled engine
        (created on first call). For a non-pooled URL this is the fallback.
        ``label`` suffixes the engine's ``application_name``; it is read
        only when the engine is created.

        Requires a running loop even for a non-pooled URL: a caller outside
        any loop is a misuse this must not paper over just because SQLite
        happens not to need the loop to pick an engine.
        """
        loop = asyncio.get_running_loop()
        if not self._pooled:
            return self._fallback_engine()
        engine = self._by_loop.get(loop)
        if engine is None:
            engine = create_async_engine(
                self._url,
                echo=self._echo,
                pool_size=self._pool_size,
                max_overflow=self._max_overflow,
                pool_timeout=30,
                pool_pre_ping=True,
                **self._connect_args(label),
            )
            self._by_loop[loop] = engine
        return engine

    def for_current_loop(self) -> AsyncEngine:
        """The running loop's engine if it was adopted, else the fallback.

        Requires a running loop even for a non-pooled URL -- see
        :meth:`adopt_current_loop`.
        """
        loop = asyncio.get_running_loop()
        if not self._pooled:
            return self._fallback_engine()
        engine = self._by_loop.get(loop)
        return engine if engine is not None else self._fallback_engine()

    def is_current_loop_adopted(self) -> bool:
        """Whether the running loop is long-lived (adopted and not yet disposed).

        Always False for a non-pooled URL, which adopts nothing.
        """
        return self._pooled and asyncio.get_running_loop() in self._by_loop

    def on_loop_close(self, closer: Callable[[], Awaitable[None]]) -> None:
        """Register ``closer`` to be awaited when the running loop's resources
        close (:meth:`close_loop_resources`, or :meth:`dispose_current_loop`).

        The running loop must be adopted: an un-adopted loop has no shutdown
        path that would ever run the closer.
        """
        if not self.is_current_loop_adopted():
            raise RuntimeError("on_loop_close needs an adopted loop")
        loop = asyncio.get_running_loop()
        self._closers.setdefault(loop, []).append(closer)

    async def close_loop_resources(self) -> None:
        """Run and forget the running loop's registered closers, in
        registration order. A failing closer is logged and does not stop
        the others. Must run *on* that loop."""
        closers = self._closers.pop(asyncio.get_running_loop(), [])
        for closer in closers:
            try:
                await closer()
            except Exception:  # noqa: BLE001 -- shutdown must reach every closer and the engine
                logger.warning("closing a loop resource failed", exc_info=True)

    async def dispose_current_loop(self) -> None:
        """Close the running loop's resources, then dispose and forget its
        engine; no-op if it has none.

        Must run *on* that loop: asyncpg closes connections on the loop that
        owns them.
        """
        if not self._pooled:
            return
        await self.close_loop_resources()
        loop = asyncio.get_running_loop()
        engine = self._by_loop.pop(loop, None)
        if engine is not None:
            await engine.dispose()

    async def dispose_fallback(self) -> None:
        engine, self._fallback = self._fallback, None
        if engine is not None:
            await engine.dispose()


def loop_sessionmaker(engines: LoopEngines) -> Callable[[], AsyncSession]:
    """A session factory that picks the current loop's engine at open time.

    Same ``() -> AsyncSession`` contract as :func:`sessionmaker`'s result
    (every store calls ``self._session_factory()`` with no arguments and
    ``async with``-es it), same ``expire_on_commit=False``.
    """

    def _open() -> AsyncSession:
        return AsyncSession(engines.for_current_loop(), expire_on_commit=False)

    return _open


def _tenant_guc_after_begin(user_id: str) -> Callable[[Session, SessionTransaction, Connection], None]:
    """Build an ``after_begin`` listener that pins ``app.user_id`` for the
    transaction that just began.

    Uses ``set_config(..., true)`` -- the function form of ``SET LOCAL``,
    so the value is scoped to the current transaction and cleared at its
    end. This is set *per transaction* rather than once per session on
    purpose: a :class:`~sqlalchemy.orm.Session`'s connection is not
    guaranteed stable across transactions. The session releases it on
    every commit/rollback, and the next transaction may get a different
    one (the per-loop pool of :class:`LoopEngines` may hand back another
    pooled connection; the NullPool fallback always opens a fresh one).
    A session-level ``SET`` would be lost the moment a store method runs
    a second query after a commit (e.g. ``PostgresMatchStore.upsert``'s
    IntegrityError retry). Re-setting on each ``after_begin`` guarantees
    every connection a query lands on carries the GUC.

    No-op on non-PostgreSQL backends (SQLite has no ``set_config`` / RLS),
    so the unit-test engine is untouched; RLS itself is proven by the
    ``pytest -m docker`` isolation test.

    #779 addition: when the current request is a read-scoped share
    request (see splitsmith.db.share_guard), the listener also issues
    SET TRANSACTION READ ONLY so any accidental write fails at Postgres
    with SQLSTATE 25006 instead of succeeding as the impersonated owner.
    Only tenant-factory sessions get the listener; raw-factory sessions
    (auth, share-token resolution) carry no tenant GUC, so RLS already
    fails their owner-state writes closed.
    """

    def _after_begin(
        session: Session,
        transaction: SessionTransaction,
        connection: Connection,
    ) -> None:
        if connection.dialect.name != "postgresql":
            return
        # #779: a read-scoped share request must not write anything, no
        # matter which code path tries - including code that never heard
        # of the share ContextVars. SET TRANSACTION must precede the
        # transaction's first query, so it goes before the GUC SELECT.
        # Same per-transaction reasoning as the GUC itself: the next
        # transaction may run on a different connection.
        if share_request_is_read_only():
            connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(
            text("SELECT set_config('app.user_id', :uid, true)"),
            {"uid": user_id},
        )

    return _after_begin


def tenant_session_factory(
    base_factory: Callable[[], AsyncSession],
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
