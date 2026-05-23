"""Runtime backend selection for ensemble inference (issue #377).

Production hides the choice of ONNX vs torch behind typed runtime
dataclasses (``ClapRuntime`` / ``PannRuntime`` / ``VisualRuntime`` in
sibling modules). Every voter calls into a numpy-in / numpy-out
callable on the runtime; they never see a torch tensor or an
``InferenceSession``. This module is the one place that decides which
backend the loaders construct.

See ``docs/local-slim/05-dev-vs-prod-parity.md`` for the parity
contract and ``06-slim-progress.md`` for the migration status.

Resolution order (matches doc 05):

1. ``override`` argument to :func:`select_backend` -- callers can pin
   per-process (CLI ``--backend``).
2. ``SPLITSMITH_BACKEND`` env var (``onnx`` / ``torch``).
3. ``onnxruntime`` if importable -- the default for slim wheel users.
4. ``torch`` if importable -- the default for contributors with the
   dev extras installed.
5. Raise :class:`SplitsmithBackendError` with install hints for both.
"""

from __future__ import annotations

import importlib.util
import os
from enum import StrEnum

ENV_BACKEND = "SPLITSMITH_BACKEND"


class Backend(StrEnum):
    """Which inference engine the runtime loaders should construct."""

    ONNX = "onnx"
    TORCH = "torch"


class SplitsmithBackendError(RuntimeError):
    """Raised when neither backend is importable in the current process.

    The message names both install paths so users can fix it without a
    grep through this module.
    """


def _is_importable(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def select_backend(override: Backend | str | None = None) -> Backend:
    """Pick the inference backend for this process.

    Idempotent and side-effect-free: callers can call this in a loader
    or a test without worrying about state. The first import of the
    chosen backend's heavy package happens later, inside the loader.

    Raises :class:`SplitsmithBackendError` when neither backend is
    available -- e.g. a slim wheel install that lost ``onnxruntime``,
    or a dev checkout without either group installed.
    """
    explicit = override if override is not None else os.environ.get(ENV_BACKEND)
    if explicit:
        try:
            return Backend(str(explicit).lower())
        except ValueError as exc:
            raise SplitsmithBackendError(
                f"Unknown backend {explicit!r}; expected one of " f"{[b.value for b in Backend]}"
            ) from exc

    if _is_importable("onnxruntime"):
        return Backend.ONNX
    if _is_importable("torch"):
        return Backend.TORCH

    raise SplitsmithBackendError(
        "No inference backend available. Install one of:\n"
        "  - onnxruntime (slim/prod):  uv pip install 'onnxruntime>=1.20'\n"
        "  - torch (dev / contributor): uv sync --all-groups"
    )
