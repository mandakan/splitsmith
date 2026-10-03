"""Tests for :class:`splitsmith.db.engine.LoopEngines` and :func:`loop_sessionmaker`.

No Postgres: ``create_async_engine`` is patched to a recorder for the
asyncpg cases, and SQLite for the round trip.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.pool import NullPool

from splitsmith.db import Base, User, create_engine, sessionmaker
from splitsmith.db import engine as engine_mod
from splitsmith.db.engine import LoopEngines, loop_sessionmaker, tenant_session_factory

PG_URL = "postgresql+asyncpg://u:p@h/db?ssl=require&prepared_statement_cache_size=0"


class _FakeEngine:
    def __init__(self, url: str, kwargs: dict[str, Any]) -> None:
        self.url = url
        self.kwargs = kwargs
        self.disposed = 0

    async def dispose(self) -> None:
        self.disposed += 1


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> list[_FakeEngine]:
    made: list[_FakeEngine] = []

    def _fake_create(url: str, **kwargs: Any) -> _FakeEngine:
        e = _FakeEngine(url, kwargs)
        made.append(e)
        return e

    monkeypatch.setattr(engine_mod, "create_async_engine", _fake_create)
    return made


def test_pooled_is_true_for_asyncpg_only() -> None:
    assert LoopEngines(PG_URL).pooled is True
    assert LoopEngines("sqlite+aiosqlite:///:memory:").pooled is False


def test_adopted_loops_get_distinct_pooled_engines(recorder: list[_FakeEngine]) -> None:
    engines = LoopEngines(PG_URL)

    async def _adopt_and_ask() -> tuple[Any, Any]:
        a = engines.adopt_current_loop()
        b = engines.for_current_loop()
        return a, b

    a1, b1 = asyncio.run(_adopt_and_ask())
    a2, b2 = asyncio.run(_adopt_and_ask())
    assert a1 is b1, "the same loop asks twice and gets the same engine"
    assert a1 is not a2, "two loops, two engines"
    assert len(recorder) == 2


def test_pooled_engine_settings(recorder: list[_FakeEngine]) -> None:
    engines = LoopEngines(PG_URL)

    async def _adopt() -> None:
        engines.adopt_current_loop()

    asyncio.run(_adopt())
    (made,) = recorder
    assert made.url == PG_URL, "the URL (and its prepared_statement_cache_size=0) passes through untouched"
    assert made.kwargs["pool_size"] == 5
    assert made.kwargs["max_overflow"] == 10
    assert made.kwargs["pool_timeout"] == 30
    assert made.kwargs["pool_pre_ping"] is True
    assert "pool_recycle" not in made.kwargs
    assert "poolclass" not in made.kwargs


def test_unadopted_loop_gets_the_shared_nullpool_engine(recorder: list[_FakeEngine]) -> None:
    """A loop nobody adopted (a concurrent.futures thread, a test's
    asyncio.run) must get an engine that never reuses a connection across
    loops: one shared NullPool engine, however many loops ask."""
    engines = LoopEngines(PG_URL)

    async def _ask() -> Any:
        return engines.for_current_loop()

    e1 = asyncio.run(_ask())
    e2 = asyncio.run(_ask())
    assert e1 is e2
    assert len(recorder) == 1
    assert recorder[0].kwargs["poolclass"] is NullPool


def test_sqlite_url_gets_one_nullpool_engine_whatever_loop_asks(recorder: list[_FakeEngine]) -> None:
    """aiosqlite is not loop-bound and ``:memory:`` is one database per
    connection, so SQLite keeps exactly what the hosted wiring built
    before: one NullPool engine."""
    engines = LoopEngines("sqlite+aiosqlite:///:memory:")

    async def _both() -> tuple[Any, Any]:
        return engines.adopt_current_loop(), engines.for_current_loop()

    a1, b1 = asyncio.run(_both())
    a2, b2 = asyncio.run(_both())
    assert a1 is b1 is a2 is b2
    assert len(recorder) == 1
    assert recorder[0].kwargs["poolclass"] is NullPool


def test_dispose_current_loop_disposes_and_forgets(recorder: list[_FakeEngine]) -> None:
    engines = LoopEngines(PG_URL)

    async def _cycle() -> None:
        first = engines.adopt_current_loop()
        await engines.dispose_current_loop()
        assert first.disposed == 1
        second = engines.adopt_current_loop()
        assert second is not first
        await engines.dispose_current_loop()
        # Disposing an un-adopted loop is a no-op, not an error.
        await engines.dispose_current_loop()

    asyncio.run(_cycle())
    assert len(recorder) == 2


def test_dispose_fallback(recorder: list[_FakeEngine]) -> None:
    engines = LoopEngines(PG_URL)

    async def _use_and_dispose() -> None:
        engines.for_current_loop()  # un-adopted -> fallback
        await engines.dispose_fallback()
        await engines.dispose_fallback()  # idempotent

    asyncio.run(_use_and_dispose())
    assert recorder[0].disposed == 1


def test_dead_loop_entry_is_dropped(recorder: list[_FakeEngine]) -> None:
    """Engines are weak-keyed on the loop; a closed, collected loop takes
    its entry with it instead of pinning a dead engine forever."""
    import gc

    engines = LoopEngines(PG_URL)

    async def _adopt() -> None:
        engines.adopt_current_loop()

    asyncio.run(_adopt())
    gc.collect()
    assert engines.adopted_count == 0


def test_loop_sessionmaker_round_trips_on_sqlite(tmp_path) -> None:
    """The stores call ``factory()`` with no arguments and ``async with`` the
    result; a loop_sessionmaker factory must satisfy that unchanged."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'x.sqlite'}"
    setup = create_engine(url)

    async def _create_all() -> None:
        async with setup.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await setup.dispose()

    asyncio.run(_create_all())

    engines = LoopEngines(url)
    factory = loop_sessionmaker(engines)

    async def _write_then_read() -> str:
        async with factory() as s:
            u = User(email="m@thias.se")
            s.add(u)
            await s.commit()
            await s.refresh(u)
            uid = u.id
        async with factory() as s:
            got = await s.get(User, uid)
            assert got is not None
            return got.email

    assert asyncio.run(_write_then_read()) == "m@thias.se"


def test_loop_sessionmaker_sessions_carry_the_tenant_listener(tmp_path) -> None:
    """``tenant_session_factory`` wraps a loop_sessionmaker factory the same
    way it wraps ``sessionmaker``'s: the after_begin listener is attached
    to each session it opens.

    ``event.contains`` needs the exact listener fn, which is awkward to
    recover across the wrap; asserting the ``after_begin`` dispatch is
    non-empty on the wrapped factory's session -- and empty on the
    unwrapped one, as a contrast -- captures the same thing.
    """
    url = f"sqlite+aiosqlite:///{tmp_path / 'y.sqlite'}"

    async def _after_begin_count(factory) -> int:  # noqa: ANN001
        session = factory()
        try:
            return len(list(session.sync_session.dispatch.after_begin))
        finally:
            await session.close()

    wrapped = tenant_session_factory(loop_sessionmaker(LoopEngines(url)), "user-1")
    unwrapped = loop_sessionmaker(LoopEngines(url))

    assert asyncio.run(_after_begin_count(wrapped)) >= 1
    assert asyncio.run(_after_begin_count(unwrapped)) == 0


def test_plain_sessionmaker_still_works() -> None:
    """Regression guard: the old factory shape is still what the tests and
    alembic-adjacent code build."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = sessionmaker(engine)

    async def _open() -> None:
        async with factory() as s:
            assert s is not None

    asyncio.run(_open())
