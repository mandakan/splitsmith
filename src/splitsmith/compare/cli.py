"""Typer sub-app for ``splitsmith compare ...``."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from . import emitter as emitter_mod
from . import manifest as manifest_mod
from . import project_loader
from ..match_model import Match, is_match_folder, slugify

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
            "Slug or name of the shooter whose audio plays. Required when SOURCE "
            "is a match folder; ignored when SOURCE is a manifest (the YAML's "
            "audio_from key takes precedence)."
        ),
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help=(
            "Where to write the FCPXML. Required when SOURCE is a match folder; "
            "ignored when SOURCE is a manifest (the YAML's output key wins)."
        ),
    ),
) -> None:
    """Render a multi-shooter comparison FCPXML.

    SOURCE accepts two shapes:

      1. A manifest YAML (legacy path): same behaviour as before --
         `splitsmith compare export examples/compare-foo.yaml`.

      2. A merged match folder (new in #320): every shooter under
         `<match>/shooters/` contributes a tile; --audio-from picks the
         unmuted one and --output names the FCPXML.
    """
    if source.is_dir() and is_match_folder(source):
        if audio_from is None:
            console.print(
                "[red]Error:[/] --audio-from is required when SOURCE is a match folder."
            )
            raise typer.Exit(code=2)
        if output is None:
            console.print(
                "[red]Error:[/] --output is required when SOURCE is a match folder."
            )
            raise typer.Exit(code=2)
        _export_from_match(source, audio_from=audio_from, output=output)
        return

    if source.is_dir():
        console.print(
            f"[red]Error:[/] {source} is a directory but does not contain match.json. "
            "Pass a manifest YAML or a merged match folder."
        )
        raise typer.Exit(code=2)

    # Manifest path.
    manifest = manifest_mod.load_manifest(source)
    shooters = [project_loader.load_shooter(s.project, s.label) for s in manifest.shooters]
    emitter_mod.emit_compare_fcpxml(
        manifest=manifest,
        shooters=shooters,
        output_path=manifest.output,
    )
    console.print(f"[green]Wrote[/] {manifest.output}")


def _export_from_match(match_root: Path, *, audio_from: str, output: Path) -> None:
    """Render the compare FCPXML directly from a merged Match."""
    match = Match.load(match_root)
    if not match.shooters:
        console.print(f"[red]Error:[/] match {match_root} has no shooters.")
        raise typer.Exit(code=2)

    # Resolve audio_from to a slug (accept slug exact match OR display-name slugify).
    resolved_audio_slug = _resolve_audio_slug(match, match_root, audio_from)
    if resolved_audio_slug is None:
        slugs = ", ".join(match.shooters)
        console.print(
            f"[red]Error:[/] --audio-from={audio_from!r} matches no shooter on this match. "
            f"Slugs available: {slugs}"
        )
        raise typer.Exit(code=2)

    # Build the bundles. Each bundle's label is the shooter's display name
    # (Shooter.name), falling back to the slug. The audio_from in the synthesized
    # manifest must match one of these labels.
    bundles = []
    audio_label = ""
    for slug in match.shooters:
        shooter = match.load_shooter(match_root, slug)
        label = shooter.name or slug
        bundles.append(
            project_loader.load_shooter_from_match(match_root, slug, label)
        )
        if slug == resolved_audio_slug:
            audio_label = label

    # Synthesize a manifest the emitter can consume. ``layout_2up`` matches
    # today's manifest default; the smallest-fits grid kicks in at 3+ shooters
    # so the choice only matters when N=2.
    synthetic = manifest_mod.CompareManifest(
        output=output,
        audio_from=audio_label,
        layout_2up="horizontal",
        shooters=[
            manifest_mod.CompareShooter(project=match_root, label=b.label) for b in bundles
        ],
    )
    emitter_mod.emit_compare_fcpxml(
        manifest=synthetic, shooters=bundles, output_path=output
    )
    console.print(f"[green]Wrote[/] {output}")


def _resolve_audio_slug(match: Match, match_root: Path, audio_from: str) -> str | None:
    """Match ``audio_from`` to a shooter slug.

    Accepts an exact slug match (``"anton-johansson"``) or a display name
    that slugifies to a registered slug (``"Anton Johansson"``,
    ``"anton johansson"``). Returns the canonical slug or ``None`` when no
    match is found.
    """
    if audio_from in match.shooters:
        return audio_from
    target = slugify(audio_from)
    if target in match.shooters:
        return target
    # Fall back to matching against display names.
    for slug in match.shooters:
        try:
            shooter = match.load_shooter(match_root, slug)
        except FileNotFoundError:
            continue
        if shooter.name == audio_from or slugify(shooter.name) == target:
            return slug
    return None
