"""Typer sub-app for ``splitsmith compare ...``."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from . import emitter as emitter_mod
from . import manifest as manifest_mod
from . import project_loader

compare_app = typer.Typer(
    name="compare",
    help="Multi-shooter comparison FCPXML.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@compare_app.command("export")
def export(
    manifest_path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to a comparison manifest YAML.",
    ),
) -> None:
    """Render a multi-shooter comparison FCPXML from MANIFEST_PATH."""
    manifest = manifest_mod.load_manifest(manifest_path)
    shooters = [project_loader.load_shooter(s.project, s.label) for s in manifest.shooters]
    emitter_mod.emit_compare_fcpxml(
        manifest=manifest,
        shooters=shooters,
        output_path=manifest.output,
    )
    console.print(f"[green]Wrote[/] {manifest.output}")
