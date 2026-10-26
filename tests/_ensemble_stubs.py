"""One stub calibration for the two model sets, for tests that need a calibration
but not the shipped one."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from splitsmith.ensemble import features as feat
from splitsmith.ensemble.api import EnsembleRuntime
from splitsmith.ensemble.calibration import ClassThresholds, EnsembleCalibration, ModelSet
from splitsmith.ensemble.decoder import StageDecoder

CLAP_ARTIFACTS = ["clap_audio_encoder", "clap_text_embeddings"]


def stub_thresholds(**over: Any) -> ClassThresholds:
    kw: dict[str, Any] = {
        "voter_a_floor": 0.05,
        "voter_b_threshold": 0.0,
        "voter_c_threshold": 0.5,
        "n_calibration_candidates": 10,
        "n_calibration_positives": 5,
        "calibration_fixtures": ["stub"],
    }
    kw.update(over)
    return ClassThresholds(**kw)


def stub_model_set(
    name: str,
    *,
    thresholds: Mapping[str, ClassThresholds] | None = None,
    decoders: Mapping[str, StageDecoder] | None = None,
) -> ModelSet:
    """``thresholds`` default to ``stub_thresholds`` for both classes; under
    ``base`` voter B's threshold is always ``None`` (no CLAP there)."""
    by_class = dict(thresholds or {"headcam": stub_thresholds(), "handheld": stub_thresholds()})
    if name == "base":
        by_class = {cls: t.model_copy(update={"voter_b_threshold": None}) for cls, t in by_class.items()}
    columns = list(feat.model_set_columns(name))
    return ModelSet(
        columns=columns,
        voter_c_feature_dim=len(columns),
        voter_c_onnx_artifacts={cls: f"voter_c_gbdt_{name}_{cls}.onnx" for cls in by_class},
        thresholds_by_camera_class=by_class,
        decoders_by_camera_class=dict(decoders or {}),
        requires_artifacts=list(CLAP_ARTIFACTS) if name == "clap" else [],
    )


def stub_calibration(
    *,
    with_clap: bool = True,
    thresholds: Mapping[str, ClassThresholds] | None = None,
    decoders: Mapping[str, StageDecoder] | None = None,
    **over: Any,
) -> EnsembleCalibration:
    """A two-set calibration (``base`` only when ``with_clap`` is false).
    ``thresholds`` and ``decoders`` apply to every set; the default has no
    decoders, so detection keeps the consensus."""
    sets = {"base": stub_model_set("base", thresholds=thresholds, decoders=decoders)}
    if with_clap:
        sets["clap"] = stub_model_set("clap", thresholds=thresholds, decoders=decoders)
    kw: dict[str, Any] = {
        "voter_c_target_recall": 0.95,
        "tolerance_ms": 75.0,
        "clap_prompts_shot": list(feat.CLAP_PROMPTS_SHOT),
        "clap_prompts": list(feat.CLAP_PROMPTS),
        "calibration_fixtures": ["stub"],
        "n_calibration_candidates": 10,
        "n_calibration_positives": 5,
        "built_at": "1970-01-01T00:00:00Z",
        "model_sets": sets,
    }
    kw.update(over)
    return EnsembleCalibration(**kw)


def with_set_update(cal: EnsembleCalibration, name: str, **update: Any) -> EnsembleCalibration:
    """``cal`` with fields of model set ``name`` replaced."""
    sets = dict(cal.model_sets)
    sets[name] = sets[name].model_copy(update=update)
    return cal.model_copy(update={"model_sets": sets})


@dataclass
class StubClapRuntime:
    """Stands in for ``ClapRuntime``; tests patch ``compute_clap_similarities``."""


class StubGBDT:
    """``predict_proba`` always returns 0.95 for class 1, 0.05 for class 0."""

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        out = np.zeros((x.shape[0], 2), dtype=np.float64)
        out[:, 0] = 0.05
        out[:, 1] = 0.95
        return out


def stub_runtime(model_set: str = "clap", **calibration_over: Any) -> EnsembleRuntime:
    """A runtime over ``stub_calibration`` that scores ``model_set``: a CLAP
    stand-in only for ``clap``, the always-0.95 GBDT for both classes."""
    return EnsembleRuntime(
        calibration=stub_calibration(**calibration_over),
        voter_c_model={"headcam": StubGBDT(), "handheld": StubGBDT()},
        clap=StubClapRuntime() if model_set == "clap" else None,  # type: ignore[arg-type]
        expected_prompts=tuple(feat.CLAP_PROMPTS),
        model_set=model_set,
    )
