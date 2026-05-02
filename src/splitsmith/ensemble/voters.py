"""Pure voter functions + consensus aggregation.

Each voter takes per-candidate signals + a calibrated threshold and
returns a ``(N,)`` 0/1 ``int`` array. The aggregator sums the votes,
adds the optional apriori boost, and compares against ``consensus``.

No file I/O; no model loading. Testable with synthetic signals.
"""

from __future__ import annotations

import numpy as np


def vote_a(confidences: np.ndarray, voter_a_floor: float) -> np.ndarray:
    """Pass when detector confidence is at or above the calibrated floor."""
    return (confidences >= voter_a_floor).astype(np.int64)


def vote_b(clap_diff: np.ndarray, voter_b_threshold: float) -> np.ndarray:
    """Pass when CLAP shot-vs-not-shot prompt similarity differential clears the threshold."""
    return (clap_diff >= voter_b_threshold).astype(np.int64)


def vote_c_global(gbdt_probs: np.ndarray, voter_c_threshold: float) -> np.ndarray:
    """Voter C with the calibrated global threshold (no apriori info)."""
    return (gbdt_probs >= voter_c_threshold).astype(np.int64)


def vote_c_adaptive(
    gbdt_probs: np.ndarray,
    expected_rounds: int,
    *,
    slack_min: int = 3,
    slack_frac: float = 0.10,
) -> np.ndarray:
    """Voter C in adaptive mode: keep the top-(K+slack) probabilities.

    ``slack = max(slack_min, round(K * slack_frac))``. Cross-bay-heavy
    stages get a tighter cutoff than the global threshold; clean stages
    stay lenient. Returns a ``(N,)`` 0/1 array.
    """
    if gbdt_probs.size == 0 or expected_rounds <= 0:
        return np.zeros_like(gbdt_probs, dtype=np.int64)
    slack = max(slack_min, int(expected_rounds * slack_frac + 0.5))
    target = expected_rounds + slack
    if target >= gbdt_probs.size:
        return np.ones_like(gbdt_probs, dtype=np.int64)
    keep = np.zeros_like(gbdt_probs, dtype=np.int64)
    top_idx = np.argsort(-gbdt_probs)[:target]
    keep[top_idx] = 1
    return keep


def vote_d(gunshot_prob: np.ndarray, voter_d_threshold: float) -> np.ndarray:
    """Pass when the PANN gunshot-class probability clears the threshold."""
    return (gunshot_prob >= voter_d_threshold).astype(np.int64)


def apriori_boost(
    confidences: np.ndarray,
    expected_rounds: int | None,
    boost: float,
) -> np.ndarray:
    """Add a soft prior toward expected-shot count regions.

    When ``expected_rounds`` is provided, the top-N candidates by
    detector confidence get ``+boost`` added to their consensus score.
    Returns a ``(N,)`` ``float`` array of zeros otherwise.
    """
    if expected_rounds is None or expected_rounds <= 0 or confidences.size == 0:
        return np.zeros_like(confidences, dtype=np.float64)
    top = np.argsort(-confidences)[:expected_rounds]
    out = np.zeros_like(confidences, dtype=np.float64)
    out[top] = boost
    return out


def consensus_keep(
    vote_total: np.ndarray,
    apriori: np.ndarray,
    threshold: int,
) -> np.ndarray:
    """Boolean mask: keep when ``vote_total + apriori >= threshold``."""
    score = vote_total.astype(np.float64) + apriori.astype(np.float64)
    return score >= threshold
