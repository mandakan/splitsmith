"""Tests for :class:`PostgresMatchStore` (PR-delta).

Runs against SQLite in-memory via aiosqlite -- same pattern as the
other per-user store tests. The store has no Postgres-specific
behaviour, so SQLite proves the SQL shapes + the multi-tenant
invariant.
"""

from __future__ import annotations

import asyncio

import pytest

from splitsmith.db import (
    Base,
    PostgresMatchStore,
    User,
    create_engine,
    sessionmaker,
)


def _engine_with_users(*emails: str) -> tuple[sessionmaker, list[str]]:
    """Fresh in-memory engine + seed one user per email; return ids."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> list[str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        ids: list[str] = []
        async with session_factory() as s:
            for email in emails:
                user = User(email=email)
                s.add(user)
                await s.commit()
                await s.refresh(user)
                ids.append(user.id)
        return ids

    return session_factory, asyncio.run(_setup())


@pytest.mark.parametrize("bad", ["", None, 0, b"abc"])
def test_construction_rejects_empty_or_non_string_user_id(bad) -> None:
    """Fail loud at construction so a None/empty user_id from a buggy
    auth layer can't silently scope every query to "no rows"."""
    sf, _ = _engine_with_users("a@thias.se")
    with pytest.raises(ValueError, match="non-empty user_id"):
        PostgresMatchStore(sf, user_id=bad)


def test_upsert_get_roundtrip() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = PostgresMatchStore(sf, user_id=uid)

    assert asyncio.run(store.get("brm-abc")) is None
    asyncio.run(store.upsert("brm-abc", "Bromma", "matches/brm-abc"))
    row = asyncio.run(store.get("brm-abc"))
    assert row is not None
    assert (row.match_id, row.name, row.storage_prefix) == ("brm-abc", "Bromma", "matches/brm-abc")


def test_upsert_is_idempotent_and_updates() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = PostgresMatchStore(sf, user_id=uid)

    asyncio.run(store.upsert("brm-abc", "Bromma", "matches/brm-abc"))
    asyncio.run(store.upsert("brm-abc", "Bromma 2026", "matches/brm-abc"))
    rows = asyncio.run(store.list())
    assert len(rows) == 1  # no duplicate row
    assert rows[0].name == "Bromma 2026"


def test_tenant_isolation_same_match_id() -> None:
    """Two users registering the same ``match_id`` own disjoint rows;
    neither can see or overwrite the other's."""
    sf, (alice, bob) = _engine_with_users("alice@thias.se", "bob@thias.se")
    a_store = PostgresMatchStore(sf, user_id=alice)
    b_store = PostgresMatchStore(sf, user_id=bob)

    asyncio.run(a_store.upsert("shared-id", "Alice Match", "matches/shared-id"))
    asyncio.run(b_store.upsert("shared-id", "Bob Match", "matches/shared-id"))

    assert asyncio.run(a_store.get("shared-id")).name == "Alice Match"
    assert asyncio.run(b_store.get("shared-id")).name == "Bob Match"
    assert len(asyncio.run(a_store.list())) == 1
    assert len(asyncio.run(b_store.list())) == 1


def test_get_does_not_leak_across_users() -> None:
    sf, (alice, bob) = _engine_with_users("alice@thias.se", "bob@thias.se")
    asyncio.run(PostgresMatchStore(sf, user_id=alice).upsert("a-only", "A", "matches/a-only"))

    # Bob has no such match -> clean None, not Alice's row.
    assert asyncio.run(PostgresMatchStore(sf, user_id=bob).get("a-only")) is None


def test_delete_removes_row() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = PostgresMatchStore(sf, user_id=uid)
    asyncio.run(store.upsert("brm-abc", "Bromma", "matches/brm-abc"))

    assert asyncio.run(store.delete("brm-abc")) is True
    assert asyncio.run(store.get("brm-abc")) is None


def test_delete_absent_returns_false() -> None:
    """Idempotent: deleting an already-gone match is a clean no-op."""
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = PostgresMatchStore(sf, user_id=uid)
    assert asyncio.run(store.delete("never-existed")) is False


def test_delete_tenant_isolation_same_match_id() -> None:
    """Deleting one user's row for a shared ``match_id`` leaves the other's."""
    sf, (alice, bob) = _engine_with_users("alice@thias.se", "bob@thias.se")
    a_store = PostgresMatchStore(sf, user_id=alice)
    b_store = PostgresMatchStore(sf, user_id=bob)
    asyncio.run(a_store.upsert("shared-id", "Alice Match", "matches/shared-id"))
    asyncio.run(b_store.upsert("shared-id", "Bob Match", "matches/shared-id"))

    assert asyncio.run(a_store.delete("shared-id")) is True

    assert asyncio.run(a_store.get("shared-id")) is None
    bob_row = asyncio.run(b_store.get("shared-id"))
    assert bob_row is not None and bob_row.name == "Bob Match"
