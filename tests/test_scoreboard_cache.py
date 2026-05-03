"""Tests for ``CachingScoreboardClient`` (project-local disk cache, issue #49).

Acceptance-criterion mapping:

- "Second open is instant; zero network calls" -- ``respx`` proves no
  HTTP traffic on the second ``get_match`` call.
- "Cache survives across instances" -- a fresh ``CachingScoreboardClient``
  pointed at the same dir reuses the file on disk.
- "Project portability" -- copy the cache directory to a new path,
  instantiate fresh, hits still work.
- "Completed never refetched, in-progress manual-refresh only" -- the
  TTL test exercises both branches.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
import respx

from splitsmith.ui.scoreboard.cache import (
    COMPLETED_THRESHOLD,
    CachingScoreboardClient,
)
from splitsmith.ui.scoreboard.http import DEFAULT_BASE_URL, TOKEN_ENV_VAR, SsiHttpClient
from splitsmith.ui.scoreboard.models import MatchData

FIXTURES = Path(__file__).parent / "fixtures" / "scoreboard"
MATCH_FIXTURE = FIXTURES / "match_22_27190.json"


@pytest.fixture(autouse=True)
def _stable_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, "test-token-abc123")


@pytest.fixture
def http_client() -> SsiHttpClient:
    return SsiHttpClient(base_url=DEFAULT_BASE_URL)


def _load_match_payload(*, scoring_completed: float) -> dict:
    payload = json.loads(MATCH_FIXTURE.read_text())
    payload["scoring_completed"] = scoring_completed
    return payload


@respx.mock
def test_first_call_hits_network_second_does_not(
    tmp_path: Path, http_client: SsiHttpClient
) -> None:
    cache = CachingScoreboardClient(http_client, tmp_path / "cache")
    payload = _load_match_payload(scoring_completed=100.0)
    route = respx.get(f"{DEFAULT_BASE_URL}/match/22/27190").mock(
        return_value=httpx.Response(200, json=payload)
    )

    first = cache.get_match(22, 27190)
    second = cache.get_match(22, 27190)

    assert isinstance(first, MatchData)
    assert isinstance(second, MatchData)
    assert first.name == second.name
    assert route.call_count == 1, "second call must be served from cache"


@respx.mock
def test_in_progress_match_does_not_serve_from_cache(
    tmp_path: Path, http_client: SsiHttpClient
) -> None:
    cache = CachingScoreboardClient(http_client, tmp_path / "cache")
    payload = _load_match_payload(scoring_completed=42.0)
    route = respx.get(f"{DEFAULT_BASE_URL}/match/22/27190").mock(
        return_value=httpx.Response(200, json=payload)
    )

    cache.get_match(22, 27190)
    cache.get_match(22, 27190)

    assert route.call_count == 2, "in-progress match must refetch every time"
    assert cache.is_cached(22, 27190), "in-progress match still gets written to disk"


@respx.mock
def test_completed_threshold_is_inclusive(tmp_path: Path, http_client: SsiHttpClient) -> None:
    cache = CachingScoreboardClient(http_client, tmp_path / "cache")
    payload = _load_match_payload(scoring_completed=COMPLETED_THRESHOLD)
    route = respx.get(f"{DEFAULT_BASE_URL}/match/22/27190").mock(
        return_value=httpx.Response(200, json=payload)
    )

    cache.get_match(22, 27190)
    cache.get_match(22, 27190)

    assert route.call_count == 1


@respx.mock
def test_invalidate_match_forces_refetch(tmp_path: Path, http_client: SsiHttpClient) -> None:
    cache = CachingScoreboardClient(http_client, tmp_path / "cache")
    payload = _load_match_payload(scoring_completed=100.0)
    route = respx.get(f"{DEFAULT_BASE_URL}/match/22/27190").mock(
        return_value=httpx.Response(200, json=payload)
    )

    cache.get_match(22, 27190)
    assert cache.invalidate_match(22, 27190) is True
    assert cache.invalidate_match(22, 27190) is False
    cache.get_match(22, 27190)

    assert route.call_count == 2


@respx.mock
def test_cache_survives_across_instances(tmp_path: Path, http_client: SsiHttpClient) -> None:
    cache_dir = tmp_path / "cache"
    payload = _load_match_payload(scoring_completed=100.0)
    route = respx.get(f"{DEFAULT_BASE_URL}/match/22/27190").mock(
        return_value=httpx.Response(200, json=payload)
    )

    CachingScoreboardClient(http_client, cache_dir).get_match(22, 27190)
    CachingScoreboardClient(http_client, cache_dir).get_match(22, 27190)

    assert route.call_count == 1


@respx.mock
def test_project_portability_copy_cache_to_new_path(
    tmp_path: Path, http_client: SsiHttpClient
) -> None:
    project_a = tmp_path / "project_a" / "scoreboard" / "cache"
    project_b = tmp_path / "project_b" / "scoreboard" / "cache"
    payload = _load_match_payload(scoring_completed=100.0)
    route = respx.get(f"{DEFAULT_BASE_URL}/match/22/27190").mock(
        return_value=httpx.Response(200, json=payload)
    )

    cache_a = CachingScoreboardClient(http_client, project_a)
    cache_a.get_match(22, 27190)

    # Simulate zip + send: copy the whole cache directory across.
    shutil.copytree(project_a, project_b)

    cache_b = CachingScoreboardClient(http_client, project_b)
    cache_b.get_match(22, 27190)

    assert route.call_count == 1, "copied cache must serve hits without network"


@respx.mock
def test_for_project_resolves_conventional_path(tmp_path: Path, http_client: SsiHttpClient) -> None:
    payload = _load_match_payload(scoring_completed=100.0)
    respx.get(f"{DEFAULT_BASE_URL}/match/22/27190").mock(
        return_value=httpx.Response(200, json=payload)
    )

    cache = CachingScoreboardClient.for_project(http_client, tmp_path)
    cache.get_match(22, 27190)
    assert (tmp_path / "scoreboard" / "cache").is_dir()
    assert any((tmp_path / "scoreboard" / "cache").iterdir())


@respx.mock
def test_unrelated_endpoints_pass_through_uncached(
    tmp_path: Path, http_client: SsiHttpClient
) -> None:
    cache = CachingScoreboardClient(http_client, tmp_path / "cache")
    route = respx.get(f"{DEFAULT_BASE_URL}/events").mock(return_value=httpx.Response(200, json=[]))

    cache.search_matches("x")
    cache.search_matches("x")

    assert route.call_count == 2, "search results are not cached by design"


def test_corrupt_cache_file_falls_back_to_inner(tmp_path: Path, http_client: SsiHttpClient) -> None:
    cache_dir = tmp_path / "cache"
    cache = CachingScoreboardClient(http_client, cache_dir)
    # Pre-seed a corrupt file at the path the cache would write.
    corrupt = cache._match_cache_path(22, 27190)  # noqa: SLF001 -- test-only inspection
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_text("not json {")

    payload = _load_match_payload(scoring_completed=100.0)
    with respx.mock() as mock:
        mock.get(f"{DEFAULT_BASE_URL}/match/22/27190").mock(
            return_value=httpx.Response(200, json=payload)
        )
        cache.get_match(22, 27190)

    # After the fetch, the corrupt file should be replaced with a valid envelope.
    data = json.loads(corrupt.read_text())
    assert data["version"] == 1
