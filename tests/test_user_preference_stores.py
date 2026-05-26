"""Tests for the RecentProjectsStore / ScoreboardIdentityStore abstractions.

Tier 3 of doc 10 (singleton elimination). The module-level JSON
functions in :mod:`splitsmith.user_config` are still the local
implementation; this file proves the Protocol abstraction is in
place so a hosted-mode backend can swap in.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from splitsmith.user_config import (
    JsonRecentProjectsStore,
    JsonScoreboardIdentityStore,
    RecentProject,
    RecentProjectsStore,
    ScoreboardIdentity,
    ScoreboardIdentityStore,
)


def test_json_stores_satisfy_their_protocols() -> None:
    """Structural-typing check: the local impls expose every Protocol
    method. Catches signature drift before it surfaces inside a
    handler that depends on the abstraction.
    """
    rp: RecentProjectsStore = JsonRecentProjectsStore()
    sb: ScoreboardIdentityStore = JsonScoreboardIdentityStore()

    async def _exercise_recent() -> None:
        # Touch every method (autouse fixture redirects ~/.splitsmith
        # to a tmp dir per conftest._isolate_user_config, so these
        # writes don't pollute the developer's real config).
        assert await rp.list() == []
        await rp.record_open(Path("/tmp/some-match"), "Test Match", kind="match")
        assert len(await rp.list()) == 1
        assert await rp.remove(Path("/tmp/some-match")) is True
        assert await rp.list() == []

    asyncio.run(_exercise_recent())

    assert sb.load() is None
    sb.save(ScoreboardIdentity(shooter_id=123, display_name="Tester"))
    loaded = sb.load()
    assert loaded is not None
    assert loaded.shooter_id == 123
    sb.clear()
    assert sb.load() is None


def test_state_recent_projects_is_swappable() -> None:
    """The whole point of the Protocol: hosted-mode (per-user
    Postgres rows) drops in here without touching handlers."""
    from splitsmith.ui.server import create_app

    recorded: list[dict] = []
    listed_count = 0

    class _FakeRecentProjects:
        async def list(self) -> list[RecentProject]:
            nonlocal listed_count
            listed_count += 1
            return [
                RecentProject(
                    path="/fake/match",
                    name="Fake",
                    last_opened_at=datetime.now(UTC),
                    kind="match",
                )
            ]

        async def record_open(self, path: Path, name: str, *, kind: str | None = None) -> None:
            recorded.append({"path": str(path), "name": name, "kind": kind})

        async def remove(self, path: Path) -> bool:
            return False

    app = create_app()
    state = app.state.splitsmith_state
    fake: RecentProjectsStore = _FakeRecentProjects()
    state.recent_projects = fake

    # Driving through the same accessor the handlers use proves the
    # swap actually intercepts -- not just that the field type
    # accepts the assignment.
    listed = asyncio.run(state.recent_projects.list())
    assert listed[0].name == "Fake"
    assert listed_count == 1


def test_state_scoreboard_identity_is_swappable() -> None:
    from splitsmith.ui.server import create_app

    saved: list[ScoreboardIdentity] = []

    class _FakeScoreboardIdentity:
        def load(self) -> ScoreboardIdentity | None:
            return ScoreboardIdentity(shooter_id=999, display_name="From Fake")

        def save(self, identity: ScoreboardIdentity) -> None:
            saved.append(identity)

        def clear(self) -> None:
            pass

    app = create_app()
    state = app.state.splitsmith_state
    fake: ScoreboardIdentityStore = _FakeScoreboardIdentity()
    state.scoreboard_identity = fake

    loaded = state.scoreboard_identity.load()
    assert loaded is not None
    assert loaded.shooter_id == 999

    state.scoreboard_identity.save(ScoreboardIdentity(shooter_id=42, display_name="x"))
    assert len(saved) == 1
    assert saved[0].shooter_id == 42
