# YouTube upload, PR B: local server and SPA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The local web app can connect a YouTube channel from the Export page, upload a finished render from the export history, and chain an upload onto a render, all through one background job.

**Architecture:** `ui/youtube_api.py` adds four settings routes (connect state machine over `youtube.oauth.connect` on a thread), one upload route, and the `youtube_upload` job body, gated to local mode. `MatchExportRequest` grows `youtube_upload` / `youtube_privacy` and `_run_match_export` chains the job. `list_export_runs` enriches each run with the sidecar's `upload` record. The SPA gets `YouTubeConnect` on the Export page's YouTube row, upload actions on `ExportHistory`, and a pure `lib/youtubeRows.ts`.

**Tech Stack:** FastAPI, pydantic, the existing job registry; React + TypeScript, vitest + testing-library, Playwright for the visual check.

**Spec:** `docs/superpowers/specs/2026-09-14-youtube-upload-design.md`. **Depends on:** PR A (#1012) merged: `splitsmith.youtube.{oauth,client,upload}` and `youtube_sidecar.{UploadRecord,load_sidecar,sidecar_path_for}`.

## Global Constraints

- Python: 3.11+, type hints, `pathlib.Path`, Black 110, Ruff; `ui/youtube_api.py` must never import `server` at module level (the `device_auth_api._hosted_gate` lazy-import idiom).
- No new dependencies, Python or npm.
- SPA: build only with `components/ui` primitives (`Button`, `Segmented`, `Menu`, `Field`, `Label`, `numeral`); one `primary` button per view (Export's stays on the Export button; Connect is `default`); no `text-[...]`; errors as `--color-destructive` text; no issue numbers in UI copy; every page's derivation in a pure `lib/*.ts` with tests.
- Prose in code and UI: ASCII punctuation, no em dashes.
- Run Python tests as `uv run pytest tests/<file> -n0 -q` per file; SPA tests as `pnpm --dir src/splitsmith/ui_static test -- --run <pattern>` (check `package.json` for the exact script name first); lint with `pnpm --dir src/splitsmith/ui_static lint` and `tsc --noEmit` via the `typecheck` script.
- Commit after every task; end commit messages with the session's attribution lines.

---

### Task 1: Move `build_client` into `youtube.upload` and add `connected_client`

**Files:**
- Modify: `src/splitsmith/youtube/upload.py`
- Modify: `src/splitsmith/youtube/cli.py`
- Test: `tests/test_youtube_upload.py`

**Interfaces:**
- Produces: `upload.build_client(conn: oauth.YouTubeConnection) -> client.YouTubeClient`; `upload.connected_client() -> tuple[client.YouTubeClient, oauth.YouTubeConnection]` (raises `oauth.NotConnectedError`). `cli.build_client` becomes a re-export (`from .upload import build_client`) and `cli.open_client` keeps calling the module-global `build_client`, so the existing monkeypatches in `tests/test_youtube_cli.py` and `tests/test_match_cli_export.py` still work.

- [ ] **Step 1: Write the failing test** (append to `tests/test_youtube_upload.py`)

```python
def test_connected_client_needs_a_stored_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.youtube import oauth
    from splitsmith.youtube.oauth import NotConnectedError

    with pytest.raises(NotConnectedError, match="youtube login"):
        upload.connected_client()
    oauth.save_connection(
        oauth.YouTubeConnection(
            refresh_token="rt", channel_id="c", channel_title="Chan", connected_at=datetime.now(UTC)
        )
    )
    client, conn = upload.connected_client()
    assert conn.channel_title == "Chan"
    assert isinstance(client, yt.YouTubeClient)
```

- [ ] **Step 2: Run it** -> FAIL with `AttributeError: module ... has no attribute 'connected_client'`.

- [ ] **Step 3: Implement.** In `upload.py`:

```python
from .client import UploadFailedError, VideoMetadata, YouTubeClient, default_http
from .oauth import AccessTokenProvider, NotConnectedError, OAuthClient, YouTubeConnection, YouTubeError, load_connection


def build_client(conn: YouTubeConnection) -> YouTubeClient:
    """A Data API client over the stored connection. One place so the CLI
    verbs and the UI job build it the same way."""
    client = OAuthClient.configured()
    http = default_http()
    return YouTubeClient(http, AccessTokenProvider(client, http, refresh_token=conn.refresh_token))


def connected_client() -> tuple[YouTubeClient, YouTubeConnection]:
    conn = load_connection()
    if conn is None:
        raise NotConnectedError("not connected to YouTube; run `splitsmith youtube login` first")
    return build_client(conn), conn
```

In `cli.py`: delete its `build_client` body, add `from .upload import build_client  # noqa: F401 -- re-exported for the tests' monkeypatch seam` and keep `open_client` as is (it calls `build_client(conn)` through the module global).

- [ ] **Step 4: Run** `uv run pytest tests/test_youtube_upload.py tests/test_youtube_cli.py tests/test_match_cli_export.py -n0 -q` -> all pass.

- [ ] **Step 5: Commit** `refactor(youtube): build_client lives in upload, not the CLI (#1000)`.

---

### Task 2: Settings routes and the connect state machine

**Files:**
- Create: `src/splitsmith/ui/youtube_api.py`
- Modify: `src/splitsmith/ui/server.py` (mount the router next to `device_router`, around line 16480)
- Test: `tests/test_youtube_api.py`

**Interfaces:**
- Produces:
  - `class YouTubeSettings(BaseModel)`: `configured: bool`, `connected: bool`, `channel_title: str | None`, `connected_at: datetime | None`.
  - `class ConnectStartResponse(BaseModel)`: `auth_url: str`, `expires_at: datetime`.
  - `class ConnectStatusResponse(BaseModel)`: `state: Literal["idle", "pending", "connected", "failed"]`, `channel_title: str | None = None`, `error: str | None = None`.
  - `class ConnectAttempt`: the in-process state (`state`, `auth_url`, `channel_title`, `error`, `thread`, `started_at`); `ConnectAttempt.start(timeout_s) -> ConnectAttempt` runs `oauth.connect` on a daemon thread with `open_browser=lambda url: None` and `on_auth_url` setting the URL and an `Event`; `start` waits up to 5 s for the URL.
  - `attempt_for(app) -> ConnectAttempt | None` / `set_attempt(app, attempt)` on `app.state.youtube_connect`.
  - Routes: `GET /api/settings/youtube`, `POST /api/settings/youtube/connect/start` (409 not configured; a second start while pending cancels nothing but replaces the attempt: the old thread's listener times out on its own), `GET /api/settings/youtube/connect/status`, `DELETE /api/settings/youtube/session`.
  - `_local_gate()`: 404 when `server._hosted_mode_active()`.
  - `router = APIRouter()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_youtube_api.py
"""The local server's YouTube surface (issue #1000, PR B): settings and the
connect state machine, the upload route and job, chaining from the match
export, and history enrichment. ``oauth.connect`` and ``upload_export``
are monkeypatched; nothing here reaches the network.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from splitsmith.youtube import oauth
from splitsmith.ui import youtube_api

from .test_ui_server import _match_create_app, _MatchClient


def _conn(title: str = "Chan") -> oauth.YouTubeConnection:
    return oauth.YouTubeConnection(
        refresh_token="rt", channel_id="c", channel_title=title, connected_at=datetime(2026, 9, 14, tzinfo=UTC)
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(oauth.ENV_CLIENT_ID, "cid")
    monkeypatch.setenv(oauth.ENV_CLIENT_SECRET, "sec")
    app = _match_create_app(project_root=tmp_path / "match", project_name="YT")
    return _MatchClient(app)


def test_settings_report_not_connected_then_connected(client) -> None:
    r = client.get("/api/settings/youtube")
    assert r.status_code == 200
    assert r.json() == {"configured": True, "connected": False, "channel_title": None, "connected_at": None}
    oauth.save_connection(_conn())
    body = client.get("/api/settings/youtube").json()
    assert body["connected"] is True and body["channel_title"] == "Chan"


def test_settings_report_unconfigured(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(oauth.ENV_CLIENT_ID)
    monkeypatch.setattr(oauth, "BUILTIN_CLIENT_ID", "")
    assert client.get("/api/settings/youtube").json()["configured"] is False
    assert client.post("/api/settings/youtube/connect/start").status_code == 409


def test_connect_start_returns_the_url_and_status_settles_connected(client, monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    def fake_connect(oauth_client: Any, http: Any, *, open_browser: Any, on_auth_url: Any, timeout_s: float) -> oauth.YouTubeConnection:
        on_auth_url("https://accounts.google.com/o/oauth2/v2/auth?state=x")
        release.wait(5.0)
        conn = _conn("Mine")
        oauth.save_connection(conn)
        return conn

    monkeypatch.setattr(youtube_api.oauth, "connect", fake_connect)
    r = client.post("/api/settings/youtube/connect/start")
    assert r.status_code == 200, r.text
    assert r.json()["auth_url"].startswith("https://accounts.google.com/")
    assert client.get("/api/settings/youtube/connect/status").json()["state"] == "pending"
    release.set()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        body = client.get("/api/settings/youtube/connect/status").json()
        if body["state"] != "pending":
            break
        time.sleep(0.05)
    assert body == {"state": "connected", "channel_title": "Mine", "error": None}
    assert client.get("/api/settings/youtube").json()["connected"] is True


def test_connect_failure_is_reported_with_its_reason(client, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_connect(*a: Any, on_auth_url: Any, **kw: Any) -> oauth.YouTubeConnection:
        on_auth_url("https://accounts.google.com/x")
        raise oauth.LoopbackError("Google reported access_denied")

    monkeypatch.setattr(youtube_api.oauth, "connect", fake_connect)
    client.post("/api/settings/youtube/connect/start")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        body = client.get("/api/settings/youtube/connect/status").json()
        if body["state"] != "pending":
            break
        time.sleep(0.05)
    assert body["state"] == "failed" and "access_denied" in body["error"]


def test_status_is_idle_before_any_attempt(client) -> None:
    assert client.get("/api/settings/youtube/connect/status").json()["state"] == "idle"


def test_delete_session_clears_the_store_and_revokes(client, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth.save_connection(_conn())
    revoked: list[str] = []
    monkeypatch.setattr(youtube_api.oauth, "revoke_token", lambda http, tok: revoked.append(tok))
    r = client.delete("/api/settings/youtube/session")
    assert r.status_code == 200 and r.json() == {"connected": False}
    assert revoked == ["rt"]
    assert oauth.load_connection() is None


def test_routes_404_in_hosted_mode(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import server as server_mod

    monkeypatch.setattr(server_mod, "_hosted_mode_active", lambda: True)
    for method, path in (("get", "/api/settings/youtube"), ("post", "/api/settings/youtube/connect/start"),
                         ("get", "/api/settings/youtube/connect/status"), ("delete", "/api/settings/youtube/session")):
        assert getattr(client, method)(path).status_code == 404, path
```

Check `_match_create_app` / `_MatchClient` names in `tests/test_ui_server.py` before running; use whatever the file exports for a local-mode match app and its client.

- [ ] **Step 2: Run** -> FAIL with `ImportError: cannot import name 'youtube_api'`.

- [ ] **Step 3: Implement `ui/youtube_api.py`**

```python
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
    from .server import _hosted_mode_active

    if _hosted_mode_active():
        raise HTTPException(status_code=404, detail="not found")


class YouTubeSettings(BaseModel):
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
    return ConnectStatusResponse(state=attempt.state, channel_title=attempt.channel_title, error=attempt.error)


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
```

Mount in `server.py` beside `device_router`:

```python
    from .youtube_api import router as youtube_router

    app.include_router(youtube_router)
```

- [ ] **Step 4: Run** `uv run pytest tests/test_youtube_api.py -n0 -q` -> 7 passed.

- [ ] **Step 5: Commit** `feat(ui): YouTube settings routes and the connect state machine (#1000)`.

---

### Task 3: Upload route and the `youtube_upload` job

**Files:**
- Modify: `src/splitsmith/ui/youtube_api.py`
- Modify: `src/splitsmith/ui/server.py` (register the body in `_register_job_bodies` next to `compare-grid`)
- Test: `tests/test_youtube_api.py`

**Interfaces:**
- Produces:
  - `class YouTubeUploadRequest(BaseModel)`: `filename: str`, `privacy: Literal["unlisted", "private", "public"] = "unlisted"`, `again: bool = False`.
  - `POST /api/shooters/{slug}/exports/youtube-upload` -> Job snapshot JSON. 400 when `filename` escapes `exports/`, is not `.mp4`, is missing, or has no sidecar; 409 when not connected.
  - `run_youtube_upload(handle: JobHandle, *, state: Any, slug: str, filename: str, privacy: str, again: bool) -> None`: `handle.update(progress=sent/total, message="Uploading 412 MB of 1.9 GB")`, `check_cancel=handle.check_cancel`, `handle.set_result({"video_id", "url", "notes"})`; an `AlreadyUploadedError` raises `RuntimeError(f"already uploaded: {url}")`.
  - `_format_mb(n: int) -> str` (`"412 MB"`, `"1.9 GB"`).
  - `exports_dir_for(state, slug) -> Path` = `state.shooter_project(slug).exports_path(state.shooter_root(slug)).resolve()`; `confine_export_filename(exports_dir, filename) -> Path` (the `download_export_file` rule, extracted).

- [ ] **Step 1: Write the failing tests** (append)

```python
def _seed_render(client_and_root, name: str = "bromma.mp4") -> Path:
    from splitsmith import youtube_sidecar

    _client, project_root = client_and_root
    exports = project_root / "shooters" / "me" / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    mp4 = exports / name
    mp4.write_bytes(b"\x00" * 2048)
    youtube_sidecar.write_sidecar(
        youtube_sidecar.YouTubeSidecar(title="Bromma", description="0:00 Stage 1", tags=["ipsc"]),
        youtube_sidecar.sidecar_path_for(mp4),
    )
    return mp4


@pytest.fixture
def export_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from .test_ui_server import _seed_match_export_project

    monkeypatch.setenv(oauth.ENV_CLIENT_ID, "cid")
    monkeypatch.setenv(oauth.ENV_CLIENT_SECRET, "sec")
    return _seed_match_export_project(tmp_path)


def _fake_upload(monkeypatch: pytest.MonkeyPatch, *, video_id: str = "vid1") -> dict[str, Any]:
    from splitsmith import youtube_sidecar
    from splitsmith.youtube import upload as upload_mod

    seen: dict[str, Any] = {}

    def fake_upload_export(mp4: Path, *, client: Any, privacy: str, channel_title: str = "", again: bool = False,
                           progress: Any = None, check_cancel: Any = None) -> youtube_sidecar.UploadRecord:
        seen.update(mp4=mp4, privacy=privacy, again=again)
        if progress:
            progress(1024, 2048)
            progress(2048, 2048)
        rec = youtube_sidecar.UploadRecord(video_id=video_id, url=f"https://youtu.be/{video_id}", privacy=privacy,
                                           uploaded_at=datetime.now(UTC), channel_title=channel_title)
        sc = youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(mp4))
        sc.upload = rec
        youtube_sidecar.write_sidecar(sc, youtube_sidecar.sidecar_path_for(mp4))
        return rec

    monkeypatch.setattr(youtube_api, "upload_export", fake_upload_export)
    monkeypatch.setattr(youtube_api, "connected_client", lambda: (object(), _conn("Mine")))
    return seen


def test_upload_route_runs_the_job_and_records_the_video(export_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import youtube_sidecar
    from .test_ui_server import _wait_for_job

    client, _root = export_client
    mp4 = _seed_render(export_client)
    oauth.save_connection(_conn("Mine"))
    seen = _fake_upload(monkeypatch)
    r = client.post("/api/shooters/me/exports/youtube-upload", json={"filename": "bromma.mp4", "privacy": "public"})
    assert r.status_code == 200, r.text
    final = _wait_for_job(client, r.json()["id"])
    assert final["status"] == "succeeded", final
    assert final["result"]["url"] == "https://youtu.be/vid1"
    assert final["progress"] == 1.0
    assert seen["privacy"] == "public" and seen["mp4"] == mp4.resolve() and seen["again"] is False
    assert youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(mp4)).upload.video_id == "vid1"


def test_upload_route_refusals(export_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, root = export_client
    _seed_render(export_client)
    assert client.post("/api/shooters/me/exports/youtube-upload", json={"filename": "bromma.mp4"}).status_code == 409
    oauth.save_connection(_conn())
    _fake_upload(monkeypatch)
    for bad in ("../bromma.mp4", "bromma.fcpxml", "missing.mp4"):
        r = client.post("/api/shooters/me/exports/youtube-upload", json={"filename": bad})
        assert r.status_code == 400, (bad, r.text)
    bare = root / "shooters" / "me" / "exports" / "bare.mp4"
    bare.write_bytes(b"0")
    r = client.post("/api/shooters/me/exports/youtube-upload", json={"filename": "bare.mp4"})
    assert r.status_code == 400 and "sidecar" in r.json()["detail"]


def test_upload_job_reports_already_uploaded_as_a_failure_with_the_url(export_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import youtube_sidecar
    from splitsmith.youtube.upload import AlreadyUploadedError
    from .test_ui_server import _wait_for_job

    client, _root = export_client
    _seed_render(export_client)
    oauth.save_connection(_conn())
    rec = youtube_sidecar.UploadRecord(video_id="old", url="https://youtu.be/old", privacy="unlisted", uploaded_at=datetime.now(UTC))

    def refuse(*a: Any, **kw: Any) -> None:
        raise AlreadyUploadedError(rec)

    monkeypatch.setattr(youtube_api, "upload_export", refuse)
    monkeypatch.setattr(youtube_api, "connected_client", lambda: (object(), _conn()))
    r = client.post("/api/shooters/me/exports/youtube-upload", json={"filename": "bromma.mp4"})
    final = _wait_for_job(client, r.json()["id"])
    assert final["status"] == "failed" and "https://youtu.be/old" in final["error"]


def test_format_mb() -> None:
    assert youtube_api._format_mb(412 * 1024 * 1024) == "412 MB"
    assert youtube_api._format_mb(int(1.9 * 1024**3)) == "1.9 GB"
```

- [ ] **Step 2: Run** -> the four new tests FAIL (404 on the route / missing names).

- [ ] **Step 3: Implement.** Add to `youtube_api.py`:

```python
from pathlib import Path

from .. import youtube_sidecar
from ..youtube.upload import AlreadyUploadedError, connected_client, upload_export


class YouTubeUploadRequest(BaseModel):
    filename: str
    privacy: Literal["unlisted", "private", "public"] = "unlisted"
    again: bool = False


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
    _local_gate()
    state = request.app.state.splitsmith_state
    if oauth.load_connection() is None:
        raise HTTPException(status_code=409, detail="not connected to YouTube")
    exports_dir = exports_dir_for(state, slug)
    target = confine_export_filename(exports_dir, req.filename)
    if target.suffix.lower() != ".mp4":
        raise HTTPException(status_code=400, detail="only a rendered .mp4 can be uploaded")
    if not target.exists():
        raise HTTPException(status_code=400, detail=f"{req.filename} is not in exports/")
    if not youtube_sidecar.sidecar_path_for(target).exists():
        raise HTTPException(status_code=400, detail=f"{req.filename} has no -youtube.json sidecar beside it")
    job = await state.jobs.submit(
        kind="youtube_upload",
        shooter_slug=slug,
        args={"slug": slug, "filename": req.filename, "privacy": req.privacy, "again": req.again},
    )
    return JSONResponse(job.model_dump(mode="json"))


def run_youtube_upload(handle: Any, *, state: Any, slug: str, filename: str, privacy: str, again: bool) -> None:
    """Job body for ``youtube_upload``. Progress is bytes sent; a cancel
    lands between chunks through ``handle.check_cancel``."""
    handle.update(progress=0.0, message="Connecting to YouTube...")
    client, conn = connected_client()
    mp4 = confine_export_filename(exports_dir_for(state, slug), filename)
    total = mp4.stat().st_size

    def on_progress(sent: int, _total: int) -> None:
        handle.update(progress=sent / max(1, total), message=f"Uploading {_format_mb(sent)} of {_format_mb(total)}")

    try:
        record = upload_export(
            mp4,
            client=client,
            privacy=privacy,  # type: ignore[arg-type]
            channel_title=conn.channel_title,
            again=again,
            progress=on_progress,
            check_cancel=handle.check_cancel,
        )
    except AlreadyUploadedError as exc:
        raise RuntimeError(f"already uploaded: {exc.record.url}") from exc
    handle.set_result({"video_id": record.video_id, "url": record.url, "notes": record.notes})
    handle.update(progress=1.0, message=f"Uploaded {record.url}")
```

Import `JSONResponse` from `fastapi.responses`. Register in `server.py`'s body registration block:

```python
    from .youtube_api import run_youtube_upload

    state.jobs.bodies.register("youtube_upload", functools.partial(run_youtube_upload, state=state))
```

Check how `_priority_for_kind` in `jobs.py` treats unknown kinds; if it needs an entry for a long-running kind, add `youtube_upload` alongside `match_export`.

- [ ] **Step 4: Run** `uv run pytest tests/test_youtube_api.py -n0 -q` -> 11 passed.

- [ ] **Step 5: Commit** `feat(ui): youtube_upload job and its route (#1000)`.

---

### Task 4: Chain the upload from the match export

**Files:**
- Modify: `src/splitsmith/ui/exports_api.py` (`MatchExportRequest`)
- Modify: `src/splitsmith/ui/server.py` (`_run_match_export` tail)
- Test: `tests/test_youtube_api.py`

**Interfaces:**
- `MatchExportRequest.youtube_upload: bool = False`, `youtube_privacy: Literal["unlisted", "private", "public"] = "unlisted"`; a `model_validator(mode="after")` raises `ValueError("youtube_upload needs youtube_sidecar and output_format mp4")` otherwise (FastAPI turns it into a 422).

- [ ] **Step 1: Write the failing tests** (append)

```python
def _match_export_body(**over: Any) -> dict[str, Any]:
    body = {"stage_numbers": [1, 2], "head_pad_seconds": 0.5, "tail_pad_seconds": 1.0,
            "include_secondaries": False, "include_overlay": False}
    body.update(over)
    return body


def test_youtube_upload_needs_mp4_and_sidecar(export_client) -> None:
    client, _root = export_client
    r = client.post("/api/shooters/me/export/match", json=_match_export_body(youtube_upload=True))
    assert r.status_code == 422
    r = client.post("/api/shooters/me/export/match", json=_match_export_body(youtube_upload=True, output_format="mp4"))
    assert r.status_code == 422


def test_match_export_chains_an_upload_job(export_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import server as server_mod
    from .test_ui_server import _stub_match_export_probe, _wait_for_job, _wait_for_jobs_to_drain

    client, _root = export_client
    _stub_match_export_probe(monkeypatch)
    oauth.save_connection(_conn("Mine"))
    seen = _fake_upload(monkeypatch, video_id="chained")
    # The MP4 renderer is stubbed the way tests/test_match_cli_export.py does it
    from splitsmith.ui import match_exports as match_exports_mod
    from splitsmith import mp4_render, youtube_sidecar

    def fake_render_mp4(comp, *, output_path, **kwargs):  # type: ignore[no-untyped-def]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x00" * 100)
        return mp4_render.Mp4RenderResult(output_path=output_path, duration_seconds=4.0)

    monkeypatch.setattr(match_exports_mod.mp4_render, "render_mp4", fake_render_mp4)
    monkeypatch.setattr(youtube_sidecar, "write_thumbnail", lambda *a, **k: None)

    r = client.post("/api/shooters/me/export/match", json=_match_export_body(
        output_format="mp4", youtube_sidecar=True, youtube_upload=True, youtube_privacy="private"))
    assert r.status_code == 200, r.text
    final = _wait_for_job(client, r.json()["id"])
    assert final["status"] == "succeeded", final
    jobs = _wait_for_jobs_to_drain(client)
    uploads = [j for j in jobs if j["kind"] == "youtube_upload"]
    assert len(uploads) == 1 and uploads[0]["status"] == "succeeded"
    assert seen["privacy"] == "private"
    assert seen["mp4"].name.endswith(".mp4")
    assert uploads[0]["result"]["url"] == "https://youtu.be/chained"


def test_match_export_without_the_flag_chains_nothing(export_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_ui_server import _stub_match_export_probe, _wait_for_job, _wait_for_jobs_to_drain

    client, _root = export_client
    _stub_match_export_probe(monkeypatch)
    r = client.post("/api/shooters/me/export/match", json=_match_export_body())
    _wait_for_job(client, r.json()["id"])
    assert [j for j in _wait_for_jobs_to_drain(client) if j["kind"] == "youtube_upload"] == []
```

Read `_wait_for_jobs_to_drain` in `tests/test_ui_server.py` first; if it returns only active jobs, use `client.get("/api/jobs")` and filter instead.

- [ ] **Step 2: Run** -> FAIL (422 not raised; no chained job).

- [ ] **Step 3: Implement.** In `exports_api.py`, after `summary_hold_seconds`:

```python
    # Issue #1000. Chain a ``youtube_upload`` job onto this export. Needs
    # the sidecar (it is the upload's metadata) and a rendered MP4.
    youtube_upload: bool = False
    youtube_privacy: Literal["unlisted", "private", "public"] = "unlisted"

    @model_validator(mode="after")
    def _youtube_upload_needs_an_mp4_and_a_sidecar(self) -> MatchExportRequest:
        if self.youtube_upload and not (self.youtube_sidecar and self.output_format == "mp4"):
            raise ValueError("youtube_upload needs youtube_sidecar and output_format mp4")
        return self
```

(`from pydantic import BaseModel, Field, model_validator`.) In `_run_match_export`, after the final `handle.update(progress=1.0, ...)`:

```python
        # Issue #1000: the upload is its own job so it has its own
        # progress and cancel, and so a failed upload never marks a
        # finished render as failed. Same thread-to-loop bridge as
        # ``_run_trim``'s shot_detect chain.
        if req.youtube_upload and result.fcpxml_path.suffix.lower() == ".mp4":
            asyncio.run(
                state.jobs.submit(
                    kind="youtube_upload",
                    shooter_slug=slug,
                    args={
                        "slug": slug,
                        "filename": result.fcpxml_path.name,
                        "privacy": req.youtube_privacy,
                        "again": True,
                    },
                )
            )
```

`again=True` for the same reason as the CLI: the render just rewrote the sidecar.

- [ ] **Step 4: Run** `uv run pytest tests/test_youtube_api.py tests/test_export_run_record.py -n0 -q` -> all pass.

- [ ] **Step 5: Commit** `feat(ui): match export chains a youtube_upload job (#1000)`.

---

### Task 5: History enrichment

**Files:**
- Modify: `src/splitsmith/ui/exports_api.py` (`list_export_runs`)
- Test: `tests/test_youtube_api.py`

**Interfaces:**
- Each run in `GET /api/shooters/{slug}/exports/runs` gains `youtube: {video_id, url, privacy, uploaded_at} | null`.
- `youtube_record_for(exports_dir: Path, artifacts: list[dict]) -> dict | None` in `exports_api.py`: the first artifact whose filename ends in `-youtube.json`, pulled through `export_storage.pull_export_file` (no-op locally), parsed with `youtube_sidecar.load_sidecar`; any failure reads as `None`.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_history_rows_carry_the_sidecars_upload_record(export_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import export_runs, youtube_sidecar
    from splitsmith.ui import server as server_mod

    client, root = export_client
    mp4 = _seed_render(export_client)
    sc_path = youtube_sidecar.sidecar_path_for(mp4)
    sc = youtube_sidecar.load_sidecar(sc_path)
    sc.upload = youtube_sidecar.UploadRecord(video_id="v9", url="https://youtu.be/v9", privacy="unlisted",
                                             uploaded_at=datetime(2026, 9, 14, tzinfo=UTC))
    youtube_sidecar.write_sidecar(sc, sc_path)
    (root / "shooters" / "me" / "exports" / "other-youtube.json").write_text("{not json", encoding="utf-8")
    log = {"schema_version": 1, "runs": [
        export_runs.ExportRun(run_id="a", kind="match", finished_at=datetime.now(UTC), duration_seconds=1.0,
                              stage_numbers=[1], formats=["mp4"], anomaly_count=0,
                              artifacts=[export_runs.ExportArtifact(filename="bromma.mp4", kind="match_video"),
                                         export_runs.ExportArtifact(filename="bromma-youtube.json", kind="sidecar")]).model_dump(mode="json"),
        export_runs.ExportRun(run_id="b", kind="match", finished_at=datetime.now(UTC), duration_seconds=1.0,
                              stage_numbers=[1], formats=["mp4"], anomaly_count=0,
                              artifacts=[export_runs.ExportArtifact(filename="other-youtube.json", kind="sidecar")]).model_dump(mode="json"),
        export_runs.ExportRun(run_id="c", kind="stage", finished_at=datetime.now(UTC), duration_seconds=1.0,
                              stage_numbers=[1], formats=["trim"], anomaly_count=0, artifacts=[]).model_dump(mode="json"),
    ]}
    (root / "shooters" / "me" / "export_runs.json").write_text(json.dumps(log), encoding="utf-8")
    rows = client.get("/api/shooters/me/exports/runs").json()["runs"]
    by_id = {r["run_id"]: r for r in rows}
    assert by_id["a"]["youtube"] == {"video_id": "v9", "url": "https://youtu.be/v9", "privacy": "unlisted",
                                     "uploaded_at": "2026-09-14T00:00:00Z"}
    assert by_id["b"]["youtube"] is None  # unparseable sidecar never 500s
    assert by_id["c"]["youtube"] is None
```

Confirm the local export-runs file name (`export_runs.json` under the shooter root) against `tests/test_export_run_record.py::test_local_mode_appends_to_a_file_in_the_shooter_root`.

- [ ] **Step 2: Run** -> FAIL with `KeyError: 'youtube'`.

- [ ] **Step 3: Implement** in `exports_api.py`:

```python
def youtube_record_for(exports_dir: Path, artifacts: list[dict]) -> dict | None:
    """The sidecar's ``upload`` block for a run, or ``None``. Read per
    request like ``available``; never raises, a bad sidecar is ``None``."""
    for artifact in artifacts:
        name = str(artifact.get("filename", ""))
        if not name.endswith("-youtube.json"):
            continue
        path = exports_dir / name
        try:
            export_storage.pull_export_file_by_path(path)  # see note below
            record = youtube_sidecar.load_sidecar(path).upload
        except Exception:  # noqa: BLE001 -- history never 500s over bookkeeping
            return None
        if record is None:
            return None
        return {"video_id": record.video_id, "url": record.url, "privacy": record.privacy,
                "uploaded_at": record.uploaded_at.isoformat().replace("+00:00", "Z")}
    return None
```

`export_storage.pull_export_file(proj, path)` takes the project; call it with `state.shooter_project(slug)` from `list_export_runs` and pass a `pull: Callable[[Path], None]` into `youtube_record_for` instead of the placeholder line above. In `list_export_runs`, after the `available` loop: `row["youtube"] = youtube_record_for(exports_dir, row["artifacts"], pull=lambda p: export_storage.pull_export_file(project, p))`. Use `model_dump(mode="json")` on the record instead of hand-building the dict if the datetime serialises as `...Z` (pydantic emits `Z` for UTC); assert the test's expected string either way.

- [ ] **Step 4: Run** `uv run pytest tests/test_youtube_api.py tests/test_export_run_record.py -n0 -q` -> pass.

- [ ] **Step 5: Commit** `feat(ui): export history rows carry the YouTube upload record (#1000)`.

---

### Task 6: SPA types, calls and `lib/youtubeRows.ts`

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts`
- Create: `src/splitsmith/ui_static/src/lib/youtubeRows.ts`
- Test: `src/splitsmith/ui_static/src/lib/youtubeRows.test.ts`

**Interfaces:**
- `api.ts`: `export type YouTubePrivacy = "unlisted" | "private" | "public"`; `interface YouTubeSettings { configured; connected; channel_title: string | null; connected_at: string | null }`; `interface YouTubeConnectStatus { state: "idle" | "pending" | "connected" | "failed"; channel_title: string | null; error: string | null }`; `interface ExportRunYouTube { video_id; url; privacy; uploaded_at }`; `ExportRun.youtube?: ExportRunYouTube | null`; `MatchExportRequestPayload.youtube_upload?: boolean; youtube_privacy?: YouTubePrivacy` and the two explicit spreads in `exportMatch`; `api.getYouTubeSettings()`, `api.startYouTubeConnect() -> {auth_url, expires_at}`, `api.youtubeConnectStatus()`, `api.disconnectYouTube()`, `api.uploadToYouTube(slug, {filename, privacy, again}) -> Job`.
- `youtubeRows.ts`:
  ```ts
  export function uploadableArtifact(run: ExportRun): string | null  // the .mp4 filename when an available match_video and its available "<stem>-youtube.json" are both listed
  export function youtubeLink(run: ExportRun): { href: string; label: string } | null  // label "youtu.be/<id>"
  export function uploadLabel(run: ExportRun): "Upload to YouTube" | "Upload again"
  export function rowPrivacy(control: "off" | YouTubePrivacy): YouTubePrivacy  // "off" -> "unlisted"
  ```

- [ ] **Step 1: Write the failing tests**

```ts
// lib/youtubeRows.test.ts
import { describe, expect, it } from "vitest";
import type { ExportRun } from "@/lib/api";
import { rowPrivacy, uploadLabel, uploadableArtifact, youtubeLink } from "@/lib/youtubeRows";

function run(over: Partial<ExportRun> = {}): ExportRun {
  return {
    run_id: "r", kind: "match", finished_at: "2026-09-14T00:00:00Z", duration_seconds: 1, stage_numbers: [1],
    formats: ["mp4", "youtube-sidecar"], anomaly_count: 0,
    artifacts: [
      { filename: "bromma.mp4", kind: "match_video", available: true },
      { filename: "bromma-youtube.json", kind: "sidecar", available: true },
      { filename: "bromma.srt", kind: "sidecar", available: true },
    ],
    youtube: null,
    ...over,
  };
}

describe("uploadableArtifact", () => {
  it("names the mp4 when it and its sidecar are present", () => {
    expect(uploadableArtifact(run())).toBe("bromma.mp4");
  });
  it("is null without the sidecar, without the mp4, or when either file is gone", () => {
    expect(uploadableArtifact(run({ artifacts: [{ filename: "bromma.mp4", kind: "match_video", available: true }] }))).toBeNull();
    expect(uploadableArtifact(run({ artifacts: [{ filename: "bromma-youtube.json", kind: "sidecar", available: true }] }))).toBeNull();
    expect(uploadableArtifact(run({ artifacts: [
      { filename: "bromma.mp4", kind: "match_video", available: false },
      { filename: "bromma-youtube.json", kind: "sidecar", available: true },
    ] }))).toBeNull();
    expect(uploadableArtifact(run({ artifacts: [
      { filename: "bromma.fcpxml", kind: "fcpxml", available: true },
      { filename: "bromma-youtube.json", kind: "sidecar", available: true },
    ] }))).toBeNull();
  });
});

describe("youtubeLink / uploadLabel / rowPrivacy", () => {
  it("links the recorded video and switches the label", () => {
    expect(youtubeLink(run())).toBeNull();
    expect(uploadLabel(run())).toBe("Upload to YouTube");
    const done = run({ youtube: { video_id: "abc", url: "https://youtu.be/abc", privacy: "unlisted", uploaded_at: "2026-09-14T00:00:00Z" } });
    expect(youtubeLink(done)).toEqual({ href: "https://youtu.be/abc", label: "youtu.be/abc" });
    expect(uploadLabel(done)).toBe("Upload again");
  });
  it("row privacy follows the form control, unlisted when off", () => {
    expect(rowPrivacy("off")).toBe("unlisted");
    expect(rowPrivacy("public")).toBe("public");
  });
});
```

- [ ] **Step 2: Run** -> FAIL (module missing).

- [ ] **Step 3: Implement** `youtubeRows.ts`:

```ts
/** Derivations for the YouTube controls on export-history rows. Pure:
 *  the components map these onto primitives. */
import type { ExportRun, YouTubePrivacy } from "@/lib/api";

export function uploadableArtifact(run: ExportRun): string | null {
  const mp4 = run.artifacts.find((a) => a.kind === "match_video" && a.available && a.filename.toLowerCase().endsWith(".mp4"));
  if (!mp4) return null;
  const stem = mp4.filename.slice(0, -4);
  const sidecar = run.artifacts.find((a) => a.available && a.filename === `${stem}-youtube.json`);
  return sidecar ? mp4.filename : null;
}

export function youtubeLink(run: ExportRun): { href: string; label: string } | null {
  const y = run.youtube;
  if (!y) return null;
  return { href: y.url, label: `youtu.be/${y.video_id}` };
}

export function uploadLabel(run: ExportRun): "Upload to YouTube" | "Upload again" {
  return run.youtube ? "Upload again" : "Upload to YouTube";
}

export function rowPrivacy(control: "off" | YouTubePrivacy): YouTubePrivacy {
  return control === "off" ? "unlisted" : control;
}
```

Add the `api.ts` types and calls following the `getSyncSettings` / `startDeviceLogin` block's style (`request<T>(path, { method, body })`). In `exportMatch`, add the two explicit spreads next to `youtube_preset`.

- [ ] **Step 4: Run** the vitest file and `typecheck` -> pass.

- [ ] **Step 5: Commit** `feat(spa): YouTube api calls and history-row derivations (#1000)`.

---

### Task 7: `YouTubeConnect` component

**Files:**
- Create: `src/splitsmith/ui_static/src/components/export/YouTubeConnect.tsx`
- Test: `src/splitsmith/ui_static/src/components/export/YouTubeConnect.test.tsx`

**Interfaces:**
```ts
export interface YouTubeConnectProps {
  settings: YouTubeSettings | null;           // null while loading
  onSettingsChange: () => void;               // parent refetches settings
  uploadAfterRender: "off" | YouTubePrivacy;
  onUploadAfterRenderChange: (v: "off" | YouTubePrivacy) => void;
  showUploadControl: boolean;                 // renderedMp4 && youtube
  busy?: boolean;
}
```
States: not configured -> one muted line "YouTube upload is not configured on this install." Not connected -> `Button` (default) "Connect YouTube"; on click: `api.startYouTubeConnect()`, `window.open(auth_url, "_blank", "noopener")`, poll `api.youtubeConnectStatus()` every 2 s until not pending (or 10 min), then `onSettingsChange()`; pending shows "Waiting for Google..." and a Cancel that stops polling. Failed -> the reason in destructive text and the Connect button again. Connected -> "Connected as <channel_title>" with a `Menu` holding "Disconnect" (calls `api.disconnectYouTube()` then `onSettingsChange()`), and when `showUploadControl`, `Segmented` "Upload after render" with Off / Unlisted / Private / Public.

- [ ] **Step 1: Write the failing tests** (four states, the poll settling to connected, disconnect). Use `vi.spyOn(api, ...)` the way `DeviceLoginDialog.test.tsx` mocks the device flow, `vi.useFakeTimers()` for the poll, and stub `window.open` with `vi.fn()`. Assert: the Connect button is not `variant="primary"` (check the class the primary variant applies in `components/ui/button.tsx` and assert its absence), the opened URL equals `auth_url`, "Connected as Mine" appears after the status flips, and `onSettingsChange` was called once.

- [ ] **Step 2: Run** -> FAIL.

- [ ] **Step 3: Implement** the component with `useEffect` cleanup that clears the poll interval on unmount; the Segmented's `label` prop is "Upload after render"; no colour outside the primitives; text through `text-md` / `text-sm` classes and `text-muted` / `text-ink-2` tokens; error text `text-destructive`.

- [ ] **Step 4: Run** the vitest file, `lint`, `typecheck` -> pass.

- [ ] **Step 5: Commit** `feat(spa): YouTubeConnect row for the Export page (#1000)`.

---

### Task 8: Wire the Export page and the history rows

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Export.tsx`
- Modify: `src/splitsmith/ui_static/src/components/export/ExportHistory.tsx`
- Test: `src/splitsmith/ui_static/src/components/export/ExportHistory.test.tsx`, `src/splitsmith/ui_static/src/pages/Export.renderOptions.test.tsx` (extend), `src/splitsmith/ui_static/src/pages/Export.youtube.test.tsx` (new)

**Interfaces:**
- `ExportHistory` props gain `youtube?: { connected: boolean; onUpload: (filename: string, again: boolean) => void; busyFilename?: string | null }`. A row with `uploadableArtifact(run)` and `youtube.connected` renders a `Button` (default, size sm) with `uploadLabel(run)`; when `run.youtube` is set the link `youtubeLink(run)` renders as an `<a target="_blank" rel="noopener">` and "Upload again" moves into a `Menu` behind an overflow button, so a finished row's one visible control is the link.
- `Export.tsx`: `youtubeSettings` state loaded in `reload()` (local mode only: `useDeploymentMode().mode === "local"`), `uploadAfterRender` state (`"off"`), `YouTubeConnect` mounted inside the existing YouTube `Field` under the Segmented + textarea, `submitBundle` sends `youtube_upload: renderedMp4 && youtube && uploadAfterRender !== "off"` and `youtube_privacy: rowPrivacy(uploadAfterRender)`, and an `onUpload` handler that calls `api.uploadToYouTube(slug, { filename, privacy: rowPrivacy(uploadAfterRender), again })`, sets the job into the existing `job` state so the rail shows it, polls with `api.pollJob`, and `reload()`s on completion.

- [ ] **Step 1: Write the failing tests**: `ExportHistory` renders the upload button only when connected and uploadable, renders the `youtu.be/<id>` link with the right href, and the menu's "Upload again" calls `onUpload(filename, true)`. `Export.youtube.test.tsx`: with settings connected and `uploadAfterRender` set to Private through the Segmented, `api.exportMatch` receives `youtube_upload: true, youtube_privacy: "private"`; with it Off, `youtube_upload: false`. Extend the existing payload assertions in `Export.renderOptions.test.tsx` (lines ~238 and ~272) with `youtube_upload: false, youtube_privacy: "unlisted"` so the "no hardcoded literal" contract holds.

- [ ] **Step 2: Run** -> FAIL.

- [ ] **Step 3: Implement.** Keep the page's one `primary` on the Export button. History refetch: after an upload job settles, `reload()`; `ExportHistory` already re-renders from `runs`.

- [ ] **Step 4: Run** the four vitest files, `lint`, `typecheck`, `build` -> pass.

- [ ] **Step 5: Commit** `feat(spa): Export page connects YouTube and uploads from the history (#1000)`.

---

### Task 9: Visual check and docs

**Files:**
- Modify: `CLAUDE.md` (the "YouTube upload (#1000)" section: add the server routes, the job kind, and the seam sentence: "a new upload option belongs on `YouTubeConnect`, a new per-run action on `ExportHistory` through `lib/youtubeRows`")
- Modify: `docs/COMMANDS.md` (one line under `## youtube`: the Export page's Connect and Upload controls do the same thing)

- [ ] **Step 1: Seed and run the app**: `uv run python scripts/seed_demo_match.py ~/.claude-tmp/yt-ui --media`, `uv run splitsmith match trims ~/.claude-tmp/yt-ui --shooter s_demo0001`, then `SPLITSMITH_YOUTUBE_CLIENT_ID=... SPLITSMITH_YOUTUBE_CLIENT_SECRET=... uv run splitsmith ui --project ~/.claude-tmp/yt-ui --skip-system-check --no-browser --port 5174` (env from `.env.local`).
- [ ] **Step 2: Screenshot the Export page's YouTube row** with Playwright in each state: not connected (delete `~/.splitsmith/youtube.json` under the test `SPLITSMITH_HOME`), pending (click Connect, do not approve), connected (approve in the browser, or copy a real `youtube.json` in), and a history row after a real private upload of a two-stage MP4. Look at every frame; the row must sit inside the existing YouTube `Field` with no second card level.
- [ ] **Step 3: Delete the test video** through the API or Studio, write the docs, run the whole touched Python and SPA test set once more.
- [ ] **Step 4: Commit** `docs: YouTube upload from the Export page (#1000)` and open the PR with the screenshots attached and the real-upload result stated.
