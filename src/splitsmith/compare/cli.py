"""Typer sub-app for ``splitsmith compare ...``."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import typer
from rich.console import Console

from .. import camera_select
from ..export_naming import slugify
from ..match_model import Match, is_match_folder
from ..overlay_theme import THEME_NAMES, ThemeName
from . import emitter as emitter_mod
from . import manifest as manifest_mod
from . import mp4_grid, project_loader

compare_app = typer.Typer(
    name="compare",
    help="Multi-shooter comparison FCPXML.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@compare_app.command("export")
def export(
    source: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help=(
            "Either a comparison manifest YAML, OR the path to a merged match "
            "folder. When a match folder is passed, --audio-from is required."
        ),
    ),
    audio_from: str | None = typer.Option(
        None,
        "--audio-from",
        help=(
            "Slug or name of the reference shooter. With --format fcpxml theirs "
            "is the one unmuted tile; with --format mp4 every shooter is mixed "
            "into the default track and gets a named track of their own, so this "
            "only sets the render's frame rate, from their footage. Required when "
            "SOURCE is a match folder; overrides the manifest's audio_from key "
            "when SOURCE is a manifest."
        ),
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help=(
            "Where to write the FCPXML. Required when SOURCE is a match folder; "
            "overrides the manifest's output key when SOURCE is a manifest. A "
            "relative value resolves against the current directory."
        ),
    ),
    camera: list[str] = typer.Option(
        [],
        "--camera",
        help=(
            "Camera selector for one shooter, as SHOOTER=VALUE (repeatable). "
            "VALUE is a camera mount ('chest') or a role ('primary', "
            "'secondary'). SHOOTER is the match shooter's slug or display "
            "name -- the same spellings --audio-from takes -- or a manifest "
            "shooter's label. Overrides the manifest's camera: key and the "
            "shooter's persisted compare_camera."
        ),
    ),
    output_format: str = typer.Option(
        "fcpxml",
        "--format",
        help=(
            "Output kind: 'fcpxml' (Final Cut timeline, the default) or "
            "'mp4' (rendered grid video). MP4 requires SOURCE to be a "
            "merged match folder."
        ),
    ),
    overlay: bool = typer.Option(
        False,
        "--overlay/--no-overlay",
        help=(
            "Composite a splits overlay -- per-tile shot counter and last "
            "split, a bottom delta-strip ranking, and a running clock -- "
            "onto the rendered grid. --format mp4 only: the FCPXML grid "
            "ships clean tiles by decision, so --overlay with --format "
            "fcpxml is refused rather than a silent no-op."
        ),
    ),
    overlay_theme: str = typer.Option(
        "splitsmith",
        "--overlay-theme",
        help=f"Palette for --overlay. One of: {', '.join(THEME_NAMES)}.",
    ),
    summary_hold: float = typer.Option(
        0.0,
        "--summary-hold",
        help=(
            "Seconds to freeze on a stage summary at the end of every "
            "stage: each tile holds its last frame, blurred and dimmed, "
            "with that shooter's hit and fault counts, hit factor, stage "
            "time and splits over their own cell. Also turns the summary "
            "on during the action -- a shooter who finishes early shows "
            "theirs from that moment instead of a black cell. 0 (the "
            "default) is off. Requires --overlay, and is charged per "
            "stage -- a 3-second hold adds 36 seconds to a 12-stage "
            "match."
        ),
    ),
) -> None:
    """Render a multi-shooter comparison FCPXML.

    SOURCE accepts two shapes:

      1. A manifest YAML (legacy path): same behaviour as before --
         `splitsmith compare export examples/compare-foo.yaml`.

      2. A merged match folder (new in #320): every shooter under
         `<match>/shooters/` contributes a tile; --audio-from names the
         reference shooter and --output names the file.

    Precedence on the manifest path: a CLI flag beats the matching YAML
    key. The flag is typed now and the YAML was written earlier, so the
    flag is the more recent statement of intent -- overriding is the
    contract, not an anomaly worth warning about.
    """
    if output_format not in ("fcpxml", "mp4"):
        console.print(f"[red]Error:[/] --format must be 'fcpxml' or 'mp4', got {output_format!r}.")
        raise typer.Exit(code=2)
    if output_format == "mp4" and not (source.is_dir() and is_match_folder(source)):
        console.print(
            "[red]Error:[/] --format mp4 requires SOURCE to be a merged match folder, not a manifest."
        )
        raise typer.Exit(code=2)
    if overlay and output_format != "mp4":
        console.print(
            "[red]Error:[/] --overlay requires --format mp4 -- the FCPXML grid ships clean "
            "tiles by decision, so --overlay with --format fcpxml would silently do nothing."
        )
        raise typer.Exit(code=2)
    # Validated whether or not --overlay is on. A typo'd theme with no
    # --overlay used to be accepted in silence, so the next run -- the one
    # that adds --overlay and re-encodes the whole match -- is where the
    # user finds out. Rejecting a name that is never a valid theme costs
    # nothing and fails at the point the typo was made.
    if overlay_theme not in THEME_NAMES:
        console.print(
            f"[red]Error:[/] --overlay-theme must be one of {', '.join(THEME_NAMES)}, "
            f"got {overlay_theme!r}."
        )
        raise typer.Exit(code=2)
    if summary_hold < 0:
        console.print(f"[red]Error:[/] --summary-hold must not be negative, got {summary_hold:g}.")
        raise typer.Exit(code=2)
    # A hold with no overlay is a contradiction, not a no-op: the summary
    # is drawn from the overlay's own shot data in the overlay's own
    # typography, so it would come out a blurred still with nothing
    # written on it. Refused here rather than in the engine so the message
    # can name the flag the user would have to add.
    if summary_hold > 0 and not overlay:
        console.print(
            "[red]Error:[/] --summary-hold requires --overlay. The end-of-stage summary is "
            "drawn from the same shot data and in the same typography as the live overlay; "
            "without it the hold would freeze on a blurred still with nothing written on it."
        )
        raise typer.Exit(code=2)
    # Accepted, not refused -- someone cutting a highlight reel may
    # genuinely want to sit on the summary. But the hold is charged per
    # stage and the bill only arrives when a 40-minute render finishes,
    # so an unusual value is said out loud before it starts.
    if summary_hold > mp4_grid.SUMMARY_HOLD_WARN_SECONDS:
        console.print(
            f"[yellow]Warning:[/] --summary-hold={summary_hold:g}s is unusually long and is "
            "charged once per stage -- a 12-stage match would gain "
            f"{summary_hold * 12:g}s of frozen summaries. Rendering it anyway."
        )

    if source.is_dir() and is_match_folder(source):
        if audio_from is None:
            console.print("[red]Error:[/] --audio-from is required when SOURCE is a match folder.")
            raise typer.Exit(code=2)
        if output is None:
            console.print("[red]Error:[/] --output is required when SOURCE is a match folder.")
            raise typer.Exit(code=2)
        try:
            cameras = camera_select.parse_camera_overrides(camera)
        except ValueError as exc:
            console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(code=2) from exc
        _export_from_match(
            source,
            audio_from=audio_from,
            output=output,
            cameras=cameras,
            output_format=output_format,
            overlay=overlay,
            overlay_theme=overlay_theme,  # type: ignore[arg-type]  # validated above against THEME_NAMES
            summary_hold=summary_hold,
        )
        return

    if source.is_dir():
        console.print(
            f"[red]Error:[/] {source} is a directory but does not contain match.json. "
            "Pass a manifest YAML or a merged match folder."
        )
        raise typer.Exit(code=2)

    # Manifest path.
    manifest = manifest_mod.load_manifest(source)
    try:
        cameras = camera_select.parse_camera_overrides(camera)
    except ValueError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=2) from exc
    manifest, cameras_by_label = _apply_manifest_overrides(
        manifest, audio_from=audio_from, output=output, cameras=cameras
    )

    shooters: list[project_loader.CompareShooterBundle] = []
    for s in manifest.shooters:
        selected = cameras_by_label.get(s.label, s.camera)
        try:
            shooters.append(project_loader.load_shooter(s.project, s.label, camera=selected))
        except camera_select.CameraResolutionError as exc:
            console.print(f"[red]Error:[/] shooter {s.label!r}: {exc}")
            raise typer.Exit(code=2) from exc
    _warn_missing_trims(shooters)
    emitter_mod.emit_compare_fcpxml(
        manifest=manifest,
        shooters=shooters,
        output_path=manifest.output,
    )
    console.print(f"[green]Wrote[/] {manifest.output}")


def _warn_missing_trims(bundles: list[project_loader.CompareShooterBundle]) -> None:
    """Say out loud which stages will render as black filler, and why.

    A stage whose trim is not on disk is dropped from the grid. That is the
    right output -- the footage isn't there -- but it is indistinguishable
    from "this shooter didn't shoot the stage", and running
    ``--camera x=chest`` against a project whose chest trims were never
    exported produced a silently empty shooter (#618). Naming the path the
    loader looked for turns a mystery into a one-line fix: export that cam.

    A warning, not an error: a partial grid is a legitimate thing to want.
    """
    for bundle in bundles:
        for miss in bundle.missing_trims:
            asked = f" for camera {miss.camera!r}" if miss.camera else ""
            console.print(
                f"[yellow]Warning:[/] {bundle.label} stage {miss.stage_number} "
                f"({miss.stage_name}){asked}: no trim at {miss.expected_path} "
                "-- this stage renders as black filler."
            )


def _apply_manifest_overrides(
    manifest: manifest_mod.CompareManifest,
    *,
    audio_from: str | None,
    output: Path | None,
    cameras: dict[str, str],
) -> tuple[manifest_mod.CompareManifest, dict[str, str]]:
    """Fold the CLI flags into ``manifest``; flags win over YAML keys.

    Returns the overridden manifest plus the per-shooter camera overrides
    keyed by the manifest's own labels (``--camera`` names a shooter by
    slug, and a manifest shooter's slug is its slugified label).

    ``model_copy`` skips the model validators, so every override is
    checked here first -- an ``--audio-from`` naming nobody would
    otherwise reach the emitter as a bare ``ValueError``.
    """
    by_key = {slugify(s.label, fallback="shooter"): s.label for s in manifest.shooters}
    cameras_by_label: dict[str, str] = {}
    unknown: list[str] = []
    for key, value in cameras.items():
        label = by_key.get(slugify(key, fallback="shooter"))
        if label is None:
            unknown.append(key)
        else:
            cameras_by_label[label] = value
    if unknown:
        console.print(
            f"[red]Error:[/] --camera names no shooter in this manifest: "
            f"{', '.join(sorted(unknown))}. "
            f"Labels available: {', '.join(sorted(by_key.values()))}"
        )
        raise typer.Exit(code=2)

    updates: dict[str, object] = {}
    if audio_from is not None:
        labels = sorted(s.label for s in manifest.shooters)
        if audio_from not in labels:
            console.print(
                f"[red]Error:[/] --audio-from={audio_from!r} matches no shooter label "
                f"in this manifest. Labels available: {', '.join(labels)}"
            )
            raise typer.Exit(code=2)
        updates["audio_from"] = audio_from
    if output is not None:
        updates["output"] = _resolve_output_override(output)
    if updates:
        manifest = manifest.model_copy(update=updates)
    return manifest, cameras_by_label


def _resolve_output_override(output: Path) -> Path:
    """Anchor a ``--output`` flag to the current directory when relative.

    The manifest's own ``output`` stays anchored to the manifest's parent
    (``load_manifest``) so a YAML stays portable. A path typed at a prompt
    has no such anchor -- it should land where the user is standing.
    """
    expanded = output.expanduser()
    return expanded if expanded.is_absolute() else (Path.cwd() / expanded).resolve()


def _export_from_match(
    match_root: Path,
    *,
    audio_from: str,
    output: Path,
    cameras: dict[str, str] | None = None,
    output_format: str = "fcpxml",
    overlay: bool = False,
    overlay_theme: ThemeName = "splitsmith",
    summary_hold: float = 0.0,
) -> None:
    """Render the compare export directly from a merged Match."""
    match = Match.load(match_root)
    if not match.shooters:
        console.print(f"[red]Error:[/] match {match_root} has no shooters.")
        raise typer.Exit(code=2)

    # Resolve audio_from to a slug (accept slug exact match OR display-name slugify).
    resolved_audio_slug = _resolve_shooter_slug(match, match_root, audio_from)
    if resolved_audio_slug is None:
        slugs = ", ".join(match.shooters)
        console.print(
            f"[red]Error:[/] --audio-from={audio_from!r} matches no shooter on this match. "
            f"Slugs available: {slugs}"
        )
        raise typer.Exit(code=2)

    # A --camera key that names nobody would apply to nothing and look like
    # it worked, so it stops the run the same way a malformed pair does.
    # Keys go through the same resolver as --audio-from: requiring an exact
    # slug here while accepting a display name there made
    # ``--audio-from "Mathias Axell" --camera "Mathias Axell=chest"`` fail on
    # the second half of one command (#618).
    cameras = cameras or {}
    resolved_cameras: dict[str, str] = {}
    unknown: list[str] = []
    for key, value in cameras.items():
        slug = _resolve_shooter_slug(match, match_root, key)
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

    # Build the bundles. Each bundle's label is the shooter's display name
    # (Shooter.name), falling back to the slug. The audio_from in the synthesized
    # manifest must match one of these labels.
    bundles = []
    audio_label = ""
    for slug in match.shooters:
        shooter = match.load_shooter(match_root, slug)
        label = shooter.name or slug
        try:
            bundles.append(
                project_loader.load_shooter_from_match(match_root, slug, label, camera=cameras.get(slug))
            )
        except camera_select.CameraResolutionError as exc:
            console.print(f"[red]Error:[/] shooter {slug}: {exc}")
            raise typer.Exit(code=2) from exc
        if slug == resolved_audio_slug:
            audio_label = label

    _warn_missing_trims(bundles)

    if output_format == "mp4":
        _render_grid_mp4(
            bundles,
            audio_label=audio_label,
            output=output,
            overlay=overlay,
            overlay_theme=overlay_theme,
            summary_hold=summary_hold,
        )
        return

    # Synthesize a manifest the emitter can consume. ``layout_2up`` matches
    # today's manifest default; the smallest-fits grid kicks in at 3+ shooters
    # so the choice only matters when N=2.
    synthetic = manifest_mod.CompareManifest(
        output=output,
        audio_from=audio_label,
        layout_2up="horizontal",
        shooters=[manifest_mod.CompareShooter(project=match_root, label=b.label) for b in bundles],
    )
    emitter_mod.emit_compare_fcpxml(manifest=synthetic, shooters=bundles, output_path=output)
    console.print(f"[green]Wrote[/] {output}")


def _render_grid_mp4(
    bundles: list[project_loader.CompareShooterBundle],
    *,
    audio_label: str,
    output: Path,
    overlay: bool = False,
    overlay_theme: ThemeName = "splitsmith",
    summary_hold: float = 0.0,
) -> None:
    """Render the grid straight to MP4, owning the scratch work dir.

    ``render_grid_mp4`` leaves its per-stage segments on disk by design --
    they're what a failed stitch gets debugged from -- so a caller that
    wants them gone has to supply a directory it owns. On a 12-stage 4K
    match those segments are many gigabytes; a CLI run has no use for them
    once the concat succeeds (or fails), so this always removes them,
    success or not. The temp dir is created beside the output rather than
    under the system tmp dir for the same reason ``render_grid_mp4``'s own
    default lives beside the output: a match's worth of 4K segments should
    not have to fit on whatever filesystem backs /tmp.

    ``render_grid_mp4`` has no progress callback, so progress is reported
    by wrapping its ``runner`` hook (already part of its public signature,
    and already how its own tests inject a fake ffmpeg) rather than
    reaching into the engine to add one. ``build_stage_plans`` is called
    once up front -- pure planning, no ffmpeg -- purely to learn the stage
    count and names for the "N of M" messages; ``render_grid_mp4`` plans
    again internally with the same inputs and so sees the same stages.

    The engine decides what a feature-poor ffmpeg means for ``--overlay``
    (architecture rule 1: the CLI orchestrates, it does not own that);
    this only renders the answer. Twice, deliberately -- once through
    ``on_notice`` before the encode starts, and again as a clause on the
    "Wrote ..." line, because the last line on screen is the one the
    user actually reads after a 40-minute render.
    """
    # ``hold_seconds`` is passed even though this copy of the plans is
    # only counted, never rendered from: these plans and the engine's own
    # are built from identical inputs on purpose, and a duration on this
    # one that quietly meant "the action" while the engine's meant "the
    # segment" is the kind of divergence that shows up much later, in a
    # progress estimate or a duration report.
    plans = mp4_grid.build_stage_plans(
        bundles,
        audio_label=audio_label,
        head_pad_seconds=1.0,
        tail_pad_seconds=0.5,
        layout_2up="horizontal",
        hold_seconds=summary_hold,
    )
    total = len(plans)
    progress = {"calls": 0}

    def _reporting_runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        index = progress["calls"]
        progress["calls"] += 1
        if index < total:
            plan = plans[index]
            console.print(
                f"[cyan]Rendering[/] stage {plan.stage_number} ({plan.stage_name}) "
                f"-- {index + 1} of {total}..."
            )
        else:
            console.print(f"[cyan]Stitching[/] {total} stage(s) into {output}...")
        return subprocess.run(cmd, **kwargs)  # type: ignore[arg-type]

    def _notice(message: str) -> None:
        console.print(f"[yellow]Note:[/] {message}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".compare-grid-work-") as tmp:
        try:
            result = mp4_grid.render_grid_mp4(
                bundles,
                audio_label=audio_label,
                output_path=output,
                overlay=overlay,
                overlay_theme=overlay_theme,
                summary_hold_seconds=summary_hold,
                runner=_reporting_runner,
                on_notice=_notice,
                work_dir=Path(tmp),
            )
        except mp4_grid.GridRenderError as exc:
            console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(code=1) from exc

    for outcome in result.failed:
        console.print(
            f"[yellow]Stage {outcome.stage_number} ({outcome.stage_name}) failed:[/] {outcome.error}"
        )
    rendered = len(result.stages) - len(result.failed)
    note = f", {result.degradation_summary}" if result.degradations else ""
    console.print(f"[green]Wrote[/] {output} ({rendered}/{len(result.stages)} stages{note})")


def _resolve_shooter_slug(match: Match, match_root: Path, name_or_slug: str) -> str | None:
    """Thin alias for :meth:`Match.resolve_shooter_slug`.

    The lookup moved onto ``Match`` so ``match trims`` shares it (#618, #620);
    this keeps the call sites here reading the same as before.
    """
    return match.resolve_shooter_slug(match_root, name_or_slug)
