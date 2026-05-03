"""Tests for splitsmith.user_config (issue #75)."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from splitsmith import user_config


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every test at a per-test ``SPLITSMITH_HOME`` so the user's
    real ``~/.splitsmith/`` is never touched. The fixture also clears the
    disable flag so each test opts in / out explicitly.
    """
    home = tmp_path / "splitsmith-home"
    monkeypatch.setenv(user_config.ENV_HOME, str(home))
    monkeypatch.delenv(user_config.ENV_DISABLE, raising=False)
    return home


def test_user_config_dir_uses_env_override(_isolated_user_config: Path) -> None:
    assert user_config.user_config_dir() == _isolated_user_config


def test_user_config_dir_unset_falls_back_to_platform_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(user_config.ENV_HOME, raising=False)
    monkeypatch.delenv(user_config.ENV_XDG, raising=False)
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    resolved = user_config.user_config_dir()
    if sys.platform.startswith("linux"):
        assert resolved == fake_home / ".config" / "splitsmith"
    else:
        assert resolved == fake_home / ".splitsmith"


def test_xdg_config_home_honoured_on_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("XDG layout only applies on Linux")
    monkeypatch.delenv(user_config.ENV_HOME, raising=False)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv(user_config.ENV_XDG, str(xdg))
    assert user_config.user_config_dir() == xdg / "splitsmith"


def test_directory_created_lazily_on_first_write(
    _isolated_user_config: Path, tmp_path: Path
) -> None:
    project = tmp_path / "match"
    project.mkdir()
    assert not _isolated_user_config.exists()

    # Pure reads must NOT create the directory (acceptance: no error if it
    # doesn't exist).
    assert user_config.get_recent_projects() == []
    assert user_config.load_scoreboard_identity() is None
    assert user_config.load_global_prefs() == user_config.GlobalPrefs()
    assert not _isolated_user_config.exists()

    # Writing creates the directory + the file.
    user_config.record_project_open(project, "Test Match")
    assert _isolated_user_config.is_dir()
    assert (_isolated_user_config / user_config.PROJECTS_FILENAME).is_file()


def test_record_project_open_appends_and_dedupes(
    _isolated_user_config: Path, tmp_path: Path
) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()

    user_config.record_project_open(a, "A")
    user_config.record_project_open(b, "B")
    recent = user_config.get_recent_projects()
    assert [p.name for p in recent] == ["B", "A"]
    assert [Path(p.path) for p in recent] == [b.resolve(), a.resolve()]

    # Re-opening A bumps it back to the front and refreshes the timestamp.
    earlier = recent[1].last_opened_at
    user_config.record_project_open(a, "A renamed")
    refreshed = user_config.get_recent_projects()
    assert [p.name for p in refreshed] == ["A renamed", "B"]
    assert refreshed[0].last_opened_at >= earlier


def test_record_project_open_resolves_paths(_isolated_user_config: Path, tmp_path: Path) -> None:
    project = tmp_path / "m"
    project.mkdir()
    user_config.record_project_open(project, "M")
    user_config.record_project_open(project / "..", "M alt")
    user_config.record_project_open(project / "." / "..", "M alt 2")
    # Different relative paths to the SAME project must collapse to one
    # entry; otherwise users see one row per ``cd`` they tried.
    assert len(user_config.get_recent_projects()) <= 2  # parent + project both valid
    # The exact project itself is one entry, not duplicated.
    project_entries = [
        p for p in user_config.get_recent_projects() if Path(p.path) == project.resolve()
    ]
    assert len(project_entries) == 1


def test_recent_projects_capped(
    _isolated_user_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(user_config, "RECENT_PROJECTS_LIMIT", 3)
    for i in range(5):
        d = tmp_path / f"p{i}"
        d.mkdir()
        user_config.record_project_open(d, f"P{i}")
    recent = user_config.get_recent_projects()
    assert len(recent) == 3
    assert [p.name for p in recent] == ["P4", "P3", "P2"]


def test_remove_recent_project(_isolated_user_config: Path, tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    user_config.record_project_open(a, "A")
    user_config.record_project_open(b, "B")

    assert user_config.remove_recent_project(a) is True
    remaining = user_config.get_recent_projects()
    assert [p.name for p in remaining] == ["B"]

    # Removing again is a no-op (returns False, no crash).
    assert user_config.remove_recent_project(a) is False


def test_save_and_load_scoreboard_identity(_isolated_user_config: Path) -> None:
    identity = user_config.ScoreboardIdentity(
        shooter_id=12345,
        display_name="Mathias Axell",
        division="Production Optics",
        club="Bromma PK",
        base_url="https://shootnscoreit.com",
    )
    user_config.save_scoreboard_identity(identity)

    loaded = user_config.load_scoreboard_identity()
    assert loaded is not None
    assert loaded.shooter_id == 12345
    assert loaded.display_name == "Mathias Axell"
    assert loaded.base_url == "https://shootnscoreit.com"

    # Schema version is stamped on write.
    on_disk = json.loads((_isolated_user_config / user_config.SCOREBOARD_FILENAME).read_text())
    assert on_disk["schema_version"] == user_config.SCHEMA_VERSION


def test_clear_scoreboard_identity(_isolated_user_config: Path) -> None:
    user_config.save_scoreboard_identity(user_config.ScoreboardIdentity(shooter_id=1))
    assert user_config.load_scoreboard_identity() is not None
    user_config.clear_scoreboard_identity()
    assert user_config.load_scoreboard_identity() is None
    # Idempotent.
    user_config.clear_scoreboard_identity()


def test_malformed_files_are_ignored(_isolated_user_config: Path) -> None:
    _isolated_user_config.mkdir(parents=True, exist_ok=True)
    (_isolated_user_config / user_config.PROJECTS_FILENAME).write_text("not json{{{")
    (_isolated_user_config / user_config.SCOREBOARD_FILENAME).write_text("also bad")
    (_isolated_user_config / user_config.CONFIG_FILENAME).write_text(": : :\n: : :")

    assert user_config.get_recent_projects() == []
    assert user_config.load_scoreboard_identity() is None
    assert user_config.load_global_prefs() == user_config.GlobalPrefs()


def test_disable_flag_blocks_reads_and_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _isolated_user_config: Path
) -> None:
    monkeypatch.setenv(user_config.ENV_DISABLE, "1")
    project = tmp_path / "m"
    project.mkdir()
    user_config.record_project_open(project, "M")
    user_config.save_scoreboard_identity(user_config.ScoreboardIdentity(shooter_id=99))
    user_config.save_global_prefs(user_config.GlobalPrefs(theme="dark"))
    assert not _isolated_user_config.exists()
    assert user_config.get_recent_projects() == []
    assert user_config.load_scoreboard_identity() is None
    assert user_config.load_global_prefs() == user_config.GlobalPrefs()


def test_global_prefs_round_trip(_isolated_user_config: Path) -> None:
    prefs = user_config.GlobalPrefs(
        theme="dark",
        default_trim_mode="audit",
        last_scoreboard_url="https://scoreboard.urdr.dev/competitions/1/2",
    )
    user_config.save_global_prefs(prefs)

    loaded = user_config.load_global_prefs()
    assert loaded.theme == "dark"
    assert loaded.default_trim_mode == "audit"
    assert loaded.last_scoreboard_url is not None

    # Stored as YAML for human-editability.
    on_disk = yaml.safe_load((_isolated_user_config / user_config.CONFIG_FILENAME).read_text())
    assert on_disk["theme"] == "dark"


def test_record_project_open_swallows_write_errors(
    _isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the global config dir is unwritable, ``record_project_open``
    must not crash the caller -- the acceptance criterion says global
    state is opt-out friendly. We simulate the disk-full / permission-
    denied case by making ``_atomic_write_text`` raise.
    """
    project = tmp_path / "m"
    project.mkdir()
    user_config.record_project_open(project, "Original")
    target = _isolated_user_config / user_config.PROJECTS_FILENAME
    original = target.read_text()

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("simulated disk full")

    monkeypatch.setattr(user_config, "_atomic_write_text", boom)
    # The write swallows the OSError and logs; the file stays intact.
    user_config.record_project_open(project, "Updated")

    assert target.read_text() == original


def test_recent_project_last_opened_at_is_iso(_isolated_user_config: Path, tmp_path: Path) -> None:
    project = tmp_path / "m"
    project.mkdir()
    user_config.record_project_open(project, "M")
    raw = json.loads((_isolated_user_config / user_config.PROJECTS_FILENAME).read_text())
    ts = raw["projects"][0]["last_opened_at"]
    # Round-trips through datetime so the SPA can parse it as ISO 8601.
    assert datetime.fromisoformat(ts.replace("Z", "+00:00"))
