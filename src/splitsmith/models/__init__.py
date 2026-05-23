"""Slim runtime model layer (issue #377 -- doc 03).

Owns the round trip from "the bundled calibration says we need
artifact X" to "here's a verified file path you can hand to
``onnxruntime``". Includes:

* Manifest schema parsing (the ``model_artifacts`` block in
  ``ensemble_calibration.json``).
* On-disk cache under ``runtime().user_config_dir / "models"``.
* SHA256-verified streaming download with typed failure modes.
* The ``splitsmith fetch-models`` CLI and ``/api/models/status``
  endpoint live one layer up but consume this module's public API.

The torch backend never reads this; only the ONNX backend does. The
manifest block in the calibration JSON is optional; older calibrations
without it are still loadable for the torch path.
"""

from __future__ import annotations

from .errors import (
    HashMismatch,
    HttpError,
    ModelError,
    NetworkUnreachable,
)
from .manifest import ArtifactSpec, ModelArtifactsSpec
from .registry import ArtifactStatus, ModelRegistry, get_default_registry

__all__ = [
    "ArtifactSpec",
    "ArtifactStatus",
    "HashMismatch",
    "HttpError",
    "ModelArtifactsSpec",
    "ModelError",
    "ModelRegistry",
    "NetworkUnreachable",
    "get_default_registry",
]
