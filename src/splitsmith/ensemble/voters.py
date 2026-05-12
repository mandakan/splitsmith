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
    confidence_override: float | None = 0.60,
) -> np.ndarray:
    """Voter C in adaptive mode: keep the top-(K+slack) probabilities.

    ``slack = max(slack_min, round(K * slack_frac))``. Cross-bay-heavy
    stages get a tighter cutoff than the global threshold; clean stages
    stay lenient. Returns a ``(N,)`` 0/1 array.

    Default ``slack_frac=0.10`` retuned on the 30-fixture corpus
    (576 positives, 1713 candidates) via the dashboard sweep at
    ``docs/ensemble_dashboard/findings/2026-05-11_voter_c_slack.md``.
    The previous value 0.25 was tuned in #103 on 4 fixtures / 281 FPs
    where 0.10 lost 4 truth shots; on the broader corpus the recall gap
    closes -- both 0.10 and 0.25 land the same 9 audio-side FNs, but
    0.25 imports 18 extra FPs voter C should reject. Per-camera-class
    sweep confirmed the same plateau holds for headcam and handheld.
    Three stage7 truth shots score below the K-cap at 0.10 and would
    be silenced if not for the audit UI's rejected-marker flow: every
    voter-A candidate stays on the timeline as a rejected marker the
    user can toggle back to ``detected`` with one click, so the FN
    cost there is one click each vs the 18 unattended FPs the wider
    slack imports.

    ``confidence_override`` (issue #103 follow-up): high-scoring
    candidates pass voter C regardless of rank. The K-cap can otherwise
    silence voter C even when its GBDT is very confident -- e.g. real
    shots that score 0.8-0.9 get cut off by the rank cutoff while the
    median FP scores ~0.005. Pass ``None`` to disable.

    Default ``0.60`` retuned on the 30-fixture corpus via
    ``docs/ensemble_dashboard/findings/2026-05-11_override_lift_apriori_dead.md``.
    The previous value 0.75 was tuned on the smaller #103 set (334
    FPs, max FP score 0.73). On the broader corpus the override sweep
    plateaus in [0.60, 0.75] for headcam and rises monotonically as
    you drop it for handheld -- lowering to 0.60 rescues 3 truth
    shots on the handheld iPhone17pro fixtures with **zero new FPs**
    on either class. Per-class evidence is Pareto-clean, which is why
    we land at 0.60 rather than 0.50 (the F1-optimal value, but 0.50
    trades 1 headcam FP for 1 headcam TP -- worse than 0.60 under
    the FP-is-more-dangerous UX argument).
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


def vote_e(probe_score: np.ndarray, voter_e_threshold: float) -> np.ndarray:
    """Pass when the CLIP visual probe's ``P(shot)`` clears the threshold.

    Voter E is calibrated to a target recall (default 0.95) on the
    labeled corpus -- it's a precision veto, not a recall preserver.
    Candidates with ``probe_score < voter_e_threshold`` fail Voter E.
    """
    return (probe_score >= voter_e_threshold).astype(np.int64)


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


def within_stage_amp_veto(
    peak_amps: np.ndarray,
    keep_mask: np.ndarray,
    *,
    expected_rounds: int | None,
    floor_ratio: float,
    fallback_percentile: float = 75.0,
    min_kept_for_filter: int = 4,
) -> np.ndarray:
    """Drop kept candidates whose peak_amp is too small for the stage.

    Headcam-only filter (the caller decides whether to invoke it).
    Anchor: median of the top-K kept by peak_amp where
    ``K = min(expected_rounds, n_kept)``. When ``expected_rounds`` is
    ``None`` or 0, falls back to ``fallback_percentile`` of kept peak_amps.

    Skipped when ``n_kept < min_kept_for_filter`` -- the anchor is too
    fragile on near-empty stages. Returns an updated keep_mask; never
    adds back vetoed candidates.
    """
    if peak_amps.size == 0 or floor_ratio <= 0.0:
        return keep_mask
    kept_idx = np.where(keep_mask)[0]
    n_kept = kept_idx.size
    if n_kept < min_kept_for_filter:
        return keep_mask
    kept_amps = peak_amps[kept_idx]
    if expected_rounds and expected_rounds > 0:
        k = min(int(expected_rounds), n_kept)
        top = np.sort(kept_amps)[-k:]
        anchor = float(np.median(top))
    else:
        anchor = float(np.percentile(kept_amps, fallback_percentile))
    if anchor <= 0.0:
        return keep_mask
    cutoff = floor_ratio * anchor
    out = keep_mask.copy()
    out[kept_idx[kept_amps < cutoff]] = False
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
