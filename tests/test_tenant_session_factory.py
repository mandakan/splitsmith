"""Tests for :func:`splitsmith.db.tenant_session_factory` and its
``after_begin`` GUC listener.

The factory pins the ``app.user_id`` GUC the Row-Level Security policies
key on, once per transaction (``set_config(..., true)`` == ``SET LOCAL``).
SQLite (the unit-test engine) has no RLS or ``set_config``, so on SQLite
the listener must be a transparent no-op -- that is what the integration
test proves. The actual RLS enforcement is proven against live Postgres
in ``tests/test_hosted_docker_smoke.py``.
"""

from __future__ import annotations

import asyncio

from splitsmith.db import (
    Base,
    PostgresMatchStore,
    User,
    create_engine,
    sessionmaker,
    tenant_session_factory,
)
from splitsmith.db.engine import _tenant_guc_after_begin


class _FakeDialect:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeConnection:
    """Records ``execute`` calls so the listener's SQL can be asserted
    without a live database."""

    def __init__(self, dialect_name: str) -> None:
        self.dialect = _FakeDialect(dialect_name)
        self.calls: list[tuple[str, dict | None]] = []

    def execute(self, statement, params=None):  # noqa: ANN001
        self.calls.append((str(statement), params))


def test_after_begin_sets_local_guc_on_postgres() -> None:
    """On a postgres-dialect connection the listener issues exactly one
    transaction-local ``set_config('app.user_id', <uid>, true)``."""
    listener = _tenant_guc_after_begin("user-123")
    conn = _FakeConnection("postgresql")

    listener(session=None, transaction=None, connection=conn)  # type: ignore[arg-type]

    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "set_config('app.user_id'" in sql
    # ``, true)`` == is_local: scoped to the current transaction (SET LOCAL).
    assert sql.strip().endswith(", true)")
    assert params == {"uid": "user-123"}


def test_after_begin_is_noop_on_non_postgres() -> None:
    """SQLite has no ``set_config``; the listener must not touch it."""
    listener = _tenant_guc_after_begin("user-xyz")
    conn = _FakeConnection("sqlite")

    listener(session=None, transaction=None, connection=conn)  # type: ignore[arg-type]

    assert conn.calls == []


def test_store_round_trips_through_wrapped_factory_on_sqlite() -> None:
    """The per-user stores consume the wrapped factory unchanged -- this
    is the no-store-change guarantee the prod wiring relies on. On SQLite
    the listener is a no-op, so a clean round-trip proves the wrapper
    doesn't break the session lifecycle (open -> begin -> commit)."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    base = sessionmaker(engine)

    async def _setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with base() as s:
            user = User(email="m@thias.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    uid = asyncio.run(_setup())
    factory = tenant_session_factory(base, uid)
    store = PostgresMatchStore(factory, user_id=uid)

    # upsert exercises the multi-statement (SELECT -> INSERT -> commit)
    # path through the wrapped session.
    asyncio.run(store.upsert("brm-abc", "Bromma", "matches/brm-abc"))
    row = asyncio.run(store.get("brm-abc"))
    assert row is not None
    assert row.match_id == "brm-abc"
