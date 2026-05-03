"""Project-local disk cache decorator for any ``ScoreboardClient``.

Sits between the UI and a real client (typically ``SsiHttpClient``) so a
match opened a second time loads instantly and the cached payload travels
with the project directory: zip the project folder and the cache goes
with it (issue #14 acceptance criterion).

Why a decorator instead of caching inside the HTTP client: the cache is
project-scoped, but the HTTP client is process-scoped; folding caching
into the HTTP layer would couple cache lifetime to the wrong object and
prevent zip-and-send portability. Wrap, don't weave.

TTL policy (issue #49):

- Completed matches (``scoring_completed >= 100.0``): cached forever.
  These payloads don't change after the match wraps.
- In-progress matches: cached, but flagged ``in_progress=True`` in the
  envelope. The UI shows a manual refresh button; ``invalidate_match``
  clears the entry on user request.
- ``search_matches``, ``find_shooter``, ``get_shooter``: not cached.
  Lists/dashboards rotate too often for a project-local cache to be
  useful, and the UI re-runs them only on explicit user input.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from splitsmith.ui.scoreboard.models import (
    MatchData,
    MatchRef,
    ShooterDashboard,
    ShooterRef,
)
from splitsmith.ui.scoreboard.protocol import ScoreboardClient

CACHE_DIRNAME = "cache"
SCOREBOARD_DIRNAME = "scoreboard"
CACHE_VERSION = 1
COMPLETED_THRESHOLD = 100.0


class CachingScoreboardClient:
    """Decorator: serves ``get_match`` from a project-local disk cache."""

    def __init__(self, inner: ScoreboardClient, cache_dir: Path) -> None:
        self._inner = inner
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_project(cls, inner: ScoreboardClient, project_dir: Path) -> CachingScoreboardClient:
        """Resolve the conventional ``<project>/scoreboard/cache/`` directory."""
        return cls(inner, project_dir / SCOREBOARD_DIRNAME / CACHE_DIRNAME)

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def search_matches(self, query: str) -> list[MatchRef]:
        return self._inner.search_matches(query)

    def find_shooter(self, name: str) -> list[ShooterRef]:
        return self._inner.find_shooter(name)

    def get_shooter(self, shooter_id: int) -> ShooterDashboard:
        return self._inner.get_shooter(shooter_id)

    def get_match(self, content_type: int, match_id: int) -> MatchData:
        cache_path = self._match_cache_path(content_type, match_id)
        cached = _read_envelope(cache_path)
        if cached is not None and not cached.get("in_progress", False):
            return MatchData.model_validate(cached["data"])

        match = self._inner.get_match(content_type, match_id)
        in_progress = match.scoring_completed < COMPLETED_THRESHOLD
        envelope = {
            "version": CACHE_VERSION,
            "endpoint": "match",
            "params": {"content_type": content_type, "match_id": match_id},
            "cached_at": _utc_now_iso(),
            "in_progress": in_progress,
            "match_status": match.match_status,
            "scoring_completed": match.scoring_completed,
            "data": match.model_dump(by_alias=True),
        }
        _write_envelope(cache_path, envelope)
        return match

    def invalidate_match(self, content_type: int, match_id: int) -> bool:
        """Drop the cached match so the next ``get_match`` refetches.

        Returns True if a cache entry was removed. Used by the UI's manual
        refresh button for in-progress matches.
        """
        path = self._match_cache_path(content_type, match_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def is_cached(self, content_type: int, match_id: int) -> bool:
        return self._match_cache_path(content_type, match_id).exists()

    def _match_cache_path(self, content_type: int, match_id: int) -> Path:
        params = {"content_type": content_type, "match_id": match_id}
        return self._cache_dir / f"match_{_param_hash('match', params)}.json"


def _param_hash(endpoint: str, params: dict[str, Any]) -> str:
    """Stable filename hash from (endpoint, sorted_params).

    Uses sorted keys + a JSON canonical form so equivalent param dicts
    produce the same hash regardless of insertion order. Truncated to 16
    hex chars -- collision risk over a project's lifetime is negligible.
    """
    payload = json.dumps(
        {"endpoint": endpoint, "params": dict(sorted(params.items()))},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_envelope(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return None
    return data


def _write_envelope(path: Path, envelope: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmp file in the same dir, then rename. Avoids a torn
    # JSON file if the process is killed mid-write.
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(envelope, tmp, indent=2, sort_keys=True)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
