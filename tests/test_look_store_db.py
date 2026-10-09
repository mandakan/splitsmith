"""``PostgresLookStore``: per-user isolation, the round trip, the hosted name
rules and the materialized folder (issue #1263).

SQLite in-memory via aiosqlite, the harness ``test_export_presets_store``
uses. Every method gets an isolation test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from splitsmith import looks
from splitsmith.db import Base, PostgresLookStore, User, create_engine, sessionmaker
from splitsmith.look_store import LookStore, LookStoreError, StoredLookBody


def _body(**changes) -> StoredLookBody:
    fields = {"label": "Club", "base": "splitsmith", "colors": dict(looks.load_look("clean").manifest.colors)}
    fields.update(changes)
    return StoredLookBody.model_validate(fields)


def _two_users() -> tuple[PostgresLookStore, PostgresLookStore]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    sf = sessionmaker(engine)

    async def _setup() -> tuple[str, str]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            a, b = User(email="a@example.com"), User(email="b@example.com")
            s.add_all([a, b])
            await s.commit()
            await s.refresh(a)
            await s.refresh(b)
            return a.id, b.id

    a_id, b_id = asyncio.run(_setup())
    return PostgresLookStore(sf, user_id=a_id), PostgresLookStore(sf, user_id=b_id)


def test_satisfies_the_protocol() -> None:
    a, _ = _two_users()
    typed: LookStore = a
    assert typed is a


@pytest.mark.parametrize("bad", ["", None, 0])
def test_construction_rejects_an_empty_user_id(bad) -> None:
    with pytest.raises(ValueError):
        PostgresLookStore(sessionmaker(create_engine("sqlite+aiosqlite:///:memory:")), user_id=bad)


def test_round_trip_by_name_and_replace() -> None:
    a, _ = _two_users()
    asyncio.run(a.put("zed", _body(label="Zed")))
    asyncio.run(a.put("club", _body(styles={"slate": "rise"})))
    asyncio.run(a.put("club", _body(label="Club 2", styles={"slate": "rise"})))
    listed = asyncio.run(a.list())
    assert [(s.name, s.body.label) for s in listed] == [("club", "Club 2"), ("zed", "Zed")]
    got = asyncio.run(a.get("club"))
    assert got is not None and got.body.styles == {"slate": "rise"}
    assert asyncio.run(a.get("nope")) is None


@pytest.mark.parametrize(
    "name, body, message",
    [
        ("clean", _body(), "shipped"),
        ("club", _body(base="mine"), "shipped Look"),
        ("Bad", _body(), "Look name"),
        ("club", _body(styles={"slate": "zoom"}), "no 'zoom' style"),
    ],
)
def test_hosted_names_and_bases_are_refused(name: str, body: StoredLookBody, message: str) -> None:
    a, _ = _two_users()
    with pytest.raises(LookStoreError, match=message):
        asyncio.run(a.put(name, body))
    assert asyncio.run(a.list()) == []


def test_another_user_sees_gets_overwrites_and_deletes_nothing() -> None:
    a, b = _two_users()
    asyncio.run(a.put("club", _body(label="A's")))
    assert asyncio.run(b.list()) == [] and asyncio.run(b.get("club")) is None
    asyncio.run(b.put("club", _body(label="B's")))
    asyncio.run(b.delete("club"))
    got = asyncio.run(a.get("club"))
    assert got is not None and got.body.label == "A's"


def test_delete_is_idempotent() -> None:
    a, _ = _two_users()
    asyncio.run(a.put("club", _body()))
    asyncio.run(a.delete("club"))
    asyncio.run(a.delete("club"))
    assert asyncio.run(a.list()) == []


def test_the_materialized_folder_follows_the_rows_and_stays_per_user(tmp_path: Path) -> None:
    a, b = _two_users()
    asyncio.run(a.put("club", _body()))
    first = a.materialized_dir(tmp_path)
    assert [p.name for p in first.iterdir()] == ["club"]
    assert list(b.materialized_dir(tmp_path).iterdir()) == []
    assert not first.is_relative_to(b.materialized_dir(tmp_path).parent)
    asyncio.run(a.put("team", _body(label="Team")))
    assert sorted(p.name for p in a.materialized_dir(tmp_path).iterdir()) == ["club", "team"]


def test_a_queued_job_sees_its_accounts_looks_and_none_after(tmp_path: Path) -> None:
    """The worker task pins the tenant's Looks provider for the body, the
    same as the request middleware does, and resets it after."""
    from types import SimpleNamespace

    from splitsmith import queue as queue_mod

    tasks: dict[str, object] = {}

    class _App:
        def task(self, *, name: str, queue: str):  # type: ignore[no-untyped-def]
            def register(fn):  # type: ignore[no-untyped-def]
                tasks[name] = fn
                return fn

            return register

    a, _ = _two_users()
    asyncio.run(a.put("club", _body()))
    seen: list[tuple[str, ...]] = []

    async def run_job(**_: object) -> None:
        seen.append(looks.look_names())

    tenant = SimpleNamespace(looks=a)
    state = SimpleNamespace(build_tenant=lambda _uid: tenant, jobs=SimpleNamespace(run_job=run_job))
    from splitsmith.ui import server

    original = server.user_looks_cache_root
    server.user_looks_cache_root = lambda: tmp_path  # type: ignore[assignment]
    try:
        queue_mod.register_compute_task(_App(), state)
        asyncio.run(tasks[queue_mod.RUN_COMPUTE_JOB_TASK](job_id="j", user_id="u", kind="noop"))  # type: ignore[operator]
    finally:
        server.user_looks_cache_root = original  # type: ignore[assignment]
    assert seen == [("splitsmith", "clean", "club")]
    assert "club" not in looks.look_names()
