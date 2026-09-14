"""``splitsmith youtube``: login, status, logout, upload (issue #1000).

Exit codes: 0 done; 1 the upload or login failed (network, quota, a
revoked token); 2 nothing to do with the network went wrong (no client
configured, not logged in, no sidecar, a bad argument); 3 the file was
already uploaded (the URL is printed; ``--again`` overrides).
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from ..youtube_sidecar import UploadRecord
from . import oauth, upload
from .client import YouTubeClient, default_http
from .upload import build_client  # noqa: F401 -- module-level so tests can monkeypatch the seam

youtube_app = typer.Typer(
    name="youtube",
    help="Put rendered match videos on YouTube (login once, then upload).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_PRIVACY_CHOICES = ("unlisted", "private", "public")


def open_client() -> tuple[YouTubeClient, oauth.YouTubeConnection]:
    conn = oauth.load_connection()
    if conn is None:
        raise oauth.NotConnectedError("not connected to YouTube; run `splitsmith youtube login` first")
    return build_client(conn), conn


def _privacy(value: str) -> upload.Privacy:
    if value not in _PRIVACY_CHOICES:
        console.print(f"[red]Error:[/] --privacy must be one of {', '.join(_PRIVACY_CHOICES)}.")
        raise typer.Exit(code=2)
    return value  # type: ignore[return-value]


def run_upload_with_progress(
    mp4: Path,
    *,
    client: upload.Uploader,
    channel_title: str,
    privacy: upload.Privacy,
    again: bool,
) -> UploadRecord:
    """``upload_export`` under a rich transfer bar. Shared with ``match
    export --youtube-upload``."""
    with Progress(
        TextColumn("[dim]uploading[/]"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    ) as bar:
        task = bar.add_task("upload", total=mp4.stat().st_size)

        def on_progress(sent: int, total: int) -> None:
            bar.update(task, completed=sent, total=total)

        return upload.upload_export(
            mp4,
            client=client,
            privacy=privacy,
            channel_title=channel_title,
            again=again,
            progress=on_progress,
        )


def report_upload(record: UploadRecord) -> None:
    """The URL on its own line (``soft_wrap`` so a narrow terminal never
    breaks it), then each note as a full sentence under it."""
    console.print(f"[bold]Uploaded[/] {record.url}", soft_wrap=True)
    for note in record.notes:
        console.print(f"[yellow]note[/] {note}", soft_wrap=True)


@youtube_app.command("login")
def login() -> None:
    """Connect a YouTube channel: opens Google's consent page in the browser."""
    client = oauth.OAuthClient.configured()
    if not client.is_configured:
        console.print(
            f"[red]Error:[/] no YouTube OAuth client is configured. "
            f"Set {oauth.ENV_CLIENT_ID} and {oauth.ENV_CLIENT_SECRET} from a Google Cloud Desktop client."
        )
        raise typer.Exit(code=2)
    try:
        with default_http() as http:
            conn = oauth.connect(
                client,
                http,
                open_browser=webbrowser.open,
                on_auth_url=lambda url: console.print(
                    f"Opening the browser. If it does not open, visit:\n{url}", soft_wrap=True
                ),
            )
    except oauth.YouTubeError as exc:
        console.print(f"[red]Error:[/] {exc}", soft_wrap=True)
        raise typer.Exit(code=1) from exc
    console.print(f"[bold]Connected[/] as {conn.channel_title}")


@youtube_app.command("status")
def status() -> None:
    """Show the connected channel, if any."""
    conn = oauth.load_connection()
    if conn is None:
        console.print("Not connected. Run `splitsmith youtube login`.")
        return
    console.print(f"Connected as [bold]{conn.channel_title}[/] since {conn.connected_at:%Y-%m-%d %H:%M} UTC")


@youtube_app.command("logout")
def logout() -> None:
    """Forget the stored YouTube login (and ask Google to revoke it)."""
    conn = oauth.load_connection()
    if conn is None:
        console.print("Not connected; nothing to do.")
        return
    with httpx.Client() as http:
        oauth.revoke_token(http, conn.refresh_token)
    oauth.clear_connection()
    console.print("Logged out of YouTube.")


@youtube_app.command("upload")
def upload_cmd(
    video: Path = typer.Argument(
        ..., exists=True, readable=True, help="A rendered MP4 with its -youtube.json beside it."
    ),
    privacy: str = typer.Option("unlisted", "--privacy", help="unlisted (default), private or public."),
    again: bool = typer.Option(
        False, "--again", help="Upload even if the sidecar records a previous upload."
    ),
) -> None:
    """Upload one rendered match video with the sidecar's title, description, tags, captions and thumbnail."""
    priv = _privacy(privacy)
    try:
        client, conn = open_client()
    except oauth.NotConnectedError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=2) from exc
    try:
        record = run_upload_with_progress(
            video, client=client, channel_title=conn.channel_title, privacy=priv, again=again
        )
    except upload.AlreadyUploadedError as exc:
        console.print(f"Already uploaded: {exc.record.url}", soft_wrap=True)
        console.print("Pass --again to upload it a second time.")
        raise typer.Exit(code=3) from exc
    except upload.SidecarMissingError as exc:
        console.print(f"[red]Error:[/] {exc}", soft_wrap=True)
        raise typer.Exit(code=2) from exc
    except oauth.ReauthorizeError as exc:
        console.print(f"[red]Error:[/] {exc} Run `splitsmith youtube login` again.", soft_wrap=True)
        raise typer.Exit(code=1) from exc
    except oauth.YouTubeError as exc:
        console.print(f"[red]Error:[/] {exc}", soft_wrap=True)
        raise typer.Exit(code=1) from exc
    report_upload(record)
