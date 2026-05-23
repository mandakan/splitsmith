"""Tests for ``splitsmith.match_registry`` (issue #353 Phase 3 PR A)."""

from __future__ import annotations

from pathlib import Path

import pytest

from splitsmith import user_config
from splitsmith.match_model import Match
from splitsmith.match_registry import MatchNotRegisteredError, MatchRegistry


@pytest.fixture
def isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``~/.splitsmith`` at a per-test tmp dir.

    The autouse fixture in conftest already isolates ``SPLITSMITH_HOME``,
    but several of these tests call ``user_config.record_project_open``
    directly, so we re-establish a fresh dir here to keep the state
    deterministic and avoid bleed from other tests in the file.
    """
    home = tmp_path / "user-config-home"
    monkeypatch.setenv("SPLITSMITH_HOME", str(home))
    return home


def _make_match(root: Path, *, name: str) -> Match:
    return Match.init(root, name=name)


def test_register_and_resolve_roundtrip(tmp_path: Path) -> None:
    reg = MatchRegistry()
    root = tmp_path / "m"
    match = _make_match(root, name="Test Match")
    reg.register(match.match_id, root)
    assert reg.resolve(match.match_id) == root.resolve()


def test_resolve_unknown_id_raises(tmp_path: Path, isolated_user_config: Path) -> None:
    reg = MatchRegistry()
    with pytest.raises(MatchNotRegisteredError):
        reg.resolve("never-seen-id-0000000000")


def test_refresh_picks_up_recent_match(tmp_path: Path, isolated_user_config: Path) -> None:
    reg = MatchRegistry()
    match_root = tmp_path / "recent"
    match = _make_match(match_root, name="Recent Match")
    user_config.record_project_open(match_root, match.name, kind="match")

    registered = reg.refresh_from_recent_projects()
    assert registered == 1
    assert reg.resolve(match.match_id) == match_root.resolve()


def test_refresh_skips_legacy_and_missing_entries(tmp_path: Path, isolated_user_config: Path) -> None:
    reg = MatchRegistry()
    # A real match.
    real_root = tmp_path / "real"
    real = _make_match(real_root, name="Real")
    user_config.record_project_open(real_root, real.name, kind="match")
    # Legacy single-shooter project (no match.json).
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    user_config.record_project_open(legacy_root, "Legacy", kind="legacy")
    # Path that disappeared from disk after being recorded.
    ghost = tmp_path / "ghost"
    ghost.mkdir()
    user_config.record_project_open(ghost, "Ghost", kind="match")
    import shutil

    shutil.rmtree(ghost)

    n = reg.refresh_from_recent_projects()
    assert n == 1
    assert reg.known_ids() == [real.match_id]


def test_resolve_falls_back_to_refresh_on_miss(tmp_path: Path, isolated_user_config: Path) -> None:
    """Freshly-recorded match becomes resolvable without an explicit refresh."""
    reg = MatchRegistry()
    match_root = tmp_path / "fresh"
    match = _make_match(match_root, name="Fresh Match")
    # Recording the match *after* the registry was built; resolve() must
    # rescan recent projects on miss to find it.
    user_config.record_project_open(match_root, match.name, kind="match")
    assert reg.resolve(match.match_id) == match_root.resolve()


def test_register_overwrites_when_path_changes(tmp_path: Path) -> None:
    reg = MatchRegistry()
    match_root = tmp_path / "original"
    match = _make_match(match_root, name="Movable")

    moved_root = tmp_path / "moved"
    match_root.rename(moved_root)

    reg.register(match.match_id, match_root)
    reg.register(match.match_id, moved_root)
    assert reg.resolve(match.match_id) == moved_root.resolve()


def test_forget_removes_id(tmp_path: Path) -> None:
    reg = MatchRegistry()
    match = _make_match(tmp_path / "m", name="Bye")
    reg.register(match.match_id, tmp_path / "m")
    reg.forget(match.match_id)
    assert match.match_id not in reg.known_ids()


def test_two_matches_same_name_get_distinct_ids(tmp_path: Path, isolated_user_config: Path) -> None:
    """Two matches with identical names but different created_at register separately."""
    import time

    a = _make_match(tmp_path / "a", name="Dup Name")
    # ``created_at`` is generated via ``datetime.now(UTC)``; sleep enough
    # microseconds to guarantee a different timestamp on the second match.
    time.sleep(0.005)
    b = _make_match(tmp_path / "b", name="Dup Name")
    assert a.match_id != b.match_id

    reg = MatchRegistry()
    reg.register(a.match_id, tmp_path / "a")
    reg.register(b.match_id, tmp_path / "b")
    assert reg.resolve(a.match_id) == (tmp_path / "a").resolve()
    assert reg.resolve(b.match_id) == (tmp_path / "b").resolve()
