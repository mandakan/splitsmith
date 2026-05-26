"""Smoke tests for the SaaS-foundation DB layer (doc 02).

Runs against SQLite in-memory via aiosqlite so the test suite
stays Postgres-free. The same model code + Alembic migrations
target Postgres in production; CI / hosted-mode set
``SPLITSMITH_DATABASE_URL`` to override the engine URL.

Async test style: ``asyncio.run`` inside sync functions. Matches
the pattern in ``test_auth.py`` and avoids pulling in
``pytest-asyncio`` for a small DB test surface.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from splitsmith.db import Base, User, create_engine, new_ulid, sessionmaker


def _build_in_memory_engine():
    """Construct an in-memory SQLite engine and create tables."""

    engine = create_engine("sqlite+aiosqlite:///:memory:")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    return engine


def test_new_ulid_returns_distinct_sortable_ids() -> None:
    """ULIDs are time-ordered, monotonically increasing within a
    single run. ``new_ulid`` should produce fresh ids each call."""
    a = new_ulid()
    b = new_ulid()
    assert a != b
    # ULIDs are 26 chars (Crockford base32).
    assert len(a) == 26
    assert a < b  # second call is strictly later in lexicographic order


def test_user_round_trip_against_sqlite_in_memory() -> None:
    """Insert a user, commit, read it back by email. Proves the
    engine + model + session machinery actually works end-to-end
    against a real (in-memory) database."""
    engine = _build_in_memory_engine()
    session_factory = sessionmaker(engine)

    async def _round_trip():
        async with session_factory() as s:
            user = User(email="m@thias.se", display_name="Mathias")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

        # Re-open the session to prove the row persisted past the
        # original write transaction.

    user_id = asyncio.run(_round_trip())
    assert len(user_id) == 26  # ULID string PK

    async def _read_back():
        async with session_factory() as s:
            stmt = select(User).where(User.email == "m@thias.se")
            result = await s.execute(stmt)
            return result.scalar_one()

    found = asyncio.run(_read_back())
    assert found.id == user_id
    assert found.email == "m@thias.se"
    assert found.display_name == "Mathias"
    # Defaults wire correctly.
    assert found.entitlement == "free"
    assert found.email_verified_at is None
    assert found.deleted_at is None
    # ``server_default=now()`` produces a real timestamp on insert.
    assert isinstance(found.created_at, datetime)


def test_email_unique_constraint_rejects_duplicates() -> None:
    """The ``users.email`` column is UNIQUE per doc 02 because
    magic-link auth resolves by email -- two users sharing an
    email would render the auth flow ambiguous."""
    engine = _build_in_memory_engine()
    session_factory = sessionmaker(engine)

    async def _insert(email: str) -> None:
        async with session_factory() as s:
            s.add(User(email=email))
            await s.commit()

    asyncio.run(_insert("dup@thias.se"))
    with pytest.raises(IntegrityError):
        asyncio.run(_insert("dup@thias.se"))


def test_migration_creates_users_table_on_clean_sqlite(tmp_path) -> None:
    """The Alembic migration we generated should apply cleanly to
    an empty database. This is the gate that proves the migration
    isn't drifting from the model definitions -- if someone adds
    a column to ``User`` without ``alembic revision --autogenerate``,
    this test fails.

    Runs against a file-backed SQLite so Alembic's process can
    open its own connection (in-memory dbs don't share across
    connections).
    """
    import os
    import subprocess

    db_path = tmp_path / "smoke.sqlite"
    url = f"sqlite+aiosqlite:///{db_path}"

    env = {**os.environ, "SPLITSMITH_DATABASE_URL": url}
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        env=env,
        cwd="/Users/mathias/work/splitsmith",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    # Verify the schema landed: try to insert a row via the ORM
    # against the same file. If the migration didn't create the
    # table or used the wrong column types, this fails loudly.
    engine = create_engine(url)
    session_factory = sessionmaker(engine)

    async def _smoke():
        async with session_factory() as s:
            s.add(User(email="post-migration@thias.se"))
            await s.commit()
        await engine.dispose()

    asyncio.run(_smoke())
