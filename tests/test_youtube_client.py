"""``splitsmith.youtube.client``: the Data API calls behind a direct upload
(issue #1000). Every HTTP exchange is a ``respx`` route; the tests pin the
resumable protocol (chunk boundaries, 308 + Range, resume after a dropped
connection), the one-refresh-on-401 rule and the error mapping.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from splitsmith.youtube import client as yt
from splitsmith.youtube import oauth


class FakeTokens:
    def __init__(self) -> None:
        self.n = 0
        self.invalidated = 0

    def token(self) -> str:
        self.n += 1
        return f"tok{self.n}"

    def invalidate(self) -> None:
        self.invalidated += 1


def _client() -> tuple[yt.YouTubeClient, FakeTokens]:
    tokens = FakeTokens()
    return yt.YouTubeClient(httpx.Client(), tokens), tokens


@respx.mock
def test_my_channel_sends_the_bearer_and_parses_the_first_item() -> None:
    route = respx.get(f"{yt.API}/channels").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "T"}}]})
    )
    c, _ = _client()
    ch = c.my_channel()
    assert (ch.id, ch.title) == ("UC1", "T")
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok1"
    assert route.calls.last.request.url.params["mine"] == "true"


@respx.mock
def test_a_401_is_retried_once_after_invalidating_the_token() -> None:
    route = respx.get(f"{yt.API}/channels").mock(
        side_effect=[
            httpx.Response(401, json={"error": {"message": "Invalid Credentials"}}),
            httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "T"}}]}),
        ]
    )
    c, tokens = _client()
    assert c.my_channel().id == "UC1"
    assert route.call_count == 2
    assert tokens.invalidated == 1
    assert route.calls[1].request.headers["Authorization"] == "Bearer tok2"


@respx.mock
def test_a_second_401_is_an_error_not_a_loop() -> None:
    respx.get(f"{yt.API}/channels").mock(
        return_value=httpx.Response(401, json={"error": {"message": "nope"}})
    )
    c, tokens = _client()
    with pytest.raises(yt.UploadFailedError, match="nope"):
        c.my_channel()
    assert tokens.invalidated == 1


@respx.mock
@pytest.mark.parametrize("reason", ["quotaExceeded", "uploadLimitExceeded"])
def test_quota_reasons_map_to_quota_exceeded(reason: str) -> None:
    respx.get(f"{yt.API}/channels").mock(
        return_value=httpx.Response(
            403,
            json={"error": {"message": "The request cannot be completed", "errors": [{"reason": reason}]}},
        )
    )
    c, _ = _client()
    with pytest.raises(yt.QuotaExceededError):
        c.my_channel()


@respx.mock
def test_other_403s_are_upload_failures_with_the_api_message() -> None:
    respx.get(f"{yt.API}/channels").mock(
        return_value=httpx.Response(
            403, json={"error": {"message": "Forbidden by policy", "errors": [{"reason": "forbidden"}]}}
        )
    )
    c, _ = _client()
    with pytest.raises(yt.UploadFailedError, match="Forbidden by policy"):
        c.my_channel()


@respx.mock
def test_reauthorize_from_the_token_source_propagates_untouched() -> None:
    class Dead:
        def token(self) -> str:
            raise oauth.ReauthorizeError("revoked")

        def invalidate(self) -> None:
            pass

    c = yt.YouTubeClient(httpx.Client(), Dead())
    with pytest.raises(oauth.ReauthorizeError):
        c.my_channel()
