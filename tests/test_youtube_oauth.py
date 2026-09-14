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
