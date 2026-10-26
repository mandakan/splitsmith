"""The calibration carries model sets; each set owns its thresholds and decoders."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from splitsmith import runtime as runtime_mod
from splitsmith.ensemble import features as feat
from splitsmith.ensemble.calibration import ClassThresholds, EnsembleCalibration, ModelSet, load_calibration
from splitsmith.ensemble.decoder import PriorTable, StageDecoder


def _thresholds(b: float | None) -> ClassThresholds:
    return ClassThresholds(
        voter_a_floor=0.01,
        voter_b_threshold=b,
        voter_c_threshold=0.5,
        n_calibration_candidates=10,
        n_calibration_positives=5,
        calibration_fixtures=["x"],
    )


def _decoder(bias: float) -> StageDecoder:
    return StageDecoder(
        features=["score_c"],
        coef=[1.0],
        intercept=0.0,
        bias=bias,
        w_split=0.0,
        w_end=0.0,
        split_prior=PriorTable(start=-1.0, step=0.1, values=[0.0, 0.0]),
        end_prior=PriorTable(start=-1.0, step=0.1, values=[0.0, 0.0]),
    )


def _cal() -> EnsembleCalibration:
    base = ModelSet(
        columns=list(feat.model_set_columns("base")),
        voter_c_feature_dim=len(feat.model_set_columns("base")),
        voter_c_onnx_artifacts={"headcam": "b_h.onnx", "handheld": "b_a.onnx"},
        thresholds_by_camera_class={"headcam": _thresholds(None), "handheld": _thresholds(None)},
        decoders_by_camera_class={"headcam": _decoder(1.0), "handheld": _decoder(2.0)},
    )
    clap = base.model_copy(
        update={
            "columns": list(feat.VOTER_C_COLUMN_NAMES),
            "voter_c_feature_dim": feat.VOTER_C_FEATURE_DIM,
            "thresholds_by_camera_class": {"headcam": _thresholds(0.03), "handheld": _thresholds(0.007)},
            "requires_artifacts": ["clap_audio_encoder", "clap_text_embeddings"],
        }
    )
    return EnsembleCalibration(
        voter_c_target_recall=0.95,
        tolerance_ms=75.0,
        clap_prompts_shot=list(feat.CLAP_PROMPTS_SHOT),
        clap_prompts=list(feat.CLAP_PROMPTS),
        calibration_fixtures=["x"],
        n_calibration_candidates=10,
        n_calibration_positives=5,
        built_at="2026-10-26T00:00:00+00:00",
        default_camera_class="headcam",
        model_sets={"base": base, "clap": clap},
    )


@pytest.fixture
def _fresh_runtime() -> Iterator[None]:
    runtime_mod._clear_runtime_cache()
    yield
    runtime_mod._clear_runtime_cache()


def test_each_set_is_read_by_name_and_an_unknown_set_names_the_others() -> None:
    cal = _cal()
    assert cal.default_model_set == "base"
    assert cal.model_set("base").requires_artifacts == []
    assert cal.model_set("clap").requires_artifacts == ["clap_audio_encoder", "clap_text_embeddings"]
    with pytest.raises(RuntimeError, match="base, clap"):
        cal.model_set("pann")


def test_thresholds_come_from_the_named_set() -> None:
    cal = _cal()
    assert cal.thresholds_for("handheld", model_set="clap").voter_b_threshold == 0.007
    assert cal.thresholds_for("handheld", model_set="base").voter_b_threshold is None


def test_unknown_camera_class_falls_to_the_default_class() -> None:
    cal = _cal()
    headcam = cal.model_set("base").thresholds_by_camera_class["headcam"]
    assert cal.thresholds_for(None, model_set="base") == headcam
    assert cal.thresholds_for("drone", model_set="base") == headcam
    ms = cal.model_set("base")
    assert ms.decoder_for(None, cal.default_camera_class).bias == 1.0
    assert ms.decoder_for("drone", cal.default_camera_class).bias == 1.0
    assert ms.decoder_for("handheld", cal.default_camera_class).bias == 2.0


def test_a_set_without_decoders_decodes_with_none() -> None:
    ms = _cal().model_set("base").model_copy(update={"decoders_by_camera_class": {}})
    assert ms.decoder_for("headcam", "headcam") is None


def test_a_flat_calibration_names_the_rebuild(tmp_path, monkeypatch, _fresh_runtime) -> None:
    flat = {"voter_a_floor": 0.01, "voter_c_threshold": 0.5, "built_at": "x"}
    (tmp_path / "ensemble_calibration.json").write_text(json.dumps(flat), encoding="utf-8")
    monkeypatch.setenv("SPLITSMITH_ARTIFACTS_DIR", str(tmp_path))
    runtime_mod._clear_runtime_cache()
    with pytest.raises(RuntimeError, match="build_ensemble_artifacts.py"):
        load_calibration()


@pytest.mark.xfail(strict=True, reason="until the rebuild in Task 10")
def test_the_shipped_calibration_validates_with_two_sets() -> None:
    cal = load_calibration()
    assert set(cal.model_sets) == {"base", "clap"}
    for name, ms in cal.model_sets.items():
        assert ms.columns == list(feat.model_set_columns(name))
        assert ms.voter_c_feature_dim == len(ms.columns)
        assert set(ms.decoders_by_camera_class) == set(ms.thresholds_by_camera_class)
