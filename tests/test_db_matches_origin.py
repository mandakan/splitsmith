"""Tests for ``matches.origin`` (desktop-to-hosted sync MVP, task 1, #631).

Desktop-synced mirrors are tagged distinctly from natively-created hosted
matches, and re-registering an already-known match must never flip the
origin it was created with -- otherwise a routine upsert from the desktop
sync path could silently reclassify a native hosted match, or vice versa.

Runs against SQLite in-memory via aiosqlite, same pattern as
``test_matches_store.py`` -- the store has no Postgres-specific behaviour
here, so SQLite proves the SQL shape.
"""

from __future__ import annotations

import asyncio

from splitsmith.db import Base, PostgresMatchStore, User, create_engine, sessionmaker


def _store_with_user(email: str = "m@thias.se") -> PostgresMatchStore:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            user = User(email=email)
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    user_id = asyncio.run(_setup())
    return PostgresMatchStore(session_factory, user_id=user_id)


def test_upsert_sets_origin_and_never_flips_it() -> None:
    store = _store_with_user()

    rec = asyncio.run(store.upsert("bromma-abc123", "Bromma", "matches/bromma-abc123", origin="desktop"))
    assert rec.origin == "desktop"

    rec2 = asyncio.run(store.upsert("bromma-abc123", "Bromma renamed", "matches/bromma-abc123"))
    assert rec2.origin == "desktop"  # default arg must not overwrite
    assert rec2.name == "Bromma renamed"


def test_origin_defaults_to_hosted() -> None:
    store = _store_with_user()

    rec = asyncio.run(store.upsert("m2", "Native", "matches/m2"))
    assert rec.origin == "hosted"
