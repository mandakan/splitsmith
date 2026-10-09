# Beep learned ranker, PRs 1-2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose timer-agnostic per-candidate features from `beep_detect` (PR 1), then measure whether a model fitted on them beats today's ranking by the spec's gate, writing a committed report (PR 2).

**Architecture:** A pure `beep_features.candidate_features` computes a `BeepFeatures` model for every run `detect_beep` already finds; `detect_beep` attaches it to each `BeepCandidate` without changing the ranking. A trainer script runs `detect_beep` over the 127-fixture manifest, fits logistic regression and a GBDT under leave-one-match-out, fits a confidence head on the out-of-fold logits, applies the gate and writes `ranker_report.json`.

**Tech Stack:** Python 3.11+, numpy, scipy, Pydantic, pytest; scikit-learn (dev group only, never imported under `src/`).

**Spec:** `docs/superpowers/specs/2026-10-06-beep-learned-ranker-design.md`

**Scope of this plan:** spec section 6, steps 1 and 2. Step 3 (wiring the ranker into `detect_beep`) forks on step 2's result (LR in config vs GBDT as ONNX) and gets its own plan once the report exists. If the gate fails, the work ends with PR 2.

## Global Constraints

- No new runtime dependency. scikit-learn stays in `[dependency-groups].dev`; nothing under `src/` imports it.
- Detection stays pure: audio + config in, `BeepDetection` out, no file I/O.
- PR 1 must not change any detected time, score, confidence or candidate order. Proven by re-running the eval and diffing against `tests/fixtures/beep_calibration/baseline.json`.
- Features are timer-agnostic: no peak frequency, no position in the window.
- Gate (spec section 2.6), out of fold, end to end over 127 fixtures: top-1 >= 78/127 (61.2 %, today 65/127 + 10 pp), top-N >= 105/127 (82.7 %), zero wrong fixtures in the >=0.95 confidence bin.
- LR ships if its out-of-fold end-to-end top-1 is within 2 pp of the GBDT's.
- Grouping for cross-validation is by match: `re.match(r"^stage-shots-(.+?)-stage\d+", stem)`, group 1; any other stem is its own group (`beep-test`). That gives 7 groups today (hfo-masters-2026 has 48 fixtures).
- Run the suite with `uv run pytest`; `-n0` for a focused run. Black line length 110, ruff clean.
- Commit messages end with the session's Co-Authored-By / Claude-Session lines.

## Review Focus

1. **A clip where `detect_beep` raises `BeepNotFoundError`.** Expected: the trainer records the fixture as an unreachable end-to-end miss and carries on; it never crashes the run. Test in Task 3.
2. **A clip with a single candidate.** Expected: the confidence head's margin input is defined (best other logit is the clamp floor, -10), not NaN or an exception. Test in Task 4.
3. **Per-fixture tolerance.** Expected: a candidate is labeled positive against that fixture's own `tolerance_ms` (15 ms for some, 100 ms for others), never a global constant. Test in Task 3.
4. **Two candidates within tolerance of the truth.** Expected: both are positive, and top-1 counts as correct if either wins. Test in Task 3.
5. **A model that is confident and wrong.** Expected: one such fixture at >=0.95 fails the gate even when top-1 and top-N pass. Test in Task 4.

---

## PR 1: features, no behaviour change

Branch: `feat/beep-features-949` from `origin/main`, worktree `.claude/worktrees/beep-features`.

### Task 1: `BeepFeatures` and `candidate_features`

**Files:**
- Modify: `src/splitsmith/config.py` (add `BeepFeatures` above `BeepCandidate`, add `features` field to `BeepCandidate`)
- Create: `src/splitsmith/beep_features.py`
- Test: `tests/test_beep_features.py`

**Interfaces:**
- Produces:
  - `splitsmith.config.BeepFeatures` (Pydantic): `log_silence: float`, `tonal_ratio: float`, `duration_ms: float`, `log_peak_over_floor: float`, `peak_over_global: float`, `spectral_flatness: float`, `log_spectral_prominence: float`.
  - `BeepCandidate.features: BeepFeatures | None = None`.
  - `splitsmith.beep_features.FEATURE_NAMES: tuple[str, ...]` (field order of `BeepFeatures`).
  - `splitsmith.beep_features.candidate_features(audio: np.ndarray, sample_rate: int, *, run_start: int, run_end: int, run_peak: float, noise_floor: float, global_peak: float, silence_score: float, tonal_ratio: float, band_lo_hz: float, band_hi_hz: float) -> BeepFeatures`.
  - `splitsmith.beep_features.feature_vector(features: BeepFeatures) -> list[float]` (values in `FEATURE_NAMES` order).

- [ ] **Step 1: Write the failing tests**

`tests/test_beep_features.py`:

```python
"""Per-candidate beep features (#949, spec 2026-10-06 section 1)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from splitsmith.beep_features import FEATURE_NAMES, candidate_features, feature_vector
from splitsmith.config import BeepCandidate, BeepFeatures

SR = 48_000


def _features(audio: np.ndarray, *, gain: float = 1.0) -> BeepFeatures:
    peak = float(np.abs(audio).max())
    return candidate_features(
        audio * gain,
        SR,
        run_start=0,
        run_end=audio.size,
        run_peak=peak * gain,
        noise_floor=0.01 * gain,
        global_peak=2.0 * peak * gain,
        silence_score=8.0,
        tonal_ratio=0.9,
        band_lo_hz=2000,
        band_hi_hz=5000,
    )


def _tone(seconds: float = 0.4, hz: float = 3000.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _noise(seconds: float = 0.4) -> np.ndarray:
    return np.random.default_rng(0).normal(0.0, 0.2, int(seconds * SR)).astype(np.float32)


def test_a_tone_is_not_flat_and_is_prominent() -> None:
    tone, noise = _features(_tone()), _features(_noise())
    assert tone.spectral_flatness < 0.05 < 0.3 < noise.spectral_flatness
    assert tone.log_spectral_prominence > noise.log_spectral_prominence + 3.0


def test_features_do_not_move_with_recording_gain() -> None:
    quiet, loud = _features(_tone()), _features(_tone(), gain=10.0)
    for name in FEATURE_NAMES:
        assert getattr(loud, name) == pytest.approx(getattr(quiet, name), rel=1e-4, abs=1e-6), name


def test_the_ratios_are_what_their_names_say() -> None:
    f = _features(_tone())
    assert f.log_silence == pytest.approx(math.log(8.0))
    assert f.tonal_ratio == pytest.approx(0.9)
    assert f.duration_ms == pytest.approx(400.0)
    assert f.log_peak_over_floor == pytest.approx(math.log(0.5 / 0.01), rel=1e-3)
    assert f.peak_over_global == pytest.approx(0.5, rel=1e-6)


def test_a_run_too_short_for_a_spectrum_reads_neutral() -> None:
    f = candidate_features(
        np.zeros(1, dtype=np.float32),
        SR,
        run_start=0,
        run_end=1,
        run_peak=0.0,
        noise_floor=0.0,
        global_peak=0.0,
        silence_score=0.0,
        tonal_ratio=0.0,
        band_lo_hz=2000,
        band_hi_hz=5000,
    )
    assert (f.spectral_flatness, f.log_spectral_prominence) == (1.0, 0.0)
    assert all(math.isfinite(v) for v in feature_vector(f))


def test_the_vector_follows_the_model_field_order() -> None:
    f = _features(_tone())
    assert FEATURE_NAMES == tuple(BeepFeatures.model_fields)
    assert feature_vector(f) == [getattr(f, n) for n in FEATURE_NAMES]


def test_a_stored_candidate_without_features_still_loads() -> None:
    old = {"time": 1.0, "score": 0.5, "peak_amplitude": 0.2, "duration_ms": 300.0}
    assert BeepCandidate.model_validate(old).features is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -n0 -q tests/test_beep_features.py`
Expected: collection error, `ModuleNotFoundError: No module named 'splitsmith.beep_features'`.

- [ ] **Step 3: Add the model**

In `src/splitsmith/config.py`, directly above `class BeepCandidate`:

```python
class BeepFeatures(BaseModel):
    """Ranker inputs for one beep candidate (#949, spec 2026-10-06).

    Computed by :func:`splitsmith.beep_features.candidate_features`, the one
    implementation both ``detect_beep`` and the ranker's trainer use.
    Timer-agnostic (no tone frequency, no position in the window) and
    unchanged by recording gain.
    """

    log_silence: float
    tonal_ratio: float
    duration_ms: float
    log_peak_over_floor: float
    peak_over_global: float
    spectral_flatness: float
    log_spectral_prominence: float
```

and on `BeepCandidate`, after `confidence`:

```python
    features: BeepFeatures | None = None
```

- [ ] **Step 4: Write the module**

`src/splitsmith/beep_features.py`:

```python
"""Per-candidate features for ranking beep candidates (#949).

Spec: docs/superpowers/specs/2026-10-06-beep-learned-ranker-design.md, section 1.
``detect_beep`` calls :func:`candidate_features` for every run it finds and the
trainer reads the result back off the candidates, so training and runtime share
this one implementation.

Pure: arrays and numbers in, a ``BeepFeatures`` out.
"""

from __future__ import annotations

import math

import numpy as np

from .config import BeepFeatures

FEATURE_NAMES: tuple[str, ...] = tuple(BeepFeatures.model_fields)

_EPS = 1e-9
# The spectrum is taken over at most this much of the run: a beep is 300-500
# ms, and a long merged run's tail is the other event, not the beep.
SPECTRUM_MAX_S = 0.5


def candidate_features(
    audio: np.ndarray,
    sample_rate: int,
    *,
    run_start: int,
    run_end: int,
    run_peak: float,
    noise_floor: float,
    global_peak: float,
    silence_score: float,
    tonal_ratio: float,
    band_lo_hz: float,
    band_hi_hz: float,
) -> BeepFeatures:
    """The feature vector for the run ``audio[run_start:run_end]``."""
    stop = min(run_end, run_start + int(SPECTRUM_MAX_S * sample_rate))
    flatness, prominence = _band_spectrum_shape(
        np.asarray(audio[run_start:stop], dtype=np.float64), sample_rate, band_lo_hz, band_hi_hz
    )
    return BeepFeatures(
        log_silence=math.log(max(silence_score, _EPS)),
        tonal_ratio=tonal_ratio,
        duration_ms=(run_end - run_start) * 1000.0 / sample_rate,
        log_peak_over_floor=math.log((run_peak + _EPS) / (noise_floor + _EPS)),
        peak_over_global=run_peak / (global_peak + _EPS),
        spectral_flatness=flatness,
        log_spectral_prominence=math.log(prominence),
    )


def feature_vector(features: BeepFeatures) -> list[float]:
    """``features`` as a list in ``FEATURE_NAMES`` order."""
    return [float(getattr(features, name)) for name in FEATURE_NAMES]


def _band_spectrum_shape(
    segment: np.ndarray, sample_rate: int, lo_hz: float, hi_hz: float
) -> tuple[float, float]:
    """(Wiener flatness, peak-over-median prominence) of the power spectrum in
    ``[lo_hz, hi_hz]``. A pure tone is near (0, large); noise near (high, small).
    A segment too short to hold a bin in the band reads neutral, (1.0, 1.0)."""
    if segment.size < 2:
        return 1.0, 1.0
    power = np.abs(np.fft.rfft(segment * np.hanning(segment.size))) ** 2
    freqs = np.fft.rfftfreq(segment.size, 1.0 / sample_rate)
    band = power[(freqs >= lo_hz) & (freqs <= hi_hz)] + _EPS
    if band.size == 0:
        return 1.0, 1.0
    flatness = float(np.exp(np.mean(np.log(band))) / np.mean(band))
    prominence = float(np.max(band) / np.median(band))
    return flatness, prominence
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -n0 -q tests/test_beep_features.py`
Expected: 6 passed. If `test_a_tone_is_not_flat_and_is_prominent` fails on the numeric bounds, print both feature sets and report the values; do not loosen the bounds without saying so in the task report.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/config.py src/splitsmith/beep_features.py tests/test_beep_features.py
git commit -m "feat(beep): per-candidate ranking features (#949)"
```

### Task 2: `detect_beep` attaches features, ranking unchanged

**Files:**
- Modify: `src/splitsmith/beep_detect.py` (the candidate loop in `detect_beep`, and the `BeepCandidate(...)` construction)
- Test: `tests/test_beep_features.py` (append)

**Interfaces:**
- Consumes: `candidate_features`, `BeepFeatures` from Task 1.
- Produces: every `BeepCandidate` returned by `detect_beep` has `features` set. Task 3 reads `candidate.features` and `candidate.score` / `candidate.confidence`.

- [ ] **Step 1: Write the failing parity test**

Append to `tests/test_beep_features.py` (merge the new imports into the file's import block):

```python
from pathlib import Path

from splitsmith.beep_calibration import load_manifest
from splitsmith.beep_detect import _candidate_runs, detect_beep, load_audio
from splitsmith.config import BeepDetectConfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_detect_beep_attaches_the_features_candidate_features_computes() -> None:
    """No skew: what the trainer reads off a candidate is exactly what a
    direct call on the same run returns. ``detect_beep`` ranks runs by
    score (a stable sort), so its candidates line up with the runs sorted
    the same way."""
    entry = load_manifest(FIXTURES / "beep_calibration" / "manifest.yaml").fixtures[1]
    audio, sr = load_audio(FIXTURES / entry.clip_wav)
    config = BeepDetectConfig(top_n_candidates=1000)

    detection = detect_beep(audio, sr, config)
    runs = sorted(_candidate_runs(audio, sr, config), key=lambda r: r.score, reverse=True)

    assert len(detection.candidates) == len(runs) > 1
    for candidate, run in zip(detection.candidates, runs, strict=True):
        assert candidate.features == candidate_features(
            run.audio,
            sr,
            run_start=run.start,
            run_end=run.end,
            run_peak=run.run_peak,
            noise_floor=run.noise_floor,
            global_peak=run.global_peak,
            silence_score=run.silence_score,
            tonal_ratio=run.tonal_ratio,
            band_lo_hz=config.freq_min_hz,
            band_hi_hz=config.freq_max_hz,
        )
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest -n0 -q tests/test_beep_features.py -k attaches`
Expected: FAIL, `ImportError: cannot import name '_candidate_runs'`.

- [ ] **Step 3: Extract the run loop into `_candidate_runs`**

In `src/splitsmith/beep_detect.py`, add `from dataclasses import dataclass` to the stdlib imports and `from .beep_features import candidate_features` to the local imports (check `.config` imports there and add `BeepFeatures` only if referenced). Then add above `detect_beep`:

```python
@dataclass(frozen=True)
class _Run:
    """One above-cutoff run that cleared ``min_duration_ms``, with what
    ranking and the onset walk need. Internal to this module and its tests."""

    audio: np.ndarray  # the search-window audio the run indexes into
    start: int
    end: int
    run_peak: float
    noise_floor: float
    global_peak: float
    silence_score: float
    tonal_ratio: float
    score: float
    features: BeepFeatures


def _candidate_runs(audio: np.ndarray, sample_rate: int, config: BeepDetectConfig) -> list[_Run]:
    ...
```

Move into `_candidate_runs`, unchanged and in order, everything `detect_beep` does from the input validation (`if audio.ndim != 1`) through the end of the `for s, e in zip(starts, ends, strict=True):` loop, with these edits only:

- The loop appends `_Run(audio=audio, start=s, end=e, run_peak=run_peak, noise_floor=noise_floor, global_peak=peak_value, silence_score=silence_score, tonal_ratio=tonal_ratio, score=score, features=candidate_features(audio, sample_rate, run_start=int(s), run_end=int(e), run_peak=run_peak, noise_floor=noise_floor, global_peak=peak_value, silence_score=silence_score, tonal_ratio=tonal_ratio, band_lo_hz=config.freq_min_hz, band_hi_hz=config.freq_max_hz))` instead of the tuple.
- `env_fine` is also needed by `detect_beep` for the onset walk. Keep computing it in `detect_beep`, after the call, from the same sliced audio: `_candidate_runs` returns runs whose `.audio` is the sliced window, so `detect_beep` uses `runs[0].audio` when there is at least one run.
- The `BeepNotFoundError` raises (empty search window, flat audio, no candidates) stay inside `_candidate_runs`, with their messages unchanged.

`detect_beep` becomes:

```python
def detect_beep(audio: np.ndarray, sample_rate: int, config: BeepDetectConfig) -> BeepDetection:
    """Locate the start beep in ``audio`` and return its leading-edge timestamp.

    Raises ``BeepNotFoundError`` if no candidate satisfies the duration/amplitude
    thresholds.
    """
    runs = _candidate_runs(audio, sample_rate, config)
    window = runs[0].audio
    noise_floor = runs[0].noise_floor
    env_fine = _bandpass_envelope(
        window, sample_rate, config.freq_min_hz, config.freq_max_hz, _LEADING_EDGE_SMOOTHING_MS
    )
    ranked = sorted(runs, key=lambda r: r.score, reverse=True)
    runner_up_score = ranked[1].score if len(ranked) > 1 else 0.0
    ranked_models: list[BeepCandidate] = []
    for run in ranked:
        leading_idx = _rise_foot_leading_edge(env_fine, run.start, run.end, noise_floor)
        duration_ms = (run.end - run.start) * 1000.0 / sample_rate
        confidence = candidate_confidence(
            silence_score=run.silence_score,
            tonal_score=run.tonal_ratio,
            duration_ms=duration_ms,
            score=run.score,
            runner_up_score=runner_up_score,
        )
        ranked_models.append(
            BeepCandidate(
                time=leading_idx / sample_rate,
                score=run.score,
                peak_amplitude=run.run_peak,
                duration_ms=duration_ms,
                silence_score=run.silence_score,
                tonal_score=run.tonal_ratio,
                confidence=confidence,
                features=run.features,
            )
        )
    ...  # the existing top_n / winner / return block, unchanged
```

Keep the existing comments (the coarse/fine envelope note, the global runner-up note) next to the code they describe. `sorted` is stable, so ties keep loop order exactly as today.

- [ ] **Step 4: Run the beep tests**

Run: `uv run pytest -q tests/test_beep_features.py tests/test_beep_detect.py tests/test_beep_windows.py tests/test_beep_calibration.py tests/test_beep_regression.py`
Expected: all pass.

- [ ] **Step 5: Prove the ranking did not move**

Run: `uv run python scripts/eval_beep_detector.py --track clip --json ~/.claude-tmp/beep_eval_features.json`
Then:

```bash
python3 - <<'EOF'
import json, os
a = {r["stem"]: r for r in json.load(open("tests/fixtures/beep_calibration/baseline.json"))["results"] if r["track"] == "clip"}
b = {r["stem"]: r for r in json.load(open(os.path.expanduser("~/.claude-tmp/beep_eval_features.json")))["results"]}
assert a.keys() == b.keys()
for k in ("detected_time_s", "detected_score", "detected_confidence", "correct_top1", "correct_in_topn", "candidate_count"):
    diff = [s for s in a if a[s][k] != b[s][k]]
    print(k, "differs on", len(diff), diff[:3])
EOF
```

Expected: every line says `differs on 0`. Any difference is a bug in the extraction; fix it before continuing.

- [ ] **Step 6: Show the parity test can fail**

In `_candidate_runs`, change only the `candidate_features(...)` call's argument to `silence_score=silence_score * 1.001` (the `_Run`'s own `silence_score` field stays unscaled). Run `uv run pytest -n0 -q tests/test_beep_features.py -k attaches`: it must FAIL, because the attached features no longer match a direct call on the run. Revert, rerun, confirm it passes. Note both results in the task report.

- [ ] **Step 7: Full suite and commit**

Run: `uv run pytest -q` then `uv run ruff check src tests && uv run black --check src tests`
Expected: all green. If a test elsewhere compares serialised `BeepCandidate` dicts exactly and now sees `features`, update that test to the new shape and name it in the task report.

```bash
git add src/splitsmith/beep_detect.py tests/test_beep_features.py
git commit -m "refactor(beep): attach ranking features to every candidate (#949)"
```

- [ ] **Step 8: Open PR 1**

Title: `feat(beep): per-candidate ranking features, ranking unchanged (#949)`. Body: what the seven features are, the Step 5 diff result (0 differences over 127 fixtures), the Step 6 mutation result, and that ranking changes come in a later PR. Watch CI with `gh pr checks <n> --watch`; report to the user, do not merge without their go.

---

## PR 2: trainer and the go/no-go report

Branch: `feat/beep-ranker-trainer-949` from `origin/main` after PR 1 merges, worktree `.claude/worktrees/beep-trainer`.

### Task 3: collect labeled candidates from the corpus

**Files:**
- Create: `scripts/train_beep_ranker.py` (collection part)
- Test: `tests/test_train_beep_ranker.py`

**Interfaces:**
- Consumes: `BeepCandidate.features`, `feature_vector`, `FEATURE_NAMES` (Task 1-2); `load_manifest`, `BeepFixtureEntry` from `splitsmith.beep_calibration`.
- Produces (module-level in the script, imported by tests via `importlib`):
  - `match_group(stem: str) -> str`
  - `@dataclass(frozen=True) class CandidateRow: stem: str; group: str; features: list[float]; positive: bool; heuristic_score: float; heuristic_confidence: float`
  - `@dataclass(frozen=True) class Clip: stem: str; group: str; rows: list[CandidateRow]` with property `reachable: bool` (any row positive)
  - `clip_from_detection(entry: BeepFixtureEntry, detection: BeepDetection | None) -> Clip`
  - `collect(manifest_path: Path, fixtures_dir: Path) -> list[Clip]`

- [ ] **Step 1: Write the failing tests**

`tests/test_train_beep_ranker.py`:

```python
"""The beep ranker trainer (#949, spec 2026-10-06 section 2)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from splitsmith.beep_calibration import BeepFixtureEntry
from splitsmith.config import BeepCandidate, BeepDetection, BeepFeatures

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "train_beep_ranker.py"


def _script():
    spec = importlib.util.spec_from_file_location("train_beep_ranker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _features(x: float = 0.0) -> BeepFeatures:
    return BeepFeatures(
        log_silence=x,
        tonal_ratio=x,
        duration_ms=x,
        log_peak_over_floor=x,
        peak_over_global=x,
        spectral_flatness=x,
        log_spectral_prominence=x,
    )


def _candidate(time: float, x: float = 0.0) -> BeepCandidate:
    return BeepCandidate(
        time=time, score=x, peak_amplitude=0.1, duration_ms=300.0, confidence=0.5, features=_features(x)
    )


def _entry(stem: str = "stage-shots-tallmilan-2026-stage3-s97dcec94", truth: float = 5.0, tol: float = 15.0):
    return BeepFixtureEntry(
        stem=stem, camera_kind="hand", clip_wav=f"{stem}.wav", ground_truth_in_clip=truth, tolerance_ms=tol
    )


def test_fixtures_group_by_match() -> None:
    m = _script()
    assert m.match_group("stage-shots-hfo-masters-2026-stage4-s36ed6e4e-apple-iphone17pro") == "hfo-masters-2026"
    assert m.match_group("stage-shots-tallmilan-2026-stage3-s97dcec94") == "tallmilan-2026"
    assert (
        m.match_group("stage-shots-vads-easter-shoot-gotta-go-fast-stage2-s36ed6e4e")
        == "vads-easter-shoot-gotta-go-fast"
    )
    assert m.match_group("beep-test") == "beep-test"


def test_a_candidate_is_positive_within_its_own_fixtures_tolerance() -> None:
    m = _script()
    detection = BeepDetection(
        time=5.010, peak_amplitude=0.1, duration_ms=300.0, candidates=[_candidate(5.010), _candidate(5.020)]
    )
    tight = m.clip_from_detection(_entry(tol=15.0), detection)
    loose = m.clip_from_detection(_entry(tol=100.0), detection)
    assert [r.positive for r in tight.rows] == [True, False]
    assert [r.positive for r in loose.rows] == [True, True]


def test_a_clip_with_no_detection_is_an_unreachable_miss() -> None:
    clip = _script().clip_from_detection(_entry(), None)
    assert clip.rows == [] and not clip.reachable


def test_rows_carry_features_and_the_heuristic_ranking() -> None:
    m = _script()
    detection = BeepDetection(time=1.0, peak_amplitude=0.1, duration_ms=300.0, candidates=[_candidate(1.0, 0.7)])
    [row] = m.clip_from_detection(_entry(), detection).rows
    assert row.features == [0.7] * 7
    assert (row.heuristic_score, row.heuristic_confidence) == (0.7, 0.5)
    assert row.group == "tallmilan-2026"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -n0 -q tests/test_train_beep_ranker.py`
Expected: FAIL, `FileNotFoundError` (no script yet).

- [ ] **Step 3: Write the collection part**

`scripts/train_beep_ranker.py`:

```python
"""Fit and evaluate a learned beep-candidate ranker (#949, step 2).

Spec: docs/superpowers/specs/2026-10-06-beep-learned-ranker-design.md.
Runs ``detect_beep`` over every calibration fixture keeping every candidate,
labels the candidate(s) at the true beep, scores logistic regression and a
GBDT by leave-one-match-out, fits a confidence head on the out-of-fold
logits, applies the ship gate and writes the report.

Run::

    uv run python scripts/train_beep_ranker.py
    uv run python scripts/train_beep_ranker.py --report /tmp/ranker_report.json

scikit-learn is a dev dependency; nothing under src/ imports it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from splitsmith.beep_calibration import BeepFixtureEntry, load_manifest
from splitsmith.beep_detect import BeepNotFoundError, detect_beep, load_audio
from splitsmith.beep_features import feature_vector
from splitsmith.config import BeepDetectConfig, BeepDetection

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "beep_calibration" / "manifest.yaml"
REPORT_PATH = FIXTURES_DIR / "beep_calibration" / "ranker_report.json"
ALL_CANDIDATES = 10_000

_MATCH = re.compile(r"^stage-shots-(.+?)-stage\d+")


def match_group(stem: str) -> str:
    """The cross-validation group: the match a fixture was shot at."""
    found = _MATCH.match(stem)
    return found.group(1) if found else stem


@dataclass(frozen=True)
class CandidateRow:
    stem: str
    group: str
    features: list[float]
    positive: bool
    heuristic_score: float
    heuristic_confidence: float


@dataclass(frozen=True)
class Clip:
    stem: str
    group: str
    rows: list[CandidateRow]

    @property
    def reachable(self) -> bool:
        return any(r.positive for r in self.rows)


def clip_from_detection(entry: BeepFixtureEntry, detection: BeepDetection | None) -> Clip:
    """Label ``detection``'s candidates against ``entry``'s truth and tolerance."""
    group = match_group(entry.stem)
    if detection is None:
        return Clip(stem=entry.stem, group=group, rows=[])
    tol_s = entry.tolerance_ms / 1000.0
    rows = []
    for c in detection.candidates:
        if c.features is None:
            raise ValueError(f"{entry.stem}: a candidate without features; is PR 1 merged?")
        rows.append(
            CandidateRow(
                stem=entry.stem,
                group=group,
                features=feature_vector(c.features),
                positive=abs(c.time - entry.ground_truth_in_clip) <= tol_s,
                heuristic_score=c.score,
                heuristic_confidence=c.confidence,
            )
        )
    return Clip(stem=entry.stem, group=group, rows=rows)


def collect(manifest_path: Path = MANIFEST_PATH, fixtures_dir: Path = FIXTURES_DIR) -> list[Clip]:
    """Every manifest fixture's clip track, every candidate kept."""
    config = BeepDetectConfig(top_n_candidates=ALL_CANDIDATES)
    clips = []
    for entry in load_manifest(manifest_path).fixtures:
        audio, sr = load_audio(fixtures_dir / entry.clip_wav)
        try:
            detection: BeepDetection | None = detect_beep(audio, sr, config)
        except BeepNotFoundError:
            detection = None
        clips.append(clip_from_detection(entry, detection))
    return clips
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -n0 -q tests/test_train_beep_ranker.py`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/train_beep_ranker.py tests/test_train_beep_ranker.py
git commit -m "feat(beep): collect labeled candidates for the ranker trainer (#949)"
```

### Task 4: leave-one-match-out evaluation, confidence head, gate, report

**Files:**
- Modify: `scripts/train_beep_ranker.py` (append evaluation, gate, report, `main`)
- Test: `tests/test_train_beep_ranker.py` (append)

**Interfaces:**
- Consumes: `Clip`, `CandidateRow`, `collect` (Task 3).
- Produces:
  - `LOGIT_CLAMP = 10.0`
  - `clip_logits(probs: Sequence[float]) -> list[float]` (logit of each prob, clamped to ±`LOGIT_CLAMP`)
  - `margins(logits: Sequence[float]) -> list[float]` (each logit minus the best *other*; best other is `-LOGIT_CLAMP` when alone)
  - `oof_probs(clips: list[Clip], make_model: Callable[[], Any]) -> dict[str, list[float]]` (per stem, per row, out-of-fold P(positive))
  - `@dataclass class ClipOutcome: stem: str; tags: list[str]; top1: bool; topn: bool; confidence: float`
  - `outcomes(clips, probs_by_stem, head_conf_by_stem, tags_by_stem, top_n=5) -> list[ClipOutcome]`
  - `oof_head_confidence(clips, probs_by_stem) -> dict[str, list[float]]` (leave-one-match-out logistic head over `[logit, margin]`, target = that row is the clip's out-of-fold top-1 *and* positive; returns per-row calibrated confidence)
  - `@dataclass class Gate: top1_hits: int; topn_hits: int; wrong_at_95: int; passed: bool`
  - `gate(outcomes: list[ClipOutcome]) -> Gate` with `TOP1_FLOOR = 78`, `TOPN_FLOOR = 105`
  - `main()` writing `REPORT_PATH`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_train_beep_ranker.py`:

```python
import math

import pytest


def test_logits_are_clamped_and_a_lone_candidate_has_a_defined_margin() -> None:
    m = _script()
    assert m.clip_logits([0.0, 1.0]) == [-m.LOGIT_CLAMP, m.LOGIT_CLAMP]
    assert m.margins([3.0]) == [3.0 + m.LOGIT_CLAMP]
    assert m.margins([3.0, 1.0, -2.0]) == [2.0, -2.0, -5.0]
    assert all(math.isfinite(v) for v in m.margins([m.LOGIT_CLAMP]))


def _clip(m, stem: str, positives: list[bool], xs: list[float]):
    group = m.match_group(stem)
    rows = [
        m.CandidateRow(stem=stem, group=group, features=[x] * 7, positive=p, heuristic_score=0.0, heuristic_confidence=0.5)
        for p, x in zip(positives, xs, strict=True)
    ]
    return m.Clip(stem=stem, group=group, rows=rows)


def test_a_separable_corpus_scores_perfectly_out_of_fold() -> None:
    """The beep always has the larger feature value; three matches."""
    from sklearn.linear_model import LogisticRegression

    m = _script()
    clips = [
        _clip(m, f"stage-shots-{match}-2026-stage{n}-s0", [False, True, False], [0.1, 0.9 + n / 100, 0.2])
        for match in ("a", "b", "c")
        for n in range(1, 5)
    ]
    probs = m.oof_probs(clips, lambda: LogisticRegression())
    assert set(probs) == {c.stem for c in clips}
    for clip in clips:
        p = probs[clip.stem]
        assert p.index(max(p)) == 1


def test_out_of_fold_never_sees_its_own_match() -> None:
    """A model that memorises its training stems would score a held-out
    match at chance; this one records which stems it was fitted on."""
    m = _script()
    fitted_on: list[set[str]] = []

    class Spy:
        def fit(self, X, y, sample_weight=None):
            fitted_on.append(set(self._stems))
            return self

        def predict_proba(self, X):
            import numpy as np

            return np.tile([0.5, 0.5], (len(X), 1))

    clips = [_clip(m, f"stage-shots-{g}-2026-stage1-s0", [True, False], [1.0, 0.0]) for g in ("a", "b")]

    def make():
        spy = Spy()
        spy._stems = []
        return spy

    m._fit_hook = lambda model, train: setattr(model, "_stems", [c.stem for c in train])
    m.oof_probs(clips, make)
    assert fitted_on == [{"stage-shots-b-2026-stage1-s0"}, {"stage-shots-a-2026-stage1-s0"}]


def test_one_confident_wrong_fixture_fails_the_gate() -> None:
    m = _script()
    right = [m.ClipOutcome(stem=f"s{i}", tags=[], top1=True, topn=True, confidence=0.99) for i in range(110)]
    wrong = [m.ClipOutcome(stem=f"w{i}", tags=[], top1=False, topn=False, confidence=0.4) for i in range(17)]
    assert m.gate(right + wrong).passed
    confident_wrong = [m.ClipOutcome(stem="x", tags=[], top1=False, topn=True, confidence=0.96)]
    result = m.gate(right[:-1] + wrong + confident_wrong)
    assert (result.wrong_at_95, result.passed) == (1, False)


def test_the_gate_floors_are_the_specs() -> None:
    m = _script()
    assert (m.TOP1_FLOOR, m.TOPN_FLOOR) == (78, 105)
    hits = [m.ClipOutcome(stem=f"s{i}", tags=[], top1=i < 77, topn=i < 110, confidence=0.5) for i in range(127)]
    assert not m.gate(hits).passed
    hits = [m.ClipOutcome(stem=f"s{i}", tags=[], top1=i < 78, topn=i < 104, confidence=0.5) for i in range(127)]
    assert not m.gate(hits).passed


def test_an_unreachable_clip_counts_as_a_miss() -> None:
    m = _script()
    clips = [m.Clip(stem="stage-shots-a-2026-stage1-s0", group="a", rows=[])]
    [outcome] = m.outcomes(clips, {}, {}, {"stage-shots-a-2026-stage1-s0": ["handheld"]})
    assert (outcome.top1, outcome.topn, outcome.confidence) == (False, False, 0.0)


def test_top1_counts_when_either_of_two_positive_candidates_wins() -> None:
    m = _script()
    clip = _clip(m, "stage-shots-a-2026-stage1-s0", [True, True, False], [0.0, 0.0, 0.0])
    [outcome] = m.outcomes([clip], {clip.stem: [0.2, 0.7, 0.1]}, {clip.stem: [0.1, 0.8, 0.0]}, {clip.stem: []})
    assert outcome.top1 and outcome.confidence == pytest.approx(0.8)
```

`test_out_of_fold_never_sees_its_own_match` relies on a `_fit_hook` seam: `oof_probs` calls `_fit_hook(model, train_clips)` (a no-op by default) right before `model.fit`. Keep it; it is the only way to observe which clips a fold trained on without trusting the implementation's own bookkeeping.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -n0 -q tests/test_train_beep_ranker.py`
Expected: the new tests FAIL with `AttributeError` (no `clip_logits`, `oof_probs`, ...).

- [ ] **Step 3: Implement evaluation, head, gate**

Append to `scripts/train_beep_ranker.py` (and add `import json, math, sys, argparse`, `from collections.abc import Callable, Sequence`, `from typing import Any`, `import numpy as np` to the imports, stdlib first):

```python
LOGIT_CLAMP = 10.0
TOP_N = 5
TOP1_FLOOR = 78  # 51.2 % + 10 pp of 127, rounded up
TOPN_FLOOR = 105  # today's top-N, 82.7 %
AUTO_TRUST = 0.95


def _fit_hook(model: Any, train: list[Clip]) -> None:
    """Test seam: called with each fold's training clips before ``fit``."""


def clip_logits(probs: Sequence[float]) -> list[float]:
    out = []
    for p in probs:
        p = min(max(p, 1e-12), 1.0 - 1e-12)
        out.append(max(-LOGIT_CLAMP, min(LOGIT_CLAMP, math.log(p / (1.0 - p)))))
    return out


def margins(logits: Sequence[float]) -> list[float]:
    out = []
    for i, z in enumerate(logits):
        others = [o for j, o in enumerate(logits) if j != i]
        out.append(z - (max(others) if others else -LOGIT_CLAMP))
    return out


def _matrix(clips: list[Clip]) -> tuple[np.ndarray, np.ndarray]:
    rows = [r for c in clips for r in c.rows]
    X = np.array([r.features for r in rows], dtype=np.float64)
    y = np.array([r.positive for r in rows], dtype=np.int8)
    return X, y


def _balanced_weights(y: np.ndarray) -> np.ndarray:
    pos = max(1, int(y.sum()))
    neg = max(1, int(y.size - y.sum()))
    return np.where(y == 1, y.size / (2.0 * pos), y.size / (2.0 * neg))


def oof_probs(clips: list[Clip], make_model: Callable[[], Any]) -> dict[str, list[float]]:
    """Leave-one-match-out P(positive) for every row of every clip with rows.
    Trains only on reachable clips (spec 2.1); predicts every clip."""
    out: dict[str, list[float]] = {}
    for group in sorted({c.group for c in clips}):
        held = [c for c in clips if c.group == group and c.rows]
        train = [c for c in clips if c.group != group and c.reachable]
        if not held:
            continue
        X, y = _matrix(train)
        model = make_model()
        _fit_hook(model, train)
        model.fit(X, y, sample_weight=_balanced_weights(y))
        for clip in held:
            Xc = np.array([r.features for r in clip.rows], dtype=np.float64)
            out[clip.stem] = [float(p) for p in model.predict_proba(Xc)[:, 1]]
    return out


def oof_head_confidence(clips: list[Clip], probs: dict[str, list[float]]) -> dict[str, list[float]]:
    """Leave-one-match-out confidence head over [logit, margin] (spec 4).
    Target per row: it is its clip's top-1 by ``probs`` and it is positive.
    Fitted over all clips with rows, unreachable ones included."""
    from sklearn.linear_model import LogisticRegression

    def table(cs: list[Clip]) -> tuple[np.ndarray, np.ndarray]:
        X, y = [], []
        for c in cs:
            z = clip_logits(probs[c.stem])
            mg = margins(z)
            best = int(np.argmax(probs[c.stem]))
            for i, row in enumerate(c.rows):
                X.append([z[i], mg[i]])
                y.append(int(i == best and row.positive))
        return np.array(X, dtype=np.float64), np.array(y, dtype=np.int8)

    with_rows = [c for c in clips if c.rows and c.stem in probs]
    out: dict[str, list[float]] = {}
    for group in sorted({c.group for c in with_rows}):
        X, y = table([c for c in with_rows if c.group != group])
        head = LogisticRegression().fit(X, y) if len(set(y.tolist())) == 2 else None
        for c in (c for c in with_rows if c.group == group):
            Xc, _ = table([c])
            out[c.stem] = (
                [float(p) for p in head.predict_proba(Xc)[:, 1]] if head is not None else [0.0] * len(c.rows)
            )
    return out


@dataclass
class ClipOutcome:
    stem: str
    tags: list[str]
    top1: bool
    topn: bool
    confidence: float


def outcomes(
    clips: list[Clip],
    probs: dict[str, list[float]],
    head: dict[str, list[float]],
    tags: dict[str, list[str]],
    top_n: int = TOP_N,
) -> list[ClipOutcome]:
    out = []
    for c in clips:
        if not c.rows or c.stem not in probs:
            out.append(ClipOutcome(stem=c.stem, tags=tags.get(c.stem, []), top1=False, topn=False, confidence=0.0))
            continue
        order = sorted(range(len(c.rows)), key=lambda i: probs[c.stem][i], reverse=True)
        out.append(
            ClipOutcome(
                stem=c.stem,
                tags=tags.get(c.stem, []),
                top1=c.rows[order[0]].positive,
                topn=any(c.rows[i].positive for i in order[:top_n]),
                confidence=head[c.stem][order[0]] if c.stem in head else 0.0,
            )
        )
    return out


def heuristic_outcomes(clips: list[Clip], tags: dict[str, list[str]], top_n: int = TOP_N) -> list[ClipOutcome]:
    """Today's detector, scored by the same rules (rows are already in its order)."""
    out = []
    for c in clips:
        if not c.rows:
            out.append(ClipOutcome(stem=c.stem, tags=tags.get(c.stem, []), top1=False, topn=False, confidence=0.0))
            continue
        out.append(
            ClipOutcome(
                stem=c.stem,
                tags=tags.get(c.stem, []),
                top1=c.rows[0].positive,
                topn=any(r.positive for r in c.rows[:top_n]),
                confidence=c.rows[0].heuristic_confidence,
            )
        )
    return out


@dataclass
class Gate:
    top1_hits: int
    topn_hits: int
    wrong_at_95: int
    passed: bool


def gate(results: list[ClipOutcome]) -> Gate:
    top1 = sum(o.top1 for o in results)
    topn = sum(o.topn for o in results)
    wrong = sum(1 for o in results if o.confidence >= AUTO_TRUST and not o.top1)
    return Gate(top1, topn, wrong, top1 >= TOP1_FLOOR and topn >= TOPN_FLOOR and wrong == 0)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -n0 -q tests/test_train_beep_ranker.py`
Expected: all pass. `test_the_gate_floors_are_the_specs` and `test_one_confident_wrong_fixture_fails_the_gate` must fail if any one clause of `gate`'s `passed` is removed: delete each clause in turn, rerun, restore, and note the three results in the task report.

- [ ] **Step 5: Add the report and `main`**

Append:

```python
BINS = ((0.0, 0.5, "<0.5"), (0.5, 0.7, "0.5-0.7"), (0.7, AUTO_TRUST, "0.7-0.95"), (AUTO_TRUST, 1.01, ">=0.95"))


def summary(results: list[ClipOutcome]) -> dict[str, Any]:
    def block(rs: list[ClipOutcome]) -> dict[str, Any]:
        n = len(rs)
        return {
            "n": n,
            "top1": sum(o.top1 for o in rs),
            "topn": sum(o.topn for o in rs),
            "top1_pct": round(100.0 * sum(o.top1 for o in rs) / n, 1) if n else 0.0,
        }

    tags = sorted({t for o in results for t in o.tags})
    bins = []
    for lo, hi, name in BINS:
        rs = [o for o in results if lo <= o.confidence < hi]
        bins.append({"bin": name, "n": len(rs), "right": sum(o.top1 for o in rs)})
    return {
        "all": block(results),
        "by_tag": {t: block([o for o in results if t in o.tags]) for t in tags},
        "bins": bins,
    }


def _models() -> dict[str, Callable[[], Any]]:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    def lr() -> Any:
        pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))

        class _Weighted:
            def fit(self, X, y, sample_weight=None):
                pipe.fit(X, y, logisticregression__sample_weight=sample_weight)
                return self

            def predict_proba(self, X):
                return pipe.predict_proba(X)

            @property
            def pipeline(self):
                return pipe

        return _Weighted()

    return {"lr": lr, "gbdt": lambda: GradientBoostingClassifier(random_state=0)}


def main() -> None:
    from splitsmith.beep_features import FEATURE_NAMES

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    clips = collect()
    tags = {e.stem: list(e.tags) for e in load_manifest(MANIFEST_PATH).fixtures}
    today = heuristic_outcomes(clips, tags)
    report: dict[str, Any] = {
        "spec": "docs/superpowers/specs/2026-10-06-beep-learned-ranker-design.md",
        "features": list(FEATURE_NAMES),
        "fixtures": len(clips),
        "reachable": sum(c.reachable for c in clips),
        "groups": sorted({c.group for c in clips}),
        "heuristic": summary(today),
        "models": {},
    }
    for name, make in _models().items():
        probs = oof_probs(clips, make)
        head = oof_head_confidence(clips, probs)
        res = outcomes(clips, probs, head, tags)
        g = gate(res)
        lost = [o.stem for o, t in zip(res, today, strict=True) if t.top1 and not o.top1]
        fixed = [o.stem for o, t in zip(res, today, strict=True) if o.top1 and not t.top1]
        report["models"][name] = {
            **summary(res),
            "gate": {"top1_hits": g.top1_hits, "topn_hits": g.topn_hits, "wrong_at_95": g.wrong_at_95, "passed": g.passed},
            "lost": lost,
            "fixed": fixed,
            "oof": {
                c.stem: {"probs": probs.get(c.stem, []), "head": head.get(c.stem, []), "positive": [r.positive for r in c.rows]}
                for c in clips
            },
        }
    lr_top1 = report["models"]["lr"]["gate"]["top1_hits"]
    gbdt_top1 = report["models"]["gbdt"]["gate"]["top1_hits"]
    winner = "lr" if (gbdt_top1 - lr_top1) / len(clips) <= 0.02 else "gbdt"
    report["winner"] = winner
    report["ship"] = report["models"][winner]["gate"]["passed"]

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    _print(report)
    print(f"\nWrote {args.report}")


def _print(report: dict[str, Any]) -> None:
    def line(name: str, s: dict[str, Any]) -> str:
        a = s["all"]
        return f"  {name:10} top1 {a['top1']:3}/{a['n']} ({a['top1_pct']:5.1f}%)  topN {a['topn']:3}"

    print(f"{report['fixtures']} fixtures, {report['reachable']} reachable, {len(report['groups'])} matches")
    print(line("heuristic", report["heuristic"]))
    for name, m in report["models"].items():
        print(line(name, m) + f"  gate={'PASS' if m['gate']['passed'] else 'FAIL'}  wrong@0.95={m['gate']['wrong_at_95']}")
        print(f"    lost {len(m['lost'])}: {', '.join(m['lost'][:6])}{' ...' if len(m['lost']) > 6 else ''}")
        for b in m["bins"]:
            print(f"    {b['bin']:9} n={b['n']:3} right={b['right']:3}")
    print(f"winner: {report['winner']}  ship: {report['ship']}")


if __name__ == "__main__":
    main()
```

Note `_balanced_weights` reaches the LR through `logisticregression__sample_weight`; `GradientBoostingClassifier.fit` takes `sample_weight` directly.

- [ ] **Step 6: Run the tests, lint, commit**

Run: `uv run pytest -n0 -q tests/test_train_beep_ranker.py && uv run ruff check scripts/train_beep_ranker.py tests/test_train_beep_ranker.py && uv run black --check scripts tests`
Expected: green.

```bash
git add scripts/train_beep_ranker.py tests/test_train_beep_ranker.py
git commit -m "feat(beep): leave-one-match-out ranker evaluation and ship gate (#949)"
```

### Task 5: run it on the corpus and report

**Files:**
- Create: `tests/fixtures/beep_calibration/ranker_report.json` (generated)
- Modify: `tests/fixtures/beep_calibration/README.md` (one paragraph: what the report is, how to regenerate)

- [ ] **Step 1: Run the trainer**

Run: `uv run python scripts/train_beep_ranker.py`
Expected: prints the heuristic line (must read 65/127 top-1, 105 top-N; if not, the collection disagrees with `baseline.json` and that is a bug to fix before anything else), both models, the winner and `ship`.

- [ ] **Step 2: Sanity-check the result before believing it**

- The heuristic line equals `baseline.json`'s summary (65 / 105).
- `reachable` is 111 +/- 2 (#949 measured 111/127; the Hilbert change and feature extraction must not move it).
- Re-run once; the report must be byte-identical (fixed `random_state`, sorted groups).

- [ ] **Step 3: README and commit**

Append to `tests/fixtures/beep_calibration/README.md`:

```markdown
* `ranker_report.json` -- committed. Out-of-fold (leave-one-match-out)
  evaluation of the learned ranker against today's detector, with the ship
  gate's verdict (#949, spec 2026-10-06). Regenerate with
  `uv run python scripts/train_beep_ranker.py`.
```

```bash
git add tests/fixtures/beep_calibration/ranker_report.json tests/fixtures/beep_calibration/README.md
git commit -m "test(beep): commit the ranker's out-of-fold report (#949)"
```

- [ ] **Step 4: Open PR 2 and stop**

Title: `feat(beep): ranker trainer and the out-of-fold report (#949)`. Body: the printed table, both models' lost and fixed lists, the bins, the gate verdict, and which model wins. Post the same numbers as a comment on #949.

**Stop here and report to the user**, whichever way the gate went. A pass means the next plan (PR 3: wiring the winner into `detect_beep`) gets written. A fail means #949's step 2 ends with this report.
