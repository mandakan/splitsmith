"""Typed seam between the UI and a scoreboard data source.

The ``ScoreboardClient`` Protocol is the only thing UI code is allowed to
depend on. Concrete implementations (``LocalJsonScoreboard`` for offline use,
``SsiHttpClient`` for the live ``scoreboard.urdr.dev`` API) live in their
own modules and arrive in follow-up issues #48 and #49.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from splitsmith.ui.scoreboard.models import (
    MatchData,
    MatchRef,
    ShooterDashboard,
    ShooterRef,
)


@runtime_checkable
class ScoreboardClient(Protocol):
    """Read-only access to IPSC match and shooter data."""

    def search_matches(self, query: str) -> list[MatchRef]:
        """Free-text search; mirrors ``GET /api/v1/events?q=``."""
        ...

    def get_match(self, content_type: int, match_id: int) -> MatchData:
        """Full match overview; mirrors ``GET /api/v1/match/{ct}/{id}``."""
        ...

    def find_shooter(self, name: str) -> list[ShooterRef]:
        """Name search over indexed shooters; mirrors ``GET /api/v1/shooter/search?q=``."""
        ...

    def get_shooter(self, shooter_id: int) -> ShooterDashboard:
        """Cross-match dashboard; mirrors ``GET /api/v1/shooter/{shooterId}``."""
        ...
