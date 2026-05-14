"""Typer sub-app for ``splitsmith match ...``.

Today's commands:

- ``merge``: consolidate N legacy single-shooter projects into one
  redesign-era match folder. Inputs are validated for scoreboard /
  stage-definition consistency; conflicts abort rather than silently
  picking a side.

- ``info``: print a one-screen summary of a match (or legacy project)
  at a given path.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import match_model, user_config
from .match_model import (
    MATCH_FILE,
    Match,
    MergeConflictError,
    execute_merge,
    is_legacy_project_folder,
    is_match_folder,
    plan_merge,
)

logger = logging.getLogger(__name__)
match_app = typer.Typer(
    name="match",
    help="Match-as-object operations: merge legacy single-shooter projects, inspect matches.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@match_app.command("merge")
def merge(
    inputs: list[Path] = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Two or more legacy single-shooter project folders to merge.",
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help=(
            "Path for the new merged match folder. Must not exist (or must "
            "not already contain match.json)."
        ),
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        help=(
            "Match name for the merged folder. Required when inputs disagree on "
            "MatchProject.name; otherwise defaults to the shared name."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Plan and print the merge without touching the filesystem.",
    ),
    move: bool = typer.Option(
        False,
        "--move",
        help=(
            "Move source directories into the new match (default: copy). Use with "
            "care -- after a successful --move the originals are gone."
        ),
    ),
    register: bool = typer.Option(
        True,
        "--register/--no-register",
        help="Add the new match to ~/.splitsmith/projects.json so the picker sees it.",
    ),
) -> None:
    """Merge legacy single-shooter projects into a single match folder.

    Validates that all INPUTS share the same scoreboard match id (or name
    when the inputs predate the scoreboard linkage). Stage definitions
    across inputs are reconciled: if two inputs have the same stage with
    different names or rounds, the merge aborts with a conflict report
    instead of silently choosing a winner.

    The default is non-destructive: source projects are copied into the
    new match. Pass ``--move`` to relocate them instead. ``--dry-run``
    inspects everything and prints the plan without writing anything.
    """
    if len(inputs) < 2:
        console.print(
            "[yellow]Warning:[/] merging fewer than 2 inputs creates a single-shooter "
            "match -- which is equivalent to the legacy layout. Continuing anyway."
        )

    try:
        plan = plan_merge(inputs, output, name=name)
    except MergeConflictError as exc:
        console.print(f"[red]Conflict:[/] {exc}")
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=2) from exc

    _render_plan(plan, dry_run=dry_run, move=move)

    if dry_run:
        return

    try:
        match = execute_merge(plan, move=move)
    except FileExistsError as exc:
        console.print(f"[red]Refused:[/] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"\n[green]Merged[/] {len(match.shooters)} shooter(s) into " f"[bold]{plan.output_root}[/]."
    )

    if register:
        user_config.record_project_open(plan.output_root, match.name, kind="match")
        console.print(f"[dim]Registered as a recent project in {user_config.user_config_dir()}.[/]")


@match_app.command("info")
def info(
    path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Match folder (with match.json) or legacy single-shooter project.",
    ),
) -> None:
    """Print a one-screen summary of the match at PATH.

    Works for both layouts: a redesign-era match folder OR a legacy
    single-shooter project (rendered as a one-shooter view).
    """
    if is_match_folder(path):
        match = Match.load(path)
        shooters = []
        for slug in match.shooters:
            try:
                shooters.append((slug, match.load_shooter(path, slug)))
            except FileNotFoundError:
                shooters.append((slug, None))
        kind = "match"
    elif is_legacy_project_folder(path):
        match, shooter = match_model.legacy_to_match_view(match_model.MatchProject.load(path))
        shooters = [(shooter.slug, shooter)]
        kind = "legacy"
    else:
        console.print(
            f"[red]Not a splitsmith project or match:[/] {path}\n"
            f"Expected {MATCH_FILE} or project.json in the directory."
        )
        raise typer.Exit(code=2)

    console.print(f"[bold]{match.name}[/]  [dim]({kind})[/]")
    if match.scoreboard_match_id:
        console.print(
            f"  scoreboard match id: {match.scoreboard_match_id} "
            f"(content_type={match.scoreboard_content_type})"
        )
    if match.match_date:
        console.print(f"  match date: {match.match_date.isoformat()}")
    console.print(f"  stages: {len(match.stages)}")
    console.print(f"  shooters: {len(shooters)}")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Slug")
    table.add_column("Name")
    table.add_column("Stages")
    table.add_column("Videos")
    for slug, sh in shooters:
        if sh is None:
            table.add_row(slug, "[red]missing shooter.json[/]", "-", "-")
            continue
        n_stages = sum(1 for s in sh.stages if s.videos)
        n_videos = sum(len(s.videos) for s in sh.stages)
        table.add_row(slug, sh.name, str(n_stages), str(n_videos))
    console.print(table)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_plan(plan: match_model.MergePlan, *, dry_run: bool, move: bool) -> None:
    """Print a human-readable summary of a planned merge."""
    heading = "[bold cyan]Merge plan (dry run)[/]" if dry_run else "[bold cyan]Merge plan[/]"
    console.print(heading)
    console.print(f"  match name:     [bold]{plan.name}[/]")
    console.print(f"  output:         {plan.output_root}")
    if plan.scoreboard_match_id:
        console.print(
            f"  scoreboard:     id={plan.scoreboard_match_id} "
            f"(content_type={plan.scoreboard_content_type})"
        )
    if plan.match_date:
        console.print(f"  match date:     {plan.match_date.isoformat()}")
    console.print(f"  stages:         {len(plan.stages)}")
    console.print(f"  mode:           {'move' if move else 'copy'}")
    console.print()
    table = Table(show_header=True, header_style="bold")
    table.add_column("Slug")
    table.add_column("Shooter")
    table.add_column("Source")
    table.add_column("-> Destination")
    for mv in plan.shooter_moves:
        table.add_row(
            mv.slug,
            mv.competitor_name,
            str(mv.source_root),
            str(mv.destination_root),
        )
    console.print(table)


__all__ = ["match_app"]
