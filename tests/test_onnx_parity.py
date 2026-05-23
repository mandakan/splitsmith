"""ONNX vs torch parity for the slim runtime (issue #377 -- doc 05).

Covers PANN + CLAP. The visual probe lands in a follow-up PR against
the same harness.

The parity files are big enough that we don't commit them to git. The
test discovers them via env vars (set by the build scripts or a
contributor) and ``pytest.skip``s otherwise. Once R2 hosting + slim
registry path lands end-to-end, this test will pull the artifacts
from the cache instead.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

ENV_PANN_ONNX = "SPLITSMITH_ONNX_PANN"
ENV_PANN_SAMPLE = "SPLITSMITH_ONNX_PANN_SAMPLE"
ENV_PANN_REF = "SPLITSMITH_ONNX_PANN_REF"

ENV_CLAP_ONNX = "SPLITSMITH_ONNX_CLAP"
ENV_CLAP_TEXT = "SPLITSMITH_CLAP_TEXT"
ENV_CLAP_SAMPLE_INPUT = "SPLITSMITH_CLAP_SAMPLE_INPUT"
ENV_CLAP_SAMPLE_AUDIO = "SPLITSMITH_CLAP_SAMPLE_AUDIO"
ENV_CLAP_REF = "SPLITSMITH_CLAP_REF"

# Same per-voter tolerance as doc 05's parity table.
PANN_L_INF_TOLERANCE = 1e-4
CLAP_L_INF_TOLERANCE = 1e-4
# Mel-spectrogram parity: dB units. 1e-4 is far tighter than the doc 05
# downstream tolerance because the upstream signal goes through a heavy
# transformer afterwards; sub-dB delta upstream is well-tolerated.
CLAP_MEL_TOLERANCE_DB = 1e-2


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


# ----------------------------------------------------------------------
# CLAP parity
# ----------------------------------------------------------------------


def test_clap_numpy_mel_matches_clap_feature_extractor_reference() -> None:
    """The license-clean numpy mel matches ``ClapFeatureExtractor`` within 1e-2 dB."""
    sample_input = _require(ENV_CLAP_SAMPLE_INPUT)
    sample_audio = _require(ENV_CLAP_SAMPLE_AUDIO)

    from splitsmith.ensemble.clap_mel import log_mel_input_features

    ref = np.load(sample_input)
    audio = np.load(sample_audio)
    got = log_mel_input_features(audio)

    assert got.shape == ref.shape, f"shape mismatch: got={got.shape} ref={ref.shape}"
    delta = float(np.abs(got - ref).max())
    assert delta < CLAP_MEL_TOLERANCE_DB, (
        f"numpy CLAP mel diverged from ClapFeatureExtractor by {delta:.3e} dB "
        f"(tolerance {CLAP_MEL_TOLERANCE_DB:.1e} dB)"
    )


def test_clap_onnx_matches_torch_reference() -> None:
    """The exported CLAP ONNX audio trunk matches the saved torch reference."""
    onnxruntime = pytest.importorskip("onnxruntime")

    onnx_path = _require(ENV_CLAP_ONNX)
    sample_input = _require(ENV_CLAP_SAMPLE_INPUT)
    ref_path = _require(ENV_CLAP_REF)

    inp = np.load(sample_input)
    ref = np.load(ref_path)

    session = onnxruntime.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name
    out = session.run(None, {input_name: inp.astype(np.float32)})[0]

    assert out.shape == ref.shape, f"shape mismatch: onnx={out.shape} ref={ref.shape}"
    delta = float(np.abs(out - ref).max())
    assert (
        delta < CLAP_L_INF_TOLERANCE
    ), f"CLAP ONNX vs torch parity exceeded {CLAP_L_INF_TOLERANCE:.1e}: L_inf={delta:.3e}"


def test_clap_onnx_runtime_branch_loads_and_encodes() -> None:
    """End-to-end: load_clap_runtime() under ONNX backend produces audio embeddings."""
    pytest.importorskip("onnxruntime")
    onnx_path = _require(ENV_CLAP_ONNX)
    text_path = _require(ENV_CLAP_TEXT)
    sample_audio_path = _require(ENV_CLAP_SAMPLE_AUDIO)

    from splitsmith import runtime as runtime_module
    from splitsmith.ensemble import features as feat

    prior = os.environ.get("SPLITSMITH_BACKEND")
    os.environ["SPLITSMITH_BACKEND"] = "onnx"
    os.environ[ENV_CLAP_ONNX] = str(onnx_path)
    os.environ[ENV_CLAP_TEXT] = str(text_path)
    runtime_module._clear_runtime_cache()
    try:
        clap = feat.load_clap_runtime()
        assert clap.backend.value == "onnx"
        sample = np.load(sample_audio_path)
        if sample.ndim == 1:
            sample = sample[None, :]
        emb = clap.encode_audio(sample.astype(np.float32))
        assert emb.shape[0] == sample.shape[0]
        assert emb.shape[1] == clap.text_embeddings.shape[1]
        # L2-normalised: norm per row close to 1.
        norms = np.linalg.norm(emb, axis=1)
        assert np.allclose(norms, 1.0, atol=1e-5), f"audio embeddings not L2-normalised: {norms}"
    finally:
        if prior is None:
            os.environ.pop("SPLITSMITH_BACKEND", None)
        else:
            os.environ["SPLITSMITH_BACKEND"] = prior
        runtime_module._clear_runtime_cache()


def test_clap_onnx_runtime_branch_raises_clear_error_without_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No artifact + no registry block -> clear error pointing at the export script."""
    pytest.importorskip("onnxruntime")
    from splitsmith.ensemble import features as feat
    from splitsmith.models import registry as registry_mod

    monkeypatch.setenv("SPLITSMITH_BACKEND", "onnx")
    monkeypatch.delenv(ENV_CLAP_ONNX, raising=False)
    monkeypatch.delenv(ENV_CLAP_TEXT, raising=False)
    monkeypatch.setattr(registry_mod, "_default_registry", None)
    monkeypatch.setattr(registry_mod, "load_spec_from_calibration", lambda: None)

    with pytest.raises(RuntimeError) as exc:
        feat.load_clap_runtime()
    msg = str(exc.value)
    assert "scripts/export_clap_onnx.py" in msg
    assert "ensemble_calibration.json" in msg
