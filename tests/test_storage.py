"""Tests for the storage abstraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith.storage import FilesystemStorage, StorageObject


def test_round_trip_write_then_read(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    storage.write_bytes("hello.txt", b"world")

    assert storage.read_bytes("hello.txt") == b"world"


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    """Callers should not have to mkdir themselves -- a project's
    on-disk layout has many nested folders and forcing every writer
    to manage them duplicates concerns."""
    storage = FilesystemStorage(tmp_path)
    storage.write_bytes("shooters/abc/audits/2026.json", b"{}")

    assert (tmp_path / "shooters" / "abc" / "audits" / "2026.json").read_bytes() == b"{}"


def test_write_is_atomic_via_temp_and_rename(tmp_path: Path) -> None:
    """A crash mid-write must not leave a torn file at the canonical
    path. We can't easily simulate a crash, but we can prove the
    rename path leaves no temp files lying around on success."""
    storage = FilesystemStorage(tmp_path)
    storage.write_bytes("data.bin", b"first version" * 100)
    storage.write_bytes("data.bin", b"second version" * 100)

    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert siblings == ["data.bin"], f"temp file leaked: {siblings}"


def test_write_overwrites_existing(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    storage.write_bytes("k", b"v1")
    storage.write_bytes("k", b"v2")

    assert storage.read_bytes("k") == b"v2"


def test_read_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)

    with pytest.raises(FileNotFoundError):
        storage.read_bytes("nope")


def test_exists_returns_true_after_write(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    assert not storage.exists("k")

    storage.write_bytes("k", b"v")
    assert storage.exists("k")


def test_stat_returns_none_for_missing(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)

    assert storage.stat("nope") is None


def test_stat_returns_size_for_existing(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    storage.write_bytes("k", b"hello")

    info = storage.stat("k")
    assert isinstance(info, StorageObject)
    assert info.path == "k"
    assert info.size == 5
    assert info.last_modified is not None


def test_list_walks_subdirectories(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    storage.write_bytes("a.txt", b"x")
    storage.write_bytes("nested/b.txt", b"x")
    storage.write_bytes("nested/deep/c.txt", b"x")

    paths = sorted(obj.path for obj in storage.list(""))
    assert paths == ["a.txt", "nested/b.txt", "nested/deep/c.txt"]


def test_list_with_prefix_narrows_to_subtree(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    storage.write_bytes("a/1.txt", b"x")
    storage.write_bytes("a/2.txt", b"x")
    storage.write_bytes("b/3.txt", b"x")

    paths = sorted(obj.path for obj in storage.list("a"))
    assert paths == ["a/1.txt", "a/2.txt"]


def test_list_missing_prefix_yields_nothing(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)

    assert list(storage.list("nope")) == []


def test_delete_removes_file(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    storage.write_bytes("k", b"v")

    storage.delete("k")

    assert not storage.exists("k")


def test_delete_missing_is_noop(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    # No exception even though the path was never written.
    storage.delete("nope")


def test_delete_removes_directory_recursively(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    storage.write_bytes("dir/a.txt", b"x")
    storage.write_bytes("dir/nested/b.txt", b"x")

    storage.delete("dir")

    assert list(storage.list("dir")) == []


# Path-traversal guard tests: every method that takes a path goes
# through ``_resolve``, which must reject absolute paths and ``..``
# parts. Without this guard a caller (or a malicious request body
# in hosted mode) could write outside the storage root.
@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd",
        "../escape.txt",
        "nested/../../escape.txt",
        "a/../../b",
    ],
)
def test_traversal_or_absolute_paths_rejected(tmp_path: Path, bad_path: str) -> None:
    storage = FilesystemStorage(tmp_path)

    with pytest.raises(ValueError, match="must be relative"):
        storage.write_bytes(bad_path, b"x")


def test_root_property_reflects_constructor(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path)
    assert storage.root == tmp_path
