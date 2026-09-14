"""OAuth for the YouTube Data API: client config, token store, consent flow.

One Google Cloud project, its OAuth client built into the app. Installed-
app client secrets are non-confidential by Google's own definition, which
is why ``BUILTIN_CLIENT_SECRET`` can be a constant. The env overrides exist
for development before the shipped id lands and for anyone running their
own project. Note that a personal project does not escape YouTube's
private-only lock on unaudited projects; see the spec.

The refresh token lives in ``<user config dir>/youtube.json`` next to
``scoreboard.json``, written through the same atomic helper. Access tokens
are never persisted; :class:`AccessTokenProvider` refreshes on demand.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from pydantic import BaseModel, Field

from .. import user_config

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

ENV_CLIENT_ID = "SPLITSMITH_YOUTUBE_CLIENT_ID"
ENV_CLIENT_SECRET = "SPLITSMITH_YOUTUBE_CLIENT_SECRET"

#: Filled in once the splitsmith Google Cloud project's Desktop client
#: exists. Empty means "not configured": ``connect`` refuses rather than
#: opening a consent page Google would reject.
BUILTIN_CLIENT_ID = ""
BUILTIN_CLIENT_SECRET = ""

CONNECTION_FILENAME = "youtube.json"


class YouTubeError(Exception):
    """Base for every error the youtube package raises on purpose."""


class NotConfiguredError(YouTubeError):
    """No OAuth client id / secret is available."""


class NotConnectedError(YouTubeError):
    """No stored connection; run ``splitsmith youtube login``."""


class ReauthorizeError(YouTubeError):
    """The refresh token was rejected (``invalid_grant``); reconnect."""


class OAuthClient(BaseModel):
    client_id: str
    client_secret: str

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id) and bool(self.client_secret)

    @classmethod
    def configured(cls) -> OAuthClient:
        """Env overrides first, then the built-in constants."""
        return cls(
            client_id=os.environ.get(ENV_CLIENT_ID) or BUILTIN_CLIENT_ID,
            client_secret=os.environ.get(ENV_CLIENT_SECRET) or BUILTIN_CLIENT_SECRET,
        )


class YouTubeConnection(BaseModel):
    """What ``youtube.json`` holds. The refresh token is the secret; the
    channel fields are for display and are refreshed on every login."""

    schema_version: int = user_config.SCHEMA_VERSION
    refresh_token: str
    channel_id: str
    channel_title: str
    connected_at: datetime
    scopes: list[str] = Field(default_factory=lambda: [SCOPE])


def _connection_path() -> Path:
    return user_config.user_config_dir() / CONNECTION_FILENAME


def load_connection() -> YouTubeConnection | None:
    """The stored connection, or ``None`` when there is none, the file is
    malformed, or user config is disabled. Never raises."""
    if user_config.is_disabled():
        return None
    raw = user_config._read_json(_connection_path())
    if not isinstance(raw, dict):
        return None
    try:
        return YouTubeConnection.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 -- a bad file reads as "not connected"
        logger.warning("Discarding malformed %s: %s", CONNECTION_FILENAME, exc)
        return None


def save_connection(conn: YouTubeConnection) -> None:
    target = user_config._ensure_dir()
    if target is None:
        return
    conn = conn.model_copy(update={"schema_version": user_config.SCHEMA_VERSION})
    payload = conn.model_dump_json(indent=2)
    try:
        user_config._atomic_write_text(target / CONNECTION_FILENAME, payload)
    except OSError as exc:
        logger.warning("Could not write %s: %s", CONNECTION_FILENAME, exc)


def clear_connection() -> None:
    if user_config.is_disabled():
        return
    try:
        _connection_path().unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("Could not delete %s: %s", CONNECTION_FILENAME, exc)


# ---------------------------------------------------------------------------
# Consent flow: PKCE, the consent URL, the loopback listener
# ---------------------------------------------------------------------------


class LoopbackError(YouTubeError):
    """The browser round trip did not deliver a usable code."""


class Pkce(BaseModel):
    verifier: str
    challenge: str


def new_pkce() -> Pkce:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return Pkce(verifier=verifier, challenge=challenge)


def authorize_url(client: OAuthClient, *, redirect_uri: str, state: str, code_challenge: str) -> str:
    """Google's consent URL. ``access_type=offline`` + ``prompt=consent``
    make Google issue a refresh token on every login, not only the first."""
    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


_CALLBACK_PAGE = (
    "<!doctype html><title>splitsmith</title>"
    "<p style='font: 15px system-ui; margin: 3em'>YouTube is connected. You can close this tab.</p>"
)
_CALLBACK_FAIL_PAGE = (
    "<!doctype html><title>splitsmith</title>"
    "<p style='font: 15px system-ui; margin: 3em'>Connecting to YouTube failed: {reason}. "
    "You can close this tab.</p>"
)


class LoopbackListener:
    """One-shot HTTP server on ``127.0.0.1`` that catches Google's redirect.

    Bound to the loopback address only; it accepts exactly one request,
    checks ``state`` against what the consent URL carried, and shuts down.
    ``wait`` is what the CLI blocks on; the server thread never outlives
    the listener.
    """

    def __init__(self, *, expected_state: str) -> None:
        self._expected_state = expected_state
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._done = threading.Event()
        self._stop = threading.Event()
        self._code: str | None = None
        self._error: str | None = None

    def start(self) -> str:
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                query = parse_qs(urlparse(self.path).query)
                state = query.get("state", [None])[0]
                code = query.get("code", [None])[0]
                error = query.get("error", [None])[0]
                if state != listener._expected_state:
                    listener._error = "state mismatch: the callback did not come from this login"
                elif error:
                    listener._error = f"Google reported {error}"
                elif not code:
                    listener._error = "no code in the callback"
                else:
                    listener._code = code
                page = (
                    _CALLBACK_PAGE
                    if listener._error is None
                    else _CALLBACK_FAIL_PAGE.format(reason=listener._error)
                )
                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                listener._done.set()

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        # Poll in short slices so ``close()`` can stop the thread cleanly
        # after a timeout instead of yanking the socket out from under a
        # blocking ``handle_request``.
        self._server.timeout = 0.25
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/callback"

    def _serve(self) -> None:
        assert self._server is not None
        while not self._stop.is_set() and not self._done.is_set():
            self._server.handle_request()

    def wait(self, timeout_s: float) -> str:
        if not self._done.wait(timeout_s):
            raise LoopbackError(f"timed out after {timeout_s:.0f}s waiting for the browser")
        if self._error is not None:
            raise LoopbackError(self._error)
        assert self._code is not None
        return self._code

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._server is not None:
            self._server.server_close()
            self._server = None

    def __enter__(self) -> LoopbackListener:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Token exchange, refresh, the whole login
# ---------------------------------------------------------------------------


class TokenResponse(BaseModel):
    access_token: str
    expires_in: int
    refresh_token: str | None = None
    scope: str = ""


def _oauth_error(resp: httpx.Response) -> tuple[str, str]:
    """(error, error_description) from an OAuth error body; tolerant of non-JSON."""
    try:
        body = resp.json()
    except ValueError:
        return ("", resp.text[:200])
    if isinstance(body, dict):
        return (str(body.get("error", "")), str(body.get("error_description", "")))
    return ("", "")


def _token_request(client: OAuthClient, http: httpx.Client, form: dict[str, str]) -> TokenResponse:
    form = {**form, "client_id": client.client_id, "client_secret": client.client_secret}
    try:
        resp = http.post(TOKEN_URL, data=form, timeout=30.0)
    except httpx.HTTPError as exc:
        raise YouTubeError(f"token endpoint unreachable: {exc}") from exc
    if resp.status_code == 200:
        try:
            return TokenResponse.model_validate(resp.json())
        except Exception as exc:  # noqa: BLE001 -- any malformed body is the same failure
            raise YouTubeError(f"token endpoint returned an unexpected body: {exc}") from exc
    error, description = _oauth_error(resp)
    if error == "invalid_grant":
        raise ReauthorizeError(description or "the stored YouTube login is no longer valid")
    raise YouTubeError(f"token endpoint: HTTP {resp.status_code} {error} {description}".rstrip())


def exchange_code(
    client: OAuthClient, http: httpx.Client, *, code: str, redirect_uri: str, code_verifier: str
) -> TokenResponse:
    return _token_request(
        client,
        http,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )


def refresh_access_token(client: OAuthClient, http: httpx.Client, *, refresh_token: str) -> TokenResponse:
    return _token_request(client, http, {"grant_type": "refresh_token", "refresh_token": refresh_token})


def revoke_token(http: httpx.Client, token: str) -> None:
    """Best effort. Logout must succeed locally whether or not Google agrees."""
    try:
        http.post(REVOKE_URL, params={"token": token}, timeout=10.0)
    except httpx.HTTPError as exc:
        logger.info("YouTube token revoke skipped: %s", exc)


class AccessTokenProvider:
    """Hands out a bearer token, refreshing it from the stored refresh
    token when it is missing, near expiry, or explicitly invalidated.

    On ``invalid_grant`` the stored connection is cleared before the error
    propagates, so the next ``load_connection`` says "not connected"
    rather than handing out a token Google will reject again.
    """

    _EARLY_S = 60.0

    def __init__(self, client: OAuthClient, http: httpx.Client, *, refresh_token: str) -> None:
        self._client = client
        self._http = http
        self._refresh_token = refresh_token
        self._access: str | None = None
        self._expires_at = 0.0

    def token(self) -> str:
        if self._access is None or time.monotonic() >= self._expires_at - self._EARLY_S:
            try:
                tok = refresh_access_token(self._client, self._http, refresh_token=self._refresh_token)
            except ReauthorizeError:
                clear_connection()
                raise
            self._access = tok.access_token
            self._expires_at = time.monotonic() + tok.expires_in
        return self._access

    def invalidate(self) -> None:
        self._access = None


def fetch_my_channel(http: httpx.Client, access_token: str) -> tuple[str, str]:
    """(channel id, channel title) of the account behind ``access_token``."""
    resp = http.get(
        CHANNELS_URL,
        params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise YouTubeError(f"channels.list: HTTP {resp.status_code} {resp.text[:200]}")
    items = resp.json().get("items") or []
    if not items:
        raise YouTubeError("this Google account has no YouTube channel; create one in YouTube first")
    return str(items[0]["id"]), str(items[0].get("snippet", {}).get("title", ""))


def connect(
    client: OAuthClient,
    http: httpx.Client,
    *,
    open_browser: Callable[[str], object],
    on_auth_url: Callable[[str], object] | None = None,
    timeout_s: float = 300.0,
) -> YouTubeConnection:
    """The whole login: consent URL, browser, callback, exchange, channel
    lookup, store. ``open_browser`` is injected so the CLI passes
    ``webbrowser.open`` and a server passes a no-op (the SPA opens the URL
    itself); ``on_auth_url`` lets the CLI print the URL for a headless
    shell."""
    if not client.is_configured:
        raise NotConfiguredError(
            f"no YouTube OAuth client is configured; set {ENV_CLIENT_ID} and {ENV_CLIENT_SECRET}"
        )
    state = secrets.token_urlsafe(16)
    pkce = new_pkce()
    with LoopbackListener(expected_state=state) as listener:
        redirect_uri = listener.start()
        url = authorize_url(client, redirect_uri=redirect_uri, state=state, code_challenge=pkce.challenge)
        if on_auth_url is not None:
            on_auth_url(url)
        open_browser(url)
        code = listener.wait(timeout_s)
    tok = exchange_code(client, http, code=code, redirect_uri=redirect_uri, code_verifier=pkce.verifier)
    if not tok.refresh_token:
        raise YouTubeError(
            "Google issued no refresh token; remove splitsmith under your Google account's "
            "third-party access and log in again"
        )
    channel_id, channel_title = fetch_my_channel(http, tok.access_token)
    conn = YouTubeConnection(
        refresh_token=tok.refresh_token,
        channel_id=channel_id,
        channel_title=channel_title,
        connected_at=datetime.now(UTC),
        scopes=tok.scope.split() if tok.scope else [SCOPE],
    )
    save_connection(conn)
    return conn
