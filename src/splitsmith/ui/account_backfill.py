"""Where the shooter book's one-time fill reads from (spec 2026-10-08): every
match the account already has, each shooter with an SSI id and a look set.

The fill itself is ``shooter_book.backfill`` (most recent match wins, ids the
book already holds are left alone, no match is written). This module only
lists candidates: from the match folders on this machine locally, and from the
account's own project docs and storage hosted. Neither imports ``server``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..identity import LOGO_DIR
from ..match_project import MatchProject
from ..shooter_book import BackfillCandidate, is_set

logger = logging.getLogger(__name__)


def _when(project: MatchProject) -> datetime:
    stamp = getattr(project, "updated_at", None)
    if isinstance(stamp, datetime):
        return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def _candidate(project: MatchProject, read_logo) -> BackfillCandidate | None:  # type: ignore[no-untyped-def]
    sid = project.selected_shooter_id
    if sid is None or not is_set(project.identity):
        return None
    return BackfillCandidate(
        shooter_id=sid,
        identity=project.identity,
        label=project.competitor_name or project.name,
        updated_at=_when(project),
        read_logo=read_logo,
    )


def local_candidates(roots: Sequence[Path]) -> list[BackfillCandidate]:
    """Every shooter project under the given match folders (or a legacy
    single-shooter project folder) on this disk."""
    out: list[BackfillCandidate] = []
    for root in roots:
        shooter_roots = (
            sorted(p for p in (root / "shooters").iterdir() if p.is_dir())
            if (root / "shooters").is_dir()
            else [root]
        )
        for shooter_root in shooter_roots:
            if not (shooter_root / "project.json").is_file():
                continue
            try:
                project = MatchProject.load(shooter_root)
            except Exception as exc:  # noqa: BLE001 -- one unreadable project must not stop the fill
                logger.info("shooter book fill: skipping %s (%s)", shooter_root, exc)
                continue
            logo = project.identity.logo

            def read(path: Path | None = (shooter_root / LOGO_DIR / logo) if logo else None) -> bytes | None:
                if path is None or path.is_symlink() or not path.is_file():
                    return None
                return path.read_bytes()

            candidate = _candidate(project, read)
            if candidate is not None:
                out.append(candidate)
    return out


async def local_backfill_source() -> list[BackfillCandidate]:
    """The matches this machine opened recently (``projects.json``)."""
    from .. import user_config

    roots = []
    for recent in user_config.get_recent_projects():
        path = Path(recent.path).expanduser()
        if path.is_dir():
            roots.append(path)
    return local_candidates(roots)


def hosted_backfill_source(matches_store: Any, project_state: Any, storage: Any):  # type: ignore[no-untyped-def]
    """A source reading the account's own matches: one listing, one batched
    docs query (``load_docs_for_matches``), each logo from the account's own
    storage only when a candidate wins."""

    async def source() -> list[BackfillCandidate]:
        if matches_store is None or project_state is None:
            return []
        rows = await matches_store.list()
        ids = [row.match_id for row in rows]
        docs = await project_state.load_docs_for_matches(ids)
        out: list[BackfillCandidate] = []
        for match_id, match_docs in docs.items():
            for slug, doc in match_docs.projects.items():
                try:
                    project = MatchProject.model_validate(doc)
                except Exception as exc:  # noqa: BLE001 -- one bad doc must not stop the fill
                    logger.info("shooter book fill: skipping %s/%s (%s)", match_id, slug, exc)
                    continue
                logo = project.identity.logo

                def read(
                    key: str | None = (
                        f"matches/{match_id}/shooters/{slug}/{LOGO_DIR}/{logo}" if logo else None
                    ),
                ) -> bytes | None:
                    if key is None or storage is None or not storage.exists(key):
                        return None
                    return storage.read_bytes(key)

                candidate = _candidate(project, read)
                if candidate is not None:
                    out.append(candidate)
        return out

    return source


__all__ = ["hosted_backfill_source", "local_backfill_source", "local_candidates"]
