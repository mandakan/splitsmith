"""HTTP streaming download for slim runtime artifacts (doc 03).

httpx-only (no new runtime dep -- httpx already ships in
``[project] dependencies``). Resumable / etag handling is a future
enhancement; v1 uses a simple stream-to-tempfile with one
exponential-backoff retry on network failure. Hash mismatches do NOT
retry -- silent retry would mask a tampered mirror.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from .errors import HttpError, NetworkUnreachable

logger = logging.getLogger(__name__)

_CHUNK_BYTES = 1024 * 1024
_DEFAULT_TIMEOUT_S = 30.0
_RETRY_BACKOFF_S = 2.0

ProgressCallback = Callable[[int, int | None], None]


def download_to(
    url: str,
    dest: Path,
    *,
    expected_size: int | None = None,
    progress: ProgressCallback | None = None,
    client: httpx.Client | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> None:
    """Stream ``url`` into ``dest``. Network failures retry once.

    ``progress`` is called with ``(bytes_downloaded, total_or_none)``
    after each chunk so callers can drive a tqdm bar or feed
    ``/api/models/status``. ``dest.parent`` is created if missing.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _stream_once(url, dest, progress=progress, client=client, timeout_s=timeout_s)
        return
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
        logger.warning("download %s: transient %s; retrying once in %.1fs", url, exc, _RETRY_BACKOFF_S)
        time.sleep(_RETRY_BACKOFF_S)
    _stream_once(url, dest, progress=progress, client=client, timeout_s=timeout_s)
    if expected_size is not None and dest.stat().st_size != expected_size:
        logger.warning(
            "download %s: size mismatch (expected %d, got %d); hash verification will catch it",
            url,
            expected_size,
            dest.stat().st_size,
        )


def _stream_once(
    url: str,
    dest: Path,
    *,
    progress: ProgressCallback | None,
    client: httpx.Client | None,
    timeout_s: float,
) -> None:
    owns_client = client is None
    http = client if client is not None else httpx.Client(timeout=timeout_s, follow_redirects=True)
    try:
        try:
            with http.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise HttpError(
                        f"GET {url} returned HTTP {resp.status_code}",
                        status_code=resp.status_code,
                    )
                total_header = resp.headers.get("content-length")
                total = int(total_header) if total_header and total_header.isdigit() else None
                seen = 0
                with dest.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=_CHUNK_BYTES):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        seen += len(chunk)
                        if progress is not None:
                            progress(seen, total)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise NetworkUnreachable(f"could not connect to {url}: {exc}") from exc
    finally:
        if owns_client:
            http.close()
