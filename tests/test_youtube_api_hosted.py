"""The YouTube surface in hosted mode (issue #1000, phase 2).

Two harnesses. ``hosted_app`` (magic-link login, no storage) drives the
settings and connect state machine: the row-backed ``pending``, Google's
redirect callback, the settings after a connect, isolation between two
signed-in users. ``hosted_client_with_match`` (moto bucket, a real
create-match) drives the upload route and the job body against a render
that exists only in storage. Google is never reached: ``exchange_code``,
``build_connection``, ``revoke_token`` and ``upload_export`` are
monkeypatched on the modules the routes import them from.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from splitsmith import youtube_sidecar
from splitsmith.db import User, create_engine, sessionmaker
from splitsmith.ui import youtube_api
from splitsmith.youtube import oauth, sealed
from tests.hosted_helpers import login  # hosted_app / hosted_env are registered in conftest

from .test_hosted_compare_grid import inline_deferrer  # noqa: F401
from .test_hosted_raw_upload import hosted_client, hosted_client_with_match, hosted_db  # noqa: F401

SETTINGS = "/api/settings/youtube"
START = f"{SETTINGS}/connect/start"
STATUS = f"{SETTINGS}/connect/status"
CALLBACK = youtube_api.CALLBACK_PATH


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(oauth.ENV_CLIENT_ID, "cid")
    monkeypatch.setenv(oauth.ENV_CLIENT_SECRET, "sec")
    monkeypatch.setenv(sealed.ENV_TOKEN_KEY, sealed.generate_key())


def _conn(title: str = "Chan") -> oauth.YouTubeConnection:
    return oauth.YouTubeConnection(
        refresh_token="1//rt-secret",
        channel_id="UC1",
        channel_title=title,
        connected_at=datetime(2026, 9, 19, tzinfo=UTC),
    )


def _fake_google(monkeypatch: pytest.MonkeyPatch, *, title: str = "Chan") -> dict[str, Any]:
    """Stand in for the token exchange and the channel lookup."""
    seen: dict[str, Any] = {}

    def fake_exchange(client: Any, http: Any, *, code: str, redirect_uri: str, code_verifier: str) -> Any:
        seen.update(code=code, redirect_uri=redirect_uri, code_verifier=code_verifier)
        return oauth.TokenResponse(
            access_token="at", expires_in=3600, refresh_token="1//rt-secret", scope=oauth.SCOPE
        )

    monkeypatch.setattr(oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(oauth, "build_connection", lambda http, tok: _conn(title))
    return seen


def _start(client: TestClient) -> tuple[str, str]:
    r = client.post(START)
    assert r.status_code == 200, r.text
    url = r.json()["auth_url"]
    q = parse_qs(urlparse(url).query)
    return url, q["state"][0]


def _raw_column(db_url: str, email: str) -> dict | None:
    factory = sessionmaker(create_engine(db_url))

    async def _go() -> dict | None:
        async with factory() as s:
            return (await s.execute(select(User.youtube_connection).where(User.email == email))).scalar_one()

    return asyncio.run(_go())


# --- settings and the connect state machine --------------------------------


def test_settings_need_a_session(hosted_app) -> None:
    client, _ = hosted_app
    assert client.get(SETTINGS).status_code in (401, 403)


def test_unconfigured_without_the_token_key(hosted_app, monkeypatch: pytest.MonkeyPatch) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    monkeypatch.setenv(oauth.ENV_CLIENT_ID, "cid")
    monkeypatch.setenv(oauth.ENV_CLIENT_SECRET, "sec")
    monkeypatch.delenv(sealed.ENV_TOKEN_KEY, raising=False)
    body = client.get(SETTINGS).json()
    assert body == {"configured": False, "connected": False, "channel_title": None, "connected_at": None}
    assert client.post(START).status_code == 409


def test_start_records_pending_and_points_google_at_the_public_callback(
    hosted_app, hosted_env, configured
) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    assert client.get(STATUS).json()["state"] == "idle"
    url, state = _start(client)
    q = parse_qs(urlparse(url).query)
    assert q["redirect_uri"] == [f"http://localhost:5174{CALLBACK}"]
    assert q["client_id"] == ["cid"]
    assert q["code_challenge_method"] == ["S256"]
    assert client.get(STATUS).json() == {"state": "pending", "channel_title": None, "error": None}
    row = _raw_column(hosted_env, "a@example.com")
    assert row["pending"]["state"] == state
    assert "connection" not in row


def test_callback_with_the_wrong_state_settles_failed(hosted_app, configured, monkeypatch) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    google = _fake_google(monkeypatch)
    _start(client)
    r = client.get(CALLBACK, params={"code": "c", "state": "forged"})
    assert r.status_code == 200 and "did not match" in r.text
    assert google == {}
    status = client.get(STATUS).json()
    assert status["state"] == "failed" and "did not match" in status["error"]
    assert client.get(SETTINGS).json()["connected"] is False


def test_callback_carrying_googles_error_settles_failed(hosted_app, configured) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    _, state = _start(client)
    r = client.get(CALLBACK, params={"error": "access_denied", "state": state})
    assert r.status_code == 200
    assert client.get(STATUS).json()["error"] == "Google did not complete the login: access_denied"


def test_callback_without_a_pending_login_says_so(hosted_app, configured) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    r = client.get(CALLBACK, params={"code": "c", "state": "s"})
    assert r.status_code == 200 and "No YouTube login in progress" in r.text
    assert client.get(STATUS).json()["state"] == "idle"


def test_expired_pending_is_idle_and_its_callback_fails(hosted_app, configured, monkeypatch) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    _, state = _start(client)
    later = datetime.now(UTC) + timedelta(seconds=youtube_api.CONNECT_TIMEOUT_S + 1)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return later

    monkeypatch.setattr(youtube_api, "datetime", _Clock)
    assert client.get(STATUS).json()["state"] == "idle"
    r = client.get(CALLBACK, params={"code": "c", "state": state})
    assert "timed out" in r.text
    assert client.get(STATUS).json()["state"] == "failed"


def test_callback_exchanges_the_code_and_the_row_holds_no_plaintext_token(
    hosted_app, hosted_env, configured, monkeypatch
) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    google = _fake_google(monkeypatch, title="Mine")
    _, state = _start(client)
    row = _raw_column(hosted_env, "a@example.com")
    verifier = row["pending"]["code_verifier"]

    r = client.get(CALLBACK, params={"code": "the-code", "state": state})
    assert r.status_code == 200, r.text
    assert "Connected as Mine" in r.text
    assert google == {
        "code": "the-code",
        "redirect_uri": f"http://localhost:5174{CALLBACK}",
        "code_verifier": verifier,
    }

    assert client.get(STATUS).json() == {"state": "connected", "channel_title": "Mine", "error": None}
    body = client.get(SETTINGS).json()
    assert body["configured"] and body["connected"] and body["channel_title"] == "Mine"
    assert body["connected_at"].startswith("2026-09-19")

    row = _raw_column(hosted_env, "a@example.com")
    assert "pending" not in row
    assert "1//rt-secret" not in repr(row)
    assert sealed.open_sealed(row["connection"]["refresh_token_sealed"]) == "1//rt-secret"


def test_exchange_failure_is_reported_with_its_reason(hosted_app, configured, monkeypatch) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")

    def boom(*a: Any, **kw: Any) -> Any:
        raise oauth.YouTubeError("token endpoint: HTTP 400 invalid_grant")

    monkeypatch.setattr(oauth, "exchange_code", boom)
    _, state = _start(client)
    r = client.get(CALLBACK, params={"code": "c", "state": state})
    assert "invalid_grant" in r.text
    assert client.get(STATUS).json() == {
        "state": "failed",
        "channel_title": None,
        "error": "token endpoint: HTTP 400 invalid_grant",
    }


def test_a_second_start_replaces_the_first(hosted_app, configured, monkeypatch) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    _fake_google(monkeypatch)
    _, first = _start(client)
    _, second = _start(client)
    assert first != second
    assert "did not match" in client.get(CALLBACK, params={"code": "c", "state": first}).text
    # The failed first attempt marked the row; a fresh start clears it.
    _, third = _start(client)
    assert client.get(STATUS).json()["state"] == "pending"
    assert "Connected as" in client.get(CALLBACK, params={"code": "c", "state": third}).text


def test_a_token_sealed_under_another_key_reads_as_not_connected(
    hosted_app, hosted_env, configured, monkeypatch
) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    _fake_google(monkeypatch)
    _, state = _start(client)
    client.get(CALLBACK, params={"code": "c", "state": state})
    assert client.get(SETTINGS).json()["connected"] is True
    monkeypatch.setenv(sealed.ENV_TOKEN_KEY, sealed.generate_key())
    assert client.get(SETTINGS).json()["connected"] is False
    assert client.get(STATUS).json()["state"] == "idle"


def test_delete_session_revokes_and_clears_the_row(hosted_app, hosted_env, configured, monkeypatch) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    _fake_google(monkeypatch)
    revoked: list[str] = []
    monkeypatch.setattr(oauth, "revoke_token", lambda http, token: revoked.append(token))
    _, state = _start(client)
    client.get(CALLBACK, params={"code": "c", "state": state})
    r = client.delete(f"{SETTINGS}/session")
    assert r.status_code == 200 and r.json() == {"connected": False}
    assert revoked == ["1//rt-secret"]
    assert _raw_column(hosted_env, "a@example.com") is None
    assert client.get(SETTINGS).json()["connected"] is False


def test_two_users_are_isolated(hosted_app, configured, monkeypatch) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    _fake_google(monkeypatch, title="A's")
    _, state_a = _start(client)

    other = TestClient(client.app, follow_redirects=False)
    login(other, sender, "b@example.com")
    assert other.get(STATUS).json()["state"] == "idle"
    # B cannot complete A's login, even with A's state.
    assert "No YouTube login in progress" in other.get(CALLBACK, params={"code": "c", "state": state_a}).text

    client.get(CALLBACK, params={"code": "c", "state": state_a})
    assert client.get(SETTINGS).json()["channel_title"] == "A's"
    assert other.get(SETTINGS).json()["connected"] is False
    other.delete(f"{SETTINGS}/session")
    assert client.get(SETTINGS).json()["channel_title"] == "A's"


def test_playlists_409_when_not_connected(hosted_app, configured) -> None:
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    assert client.get(f"{SETTINGS}/playlists").status_code == 409


def test_reauthorize_on_a_hosted_client_clears_the_row(
    hosted_app, hosted_env, configured, monkeypatch
) -> None:
    """``invalid_grant`` on the token refresh forgets the *row*, not the
    local file: the next settings read says Connect again."""
    client, sender = hosted_app
    login(client, sender, "a@example.com")
    _fake_google(monkeypatch)
    _, state = _start(client)
    client.get(CALLBACK, params={"code": "c", "state": state})

    def refresh_fails(*a: Any, **kw: Any) -> Any:
        raise oauth.ReauthorizeError("the stored YouTube login is no longer valid")

    monkeypatch.setattr(oauth, "refresh_access_token", refresh_fails)
    r = client.get(f"{SETTINGS}/playlists")
    assert r.status_code == 409 and "Connect YouTube again" in r.json()["detail"]
    assert _raw_column(hosted_env, "a@example.com") is None
    assert client.get(SETTINGS).json()["connected"] is False


# --- the upload route and the job body against storage ---------------------


def _connect_directly(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_google(monkeypatch)
    _, state = _start(client)
    assert "Connected as" in client.get(CALLBACK, params={"code": "c", "state": state}).text


def _seed_storage_render(storage: Any, match_id: str, slug: str, name: str = "bromma.mp4") -> None:
    prefix = f"matches/{match_id}/shooters/{slug}/exports"
    storage.write_bytes(f"{prefix}/{name}", b"\x00" * 2048)
    sidecar = youtube_sidecar.YouTubeSidecar(title="Bromma", description="0:00 Stage 1", tags=["ipsc"])
    storage.write_bytes(
        f"{prefix}/{Path(name).stem}-youtube.json", sidecar.model_dump_json(indent=2).encode()
    )


@pytest.fixture
def hosted_yt(inline_deferrer, hosted_client_with_match, configured):  # noqa: F811
    client, storage, match_id, slug = hosted_client_with_match
    inline_deferrer["state"] = client.app.state.splitsmith_state
    return client, storage, match_id, slug


def test_upload_route_409s_when_not_connected(hosted_yt) -> None:
    client, storage, match_id, slug = hosted_yt
    _seed_storage_render(storage, match_id, slug)
    r = client.post(
        f"/api/matches/{match_id}/shooters/{slug}/exports/youtube-upload", json={"filename": "bromma.mp4"}
    )
    assert r.status_code == 409


def test_upload_route_checks_storage_not_disk(hosted_yt, monkeypatch) -> None:
    client, storage, match_id, slug = hosted_yt
    _connect_directly(client, monkeypatch)
    url = f"/api/matches/{match_id}/shooters/{slug}/exports/youtube-upload"
    assert client.post(url, json={"filename": "missing.mp4"}).status_code == 400
    storage.write_bytes(f"matches/{match_id}/shooters/{slug}/exports/lonely.mp4", b"\x00" * 10)
    r = client.post(url, json={"filename": "lonely.mp4"})
    assert r.status_code == 400 and "sidecar" in r.json()["detail"]


def test_upload_job_pulls_the_render_and_pushes_the_sidecar_back(hosted_yt, monkeypatch) -> None:
    client, storage, match_id, slug = hosted_yt
    _connect_directly(client, monkeypatch)
    _seed_storage_render(storage, match_id, slug)
    seen: dict[str, Any] = {}

    def fake_upload_export(
        mp4: Path, *, client: Any, options: Any, channel_title: str, again: bool, **kw: Any
    ):
        seen.update(mp4=mp4, size=mp4.stat().st_size, channel_title=channel_title, privacy=options.privacy)
        rec = youtube_sidecar.UploadRecord(
            video_id="vid1",
            url="https://youtu.be/vid1",
            privacy=options.privacy,
            uploaded_at=datetime.now(UTC),
            channel_title=channel_title,
        )
        sc = youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(mp4))
        sc.upload = rec
        youtube_sidecar.write_sidecar(sc, youtube_sidecar.sidecar_path_for(mp4))
        return rec

    monkeypatch.setattr(youtube_api, "upload_export", fake_upload_export)
    r = client.post(
        f"/api/matches/{match_id}/shooters/{slug}/exports/youtube-upload",
        json={"filename": "bromma.mp4", "privacy": "private"},
    )
    assert r.status_code == 200, r.text
    job = client.get(f"/api/matches/{match_id}/me/jobs/{r.json()['id']}").json()
    assert job["status"] == "succeeded", job
    assert job["result"]["url"] == "https://youtu.be/vid1"
    assert seen["size"] == 2048 and seen["channel_title"] == "Chan" and seen["privacy"] == "private"

    pushed = storage.read_bytes(f"matches/{match_id}/shooters/{slug}/exports/bromma-youtube.json")
    assert youtube_sidecar.YouTubeSidecar.model_validate_json(pushed).upload.video_id == "vid1"


def test_job_client_fails_naming_the_key_when_the_worker_has_none(hosted_yt, monkeypatch) -> None:
    """A worker whose bundle predates the ``youtube`` key refuses the
    job with the variable's name rather than a decrypt error."""
    client, storage, match_id, slug = hosted_yt
    _connect_directly(client, monkeypatch)
    state = client.app.state.splitsmith_state
    from splitsmith.ui.server import current_tenant

    uid = client.get("/api/me").json()["id"]
    monkeypatch.delenv(sealed.ENV_TOKEN_KEY)
    token = current_tenant.set(state.build_tenant(uid))
    try:
        with pytest.raises(oauth.NotConfiguredError, match=sealed.ENV_TOKEN_KEY):
            youtube_api._job_client(state)
    finally:
        current_tenant.reset(token)
