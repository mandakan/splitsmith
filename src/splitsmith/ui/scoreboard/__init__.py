"""SSI Scoreboard integration package.

The seam between the splitsmith UI and any IPSC match-data source. See
issues #14 and #47.

Sub-modules:
- ``protocol``: ``ScoreboardClient`` Protocol the UI consumes
- ``models``: Pydantic v2 models matching the public ``/api/v1/`` shapes
  documented at https://github.com/mandakan/ssi-scoreboard ``docs/api-v1.md``
- ``local``: ``LocalJsonScoreboard`` -- offline implementation that serves
  a single dropped match file (issue #48)
"""

from splitsmith.ui.scoreboard.local import LocalJsonScoreboard
from splitsmith.ui.scoreboard.models import (
    AchievementProgress,
    CacheInfo,
    CompetitorInfo,
    MatchData,
    MatchRef,
    ShooterAggregateStats,
    ShooterDashboard,
    ShooterMatchSummary,
    ShooterRef,
    SquadInfo,
    StageInfo,
    UpcomingMatch,
)
from splitsmith.ui.scoreboard.protocol import ScoreboardClient

__all__ = [
    "AchievementProgress",
    "CacheInfo",
    "CompetitorInfo",
    "LocalJsonScoreboard",
    "MatchData",
    "MatchRef",
    "ScoreboardClient",
    "ShooterAggregateStats",
    "ShooterDashboard",
    "ShooterMatchSummary",
    "ShooterRef",
    "SquadInfo",
    "StageInfo",
    "UpcomingMatch",
]
