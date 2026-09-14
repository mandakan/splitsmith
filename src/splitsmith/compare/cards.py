"""The generated-card knobs for a grid render, shared by the CLI and the
HTTP endpoint (issue #973).

Lives beside ``mp4_grid`` rather than inside ``compare/cli.py`` so the
server never imports a Typer module to build a title card.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..composition import MatchTitle
from ..match_model import Match
from .mp4_grid import StageTitleKind


@dataclass(frozen=True)
class CardOptions:
    """The generated-card flags, carried together so a caller passes one
    argument rather than six."""

    stage_titles: StageTitleKind = "none"
    title_duration_seconds: float = 1.5
    title_page: bool = False
    title_info: str | None = None
    title_page_duration_seconds: float = 3.0
    closing_card: bool = False


def match_title(match: Match, *, extra: str | None = None) -> MatchTitle:
    """The grid's match title card: the match name over its date (when
    known) and the caller's free-text line. Only what the match carries;
    a blank line is never printed."""
    info: list[str] = []
    if match.match_date is not None:
        info.append(match.match_date.isoformat())
    if extra and extra.strip():
        info.append(extra.strip())
    return MatchTitle(text=match.name, info=tuple(info))


def title_cards(match: Match, cards: CardOptions) -> tuple[MatchTitle | None, MatchTitle | None]:
    """``(title_page, closing)`` for ``render_grid_mp4``: the same text on
    both, each held for ``title_page_duration_seconds``; ``None`` where
    the option is off. The closing card repeats the title page -- it is
    the data there is."""
    if not (cards.title_page or cards.closing_card):
        return None, None
    card = match_title(match, extra=cards.title_info)
    card = MatchTitle(text=card.text, info=card.info, duration_seconds=cards.title_page_duration_seconds)
    return (card if cards.title_page else None), (card if cards.closing_card else None)


__all__ = ["CardOptions", "match_title", "title_cards"]
