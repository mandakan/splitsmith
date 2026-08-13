"""PostgresProfileStore -- the only writer of users.display_name (#867).

Follows tests/test_scoreboard_identity_store.py exactly: SQLite
in-memory via aiosqlite, sync test functions driving ``asyncio.run``.
**There is no pytest-asyncio in this project** -- ``pyproject.toml``
configures no ``asyncio_mode``, so an ``@pytest.mark.asyncio`` test
would be collected and silently skipped as an un-awaited coroutine.

The multi-tenant invariant is that every statement filters on this
store's user_id, and the isolation test is what guards it. Add one per
new method.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from splitsmith.db import Base, User, create_engine, sessionmaker
from splitsmith.db.profile import PostgresProfileStore


def _build_store_for_new_users(*emails: str) -> tuple[list[str], sessionmaker]:
    """Fresh in-memory engine + one user row per email. Returns the ids
    in the order given, plus the session factory. Mirrors
    ``_build_store_for_new_user`` in test_scoreboard_identity_store.py.
    """
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> list[str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        ids = []
        async with session_factory() as s:
            for email in emails:
                user = User(email=email)
                s.add(user)
                await s.commit()
                await s.refresh(user)
                ids.append(user.id)
        return ids

    return asyncio.run(_setup()), session_factory


def _read_display_name(session_factory: sessionmaker, user_id: str) -> str | None:
    async def _read() -> str | None:
        async with session_factory() as s:
            return (await s.execute(select(User.display_name).where(User.id == user_id))).scalar_one()

    return asyncio.run(_read())


@pytest.mark.parametrize("bad", ["", None, 0, b"abc"])
def test_construction_rejects_empty_or_non_string_user_id(bad) -> None:
    """Same defence-in-depth pattern as the scoreboard-identity store:
    a silent empty query hides an auth bug."""
    with pytest.raises(ValueError, match="non-empty user_id"):
        PostgresProfileStore(object(), user_id=bad)


def test_set_and_read_back() -> None:
    (user_id,), sf = _build_store_for_new_users("a@example.com")
    store = PostgresProfileStore(sf, user_id=user_id)

    asyncio.run(store.set_display_name("Anders Berg"))

    assert _read_display_name(sf, user_id) == "Anders Berg"


def test_clearing_writes_null() -> None:
    (user_id,), sf = _build_store_for_new_users("b@example.com")
    store = PostgresProfileStore(sf, user_id=user_id)
    asyncio.run(store.set_display_name("Anders Berg"))

    asyncio.run(store.set_display_name(None))

    assert _read_display_name(sf, user_id) is None


def test_a_write_never_touches_another_users_row() -> None:
    """The multi-tenant invariant. Two stores, two users, one write."""
    (alice, bob), sf = _build_store_for_new_users("alice@example.com", "bob@example.com")
    asyncio.run(PostgresProfileStore(sf, user_id=bob).set_display_name("Bob"))

    asyncio.run(PostgresProfileStore(sf, user_id=alice).set_display_name("Alice"))

    assert _read_display_name(sf, alice) == "Alice"
    assert _read_display_name(sf, bob) == "Bob"


def test_a_missing_user_raises() -> None:
    """The auth layer materialises the row before the handler runs. If it
    did not, that is an invariant violation worth surfacing rather than a
    silent no-op that reads as a successful save."""
    _ids, sf = _build_store_for_new_users("present@example.com")
    store = PostgresProfileStore(sf, user_id="01JNOTAREALUSERID000000000")

    with pytest.raises(LookupError, match="not found"):
        asyncio.run(store.set_display_name("Ghost"))
