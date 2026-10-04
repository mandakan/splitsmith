"""``PostgresMatchStore.get_many``: the origins of a list of matches in one bounded query (#1179).

The picker needs each recent match's ``origin``. ``list()`` would read every
match the user has; ``get_many`` reads only the ids asked for, and -- like
every method on this store -- only the caller's own rows.
"""

from __future__ import annotations

import asyncio

from splitsmith.db import PostgresMatchStore

from .test_project_state_batch import _count_statements, _engine_with_users


def test_get_many_returns_only_the_requested_rows_of_this_user() -> None:
    engine, sf, (uid, other) = _engine_with_users("m@thias.se", "other@example.com")
    store = PostgresMatchStore(sf, user_id=uid)
    intruder = PostgresMatchStore(sf, user_id=other)

    async def _seed() -> None:
        await store.upsert("m1", "One", "matches/m1")
        await store.upsert("m2", "Two", "matches/m2", origin="desktop")
        await store.upsert("m9", "Nine", "matches/m9")
        await intruder.upsert("m1", "Not yours", "matches/m1", origin="desktop")

    asyncio.run(_seed())
    seen = _count_statements(engine)

    rows = asyncio.run(store.get_many(["m1", "m2", "m3"]))

    assert {r.match_id: r.origin for r in rows} == {"m1": "hosted", "m2": "desktop"}
    assert len([s for s in seen if s.lstrip().upper().startswith("SELECT")]) == 1


def test_get_many_with_no_ids_issues_no_query() -> None:
    engine, sf, (uid,) = _engine_with_users("m@thias.se")
    store = PostgresMatchStore(sf, user_id=uid)
    seen = _count_statements(engine)

    assert asyncio.run(store.get_many([])) == []
    assert seen == []
