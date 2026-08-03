"""Typer sub-app for ``splitsmith match ...``.

Today's commands:

- ``merge``: consolidate N legacy single-shooter projects into one
  redesign-era match folder. Inputs are validated for scoreboard /
  stage-definition consistency; conflicts abort rather than silently
  picking a side.

- ``info``: print a one-screen summary of a match (or legacy project)
  at a given path.

- ``trims``: write lossless per-stage trims for every shooter in a match
  from a beep and a stage time alone -- no shot detection. Feeds
  ``splitsmith compare export``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import camera_select, match_model, match_trims, user_config
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

# Skip reasons that are *not* outstanding work: ``already_exported`` is a
# re-run of a finished match, ``skipped`` is a deliberate user choice.
# Anything else ("this stage still needs a trim and doesn't have one")
# fails the run, so a fully re-run match exits 0 while a match that never
# got any trims (no_beep / no_stage_time / ...) exits 1. Typed against
# ``match_trims.SkipReason`` so a rename can't leave this matching on a
# string nothing produces any more (#614) -- the documented
# ``match trims && compare export`` chain hangs off this exit code.
SATISFIED_REASONS: tuple[match_trims.SkipReason, ...] = ("already_exported", "skipped")


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


@match_app.command("rename-shooter-slugs")
def rename_shooter_slugs(
    path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help=("Match folder (with match.json) whose shooter slugs should be " "replaced with opaque ids."),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the rename plan without touching disk.",
    ),
) -> None:
    """Rename every shooter under PATH from a human-readable slug to an
    opaque random id (``s_<hex>``).

    Use this once after upgrading to drop PII from on-disk paths and
    URLs. Renames the ``shooters/<old>/`` directory, rewrites
    ``match.json`` with the new slug, and refreshes the shooter list in
    place. Safe to re-run; shooters that already have an opaque slug
    (``s_*``) are skipped.
    """
    if not is_match_folder(path):
        console.print(
            f"[red]Not a match folder:[/] {path}\n" f"Expected {MATCH_FILE} alongside a shooters/ subdir."
        )
        raise typer.Exit(code=2)

    match = Match.load(path)
    shooters_dir = path / match_model.SHOOTERS_DIR
    plan: list[tuple[str, str]] = []  # (old_slug, new_slug)
    taken: set[str] = set()
    for old in match.shooters:
        if old.startswith("s_") and len(old) == 10:
            taken.add(old)
            continue
        new = match_model.mint_shooter_slug(taken)
        taken.add(new)
        plan.append((old, new))

    if not plan:
        console.print("[green]Nothing to rename.[/] Every shooter slug is already opaque.")
        return

    console.print(f"[bold cyan]Slug rename plan ({len(plan)} shooter(s))[/]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("From")
    table.add_column("To")
    for old, new in plan:
        table.add_row(old, new)
    console.print(table)

    if dry_run:
        console.print("[dim]Dry run -- no changes written.[/]")
        return

    # Apply: rename dirs first, then rewrite match.json. If any rename
    # fails halfway we leave the disk in a known-bad state; the user
    # can re-run after fixing permissions / collisions.
    new_shooters: list[str] = []
    rename_map = dict(plan)
    for old in match.shooters:
        new = rename_map.get(old, old)
        if new != old:
            src = shooters_dir / old
            dst = shooters_dir / new
            if dst.exists():
                console.print(f"[red]Refusing to rename {old} -> {new}: target dir exists[/]")
                raise typer.Exit(code=1)
            src.rename(dst)
        new_shooters.append(new)

    match.shooters = new_shooters
    match.save(path)
    console.print(f"[green]Renamed {len(plan)} shooter(s).[/]")


@match_app.command("trims")
def trims(
    match_path: Path = typer.Argument(
        ..., exists=True, readable=True, help="Match folder to produce trims for."
    ),
    shooter: list[str] = typer.Option([], "--shooter", help="Limit to these shooter slugs (repeatable)."),
    stage: list[int] = typer.Option([], "--stage", help="Limit to these stage numbers (repeatable)."),
    camera: list[str] = typer.Option(
        [],
        "--camera",
        help=(
            "Camera for one shooter as SLUG=VALUE (repeatable). VALUE is a "
            "camera mount ('chest') or a role ('primary', 'secondary'). "
            "Overrides the shooter's persisted compare_camera."
        ),
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan; write nothing."),
    force: bool = typer.Option(False, "--force", help="Re-cut trims that already exist."),
) -> None:
    """Write lossless per-stage trims for every shooter in a match.

    Needs only a confirmed beep and a stage time per stage -- no shot
    detection. Feeds ``splitsmith compare export``, which reads these trims
    to build the beep-aligned grid.
    """
    if not is_match_folder(match_path):
        console.print(
            f"[red]Error:[/] {match_path} is not a match folder (no {MATCH_FILE}). "
            "Pass a merged match folder."
        )
        raise typer.Exit(code=2)

    try:
        cameras = camera_select.parse_camera_overrides(camera)
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=2) from exc

    match = Match.load(match_path)
    unknown = sorted(set(cameras) - set(match.shooters))
    if unknown:
        console.print(
            f"[red]Error:[/] --camera names no shooter on this match: {', '.join(unknown)}. "
            f"Slugs available: {', '.join(match.shooters)}"
        )
        raise typer.Exit(code=2)

    try:
        plan = match_trims.plan_trims(
            match_path,
            shooters=shooter or None,
            stages=stage or None,
            cameras=cameras,
            force=force,
        )
    except camera_select.CameraResolutionError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=2) from exc

    if dry_run:
        console.print(_render_trims_table(plan))
        console.print("[dim]Dry run -- no trims written.[/]")
        return

    results = match_trims.run_trims(
        match_path,
        plan,
        progress=lambda e: console.print(f"[dim]exporting {e.shooter_slug} stage {e.stage_number}...[/]"),
    )
    console.print(_render_trims_table(plan, results))

    written = sum(1 for r in results if r.trim_path is not None)
    skipped = len(results) - written
    substitutions = sum(1 for e in plan if e.substituted_from is not None)
    console.print(f"\n[bold]{written}[/] trims written, {skipped} skipped, {substitutions} substitutions")

    outstanding = [r for r in results if r.trim_path is None and r.entry.reason not in SATISFIED_REASONS]
    if written == 0 and outstanding:
        raise typer.Exit(code=1)


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


def _camera_cell(entry: match_trims.TrimPlanEntry) -> str:
    """Render the Camera column; a substitution shows ``requested -> primary``."""
    if entry.substituted_from:
        return f"{entry.substituted_from} -> primary"
    return entry.camera or "primary"


def _status_cell(entry: match_trims.TrimPlanEntry, result: match_trims.TrimResult | None) -> str:
    """Render the Status column.

    Before a run (or under ``--dry-run``) this is the plan's classification.
    Once ``run_trims`` has actually run, it is what happened -- a write, or
    the reason(s) it didn't happen, which may differ from the plan (e.g. the
    beep vanished between planning and running).
    """
    if result is not None:
        if result.trim_path is not None:
            return "[green]written[/]"
        if result.skip_reasons:
            return "; ".join(result.skip_reasons)
    if entry.eligible:
        return "eligible"
    # ``match_trims`` reasons are already human-readable, so they render
    # as-is; a new reason added there can never blank out this cell.
    return entry.reason or "ineligible"


def _render_trims_table(
    plan: list[match_trims.TrimPlanEntry],
    results: list[match_trims.TrimResult] | None = None,
) -> Table:
    """Build the Shooter / Stage / Camera / Status table for ``match trims``.

    ``results`` is ``None`` for ``--dry-run`` (nothing ran yet); otherwise it
    is ``run_trims``'s output, one-to-one and in order with ``plan``.
    """
    table = Table(show_header=True, header_style="bold")
    table.add_column("Shooter")
    table.add_column("Stage")
    table.add_column("Camera")
    table.add_column("Status")
    rows = zip(plan, results, strict=True) if results is not None else ((entry, None) for entry in plan)
    for entry, result in rows:
        table.add_row(
            entry.shooter_slug,
            f"{entry.stage_number} -- {entry.stage_name}",
            _camera_cell(entry),
            _status_cell(entry, result),
        )
    return table


__all__ = ["match_app"]
