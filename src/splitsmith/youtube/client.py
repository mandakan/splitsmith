"""YouTube Data API v3 calls behind a direct upload (issue #1000).

Deliberately thin: four endpoints over an injected ``httpx.Client``, a
bearer token from a :class:`TokenSource`, one refresh-and-retry on 401,
and the resumable upload protocol with resume on a dropped connection.
No Google client library: the protocol is a handful of headers and it is
easier to test against ``respx`` than to mock a discovery document.

Only :meth:`YouTubeClient.upload_bytes` touches the filesystem, and only
to read the file it is sending.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from .oauth import ReauthorizeError, YouTubeError

__all__ = [
    "API",
    "UPLOAD_API",
    "Channel",
    "QuotaExceededError",
    "ReauthorizeError",
    "TokenSource",
    "UploadFailedError",
    "YouTubeClient",
    "YouTubeError",
    "default_http",
    "raise_for_api_error",
]

API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"

_QUOTA_REASONS = frozenset(
    {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded", "rateLimitExceeded"}
)


class QuotaExceededError(YouTubeError):
    """The project's daily upload or unit quota is spent."""


class UploadFailedError(YouTubeError):
    """Any other API refusal, with Google's message when it gave one."""


class TokenSource(Protocol):
    def token(self) -> str: ...

    def invalidate(self) -> None: ...


class Channel(BaseModel):
    id: str
    title: str


def default_http() -> httpx.Client:
    """Long read timeout and no write timeout: an 8 MiB chunk on a slow
    uplink takes longer than any sane default."""
    return httpx.Client(timeout=httpx.Timeout(connect=30.0, read=120.0, write=None, pool=30.0))


def _api_error(resp: httpx.Response) -> tuple[str, set[str]]:
    """(message, reasons) from a Data API error body; tolerant of non-JSON."""
    try:
        body = resp.json()
    except ValueError:
        return (resp.text[:200], set())
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return (resp.text[:200], set())
    reasons = {str(e.get("reason", "")) for e in err.get("errors", []) if isinstance(e, dict)}
    return (str(err.get("message", "")), reasons)


def raise_for_api_error(resp: httpx.Response) -> None:
    """Map a non-2xx Data API response onto the error hierarchy."""
    if resp.is_success:
        return
    message, reasons = _api_error(resp)
    if resp.status_code == 403 and reasons & _QUOTA_REASONS:
        raise QuotaExceededError(message or "YouTube API quota exceeded")
    raise UploadFailedError(f"HTTP {resp.status_code}: {message or 'no message'}")


class YouTubeClient:
    def __init__(self, http: httpx.Client, tokens: TokenSource) -> None:
        self._http = http
        self._tokens = tokens

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        """One call with the bearer; on 401, refresh once and retry once.
        A :class:`ReauthorizeError` from the token source passes through."""
        base_headers = dict(kw.pop("headers", None) or {})
        for attempt in (0, 1):
            headers = {**base_headers, "Authorization": f"Bearer {self._tokens.token()}"}
            try:
                resp = self._http.request(method, url, headers=headers, **kw)
            except httpx.HTTPError as exc:
                raise UploadFailedError(f"{method} {url}: {exc}") from exc
            if resp.status_code == 401 and attempt == 0:
                self._tokens.invalidate()
                continue
            raise_for_api_error(resp)
            return resp
        raise AssertionError("unreachable")  # pragma: no cover

    def my_channel(self) -> Channel:
        resp = self._request("GET", f"{API}/channels", params={"part": "snippet", "mine": "true"})
        items = resp.json().get("items") or []
        if not items:
            raise UploadFailedError("this Google account has no YouTube channel")
        return Channel(id=str(items[0]["id"]), title=str(items[0].get("snippet", {}).get("title", "")))
