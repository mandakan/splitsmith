"""Per-request storage presence index: one listing per prefix, not a HEAD per key (#1180).

``GET /api/match/shooters`` decides, for every angle of every stage,
whether its source exists and whether its audit trim is cached
(:func:`splitsmith.ui.server._audit_trim_targets`). Those are metadata
questions by design (#637: this route must never fetch bytes), but in
hosted mode each one was a ``storage.exists`` HEAD to R2, two per angle,
in sequence -- 11 s to open a 20-stage match.

:class:`StoragePresence` answers the same questions from a set built by
``storage.list(prefix)`` the first time a key under that prefix is asked
for. Raw uploads live under the tenant's ``raw/`` prefix (one listing per
request, shared across shooters); audit trims under
``<scope>/trimmed/`` (one listing per shooter). Local-disk-first semantics
are unchanged: a key that exists under the project root never reaches
storage, exactly as :meth:`MatchProject.source_present` and
:func:`splitsmith.ui.audio.trim_available` behave. A key under no indexed
prefix falls back to a single HEAD, so an unusual path keeps today's
answer rather than a wrong one.

A listing that raises is remembered for the request: every later lookup
under that prefix re-raises, so the caller's existing
``source_unreachable`` skip fires for each angle instead of one HEAD per
angle failing in turn. In local mode (no storage bound) the index is
inert and delegates to the project's own local checks.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..match_project import MatchProject
from ..storage import Storage
from . import audio as audio_helpers

logger = logging.getLogger(__name__)

RAW_PREFIX = "raw/"
TRIMMED_SEGMENT = "/trimmed/"


def _covering_prefix(key: str) -> str | None:
    """The listing prefix that answers ``key``, or ``None`` for a HEAD fallback."""
    if key.startswith(RAW_PREFIX):
        return RAW_PREFIX
    idx = key.find(TRIMMED_SEGMENT)
    if idx != -1:
        return key[: idx + len(TRIMMED_SEGMENT)]
    return None


class StoragePresence:
    """Existence answers for one request, from at most one listing per prefix."""

    def __init__(self, storage: Storage | None) -> None:
        self._storage = storage
        # prefix -> the keys under it, or the exception its listing raised.
        self._listed: dict[str, set[str] | BaseException] = {}

    def has_key(self, key: str) -> bool:
        """Whether ``key`` exists in storage; raises if its prefix could not be listed."""
        if self._storage is None:
            return False
        prefix = _covering_prefix(key)
        if prefix is None:
            return self._storage.exists(key)
        listed = self._listed.get(prefix)
        if listed is None:
            try:
                listed = {obj.path for obj in self._storage.list(prefix)}
            except Exception as exc:  # noqa: BLE001 -- remembered and re-raised per lookup
                listed = exc
            self._listed[prefix] = listed
        if isinstance(listed, BaseException):
            raise listed
        return key in listed

    def source_present(self, project: MatchProject, root: Path, video_path: Path) -> bool:
        """:meth:`MatchProject.source_present` answered from the index.

        Absolute and confined paths, and projects with no bound storage,
        are the project's own business and go straight to it.
        """
        if project._storage is None or video_path.is_absolute() or ".." in video_path.parts:
            return project.source_present(root, video_path)
        if (root / video_path).exists():
            return True
        return self.has_key(str(video_path))

    def trim_available(self, project: MatchProject, local_mp4: Path) -> bool:
        """:func:`splitsmith.ui.audio.trim_available` answered from the index."""
        if local_mp4.exists() and local_mp4.stat().st_size > 0:
            return True
        key = audio_helpers._storage_trim_key(project, local_mp4)
        if key is None:
            return False
        try:
            return self.has_key(key)
        except Exception as exc:  # noqa: BLE001 -- same contract as trim_available
            logger.info("trim cache: listing for %s raised %s", key, exc)
            return False
