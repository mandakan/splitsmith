"""In-process ``match_id`` -> match-root resolver (issue #353 Phase 3).

The first step toward dropping the ``state._bound_root`` singleton is a
way to address a match by something other than the bound filesystem
path. :func:`splitsmith.match_model.generate_match_id` mints stable
ids; this module gives the server a fast lookup from id back to disk.

The registry is intentionally simple:

* Backed by an in-memory dict keyed on ``match_id``.
* Populated lazily from :mod:`splitsmith.user_config` recent projects
  on the first :meth:`MatchRegistry.resolve` (or eagerly via
  :meth:`MatchRegistry.refresh` -- the server boot does this).
* Re-reads ``match.json`` on misses so a match that was just created /
  bound shows up without a server restart.
* Stateless beyond the cache: no on-disk index of its own. The source
  of truth stays the per-match ``match.json`` + the recent-projects
  list, both of which already exist.

In follow-up PRs the SPA URLs grow a ``/match/:matchId/`` segment and
shooter-scoped endpoints look up the match root via this registry
instead of ``state.bound_root``. This PR is the foundation; nothing
calls :meth:`resolve` for routing yet.
"""

from __future__ import annotations

import logging
from pathlib import Path
from threading import RLock

from . import user_config
from .match_model import Match

logger = logging.getLogger(__name__)


class MatchNotRegisteredError(KeyError):
    """Raised when a ``match_id`` is not known to the registry."""


class MatchRegistry:
    """Resolves stable ``match_id`` strings back to filesystem paths.

    Thread-safe (FastAPI happily fans requests across a thread pool;
    cache mutations need to be serialised).
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Path] = {}
        self._lock = RLock()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, match_id: str, match_root: Path) -> None:
        """Pin ``match_id`` to ``match_root``.

        Idempotent: re-registering the same id with a different root
        overwrites (the user moved the folder). Re-registering with the
        same root is a no-op.
        """
        resolved = Path(match_root).resolve()
        with self._lock:
            self._by_id[match_id] = resolved

    def forget(self, match_id: str) -> None:
        """Drop ``match_id`` from the registry. No-op if absent."""
        with self._lock:
            self._by_id.pop(match_id, None)

    def clear(self) -> None:
        """Empty the cache. Test-only helper."""
        with self._lock:
            self._by_id.clear()

    def refresh_from_recent_projects(self) -> int:
        """Re-scan :func:`user_config.get_recent_projects` and re-pin every
        match folder. Returns the number of entries that were registered.

        Cheap on cold start (one ``match.json`` read per entry); the
        recent-projects list is bounded so this stays under a few dozen.
        Skips entries that no longer exist on disk and entries marked
        ``kind="legacy"`` (legacy single-shooter projects have no
        ``match_id``).
        """
        registered = 0
        for entry in user_config.get_recent_projects():
            if entry.kind == "legacy":
                continue
            root = Path(entry.path)
            if not root.exists():
                continue
            try:
                match = Match.load(root)
            except (FileNotFoundError, ValueError) as exc:
                logger.debug("skipping recent project %s: %s", root, exc)
                continue
            if match.match_id:
                self.register(match.match_id, root)
                registered += 1
        return registered

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def resolve(self, match_id: str) -> Path:
        """Return the on-disk match root for ``match_id``.

        Falls back to a one-shot rescan of recent projects on a miss so
        a freshly-created match doesn't require a server restart.
        Raises :class:`MatchNotRegisteredError` if the id still doesn't
        resolve afterwards.
        """
        with self._lock:
            hit = self._by_id.get(match_id)
        if hit is not None:
            return hit
        self.refresh_from_recent_projects()
        with self._lock:
            hit = self._by_id.get(match_id)
        if hit is None:
            raise MatchNotRegisteredError(match_id)
        return hit

    def known_ids(self) -> list[str]:
        """Snapshot of currently-registered ids (for diagnostics)."""
        with self._lock:
            return sorted(self._by_id)
