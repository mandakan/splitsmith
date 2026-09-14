"""``splitsmith.youtube.oauth``: the built-in OAuth client, the connection
store under ``~/.splitsmith/youtube.json``, the loopback listener and the
consent / token exchange (issue #1000, phase 1). No network: token calls
go through ``respx``; the listener is driven by a real ``httpx`` request
to its loopback port.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

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


# --- PKCE, consent URL, loopback listener ----------------------------------


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


# --- token exchange, refresh, connect --------------------------------------

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
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Token revoked"}
        )
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
    respx.route(host="127.0.0.1").pass_through()
    respx.post(oauth.TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "at", "expires_in": 3600, "refresh_token": "rt"}
        )
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
    respx.route(host="127.0.0.1").pass_through()
    respx.post(oauth.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 1})
    )

    def fake_browser(url: str) -> None:
        q = parse_qs(urlparse(url).query)
        threading.Thread(target=lambda: _hit(f"{q['redirect_uri'][0]}?state={q['state'][0]}&code=c")).start()

    with httpx.Client() as http, pytest.raises(oauth.YouTubeError, match="refresh token"):
        oauth.connect(CLIENT, http, open_browser=fake_browser, timeout_s=5.0)
