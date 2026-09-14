"""``splitsmith.youtube.client``: the Data API calls behind a direct upload
(issue #1000). Every HTTP exchange is a ``respx`` route; the tests pin the
resumable protocol (chunk boundaries, 308 + Range, resume after a dropped
connection), the one-refresh-on-401 rule and the error mapping.
"""

from __future__ import annotations

from pathlib import Path

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


# --- resumable upload -------------------------------------------------------

SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=abc"


def _meta() -> yt.VideoMetadata:
    return yt.VideoMetadata(title="T", description="D", tags=["ipsc"], category_id="17", privacy="unlisted")


def _video(tmp_path: Path, size: int) -> Path:
    p = tmp_path / "match.mp4"
    p.write_bytes(bytes(i % 251 for i in range(size)))
    return p


@respx.mock
def test_start_resumable_upload_posts_metadata_and_returns_the_session() -> None:
    route = respx.post(f"{yt.UPLOAD_API}/videos").mock(
        return_value=httpx.Response(200, headers={"Location": SESSION})
    )
    c, _ = _client()
    assert c.start_resumable_upload(_meta(), size=1234) == SESSION
    req = route.calls.last.request
    assert req.url.params["uploadType"] == "resumable"
    assert req.url.params["part"] == "snippet,status"
    assert req.headers["X-Upload-Content-Length"] == "1234"
    assert req.headers["X-Upload-Content-Type"] == "video/mp4"
    import json

    body = json.loads(req.content)
    assert body["snippet"] == {"title": "T", "description": "D", "tags": ["ipsc"], "categoryId": "17"}
    assert body["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}


@respx.mock
def test_start_resumable_upload_without_location_is_a_failure() -> None:
    respx.post(f"{yt.UPLOAD_API}/videos").mock(return_value=httpx.Response(200))
    c, _ = _client()
    with pytest.raises(yt.UploadFailedError, match="Location"):
        c.start_resumable_upload(_meta(), size=1)


def _content_range(req: httpx.Request) -> str:
    return req.headers.get("Content-Range", "")


@respx.mock
def test_upload_bytes_sends_exact_chunks_and_reports_progress(tmp_path: Path) -> None:
    video = _video(tmp_path, 2 * 1024 + 1)  # two full chunks + one byte at chunk_size=1024
    responses = [
        httpx.Response(308, headers={"Range": "bytes=0-1023"}),
        httpx.Response(308, headers={"Range": "bytes=0-2047"}),
        httpx.Response(200, json={"id": "vid123"}),
    ]
    route = respx.put(SESSION).mock(side_effect=responses)
    seen: list[tuple[int, int]] = []
    c, _ = _client()
    assert (
        c.upload_bytes(SESSION, video, chunk_size=1024, progress=lambda s, t: seen.append((s, t))) == "vid123"
    )
    ranges = [_content_range(call.request) for call in route.calls]
    assert ranges == ["bytes 0-1023/2049", "bytes 1024-2047/2049", "bytes 2048-2048/2049"]
    assert [len(call.request.content) for call in route.calls] == [1024, 1024, 1]
    assert seen == [(1024, 2049), (2048, 2049), (2049, 2049)]


@respx.mock
def test_upload_bytes_resumes_from_the_acknowledged_range_after_a_drop(tmp_path: Path) -> None:
    video = _video(tmp_path, 3 * 1024)
    responses = [
        httpx.Response(308, headers={"Range": "bytes=0-1023"}),
        httpx.ConnectError("dropped"),  # chunk 2 never acknowledged
        httpx.Response(308, headers={"Range": "bytes=0-1535"}),  # status query: half of chunk 2 landed
        httpx.Response(308, headers={"Range": "bytes=0-2559"}),  # a full chunk from 1536, not re-aligned
        httpx.Response(201, json={"id": "v"}),
    ]
    route = respx.put(SESSION).mock(side_effect=responses)
    c, _ = _client()
    slept: list[float] = []
    assert c.upload_bytes(SESSION, video, chunk_size=1024, sleep=slept.append) == "v"
    reqs = [call.request for call in route.calls]
    assert _content_range(reqs[2]) == "bytes */3072"
    assert reqs[2].headers["Content-Length"] == "0"
    assert _content_range(reqs[3]) == "bytes 1536-2559/3072"
    assert _content_range(reqs[4]) == "bytes 2560-3071/3072"
    assert slept and slept[0] > 0


@respx.mock
def test_upload_bytes_retries_a_5xx_then_gives_up_after_max_attempts(tmp_path: Path) -> None:
    video = _video(tmp_path, 100)
    respx.put(SESSION).mock(return_value=httpx.Response(503, text="unavailable"))
    c, _ = _client()
    with pytest.raises(yt.UploadFailedError, match="3 attempts"):
        c.upload_bytes(SESSION, video, max_attempts=3, sleep=lambda s: None)


@respx.mock
def test_upload_bytes_status_query_with_no_range_restarts_from_zero(tmp_path: Path) -> None:
    video = _video(tmp_path, 100)
    responses = [
        httpx.ReadTimeout("slow"),
        httpx.Response(308),  # nothing received yet: no Range header
        httpx.Response(200, json={"id": "v"}),
    ]
    route = respx.put(SESSION).mock(side_effect=responses)
    c, _ = _client()
    assert c.upload_bytes(SESSION, video, sleep=lambda s: None) == "v"
    assert _content_range(route.calls[2].request) == "bytes 0-99/100"


@respx.mock
def test_upload_bytes_status_query_can_report_completion(tmp_path: Path) -> None:
    video = _video(tmp_path, 100)
    responses = [httpx.ReadTimeout("slow"), httpx.Response(200, json={"id": "done"})]
    respx.put(SESSION).mock(side_effect=responses)
    c, _ = _client()
    assert c.upload_bytes(SESSION, video, sleep=lambda s: None) == "done"


@respx.mock
def test_upload_bytes_stops_at_a_chunk_boundary_when_cancelled(tmp_path: Path) -> None:
    video = _video(tmp_path, 3 * 1024)
    route = respx.put(SESSION).mock(return_value=httpx.Response(308, headers={"Range": "bytes=0-1023"}))
    calls = {"n": 0}

    def cancel() -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt

    c, _ = _client()
    with pytest.raises(KeyboardInterrupt):
        c.upload_bytes(SESSION, video, chunk_size=1024, check_cancel=cancel)
    assert route.call_count == 1


@respx.mock
def test_upload_bytes_4xx_is_not_retried(tmp_path: Path) -> None:
    video = _video(tmp_path, 10)
    route = respx.put(SESSION).mock(
        return_value=httpx.Response(400, json={"error": {"message": "Bad Request", "errors": []}})
    )
    c, _ = _client()
    with pytest.raises(yt.UploadFailedError, match="Bad Request"):
        c.upload_bytes(SESSION, video, sleep=lambda s: None)
    assert route.call_count == 1
