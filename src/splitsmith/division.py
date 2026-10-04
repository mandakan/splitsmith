"""The competitor's division for a generated title page.

SSI spells the power factor into the division name wherever a division
allows both ("Classic Major", "Open Minor", "Standard Minor"); a
minor-only division carries no suffix ("Production Optics"). So the
string as the scoreboard gives it is what a title page prints, and
nothing here parses it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .match_project import MatchProject


def competitor_division(project: MatchProject, project_dir: Path | None) -> str | None:
    """The pinned competitor's division as the scoreboard spells it
    ("Classic Major"): the stored value, else the project's own
    scoreboard files -- a dropped ``match.json`` or the cached match --
    for a project pinned before the division was stored. Never the
    network: an export must not wait on SSI for a title line."""
    if project.competitor_division:
        return project.competitor_division
    if project_dir is None or project.selected_competitor_id is None or not project.scoreboard_match_id:
        return None
    from .ui.scoreboard.cache import read_cached_match
    from .ui.scoreboard.local import LocalJsonScoreboard

    try:
        match_id = int(project.scoreboard_match_id)
    except ValueError:
        return None
    try:
        local = LocalJsonScoreboard.from_project(project_dir)
        match_data = local.get_match(project.scoreboard_content_type or 0, match_id)
    except Exception:  # noqa: BLE001 -- no dropped file, an unreadable one, or one for another match
        match_data = None
    if match_data is None and project.scoreboard_content_type is not None:
        match_data = read_cached_match(project_dir, project.scoreboard_content_type, match_id)
    if match_data is None:
        return None
    probe = project.model_copy()
    probe.merge_competitor_division(match_data)
    return probe.competitor_division


def roster_lines(roster: Sequence[tuple[str, str | None]]) -> tuple[str, ...]:
    """One title-page line per shooter of a multi-shooter card, "Name ·
    Division", the name alone where no division is on record. Empty when
    nobody has one: a roster of bare names is not what the option asked
    for, and the grid's tiles already carry the names."""
    if not any(division for _label, division in roster):
        return ()
    return tuple(f"{label} · {division}" if division else label for label, division in roster)


__all__ = ["competitor_division", "roster_lines"]
