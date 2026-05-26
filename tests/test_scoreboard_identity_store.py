"""Tests for :class:`PostgresScoreboardIdentityStore` (doc 10, Tier 3).

Runs against SQLite in-memory via aiosqlite. Same setup pattern as
``test_recent_projects_store`` and ``test_db_foundation``. The store
itself is engine-agnostic; the tests prove the per-user filter
holds, the JSON column round-trips every field, and the
construction guard fires before any query runs.
"""

from __future__ import annotations

import asyncio

import pytest

from splitsmith.db import (
    Base,
    PostgresScoreboardIdentityStore,
    User,
    create_engine,
    sessionmaker,
)
from splitsmith.user_config import ScoreboardIdentity, ScoreboardIdentityStore


def _build_store_for_new_user(
    email: str = "picker@thias.se",
) -> tuple[PostgresScoreboardIdentityStore, sessionmaker]:
    """Spin up a fresh in-memory engine + seed one user + return the
    store bound to that user id."""
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
    return PostgresScoreboardIdentityStore(session_factory, user_id=user_id), session_factory


def test_satisfies_scoreboard_identity_protocol() -> None:
    """Structural-typing check: the new class fits the Protocol so
    handlers depending on the abstraction accept it without changes."""
    store, _ = _build_store_for_new_user()
    typed: ScoreboardIdentityStore = store
    assert typed is store


@pytest.mark.parametrize("bad", ["", None, 0, b"abc"])
def test_construction_rejects_empty_or_non_string_user_id(bad) -> None:
    """Same defence-in-depth pattern as the recent-projects store --
    silent empty queries hide auth bugs and risk leaking once a
    future helper forgets the per-user filter."""
    from splitsmith.db import sessionmaker as smaker
    from splitsmith.db.engine import create_engine as _ce

    engine = _ce("sqlite+aiosqlite:///:memory:")
    factory = smaker(engine)
    with pytest.raises(ValueError, match="non-empty user_id"):
        PostgresScoreboardIdentityStore(factory, user_id=bad)  # type: ignore[arg-type]


def test_load_returns_none_when_unpinned() -> None:
    """Freshly created user has no scoreboard identity yet -- the
    SPA's picker hits the GET endpoint on every page load and must
    not see a 404 here."""
    store, _ = _build_store_for_new_user()
    assert asyncio.run(store.load()) is None


def test_round_trip_preserves_all_fields() -> None:
    """The JSON column stores the whole pydantic dump; every field
    must come back exactly as it went in, including the optional
    ones, so the picker's prefill works for users who pinned a
    custom division + club + base_url."""
    store, _ = _build_store_for_new_user()
    identity = ScoreboardIdentity(
        shooter_id=12345,
        display_name="Mathias",
        division="Production Optics",
        club="STÖD",
        base_url="https://scoreboard.example/",
    )

    async def _go() -> None:
        await store.save(identity)
        loaded = await store.load()
        assert loaded is not None
        assert loaded.shooter_id == 12345
        assert loaded.display_name == "Mathias"
        assert loaded.division == "Production Optics"
        assert loaded.club == "STÖD"
        assert loaded.base_url == "https://scoreboard.example/"

    asyncio.run(_go())


def test_save_overwrites_prior_value() -> None:
    """The endpoint is PUT (not POST) -- second save replaces, not
    appends. Required so users can rebind themselves after a typo."""
    store, _ = _build_store_for_new_user()

    async def _go() -> None:
        await store.save(ScoreboardIdentity(shooter_id=1, display_name="first"))
        await store.save(ScoreboardIdentity(shooter_id=2, display_name="second"))
        loaded = await store.load()
        assert loaded is not None
        assert loaded.shooter_id == 2
        assert loaded.display_name == "second"

    asyncio.run(_go())


def test_clear_resets_to_none() -> None:
    store, _ = _build_store_for_new_user()

    async def _go() -> None:
        await store.save(ScoreboardIdentity(shooter_id=42))
        assert (await store.load()) is not None
        await store.clear()
        assert (await store.load()) is None

    asyncio.run(_go())


def test_clear_is_idempotent_when_already_empty() -> None:
    """The SPA's "unpin" affordance shouldn't 500 if the user clicks
    it twice; the second clear is a no-op."""
    store, _ = _build_store_for_new_user()

    async def _go() -> None:
        await store.clear()
        await store.clear()
        assert (await store.load()) is None

    asyncio.run(_go())


def test_per_user_isolation() -> None:
    """Two users pin different identities; each sees only their own.
    The boundary is enforced by the (user_id, row) PK lookup, but
    the per-method filter is the belt-and-braces."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> tuple[str, str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            alice = User(email="alice-sb@thias.se")
            bob = User(email="bob-sb@thias.se")
            s.add_all([alice, bob])
            await s.commit()
            await s.refresh(alice)
            await s.refresh(bob)
            return alice.id, bob.id

    alice_id, bob_id = asyncio.run(_setup())
    alice_store = PostgresScoreboardIdentityStore(session_factory, user_id=alice_id)
    bob_store = PostgresScoreboardIdentityStore(session_factory, user_id=bob_id)

    async def _go() -> None:
        await alice_store.save(ScoreboardIdentity(shooter_id=111, display_name="Alice"))
        # Bob hasn't pinned yet.
        assert (await bob_store.load()) is None
        # Alice still sees her own.
        alice_loaded = await alice_store.load()
        assert alice_loaded is not None and alice_loaded.shooter_id == 111

        # Bob pins his own; the two never converge.
        await bob_store.save(ScoreboardIdentity(shooter_id=222, display_name="Bob"))
        a = await alice_store.load()
        b = await bob_store.load()
        assert a is not None and a.shooter_id == 111
        assert b is not None and b.shooter_id == 222

        # Bob clearing his identity does NOT touch Alice's.
        await bob_store.clear()
        assert (await bob_store.load()) is None
        a = await alice_store.load()
        assert a is not None and a.shooter_id == 111

    asyncio.run(_go())


def test_save_fails_loud_for_missing_user() -> None:
    """If the auth layer somehow hands us a user_id that doesn't
    exist in ``users`` (e.g. an account that was hard-deleted), the
    store should raise instead of silently dropping the write -- the
    upstream invariant is broken and we want to surface that."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    store = PostgresScoreboardIdentityStore(session_factory, user_id="01H0000000000000000000DEAD")

    with pytest.raises(LookupError, match="must materialise the user row"):
        asyncio.run(store.save(ScoreboardIdentity(shooter_id=1)))
