"""Tests for ``SsiHttpClient`` (online ``ScoreboardClient``, issue #49).

The same fixtures the local-json tests use back these too: that's the
acceptance criterion that both sources produce an identical internal
shape (issue #14). HTTP is mocked with ``respx`` -- no real network ever
opens.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from splitsmith.ui.scoreboard.http import (
    DEFAULT_BASE_URL,
    TOKEN_ENV_VAR,
    MatchNotFound,
    ScoreboardAuthError,
    ScoreboardRateLimited,
    ScoreboardUpstreamError,
    ShooterNotFound,
    SsiHttpClient,
)
from splitsmith.ui.scoreboard.models import MatchData, MatchRef, ShooterDashboard, ShooterRef
from splitsmith.ui.scoreboard.protocol import ScoreboardClient

FIXTURES = Path(__file__).parent / "fixtures" / "scoreboard"
MATCH_FIXTURE = FIXTURES / "match_22_27190.json"
EVENTS_FIXTURE = FIXTURES / "events.json"
SHOOTER_SEARCH_FIXTURE = FIXTURES / "shooter_search.json"
SHOOTER_DASHBOARD_FIXTURE = FIXTURES / "shooter_dashboard.json"


@pytest.fixture(autouse=True)
def _stable_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, "test-token-abc123")


@pytest.fixture
def client() -> SsiHttpClient:
    return SsiHttpClient(base_url=DEFAULT_BASE_URL)


def _load(path: Path):
    return json.loads(path.read_text())


def test_satisfies_protocol(client: SsiHttpClient) -> None:
    assert isinstance(client, ScoreboardClient)


def test_missing_token_raises_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
    with pytest.raises(ScoreboardAuthError, match=TOKEN_ENV_VAR):
        SsiHttpClient()


def test_explicit_token_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
    # Should not raise -- token argument supplies the bearer.
    SsiHttpClient(token="explicit").close()


@respx.mock
def test_bearer_header_sent(client: SsiHttpClient) -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/events").mock(return_value=httpx.Response(200, json=[]))
    client.search_matches("anything")
    assert route.called
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer test-token-abc123"


@respx.mock
def test_get_match_returns_parsed_match_data(client: SsiHttpClient) -> None:
    payload = _load(MATCH_FIXTURE)
    respx.get(f"{DEFAULT_BASE_URL}/match/22/27190").mock(
        return_value=httpx.Response(200, json=payload)
    )
    match = client.get_match(22, 27190)
    assert isinstance(match, MatchData)
    assert match.name == payload["name"]
    assert len(match.stages) == match.stages_count


@respx.mock
def test_get_match_404_maps_to_match_not_found(client: SsiHttpClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/match/22/99999").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    with pytest.raises(MatchNotFound, match="22/99999"):
        client.get_match(22, 99999)


@respx.mock
def test_search_matches_passes_query(client: SsiHttpClient) -> None:
    payload = _load(EVENTS_FIXTURE)
    route = respx.get(f"{DEFAULT_BASE_URL}/events").mock(
        return_value=httpx.Response(200, json=payload)
    )
    hits = client.search_matches("SPSK")
    assert all(isinstance(h, MatchRef) for h in hits)
    assert route.calls.last.request.url.params["q"] == "SPSK"


@respx.mock
def test_search_matches_empty_query_omits_param(client: SsiHttpClient) -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/events").mock(return_value=httpx.Response(200, json=[]))
    client.search_matches("")
    assert "q" not in route.calls.last.request.url.params


@respx.mock
def test_find_shooter(client: SsiHttpClient) -> None:
    payload = _load(SHOOTER_SEARCH_FIXTURE)
    respx.get(f"{DEFAULT_BASE_URL}/shooter/search").mock(
        return_value=httpx.Response(200, json=payload)
    )
    hits = client.find_shooter("Axell")
    assert hits, "fixture must have at least one shooter"
    assert all(isinstance(h, ShooterRef) for h in hits)


@respx.mock
def test_get_shooter(client: SsiHttpClient) -> None:
    payload = _load(SHOOTER_DASHBOARD_FIXTURE)
    respx.get(f"{DEFAULT_BASE_URL}/shooter/{payload['shooterId']}").mock(
        return_value=httpx.Response(200, json=payload)
    )
    dash = client.get_shooter(payload["shooterId"])
    assert isinstance(dash, ShooterDashboard)
    assert dash.shooterId == payload["shooterId"]


@respx.mock
def test_get_shooter_404_maps_to_shooter_not_found(client: SsiHttpClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/shooter/9999999").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    with pytest.raises(ShooterNotFound, match="9999999"):
        client.get_shooter(9999999)


@respx.mock
def test_401_unauthorized_message(client: SsiHttpClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/events").mock(return_value=httpx.Response(401))
    with pytest.raises(ScoreboardAuthError, match=TOKEN_ENV_VAR):
        client.search_matches("x")


@respx.mock
def test_429_surfaces_retry_after(client: SsiHttpClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/events").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "12"})
    )
    with pytest.raises(ScoreboardRateLimited) as excinfo:
        client.search_matches("x")
    assert excinfo.value.retry_after == 12.0
    assert "rate limit" in str(excinfo.value).lower()


@respx.mock
def test_429_without_retry_after_header(client: SsiHttpClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/events").mock(return_value=httpx.Response(429))
    with pytest.raises(ScoreboardRateLimited) as excinfo:
        client.search_matches("x")
    assert excinfo.value.retry_after is None


@respx.mock
def test_5xx_maps_to_upstream_error_with_offline_hint(client: SsiHttpClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/events").mock(return_value=httpx.Response(503))
    with pytest.raises(ScoreboardUpstreamError, match="offline JSON"):
        client.search_matches("x")


@respx.mock
def test_network_error_maps_to_upstream_error(client: SsiHttpClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/events").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(ScoreboardUpstreamError, match="offline JSON"):
        client.search_matches("x")


@respx.mock
def test_context_manager_closes_owned_client() -> None:
    with SsiHttpClient(token="t") as c:
        respx.get(f"{DEFAULT_BASE_URL}/events").mock(return_value=httpx.Response(200, json=[]))
        c.search_matches("x")
    assert c._client.is_closed  # noqa: SLF001 -- introspection check
