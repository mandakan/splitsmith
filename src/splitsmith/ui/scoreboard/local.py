"""Offline ``ScoreboardClient`` -- serves a single SSI match from disk.

The user drops the response body of ``GET /api/v1/match/{ct}/{id}`` into
``<project>/scoreboard/match.json``; this client parses it through the
same ``MatchData`` model an ``SsiHttpClient`` populates online, so both
paths produce an identical internal shape (acceptance criterion from
issue #14).

Cross-match shooter aggregates (``get_shooter``) require many matches and
are not implementable from a single dropped file -- that operation raises
``NotImplementedError`` in offline mode.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from splitsmith.ui.scoreboard.models import (
    MatchData,
    MatchRef,
    ShooterDashboard,
    ShooterRef,
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
        self._match = MatchData.model_validate(raw)
        self._content_type, self._match_id = _parse_ssi_url(self._match.ssi_url)

    @classmethod
    def from_project(cls, project_dir: Path) -> LocalJsonScoreboard:
        """Resolve the conventional ``<project>/scoreboard/match.json`` path."""
        return cls(project_dir / DEFAULT_SCOREBOARD_DIRNAME / DEFAULT_MATCH_FILENAME)

    @property
    def match(self) -> MatchData:
        """The parsed match -- exposed for callers that don't want a round-trip."""
        return self._match

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
        # cross-match career aggregates. UI wiring (#50) should hide shooter
        # search when the active client is a ``LocalJsonScoreboard``.
        # Future: if the user wants offline shooter dashboards, expand the
        # file format to also accept ``<project>/scoreboard/shooter/{id}.json``
        # captured from ``GET /api/v1/shooter/{shooterId}`` -- track in a
        # follow-up issue rather than reopening #48.
        raise NotImplementedError(
            "shooter dashboard requires the live scoreboard; offline JSON only "
            "carries a single match's competitor list, not cross-match aggregates"
        )

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


def _parse_ssi_url(url: str | None) -> tuple[int | None, int | None]:
    if not url:
        return None, None
    m = _SSI_URL_RE.search(url)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))
