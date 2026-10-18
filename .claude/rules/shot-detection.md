---
paths:
  - "src/splitsmith/shot_*.py"
  - "src/splitsmith/rise_foot.py"
  - "src/splitsmith/ensemble/**"
  - "src/splitsmith/fixture_schema.py"
  - "src/splitsmith/runtime.py"
  - "src/splitsmith/lab/**"
  - "src/splitsmith/data/ensemble*"
  - "src/splitsmith/data/*.onnx"
  - "scripts/build_ensemble*.py"
  - "scripts/*ensemble*.py"
  - "scripts/fit_stage_decoder.py"
  - "scripts/feature_caches.py"
  - "scripts/fixture_audio.py"
  - "scripts/fixture_review_inventory.py"
  - "scripts/promote_*.py"
  - "scripts/train_*.py"
  - "tests/fixtures/*.json"
  - "tests/fixtures/audio.*"
  - "tests/test_ensemble*.py"
  - "tests/test_stage_decoder.py"
  - "tests/test_fixture_audio.py"
  - "tests/test_build_ensemble_preconditions.py"
  - "scripts/analyze_negatives.py"
  - "scripts/build_sweep_signals.py"
  - "scripts/eval_cwt_twopass.py"
  - "scripts/eval_detector.py"
  - "scripts/eval_refinement.py"
  - "scripts/extract_*.py"
  - "scripts/lofo_mining_experiment.py"
  - "scripts/mine_negatives.py"
  - "scripts/refresh_candidates.py"
  - "scripts/sweep_*.py"
  - "tests/test_shot_detect.py"
  - "tests/test_model_builds.py"
  - "tests/test_fixture_review_status.py"
  - "tests/test_fixtures_registry.py"
  - "tests/test_lab_*.py"
  - "tests/test_onnx_parity.py"
  - "tests/test_runtime.py"
  - "docs/cameras.md"
---

# Shot detection: the ensemble, calibration and the fixture corpus

Read `docs/cameras.md` before tuning a threshold or a voter C feature; add to
it, dated, whenever a measurement shows something new about a camera.

## Two times per shot

- `Shot.time_absolute` is the shot time (the rise foot, `splitsmith.rise_foot`,
  `docs/METHODOLOGY.md`) that every output reports.
- `Shot.feature_time` is the detector's own leading-edge walk, where candidates
  are found and scored. It is never serialized.
- **Every model input reads `Shot.scoring_time`** (the ensemble, TTA, the build,
  the CLAP/PANN caches, the sweeps). Computing features from `time_absolute`
  shifted them between two camera firmwares and made held-out footage
  undetectable.

## The ensemble (`src/splitsmith/ensemble/`)

- **Voter A**: `shot_detect` envelope onsets, gated at the auto-calibrated
  `min_confidence` floor (the lowest positive-shot confidence across the
  calibration set). **It is the candidate generator: every other voter sees only
  candidates A emits.**
- **Voter B**: threshold on the CLAP shot-vs-not-shot prompt similarity
  differential, calibrated against labelled fixtures.
- **Voter C**: a `GradientBoostingClassifier` over hand-crafted features + CLAP
  per-prompt similarities + PANN gunshot probability, calibrated to a target
  recall on the same set. Trained with sklearn in the build script but **shipped as one ONNX
  graph per camera class and run through `onnxruntime`: nothing under `src/`
  imports sklearn or unpickles an estimator.** Switches to per-stage adaptive
  top-(K+slack) when the audit JSON has `stage_rounds.expected`. PANN is a
  feature column on C (it used to be a separate voter D) so the GBDT learns its
  interaction with the other inputs.
- **Consensus**: a candidate is kept when `vote_total + apriori_boost >=
  consensus` (default 2-of-3); the apriori boost biases toward expected-count
  regions when prior info is known.
- **Whole-stage decoder** (`ensemble/decoder.py`, on by default): replaces the
  consensus and the headcam amplitude veto when the calibration has a `decoder`
  block. One logistic recalibration over the voters' signals gives each
  candidate P(shot), and a dynamic program keeps the best-scoring *set* for the
  stage under a split prior (the corpus's splits) and an end prior (last shot at
  beep + stage time; off when the clip ends early). It needs only the beep and
  the stage time; the round count is not used (its weight tunes to 0). Fitted by
  the build on leave-one-match-out voter C scores
  (`scripts/fit_stage_decoder.py`); `--decoder-only` fits it for an existing
  calibration and writes only that block (which is how the shipped calibration
  got its decoder). `SPLITSMITH_STAGE_DECODER=0` keeps the
  consensus, `=1` forces the decoder.

Wired into the UI's `/api/stages/{n}/shot-detect`; the FastAPI server lazy-loads
CLAP / PANN / GBDT on the first detection.

## Calibration artifacts

- Ship under `src/splitsmith/data/`: `ensemble_calibration.json` plus the voter
  C / voter E ONNX graphs it names, built by
  `scripts/build_ensemble_artifacts.py`. Re-run it after adding audited
  fixtures.
- `SPLITSMITH_ARTIFACTS_DIR=/path` points the engine at another artifact set
  for A/B comparisons without rebuilding the shipped one (`splitsmith.runtime`
  lists the env vars).
- `--out build/models/<name>` writes a **model bundle**
  (`ensemble.model_bundle`: calibration, ONNX graphs, `heldout/` with per-match
  leave-one-match-out figures and labelled OOF rows, and `model.json` naming
  every file and fixture WAV by sha256) and leaves the shipped model and the
  parity references alone. `--matches` picks the corpus by match. The lab's
  Models page (`/dev/models?model=&vs=`) lists and compares bundles. Choosing a
  bundle at runtime is not built.
- **The build checks its inputs before fitting** (`ensure_audio`,
  `scripts/feature_caches.py`): every fixture's WAV against the lock (missing
  ones fetched; one the lock lacks, one R2 cannot serve, or a local one that
  differs from the lock, i.e. an unpushed re-cut, stops the build), and its
  CLAP / PANN caches against that WAV by the stamped `wav_sha256` (an unstamped
  cache is checked once against fresh candidate times, then stamped). A stale
  or missing cache stops the build unless `--extract`. Don't go back to a
  row-count check: it let a re-cut clip's old features through.
- `scripts/build_ensemble_fixture.py` (review-time variants under
  `build/ensemble-review/`) still exists for offline comparison.

## Fixture review status

- `fixture_schema.review_status`: a fixture snapped from another angle
  (`anchor` block, `promote-from-anchor` history) is `needs_review` until its
  `review` block says `reviewed`; everything else counts as hand-audited.
- `scripts/promote_secondary_angles.py` snaps every secondary angle without a
  fixture onto its stage's reviewed fixture (dry run by default, `--write` to
  promote; a snap of a snap is refused). The corpus holds unreviewed footage on
  purpose: **the build trains on it by default and `--reviewed-only`
  (`fixture_stems(reviewed_only=True)`) leaves it out. Compare both before
  trusting a number.**
- `scripts/fixture_review_inventory.py` scores every fixture for review
  (`lab.inventory`: needing review, snapped, the share of shots at the snap
  window's edge, how much stored leading edges disagree with a stricter onset)
  into `build/fixture_review_inventory.json`; the lab's review
  queue (`/dev/review`) orders shot times by it. "Approve to corpus" or "Mark
  reviewed" (`lab.confirm_review`) writes `review.status = reviewed`. **A label
  pass never clears a fixture whose times need checking.**

## Fixture audio is not in git

- `tests/fixtures/audio.lock.json` maps every fixture WAV to its sha256; bytes
  live content-addressed in a public-read R2 bucket (`wav/<sha256>.wav`, served
  at `fixtures.splitsmith.app`), and `tests/fixtures/.gitignore` keeps local
  copies out.
- One WAV stays in git on purpose: the smoke fixture
  `stage-shots-tallmilan-2026-stage3-s97dcec94-pin.wav` (the slim wheel and both
  desktop smokes read it, one in a bare container).
- CI's python shards fetch only the WAVs listed in
  `tests/fixtures/audio.ci.txt` (`fetch --only tests/fixtures/audio.ci.txt`,
  cached on the lock and the list).
- `uv run python scripts/fixture_audio.py fetch` before a sweep: there a
  missing WAV is a skipped fixture, silently. `push <stem>` uploads a new or
  changed WAV to the `splitsmith-fixtures` bucket through
  `wrangler r2 object put` (needs a `wrangler login` on the
  account that owns splitsmith.app) and records it in the lock.
