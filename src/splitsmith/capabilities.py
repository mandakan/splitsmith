"""Host capability probing for GPU-aware agent advertisement (issue #796).

An agent advertises what it can *actually* do, not what its libraries claim.
The dispatcher can then bias GPU-amenable jobs toward GPU hosts -- a soft
preference, never a hard requirement, because every path stays CPU-capable.

Two signals, at deliberately different strictness, and the asymmetry is the
point:

- ``nvenc_h264`` is gated on a **real trial encode** (``trim._nvenc_encode_usable``).
  It has to be: audit-encoder selection commits to an encoder up front, and a
  box that advertises NVENC with no usable GPU fails only once the encode has
  started -- there is no mid-job fallback. A false positive costs a dead job.

- ``cuda_ep`` is gated only on onnxruntime listing ``CUDAExecutionProvider``
  (i.e. ``onnxruntime-gpu`` is installed and built for CUDA). A lighter bar is
  correct here precisely because the inference path *does* fall back: the
  Phase 3 provider selection drops to ``CPUExecutionProvider`` when CUDA can't
  initialise at runtime (issue #796). So a box that lists CUDA but can't use it
  degrades to CPU for that job -- slower, never broken -- which is exactly the
  failure mode a soft routing preference tolerates.

The capabilities ride in the agent registration ``info`` dict (persisted whole
by the server into ``WorkerRecord.info``); scheduling on them is a separate,
measurement-gated follow-up.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

from .trim import _nvenc_encode_usable

Runner = Callable[..., subprocess.CompletedProcess]


def _cuda_ep_advertised() -> bool:
    """Is onnxruntime built to offer the CUDA execution provider here?

    ``get_available_providers()`` reflects the installed wheel, not a live
    device check -- ``onnxruntime-gpu`` lists ``CUDAExecutionProvider`` even on
    a CPU-only box. That is the honest bar for *advertisement* given the
    runtime CPU fallback (see module docstring); it must not be read as "a CUDA
    inference will succeed here".
    """
    try:
        import onnxruntime as ort
    except Exception:  # noqa: BLE001 - any import failure means "no CUDA here"
        return False
    try:
        return "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:  # noqa: BLE001 - a broken ORT install advertises nothing
        return False


def _gpu_name(runner: Runner) -> str | None:
    """Best-effort NVIDIA GPU name via ``nvidia-smi``; ``None`` if unavailable.

    Cosmetic metadata for the admin UI -- never gates a capability. Any failure
    (no ``nvidia-smi``, no driver, non-zero exit) is silently ``None`` so a
    box without the tool is indistinguishable from a box without a GPU, which
    is fine: the capability booleans, not this string, drive any routing.
    """
    try:
        completed = runner(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if getattr(completed, "returncode", 1) != 0:
        return None
    first = (completed.stdout or "").strip().splitlines()
    return first[0].strip() if first and first[0].strip() else None


def probe_capabilities(
    *,
    ffmpeg_binary: str = "ffmpeg",
    runner: Runner = subprocess.run,
) -> dict:
    """Probe this host and return the capability bundle for agent registration.

    Shape is a flat JSON-serialisable dict so it can be embedded in the
    registration ``info`` and surfaced verbatim by the admin UI. Every probe
    is failure-tolerant: a box with no GPU and no ffmpeg returns all-false /
    ``None`` rather than raising, because a capability probe must never be able
    to keep an agent from registering.

    ``runner`` is injected for tests; it drives both the NVENC trial encode and
    the ``nvidia-smi`` lookup without shelling out.
    """
    return {
        "nvenc_h264": _nvenc_encode_usable(ffmpeg_binary, runner),
        "cuda_ep": _cuda_ep_advertised(),
        "gpu_name": _gpu_name(runner),
    }
