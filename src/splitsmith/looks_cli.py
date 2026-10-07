"""``splitsmith looks``: list, new, check, preview (issue #1262).

Exit codes: 0 done; 1 ``check`` found an error; 2 the request could not
be carried out (a bad name, an existing Look, no Chromium, no such stage).
The work is ``splitsmith.look_tools``; this file only prints.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import look_tools
from .identity import ResolvedIdentity
from .look_tools import CheckReport, LookToolError
from .looks import LookError, list_looks, load_look, user_looks_dir, variants_for
from .overlay_raster import INSTALL_HINT, ChromiumRasterizer, RasterizerUnavailableError

looks_app = typer.Typer(
    name="looks",
    help="Make, check and preview Looks: the colours and card templates of a rendered video.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console(soft_wrap=True)
#: The authoring guide, as a link: the repo path does not exist in a wheel
#: or the desktop app.
GUIDE = "https://github.com/mandakan/splitsmith/blob/main/docs/looks/authoring.md"
_LEVEL_STYLE = {"ok": "green", "warn": "yellow", "error": "red"}


@contextmanager
def open_chromium() -> Iterator[ChromiumRasterizer]:
    """The browser ``check`` and ``preview`` draw with; a seam for the tests."""
    with ChromiumRasterizer() as rasterizer:
        yield rasterizer


@looks_app.command("list")
def list_command() -> None:
    """The installed Looks: shipped ones first, then yours."""
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Look")
    table.add_column("Source")
    table.add_column("Card styles")
    table.add_column("Stings")
    for look in list_looks():
        styles = sorted(
            {
                v
                for slot in ("title_page", "slate", "lower_third", "closing")
                for v in variants_for(look, slot)
            }
        )
        stings = variants_for(look, "transition")
        table.add_row(
            f"{look.name}  [dim]{look.label}[/]", look.source, ", ".join(styles), ", ".join(stings) or "-"
        )
    console.print(table)
    console.print(f"[dim]Your Looks live in {user_looks_dir()}. Guide: {GUIDE}[/]")


@looks_app.command("new")
def new_command(
    name: str = typer.Argument(..., help="The new Look's name: lower-case letters, digits, '-' and '_'."),
    from_look: str | None = typer.Option(None, "--from", help="Copy this Look (its colours and templates)."),
    starter: str | None = typer.Option(
        None, "--starter", help=f"Start from a template: {', '.join(sorted(look_tools.STARTERS))}."
    ),
) -> None:
    """Make a Look in your Looks folder to edit."""
    try:
        root = look_tools.new_look(name, from_look=from_look, starter=starter)
    except (LookToolError, LookError) as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=2) from None
    origin = f"the {starter} starter" if starter else f"the {from_look or 'splitsmith'} Look"
    console.print(f"Made {root} from {origin}.")
    console.print(f"Edit look.json and the .html files there, then: splitsmith looks check {name}")
    console.print(f"[dim]Guide: {GUIDE}  ·  Preview: splitsmith looks preview {name}[/]")


def _print_report(report: CheckReport) -> None:
    where = f"  {report.root}" if report.root else ""
    console.print(f"Look [bold]{report.look}[/]{where}  {report.source}")
    width = max((len(item.subject) for item in report.items), default=10)
    for item in report.items:
        style = _LEVEL_STYLE[item.level]
        console.print(
            f"  {item.subject:<{width}}  [{style}]{item.level:<5}[/]  {item.message}", highlight=False
        )
    console.print(
        f"{report.errors} error{'s' if report.errors != 1 else ''}, "
        f"{report.warnings} warning{'s' if report.warnings != 1 else ''}"
        + (f"  ·  see {GUIDE}#checking" if report.errors or report.warnings else "")
    )


@looks_app.command("check")
def check_command(name: str = typer.Argument(..., help="The Look to check.")) -> None:
    """Load every template the Look owns in Chromium against sample cards and report problems."""
    try:
        with _LazyProber() as prober:
            report = look_tools.check_look(name, prober=prober)
    except RasterizerUnavailableError as exc:
        console.print(f"[red]Error:[/] {exc.detail}\nInstall it with: {INSTALL_HINT}")
        raise typer.Exit(code=2) from None
    _print_report(report)
    if report.errors:
        raise typer.Exit(code=1)


@looks_app.command("preview")
def preview_command(
    name: str = typer.Argument(..., help="The Look to preview."),
    out: Path | None = typer.Option(None, "--out", help="Where the PNGs go (default ./<name>-preview)."),
    project: Path | None = typer.Option(
        None, "--project", help="A shooter's project folder: preview on its footage."
    ),
    stage: int | None = typer.Option(
        None, "--stage", help="The stage number to take the frame from (with --project)."
    ),
) -> None:
    """Render every card style and sting of the Look to PNGs and a contact sheet."""
    try:
        look_tools.strict_look(name)
    except LookToolError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=2) from None
    if (project is None) != (stage is None):
        console.print("[red]Error:[/] --project and --stage go together")
        raise typer.Exit(code=2)
    target = out or Path.cwd() / f"{name}-preview"
    backdrop = None
    shooters: tuple[ResolvedIdentity, ...] = ()
    if project is not None and stage is not None:
        backdrop, shooters = _stage_frame(project, stage, name)
    try:
        with open_chromium() as rasterizer:
            result = look_tools.preview_look(
                name, rasterizer=rasterizer, out=target, backdrop=backdrop, shooters=shooters
            )
    except RasterizerUnavailableError as exc:
        console.print(f"[red]Error:[/] {exc.detail}\nInstall it with: {INSTALL_HINT}")
        raise typer.Exit(code=2) from None
    for subject in result.skipped:
        console.print(
            f"[yellow]Left out {subject}:[/] its template failed; splitsmith looks check {name} says why"
        )
    console.print(
        f"Wrote {len(result.written) - 1} cards to {target}; "
        f"the contact sheet is {target / 'contact-sheet.png'}"
    )


class _LazyProber:
    """Opens Chromium on the first template it probes, so a Look that is
    missing or whose manifest does not read is reported with no browser."""

    def __init__(self) -> None:
        self._context = None
        self._rasterizer = None

    def __enter__(self) -> _LazyProber:
        return self

    def __exit__(self, *exc: object) -> None:
        if self._context is not None:
            self._context.__exit__(*exc)

    def probe_template(self, template: Path, *, context, width: int, height: int):  # type: ignore[no-untyped-def]
        if self._rasterizer is None:
            self._context = open_chromium()
            self._rasterizer = self._context.__enter__()
        return self._rasterizer.probe_template(template, context=context, width=width, height=height)


def _stage_frame(project_root: Path, stage: int, look_name: str):  # type: ignore[no-untyped-def]
    """The stage's first visible frame and the shooter's identity, as the
    export would draw them."""
    import tempfile

    from PIL import Image

    from .export_preview import _trim_for, grab_frame
    from .match_project import MatchProject
    from .runtime import runtime
    from .ui.identity_media import resolved_identity_for

    try:
        project = MatchProject.load(project_root)
        project.stage(stage)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        console.print(f"[red]Error:[/] {project_root} stage {stage}: {exc}")
        raise typer.Exit(code=2) from None
    trim, beep = _trim_for(project, project_root, stage)
    primary = project.stage(stage).primary()
    image = None
    why = None
    if primary is None or primary.beep_time is None:
        why = f"Stage {stage} has no confirmed beep yet"
    elif trim is None:
        why = f"No trim for stage {stage} on this disk"
    elif not runtime().ffmpeg_binary:
        why = "No ffmpeg on this machine"
    else:
        with tempfile.TemporaryDirectory(prefix="looks-frame-") as tmp:
            frame = grab_frame(
                trim, seconds=beep, at="head", ffmpeg_binary=runtime().ffmpeg_binary, out=Path(tmp) / "f.png"
            )
            if frame is not None:
                with Image.open(frame) as im:
                    image = im.convert("RGB")
            else:
                why = f"Could not read a frame of stage {stage}'s trim"
    if image is None:
        console.print(f"[yellow]{why}; previewing on the demo scene.[/]")
    identity = resolved_identity_for(
        project,
        project_root,
        look=load_look(look_name),
        index=0,
        label=project.competitor_name or project.name,
    )
    return image, (identity,)


__all__ = ["looks_app", "open_chromium"]
