"""A hosted grid's segments live in R2 so every worker finds them (#1691).

A stage job commits its segment into the cache and the commit is pushed
under the match; the stitch, on another execution with an empty disk, finds
it by the same key and pulls it.
"""

from __future__ import annotations

from pathlib import Path

from splitsmith.storage import FilesystemStorage
from splitsmith.stored_segment_cache import StoredSegmentCache

PREFIX = "matches/m1/render-segments"
KEY = "ab" * 32


def _cache(tmp_path: Path, name: str, storage) -> StoredSegmentCache:  # type: ignore[no-untyped-def]
    return StoredSegmentCache(tmp_path / name, 1 << 30, storage=storage, prefix=PREFIX)


def _commit(cache: StoredSegmentCache, data: bytes = b"segment") -> Path:
    partial = cache.partial_path(KEY, suffix=".mov")
    partial.write_bytes(data)
    return cache.commit(partial, KEY, suffix=".mov")


def test_a_commit_is_found_by_another_root(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path / "store")
    _commit(_cache(tmp_path, "worker-a", storage))
    found = _cache(tmp_path, "worker-b", storage).lookup(KEY, suffix=".mov")
    assert found is not None and found.read_bytes() == b"segment"
    assert storage.exists(f"{PREFIX}/{KEY}.mov")


def test_a_missing_object_is_a_miss(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path / "store")
    assert _cache(tmp_path, "worker", storage).lookup(KEY, suffix=".mov") is None


class _Broken(FilesystemStorage):
    def open_stream(self, path: str):  # type: ignore[no-untyped-def]
        raise OSError("connection reset")


def test_a_failed_pull_is_a_miss_and_leaves_no_partial(tmp_path: Path) -> None:
    good = FilesystemStorage(tmp_path / "store")
    _commit(_cache(tmp_path, "worker-a", good))
    cache = _cache(tmp_path, "worker-b", _Broken(tmp_path / "store"))
    assert cache.lookup(KEY, suffix=".mov") is None
    assert not [p for p in (tmp_path / "worker-b").rglob("*") if p.is_file()]


def test_a_failed_push_keeps_the_local_commit(tmp_path: Path) -> None:
    class _NoPush(FilesystemStorage):
        def upload_stream(self, path: str, fileobj):  # type: ignore[no-untyped-def]
            raise OSError("quota")

    cache = _cache(tmp_path, "worker", _NoPush(tmp_path / "store"))
    committed = _commit(cache)
    assert committed.read_bytes() == b"segment"
    assert cache.lookup(KEY, suffix=".mov") == committed


def test_two_commits_of_one_key_both_succeed(tmp_path: Path) -> None:
    """Two renders of the same grid at once encode the same segment twice;
    both commits land, and either copy is the segment."""
    storage = FilesystemStorage(tmp_path / "store")
    _commit(_cache(tmp_path, "worker-a", storage), b"segment")
    _commit(_cache(tmp_path, "worker-b", storage), b"segment")
    found = _cache(tmp_path, "worker-c", storage).lookup(KEY, suffix=".mov")
    assert found is not None and found.read_bytes() == b"segment"


def test_forget_local_keeps_the_stored_copy(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path / "store")
    cache = _cache(tmp_path, "worker", storage)
    committed = _commit(cache)
    cache.forget_local(KEY, suffix=".mov")
    assert not committed.exists()
    assert storage.exists(f"{PREFIX}/{KEY}.mov")
    found = cache.lookup(KEY, suffix=".mov")
    assert found is not None and found.read_bytes() == b"segment"
