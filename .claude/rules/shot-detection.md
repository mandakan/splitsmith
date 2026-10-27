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
  - "tests/test_model_set_columns.py"
  - "tests/test_calibration_model_sets.py"
  - "tests/test_pann_is_gone.py"
  - "tests/_ensemble_stubs.py"
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
  the CLAP caches, the sweeps). Computing features from `time_absolute`
  shifted them between two camera firmwares and made held-out footage
  undetectable.

## The ensemble (`src/splitsmith/ensemble/`)

- **Voter A**: `shot_detect` envelope onsets, gated at the auto-calibrated
  `min_confidence` floor (the lowest positive-shot confidence across the
  calibration set). **It is the candidate generator: every other voter sees only
  candidates A emits.**
- **Two model sets** (`features.MODEL_SETS`, spec 2026-10-26): `base` needs no
  download (hand-crafted features, the camera one-hot and the stage-relative
  block without its CLAP columns); `clap` adds the CLAP per-prompt
  similarities, `clap_diff` and their relative columns, and needs the 120 MB
  CLAP artifacts. **Columns are picked by name** (`model_set_columns`,
  `select_columns`) in the runtime and the build alike, so the two cannot
  disagree on order. Both sets come from one build over one fixture list; the
  build refuses otherwise (`_assert_sets_agree`).
- **Which set runs**: `SPLITSMITH_MODEL_SET`, then the machine's
  `GlobalPrefs.detection_model_set` (local only), then `base`
  (`api.resolve_model_set`). Hosted images set the env to `clap`. **A `clap`
  request whose CLAP artifacts are missing, unreachable or unreadable
  (`ModelError`, `OSError`) detects with `base`**; any other error propagates.
  Only `clap` constructs a CLAP session. Every result, the audit doc's
  `_candidates_pending_audit` (`_model_set`, `_calibration_built_at`) and the
  `shot_detect_run` event record the set that actually ran; a doc from before
  sets has neither and reads as unknown. The server, the compute backend and
  the MCP tool cache one runtime per set *returned*, so a fallen-back `clap`
  request is retried.
- **Voter B** (`clap` only): threshold on the CLAP shot-vs-not-shot prompt
  similarity differential, calibrated against labelled fixtures. Under `base`
  its threshold is `None` and it never votes.
- **Voter C**: a `GradientBoostingClassifier` per camera class per set over that
  set's columns, calibrated to a target recall on the same set. Trained with
  sklearn in the build script but **shipped as one ONNX graph per set and class
  (`voter_c_gbdt_<set>_<class>.onnx`) and run through `onnxruntime`: nothing
  under `src/` imports sklearn or unpickles an estimator.** The runtime refuses a
  graph whose width differs from its set's `voter_c_feature_dim`. Switches to
  per-stage adaptive top-(K+slack) when the audit JSON has
  `stage_rounds.expected`. **PANN is retired**: no runtime, build, cache or
  manifest entry reads it (`tests/test_pann_is_gone.py`); old audit docs and
  lab records that carry `gunshot_prob` still load.
- **Consensus**: a candidate is kept when `vote_total + apriori_boost >=
  consensus` (default 2-of-3; under `base`, A and C must both vote); the
  apriori boost biases toward expected-count regions when prior info is known.
- **Whole-stage decoder** (`ensemble/decoder.py`, on by default): **one per
  camera class per set** (`ModelSet.decoders_by_camera_class`). One logistic
  recalibration over the voters' signals gives each candidate P(shot), and a
  dynamic program keeps the best-scoring *set* for the stage under a split prior
  (the corpus's splits) and an end prior (last shot at beep + stage time; off
  when the clip ends early). The recalibration reads **its own `features`
  list** (`decoder_features`; `base` decodes on `score_c`, `confidence`,
  `log_peak_amplitude`, `clap` adds `clap_diff` and `vote_b`), so a
  calibration always decodes with the columns it was fitted on. It needs only
  the beep and the stage time; the round count is not used (its weight tunes to
  0). Fitted by the build on leave-one-match-out voter C scores
  (`scripts/fit_stage_decoder.py`); `--decoder-only` refits every set's
  decoders for an existing calibration and writes only those blocks.
  `SPLITSMITH_STAGE_DECODER=0` keeps the consensus, `=1` forces the decoder.

Wired into the UI's `/api/stages/{n}/shot-detect`; the FastAPI server lazy-loads
the requested set's models on the first detection.

## Calibration artifacts

- Ship under `src/splitsmith/data/`: `ensemble_calibration.json` (per-set
  numbers only under `model_sets`; `load_calibration` refuses a calibration
  without them and names the rebuild) plus the voter C / voter E ONNX graphs it
  names, built by `scripts/build_ensemble_artifacts.py`. Re-run it after adding
  audited fixtures. The lab reads a calibration through
  `lab.model_map.model_set_block`; a flat one from git history reads as one set,
  `legacy`. The ONNX parity references are per set
  (`tests/data/voter_c_parity_reference_<set>.npz`).
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
  CLAP cache against that WAV by the stamped `wav_sha256` (an unstamped
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
