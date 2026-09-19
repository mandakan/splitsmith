"""The server's YouTube surface (issue #1000).

Settings and the connect state machine, the upload route and the
``youtube_upload`` job body. Works in both modes behind one route shape:

* **Local** (phase 1): the connection is ``youtube.json`` under the user
  config dir; a login runs ``oauth.connect`` on a thread with the
  loopback listener, and the SPA polls ``connect/status`` until the
  thread settles.
* **Hosted** (phase 2, spec 2026-09-19): the connection is the account's
  ``users.youtube_connection`` row through the tenant's
  :class:`PostgresYouTubeConnectionStore`. A login is Google's redirect
  flow: ``connect/start`` records the ``state`` and PKCE verifier as
  ``pending`` on the row and the browser comes back to
  :data:`CALLBACK_PATH` carrying the session cookie, so the auth gate
  pins the tenant and the callback knows whose ``pending`` to check.
  ``connect/status`` derives its answer from the row, which is what
  lets the SPA's poll settle the same way in both modes.

Never imports ``server`` at module level (the ``device_auth_api`` idiom):
``server`` mounts this router and registers the job body.
"""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .. import youtube_sidecar
from ..db.youtube_connections import PendingLogin, PostgresYouTubeConnectionStore, StoredYouTubeConnection
from ..youtube import oauth, sealed
from ..youtube.client import QuotaExceededError, YouTubeClient, default_http
from ..youtube.upload import (
    AlreadyUploadedError,
    UploadOptions,
    build_client,
    connected_client,
    upload_export,
)
from . import export_storage

logger = logging.getLogger(__name__)
router = APIRouter()

CONNECT_TIMEOUT_S = 600.0
CALLBACK_PATH = "/api/settings/youtube/callback"


def _hosted_store(request: Request) -> PostgresYouTubeConnectionStore | None:
    """The tenant's connection store, or ``None`` in local mode."""
    return request.app.state.splitsmith_state.youtube_connections


def _hosted_configured() -> bool:
    """Hosted needs the OAuth client *and* the sealing key; with either
    missing, Connect is a muted line rather than a consent page that
    would end in a 500."""
    return oauth.OAuthClient.configured().is_configured and sealed.is_key_configured()


def _callback_uri(app_state: Any) -> str:
    base = getattr(app_state, "public_base_url", None)
    if not base:
        raise HTTPException(
            status_code=500, detail="SPLITSMITH_PUBLIC_URL is not set; the YouTube callback has no origin"
        )
    return f"{base}{CALLBACK_PATH}"


class YouTubeSettings(BaseModel):
    """Response for GET /api/settings/youtube."""

    configured: bool
    connected: bool
    channel_title: str | None = None
    connected_at: datetime | None = None


class ConnectStartResponse(BaseModel):
    auth_url: str
    expires_at: datetime


class ConnectStatusResponse(BaseModel):
    state: Literal["idle", "pending", "connected", "failed"]
    channel_title: str | None = None
    error: str | None = None


class ConnectAttempt:
    """One local-mode login in flight. ``oauth.connect`` blocks on the
    loopback listener, so it runs on a daemon thread; the routes read the
    fields this object mutates. The SPA opens ``auth_url`` itself, which
    is why ``open_browser`` is a no-op here."""

    def __init__(self) -> None:
        self.state: Literal["pending", "connected", "failed"] = "pending"
        self.auth_url: str | None = None
        self.channel_title: str | None = None
        self.error: str | None = None
        self.started_at = datetime.now(UTC)
        self._url_ready = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self, client: oauth.OAuthClient, *, timeout_s: float = CONNECT_TIMEOUT_S) -> ConnectAttempt:
        def run() -> None:
            try:
                with default_http() as http:
                    conn = oauth.connect(
                        client,
                        http,
                        open_browser=lambda url: None,
                        on_auth_url=self._set_url,
                        timeout_s=timeout_s,
                    )
            except oauth.YouTubeError as exc:
                self.error = str(exc)
                self.state = "failed"
            except Exception as exc:  # noqa: BLE001 -- the thread must not die silently
                logger.exception("YouTube connect failed")
                self.error = f"unexpected error: {exc}"
                self.state = "failed"
            else:
                self.channel_title = conn.channel_title
                self.state = "connected"
            finally:
                self._url_ready.set()

        self.thread = threading.Thread(target=run, name="youtube-connect", daemon=True)
        self.thread.start()
        self._url_ready.wait(5.0)
        return self

    def _set_url(self, url: str) -> None:
        self.auth_url = url
        self._url_ready.set()


def _attempt(request: Request) -> ConnectAttempt | None:
    return getattr(request.app.state, "youtube_connect", None)


# ---------------------------------------------------------------------------
# The connection, both modes
# ---------------------------------------------------------------------------


async def _hosted_connection(store: PostgresYouTubeConnectionStore) -> oauth.YouTubeConnection | None:
    """The live connection from the row, or ``None``. A token that does
    not open under the current key reads as not connected (and is logged
    once per read): the user's remedy is Connect again, whatever the key
    did."""
    stored = await store.get()
    if stored is None:
        return None
    try:
        return stored.open()
    except (sealed.SealedTokenError, oauth.NotConfiguredError) as exc:
        logger.warning("YouTube connection unusable for user %s: %s", store._user_id, exc)
        return None


def _hosted_client(store: PostgresYouTubeConnectionStore, conn: oauth.YouTubeConnection) -> YouTubeClient:
    """A client whose ``invalid_grant`` clears the row, not the file. The
    hook runs on whatever thread the token refresh happens on, never
    inside a running loop, so ``asyncio.run`` is the right bridge."""
    return build_client(conn, on_reauthorize=lambda: asyncio.run(store.clear()))


async def _client_for(request: Request) -> tuple[YouTubeClient, oauth.YouTubeConnection]:
    """The route-side client, or :class:`oauth.NotConnectedError`."""
    store = _hosted_store(request)
    if store is None:
        return connected_client()
    conn = await _hosted_connection(store)
    if conn is None:
        raise oauth.NotConnectedError("not connected to YouTube")
    return _hosted_client(store, conn), conn


def _job_client(state: Any) -> tuple[YouTubeClient, oauth.YouTubeConnection]:
    """The job-body client: sync, on the worker's job thread, with the
    tenant pinned by the queue task."""
    store = state.youtube_connections
    if store is None:
        return connected_client()
    if not sealed.is_key_configured():
        raise oauth.NotConfiguredError(
            f"this worker has no {sealed.ENV_TOKEN_KEY}; set it or re-register the agent"
        )
    conn = asyncio.run(_hosted_connection(store))
    if conn is None:
        raise oauth.NotConnectedError("not connected to YouTube")
    return _hosted_client(store, conn), conn


# ---------------------------------------------------------------------------
# Settings and the connect state machine
# ---------------------------------------------------------------------------


@router.get("/api/settings/youtube", response_model=YouTubeSettings)
async def get_youtube_settings(request: Request) -> YouTubeSettings:
    store = _hosted_store(request)
    if store is None:
        conn = oauth.load_connection()
        configured = oauth.OAuthClient.configured().is_configured
    else:
        conn = await _hosted_connection(store)
        configured = _hosted_configured()
    return YouTubeSettings(
        configured=configured,
        connected=conn is not None,
        channel_title=conn.channel_title if conn else None,
        connected_at=conn.connected_at if conn else None,
    )


@router.post("/api/settings/youtube/connect/start", response_model=ConnectStartResponse)
async def start_youtube_connect(request: Request) -> ConnectStartResponse:
    """Start a login; the SPA opens ``auth_url`` and polls the status.
    A second start while one is pending replaces it: locally the old
    attempt's listener times out on its own, hosted the row's ``pending``
    is overwritten and the old ``state`` can no longer match."""
    store = _hosted_store(request)
    client = oauth.OAuthClient.configured()
    if store is None:
        if not client.is_configured:
            raise HTTPException(
                status_code=409, detail="no YouTube OAuth client is configured on this install"
            )
        attempt = await run_in_threadpool(ConnectAttempt().start, client)
        if attempt.auth_url is None:
            raise HTTPException(status_code=500, detail=attempt.error or "could not start the login")
        request.app.state.youtube_connect = attempt
        return ConnectStartResponse(
            auth_url=attempt.auth_url, expires_at=attempt.started_at + timedelta(seconds=CONNECT_TIMEOUT_S)
        )
    if not _hosted_configured():
        raise HTTPException(status_code=409, detail="YouTube upload is not configured on this server")
    redirect_uri = _callback_uri(request.app.state.splitsmith_state)
    pkce = oauth.new_pkce()
    pending = PendingLogin(
        state=secrets.token_urlsafe(16), code_verifier=pkce.verifier, started_at=datetime.now(UTC)
    )
    await store.set_pending(pending)
    url = oauth.authorize_url(
        client, redirect_uri=redirect_uri, state=pending.state, code_challenge=pkce.challenge
    )
    return ConnectStartResponse(
        auth_url=url, expires_at=pending.started_at + timedelta(seconds=CONNECT_TIMEOUT_S)
    )


def _pending_expired(pending: PendingLogin, *, now: datetime) -> bool:
    return now - pending.started_at > timedelta(seconds=CONNECT_TIMEOUT_S)


@router.get("/api/settings/youtube/connect/status", response_model=ConnectStatusResponse)
async def youtube_connect_status(request: Request) -> ConnectStatusResponse:
    store = _hosted_store(request)
    if store is None:
        attempt = _attempt(request)
        if attempt is None:
            return ConnectStatusResponse(state="idle")
        return ConnectStatusResponse(
            state=attempt.state, channel_title=attempt.channel_title, error=attempt.error
        )
    pending = await store.get_pending()
    if pending is not None and pending.error:
        return ConnectStatusResponse(state="failed", error=pending.error)
    if pending is not None and not _pending_expired(pending, now=datetime.now(UTC)):
        return ConnectStatusResponse(state="pending")
    conn = await _hosted_connection(store)
    if conn is not None:
        return ConnectStatusResponse(state="connected", channel_title=conn.channel_title)
    return ConnectStatusResponse(state="idle")


def _callback_page(title: str, body: str) -> HTMLResponse:
    """The tab Google sent the user back to. It was opened by the SPA
    for the consent page alone, so it says what happened and that it
    can be closed; the SPA's poll picks up the result."""
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:15px system-ui,sans-serif;margin:4rem auto;max-width:32rem;padding:0 1rem;"
        "color:#e6e6e6;background:#111}h1{font-size:1.25rem}</style></head>"
        f"<body><h1>{html.escape(title)}</h1><p>{html.escape(body)}</p>"
        "<p>You can close this tab.</p></body></html>"
    )


@router.get(CALLBACK_PATH, response_class=HTMLResponse)
async def youtube_connect_callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> HTMLResponse:
    """Google's redirect target, hosted only (404 locally: the loopback
    listener is the local callback). Always answers a page, never JSON:
    a browser is what lands here. Every failure is written as
    ``pending.error`` so ``connect/status`` settles ``failed`` with the
    same reason the page shows."""
    store = _hosted_store(request)
    if store is None:
        raise HTTPException(status_code=404, detail="not found")
    pending = await store.get_pending()
    if pending is None:
        return _callback_page("No YouTube login in progress", "Start the login again from splitsmith.")

    async def fail(reason: str) -> HTMLResponse:
        await store.set_pending(pending.model_copy(update={"error": reason}))
        return _callback_page("YouTube login failed", reason)

    if error:
        return await fail(f"Google did not complete the login: {error}")
    if not code or not state or not secrets.compare_digest(state, pending.state):
        return await fail("the login did not match the one splitsmith started; start it again")
    if _pending_expired(pending, now=datetime.now(UTC)):
        return await fail("the login timed out; start it again")
    if not _hosted_configured():
        return await fail("YouTube upload is not configured on this server")
    client = oauth.OAuthClient.configured()
    redirect_uri = _callback_uri(request.app.state.splitsmith_state)

    def exchange() -> oauth.YouTubeConnection:
        with default_http() as http:
            tok = oauth.exchange_code(
                client, http, code=code, redirect_uri=redirect_uri, code_verifier=pending.code_verifier
            )
            return oauth.build_connection(http, tok)

    try:
        conn = await run_in_threadpool(exchange)
    except oauth.YouTubeError as exc:
        return await fail(str(exc))
    except Exception as exc:  # noqa: BLE001 -- the page must say something; the log has the trace
        logger.exception("YouTube callback failed")
        return await fail(f"unexpected error: {exc}")
    await store.set(StoredYouTubeConnection.seal_from(conn))
    await store.clear_pending()
    return _callback_page("YouTube connected", f"Connected as {conn.channel_title}.")


@router.get("/api/settings/youtube/playlists")
async def list_youtube_playlists(request: Request) -> dict[str, Any]:
    """The connected channel's playlists, for the Export page's picker.
    One Data API call per page of 50; nothing cached, the block opens
    rarely. 409 when not connected, 429 when the quota is spent."""
    try:
        client, _conn = await _client_for(request)
    except oauth.NotConnectedError as exc:
        raise HTTPException(status_code=409, detail="not connected to YouTube") from exc
    try:
        playlists = await run_in_threadpool(client.list_playlists)
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except oauth.ReauthorizeError as exc:
        # The provider already cleared the stored token; the SPA re-reads
        # the settings on this status and shows Connect again.
        raise HTTPException(
            status_code=409, detail=f"{str(exc).rstrip('.')}. Connect YouTube again."
        ) from exc
    except oauth.YouTubeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"playlists": [{"id": p.id, "title": p.title} for p in playlists]}


@router.delete("/api/settings/youtube/session")
async def delete_youtube_session(request: Request) -> dict[str, Any]:
    store = _hosted_store(request)
    if store is None:
        conn = oauth.load_connection()
    else:
        conn = await _hosted_connection(store)
    if conn is not None:

        def revoke() -> None:
            with default_http() as http:
                oauth.revoke_token(http, conn.refresh_token)

        await run_in_threadpool(revoke)
    if store is None:
        oauth.clear_connection()
        request.app.state.youtube_connect = None
    else:
        await store.clear()
        await store.clear_pending()
    return {"connected": False}


# ---------------------------------------------------------------------------
# Upload: the route and the job body
# ---------------------------------------------------------------------------


class YouTubeUploadRequest(BaseModel):
    """Body for POST /api/shooters/{slug}/exports/youtube-upload. ``filename``
    is a basename under the shooter's ``exports/`` dir, the key
    ``download_export_file`` takes."""

    filename: str
    privacy: Literal["unlisted", "private", "public"] = "unlisted"
    again: bool = False
    # The convenience options: a playlist by title (created if missing), a
    # scheduled publish time (implies private), subscriber notification.
    playlist: str | None = None
    playlist_id: str | None = None
    publish_at: datetime | None = None
    notify_subscribers: bool = True


def exports_dir_for(state: Any, slug: str) -> Path:
    return state.shooter_project(slug).exports_path(state.shooter_root(slug)).resolve()


def confine_export_filename(exports_dir: Path, filename: str) -> Path:
    """The ``download_export_file`` rule: the resolved path must stay
    inside ``exports/``; ``..`` traversal is a 400."""
    target = (exports_dir / filename).resolve()
    try:
        target.relative_to(exports_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="filename escapes the exports directory") from exc
    return target


def _format_mb(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    return f"{n // (1024 * 1024)} MB"


@router.post("/api/shooters/{slug}/exports/youtube-upload")
async def submit_youtube_upload(slug: str, req: YouTubeUploadRequest, request: Request) -> JSONResponse:
    """Queue a ``youtube_upload`` job for one rendered MP4. Everything
    that can refuse it is checked here, before a job exists. Hosted, the
    render may live only in storage: the MP4 is checked with one HEAD and
    the small sidecar is pulled, never the video."""
    state = request.app.state.splitsmith_state
    store = _hosted_store(request)
    if store is None:
        connected = oauth.load_connection() is not None
    else:
        connected = await _hosted_connection(store) is not None
    if not connected:
        raise HTTPException(status_code=409, detail="not connected to YouTube")
    project = state.shooter_project(slug)
    exports_dir = exports_dir_for(state, slug)
    target = confine_export_filename(exports_dir, req.filename)
    if target.suffix.lower() != ".mp4":
        raise HTTPException(status_code=400, detail="only a rendered .mp4 can be uploaded")
    if not await run_in_threadpool(export_storage.export_present, project, target):
        raise HTTPException(status_code=400, detail=f"{req.filename} is not in exports/")
    sidecar = youtube_sidecar.sidecar_path_for(target)
    if not await run_in_threadpool(export_storage.pull_export_file, project, sidecar):
        raise HTTPException(status_code=400, detail=f"{req.filename} has no -youtube.json sidecar beside it")
    job = await state.jobs.submit(
        kind="youtube_upload",
        shooter_slug=slug,
        args={
            "slug": slug,
            "filename": req.filename,
            "privacy": req.privacy,
            "again": req.again,
            "playlist": req.playlist,
            "playlist_id": req.playlist_id,
            "publish_at": req.publish_at.isoformat() if req.publish_at else None,
            "notify_subscribers": req.notify_subscribers,
        },
    )
    return JSONResponse(job.model_dump(mode="json"))


def run_youtube_upload(
    handle: Any,
    *,
    state: Any,
    slug: str,
    filename: str,
    privacy: str,
    again: bool,
    playlist: str | None = None,
    playlist_id: str | None = None,
    publish_at: str | None = None,
    notify_subscribers: bool = True,
) -> None:
    """Job body for ``youtube_upload``. Progress is bytes sent; a cancel
    lands between chunks through ``handle.check_cancel``. Registered by
    ``server`` with ``state`` bound. ``publish_at`` travels as an ISO
    string because job args are JSON.

    Hosted, the render was pushed to storage by the export job, possibly
    on another worker: the MP4 and its sidecar are pulled first (the
    captions and thumbnail best-effort, they are optional to the upload),
    and the sidecar with its new ``upload`` record is pushed back so the
    API's history route sees it. Every pull is a no-op locally and for a
    file already on this disk."""
    options = UploadOptions(
        privacy=privacy,  # type: ignore[arg-type]
        playlist=playlist or None,
        playlist_id=playlist_id or None,
        publish_at=datetime.fromisoformat(publish_at) if publish_at else None,
        notify_subscribers=notify_subscribers,
    )
    handle.update(progress=0.0, message="Connecting to YouTube...")
    client, conn = _job_client(state)
    project = state.shooter_project(slug)
    mp4 = confine_export_filename(exports_dir_for(state, slug), filename)
    sidecar_path = youtube_sidecar.sidecar_path_for(mp4)
    handle.update(progress=0.0, message="Fetching the render...")
    if not export_storage.pull_export_file(project, mp4):
        raise RuntimeError(f"{filename} is not in exports/ and could not be fetched")
    if not export_storage.pull_export_file(project, sidecar_path):
        raise RuntimeError(f"{filename} has no -youtube.json sidecar")
    for optional in (youtube_sidecar.srt_path_for(mp4), youtube_sidecar.thumbnail_path_for(mp4)):
        export_storage.pull_export_file(project, optional)
    total = mp4.stat().st_size

    def on_progress(sent: int, _total: int) -> None:
        handle.update(
            progress=sent / max(1, total), message=f"Uploading {_format_mb(sent)} of {_format_mb(total)}"
        )

    try:
        record = upload_export(
            mp4,
            client=client,
            options=options,
            channel_title=conn.channel_title,
            again=again,
            progress=on_progress,
            check_cancel=handle.check_cancel,
        )
    except AlreadyUploadedError as exc:
        raise RuntimeError(f"already uploaded: {exc.record.url}") from exc
    export_storage.push_export_file(project, sidecar_path)
    handle.set_result({"video_id": record.video_id, "url": record.url, "notes": record.notes})
    handle.update(progress=1.0, message=f"Uploaded {record.url}")
