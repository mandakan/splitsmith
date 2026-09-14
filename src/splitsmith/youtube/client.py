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

import json
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel

from .oauth import ReauthorizeError, YouTubeError

__all__ = [
    "API",
    "CHUNK_SIZE",
    "UPLOAD_API",
    "Channel",
    "QuotaExceededError",
    "ReauthorizeError",
    "TokenSource",
    "UploadFailedError",
    "VideoMetadata",
    "YouTubeClient",
    "YouTubeError",
    "default_http",
    "multipart_related",
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


CHUNK_SIZE = 8 * 1024 * 1024  # Google requires a multiple of 256 KiB


class VideoMetadata(BaseModel):
    title: str
    description: str
    tags: list[str]
    category_id: str
    privacy: Literal["unlisted", "private", "public"]

    def to_body(self) -> dict[str, Any]:
        return {
            "snippet": {
                "title": self.title,
                "description": self.description,
                "tags": self.tags,
                "categoryId": self.category_id,
            },
            "status": {"privacyStatus": self.privacy, "selfDeclaredMadeForKids": False},
        }


def _parse_range_end(header: str | None) -> int:
    """``Range: bytes=0-1535`` -> 1536 (the next byte to send). Absent -> 0."""
    if not header:
        return 0
    try:
        _, _, end = header.partition("=")[2].partition("-")
        return int(end) + 1
    except ValueError:
        return 0


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


def multipart_related(meta: dict[str, Any], data: bytes, data_content_type: str) -> tuple[bytes, str]:
    """Google's ``uploadType=multipart`` body: a JSON part then the media
    part, ``multipart/related``. Built by hand because httpx's multipart
    support is ``form-data``, which the upload endpoint rejects."""
    boundary = f"splitsmith-{secrets.token_hex(12)}"
    crlf = b"\r\n"
    body = crlf.join(
        [
            f"--{boundary}".encode(),
            b"Content-Type: application/json; charset=UTF-8",
            b"",
            json.dumps(meta).encode("utf-8"),
            f"--{boundary}".encode(),
            f"Content-Type: {data_content_type}".encode(),
            b"",
            data,
            f"--{boundary}--".encode(),
            b"",
        ]
    )
    return body, f"multipart/related; boundary={boundary}"


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

    def start_resumable_upload(
        self, metadata: VideoMetadata, *, size: int, content_type: str = "video/mp4"
    ) -> str:
        resp = self._request(
            "POST",
            f"{UPLOAD_API}/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type": content_type,
            },
            json=metadata.to_body(),
        )
        location = resp.headers.get("Location")
        if not location:
            raise UploadFailedError("resumable upload start returned no Location header")
        return location

    def upload_bytes(
        self,
        session_url: str,
        path: Path,
        *,
        chunk_size: int = CHUNK_SIZE,
        progress: Callable[[int, int], None] | None = None,
        check_cancel: Callable[[], None] | None = None,
        max_attempts: int = 8,
        sleep: Callable[[float], None] = time.sleep,
    ) -> str:
        """Send the file in ``Content-Range`` chunks; return the video id.

        Google acknowledges each chunk with 308 and a ``Range`` header
        naming the bytes it holds; the last chunk answers 200/201 with the
        video resource. After a transport error or a 5xx the session is
        asked where it is (a zero-length PUT with ``bytes */total``) and
        the send continues from the byte after the acknowledged range,
        which may be inside the chunk that failed. ``max_attempts``
        bounds *consecutive* failures; any acknowledged chunk resets it.
        ``check_cancel`` runs before every chunk so a cancel lands on a
        boundary and never mid-PUT. 4xx is final: the session is dead.
        """
        total = path.stat().st_size
        offset = 0
        failures = 0
        with path.open("rb") as fh:
            while True:
                if check_cancel is not None:
                    check_cancel()
                fh.seek(offset)
                chunk = fh.read(chunk_size) if total else b""
                end = offset + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{total}" if chunk else f"bytes */{total}",
                }
                try:
                    resp = self._put_chunk(session_url, headers, chunk)
                except httpx.HTTPError as exc:
                    failures += 1
                    if failures >= max_attempts:
                        raise UploadFailedError(
                            f"upload gave up after {max_attempts} attempts: {exc}"
                        ) from exc
                    sleep(min(2.0**failures, 60.0))
                    try:
                        offset, done = self._resume_offset(session_url, total)
                    except httpx.HTTPError:
                        continue  # counted on the next PUT's failure
                    if done is not None:
                        return done
                    continue
                if resp.status_code == 308:
                    failures = 0
                    offset = _parse_range_end(resp.headers.get("Range"))
                    if progress is not None:
                        progress(offset, total)
                    continue
                if resp.is_success:
                    if progress is not None:
                        progress(total, total)
                    return str(resp.json()["id"])
                if resp.status_code >= 500:
                    failures += 1
                    if failures >= max_attempts:
                        raise UploadFailedError(
                            f"upload gave up after {max_attempts} attempts: HTTP {resp.status_code}"
                        )
                    sleep(min(2.0**failures, 60.0))
                    try:
                        offset, done = self._resume_offset(session_url, total)
                    except httpx.HTTPError:
                        continue
                    if done is not None:
                        return done
                    continue
                raise_for_api_error(resp)
                raise AssertionError("unreachable")  # pragma: no cover

    def _put_chunk(self, session_url: str, headers: dict[str, str], chunk: bytes) -> httpx.Response:
        headers = {**headers, "Authorization": f"Bearer {self._tokens.token()}"}
        resp = self._http.put(session_url, headers=headers, content=chunk)
        if resp.status_code == 401:
            self._tokens.invalidate()
            headers["Authorization"] = f"Bearer {self._tokens.token()}"
            resp = self._http.put(session_url, headers=headers, content=chunk)
        return resp

    def _resume_offset(self, session_url: str, total: int) -> tuple[int, str | None]:
        """Ask the session how much it holds. Returns ``(next_offset, None)``,
        or ``(total, video_id)`` when the session turns out to be complete.
        A 5xx answer returns offset 0 so the caller's next PUT fails again
        and counts an attempt; a transport error propagates for the same
        reason."""
        headers = {"Content-Length": "0", "Content-Range": f"bytes */{total}"}
        resp = self._put_chunk(session_url, headers, b"")
        if resp.status_code == 308:
            return _parse_range_end(resp.headers.get("Range")), None
        if resp.is_success:
            return total, str(resp.json()["id"])
        if resp.status_code >= 500:
            return 0, None
        raise_for_api_error(resp)
        raise AssertionError("unreachable")  # pragma: no cover

    def insert_caption(
        self, video_id: str, srt_path: Path, *, language: str = "en", name: str = "Shots"
    ) -> str:
        meta = {"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}}
        body, ctype = multipart_related(meta, srt_path.read_bytes(), "application/octet-stream")
        resp = self._request(
            "POST",
            f"{UPLOAD_API}/captions",
            params={"uploadType": "multipart", "part": "snippet"},
            headers={"Content-Type": ctype},
            content=body,
        )
        return str(resp.json().get("id", ""))

    def set_thumbnail(self, video_id: str, jpg_path: Path) -> None:
        self._request(
            "POST",
            f"{UPLOAD_API}/thumbnails/set",
            params={"videoId": video_id, "uploadType": "media"},
            headers={"Content-Type": "image/jpeg"},
            content=jpg_path.read_bytes(),
        )
