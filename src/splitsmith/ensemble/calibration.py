"""Load shipped ensemble calibration artifacts.

Two artifacts ship with the package, both written by
``scripts/build_ensemble_artifacts.py``:

* ``data/ensemble_calibration.json`` -- voter thresholds, the CLAP prompt
  bank, and provenance (which fixtures, what tolerances). Lightweight;
  cheap to read every call.
* ``data/voter_c_gbdt.joblib`` -- the trained GradientBoostingClassifier.
  Heavier (a few hundred kB); loaded once and cached via
  ``EnsembleRuntime``.

Loading uses ``importlib.resources`` so it works whether the package is
installed (wheel) or run from the source tree.
"""

from __future__ import annotations

import json
from importlib.resources import as_file, files
from typing import Any

from pydantic import BaseModel, Field


class EnsembleCalibration(BaseModel):
    """Per-voter thresholds + provenance.

    Built once over the audited fixture set; shipped as JSON in package
    data so the FastAPI server can load it without invoking the
    calibration script.
    """

    voter_a_floor: float = Field(
        description=(
            "Minimum detector confidence below which voter A drops a "
            "candidate. Auto-calibrated to the lowest positive-shot "
            "confidence across the calibration set."
        ),
    )
    voter_b_threshold: float = Field(
        description=(
            "CLAP (shot - not-shot) prompt-similarity differential "
            "threshold. Calibrated to the minimum value across labelled "
            "positives -- preserves recall by construction."
        ),
    )
    voter_c_threshold: float = Field(
        description=(
            "GBDT probability threshold for voter C. Picked from "
            "5-fold CV predictions on the calibration set to hit "
            "``voter_c_target_recall``."
        ),
    )
    voter_d_threshold: float = Field(
        description=(
            "PANN ``Gunshot, gunfire`` class-probability threshold. "
            "Calibrated to the minimum value across labelled positives."
        ),
    )
    voter_c_target_recall: float = Field(
        ge=0.0,
        le=1.0,
        description="Target recall used when picking voter C's threshold.",
    )
    tolerance_ms: float = Field(
        description=(
            "Hand-label-to-candidate matching tolerance, in milliseconds, "
            "used when computing per-candidate labels for calibration."
        ),
    )
    clap_prompts_shot: list[str] = Field(
        description="CLAP prompts treated as shot-positive for the differential.",
    )
    clap_prompts: list[str] = Field(
        description=(
            "All CLAP prompts in the column order the GBDT expects. "
            "Voter C's feature vector includes the per-prompt similarities."
        ),
    )
    calibration_fixtures: list[str] = Field(
        description="Fixture stems used for calibration (audited).",
    )
    n_calibration_candidates: int = Field(
        description="Total candidate count across the calibration set.",
    )
    n_calibration_positives: int = Field(
        description="Number of labelled positives across the calibration set.",
    )
    voter_c_feature_dim: int = Field(
        description=(
            "Number of features the GBDT classifier expects: hand-crafted "
            "features + per-prompt CLAP similarities + the CLAP "
            "shot/not-shot differential."
        ),
    )
    built_at: str = Field(
        description="ISO-8601 timestamp of when the artifacts were generated.",
    )


_DATA_PACKAGE = "splitsmith.data"
_CALIBRATION_FILENAME = "ensemble_calibration.json"
_VOTER_C_MODEL_FILENAME = "voter_c_gbdt.joblib"


def load_calibration() -> EnsembleCalibration:
    """Read ``ensemble_calibration.json`` from the installed package."""
    resource = files(_DATA_PACKAGE).joinpath(_CALIBRATION_FILENAME)
    with resource.open("r", encoding="utf-8") as fh:
        return EnsembleCalibration.model_validate(json.load(fh))


def load_voter_c_model() -> Any:
    """Load the serialised GradientBoostingClassifier for voter C."""
    import joblib

    resource = files(_DATA_PACKAGE).joinpath(_VOTER_C_MODEL_FILENAME)
    # joblib needs a real filesystem path, so materialise via as_file (which
    # is a no-op when the package is laid out on disk).
    with as_file(resource) as path:
        return joblib.load(path)
