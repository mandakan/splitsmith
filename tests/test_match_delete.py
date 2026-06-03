"""Tests for the project/match delete cascade (``ui.match_delete``).

Drives :func:`delete_match_cascade` against sqlite-backed stores and a
fake in-memory :class:`~splitsmith.storage.Storage`, so the orchestration
(storage prefix sweep, raw-upload refcount, state/match/registry/picker
teardown, best-effort partial failure) is exercised without spinning the
FastAPI app or a real object store.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from splitsmith.db import (
    Base,
    PostgresMatchStore,
    ProjectStateStore,
    User,
    create_engine,
    sessionmaker,
)
from splitsmith.match_model import MATCH_FILE
from splitsmith.match_registry import MatchRegistry
from splitsmith.storage import StorageObject
from splitsmith.ui.match_delete import delete_match_cascade


class FakeStorage:
    """In-memory stand-in: a ``path -> size`` map with prefix list + delete."""

    def __init__(self) -> None:
        self.objects: dict[str, int] = {}
        self.fail_on: set[str] = set()

    def list(self, prefix: str):
        for path, size in list(self.objects.items()):
            if path.startswith(prefix):
                yield StorageObject(path=path, size=size)

    def delete(self, path: str) -> None:
        if path in self.fail_on:
            raise RuntimeError(f"boom: {path}")
        self.objects.pop(path, None)


class FakeJobs:
    """Records the bulk-cancel call and returns a canned count."""

    def __init__(self, count: int = 0) -> None:
        self.count = count
        self.called = False

    async def cancel_active_for_user(self) -> int:
        self.called = True
        return self.count


class FakeRecentProjects:
    """Async ``remove`` over a set of resolved paths (matches the real store)."""

    def __init__(self, paths: list[str]) -> None:
        self._paths = {str(Path(p).expanduser().resolve()) for p in paths}
        self.removed: list[str] = []

    async def remove(self, path: Path) -> bool:
        resolved = str(Path(path).expanduser().resolve())
        existed = resolved in self._paths
        self._paths.discard(resolved)
        self.removed.append(resolved)
        return existed


def _hosted_state(tmp_path: Path, *, recent_paths: list[str], jobs: int = 1) -> SimpleNamespace:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/md.sqlite")
    sf = sessionmaker(engine)

    async def _setup() -> str:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sf() as s:
            user = User(email="m@thias.se")
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

    uid = asyncio.run(_setup())
    return SimpleNamespace(
        matches_store=PostgresMatchStore(sf, user_id=uid),
        project_state=ProjectStateStore(sf, user_id=uid),
        storage=FakeStorage(),
        jobs=FakeJobs(count=jobs),
        recent_projects=FakeRecentProjects(recent_paths),
        matches=MatchRegistry(),
    )


def _seed_match(
    state: SimpleNamespace,
    match_id: str,
    *,
    raws_by_shooter: dict[str, list[str]],
    storage_objs: list[str],
) -> None:
    asyncio.run(state.matches_store.upsert(match_id, match_id, f"matches/{match_id}"))
    asyncio.run(state.project_state.save_match(match_id, {"name": match_id}, expected_version=0))
    for slug, raws in raws_by_shooter.items():
        asyncio.run(
            state.project_state.save_project(
                match_id,
                slug,
                {"raw_videos": [{"storage_path": r} for r in raws]},
                expected_version=0,
            )
        )
    for path in storage_objs:
        state.storage.objects[path] = 10


def test_hosted_cascade_removes_everything() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        state = _hosted_state(tmp, recent_paths=["/work/m1"], jobs=1)
        _seed_match(
            state,
            "m1",
            raws_by_shooter={"ada": ["raw/ada.mp4"], "bo": ["raw/bo.mp4"]},
            storage_objs=[
                "matches/m1/match.json",
                "matches/m1/shooters/ada/trim.mp4",
                "raw/ada.mp4",
                "raw/bo.mp4",
            ],
        )
        state.matches.register("m1", Path("/work/m1"))

        summary = asyncio.run(
            delete_match_cascade(
                state,
                path="/work/m1",
                match_id="m1",
                storage_prefix="matches/m1",
                delete_local_files=False,
                delete_raw_uploads=True,
            )
        )

        # Jobs stopped first.
        assert state.jobs.called and summary.jobs_cancelled == 1
        # Match storage prefix swept (2 objects), raw uploads removed.
        assert summary.storage_objects_deleted == 2
        assert not any(p.startswith("matches/m1/") for p in state.storage.objects)
        assert summary.raw_uploads_deleted == ["raw/ada.mp4", "raw/bo.mp4"]
        assert "raw/ada.mp4" not in state.storage.objects
        # State docs (match + 2 project) gone.
        assert summary.state_docs_removed == 3
        assert asyncio.run(state.project_state.load_match("m1")) == (None, 0)
        # Match row + registry + picker row gone.
        assert summary.match_row_removed is True
        assert asyncio.run(state.matches_store.get("m1")) is None
        assert "m1" not in state.matches._by_id
        assert summary.recent_project_removed is True
        assert str(Path("/work/m1").resolve()) in state.recent_projects.removed
        assert summary.errors == []


def test_hosted_raw_refcount_skips_shared() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        state = _hosted_state(tmp, recent_paths=["/work/m1"])
        # m2 also references raw/shared.mp4 -> it must survive m1's delete.
        _seed_match(state, "m2", raws_by_shooter={"x": ["raw/shared.mp4"]}, storage_objs=[])
        _seed_match(
            state,
            "m1",
            raws_by_shooter={"ada": ["raw/shared.mp4", "raw/solo.mp4"]},
            storage_objs=["matches/m1/f", "raw/shared.mp4", "raw/solo.mp4"],
        )

        summary = asyncio.run(
            delete_match_cascade(
                state,
                path="/work/m1",
                match_id="m1",
                storage_prefix="matches/m1",
                delete_local_files=False,
                delete_raw_uploads=True,
            )
        )

        assert summary.raw_uploads_deleted == ["raw/solo.mp4"]
        assert summary.raw_uploads_skipped_shared == ["raw/shared.mp4"]
        assert "raw/solo.mp4" not in state.storage.objects
        assert "raw/shared.mp4" in state.storage.objects  # kept for m2


def test_hosted_partial_failure_is_recorded_but_continues() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        state = _hosted_state(tmp, recent_paths=["/work/m1"])
        _seed_match(
            state,
            "m1",
            raws_by_shooter={"ada": []},
            storage_objs=["matches/m1/a", "matches/m1/b"],
        )
        state.storage.fail_on = {"matches/m1/a"}

        summary = asyncio.run(
            delete_match_cascade(
                state,
                path="/work/m1",
                match_id="m1",
                storage_prefix="matches/m1",
                delete_local_files=False,
                delete_raw_uploads=False,
            )
        )

        assert any("matches/m1/a" in e for e in summary.errors)
        # The other object still went, and the rest of the cascade completed.
        assert "matches/m1/b" not in state.storage.objects
        assert asyncio.run(state.matches_store.get("m1")) is None
        assert asyncio.run(state.project_state.load_match("m1")) == (None, 0)
        assert summary.recent_project_removed is True


def test_hosted_missing_match_id_drops_picker_only() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        state = _hosted_state(tmp, recent_paths=["/work/legacy"])

        summary = asyncio.run(
            delete_match_cascade(
                state,
                path="/work/legacy",
                match_id=None,
                storage_prefix=None,
                delete_local_files=False,
                delete_raw_uploads=True,
            )
        )

        assert summary.recent_project_removed is True
        assert any("no match_id" in e for e in summary.errors)


def _local_state(recent_paths: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        matches_store=None,
        project_state=None,
        storage=None,
        jobs=FakeJobs(),
        recent_projects=FakeRecentProjects(recent_paths),
        matches=MatchRegistry(),
    )


def _make_match_dir(root: Path) -> Path:
    d = root / "mymatch"
    d.mkdir()
    (d / MATCH_FILE).write_text("{}")
    (d / "video.mp4").write_text("x")
    return d


def test_local_delete_with_optin_rmtrees(tmp_path: Path) -> None:
    match_dir = _make_match_dir(tmp_path)
    state = _local_state([str(match_dir)])
    state.matches.register("m1", match_dir)

    summary = asyncio.run(
        delete_match_cascade(
            state,
            path=str(match_dir),
            match_id="m1",
            storage_prefix=None,
            delete_local_files=True,
            delete_raw_uploads=False,
        )
    )

    assert summary.local_dir_removed is True
    assert not match_dir.exists()
    assert summary.recent_project_removed is True
    assert "m1" not in state.matches._by_id
    # Local mode never touches jobs.
    assert state.jobs.called is False


def test_local_delete_without_optin_keeps_dir(tmp_path: Path) -> None:
    match_dir = _make_match_dir(tmp_path)
    state = _local_state([str(match_dir)])

    summary = asyncio.run(
        delete_match_cascade(
            state,
            path=str(match_dir),
            match_id="m1",
            storage_prefix=None,
            delete_local_files=False,
            delete_raw_uploads=False,
        )
    )

    assert summary.local_dir_removed is False
    assert match_dir.exists()  # files left intact
    assert summary.recent_project_removed is True


def test_local_rmtree_refuses_without_match_marker(tmp_path: Path) -> None:
    """The MATCH_FILE guard stops a stray path from nuking an unrelated dir."""
    not_a_match = tmp_path / "random"
    not_a_match.mkdir()
    (not_a_match / "keep.txt").write_text("important")
    state = _local_state([str(not_a_match)])

    summary = asyncio.run(
        delete_match_cascade(
            state,
            path=str(not_a_match),
            match_id=None,
            storage_prefix=None,
            delete_local_files=True,
            delete_raw_uploads=False,
        )
    )

    assert summary.local_dir_removed is False
    assert not_a_match.exists()
    assert any("marker" in e for e in summary.errors)
