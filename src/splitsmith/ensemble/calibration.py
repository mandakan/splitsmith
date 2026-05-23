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
from typing import Any

from pydantic import BaseModel, Field

from ..runtime import runtime

# Coarse camera classes the ensemble stratifies thresholds on. Keeping the
# vocabulary small on purpose: we only stratify per-voter thresholds today,
# the GBDT is shared, and deeper splits need more fixtures than we have.
# When the corpus grows, additional classes can be added without breaking
# old artifacts (loader falls back to ``DEFAULT_CAMERA_CLASS`` for unknown
# values).
CAMERA_CLASS_HEADCAM = "headcam"
CAMERA_CLASS_HANDHELD = "handheld"
DEFAULT_CAMERA_CLASS = CAMERA_CLASS_HEADCAM

# Map fixture-schema mounts to a calibration class. Body-worn mounts share
# acoustics (close-mic, AGC mostly off, similar noise floor); handheld /
# stand mounts are the off-body bucket (phone in pocket / hand, gimbal,
# tripod). Unknown / new mounts fall back to the default class so old
# fixtures and new mount values keep working.
_MOUNT_TO_CLASS: dict[str, str] = {
    "head": CAMERA_CLASS_HEADCAM,
    "chest": CAMERA_CLASS_HEADCAM,
    "helmet": CAMERA_CLASS_HEADCAM,
    "belt": CAMERA_CLASS_HEADCAM,
    "hand": CAMERA_CLASS_HANDHELD,
    "gimbal": CAMERA_CLASS_HANDHELD,
    "tripod": CAMERA_CLASS_HANDHELD,
    "monopod": CAMERA_CLASS_HANDHELD,
}


def camera_class_from_mount(mount: str | None) -> str:
    """Map a fixture-schema ``CameraMount`` value to a calibration class.

    Unknown / missing mounts return ``DEFAULT_CAMERA_CLASS`` so callers
    can blindly forward whatever they have without guarding.
    """
    if mount is None:
        return DEFAULT_CAMERA_CLASS
    return _MOUNT_TO_CLASS.get(str(mount), DEFAULT_CAMERA_CLASS)


def normalize_camera_model_key(make: str | None, model: str | None) -> str | None:
    """Canonical lookup key for the per-model amplitude-floor table.

    ``"Insta360", "GO 3S"`` becomes ``"insta360 go 3s"`` -- lower-cased
    and whitespace-collapsed so ffprobe quirks ("INSTA360" vs "Insta360",
    multiple spaces, trailing newlines) don't fragment the lookup.

    Returns ``None`` when either input is missing or empty, signalling
    the caller to fall back to the class default.
    """
    if not make or not model:
        return None
    norm_make = " ".join(str(make).strip().lower().split())
    norm_model = " ".join(str(model).strip().lower().split())
    if not norm_make or not norm_model:
        return None
    return f"{norm_make} {norm_model}"


class ClassThresholds(BaseModel):
    """Per-camera-class voter thresholds + the slice of provenance they were derived from.

    Voter A/B thresholds use the lowest-positive rule on this class's
    slice of the calibration universe. Voter C uses the shared GBDT but
    its operating threshold is picked from per-class CV predictions to
    hit ``voter_c_target_recall`` *on this class*, so a class with a
    different score distribution doesn't drag the cutoff with it. The
    PANN gunshot probability is now a feature column on voter C rather
    than a separate vote, so there is no voter_d threshold.
    """

    voter_a_floor: float
    voter_b_threshold: float
    voter_c_threshold: float
    voter_e_threshold: float | None = Field(
        default=None,
        description=(
            "CLIP visual probe ``P(shot)`` threshold for Voter E (issue "
            "#183). ``None`` means Voter E was not calibrated for this "
            "camera class -- the runtime skips it for this class. Picked "
            "via leave-one-fixture-out CV on shots vs cross_bay frames "
            "at ``voter_e_target_recall``."
        ),
    )
    n_calibration_candidates: int
    n_calibration_positives: int
    calibration_fixtures: list[str]


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
    voter_e_threshold: float | None = Field(
        default=None,
        description=(
            "Default-class CLIP visual-probe threshold for Voter E "
            "(issue #183). ``None`` on artifacts that pre-date Voter E or "
            "where the default class had no shots-vs-cross_bay calibration."
        ),
    )
    voter_c_target_recall: float = Field(
        ge=0.0,
        le=1.0,
        description="Target recall used when picking voter C's threshold.",
    )
    voter_e_target_recall: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Target recall used when picking Voter E's threshold from CV "
            "held-out probe scores. ``None`` on pre-Voter-E artifacts."
        ),
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
    voter_e_clip_model_id: str | None = Field(
        default=None,
        description=(
            "HuggingFace model ID for the CLIP backbone used by Voter E. "
            "Captured at calibration time so the runtime can detect drift."
        ),
    )
    voter_e_frame_offsets: list[float] | None = Field(
        default=None,
        description=(
            "Frame offsets (in seconds) used when extracting the per-"
            "candidate CLIP image embeddings for Voter E. v0 = (0.0,); "
            "multi-frame variants (#184) extend this."
        ),
    )
    voter_e_probe_artifact: str | None = Field(
        default=None,
        description=(
            "Filename of the Voter E probe head joblib in package data, "
            "e.g. ``voter_e_visual_probe.joblib``. ``None`` on artifacts "
            "that pre-date Voter E."
        ),
    )
    voter_e_audio_strong_min_votes_recommended: int | None = Field(
        default=None,
        description=(
            "Issue #185: provenance for the conditional-veto gate "
            "(``EnsembleConfig.e_audio_strong_min_votes``) the corpus "
            "supports. Informational; the live default lives in "
            "``EnsembleConfig`` so config drift is visible at the call "
            "site rather than buried in the calibration JSON. ``4`` "
            "matches the head-mounted Go 3S sweep that landed in #185."
        ),
    )
    built_at: str = Field(
        description="ISO-8601 timestamp of when the artifacts were generated.",
    )
    default_camera_class: str = Field(
        default=DEFAULT_CAMERA_CLASS,
        description=(
            "Class used when the caller does not provide one or provides "
            "a class with no calibrated thresholds. Default ``headcam`` "
            "preserves byte-identical behaviour for existing projects."
        ),
    )
    thresholds_by_camera_class: dict[str, ClassThresholds] | None = Field(
        default=None,
        description=(
            "Per-camera-class threshold sets. ``None`` on legacy artifacts "
            "(pre-issue #137); the loader synthesizes a single-class entry "
            "from the top-level voter_*_threshold fields so old artifacts "
            "still load. Default headcam thresholds are frozen across "
            "rebuilds to protect the dominant class."
        ),
    )
    camera_model_metadata: dict[str, dict[str, str]] | None = Field(
        default=None,
        description=(
            "Issue #303-followup: human-readable make + model for each "
            "calibrated camera-model key. The key matches "
            ":attr:`amp_floor_by_camera_model` so the UI can present a "
            "dropdown of calibrated cameras with their original casing. "
            'Schema: ``{normalized_key: {"make": str, "model": str}}``.'
        ),
    )
    amp_floor_by_camera_model: dict[str, float] | None = Field(
        default=None,
        description=(
            "Issue #304: per-camera-model within-stage amplitude floor. "
            "Keys come from :func:`normalize_camera_model_key` (lower-cased "
            '``"<make> <model>"``). Models present here override the '
            "engine-side ``EnsembleConfig.within_stage_amp_floor`` default; "
            "unknown models fall back to the config default (the "
            "generic-headcam value). ``None`` on artifacts built before "
            "per-model calibration -- everything falls back to the config "
            "default, byte-identical to pre-#304 behaviour."
        ),
    )

    def amp_floor_for(
        self,
        camera_make: str | None,
        camera_model: str | None,
        *,
        default: float | None,
    ) -> float | None:
        """Resolve the within-stage amplitude floor for a given camera.

        Lookup order:

        1. ``amp_floor_by_camera_model[normalize_camera_model_key(...)]``
           when both make and model are known and the key is calibrated.
        2. ``default`` -- the caller's class-level / engine-side fallback
           (Phase 1's ``EnsembleConfig.within_stage_amp_floor`` value, or
           ``None`` to disable the veto entirely).

        Returning ``None`` (only possible when ``default`` is ``None``)
        means "no floor"; the veto is skipped.
        """
        if self.amp_floor_by_camera_model:
            key = normalize_camera_model_key(camera_make, camera_model)
            if key is not None and key in self.amp_floor_by_camera_model:
                return self.amp_floor_by_camera_model[key]
        return default

    def thresholds_for(self, camera_class: str | None) -> ClassThresholds:
        """Return calibrated thresholds for ``camera_class``, falling back to the default class.

        ``camera_class=None`` returns the default-class set. Unknown
        classes (no calibration on file) also fall back -- with a future
        warning hook so the server can surface the miss.
        """
        per_class = self.thresholds_by_camera_class
        if per_class is None:
            # Pre-issue-#137 artifact: synthesize a single-class set from
            # the top-level fields. No need to cache; this branch is
            # rare and the result is cheap.
            return ClassThresholds(
                voter_a_floor=self.voter_a_floor,
                voter_b_threshold=self.voter_b_threshold,
                voter_c_threshold=self.voter_c_threshold,
                voter_e_threshold=self.voter_e_threshold,
                n_calibration_candidates=self.n_calibration_candidates,
                n_calibration_positives=self.n_calibration_positives,
                calibration_fixtures=list(self.calibration_fixtures),
            )
        cls = camera_class or self.default_camera_class
        if cls in per_class:
            return per_class[cls]
        if self.default_camera_class in per_class:
            return per_class[self.default_camera_class]
        # Last-resort: pick any class. Should never happen on a real
        # artifact since the build script always emits the default class.
        return next(iter(per_class.values()))


DEFAULT_VOTER_E_PROBE_FILENAME = "voter_e_visual_probe.joblib"


def load_calibration() -> EnsembleCalibration:
    """Read ``ensemble_calibration.json`` from the resolved artifacts dir."""
    path = runtime().artifact("ensemble_calibration.json")
    with path.open("r", encoding="utf-8") as fh:
        return EnsembleCalibration.model_validate(json.load(fh))


def load_voter_c_model() -> dict[str, Any]:
    """Load voter C's per-class GradientBoostingClassifiers (issue #297).

    Returns a dict keyed by ``camera_class`` -- callers pick the right
    model via ``models[camera_class]`` (with the calibration's
    ``default_camera_class`` as fallback for unknown classes).
    """
    import joblib

    return joblib.load(runtime().artifact("voter_c_gbdt.joblib"))


def load_voter_e_probe(filename: str | None = None) -> Any | None:
    """Load the serialised Voter E linear probe head, or ``None`` if absent.

    Returns ``None`` (rather than raising) when the artifact has not been
    built yet -- callers use that signal to skip wiring Voter E and fall
    back to the 4-voter behaviour. The filename comes from the
    ``voter_e_probe_artifact`` field of the calibration.
    """
    import joblib

    name = filename or DEFAULT_VOTER_E_PROBE_FILENAME
    path = runtime().artifacts_dir / name
    if not path.is_file():
        return None
    return joblib.load(path)
