"""Shared pytest fixtures."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Redirect ``~/.splitsmith/`` to a per-test tmp dir for every test.

    Without this, ``create_app`` (and anything else that calls
    ``user_config.record_project_open`` / writes scoreboard identity)
    persists test-only entries into the developer's real
    ``~/.splitsmith/projects.json``. The 50-entry cap then evicts the
    user's actual matches off the back of the list, which surfaces in
    the picker as "only `Beep Match` and `x`, no real projects".

    Tests that need to inspect the on-disk projects.json can read the
    returned path; the existing per-test ``_user_config_home`` fixture
    in ``test_ui_server.py`` continues to work since it overrides the
    same env var with a deterministic name.
    """
    import os

    home = tmp_path_factory.mktemp("user-config")
    prev = os.environ.get("SPLITSMITH_HOME")
    os.environ["SPLITSMITH_HOME"] = str(home)
    try:
        yield home
    finally:
        if prev is None:
            os.environ.pop("SPLITSMITH_HOME", None)
        else:
            os.environ["SPLITSMITH_HOME"] = prev
