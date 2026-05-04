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
    slack_frac: float = 0.25,
    confidence_override: float | None = 0.75,
) -> np.ndarray:
    """Voter C in adaptive mode: keep the top-(K+slack) probabilities.

    ``slack = max(slack_min, round(K * slack_frac))``. Cross-bay-heavy
    stages get a tighter cutoff than the global threshold; clean stages
    stay lenient. Returns a ``(N,)`` 0/1 array.

    Default ``slack_frac=0.25`` was tuned (issue #103) on 281 hand-
    labeled FPs across 4 fixtures: it recovers 100 % of audited truth
    shots while letting only 3 / 281 labeled FPs slip past voter C
    (vs the original 0.10 which missed 4 truth shots).

    ``confidence_override`` (issue #103 follow-up): high-scoring
    candidates pass voter C regardless of rank. The K-cap can otherwise
    silence voter C even when its GBDT is very confident -- e.g. real
    shots that score 0.8-0.9 get cut off by the rank cutoff while the
    median FP scores ~0.005. The default 0.75 was tuned on 334 labeled
    FPs (max score 0.73, 13 / 334 ≥ 0.10) and 226 truth shots (the two
    rank-cut FNs scored 0.80 / 0.90). Pass ``None`` to disable.
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
    if confidence_override is not None:
        keep[gbdt_probs >= confidence_override] = 1
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
    *,
    vote_c: np.ndarray | None = None,
    c_required: bool = False,
) -> np.ndarray:
    """Boolean mask: keep when ``vote_total + apriori >= threshold``.

    When ``c_required`` is True (issue #103), voter C must additionally
    say yes -- candidates with ``vote_c == 0`` are dropped regardless
    of how many other voters agreed. This is the C-veto rule that, on
    the labeled fixture set, suppresses 281/281 hand-labeled FPs while
    holding 100 % recall (paired with the broadened voter-C adaptive
    slack; see :func:`vote_c_adaptive`).
    """
    score = vote_total.astype(np.float64) + apriori.astype(np.float64)
    keep = score >= threshold
    if c_required:
        if vote_c is None:
            raise ValueError("c_required=True requires vote_c to be passed")
        keep = keep & (vote_c.astype(bool))
    return keep
