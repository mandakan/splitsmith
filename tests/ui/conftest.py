"""Fixtures every UI route test gets (formerly module-level in ``tests/test_ui_server.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _disable_auto_beep_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests in this module exercise specific beep flows (manual
    overrides, force re-detect, candidate selection) and would race
    against the on-assignment auto-fire from #67. Default to disabled
    here; the new auto-fire tests explicitly re-enable via
    :func:`_enable_auto_beep` so the assertions stay deterministic.
    """
    monkeypatch.setenv("SPLITSMITH_AUTO_BEEP_DISABLED", "1")


@pytest.fixture
def _user_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``~/.splitsmith/`` to a per-test directory so the user's
    real recent-projects list isn't touched by these tests.
    """
    from splitsmith import user_config

    home = tmp_path / "user-config"
    monkeypatch.setenv(user_config.ENV_HOME, str(home))
    monkeypatch.delenv(user_config.ENV_DISABLE, raising=False)
    return home
