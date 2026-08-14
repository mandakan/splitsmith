"""Hosted sync HTTP client for the desktop-to-hosted sync push (#631).

``HostedSyncClient`` wraps the two httpx clients a push needs: ``http``
talks to the splitsmith API (base_url is the bare hosted origin - the
client owns the /api/sync path prefix - plus bearer auth, built by the
caller) for match adoption, the doc manifest / per-doc GET routes, the
three version-guarded doc-upsert PUT routes, and the presign/complete
legs of the multipart media protocol (see ``splitsmith.ui.sync_api``);
``media_http`` is a plain client that PUTs part bytes straight to the
presigned storage URLs the API hands back - no auth header, no
base_url, since those URLs are already fully qualified and pre-signed.

Every method raises :class:`SyncClientError` with a user-facing message
on the error conditions a desktop operator can actually act on: a
revoked/expired token (401, any route) and a match_id collision with a
natively-created hosted match (409, only on ``ensure_match``).
``put_doc`` raises the narrower :class:`SyncVersionConflict` on its own
409 - a lost optimistic-lock race, not an operator-facing error, but
one the caller (the push executor) is expected to catch and retry.
Every other HTTP error is left as an ``httpx.HTTPStatusError`` - there's
nothing more useful to say about a 500 than what the server already
said.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import threading
from collections.abc import Callable
from typing import IO

import httpx

from .plan import DocItem, MediaItem, doc_identity_key

#: Chunk size for each ``.read()`` call while filling out one upload
#: part - independent of the server-chosen ``part_size`` (which arrives
#: from ``/media/create``); a read chunk only needs to be small relative
#: to ``part_size``, never equal to it.
_READ_CHUNK_SIZE = 8 * 1024 * 1024

#: How many part PUTs one ``upload_media`` call keeps in flight (#713).
#: The live #631 push sustained ~7.4 MB/s on a single stream with media
#: at 89% of push wall time, so extra TCP streams are the lever when the
#: uplink has headroom. Each in-flight part holds its bytes in memory:
#: at the server's 16 MiB part_size, 4 workers + the read-ahead part is
#: ~80 MiB peak - fine for a desktop push, and files smaller than one
#: part_size (sidecars, most clips) never fan out at all.
_PART_UPLOAD_CONCURRENCY = 4


class SyncClientError(RuntimeError):
    """Raised with a user-facing message (401 -> token revoked, 409 -> hosted match exists, etc.)."""


class SyncVersionConflict(SyncClientError):
    """A doc PUT lost the optimistic-lock race (hosted 409
    ``version_conflict``) - the caller re-pulls, re-merges, retries."""


class HostedSyncClient:
    """Thin wrapper over the ``/api/sync/*`` HTTP surface for one push."""

    def __init__(self, *, http: httpx.Client, media_http: httpx.Client | None = None) -> None:
        self._http = http
        self._media_http = media_http

    def close(self) -> None:
        """Close the underlying ``httpx.Client``(s).

        A push's ``http_client`` is already closed by its own caller (see
        ``server.py``'s ``_run_sync_match`` job), so this exists for
        shorter-lived callers - the device-flow routes (#719) build a
        fresh client per call and must not leak the connection pool.
        ``media_http`` is only ever created lazily via ``_media``, so it
        is only closed here if a caller actually touched it.
        """
        self._http.close()
        if self._media_http is not None:
            self._media_http.close()

    @property
    def _media(self) -> httpx.Client:
        """The client used for presigned-URL part PUTs, created lazily."""
        if self._media_http is None:
            self._media_http = httpx.Client()
        return self._media_http

    def ensure_match(self, match_id: str, name: str) -> None:
        """Adopt (or refresh the name of) the hosted mirror row for ``match_id``."""
        resp = self._http.post("/api/sync/matches", json={"match_id": match_id, "name": name})
        self._raise_for_status(resp, on_409="a hosted match with this id already exists and is not a mirror")

    def device_authorize(self, device_name: str) -> dict:
        """Start a device authorization on the hosted side (#719).

        Public route: the client this runs on carries no bearer, by
        definition - there is no credential yet. Full path, because
        ``base_url`` is the bare hosted origin (#712) and this client
        owns every prefix it uses.
        """
        resp = self._http.post("/api/device/authorize", json={"device_name": device_name})
        self._raise_for_status(resp)
        return resp.json()

    def device_poll(self, device_code: str) -> dict:
        """Poll for the outcome. Always 200; the verdict is in the body."""
        resp = self._http.post("/api/device/token", json={"device_code": device_code})
        self._raise_for_status(resp)
        return resp.json()

    def device_revoke_session(self) -> None:
        """Revoke this install's own token. Needs the bearer."""
        resp = self._http.delete("/api/device/session")
        self._raise_for_status(resp)

    def whoami(self) -> dict:
        """The linked account's identity (#877). Needs the bearer.

        Used by the desktop to refresh the account snapshot it cached at
        link time. ``/api/me`` is unreachable with this client's
        sync-scoped token, which is why this route exists.
        """
        resp = self._http.get("/api/sync/whoami")
        self._raise_for_status(resp)
        return resp.json()

    def put_doc(self, match_id: str, item: DocItem, *, expected_version: int) -> int:
        """Upsert one doc at ``expected_version`` (0 = create), returning
        the version the hosted side assigned. Raises
        :class:`SyncVersionConflict` when the row moved on since the
        manifest/pull this ``expected_version`` came from."""
        resp = self._http.put(
            self._doc_url(match_id, item),
            params={"expected_version": expected_version},
            json=item.body,
        )
        if resp.status_code == 409:
            raise SyncVersionConflict(
                f"doc {doc_identity_key(item.kind, item.slug, item.stage_number)} "
                "changed on the hosted side during this sync"
            )
        self._raise_for_status(resp)
        return resp.json()["version"]

    def get_doc_manifest(self, match_id: str) -> list[dict]:
        """Identity + version of every hosted doc for this match."""
        resp = self._http.get(f"/api/sync/matches/{match_id}/docs")
        self._raise_for_status(resp)
        return resp.json()["docs"]

    def get_doc(
        self, match_id: str, kind: str, slug: str | None, stage_number: int | None
    ) -> tuple[dict, int]:
        """Fetch one doc body + version by identity."""
        path = self._doc_path(kind, slug, stage_number)
        resp = self._http.get(f"/api/sync/matches/{match_id}/{path}")
        self._raise_for_status(resp)
        payload = resp.json()
        return payload["doc"], payload["version"]

    def upload_media(
        self,
        match_id: str,
        item: MediaItem,
        *,
        progress: Callable[[int], None] | None = None,
    ) -> str:
        """Push one local file via presigned multipart upload.

        Streams ``item.local_path`` exactly once: every chunk both feeds
        the running sha256 and becomes (part of) an upload part. Parts
        are read (and hashed) in order but PUT concurrently, up to
        ``_PART_UPLOAD_CONCURRENCY`` in flight (#713); ``complete`` is
        only sent after every part landed, with parts sorted by number.
        Calls ``progress`` with the number of newly-uploaded bytes after
        each part lands (serialized - implementations need no locking).
        Returns the hex sha256 digest of the whole file.
        """
        create_resp = self._http.post(
            f"/api/sync/matches/{match_id}/media/create", json={"key": item.remote_key}
        )
        self._raise_for_status(create_resp)
        create_body = create_resp.json()
        upload_id = create_body["upload_id"]
        part_size = create_body["part_size"]

        progress_lock = threading.Lock()

        def _upload_part(part_number: int, part_bytes: bytes) -> dict[str, int | str]:
            url_resp = self._http.post(
                f"/api/sync/matches/{match_id}/media/part-url",
                json={"key": item.remote_key, "upload_id": upload_id, "part_number": part_number},
            )
            self._raise_for_status(url_resp)
            part_url = url_resp.json()["url"]

            put_resp = self._media.put(part_url, content=part_bytes)
            self._raise_for_status(put_resp)

            if progress is not None:
                with progress_lock:
                    progress(len(part_bytes))
            return {"part_number": part_number, "etag": put_resp.headers["ETag"]}

        try:
            digest = hashlib.sha256()
            parts: list[dict[str, int | str]] = []
            # The executor context waits for in-flight parts on exit, so a
            # raising part never leaves worker threads writing while the
            # abort below runs. In-flight submissions are bounded at the
            # worker count: each pending future holds its part's bytes, so
            # an unbounded submit loop would buffer the whole file.
            with (
                item.local_path.open("rb") as fh,
                concurrent.futures.ThreadPoolExecutor(max_workers=_PART_UPLOAD_CONCURRENCY) as pool,
            ):
                pending: set[concurrent.futures.Future[dict[str, int | str]]] = set()
                part_number = 1
                while True:
                    part_bytes = _read_part(fh, part_size)
                    if not part_bytes:
                        break
                    digest.update(part_bytes)
                    pending.add(pool.submit(_upload_part, part_number, part_bytes))
                    part_number += 1
                    if len(pending) >= _PART_UPLOAD_CONCURRENCY:
                        done, pending = concurrent.futures.wait(
                            pending, return_when=concurrent.futures.FIRST_COMPLETED
                        )
                        for future in done:
                            parts.append(future.result())
                for future in concurrent.futures.as_completed(pending):
                    parts.append(future.result())

            parts.sort(key=lambda p: p["part_number"])
            complete_resp = self._http.post(
                f"/api/sync/matches/{match_id}/media/complete",
                json={"key": item.remote_key, "upload_id": upload_id, "parts": parts},
            )
            self._raise_for_status(complete_resp)
        except Exception:
            # Best-effort cleanup: a half-open multipart upload is orphaned
            # on R2 forever (no lifecycle rules configured anywhere), so
            # always try to abort it. This is cleanup, not the operation
            # the caller asked for - swallow any failure of the abort call
            # itself (network error, 401, 500, ...) and let the original
            # exception propagate unchanged below.
            try:
                self._http.post(
                    f"/api/sync/matches/{match_id}/media/abort",
                    json={"key": item.remote_key, "upload_id": upload_id},
                )
            except Exception:  # noqa: BLE001 - deliberately swallow, see above
                pass
            raise
        return digest.hexdigest()

    def delete_media(self, match_id: str, remote_key: str) -> None:
        """Remove a pushed beep_review object (#821). Idempotent."""
        resp = self._http.post(
            f"/api/sync/matches/{match_id}/media/delete",
            json={"key": remote_key},
        )
        self._raise_for_status(resp)

    @staticmethod
    def _doc_path(kind: str, slug: str | None, stage_number: int | None) -> str:
        """The ``docs/...`` URL suffix for one doc identity - shared by
        ``_doc_url`` (a ``DocItem`` in hand) and ``get_doc`` (bare identity
        fields, no ``DocItem`` to build for a read)."""
        if kind == "match":
            return "docs/match"
        if kind == "project":
            return f"docs/project/{slug}"
        return f"docs/audit/{slug}/{stage_number}"

    @classmethod
    def _doc_url(cls, match_id: str, item: DocItem) -> str:
        return f"/api/sync/matches/{match_id}/{cls._doc_path(item.kind, item.slug, item.stage_number)}"

    @staticmethod
    def _raise_for_status(resp: httpx.Response, *, on_409: str | None = None) -> None:
        if resp.status_code == 401:
            raise SyncClientError("hosted rejected the token - generate a new one on your account page")
        if resp.status_code == 409 and on_409 is not None:
            raise SyncClientError(on_409)
        resp.raise_for_status()


def _read_part(fh: IO[bytes], part_size: int) -> bytes:
    """Read up to ``part_size`` bytes from ``fh``, looping until either
    ``part_size`` bytes have accumulated or EOF.

    A single ``.read(n)`` call is not guaranteed to return a full ``n``
    bytes even on a regular file, and an upload part must be exactly
    ``part_size`` bytes (except the last one).
    """
    chunks: list[bytes] = []
    remaining = part_size
    while remaining > 0:
        chunk = fh.read(min(remaining, _READ_CHUNK_SIZE))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
