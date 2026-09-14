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
from splitsmith.youtube import client as yt
from splitsmith.youtube import oauth
from tests.conftest import strip_ansi

runner = CliRunner()


def _conn() -> oauth.YouTubeConnection:
    return oauth.YouTubeConnection(
        refresh_token="rt",
        channel_id="UC1",
        channel_title="Mathias shoots",
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

    def start_resumable_upload(
        self, metadata: yt.VideoMetadata, *, size: int, content_type: str = "video/mp4"
    ) -> str:
        self.privacy = metadata.privacy
        return "s"

    def upload_bytes(
        self, session_url: str, path: Path, *, progress: Any = None, check_cancel: Any = None, **kw: Any
    ) -> str:
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


def test_upload_rejects_an_unknown_privacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _connected(monkeypatch)
    result = runner.invoke(app, ["youtube", "upload", str(_seed_export(tmp_path)), "--privacy", "secret"])
    assert result.exit_code == 2
    assert "unlisted" in strip_ansi(result.output)


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


def test_upload_twice_exits_3_and_shows_the_existing_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_upload_reauthorize_exits_1_and_points_at_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
