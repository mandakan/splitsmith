"""CommentStore behaviour + the multi-tenant isolation guard.

``test_share_tokens_store.py`` sets the precedent twice over: the
``asyncio.run`` idiom (there is no async-test plugin in this repo), and
one isolation test per store method. A method that forgets its
``user_id`` filter is the single most damaging bug this table can carry,
because the owner tenant is pinned by impersonation on every anonymous
write.
"""

from __future__ import annotations

import asyncio

import pytest

from splitsmith.db import Base, User, create_engine, sessionmaker
from splitsmith.db.comments import CommentStore


def _engine_with_owner_and_other() -> sessionmaker:
    """Fresh in-memory engine seeded with two users: 'owner' and 'other'."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = sessionmaker(engine)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as s:
            s.add(User(id="owner", email="owner@example.com"))
            s.add(User(id="other", email="other@example.com"))
            await s.commit()

    asyncio.run(_setup())
    return session_factory


def _store(factory: sessionmaker, user_id: str = "owner") -> CommentStore:
    return CommentStore(factory, user_id=user_id)


def _seed(store: CommentStore, **over) -> str:
    """Create one comment with sensible defaults; return its id."""
    kwargs = {
        "match_id": "m1",
        "slug": "alice",
        "stage_number": 3,
        "anchor_t": 4.32,
        "anchor_kind": "time",
        "anchor_shot_id": None,
        "author_kind": "handle",
        "author_user_id": None,
        "author_handle": "Prone Popper 47",
        "author_key_hash": "hash-a",
        "author_code": "ABCDEF",
        "share_token_id": "tok-1",
        "body": "reload looks early",
    }
    kwargs.update(over)
    return asyncio.run(store.create(**kwargs)).id


def _bodies(store: CommentStore, match_id: str = "m1", slug: str = "alice", stage: int = 3):
    return [c.body for c in asyncio.run(store.list_for_stage(match_id, slug, stage))]


def test_create_then_list_round_trips() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store)
    got = asyncio.run(store.list_for_stage("m1", "alice", 3))
    assert [c.body for c in got] == ["reload looks early"]
    assert got[0].anchor_t == pytest.approx(4.32)


def test_list_is_oldest_first() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store, body="first")
    _seed(store, body="second")
    assert _bodies(store) == ["first", "second"]


def test_list_is_scoped_to_stage_and_slug() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store, body="alice s3")
    _seed(store, slug="bob", body="bob s3")
    _seed(store, stage_number=4, body="alice s4")
    assert _bodies(store) == ["alice s3"]


def test_list_omits_soft_deleted() -> None:
    store = _store(_engine_with_owner_and_other())
    cid = _seed(store)
    assert asyncio.run(store.delete_as_owner(cid, match_id="m1", slug="alice", stage_number=3)) is True
    assert _bodies(store) == []


def test_delete_own_requires_the_matching_author_key() -> None:
    store = _store(_engine_with_owner_and_other())
    cid = _seed(store, author_key_hash="hash-a")
    wrong = asyncio.run(
        store.delete_own(cid, match_id="m1", slug="alice", stage_number=3, author_key_hash="hash-b")
    )
    assert wrong is False
    assert len(_bodies(store)) == 1
    right = asyncio.run(
        store.delete_own(cid, match_id="m1", slug="alice", stage_number=3, author_key_hash="hash-a")
    )
    assert right is True
    assert _bodies(store) == []


def test_delete_own_is_scoped_to_slug_and_stage_number() -> None:
    """F3 (fix round 1): the URL's slug/stage_number must be part of the
    delete predicate, not decorative. A comment posted at alice/3 must not
    delete through bob/3, alice/99, or any other (slug, stage) pairing on
    the same match."""
    store = _store(_engine_with_owner_and_other())
    cid = _seed(store, slug="alice", stage_number=3, author_key_hash="hash-a")

    wrong_slug = asyncio.run(
        store.delete_own(cid, match_id="m1", slug="bob", stage_number=3, author_key_hash="hash-a")
    )
    assert wrong_slug is False
    wrong_stage = asyncio.run(
        store.delete_own(cid, match_id="m1", slug="alice", stage_number=99, author_key_hash="hash-a")
    )
    assert wrong_stage is False
    assert len(_bodies(store)) == 1

    right = asyncio.run(
        store.delete_own(cid, match_id="m1", slug="alice", stage_number=3, author_key_hash="hash-a")
    )
    assert right is True
    assert _bodies(store) == []


def test_delete_as_owner_is_scoped_to_slug_and_stage_number() -> None:
    store = _store(_engine_with_owner_and_other())
    cid = _seed(store, slug="alice", stage_number=3)

    assert asyncio.run(store.delete_as_owner(cid, match_id="m1", slug="bob", stage_number=3)) is False
    assert asyncio.run(store.delete_as_owner(cid, match_id="m1", slug="alice", stage_number=99)) is False
    assert len(_bodies(store)) == 1

    assert asyncio.run(store.delete_as_owner(cid, match_id="m1", slug="alice", stage_number=3)) is True
    assert _bodies(store) == []


def test_bulk_delete_by_share_token() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store, share_token_id="tok-1", body="from link 1")
    _seed(store, share_token_id="tok-2", body="from link 2")
    assert asyncio.run(store.delete_by_share_token("m1", "tok-1")) == 1
    assert _bodies(store) == ["from link 2"]


def test_bulk_delete_by_author_key_hash() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store, author_key_hash="hash-a", body="nuisance")
    _seed(store, author_key_hash="hash-b", body="fine")
    assert asyncio.run(store.delete_by_author_key_hash("m1", "hash-a")) == 1
    assert _bodies(store) == ["fine"]


def test_purge_match_hard_deletes_every_stage() -> None:
    store = _store(_engine_with_owner_and_other())
    _seed(store, stage_number=3)
    _seed(store, stage_number=4, slug="bob")
    _seed(store, match_id="m2", body="other match")
    assert asyncio.run(store.purge_match("m1")) == 2
    assert _bodies(store) == []
    assert len(_bodies(store, match_id="m2")) == 1


def test_purge_match_removes_already_soft_deleted_rows() -> None:
    """Soft-deleted rows still hold a body. A match delete must take them
    too, or 'delete my match' leaves text behind."""
    store = _store(_engine_with_owner_and_other())
    cid = _seed(store)
    asyncio.run(store.delete_as_owner(cid, match_id="m1", slug="alice", stage_number=3))
    assert asyncio.run(store.purge_match("m1")) == 1


def test_count_for_stage_ignores_deleted() -> None:
    store = _store(_engine_with_owner_and_other())
    cid = _seed(store)
    assert asyncio.run(store.count_for_stage("m1", "alice", 3)) == 1
    asyncio.run(store.delete_as_owner(cid, match_id="m1", slug="alice", stage_number=3))
    assert asyncio.run(store.count_for_stage("m1", "alice", 3)) == 0


# --- isolation: one per method -------------------------------------------


def test_list_is_isolated_by_user() -> None:
    sf = _engine_with_owner_and_other()
    _seed(_store(sf, "owner"))
    assert asyncio.run(_store(sf, "other").list_for_stage("m1", "alice", 3)) == []


def test_delete_own_is_isolated_by_user() -> None:
    sf = _engine_with_owner_and_other()
    cid = _seed(_store(sf, "owner"))
    stolen = asyncio.run(
        _store(sf, "other").delete_own(
            cid, match_id="m1", slug="alice", stage_number=3, author_key_hash="hash-a"
        )
    )
    assert stolen is False
    assert len(_bodies(_store(sf, "owner"))) == 1


def test_delete_as_owner_is_isolated_by_user() -> None:
    sf = _engine_with_owner_and_other()
    cid = _seed(_store(sf, "owner"))
    assert (
        asyncio.run(_store(sf, "other").delete_as_owner(cid, match_id="m1", slug="alice", stage_number=3))
        is False
    )
    assert len(_bodies(_store(sf, "owner"))) == 1


def test_bulk_deletes_are_isolated_by_user() -> None:
    sf = _engine_with_owner_and_other()
    _seed(_store(sf, "owner"))
    other = _store(sf, "other")
    assert asyncio.run(other.delete_by_share_token("m1", "tok-1")) == 0
    assert asyncio.run(other.delete_by_author_key_hash("m1", "hash-a")) == 0
    assert len(_bodies(_store(sf, "owner"))) == 1


def test_purge_match_is_isolated_by_user() -> None:
    sf = _engine_with_owner_and_other()
    _seed(_store(sf, "owner"))
    assert asyncio.run(_store(sf, "other").purge_match("m1")) == 0
    assert len(_bodies(_store(sf, "owner"))) == 1


def test_store_refuses_an_empty_user_id() -> None:
    sf = _engine_with_owner_and_other()
    with pytest.raises(ValueError, match="non-empty user_id"):
        CommentStore(sf, user_id="")
