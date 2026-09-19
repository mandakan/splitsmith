"""``PostgresYouTubeConnectionStore`` (issue #1000, phase 2), against
SQLite via aiosqlite like the sibling store tests. Proves: the row never
holds the plaintext refresh token; ``connection`` and ``pending`` are
independent blocks; a malformed block reads as absent; and two users
through one engine never see each other's column (one isolation case
per method)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from splitsmith.db import Base, PostgresYouTubeConnectionStore, User, create_engine, sessionmaker
from splitsmith.db.youtube_connections import PendingLogin, StoredYouTubeConnection
from splitsmith.youtube import sealed
from splitsmith.youtube.oauth import YouTubeConnection


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sealed.ENV_TOKEN_KEY, sealed.generate_key())


def _engine_with_users(*emails: str) -> tuple[sessionmaker, list[str]]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = sessionmaker(engine)

    async def _setup() -> list[str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        ids = []
        async with factory() as s:
            for email in emails:
                user = User(email=email)
                s.add(user)
                await s.commit()
                await s.refresh(user)
                ids.append(user.id)
        return ids

    return factory, asyncio.run(_setup())


def _store() -> tuple[PostgresYouTubeConnectionStore, sessionmaker, str]:
    factory, (uid,) = _engine_with_users("a@example.com")
    return PostgresYouTubeConnectionStore(factory, user_id=uid), factory, uid


def _two() -> tuple[PostgresYouTubeConnectionStore, PostgresYouTubeConnectionStore]:
    factory, (a, b) = _engine_with_users("a@example.com", "b@example.com")
    return (
        PostgresYouTubeConnectionStore(factory, user_id=a),
        PostgresYouTubeConnectionStore(factory, user_id=b),
    )


def _conn(title: str = "Chan") -> YouTubeConnection:
    return YouTubeConnection(
        refresh_token="1//secret-refresh",
        channel_id="UC1",
        channel_title=title,
        connected_at=datetime(2026, 9, 19, 10, 0, tzinfo=UTC),
    )


def _pending(state: str = "st") -> PendingLogin:
    return PendingLogin(state=state, code_verifier="ver", started_at=datetime(2026, 9, 19, 10, 0, tzinfo=UTC))


async def _raw_column(factory: sessionmaker, uid: str) -> dict | None:
    async with factory() as s:
        return (await s.execute(select(User.youtube_connection).where(User.id == uid))).scalar_one()


@pytest.mark.parametrize("bad", ["", None, 0])
def test_construction_rejects_empty_user_id(bad) -> None:
    factory, _ = _engine_with_users()
    with pytest.raises(ValueError, match="non-empty user_id"):
        PostgresYouTubeConnectionStore(factory, user_id=bad)  # type: ignore[arg-type]


def test_fresh_user_has_no_connection_and_no_pending() -> None:
    store, _, _ = _store()
    assert asyncio.run(store.get()) is None
    assert asyncio.run(store.get_pending()) is None


def test_round_trip_seals_the_token_and_opens_it_back() -> None:
    store, factory, uid = _store()

    async def _go() -> None:
        await store.set(StoredYouTubeConnection.seal_from(_conn("Mine")))
        stored = await store.get()
        assert stored is not None
        assert stored.channel_title == "Mine"
        assert stored.refresh_token_sealed != "1//secret-refresh"
        opened = stored.open()
        assert opened.refresh_token == "1//secret-refresh"
        assert opened.connected_at == _conn().connected_at
        assert opened.scopes == _conn().scopes
        raw = await _raw_column(factory, uid)
        assert "1//secret-refresh" not in repr(raw)

    asyncio.run(_go())


def test_connection_and_pending_are_independent_blocks() -> None:
    store, _, _ = _store()

    async def _go() -> None:
        await store.set_pending(_pending("one"))
        await store.set(StoredYouTubeConnection.seal_from(_conn()))
        assert (await store.get_pending()).state == "one"
        assert (await store.get()) is not None
        await store.clear_pending()
        assert await store.get_pending() is None
        assert (await store.get()) is not None
        await store.set_pending(_pending("two"))
        await store.clear()
        assert await store.get() is None
        assert (await store.get_pending()).state == "two"

    asyncio.run(_go())


def test_set_pending_overwrites_and_keeps_error() -> None:
    store, _, _ = _store()

    async def _go() -> None:
        await store.set_pending(_pending("one"))
        await store.set_pending(_pending("two").model_copy(update={"error": "nope"}))
        p = await store.get_pending()
        assert p is not None and p.state == "two" and p.error == "nope"

    asyncio.run(_go())


def test_clearing_everything_nulls_the_column() -> None:
    store, factory, uid = _store()

    async def _go() -> None:
        await store.set(StoredYouTubeConnection.seal_from(_conn()))
        await store.clear()
        assert await _raw_column(factory, uid) is None

    asyncio.run(_go())


def test_malformed_blocks_read_as_absent() -> None:
    store, factory, uid = _store()

    async def _go() -> None:
        async with factory() as s:
            user = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            user.youtube_connection = {"connection": {"nonsense": 1}, "pending": "not-a-dict"}
            await s.commit()
        assert await store.get() is None
        assert await store.get_pending() is None

    asyncio.run(_go())


def test_writes_to_a_missing_user_fail_loud() -> None:
    factory, _ = _engine_with_users()
    store = PostgresYouTubeConnectionStore(factory, user_id="ghost")
    with pytest.raises(LookupError, match="not found"):
        asyncio.run(store.set_pending(_pending()))


# --- isolation: one case per method ----------------------------------------


def test_isolation_get_and_set() -> None:
    a, b = _two()

    async def _go() -> None:
        await a.set(StoredYouTubeConnection.seal_from(_conn("A's")))
        assert await b.get() is None
        await b.set(StoredYouTubeConnection.seal_from(_conn("B's")))
        assert (await a.get()).channel_title == "A's"
        assert (await b.get()).channel_title == "B's"

    asyncio.run(_go())


def test_isolation_clear() -> None:
    a, b = _two()

    async def _go() -> None:
        await a.set(StoredYouTubeConnection.seal_from(_conn("A's")))
        await b.set(StoredYouTubeConnection.seal_from(_conn("B's")))
        await b.clear()
        assert (await a.get()).channel_title == "A's"
        assert await b.get() is None

    asyncio.run(_go())


def test_isolation_pending() -> None:
    a, b = _two()

    async def _go() -> None:
        await a.set_pending(_pending("a-state"))
        assert await b.get_pending() is None
        await b.set_pending(_pending("b-state"))
        await b.clear_pending()
        assert (await a.get_pending()).state == "a-state"
        assert await b.get_pending() is None

    asyncio.run(_go())
