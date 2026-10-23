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
        s: DocStore = FileDocStore(
            root,
            audit_lock=threading.Lock(),
            export_run_lock=threading.Lock(),
            project_lock=threading.Lock(),
        )
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


def test_export_runs_save_and_load_round_trip(store: DocStore) -> None:
    v = store.save_export_runs("anna", {"runs": ["r0"]}, version=0)
    doc, version = store.load_export_runs("anna")
    assert doc == {"runs": ["r0"]} and version == v
    if _is_db(store):
        with pytest.raises(StateConflictError):
            store.save_export_runs("anna", {"runs": []}, version=0)
    else:
        assert v == 0


def test_update_audit_gives_up_after_max_attempts(store: DocStore) -> None:
    """A writer that loses every race stops after ``max_attempts`` and
    raises the conflict rather than spinning; the audit it never wrote is
    not on disk under a stale shape either."""
    if not _is_db(store):
        pytest.skip("files serialize on the lock; there is no race to lose")
    assert isinstance(store, DbDocStore)
    short = DbDocStore(store._store, MID, max_attempts=3)
    short.save_audit("anna", 1, {"n": 0}, version=0)
    calls = 0

    def always_lose(doc: dict) -> dict:
        nonlocal calls
        calls += 1
        _current, version = store.load_audit("anna", 1)
        store.save_audit("anna", 1, {**doc, "winner": calls}, version=version)
        return {**doc, "mine": True}

    with pytest.raises(StateConflictError):
        short.update_audit("anna", 1, always_lose, dict)
    assert calls == 3
    doc, _ = store.load_audit("anna", 1)
    assert doc is not None and "mine" not in doc and doc["winner"] == 3


def test_update_audit_at_hands_the_merge_the_version_and_returns_the_result(store: DocStore) -> None:
    v0 = store.save_audit("anna", 1, {"n": 0}, version=0)
    seen: list[tuple[dict | None, int]] = []

    def merge(doc: dict | None, version: int) -> dict:
        seen.append((doc, version))
        assert doc is not None
        return {**doc, "n": doc["n"] + 1}

    merged, version = store.update_audit_at("anna", 1, merge)
    assert seen == [({"n": 0}, v0)]
    assert merged == {"n": 1}
    assert store.load_audit("anna", 1) == ({"n": 1}, version)
    assert version == (0 if not _is_db(store) else v0 + 1)
    # no document yet: the merge sees (None, 0)
    merged2, _ = store.update_audit_at("anna", 7, lambda doc, v: {"from": doc, "at": v})
    assert merged2 == {"from": None, "at": 0}


def test_update_audit_at_lets_the_merge_refuse_and_writes_nothing(store: DocStore) -> None:
    """A handler's 409 or 404 is raised from inside the merge; it is not a
    conflict, is not retried, and leaves the document as it was."""
    store.save_audit("anna", 1, {"n": 0}, version=0)
    calls = 0

    def refuse(doc: dict | None, version: int) -> dict:
        nonlocal calls
        calls += 1
        raise LookupError("expected version mismatch")

    with pytest.raises(LookupError):
        store.update_audit_at("anna", 1, refuse)
    assert calls == 1
    assert store.load_audit("anna", 1)[0] == {"n": 0}


def test_update_audit_at_re_merges_with_the_winner_version(store: DocStore) -> None:
    if not _is_db(store):
        pytest.skip("files serialize on the lock; there is no race to lose")
    store.save_audit("anna", 1, {"n": 0}, version=0)
    versions: list[int] = []

    def merge(doc: dict | None, version: int) -> dict:
        versions.append(version)
        assert doc is not None
        if len(versions) == 1:
            store.save_audit("anna", 1, {**doc, "other": True}, version=version)
        return {**doc, "mine": True}

    merged, new_version = store.update_audit_at("anna", 1, merge)
    assert len(versions) == 2 and versions[1] == versions[0] + 1
    assert merged == {"n": 0, "other": True, "mine": True}
    assert new_version == versions[1] + 1


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


def test_delete_match_docs_removes_every_document_and_counts_them(store: DocStore) -> None:
    store.save_project("anna", {"name": "anna"}, version=0)
    store.save_audit("anna", 1, {"shots": []}, version=0)
    store.save_audit("anna", 2, {"shots": []}, version=0)
    store.save_export_runs("anna", {"runs": []}, version=0)

    assert store.delete_match_docs() == 5

    assert store.load_match() == (None, 0)
    assert store.list_projects() == {}
    assert store.list_audits("anna") == {}
    assert store.load_export_runs("anna") == (None, 0)


def test_the_two_worlds_say_which_one_they_are(store: DocStore) -> None:
    """The one mode fact a consumer may read: whether the documents are the
    files under the match root. Everything else goes through the methods."""
    assert store.documents_on_disk is not _is_db(store)
    assert store.confines_paths is _is_db(store)


def test_update_project_keeps_every_concurrent_edit(store: DocStore) -> None:
    """Eight jobs of one shooter each set their own field through
    ``update_project``; the document ends with all eight (#1686: saved at
    the version each had loaded, all but the first failed hosted)."""
    store.save_project("anna", {"name": "anna", "n": 0}, version=0)
    errors: list[BaseException] = []

    def writer(key: str) -> None:
        try:
            store.update_project("anna", lambda doc: {**doc, key: True, "n": doc["n"] + 1})
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(f"k{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    doc, _ = store.load_project("anna")
    assert doc is not None and doc["n"] == 8 and all(doc.get(f"k{i}") for i in range(8))


def test_update_project_re_merges_onto_the_winner(store: DocStore) -> None:
    if not _is_db(store):
        pytest.skip("files serialize on the lock; there is no race to lose")
    store.save_project("anna", {"name": "anna"}, version=0)
    calls = 0

    def merge(doc: dict) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            _current, version = store.load_project("anna")
            store.save_project("anna", {**doc, "other": True}, version=version)
        return {**doc, "mine": True}

    store.update_project("anna", merge)
    doc, _ = store.load_project("anna")
    assert calls == 2
    assert doc == {"name": "anna", "other": True, "mine": True}


def test_update_project_refuses_a_missing_document(store: DocStore) -> None:
    with pytest.raises(LookupError):
        store.update_project("nobody", lambda doc: doc)
