# Claude Code Guidance

This file gives Claude Code project-specific context. Read SPEC.md for the full technical specification.

## Project context

Personal tool for an IPSC competitor to extract shot splits from head-mounted camera footage. The user is an experienced developer who uses Claude Code daily. They prefer:
- Concise, direct communication
- Pushing back when something is wrong rather than agreeing reflexively
- Asking clarifying questions before diving into detailed implementations

## Code conventions

- Python 3.11+, type hints everywhere
- `uv` for dependency management — never use `pip` directly
- Pydantic for data validation
- `pathlib.Path` for paths, never strings
- f-strings for formatting
- Black formatting (line length 110)
- Ruff for linting
- Imports: stdlib, third-party, local — separated by blank lines
- No relative imports beyond a single dot (`.module`, not `..module`)

## Architecture rules

1. **Detection logic stays out of the CLI.** `cli.py` orchestrates; analysis happens in dedicated modules.
2. **Pure functions where possible.** Detection functions take audio data + config and return results. No file I/O inside detection logic.
3. **Pydantic models for all data crossing module boundaries.** No dicts of unknown shape passed around.
4. **Configuration is data, not code.** Tunable parameters go in `config.py` as Pydantic models with defaults; users can override via YAML.
5. **Every detection module has fixture-based tests.** Don't merge a detection change without a test that would have caught it.

## Testing approach

- `pytest` for everything
- The suite runs in parallel by default (`addopts` carries `-n auto --dist load`).
  `-n0` restores serial execution and is the right thing when debugging a single
  test — worker startup dominates a focused run, and tracebacks are cleaner.
  New tests must not depend on execution order or share mutable state outside
  `tmp_path`: a worker's process-global caches are its own, but the filesystem,
  ports, and `~/` are not.
- Fixtures live in `tests/fixtures/` — short audio clips with hand-labeled ground truth in adjacent JSON files
- Detection tests assert within tolerance (e.g., ±15ms for shot times)
- Mock ffmpeg in trim tests; don't actually shell out during unit tests
- Integration tests can use real ffmpeg but mark them with `@pytest.mark.integration`
- CI installs ffmpeg and runs the integration suite with
  `SPLITSMITH_REQUIRE_INTEGRATION=1`, which turns any skip of an
  `integration`-marked test into a failure. A test that needs media builds it
  with `tests/synthetic_media.py` rather than depending on the gitignored
  `stage_sample.mp4` — if a new integration test skips in CI, supply the input,
  don't re-add the skip.

## Review practice

For changes to the detection or export pipeline, run a review pass before merging. On PR #612 every substantive defect was found this way and none by the test suite, which was green over all four.

What actually finds things:

- **Name the specific claims to verify.** "The implementer says X is provably equivalent to Y -- check it against the original and treat any diverging input as a finding" beats "review this diff", which returns generic results.
- **Tell the reviewer the implementation report is unverified.** A stated rationale never downgrades a finding's severity.
- **Ask whether each new test genuinely fails against the pre-change code.** Several tests on that branch would have passed against the bug they claimed to cover. Deleting the fix and watching the test fail takes a minute and is the only real proof.
- **Run the code when behaviour is in question.** The exit-code defect was demonstrated by invoking the verb twice and capturing both codes, not by reading.
- **Finish with one whole-branch pass over the seams.** One defect lived in a seam no single task owned; only a cross-cutting read found it.

A green suite over a change is evidence the change didn't break anything known -- not evidence it works. A fix can also be real and still invisible: on #617 the note reached the table cell and rich ellipsized it away, so the assertion passed while the user saw nothing. Read the actual output.

## When in doubt

- **Ask before guessing.** Especially about audio detection thresholds, FCPXML structure, or anything user-facing.
- **Default to the conservative choice.** Better to under-detect shots and flag uncertainty than to invent shots from echoes.
- **Optimize for the audit trail.** Every analysis should produce a report file the user can review later. Don't silently make decisions.

## What this project is NOT

- A real-time tool. All processing is offline batch.
- A library. Single-purpose application.

## Detection pipeline

Beep detection runs inside per-stage derived search windows for multi-stage single-take videos (ffmpeg extracts the window's audio via -ss/-t; results are offset back to source-absolute). The shot-detection pipeline is a 3-voter ensemble, not raw signal processing:

- **Voter A** -- ``splitsmith.shot_detect`` envelope onsets, gated at the
  auto-calibrated ``min_confidence`` floor (the lowest positive-shot
  confidence across the calibration set). This is the candidate generator;
  every other voter sees only candidates A emits.
- **Voter B** -- threshold on the CLAP shot-vs-not-shot prompt similarity
  differential; calibrated against labeled fixtures.
- **Voter C** -- a ``GradientBoostingClassifier`` over hand-crafted
  features + CLAP per-prompt similarities + PANN gunshot probability;
  calibrated to a target recall on the same set. Trained with
  ``sklearn`` in the build script, but shipped as one ONNX graph per
  camera class and run through ``onnxruntime`` -- nothing under
  ``src/`` imports sklearn or unpickles an estimator. Switches
  to a per-stage adaptive top-(K+slack) mode when the audit JSON has
  ``stage_rounds.expected``. The PANN gunshot-class probability used to
  be a separate voter D; it is now a feature column on voter C so the
  GBDT learns its interaction with the other inputs instead of casting
  an independent vote.
- **Consensus** -- a candidate is kept when ``vote_total + apriori_boost
  >= consensus`` (default 2-of-3). The apriori boost biases toward
  expected-shot-count regions when prior info is known.

The pipeline lives in ``src/splitsmith/ensemble/`` and is wired into the
production UI's ``/api/stages/{n}/shot-detect`` endpoint. Calibration
artifacts ship under ``src/splitsmith/data/`` (built once by
``scripts/build_ensemble_artifacts.py``) -- ``ensemble_calibration.json``
plus the voter C / voter E ONNX graphs it names; the FastAPI server
lazy-loads the CLAP / PANN / GBDT models on the first detection. Re-run
the build script after adding new audited fixtures. Set
``SPLITSMITH_ARTIFACTS_DIR=/path/to/experimental`` to point the engine
at a different artifact set for A/B comparisons without rebuilding the
shipped one (see ``splitsmith.runtime`` for the full env-var list).

The review-time variant generator ``scripts/build_ensemble_fixture.py``
still exists for offline comparison under ``build/ensemble-review/``.

## Multi-shooter comparison (`compare/` package)

``splitsmith compare export <manifest>`` reads N existing single-shooter
``MatchProject`` directories (all from the same match) and emits one
FCPXML where each stage is a beep-aligned grid compound clip. It does
not run detection -- it only reads finished projects' per-stage trims.

Slot order is alphabetical by manifest label and stable across stages
(missing trims become black filler, never reshuffle the grid). The
audio-source shooter from the manifest drives the sequence frame rate
and is the only unmuted tile. Per-module breakdown lives in SPEC.md
under "Module responsibilities"; the example manifest is at
``examples/compare-bromma-classifier-2026.yaml``.

## Things Claude Code should not do

- Add new dependencies without asking. The dep list is small on purpose.
- Refactor the architecture without discussion. The pipeline structure in SPEC.md is intentional.
- Add features not in SPEC.md without confirming they belong.
- Generate fake test fixtures. Real audio samples or skip the test.

## First-session checklist

When starting fresh on this project:
1. Read SPEC.md fully before writing code.
2. Check that `uv`, `ffmpeg`, and Python 3.11+ are available.
3. Set up the project skeleton (pyproject.toml, src layout, tests dir).
4. Get a sample video from the user before tuning detection thresholds.
5. Build modules in pipeline order: video_match → beep_detect → trim → shot_detect → csv_gen → fcpxml_gen → cli.
6. Test each module against fixtures before moving to the next.

## Useful prior context

The user has prior data sources from match scoring. Example JSON format is in `examples/` — review it before designing the stage matching logic. Field names matter: `time_seconds`, `scorecard_updated_at`, `stage_number`, `stage_name`, `competitor_id`, `division`, `club`.

The tool should be agnostic to division but the user typically shoots Production Optics, where splits in the 0.15-0.40s range are typical for accurate-paced shooting; use that for sensible defaults.
