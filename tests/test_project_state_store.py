"""Tests for :class:`ProjectStateStore` (state refactor, Phase 1).

Runs against SQLite in-memory via aiosqlite -- same pattern as the other
per-user store tests. The store has no Postgres-specific behaviour beyond
the expression unique index (proven by ``pytest -m docker``), so SQLite
proves the load/save/version SQL shapes + the multi-tenant invariant.
"""

from __future__ import annotations

import asyncio

import pytest

from splitsmith.db import (
    Base,
    ProjectStateStore,
    StateConflictError,
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
    sf, _ = _engine_with_users("a@thias.se")
    with pytest.raises(ValueError, match="non-empty user_id"):
        ProjectStateStore(sf, user_id=bad)


# -- match doc ----------------------------------------------------------


def test_match_load_absent_then_insert_then_load() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)

    assert asyncio.run(store.load_match("brm-abc")) == (None, 0)
    v = asyncio.run(store.save_match("brm-abc", {"name": "Bromma"}, expected_version=0))
    assert v == 1
    assert asyncio.run(store.load_match("brm-abc")) == ({"name": "Bromma"}, 1)


def test_match_save_bumps_version() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)

    asyncio.run(store.save_match("brm-abc", {"name": "Bromma"}, expected_version=0))
    v2 = asyncio.run(store.save_match("brm-abc", {"name": "Bromma 2026"}, expected_version=1))
    assert v2 == 2
    assert asyncio.run(store.load_match("brm-abc")) == ({"name": "Bromma 2026"}, 2)


def test_insert_conflict_when_already_exists() -> None:
    """A second expected_version==0 save for the same identity is a
    genuine creation race -> StateConflictError, not a silent second row.

    Tested with an *audit* doc on purpose: its identity columns (slug +
    stage_number) are both non-NULL, so SQLite's plain unique index
    catches the duplicate here. The match/project kinds carry NULL
    slug/stage, where only Postgres's coalesce expression index bites --
    that NULL case is proven in the ``pytest -m docker`` suite, not here.
    """
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)

    asyncio.run(store.save_audit("brm-abc", "alice", 1, {"a": 1}, expected_version=0))
    with pytest.raises(StateConflictError):
        asyncio.run(store.save_audit("brm-abc", "alice", 1, {"a": 2}, expected_version=0))


def test_match_stale_update_raises_conflict() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)

    asyncio.run(store.save_match("brm-abc", {"a": 1}, expected_version=0))  # v1
    asyncio.run(store.save_match("brm-abc", {"a": 2}, expected_version=1))  # v2
    # A writer still holding v1 tries to save -> loses the race.
    with pytest.raises(StateConflictError):
        asyncio.run(store.save_match("brm-abc", {"a": 3}, expected_version=1))
    # The winning write is intact.
    assert asyncio.run(store.load_match("brm-abc")) == ({"a": 2}, 2)


def test_update_missing_row_raises_conflict() -> None:
    """expected_version>0 against a row that never existed is a conflict,
    not a silent no-op."""
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)
    with pytest.raises(StateConflictError):
        asyncio.run(store.save_match("ghost", {"a": 1}, expected_version=1))


# -- project doc (slug set) --------------------------------------------


def test_project_roundtrip_and_distinct_slugs() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)

    assert asyncio.run(store.load_project("brm-abc", "alice")) == (None, 0)
    asyncio.run(store.save_project("brm-abc", "alice", {"shooter": "Alice"}, expected_version=0))
    asyncio.run(store.save_project("brm-abc", "bob", {"shooter": "Bob"}, expected_version=0))

    assert asyncio.run(store.load_project("brm-abc", "alice")) == ({"shooter": "Alice"}, 1)
    assert asyncio.run(store.load_project("brm-abc", "bob")) == ({"shooter": "Bob"}, 1)


def test_match_and_project_coexist_for_same_match_id() -> None:
    """The match doc (NULL slug) and a project doc (slug set) for the same
    match_id are distinct rows -- the NULL-slug match row must not collide
    with or be returned for a project load."""
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)

    asyncio.run(store.save_match("brm-abc", {"kind": "match"}, expected_version=0))
    asyncio.run(store.save_project("brm-abc", "alice", {"kind": "project"}, expected_version=0))

    assert asyncio.run(store.load_match("brm-abc")) == ({"kind": "match"}, 1)
    assert asyncio.run(store.load_project("brm-abc", "alice")) == ({"kind": "project"}, 1)


# -- audit doc (slug + stage set) --------------------------------------


def test_audit_roundtrip_and_distinct_stages() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)

    assert asyncio.run(store.load_audit("brm-abc", "alice", 1)) == (None, 0)
    asyncio.run(store.save_audit("brm-abc", "alice", 1, {"shots": [1]}, expected_version=0))
    asyncio.run(store.save_audit("brm-abc", "alice", 2, {"shots": [2]}, expected_version=0))

    assert asyncio.run(store.load_audit("brm-abc", "alice", 1)) == ({"shots": [1]}, 1)
    assert asyncio.run(store.load_audit("brm-abc", "alice", 2)) == ({"shots": [2]}, 1)
    # Stage 1 update doesn't disturb stage 2.
    asyncio.run(store.save_audit("brm-abc", "alice", 1, {"shots": [1, 1]}, expected_version=1))
    assert asyncio.run(store.load_audit("brm-abc", "alice", 1)) == ({"shots": [1, 1]}, 2)
    assert asyncio.run(store.load_audit("brm-abc", "alice", 2)) == ({"shots": [2]}, 1)


# -- multi-tenant isolation, per kind ----------------------------------


def test_tenant_isolation_match() -> None:
    sf, (alice, bob) = _engine_with_users("alice@thias.se", "bob@thias.se")
    a, b = ProjectStateStore(sf, user_id=alice), ProjectStateStore(sf, user_id=bob)

    asyncio.run(a.save_match("shared", {"who": "alice"}, expected_version=0))
    asyncio.run(b.save_match("shared", {"who": "bob"}, expected_version=0))

    assert asyncio.run(a.load_match("shared")) == ({"who": "alice"}, 1)
    assert asyncio.run(b.load_match("shared")) == ({"who": "bob"}, 1)


def test_tenant_isolation_project() -> None:
    sf, (alice, bob) = _engine_with_users("alice@thias.se", "bob@thias.se")
    a, b = ProjectStateStore(sf, user_id=alice), ProjectStateStore(sf, user_id=bob)

    asyncio.run(a.save_project("shared", "x", {"who": "alice"}, expected_version=0))
    asyncio.run(b.save_project("shared", "x", {"who": "bob"}, expected_version=0))

    assert asyncio.run(a.load_project("shared", "x")) == ({"who": "alice"}, 1)
    assert asyncio.run(b.load_project("shared", "x")) == ({"who": "bob"}, 1)


def test_tenant_isolation_audit() -> None:
    sf, (alice, bob) = _engine_with_users("alice@thias.se", "bob@thias.se")
    a, b = ProjectStateStore(sf, user_id=alice), ProjectStateStore(sf, user_id=bob)

    asyncio.run(a.save_audit("shared", "x", 1, {"who": "alice"}, expected_version=0))
    asyncio.run(b.save_audit("shared", "x", 1, {"who": "bob"}, expected_version=0))

    assert asyncio.run(a.load_audit("shared", "x", 1)) == ({"who": "alice"}, 1)
    assert asyncio.run(b.load_audit("shared", "x", 1)) == ({"who": "bob"}, 1)


def test_load_does_not_leak_across_users() -> None:
    sf, (alice, bob) = _engine_with_users("alice@thias.se", "bob@thias.se")
    asyncio.run(ProjectStateStore(sf, user_id=alice).save_match("a-only", {"x": 1}, expected_version=0))

    assert asyncio.run(ProjectStateStore(sf, user_id=bob).load_match("a-only")) == (None, 0)


# -- cascade cleanup ----------------------------------------------------


def _seed_full_match(store: ProjectStateStore, match_id: str) -> None:
    """A match doc + two shooters' project docs + one audit doc."""
    asyncio.run(store.save_match(match_id, {"name": match_id}, expected_version=0))
    asyncio.run(
        store.save_project(
            match_id,
            "ada",
            {"raw_videos": [{"storage_path": "raw/ada.mp4"}]},
            expected_version=0,
        )
    )
    asyncio.run(
        store.save_project(
            match_id,
            "bo",
            {"raw_videos": [{"storage_path": "raw/bo.mp4"}]},
            expected_version=0,
        )
    )
    asyncio.run(store.save_audit(match_id, "ada", 1, {"shots": []}, expected_version=0))


def test_list_project_docs_returns_slug_and_doc() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)
    _seed_full_match(store, "brm-abc")

    docs = dict(asyncio.run(store.list_project_docs("brm-abc")))
    assert set(docs) == {"ada", "bo"}
    assert docs["ada"]["raw_videos"][0]["storage_path"] == "raw/ada.mp4"


def test_delete_match_removes_all_kinds() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)
    _seed_full_match(store, "brm-abc")

    # match + 2 project + 1 audit = 4 docs.
    assert asyncio.run(store.delete_match("brm-abc")) == 4

    assert asyncio.run(store.load_match("brm-abc")) == (None, 0)
    assert asyncio.run(store.load_project("brm-abc", "ada")) == (None, 0)
    assert asyncio.run(store.load_audit("brm-abc", "ada", 1)) == (None, 0)
    assert asyncio.run(store.list_project_docs("brm-abc")) == []


def test_delete_match_absent_returns_zero() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)
    assert asyncio.run(store.delete_match("never-existed")) == 0


def test_delete_match_tenant_isolation() -> None:
    """Deleting one user's match docs leaves another user's same-id docs."""
    sf, (alice, bob) = _engine_with_users("alice@thias.se", "bob@thias.se")
    a = ProjectStateStore(sf, user_id=alice)
    b = ProjectStateStore(sf, user_id=bob)
    _seed_full_match(a, "shared")
    _seed_full_match(b, "shared")

    assert asyncio.run(a.delete_match("shared")) == 4

    assert asyncio.run(a.load_match("shared")) == (None, 0)
    assert asyncio.run(b.load_match("shared")) == ({"name": "shared"}, 1)
    assert len(asyncio.run(b.list_project_docs("shared"))) == 2


def test_delete_shooter_removes_project_and_audit_only() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)
    _seed_full_match(store, "brm-abc")

    # ada's project doc + ada's stage-1 audit doc = 2.
    assert asyncio.run(store.delete_shooter("brm-abc", "ada")) == 2

    assert asyncio.run(store.load_project("brm-abc", "ada")) == (None, 0)
    assert asyncio.run(store.load_audit("brm-abc", "ada", 1)) == (None, 0)
    # The match doc (slug NULL) and the other shooter are untouched.
    assert asyncio.run(store.load_match("brm-abc")) == ({"name": "brm-abc"}, 1)
    assert asyncio.run(store.load_project("brm-abc", "bo")) == (
        {"raw_videos": [{"storage_path": "raw/bo.mp4"}]},
        1,
    )


def test_delete_shooter_absent_returns_zero() -> None:
    sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)
    _seed_full_match(store, "brm-abc")
    assert asyncio.run(store.delete_shooter("brm-abc", "ghost")) == 0


def test_delete_shooter_tenant_isolation() -> None:
    sf, (alice, bob) = _engine_with_users("alice@thias.se", "bob@thias.se")
    a = ProjectStateStore(sf, user_id=alice)
    b = ProjectStateStore(sf, user_id=bob)
    _seed_full_match(a, "shared")
    _seed_full_match(b, "shared")

    assert asyncio.run(a.delete_shooter("shared", "ada")) == 2

    assert asyncio.run(a.load_project("shared", "ada")) == (None, 0)
    # Bob's same-slug docs survive.
    assert asyncio.run(b.load_project("shared", "ada")) == (
        {"raw_videos": [{"storage_path": "raw/ada.mp4"}]},
        1,
    )
    assert asyncio.run(b.load_audit("shared", "ada", 1)) == ({"shots": []}, 1)
