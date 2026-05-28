"""Shared pytest fixtures."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


def submit_fn(backend, *, kind: str, fn, **kwargs):
    """Test shim bridging the pre-gamma ``submit(fn=callable)`` API to the
    ``kind`` + body-registry dispatch.

    Registers ``fn`` as the body for ``kind`` on ``backend.bodies`` then
    returns the ``backend.submit(kind=...)`` coroutine, so existing tests
    can keep writing ``asyncio.run(submit_fn(backend, kind=..., fn=...))``.
    The body adapter swallows any ``args`` the dispatch would pass, since
    these test callables take only the handle.
    """
    backend.bodies.register(kind, lambda handle, **_args: fn(handle))
    return backend.submit(kind=kind, **kwargs)


def scaffold_match(
    tmp_path: Path,
    *,
    name: str = "Test Match",
    shooter_slug: str = "me",
    shooter_name: str = "Me",
    subdir: str = "match",
) -> tuple[Path, Path]:
    """Create a minimal Match folder + one shooter at ``tmp_path/subdir``.

    Returns ``(match_root, shooter_root)``. Tier 1 step 3 of doc 10
    retired the legacy single-shooter layout, so tests that used to
    ``MatchProject.init(tmp_path / "match", ...)`` and bind it
    directly must scaffold a Match folder instead. The default
    shooter slug ``"me"`` matches what the retired ``legacy_slug``
    helper produced for unnamed single-shooter projects, keeping
    migrated URLs ergonomic.

    Callers that don't care about the shooter slot can ignore the
    returned ``shooter_root``; callers that previously wrote per-
    shooter ``project.json`` (stages, audit, etc) at the match root
    should now write at ``shooter_root`` instead.
    """
    from splitsmith import match_model
    from splitsmith.ui.project import MatchProject

    root = tmp_path / subdir
    match = match_model.Match.init(root, name=name)
    match.add_shooter(root, match_model.Shooter(slug=shooter_slug, name=shooter_name))
    shooter_root = match_model.Match.shooter_root(root, shooter_slug)
    MatchProject.init(shooter_root, name=name)
    return root, shooter_root


def bound_match_id(app) -> str:
    """Read the registered match id from a test app's state.

    Post Tier 1 step 4 of doc 10, the server has no "bound" project --
    matches are registered in :attr:`AppState.matches` and addressed
    by URL. Tests typically scaffold a single Match folder; this
    helper returns that match's id so they can construct
    ``/api/matches/{match_id}/...`` URLs.
    """
    ids = app.state.splitsmith_state.matches.known_ids()
    assert len(ids) == 1, f"expected exactly one match registered, got {len(ids)}: {ids}"
    return ids[0]


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
