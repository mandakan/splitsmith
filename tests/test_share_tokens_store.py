"""Tests for ShareTokenStore and resolve_share_token.

Runs against SQLite in-memory via aiosqlite - same pattern as
test_matches_store.py. The store has no Postgres-specific behaviour,
so SQLite proves the SQL shapes + the multi-tenant invariant.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select as _select

from splitsmith.db import (
    Base,
    ShareTokenRow,
    User,
    create_engine,
    sessionmaker,
)
from splitsmith.db.share_tokens import ResolvedShare, ShareToken, ShareTokenStore, resolve_share_token


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


# - create() returns a ShareToken with a 43-char urlsafe token; row lands in the table
def test_create_returns_share_token_with_43_char_token() -> None:
    sf, (uid,) = _engine_with_users("a@thias.se")
    store = ShareTokenStore(sf, user_id=uid)
    token = asyncio.run(store.create("match-1"))
    assert isinstance(token, ShareToken)
    assert len(token.token) == 43
    assert token.match_id == "match-1"
    assert token.revoked_at is None
    # Confirm the row landed in the table.
    rows = asyncio.run(store.list_for_match("match-1"))
    assert len(rows) == 1
    assert rows[0].token == token.token


# - list_for_match() returns newest first and includes revoked rows
def test_list_for_match_newest_first_includes_revoked() -> None:
    sf, (uid,) = _engine_with_users("a@thias.se")
    store = ShareTokenStore(sf, user_id=uid)
    first = asyncio.run(store.create("match-1"))
    second = asyncio.run(store.create("match-1"))
    asyncio.run(store.revoke(first.id, match_id="match-1"))
    rows = asyncio.run(store.list_for_match("match-1"))
    # Both returned (revoked included), newest first.
    assert len(rows) == 2
    assert rows[0].id == second.id
    assert rows[1].id == first.id
    assert rows[1].revoked_at is not None


# - revoke() sets revoked_at and returns True
def test_revoke_sets_revoked_at_and_returns_true() -> None:
    sf, (uid,) = _engine_with_users("a@thias.se")
    store = ShareTokenStore(sf, user_id=uid)
    token = asyncio.run(store.create("match-1"))
    assert asyncio.run(store.revoke(token.id, match_id="match-1")) is True
    rows = asyncio.run(store.list_for_match("match-1"))
    assert rows[0].revoked_at is not None


# - revoke() unknown id returns False
def test_revoke_unknown_id_returns_false() -> None:
    sf, (uid,) = _engine_with_users("a@thias.se")
    store = ShareTokenStore(sf, user_id=uid)
    assert asyncio.run(store.revoke("no-such-id", match_id="match-1")) is False


# - revoke() on another user's share id returns False (isolation)
def test_revoke_other_users_share_returns_false() -> None:
    sf, (uid_a, uid_b) = _engine_with_users("a@thias.se", "b@thias.se")
    store_a = ShareTokenStore(sf, user_id=uid_a)
    store_b = ShareTokenStore(sf, user_id=uid_b)
    token = asyncio.run(store_a.create("match-1"))
    # user-b cannot revoke user-a's share.
    assert asyncio.run(store_b.revoke(token.id, match_id="match-1")) is False
    # user-a's share is still live.
    rows = asyncio.run(store_a.list_for_match("match-1"))
    assert rows[0].revoked_at is None


# - list_for_match() never returns another user's rows (isolation)
def test_list_for_match_does_not_leak_across_users() -> None:
    sf, (uid_a, uid_b) = _engine_with_users("a@thias.se", "b@thias.se")
    asyncio.run(ShareTokenStore(sf, user_id=uid_a).create("match-1"))
    rows = asyncio.run(ShareTokenStore(sf, user_id=uid_b).list_for_match("match-1"))
    assert rows == []


# - resolve_share_token(): live token -> ResolvedShare(owner_user_id, match_id)
def test_resolve_live_token_returns_resolved_share() -> None:
    sf, (uid,) = _engine_with_users("a@thias.se")
    store = ShareTokenStore(sf, user_id=uid)
    token = asyncio.run(store.create("match-1"))
    result = asyncio.run(resolve_share_token(sf, token.token))
    assert isinstance(result, ResolvedShare)
    assert result.owner_user_id == uid
    assert result.match_id == "match-1"


# - resolve_share_token(): unknown token -> None
def test_resolve_unknown_token_returns_none() -> None:
    sf, _ = _engine_with_users("a@thias.se")
    assert asyncio.run(resolve_share_token(sf, "not-a-real-token")) is None


# - resolve_share_token(): revoked token -> None
def test_resolve_revoked_token_returns_none() -> None:
    sf, (uid,) = _engine_with_users("a@thias.se")
    store = ShareTokenStore(sf, user_id=uid)
    token = asyncio.run(store.create("match-1"))
    asyncio.run(store.revoke(token.id, match_id="match-1"))
    assert asyncio.run(resolve_share_token(sf, token.token)) is None


# - resolve_share_token(): expires_at in the past -> None (seed expires_at directly on the row)
def test_resolve_expired_token_returns_none() -> None:
    sf, (uid,) = _engine_with_users("a@thias.se")
    store = ShareTokenStore(sf, user_id=uid)
    token = asyncio.run(store.create("match-1"))

    async def _seed_expiry() -> None:
        async with sf() as s:
            row = (await s.execute(_select(ShareTokenRow).where(ShareTokenRow.id == token.id))).scalar_one()
            row.expires_at = datetime.now(UTC) - timedelta(hours=1)
            await s.commit()

    asyncio.run(_seed_expiry())
    assert asyncio.run(resolve_share_token(sf, token.token)) is None


# - revoke() with a wrong match_id returns False (match-scope guard)
def test_revoke_wrong_match_id_returns_false() -> None:
    sf, (uid,) = _engine_with_users("a@thias.se")
    store = ShareTokenStore(sf, user_id=uid)
    token = asyncio.run(store.create("match-1"))
    # Correct user_id, wrong match_id - must not revoke.
    assert asyncio.run(store.revoke(token.id, match_id="match-2")) is False
    # Share is still live.
    rows = asyncio.run(store.list_for_match("match-1"))
    assert rows[0].revoked_at is None
