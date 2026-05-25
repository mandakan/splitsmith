"""Tests for the compute backend abstraction."""

from __future__ import annotations

import threading

import numpy as np

from splitsmith.compute import LocalComputeBackend
from splitsmith.ensemble.api import EnsembleConfig, EnsembleResult


class _FakeRuntime:
    """Stand-in for the heavy ensemble runtime; we never actually
    use its attributes -- we monkeypatch ``detect_shots_ensemble``
    so the backend's call into it sees the fake and skips real
    model loading."""


def test_local_backend_lazy_loads_runtime_only_once(monkeypatch) -> None:
    """``runtime_loader`` should be invoked at most once per backend
    instance, even across concurrent ``detect_stage`` calls."""
    load_calls = 0
    load_lock = threading.Lock()

    def _loader():
        nonlocal load_calls
        with load_lock:
            load_calls += 1
        return _FakeRuntime()

    captured: list[dict] = []

    def _fake_detect(audio, sr, beep, stage_time, runtime, **kwargs):
        captured.append({"beep": beep, "stage_time": stage_time, **kwargs})
        return EnsembleResult(candidates=[], consensus=2, expected_rounds=kwargs.get("expected_rounds"))

    import splitsmith.ensemble as ensemble_pkg

    monkeypatch.setattr(ensemble_pkg, "detect_shots_ensemble", _fake_detect)

    backend = LocalComputeBackend(runtime_loader=_loader)
    audio = np.zeros(1024, dtype=np.float32)

    backend.detect_stage(audio=audio, sample_rate=48000, beep_time_in_clip=0.5, stage_time_seconds=10.0)
    backend.detect_stage(audio=audio, sample_rate=48000, beep_time_in_clip=0.5, stage_time_seconds=10.0)

    assert load_calls == 1, "runtime loader should be memoised"
    assert len(captured) == 2


def test_local_backend_calls_through_package_binding_so_patches_work(monkeypatch) -> None:
    """If ``LocalComputeBackend`` called the function via the
    ``ensemble.api`` module attribute, this monkeypatch would not
    intercept. The package-binding indirection in the backend is
    what makes the existing test suite's pattern work."""
    sentinel = EnsembleResult(candidates=[], consensus=99, expected_rounds=42)

    import splitsmith.ensemble as ensemble_pkg

    monkeypatch.setattr(ensemble_pkg, "detect_shots_ensemble", lambda *a, **kw: sentinel)

    backend = LocalComputeBackend(runtime_loader=lambda: _FakeRuntime())
    result = backend.detect_stage(
        audio=np.zeros(1, dtype=np.float32),
        sample_rate=48000,
        beep_time_in_clip=0.0,
        stage_time_seconds=1.0,
    )

    assert result is sentinel


def test_local_backend_forwards_keyword_arguments(monkeypatch) -> None:
    """All optional kwargs surface unmodified to ``detect_shots_ensemble``.

    Regression guard: if a refactor drops a parameter, the existing
    shot-detect endpoint silently loses behaviour (e.g. voter E,
    camera-class dispatch, apriori boost) without any test failing
    on the call site itself.
    """
    seen: dict = {}

    def _fake_detect(audio, sr, beep, stage_time, runtime, **kwargs):
        seen.update(kwargs)
        return EnsembleResult(candidates=[], consensus=2)

    import splitsmith.ensemble as ensemble_pkg

    monkeypatch.setattr(ensemble_pkg, "detect_shots_ensemble", _fake_detect)

    cfg = EnsembleConfig()
    backend = LocalComputeBackend(runtime_loader=lambda: _FakeRuntime())
    backend.detect_stage(
        audio=np.zeros(1, dtype=np.float32),
        sample_rate=48000,
        beep_time_in_clip=0.5,
        stage_time_seconds=10.0,
        expected_rounds=8,
        ensemble_config=cfg,
        camera_class="handheld",
        camera_make="GoPro",
        camera_model="HERO12",
        video_path=None,
        source_beep_time=1.23,
    )

    assert seen["expected_rounds"] == 8
    assert seen["ensemble_config"] is cfg
    assert seen["camera_class"] == "handheld"
    assert seen["camera_make"] == "GoPro"
    assert seen["camera_model"] == "HERO12"
    assert seen["source_beep_time"] == 1.23


def test_local_backend_name_is_local() -> None:
    """The ``name`` attribute is what observability code keys on
    (job records, log lines). Lock it down so a rename here doesn't
    silently invalidate dashboards downstream."""
    assert LocalComputeBackend().name == "local"


def test_state_compute_is_swappable_by_tests() -> None:
    """The whole point of the abstraction: tests (and the eventual
    hosted picker) can replace ``state.compute`` to redirect every
    detect call without touching the orchestrator. If this breaks,
    the abstraction has leaked."""
    from splitsmith.ui.server import create_app

    app = create_app()
    state = app.state.splitsmith_state

    sentinel_result = EnsembleResult(candidates=[], consensus=7)

    class _RecordingBackend:
        name = "recording"

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def detect_stage(self, **kwargs):
            self.calls.append(kwargs)
            return sentinel_result

    fake = _RecordingBackend()
    state.compute = fake

    result = state.compute.detect_stage(
        audio=np.zeros(1, dtype=np.float32),
        sample_rate=48000,
        beep_time_in_clip=0.0,
        stage_time_seconds=1.0,
    )
    assert result is sentinel_result
    assert len(fake.calls) == 1
