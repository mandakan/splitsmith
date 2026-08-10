"""Tests for capabilities.py (issue #796).

Unit tests inject a fake runner so the NVENC trial encode and the
``nvidia-smi`` lookup never shell out (per CLAUDE.md), and monkeypatch the
onnxruntime provider list rather than depending on the installed wheel.
"""

from __future__ import annotations

import subprocess
from typing import Any

import splitsmith.capabilities as caps
import splitsmith.trim as trim_module


class _ScriptedRunner:
    """A runner that answers each command by prefix from a scripted table.

    Keyed on the binary name (``argv[0]``) so one runner can serve both the
    ffmpeg trial encode and the ``nvidia-smi`` lookup. Missing keys raise
    ``FileNotFoundError`` -- the "tool not installed" case the probes tolerate.
    """

    def __init__(self, table: dict[str, subprocess.CompletedProcess]) -> None:
        self.table = table
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        try:
            return self.table[cmd[0]]
        except KeyError as exc:
            raise FileNotFoundError(cmd[0]) from exc


def _cp(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def _clear_caches() -> None:
    trim_module._nvenc_encode_usable.cache_clear()


def test_probe_reports_gpu_box(monkeypatch) -> None:
    """A host with a working NVENC encode, onnxruntime-gpu and nvidia-smi
    advertises all three signals."""
    _clear_caches()
    monkeypatch.setattr(caps, "_cuda_ep_advertised", lambda: True)
    runner = _ScriptedRunner(
        {
            "ffmpeg": _cp(0),
            "nvidia-smi": _cp(0, stdout="NVIDIA GeForce RTX 2070 SUPER\n"),
        }
    )

    result = caps.probe_capabilities(runner=runner)

    assert result == {
        "nvenc_h264": True,
        "cuda_ep": True,
        "gpu_name": "NVIDIA GeForce RTX 2070 SUPER",
    }


def test_probe_reports_cpu_only_box(monkeypatch) -> None:
    """A box with no GPU tooling advertises nothing and never raises -- an
    empty/false bundle must not be able to block agent registration."""
    _clear_caches()
    monkeypatch.setattr(caps, "_cuda_ep_advertised", lambda: False)
    runner = _ScriptedRunner({})  # neither ffmpeg nor nvidia-smi present

    result = caps.probe_capabilities(runner=runner)

    assert result == {"nvenc_h264": False, "cuda_ep": False, "gpu_name": None}


def test_nvenc_gated_on_trial_encode_not_string(monkeypatch) -> None:
    """NVENC is advertised only when the trial encode exits 0. A build that
    lists the encoder but fails at encode time must report False -- the strict
    bar the module docstring justifies."""
    _clear_caches()
    monkeypatch.setattr(caps, "_cuda_ep_advertised", lambda: False)
    runner = _ScriptedRunner({"ffmpeg": _cp(1), "nvidia-smi": _cp(0, stdout="RTX 2070\n")})

    result = caps.probe_capabilities(runner=runner)

    assert result["nvenc_h264"] is False
    # gpu_name is independent of the NVENC verdict.
    assert result["gpu_name"] == "RTX 2070"


def test_gpu_name_none_when_nvidia_smi_fails(monkeypatch) -> None:
    """A non-zero ``nvidia-smi`` (e.g. driver/device mismatch) yields no name
    rather than a garbage string."""
    _clear_caches()
    monkeypatch.setattr(caps, "_cuda_ep_advertised", lambda: False)
    runner = _ScriptedRunner({"ffmpeg": _cp(0), "nvidia-smi": _cp(9, stdout="")})

    result = caps.probe_capabilities(runner=runner)

    assert result["gpu_name"] is None
