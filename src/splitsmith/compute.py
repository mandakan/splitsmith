"""Compute backend abstraction for shot detection.

The interface lets the same orchestration code drive two execution
modes:

- **Local mode** -- ``LocalComputeBackend`` runs the ensemble in
  this process. This is what ``splitsmith ui`` does today.
- **Hosted mode** -- a future ``RemoteComputeBackend`` will ship
  audio (or a storage pointer) to a cloud worker and poll for the
  result.

The Protocol is intentionally narrow today: a single ``detect_stage``
method returning an ``EnsembleResult``. Progress reporting,
``health()``, and the ``AudioRef`` tagged union from
``docs/saas-readiness/04-compute-backends.md`` get added when the
remote backend lands and actually needs them.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np

from . import ensemble as ensemble_pkg
from .ensemble.api import (
    EnsembleConfig,
    EnsembleResult,
    EnsembleRuntime,
    load_ensemble_runtime,
    resolve_model_set,
)


class ComputeBackend(Protocol):
    name: str

    def detect_stage(
        self,
        *,
        audio: np.ndarray,
        sample_rate: int,
        beep_time_in_clip: float,
        stage_time_seconds: float,
        expected_rounds: int | None = None,
        ensemble_config: EnsembleConfig | None = None,
        camera_class: str | None = None,
        camera_make: str | None = None,
        camera_model: str | None = None,
        video_path: Path | None = None,
        source_beep_time: float | None = None,
        model_set: str | None = None,
    ) -> EnsembleResult:
        """Run the shot-detection ensemble for a single stage.

        ``model_set`` is the caller's preference (``resolve_model_set``);
        the result's ``model_set`` names the set that actually ran."""


class LocalComputeBackend:
    """Runs the ensemble in this process.

    ``runtime_loader`` is injected so the server can hand us its
    existing module-level lazy-loader (which test code already
    monkeypatches); it takes the requested model set. When called
    outside the server -- e.g. from a standalone script -- the default
    ``load_ensemble_runtime`` is used, which actually downloads /
    instantiates the heavy models.

    Runtimes are cached by the set the loader *returned*: a ``clap``
    request that came back ``base`` (artifacts missing) is cached as
    ``base``, so the next ``clap`` request tries again.
    """

    name = "local"

    def __init__(
        self,
        *,
        runtime_loader: Callable[[str | None], EnsembleRuntime] | None = None,
    ) -> None:
        self._runtime_loader = runtime_loader or (lambda s: load_ensemble_runtime(model_set=s))
        self._runtimes: dict[str, EnsembleRuntime] = {}
        self._lock = threading.Lock()

    def _ensure_runtime(self, model_set: str | None = None) -> EnsembleRuntime:
        wanted = resolve_model_set(model_set)
        runtime = self._runtimes.get(wanted)
        if runtime is None:
            with self._lock:
                runtime = self._runtimes.get(wanted)
                if runtime is None:
                    runtime = self._runtime_loader(model_set)
                    # A test double may carry no set; key it by the request.
                    self._runtimes[getattr(runtime, "model_set", wanted)] = runtime
        return runtime

    def detect_stage(
        self,
        *,
        audio: np.ndarray,
        sample_rate: int,
        beep_time_in_clip: float,
        stage_time_seconds: float,
        expected_rounds: int | None = None,
        ensemble_config: EnsembleConfig | None = None,
        camera_class: str | None = None,
        camera_make: str | None = None,
        camera_model: str | None = None,
        video_path: Path | None = None,
        source_beep_time: float | None = None,
        model_set: str | None = None,
    ) -> EnsembleResult:
        runtime = self._ensure_runtime(model_set)
        # Call through the package binding so tests that monkeypatch
        # ``splitsmith.ensemble.detect_shots_ensemble`` still intercept
        # us. ``ensemble.api.detect_shots_ensemble`` is the same
        # function but a different attribute path that monkeypatch
        # doesn't replace.
        return ensemble_pkg.detect_shots_ensemble(
            audio,
            sample_rate,
            beep_time_in_clip,
            stage_time_seconds,
            runtime,
            expected_rounds=expected_rounds,
            ensemble_config=ensemble_config,
            camera_class=camera_class,
            camera_make=camera_make,
            camera_model=camera_model,
            video_path=video_path,
            source_beep_time=source_beep_time,
        )
