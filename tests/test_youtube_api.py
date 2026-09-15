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
        options: Any = None,
        channel_title: str = "",
        again: bool = False,
        progress: Any = None,
        check_cancel: Any = None,
    ) -> youtube_sidecar.UploadRecord:
        from splitsmith.youtube.upload import UploadOptions

        options = options or UploadOptions()
        privacy = options.effective_privacy
        seen.update(mp4=mp4, privacy=privacy, again=again, channel_title=channel_title, options=options)
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


# --- history enrichment -----------------------------------------------------


def test_history_rows_carry_the_sidecars_upload_record(export_client) -> None:
    import json

    from splitsmith import export_runs, youtube_sidecar

    client, root = export_client
    mp4 = _seed_render(export_client)
    sc_path = youtube_sidecar.sidecar_path_for(mp4)
    sc = youtube_sidecar.load_sidecar(sc_path)
    sc.upload = youtube_sidecar.UploadRecord(
        video_id="v9",
        url="https://youtu.be/v9",
        privacy="unlisted",
        uploaded_at=datetime(2026, 9, 14, tzinfo=UTC),
    )
    youtube_sidecar.write_sidecar(sc, sc_path)
    (root / "shooters" / "me" / "exports" / "other-youtube.json").write_text("{not json", encoding="utf-8")

    def run(run_id: str, artifacts: list[export_runs.ExportArtifact], kind: str = "match") -> dict[str, Any]:
        return export_runs.ExportRun(
            run_id=run_id,
            kind=kind,  # type: ignore[arg-type]
            finished_at=datetime.now(UTC),
            duration_seconds=1.0,
            stage_numbers=[1],
            formats=["mp4"],
            anomaly_count=0,
            artifacts=artifacts,
        ).model_dump(mode="json")

    log = {
        "schema_version": 1,
        "runs": [
            run(
                "a",
                [
                    export_runs.ExportArtifact(filename="bromma.mp4", kind="match_video"),
                    export_runs.ExportArtifact(filename="bromma-youtube.json", kind="sidecar"),
                ],
            ),
            run("b", [export_runs.ExportArtifact(filename="other-youtube.json", kind="sidecar")]),
            run("c", [], kind="stage"),
        ],
    }
    (root / "shooters" / "me" / "export_runs.json").write_text(json.dumps(log), encoding="utf-8")
    rows = client.get("/api/shooters/me/exports/runs").json()["runs"]
    by_id = {r["run_id"]: r for r in rows}
    assert by_id["a"]["youtube"] == {
        "video_id": "v9",
        "url": "https://youtu.be/v9",
        "privacy": "unlisted",
        "uploaded_at": "2026-09-14T00:00:00Z",
        "playlist_title": None,
        "publish_at": None,
    }
    assert by_id["b"]["youtube"] is None  # an unparseable sidecar never 500s
    assert by_id["c"]["youtube"] is None


# --- options on the routes ---------------------------------------------------


def test_upload_route_forwards_playlist_schedule_and_notify(
    export_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta, timezone

    from .test_ui_server import _wait_for_job

    client, _root = export_client
    _seed_render(export_client)
    oauth.save_connection(_conn())
    seen = _fake_upload(monkeypatch)
    r = client.post(
        "/api/shooters/me/exports/youtube-upload",
        json={
            "filename": "bromma.mp4",
            "privacy": "public",
            "playlist": "Bromma 2026",
            "publish_at": "2026-09-20T18:00:00+02:00",
            "notify_subscribers": False,
        },
    )
    assert r.status_code == 200, r.text
    assert _wait_for_job(client, r.json()["id"])["status"] == "succeeded"
    opts = seen["options"]
    assert opts.playlist == "Bromma 2026" and opts.notify_subscribers is False
    assert opts.publish_at == datetime(2026, 9, 20, 18, 0, tzinfo=timezone(timedelta(hours=2)))
    assert seen["privacy"] == "private"  # scheduled implies private


def test_match_export_chains_the_options(export_client, monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_ui_server import _stub_match_export_probe, _wait_for_job, _wait_for_jobs_to_drain

    client, _root = export_client
    _stub_match_export_probe(monkeypatch)
    _stub_mp4_render(monkeypatch)
    oauth.save_connection(_conn("Mine"))
    seen = _fake_upload(monkeypatch, video_id="chained")
    r = client.post(
        "/api/shooters/me/export/match",
        json=_match_export_body(
            output_format="mp4",
            youtube_sidecar=True,
            youtube_upload=True,
            youtube_privacy="unlisted",
            youtube_playlist="Bromma 2026",
            youtube_publish_at="2026-09-20T16:00:00Z",
            youtube_notify_subscribers=False,
        ),
    )
    assert r.status_code == 200, r.text
    assert _wait_for_job(client, r.json()["id"])["status"] == "succeeded"
    uploads = [j for j in _wait_for_jobs_to_drain(client) if j["kind"] == "youtube_upload"]
    assert len(uploads) == 1 and uploads[0]["status"] == "succeeded", uploads
    opts = seen["options"]
    assert opts.playlist == "Bromma 2026" and opts.notify_subscribers is False
    assert opts.publish_at == datetime(2026, 9, 20, 16, 0, tzinfo=UTC)


def test_history_row_carries_playlist_and_schedule(export_client) -> None:
    import json

    from splitsmith import export_runs, youtube_sidecar

    client, root = export_client
    mp4 = _seed_render(export_client)
    sc_path = youtube_sidecar.sidecar_path_for(mp4)
    sc = youtube_sidecar.load_sidecar(sc_path)
    sc.upload = youtube_sidecar.UploadRecord(
        video_id="v9",
        url="https://youtu.be/v9",
        privacy="private",
        uploaded_at=datetime(2026, 9, 14, tzinfo=UTC),
        playlist_id="PL1",
        playlist_title="Bromma 2026",
        publish_at=datetime(2026, 9, 20, 16, 0, tzinfo=UTC),
    )
    youtube_sidecar.write_sidecar(sc, sc_path)
    run = export_runs.ExportRun(
        run_id="a",
        kind="match",
        finished_at=datetime.now(UTC),
        duration_seconds=1.0,
        stage_numbers=[1],
        formats=["mp4"],
        anomaly_count=0,
        artifacts=[export_runs.ExportArtifact(filename="bromma-youtube.json", kind="sidecar")],
    ).model_dump(mode="json")
    (root / "shooters" / "me" / "export_runs.json").write_text(
        json.dumps({"schema_version": 1, "runs": [run]}), encoding="utf-8"
    )
    row = client.get("/api/shooters/me/exports/runs").json()["runs"][0]
    assert row["youtube"]["playlist_title"] == "Bromma 2026"
    assert row["youtube"]["publish_at"] == "2026-09-20T16:00:00Z"
