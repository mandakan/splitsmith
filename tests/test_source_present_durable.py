"""``source_present(durable=True)`` asks storage, not the local cache.

On hosted, ``root / video_path`` is an ephemeral container cache. A
cached copy does not make a derived artefact reconstructable -- the cache
is wiped on the next redeploy. The cleanup planner is the only caller
that needs that distinction, and it needs it badly: the whole value of
its ``reconstructable`` flag is that the answer survives the container.
"""

from __future__ import annotations

from pathlib import Path

from splitsmith.match_project import MatchProject
from splitsmith.storage import FilesystemStorage

SCOPE = "matches/m1/shooters/me"


def _project(tmp_path: Path, *, with_storage: bool = True) -> tuple[MatchProject, Path]:
    root = tmp_path / "p"
    project = MatchProject.init(root, name="durable-test")
    if with_storage:
        backing = tmp_path / "tenant"
        backing.mkdir(exist_ok=True)
        project.bind_storage(FilesystemStorage(backing), scope=SCOPE)
    return project, root


def test_durable_ignores_a_local_cache_copy(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    rel = Path("raw/clip.mp4")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(b"cached")

    # Non-durable sees the cache; durable does not, because storage is empty.
    assert project.source_present(root, rel) is True
    assert project.source_present(root, rel, durable=True) is False


def test_durable_sees_the_storage_object(tmp_path: Path) -> None:
    project, root = _project(tmp_path)
    rel = Path("raw/clip.mp4")
    backing = tmp_path / "tenant"
    (backing / str(rel)).parent.mkdir(parents=True, exist_ok=True)
    (backing / str(rel)).write_bytes(b"durable")

    assert project.source_present(root, rel, durable=True) is True


def test_durable_is_a_noop_on_desktop(tmp_path: Path) -> None:
    # No storage bound: the local file IS the durable copy.
    project, root = _project(tmp_path, with_storage=False)
    rel = Path("raw/clip.mp4")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(b"local")

    assert project.source_present(root, rel) is True
    assert project.source_present(root, rel, durable=True) is True
