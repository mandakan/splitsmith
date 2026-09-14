# YouTube upload, PR A: engine and CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `splitsmith youtube login|status|logout|upload` and `splitsmith match export --youtube-upload` put a rendered MP4 on YouTube with the sidecar's title, chaptered description, tags, captions and thumbnail, recording the result in the sidecar.

**Architecture:** A new `splitsmith.youtube` package: `oauth.py` (built-in client, loopback PKCE flow, refresh-token store under `~/.splitsmith/youtube.json`), `client.py` (a thin `httpx` client for the four Data API calls with resumable upload and resume-on-failure), `upload.py` (reads the sidecar beside an MP4, runs the calls, writes the `upload` record back), `cli.py` (the typer group). Nothing under the package imports FastAPI or typer except `cli.py`. PR B (server, job, SPA) is a separate plan.

**Tech Stack:** Python 3.11+, `httpx` (already a dependency), pydantic, typer, rich, `respx` for HTTP tests, pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-youtube-upload-design.md`

## Global Constraints

- Python 3.11+, type hints everywhere, `pathlib.Path` for paths, f-strings, Black at line length 110, Ruff clean. Imports: stdlib, third-party, local, separated by blank lines; local imports are `from . import x` / `from .x import y` (single dot; from inside `youtube/` use `from ..user_config import ...`).
- No new dependencies. `httpx` and `respx` are already in `pyproject.toml`.
- Pydantic models for every value crossing a module boundary.
- No file I/O in `client.py` beyond reading the file being uploaded; no network in any unit test (`respx.mock` or a fake client).
- The one OAuth scope is `https://www.googleapis.com/auth/youtube.force-ssl`.
- Prose in docstrings and CLI output: ASCII punctuation only, no em dashes, no `--` used as a dash.
- Run tests as `uv run pytest tests/<file>.py -n0 -q` while working on one file; the full suite pops crash dialogs on this Mac (Python 3.14), so run targeted files.
- Commit after every task with a conventional message; end each commit message with the attribution lines the session provides.

---

### Task 1: OAuth client config and the connection store

**Files:**
- Create: `src/splitsmith/youtube/__init__.py`
- Create: `src/splitsmith/youtube/oauth.py`
- Test: `tests/test_youtube_oauth.py`

**Interfaces:**
- Consumes: `splitsmith.user_config.user_config_dir()`, `user_config.is_disabled()`, `user_config._ensure_dir()`, `user_config._atomic_write_text()`, `user_config._read_json()`, `user_config.SCHEMA_VERSION`.
- Produces:
  - `SCOPE: str`
  - `class OAuthClient(BaseModel)`: `client_id: str`, `client_secret: str`; `@property is_configured -> bool`; `@classmethod configured() -> OAuthClient` (env `SPLITSMITH_YOUTUBE_CLIENT_ID` / `SPLITSMITH_YOUTUBE_CLIENT_SECRET`, else module constants `BUILTIN_CLIENT_ID` / `BUILTIN_CLIENT_SECRET`, both `""` today).
  - `class YouTubeConnection(BaseModel)`: `schema_version: int`, `refresh_token: str`, `channel_id: str`, `channel_title: str`, `connected_at: datetime`, `scopes: list[str]`.
  - `load_connection() -> YouTubeConnection | None`, `save_connection(conn) -> None`, `clear_connection() -> None`, `CONNECTION_FILENAME = "youtube.json"`.
  - Error classes (all in `oauth.py`, re-used by `client.py`): `class YouTubeError(Exception)`, `class NotConnectedError(YouTubeError)`, `class ReauthorizeError(YouTubeError)`, `class NotConfiguredError(YouTubeError)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_youtube_oauth.py
"""``splitsmith.youtube.oauth``: the built-in OAuth client, the connection
store under ``~/.splitsmith/youtube.json``, the loopback listener and the
consent / token exchange (issue #1000, phase 1). No network: token calls
go through ``respx``; the listener is driven by a real ``httpx`` request
to its loopback port.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from splitsmith import user_config
from splitsmith.youtube import oauth


def _conn() -> oauth.YouTubeConnection:
    return oauth.YouTubeConnection(
        refresh_token="1//refresh",
        channel_id="UC123",
        channel_title="Mathias shoots",
        connected_at=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
        scopes=[oauth.SCOPE],
    )


def test_client_reads_env_overrides_before_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPLITSMITH_YOUTUBE_CLIENT_ID", "id-from-env")
    monkeypatch.setenv("SPLITSMITH_YOUTUBE_CLIENT_SECRET", "secret-from-env")
    client = oauth.OAuthClient.configured()
    assert client.client_id == "id-from-env"
    assert client.client_secret == "secret-from-env"
    assert client.is_configured


def test_client_is_unconfigured_when_either_half_is_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPLITSMITH_YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPLITSMITH_YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(oauth, "BUILTIN_CLIENT_ID", "")
    monkeypatch.setattr(oauth, "BUILTIN_CLIENT_SECRET", "")
    assert not oauth.OAuthClient.configured().is_configured
    monkeypatch.setenv("SPLITSMITH_YOUTUBE_CLIENT_ID", "only-id")
    assert not oauth.OAuthClient.configured().is_configured


def test_connection_round_trips_through_the_user_config_dir() -> None:
    assert oauth.load_connection() is None
    oauth.save_connection(_conn())
    path = user_config.user_config_dir() / oauth.CONNECTION_FILENAME
    assert path.exists()
    loaded = oauth.load_connection()
    assert loaded is not None
    assert loaded.refresh_token == "1//refresh"
    assert loaded.channel_title == "Mathias shoots"
    assert loaded.schema_version == user_config.SCHEMA_VERSION
    oauth.clear_connection()
    assert oauth.load_connection() is None
    oauth.clear_connection()  # idempotent


def test_connection_store_is_inert_when_user_config_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(user_config.ENV_DISABLE, "1")
    oauth.save_connection(_conn())
    assert oauth.load_connection() is None
    assert not (Path(user_config.user_config_dir()) / oauth.CONNECTION_FILENAME).exists()


def test_malformed_connection_file_reads_as_not_connected() -> None:
    home = user_config.user_config_dir()
    home.mkdir(parents=True, exist_ok=True)
    (home / oauth.CONNECTION_FILENAME).write_text('{"refresh_token": 5}', encoding="utf-8")
    assert oauth.load_connection() is None
```

Note: `tests/conftest.py` already redirects `SPLITSMITH_HOME` to a per-test tmp dir for every test (`_isolate_user_config`), so no fixture is needed here. Check `user_config.ENV_DISABLE` is cleared by that fixture; if `test_connection_store_is_inert_when_user_config_is_disabled` leaks, add `monkeypatch.delenv(user_config.ENV_DISABLE, raising=False)` to the other tests.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_youtube_oauth.py -n0 -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'splitsmith.youtube'`

- [ ] **Step 3: Create the package and the store**

```python
# src/splitsmith/youtube/__init__.py
"""Direct YouTube upload (issue #1000, phase 1).

``oauth`` holds the built-in OAuth client, the loopback consent flow and
the refresh-token store; ``client`` is the Data API client (resumable
upload, captions, thumbnail); ``upload`` reads the sidecar beside a
rendered MP4 and drives the client; ``cli`` is the ``splitsmith youtube``
verb group. Only ``cli`` imports typer.
"""
```

```python
# src/splitsmith/youtube/oauth.py
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

import logging
import os
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .. import user_config

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_youtube_oauth.py -n0 -q`
Expected: 5 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/splitsmith/youtube tests/test_youtube_oauth.py && uv run black --check src/splitsmith/youtube tests/test_youtube_oauth.py
git add src/splitsmith/youtube tests/test_youtube_oauth.py
git commit -m "feat(youtube): OAuth client config and connection store (#1000)"
```

---

### Task 2: Loopback listener, PKCE and the consent URL

**Files:**
- Modify: `src/splitsmith/youtube/oauth.py`
- Test: `tests/test_youtube_oauth.py`

**Interfaces:**
- Produces:
  - `class Pkce(BaseModel)`: `verifier: str`, `challenge: str`; `new_pkce() -> Pkce`.
  - `authorize_url(client: OAuthClient, *, redirect_uri: str, state: str, code_challenge: str) -> str`.
  - `class LoopbackListener`: `__init__(self, *, expected_state: str)`; `start() -> str` (returns `http://127.0.0.1:<port>/callback`); `wait(timeout_s: float) -> str` (the `code`); `close() -> None`; context-manager support. Raises `LoopbackError` (subclass of `YouTubeError`) on state mismatch, Google error (`?error=access_denied`) or timeout.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_youtube_oauth.py`:

```python
import threading
from urllib.parse import parse_qs, urlparse

import httpx


def test_pkce_challenge_is_s256_of_verifier() -> None:
    import base64
    import hashlib

    pkce = oauth.new_pkce()
    assert 43 <= len(pkce.verifier) <= 128
    digest = hashlib.sha256(pkce.verifier.encode("ascii")).digest()
    assert pkce.challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def test_authorize_url_carries_the_offline_consent_params() -> None:
    client = oauth.OAuthClient(client_id="cid", client_secret="sec")
    url = oauth.authorize_url(
        client, redirect_uri="http://127.0.0.1:5555/callback", state="st", code_challenge="ch"
    )
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == oauth.AUTH_URL
    q = parse_qs(parsed.query)
    assert q["client_id"] == ["cid"]
    assert q["redirect_uri"] == ["http://127.0.0.1:5555/callback"]
    assert q["response_type"] == ["code"]
    assert q["scope"] == [oauth.SCOPE]
    assert q["state"] == ["st"]
    assert q["code_challenge"] == ["ch"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]


def _hit(url: str) -> httpx.Response:
    return httpx.get(url, timeout=5.0)


def test_loopback_listener_captures_the_code_for_a_matching_state() -> None:
    with oauth.LoopbackListener(expected_state="abc") as listener:
        redirect = listener.start()
        assert redirect.startswith("http://127.0.0.1:")
        assert redirect.endswith("/callback")
        got: list[httpx.Response] = []
        t = threading.Thread(target=lambda: got.append(_hit(f"{redirect}?state=abc&code=4/xyz")))
        t.start()
        assert listener.wait(timeout_s=5.0) == "4/xyz"
        t.join(5.0)
    assert got and got[0].status_code == 200
    assert "close this tab" in got[0].text


def test_loopback_listener_rejects_a_mismatched_state() -> None:
    with oauth.LoopbackListener(expected_state="abc") as listener:
        redirect = listener.start()
        t = threading.Thread(target=lambda: _hit(f"{redirect}?state=WRONG&code=4/xyz"))
        t.start()
        with pytest.raises(oauth.LoopbackError, match="state"):
            listener.wait(timeout_s=5.0)
        t.join(5.0)


def test_loopback_listener_surfaces_googles_error_param() -> None:
    with oauth.LoopbackListener(expected_state="abc") as listener:
        redirect = listener.start()
        t = threading.Thread(target=lambda: _hit(f"{redirect}?state=abc&error=access_denied"))
        t.start()
        with pytest.raises(oauth.LoopbackError, match="access_denied"):
            listener.wait(timeout_s=5.0)
        t.join(5.0)


def test_loopback_listener_times_out() -> None:
    with oauth.LoopbackListener(expected_state="abc") as listener:
        listener.start()
        with pytest.raises(oauth.LoopbackError, match="timed out"):
            listener.wait(timeout_s=0.2)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_youtube_oauth.py -n0 -q`
Expected: the six new tests FAIL with `AttributeError: module 'splitsmith.youtube.oauth' has no attribute 'new_pkce'` (and similar).

- [ ] **Step 3: Implement**

Add to `oauth.py` (new imports: `base64`, `hashlib`, `secrets`, `threading`, `from http.server import BaseHTTPRequestHandler, HTTPServer`, `from urllib.parse import parse_qs, urlencode, urlparse`):

```python
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
                body = (
                    _CALLBACK_PAGE if listener._error is None else _CALLBACK_FAIL_PAGE.format(reason=listener._error)
                ).encode("utf-8")
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
```

`handle_request` returns after `timeout` seconds with no request (calling the no-op `handle_timeout`), so `_serve` re-checks the stop flag four times a second; do not use `serve_forever`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_youtube_oauth.py -n0 -q`
Expected: 11 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/splitsmith/youtube tests/test_youtube_oauth.py && uv run black --check src/splitsmith/youtube tests/test_youtube_oauth.py
git add src/splitsmith/youtube/oauth.py tests/test_youtube_oauth.py
git commit -m "feat(youtube): loopback listener, PKCE and consent URL (#1000)"
```

---

### Task 3: Token exchange, refresh, revoke and `connect`

**Files:**
- Modify: `src/splitsmith/youtube/oauth.py`
- Test: `tests/test_youtube_oauth.py`

**Interfaces:**
- Produces:
  - `class TokenResponse(BaseModel)`: `access_token: str`, `expires_in: int`, `refresh_token: str | None = None`, `scope: str = ""`.
  - `exchange_code(client, http: httpx.Client, *, code, redirect_uri, code_verifier) -> TokenResponse`.
  - `refresh_access_token(client, http, *, refresh_token) -> TokenResponse` (raises `ReauthorizeError` on `invalid_grant`).
  - `revoke_token(http, token) -> None` (never raises).
  - `class AccessTokenProvider`: `__init__(self, client, http, *, refresh_token)`; `token() -> str` (cached until 60 s before expiry); `invalidate() -> None`. On `ReauthorizeError` it calls `clear_connection()` then re-raises.
  - `fetch_my_channel(http, access_token) -> tuple[str, str]` (id, title) via `channels.list?part=snippet&mine=true`.
  - `connect(client, http, *, open_browser: Callable[[str], object], on_auth_url: Callable[[str], object] | None = None, timeout_s: float = 300.0) -> YouTubeConnection`.
  - `CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_youtube_oauth.py`:

```python
import respx

CLIENT = oauth.OAuthClient(client_id="cid", client_secret="sec")


@respx.mock
def test_exchange_code_posts_the_pkce_verifier_and_returns_tokens() -> None:
    route = respx.post(oauth.TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "at", "expires_in": 3599, "refresh_token": "rt", "scope": oauth.SCOPE},
        )
    )
    with httpx.Client() as http:
        tok = oauth.exchange_code(
            CLIENT, http, code="4/xyz", redirect_uri="http://127.0.0.1:1/callback", code_verifier="ver"
        )
    assert tok.access_token == "at" and tok.refresh_token == "rt"
    form = parse_qs(route.calls.last.request.content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["4/xyz"]
    assert form["code_verifier"] == ["ver"]
    assert form["client_id"] == ["cid"] and form["client_secret"] == ["sec"]


@respx.mock
def test_refresh_maps_invalid_grant_to_reauthorize() -> None:
    respx.post(oauth.TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token revoked"})
    )
    with httpx.Client() as http, pytest.raises(oauth.ReauthorizeError, match="Token revoked"):
        oauth.refresh_access_token(CLIENT, http, refresh_token="rt")


@respx.mock
def test_refresh_other_failures_are_youtube_errors_not_reauthorize() -> None:
    respx.post(oauth.TOKEN_URL).mock(return_value=httpx.Response(500, text="boom"))
    with httpx.Client() as http, pytest.raises(oauth.YouTubeError) as info:
        oauth.refresh_access_token(CLIENT, http, refresh_token="rt")
    assert not isinstance(info.value, oauth.ReauthorizeError)


@respx.mock
def test_access_token_provider_caches_and_invalidates() -> None:
    route = respx.post(oauth.TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "at1", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "at2", "expires_in": 3600}),
        ]
    )
    with httpx.Client() as http:
        provider = oauth.AccessTokenProvider(CLIENT, http, refresh_token="rt")
        assert provider.token() == "at1"
        assert provider.token() == "at1"
        assert route.call_count == 1
        provider.invalidate()
        assert provider.token() == "at2"
        assert route.call_count == 2


@respx.mock
def test_access_token_provider_clears_the_store_on_reauthorize() -> None:
    oauth.save_connection(_conn())
    respx.post(oauth.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with httpx.Client() as http:
        provider = oauth.AccessTokenProvider(CLIENT, http, refresh_token="rt")
        with pytest.raises(oauth.ReauthorizeError):
            provider.token()
    assert oauth.load_connection() is None


@respx.mock
def test_revoke_never_raises() -> None:
    respx.post(oauth.REVOKE_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_token"}))
    with httpx.Client() as http:
        oauth.revoke_token(http, "rt")


@respx.mock
def test_connect_runs_the_whole_flow_and_saves_the_connection() -> None:
    respx.post(oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 3600, "refresh_token": "rt"})
    )
    respx.get(oauth.CHANNELS_URL).mock(
        return_value=httpx.Response(200, json={"items": [{"id": "UC9", "snippet": {"title": "My channel"}}]})
    )
    opened: list[str] = []

    def fake_browser(url: str) -> None:
        opened.append(url)
        q = parse_qs(urlparse(url).query)
        redirect = q["redirect_uri"][0]
        threading.Thread(target=lambda: _hit(f"{redirect}?state={q['state'][0]}&code=4/ok")).start()

    with httpx.Client() as http:
        conn = oauth.connect(CLIENT, http, open_browser=fake_browser, timeout_s=5.0)
    assert opened and opened[0].startswith(oauth.AUTH_URL)
    assert conn.refresh_token == "rt"
    assert conn.channel_id == "UC9" and conn.channel_title == "My channel"
    stored = oauth.load_connection()
    assert stored is not None and stored.channel_title == "My channel"


def test_connect_refuses_an_unconfigured_client() -> None:
    with httpx.Client() as http, pytest.raises(oauth.NotConfiguredError, match=oauth.ENV_CLIENT_ID):
        oauth.connect(oauth.OAuthClient(client_id="", client_secret=""), http, open_browser=lambda u: None)


@respx.mock
def test_connect_fails_when_google_issues_no_refresh_token() -> None:
    respx.post(oauth.TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 1}))

    def fake_browser(url: str) -> None:
        q = parse_qs(urlparse(url).query)
        threading.Thread(target=lambda: _hit(f"{q['redirect_uri'][0]}?state={q['state'][0]}&code=c")).start()

    with httpx.Client() as http, pytest.raises(oauth.YouTubeError, match="refresh token"):
        oauth.connect(CLIENT, http, open_browser=fake_browser, timeout_s=5.0)
```

`respx.mock` intercepts every `httpx` request in the process, including the loopback GET the fake browser fires from its thread, and raises on any request without a route. In the two `connect` tests add `respx.route(host="127.0.0.1").pass_through()` as the first line of the body so that GET reaches the real listener.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_youtube_oauth.py -n0 -q`
Expected: the nine new tests FAIL with `AttributeError` on the missing names.

- [ ] **Step 3: Implement**

Add to `oauth.py` (new imports: `time`, `from collections.abc import Callable`, `from datetime import UTC`, `import httpx`):

```python
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


class TokenResponse(BaseModel):
    access_token: str
    expires_in: int
    refresh_token: str | None = None
    scope: str = ""


def _token_request(client: OAuthClient, http: httpx.Client, form: dict[str, str]) -> TokenResponse:
    form = {**form, "client_id": client.client_id, "client_secret": client.client_secret}
    try:
        resp = http.post(TOKEN_URL, data=form, timeout=30.0)
    except httpx.HTTPError as exc:
        raise YouTubeError(f"token endpoint unreachable: {exc}") from exc
    if resp.status_code == 200:
        try:
            return TokenResponse.model_validate(resp.json())
        except Exception as exc:  # noqa: BLE001
            raise YouTubeError(f"token endpoint returned an unexpected body: {exc}") from exc
    detail = _oauth_error(resp)
    if detail[0] == "invalid_grant":
        raise ReauthorizeError(detail[1] or "the stored YouTube login is no longer valid")
    raise YouTubeError(f"token endpoint: HTTP {resp.status_code} {detail[0]} {detail[1]}".rstrip())


def _oauth_error(resp: httpx.Response) -> tuple[str, str]:
    try:
        body = resp.json()
    except ValueError:
        return ("", resp.text[:200])
    if isinstance(body, dict):
        return (str(body.get("error", "")), str(body.get("error_description", "")))
    return ("", "")


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
        raise YouTubeError("Google issued no refresh token; remove splitsmith under your Google account's third-party access and log in again")
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_youtube_oauth.py -n0 -q`
Expected: 20 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/splitsmith/youtube tests/test_youtube_oauth.py && uv run black --check src/splitsmith/youtube tests/test_youtube_oauth.py
git add src/splitsmith/youtube/oauth.py tests/test_youtube_oauth.py
git commit -m "feat(youtube): token exchange, refresh and the connect flow (#1000)"
```

---

### Task 4: `YouTubeClient` core: errors, auth retry, `my_channel`

**Files:**
- Create: `src/splitsmith/youtube/client.py`
- Test: `tests/test_youtube_client.py`

**Interfaces:**
- Consumes: `oauth.YouTubeError`, `oauth.ReauthorizeError`, `oauth.AccessTokenProvider` (only its `token()` / `invalidate()` shape, typed as a `Protocol`).
- Produces:
  - `class TokenSource(Protocol)`: `def token(self) -> str`, `def invalidate(self) -> None`.
  - `class QuotaExceededError(YouTubeError)`, `class UploadFailedError(YouTubeError)`.
  - `class Channel(BaseModel)`: `id: str`, `title: str`.
  - `class YouTubeClient`: `__init__(self, http: httpx.Client, tokens: TokenSource)`; `my_channel() -> Channel`; `_request(method, url, **kw) -> httpx.Response` (adds bearer, retries once after `invalidate()` on 401, maps 403 quota reasons to `QuotaExceededError`, other 4xx/5xx to `UploadFailedError` with the API message).
  - `API = "https://www.googleapis.com/youtube/v3"`, `UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"`.
  - `default_http() -> httpx.Client` (timeouts `connect=30, read=120, write=None, pool=30`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_youtube_client.py
"""``splitsmith.youtube.client``: the Data API calls behind a direct upload
(issue #1000). Every HTTP exchange is a ``respx`` route; the tests pin the
resumable protocol (chunk boundaries, 308 + Range, resume after a dropped
connection), the one-refresh-on-401 rule and the error mapping.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from splitsmith.youtube import client as yt
from splitsmith.youtube import oauth


class FakeTokens:
    def __init__(self) -> None:
        self.n = 0
        self.invalidated = 0

    def token(self) -> str:
        self.n += 1
        return f"tok{self.n}"

    def invalidate(self) -> None:
        self.invalidated += 1


def _client() -> tuple[yt.YouTubeClient, FakeTokens]:
    tokens = FakeTokens()
    return yt.YouTubeClient(httpx.Client(), tokens), tokens


@respx.mock
def test_my_channel_sends_the_bearer_and_parses_the_first_item() -> None:
    route = respx.get(f"{yt.API}/channels").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "T"}}]})
    )
    c, _ = _client()
    ch = c.my_channel()
    assert (ch.id, ch.title) == ("UC1", "T")
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok1"
    assert route.calls.last.request.url.params["mine"] == "true"


@respx.mock
def test_a_401_is_retried_once_after_invalidating_the_token() -> None:
    route = respx.get(f"{yt.API}/channels").mock(
        side_effect=[
            httpx.Response(401, json={"error": {"message": "Invalid Credentials"}}),
            httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "T"}}]}),
        ]
    )
    c, tokens = _client()
    assert c.my_channel().id == "UC1"
    assert route.call_count == 2
    assert tokens.invalidated == 1
    assert route.calls[1].request.headers["Authorization"] == "Bearer tok2"


@respx.mock
def test_a_second_401_is_an_error_not_a_loop() -> None:
    respx.get(f"{yt.API}/channels").mock(return_value=httpx.Response(401, json={"error": {"message": "nope"}}))
    c, tokens = _client()
    with pytest.raises(yt.UploadFailedError, match="nope"):
        c.my_channel()
    assert tokens.invalidated == 1


@respx.mock
@pytest.mark.parametrize("reason", ["quotaExceeded", "uploadLimitExceeded"])
def test_quota_reasons_map_to_quota_exceeded(reason: str) -> None:
    respx.get(f"{yt.API}/channels").mock(
        return_value=httpx.Response(
            403, json={"error": {"message": "The request cannot be completed", "errors": [{"reason": reason}]}}
        )
    )
    c, _ = _client()
    with pytest.raises(yt.QuotaExceededError):
        c.my_channel()


@respx.mock
def test_other_403s_are_upload_failures_with_the_api_message() -> None:
    respx.get(f"{yt.API}/channels").mock(
        return_value=httpx.Response(403, json={"error": {"message": "Forbidden by policy", "errors": [{"reason": "forbidden"}]}})
    )
    c, _ = _client()
    with pytest.raises(yt.UploadFailedError, match="Forbidden by policy"):
        c.my_channel()


@respx.mock
def test_reauthorize_from_the_token_source_propagates_untouched() -> None:
    class Dead:
        def token(self) -> str:
            raise oauth.ReauthorizeError("revoked")

        def invalidate(self) -> None:
            pass

    c = yt.YouTubeClient(httpx.Client(), Dead())
    with pytest.raises(oauth.ReauthorizeError):
        c.my_channel()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_youtube_client.py -n0 -q`
Expected: FAIL with `ImportError: cannot import name 'client'`

- [ ] **Step 3: Implement**

```python
# src/splitsmith/youtube/client.py
"""YouTube Data API v3 calls behind a direct upload (issue #1000).

Deliberately thin: four endpoints over an injected ``httpx.Client``, a
bearer token from a :class:`TokenSource`, one refresh-and-retry on 401,
and the resumable upload protocol with resume on a dropped connection.
No Google client library: the protocol is a handful of headers and it is
easier to test against ``respx`` than to mock a discovery document.

Only :meth:`YouTubeClient.upload_bytes` touches the filesystem, and only
to read the file it is sending.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from .oauth import ReauthorizeError, YouTubeError

API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"

_QUOTA_REASONS = frozenset({"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded", "rateLimitExceeded"})


class QuotaExceededError(YouTubeError):
    """The project's daily upload or unit quota is spent."""


class UploadFailedError(YouTubeError):
    """Any other API refusal, with Google's message when it gave one."""


class TokenSource(Protocol):
    def token(self) -> str: ...

    def invalidate(self) -> None: ...


class Channel(BaseModel):
    id: str
    title: str


def default_http() -> httpx.Client:
    """Long read timeout and no write timeout: an 8 MiB chunk on a slow
    uplink takes longer than any sane default."""
    return httpx.Client(timeout=httpx.Timeout(connect=30.0, read=120.0, write=None, pool=30.0))


def _api_error(resp: httpx.Response) -> tuple[str, set[str]]:
    """(message, reasons) from a Data API error body; tolerant of non-JSON."""
    try:
        body = resp.json()
    except ValueError:
        return (resp.text[:200], set())
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return (resp.text[:200], set())
    reasons = {str(e.get("reason", "")) for e in err.get("errors", []) if isinstance(e, dict)}
    return (str(err.get("message", "")), reasons)


def raise_for_api_error(resp: httpx.Response) -> None:
    """Map a non-2xx Data API response onto the error hierarchy."""
    if resp.is_success:
        return
    message, reasons = _api_error(resp)
    if resp.status_code == 403 and reasons & _QUOTA_REASONS:
        raise QuotaExceededError(message or "YouTube API quota exceeded")
    raise UploadFailedError(f"HTTP {resp.status_code}: {message or 'no message'}")


class YouTubeClient:
    def __init__(self, http: httpx.Client, tokens: TokenSource) -> None:
        self._http = http
        self._tokens = tokens

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        """One call with the bearer; on 401, refresh once and retry once.
        A :class:`ReauthorizeError` from the token source passes through."""
        base_headers = dict(kw.pop("headers", None) or {})
        for attempt in (0, 1):
            headers = {**base_headers, "Authorization": f"Bearer {self._tokens.token()}"}
            try:
                resp = self._http.request(method, url, headers=headers, **kw)
            except httpx.HTTPError as exc:
                raise UploadFailedError(f"{method} {url}: {exc}") from exc
            if resp.status_code == 401 and attempt == 0:
                self._tokens.invalidate()
                continue
            raise_for_api_error(resp)
            return resp
        raise AssertionError("unreachable")  # pragma: no cover

    def my_channel(self) -> Channel:
        resp = self._request("GET", f"{API}/channels", params={"part": "snippet", "mine": "true"})
        items = resp.json().get("items") or []
        if not items:
            raise UploadFailedError("this Google account has no YouTube channel")
        return Channel(id=str(items[0]["id"]), title=str(items[0].get("snippet", {}).get("title", "")))
```

`ReauthorizeError` is imported so callers can catch it from one module; keep the import even if unused by name (`__all__` it, or reference it in the docstring; ruff needs it used: add `__all__ = ["ReauthorizeError", ...]`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_youtube_client.py -n0 -q`
Expected: 7 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/splitsmith/youtube tests/test_youtube_client.py && uv run black --check src/splitsmith/youtube tests/test_youtube_client.py
git add src/splitsmith/youtube/client.py tests/test_youtube_client.py
git commit -m "feat(youtube): Data API client core with auth retry and error mapping (#1000)"
```

---

### Task 5: Resumable upload with resume-on-failure

**Files:**
- Modify: `src/splitsmith/youtube/client.py`
- Test: `tests/test_youtube_client.py`

**Interfaces:**
- Produces:
  - `class VideoMetadata(BaseModel)`: `title: str`, `description: str`, `tags: list[str]`, `category_id: str`, `privacy: Literal["unlisted", "private", "public"]`; `to_body() -> dict` (`{"snippet": {...}, "status": {"privacyStatus": ..., "selfDeclaredMadeForKids": False}}`).
  - `YouTubeClient.start_resumable_upload(metadata: VideoMetadata, *, size: int, content_type: str = "video/mp4") -> str`.
  - `YouTubeClient.upload_bytes(session_url: str, path: Path, *, chunk_size: int = 8 * 1024 * 1024, progress: Callable[[int, int], None] | None = None, check_cancel: Callable[[], None] | None = None, max_attempts: int = 8, sleep: Callable[[float], None] = time.sleep) -> str` (returns the video id).
  - `CHUNK_SIZE = 8 * 1024 * 1024`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_youtube_client.py`:

```python
SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=abc"


def _meta() -> yt.VideoMetadata:
    return yt.VideoMetadata(title="T", description="D", tags=["ipsc"], category_id="17", privacy="unlisted")


def _video(tmp_path: Path, size: int) -> Path:
    p = tmp_path / "match.mp4"
    p.write_bytes(bytes(i % 251 for i in range(size)))
    return p


@respx.mock
def test_start_resumable_upload_posts_metadata_and_returns_the_session() -> None:
    route = respx.post(f"{yt.UPLOAD_API}/videos").mock(
        return_value=httpx.Response(200, headers={"Location": SESSION})
    )
    c, _ = _client()
    assert c.start_resumable_upload(_meta(), size=1234) == SESSION
    req = route.calls.last.request
    assert req.url.params["uploadType"] == "resumable"
    assert req.url.params["part"] == "snippet,status"
    assert req.headers["X-Upload-Content-Length"] == "1234"
    assert req.headers["X-Upload-Content-Type"] == "video/mp4"
    import json

    body = json.loads(req.content)
    assert body["snippet"] == {"title": "T", "description": "D", "tags": ["ipsc"], "categoryId": "17"}
    assert body["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}


@respx.mock
def test_start_resumable_upload_without_location_is_a_failure() -> None:
    respx.post(f"{yt.UPLOAD_API}/videos").mock(return_value=httpx.Response(200))
    c, _ = _client()
    with pytest.raises(yt.UploadFailedError, match="Location"):
        c.start_resumable_upload(_meta(), size=1)


def _content_range(req: httpx.Request) -> str:
    return req.headers.get("Content-Range", "")


@respx.mock
def test_upload_bytes_sends_exact_chunks_and_reports_progress(tmp_path: Path) -> None:
    video = _video(tmp_path, 2 * 1024 + 1)  # two full chunks + one byte at chunk_size=1024
    responses = [
        httpx.Response(308, headers={"Range": "bytes=0-1023"}),
        httpx.Response(308, headers={"Range": "bytes=0-2047"}),
        httpx.Response(200, json={"id": "vid123"}),
    ]
    route = respx.put(SESSION).mock(side_effect=responses)
    seen: list[tuple[int, int]] = []
    c, _ = _client()
    assert c.upload_bytes(SESSION, video, chunk_size=1024, progress=lambda s, t: seen.append((s, t))) == "vid123"
    ranges = [_content_range(call.request) for call in route.calls]
    assert ranges == ["bytes 0-1023/2049", "bytes 1024-2047/2049", "bytes 2048-2048/2049"]
    assert [len(call.request.content) for call in route.calls] == [1024, 1024, 1]
    assert seen == [(1024, 2049), (2048, 2049), (2049, 2049)]


@respx.mock
def test_upload_bytes_resumes_from_the_acknowledged_range_after_a_drop(tmp_path: Path) -> None:
    video = _video(tmp_path, 3 * 1024)
    responses = [
        httpx.Response(308, headers={"Range": "bytes=0-1023"}),
        httpx.ConnectError("dropped"),  # chunk 2 never acknowledged
        httpx.Response(308, headers={"Range": "bytes=0-1535"}),  # status query: half of chunk 2 landed
        httpx.Response(308, headers={"Range": "bytes=0-2559"}),  # a full chunk from 1536, not re-aligned
        httpx.Response(201, json={"id": "v"}),
    ]
    route = respx.put(SESSION).mock(side_effect=responses)
    c, _ = _client()
    slept: list[float] = []
    assert c.upload_bytes(SESSION, video, chunk_size=1024, sleep=slept.append) == "v"
    reqs = [call.request for call in route.calls]
    assert _content_range(reqs[2]) == "bytes */3072"
    assert reqs[2].headers["Content-Length"] == "0"
    assert _content_range(reqs[3]) == "bytes 1536-2559/3072"
    assert _content_range(reqs[4]) == "bytes 2560-3071/3072"
    assert slept and slept[0] > 0


@respx.mock
def test_upload_bytes_retries_a_5xx_then_gives_up_after_max_attempts(tmp_path: Path) -> None:
    video = _video(tmp_path, 100)
    respx.put(SESSION).mock(return_value=httpx.Response(503, text="unavailable"))
    c, _ = _client()
    with pytest.raises(yt.UploadFailedError, match="3 attempts"):
        c.upload_bytes(SESSION, video, max_attempts=3, sleep=lambda s: None)


@respx.mock
def test_upload_bytes_status_query_with_no_range_restarts_from_zero(tmp_path: Path) -> None:
    video = _video(tmp_path, 100)
    responses = [
        httpx.ReadTimeout("slow"),
        httpx.Response(308),  # nothing received yet: no Range header
        httpx.Response(200, json={"id": "v"}),
    ]
    route = respx.put(SESSION).mock(side_effect=responses)
    c, _ = _client()
    assert c.upload_bytes(SESSION, video, sleep=lambda s: None) == "v"
    assert _content_range(route.calls[2].request) == "bytes 0-99/100"


@respx.mock
def test_upload_bytes_status_query_can_report_completion(tmp_path: Path) -> None:
    video = _video(tmp_path, 100)
    responses = [httpx.ReadTimeout("slow"), httpx.Response(200, json={"id": "done"})]
    respx.put(SESSION).mock(side_effect=responses)
    c, _ = _client()
    assert c.upload_bytes(SESSION, video, sleep=lambda s: None) == "done"


@respx.mock
def test_upload_bytes_stops_at_a_chunk_boundary_when_cancelled(tmp_path: Path) -> None:
    video = _video(tmp_path, 3 * 1024)
    route = respx.put(SESSION).mock(return_value=httpx.Response(308, headers={"Range": "bytes=0-1023"}))
    calls = {"n": 0}

    def cancel() -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt

    c, _ = _client()
    with pytest.raises(KeyboardInterrupt):
        c.upload_bytes(SESSION, video, chunk_size=1024, check_cancel=cancel)
    assert route.call_count == 1


@respx.mock
def test_upload_bytes_4xx_is_not_retried(tmp_path: Path) -> None:
    video = _video(tmp_path, 10)
    route = respx.put(SESSION).mock(
        return_value=httpx.Response(400, json={"error": {"message": "Bad Request", "errors": []}})
    )
    c, _ = _client()
    with pytest.raises(yt.UploadFailedError, match="Bad Request"):
        c.upload_bytes(SESSION, video, sleep=lambda s: None)
    assert route.call_count == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_youtube_client.py -n0 -q`
Expected: the nine new tests FAIL with `AttributeError` (`VideoMetadata`, `start_resumable_upload`, `upload_bytes`).

- [ ] **Step 3: Implement**

Add to `client.py` (new imports: `time`, `from collections.abc import Callable`, `from pathlib import Path`, `from typing import Literal`):

```python
CHUNK_SIZE = 8 * 1024 * 1024  # Google requires a multiple of 256 KiB


class VideoMetadata(BaseModel):
    title: str
    description: str
    tags: list[str]
    category_id: str
    privacy: Literal["unlisted", "private", "public"]

    def to_body(self) -> dict[str, Any]:
        return {
            "snippet": {
                "title": self.title,
                "description": self.description,
                "tags": self.tags,
                "categoryId": self.category_id,
            },
            "status": {"privacyStatus": self.privacy, "selfDeclaredMadeForKids": False},
        }


def _parse_range_end(header: str | None) -> int:
    """``Range: bytes=0-1535`` -> 1536 (the next byte to send). Absent -> 0."""
    if not header:
        return 0
    try:
        _, _, end = header.partition("=")[2].partition("-")
        return int(end) + 1
    except ValueError:
        return 0


class YouTubeClient:  # continued
    def start_resumable_upload(self, metadata: VideoMetadata, *, size: int, content_type: str = "video/mp4") -> str:
        resp = self._request(
            "POST",
            f"{UPLOAD_API}/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type": content_type,
            },
            json=metadata.to_body(),
        )
        location = resp.headers.get("Location")
        if not location:
            raise UploadFailedError("resumable upload start returned no Location header")
        return location

    def upload_bytes(
        self,
        session_url: str,
        path: Path,
        *,
        chunk_size: int = CHUNK_SIZE,
        progress: Callable[[int, int], None] | None = None,
        check_cancel: Callable[[], None] | None = None,
        max_attempts: int = 8,
        sleep: Callable[[float], None] = time.sleep,
    ) -> str:
        """Send the file in ``Content-Range`` chunks; return the video id.

        Google acknowledges each chunk with 308 and a ``Range`` header
        naming the bytes it holds; the last chunk answers 200/201 with the
        video resource. After a transport error or a 5xx the session is
        asked where it is (a zero-length PUT with ``bytes */total``) and
        the send continues from the byte after the acknowledged range,
        which may be inside the chunk that failed. ``max_attempts``
        bounds *consecutive* failures; any acknowledged chunk resets it.
        ``check_cancel`` runs before every chunk so a cancel lands on a
        boundary and never mid-PUT. 4xx is final: the session is dead.
        """
        total = path.stat().st_size
        offset = 0
        failures = 0
        with path.open("rb") as fh:
            while True:
                if check_cancel is not None:
                    check_cancel()
                fh.seek(offset)
                chunk = fh.read(chunk_size) if total else b""
                end = offset + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{total}" if chunk else f"bytes */{total}",
                }
                try:
                    resp = self._put_chunk(session_url, headers, chunk)
                except httpx.HTTPError as exc:
                    failures += 1
                    if failures >= max_attempts:
                        raise UploadFailedError(f"upload gave up after {max_attempts} attempts: {exc}") from exc
                    sleep(min(2.0**failures, 60.0))
                    offset, done = self._resume_offset(session_url, total, sleep)
                    if done is not None:
                        return done
                    continue
                if resp.status_code == 308:
                    failures = 0
                    offset = _parse_range_end(resp.headers.get("Range"))
                    if progress is not None:
                        progress(offset, total)
                    continue
                if resp.is_success:
                    if progress is not None:
                        progress(total, total)
                    return str(resp.json()["id"])
                if resp.status_code >= 500:
                    failures += 1
                    if failures >= max_attempts:
                        raise UploadFailedError(
                            f"upload gave up after {max_attempts} attempts: HTTP {resp.status_code}"
                        )
                    sleep(min(2.0**failures, 60.0))
                    offset, done = self._resume_offset(session_url, total, sleep)
                    if done is not None:
                        return done
                    continue
                raise_for_api_error(resp)
                raise AssertionError("unreachable")  # pragma: no cover

    def _put_chunk(self, session_url: str, headers: dict[str, str], chunk: bytes) -> httpx.Response:
        headers = {**headers, "Authorization": f"Bearer {self._tokens.token()}"}
        resp = self._http.put(session_url, headers=headers, content=chunk)
        if resp.status_code == 401:
            self._tokens.invalidate()
            headers["Authorization"] = f"Bearer {self._tokens.token()}"
            resp = self._http.put(session_url, headers=headers, content=chunk)
        return resp

    def _resume_offset(
        self, session_url: str, total: int, sleep: Callable[[float], None]
    ) -> tuple[int, str | None]:
        """Ask the session how much it holds. Returns ``(next_offset, None)``,
        or ``(total, video_id)`` when the session turns out to be complete.
        A failing status query counts as one more attempt via the caller's
        loop: it raises the transport error back up."""
        headers = {"Content-Length": "0", "Content-Range": f"bytes */{total}"}
        resp = self._put_chunk(session_url, headers, b"")
        if resp.status_code == 308:
            return _parse_range_end(resp.headers.get("Range")), None
        if resp.is_success:
            return total, str(resp.json()["id"])
        if resp.status_code >= 500:
            return 0, None  # the next PUT will fail again and count an attempt
        raise_for_api_error(resp)
        raise AssertionError("unreachable")  # pragma: no cover
```

Trace `test_upload_bytes_resumes_from_the_acknowledged_range_after_a_drop` against this: PUT 0-1023 -> 308 (offset 1024); PUT 1024-2047 -> ConnectError (failures 1, sleep, status query -> 308 Range 0-1535 -> offset 1536); PUT 1536-2559 -> 308 (offset 2560); PUT 2560-3071 -> 201. Five requests, matching the test. Chunks are not re-aligned after a resume; Google only asks that every chunk but the last be a multiple of 256 KiB, and a resumed offset from a 308 always is. In `test_upload_bytes_status_query_with_no_range_restarts_from_zero`: PUT 0-99 -> ReadTimeout; status -> 308 no Range -> offset 0; PUT 0-99 -> 200. In the 5xx give-up test, every PUT is 503: PUT -> 503 (failures 1), status -> 503 -> (0, None); PUT -> 503 (failures 2); status; PUT -> 503 (failures 3 >= 3) -> raise. Message contains "3 attempts". Good. The `_resume_offset` raising a transport error propagates as `httpx.HTTPError` out of `upload_bytes` uncaught: wrap the status query call in the same try so a dropped status query counts as a failure too:

```python
                    try:
                        offset, done = self._resume_offset(session_url, total, sleep)
                    except httpx.HTTPError:
                        continue  # counted on the next PUT's failure
```

Apply that at both call sites.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_youtube_client.py -n0 -q`
Expected: 16 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/splitsmith/youtube tests/test_youtube_client.py && uv run black --check src/splitsmith/youtube tests/test_youtube_client.py
git add src/splitsmith/youtube/client.py tests/test_youtube_client.py
git commit -m "feat(youtube): resumable upload with resume after a dropped chunk (#1000)"
```

---

### Task 6: Captions and thumbnail

**Files:**
- Modify: `src/splitsmith/youtube/client.py`
- Test: `tests/test_youtube_client.py`

**Interfaces:**
- Produces:
  - `YouTubeClient.insert_caption(video_id: str, srt_path: Path, *, language: str = "en", name: str = "Shots") -> str` (caption id).
  - `YouTubeClient.set_thumbnail(video_id: str, jpg_path: Path) -> None`.
  - `multipart_related(meta: dict, data: bytes, data_content_type: str) -> tuple[bytes, str]` (body, content-type header value).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_youtube_client.py`:

```python
@respx.mock
def test_insert_caption_sends_multipart_related_with_snippet_and_srt(tmp_path: Path) -> None:
    srt = tmp_path / "m.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:01,500\nShot 1\n", encoding="utf-8")
    route = respx.post(f"{yt.UPLOAD_API}/captions").mock(return_value=httpx.Response(200, json={"id": "cap1"}))
    c, _ = _client()
    assert c.insert_caption("vid", srt) == "cap1"
    req = route.calls.last.request
    assert req.url.params["uploadType"] == "multipart"
    assert req.url.params["part"] == "snippet"
    ctype = req.headers["Content-Type"]
    assert ctype.startswith("multipart/related; boundary=")
    boundary = ctype.split("boundary=", 1)[1]
    body = req.content
    assert body.count(f"--{boundary}".encode()) == 3  # two parts + closing
    assert b'"videoId": "vid"' in body and b'"language": "en"' in body and b'"name": "Shots"' in body
    assert b"Shot 1" in body
    assert b"Content-Type: application/octet-stream" in body


@respx.mock
def test_set_thumbnail_posts_the_jpeg_bytes(tmp_path: Path) -> None:
    jpg = tmp_path / "t.jpg"
    jpg.write_bytes(b"\xff\xd8jpegbytes")
    route = respx.post(f"{yt.UPLOAD_API}/thumbnails/set").mock(return_value=httpx.Response(200, json={}))
    c, _ = _client()
    c.set_thumbnail("vid", jpg)
    req = route.calls.last.request
    assert req.url.params["videoId"] == "vid"
    assert req.url.params["uploadType"] == "media"
    assert req.headers["Content-Type"] == "image/jpeg"
    assert req.content == b"\xff\xd8jpegbytes"


@respx.mock
def test_set_thumbnail_failure_is_an_upload_failed_error(tmp_path: Path) -> None:
    jpg = tmp_path / "t.jpg"
    jpg.write_bytes(b"x")
    respx.post(f"{yt.UPLOAD_API}/thumbnails/set").mock(
        return_value=httpx.Response(403, json={"error": {"message": "The authenticated user doesn't have permissions to upload and set custom video thumbnails.", "errors": [{"reason": "forbidden"}]}})
    )
    c, _ = _client()
    with pytest.raises(yt.UploadFailedError, match="custom video thumbnails"):
        c.set_thumbnail("vid", jpg)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_youtube_client.py -n0 -q`
Expected: 3 new FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Add to `client.py` (new imports: `json`, `secrets`):

```python
def multipart_related(meta: dict[str, Any], data: bytes, data_content_type: str) -> tuple[bytes, str]:
    """Google's ``uploadType=multipart`` body: a JSON part then the media
    part, ``multipart/related``. Built by hand because httpx's multipart
    support is ``form-data``, which the upload endpoint rejects."""
    boundary = f"splitsmith-{secrets.token_hex(12)}"
    crlf = b"\r\n"
    body = crlf.join(
        [
            f"--{boundary}".encode(),
            b"Content-Type: application/json; charset=UTF-8",
            b"",
            json.dumps(meta).encode("utf-8"),
            f"--{boundary}".encode(),
            f"Content-Type: {data_content_type}".encode(),
            b"",
            data,
            f"--{boundary}--".encode(),
            b"",
        ]
    )
    return body, f"multipart/related; boundary={boundary}"


class YouTubeClient:  # continued
    def insert_caption(self, video_id: str, srt_path: Path, *, language: str = "en", name: str = "Shots") -> str:
        meta = {"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}}
        body, ctype = multipart_related(meta, srt_path.read_bytes(), "application/octet-stream")
        resp = self._request(
            "POST",
            f"{UPLOAD_API}/captions",
            params={"uploadType": "multipart", "part": "snippet"},
            headers={"Content-Type": ctype},
            content=body,
        )
        return str(resp.json().get("id", ""))

    def set_thumbnail(self, video_id: str, jpg_path: Path) -> None:
        self._request(
            "POST",
            f"{UPLOAD_API}/thumbnails/set",
            params={"videoId": video_id, "uploadType": "media"},
            headers={"Content-Type": "image/jpeg"},
            content=jpg_path.read_bytes(),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_youtube_client.py -n0 -q`
Expected: 19 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/splitsmith/youtube tests/test_youtube_client.py && uv run black --check src/splitsmith/youtube tests/test_youtube_client.py
git add src/splitsmith/youtube/client.py tests/test_youtube_client.py
git commit -m "feat(youtube): captions.insert and thumbnails.set (#1000)"
```

---

### Task 7: Sidecar `upload` record and `upload_export`

**Files:**
- Modify: `src/splitsmith/youtube_sidecar.py:41-58` (the `YouTubeSidecar` model) and `:99-106` (`write_sidecar` becomes atomic)
- Create: `src/splitsmith/youtube/upload.py`
- Test: `tests/test_youtube_upload.py`
- Test: `tests/test_youtube_sidecar.py` (one added case; find the existing file with `ls tests | grep sidecar`)

**Interfaces:**
- Consumes: `youtube_sidecar.YouTubeSidecar`, `youtube_sidecar.write_sidecar`; `client.YouTubeClient` (duck-typed through a `Protocol` so tests pass a fake), `client.VideoMetadata`, `client.UploadFailedError`.
- Produces:
  - In `youtube_sidecar.py`: `class UploadRecord(BaseModel)`: `video_id: str`, `url: str`, `privacy: str`, `uploaded_at: datetime`, `channel_title: str`, `captions_uploaded: bool = False`, `thumbnail_set: bool = False`, `notes: list[str] = []`; `YouTubeSidecar.upload: UploadRecord | None = None`; `load_sidecar(path: Path) -> YouTubeSidecar`; `sidecar_path_for(mp4: Path) -> Path` (`<stem>-youtube.json`), `srt_path_for(mp4) -> Path`, `thumbnail_path_for(mp4) -> Path`.
  - In `youtube/upload.py`: `Privacy = Literal["unlisted", "private", "public"]`; `class SidecarMissingError(UploadFailedError)`; `class AlreadyUploadedError(YouTubeError)` with `.record: UploadRecord`; `class Uploader(Protocol)` (the four client methods used); `CATEGORY_IDS: dict[str, str]`; `upload_export(mp4: Path, *, client: Uploader, privacy: Privacy = "unlisted", channel_title: str = "", again: bool = False, progress: Callable[[int, int], None] | None = None, check_cancel: Callable[[], None] | None = None) -> UploadRecord`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_youtube_upload.py
"""``splitsmith.youtube.upload``: from a rendered MP4 and its sidecar to a
video on the channel, with the result written back into the sidecar
(issue #1000). The client is a fake; the sidecar files are real.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from splitsmith import youtube_sidecar
from splitsmith.youtube import client as yt
from splitsmith.youtube import upload
from splitsmith.youtube.oauth import YouTubeError


class FakeClient:
    def __init__(self, *, caption_error: Exception | None = None, thumb_error: Exception | None = None) -> None:
        self.started: list[tuple[yt.VideoMetadata, int]] = []
        self.uploaded: list[Path] = []
        self.captions: list[Path] = []
        self.thumbs: list[Path] = []
        self.caption_error = caption_error
        self.thumb_error = thumb_error

    def start_resumable_upload(self, metadata: yt.VideoMetadata, *, size: int, content_type: str = "video/mp4") -> str:
        self.started.append((metadata, size))
        return "https://session"

    def upload_bytes(self, session_url: str, path: Path, *, progress=None, check_cancel=None, **kw) -> str:  # type: ignore[no-untyped-def]
        assert session_url == "https://session"
        self.uploaded.append(path)
        if progress is not None:
            progress(path.stat().st_size, path.stat().st_size)
        return "vid42"

    def insert_caption(self, video_id: str, srt_path: Path, *, language: str = "en", name: str = "Shots") -> str:
        if self.caption_error:
            raise self.caption_error
        self.captions.append(srt_path)
        return "cap"

    def set_thumbnail(self, video_id: str, jpg_path: Path) -> None:
        if self.thumb_error:
            raise self.thumb_error
        self.thumbs.append(jpg_path)


def _seed(tmp_path: Path, *, srt: bool = True, thumb: bool = True, category: str = "Sports") -> Path:
    mp4 = tmp_path / "bromma.mp4"
    mp4.write_bytes(b"\x00" * 1000)
    sidecar = youtube_sidecar.YouTubeSidecar(
        title="Bromma Classifier",
        description="Production Optics\n\n0:00 Stage 1\n0:45 Stage 2\n1:30 Stage 3",
        tags=["ipsc", "splitsmith"],
        category=category,
        captions_path="bromma.srt" if srt else None,
        output_video="bromma.mp4",
        thumbnail_path="bromma-thumbnail.jpg" if thumb else None,
    )
    youtube_sidecar.write_sidecar(sidecar, youtube_sidecar.sidecar_path_for(mp4))
    if srt:
        youtube_sidecar.srt_path_for(mp4).write_text("1\n00:00:01,000 --> 00:00:01,500\nShot 1\n")
    if thumb:
        youtube_sidecar.thumbnail_path_for(mp4).write_bytes(b"\xff\xd8")
    return mp4


def test_upload_export_sends_sidecar_metadata_and_records_the_result(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path)
    client = FakeClient()
    seen: list[tuple[int, int]] = []
    record = upload.upload_export(
        mp4, client=client, privacy="unlisted", channel_title="Mine", progress=lambda s, t: seen.append((s, t))
    )
    meta, size = client.started[0]
    assert size == 1000
    assert meta.title == "Bromma Classifier"
    assert "0:45 Stage 2" in meta.description
    assert meta.tags == ["ipsc", "splitsmith"]
    assert meta.category_id == "17"
    assert meta.privacy == "unlisted"
    assert client.uploaded == [mp4]
    assert client.captions == [youtube_sidecar.srt_path_for(mp4)]
    assert client.thumbs == [youtube_sidecar.thumbnail_path_for(mp4)]
    assert record.video_id == "vid42"
    assert record.url == "https://youtu.be/vid42"
    assert record.captions_uploaded and record.thumbnail_set
    assert record.channel_title == "Mine"
    assert record.notes == []
    assert seen == [(1000, 1000)]
    stored = youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(mp4))
    assert stored.upload is not None and stored.upload.video_id == "vid42"
    assert stored.title == "Bromma Classifier"  # nothing else touched


def test_upload_export_refuses_without_a_sidecar(tmp_path: Path) -> None:
    mp4 = tmp_path / "x.mp4"
    mp4.write_bytes(b"0")
    with pytest.raises(upload.SidecarMissingError, match="x-youtube.json"):
        upload.upload_export(mp4, client=FakeClient())


def test_upload_export_refuses_a_second_upload_unless_again(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path)
    client = FakeClient()
    first = upload.upload_export(mp4, client=client)
    with pytest.raises(upload.AlreadyUploadedError) as info:
        upload.upload_export(mp4, client=client)
    assert info.value.record.video_id == first.video_id
    assert len(client.uploaded) == 1
    second = upload.upload_export(mp4, client=client, again=True)
    assert len(client.uploaded) == 2
    assert second.uploaded_at >= first.uploaded_at


def test_upload_export_skips_captions_and_thumbnail_when_the_files_are_absent(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path, srt=False, thumb=False)
    client = FakeClient()
    record = upload.upload_export(mp4, client=client)
    assert client.captions == [] and client.thumbs == []
    assert not record.captions_uploaded and not record.thumbnail_set
    assert record.notes == []


def test_caption_and_thumbnail_failures_become_notes_not_failures(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path)
    client = FakeClient(
        caption_error=yt.UploadFailedError("HTTP 400: bad srt"),
        thumb_error=yt.UploadFailedError("HTTP 403: no custom thumbnails"),
    )
    record = upload.upload_export(mp4, client=client)
    assert record.video_id == "vid42"
    assert not record.captions_uploaded and not record.thumbnail_set
    assert any("captions" in n and "bad srt" in n for n in record.notes)
    assert any("thumbnail" in n and "no custom thumbnails" in n for n in record.notes)


def test_unknown_category_falls_back_to_sports_with_a_note(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path, category="Underwater basket weaving")
    client = FakeClient()
    record = upload.upload_export(mp4, client=client)
    assert client.started[0][0].category_id == "17"
    assert any("category" in n for n in record.notes)


def test_upload_failure_leaves_the_sidecar_untouched(tmp_path: Path) -> None:
    mp4 = _seed(tmp_path)

    class Broken(FakeClient):
        def upload_bytes(self, *a, **k):  # type: ignore[no-untyped-def]
            raise yt.UploadFailedError("HTTP 500")

    with pytest.raises(yt.UploadFailedError):
        upload.upload_export(mp4, client=Broken())
    assert youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(mp4)).upload is None


def test_sidecar_round_trips_an_upload_record(tmp_path: Path) -> None:
    rec = youtube_sidecar.UploadRecord(
        video_id="v", url="https://youtu.be/v", privacy="public",
        uploaded_at=datetime(2026, 9, 14, tzinfo=UTC), channel_title="C",
    )
    sc = youtube_sidecar.YouTubeSidecar(title="t", description="d", upload=rec)
    path = tmp_path / "s-youtube.json"
    youtube_sidecar.write_sidecar(sc, path)
    assert json.loads(path.read_text())["upload"]["video_id"] == "v"
    assert youtube_sidecar.load_sidecar(path).upload == rec
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_youtube_upload.py -n0 -q`
Expected: FAIL with `ImportError: cannot import name 'upload'`

- [ ] **Step 3: Extend the sidecar module**

In `src/splitsmith/youtube_sidecar.py`, add `from datetime import datetime`, `import os`, `import tempfile` to the imports, and above `class YouTubeSidecar`:

```python
class UploadRecord(BaseModel):
    """What a direct upload left behind (issue #1000). Lives inside the
    sidecar so the CLI and the UI share one record, and so a re-render,
    which rewrites the sidecar, clears it: a new file is uploadable
    again."""

    model_config = ConfigDict(extra="forbid")

    video_id: str
    url: str
    privacy: str
    uploaded_at: datetime
    channel_title: str = ""
    captions_uploaded: bool = False
    thumbnail_set: bool = False
    notes: list[str] = Field(default_factory=list)
```

Add to `YouTubeSidecar` after `thumbnail_path`:

```python
    upload: UploadRecord | None = None  # set by youtube.upload after a direct upload
```

Replace `write_sidecar` and add the loaders:

```python
def write_sidecar(sidecar: YouTubeSidecar, output_path: Path) -> None:
    """Write the sidecar JSON, indented for hand-editing. Atomic (temp +
    ``os.replace``): the upload record is written into this file after
    a multi-minute upload and a crash mid-write must not lose it."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=str(output_path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sidecar.model_dump_json(indent=2))
        tmp.replace(output_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def load_sidecar(path: Path) -> YouTubeSidecar:
    """Parse a sidecar written by :func:`write_sidecar`. Raises
    ``FileNotFoundError`` / ``pydantic.ValidationError`` as they come; the
    caller decides what a missing or malformed sidecar means."""
    return YouTubeSidecar.model_validate_json(path.read_text(encoding="utf-8"))


def sidecar_path_for(video: Path) -> Path:
    """``<stem>-youtube.json`` beside the rendered output. The one naming
    rule for the sidecar; ``ui/server.py`` and ``match_cli`` spell the
    same suffix, so a change here is a change there."""
    return video.with_name(video.stem + "-youtube.json")


def srt_path_for(video: Path) -> Path:
    return video.with_suffix(".srt")


def thumbnail_path_for(video: Path) -> Path:
    return video.with_name(video.stem + "-thumbnail.jpg")
```

- [ ] **Step 4: Write `upload.py`**

```python
# src/splitsmith/youtube/upload.py
"""From a rendered MP4 and its sidecar to a video on the channel (issue #1000).

The sidecar (``<stem>-youtube.json``, written by the export with
``--youtube-sidecar``) is the metadata: title, description with the
chapter lines already embedded, tags, category. There is no fallback
without it; an MP4 without a sidecar is not an export splitsmith knows
how to describe.

The result is written back into the same sidecar as ``upload``. That is
the only record, shared by the CLI and the UI: a second upload of the
same file is refused unless ``again`` is set, and a re-render, which
rewrites the sidecar, makes the new file uploadable.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from .. import youtube_sidecar
from .client import UploadFailedError, VideoMetadata
from .oauth import YouTubeError

Privacy = Literal["unlisted", "private", "public"]

#: YouTube's fixed category ids. The sidecar stores the name so it stays
#: readable; the API wants the id.
CATEGORY_IDS: dict[str, str] = {
    "Sports": "17",
    "Entertainment": "24",
    "People & Blogs": "22",
    "Education": "27",
    "Howto & Style": "26",
}
_DEFAULT_CATEGORY = "17"


class SidecarMissingError(UploadFailedError):
    """No ``<stem>-youtube.json`` beside the video. Its own class so the
    CLI can give it the "nothing to do with the network" exit code."""


class AlreadyUploadedError(YouTubeError):
    """The sidecar already carries an upload record."""

    def __init__(self, record: youtube_sidecar.UploadRecord) -> None:
        super().__init__(f"already uploaded as {record.url}")
        self.record = record


class Uploader(Protocol):
    """The slice of :class:`client.YouTubeClient` this module drives."""

    def start_resumable_upload(self, metadata: VideoMetadata, *, size: int, content_type: str = "video/mp4") -> str: ...

    def upload_bytes(
        self,
        session_url: str,
        path: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
        check_cancel: Callable[[], None] | None = None,
    ) -> str: ...

    def insert_caption(self, video_id: str, srt_path: Path, *, language: str = "en", name: str = "Shots") -> str: ...

    def set_thumbnail(self, video_id: str, jpg_path: Path) -> None: ...


def upload_export(
    mp4: Path,
    *,
    client: Uploader,
    privacy: Privacy = "unlisted",
    channel_title: str = "",
    again: bool = False,
    progress: Callable[[int, int], None] | None = None,
    check_cancel: Callable[[], None] | None = None,
) -> youtube_sidecar.UploadRecord:
    """Upload ``mp4`` with its sidecar's metadata; return and record the result.

    Captions (``<stem>.srt``) and the thumbnail (``<stem>-thumbnail.jpg``)
    are sent when the files exist. Either failing is a note on the
    record, not a failure: the video is up, and a channel without phone
    verification cannot take a custom thumbnail at all.
    """
    sidecar_path = youtube_sidecar.sidecar_path_for(mp4)
    try:
        sidecar = youtube_sidecar.load_sidecar(sidecar_path)
    except FileNotFoundError as exc:
        raise SidecarMissingError(
            f"no sidecar beside the video: {sidecar_path.name} (export with --youtube-sidecar)"
        ) from exc
    except ValueError as exc:
        raise UploadFailedError(f"sidecar {sidecar_path.name} is not readable: {exc}") from exc
    if sidecar.upload is not None and not again:
        raise AlreadyUploadedError(sidecar.upload)

    notes: list[str] = []
    category_id = CATEGORY_IDS.get(sidecar.category)
    if category_id is None:
        notes.append(f"category {sidecar.category!r} is not a YouTube category; uploaded as Sports")
        category_id = _DEFAULT_CATEGORY
    metadata = VideoMetadata(
        title=sidecar.title,
        description=sidecar.description,
        tags=list(sidecar.tags),
        category_id=category_id,
        privacy=privacy,
    )

    session = client.start_resumable_upload(metadata, size=mp4.stat().st_size)
    video_id = client.upload_bytes(session, mp4, progress=progress, check_cancel=check_cancel)

    captions_uploaded = False
    srt = youtube_sidecar.srt_path_for(mp4)
    if srt.exists():
        try:
            client.insert_caption(video_id, srt)
            captions_uploaded = True
        except YouTubeError as exc:
            notes.append(f"captions not uploaded: {exc}")

    thumbnail_set = False
    jpg = youtube_sidecar.thumbnail_path_for(mp4)
    if jpg.exists():
        try:
            client.set_thumbnail(video_id, jpg)
            thumbnail_set = True
        except YouTubeError as exc:
            notes.append(f"thumbnail not set: {exc}")

    record = youtube_sidecar.UploadRecord(
        video_id=video_id,
        url=f"https://youtu.be/{video_id}",
        privacy=privacy,
        uploaded_at=datetime.now(UTC),
        channel_title=channel_title,
        captions_uploaded=captions_uploaded,
        thumbnail_set=thumbnail_set,
        notes=notes,
    )
    sidecar.upload = record
    youtube_sidecar.write_sidecar(sidecar, sidecar_path)
    return record
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_youtube_upload.py tests/test_youtube_sidecar.py tests/test_match_cli_export.py -n0 -q`
Expected: all pass (the existing sidecar and CLI-export tests prove the atomic `write_sidecar` and the new optional field broke nothing; a sidecar written before this change has no `upload` key and still validates).

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/splitsmith/youtube src/splitsmith/youtube_sidecar.py tests/test_youtube_upload.py && uv run black --check src/splitsmith/youtube src/splitsmith/youtube_sidecar.py tests/test_youtube_upload.py
git add src/splitsmith/youtube/upload.py src/splitsmith/youtube_sidecar.py tests/test_youtube_upload.py
git commit -m "feat(youtube): upload_export drives the client from the sidecar and records the result (#1000)"
```

---

### Task 8: `splitsmith youtube login | status | logout | upload`

**Files:**
- Create: `src/splitsmith/youtube/cli.py`
- Modify: `src/splitsmith/cli.py:62-68` (register the group)
- Test: `tests/test_youtube_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `youtube_app: typer.Typer`.
  - `open_client() -> tuple[YouTubeClient, YouTubeConnection]` (raises `NotConnectedError`); `build_client(conn) -> YouTubeClient`. Task 9 and PR B's job body reuse `build_client`.
  - `run_upload_with_progress(console, mp4, *, client, channel_title, privacy, again) -> UploadRecord` (rich progress bar), used by Task 9.
  - Exit codes: 0 ok; 1 upload failed / quota / reauthorize; 2 not configured, not connected, no sidecar, bad argument; 3 already uploaded.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_youtube_cli.py
"""``splitsmith youtube``: login, status, logout, upload (issue #1000).
The OAuth flow and the API client are monkeypatched; what is under test
is the verbs' wiring, wording and exit codes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from splitsmith import youtube_sidecar
from splitsmith.cli import app
from splitsmith.youtube import cli as ycli
from splitsmith.youtube import oauth, upload
from splitsmith.youtube import client as yt
from tests.conftest import strip_ansi

runner = CliRunner()


def _conn() -> oauth.YouTubeConnection:
    return oauth.YouTubeConnection(
        refresh_token="rt", channel_id="UC1", channel_title="Mathias shoots",
        connected_at=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
    )


def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(oauth.ENV_CLIENT_ID, "cid")
    monkeypatch.setenv(oauth.ENV_CLIENT_SECRET, "sec")


def test_login_runs_connect_and_prints_the_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    calls: dict[str, Any] = {}

    def fake_connect(client: oauth.OAuthClient, http: Any, **kw: Any) -> oauth.YouTubeConnection:
        calls["client"] = client
        calls["kw"] = kw
        kw["on_auth_url"]("https://accounts.google.com/o/oauth2/v2/auth?x=1")
        return _conn()

    monkeypatch.setattr(ycli.oauth, "connect", fake_connect)
    result = runner.invoke(app, ["youtube", "login"])
    assert result.exit_code == 0, result.output
    text = strip_ansi(result.output)
    assert "accounts.google.com" in text
    assert "Mathias shoots" in text
    assert calls["client"].client_id == "cid"
    assert calls["kw"]["open_browser"] is not None


def test_login_without_a_client_exits_2_and_names_the_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(oauth.ENV_CLIENT_ID, raising=False)
    monkeypatch.delenv(oauth.ENV_CLIENT_SECRET, raising=False)
    monkeypatch.setattr(oauth, "BUILTIN_CLIENT_ID", "")
    monkeypatch.setattr(oauth, "BUILTIN_CLIENT_SECRET", "")
    result = runner.invoke(app, ["youtube", "login"])
    assert result.exit_code == 2
    assert oauth.ENV_CLIENT_ID in strip_ansi(result.output)


def test_login_failure_exits_1_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)

    def fake_connect(*a: Any, **kw: Any) -> oauth.YouTubeConnection:
        raise oauth.LoopbackError("timed out after 300s waiting for the browser")

    monkeypatch.setattr(ycli.oauth, "connect", fake_connect)
    result = runner.invoke(app, ["youtube", "login"])
    assert result.exit_code == 1
    assert "timed out" in strip_ansi(result.output)


def test_status_reports_the_stored_connection_or_not_connected() -> None:
    result = runner.invoke(app, ["youtube", "status"])
    assert result.exit_code == 0
    assert "not connected" in strip_ansi(result.output).lower()
    oauth.save_connection(_conn())
    result = runner.invoke(app, ["youtube", "status"])
    assert result.exit_code == 0
    text = strip_ansi(result.output)
    assert "Mathias shoots" in text and "2026-09-14" in text


def test_logout_clears_the_store_and_revokes_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    oauth.save_connection(_conn())
    revoked: list[str] = []
    monkeypatch.setattr(ycli.oauth, "revoke_token", lambda http, tok: revoked.append(tok))
    result = runner.invoke(app, ["youtube", "logout"])
    assert result.exit_code == 0, result.output
    assert revoked == ["rt"]
    assert oauth.load_connection() is None
    result = runner.invoke(app, ["youtube", "logout"])
    assert result.exit_code == 0  # already logged out is fine


def _seed_export(tmp_path: Path) -> Path:
    mp4 = tmp_path / "bromma.mp4"
    mp4.write_bytes(b"\x00" * 100)
    youtube_sidecar.write_sidecar(
        youtube_sidecar.YouTubeSidecar(title="Bromma", description="0:00 Stage 1", tags=["ipsc"]),
        youtube_sidecar.sidecar_path_for(mp4),
    )
    return mp4


class FakeClient:
    def __init__(self) -> None:
        self.privacy: str | None = None

    def start_resumable_upload(self, metadata: yt.VideoMetadata, *, size: int, content_type: str = "video/mp4") -> str:
        self.privacy = metadata.privacy
        return "s"

    def upload_bytes(self, session_url: str, path: Path, *, progress=None, check_cancel=None, **kw) -> str:  # type: ignore[no-untyped-def]
        if progress:
            progress(50, 100)
            progress(100, 100)
        return "vid9"

    def insert_caption(self, *a: Any, **kw: Any) -> str:
        return "c"

    def set_thumbnail(self, *a: Any, **kw: Any) -> None:
        pass


def _connected(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    _configured(monkeypatch)
    oauth.save_connection(_conn())
    fake = FakeClient()
    monkeypatch.setattr(ycli, "build_client", lambda conn: fake)
    return fake


def test_upload_prints_the_url_and_records_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _connected(monkeypatch)
    mp4 = _seed_export(tmp_path)
    result = runner.invoke(app, ["youtube", "upload", str(mp4), "--privacy", "public"])
    assert result.exit_code == 0, result.output
    text = strip_ansi(result.output)
    assert "https://youtu.be/vid9" in text
    assert fake.privacy == "public"
    assert youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(mp4)).upload is not None


def test_upload_default_privacy_is_unlisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _connected(monkeypatch)
    result = runner.invoke(app, ["youtube", "upload", str(_seed_export(tmp_path))])
    assert result.exit_code == 0, result.output
    assert fake.privacy == "unlisted"


def test_upload_not_connected_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    result = runner.invoke(app, ["youtube", "upload", str(_seed_export(tmp_path))])
    assert result.exit_code == 2
    assert "youtube login" in strip_ansi(result.output)


def test_upload_without_sidecar_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _connected(monkeypatch)
    mp4 = tmp_path / "bare.mp4"
    mp4.write_bytes(b"0")
    result = runner.invoke(app, ["youtube", "upload", str(mp4)])
    assert result.exit_code == 2
    assert "bare-youtube.json" in strip_ansi(result.output)


def test_upload_twice_exits_3_and_shows_the_existing_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _connected(monkeypatch)
    mp4 = _seed_export(tmp_path)
    assert runner.invoke(app, ["youtube", "upload", str(mp4)]).exit_code == 0
    result = runner.invoke(app, ["youtube", "upload", str(mp4)])
    assert result.exit_code == 3
    text = strip_ansi(result.output)
    assert "https://youtu.be/vid9" in text and "--again" in text
    assert runner.invoke(app, ["youtube", "upload", str(mp4), "--again"]).exit_code == 0


def test_upload_failure_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _connected(monkeypatch)

    def boom(*a: Any, **kw: Any) -> str:
        raise yt.QuotaExceededError("The request cannot be completed because you have exceeded your quota.")

    fake.upload_bytes = boom  # type: ignore[method-assign]
    result = runner.invoke(app, ["youtube", "upload", str(_seed_export(tmp_path))])
    assert result.exit_code == 1
    assert "quota" in strip_ansi(result.output)


def test_upload_reauthorize_exits_1_and_points_at_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _connected(monkeypatch)

    def boom(*a: Any, **kw: Any) -> str:
        raise oauth.ReauthorizeError("Token has been expired or revoked.")

    fake.start_resumable_upload = boom  # type: ignore[method-assign]
    result = runner.invoke(app, ["youtube", "upload", str(_seed_export(tmp_path))])
    assert result.exit_code == 1
    assert "youtube login" in strip_ansi(result.output)


def test_upload_notes_are_printed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _connected(monkeypatch)
    mp4 = _seed_export(tmp_path)
    youtube_sidecar.thumbnail_path_for(mp4).write_bytes(b"x")

    def no_thumb(*a: Any, **kw: Any) -> None:
        raise yt.UploadFailedError("HTTP 403: no custom thumbnails")

    fake.set_thumbnail = no_thumb  # type: ignore[method-assign]
    result = runner.invoke(app, ["youtube", "upload", str(mp4)])
    assert result.exit_code == 0, result.output
    assert "thumbnail not set" in strip_ansi(result.output)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_youtube_cli.py -n0 -q`
Expected: FAIL with `ImportError: cannot import name 'cli'`

- [ ] **Step 3: Implement the verb group**

```python
# src/splitsmith/youtube/cli.py
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
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn

from ..youtube_sidecar import UploadRecord
from . import oauth, upload
from .client import YouTubeClient, default_http

youtube_app = typer.Typer(
    name="youtube",
    help="Put rendered match videos on YouTube (login once, then upload).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_PRIVACY_CHOICES = ("unlisted", "private", "public")


def build_client(conn: oauth.YouTubeConnection) -> YouTubeClient:
    """A Data API client over the stored connection. One place so the
    ``match export`` verb and the UI job build it the same way."""
    client = oauth.OAuthClient.configured()
    http = default_http()
    return YouTubeClient(http, oauth.AccessTokenProvider(client, http, refresh_token=conn.refresh_token))


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
            mp4, client=client, privacy=privacy, channel_title=channel_title, again=again, progress=on_progress
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
    video: Path = typer.Argument(..., exists=True, readable=True, help="A rendered MP4 with its -youtube.json beside it."),
    privacy: str = typer.Option("unlisted", "--privacy", help="unlisted (default), private or public."),
    again: bool = typer.Option(False, "--again", help="Upload even if the sidecar records a previous upload."),
) -> None:
    """Upload one rendered match video using the sidecar's title, description, tags, captions and thumbnail."""
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
```

Register in `src/splitsmith/cli.py` after the `match` line:

```python
from .youtube.cli import youtube_app  # noqa: E402

app.add_typer(youtube_app, name="youtube")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_youtube_cli.py tests/test_youtube_upload.py -n0 -q`
Expected: all pass. Then `uv run splitsmith youtube --help` and read the four verbs.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/splitsmith/youtube src/splitsmith/cli.py tests/test_youtube_cli.py && uv run black --check src/splitsmith/youtube src/splitsmith/cli.py tests/test_youtube_cli.py
git add src/splitsmith/youtube/cli.py src/splitsmith/cli.py tests/test_youtube_cli.py
git commit -m "feat(cli): splitsmith youtube login, status, logout and upload (#1000)"
```

---

### Task 9: `match export --youtube-upload`

**Files:**
- Modify: `src/splitsmith/match_cli.py:460-490` (options) and `:620-640` (after the rename and the "Wrote" line)
- Test: `tests/test_match_cli_export.py`

**Interfaces:**
- Consumes: `youtube.cli.open_client`, `youtube.cli.run_upload_with_progress`, `youtube.cli.report_upload`, `youtube.upload.AlreadyUploadedError`, `oauth.NotConnectedError`, `oauth.YouTubeError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_match_cli_export.py` (it already has `_seed`, `_capture_mp4`, `runner`, `strip_ansi`; the thumbnail grab is stubbed the same way `test_youtube_sidecar_and_captions_move_with_a_renamed_output` does it, read that test first and copy its `write_thumbnail` stub):

```python
def _stub_youtube(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from datetime import UTC, datetime

    from splitsmith import youtube_sidecar
    from splitsmith.youtube import cli as ycli
    from splitsmith.youtube import oauth

    seen: dict[str, Any] = {}
    oauth.save_connection(
        oauth.YouTubeConnection(refresh_token="rt", channel_id="c", channel_title="Chan", connected_at=datetime.now(UTC))
    )
    monkeypatch.setattr(ycli, "build_client", lambda conn: object())

    def fake_run(mp4: Path, *, client: Any, channel_title: str, privacy: str, again: bool) -> Any:
        seen["mp4"] = mp4
        seen["privacy"] = privacy
        return youtube_sidecar.UploadRecord(
            video_id="v1", url="https://youtu.be/v1", privacy=privacy, uploaded_at=datetime.now(UTC),
            channel_title=channel_title,
        )

    monkeypatch.setattr(match_cli, "_run_youtube_upload", fake_run)
    return seen


def test_youtube_upload_runs_after_the_render_on_the_final_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import youtube_sidecar

    root = _seed(tmp_path)
    _capture_mp4(monkeypatch)
    monkeypatch.setattr(youtube_sidecar, "write_thumbnail", lambda *a, **k: None)
    seen = _stub_youtube(monkeypatch)
    out = tmp_path / "out" / "final.mp4"
    result = runner.invoke(
        app,
        ["match", "export", str(root), "--shooter", "me", "--format", "mp4", "--youtube-upload",
         "--youtube-privacy", "private", "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert seen["mp4"] == out.resolve()
    assert seen["privacy"] == "private"
    assert out.with_name("final-youtube.json").exists()  # --youtube-upload implied --youtube-sidecar
    assert "https://youtu.be/v1" in strip_ansi(result.output)


def test_youtube_upload_refuses_non_mp4(tmp_path: Path) -> None:
    root = _seed(tmp_path)
    result = runner.invoke(app, ["match", "export", str(root), "--shooter", "me", "--youtube-upload"])
    assert result.exit_code == 2
    assert "mp4" in strip_ansi(result.output)


def test_youtube_upload_not_connected_fails_before_rendering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _seed(tmp_path)
    captured = _capture_mp4(monkeypatch)
    result = runner.invoke(app, ["match", "export", str(root), "--shooter", "me", "--format", "mp4", "--youtube-upload"])
    assert result.exit_code == 2
    assert "youtube login" in strip_ansi(result.output)
    assert "comp" not in captured  # nothing rendered


def test_youtube_upload_failure_after_a_good_render_exits_1_and_keeps_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import youtube_sidecar
    from splitsmith.youtube import oauth

    root = _seed(tmp_path)
    _capture_mp4(monkeypatch)
    monkeypatch.setattr(youtube_sidecar, "write_thumbnail", lambda *a, **k: None)
    _stub_youtube(monkeypatch)

    def boom(*a: Any, **kw: Any) -> Any:
        raise oauth.YouTubeError("HTTP 500: upload gave up after 8 attempts")

    monkeypatch.setattr(match_cli, "_run_youtube_upload", boom)
    out = tmp_path / "out" / "final.mp4"
    result = runner.invoke(
        app, ["match", "export", str(root), "--shooter", "me", "--format", "mp4", "--youtube-upload", "--output", str(out)]
    )
    assert result.exit_code == 1
    assert out.exists()
    text = strip_ansi(result.output)
    assert "Wrote" in text and "gave up" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_match_cli_export.py -n0 -q -k youtube_upload`
Expected: FAIL with `No such option: --youtube-upload`

- [ ] **Step 3: Implement**

In `match_cli.py` add two options to `export` after `description_lead`:

```python
    youtube_upload: bool = typer.Option(
        False,
        "--youtube-upload",
        help="After the render, upload the MP4 to the connected YouTube channel (implies --youtube-sidecar; "
        "run `splitsmith youtube login` first).",
    ),
    youtube_privacy: str = typer.Option(
        "unlisted", "--youtube-privacy", help="Privacy for --youtube-upload: unlisted, private or public."
    ),
```

At module level in `match_cli.py`:

```python
def _run_youtube_upload(mp4: Path, *, client: Any, channel_title: str, privacy: str, again: bool) -> Any:
    """Indirection so the export test can stub the upload without an HTTP client."""
    from .youtube.cli import run_upload_with_progress

    return run_upload_with_progress(mp4, client=client, channel_title=channel_title, privacy=privacy, again=again)  # type: ignore[arg-type]
```

Before the "no stage has a primary video" check in `export` (i.e. before any work), validate and connect:

```python
    yt_client = None
    yt_conn = None
    if youtube_upload:
        from .youtube import oauth as yt_oauth
        from .youtube.cli import open_client

        if output_format != "mp4":
            console.print("[red]Error:[/] --youtube-upload needs --format mp4.")
            raise typer.Exit(code=2)
        if youtube_privacy not in ("unlisted", "private", "public"):
            console.print("[red]Error:[/] --youtube-privacy must be unlisted, private or public.")
            raise typer.Exit(code=2)
        try:
            yt_client, yt_conn = open_client()
        except yt_oauth.NotConnectedError as exc:
            console.print(f"[red]Error:[/] {exc}")
            raise typer.Exit(code=2) from exc
        youtube_sidecar = True
```

After the `for note in result.anomalies:` loop at the end of `export`:

```python
    if youtube_upload:
        from .youtube import oauth as yt_oauth
        from .youtube.cli import report_upload

        assert yt_client is not None and yt_conn is not None
        # ``again=True``: the render just rewrote the sidecar, so there is
        # no earlier record to protect and AlreadyUploadedError cannot fire.
        try:
            record = _run_youtube_upload(
                written, client=yt_client, channel_title=yt_conn.channel_title, privacy=youtube_privacy, again=True
            )
        except yt_oauth.YouTubeError as exc:
            console.print(f"[red]Error:[/] upload failed: {exc}", soft_wrap=True)
            raise typer.Exit(code=1) from exc
        report_upload(record)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_match_cli_export.py -n0 -q`
Expected: all pass, including the pre-existing ones.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/splitsmith/match_cli.py tests/test_match_cli_export.py && uv run black --check src/splitsmith/match_cli.py tests/test_match_cli_export.py
git add src/splitsmith/match_cli.py tests/test_match_cli_export.py
git commit -m "feat(cli): match export --youtube-upload runs the upload after the render (#1000)"
```

---

### Task 10: Docs, end-to-end check, PR

**Files:**
- Modify: `docs/COMMANDS.md` (a `## youtube` section before `## compare`, and the two flags in the `match export` section if one exists; otherwise a line under `## match trims`)
- Modify: `CLAUDE.md` (three sentences under a new `## YouTube upload (#1000)` heading: where the client id comes from, the sidecar is the record, the private lock until the audit)

- [ ] **Step 1: Write the docs**

`docs/COMMANDS.md`:

```markdown
## `youtube` -- put rendered match videos on YouTube

```bash
splitsmith youtube login                       # opens Google's consent page; stores the refresh token in ~/.splitsmith/youtube.json
splitsmith youtube status
splitsmith youtube logout
splitsmith youtube upload exports/bromma.mp4 --privacy unlisted   # reads bromma-youtube.json, bromma.srt, bromma-thumbnail.jpg beside it
splitsmith match export <match> --shooter me --format mp4 --youtube-upload --youtube-privacy private
```

The sidecar (`--youtube-sidecar`, implied by `--youtube-upload`) is the
metadata: title, description with chapter lines, tags. After an upload the
sidecar carries an `upload` block with the video id and URL; a second
`upload` of the same file is refused (exit 3) unless `--again`. A re-render
rewrites the sidecar and is uploadable again.

Until the splitsmith Google Cloud project passes the YouTube API audit,
every upload lands private and stays private. The OAuth client id is built
in; `SPLITSMITH_YOUTUBE_CLIENT_ID` / `SPLITSMITH_YOUTUBE_CLIENT_SECRET`
override it for development.
```

`CLAUDE.md`, after the "Rendered cards and stage summaries" section:

```markdown
## YouTube upload (#1000)

``splitsmith.youtube`` uploads a rendered MP4 with its ``-youtube.json``
sidecar's metadata; the result is written back into the sidecar as
``upload`` and that is the only record (a re-render rewrites the sidecar,
which is when a new upload is allowed). The OAuth client is built in
(``youtube/oauth.py`` constants, env overrides for development); a
user-supplied client would not escape YouTube's private-only lock on
unaudited projects, so there is none. One scope, ``youtube.force-ssl``.
The spec's two corrections to the issue text are in
``docs/superpowers/specs/2026-09-14-youtube-upload-design.md``.
```

- [ ] **Step 2: Run every touched test file and the lint**

```bash
uv run pytest tests/test_youtube_oauth.py tests/test_youtube_client.py tests/test_youtube_upload.py tests/test_youtube_cli.py tests/test_match_cli_export.py tests/test_youtube_sidecar.py tests/test_user_config.py -q
uv run ruff check src tests && uv run black --check src tests
```

Expected: all green.

- [ ] **Step 3: Real-network smoke test (manual, owner only)**

With a Desktop OAuth client from the splitsmith Google Cloud project in `SPLITSMITH_YOUTUBE_CLIENT_ID` / `_SECRET` and the owner's account listed as a test user: `uv run splitsmith youtube login`, then `uv run splitsmith youtube upload <a small rendered mp4 with sidecar> --privacy private`. Confirm the video, chapters, captions track and thumbnail in Studio. Record what happened in the PR description; this is the one check no unit test covers.

- [ ] **Step 4: Commit and open the PR**

```bash
git add docs/COMMANDS.md CLAUDE.md
git commit -m "docs: splitsmith youtube verbs and the upload record (#1000)"
```

Then `superpowers:finishing-a-development-branch`. The PR body names the spec, lists the exit codes, and states the smoke-test result (or that it was not run and why).
