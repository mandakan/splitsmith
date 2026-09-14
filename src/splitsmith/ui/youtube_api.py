"""The local server's YouTube surface (issue #1000, PR B).

Settings and the connect state machine, the upload route and the
``youtube_upload`` job body. Local mode only: ``_local_gate`` answers 404
in hosted mode until phase 2 stores tokens per account.

Never imports ``server`` at module level (the ``device_auth_api`` idiom):
``server`` mounts this router and registers the job body.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..youtube import oauth
from ..youtube.client import default_http

logger = logging.getLogger(__name__)
router = APIRouter()

CONNECT_TIMEOUT_S = 600.0


def _local_gate() -> None:
    """Raise 404 in hosted mode. Lazy import, same as device_auth_api."""
    from .server import _hosted_mode_active

    if _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")


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
    """One login in flight. ``oauth.connect`` blocks on the loopback
    listener, so it runs on a daemon thread; the routes read the fields
    this object mutates. The SPA opens ``auth_url`` itself, which is why
    ``open_browser`` is a no-op here."""

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


@router.get("/api/settings/youtube", response_model=YouTubeSettings)
def get_youtube_settings() -> YouTubeSettings:
    _local_gate()
    conn = oauth.load_connection()
    return YouTubeSettings(
        configured=oauth.OAuthClient.configured().is_configured,
        connected=conn is not None,
        channel_title=conn.channel_title if conn else None,
        connected_at=conn.connected_at if conn else None,
    )


@router.post("/api/settings/youtube/connect/start", response_model=ConnectStartResponse)
def start_youtube_connect(request: Request) -> ConnectStartResponse:
    """Start a login; the SPA opens ``auth_url`` and polls the status.
    A second start while one is pending replaces it: the old attempt's
    listener times out on its own."""
    _local_gate()
    client = oauth.OAuthClient.configured()
    if not client.is_configured:
        raise HTTPException(status_code=409, detail="no YouTube OAuth client is configured on this install")
    attempt = ConnectAttempt().start(client)
    if attempt.auth_url is None:
        raise HTTPException(status_code=500, detail=attempt.error or "could not start the login")
    request.app.state.youtube_connect = attempt
    return ConnectStartResponse(
        auth_url=attempt.auth_url, expires_at=attempt.started_at + timedelta(seconds=CONNECT_TIMEOUT_S)
    )


@router.get("/api/settings/youtube/connect/status", response_model=ConnectStatusResponse)
def youtube_connect_status(request: Request) -> ConnectStatusResponse:
    _local_gate()
    attempt = _attempt(request)
    if attempt is None:
        return ConnectStatusResponse(state="idle")
    return ConnectStatusResponse(
        state=attempt.state, channel_title=attempt.channel_title, error=attempt.error
    )


@router.delete("/api/settings/youtube/session")
def delete_youtube_session(request: Request) -> dict[str, Any]:
    _local_gate()
    conn = oauth.load_connection()
    if conn is not None:
        with default_http() as http:
            oauth.revoke_token(http, conn.refresh_token)
        oauth.clear_connection()
    request.app.state.youtube_connect = None
    return {"connected": False}
