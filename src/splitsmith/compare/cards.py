"""The generated-card knobs for a grid render, shared by the CLI and the
HTTP endpoint (issue #973).

Lives beside ``mp4_grid`` rather than inside ``compare/cli.py`` so the
server never imports a Typer module to build a title card.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..composition import MatchTitle
from ..division import competitor_division, roster_lines
from ..match_model import Match
from ..match_project import MatchProject
from .mp4_grid import StageTitleKind
from .project_loader import CompareShooterBundle


@dataclass(frozen=True)
class CardOptions:
    """The generated-card flags, carried together so a caller passes one
    argument rather than six."""

    stage_titles: StageTitleKind = "none"
    title_duration_seconds: float = 1.5
    title_page: bool = False
    title_info: str | None = None
    #: One "Name · Division" line per shooter under the date.
    title_division: bool = True
    title_page_duration_seconds: float = 3.0
    closing_card: bool = False


def match_title(match: Match, *, extra: str | None = None, roster: tuple[str, ...] = ()) -> MatchTitle:
    """The grid's match title card: the match name over its date (when
    known), the ``roster`` lines (:func:`splitsmith.division.roster_lines`)
    and the caller's free-text line. Only what the match carries; a blank
    line is never printed."""
    info: list[str] = []
    if match.match_date is not None:
        info.append(match.match_date.isoformat())
    info.extend(roster)
    if extra and extra.strip():
        info.append(extra.strip())
    return MatchTitle(text=match.name, info=tuple(info))


def title_cards(
    match: Match, cards: CardOptions, *, divisions: Sequence[tuple[str, str | None]] = ()
) -> tuple[MatchTitle | None, MatchTitle | None]:
    """``(title_page, closing)`` for ``render_grid_mp4``: the same text on
    both, each held for ``title_page_duration_seconds``; ``None`` where
    the option is off. The closing card repeats the title page -- it is
    the data there is. ``divisions`` is ``(label, division)`` per tile in
    slot order; it reaches the card only with ``title_division`` on."""
    if not (cards.title_page or cards.closing_card):
        return None, None
    roster = roster_lines(divisions) if cards.title_division else ()
    card = match_title(match, extra=cards.title_info, roster=roster)
    card = MatchTitle(text=card.text, info=card.info, duration_seconds=cards.title_page_duration_seconds)
    return (card if cards.title_page else None), (card if cards.closing_card else None)


def bundle_divisions(bundles: Sequence[CompareShooterBundle]) -> list[tuple[str, str | None]]:
    """``(label, division)`` per shooter in the grid's slot order
    (alphabetical by label, as ``mp4_grid`` lays the tiles out). A
    shooter whose project cannot be read has no division, never an
    error: a title line is not worth a failed render."""
    out: list[tuple[str, str | None]] = []
    for bundle in sorted(bundles, key=lambda b: b.label):
        project = bundle.project
        if project is None:
            try:
                project = MatchProject.load(bundle.project_root)
            except (OSError, ValueError):
                out.append((bundle.label, None))
                continue
        out.append((bundle.label, competitor_division(project, bundle.project_root)))
    return out


__all__ = ["CardOptions", "bundle_divisions", "match_title", "title_cards"]
