"""SSI Scoreboard integration package.

The seam between the splitsmith UI and any IPSC match-data source. See
issues #14 and #47.

Sub-modules:
- ``protocol``: ``ScoreboardClient`` Protocol the UI consumes
- ``models``: Pydantic v2 models matching the public ``/api/v1/`` shapes
  documented at https://github.com/mandakan/ssi-scoreboard ``docs/api-v1.md``
- ``local``: ``LocalJsonScoreboard`` -- offline implementation that serves
  a single dropped match file (issue #48)
- ``http``: ``SsiHttpClient`` -- live online implementation (issue #49)
- ``cache``: ``CachingScoreboardClient`` -- project-local disk cache
  decorator that wraps any ``ScoreboardClient`` (issue #49)
"""

from splitsmith.ui.scoreboard.cache import CachingScoreboardClient
from splitsmith.ui.scoreboard.http import (
    CompetitorNotInMatch,
    MatchNotFound,
    ScoreboardAuthError,
    ScoreboardError,
    ScoreboardRateLimited,
    ScoreboardUpstreamError,
    ShooterNotFound,
    SsiHttpClient,
    StageTimesNotImplemented,
    StageTimesUnavailable,
)
from splitsmith.ui.scoreboard.local import LocalJsonScoreboard
from splitsmith.ui.scoreboard.models import (
    AchievementProgress,
    CacheInfo,
    CompetitorInfo,
    CompetitorStageResult,
    CompetitorStageResults,
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
    "CachingScoreboardClient",
    "CompetitorInfo",
    "CompetitorNotInMatch",
    "CompetitorStageResult",
    "CompetitorStageResults",
    "LocalJsonScoreboard",
    "MatchData",
    "MatchNotFound",
    "MatchRef",
    "ScoreboardAuthError",
    "ScoreboardClient",
    "ScoreboardError",
    "ScoreboardRateLimited",
    "ScoreboardUpstreamError",
    "ShooterAggregateStats",
    "ShooterDashboard",
    "ShooterMatchSummary",
    "ShooterNotFound",
    "ShooterRef",
    "SquadInfo",
    "SsiHttpClient",
    "StageInfo",
    "StageTimesNotImplemented",
    "StageTimesUnavailable",
    "UpcomingMatch",
]
