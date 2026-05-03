"""Pydantic v2 models for ``scoreboard.urdr.dev/api/v1/`` responses.

Field names mirror the documented JSON shapes verbatim (mixed snake_case and
camelCase) so a parsed model round-trips back to the wire shape with no
adapter. Translation into splitsmith's snake_case ``MatchProject`` happens
in a separate adapter, not here.

Per the v1 versioning policy, additional optional fields may appear over
time; ``model_config = ConfigDict(extra="ignore")`` lets old clients keep
parsing newer payloads.

Contract reference: https://github.com/mandakan/ssi-scoreboard/blob/main/docs/api-v1.md
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MatchRef(_ApiModel):
    """One entry in the ``GET /api/v1/events`` array."""

    id: int
    content_type: int
    name: str
    venue: str | None = None
    date: str
    ends: str | None = None
    status: str
    region: str
    discipline: str
    level: str
    registration_status: str
    registration_starts: str | None = None
    registration_closes: str | None = None
    is_registration_possible: bool
    squadding_starts: str | None = None
    squadding_closes: str | None = None
    is_squadding_possible: bool
    max_competitors: int | None = None
    scoring_completed: float


class StageInfo(_ApiModel):
    id: int
    name: str
    stage_number: int
    max_points: int | None = None
    min_rounds: int | None = None
    paper_targets: int | None = None
    steel_targets: int | None = None
    ssi_url: str | None = None
    course_display: str | None = None
    procedure: str | None = None
    firearm_condition: str | None = None


class CompetitorInfo(_ApiModel):
    id: int
    shooterId: int
    name: str
    competitor_number: int | None = None
    club: str | None = None
    division: str | None = None
    region: str | None = None
    region_display: str | None = None
    category: str | None = None
    ics_alias: str | None = None
    license: str | None = None


class SquadInfo(_ApiModel):
    id: int
    number: int
    name: str
    competitorIds: list[int]


class CacheInfo(_ApiModel):
    cachedAt: str | None = None
    upstreamDegraded: bool | None = None
    lastScorecardAt: str | None = None
    scorecardsCachedAt: str | None = None


class MatchData(_ApiModel):
    """``GET /api/v1/match/{ct}/{id}`` response body."""

    name: str
    venue: str | None = None
    lat: float | None = None
    lng: float | None = None
    date: str | None = None
    ends: str | None = None
    level: str | None = None
    sub_rule: str | None = None
    discipline: str | None = None
    region: str | None = None
    stages_count: int
    competitors_count: int
    max_competitors: int | None = None
    scoring_completed: float
    match_status: str
    results_status: str
    registration_status: str
    registration_starts: str | None = None
    registration_closes: str | None = None
    is_registration_possible: bool
    squadding_starts: str | None = None
    squadding_closes: str | None = None
    is_squadding_possible: bool
    ssi_url: str | None = None
    stages: list[StageInfo]
    competitors: list[CompetitorInfo]
    squads: list[SquadInfo]
    cacheInfo: CacheInfo


class ShooterRef(_ApiModel):
    """One entry in the ``GET /api/v1/shooter/search`` array."""

    shooterId: int
    name: str
    club: str | None = None
    division: str | None = None
    lastSeen: str


class ShooterMatchSummary(_ApiModel):
    # ``ct`` and ``matchId`` come back as strings in this endpoint even though
    # they're numbers in MatchRef -- match the wire shape.
    ct: str
    matchId: str
    name: str
    date: str
    venue: str | None = None
    level: str | None = None
    region: str | None = None
    division: str | None = None
    competitorId: int
    competitorsInDivision: int | None = None
    stageCount: int
    avgHF: float | None = None
    matchPct: float | None = None
    totalA: int
    totalC: int
    totalD: int
    totalMiss: int
    totalNoShoots: int
    totalProcedurals: int | None = None
    dq: bool | None = None
    perfectStages: int | None = None
    consistencyIndex: float | None = None
    squadmateShooterIds: list[int] | None = None
    squadAllSameClub: bool | None = None
    discipline: str | None = None


class ShooterDateRange(BaseModel):
    # ``from`` is a Python keyword; dump with ``by_alias=True`` to round-trip.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class ShooterAggregateStats(_ApiModel):
    totalStages: int
    dateRange: ShooterDateRange
    overallAvgHF: float | None = None
    overallMatchPct: float | None = None
    aPercent: float | None = None
    cPercent: float | None = None
    dPercent: float | None = None
    missPercent: float | None = None
    consistencyCV: float | None = None
    hfTrendSlope: float | None = None
    avgPenaltyRate: float | None = None
    avgConsistencyIndex: float | None = None


class AchievementTier(_ApiModel):
    level: int
    name: str | None = None
    threshold: float
    label: str | None = None


class AchievementUnlock(_ApiModel):
    level: int
    unlockedAt: str
    matchRef: dict | None = None
    value: float | None = None


class AchievementDefinition(_ApiModel):
    id: str
    name: str
    description: str | None = None
    category: str | None = None
    icon: str | None = None
    tiers: list[AchievementTier]


class AchievementProgress(_ApiModel):
    definition: AchievementDefinition
    currentValue: float | None = None
    unlockedTiers: list[AchievementUnlock]
    nextTier: AchievementTier | None = None
    progressToNext: float | None = None


class UpcomingMatch(_ApiModel):
    ct: str
    matchId: str
    name: str
    date: str
    venue: str | None = None
    level: str | None = None
    division: str | None = None
    competitorId: int | None = None
    registrationStarts: str | None = None
    registrationCloses: str | None = None
    isRegistrationPossible: bool | None = None
    squaddingStarts: str | None = None
    squaddingCloses: str | None = None
    isSquaddingPossible: bool | None = None
    isRegistered: bool | None = None
    isSquadded: bool | None = None


class ShooterProfile(_ApiModel):
    name: str
    club: str | None = None
    division: str | None = None
    lastSeen: str
    region: str | None = None
    region_display: str | None = None
    category: str | None = None
    ics_alias: str | None = None
    license: str | None = None


class ShooterDashboard(_ApiModel):
    """``GET /api/v1/shooter/{shooterId}`` response body."""

    shooterId: int
    profile: ShooterProfile | None = None
    matchCount: int
    matches: list[ShooterMatchSummary]
    stats: ShooterAggregateStats
    achievements: list[AchievementProgress] | None = None
    upcomingMatches: list[UpcomingMatch] | None = None


class CompetitorStageResult(_ApiModel):
    """One scored stage for one competitor.

    Mirrors the proposed shape in ``ssi-scoreboard#400``: per-stage timing,
    score breakdown, and rank context for a single (match, competitor)
    pair. Optional fields are nullable because partially-typed scorecards
    are common during a live match.
    """

    stage_number: int
    stage_name: str | None = None
    stage_id: int | None = None
    time_seconds: float | None = None
    scorecard_updated_at: str | None = None
    hit_factor: float | None = None
    stage_points: float | None = None
    stage_pct: float | None = None
    alphas: int | None = None
    charlies: int | None = None
    deltas: int | None = None
    misses: int | None = None
    no_shoots: int | None = None
    procedurals: int | None = None
    dq: bool | None = None


class CompetitorStageResults(_ApiModel):
    """``GET /api/v1/match/{ct}/{id}/competitor/{competitorId}/stages``
    response body.

    The endpoint isn't shipped yet upstream (``ssi-scoreboard#400``) -- the
    model is shipped now so the offline ``LocalJsonScoreboard`` path can
    serve it from richer dropped JSON, and so the HTTP path has a typed
    target the moment the upstream lands.
    """

    ct: int | None = None
    matchId: int | None = None
    competitorId: int
    shooterId: int | None = None
    division: str | None = None
    results: list[CompetitorStageResult]
