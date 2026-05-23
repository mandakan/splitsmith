"""``splitsmith fetch-models`` -- prefetch + diagnose slim model cache (doc 03).

The command is fully scriptable: stdout is the progress UI, stderr is
errors, exit code is non-zero on any failure. Wired into the main
Typer app via ``app.command("fetch-models")`` in :mod:`splitsmith.cli`.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

import typer
from rich.console import Console
from rich.table import Table

from .models import (
    ArtifactStatus,
    HashMismatch,
    HttpError,
    ModelError,
    ModelRegistry,
    NetworkUnreachable,
    get_default_registry,
)
from .models.registry import _reset_default_registry  # noqa: F401  re-export for tests

console = Console()
err_console = Console(stderr=True)


def _no_artifacts_block() -> None:
    err_console.print(
        "[yellow]No model_artifacts block in ensemble_calibration.json.[/]\n"
        "This wheel ships without slim ONNX artifacts -- "
        "either install dev extras (uv sync --all-groups) and run the\n"
        "torch backend, or wait for a wheel that includes the ONNX block."
    )


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes/1024:.1f} KiB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes/(1024*1024):.1f} MiB"
    return f"{size_bytes/(1024*1024*1024):.2f} GiB"


def _state_badge(state: str) -> str:
    if state == "present":
        return "[green]present[/]"
    if state == "missing":
        return "[red]missing[/]"
    return "[red]mismatched[/]"


def _print_status_table(statuses: Iterable[ArtifactStatus]) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("slug", style="cyan")
    table.add_column("size", justify="right")
    table.add_column("sha256")
    table.add_column("state")
    for status in statuses:
        table.add_row(
            status.slug,
            _format_size(status.size_bytes),
            status.expected_sha256[:12] + "...",
            _state_badge(status.state),
        )
    console.print(table)


def _fetch(registry: ModelRegistry, slugs: Iterable[str]) -> int:
    """Resolve each slug; return ``0`` on full success, ``1`` on any failure."""
    rc = 0
    for slug in slugs:
        size_str = ""
        spec = registry.spec.artifact(slug)
        if spec is not None:
            size_str = f" ({_format_size(spec.size_bytes)})"
        console.print(f"Downloading {slug}{size_str}... ", end="")
        try:
            registry.resolve(slug)
        except HashMismatch as exc:
            err_console.print(f"\n[red]integrity check failed for {slug}[/]: {exc}")
            rc = 1
            continue
        except NetworkUnreachable as exc:
            err_console.print(
                f"\n[red]network unreachable[/]: {exc}\n"
                "Connect to the internet and re-run splitsmith fetch-models."
            )
            rc = 1
            continue
        except HttpError as exc:
            err_console.print(
                f"\n[red]HTTP {exc.status_code}[/]: {exc}\n"
                "The artifact may have moved -- try `uv tool upgrade splitsmith`."
            )
            rc = 1
            continue
        except ModelError as exc:
            err_console.print(f"\n[red]{exc}[/]")
            rc = 1
            continue
        console.print("[green]ok[/]")
    return rc


def fetch_models(
    list_only: bool = typer.Option(False, "--list", help="Print artifact state and exit; no downloads."),
    verify: bool = typer.Option(
        False, "--verify", help="Re-hash every cached file; redownload mismatched entries."
    ),
    force: bool = typer.Option(
        False, "--force", help="Redownload every artifact even if the cached file verifies."
    ),
) -> None:
    """Prefetch the slim runtime ONNX artifacts into the local cache.

    Used both by end users on metered connections (download once, run
    offline later) and by CI environments that pre-warm before the
    test suite runs detection.
    """
    registry = get_default_registry()
    if registry is None:
        _no_artifacts_block()
        raise typer.Exit(code=1)

    if list_only:
        _print_status_table(registry.status())
        return

    if force:
        for slug in registry.known_slugs():
            registry.remove(slug)
    if verify:
        # Drop the in-process verified cache so every cached file is
        # re-hashed on the next status() / resolve() pass.
        statuses = registry.verify_all()
        # Remove any file that doesn't verify so resolve() redownloads
        # cleanly under the lock.
        for status in statuses:
            if status.state == "mismatched":
                registry.remove(status.slug)

    rc = _fetch(registry, registry.known_slugs())
    cache_dir = registry.root
    if rc == 0:
        console.print(f"All models cached at [cyan]{cache_dir}[/]")
    sys.exit(rc)
