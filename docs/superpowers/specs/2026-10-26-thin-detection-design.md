# Thin shot detection: no model downloads by default, CLAP as an opt-in

Date: 2026-10-26. Status: approved design, not yet planned.

## Intent

A fresh install of splitsmith detects shots on both camera classes without
downloading any model, and scores no worse than the shipped ensemble on
held-out matches. The 327 MB PANN model is retired. The 120 MB CLAP model
becomes an opt-in that buys about one point of headcam F1 for the user who
asks for it.

## Why

A spike on 2026-10-26 (`~/.claude-tmp/thin-detect/` on gaspode; the
numbers are reproduced below so the spike need not survive) measured each
input's contribution with the build's own leave-one-match-out (LOMO)
machinery, one GBDT per camera class, the stage decoder fitted on the
LOMO scores, event-level P/R/F1 at 75 ms:

| Headcam (108 fixtures, 9 matches, 2,314 shots) | downloads | F1 | FP | FN |
|---|---|---|---|---|
| hand features only | 0 | 0.927 | 132 | 195 |
| hand + CLAP, no PANN (same 104 fixtures) | 120 MB | 0.936 | 112 | 178 |

| Handheld (68 fixtures, 5 matches) | downloads | F1 |
|---|---|---|
| today's columns | 447 MB | 0.971 |
| no PANN | 120 MB | 0.970 |
| hand features only | 0 | 0.974 |

Three facts drive the design:

1. PANN's `gunshot_prob` has GBDT importance 0.0025 and removing it
   improves headcam in every configuration. It is dead weight.
2. Handheld needs no learned model at all.
3. The shipped headcam model is data-starved, not feature-starved. The
   calibration (built 2026-10-09, decoder 2026-10-16) trained its headcam
   class on 37 fixtures / 760 shots from 4 matches. The corpus holds 108
   headcam fixtures / 2,314 shots from 9 matches; five matches
   (ESS Black, HFO Masters, Hugelsta, Satra, Oden Cup) have never been in
   a build. On the three matches the shipped model knew, hand-only scored
   0.59; on all nine it scores 0.93. CLAP adds 0.009 on top.

The owner decided on 2026-10-26: CLAP stays as an opt-in, not cut.

## Goals

- A fresh desktop or pip install runs shot detection on both classes with
  no network access and no model fetch.
- The build's held-out figures clear the gate in "Acceptance gate"
  below: `clap` is no worse than today's columns re-fitted the same way
  on the same rows, and `base` holds the spike's hand-only level.
- The opt-in `clap` set, when enabled and present, is what detection
  uses; both sets come from one build over the same fixtures.
- PANN is gone from the runtime, the build, the feature caches, the
  artifact manifest and `fetch-models`.
- Every detection records which model set produced it (audit trail).

## Non-goals

- Replacing librosa (#442), walking the GBDT in numpy to drop
  `onnxruntime` (CLAP needs it anyway), new audio encoders (#1047),
  synthetic negatives and corpus targets (#1050).
- Fixing the DJI Osmo Action 4 false positives or the GO 3S misses (see
  "What the spike also found"). They get recorded, not fixed, here.
- Chromium / playwright size (overlay rendering, unrelated).
- Any change to beep detection (already model-free).

## Anti-goals

- A thin build that quietly scores below today's on handheld or on any
  single match it shares with the shipped model. The per-match LOMO table
  is part of the PR, and a regression on a match is a finding, not a
  rounding error.
- A user who enabled CLAP silently getting `base` results after an update
  or a cache wipe without the audit doc saying so.
- Two model sets fitted on different fixture lists, or a calibration
  whose numbers a reader cannot attribute to a set.
- Detection blocking on a download. The download is a job; detection runs
  with what is present.

## Acceptance gate

The comparison is leave-one-match-out on the same candidate rows, never
the shipped artifact as-is (scoring it on its own training fixtures is
in-sample). The build already produces the old-columns LOMO figures
(`_heldout_probs` over today's feature set); it keeps doing so as a
baseline mode, so one build run prints the whole table. My calls, the
owner can tighten them:

- `clap` set, per class: event F1 at 75 ms within 0.005 of the
  old-columns LOMO figure on the same rows. Per match: a drop of more
  than 0.02 F1 blocks the PR; a smaller drop is listed in it.
- `base` set, per class: LOMO event F1 at least 0.92 headcam and 0.97
  handheld (the spike measured 0.923 on the 108-fixture headcam universe
  and 0.974 on 68 handheld fixtures; the full handheld corpus is larger
  and the number is expected to move a little). Per match: no more than
  0.03 under the `clap` set on that match.

What the spike measured, so the plan knows what it has to reproduce:
the build's candidate universe (`detect_shots` at `min_confidence=0`,
`recall_fallback="cwt"`), per-class thresholds by the 95 % recall rule,
voter C out of fold by match, the decoder fitted on those LOMO scores
and scored with an out-of-fold recalibration (`fit_stage_decoder`'s own
loop), `clap_diff` and `vote_b` zero for the hand-only arm. It did not
apply `voter_a_floor` and did not run the consensus path. The first
step of the plan is the real build reproducing the spike's table; a gap
there is a finding before anything else is built.

## Design

### 1. Two model sets, one build

`scripts/build_ensemble_artifacts.py` builds, from one universe, two
model sets:

- `base`: hand-crafted features + camera one-hot + the stage-relative
  block minus its CLAP and PANN columns.
- `clap`: `base` + CLAP per-prompt similarities + `clap_diff` + the
  relative CLAP columns.

Each set has, per camera class: a voter C ONNX graph, the thresholds
(`voter_a_floor`, `voter_c_threshold`; `voter_b_threshold` only in
`clap`), a stage decoder (see 3), and the held-out metrics. Columns are
selected by name from one full feature matrix (`features.py` gets a
column-name list per set; the runtime and the build pick columns through
the same function, so the two cannot disagree on order). The calibration
records each set's column names and dimension; the runtime refuses a
graph whose input width does not match.

The exact calibration schema is the builder's. The invariants are: one
`calibration_fixtures` list for both sets; every per-set number lives
under its set's key; the lab's model pages and `tests/test_lab_model_map.py`
read through one accessor and show the set they are reading.

The build runs on the whole corpus (`fixture_stems()`), both sets on
the same fixtures, and the PR reports `--reviewed-only` beside it
(shot-detection.md's rule). The CLAP caches for the 94 headcam fixtures
that lacked them were extracted on gaspode on 2026-10-26
(`tests/fixtures/.cache/`, local only); four of them the spike's
universe rejected on row count, and the build's own stale check names
them, so the build runs with `--extract` once and regenerates them
rather than dropping fixtures from either set. PANN caches are no longer
read, written or checked; `scripts/extract_audio_embeddings.py` and the
PANN branch of `scripts/feature_caches.py` go.

Voter E (the visual probe) is untouched and is not keyed by set.

### 2. PANN retired

`PannRuntime`, `compute_pann_gunshot_probs`, `load_pann_runtime`,
`EnsembleCandidate.gunshot_prob`, the `pann_cnn14` manifest entry and
the PANN column in `voter_c_feature_matrix` are removed. The R2 object
may stay (nothing references it). `splitsmith fetch-models` then lists
only the two CLAP artifacts. The SPA's generated types follow
(`scripts/export_openapi.py`, `pnpm gen:api`); there is no deprecation
period, the SPA is the field's only API consumer. Audit docs written
before this change carry `gunshot_prob` on their candidate rows
(`detection_merge.py`); readers (the lab, the audit UI) treat it as
optional and never write it again. No share page, export, CSV or JSON
output reads it. Tests that hard-code the old headcam metrics (37
fixtures, 760 positives) are updated freely.

### 3. One decoder per camera class, per set

`fit_stage_decoder` is called per class (the build already splits rows
by class for voter C). The decoder's `features` list is per set: `base`
drops `clap_diff` and `vote_b` as well as `gunshot_prob`; `clap` keeps
`clap_diff` and `vote_b`. `decoder_features` builds the matrix from the
decoder's own `features` list rather than a module constant, so a
calibration always decodes with the columns it was fitted on. A decoder
block in the calibration is keyed by class under its set.

The consensus escape hatch (`SPLITSMITH_STAGE_DECODER=0`) survives: for
`base` it is A and C with consensus 2 (both must vote; there is no B).
That is my call, chosen because the 2026-05-11 sweeps showed A and B
have no leverage under `c_required`, so nothing is lost.

### 4. Choosing a set at runtime

`load_ensemble_runtime(model_set=...)` constructs the CLAP session only
for `clap`; `base` never touches the model registry. Resolution order:

1. `SPLITSMITH_MODEL_SET` env (CLI, lab, tests, hosted).
2. The local preference (`GlobalPrefs.detection_model_set`, default
   `base`).
3. `base`.

When `clap` is asked for and either CLAP artifact is absent or fails
verification, detection runs `base` and says so: the ensemble result and
the stage's audit doc carry `model_set` (the set actually used) and the
calibration's `built_at`; the UI shows the set on the detection result
the same way it shows other provenance (placement is the builder's,
under `ui-budget.md`). A missing artifact never raises out of detection.

Hosted runs `clap`: `SPLITSMITH_MODEL_SET=clap` goes in the hosted
Dockerfile's `ENV`, so every hosted image (Cloud Run, the Railway
fallback, a self-hosted worker) carries it without per-deployment
config; that is in scope of the first PR. A hosted process without the
variable runs `base` and records it; it does not refuse. Hosted has the
bandwidth, and 120 MB on a cold Cloud Run execution replaces 447 MB.
That is my call; the owner can flip it with the env var.

Preferences do not sync, so a stage detected on the desktop and one
detected hosted can differ by set. That is fine because `model_set`
lives in the stage's audit doc, which already syncs; no new doc kind.

Old audit docs have no `model_set`. They read as "unknown" (the model
that made them no longer ships), never as `base`; there is no
migration. The lab reads calibrations through one accessor; a flat
calibration from git history is exposed by it as one set named
`legacy`, so the Models page's version history keeps rendering older
builds, while the runtime refuses the flat shape (the committed test
fixtures move to the new shape).

### 5. The opt-in

Settings gets one control: "Audio model for head cameras", off by
default, with copy that says it downloads 120 MB once and improves head
camera detection a little. Turning it on sets the preference and submits
the existing `model_download` job for the CLAP artifacts; the startup
prefetch (`_maybe_submit_model_download`) runs only when the preference
is `clap`, and `/api/models/status` reports against the artifacts the
preference needs. Turning it off sets the preference back; cached files
stay (the user can clear them with `fetch-models` tooling, not from
Settings). While a download is in flight, detection runs `base` and the
result says so (section 4); the user re-runs detection if they want the
`clap` result. A `clap` preference on a machine without the files
(fresh install, cache wipe) is handled by the existing startup prefetch,
which already re-submits on every boot while anything is missing and is
idempotent; Settings shows the download state from
`/api/models/status`. No extra nudge anywhere else.

A What's new entry ships in the same PR (`whats-new` skill).

### 6. Tests that must fail against the pre-change code

- ONNX parity per set and class (`tests/test_onnx_parity.py` pattern).
- `base` detection constructs no CLAP session and makes no registry
  call (mock the loader; assert not called).
- `clap` requested with an artifact missing: detection completes, the
  result's `model_set` is `base`, the audit doc records it.
- The build refuses two sets with different fixture lists, and a graph
  whose input width differs from its set's column count.
- `fetch-models --list` names no PANN artifact.
- The slim smoke (the one WAV in git) detects with `base` and no network.
- Decoder: a calibration whose decoder `features` omit `clap_diff`
  decodes without CLAP values; the existing `test_stage_decoder.py` fit
  still reproduces.

Per CLAUDE.md, a detection-pipeline change gets a review pass before
merge, with the reviewer told which claims to check (the per-match LOMO
table, that each new test fails without its change, that no code path
can construct a CLAP session under `base`).

### 7. Documentation

- `docs/cameras.md`, dated 2026-10-26: DJI Osmo Action 4 makes 24 FP per
  100 shots against 3 to 5 for the GO 3S and Vanguard (8 fixtures, one
  shooter, Hostfinalen); most FPs are not echoes (median 0.7 s from the
  nearest true shot). GO 3S makes the misses, 11 per 100, concentrated
  in Satra and HFO Masters; the Satra labels have not been checked on a
  spectrogram.
- `.claude/rules/shot-detection.md`: the two sets, the selection order,
  the fallback rule, PANN gone, per-class decoders.
- A finding note under `docs/ensemble_dashboard/findings/` with the
  spike's tables.
- `docs/ensemble_dashboard/findings/2026-05-11_fold_voter_d_into_c.md`
  stays as history.

## Constraints

- No new dependencies. `huggingface_hub` leaves the runtime list (nothing
  under `src/` imports it); `onnxruntime` stays.
- Never commit a fixture WAV or a feature cache.
- `uv run` rewrites `uv.lock`; the PR must not drag dependency churn in.
- The hosted image's `BAKE_MODELS=1` path still works (it fetches the
  CLAP artifacts).
- The desktop slim-import check (`assert_slim_import_surface.py`) still
  passes; nothing on the `create_app` path imports the registry eagerly.

## What the spike also found (not in scope, recorded for later issues)

- The DJI and GO 3S error profiles above.
- `extract_clap_features.py` takes `--fixture` repeatedly; in zsh an
  unquoted `$args` passes one word. CPU extraction on gaspode takes
  about 50 minutes for 94 fixtures.
- Four headcam fixtures had CLAP caches the universe builder rejected
  (row count mismatch); the build's own stale check will name them.

## Left to the builder

- The calibration schema for two sets and per-class decoders, and the
  accessor the lab reads through.
- How the column-name list is represented and validated.
- Settings placement and copy (`ui-budget.md`), and where the result
  shows its `model_set`.
- Whether to split the work into two PRs (build + runtime + PANN
  retirement first; Settings opt-in second) or one. Two is my suggestion:
  the first PR is already the whole detection-pipeline review.
