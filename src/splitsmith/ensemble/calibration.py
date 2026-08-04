"""Load shipped ensemble calibration artifacts.

Several artifacts ship with the package, all written by
``scripts/build_ensemble_artifacts.py``:

* ``data/ensemble_calibration.json`` -- voter thresholds, the CLAP prompt
  bank, the artifact filename maps, and provenance (which fixtures, what
  tolerances). Lightweight; cheap to read every call.
* ``data/voter_c_gbdt_<camera_class>.onnx`` -- the trained voter C
  gradient-boosted classifiers, one graph per camera class, named by
  the ``voter_c_onnx_artifacts`` map. A few hundred kB in total; loaded
  once and cached via ``EnsembleRuntime``.
* ``data/voter_e_visual_probe.onnx`` -- the Voter E linear probe head,
  named by ``voter_e_probe_artifact``. Optional.

The model artifacts are ONNX graphs run through ``onnxruntime``, the
same path CLAP and PANN use (issue #649). They used to be pickled
scikit-learn estimators, which bound the shipped artifact to whatever
scikit-learn version the *installing* user resolved; ONNX removes that
coupling. There is deliberately no joblib fallback.

Path resolution goes through :func:`splitsmith.runtime.runtime` so it
works whether the package is installed (wheel) or run from the source
tree, and so ``SPLITSMITH_ARTIFACTS_DIR`` can point at an experimental
artifact set.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import numpy as np
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
    voter_c_onnx_artifacts: dict[str, str] | None = Field(
        default=None,
        description=(
            "Issue #649: filename of the voter C ONNX graph for each "
            'camera class, e.g. ``{"headcam": "voter_c_gbdt_headcam.onnx"}``. '
            "The map is explicit rather than derived from the class name so "
            "a new camera class needs no code change and a hand-assembled "
            "``SPLITSMITH_ARTIFACTS_DIR`` set can name its files freely. "
            "``None`` on artifacts built before the ONNX export -- those "
            "shipped a scikit-learn pickle, which the loader no longer "
            "reads, so it raises and asks for a rebuild."
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
            "Filename of the Voter E probe head ONNX graph in package "
            "data, e.g. ``voter_e_visual_probe.onnx``. ``None`` on "
            "artifacts that pre-date Voter E."
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


DEFAULT_VOTER_E_PROBE_FILENAME = "voter_e_visual_probe.onnx"

# Name skl2onnx gives the probability output when exported with
# ``options={id(clf): {"zipmap": False}}``. Index 1 is the positional
# fallback -- classifier graphs emit ``(label, probabilities)``.
_PROBA_OUTPUT_NAME = "probabilities"


class OnnxProbaModel:
    """One ``onnxruntime`` session behind the ``predict_proba`` call.

    Stands in for the scikit-learn classifiers voter C and voter E used
    to unpickle (issue #649), exposing the only method their call sites
    ever used. Duck-typed on purpose: nothing downstream asks for the
    concrete class, so tests can substitute any object with a
    ``predict_proba``.

    Sessions are cheap to hold and expensive to build, so construct one
    per artifact and keep it on ``EnsembleRuntime``.
    """

    def __init__(self, path: Path) -> None:
        # Imported lazily, matching ``features.py``'s PANN/CLAP branches:
        # onnxruntime pulls in native libraries and nothing should pay
        # for that at import time.
        import onnxruntime as ort

        self.path = path
        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        spec = self._session.get_inputs()[0]
        self._input_name = spec.name
        # skl2onnx declares ``[None, n_features]``: a symbolic batch axis
        # and a fixed feature count. Anything else means the artifact was
        # not exported the way the build script exports it.
        dims = list(spec.shape)
        if len(dims) != 2 or not isinstance(dims[1], int):
            raise ValueError(
                f"{path} declares input shape {spec.shape!r}; expected a "
                "2-D tensor with a fixed feature dimension. Rebuild with "
                "scripts/build_ensemble_artifacts.py."
            )
        self._n_features = int(dims[1])
        names = [out.name for out in self._session.get_outputs()]
        if _PROBA_OUTPUT_NAME in names:
            self._output_name = _PROBA_OUTPUT_NAME
        elif len(names) > 1:
            self._output_name = names[1]
        else:
            raise ValueError(
                f"{path} has no output named {_PROBA_OUTPUT_NAME!r} and only "
                f"{len(names)} output(s) ({names!r}); expected a classifier "
                "graph exported with zipmap disabled."
            )

    @property
    def n_features(self) -> int:
        """Feature-column count the graph declares on its input tensor."""
        return self._n_features

    def predict_proba(self, X: np.ndarray) -> np.ndarray:  # noqa: N803 -- sklearn call convention
        """Per-row class probabilities, shape ``(N, 2)`` ``float64``.

        The graph's input tensor is ``float32`` (``DoubleTensorType``
        converts but the session refuses to instantiate for tree
        ensembles), so the input is cast and made contiguous first. The
        result is widened back to ``float64`` so downstream threshold
        comparisons keep the dtype they had under scikit-learn.
        """
        arr = np.ascontiguousarray(np.asarray(X, dtype=np.float32))
        if arr.ndim != 2 or arr.shape[1] != self._n_features:
            raise ValueError(
                f"{self.path} expects {self._n_features} features per row, "
                f"got array of shape {arr.shape}."
            )
        proba = self._session.run([self._output_name], {self._input_name: arr})[0]
        return np.asarray(proba, dtype=np.float64)


def load_calibration() -> EnsembleCalibration:
    """Read ``ensemble_calibration.json`` from the resolved artifacts dir."""
    path = runtime().artifact("ensemble_calibration.json")
    with path.open("r", encoding="utf-8") as fh:
        return EnsembleCalibration.model_validate(json.load(fh))


def load_voter_c_model(artifacts: Mapping[str, str] | None = None) -> dict[str, OnnxProbaModel]:
    """Load voter C's per-class ONNX classifiers (issues #297, #649).

    Returns a dict keyed by ``camera_class`` -- callers pick the right
    model via ``models[camera_class]`` (with the calibration's
    ``default_camera_class`` as fallback for unknown classes).

    ``artifacts`` is the calibration's ``voter_c_onnx_artifacts`` map;
    pass it when the caller already holds a calibration so the JSON is
    not read twice. ``None`` reads it here.
    """
    names = artifacts if artifacts is not None else load_calibration().voter_c_onnx_artifacts
    resolved = runtime()
    if not names:
        raise RuntimeError(
            "ensemble calibration has no voter_c_onnx_artifacts entry, so "
            "voter C has no model to load (artifacts dir: "
            f"{resolved.artifacts_dir}). Rebuild the artifacts with "
            "`uv run python scripts/build_ensemble_artifacts.py`. Joblib "
            "model artifacts are no longer supported: loading a pickle "
            "would reinstate the scikit-learn version coupling the ONNX "
            "export exists to remove."
        )
    return {camera_class: OnnxProbaModel(resolved.artifact(name)) for camera_class, name in names.items()}


def load_voter_e_probe(filename: str | None = None) -> OnnxProbaModel | None:
    """Load the Voter E linear probe head, or ``None`` if absent.

    Returns ``None`` (rather than raising) when the artifact has not been
    built yet -- callers use that signal to skip wiring Voter E and fall
    back to the audio-only behaviour. The filename comes from the
    ``voter_e_probe_artifact`` field of the calibration.
    """
    name = filename or DEFAULT_VOTER_E_PROBE_FILENAME
    path = runtime().artifacts_dir / name
    if not path.is_file():
        return None
    return OnnxProbaModel(path)
