"""Tests for the self-hosted worker's LRU source-cache sweep."""

from __future__ import annotations

import os
from pathlib import Path

from splitsmith.source_cache import (
    _DEFAULT_MAX_GB,
    configured_cache_max_bytes,
    sweep_source_cache,
)

_GB = 1024**3


def test_config_default_when_unset() -> None:
    assert configured_cache_max_bytes({}) == int(_DEFAULT_MAX_GB * _GB)


def test_config_parses_gb() -> None:
    assert configured_cache_max_bytes({"SPLITSMITH_SOURCE_CACHE_MAX_GB": "5"}) == 5 * _GB
    assert configured_cache_max_bytes({"SPLITSMITH_SOURCE_CACHE_MAX_GB": "0.5"}) == _GB // 2


def test_config_zero_or_negative_disables() -> None:
    assert configured_cache_max_bytes({"SPLITSMITH_SOURCE_CACHE_MAX_GB": "0"}) is None
    assert configured_cache_max_bytes({"SPLITSMITH_SOURCE_CACHE_MAX_GB": "-3"}) is None


def test_config_garbage_falls_back_to_default() -> None:
    assert configured_cache_max_bytes({"SPLITSMITH_SOURCE_CACHE_MAX_GB": "lots"}) == int(
        _DEFAULT_MAX_GB * _GB
    )


def _write(path: Path, size: int, *, mtime: float) -> None:
    """Write a ``size``-byte file and stamp its mtime for LRU ordering."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    os.utime(path, (mtime, mtime))


def test_missing_root_is_a_noop(tmp_path: Path) -> None:
    result = sweep_source_cache(tmp_path / "nope", max_bytes=1_000)

    assert result.evicted_files == 0
    assert result.evicted_bytes == 0
    assert result.total_bytes_before == 0
    assert result.total_bytes_after == 0


def test_under_budget_evicts_nothing(tmp_path: Path) -> None:
    _write(tmp_path / "a.mp4", 100, mtime=1.0)
    _write(tmp_path / "b.mp4", 100, mtime=2.0)

    result = sweep_source_cache(tmp_path, max_bytes=1_000)

    assert result.evicted_files == 0
    assert result.total_bytes_after == 200
    assert (tmp_path / "a.mp4").exists()
    assert (tmp_path / "b.mp4").exists()


def test_over_budget_evicts_oldest_first(tmp_path: Path) -> None:
    # Three 100-byte files; budget 250 forces one eviction (the oldest).
    _write(tmp_path / "old.mp4", 100, mtime=1.0)
    _write(tmp_path / "mid.mp4", 100, mtime=2.0)
    _write(tmp_path / "new.mp4", 100, mtime=3.0)

    result = sweep_source_cache(tmp_path, max_bytes=250)

    assert result.evicted_files == 1
    assert result.evicted_bytes == 100
    assert result.total_bytes_after == 200
    assert not (tmp_path / "old.mp4").exists()
    assert (tmp_path / "mid.mp4").exists()
    assert (tmp_path / "new.mp4").exists()


def test_eviction_is_byte_keyed_not_count_keyed(tmp_path: Path) -> None:
    # One big old file dwarfs many small new ones. A count-keyed policy would
    # evict several small files; a byte-keyed one drops the single big file.
    _write(tmp_path / "big_old.mp4", 1_000, mtime=1.0)
    for i in range(5):
        _write(tmp_path / f"small_{i}.mp4", 10, mtime=10.0 + i)

    result = sweep_source_cache(tmp_path, max_bytes=100)

    assert not (tmp_path / "big_old.mp4").exists()
    assert result.evicted_files == 1
    assert result.evicted_bytes == 1_000
    for i in range(5):
        assert (tmp_path / f"small_{i}.mp4").exists()


def test_prunes_directories_left_empty(tmp_path: Path) -> None:
    _write(tmp_path / "match-1" / "raw.mp4", 500, mtime=1.0)
    _write(tmp_path / "match-2" / "raw.mp4", 100, mtime=2.0)

    sweep_source_cache(tmp_path, max_bytes=200)

    assert not (tmp_path / "match-1").exists()
    assert (tmp_path / "match-2" / "raw.mp4").exists()


def test_nested_files_counted_and_evicted(tmp_path: Path) -> None:
    _write(tmp_path / "users" / "u1" / "old.mp4", 100, mtime=1.0)
    _write(tmp_path / "users" / "u2" / "new.mp4", 100, mtime=5.0)

    result = sweep_source_cache(tmp_path, max_bytes=100)

    assert result.total_bytes_before == 200
    assert result.evicted_files == 1
    assert not (tmp_path / "users" / "u1" / "old.mp4").exists()
    assert (tmp_path / "users" / "u2" / "new.mp4").exists()
