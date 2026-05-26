"""Storage abstraction for project file IO.

The interface lets the same project code read/write files in two
modes:

- **Local mode** -- ``FilesystemStorage`` wraps ``pathlib.Path`` and
  writes go through a temp-file + atomic rename so partial writes
  never appear at the canonical path.
- **Hosted mode** -- a future ``S3Storage`` will wrap an
  S3-compatible client (Cloudflare R2 in production). When it lands
  the dependency on fsspec earns its keep; today pathlib is enough.

The Protocol is intentionally narrow today: bytes IO + a handful of
metadata methods. ``open_stream``, ``signed_url``, and the JSON
helpers from ``docs/saas-readiness/03-storage-layer.md`` get added
when a callsite actually needs them.

Scoping: paths passed to a ``Storage`` instance are **relative to
its root**. The root is opaque to callers -- it might be a local
directory, a bucket prefix, or an in-memory namespace. Multiple
``Storage`` instances may coexist (one per project root in local
mode; per-tenant prefixes in hosted mode); there is no global
storage singleton.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class StorageObject(BaseModel):
    """Metadata for a single object in storage.

    ``etag`` is None for backends that don't compute one (local FS
    today). ``last_modified`` is None when the backend reports no
    mtime (rare; some in-memory test backends).
    """

    path: str
    size: int
    etag: str | None = None
    last_modified: datetime | None = None


class Storage(Protocol):
    """Project-scoped byte storage. Paths are relative to the root."""

    def read_bytes(self, path: str) -> bytes:
        """Return the object's bytes. Raises ``FileNotFoundError`` if absent."""

    def write_bytes(self, path: str, data: bytes) -> None:
        """Atomic write. Replaces an existing object at the same path."""

    def exists(self, path: str) -> bool:
        """Return True iff an object exists at the path."""

    def stat(self, path: str) -> StorageObject | None:
        """Return metadata, or None if the path doesn't exist."""

    def list(self, prefix: str) -> Iterator[StorageObject]:
        """Yield every object whose path starts with ``prefix``.

        ``prefix=''`` walks the whole storage root. Returned paths
        are relative to the root (same as ``read_bytes`` accepts).
        Order is unspecified.
        """

    def delete(self, path: str) -> None:
        """Remove the object. No-op if absent."""


class FilesystemStorage:
    """Local-disk implementation backed by ``pathlib.Path``.

    Writes go through a sibling temp file + ``os.replace`` so a
    crash mid-write can't leave a torn file at the canonical path.
    Tests construct one of these against ``tmp_path``; production
    constructs one against the user's chosen project root.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, path: str) -> Path:
        # Reject absolute paths and ``..`` traversal up front so a
        # caller can't accidentally write outside the storage root.
        # In hosted mode the same check protects against tenant-
        # crossing prefixes that bypass the bucket scope.
        rel = Path(path)
        if rel.is_absolute() or any(part == ".." for part in rel.parts):
            raise ValueError(f"path must be relative and contain no '..': {path!r}")
        return self._root / rel

    def read_bytes(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # ``delete=False`` so the temp file survives the context; we
        # close it before the rename so Windows-style locks don't
        # interfere (the production target is POSIX but the test
        # suite runs on macOS / Linux either way).
        fd, tmp_name = tempfile.mkstemp(
            prefix=target.name + ".",
            suffix=".tmp",
            dir=target.parent,
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            Path(tmp_name).replace(target)
        except Exception:
            # Best-effort cleanup; if the rename succeeded the temp
            # is already gone. Suppress because we want to re-raise
            # the original write error, not a cleanup error.
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass
            raise

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def stat(self, path: str) -> StorageObject | None:
        target = self._resolve(path)
        if not target.exists():
            return None
        st = target.stat()
        return StorageObject(
            path=path,
            size=st.st_size,
            last_modified=datetime.fromtimestamp(st.st_mtime).astimezone(),
        )

    def list(self, prefix: str) -> Iterator[StorageObject]:
        # Resolve the prefix to a directory if it points at one, or
        # treat it as a filename prefix within the storage root if
        # not. ``prefix=''`` walks everything from the root.
        if prefix:
            base = self._resolve(prefix)
        else:
            base = self._root
        if not base.exists():
            return
        if base.is_file():
            st = base.stat()
            yield StorageObject(
                path=str(base.relative_to(self._root).as_posix()),
                size=st.st_size,
                last_modified=datetime.fromtimestamp(st.st_mtime).astimezone(),
            )
            return
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            st = p.stat()
            yield StorageObject(
                path=str(p.relative_to(self._root).as_posix()),
                size=st.st_size,
                last_modified=datetime.fromtimestamp(st.st_mtime).astimezone(),
            )

    def delete(self, path: str) -> None:
        target = self._resolve(path)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def _validate_relative_key(key: str) -> str:
    """Reject absolute / traversing keys before we hand them to a
    backend. The same guard ``FilesystemStorage._resolve`` runs --
    factored out so ``S3Storage`` doesn't have to reimplement it.
    """
    rel = Path(key)
    if rel.is_absolute() or any(part == ".." for part in rel.parts):
        raise ValueError(f"path must be relative and contain no '..': {key!r}")
    return key


class S3Storage:
    """Hosted-mode implementation backed by an S3-compatible bucket.

    Targets Cloudflare R2 in production (doc 03) but works against
    AWS S3, MinIO, and the ``moto`` in-memory mock used in tests --
    they're all the same S3 API. Construct one instance per
    tenant-scoped prefix; the bucket itself is process-wide.

    Path semantics match :class:`FilesystemStorage`: keys are
    relative to ``prefix``, ``/`` separators (POSIX-style), no
    leading ``/``, no ``..``. The class uses S3's ``Key`` directly,
    so a `prefix` of ``"projects/abc/"`` plus a `path` of
    ``"audit/stage1.json"`` produces the S3 key
    ``"projects/abc/audit/stage1.json"``.

    boto3 is imported lazily so a local-mode install that never
    constructs an S3Storage doesn't pay the import cost.
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        region_name: str = "auto",
        client: object | None = None,
    ) -> None:
        self._bucket = bucket
        # Normalize the prefix: no leading slash, exactly one trailing
        # slash when non-empty. ``""`` means "the bucket root".
        prefix = prefix.strip("/")
        self._prefix = f"{prefix}/" if prefix else ""
        if client is not None:
            self._client = client
        else:
            import boto3

            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region_name,
            )

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def prefix(self) -> str:
        return self._prefix

    def _key(self, path: str) -> str:
        _validate_relative_key(path)
        return f"{self._prefix}{path}"

    def read_bytes(self, path: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(path))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                raise FileNotFoundError(f"no object at {path!r}") from exc
            raise
        return response["Body"].read()

    def write_bytes(self, path: str, data: bytes) -> None:
        # S3 PUT is atomic: either the new object becomes visible in
        # full, or the put fails and the old object (if any) survives.
        # No temp-and-rename dance needed.
        self._client.put_object(Bucket=self._bucket, Key=self._key(path), Body=data)

    def exists(self, path: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=self._key(path))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404", "NotFound"):
                return False
            raise
        return True

    def stat(self, path: str) -> StorageObject | None:
        from botocore.exceptions import ClientError

        try:
            head = self._client.head_object(Bucket=self._bucket, Key=self._key(path))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404", "NotFound"):
                return None
            raise
        return StorageObject(
            path=path,
            size=int(head["ContentLength"]),
            etag=head.get("ETag", "").strip('"') or None,
            last_modified=head.get("LastModified"),
        )

    def list(self, prefix: str) -> Iterator[StorageObject]:
        _validate_relative_key(prefix) if prefix else None
        full_prefix = self._key(prefix) if prefix else self._prefix
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                # Strip the storage-root prefix so callers see paths
                # relative to their scope, matching FilesystemStorage.
                rel_key = key[len(self._prefix) :] if self._prefix else key
                yield StorageObject(
                    path=rel_key,
                    size=int(obj["Size"]),
                    etag=obj.get("ETag", "").strip('"') or None,
                    last_modified=obj.get("LastModified"),
                )

    def delete(self, path: str) -> None:
        # S3 ``delete_object`` is a no-op when the key doesn't exist
        # (same contract as :class:`FilesystemStorage.delete`).
        self._client.delete_object(Bucket=self._bucket, Key=self._key(path))
