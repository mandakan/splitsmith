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

- ``export``: stitch one shooter's existing per-stage trims into a match
  video -- FCPXML, FCP7 XML or a rendered MP4 with generated title /
  stage cards (issue #973). Reads finished artefacts; never re-cuts.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import camera_select, match_model, match_trims, user_config
from .config import Config
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
            "Path for the new merged match folder. Must not exist (or must not already contain match.json)."
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

    console.print(f"\n[green]Merged[/] {len(match.shooters)} shooter(s) into [bold]{plan.output_root}[/].")

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
        help=("Match folder (with match.json) whose shooter slugs should be replaced with opaque ids."),
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
            f"[red]Not a match folder:[/] {path}\nExpected {MATCH_FILE} alongside a shooters/ subdir."
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
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        # Not "writes nothing": planning calls ``MatchProject.load``, which
        # rewrites project.json once on a schema-version bump. That is
        # unavoidable on any read of a stale project, but no media is
        # touched and no trim is written, which is what the flag promises.
        help="Print the plan; write no trims.",
    ),
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
    # Accept a slug or a display name, exactly as ``compare export`` does --
    # these two commands are documented as a chain, so a shooter spelled one
    # way in the first must be spellable the same way in the second (#618).
    resolved_cameras: dict[str, str] = {}
    unknown: list[str] = []
    for key, value in cameras.items():
        slug = match.resolve_shooter_slug(match_path, key)
        if slug is None:
            unknown.append(key)
        else:
            resolved_cameras[slug] = value
    if unknown:
        console.print(
            f"[red]Error:[/] --camera names no shooter on this match: {', '.join(sorted(unknown))}. "
            f"Slugs available: {', '.join(match.shooters)}"
        )
        raise typer.Exit(code=2)
    cameras = resolved_cameras

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
    # Count off the results, not the plan: ``entry.substituted_from`` is what
    # we *intended* to substitute, and a cam that appeared or vanished in
    # between makes the two disagree. The summary has to describe the run
    # that happened (#617).
    substitutions = sum(1 for r in results if r.substituted_from is not None)
    console.print(f"\n[bold]{written}[/] trims written, {skipped} skipped, {substitutions} substitutions")
    # Printed under the table rather than inside it: these are full
    # sentences, and a rich column would ellipsize them away (#617).
    for result in results:
        for note in result.notes:
            console.print(
                f"[yellow]note[/] {result.entry.shooter_slug} stage {result.entry.stage_number}: {note}"
            )

    outstanding = [r for r in results if r.trim_path is None and r.entry.reason not in SATISFIED_REASONS]
    if written == 0 and outstanding:
        raise typer.Exit(code=1)


_EXPORT_FORMATS = ("fcpxml", "fcp7xml", "mp4")
_TITLE_KINDS = ("none", "slate", "lower-third")


@match_app.command("export")
def export(
    match_path: Path = typer.Argument(..., exists=True, readable=True, help="Match folder."),
    shooter: str | None = typer.Option(
        None,
        "--shooter",
        help="Slug or display name of the shooter to export. Optional when the match has one shooter.",
    ),
    stage: list[int] = typer.Option([], "--stage", help="Limit to these stage numbers (repeatable)."),
    output_format: str = typer.Option(
        "fcpxml",
        "--format",
        help="'fcpxml' (Final Cut Pro), 'fcp7xml' (Premiere / Resolve) or 'mp4' (rendered by ffmpeg).",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Where to write. Defaults to the shooter's exports directory, named after the match.",
    ),
    head_pad: float = typer.Option(5.0, "--head-pad", help="Seconds of footage kept before each beep."),
    tail_pad: float = typer.Option(5.0, "--tail-pad", help="Seconds of footage kept after each last shot."),
    no_secondaries: bool = typer.Option(False, "--no-secondaries", help="Leave secondary cams out."),
    no_overlay: bool = typer.Option(False, "--no-overlay", help="Leave the rendered overlay out."),
    pip_layout: str = typer.Option("stacked", "--pip", help="'stacked' or 'pip-corners' for secondary cams."),
    titles: str = typer.Option("none", "--titles", help="Per-stage title: 'slate', 'lower-third' or 'none'."),
    title_duration: float = typer.Option(1.5, "--title-duration", help="Seconds a stage title shows for."),
    title_page: bool = typer.Option(
        False, "--title-page/--no-title-page", help="Open with a generated match title card (mp4 only)."
    ),
    title_info: str | None = typer.Option(
        None, "--title-info", help="Free-text line under the match name on the title page."
    ),
    title_page_duration: float = typer.Option(
        3.0, "--title-page-duration", help="Seconds the title page holds."
    ),
    closing_card: bool = typer.Option(
        False, "--closing-card", help="Close with a generated card (mp4 only)."
    ),
    summary_hold: float = typer.Option(
        0.0,
        "--summary-hold",
        help=(
            "Seconds to hold each stage's summary -- name, scoring, splits over the blurred last frame "
            "-- after its action (mp4 only). 0 is off."
        ),
    ),
    intro: Path | None = typer.Option(None, "--intro", help="Video clip to play before the first stage."),
    outro: Path | None = typer.Option(None, "--outro", help="Video clip to play after the last stage."),
    youtube_preset: bool = typer.Option(
        False, "--youtube-preset", help="YouTube's recommended H.264 encode."
    ),
    youtube_sidecar: bool = typer.Option(
        False,
        "--youtube-sidecar",
        help=(
            "Also write <output>-youtube.json (title, chaptered description, tags) and "
            "<output>.srt (per-shot captions) for the upload, and embed the chapters in "
            "the output (FCPXML markers, MP4 chapter atoms)."
        ),
    ),
    overlay_theme: str = typer.Option(
        "splitsmith", "--theme", help="Overlay / card theme: 'splitsmith' or 'clean'."
    ),
    config_path: Path | None = typer.Option(None, "--config", help="Optional YAML config."),
) -> None:
    """Stitch one shooter's finished per-stage exports into a match video.

    Reads the lossless trims, audits and overlay MOVs the per-stage export
    already wrote under the shooter's ``exports/`` and ``audit/``; a stage
    without a trim fails the run and names the stage. Nothing is re-cut
    -- run the per-stage export in the UI first.

    ``--format mp4`` renders the stitched match with ffmpeg and can add
    generated cards: ``--titles slate`` before each stage, ``--title-page``
    at the head, ``--closing-card`` at the tail (issue #973). The XML
    formats carry the stage titles FCP can draw and record the rest as
    notes.
    """
    from .match_project import MatchProject
    from .ui import match_exports

    if output_format not in _EXPORT_FORMATS:
        console.print(
            f"[red]Error:[/] --format must be one of {', '.join(_EXPORT_FORMATS)}, got {output_format!r}."
        )
        raise typer.Exit(code=2)
    if titles not in _TITLE_KINDS:
        console.print(f"[red]Error:[/] --titles must be one of {', '.join(_TITLE_KINDS)}, got {titles!r}.")
        raise typer.Exit(code=2)
    if pip_layout not in ("stacked", "pip-corners"):
        console.print(f"[red]Error:[/] --pip must be 'stacked' or 'pip-corners', got {pip_layout!r}.")
        raise typer.Exit(code=2)
    if overlay_theme not in ("splitsmith", "clean"):
        console.print(f"[red]Error:[/] --theme must be 'splitsmith' or 'clean', got {overlay_theme!r}.")
        raise typer.Exit(code=2)
    if not is_match_folder(match_path):
        console.print(f"[red]Error:[/] {match_path} is not a match folder (no {MATCH_FILE}).")
        raise typer.Exit(code=2)
    if summary_hold < 0:
        console.print(f"[red]Error:[/] --summary-hold must not be negative, got {summary_hold:g}.")
        raise typer.Exit(code=2)
    if output is not None and output.expanduser().is_dir():
        console.print(f"[red]Error:[/] --output {output} is a directory; pass the file to write.")
        raise typer.Exit(code=2)

    match = Match.load(match_path)
    if shooter is None:
        if len(match.shooters) != 1:
            console.print(
                f"[red]Error:[/] --shooter is required; this match has {len(match.shooters)} shooters: "
                f"{', '.join(match.shooters)}"
            )
            raise typer.Exit(code=2)
        slug: str | None = match.shooters[0]
    else:
        slug = match.resolve_shooter_slug(match_path, shooter)
    if slug is None:
        console.print(
            f"[red]Error:[/] --shooter {shooter!r} names no shooter on this match. "
            f"Slugs available: {', '.join(match.shooters)}"
        )
        raise typer.Exit(code=2)

    shooter_root = Match.shooter_root(match_path, slug)
    project = MatchProject.load(shooter_root)
    # The summary's identity row: the scoreboard's competitor name when
    # the project has one, else the match roster's display name. Only
    # read when a summary is asked for -- the roster file is not part of
    # an ordinary export and must not be able to fail one.
    shooter_label: str | None = project.competitor_name
    if summary_hold > 0 and not shooter_label:
        try:
            shooter_label = match.load_shooter(match_path, slug).name
        except (OSError, KeyError, ValueError):
            shooter_label = None  # export_match falls back to the match name
    stage_numbers = stage or [
        entry.stage_number
        for entry in project.stages
        if not entry.skipped and (primary := entry.primary()) is not None and primary.beep_time is not None
    ]
    if not stage_numbers:
        console.print("[red]Error:[/] no stage has a primary video with a confirmed beep.")
        raise typer.Exit(code=1)
    try:
        stages_input = match_exports.stage_inputs_for_project(project, shooter_root, stage_numbers)
    except (KeyError, ValueError) as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    project_name = project.name or match.name or "match"
    request = match_exports.MatchExportRequestData(
        stage_numbers=tuple(stage_numbers),
        head_pad_seconds=head_pad,
        tail_pad_seconds=tail_pad,
        include_secondaries=not no_secondaries,
        include_overlay=not no_overlay,
        project_name=project_name,
        pip_layout=pip_layout,  # type: ignore[arg-type]
        output_format=output_format,  # type: ignore[arg-type]
        title_kind=titles,  # type: ignore[arg-type]
        title_duration_seconds=title_duration,
        intro_path=intro.expanduser() if intro else None,
        outro_path=outro.expanduser() if outro else None,
        youtube_preset=youtube_preset,
        youtube_sidecar=youtube_sidecar,
        title_page=title_page,
        title_page_info=match_exports.title_info_lines(project, extra=title_info),
        title_page_duration_seconds=title_page_duration,
        closing_card=closing_card,
        overlay_theme=overlay_theme,  # type: ignore[arg-type]
        summary_hold_seconds=summary_hold,
        shooter_label=shooter_label,
    )
    exports_dir = project.exports_path(shooter_root)
    if output is not None:
        # ``export_match`` names the file itself; steer it by handing the
        # requested directory over and renaming afterwards, so the
        # naming rule stays in one place.
        target_dir = output.expanduser().resolve().parent
    else:
        target_dir = exports_dir
    console.print(
        f"[dim]exporting {project_name} ({slug}), {len(stage_numbers)} stages, {output_format}...[/]"
    )
    try:
        result = match_exports.export_match(
            stages=stages_input,
            request=request,
            exports_dir=target_dir,
            config=Config.load(config_path).output,
        )
    except match_exports.MatchExportError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    written = result.fcpxml_path
    if output is not None:
        wanted = output.expanduser().resolve()
        if wanted != written:
            # The sidecar and the captions sit beside the video under its
            # stem; they move with it or the upload kit is split in two.
            for companion, suffix in (
                (written.with_suffix(".srt"), ".srt"),
                (written.with_name(written.stem + "-youtube.json"), "-youtube.json"),
            ):
                if companion.exists():
                    companion.replace(wanted.with_name(wanted.stem + suffix))
            written.replace(wanted)
            written = wanted
    # ``soft_wrap``: a path or a note is one line the user copies or reads
    # whole; rich's hard wrap would break it mid-word (#617).
    console.print(f"[bold]Wrote[/] {written}", soft_wrap=True)
    console.print(f"  {result.stage_count} stages, {result.duration_seconds:.1f}s timeline")
    for note in result.anomalies:
        console.print(f"[yellow]note[/] {note}", soft_wrap=True)


@match_app.command("reclassify")
def reclassify(
    match_path: Path = typer.Argument(..., exists=True, readable=True, help="Match folder."),
    shooter: str | None = typer.Option(
        None, "--shooter", help="Slug or display name. Optional when the match has one shooter."
    ),
    stage: list[int] = typer.Option([], "--stage", help="Limit to these stage numbers (repeatable)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would move; write nothing."),
) -> None:
    """Re-run the Coach interval auto-classifier over a shooter's stored audits.

    For when the thresholds change (``CoachAutoClassifyConfig``, or a
    ``SPLITSMITH_CONFIG`` YAML): every auto-classified interval is
    re-judged against the current rule, manual overrides are kept, and
    the per-stage class counts before and after are printed so the audit
    trail shows what moved. Each rewritten audit records a
    ``coach_reclassify`` event, as the Coach view's own reclassify does.
    """
    import json
    import uuid
    from datetime import UTC, datetime

    from . import coach as coach_module
    from .match_project import MatchProject, atomic_write_json

    if not is_match_folder(match_path):
        console.print(f"[red]Error:[/] {match_path} is not a match folder (no {MATCH_FILE}).")
        raise typer.Exit(code=2)
    match = Match.load(match_path)
    if shooter is None:
        if len(match.shooters) != 1:
            console.print(
                f"[red]Error:[/] --shooter is required; this match has {len(match.shooters)} shooters: "
                f"{', '.join(match.shooters)}"
            )
            raise typer.Exit(code=2)
        slug: str | None = match.shooters[0]
    else:
        slug = match.resolve_shooter_slug(match_path, shooter)
    if slug is None:
        console.print(f"[red]Error:[/] --shooter {shooter!r} names no shooter on this match.")
        raise typer.Exit(code=2)

    shooter_root = Match.shooter_root(match_path, slug)
    project = MatchProject.load(shooter_root)
    audit_dir = project.audit_path(shooter_root)
    cfg = coach_module.auto_classify_config()
    console.print(
        f"[dim]split <= {cfg.split_max_s:g}s, transition <= {cfg.transition_max_s:g}s, "
        f"movement above{' (dry run)' if dry_run else ''}[/]"
    )
    wanted = set(stage) if stage else None
    moved_total = 0
    for entry in project.stages:
        if wanted is not None and entry.stage_number not in wanted:
            continue
        audit_file = audit_dir / f"stage{entry.stage_number}.json"
        if not audit_file.exists():
            continue
        doc = json.loads(audit_file.read_text(encoding="utf-8"))
        shots = doc.get("shots")
        if not isinstance(shots, list) or not shots:
            continue
        before = Counter(s.get("interval_class") or "unset" for s in shots if isinstance(s, dict))
        coach_module.classify_intervals_in_dicts([s for s in shots if isinstance(s, dict)], cfg)
        after = Counter(s.get("interval_class") or "unset" for s in shots if isinstance(s, dict))
        moved = sum((after - before).values())
        moved_total += moved
        label = f"stage {entry.stage_number} {entry.stage_name}"
        console.print(
            f"{label}: {_counts(before)} -> {_counts(after)}" + (f"  ({moved} moved)" if moved else ""),
            soft_wrap=True,
        )
        if dry_run or not moved:
            continue
        events = list(doc.get("audit_events") or [])
        events.append(
            {
                "id": uuid.uuid4().hex,
                "ts": datetime.now(UTC).isoformat(),
                "kind": "coach_reclassify",
                "payload": {"shot_count": len(shots), "source": "cli"},
            }
        )
        doc["audit_events"] = events
        atomic_write_json(audit_file, doc)
    console.print(f"[bold]{moved_total}[/] intervals moved{' (nothing written)' if dry_run else ''}")


def _counts(counter: Counter[str]) -> str:
    order = ("first_shot", "split", "transition", "movement", "reload", "activation", "unset")
    return " ".join(f"{k}={counter[k]}" for k in order if counter.get(k))


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
            f"  scoreboard:     id={plan.scoreboard_match_id} (content_type={plan.scoreboard_content_type})"
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


def _camera_cell(entry: match_trims.TrimPlanEntry, result: match_trims.TrimResult | None) -> str:
    """Render the Camera column; a substitution shows ``requested -> primary``.

    After a run this describes the run, not the plan: a cam that appeared
    between the two means the plan's ``substituted_from`` is stale, and a
    post-run table claiming ``chest -> primary`` for a row that actually
    shipped the chest angle is simply wrong (#617). ``--dry-run`` has no
    result, and an ineligible entry never ran, so both fall back to the
    plan -- its intent is all the information those rows have.
    """
    ran = result is not None and entry.eligible
    substituted_from = result.substituted_from if ran and result else entry.substituted_from
    if substituted_from:
        return f"{substituted_from} -> primary"
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
            # Notes ride along on a success -- a camera that changed between
            # plan and run means this row shipped a different angle than
            # --dry-run showed. Returning a bare "written" here is what kept
            # that datum off the screen entirely (#617). Flagged, not spelled
            # out: the full note goes under the table, where rich won't
            # ellipsize it into uselessness at 80 columns.
            if result.notes:
                return "[green]written[/] [yellow](see note)[/]"
            return "[green]written[/]"
        if result.skip_reasons:
            return "; ".join(result.skip_reasons)
        if result.notes:
            return "[yellow](see note)[/]"
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
            _camera_cell(entry, result),
            _status_cell(entry, result),
        )
    return table


__all__ = ["match_app"]
