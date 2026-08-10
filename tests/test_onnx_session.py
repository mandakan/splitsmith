"""Tests for ensemble.onnx_session provider selection (issue #796).

Device resolution is pure and tested directly. Session construction is tested
against a fake ``onnxruntime`` module injected into ``sys.modules`` so nothing
shells out to a real runtime or GPU -- the point is the provider *decision* and
the CPU fallback, not onnxruntime itself.
"""

from __future__ import annotations

import sys
import types

from splitsmith.ensemble.onnx_session import ENV_ONNX_DEVICE, build_onnx_session, resolve_onnx_device

CPU = "CPUExecutionProvider"
CUDA = "CUDAExecutionProvider"


# --------------------------------------------------------------------------
# resolve_onnx_device
# --------------------------------------------------------------------------


def test_auto_uses_cuda_when_available(monkeypatch) -> None:
    monkeypatch.delenv(ENV_ONNX_DEVICE, raising=False)
    assert resolve_onnx_device([CUDA, CPU]) == "cuda"


def test_auto_uses_cpu_when_cuda_absent(monkeypatch) -> None:
    """The shipped CPU wheel never lists CUDA, so auto is a no-op there."""
    monkeypatch.delenv(ENV_ONNX_DEVICE, raising=False)
    assert resolve_onnx_device([CPU]) == "cpu"


def test_explicit_cpu_ignores_available_cuda(monkeypatch) -> None:
    """A deployment that hasn't passed the parity check pins cpu and gets cpu."""
    monkeypatch.setenv(ENV_ONNX_DEVICE, "cpu")
    assert resolve_onnx_device([CUDA, CPU]) == "cpu"


def test_explicit_cuda_without_provider_falls_back_to_cpu(monkeypatch) -> None:
    monkeypatch.setenv(ENV_ONNX_DEVICE, "cuda")
    assert resolve_onnx_device([CPU]) == "cpu"


def test_unknown_value_treated_as_auto(monkeypatch) -> None:
    monkeypatch.setenv(ENV_ONNX_DEVICE, "banana")
    assert resolve_onnx_device([CUDA, CPU]) == "cuda"


# --------------------------------------------------------------------------
# build_onnx_session -- fake onnxruntime
# --------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, path, sess_options=None, providers=None):
        self.path = path
        self.providers = list(providers or [])

    def get_providers(self):
        return self.providers


def _install_fake_ort(monkeypatch, available, *, cuda_build_raises=False, cuda_silently_drops=False):
    """Inject a fake onnxruntime whose InferenceSession records its providers.

    ``cuda_build_raises``: constructing with CUDA in the list raises (init
    failure). ``cuda_silently_drops``: construction succeeds but the session
    reports only CPU (onnxruntime's real "un-initialisable provider dropped"
    behaviour).
    """
    calls: list[list[str]] = []

    class InferenceSession(_FakeSession):
        def __init__(self, path, sess_options=None, providers=None):
            calls.append(list(providers or []))
            if cuda_build_raises and CUDA in (providers or []):
                raise RuntimeError("CUDA init failed")
            super().__init__(path, sess_options, providers)
            if cuda_silently_drops and CUDA in self.providers:
                self.providers = [CPU]

    fake = types.ModuleType("onnxruntime")
    fake.InferenceSession = InferenceSession
    fake.get_available_providers = lambda: list(available)
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    return calls


def test_cpu_device_builds_cpu_only(monkeypatch) -> None:
    """The exact pre-#796 call: providers=['CPUExecutionProvider']."""
    monkeypatch.setenv(ENV_ONNX_DEVICE, "cpu")
    calls = _install_fake_ort(monkeypatch, [CUDA, CPU])

    session = build_onnx_session("m.onnx")

    assert calls == [[CPU]]
    assert session.get_providers() == [CPU]


def test_cuda_device_prefers_cuda_then_cpu(monkeypatch) -> None:
    monkeypatch.setenv(ENV_ONNX_DEVICE, "cuda")
    calls = _install_fake_ort(monkeypatch, [CUDA, CPU])

    session = build_onnx_session("m.onnx")

    assert calls == [[CUDA, CPU]]
    assert CUDA in session.get_providers()


def test_cuda_init_failure_falls_back_to_cpu(monkeypatch) -> None:
    """A GPU agent whose GPU can't init the session must not fail closed."""
    monkeypatch.setenv(ENV_ONNX_DEVICE, "cuda")
    calls = _install_fake_ort(monkeypatch, [CUDA, CPU], cuda_build_raises=True)

    session = build_onnx_session("m.onnx")

    # First attempt CUDA+CPU (raised), second attempt CPU-only (succeeded).
    assert calls == [[CUDA, CPU], [CPU]]
    assert session.get_providers() == [CPU]


def test_cuda_silently_dropped_still_returns_session(monkeypatch) -> None:
    """onnxruntime may drop an un-initialisable provider instead of raising;
    the session still runs (on CPU) and we don't crash chasing CUDA."""
    monkeypatch.setenv(ENV_ONNX_DEVICE, "cuda")
    _install_fake_ort(monkeypatch, [CUDA, CPU], cuda_silently_drops=True)

    session = build_onnx_session("m.onnx")

    assert session.get_providers() == [CPU]
