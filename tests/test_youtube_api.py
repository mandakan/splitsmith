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

from splitsmith.ui import youtube_api
from splitsmith.youtube import oauth

from .test_ui_server import _match_create_app, _MatchClient


def _conn(title: str = "Chan") -> oauth.YouTubeConnection:
    return oauth.YouTubeConnection(
        refresh_token="rt",
        channel_id="c",
        channel_title=title,
        connected_at=datetime(2026, 9, 14, tzinfo=UTC),
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(oauth.ENV_CLIENT_ID, "cid")
    monkeypatch.setenv(oauth.ENV_CLIENT_SECRET, "sec")
    app = _match_create_app(project_root=tmp_path / "match", project_name="YT")
    return _MatchClient(app)


def _settle(client: Any) -> dict[str, Any]:
    deadline = time.monotonic() + 5.0
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = client.get("/api/settings/youtube/connect/status").json()
        if body["state"] != "pending":
            return body
        time.sleep(0.05)
    return body


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


def test_connect_start_returns_the_url_and_status_settles_connected(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def fake_connect(
        oauth_client: Any, http: Any, *, open_browser: Any, on_auth_url: Any, timeout_s: float
    ) -> oauth.YouTubeConnection:
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
    assert _settle(client) == {"state": "connected", "channel_title": "Mine", "error": None}
    assert client.get("/api/settings/youtube").json()["connected"] is True


def test_connect_failure_is_reported_with_its_reason(client, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_connect(*a: Any, on_auth_url: Any, **kw: Any) -> oauth.YouTubeConnection:
        on_auth_url("https://accounts.google.com/x")
        raise oauth.LoopbackError("Google reported access_denied")

    monkeypatch.setattr(youtube_api.oauth, "connect", fake_connect)
    client.post("/api/settings/youtube/connect/start")
    body = _settle(client)
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
    for method, path in (
        ("get", "/api/settings/youtube"),
        ("post", "/api/settings/youtube/connect/start"),
        ("get", "/api/settings/youtube/connect/status"),
        ("delete", "/api/settings/youtube/session"),
    ):
        assert getattr(client, method)(path).status_code == 404, path
