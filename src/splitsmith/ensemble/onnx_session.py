"""ONNX execution-provider selection for ensemble inference (issue #796).

One place decides which onnxruntime execution providers a session is built
with, so the three loaders (CLAP audio, PANN, voter C / voter E graphs) share
identical CPU-vs-GPU behaviour instead of each hardcoding
``providers=["CPUExecutionProvider"]``.

Two invariants, both load-bearing:

1. **The shipped CPU path is byte-identical to before.** When the resolved
   device is CPU -- which is *always* the case on the CPU ``onnxruntime`` wheel,
   because it does not even offer ``CUDAExecutionProvider`` -- the session is
   built with exactly ``["CPUExecutionProvider"]``, the same call the loaders
   made before this module existed. A CPU-only install cannot behave
   differently by construction.

2. **A GPU agent never fails closed.** CUDA is opportunistic: if it is
   requested/available but the provider cannot initialise at session-build time
   (GPU busy, driver/cuDNN mismatch, transient OOM), we log and rebuild the
   session CPU-only rather than propagate the error. A detect job on a GPU box
   whose GPU is momentarily unusable runs slower, never broken.

Device resolution (``resolve_onnx_device``):

- ``SPLITSMITH_ONNX_DEVICE`` env var: ``cpu`` | ``cuda`` | ``auto``.
- Default is ``auto``: use CUDA when onnxruntime advertises it, else CPU.
  ``auto`` is safe to default because CPU-only installs never advertise CUDA,
  so it is a no-op there; it only engages on an ``onnxruntime-gpu`` box.

NOTE on parity: CPU and CUDA execution providers can produce slightly different
floating-point results, and the ensemble thresholds are calibrated tight. The
CUDA path must be parity-checked against the CPU path on real fixtures before a
box is allowed to default to it (see scripts/parity_check.py). Until that check
passes for a deployment, pin ``SPLITSMITH_ONNX_DEVICE=cpu`` there.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import onnxruntime as ort

logger = logging.getLogger(__name__)

ENV_ONNX_DEVICE = "SPLITSMITH_ONNX_DEVICE"

_CPU_ONLY = ["CPUExecutionProvider"]
_CUDA_THEN_CPU = ["CUDAExecutionProvider", "CPUExecutionProvider"]


def resolve_onnx_device(available: list[str]) -> str:
    """Resolve the target device from the env var + what onnxruntime offers.

    ``available`` is ``onnxruntime.get_available_providers()``. Returns
    ``"cuda"`` or ``"cpu"``. An explicit ``SPLITSMITH_ONNX_DEVICE=cuda`` on a
    build that does not offer the provider falls back to ``cpu`` with a warning
    rather than constructing a session that cannot run.
    """
    requested = (os.environ.get(ENV_ONNX_DEVICE) or "auto").strip().lower()
    has_cuda = "CUDAExecutionProvider" in available
    if requested == "cpu":
        return "cpu"
    if requested == "cuda":
        if not has_cuda:
            logger.warning(
                "%s=cuda but onnxruntime offers no CUDAExecutionProvider "
                "(providers=%s); falling back to CPU",
                ENV_ONNX_DEVICE,
                available,
            )
            return "cpu"
        return "cuda"
    if requested != "auto":
        logger.warning("%s=%r is not cpu/cuda/auto; treating as auto", ENV_ONNX_DEVICE, requested)
    # auto
    return "cuda" if has_cuda else "cpu"


def build_onnx_session(
    path: Path | str,
    *,
    sess_options: ort.SessionOptions | None = None,
) -> ort.InferenceSession:
    """Construct an ``InferenceSession`` for ``path`` on the resolved device.

    The single entry point every ensemble loader uses. On the CPU device it is
    exactly ``InferenceSession(path, providers=["CPUExecutionProvider"])`` --
    unchanged from the pre-#796 loaders. On CUDA it prefers the CUDA provider
    with CPU as the per-node fallback, and if the session cannot be built on
    CUDA at all it retries CPU-only so a GPU agent never fails closed.
    """
    import onnxruntime as ort

    device = resolve_onnx_device(list(ort.get_available_providers()))
    path_str = str(path)

    if device == "cpu":
        return ort.InferenceSession(path_str, sess_options=sess_options, providers=_CPU_ONLY)

    try:
        session = ort.InferenceSession(path_str, sess_options=sess_options, providers=_CUDA_THEN_CPU)
    except Exception:  # noqa: BLE001 - any CUDA init failure must degrade to CPU
        logger.warning(
            "CUDA onnxruntime session for %s failed to initialise; falling back to CPU",
            path_str,
            exc_info=True,
        )
        return ort.InferenceSession(path_str, sess_options=sess_options, providers=_CPU_ONLY)

    # onnxruntime silently drops an un-initialisable provider rather than
    # raising, so confirm CUDA actually took -- otherwise say so, once, for the
    # audit trail (the session still runs, on CPU).
    active = session.get_providers()
    if "CUDAExecutionProvider" not in active:
        logger.warning("requested CUDA for %s but the session is running on %s", path_str, active)
    else:
        logger.info("onnxruntime session for %s on CUDA", path_str)
    return session
