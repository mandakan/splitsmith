"""One suite, both storage worlds (#1581, spec 2026-10-19 s4 step 1).

Every test runs against ``FileDocStore`` over a temp match folder and
``DbDocStore`` over the SQLite engine the hosted tests use. What they pin is
the contract a handler relies on once it stops asking which world it is in:
load after save, the version semantics (files 0, the database counting up),
``update_*`` keeping a concurrent writer's edit, delete, and the bundle
equalling the per-item loads.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select

from splitsmith.db import Base, ProjectStateStore, StateConflictError, User, create_engine, sessionmaker
from splitsmith.ui.docstore import DbDocStore, DocStore, FileDocStore

MID = "m-contract"
MATCH = {"match_id": MID, "name": "Contract", "shooters": ["anna", "bo"], "stages": []}


@pytest.fixture(params=["files", "db"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[DocStore]:
    if request.param == "files":
        root = tmp_path / "match"
        root.mkdir()
        s: DocStore = FileDocStore(root, audit_lock=threading.Lock(), export_run_lock=threading.Lock())
        s.save_match(MATCH, version=0)
        yield s
        return
    url = f"sqlite+aiosqlite:///{tmp_path / 'docs.sqlite'}"
    engine = create_engine(url)

    async def _seed() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessionmaker(engine)() as session:
            session.add(User(email="contract@x.se", access_tier="full"))
            await session.commit()
            return (await session.execute(select(User))).scalar_one().id

    user_id = asyncio.run(_seed())
    db = DbDocStore(ProjectStateStore(sessionmaker(engine), user_id=user_id), MID)
    db.save_match(MATCH, version=0)
    yield db
    asyncio.run(engine.dispose())


def _is_db(store: DocStore) -> bool:
    return isinstance(store, DbDocStore)


def test_a_missing_document_is_none_at_version_zero(store: DocStore) -> None:
    assert store.load_project("nobody") == (None, 0)
    assert store.load_audit("anna", 9) == (None, 0)
    assert store.load_export_runs("anna") == (None, 0)
    assert store.list_audits("anna") == {}


def test_match_round_trips_and_versions_behave(store: DocStore) -> None:
    doc, v1 = store.load_match()
    assert doc == MATCH
    v2 = store.save_match({**MATCH, "name": "Renamed"}, version=v1)
    doc2, v3 = store.load_match()
    assert doc2 is not None and doc2["name"] == "Renamed"
    if _is_db(store):
        assert v1 >= 1 and v2 == v3 == v1 + 1
        with pytest.raises(StateConflictError):
            store.save_match(MATCH, version=v1)
    else:
        assert (v1, v2, v3) == (0, 0, 0)


def test_projects_round_trip_and_list(store: DocStore) -> None:
    for slug in ("anna", "bo"):
        store.save_project(slug, {"name": slug.title(), "stages": []}, version=0)
    listed = store.list_projects()
    assert set(listed) == {"anna", "bo"}
    for slug, (doc, version) in listed.items():
        assert store.load_project(slug) == (doc, version)
    store.delete_project("bo")
    assert store.load_project("bo") == (None, 0)
    assert set(store.list_projects()) == {"anna"}


def test_audits_round_trip_list_and_delete(store: DocStore) -> None:
    store.save_project("anna", {"name": "Anna", "stages": []}, version=0)
    v = store.save_audit("anna", 1, {"shots": [1.0]}, version=0)
    store.save_audit("anna", 3, {"shots": []}, version=0)
    doc, version = store.load_audit("anna", 1)
    assert doc == {"shots": [1.0]} and version == v
    assert {s: d for s, (d, _v) in store.list_audits("anna").items()} == {
        1: {"shots": [1.0]},
        3: {"shots": []},
    }
    assert store.delete_audit("anna", 1) is True
    assert store.delete_audit("anna", 1) is False
    assert set(store.list_audits("anna")) == {3}


def test_update_audit_keeps_every_concurrent_edit(store: DocStore) -> None:
    """Eight writers each add their own key through ``update_audit``; the
    document ends with all eight. Files serialize on the lock, the database
    re-merges onto whoever won each version race."""
    store.save_audit("anna", 1, {"n": 0}, version=0)
    errors: list[BaseException] = []

    def writer(key: str) -> None:
        try:
            store.update_audit("anna", 1, lambda doc: {**doc, key: True, "n": doc["n"] + 1}, dict)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(f"k{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    doc, _ = store.load_audit("anna", 1)
    assert doc is not None and doc["n"] == 8 and all(doc.get(f"k{i}") for i in range(8))


def test_update_audit_re_merges_onto_the_winner(store: DocStore) -> None:
    """A writer that loses the version race re-applies its merge to the
    winner's document instead of overwriting it. Deterministic: the merge
    itself plays the concurrent writer on its first call."""
    if not _is_db(store):
        pytest.skip("files serialize on the lock; there is no race to lose")
    store.save_audit("anna", 1, {"n": 0}, version=0)
    calls = 0

    def merge(doc: dict) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            _current, version = store.load_audit("anna", 1)
            store.save_audit("anna", 1, {**doc, "other": True}, version=version)
        return {**doc, "mine": True}

    store.update_audit("anna", 1, merge, dict)
    doc, _ = store.load_audit("anna", 1)
    assert calls == 2
    assert doc == {"n": 0, "other": True, "mine": True}


def test_update_audit_builds_the_document_from_default(store: DocStore) -> None:
    store.update_audit("anna", 2, lambda d: {**d, "touched": True}, lambda: {"shots": [], "fresh": True})
    doc, _ = store.load_audit("anna", 2)
    assert doc == {"shots": [], "fresh": True, "touched": True}


def test_update_export_runs_never_drops_a_run_under_contention(store: DocStore) -> None:
    def append(run_id: str):
        def merge(doc: dict | None) -> dict:
            runs = list((doc or {}).get("runs", []))
            return {"runs": [*runs, run_id]}

        return merge

    errors: list[BaseException] = []

    def writer(run_id: str) -> None:
        try:
            store.update_export_runs("anna", append(run_id))
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(f"r{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    doc, _ = store.load_export_runs("anna")
    assert doc is not None and sorted(doc["runs"]) == [f"r{i}" for i in range(8)]


def test_bundle_equals_the_per_item_loads(store: DocStore) -> None:
    store.save_project("anna", {"name": "Anna", "stages": []}, version=0)
    store.save_project("bo", {"name": "Bo", "stages": []}, version=0)
    store.save_audit("anna", 1, {"shots": [1.0]}, version=0)
    store.save_audit("bo", 2, {"shots": []}, version=0)
    bundle = store.bundle()
    assert bundle.match == store.load_match()[0]
    assert bundle.match_version == store.load_match()[1]
    assert bundle.projects == store.list_projects()
    assert bundle.audits == {"anna": store.list_audits("anna"), "bo": store.list_audits("bo")}
    assert bundle.audits["anna"][1] == store.load_audit("anna", 1)
