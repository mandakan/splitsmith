"""Fit the whole-stage decoder (``splitsmith.ensemble.decoder``) for a build.

Inputs are the build's candidate rows with voter C scores that are out of
fold by match (``score_c_lomo``), so the recalibration never sees a score
the shipped model made on its own training data. Steps:

1. Out-of-fold probabilities: for each match, a logistic recalibration
   fitted on the other matches' rows scores that match's rows.
2. Priors from the audited shots: splits (log10 seconds, against a
   rhythm-free reference) and the last shot against the timer stop.
3. The decoder's weights (bias, w_split, w_end) by coordinate descent on
   the out-of-fold probabilities, scored like the eval script (greedy
   matching within the build tolerance).
4. The shipped recalibration on every row.

The decoding is ``decode_stage`` itself, so the build and the runtime
cannot drift apart. Single camera: every term reads one fixture.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression

from splitsmith.ensemble.decoder import (  # noqa: F401  decoder_features is re-exported for callers
    PriorTable,
    StageDecoder,
    decode_stage,
    decoder_features,
    feature_matrix,
)

SPLIT_GRID = (-1.2, 1.2, 0.01)  # log10 seconds
END_GRID = (-1.0, 0.3, 0.002)  # seconds from the timer stop
COUNT_GRID = (-40.0, 40.0, 0.5)  # kept shots minus the round count
W_COUNT_GRID = [0.0, 0.5, 1.0, 2.0, 3.0]
COUNT_F1_SLACK = 0.002
DENSITY_FLOOR = 1e-4
WEIGHT_GRID = {
    "bias": [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0],
    "w_split": [0.0, 0.25, 0.5, 1.0, 2.0],
    "w_end": [0.0, 0.5, 1.0, 2.0],
}


def _kde_log(values: np.ndarray, bw: float, grid: np.ndarray) -> np.ndarray:
    d = (grid[:, None] - values[None, :]) / bw
    dens = np.exp(-0.5 * d * d).mean(axis=1) / (bw * math.sqrt(2 * math.pi))
    return np.log(dens + DENSITY_FLOOR)


def _table(start: float, stop: float, step: float, values: np.ndarray) -> PriorTable:
    return PriorTable(start=start, step=step, values=[round(float(v), 5) for v in values])


def fit_priors(stages: list[dict], *, fast_floor_s: float | None = None) -> tuple[PriorTable, PriorTable]:
    """Split and end priors from audited stages (``truth``, ``beep``,
    ``stage_time``, ``duration``). ``fast_floor_s`` flattens the split prior
    below that split to its value there, so shooting faster than the corpus
    is not penalised; ``None`` keeps the corpus's shape."""
    splits, ends = [], []
    for s in stages:
        t = sorted(s["truth"])
        splits += [math.log10(b - a) for a, b in zip(t, t[1:], strict=False) if b > a]
        stop = s["beep"] + s["stage_time"]
        if t and s["duration"] >= stop + 0.3:
            ends.append(t[-1] - stop)
    lo, hi, step = SPLIT_GRID
    grid = np.arange(lo, hi + step / 2, step)
    split = _kde_log(np.array(splits), 0.06, grid)
    ref = _kde_log(np.random.default_rng(0).uniform(-1.0, 1.0, 4000), 0.2, np.clip(grid, -1.0, 1.0))
    split = split - ref
    if fast_floor_s is not None:
        at = float(np.interp(math.log10(fast_floor_s), grid, split))
        split = np.where(grid < math.log10(fast_floor_s), np.maximum(split, at), split)
    elo, ehi, estep = END_GRID
    egrid = np.arange(elo, ehi + estep / 2, estep)
    end = _kde_log(np.array(ends) if ends else np.zeros(1), 0.02, egrid)
    return _table(lo, hi, step, split), _table(elo, ehi, estep, end)


def fit_count_prior(stages: list[dict]) -> PriorTable | None:
    """Audited shots minus the round count, over the stages that have one
    (extra shots are common; fewer happen when a clip stops early)."""
    extra = [len(s["truth"]) - s["expected"] for s in stages if s.get("expected")]
    if not extra:
        return None
    lo, hi, step = COUNT_GRID
    return _table(
        lo, hi, step, _kde_log(np.array(extra, dtype=float), 1.0, np.arange(lo, hi + step / 2, step))
    )


def _x(rows: list[dict], features: Sequence[str]) -> np.ndarray:
    """The recalibration matrix over ``rows`` (``peak_amp`` is the row key
    for the runtime's ``peak_amplitude``); a feature a row lacks raises."""
    signals: dict[str, np.ndarray] = {}
    for name in features:
        if name == "log_peak_amplitude":
            signals["peak_amplitude"] = np.array([r["peak_amp"] for r in rows])
        else:
            signals[name] = np.array([r[name] for r in rows])
    return feature_matrix(features, signals)


def _logistic(rows: list[dict], features: Sequence[str]) -> LogisticRegression:
    return LogisticRegression(max_iter=3000, C=1.0).fit(
        _x(rows, features), np.array([r["label"] for r in rows])
    )


def match_count(kept: list[float], truth: list[float], tol_s: float) -> tuple[int, int, int]:
    """Greedy nearest matching within ``tol_s``: (tp, fp, fn)."""
    used: set[int] = set()
    tp = 0
    for t in sorted(kept):
        best = None
        for j, s in enumerate(truth):
            if j not in used and abs(s - t) <= tol_s and (best is None or abs(s - t) < abs(truth[best] - t)):
                best = j
        if best is not None:
            used.add(best)
            tp += 1
    return tp, len(kept) - tp, len(truth) - tp


def fit_stage_decoder(
    rows: list[dict],
    stages: dict[str, dict],
    *,
    features: Sequence[str],
    tolerance_ms: float,
    fast_floor_s: float | None = None,
    log: Callable[[str], None] = print,
) -> StageDecoder | None:
    """``rows``: one per candidate with ``fixture``, ``match``, ``t_shot``,
    ``label``, ``score_c`` (out of fold by match) and the other inputs
    ``features`` names (``peak_amp`` for ``log_peak_amplitude``); the
    decoder is fitted on, and decodes with, exactly ``features``. ``stages``: fixture -> ``truth``, ``beep``,
    ``stage_time``, ``duration`` and ``expected`` (the round count or
    ``None``). ``None`` with fewer than two matches."""
    matches = sorted({r["match"] for r in rows})
    if len(matches) < 2:
        log("  decoder: needs at least two matches for out-of-fold scores; skipped")
        return None
    tol = tolerance_ms / 1000.0
    split, end = fit_priors(list(stages.values()), fast_floor_s=fast_floor_s)
    count_prior = fit_count_prior(list(stages.values()))

    # 1. Out-of-fold probabilities, by match.
    oof = np.full(len(rows), np.nan)
    for m in matches:
        train = [r for r in rows if r["match"] != m]
        idx = [i for i, r in enumerate(rows) if r["match"] == m]
        oof[idx] = _logistic(train, features).predict_proba(_x([rows[i] for i in idx], features))[:, 1]
    by_fixture: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        by_fixture.setdefault(r["fixture"], []).append(i)

    model = StageDecoder(
        features=list(features),
        coef=[0.0] * len(features),
        intercept=0.0,
        bias=0.0,
        w_split=0.0,
        w_end=0.0,
        split_prior=split,
        end_prior=end,
    )

    def totals(w: dict, *, counted: bool = False) -> tuple[int, int, int, int]:
        """(tp, fp, fn, sum of squared per-stage errors)."""
        tp = fp = fn = sq = 0
        for fix, idx in by_fixture.items():
            st = stages[fix]
            times = np.array([rows[i]["t_shot"] for i in idx])
            keep = decode_stage(
                model,
                times,
                oof[idx],
                beep_time=st["beep"],
                stage_time=st["stage_time"],
                clip_duration=st["duration"],
                expected_rounds=st.get("expected") if counted else None,
                **w,
            )
            a, b, c = match_count(times[keep].tolist(), st["truth"], tol)
            tp, fp, fn, sq = tp + a, fp + b, fn + c, sq + (b + c) ** 2
        return tp, fp, fn, sq

    def f1(w: dict, *, counted: bool = False) -> float:
        tp, fp, fn, _ = totals(w, counted=counted)
        return 2 * tp / (2 * tp + fp + fn) if tp else 0.0

    # 3. Weights by coordinate descent, two passes from neutral.
    w = {"bias": 0.0, "w_split": 0.0, "w_end": 0.0}
    best = f1(w)
    for _ in range(2):
        for key, values in WEIGHT_GRID.items():
            for v in values:
                trial = {**w, key: v}
                f = f1(trial)
                if f > best + 1e-9:
                    w, best = trial, f
    neutral = totals({"bias": 0.0, "w_split": 0.0, "w_end": 0.0})
    tuned = totals(w)
    # The round-count weight, with the round count given, the rest fixed.
    # Its value is in the worst stages (a camera whose shots score low,
    # another bay's shots that score high), which an average hides: among
    # the weights within COUNT_F1_SLACK of the best F1, take the one with the
    # fewest errors piled on single stages (sum of squared per-stage errors).
    w_count = 0.0
    counted = totals({**w, "w_count": 0.0}, counted=True)
    if count_prior is not None:
        trials = {v: totals({**w, "w_count": v}, counted=True) for v in W_COUNT_GRID}
        f1s = {v: 2 * t[0] / max(1, 2 * t[0] + t[1] + t[2]) for v, t in trials.items()}
        top = max(f1s.values())
        near = [v for v in W_COUNT_GRID if f1s[v] >= top - COUNT_F1_SLACK]
        w_count = min(near, key=lambda v: trials[v][3])
        counted = trials[w_count]
    best_counted = 2 * counted[0] / max(1, 2 * counted[0] + counted[1] + counted[2])
    log(
        f"  decoder: weights {w}, w_count {w_count}; out-of-fold F1 {best * 100:.1f} without the "
        f"round count, {best_counted * 100:.1f} with it, squared stage errors {counted[3]} "
        f"(recalibration alone "
        f"{2 * neutral[0] / max(1, 2 * neutral[0] + neutral[1] + neutral[2]) * 100:.1f})"
    )

    # 4. The shipped recalibration, on every row.
    final = _logistic(rows, features)
    return model.model_copy(
        update={
            "coef": [round(float(c), 6) for c in final.coef_[0]],
            "intercept": round(float(final.intercept_[0]), 6),
            **w,
            "w_count": w_count,
            "fit_report": {
                "matches": matches,
                "fixtures": len(by_fixture),
                "candidates": len(rows),
                "fast_floor_s": fast_floor_s,
                "out_of_fold": {"tp": tuned[0], "fp": tuned[1], "fn": tuned[2], "f1": round(best, 4)},
                "out_of_fold_with_round_count": {
                    "tp": counted[0],
                    "fp": counted[1],
                    "fn": counted[2],
                    "f1": round(best_counted, 4),
                },
                "recalibration_only": {"tp": neutral[0], "fp": neutral[1], "fn": neutral[2]},
            },
        }
    )
