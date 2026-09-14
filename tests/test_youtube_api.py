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


# --- upload route and job ---------------------------------------------------


def _seed_render(client_and_root: tuple[Any, Path], name: str = "bromma.mp4") -> Path:
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

    seen: dict[str, Any] = {}

    def fake_upload_export(
        mp4: Path,
        *,
        client: Any,
        privacy: str,
        channel_title: str = "",
        again: bool = False,
        progress: Any = None,
        check_cancel: Any = None,
    ) -> youtube_sidecar.UploadRecord:
        seen.update(mp4=mp4, privacy=privacy, again=again, channel_title=channel_title)
        if progress:
            progress(1024, 2048)
            progress(2048, 2048)
        rec = youtube_sidecar.UploadRecord(
            video_id=video_id,
            url=f"https://youtu.be/{video_id}",
            privacy=privacy,
            uploaded_at=datetime.now(UTC),
            channel_title=channel_title,
        )
        sc = youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(mp4))
        sc.upload = rec
        youtube_sidecar.write_sidecar(sc, youtube_sidecar.sidecar_path_for(mp4))
        return rec

    monkeypatch.setattr(youtube_api, "upload_export", fake_upload_export)
    monkeypatch.setattr(youtube_api, "connected_client", lambda: (object(), _conn("Mine")))
    return seen


def test_upload_route_runs_the_job_and_records_the_video(
    export_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith import youtube_sidecar

    from .test_ui_server import _wait_for_job

    client, _root = export_client
    mp4 = _seed_render(export_client)
    oauth.save_connection(_conn("Mine"))
    seen = _fake_upload(monkeypatch)
    r = client.post(
        "/api/shooters/me/exports/youtube-upload", json={"filename": "bromma.mp4", "privacy": "public"}
    )
    assert r.status_code == 200, r.text
    final = _wait_for_job(client, r.json()["id"])
    assert final["status"] == "succeeded", final
    assert final["result"]["url"] == "https://youtu.be/vid1"
    assert final["progress"] == 1.0
    assert seen["privacy"] == "public" and seen["mp4"] == mp4.resolve() and seen["again"] is False
    assert seen["channel_title"] == "Mine"
    stored = youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(mp4)).upload
    assert stored is not None and stored.video_id == "vid1"


def test_upload_route_refusals(export_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, root = export_client
    _seed_render(export_client)
    r = client.post("/api/shooters/me/exports/youtube-upload", json={"filename": "bromma.mp4"})
    assert r.status_code == 409
    oauth.save_connection(_conn())
    _fake_upload(monkeypatch)
    for bad in ("../bromma.mp4", "bromma.fcpxml", "missing.mp4"):
        r = client.post("/api/shooters/me/exports/youtube-upload", json={"filename": bad})
        assert r.status_code == 400, (bad, r.text)
    bare = root / "shooters" / "me" / "exports" / "bare.mp4"
    bare.write_bytes(b"0")
    r = client.post("/api/shooters/me/exports/youtube-upload", json={"filename": "bare.mp4"})
    assert r.status_code == 400 and "sidecar" in r.json()["detail"]
    r = client.post(
        "/api/shooters/me/exports/youtube-upload", json={"filename": "bromma.mp4", "privacy": "x"}
    )
    assert r.status_code == 422


def test_upload_job_reports_already_uploaded_as_a_failure_with_the_url(
    export_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith import youtube_sidecar
    from splitsmith.youtube.upload import AlreadyUploadedError

    from .test_ui_server import _wait_for_job

    client, _root = export_client
    _seed_render(export_client)
    oauth.save_connection(_conn())
    rec = youtube_sidecar.UploadRecord(
        video_id="old", url="https://youtu.be/old", privacy="unlisted", uploaded_at=datetime.now(UTC)
    )

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


# --- chaining from the match export ----------------------------------------


def _match_export_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "stage_numbers": [1, 2],
        "head_pad_seconds": 0.5,
        "tail_pad_seconds": 1.0,
        "include_secondaries": False,
        "include_overlay": False,
    }
    body.update(over)
    return body


def test_youtube_upload_needs_mp4_and_sidecar(export_client) -> None:
    client, _root = export_client
    r = client.post("/api/shooters/me/export/match", json=_match_export_body(youtube_upload=True))
    assert r.status_code == 422
    r = client.post(
        "/api/shooters/me/export/match", json=_match_export_body(youtube_upload=True, output_format="mp4")
    )
    assert r.status_code == 422


def _stub_mp4_render(monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import mp4_render, youtube_sidecar
    from splitsmith.ui import match_exports as match_exports_mod

    def fake_render_mp4(comp: Any, *, output_path: Path, **kwargs: Any) -> mp4_render.Mp4RenderResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x00" * 100)
        return mp4_render.Mp4RenderResult(output_path=output_path, duration_seconds=4.0)

    monkeypatch.setattr(match_exports_mod.mp4_render, "render_mp4", fake_render_mp4)
    monkeypatch.setattr(youtube_sidecar, "write_thumbnail", lambda *a, **k: None)


def test_match_export_chains_an_upload_job(export_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_ui_server import _stub_match_export_probe, _wait_for_job, _wait_for_jobs_to_drain

    client, _root = export_client
    _stub_match_export_probe(monkeypatch)
    _stub_mp4_render(monkeypatch)
    oauth.save_connection(_conn("Mine"))
    seen = _fake_upload(monkeypatch, video_id="chained")
    r = client.post(
        "/api/shooters/me/export/match",
        json=_match_export_body(
            output_format="mp4", youtube_sidecar=True, youtube_upload=True, youtube_privacy="private"
        ),
    )
    assert r.status_code == 200, r.text
    final = _wait_for_job(client, r.json()["id"])
    assert final["status"] == "succeeded", final
    jobs = _wait_for_jobs_to_drain(client)
    uploads = [j for j in jobs if j["kind"] == "youtube_upload"]
    assert len(uploads) == 1, jobs
    assert uploads[0]["status"] == "succeeded", uploads[0]
    assert seen["privacy"] == "private" and seen["again"] is True
    assert seen["mp4"].name.endswith(".mp4")
    assert uploads[0]["result"]["url"] == "https://youtu.be/chained"


def test_match_export_without_the_flag_chains_nothing(export_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_ui_server import _stub_match_export_probe, _wait_for_job, _wait_for_jobs_to_drain

    client, _root = export_client
    _stub_match_export_probe(monkeypatch)
    r = client.post("/api/shooters/me/export/match", json=_match_export_body())
    _wait_for_job(client, r.json()["id"])
    assert [j for j in _wait_for_jobs_to_drain(client) if j["kind"] == "youtube_upload"] == []
