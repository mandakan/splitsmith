"""Typer sub-app for ``splitsmith compare ...``."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from .. import camera_select
from ..match_model import Match, is_match_folder
from ..ui.match_exports import _slugify
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
            "is a match folder; overrides the manifest's audio_from key when "
            "SOURCE is a manifest."
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
            "Camera selector for one shooter, as SLUG=VALUE (repeatable). "
            "VALUE is a camera mount ('chest') or a role ('primary', "
            "'secondary'). SLUG is the match shooter's slug, or a manifest "
            "shooter's label. Overrides the manifest's camera: key and the "
            "shooter's persisted compare_camera."
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

    Precedence on the manifest path: a CLI flag beats the matching YAML
    key. The flag is typed now and the YAML was written earlier, so the
    flag is the more recent statement of intent -- overriding is the
    contract, not an anomaly worth warning about.
    """
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
        _export_from_match(source, audio_from=audio_from, output=output, cameras=cameras)
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
    emitter_mod.emit_compare_fcpxml(
        manifest=manifest,
        shooters=shooters,
        output_path=manifest.output,
    )
    console.print(f"[green]Wrote[/] {manifest.output}")


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
    by_key = {_slugify(s.label): s.label for s in manifest.shooters}
    cameras_by_label: dict[str, str] = {}
    unknown: list[str] = []
    for key, value in cameras.items():
        label = by_key.get(_slugify(key))
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
    match_root: Path, *, audio_from: str, output: Path, cameras: dict[str, str] | None = None
) -> None:
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

    # A --camera slug that names nobody would apply to nothing and look like
    # it worked, so it stops the run the same way a malformed pair does.
    cameras = cameras or {}
    unknown = sorted(set(cameras) - set(match.shooters))
    if unknown:
        console.print(
            f"[red]Error:[/] --camera names no shooter on this match: {', '.join(unknown)}. "
            f"Slugs available: {', '.join(match.shooters)}"
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
        try:
            bundles.append(
                project_loader.load_shooter_from_match(match_root, slug, label, camera=cameras.get(slug))
            )
        except camera_select.CameraResolutionError as exc:
            console.print(f"[red]Error:[/] shooter {slug}: {exc}")
            raise typer.Exit(code=2) from exc
        if slug == resolved_audio_slug:
            audio_label = label

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


def _resolve_audio_slug(match: Match, match_root: Path, audio_from: str) -> str | None:
    """Match ``audio_from`` to a shooter slug.

    Accepts an exact slug (``"s_a4f12d8e"``) or a display name
    (``"Anton Johansson"``, case-insensitive). Slugs are opaque random
    ids now, so the old "slugify the display name to guess a slug"
    fallback no longer applies; we look up by display name instead.
    """
    if audio_from in match.shooters:
        return audio_from
    needle = audio_from.casefold().strip()
    for slug in match.shooters:
        try:
            shooter = match.load_shooter(match_root, slug)
        except FileNotFoundError:
            continue
        if shooter.name.casefold().strip() == needle:
            return slug
    return None
