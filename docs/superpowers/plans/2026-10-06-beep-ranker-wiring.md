# Beep learned ranker, PR 3 (wiring) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `detect_beep` ranks candidates with the logistic-regression ranker and sets each candidate's confidence from the calibrated head; auto-trust moves to 0.97.

**Architecture:** The trainer's final LR fit and a final confidence head (fitted on the out-of-fold logits of all 127 fixtures) are written into the report. Their numbers become the defaults of a `BeepRankerConfig` on `BeepDetectConfig`, pinned to the report by a test. `detect_beep` scores each run with them: a standardised linear logit, the probability for ranking, and the head over (logit, margin) for confidence. `ranker: "heuristic"` keeps today's path intact.

**Tech Stack:** Python 3.11+, numpy, Pydantic, pytest; scikit-learn in the trainer only.

**Spec:** `docs/superpowers/specs/2026-10-06-beep-learned-ranker-design.md`, sections 3-5 and "Decision after the report" (ship LR, auto-trust 0.97).

**Precondition:** PR #1252 (trainer + report) merged. Branch `feat/beep-ranker-wiring-949` from `origin/main`.

## Global Constraints

- No new runtime dependency; nothing under `src/` imports scikit-learn.
- `detect_beep` stays pure. Run segmentation, the cutoff, the onset walk, search windows and the 14 call sites are unchanged.
- The shipped ranker's numbers live in `config.py` as defaults and must equal the report's `final_fit` exactly (test-pinned). A YAML override is allowed; the test pins the defaults only.
- Logits are clamped to +/-10 (`LOGIT_CLAMP`), and a lone candidate's margin uses -10 as the best other logit, exactly as the trainer.
- `AutomationConfig.beep_low_confidence_threshold` default 0.95 -> 0.97.
- Old project JSON (no `features`, no `ranker_version`) still loads.

## Review Focus

1. **A candidate whose features are missing** (a `_Run` always has them, but a caller could pass a config with a shorter coefficient list). Expected: a `ValueError` naming the mismatch at config validation, never a silent zip truncation. Test in Task 2.
2. **Ties in probability.** Expected: the stable sort keeps time order, as the heuristic does. Test in Task 3.
3. **A single-candidate clip.** Expected: its confidence comes from margin = logit + 10, finite. Test in Task 3.
4. **`ranker: "heuristic"`.** Expected: byte-for-byte today's times, scores and confidences on pinned fixtures. Test in Task 3.
5. **A user whose settings already pin `beep_low_confidence_threshold`.** Expected: their value wins over the new default (layering is unchanged). Test in Task 4.

---

### Task 1: the trainer emits the final head and its bins

**Files:**
- Modify: `scripts/train_beep_ranker.py`
- Modify: `tests/fixtures/beep_calibration/ranker_report.json` (regenerated)
- Test: `tests/test_train_beep_ranker.py`

**Interfaces:**
- Produces: `fit_final_head(clips, probs) -> dict` returning `{"coef": [a, b], "intercept": c}` fitted on every clip with rows (out-of-fold logits and margins, target as in `oof_head_confidence`). `report["models"]["lr"]["final_fit"]["head"]` holds it; `report["models"]["lr"]["final_head_bins"]` holds `[{"threshold": t, "n": n, "wrong": w}]` for t in (0.95, 0.97, 0.99), computed by applying the final head to the out-of-fold logits.

- [ ] **Step 1: Failing test**

```python
def test_the_final_head_is_fitted_on_every_clip_with_rows(monkeypatch) -> None:
    m = _script()
    _, head_fits = _spy_hooks(m, monkeypatch)
    clips = [
        _clip(m, "stage-shots-a-2026-stage1-s0", [True, False], [0.9, 0.1]),
        _clip(m, "stage-shots-b-2026-stage1-s0", [False, False], [0.8, 0.2]),
    ]
    probs = {c.stem: [r.features[0] for r in c.rows] for c in clips}
    m.fit_final_head(clips, probs)
    assert head_fits[-1][0] == ["stage-shots-a-2026-stage1-s0", "stage-shots-b-2026-stage1-s0"]
```

(`_SpyModel` has no `coef_`; give it `coef_ = np.array([[0.0, 0.0]])` and `intercept_ = np.array([0.0])` as class attributes in the test helper.)

- [ ] **Step 2:** run it, expect `AttributeError: ... fit_final_head`.
- [ ] **Step 3:** implement `fit_final_head` reusing `oof_head_confidence`'s `table` logic (lift `table` to a module-level `_head_table(clips, probs)` and use it in both), calling `_make_head()`, `_head_fit_hook(head, clips)`, `head.fit(x, y)`; return the rounded coefficients. In `main`, for `lr` only, add `final_fit["head"]` and `final_head_bins` (apply `sigmoid(a*z + b*margin + c)` to each clip's out-of-fold winner).
- [ ] **Step 4:** tests pass; regenerate the report; confirm `final_head_bins` at 0.97 shows `wrong: 0` (if not, stop and report to the user: the decision rested on the out-of-fold heads).
- [ ] **Step 5:** commit `feat(beep): the trainer emits the final confidence head (#949)`.

### Task 2: `BeepRankerConfig`

**Files:**
- Modify: `src/splitsmith/config.py` (new model, field on `BeepDetectConfig`, `BeepDetection.ranker_version`)
- Test: `tests/test_beep_ranker_config.py`

**Interfaces:**
- Produces:

```python
class BeepRankerConfig(BaseModel):
    ranker: Literal["learned", "heuristic"] = "learned"
    model_version: str = "<report final_fit.model_version>"
    features: tuple[str, ...] = (<FEATURE_NAMES>)
    mean: tuple[float, ...] = (...)
    scale: tuple[float, ...] = (...)
    coef: tuple[float, ...] = (...)
    intercept: float = ...
    head_coef: tuple[float, float] = (...)
    head_intercept: float = ...
```

with a `model_validator(mode="after")` raising `ValueError` unless `mean`, `scale`, `coef` and `features` have equal length and `features == beep_features.FEATURE_NAMES`. `BeepDetectConfig.ranker: BeepRankerConfig = Field(default_factory=BeepRankerConfig)`. `BeepDetection.ranker_version: str | None = None`.

- [ ] **Step 1: Failing tests**

```python
import json
from pathlib import Path

import pytest

from splitsmith.config import BeepDetection, BeepDetectConfig, BeepRankerConfig

REPORT = Path(__file__).resolve().parent / "fixtures" / "beep_calibration" / "ranker_report.json"


def test_the_shipped_ranker_is_the_reports_final_fit() -> None:
    fit = json.loads(REPORT.read_text())["models"]["lr"]["final_fit"]
    shipped = BeepRankerConfig()
    assert shipped.model_version == fit["model_version"]
    assert list(shipped.features) == fit["features"]
    assert list(shipped.mean) == fit["mean"]
    assert list(shipped.scale) == fit["scale"]
    assert list(shipped.coef) == fit["coef"]
    assert shipped.intercept == fit["intercept"]
    assert list(shipped.head_coef) == fit["head"]["coef"]
    assert shipped.head_intercept == fit["head"]["intercept"]


def test_a_coefficient_list_of_the_wrong_length_is_refused() -> None:
    with pytest.raises(ValueError, match="coef"):
        BeepRankerConfig(coef=(1.0,))


def test_the_detector_config_carries_the_learned_ranker_by_default() -> None:
    assert BeepDetectConfig().ranker.ranker == "learned"


def test_an_old_detection_without_a_ranker_version_loads() -> None:
    old = {"time": 1.0, "peak_amplitude": 0.1, "duration_ms": 300.0}
    assert BeepDetection.model_validate(old).ranker_version is None
```

- [ ] **Step 2:** run, expect ImportError on `BeepRankerConfig`.
- [ ] **Step 3:** implement; copy the numbers from the report's `final_fit` (a one-off `python -c` that prints the literal tuples is fine; paste them).
- [ ] **Step 4:** tests pass; also edit one coefficient by 1e-6 and confirm the first test fails, then revert.
- [ ] **Step 5:** commit `feat(beep): the learned ranker's coefficients as config (#949)`.

### Task 3: `detect_beep` ranks with the learned ranker

**Files:**
- Modify: `src/splitsmith/beep_detect.py` (ranking in `detect_beep`; a pure `_learned_scores(runs, ranker) -> list[tuple[float, float]]` returning (probability, confidence) per run)
- Modify: `src/splitsmith/config.py` (`BeepCandidate.score` / `confidence` docstrings)
- Test: `tests/test_beep_learned_ranking.py`

**Interfaces:**
- Consumes: `BeepRankerConfig` (Task 2), `_Run.features`, `feature_vector`.
- Produces: with `ranker="learned"`, `BeepCandidate.score` = probability, `confidence` = head output, candidates sorted by probability (stable), `BeepDetection.ranker_version = ranker.model_version`. With `"heuristic"`, everything as today and `ranker_version = "heuristic"`.

- [ ] **Step 1: Failing tests**

```python
from pathlib import Path

import pytest

from splitsmith.beep_calibration import load_manifest
from splitsmith.beep_detect import detect_beep, load_audio
from splitsmith.config import BeepDetectConfig, BeepRankerConfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MANIFEST = load_manifest(FIXTURES / "beep_calibration" / "manifest.yaml")


def _entry(stem: str):
    return next(e for e in MANIFEST.fixtures if e.stem == stem)


# The heuristic gets this one wrong; the out-of-fold LR gets it right
# (ranker_report.json, models.lr.fixed, one of 42).
FIXED = "stage-shots-blacksmith-2026-stage6-s97dcec94-apple-iphone17pro"


def test_the_learned_ranker_picks_the_beep_the_heuristic_missed() -> None:
    e = _entry(FIXED)
    audio, sr = load_audio(FIXTURES / e.clip_wav)
    learned = detect_beep(audio, sr, BeepDetectConfig())
    heuristic = detect_beep(audio, sr, BeepDetectConfig(ranker=BeepRankerConfig(ranker="heuristic")))
    tol = e.tolerance_ms / 1000.0
    assert abs(learned.time - e.ground_truth_in_clip) <= tol
    assert abs(heuristic.time - e.ground_truth_in_clip) > tol
    assert learned.ranker_version == BeepRankerConfig().model_version
    assert heuristic.ranker_version == "heuristic"


def test_candidates_are_ordered_by_probability_and_confidence_is_the_heads() -> None:
    e = MANIFEST.fixtures[1]
    audio, sr = load_audio(FIXTURES / e.clip_wav)
    d = detect_beep(audio, sr, BeepDetectConfig(top_n_candidates=1000))
    scores = [c.score for c in d.candidates]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= c.confidence <= 1.0 for c in d.candidates)
    assert d.confidence == d.candidates[0].confidence
    assert d.candidates[0].confidence >= max(c.confidence for c in d.candidates[1:])


def test_a_lone_candidate_gets_a_finite_confidence() -> None:
    from splitsmith.beep_detect import _learned_scores

    run = _single_run_fixture()
    [(prob, conf)] = _learned_scores([run], BeepRankerConfig())
    assert 0.0 < prob < 1.0 and 0.0 <= conf <= 1.0


def test_equal_probabilities_keep_time_order() -> None:
    from splitsmith.beep_detect import _learned_scores

    run = _single_run_fixture()
    scores = _learned_scores([run, run], BeepRankerConfig())
    assert scores[0] == scores[1]
```

Put this helper at the top of the test file (add `import numpy as np`):

```python
def _single_run_fixture():
    from splitsmith.beep_detect import _candidate_runs

    sr = 48_000
    audio = np.random.default_rng(0).normal(0.0, 0.002, 2 * sr).astype(np.float32)
    t = np.arange(int(0.4 * sr)) / sr
    audio[sr : sr + t.size] += (0.5 * np.sin(2 * np.pi * 3000.0 * t)).astype(np.float32)
    runs = _candidate_runs(audio, sr, BeepDetectConfig())
    assert len(runs) == 1
    return runs[0]
``` The tie test asserts equal scores; the ordering half is covered by `sorted` being stable, so assert in `detect_beep`'s code review that the sort key is the probability alone.

Also extend `tests/test_beep_regression.py` with:

```python
def _pinned_heuristic() -> list[str]:
    baseline = json.loads((CALIBRATION_DIR / "baseline_heuristic.json").read_text())
    return sorted(r["stem"] for r in baseline["results"] if r["track"] == "clip" and r["correct_top1"])


@pytest.mark.parametrize("stem", _pinned_heuristic()[:5])
def test_the_heuristic_ranker_still_reproduces_today(stem: str) -> None:
    entry = next(e for e in load_manifest(CALIBRATION_DIR / "manifest.yaml").fixtures if e.stem == stem)
    audio, sample_rate = load_audio(FIXTURES_DIR / entry.clip_wav)
    config = BeepDetectConfig(ranker=BeepRankerConfig(ranker="heuristic"))

    detected = detect_beep(audio, sample_rate, config).time

    assert abs(detected - entry.ground_truth_in_clip) * 1000.0 <= entry.tolerance_ms
```

(`BeepRankerConfig` joins the file's `splitsmith.config` import.)

reading the *pre-PR* pinned set from a copy committed as `tests/fixtures/beep_calibration/baseline_heuristic.json` (copy today's `baseline.json` before Task 5 regenerates it).

- [ ] **Step 2:** run, expect failures (`_learned_scores` missing; `ranker` field unknown until Task 2 merged into this branch).
- [ ] **Step 3:** implement:

```python
def _learned_scores(runs: list[_Run], ranker: BeepRankerConfig) -> list[tuple[float, float]]:
    """(probability, confidence) per run, the trainer's exact arithmetic:
    standardised linear logit clamped to +/-LOGIT_CLAMP, the probability
    for ranking, and the confidence head over (logit, margin to the best
    other run)."""
    logits = []
    for run in runs:
        x = feature_vector(run.features)
        z = ranker.intercept + sum(c * (v - m) / s for c, v, m, s in zip(ranker.coef, x, ranker.mean, ranker.scale, strict=True))
        logits.append(max(-LOGIT_CLAMP, min(LOGIT_CLAMP, z)))
    out = []
    for i, z in enumerate(logits):
        others = [o for j, o in enumerate(logits) if j != i]
        margin = z - (max(others) if others else -LOGIT_CLAMP)
        a, b = ranker.head_coef
        out.append((_sigmoid(z), _sigmoid(a * z + b * margin + ranker.head_intercept)))
    return out
```

In `detect_beep`, branch on `config.ranker.ranker`: `"heuristic"` runs today's loop unchanged; `"learned"` sorts runs by probability (stable), uses the probability as `score` and the head as `confidence`. Set `ranker_version`. Note the trainer clamps `logit(predict_proba)`, which equals the clamped linear decision value up to float rounding; Task 5 measures the agreement.
- [ ] **Step 4:** tests pass. Mutation: drop the clamp, confirm the lone-candidate test or a parity test fails; swap `head_coef` order, confirm a test fails. Revert.
- [ ] **Step 5:** commit `feat(beep): rank beep candidates with the learned ranker (#949)`.

### Task 4: auto-trust at 0.97

**Files:**
- Modify: `src/splitsmith/automation.py` (default and docstring history)
- Test: `tests/test_automation*.py` (find the file that pins the default; `grep -rn beep_low_confidence_threshold tests`)

- [ ] **Step 1:** failing test: the resolved default is 0.97; a user-layer setting of 0.9 still resolves to 0.9.
- [ ] **Step 2:** run, expect the default assertion to fail.
- [ ] **Step 3:** change the default; replace the docstring's last paragraph with the history plus: "Raised to 0.97 with the learned ranker (#949): out of fold over 127 fixtures, 70 beeps clear it and none is wrong (`ranker_report.json`)."
- [ ] **Step 4:** tests pass; grep the SPA for a hard-coded 0.95 tied to this setting (`lib/api.ts` reads it from the server) and update any copy that quotes the number.
- [ ] **Step 5:** commit `feat(automation): auto-trust a beep at 0.97 confidence (#949)`.

### Task 5: baselines, losses, docs

- [ ] **Step 1:** `uv run python scripts/eval_beep_detector.py --track clip --json tests/fixtures/beep_calibration/baseline.json`. Record top-1, top-N and the bins. These are in-sample for the final fit (it saw every fixture), so expect them at or above the out-of-fold 106/127; say so in the PR.
- [ ] **Step 2:** list every fixture in `baseline_heuristic.json`'s correct set that is now wrong, with both candidate tables (from the trainer's `candidate_table` on this run). The PR body carries the list for the user to accept.
- [ ] **Step 3:** `uv run pytest -q tests/test_beep_regression.py` now pins the new correct set.
- [ ] **Step 4:** update `BeepCandidate`'s docstring (score = ranker probability, confidence = calibrated head; point at `ranker_report.json`), the beep_detect module docstring's "Composite scoring" section (learned by default, heuristic on `ranker: heuristic`), CLAUDE.md's detection section (one paragraph: the ranker, where its numbers come from, how to retrain: run the trainer, paste `final_fit` into `BeepRankerConfig`, the pin test enforces it).
- [ ] **Step 5:** full suite, lint; commit `test(beep): pin the learned ranker's baseline (#949)`.
- [ ] **Step 6:** whole-branch review (detection change), then PR. Ask the user to run the full-track eval on the Mac (spec step 4) before release.
