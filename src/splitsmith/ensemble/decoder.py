"""Whole-stage decoding: choose a stage's shots as one sequence.

The consensus vote judges each candidate on its own. The decoder instead
picks the best-scoring *set* of candidates for the stage:

    sum over chosen shots of  logit(p_i) + bias
  + w_split * split_prior(t_i - t_prev)   for consecutive chosen shots
  + w_end   * end_prior(t_last - (beep + stage_time))

where ``p_i`` is one logistic recalibration over the voters' signals, the
split prior is the corpus's split distribution against a rhythm-free
reference, and the end prior says the timer stopped on the last shot. A
dynamic program over the candidates finds the exact best set. A round
count, when known, can enter as a count prior (``w_count``); the build
tunes it to 0 on the 2026 corpus.

Everything here is fitted by ``scripts/build_ensemble_artifacts.py`` on
leave-one-match-out voter C scores and shipped in the calibration JSON as a
:class:`StageDecoder`; the runtime evaluates it with numpy alone. It needs
only what detection already waits for (the confirmed beep and the stage
time). The runtime uses it by default (``EnsembleConfig.stage_decoder``;
SPLITSMITH_STAGE_DECODER=0 is the escape hatch).

Measured (2026-10-16): held out by match on the 2026 corpus (230 fixtures,
five matches), F1 94.2 -> 96.0 with round counts and 93.3 -> 95.9 without,
headcams 86.5 -> 92.0 blind; on a match no model had seen (ESs Black
Handgun 2026, 20 fixtures) with the shipped voters, 95.0 -> 96.5 with round
counts and 93.2 -> 96.5 without, no stage three errors worse. The stages it
does worse on are a camera whose shots score low and shots from another bay
that score high.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np
from pydantic import BaseModel, Field

#: Every signal the recalibration can read. A fitted decoder's own
#: ``features`` list (a subset, in coefficient order) is what it decodes with.
FEATURES: tuple[str, ...] = (
    "score_c",
    "clap_diff",
    "confidence",
    "log_peak_amplitude",
    "vote_b",
)

#: The features each model set's decoders are fitted on: ``base`` has no
#: CLAP, so no ``clap_diff`` and no voter B.
DECODER_FEATURES_BY_SET: dict[str, tuple[str, ...]] = {
    "base": ("score_c", "confidence", "log_peak_amplitude"),
    "clap": FEATURES,
}


class PriorTable(BaseModel):
    """A log density sampled on an even grid; evaluated by linear
    interpolation and held at its end values outside the grid."""

    start: float
    step: float
    values: list[float]

    def __call__(self, x: np.ndarray) -> np.ndarray:
        grid = self.start + self.step * np.arange(len(self.values))
        return np.interp(x, grid, np.asarray(self.values, dtype=np.float64))


class StageDecoder(BaseModel):
    """A fitted decoder, as shipped in ``ensemble_calibration.json``."""

    features: list[str] = Field(default_factory=lambda: list(FEATURES))
    coef: list[float]
    intercept: float
    bias: float = Field(description="Added to every candidate's logit: the keep threshold.")
    w_split: float
    w_end: float
    split_prior: PriorTable = Field(description="Over log10(split seconds), minus the reference.")
    end_prior: PriorTable = Field(description="Over last shot minus (beep + stage time), seconds.")
    min_split_s: float = Field(default=0.09, description="No two shots closer than this.")
    pre_beep_s: float = Field(default=0.3, description="No shot sooner than this after the beep.")
    after_stop_s: float = Field(default=0.25, description="No shot later than this after the timer stopped.")
    end_cover_s: float = Field(
        default=0.3,
        description=(
            "The stage-time terms apply only when the clip runs at least this far past "
            "the timer stop; a clip that ends early says nothing about its last shot."
        ),
    )
    w_count: float = Field(
        default=0.0,
        description=(
            "Weight of the round-count prior, used only when the stage's round count is "
            "known. It pulls the number of kept shots toward a plausible count both ways: "
            "a camera whose shots score low keeps them, and shots from another bay that "
            "score high are dropped."
        ),
    )
    count_prior: PriorTable | None = Field(default=None, description="Over kept shots minus the round count.")
    fit_report: dict = Field(default_factory=dict, description="Held-out figures from the build.")


def feature_matrix(features: Sequence[str], signals: Mapping[str, np.ndarray]) -> np.ndarray:
    """The recalibration's matrix with one column per name in ``features``.
    ``log_peak_amplitude`` is computed from ``signals["peak_amplitude"]``;
    every other name is a signal key. A name missing from ``signals`` raises
    ``KeyError``. The fit and the runtime both go through here."""
    cols = []
    for name in features:
        if name == "log_peak_amplitude":
            cols.append(np.log(np.maximum(np.asarray(signals["peak_amplitude"], dtype=np.float64), 1e-4)))
        else:
            cols.append(np.asarray(signals[name], dtype=np.float64))
    return np.column_stack(cols)


def decoder_features(model: StageDecoder, signals: Mapping[str, np.ndarray]) -> np.ndarray:
    """The recalibration matrix for ``model``, in its own ``features`` order."""
    return feature_matrix(model.features, signals)


def shot_probabilities(model: StageDecoder, x: np.ndarray) -> np.ndarray:
    z = x @ np.asarray(model.coef, dtype=np.float64) + model.intercept
    return 1.0 / (1.0 + np.exp(-z))


def decode_stage(
    model: StageDecoder,
    times: np.ndarray,
    probs: np.ndarray,
    *,
    beep_time: float,
    stage_time: float,
    clip_duration: float,
    bias: float | None = None,
    w_split: float | None = None,
    w_end: float | None = None,
    w_count: float | None = None,
    expected_rounds: int | None = None,
) -> np.ndarray:
    """The kept mask: the best-scoring set of candidates for one stage.

    ``times`` are the candidates' shot times in clip seconds (any order);
    ``probs`` their recalibrated shot probabilities. With ``expected_rounds``
    (the stage's round count) and a count prior, the number of kept shots
    is part of the score. The keyword weights override the model's, which
    is how the build tunes them.
    """
    times = np.asarray(times, dtype=np.float64)
    n = len(times)
    keep = np.zeros(n, dtype=bool)
    if n == 0:
        return keep
    bias = model.bias if bias is None else bias
    w_split = model.w_split if w_split is None else w_split
    w_end = model.w_end if w_end is None else w_end
    w_count = model.w_count if w_count is None else w_count

    order = np.argsort(times, kind="stable")
    t = times[order]
    p = np.clip(np.asarray(probs, dtype=np.float64)[order], 1e-4, 1 - 1e-4)
    u = np.log(p / (1 - p)) + bias

    stop = beep_time + stage_time
    has_end = clip_duration >= stop + model.end_cover_s
    blocked = t < beep_time + model.pre_beep_s
    if has_end:
        blocked |= t > stop + model.after_stop_s
    u = np.where(blocked, -math.inf, u)
    end = w_end * model.end_prior(t - stop) if has_end else np.zeros(n)

    gap = t[None, :] - t[:, None]  # gap[j, i] = t_i - t_j
    trans = np.full((n, n), -math.inf)
    ok = gap >= model.min_split_s
    trans[ok] = w_split * model.split_prior(np.log10(gap[ok]))

    if expected_rounds and model.count_prior is not None and w_count > 0:
        return _decode_counted(
            t, order, u, end, trans, w_count * model.count_prior(np.arange(n + 1) - expected_rounds)
        )

    # best[i]: the best chain of chosen shots ending at candidate i.
    best = u.copy()
    back = np.full(n, -1, dtype=np.int64)
    for i in range(1, n):
        if not np.isfinite(u[i]):
            continue
        cand = best[:i] + trans[:i, i]
        j = int(np.argmax(cand))
        if np.isfinite(cand[j]) and cand[j] + u[i] > best[i]:
            best[i] = cand[j] + u[i]
            back[i] = j
    total = best + end
    i = int(np.argmax(total))
    if not np.isfinite(total[i]) or total[i] <= 0:
        return keep  # no chain scores better than choosing nothing
    while i >= 0:
        keep[order[i]] = True
        i = int(back[i])
    return keep


def _decode_counted(
    t: np.ndarray, order: np.ndarray, u: np.ndarray, end: np.ndarray, trans: np.ndarray, count: np.ndarray
) -> np.ndarray:
    """The same chain search with the number of shots in the state:
    ``best[i, k]`` is the best chain of ``k`` shots ending at ``i``, and the
    count prior ``count[k]`` is added once at the end."""
    n = len(t)
    keep = np.zeros(n, dtype=bool)
    best = np.full((n, n + 1), -math.inf)
    back = np.full((n, n + 1), -1, dtype=np.int64)
    best[:, 1] = u
    for i in range(1, n):
        if not np.isfinite(u[i]):
            continue
        prev = best[:i, :-1] + trans[:i, i][:, None]  # chains of k-1 shots, then i
        j = np.argmax(prev, axis=0)
        val = prev[j, np.arange(n)] + u[i]
        better = np.isfinite(val) & (val > best[i, 1:])
        best[i, 1:] = np.where(better, val, best[i, 1:])
        back[i, 1:] = np.where(better, j, back[i, 1:])
    total = best + end[:, None] + count[None, :]
    i, k = np.unravel_index(int(np.argmax(total)), total.shape)
    if not np.isfinite(total[i, k]) or total[i, k] <= count[0]:
        return keep  # choosing nothing scores at least as well
    while i >= 0 and k >= 1:
        keep[order[i]] = True
        i, k = int(back[i, k]), k - 1
    return keep
