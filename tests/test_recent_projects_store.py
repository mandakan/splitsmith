"""Tests for :class:`PostgresRecentProjectsStore` (doc 10, Tier 3).

Runs against SQLite in-memory via aiosqlite -- same pattern as
:mod:`test_db_foundation`. The store has no Postgres-specific
behaviour, so SQLite-in-memory is enough to prove the SQL shapes
work and the Protocol is honoured.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from splitsmith.db import (
    Base,
    PostgresRecentProjectsStore,
    User,
    create_engine,
    sessionmaker,
)
from splitsmith.user_config import RECENT_PROJECTS_LIMIT, RecentProjectsStore


def _build_store_for_new_user() -> tuple[PostgresRecentProjectsStore, sessionmaker]:
    """Spin up a fresh in-memory engine + seed one user + return the
    store bound to that user id."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            user = User(email="picker@thias.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    user_id = asyncio.run(_setup())
    return PostgresRecentProjectsStore(session_factory, user_id=user_id), session_factory


def test_satisfies_recent_projects_protocol() -> None:
    """Structural-typing check: the new class fits the Protocol so
    handlers depending on the abstraction accept it without changes."""
    store, _ = _build_store_for_new_user()
    typed: RecentProjectsStore = store
    assert typed is store


@pytest.mark.parametrize("bad", ["", None, 0, b"abc"])
def test_construction_rejects_empty_or_non_string_user_id(bad) -> None:
    """Defence-in-depth: if the auth layer ever passes ``None`` or an
    empty string for ``user_id``, the store must fail loud at
    construction time. A silent empty WHERE-clause match would
    masquerade as "this user has no recents" and hide the upstream
    auth bug -- and a future query that forgets the filter would
    leak across tenants."""
    from splitsmith.db import sessionmaker as smaker
    from splitsmith.db.engine import create_engine as _ce

    engine = _ce("sqlite+aiosqlite:///:memory:")
    factory = smaker(engine)
    with pytest.raises(ValueError, match="non-empty user_id"):
        PostgresRecentProjectsStore(factory, user_id=bad)  # type: ignore[arg-type]


def test_empty_user_has_no_recent_projects() -> None:
    store, _ = _build_store_for_new_user()
    assert asyncio.run(store.list()) == []


def test_record_open_then_list_round_trip(tmp_path) -> None:
    store, _ = _build_store_for_new_user()
    target = tmp_path / "my-match"
    target.mkdir()

    async def _go() -> None:
        await store.record_open(target, "My Match", kind="match")
        rows = await store.list()
        assert len(rows) == 1
        assert rows[0].path == str(target.resolve())
        assert rows[0].name == "My Match"
        assert rows[0].kind == "match"

    asyncio.run(_go())


def test_record_open_dedups_by_resolved_path(tmp_path) -> None:
    """Two opens via different relative paths collapse to one row --
    the resolved absolute path is the deduplication key. Matches the
    Json store's behaviour so the picker doesn't show duplicates."""
    store, _ = _build_store_for_new_user()
    target = tmp_path / "match"
    target.mkdir()
    relative_view = Path(str(target.parent)) / "match" / "."

    async def _go() -> None:
        await store.record_open(target, "first", kind="match")
        await store.record_open(relative_view, "second", kind="match")
        rows = await store.list()
        assert len(rows) == 1
        # The second open's name wins -- it's an "upsert with new metadata".
        assert rows[0].name == "second"

    asyncio.run(_go())


def test_record_open_reorders_to_top(tmp_path) -> None:
    """Re-opening an older project bumps it back to the front."""
    store, _ = _build_store_for_new_user()
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    async def _go() -> None:
        await store.record_open(a, "A", kind="match")
        await store.record_open(b, "B", kind="match")
        # Re-touch A so it should appear first now.
        await store.record_open(a, "A", kind="match")
        names = [r.name for r in await store.list()]
        assert names == ["A", "B"]

    asyncio.run(_go())


def test_remove_returns_true_when_a_row_is_deleted(tmp_path) -> None:
    store, _ = _build_store_for_new_user()
    target = tmp_path / "match"
    target.mkdir()

    async def _go() -> None:
        await store.record_open(target, "X", kind="match")
        assert await store.remove(target) is True
        assert await store.remove(target) is False
        assert await store.list() == []

    asyncio.run(_go())


def test_per_user_isolation(tmp_path) -> None:
    """Two users with the same path on disk see disjoint lists -- the
    point of moving off the per-machine JSON file."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> tuple[str, str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            alice = User(email="alice@thias.se")
            bob = User(email="bob@thias.se")
            s.add_all([alice, bob])
            await s.commit()
            await s.refresh(alice)
            await s.refresh(bob)
            return alice.id, bob.id

    alice_id, bob_id = asyncio.run(_setup())
    alice_store = PostgresRecentProjectsStore(session_factory, user_id=alice_id)
    bob_store = PostgresRecentProjectsStore(session_factory, user_id=bob_id)

    shared_path = tmp_path / "shared"
    shared_path.mkdir()

    async def _go() -> None:
        await alice_store.record_open(shared_path, "Alice's view", kind="match")
        await bob_store.record_open(shared_path, "Bob's view", kind="match")
        # Each user sees their own row, with their own name -- the
        # unique constraint is (user_id, path), not path alone.
        alice_rows = await alice_store.list()
        bob_rows = await bob_store.list()
        assert len(alice_rows) == 1 and alice_rows[0].name == "Alice's view"
        assert len(bob_rows) == 1 and bob_rows[0].name == "Bob's view"
        # Removing as Bob touches only Bob's row, even though the
        # path is identical on disk.
        assert await bob_store.remove(shared_path) is True
        assert len(await alice_store.list()) == 1
        assert await bob_store.list() == []

    asyncio.run(_go())


def test_trim_does_not_cross_tenant_boundary(tmp_path) -> None:
    """Alice churning past the limit must not evict any of Bob's rows --
    the trim is scoped to the operating user_id."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> tuple[str, str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            alice = User(email="alice2@thias.se")
            bob = User(email="bob2@thias.se")
            s.add_all([alice, bob])
            await s.commit()
            await s.refresh(alice)
            await s.refresh(bob)
            return alice.id, bob.id

    alice_id, bob_id = asyncio.run(_setup())
    alice_store = PostgresRecentProjectsStore(session_factory, user_id=alice_id)
    bob_store = PostgresRecentProjectsStore(session_factory, user_id=bob_id)

    async def _go() -> None:
        # Seed Bob with one row that must survive Alice's churn.
        bob_path = tmp_path / "bob-only"
        bob_path.mkdir()
        await bob_store.record_open(bob_path, "Bob keep", kind="match")
        # Alice fills past the limit; her trim runs.
        for i in range(RECENT_PROJECTS_LIMIT + 3):
            p = tmp_path / f"alice-{i}"
            p.mkdir()
            await alice_store.record_open(p, f"A{i}", kind="match")
        assert len(await alice_store.list()) == RECENT_PROJECTS_LIMIT
        # Bob's row is untouched.
        bob_rows = await bob_store.list()
        assert len(bob_rows) == 1
        assert bob_rows[0].name == "Bob keep"

    asyncio.run(_go())


def test_trims_to_recent_projects_limit(tmp_path) -> None:
    """Insert one more than the limit and confirm the oldest is dropped."""
    store, session_factory = _build_store_for_new_user()

    async def _go() -> None:
        # Insert RECENT_PROJECTS_LIMIT + 5 distinct paths.
        for i in range(RECENT_PROJECTS_LIMIT + 5):
            p = tmp_path / f"project-{i}"
            p.mkdir()
            await store.record_open(p, f"P{i}", kind="match")
        rows = await store.list()
        assert len(rows) == RECENT_PROJECTS_LIMIT
        # The five oldest (project-0..project-4) should be gone.
        kept_paths = {r.path for r in rows}
        for i in range(5):
            assert str((tmp_path / f"project-{i}").resolve()) not in kept_paths

    asyncio.run(_go())


@pytest.mark.parametrize("kind", [None, "match", "legacy"])
def test_kind_round_trips_for_all_values(tmp_path, kind) -> None:
    """``kind`` is nullable + free-form on the model; the store must
    preserve whatever the caller passes (None, "match", legacy values
    surfaced from older indexes)."""
    store, _ = _build_store_for_new_user()
    target = tmp_path / "match"
    target.mkdir()

    async def _go() -> None:
        await store.record_open(target, "Test", kind=kind)
        rows = await store.list()
        assert rows[0].kind == kind

    asyncio.run(_go())
