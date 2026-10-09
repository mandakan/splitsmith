"""The Shooters page's roster (spec 2026-10-09): everyone you have filmed.

Every shooter project in the account's matches, one row per SSI shooter id
(the shooter book's key) and, for a shooter without one, one row per match.
A row shows the look the videos draw: the book's entry when it sets
anything (the book wins), else the newest match's own record. Listing
writes nothing. Locally the matches are the ones this machine opened
recently (``projects.json``), as the book's one-time fill reads them;
hosted they are the account's own matches. Neither imports ``server``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..identity import ShooterIdentity
from ..match_project import MatchProject
from ..shooter_book import BookSnapshot, is_set

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RosterSeen:
    """One shooter project, as the roster reads it."""

    shooter_id: int | None
    name: str
    match_id: str | None
    match_name: str
    slug: str
    updated_at: datetime
    identity: ShooterIdentity


@dataclass(frozen=True)
class RosterRow:
    shooter_id: int | None
    name: str
    club: str | None
    accent: str | None
    logo_url: str | None
    match_count: int
    last_match_at: datetime
    last_match_name: str
    you: bool
    #: Where the look comes from: ``book``, ``match`` or ``none``.
    source: str
    #: For a shooter without an SSI id: the one match the row stands for.
    match_id: str | None = None
    slug: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "shooter_id": self.shooter_id,
            "name": self.name,
            "club": self.club,
            "accent": self.accent,
            "logo_url": self.logo_url,
            "match_count": self.match_count,
            "last_match_at": self.last_match_at.isoformat(),
            "last_match_name": self.last_match_name,
            "you": self.you,
            "source": self.source,
            "match_id": self.match_id,
            "slug": self.slug,
        }


def _when(project: MatchProject) -> datetime:
    stamp = getattr(project, "updated_at", None)
    if isinstance(stamp, datetime):
        return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def seen_from_project(
    project: MatchProject, *, match_id: str | None, match_name: str, slug: str
) -> RosterSeen:
    return RosterSeen(
        shooter_id=project.selected_shooter_id,
        name=project.competitor_name or project.name,
        match_id=match_id,
        match_name=match_name,
        slug=slug,
        updated_at=_when(project),
        identity=project.identity,
    )


def _match_logo_url(seen: RosterSeen) -> str | None:
    if not seen.identity.logo or seen.match_id is None:
        return None
    return (
        f"/api/matches/{quote(seen.match_id)}/shooters/{quote(seen.slug)}/identity/logo"
        f"?v={quote(seen.identity.logo)}"
    )


def _book_logo_url(shooter_id: int, look: ShooterIdentity) -> str | None:
    return f"/api/me/shooter-book/{shooter_id}/logo?v={quote(look.logo)}" if look.logo else None


def build_roster(seen: Iterable[RosterSeen], book: BookSnapshot, you_id: int | None) -> list[RosterRow]:
    """The rows, pure: one per SSI id (the newest match's name, every match
    counted, the book's look when set else the newest match's own), one per
    match for a shooter without an id. You first, then the newest match,
    then by name."""
    by_id: dict[int, list[RosterSeen]] = {}
    rows: list[RosterRow] = []
    for item in seen:
        if item.shooter_id is None:
            look = item.identity
            rows.append(
                RosterRow(
                    shooter_id=None,
                    name=item.name,
                    club=look.club,
                    accent=look.accent,
                    logo_url=_match_logo_url(item),
                    match_count=1,
                    last_match_at=item.updated_at,
                    last_match_name=item.match_name,
                    you=False,
                    source="match" if is_set(look) else "none",
                    match_id=item.match_id,
                    slug=item.slug,
                )
            )
        else:
            by_id.setdefault(item.shooter_id, []).append(item)
    for shooter_id, items in by_id.items():
        items.sort(key=lambda s: s.updated_at, reverse=True)
        newest = items[0]
        entry = book.get(shooter_id)
        if is_set(entry):
            assert entry is not None
            look, source, logo_url = entry, "book", _book_logo_url(shooter_id, entry)
        else:
            own = next((s for s in items if is_set(s.identity)), None)
            look = own.identity if own is not None else ShooterIdentity()
            source = "match" if own is not None else "none"
            logo_url = _match_logo_url(own) if own is not None else None
        rows.append(
            RosterRow(
                shooter_id=shooter_id,
                name=newest.name,
                club=look.club,
                accent=look.accent,
                logo_url=logo_url,
                match_count=len(items),
                last_match_at=newest.updated_at,
                last_match_name=newest.match_name,
                you=you_id is not None and shooter_id == you_id,
                source=source,
            )
        )
    rows.sort(key=lambda r: (not r.you, -r.last_match_at.timestamp(), r.name.lower()))
    return rows


def local_seen(roots: Sequence[Path]) -> list[RosterSeen]:
    """Every shooter project under the given match folders on this disk."""
    from ..match_model import load_match_or_legacy

    out: list[RosterSeen] = []
    for root in roots:
        try:
            match, shooter_roots = load_match_or_legacy(root)
        except Exception as exc:  # noqa: BLE001 -- one unreadable match must not hide the rest
            logger.info("shooter roster: skipping %s (%s)", root, exc)
            continue
        for slug, shooter_root in sorted(shooter_roots.items()):
            try:
                project = MatchProject.load(shooter_root)
            except Exception as exc:  # noqa: BLE001
                logger.info("shooter roster: skipping %s (%s)", shooter_root, exc)
                continue
            out.append(seen_from_project(project, match_id=match.match_id, match_name=match.name, slug=slug))
    return out


def local_roots() -> list[Path]:
    """The matches this machine opened recently (``projects.json``)."""
    from .. import user_config

    roots = []
    for recent in user_config.get_recent_projects():
        path = Path(recent.path).expanduser()
        if path.is_dir() and path not in roots:
            roots.append(path)
    return roots


async def hosted_seen(matches_store: Any, project_state: Any) -> list[RosterSeen]:
    """Every shooter project of the account's matches: one listing and one
    batched docs query (``load_docs_for_matches``)."""
    if matches_store is None or project_state is None:
        return []
    rows = await matches_store.list()
    names = {row.match_id: row.name for row in rows}
    docs = await project_state.load_docs_for_matches(list(names))
    out: list[RosterSeen] = []
    for match_id, match_docs in docs.items():
        for slug, doc in match_docs.projects.items():
            try:
                project = MatchProject.model_validate(doc)
            except Exception as exc:  # noqa: BLE001 -- one bad doc must not hide the rest
                logger.info("shooter roster: skipping %s/%s (%s)", match_id, slug, exc)
                continue
            out.append(
                seen_from_project(project, match_id=match_id, match_name=names.get(match_id, ""), slug=slug)
            )
    return out


__all__ = [
    "RosterRow",
    "RosterSeen",
    "build_roster",
    "hosted_seen",
    "local_roots",
    "local_seen",
    "seen_from_project",
]
