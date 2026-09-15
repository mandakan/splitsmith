"""``PostgresExportPresetStore``: per-user isolation and the round trip.

SQLite in-memory via aiosqlite, the same harness as
``test_scoreboard_identity_store``. Every method gets an isolation test:
a second user must see, overwrite and delete nothing of the first's.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from splitsmith.db import Base, PostgresExportPresetStore, User, create_engine, sessionmaker
from splitsmith.export_presets import BUILTIN_PRESETS, ExportPreset, ExportPresetBody, ExportPresetStore


def _preset(preset_id: str = "p1", name: str = "Club night", **body) -> ExportPreset:
    return ExportPreset(
        preset_id=preset_id,
        name=name,
        updated_at=datetime(2026, 9, 15, tzinfo=UTC),
        body=ExportPresetBody(**body),
    )


def _two_users() -> tuple[PostgresExportPresetStore, PostgresExportPresetStore]:
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
    return PostgresExportPresetStore(sf, user_id=a_id), PostgresExportPresetStore(sf, user_id=b_id)


def test_satisfies_the_protocol() -> None:
    a, _ = _two_users()
    typed: ExportPresetStore = a
    assert typed is a


@pytest.mark.parametrize("bad", ["", None, 0])
def test_construction_rejects_an_empty_user_id(bad) -> None:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    with pytest.raises(ValueError):
        PostgresExportPresetStore(sessionmaker(engine), user_id=bad)


def test_round_trip_and_name_order() -> None:
    a, _ = _two_users()
    asyncio.run(a.put(_preset("p2", "Zed", mode="compare")))
    asyncio.run(a.put(_preset("p1", "Alpha")))
    listed = asyncio.run(a.list())
    assert [(p.preset_id, p.name) for p in listed] == [("p1", "Alpha"), ("p2", "Zed")]
    assert listed[1].body.mode == "compare"
    assert listed[0].builtin is False


def test_put_replaces_by_id() -> None:
    a, _ = _two_users()
    asyncio.run(a.put(_preset("p1", "Old")))
    asyncio.run(a.put(_preset("p1", "New", mode="trims")))
    listed = asyncio.run(a.list())
    assert [(p.name, p.body.mode) for p in listed] == [("New", "trims")]


def test_delete_is_idempotent() -> None:
    a, _ = _two_users()
    asyncio.run(a.put(_preset("p1")))
    asyncio.run(a.delete("p1"))
    asyncio.run(a.delete("p1"))
    assert asyncio.run(a.list()) == []


def test_refuses_a_builtin() -> None:
    a, _ = _two_users()
    with pytest.raises(ValueError):
        asyncio.run(a.put(BUILTIN_PRESETS[0]))


def test_list_is_isolated_per_user() -> None:
    a, b = _two_users()
    asyncio.run(a.put(_preset("p1")))
    assert asyncio.run(b.list()) == []


def test_put_with_the_same_id_does_not_cross_users() -> None:
    a, b = _two_users()
    asyncio.run(a.put(_preset("p1", "A's")))
    asyncio.run(b.put(_preset("p1", "B's")))
    assert [p.name for p in asyncio.run(a.list())] == ["A's"]
    assert [p.name for p in asyncio.run(b.list())] == ["B's"]


def test_delete_does_not_cross_users() -> None:
    a, b = _two_users()
    asyncio.run(a.put(_preset("p1")))
    asyncio.run(b.delete("p1"))
    assert [p.preset_id for p in asyncio.run(a.list())] == ["p1"]
