"""4-voter ensemble shot detection.

Composes four detectors over the same per-candidate universe:

* Voter A -- ``shot_detect.detect_shots`` envelope onsets, gated by an
  auto-calibrated ``min_confidence`` floor.
* Voter B -- CLAP zero-shot threshold on (shot - not-shot) prompt cosine
  similarity differential.
* Voter C -- ``sklearn`` GradientBoostingClassifier on hand-crafted
  features + per-prompt CLAP similarities. Includes a per-stage adaptive
  override (top-(K+slack)) when the audit JSON has
  ``stage_rounds.expected``.
* Voter D -- PANNs CNN14 ``Gunshot, gunfire`` class probability threshold.

Consensus: a candidate is kept when ``vote_total + apriori_boost >=
consensus`` (default 3-of-4). The apriori boost biases toward the
expected-shot count when the audit JSON carries ``stage_rounds.expected``.

Public API: ``detect_shots_ensemble`` returns the consensus shots and the
full voter-A universe with per-voter signals attached. Heavy models
(CLAP, PANN, the GBDT classifier) are loaded once via
``load_ensemble_runtime`` and reused across calls.

Calibration thresholds and the trained GBDT are shipped in the package's
``data/`` directory; rebuild them with
``scripts/build_ensemble_artifacts.py`` after adding fixtures.
"""

from .api import (
    EnsembleCandidate,
    EnsembleConfig,
    EnsembleResult,
    EnsembleRuntime,
    detect_shots_ensemble,
    load_ensemble_runtime,
)
from .calibration import (
    EnsembleCalibration,
    load_calibration,
    load_voter_c_model,
)

__all__ = [
    "EnsembleCalibration",
    "EnsembleCandidate",
    "EnsembleConfig",
    "EnsembleResult",
    "EnsembleRuntime",
    "detect_shots_ensemble",
    "load_calibration",
    "load_ensemble_runtime",
    "load_voter_c_model",
]
