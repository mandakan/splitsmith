"""ONNX vs torch parity for the slim runtime (issue #377 -- doc 05).

Currently covers PANN only -- the first voter ported to ONNX. CLAP and
the visual probe land in follow-up PRs against the same harness.

The parity files are big enough that we don't commit them to git. The
test discovers them via env vars (set by the build script or a
contributor) and ``pytest.skip``s otherwise. Once the R2 hosting +
slim registry path lands end-to-end, this test will pull the
artifacts from the cache instead.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

ENV_PANN_ONNX = "SPLITSMITH_ONNX_PANN"
ENV_PANN_SAMPLE = "SPLITSMITH_ONNX_PANN_SAMPLE"
ENV_PANN_REF = "SPLITSMITH_ONNX_PANN_REF"

# Same per-voter tolerance as doc 05's parity table.
PANN_L_INF_TOLERANCE = 1e-4


def _require(env: str) -> Path:
    value = os.environ.get(env)
    if not value:
        pytest.skip(
            f"{env} not set -- export with scripts/export_pann_onnx.py and set "
            f"{ENV_PANN_ONNX} / {ENV_PANN_SAMPLE} / {ENV_PANN_REF} to enable parity"
        )
    path = Path(value)
    if not path.is_file():
        pytest.skip(f"{env}={value!r} does not exist")
    return path


def test_pann_onnx_matches_torch_reference() -> None:
    """The exported PANN ONNX produces clipwise outputs matching the saved torch reference."""
    onnxruntime = pytest.importorskip("onnxruntime")

    onnx_path = _require(ENV_PANN_ONNX)
    sample_path = _require(ENV_PANN_SAMPLE)
    ref_path = _require(ENV_PANN_REF)

    sample = np.load(sample_path)
    ref = np.load(ref_path)
    if sample.ndim == 1:
        sample = sample[None, :]

    session = onnxruntime.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    out = session.run(None, {input_name: sample.astype(np.float32)})[0]

    assert out.shape == ref.shape, f"shape mismatch: onnx={out.shape} ref={ref.shape}"
    delta = float(np.abs(out - ref).max())
    assert (
        delta < PANN_L_INF_TOLERANCE
    ), f"PANN ONNX vs torch parity exceeded {PANN_L_INF_TOLERANCE:.1e}: L_inf={delta:.3e}"


def test_pann_onnx_runtime_branch_loads_and_predicts() -> None:
    """End-to-end: load_pann_runtime() under ONNX backend produces gunshot probs."""
    pytest.importorskip("onnxruntime")
    onnx_path = _require(ENV_PANN_ONNX)
    sample_path = _require(ENV_PANN_SAMPLE)

    from splitsmith import runtime as runtime_module
    from splitsmith.ensemble import features as feat

    # Force the ONNX backend even if torch is also installed.
    prior = os.environ.get("SPLITSMITH_BACKEND")
    os.environ["SPLITSMITH_BACKEND"] = "onnx"
    os.environ[ENV_PANN_ONNX] = str(onnx_path)
    runtime_module._clear_runtime_cache()
    try:
        pann = feat.load_pann_runtime()
        assert pann.backend.value == "onnx"
        sample = np.load(sample_path)
        if sample.ndim == 1:
            sample = sample[None, :]
        probs = pann.predict_gunshot_prob(sample.astype(np.float32))
        assert probs.shape == (sample.shape[0],)
        # Sanity: probability between 0 and 1.
        assert (probs >= 0).all() and (probs <= 1.0).all()
    finally:
        if prior is None:
            os.environ.pop("SPLITSMITH_BACKEND", None)
        else:
            os.environ["SPLITSMITH_BACKEND"] = prior
        runtime_module._clear_runtime_cache()


def test_pann_onnx_runtime_branch_raises_clear_error_without_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no artifact is reachable, the ONNX branch tells the user what to do."""
    pytest.importorskip("onnxruntime")
    from splitsmith.ensemble import features as feat
    from splitsmith.models import registry as registry_mod

    monkeypatch.setenv("SPLITSMITH_BACKEND", "onnx")
    monkeypatch.delenv(ENV_PANN_ONNX, raising=False)
    monkeypatch.setattr(registry_mod, "_default_registry", None)
    monkeypatch.setattr(registry_mod, "load_spec_from_calibration", lambda: None)

    with pytest.raises(RuntimeError) as exc:
        feat.load_pann_runtime()
    msg = str(exc.value)
    assert "scripts/export_pann_onnx.py" in msg
    assert "ensemble_calibration.json" in msg
