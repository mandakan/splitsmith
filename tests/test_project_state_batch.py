"""``ProjectStateStore.load_docs_for_matches``: every doc the picker needs, one query (#1179).

The hosted picker loaded 3 + shooters docs per match in sequence. This
method returns the match, project and audit docs for a whole list of
matches grouped per match, so the route's query count stops growing with
the list.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import event

from splitsmith.db import Base, ProjectStateStore, User, create_engine, sessionmaker
from splitsmith.db.project_state import MatchDocs


def _engine_with_users(*emails: str):
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

    return engine, session_factory, asyncio.run(_setup())


def _count_statements(engine) -> list[str]:
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    return seen


def test_groups_match_project_and_audit_docs_per_match() -> None:
    engine, sf, (uid, other) = _engine_with_users("m@thias.se", "other@example.com")
    store = ProjectStateStore(sf, user_id=uid)
    intruder = ProjectStateStore(sf, user_id=other)

    async def _seed() -> None:
        await store.save_match("m1", {"name": "One", "shooters": ["a", "b"]}, expected_version=0)
        await store.save_project("m1", "a", {"name": "A"}, expected_version=0)
        await store.save_project("m1", "b", {"name": "B"}, expected_version=0)
        await store.save_audit("m1", "a", 1, {"shots": [1]}, expected_version=0)
        await store.save_audit("m1", "a", 2, {"shots": [2]}, expected_version=0)
        await store.save_audit("m1", "b", 1, {"shots": [3]}, expected_version=0)
        await store.save_export_runs("m1", "a", {"runs": []}, expected_version=0)
        await store.save_match("m2", {"name": "Two", "shooters": []}, expected_version=0)
        # Another tenant's docs under the same ids must never leak in.
        await intruder.save_match("m1", {"name": "Not yours"}, expected_version=0)
        await intruder.save_project("m1", "a", {"name": "Not yours"}, expected_version=0)

    asyncio.run(_seed())

    docs = asyncio.run(store.load_docs_for_matches(["m1", "m2", "m3"]))

    assert set(docs) == {"m1", "m2", "m3"}
    assert docs["m1"].match == {"name": "One", "shooters": ["a", "b"]}
    assert docs["m1"].projects == {"a": {"name": "A"}, "b": {"name": "B"}}
    assert docs["m1"].audits == {"a": {1: {"shots": [1]}, 2: {"shots": [2]}}, "b": {1: {"shots": [3]}}}
    assert docs["m2"] == MatchDocs(match={"name": "Two", "shooters": []}, projects={}, audits={})
    assert docs["m3"] == MatchDocs(match=None, projects={}, audits={})


def test_one_select_for_any_number_of_matches() -> None:
    engine, sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)

    async def _seed() -> None:
        for n in range(5):
            await store.save_match(f"m{n}", {"name": str(n), "shooters": ["a"]}, expected_version=0)
            await store.save_project(f"m{n}", "a", {"name": "A"}, expected_version=0)
            await store.save_audit(f"m{n}", "a", 1, {"shots": []}, expected_version=0)

    asyncio.run(_seed())
    seen = _count_statements(engine)

    asyncio.run(store.load_docs_for_matches([f"m{n}" for n in range(5)]))

    selects = [s for s in seen if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1, selects


def test_empty_id_list_issues_no_query() -> None:
    engine, sf, (uid,) = _engine_with_users("m@thias.se")
    store = ProjectStateStore(sf, user_id=uid)
    seen = _count_statements(engine)

    assert asyncio.run(store.load_docs_for_matches([])) == {}
    assert seen == []
