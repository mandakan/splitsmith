"""Offline ``ScoreboardClient`` -- serves match + stage data from disk.

Three input shapes are supported, in order of preference:

1. **Combined v1**: an SSI v1 ``MatchData`` (the response body of
   ``GET /api/v1/match/{ct}/{id}``) augmented with a top-level
   ``competitor_stages`` array of ``CompetitorStageResults``-shaped
   entries. This is the future shape we'd like SSI exports to emit so a
   single dropped file populates everything (#64).

2. **Legacy splitsmith export** (``examples/blacksmith-handgun-open-2026.json``
   etc): top-level ``{match, competitors[].stages[]}`` with per-competitor
   per-stage ``time_seconds`` + ``scorecard_updated_at``. Predates the v1
   API and predates this module; kept working so users with existing files
   don't have to re-export.

3. **Pure v1 ``MatchData``**: stages + competitor list, no per-competitor
   times. ``get_match`` works; ``get_stage_times`` raises
   ``StageTimesUnavailable`` so the UI can suggest a richer JSON.

Format detection happens once at construction and the resulting
representation is stored both as a synthesised ``MatchData`` (for
``get_match`` / ``search_matches`` / ``find_shooter``) and as an
optional ``competitor_id -> list[CompetitorStageResult]`` index (for
``get_stage_times``).

Cross-match shooter aggregates (``get_shooter``) are not implementable
from a single dropped file -- that operation raises ``NotImplementedError``
in offline mode regardless of input shape.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from splitsmith.ui.scoreboard.http import StageTimesUnavailable
from splitsmith.ui.scoreboard.models import (
    CacheInfo,
    CompetitorInfo,
    CompetitorStageResult,
    CompetitorStageResults,
    MatchData,
    MatchRef,
    ShooterDashboard,
    ShooterRef,
    StageInfo,
)

DEFAULT_MATCH_FILENAME = "match.json"
DEFAULT_SCOREBOARD_DIRNAME = "scoreboard"

# Captures (content_type, match_id) from URLs like
# ``https://shootnscoreit.com/event/22/27190/``. Used to validate
# ``get_match(ct, id)`` calls against the loaded file.
_SSI_URL_RE = re.compile(r"/event/(\d+)/(\d+)/?")


class LocalJsonScoreboard:
    """Read-only ``ScoreboardClient`` backed by one project-local match file."""

    def __init__(self, json_path: Path) -> None:
        self._path = json_path
        with json_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        (
            self._match,
            self._stage_times,
            self._content_type,
            self._match_id,
        ) = _parse_offline_payload(raw)

    @classmethod
    def from_project(cls, project_dir: Path) -> LocalJsonScoreboard:
        """Resolve the conventional ``<project>/scoreboard/match.json`` path."""
        return cls(project_dir / DEFAULT_SCOREBOARD_DIRNAME / DEFAULT_MATCH_FILENAME)

    @property
    def match(self) -> MatchData:
        """The parsed match -- exposed for callers that don't want a round-trip."""
        return self._match

    @property
    def content_type(self) -> int | None:
        """SSI ``content_type`` parsed from the loaded match's ``ssi_url``."""
        return self._content_type

    @property
    def match_id(self) -> int | None:
        """SSI numeric match id parsed from the loaded match's ``ssi_url``."""
        return self._match_id

    @property
    def has_stage_times(self) -> bool:
        """True when the dropped file carries per-competitor stage results.

        The server's auto-merge-on-upload path reads this to decide whether
        to call :meth:`get_stage_times` after :meth:`MatchProject.populate_from_match_data`.
        """
        return bool(self._stage_times)

    def default_competitor_id(self) -> int | None:
        """Return the lone competitor id when stage times are present for exactly one.

        Used by the upload path: when a user drops a richer single-competitor
        export, we can auto-pin without forcing them through the shooter
        picker. Returns ``None`` when the file holds zero or more than one
        competitor's results.
        """
        if len(self._stage_times) != 1:
            return None
        return next(iter(self._stage_times))

    def search_matches(self, query: str) -> list[MatchRef]:
        needle = query.strip().lower()
        if not needle:
            return [self._as_match_ref()]
        if needle in self._match.name.lower():
            return [self._as_match_ref()]
        return []

    def get_match(self, content_type: int, match_id: int) -> MatchData:
        if (
            self._content_type is not None
            and self._match_id is not None
            and (content_type, match_id) != (self._content_type, self._match_id)
        ):
            raise KeyError(
                f"local scoreboard holds match {self._content_type}/{self._match_id};"
                f" requested {content_type}/{match_id}"
            )
        return self._match

    def find_shooter(self, name: str) -> list[ShooterRef]:
        needle = name.strip().lower()
        if not needle:
            return []
        last_seen = self._match.date or ""
        return [
            ShooterRef(
                shooterId=c.shooterId,
                name=c.name,
                club=c.club,
                division=c.division,
                lastSeen=last_seen,
            )
            for c in self._match.competitors
            if needle in c.name.lower()
        ]

    def get_shooter(self, shooter_id: int) -> ShooterDashboard:
        # Offline-only by design: a single dropped match.json can't supply
        # cross-match career aggregates.
        raise NotImplementedError(
            "shooter dashboard requires the live scoreboard; offline JSON only "
            "carries a single match's competitor list, not cross-match aggregates"
        )

    def get_stage_times(
        self, content_type: int, match_id: int, competitor_id: int
    ) -> CompetitorStageResults:
        if (
            self._content_type is not None
            and self._match_id is not None
            and (content_type, match_id) != (self._content_type, self._match_id)
        ):
            raise KeyError(
                f"local scoreboard holds match {self._content_type}/{self._match_id};"
                f" requested stage times for {content_type}/{match_id}"
            )
        if not self._stage_times:
            raise StageTimesUnavailable(
                "the dropped file is pure SSI v1 MatchData -- it carries the "
                "match shell but no per-competitor stage results. Drop a "
                "richer JSON (legacy examples/* shape, or an SSI export with "
                "a top-level ``competitor_stages`` array) to populate stage "
                "times offline."
            )
        results = self._stage_times.get(competitor_id)
        if results is None:
            raise KeyError(
                f"local scoreboard has stage times for competitors "
                f"{sorted(self._stage_times)}; requested {competitor_id}"
            )
        return CompetitorStageResults(
            ct=self._content_type,
            matchId=self._match_id,
            competitorId=competitor_id,
            shooterId=self._shooter_id_for(competitor_id),
            division=self._division_for(competitor_id),
            stages=list(results),
        )

    def _shooter_id_for(self, competitor_id: int) -> int | None:
        for c in self._match.competitors:
            if c.id == competitor_id:
                return c.shooterId
        return None

    def _division_for(self, competitor_id: int) -> str | None:
        for c in self._match.competitors:
            if c.id == competitor_id:
                return c.division
        return None

    def _as_match_ref(self) -> MatchRef:
        m = self._match
        return MatchRef(
            id=self._match_id or 0,
            content_type=self._content_type or 0,
            name=m.name,
            venue=m.venue,
            date=m.date or "",
            ends=m.ends,
            status=m.match_status,
            region=m.region or "",
            discipline=m.discipline or "",
            level=m.level or "",
            registration_status=m.registration_status,
            registration_starts=m.registration_starts,
            registration_closes=m.registration_closes,
            is_registration_possible=m.is_registration_possible,
            squadding_starts=m.squadding_starts,
            squadding_closes=m.squadding_closes,
            is_squadding_possible=m.is_squadding_possible,
            max_competitors=m.max_competitors,
            scoring_completed=m.scoring_completed,
        )


def _parse_offline_payload(
    raw: dict[str, Any],
) -> tuple[MatchData, dict[int, list[CompetitorStageResult]], int | None, int | None]:
    """Detect input shape and produce a uniform internal representation.

    Returns ``(match_data, stage_times_by_competitor, ct, match_id)``. The
    stage-times dict is empty when the file is pure ``MatchData``.
    """
    if _looks_like_legacy(raw):
        return _from_legacy(raw)
    # Combined v1 (with optional ``competitor_stages``) and pure v1 both
    # parse as MatchData; the only difference is the presence of the
    # extra top-level array.
    match = MatchData.model_validate(raw)
    ct, mid = _parse_ssi_url(match.ssi_url)
    stage_times = _from_combined_v1(raw.get("competitor_stages"))
    return match, stage_times, ct, mid


def _looks_like_legacy(raw: dict[str, Any]) -> bool:
    """Heuristic: legacy export has top-level ``match`` + ``competitors[].stages[]``
    with no ``stages`` array at the top level (which v1 ``MatchData`` requires)."""
    if "stages" in raw and isinstance(raw["stages"], list):
        return False
    if not isinstance(raw.get("match"), dict):
        return False
    competitors = raw.get("competitors")
    if not isinstance(competitors, list) or not competitors:
        return False
    first = competitors[0]
    return isinstance(first, dict) and isinstance(first.get("stages"), list)


def _from_legacy(
    raw: dict[str, Any],
) -> tuple[MatchData, dict[int, list[CompetitorStageResult]], int | None, int | None]:
    """Synthesise a v1-shaped ``MatchData`` from the legacy
    ``examples/`` export, plus a per-competitor stage-times index.
    """
    match_meta = raw["match"]
    competitors_raw = raw["competitors"]
    ct = _coerce_int(match_meta.get("ct"))
    match_id = _coerce_int(match_meta.get("id") or match_meta.get("match_id"))
    name = match_meta.get("name") or "Match"

    # Reconstruct a stage list from the first competitor's stage rows --
    # every row carries stage_number + stage_name, which is enough to
    # build StageInfo entries for the match shell.
    first_stages: list[dict[str, Any]] = competitors_raw[0].get("stages", [])
    stages = [
        StageInfo(
            id=s.get("stage_id") or s.get("id") or s["stage_number"],
            name=s.get("stage_name") or f"Stage {s['stage_number']}",
            stage_number=s["stage_number"],
        )
        for s in first_stages
    ]
    stages.sort(key=lambda s: s.stage_number)

    competitors = [
        CompetitorInfo(
            id=c.get("competitor_id") or idx + 1,
            shooterId=c.get("shooterId") or c.get("competitor_id") or idx + 1,
            name=c.get("name") or f"Competitor {idx + 1}",
            competitor_number=c.get("competitor_number"),
            club=c.get("club"),
            division=c.get("division"),
        )
        for idx, c in enumerate(competitors_raw)
    ]

    match = MatchData(
        name=name,
        ssi_url=match_meta.get("ssi_url")
        or (f"https://shootnscoreit.com/event/{ct}/{match_id}/" if ct and match_id else None),
        stages_count=len(stages),
        competitors_count=len(competitors),
        scoring_completed=100.0,
        match_status=match_meta.get("match_status", "cp"),
        results_status=match_meta.get("results_status", "all"),
        registration_status=match_meta.get("registration_status", "cl"),
        is_registration_possible=False,
        is_squadding_possible=False,
        stages=stages,
        competitors=competitors,
        squads=[],
        cacheInfo=CacheInfo(),
    )

    stage_times: dict[int, list[CompetitorStageResult]] = {}
    for c in competitors_raw:
        cid = c.get("competitor_id")
        if cid is None:
            continue
        results = [
            CompetitorStageResult(
                stage_number=s["stage_number"],
                stage_name=s.get("stage_name"),
                time_seconds=s.get("time_seconds"),
                scorecard_updated_at=s.get("scorecard_updated_at"),
            )
            for s in c.get("stages", [])
        ]
        # The legacy fixture has duplicate competitor_id rows for separate
        # squads -- last write wins is fine for our purposes since they
        # share the same id key. Real exports won't duplicate.
        stage_times[cid] = results

    return match, stage_times, ct, match_id


def _from_combined_v1(
    competitor_stages: Any,
) -> dict[int, list[CompetitorStageResult]]:
    """Parse a combined-v1 file's optional top-level ``competitor_stages`` array."""
    if not isinstance(competitor_stages, list):
        return {}
    out: dict[int, list[CompetitorStageResult]] = {}
    for entry in competitor_stages:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("competitorId")
        if not isinstance(cid, int):
            continue
        results = [
            CompetitorStageResult.model_validate(r)
            for r in entry.get("stages", [])
            if isinstance(r, dict)
        ]
        out[cid] = results
    return out


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_ssi_url(url: str | None) -> tuple[int | None, int | None]:
    if not url:
        return None, None
    m = _SSI_URL_RE.search(url)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))
