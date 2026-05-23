"""Orchestrator for the slim model layer (doc 03).

Reads the ``model_artifacts`` block from the bundled calibration,
resolves slugs to cached file paths, and downloads missing or
corrupted files. One :class:`ModelRegistry` per process; built on
first call via :func:`get_default_registry` so the calibration is read
lazily (the torch backend never instantiates one).
"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..runtime import runtime as process_runtime
from .cache import (
    artifact_path,
    cache_lock,
    cache_root,
    install_verified,
    remove_artifact,
    verify_artifact,
)
from .download import ProgressCallback, download_to
from .errors import ModelError
from .manifest import ArtifactSpec, ModelArtifactsSpec

logger = logging.getLogger(__name__)

ArtifactState = Literal["present", "missing", "mismatched"]


@dataclass(frozen=True)
class ArtifactStatus:
    """Snapshot of one artifact's local state.

    Used by :meth:`ModelRegistry.status` and the ``/api/models/status``
    endpoint. The frontend overlay branches on ``state``:

    * ``present`` -- file exists and hashes correctly
    * ``missing`` -- file is not on disk at all
    * ``mismatched`` -- file exists but the SHA256 differs from the
      expected value; the runtime treats this as missing + will
      redownload on next request
    """

    slug: str
    state: ArtifactState
    path: Path
    expected_sha256: str
    size_bytes: int


class ModelRegistry:
    """Resolves slim model artifact slugs to verified cache paths.

    Construct via :func:`get_default_registry` for the process-wide
    instance, or directly for tests that need to point at a custom
    spec and cache root.
    """

    def __init__(
        self,
        spec: ModelArtifactsSpec,
        *,
        root: Path | None = None,
    ) -> None:
        self._spec = spec
        self._root = root if root is not None else cache_root()
        self._lock = threading.Lock()
        # Cache "this artifact was verified during this process" so we
        # don't re-hash a multi-hundred-MB file on every ensemble run.
        self._verified: set[str] = set()

    # -- introspection --------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def spec(self) -> ModelArtifactsSpec:
        return self._spec

    def known_slugs(self) -> list[str]:
        return self._spec.slugs()

    def status(self) -> list[ArtifactStatus]:
        """Return the per-slug local state. No downloads triggered."""
        out: list[ArtifactStatus] = []
        for slug in self._spec.slugs():
            artifact = self._spec.artifact(slug)
            if artifact is None:
                continue
            path = artifact_path(artifact, root=self._root)
            if not path.is_file():
                state: ArtifactState = "missing"
            elif slug in self._verified or verify_artifact(artifact, root=self._root):
                state = "present"
                self._verified.add(slug)
            else:
                state = "mismatched"
            out.append(
                ArtifactStatus(
                    slug=slug,
                    state=state,
                    path=path,
                    expected_sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                )
            )
        return out

    # -- resolution -----------------------------------------------------

    def resolve(self, slug: str, *, progress: ProgressCallback | None = None) -> Path:
        """Return a verified on-disk path for ``slug``.

        Idempotent: a verified cached file is returned without touching
        the network. A missing or mismatched file triggers a download
        under the cache lock. Raises :class:`ModelError` (and
        subclasses for typed failures) if the artifact can't be
        produced.
        """
        artifact = self._require(slug)
        with self._lock:
            if slug in self._verified:
                path = artifact_path(artifact, root=self._root)
                if path.is_file():
                    return path
                # Disk gone behind our back -- re-verify on the next pass.
                self._verified.discard(slug)
            if verify_artifact(artifact, root=self._root):
                self._verified.add(slug)
                return artifact_path(artifact, root=self._root)
            self._download_locked(slug, artifact, progress=progress)
            self._verified.add(slug)
            return artifact_path(artifact, root=self._root)

    def fetch_all(self, *, progress: ProgressCallback | None = None) -> list[Path]:
        """Resolve every artifact in the spec; useful for ``--prefetch``."""
        return [self.resolve(slug, progress=progress) for slug in self.known_slugs()]

    def verify_all(self) -> list[ArtifactStatus]:
        """Re-hash every cached file; refresh the in-memory verified set."""
        self._verified.clear()
        return self.status()

    # -- mutation -------------------------------------------------------

    def remove(self, slug: str) -> None:
        """Drop the cached file for ``slug`` so the next resolve redownloads."""
        artifact = self._require(slug)
        with self._lock:
            self._verified.discard(slug)
            remove_artifact(artifact, root=self._root)

    # -- internals ------------------------------------------------------

    def _require(self, slug: str) -> ArtifactSpec:
        artifact = self._spec.artifact(slug)
        if artifact is None:
            raise ModelError(f"unknown model artifact slug {slug!r}; check ensemble_calibration.json")
        return artifact

    def _download_locked(
        self,
        slug: str,
        artifact: ArtifactSpec,
        *,
        progress: ProgressCallback | None,
    ) -> None:
        url = self._spec.composed_url(artifact)
        logger.info("fetching slim model artifact %s from %s", slug, url)
        with cache_lock(root=self._root):
            with tempfile.NamedTemporaryFile(
                dir=str(self._root),
                prefix=f".{slug}.",
                suffix=".part",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
            try:
                download_to(
                    url,
                    tmp_path,
                    expected_size=artifact.size_bytes,
                    progress=progress,
                )
                install_verified(artifact, tmp_path, root=self._root)
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except FileNotFoundError:
                        pass


# -- module-level helpers ----------------------------------------------


def load_spec_from_calibration() -> ModelArtifactsSpec | None:
    """Read the bundled calibration; return the ``model_artifacts`` block.

    Returns ``None`` when the block is absent (older calibrations, dev
    flows still on torch). The torch backend never reads this; the
    ONNX backend will raise a clear error pointing at
    ``splitsmith fetch-models``.
    """
    runtime = process_runtime()
    try:
        calibration_path = runtime.artifact("ensemble_calibration.json")
    except FileNotFoundError:
        return None
    payload = json.loads(calibration_path.read_text())
    block = payload.get("model_artifacts")
    if block is None:
        return None
    return ModelArtifactsSpec.model_validate(block)


_default_registry: ModelRegistry | None = None
_default_lock = threading.Lock()


def get_default_registry() -> ModelRegistry | None:
    """Process-wide :class:`ModelRegistry` for the bundled calibration.

    Returns ``None`` when the calibration has no ``model_artifacts``
    block. Callers should treat that as "ONNX path unavailable in this
    install" and either fall back to torch via the backend selector
    or raise a clear error.
    """
    global _default_registry
    if _default_registry is not None:
        return _default_registry
    with _default_lock:
        if _default_registry is not None:
            return _default_registry
        spec = load_spec_from_calibration()
        if spec is None:
            return None
        _default_registry = ModelRegistry(spec)
        return _default_registry


def _reset_default_registry() -> None:
    """Test-only: clear the cached default registry between tests."""
    global _default_registry
    with _default_lock:
        _default_registry = None
