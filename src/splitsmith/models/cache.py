"""On-disk cache for slim runtime artifacts (doc 03).

Mirror of the R2 object layout:

::

    <root>/
        artifacts/
            <sha256>/
                <filename>
        .lock

``<root>`` defaults to ``runtime().user_config_dir / "models"`` so the
cache follows ``SPLITSMITH_CONFIG_DIR`` overrides without each
consumer threading paths through their own kwargs.

The ``.lock`` file guards concurrent downloads from two parallel slim
processes overwriting each other's partial files. Held only during
writes; reads are lock-free.
"""

from __future__ import annotations

import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path

from ..runtime import runtime as process_runtime
from .errors import HashMismatch
from .manifest import ArtifactSpec

_CHUNK_BYTES = 1024 * 1024


def cache_root() -> Path:
    """Return ``~/.splitsmith/models`` (or its override-equivalent)."""
    return process_runtime().user_config_dir / "models"


def artifact_path(spec: ArtifactSpec, *, root: Path | None = None) -> Path:
    """Canonical on-disk path for an artifact's verified bytes."""
    base = root if root is not None else cache_root()
    return base / "artifacts" / spec.sha256 / spec.filename


def sha256_file(path: Path) -> str:
    """SHA256 hex digest of ``path``. Streams in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(spec: ArtifactSpec, *, root: Path | None = None) -> bool:
    """``True`` iff the cached file exists and hashes to ``spec.sha256``."""
    path = artifact_path(spec, root=root)
    if not path.is_file():
        return False
    return sha256_file(path) == spec.sha256


def remove_artifact(spec: ArtifactSpec, *, root: Path | None = None) -> None:
    """Delete the cached file for ``spec``. Idempotent."""
    path = artifact_path(spec, root=root)
    try:
        path.unlink()
    except FileNotFoundError:
        return


@contextmanager
def cache_lock(*, root: Path | None = None, timeout_s: float = 60.0):
    """Cross-process advisory lock on the cache root.

    Uses an exclusive ``open(..., 'x')`` on ``<root>/.lock`` as the
    primitive -- portable, no fcntl/msvcrt branching, and the failure
    mode is obvious (stale lock => delete the file). ``timeout_s``
    bounds how long we wait before raising; default is the maximum
    plausible download time for a single artifact on a slow link.
    """
    base = root if root is not None else cache_root()
    base.mkdir(parents=True, exist_ok=True)
    lockfile = base / ".lock"
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(str(lockfile), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"could not acquire {lockfile} within {timeout_s:.1f}s; "
                    "delete it if you're sure no other splitsmith process is running"
                ) from None
            time.sleep(0.1)
    try:
        yield
    finally:
        try:
            lockfile.unlink()
        except FileNotFoundError:
            pass


def install_verified(
    spec: ArtifactSpec,
    source: Path,
    *,
    root: Path | None = None,
) -> Path:
    """Move ``source`` into the cache after verifying it hashes to ``spec.sha256``.

    Raises :class:`HashMismatch` (and deletes ``source``) if the bytes
    don't match -- we never install a file we can't vouch for.
    Returns the final cache path on success.
    """
    actual = sha256_file(source)
    if actual != spec.sha256:
        try:
            source.unlink()
        except FileNotFoundError:
            pass
        raise HashMismatch(
            f"artifact {spec.filename!r} failed integrity check; "
            f"expected {spec.sha256[:12]}..., got {actual[:12]}...",
            expected=spec.sha256,
            actual=actual,
        )
    dest = artifact_path(spec, root=root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    source.replace(dest)
    return dest
