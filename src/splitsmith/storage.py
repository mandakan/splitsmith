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
