"""Tests for :class:`DesktopTokenStore` / :class:`DesktopTokenAuth` (#631).

Runs against SQLite in-memory via aiosqlite - same pattern as the other
per-user store tests (see ``test_matches_store.py``). Sync ``def
test_...()`` wrapping ``asyncio.run(...)``: this repo has no
pytest-asyncio, so a literal ``async def`` test would silently not run.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from fastapi import Request
from sqlalchemy import select

from splitsmith.db import Base, DesktopTokenRow, User, create_engine, sessionmaker
from splitsmith.db.desktop_tokens import DesktopTokenAuth, DesktopTokenRecord, DesktopTokenStore
from tests.hosted_helpers import login


def _engine_with_user(email: str = "m@thias.se") -> tuple[sessionmaker, str]:
    """Fresh in-memory engine + one seeded user; return (session_factory, user_id)."""
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

    return session_factory, asyncio.run(_setup())


def _store_and_auth() -> tuple[DesktopTokenStore, DesktopTokenAuth, sessionmaker, str]:
    """A store + auth backend sharing one engine and one known user id."""
    sf, uid = _engine_with_user()
    return DesktopTokenStore(sf, user_id=uid), DesktopTokenAuth(sf), sf, uid


def _fetch_row(session_factory: sessionmaker, token_id: str) -> DesktopTokenRow:
    async def _fetch() -> DesktopTokenRow:
        async with session_factory() as s:
            return (
                await s.execute(select(DesktopTokenRow).where(DesktopTokenRow.id == token_id))
            ).scalar_one()

    return asyncio.run(_fetch())


def _request_with_bearer(raw: str) -> Request:
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {raw}".encode())],
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# DesktopTokenStore
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", None, 0, b"abc"])
def test_construction_rejects_empty_or_non_string_user_id(bad) -> None:
    sf, _ = _engine_with_user()
    with pytest.raises(ValueError, match="non-empty user_id"):
        DesktopTokenStore(sf, user_id=bad)


def test_create_returns_raw_token_and_hashes_at_rest() -> None:
    store, _auth, sf, _uid = _store_and_auth()

    rec, raw = asyncio.run(store.create("mac studio"))
    assert isinstance(rec, DesktopTokenRecord)
    assert raw and raw not in (rec.id, rec.name)

    row = _fetch_row(sf, rec.id)
    assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
    # Never exposed on the record.
    assert not hasattr(rec, "token_hash")


def test_list_returns_records_never_the_hash() -> None:
    store, _auth, _sf, _uid = _store_and_auth()
    asyncio.run(store.create("mac studio"))
    asyncio.run(store.create("linux box"))

    records = asyncio.run(store.list())
    assert len(records) == 2
    names = {r.name for r in records}
    assert names == {"mac studio", "linux box"}
    for r in records:
        assert not hasattr(r, "token_hash")


def test_list_only_returns_the_owning_users_tokens() -> None:
    sf, uid_a = _engine_with_user("a@example.com")

    async def _add_second_user() -> str:
        async with sf() as s:
            user = User(email="b@example.com")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    uid_b = asyncio.run(_add_second_user())

    store_a = DesktopTokenStore(sf, user_id=uid_a)
    store_b = DesktopTokenStore(sf, user_id=uid_b)
    asyncio.run(store_a.create("a's token"))
    asyncio.run(store_b.create("b's token"))

    records_a = asyncio.run(store_a.list())
    assert [r.name for r in records_a] == ["a's token"]


def test_revoke_unknown_id_returns_false() -> None:
    store, _auth, _sf, _uid = _store_and_auth()
    assert asyncio.run(store.revoke("no-such-id")) is False


def test_revoke_another_users_token_returns_false() -> None:
    sf, uid_a = _engine_with_user("a@example.com")

    async def _add_second_user() -> str:
        async with sf() as s:
            user = User(email="b@example.com")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    uid_b = asyncio.run(_add_second_user())
    store_a = DesktopTokenStore(sf, user_id=uid_a)
    store_b = DesktopTokenStore(sf, user_id=uid_b)
    rec, _raw = asyncio.run(store_a.create("a's token"))

    assert asyncio.run(store_b.revoke(rec.id)) is False


# ---------------------------------------------------------------------------
# DesktopTokenAuth
# ---------------------------------------------------------------------------


def test_authenticate_request_resolves_the_owning_user() -> None:
    store, auth, _sf, uid = _store_and_auth()
    _rec, raw = asyncio.run(store.create("t"))

    user = asyncio.run(auth.authenticate_request(_request_with_bearer(raw)))
    assert user is not None
    assert user.id == uid


def test_revoked_token_stops_authenticating() -> None:
    store, auth, _sf, _uid = _store_and_auth()
    rec, raw = asyncio.run(store.create("t"))

    assert asyncio.run(auth.authenticate_request(_request_with_bearer(raw))) is not None
    assert asyncio.run(store.revoke(rec.id)) is True
    assert asyncio.run(auth.authenticate_request(_request_with_bearer(raw))) is None


def test_garbage_bearer_is_none_not_error() -> None:
    _store, auth, _sf, _uid = _store_and_auth()
    assert asyncio.run(auth.authenticate_request(_request_with_bearer("nonsense"))) is None


def test_missing_authorization_header_is_none() -> None:
    _store, auth, _sf, _uid = _store_and_auth()
    request = Request({"type": "http", "headers": []})
    assert asyncio.run(auth.authenticate_request(request)) is None


def test_non_bearer_scheme_is_none() -> None:
    _store, auth, _sf, _uid = _store_and_auth()
    request = Request({"type": "http", "headers": [(b"authorization", b"Basic dXNlcjpwYXNz")]})
    assert asyncio.run(auth.authenticate_request(request)) is None


def test_authenticate_stamps_last_used_at() -> None:
    store, auth, sf, _uid = _store_and_auth()
    rec, raw = asyncio.run(store.create("t"))

    before = _fetch_row(sf, rec.id)
    assert before.last_used_at is None

    asyncio.run(auth.authenticate_request(_request_with_bearer(raw)))

    after = _fetch_row(sf, rec.id)
    assert after.last_used_at is not None


# ---------------------------------------------------------------------------
# End-to-end: a bearer token authenticates through the real hosted app gate
# (CompositeAuth), with no session cookie at all.
# ---------------------------------------------------------------------------


def test_bearer_token_authenticates_the_gate_with_no_cookie(hosted_app, hosted_env: str) -> None:
    client, sender = hosted_app

    login(client, sender, "m@thias.se")
    me = client.get("/api/me")
    assert me.status_code == 200
    user_id = me.json()["id"]

    engine = create_engine(hosted_env)
    sf = sessionmaker(engine)
    raw = "test-raw-desktop-token-value"

    async def _insert() -> None:
        async with sf() as s:
            row = DesktopTokenRow(
                user_id=user_id,
                name="ci box",
                token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            )
            s.add(row)
            await s.commit()

    asyncio.run(_insert())

    # No session cookie at all - the bearer token alone must authenticate.
    client.cookies.clear()
    ok = client.get("/api/me/recent-projects", headers={"Authorization": f"Bearer {raw}"})
    assert ok.status_code == 200

    bad = client.get("/api/me/recent-projects", headers={"Authorization": "Bearer garbage-token"})
    assert bad.status_code == 401
